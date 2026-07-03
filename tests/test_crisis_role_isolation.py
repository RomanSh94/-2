"""PR 1B-1 checkpoint item 2/3 — trigger_crisis role-aware bookkeeping/alerts,
tested against the REAL bot.trigger_crisis (not a reimplementation).

Proves, with spies on the real DB/alert functions:
  - crisis screen delivery is unconditional and identical regardless of role;
  - UNKNOWN gets NO save_message/maybe_update_profile (no ordinary memory/profile
    building for an uninvited person) and NO owner/reviewer alert;
  - a KNOWN role (OWNER) gets save_message/maybe_update_profile exactly ONCE
    (not duplicated) and its existing alert behavior;
  - alert-routing failures never propagate past the crisis send (best-effort).
"""
import asyncio
import types

import pytest

import bot
import access_control as ac


class FakeUser:
    def __init__(self, uid, username="user"):
        self.id = uid
        self.username = username


class FakeMessage:
    def __init__(self, user):
        self.from_user = user
        self.chat = types.SimpleNamespace(id=user.id)
        self.answers = []

    async def answer(self, text, **kw):
        self.answers.append((text, kw))


def _risk():
    return {"score": 100, "level": "critical", "categories": ["suicide"]}


@pytest.fixture
def spies(monkeypatch):
    calls = {"save_message": 0, "maybe_update_profile": 0, "get_recent_messages": 0,
             "owner_sends": [], "reviewer_sends": []}

    async def fake_log_crisis_event(*a, **kw):
        return 7

    async def fake_save_message(*a, **kw):
        calls["save_message"] += 1

    async def fake_maybe_update_profile(*a, **kw):
        calls["maybe_update_profile"] += 1

    async def fake_get_user_message_count(uid):
        return 1

    async def fake_get_recent_messages(uid, limit=10):
        calls["get_recent_messages"] += 1
        return []

    def fake_detect_protective_factors(text):
        return []

    async def fake_push_alert(*a, **kw):
        pass

    def fake_admin_alert_text(*a, **kw):
        return "OWNER ALERT TEXT"

    class FakeBotSend:
        async def send_message(self, target_id, text):
            # distinguish by which target list called it via closures below
            calls.setdefault("_raw_sends", []).append((target_id, text))

    fake_bot = FakeBotSend()

    monkeypatch.setattr(bot, "log_crisis_event", fake_log_crisis_event)
    monkeypatch.setattr(bot, "save_message", fake_save_message)
    monkeypatch.setattr(bot, "maybe_update_profile", fake_maybe_update_profile)
    monkeypatch.setattr(bot, "get_user_message_count", fake_get_user_message_count)
    monkeypatch.setattr(bot, "get_recent_messages", fake_get_recent_messages)
    monkeypatch.setattr(bot, "detect_protective_factors", fake_detect_protective_factors)
    monkeypatch.setattr(bot, "set_crisis_protective_factors", lambda *a, **kw: _noop())
    monkeypatch.setattr(bot, "push_alert", fake_push_alert)
    monkeypatch.setattr(bot, "admin_alert_text", fake_admin_alert_text)
    monkeypatch.setattr(bot, "bot", fake_bot)

    # send_crisis (the screen delivery ladder) is exercised for real up to the
    # point of an actual Telegram call, which we stub via message.answer.
    return calls


async def _noop():
    return None


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "controlled_clinical_test")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", {20})
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})
    monkeypatch.setattr(ac, "ADMIN_USER_IDS", [999])
    monkeypatch.setattr(bot, "ADMIN_USER_IDS", [999])


def test_unknown_red_delivers_screen_no_bookkeeping_no_alert(spies):
    user = FakeUser(424242)
    msg = FakeMessage(user)
    asyncio.run(bot.trigger_crisis(msg, user.id, user.username, "RED text", _risk(), "ru"))

    assert len(msg.answers) == 1                     # crisis screen delivered
    assert spies["save_message"] == 0                 # no ordinary memory
    assert spies["maybe_update_profile"] == 0          # no profile building
    assert spies.get("_raw_sends", []) == []           # no owner/reviewer alert at all


def test_owner_red_delivers_screen_and_bookkeeping_exactly_once(spies):
    user = FakeUser(1)   # OWNER_USER_ID
    msg = FakeMessage(user)
    asyncio.run(bot.trigger_crisis(msg, user.id, user.username, "RED text", _risk(), "ru"))

    assert len(msg.answers) == 1                     # crisis screen delivered
    assert spies["save_message"] == 1                 # exactly once, not duplicated
    assert spies["maybe_update_profile"] == 1          # exactly once, not duplicated
    assert len(spies.get("_raw_sends", [])) == 1       # owner alert sent once
    assert spies["_raw_sends"][0][0] == 999            # to the configured admin id


def test_alert_routing_exception_never_reaches_the_caller(spies, monkeypatch):
    # Even if role resolution blows up mid-alert-routing, trigger_crisis must not
    # raise -- the screen was already delivered before this code runs.
    def _boom(uid):
        raise RuntimeError("resolver broke")
    monkeypatch.setattr(ac, "resolve_role", _boom)

    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.trigger_crisis(msg, user.id, user.username, "RED text", _risk(), "ru"))
    assert len(msg.answers) == 1                     # screen still delivered
    assert spies.get("_raw_sends", []) == []           # fail-closed: no alert sent
