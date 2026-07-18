"""Mandatory onboarding gate (spec items A + C).

A user with an ACTIVE first-user onboarding must not reach ANY ordinary
product entrypoint -- command, callback, text, or voice -- while it's in
progress. Two integration points implement this with ONE shared decision
function (bot._onboarding_blocks_ordinary_entry) and ONE shared response
(bot._resume_onboarding_card):

  1. bot.OnboardingGateMiddleware -- OUTER middleware on dp.message and
     dp.callback_query, intercepting EVERY command and EVERY callback before
     any specific handler's filters are evaluated (spec item C: "one reusable
     guard", not scattered per-handler checks). Classification is
     default-deny: anything not in _ONBOARDING_EXEMPT_COMMANDS /
     _ONBOARDING_EXEMPT_CALLBACK_PREFIXES is blocked.

  2. bot.pipeline() -- gates plain text/voice messages itself, AFTER its own
     active-crisis and RED checks. Free text is deliberately EXEMPT from the
     middleware's own judgment (a command/callback is judged there instead)
     because whether a text message is a crisis reply is content/state
     dependent; only pipeline() can safely make that call, and crisis
     handling must always preempt onboarding.

Required ordering for text/voice:

    deterministic risk detection -> active crisis handling -> access gate
    -> onboarding gate -> ordinary product pipeline

Handler-level tests against bot.pipeline / bot.handle_voice / bot.cb_mood /
the middleware wrapping representative real and trivial handlers, with a real
tmp DB (the gate reads onboarding state from the DB) and a fake `bot` double
for the card re-render (no Telegram network).
"""
import asyncio
import types

import pytest

import access_control as ac
import bot
import config
import database

run = asyncio.run


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
        self.voice = types.SimpleNamespace(file_id="v1")
        self.answers = []

    async def answer(self, text, **kw):
        self.answers.append((text, kw))

    async def edit_reply_markup(self, **kw):
        pass


class FakeCallback:
    def __init__(self, user, message, data=""):
        self.from_user = user
        self.message = message
        self.data = data
        self.answered = 0

    async def answer(self, *a, **kw):
        self.answered += 1


class FakeBot:
    """Matches the real aiogram Bot surface used by
    onboarding.send_or_edit_onboarding_card (chat_id/message_id addressed)."""

    def __init__(self):
        self.sent = []
        self.edits = []
        self._next_id = 9000

    def _new_id(self):
        self._next_id += 1
        return self._next_id

    async def send_photo(self, chat_id, photo, caption, reply_markup=None):
        mid = self._new_id()
        self.sent.append(("photo", chat_id, caption, reply_markup))
        return types.SimpleNamespace(chat=types.SimpleNamespace(id=chat_id), message_id=mid)

    async def send_message(self, chat_id, text, reply_markup=None):
        mid = self._new_id()
        self.sent.append(("text", chat_id, text, reply_markup))
        return types.SimpleNamespace(chat=types.SimpleNamespace(id=chat_id), message_id=mid)

    async def edit_message_media(self, chat_id, message_id, media, reply_markup=None):
        self.edits.append(("media", chat_id, message_id, media.caption, reply_markup))

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
        self.edits.append(("text", chat_id, message_id, text, reply_markup))

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        pass

    async def send_chat_action(self, chat_id, action):
        pass


def _async(value=None):
    async def _f(*a, **kw):
        return value
    return _f


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    run(database.init_db())
    return database


@pytest.fixture
def fake_bot(monkeypatch):
    fb = FakeBot()
    monkeypatch.setattr(bot, "bot", fb)
    return fb


@pytest.fixture(autouse=True)
def _pin(monkeypatch, tmp_db, fake_bot):
    monkeypatch.setattr(config, "FIRST_USER_ONBOARDING_ENABLED", True)
    monkeypatch.setattr(config, "PRIVACY_POLICY_URL", "")
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})
    monkeypatch.setattr(bot, "get_active_crisis", _async(None))
    monkeypatch.setattr(bot, "log_crisis_delivery", _async(None))
    monkeypatch.setattr(bot, "reset_unanswered", _async(None))
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    yield


def _spy_upsert(monkeypatch, calls):
    async def fake_upsert_user(*a, **kw):
        calls["upsert_user"] = calls.get("upsert_user", 0) + 1
    monkeypatch.setattr(bot, "upsert_user", fake_upsert_user)


def _begin_active_onboarding(uid):
    run(database.grant_user_access(uid, source="invite"))
    run(database.start_or_get_onboarding(uid, "v1"))
    return run(database.get_active_onboarding_state(uid))


# ── A: ordinary text cannot bypass active onboarding ──────────────────────────
def test_ordinary_text_blocked_while_onboarding_active(monkeypatch, fake_bot):
    uid = 501
    _begin_active_onboarding(uid)
    calls = {}
    _spy_upsert(monkeypatch, calls)
    fake_bot.sent.clear()  # clear the initial-card send from start_or_get_onboarding setup
    msg = FakeMessage(FakeUser(uid), "у меня был тяжёлый день")
    run(bot.pipeline(msg, msg.text, None))
    assert calls.get("upsert_user", 0) == 0            # never entered ordinary pipeline
    assert not msg.answers                              # no ordinary reply sent to the message
    # The onboarding card was re-shown (edited in place) instead.
    assert fake_bot.edits or fake_bot.sent
    st = run(database.get_active_onboarding_state(uid))
    assert st["current_step"] == 1                       # gate never advances state


# ── A: voice cannot bypass active onboarding ──────────────────────────────────
def test_voice_blocked_while_onboarding_active(monkeypatch, fake_bot):
    uid = 502
    _begin_active_onboarding(uid)
    calls = {}
    _spy_upsert(monkeypatch, calls)

    async def fake_transcribe(*a, **kw):
        return "у меня был тяжёлый день"
    monkeypatch.setattr(bot, "transcribe_voice", fake_transcribe)

    msg = FakeMessage(FakeUser(uid))
    run(bot.handle_voice(msg, None))
    assert calls.get("upsert_user", 0) == 0
    # handle_voice always echoes the transcript itself (not "ordinary
    # conversation") -- but pipeline() must not have produced a product reply.
    st = run(database.get_active_onboarding_state(uid))
    assert st["current_step"] == 1


def _via_message_gate(event, handler, *extra_args):
    """Route `event` through the REAL OnboardingGateMiddleware wrapping
    `handler`, exactly as dp.message would at dispatch time. Returns the
    number of times the wrapped handler actually ran (0 = blocked)."""
    mw = bot.OnboardingGateMiddleware(bot._message_is_onboarding_exempt, kind="message")
    called = {"n": 0}

    async def _handler(ev, data):
        called["n"] += 1
        return await handler(ev, *extra_args)

    run(mw(_handler, event, {}))
    return called["n"]


def _via_callback_gate(event, handler, *extra_args):
    mw = bot.OnboardingGateMiddleware(bot._callback_is_onboarding_exempt, kind="callback")
    called = {"n": 0}

    async def _handler(ev, data):
        called["n"] += 1
        return await handler(ev, *extra_args)

    run(mw(_handler, event, {}))
    return called["n"]


# ── A/C: an old mood callback cannot bypass active onboarding ────────────────
def test_old_mood_callback_blocked_while_onboarding_active(monkeypatch, fake_bot):
    """Routed through the REAL middleware (not a hand-rolled inline check in
    cb_mood -- that inline check was removed in favor of the single shared
    middleware, spec item C)."""
    uid = 503
    _begin_active_onboarding(uid)
    calls = {}
    _spy_upsert(monkeypatch, calls)
    fake_bot.sent.clear()
    msg = FakeMessage(FakeUser(uid))
    cb = FakeCallback(FakeUser(uid), msg, data="mood:0")
    ran = _via_callback_gate(cb, bot.cb_mood, None)
    assert ran == 0                                       # handler never actually ran
    assert cb.answered == 1                              # callback still acknowledged
    assert calls.get("upsert_user", 0) == 0               # never reached pipeline
    assert fake_bot.edits or fake_bot.sent                # card re-shown instead
    st = run(database.get_active_onboarding_state(uid))
    assert st["current_step"] == 1


# ── A: active crisis still reaches the existing crisis flow ──────────────────
def test_active_crisis_preempts_onboarding_gate(monkeypatch, fake_bot):
    uid = 504
    _begin_active_onboarding(uid)
    calls = {"onboarding_gate": 0}

    async def spy_gate(u):
        calls["onboarding_gate"] += 1
        return True  # would block if reached -- proves it's never even called
    monkeypatch.setattr(bot, "_onboarding_blocks_ordinary_entry", spy_gate)
    monkeypatch.setattr(bot, "get_active_crisis", _async((77, 0, "ru")))
    monkeypatch.setattr(bot, "is_reassuring", lambda *a, **kw: False)
    monkeypatch.setattr(bot, "save_message", _async(None))
    monkeypatch.setattr(bot, "send_crisis", _async(None))

    msg = FakeMessage(FakeUser(uid), "мне плохо")
    run(bot.pipeline(msg, msg.text, None))
    # The active-crisis branch returns BEFORE the access gate / onboarding gate
    # are ever reached.
    assert calls["onboarding_gate"] == 0


def test_red_risk_crisis_preempts_onboarding_gate(monkeypatch, fake_bot):
    uid = 505
    _begin_active_onboarding(uid)
    calls = {"onboarding_gate": 0, "trigger_crisis": 0}

    async def spy_gate(u):
        calls["onboarding_gate"] += 1
        return True
    monkeypatch.setattr(bot, "_onboarding_blocks_ordinary_entry", spy_gate)

    async def spy_trigger(*a, **kw):
        calls["trigger_crisis"] += 1
    monkeypatch.setattr(bot, "trigger_crisis", spy_trigger)

    msg = FakeMessage(FakeUser(uid), "я хочу покончить с собой")
    run(bot.pipeline(msg, msg.text, None))
    assert calls["trigger_crisis"] == 1
    assert calls["onboarding_gate"] == 0   # RED returns before the gate is reached


# ── A: after completion, text/voice/mood behave exactly as before ────────────
def test_text_reaches_ordinary_pipeline_after_onboarding_completed(monkeypatch, fake_bot):
    uid = 506
    run(database.grant_user_access(uid, source="invite"))
    run(database.start_or_get_onboarding(uid, "v1"))
    run(database.skip_onboarding_to_privacy(uid, "v1"))
    run(database.complete_onboarding(uid, "v1", privacy_notice_version="v1"))
    calls = {}
    _spy_upsert(monkeypatch, calls)

    # Full downstream stub set (same as test_checkpoint2_fixes.py's
    # test_owner_non_red_ordinary_persistence_still_works) so the REAL
    # pipeline can run end-to-end without a real DB/LLM.
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
    monkeypatch.setattr(bot, "log_moderation", _async(None))
    monkeypatch.setattr(bot, "save_message", _async(None))
    monkeypatch.setattr(bot, "push_alert", _async(None))

    class _Choice:
        def __init__(self):
            self.message = types.SimpleNamespace(content="ok, noted")

    async def fake_create(*a, **kw):
        return types.SimpleNamespace(choices=[_Choice()])
    monkeypatch.setattr(bot.client.chat.completions, "create", fake_create)

    msg = FakeMessage(FakeUser(uid), "у меня был тяжёлый день на работе")
    run(bot.pipeline(msg, msg.text, None))
    assert calls.get("upsert_user", 0) == 1   # ordinary pipeline WAS reached
    assert not fake_bot.sent and not fake_bot.edits  # no onboarding card involved


def test_mood_callback_reaches_pipeline_after_onboarding_completed(monkeypatch, fake_bot):
    uid = 507
    run(database.grant_user_access(uid, source="invite"))
    run(database.start_or_get_onboarding(uid, "v1"))
    run(database.skip_onboarding_to_privacy(uid, "v1"))
    run(database.complete_onboarding(uid, "v1", privacy_notice_version="v1"))
    calls = {"pipeline": 0}

    async def spy_pipeline(*a, **kw):
        calls["pipeline"] += 1
    monkeypatch.setattr(bot, "pipeline", spy_pipeline)

    msg = FakeMessage(FakeUser(uid))
    cb = FakeCallback(FakeUser(uid), msg, data="mood:0")
    ran = _via_callback_gate(cb, bot.cb_mood, None)
    assert ran == 1                             # middleware let the handler run
    assert calls["pipeline"] == 1              # mood tap reached the ordinary pipeline
    assert not fake_bot.sent and not fake_bot.edits


# ── A: privacy self-service commands remain available during active onboarding ─
def test_privacy_export_command_available_during_active_onboarding(monkeypatch, fake_bot):
    uid = 508
    _begin_active_onboarding(uid)
    monkeypatch.setattr(bot, "export_all_personal_data", _async({"users": []}))
    msg = FakeMessage(FakeUser(uid), "/privacy_export_all")
    run(bot.cmd_privacy_export_all(msg))
    # The command handler is not routed through pipeline()/the onboarding gate
    # at all -- it must still respond normally.
    assert msg.answers


def test_privacy_export_command_passes_the_middleware_while_active(monkeypatch, fake_bot):
    """Same scenario, but routed through the REAL middleware -- proves
    /privacy_export_all is exempt AT THE DISPATCH LAYER, not merely reachable
    because nothing calls it through pipeline()."""
    uid = 5081
    _begin_active_onboarding(uid)
    monkeypatch.setattr(bot, "export_all_personal_data", _async({"users": []}))
    msg = FakeMessage(FakeUser(uid), "/privacy_export_all")
    ran = _via_message_gate(msg, bot.cmd_privacy_export_all)
    assert ran == 1
    assert msg.answers


# ── C: exhaustive command/callback classification inventory ──────────────────
# Every command and callback namespace registered in bot.py, classified. See
# bot.py's "C: ONE reusable guard" comment for the exempt-category reasoning.
_BLOCKED_COMMANDS = [
    "profile", "profile_reset", "memory", "mute", "mute_today", "mute_week",
    "unmute", "checkin", "checkin_8", "checkin_10", "checkin_12", "checkin_18",
    "checkin_20", "checkin_off", "emotion", "journal_cancel", "cbt", "report",
    "journal", "journal_settings", "time", "journal_export", "journal_delete",
    "dass21", "questionnaire", "menu",
]
_EXEMPT_COMMANDS = [
    "start", "forget_all", "privacy_export_all", "privacy_delete_all", "help",
    "unblock", "review_pack",
]

_BLOCKED_CALLBACK_DATA = [
    "mood:0", "before:p1:s1:ru:5", "after:5", "quality:1",
    "profile:reset", "jhub:emotion", "jset:tz", "jtz:+3", "checkin:8",
    "q:l", "q:c:stress", "q:i:demo", "q:d:demo", "q:s:demo", "q:a:1:0:a1",
    "q:b:1", "q:p:1", "q:x:1", "q:r:1", "q:k:1", "q:e:1", "q:o:1",
    "q:m:1", "q:m:1:why",
    "tests:hub", "journals:hub", "results:hub", "about:hub", "menu:back",
    "emotion:map",
]
_EXEMPT_CALLBACK_DATA = [
    "tester_ack:yes", "crisis:safe:1", "crisis:screen:1", "onb:v1:next:2",
    "onb:v1:skip", "onb:v1:start", "onb:v1:privacy", "forget:1", "forget:confirm",
    "privacy_delete:1", "privacy:hub",
]


@pytest.mark.parametrize("cmd", _BLOCKED_COMMANDS)
def test_command_classified_blocked(cmd):
    msg = FakeMessage(FakeUser(1), f"/{cmd}")
    assert bot._message_is_onboarding_exempt(msg) is False, cmd


@pytest.mark.parametrize("cmd", _EXEMPT_COMMANDS)
def test_command_classified_exempt(cmd):
    msg = FakeMessage(FakeUser(1), f"/{cmd}")
    assert bot._message_is_onboarding_exempt(msg) is True, cmd


def test_plain_text_and_voice_are_exempt_from_the_middleware_itself():
    """Not a security hole -- see module docstring: free text/voice is judged
    by pipeline() instead, AFTER crisis checks, not by this middleware."""
    text_msg = FakeMessage(FakeUser(1), "у меня был тяжёлый день")
    assert bot._message_is_onboarding_exempt(text_msg) is True
    voice_msg = FakeMessage(FakeUser(1), "")
    voice_msg.text = None
    assert bot._message_is_onboarding_exempt(voice_msg) is True


@pytest.mark.parametrize("data", _BLOCKED_CALLBACK_DATA)
def test_callback_classified_blocked(data):
    assert bot._callback_is_onboarding_exempt(FakeCallback(FakeUser(1), None, data)) is False, data


@pytest.mark.parametrize("data", _EXEMPT_CALLBACK_DATA)
def test_callback_classified_exempt(data):
    assert bot._callback_is_onboarding_exempt(FakeCallback(FakeUser(1), None, data)) is True, data


# ── C: the middleware is actually REGISTERED on the real dispatcher ──────────
def test_onboarding_gate_middleware_registered_on_message_and_callback():
    assert any(isinstance(m, bot.OnboardingGateMiddleware) for m in bot.dp.message.outer_middleware)
    assert any(isinstance(m, bot.OnboardingGateMiddleware) for m in bot.dp.callback_query.outer_middleware)


# ── C: end-to-end proof for one representative BLOCKED command/callback per
# major category, using a trivial recording handler (the classification test
# above already proves EVERY namespace; this proves the MIDDLEWARE actually
# stops dispatch, with the right side effects: card re-shown, callback
# answered, handler never runs). ─────────────────────────────────────────────
@pytest.mark.parametrize("data", [
    "mood:0", "emotion:map", "menu:back", "q:s:demo_q", "jhub:emotion",
    "profile:reset", "before:p1:s1:ru:5", "checkin:8", "jset:tz",
])
def test_blocked_callback_categories_never_reach_handler(monkeypatch, fake_bot, data):
    uid = 509
    _begin_active_onboarding(uid)
    fake_bot.sent.clear()
    cb = FakeCallback(FakeUser(uid), FakeMessage(FakeUser(uid)), data=data)

    async def trivial_handler(event, _state=None):
        raise AssertionError("handler must not run while onboarding is active")

    ran = _via_callback_gate(cb, trivial_handler, None)
    assert ran == 0
    assert cb.answered == 1
    assert fake_bot.edits or fake_bot.sent


@pytest.mark.parametrize("data", [
    "crisis:safe:1", "onb:v1:next:2", "tester_ack:yes", "forget:1",
    "privacy_delete:1", "privacy:hub",
])
def test_exempt_callback_categories_reach_handler_even_while_active(monkeypatch, fake_bot, data):
    uid = 510
    _begin_active_onboarding(uid)
    cb = FakeCallback(FakeUser(uid), FakeMessage(FakeUser(uid)), data=data)

    async def trivial_handler(event, _state=None):
        return "ran"

    ran = _via_callback_gate(cb, trivial_handler, None)
    assert ran == 1


@pytest.mark.parametrize("cmd", [
    "profile", "journal", "menu", "dass21", "questionnaire", "mute", "time",
    "emotion", "cbt", "report", "checkin",
])
def test_blocked_command_categories_never_reach_handler(monkeypatch, fake_bot, cmd):
    uid = 511
    _begin_active_onboarding(uid)
    fake_bot.sent.clear()
    msg = FakeMessage(FakeUser(uid), f"/{cmd}")

    async def trivial_handler(event):
        raise AssertionError("handler must not run while onboarding is active")

    ran = _via_message_gate(msg, trivial_handler)
    assert ran == 0
    assert fake_bot.edits or fake_bot.sent


@pytest.mark.parametrize("cmd", ["start", "help", "unblock", "review_pack"])
def test_exempt_command_categories_reach_handler_even_while_active(monkeypatch, fake_bot, cmd):
    uid = 512
    _begin_active_onboarding(uid)
    msg = FakeMessage(FakeUser(uid), f"/{cmd}")

    async def trivial_handler(event):
        return "ran"

    ran = _via_message_gate(msg, trivial_handler)
    assert ran == 1
