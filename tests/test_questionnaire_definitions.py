"""Questionnaire Registry (PR A) — definition loader, validation, and
fail-closed rejection of risk-bearing definitions.

Fully replaces the old single-definition-loader test suite (get_validated_
definition). No real/licensed questionnaire text is used anywhere here --
only the synthetic fixture at tests/fixtures/synthetic_questionnaire.json
(invented, non-clinical items) and small ad-hoc risk-bearing definitions
constructed inline for the negative-control tests.
"""
import json
import pathlib

import pytest

import questionnaires as q

FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "synthetic_questionnaire.json"


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


# ── licensing / gitignore hygiene ───────────────────────────────────────────────
def test_private_questionnaires_dir_is_gitignored():
    gitignore = (pathlib.Path(__file__).resolve().parent.parent / ".gitignore").read_text(encoding="utf-8")
    assert "private_questionnaires/" in gitignore


def test_synthetic_questionnaire_contains_no_clinical_instrument_text():
    src = FIXTURE_PATH.read_text(encoding="utf-8").lower()
    forbidden_fragments = (
        "phq-9", "phq9", "little interest or pleasure",
        "beck depression", "bdi-ii", "gad-7", "gad7",
        "feeling nervous, anxious",
    )
    for frag in forbidden_fragments:
        assert frag not in src


# ── validation: the good path ───────────────────────────────────────────────────
def test_synthetic_fixture_validates_successfully():
    d = _load_fixture()
    q._validate_definition(d)   # must not raise


def test_registry_returns_fixture_when_present(tmp_path):
    (tmp_path / "demo.json").write_text(FIXTURE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    registry = q.load_registry(tmp_path)
    assert registry.get("synthetic_demo_v1") is not None
    assert registry.get("synthetic_demo_v1")["id"] == "synthetic_demo_v1"


# ── empty / missing directory ───────────────────────────────────────────────────
def test_no_private_definitions_reports_empty_registry(tmp_path):
    registry = q.load_registry(tmp_path)   # empty dir
    assert registry.by_id == {}
    assert registry.list_active() == []


def test_missing_directory_reports_empty_registry(tmp_path):
    missing = tmp_path / "does_not_exist"
    registry = q.load_registry(missing)
    assert registry.by_id == {}


# ── fail-closed: risk-bearing definitions ───────────────────────────────────────
def test_definition_with_contains_risk_items_true_refuses_to_load(tmp_path):
    d = _load_fixture()
    d["contains_risk_items"] = True
    (tmp_path / "risky.json").write_text(json.dumps(d), encoding="utf-8")
    registry = q.load_registry(tmp_path)
    assert registry.get("synthetic_demo_v1") is None
    assert registry.by_id == {}


def test_definition_with_item_risk_flag_refuses_to_load(tmp_path):
    d = _load_fixture()
    d["items"][0]["risk_flag"] = True
    (tmp_path / "risky.json").write_text(json.dumps(d), encoding="utf-8")
    registry = q.load_registry(tmp_path)
    assert registry.by_id == {}


def test_definition_with_option_risk_flag_refuses_to_load(tmp_path):
    d = _load_fixture()
    d["items"][0]["options"][0]["risk_flag"] = True
    (tmp_path / "risky.json").write_text(json.dumps(d), encoding="utf-8")
    registry = q.load_registry(tmp_path)
    assert registry.by_id == {}


def test_invalid_definition_missing_required_fields_excluded(tmp_path):
    (tmp_path / "broken.json").write_text(json.dumps({"id": "x"}), encoding="utf-8")
    registry = q.load_registry(tmp_path)
    assert registry.by_id == {}


def test_definition_with_empty_items_excluded(tmp_path):
    d = _load_fixture()
    d["items"] = []
    (tmp_path / "empty.json").write_text(json.dumps(d), encoding="utf-8")
    registry = q.load_registry(tmp_path)
    assert registry.by_id == {}


def test_option_without_id_or_value_excluded(tmp_path):
    d = _load_fixture()
    del d["items"][0]["options"][0]["value"]
    (tmp_path / "broken_option.json").write_text(json.dumps(d), encoding="utf-8")
    registry = q.load_registry(tmp_path)
    assert registry.by_id == {}


# ── malformed / multiple files: registry semantics ──────────────────────────────
def test_malformed_definition_is_excluded_not_fatal(tmp_path):
    (tmp_path / "broken.json").write_text("{not valid json", encoding="utf-8")
    registry = q.load_registry(tmp_path)
    assert registry.by_id == {}


def test_multiple_valid_definitions_both_load(tmp_path):
    # Multiple files is the NORMAL case for a registry (unlike the old
    # single-definition loader, which refused outright on ambiguity) -- each
    # valid file is loaded and keyed by its own id.
    d1 = _load_fixture()
    d1["id"] = "demo_one"
    d2 = _load_fixture()
    d2["id"] = "demo_two"
    (tmp_path / "a_first.json").write_text(json.dumps(d1), encoding="utf-8")
    (tmp_path / "b_second.json").write_text(json.dumps(d2), encoding="utf-8")

    registry = q.load_registry(tmp_path)

    assert set(registry.by_id.keys()) == {"demo_one", "demo_two"}


def test_valid_plus_malformed_definition_loads_only_the_valid_one(tmp_path):
    # One valid file + one malformed file: the malformed one is simply
    # excluded (fail-closed PER FILE); the valid one still loads. This is a
    # deliberate registry-semantics change from the old loader (which treated
    # "two files present, one broken" as ambiguous/invalid as a WHOLE) --
    # documented explicitly since a registry with many files must not refuse
    # entirely just because one unrelated file is malformed.
    d = _load_fixture()
    (tmp_path / "a_valid.json").write_text(json.dumps(d), encoding="utf-8")
    (tmp_path / "b_broken.json").write_text("{not valid json", encoding="utf-8")
    registry = q.load_registry(tmp_path)
    assert registry.get("synthetic_demo_v1") is not None
    assert len(registry.by_id) == 1


# ── strengthened schema validation ───────────────────────────────────────────────
def test_missing_item_text_excluded(tmp_path):
    d = _load_fixture()
    del d["items"][0]["text"]
    (tmp_path / "broken.json").write_text(json.dumps(d), encoding="utf-8")
    registry = q.load_registry(tmp_path)
    assert registry.by_id == {}


def test_missing_option_label_excluded(tmp_path):
    d = _load_fixture()
    del d["items"][0]["options"][0]["label"]
    (tmp_path / "broken.json").write_text(json.dumps(d), encoding="utf-8")
    registry = q.load_registry(tmp_path)
    assert registry.by_id == {}


def test_option_id_too_long_excluded(tmp_path):
    d = _load_fixture()
    d["items"][0]["options"][0]["id"] = "x" * (q.MAX_ANSWER_ID_LEN + 1)
    (tmp_path / "broken.json").write_text(json.dumps(d), encoding="utf-8")
    registry = q.load_registry(tmp_path)
    assert registry.by_id == {}


def test_option_id_with_colon_excluded(tmp_path):
    d = _load_fixture()
    d["items"][0]["options"][0]["id"] = "bad:id"
    (tmp_path / "broken.json").write_text(json.dumps(d), encoding="utf-8")
    registry = q.load_registry(tmp_path)
    assert registry.by_id == {}


def test_option_id_with_space_excluded(tmp_path):
    d = _load_fixture()
    d["items"][0]["options"][0]["id"] = "bad id"
    (tmp_path / "broken.json").write_text(json.dumps(d), encoding="utf-8")
    registry = q.load_registry(tmp_path)
    assert registry.by_id == {}


# ── helper functions ─────────────────────────────────────────────────────────
def test_get_item_by_index():
    d = _load_fixture()
    assert q.get_item(d, 0)["id"] == "energy"
    assert q.get_item(d, 1)["id"] == "focus"
    assert q.get_item(d, 99) is None


def test_find_option_by_id():
    d = _load_fixture()
    item = q.get_item(d, 0)
    assert q.find_option(item, "mid")["value"] == "2"
    assert q.find_option(item, "does_not_exist") is None
