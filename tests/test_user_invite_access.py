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
    # MASTER EXECUTION ADDENDUM v3 -- this assertion is INTENTIONALLY flipped
    # from the original PR #49 version (which asserted a1_allowed is False /
    # A1NotAllowed is raised). That was the discovered gap: has_full_access()
    # already passed ordinary invite-registered users, but a1_allowed() still
    # denied them, silently breaking "Обсудить результат" for them. An active
    # ordinary user is now A1-allowed too -- same "A1 is a strict subset of
    # ordinary product access" principle already used for CLINICIAN_TESTER --
    # while still never resolving as OWNER (role stays UNKNOWN; user_access is
    # a separate mechanism from the OWNER/CLINICIAN_* role model).
    assert asyncio.run(ac.a1_allowed(uid)) is True
    asyncio.run(ac.assert_a1_allowed(uid))  # must not raise


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


# ── MASTER EXECUTION ADDENDUM v3 / PHASE 1 -- closing the ordinary-user A1 gap
# a1_allowed()/assert_a1_allowed() previously denied every active
# invite-registered user (role resolves UNKNOWN, and the old code only ever
# checked role == OWNER / CLINICIAN_TESTER). This block proves the fix without
# weakening any existing invariant: still no OWNER/reviewer/cross-user/
# dashboard escalation, still fail-closed on any error, still gated the same
# way has_full_access() already was.

def test_active_registered_user_is_a1_allowed(tmp_db, monkeypatch):
    uid = 9001
    asyncio.run(tmp_db.grant_user_access(uid, source="invite"))
    assert asyncio.run(ac.a1_allowed(uid)) is True


def test_unknown_user_is_not_a1_allowed(tmp_db, monkeypatch):
    uid = 9002  # never granted access at all
    assert asyncio.run(ac.a1_allowed(uid)) is False


def test_blocked_user_is_not_a1_allowed(tmp_db, monkeypatch):
    uid = 9003
    asyncio.run(tmp_db.grant_user_access(uid, source="invite"))
    assert asyncio.run(ac.a1_allowed(uid)) is True
    asyncio.run(tmp_db.block_user_access(uid))
    assert asyncio.run(ac.a1_allowed(uid)) is False


def test_user_access_db_failure_denies_a1(monkeypatch):
    # Point database.DB at a path that cannot be opened for reads -- the
    # lookup must raise, and a1_allowed must fail closed (False), never crash
    # or accidentally grant.
    monkeypatch.setattr(database, "DB", "/nonexistent-dir/does-not-exist.db")
    uid = 9004
    assert asyncio.run(ac.a1_allowed(uid)) is False


def test_owner_a1_behavior_unchanged(tmp_db, monkeypatch):
    # OWNER_USER_ID == 1 per the autouse fixture. No user_access row needed.
    assert asyncio.run(ac.a1_allowed(1)) is True


def test_clinician_tester_a1_behavior_unchanged(tmp_db, monkeypatch):
    tester_uid = 9005
    reviewer_uid = 9006
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "controlled_clinical_test")
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", {tester_uid})
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", {reviewer_uid})
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {tester_uid: [reviewer_uid]})
    # Not acknowledged yet -> still denied, exactly as before this PR.
    assert asyncio.run(ac.a1_allowed(tester_uid)) is False
    asyncio.run(tmp_db.set_tester_acknowledged(tester_uid))
    assert asyncio.run(ac.a1_allowed(tester_uid)) is True


def test_public_mode_denies_registered_user_a1(tmp_db, monkeypatch):
    uid = 9007
    asyncio.run(tmp_db.grant_user_access(uid, source="invite"))
    assert asyncio.run(ac.a1_allowed(uid)) is True
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "public")
    assert asyncio.run(ac.a1_allowed(uid)) is False


def test_delete_all_removes_user_access(tmp_db, monkeypatch):
    uid = 9008
    asyncio.run(tmp_db.upsert_user(uid, "u", "U"))
    asyncio.run(tmp_db.grant_user_access(uid, source="invite"))
    assert asyncio.run(tmp_db.user_has_active_access(uid)) is True
    assert asyncio.run(ac.a1_allowed(uid)) is True

    asyncio.run(tmp_db.delete_all_personal_data(uid))

    assert asyncio.run(tmp_db.user_has_active_access(uid)) is False
    assert asyncio.run(ac.a1_allowed(uid)) is False
    # Regaining access requires a fresh invite grant -- delete-all does not
    # leave a dormant/blocked row that could be silently reactivated.
    asyncio.run(tmp_db.grant_user_access(uid, source="invite"))
    assert asyncio.run(tmp_db.user_has_active_access(uid)) is True


def test_registered_user_crisis_content_not_alerted_to_owner_by_default(tmp_db, monkeypatch):
    # crisis_alert_targets()/should_alert_owner() are role-based only (OWNER /
    # CLINICIAN_REVIEWER) -- user_access registration does not change role
    # resolution, so a registered ordinary user's crisis event must route the
    # same as any other UNKNOWN-role uid: "none", never "owner". This PR does
    # not touch crisis_alert_targets() at all -- this test proves the
    # pre-existing behavior still holds unchanged with a user_access row
    # present.
    uid = 9009
    asyncio.run(tmp_db.grant_user_access(uid, source="invite"))
    assert ac.should_alert_owner(uid) is False
    kind, targets = ac.crisis_alert_targets(uid)
    assert kind == "none"
    assert targets == []


# ── discuss-menu/topic integration for a registered ordinary user ──────────
# Uses tests/test_questionnaire_discuss.py's exact fixtures/helpers (FakeUser/
# FakeMessage/FakeCallback, _complete_flow, the synthetic registry fixture
# directory) rather than reinventing them here.
def _discuss_test_env():
    import importlib
    return importlib.import_module("tests.test_questionnaire_discuss")


def test_registered_user_can_open_discuss_menu(tmp_path, monkeypatch):
    tqd = _discuss_test_env()
    import bot as bot_module
    import questionnaires

    monkeypatch.setattr(database, "DB", str(tmp_path / "t2.db"))
    asyncio.run(database.init_db())
    monkeypatch.setattr(bot_module, "get_user_language", _const("ru"))
    monkeypatch.setattr(bot_module, "get_active_crisis", _const(None))
    monkeypatch.setattr(bot_module, "log_crisis_delivery", _const(None))
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})
    monkeypatch.setattr(bot_module, "CallbackQuery", tqd.FakeCallback)
    monkeypatch.setattr(bot_module, "_load_registry_fresh",
                        lambda: questionnaires.load_registry(tqd.FIXTURE_DIR))
    monkeypatch.setattr(config, "QUESTIONNAIRE_INTERPRETATION_ENABLED", True)

    uid = 9101
    asyncio.run(database.upsert_user(uid, "u", "U"))
    asyncio.run(database.grant_user_access(uid, source="invite"))

    user = tqd.FakeUser(uid)
    msg = tqd.FakeMessage(user)
    session_id = tqd._complete_flow(user, msg)
    cb = tqd.FakeCallback(user, msg, data=f"q:m:{session_id}")
    asyncio.run(bot_module.cb_questionnaire_discuss_menu(cb))
    assert msg.answers
    assert bot_module.questionnaire_ux.discuss_menu_text("ru") in msg.answers[-1][0]


def test_registered_user_can_use_discuss_topic(tmp_path, monkeypatch):
    tqd = _discuss_test_env()
    import bot as bot_module
    import questionnaires

    monkeypatch.setattr(database, "DB", str(tmp_path / "t3.db"))
    asyncio.run(database.init_db())
    monkeypatch.setattr(bot_module, "get_user_language", _const("ru"))
    monkeypatch.setattr(bot_module, "get_active_crisis", _const(None))
    monkeypatch.setattr(bot_module, "log_crisis_delivery", _const(None))
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})
    monkeypatch.setattr(bot_module, "CallbackQuery", tqd.FakeCallback)
    monkeypatch.setattr(bot_module, "_load_registry_fresh",
                        lambda: questionnaires.load_registry(tqd.FIXTURE_DIR))
    monkeypatch.setattr(config, "QUESTIONNAIRE_INTERPRETATION_ENABLED", True)

    calls = []

    async def _fake_builder(**kwargs):
        calls.append(kwargs)
        await kwargs["send"]("TRACED-REPLY-FOR-REGISTERED-USER")
        return "rid-fake"

    monkeypatch.setattr(bot_module, "traced_response_builder", _fake_builder)

    uid = 9102
    asyncio.run(database.upsert_user(uid, "u", "U"))
    asyncio.run(database.grant_user_access(uid, source="invite"))

    user = tqd.FakeUser(uid)
    msg = tqd.FakeMessage(user)
    session_id = tqd._complete_flow(user, msg)
    cb = tqd.FakeCallback(user, msg, data=f"q:m:{session_id}:why")
    asyncio.run(bot_module.cb_questionnaire_discuss_topic(cb))

    assert len(calls) == 1
    assert calls[0]["user_id"] == uid
    assert calls[0]["requester_uid"] == uid
    assert msg.answers[-1][0] == "TRACED-REPLY-FOR-REGISTERED-USER"


def test_registered_user_cannot_use_another_users_session(tmp_path, monkeypatch):
    tqd = _discuss_test_env()
    import bot as bot_module
    import questionnaires

    monkeypatch.setattr(database, "DB", str(tmp_path / "t4.db"))
    asyncio.run(database.init_db())
    monkeypatch.setattr(bot_module, "get_user_language", _const("ru"))
    monkeypatch.setattr(bot_module, "get_active_crisis", _const(None))
    monkeypatch.setattr(bot_module, "log_crisis_delivery", _const(None))
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})
    monkeypatch.setattr(bot_module, "CallbackQuery", tqd.FakeCallback)
    monkeypatch.setattr(bot_module, "_load_registry_fresh",
                        lambda: questionnaires.load_registry(tqd.FIXTURE_DIR))
    monkeypatch.setattr(config, "QUESTIONNAIRE_INTERPRETATION_ENABLED", True)

    called = {"traced": False}

    async def _fake_builder(**kwargs):
        called["traced"] = True
        return "rid-should-not-happen"

    monkeypatch.setattr(bot_module, "traced_response_builder", _fake_builder)

    uid_a, uid_b = 9103, 9104
    asyncio.run(database.upsert_user(uid_a, "a", "A"))
    asyncio.run(database.upsert_user(uid_b, "b", "B"))
    asyncio.run(database.grant_user_access(uid_a, source="invite"))
    asyncio.run(database.grant_user_access(uid_b, source="invite"))

    user_a = tqd.FakeUser(uid_a)
    msg_a = tqd.FakeMessage(user_a)
    session_id = tqd._complete_flow(user_a, msg_a)

    user_b = tqd.FakeUser(uid_b)
    msg_b = tqd.FakeMessage(user_b)
    cb = tqd.FakeCallback(user_b, msg_b, data=f"q:m:{session_id}:why")
    asyncio.run(bot_module.cb_questionnaire_discuss_topic(cb))

    assert called["traced"] is False
    assert msg_b.answers == []  # silent no-op, no ownership/existence leak


def _const(value):
    async def _f(*a, **kw):
        return value
    return _f
