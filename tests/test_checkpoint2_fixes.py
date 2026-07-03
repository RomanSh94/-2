"""PR 1B-1 checkpoint-2 fixes:

  1. trigger_crisis: protective-factor detection/persistence gated on
     kind == "owner", not merely role != UNKNOWN (no wasted psych-interpretation
     built/stored for a CLINICIAN_TESTER event that only ever gets a minimal
     reviewer payload).
  2. Aggression alert routed through access_control (no raw-text leak / no owner
     alert for UNKNOWN or CLINICIAN_TESTER; RED+aggression never double-alerts).
  4. Invalid DEPLOYMENT_MODE fails closed for has_full_access/a1_allowed, but
     never blocks the crisis path.
  5. pipeline(): no upsert_user/log_moderation/save_message for an unauthorized
     non-RED user; RED still fires regardless of role/access.
  6. Product-access gate on entrypoints via ensure_full_access_or_closed_test.
"""
import asyncio
import types

import pytest

import bot
import access_control as ac


class FakeUser:
    def __init__(self, uid, username="user", first="U"):
        self.id = uid
        self.username = username
        self.first_name = first


class FakeMessage:
    def __init__(self, user, text=""):
        self.from_user = user
        self.text = text
        self.chat = types.SimpleNamespace(id=user.id)
        self.answers = []

    async def answer(self, text, **kw):
        self.answers.append((text, kw))

    async def edit_reply_markup(self, **kw):
        pass


def _async(value=None):
    async def _f(*a, **kw):
        return value
    return _f


def _risk(categories=("suicide",), level="critical", score=100):
    return {"score": score, "level": level, "categories": list(categories)}


# ── shared role config ────────────────────────────────────────────────────────
@pytest.fixture
def role_config(monkeypatch):
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "controlled_clinical_test")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", {10})
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", {20})
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {10: [20]})
    monkeypatch.setattr(ac, "ADMIN_USER_IDS", [999])
    monkeypatch.setattr(bot, "ADMIN_USER_IDS", [999])
    return monkeypatch


# ── item 1: protective-factor gating in trigger_crisis ─────────────────────────
@pytest.fixture
def pf_spies(monkeypatch):
    calls = {"get_recent_messages": 0, "detect_protective_factors": 0,
              "set_crisis_protective_factors": 0}

    async def fake_log_crisis_event(*a, **kw):
        return 7

    async def fake_get_recent_messages(uid, limit=10):
        calls["get_recent_messages"] += 1
        return []

    def fake_detect_protective_factors(text):
        calls["detect_protective_factors"] += 1
        return []

    async def fake_set_crisis_protective_factors(*a, **kw):
        calls["set_crisis_protective_factors"] += 1

    class FakeBotSend:
        async def send_message(self, target_id, text):
            pass

    monkeypatch.setattr(bot, "log_crisis_event", fake_log_crisis_event)
    monkeypatch.setattr(bot, "save_message", _async(None))
    monkeypatch.setattr(bot, "maybe_update_profile", _async(None))
    monkeypatch.setattr(bot, "get_user_message_count", _async(1))
    monkeypatch.setattr(bot, "get_recent_messages", fake_get_recent_messages)
    monkeypatch.setattr(bot, "detect_protective_factors", fake_detect_protective_factors)
    monkeypatch.setattr(bot, "set_crisis_protective_factors", fake_set_crisis_protective_factors)
    monkeypatch.setattr(bot, "push_alert", _async(None))
    monkeypatch.setattr(bot, "admin_alert_text", lambda *a, **kw: "OWNER ALERT")
    monkeypatch.setattr(bot, "bot", FakeBotSend())
    return calls


def test_unknown_red_no_protective_factor_work(role_config, pf_spies):
    user = FakeUser(424242)
    msg = FakeMessage(user)
    asyncio.run(bot.trigger_crisis(msg, user.id, user.username, "RED text", _risk(), "ru"))
    assert pf_spies["get_recent_messages"] == 0
    assert pf_spies["detect_protective_factors"] == 0
    assert pf_spies["set_crisis_protective_factors"] == 0


def test_mapped_tester_red_no_protective_factor_work(role_config, pf_spies):
    user = FakeUser(10)   # CLINICIAN_TESTER_IDS, mapped to reviewer 20
    msg = FakeMessage(user)
    asyncio.run(bot.trigger_crisis(msg, user.id, user.username, "RED text", _risk(), "ru"))
    assert pf_spies["get_recent_messages"] == 0
    assert pf_spies["detect_protective_factors"] == 0
    assert pf_spies["set_crisis_protective_factors"] == 0


def test_owner_red_protective_factor_path_runs_no_unbound_error(role_config, pf_spies):
    user = FakeUser(1)   # OWNER_USER_ID
    msg = FakeMessage(user)
    # Must not raise (UnboundLocalError guard).
    asyncio.run(bot.trigger_crisis(msg, user.id, user.username, "RED text", _risk(), "ru"))
    assert pf_spies["get_recent_messages"] == 1
    assert pf_spies["detect_protective_factors"] == 1


# ── item 4: invalid DEPLOYMENT_MODE fails closed, crisis unaffected ────────────
def test_invalid_mode_denies_owner_full_access(role_config, monkeypatch):
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "broken_typo")
    assert asyncio.run(ac.has_full_access(1)) is False
    assert asyncio.run(ac.a1_allowed(1)) is False


def test_invalid_mode_still_delivers_crisis_screen(role_config, monkeypatch, pf_spies):
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "broken_typo")
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.trigger_crisis(msg, user.id, user.username, "RED text", _risk(), "ru"))
    assert len(msg.answers) == 1


# ── item 5 / item 6: pipeline ordering + product-access gate ───────────────────
@pytest.fixture
def pipeline_spies(monkeypatch, role_config):
    calls = {"upsert_user": 0, "log_moderation": 0, "save_message": 0, "push_alert": 0,
              "trigger_crisis": 0}

    async def fake_trigger_crisis(*a, **kw):
        calls["trigger_crisis"] += 1

    async def fake_upsert_user(*a, **kw):
        calls["upsert_user"] += 1

    async def fake_log_moderation(*a, **kw):
        calls["log_moderation"] += 1

    async def fake_save_message(*a, **kw):
        calls["save_message"] += 1

    async def fake_push_alert(*a, **kw):
        calls["push_alert"] += 1

    monkeypatch.setattr(bot, "trigger_crisis", fake_trigger_crisis)
    monkeypatch.setattr(bot, "upsert_user", fake_upsert_user)
    monkeypatch.setattr(bot, "reset_unanswered", _async(None))
    monkeypatch.setattr(bot, "log_moderation", fake_log_moderation)
    monkeypatch.setattr(bot, "save_message", fake_save_message)
    monkeypatch.setattr(bot, "push_alert", fake_push_alert)
    monkeypatch.setattr(bot, "get_active_crisis", _async(None))
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    return calls


def test_unknown_non_red_gets_closed_test_no_ordinary_persistence(pipeline_spies):
    user = FakeUser(424242)
    msg = FakeMessage(user, "у меня был тяжёлый день на работе")
    asyncio.run(bot.pipeline(msg, msg.text, None, tg_user=user))
    assert pipeline_spies["upsert_user"] == 0
    assert pipeline_spies["log_moderation"] == 0
    assert pipeline_spies["save_message"] == 0
    assert pipeline_spies["trigger_crisis"] == 0
    assert len(msg.answers) == 1   # closed-test message, not product reply


def test_unacknowledged_tester_non_red_gets_tester_ack_screen(pipeline_spies, monkeypatch):
    monkeypatch.setattr(bot, "get_tester_acknowledged", _async(False))
    user = FakeUser(10)
    msg = FakeMessage(user, "у меня был тяжёлый день на работе")
    asyncio.run(bot.pipeline(msg, msg.text, None, tg_user=user))
    assert pipeline_spies["upsert_user"] == 0
    assert len(msg.answers) == 1
    assert "clinical tester" in msg.answers[0][0] or "тестер" in msg.answers[0][0]


def test_unknown_red_still_triggers_crisis_bypassing_gate(pipeline_spies):
    user = FakeUser(424242)
    msg = FakeMessage(user, "я хочу покончить с собой")
    asyncio.run(bot.pipeline(msg, msg.text, None, tg_user=user))
    assert pipeline_spies["trigger_crisis"] == 1
    assert pipeline_spies["upsert_user"] == 0   # gate never reached — RED returned first


def test_owner_non_red_ordinary_persistence_still_works(pipeline_spies, monkeypatch):
    # Full pipeline for OWNER needs many more collaborators stubbed; just prove
    # the gate lets it PAST the closed-test short-circuit by checking upsert_user
    # fires (rest of the pipeline is exercised by the pre-existing test suite).
    user = FakeUser(1)
    msg = FakeMessage(user, "у меня был тяжёлый день на работе")
    # Stub everything downstream of the gate so this stays a narrow ordering test.
    monkeypatch.setattr(bot, "get_emotional_trajectory", _async(types.SimpleNamespace(
        trend="stable", hopelessness_streak=0, yellow_plus_streak=0, messages_analyzed=0)))
    monkeypatch.setattr(bot.dependency_monitor, "record_message", _async(None))
    monkeypatch.setattr(bot.dependency_monitor, "check_dependency", _async(None))
    monkeypatch.setattr(bot, "load_state", _async(None))
    monkeypatch.setattr(bot, "save_state", _async(None))
    monkeypatch.setattr(bot, "monitor_relationship", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "log_router_decision", _async(None))
    monkeypatch.setattr(bot, "maybe_summarize", _async(None))
    monkeypatch.setattr(bot, "build_context", _async(("", [])))
    monkeypatch.setattr(bot, "maybe_update_profile", _async(None))
    monkeypatch.setattr(bot, "get_user_message_count", _async(1))
    monkeypatch.setattr(bot, "check_sudden_improvement", _async(False))

    async def fake_typing(chat_id, action):
        return None
    monkeypatch.setattr(bot.bot, "send_chat_action", fake_typing)

    class FakeCompletion:
        class choices:
            pass

    async def fake_create(*a, **kw):
        msg_obj = types.SimpleNamespace(content="ok, noted")
        choice = types.SimpleNamespace(message=msg_obj)
        return types.SimpleNamespace(choices=[choice])
    monkeypatch.setattr(bot.client.chat.completions, "create", fake_create)

    asyncio.run(bot.pipeline(msg, msg.text, None, tg_user=user))
    assert pipeline_spies["upsert_user"] == 1
    assert pipeline_spies["log_moderation"] == 0   # not medium+ risk text


# ── item 2: aggression alert routing ────────────────────────────────────────────
def test_tester_aggression_non_red_no_owner_alert(pipeline_spies, monkeypatch):
    monkeypatch.setattr(bot, "get_tester_acknowledged", _async(True))
    monkeypatch.setattr(bot, "get_emotional_trajectory", _async(types.SimpleNamespace(
        trend="stable", hopelessness_streak=0, yellow_plus_streak=0, messages_analyzed=0)))
    monkeypatch.setattr(bot.dependency_monitor, "record_message", _async(None))
    monkeypatch.setattr(bot.dependency_monitor, "check_dependency", _async(None))
    monkeypatch.setattr(bot, "load_state", _async(None))
    monkeypatch.setattr(bot, "save_state", _async(None))
    monkeypatch.setattr(bot, "monitor_relationship", lambda *a, **kw: "stop")
    user = FakeUser(10)
    msg = FakeMessage(user, "ненависть переполняет, они поплатятся")
    asyncio.run(bot.pipeline(msg, msg.text, None, tg_user=user))
    assert pipeline_spies["push_alert"] == 0


def test_owner_aggression_non_red_owner_alert_preserved(pipeline_spies, monkeypatch):
    monkeypatch.setattr(bot, "get_emotional_trajectory", _async(types.SimpleNamespace(
        trend="stable", hopelessness_streak=0, yellow_plus_streak=0, messages_analyzed=0)))
    monkeypatch.setattr(bot.dependency_monitor, "record_message", _async(None))
    monkeypatch.setattr(bot.dependency_monitor, "check_dependency", _async(None))
    monkeypatch.setattr(bot, "load_state", _async(None))
    monkeypatch.setattr(bot, "save_state", _async(None))
    monkeypatch.setattr(bot, "monitor_relationship", lambda *a, **kw: "stop")
    user = FakeUser(1)
    msg = FakeMessage(user, "ненависть переполняет, они поплатятся")
    asyncio.run(bot.pipeline(msg, msg.text, None, tg_user=user))
    assert pipeline_spies["push_alert"] == 1


def test_unknown_aggression_non_red_no_owner_alert(pipeline_spies):
    # UNKNOWN never even reaches the aggression check -- the product-access
    # gate returns first. Explicit regression test per checkpoint-2 item 2
    # (was previously only implicitly covered by a different risk text).
    user = FakeUser(424242)
    msg = FakeMessage(user, "ненависть переполняет, они поплатятся")
    asyncio.run(bot.pipeline(msg, msg.text, None, tg_user=user))
    assert pipeline_spies["push_alert"] == 0
    assert pipeline_spies["upsert_user"] == 0   # confirms it was gate-blocked, not just quiet


def test_red_plus_aggression_crisis_screen_no_duplicate_owner_alert(pipeline_spies):
    # A RED text that ALSO matches the aggression category must produce exactly
    # the crisis path (trigger_crisis), never an additional push_alert -- the
    # RED branch returns before the aggression check is ever reached.
    user = FakeUser(1)   # OWNER -- the role most likely to receive an alert
    msg = FakeMessage(user, "хочу всех убить, я хочу покончить с собой")
    asyncio.run(bot.pipeline(msg, msg.text, None, tg_user=user))
    assert pipeline_spies["trigger_crisis"] == 1
    assert pipeline_spies["push_alert"] == 0    # no separate aggression alert fired
    assert pipeline_spies["upsert_user"] == 0   # RED returned before the gate/persistence


def test_should_alert_owner_directly_denies_tester_and_unknown():
    """Item 2's leakage guard, independent of pipeline() ordering: even if a
    future refactor moves the aggression block earlier/later, should_alert_owner
    itself must stay False for CLINICIAN_TESTER/UNKNOWN and True for
    OWNER/CLINICIAN_REVIEWER -- the function this gate depends on is asserted
    directly, not just observed through today's call order."""
    import access_control as ac
    ac_state = {
        "OWNER_USER_ID": 1, "CLINICIAN_TESTER_IDS": {10}, "CLINICIAN_REVIEWER_IDS": {20},
    }
    import pytest as _pytest  # local import keeps this test self-contained
    mp = _pytest.MonkeyPatch()
    try:
        mp.setattr(ac, "OWNER_USER_ID", 1)
        mp.setattr(ac, "CLINICIAN_TESTER_IDS", {10})
        mp.setattr(ac, "CLINICIAN_REVIEWER_IDS", {20})
        assert ac.should_alert_owner(1) is True     # OWNER
        assert ac.should_alert_owner(20) is True     # CLINICIAN_REVIEWER
        assert ac.should_alert_owner(10) is False    # CLINICIAN_TESTER
        assert ac.should_alert_owner(999999) is False  # UNKNOWN
    finally:
        mp.undo()
