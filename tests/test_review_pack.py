"""PR 1A — psychologist_review_pack: generate -> save -> delete on test data, plus
a static guard (same style as A1's default-deny) proving the module never routes
through any alert/log-broadcast channel.
"""
import asyncio
import pathlib

import pytest

import database
import review_pack
import access_control as ac

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    return database


@pytest.fixture(autouse=True)
def _allow_self_request(monkeypatch):
    # PR 1B-2: generate_review_pack now enforces access_control.
    # can_request_review_pack(requester_uid, target_uid). These pre-existing
    # PR 1A tests exercise the SHELL/DATA mechanics, not the permission
    # contract (that has its own dedicated tests in test_review_pack_permission.py)
    # -- pin uid 42 as OWNER requesting their own pack so they keep validating
    # what they were written to validate.
    monkeypatch.setattr(ac, "OWNER_USER_ID", 42)


def test_generate_review_pack_shell_structure(tmp_db):
    async def go():
        await tmp_db.upsert_user(42, "alice", "Alice", "ru")
        return await review_pack.generate_review_pack(42, requester_uid=42)
    pack = asyncio.run(go())
    expected_keys = {
        "generated_at", "user_id", "user_message", "bot_response", "active_mode",
        "hard_mirror_state", "hard_mirror_brake_result", "influence_trace",
        "questionnaire_sources", "pattern_hypotheses", "proposed_experiment",
        "user_reaction", "needs_specialist_review",
    }
    assert set(pack.keys()) == expected_keys
    assert pack["user_id"] == 42
    # No product content is wired yet — these must stay empty/None shells.
    assert pack["active_mode"] is None
    assert pack["questionnaire_sources"] == []
    assert pack["pattern_hypotheses"] == []


def test_generate_review_pack_wires_real_influence_trace(tmp_db):
    # The one REAL data source: proves the shell actually connects to something,
    # not just a fake shape.
    async def go():
        await tmp_db.upsert_user(42, "alice", "Alice", "ru")
        await tmp_db.log_influence_trace("rid-1", 42, [
            ("mode", "m1", "reply drew on mode m1"),
        ])
        return await review_pack.generate_review_pack(42, requester_uid=42)
    pack = asyncio.run(go())
    assert len(pack["influence_trace"]) == 1
    assert pack["influence_trace"][0]["source_id"] == "m1"


def test_save_and_delete_review_pack_roundtrip(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(review_pack, "REVIEW_PACK_DIR", tmp_path / "private_review_packs")

    async def go():
        await tmp_db.upsert_user(42, "alice", "Alice", "ru")
        return await review_pack.generate_review_pack(42, requester_uid=42)
    pack = asyncio.run(go())

    path = review_pack.save_review_pack(pack, 42)
    assert path.exists()
    assert path.parent == tmp_path / "private_review_packs"

    review_pack.delete_review_pack(path)
    assert not path.exists()


def test_delete_review_pack_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(review_pack, "REVIEW_PACK_DIR", tmp_path / "private_review_packs")
    missing = tmp_path / "private_review_packs" / "does_not_exist.json"
    review_pack.delete_review_pack(missing)   # must not raise


# ── static guard: review_pack.py must never touch an alert/log-broadcast path ──
# Lives here, not in review_pack.py: a module cannot hold the denylist of words it
# promises not to contain (that list would itself contain every word, making the
# module self-match). Both the real guard test AND its positive control call the
# SAME find_forbidden_alert_symbols() — the control proves the actual check
# function catches a violation, not a copy of its logic.
FORBIDDEN_ALERT_SYMBOLS = (
    "notifications", "push_alert", "admin_alert_text", "_send_email",
    "_send_webhook", "ALERT_WEBHOOK_URL", "ALERT_EMAIL_TO", "logging.",
)


def find_forbidden_alert_symbols(src: str) -> list[str]:
    return [s for s in FORBIDDEN_ALERT_SYMBOLS if s in src]


def test_review_pack_module_never_imports_alert_or_log_channels():
    src = (ROOT / "review_pack.py").read_text(encoding="utf-8")
    offenders = find_forbidden_alert_symbols(src)
    assert not offenders, (
        "review_pack.py references an alert/log-broadcast symbol — the review pack "
        "must NEVER reach logs/alerts/webhooks/CI artifacts/debug output:\n  "
        + "\n  ".join(offenders))


def test_scanner_would_catch_a_forbidden_import(tmp_path):
    # Positive control: the REAL find_forbidden_alert_symbols, run against a
    # synthetic rogue module, must actually flag it.
    rogue = tmp_path / "rogue_review_pack.py"
    rogue.write_text("from notifications import push_alert\n", encoding="utf-8")
    offenders = find_forbidden_alert_symbols(rogue.read_text(encoding="utf-8"))
    assert offenders, "positive control failed: the forbidden-symbol scan caught nothing"
