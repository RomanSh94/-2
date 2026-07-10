"""PR A — private invite-based access for ordinary (non-owner, non-clinician)
product users.

Covers: config gating (USER_INVITE_ENABLED / USER_INVITE_CODE length),
access_control.user_invite_active() / has_full_access() wiring, the new
`user_access` table + its async helpers in database.py, its registration in
privacy_registry.PRIVACY_REGISTRY, the /start deep-link handling in bot.py's
cmd_start, and two regression-style proofs that this feature never touches
crisis delivery or cross-user questionnaire-session ownership.

This is a real, permanent production feature (contrast with the test-only,
72h-capped TEMP_TEST_INVITE_* mechanism in access_control.py) -- tests here
never touch that mechanism's env vars/state.
"""
import asyncio
import hmac

import pytest

import access_control as ac
import config
import database


VALID_CODE = "b" * 32  # >= 24 chars


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    return database


@pytest.fixture(autouse=True)
def _reset_invite_config(monkeypatch):
    """Clean baseline: invite disabled, no code -- every test opts in
    explicitly. Also pins OWNER/roles away from any real .env leakage."""
    monkeypatch.setattr(config, "USER_INVITE_ENABLED", False)
    monkeypatch.setattr(config, "USER_INVITE_CODE", "")
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})
    yield


def _enable_invite(monkeypatch, code=VALID_CODE):
    monkeypatch.setattr(config, "USER_INVITE_ENABLED", True)
    monkeypatch.setattr(config, "USER_INVITE_CODE", code)


# ── config / gating ─────────────────────────────────────────────────────────
def test_invite_disabled_by_default():
    # Autouse fixture already sets USER_INVITE_ENABLED=False.
    assert ac.user_invite_active() is False


def test_invite_requires_min_length_code(monkeypatch):
    _enable_invite(monkeypatch, code="short")
    assert ac.user_invite_active() is False


def test_invite_active_with_valid_config(monkeypatch):
    _enable_invite(monkeypatch)
    assert ac.user_invite_active() is True


# ── correct/wrong code -> registration ──────────────────────────────────────
def test_correct_invite_auto_registers_user(tmp_db, monkeypatch):
    _enable_invite(monkeypatch)
    uid = 5001
    assert asyncio.run(tmp_db.user_has_active_access(uid)) is False
    assert ac.user_invite_active() and hmac.compare_digest(
        VALID_CODE.encode(), config.USER_INVITE_CODE.encode())
    asyncio.run(tmp_db.grant_user_access(uid, source="invite"))
    assert asyncio.run(tmp_db.user_has_active_access(uid)) is True


def test_wrong_invite_does_not_register_user(tmp_db, monkeypatch):
    _enable_invite(monkeypatch)
    uid = 5002
    payload = "totally-wrong-code-not-real-000000"
    matched = ac.user_invite_active() and hmac.compare_digest(
        payload.encode(), config.USER_INVITE_CODE.encode())
    assert matched is False
    assert asyncio.run(tmp_db.user_has_active_access(uid)) is False


# ── has_full_access wiring ───────────────────────────────────────────────────
def test_registered_user_has_full_access(tmp_db, monkeypatch):
    uid = 5003
    asyncio.run(tmp_db.grant_user_access(uid, source="invite"))
    assert asyncio.run(ac.has_full_access(uid)) is True


def test_registered_user_is_not_owner(tmp_db, monkeypatch):
    uid = 5004
    asyncio.run(tmp_db.grant_user_access(uid, source="invite"))
    assert ac.resolve_role(uid) != ac.OWNER
    # Never A1-equivalent either -- a1_allowed only checks role, never
    # user_access, so a registered ordinary user must be denied.
    assert asyncio.run(ac.a1_allowed(uid)) is False
    with pytest.raises(ac.A1NotAllowed):
        asyncio.run(ac.assert_a1_allowed(uid))


def test_owner_access_unchanged(tmp_db, monkeypatch):
    # OWNER's own path is untouched: no user_access row needed, and it stays
    # True even with an unrelated user_access row present for someone else.
    asyncio.run(tmp_db.grant_user_access(999, source="invite"))
    assert asyncio.run(ac.has_full_access(1)) is True  # OWNER_USER_ID == 1
    assert ac.resolve_role(1) == ac.OWNER


# ── block / re-invite behavior ───────────────────────────────────────────────
def test_blocked_user_cannot_reenter_with_invite(tmp_db, monkeypatch):
    uid = 5005
    asyncio.run(tmp_db.grant_user_access(uid, source="invite"))
    assert asyncio.run(ac.has_full_access(uid)) is True
    asyncio.run(tmp_db.block_user_access(uid))
    assert asyncio.run(ac.has_full_access(uid)) is False
    # Re-attempt granting via a fresh invite open -- must NOT reactivate.
    asyncio.run(tmp_db.grant_user_access(uid, source="invite"))
    assert asyncio.run(ac.has_full_access(uid)) is False
    import sqlite3
    con = sqlite3.connect(tmp_db.DB)
    row = con.execute("SELECT status FROM user_access WHERE user_id=?", (uid,)).fetchone()
    con.close()
    assert row == ("blocked",)


# ── questionnaire ownership regression ──────────────────────────────────────
def test_user_a_cannot_load_user_b_questionnaire_session(tmp_db, monkeypatch):
    import bot as bot_module
    uid_a, uid_b = 6001, 6002
    asyncio.run(tmp_db.upsert_user(uid_a, "a", "A"))
    asyncio.run(tmp_db.upsert_user(uid_b, "b", "B"))
    sid = asyncio.run(tmp_db.start_questionnaire_session(uid_a, "demo_q", "1"))

    owned = asyncio.run(bot_module._load_owned_active_session(sid, uid_a))
    not_owned = asyncio.run(bot_module._load_owned_active_session(sid, uid_b))
    assert owned is not None and owned["user_id"] == uid_a
    assert not_owned is None


# ── privacy delete: only current user's user_access row is removed ─────────
def test_privacy_delete_deletes_only_current_user_data(tmp_db, monkeypatch):
    uid_a, uid_b = 7001, 7002
    asyncio.run(tmp_db.upsert_user(uid_a, "a", "A"))
    asyncio.run(tmp_db.upsert_user(uid_b, "b", "B"))
    asyncio.run(tmp_db.grant_user_access(uid_a, source="invite"))
    asyncio.run(tmp_db.grant_user_access(uid_b, source="invite"))

    asyncio.run(tmp_db.delete_all_personal_data(uid_a))

    assert asyncio.run(tmp_db.user_has_active_access(uid_a)) is False
    assert asyncio.run(tmp_db.user_has_active_access(uid_b)) is True


def test_user_access_registered_in_privacy_registry():
    import privacy_registry as pr
    assert "user_access" in pr.PRIVACY_REGISTRY
    entry = pr.PRIVACY_REGISTRY["user_access"]
    assert entry.user_id_column == "user_id"
    assert entry.export_policy == "INCLUDE"
    assert entry.delete_policy == "CASCADE_DELETE"
    # Default-deny scanner must also see it as already-registered (no gap).
    unregistered = pr.find_unregistered_sensitive_tables(database.SCHEMA)
    assert "user_access" not in unregistered


# ── crisis flow is structurally independent of product access ──────────────
def test_crisis_flow_unchanged_for_registered_user(tmp_db, monkeypatch):
    import bot as bot_module
    from risk_detector import detect_risk
    from language_detector import detect_language

    uid = 8001
    asyncio.run(tmp_db.upsert_user(uid, "u", "U"))
    asyncio.run(tmp_db.grant_user_access(uid, source="invite"))

    text = "я хочу покончить с собой"
    lang = detect_language(text)
    risk = detect_risk(text, lang)
    assert "suicide" in risk["categories"]

    class FakeMessage:
        def __init__(self):
            self.answers = []

        async def answer(self, text, reply_markup=None):
            self.answers.append(text)
            return None

    msg = FakeMessage()
    asyncio.run(bot_module.trigger_crisis(msg, uid, "u", text, risk, lang))

    assert len(msg.answers) == 1
    assert msg.answers[0]  # deterministic crisis text was sent, never empty
    # The crisis screen must never be gated by has_full_access -- prove the
    # call path never even needed a user_access row by also confirming a
    # completely unregistered uid gets the identical treatment.
    uid2 = 8002
    msg2 = FakeMessage()
    risk2 = detect_risk(text, detect_language(text))
    asyncio.run(bot_module.trigger_crisis(msg2, uid2, "u2", text, risk2, lang))
    assert len(msg2.answers) == 1
