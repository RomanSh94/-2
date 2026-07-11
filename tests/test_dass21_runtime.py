"""Owner-only DASS-21 runtime gate (PR #55) — identity/integrity fail-closed.
Uses ONLY the synthetic shape fixture as the 'private' file."""
import hashlib
import json
import pathlib
import shutil

import pytest

import access_control as ac
import config
import dass21_runtime as rt

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "dass21" / "synthetic_dass21_shape.json"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Enabled, owner=1, valid file+hash by default; each test breaks one leg."""
    path = tmp_path / "dass21_private.json"
    shutil.copyfile(FIXTURE, path)
    monkeypatch.setattr(config, "DASS21_ENABLED", True)
    monkeypatch.setattr(config, "DASS21_OWNER_ONLY", True)
    monkeypatch.setattr(config, "DASS21_DEFINITION_PATH", str(path))
    monkeypatch.setattr(config, "DASS21_DEFINITION_SHA256",
                        hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    return path


def _rewrite(path, mutate):
    d = json.loads(path.read_text(encoding="utf-8"))
    mutate(d)
    path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_matching_hash_allows_owner(env):
    status = rt.dass21_runtime_status(1)
    assert status.available and status.reason_code == "ok"


def test_disabled_blocks_even_owner(env, monkeypatch):
    monkeypatch.setattr(config, "DASS21_ENABLED", False)
    assert not rt.dass21_runtime_status(1).available


def test_non_owner_blocked(env):
    assert not rt.dass21_runtime_status(2).available
    assert rt.dass21_runtime_status(2).reason_code == "not-owner"


def test_owner_only_cannot_be_disabled_by_env(env, monkeypatch):
    # HARD boundary: DASS21_OWNER_ONLY=false NEVER broadens access -- it fails
    # closed for everyone (owner included) until a later explicit PR defines
    # non-owner access.
    monkeypatch.setattr(config, "DASS21_OWNER_ONLY", False)
    assert not rt.dass21_runtime_status(1).available
    assert not rt.dass21_runtime_status(2).available


def test_invited_user_denied_when_owner_only_env_false(env, monkeypatch):
    monkeypatch.setattr(config, "DASS21_OWNER_ONLY", False)
    invited_uid = 777  # an invited ordinary user is still not the owner
    assert not rt.dass21_runtime_status(invited_uid).available


def test_unknown_user_denied(env):
    assert not rt.dass21_runtime_status(999999).available
    assert rt.dass21_runtime_status(999999).reason_code == "not-owner"


def test_owner_allowed_only_when_every_other_gate_passes(env, monkeypatch):
    assert rt.dass21_runtime_status(1).available  # all gates green
    monkeypatch.setattr(config, "DASS21_DEFINITION_SHA256", "0" * 64)
    assert not rt.dass21_runtime_status(1).available  # owner alone is not enough


def test_hash_comparison_uses_compare_digest():
    src = pathlib.Path(rt.__file__).read_text(encoding="utf-8")
    assert "hmac.compare_digest(" in src
    assert ".hexdigest() != pinned" not in src  # no ordinary == / != compare
    assert ".hexdigest() == pinned" not in src


def test_missing_hash_blocks(env, monkeypatch):
    monkeypatch.setattr(config, "DASS21_DEFINITION_SHA256", "")
    assert not rt.dass21_runtime_status(1).available


def test_malformed_hash_blocks(env, monkeypatch):
    for bad in ("xyz", "a" * 63, "A" * 65, "не-хеш"):
        monkeypatch.setattr(config, "DASS21_DEFINITION_SHA256", bad)
        assert not rt.dass21_runtime_status(1).available


def test_missing_file_blocks(env, monkeypatch):
    monkeypatch.setattr(config, "DASS21_DEFINITION_PATH",
                        str(env.parent / "no_such_file.json"))
    assert not rt.dass21_runtime_status(1).available


def test_hash_mismatch_blocks(env, monkeypatch):
    _rewrite(env, lambda d: None)  # reserialize -> bytes differ from pin
    assert not rt.dass21_runtime_status(1).available
    assert rt.dass21_runtime_status(1).reason_code == "hash-mismatch"


def test_wrong_definition_id_blocks_even_with_matching_hash(env, monkeypatch):
    new_hash = _rewrite(env, lambda d: d.update(id="other_definition_v1"))
    monkeypatch.setattr(config, "DASS21_DEFINITION_SHA256", new_hash)
    assert not rt.dass21_runtime_status(1).available


def test_wrong_metadata_blocks(env, monkeypatch):
    for mutate in (
            lambda d: d["clinical_instrument"].update(instrument_version="DASS-42"),
            lambda d: d["clinical_instrument"].update(translation_id="other_ru"),
            lambda d: d["clinical_instrument"].update(scoring_version="v2"),
            lambda d: d["clinical_instrument"].update(risk_contract_id="x"),
            lambda d: d.update(version="OTHER"),
            lambda d: d.update(contains_risk_items=True),
            lambda d: d["items"][0].update(id="dass21_99"),
            lambda d: d["items"][0]["options"][0].update(id="b0"),
            lambda d: d["items"][0]["options"][0].update(value="9"),
            lambda d: d["items"][5]["options"][2].update(risk_flag=True),
            lambda d: d["items"].pop(),
    ):
        path = env
        shutil.copyfile(FIXTURE, path)
        new_hash = _rewrite(path, mutate)
        monkeypatch.setattr(config, "DASS21_DEFINITION_SHA256", new_hash)
        assert not rt.dass21_runtime_status(1).available


def test_unparseable_file_blocks(env, monkeypatch):
    env.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(config, "DASS21_DEFINITION_SHA256",
                        hashlib.sha256(env.read_bytes()).hexdigest())
    assert not rt.dass21_runtime_status(1).available


def test_no_fallback_to_another_dass_definition():
    # The gate binds to ONE exact definition id; nothing else matches.
    assert rt.is_dass21_definition_id("dass21_ru_fattakhov_2024")
    assert not rt.is_dass21_definition_id("dass42_ru")
    assert not rt.is_dass21_definition_id("dass21_en")
    assert not rt.is_dass21_definition_id(None)


def test_gate_is_read_only_no_forbidden_imports():
    src = (pathlib.Path(rt.__file__)).read_text(encoding="utf-8")
    for banned in ("import database", "import openai", "import aiogram",
                   "from database", "from openai", "from aiogram",
                   "import bot", "from bot"):
        assert banned not in src
