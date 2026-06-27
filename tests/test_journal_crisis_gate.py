"""PR1 — active-crisis gate for journals + delivery-first crisis + uid fix.

These are handler-level tests: they import bot.py (dummy creds come from
conftest.py) and drive the real handlers with lightweight fakes, monkeypatching
the DB/alert side-effects. The point is the SAFETY wiring, not aiogram itself.
"""
import asyncio
import types

import pytest

import bot
from crisis_protocol import get_hotline


# ── lightweight aiogram fakes ─────────────────────────────────────────────────
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
        self.answers = []          # list of (text, kwargs)

    async def answer(self, text, **kw):
        self.answers.append((text, kw))

    async def edit_reply_markup(self, **kw):
        pass


class FakeCallback:
    def __init__(self, user, message, data=""):
        self.from_user = user
        self.message = message
        self.data = data

    async def answer(self, *a, **kw):
        pass


class FakeFSM:
    def __init__(self):
        self._data = {}
        self._state = None

    async def get_data(self):
        return dict(self._data)

    async def update_data(self, **kw):
        self._data.update(kw)

    async def set_state(self, s):
        self._state = s

    async def get_state(self):
        return self._state

    async def clear(self):
        self._data = {}
        self._state = None


def _async(value=None):
    async def _f(*a, **kw):
        return value
    return _f


@pytest.fixture
def patch_common(monkeypatch):
    """Neutralise every DB/alert side-effect so handlers run in isolation."""
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    monkeypatch.setattr(bot, "log_checkin", _async(None))
    monkeypatch.setattr(bot, "save_emotion_entry", _async(None))
    monkeypatch.setattr(bot, "save_cbt_entry", _async(None))
    # journal_guard's active-crisis screen now goes through send_crisis → keep the
    # delivery-log write out of the real DB during these unit tests.
    monkeypatch.setattr(bot, "log_crisis_delivery", _async(None))
    # default: no active crisis (overridden per-test)
    monkeypatch.setattr(bot, "get_active_crisis", _async(None))
    return monkeypatch


# ── delivery-first: crisis screen survives post-screen failures ───────────────
def test_screen_first_survives_bookkeeping_failure(monkeypatch):
    order = []
    monkeypatch.setattr(bot, "log_crisis_event", _async(7))

    async def boom_profile(*a, **kw):
        order.append("profile")
        raise RuntimeError("DB down")

    async def boom_alert(*a, **kw):
        order.append("alert")
        raise RuntimeError("webhook/email down")

    async def ok_save(*a, **kw):
        order.append("save")

    monkeypatch.setattr(bot, "save_message", ok_save)
    monkeypatch.setattr(bot, "maybe_update_profile", boom_profile)
    monkeypatch.setattr(bot, "get_user_message_count", _async(5))
    monkeypatch.setattr(bot, "get_recent_messages", _async([]))
    monkeypatch.setattr(bot, "detect_protective_factors", lambda t: [])
    monkeypatch.setattr(bot, "set_crisis_protective_factors", _async(None))
    monkeypatch.setattr(bot, "push_alert", boom_alert)

    user = FakeUser(42)
    msg = FakeMessage(user, "test crisis text")
    risk = {"score": 100, "level": "critical", "categories": ["suicide"]}

    # Must NOT raise, despite profile + alert blowing up.
    asyncio.run(bot.trigger_crisis(msg, 42, "user", "test crisis text", risk, "ru"))

    # The crisis screen was delivered, and it carries the real hotline number.
    assert len(msg.answers) == 1
    assert get_hotline("ru")["primary"] in msg.answers[0][0]
    # Delivery happened BEFORE the (failing) profile refresh ran.
    assert order and order[0] == "save"      # save ran after the screen
    assert "profile" in order                # bookkeeping attempted, then failed silently


# ── uid fix: evening check-in button enters journal under the USER's id ───────
def test_checkin_button_uses_real_user_id_not_bot(patch_common):
    seen = {}

    async def fake_active(uid):
        seen["uid"] = uid
        return None
    patch_common.setattr(bot, "get_active_crisis", fake_active)

    botuser = FakeUser(999, username="thebot")     # message.from_user is the bot
    realuser = FakeUser(42, username="real")
    msg = FakeMessage(botuser)
    cb = FakeCallback(realuser, msg, data="checkin:evening:emotion_journal")
    fsm = FakeFSM()

    asyncio.run(bot.cb_checkin(cb, fsm))

    # The gate checked the REAL user's id, not the bot's.
    assert seen["uid"] == 42
    # No active crisis → journal actually started under the real user.
    assert fsm._state == bot.EmotionJournal.active


def test_checkin_button_active_crisis_shows_screen_not_journal(patch_common):
    """The required case: evening button while a crisis is active → crisis
    screen, journal NOT entered, gate keyed on the real uid."""
    seen = {}

    async def fake_active(uid):
        seen["uid"] = uid
        return (7, 1, "ru") if uid == 42 else None    # active stage-1 for the user
    patch_common.setattr(bot, "get_active_crisis", fake_active)

    botuser = FakeUser(999)
    realuser = FakeUser(42)
    msg = FakeMessage(botuser)
    cb = FakeCallback(realuser, msg, data="checkin:evening:emotion_journal")
    fsm = FakeFSM()

    asyncio.run(bot.cb_checkin(cb, fsm))

    assert seen["uid"] == 42                         # keyed on the user, not the bot
    assert fsm._state is None                        # journal NOT entered
    assert len(msg.answers) == 1
    assert get_hotline("ru")["primary"] in msg.answers[0][0]   # current crisis screen


# ── entry command directly: /emotion during active crisis → screen, no FSM ────
def test_emotion_entry_blocked_by_active_crisis(patch_common):
    patch_common.setattr(bot, "get_active_crisis", _async((7, 0, "ru")))
    user = FakeUser(42)
    msg = FakeMessage(user)
    fsm = FakeFSM()
    asyncio.run(bot.cmd_emotion(msg, fsm))
    assert fsm._state is None
    assert len(msg.answers) == 1
    assert get_hotline("ru")["primary"] in msg.answers[0][0]


def test_emotion_entry_ok_when_no_crisis(patch_common):
    user = FakeUser(42)
    msg = FakeMessage(user)
    fsm = FakeFSM()
    asyncio.run(bot.cmd_emotion(msg, fsm))
    assert fsm._state == bot.EmotionJournal.active


# ── step handler: active crisis mid-journal → screen, FSM cleared, no save ─────
def test_emotion_step_active_crisis_aborts_and_no_save(patch_common):
    saved = {"n": 0}

    async def spy_save(*a, **kw):
        saved["n"] += 1
    patch_common.setattr(bot, "save_emotion_entry", spy_save)
    patch_common.setattr(bot, "get_active_crisis", _async((7, 2, "ru")))

    user = FakeUser(42)
    msg = FakeMessage(user, "что произошло")        # neutral text, but crisis active
    fsm = FakeFSM()
    asyncio.run(fsm.set_state(bot.EmotionJournal.active))
    asyncio.run(fsm.update_data(jstep=0, jdata={}, orange=False, nudged=False))

    asyncio.run(bot.emotion_step(msg, fsm))

    assert fsm._state is None                        # journal aborted
    assert saved["n"] == 0                           # nothing saved
    assert get_hotline("ru")["primary"] in msg.answers[-1][0]   # stage-2 screen shown


def test_emotion_step_red_text_triggers_crisis_no_save(patch_common):
    saved = {"n": 0}
    crisis = {"n": 0}

    async def spy_save(*a, **kw):
        saved["n"] += 1

    async def spy_crisis(*a, **kw):
        crisis["n"] += 1
    patch_common.setattr(bot, "save_emotion_entry", spy_save)
    patch_common.setattr(bot, "trigger_crisis", spy_crisis)
    # no active crisis; RED comes from the text itself
    user = FakeUser(42)
    msg = FakeMessage(user, "я хочу покончить с собой")
    fsm = FakeFSM()
    asyncio.run(fsm.set_state(bot.EmotionJournal.active))
    asyncio.run(fsm.update_data(jstep=0, jdata={}, orange=False, nudged=False))

    asyncio.run(bot.emotion_step(msg, fsm))

    assert crisis["n"] == 1
    assert saved["n"] == 0
    assert fsm._state is None


def test_emotion_step_safe_text_advances(patch_common):
    user = FakeUser(42)
    msg = FakeMessage(user, "поругался с коллегой на работе")
    fsm = FakeFSM()
    asyncio.run(fsm.set_state(bot.EmotionJournal.active))
    asyncio.run(fsm.update_data(jstep=0, jdata={}, orange=False, nudged=False))

    asyncio.run(bot.emotion_step(msg, fsm))

    assert fsm._state == bot.EmotionJournal.active   # still journaling
    assert fsm._data["jstep"] == 1                   # advanced to next field


# ── RED text in a step DURING an active crisis: current screen, no new event ───
def test_active_crisis_plus_red_in_step_keeps_stage_no_second_event(patch_common):
    """Active stage-2 crisis + a NEW red phrase typed in a journal step. The
    active-crisis check must intercept BEFORE the RED->trigger_crisis branch, so:
    the CURRENT (stage-2) screen is shown, no second crisis_event is created
    (stage is NOT reset to 0), trigger_crisis is never called, nothing saved."""
    logged = {"n": 0}
    saved = {"n": 0}
    crisis = {"n": 0}

    async def spy_log(*a, **kw):
        logged["n"] += 1
        return 99
    async def spy_save(*a, **kw):
        saved["n"] += 1
    async def spy_crisis(*a, **kw):
        crisis["n"] += 1

    patch_common.setattr(bot, "log_crisis_event", spy_log)
    patch_common.setattr(bot, "save_emotion_entry", spy_save)
    patch_common.setattr(bot, "trigger_crisis", spy_crisis)
    patch_common.setattr(bot, "get_active_crisis", _async((7, 2, "ru")))   # stage 2

    user = FakeUser(42)
    msg = FakeMessage(user, "я хочу покончить с собой")    # RED phrase in the step
    fsm = FakeFSM()
    asyncio.run(fsm.set_state(bot.EmotionJournal.active))
    asyncio.run(fsm.update_data(jstep=0, jdata={}, orange=False, nudged=False))

    asyncio.run(bot.emotion_step(msg, fsm))

    assert crisis["n"] == 0          # RED branch NOT reached → no new crisis flow
    assert logged["n"] == 0          # no second crisis_event (no stage reset to 0)
    assert saved["n"] == 0           # nothing saved
    assert fsm._state is None        # journal aborted
    # The CURRENT stage-2 screen was shown (not the stage-0 entry screen).
    from crisis_protocol import crisis_screen
    assert msg.answers[-1][0] == crisis_screen(2, "ru", 7)[0]


# ── gate never spawns a second crisis_event when one is already active ─────────
def test_active_crisis_does_not_spawn_second_event(patch_common):
    logged = {"n": 0}

    async def spy_log(*a, **kw):
        logged["n"] += 1
        return 7
    patch_common.setattr(bot, "log_crisis_event", spy_log)
    patch_common.setattr(bot, "get_active_crisis", _async((7, 0, "ru")))

    user = FakeUser(42)
    msg = FakeMessage(user, "что произошло")
    decision, _ = asyncio.run(bot.journal_guard(msg, 42, "ru", "что произошло", "user"))
    assert decision == "crisis"
    assert logged["n"] == 0          # reused the existing event, no new one
