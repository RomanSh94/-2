"""Round 4 — real aiogram dispatcher/filter regression for the journal FSM
navigation-escape fix.

Every other test in this suite (test_navigation.py, test_journals.py, ...)
calls handler functions DIRECTLY (asyncio.run(bot.emotion_step(fake_msg,
fake_state))). That style cannot prove this round's fix, because the bug it
fixes was a REGISTRATION-ORDER / FILTER-PRECEDENCE bug: aiogram's Dispatcher
resolves @dp.message(...) handlers strictly in the order they were
registered, and matches the FIRST whose whole filter chain passes. Before
this round, emotion_step/cbt_step were registered with a bare F.text filter
(no exclusion), so an active journal FSM unconditionally intercepted every
text message -- including persistent-lower-menu button presses -- before the
dispatcher ever reached the later-registered lower-menu handlers. Calling
emotion_step(...) directly always "works" and would never have caught this;
only feeding a real Update through the real bot.dp proves the fix holds.

Mechanics: builds a real aiogram Update/Message via
Update.model_validate(..., context={"bot": bot.bot}) and feeds it through
bot.dp.feed_update(bot.bot, update) -- the exact entrypoint aiogram polling
uses in production. A minimal stub Session (overriding only
BaseSession.make_request) replaces bot.bot's real HTTP session so handlers'
message.answer(...)/callback.answer(...) calls are recorded instead of
hitting the network, and FSM state is pre-seeded/inspected through
bot.dp.fsm.get_context(...) -- the SAME MemoryStorage-backed FSMContext the
real dispatch path resolves for that (bot, chat, user).
"""
import asyncio
import itertools
import time

import pytest

import bot
import access_control as ac
import crisis_protocol
import navigation
import questionnaire_ux
from aiogram.client.session.base import BaseSession
from aiogram.methods.base import Response
from aiogram.types import Update

USER_ID = 555111

_id_counter = itertools.count(1)


class _StubSession(BaseSession):
    """Records every outgoing Telegram method instead of making a real HTTP
    call, and hands back the minimal valid response its return type needs.
    What a handler actually SENDS is the only thing under test here."""

    def __init__(self):
        super().__init__()
        self.sent = []

    async def close(self):
        pass

    async def make_request(self, bot_, method, timeout=None):
        self.sent.append(method)
        returning = method.__returning__
        if returning is bool:
            data = {"ok": True, "result": True}
        else:
            data = {"ok": True, "result": {
                "message_id": next(_id_counter),
                "date": int(time.time()),
                "chat": {"id": USER_ID, "type": "private"},
                "text": getattr(method, "text", "") or "",
            }}
        resp = Response[returning].model_validate(data, context={"bot": bot_})
        return resp.result

    async def stream_content(self, *a, **kw):
        raise NotImplementedError

    def texts(self):
        return [getattr(m, "text", None) for m in self.sent]


def _async(value=None):
    async def _f(*a, **kw):
        return value
    return _f


@pytest.fixture(autouse=True)
def _common(monkeypatch):
    # Same no-DB monkeypatch convention as tests/test_navigation.py -- these
    # are looked up by NAME inside bot.py at call time (late-bound module
    # globals), so patching them here affects handlers reached via the REAL
    # dispatcher exactly the same way it affects direct handler calls.
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    monkeypatch.setattr(bot, "get_active_crisis", _async(None))
    monkeypatch.setattr(bot, "log_crisis_delivery", _async(None))
    monkeypatch.setattr(bot, "_voice_ux_enabled_for", _async(False))
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", USER_ID)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})
    stub = _StubSession()
    monkeypatch.setattr(bot.bot, "session", stub)
    return stub


def _make_text_update(text, uid=USER_ID):
    return Update.model_validate({
        "update_id": next(_id_counter),
        "message": {
            "message_id": next(_id_counter),
            "date": int(time.time()),
            "chat": {"id": uid, "type": "private"},
            "from": {"id": uid, "is_bot": False, "first_name": "T"},
            "text": text,
        },
    }, context={"bot": bot.bot})


async def _seed_emotion_journal(uid=USER_ID):
    ctx = bot.dp.fsm.get_context(bot.bot, chat_id=uid, user_id=uid)
    await ctx.set_state(bot.EmotionJournal.active)
    await ctx.set_data({"jstep": 0, "jdata": {}, "orange": False, "nudged": False})
    return ctx


async def _seed_cbt_journal(uid=USER_ID):
    ctx = bot.dp.fsm.get_context(bot.bot, chat_id=uid, user_id=uid)
    await ctx.set_state(bot.CbtJournal.active)
    await ctx.set_data({"cstep": 0, "cdata": {}})
    return ctx


# ── Section 15's required scenario, for all 5 RU lower-menu controls: an
# active journal FSM must not swallow a control press, and the control's
# REAL navigation output must actually be produced. ─────────────────────────
LOWER_MENU_ESCAPE_CASES = [
    ("🧠 Психологические тесты", lambda: questionnaire_ux.list_text("ru")),
    ("📊 Мои результаты", lambda: navigation.results_hub_text("ru")),
    ("📝 Дневники", lambda: bot._journal_hub_text("ru")),
    ("🎛 Как отвечать", lambda: navigation.response_settings_text("ru", available=False)),
    ("🔒 Данные и приватность", lambda: navigation.privacy_hub_text("ru")),
]


@pytest.mark.parametrize("label,expected_text_fn", LOWER_MENU_ESCAPE_CASES)
def test_emotion_journal_active_lower_menu_control_escapes_via_real_dispatch(
        label, expected_text_fn, _common):
    async def run():
        ctx = await _seed_emotion_journal()
        await bot.dp.feed_update(bot.bot, _make_text_update(label))
        return ctx
    ctx = asyncio.run(run())

    state = asyncio.run(ctx.get_state())
    data = asyncio.run(ctx.get_data())
    # emotion_step did NOT consume it: no jstep/jdata survive, FSM exited.
    assert state != bot.EmotionJournal.active.state
    assert state is None
    assert data == {}
    # the real navigation handler ran and produced its real output.
    assert expected_text_fn() in _common.texts()


@pytest.mark.parametrize("label,expected_text_fn", LOWER_MENU_ESCAPE_CASES)
def test_cbt_journal_active_lower_menu_control_escapes_via_real_dispatch(
        label, expected_text_fn, _common):
    async def run():
        ctx = await _seed_cbt_journal()
        await bot.dp.feed_update(bot.bot, _make_text_update(label))
        return ctx
    ctx = asyncio.run(run())

    state = asyncio.run(ctx.get_state())
    data = asyncio.run(ctx.get_data())
    # cbt_step did NOT consume it: no cstep/cdata survive, FSM exited.
    assert state != bot.CbtJournal.active.state
    assert state is None
    assert data == {}
    assert expected_text_fn() in _common.texts()


# ── Round 4 final correction: "📝 Дневники" now reaches cmd_journal (via
# lower_menu_journals) even while a journal FSM is active -- before the
# routing fix, an active crisis would have been caught by emotion_step/
# cbt_step's own journal_guard call instead, since the label never escaped
# them. cmd_journal must therefore run the SAME journal_guard/active-crisis
# check itself (via _nav_gate) before ever rendering the journal hub, so an
# unresolved crisis always wins over ordinary journal navigation. ──────────
def test_emotion_journal_active_crisis_blocks_journal_hub_via_real_dispatch(monkeypatch, _common):
    EVENT_ID, STAGE, LANG = 7, 0, "ru"
    monkeypatch.setattr(bot, "get_active_crisis", _async((EVENT_ID, STAGE, LANG)))
    expected_crisis_text, _ = crisis_protocol.crisis_screen(STAGE, LANG, EVENT_ID)

    async def run():
        ctx = await _seed_emotion_journal()
        await bot.dp.feed_update(bot.bot, _make_text_update("📝 Дневники"))
        return ctx
    ctx = asyncio.run(run())

    # emotion_step did NOT consume the label: jstep did not advance, no
    # answer was written to jdata -- routing reached the journal nav path
    # (cmd_journal), which itself blocked on the active-crisis gate BEFORE
    # ever calling _clear_active_journal_if_leaving, so the still-unfinished
    # journal is left exactly as it was (same as cmd_emotion/cmd_cbt's own
    # pre-existing "crisis blocks before state.clear()" behavior) -- not
    # silently discarded by a blocked navigation attempt.
    state = asyncio.run(ctx.get_state())
    data = asyncio.run(ctx.get_data())
    assert state == bot.EmotionJournal.active.state
    assert data["jstep"] == 0
    assert data["jdata"] == {}

    texts = _common.texts()
    # the active-crisis gate ran and won: crisis screen sent, journal hub NOT.
    assert expected_crisis_text in texts
    assert bot._journal_hub_text("ru") not in texts


def test_cbt_journal_active_crisis_blocks_journal_hub_via_real_dispatch(monkeypatch, _common):
    EVENT_ID, STAGE, LANG = 7, 0, "ru"
    monkeypatch.setattr(bot, "get_active_crisis", _async((EVENT_ID, STAGE, LANG)))
    expected_crisis_text, _ = crisis_protocol.crisis_screen(STAGE, LANG, EVENT_ID)

    async def run():
        ctx = await _seed_cbt_journal()
        await bot.dp.feed_update(bot.bot, _make_text_update("📝 Дневники"))
        return ctx
    ctx = asyncio.run(run())

    state = asyncio.run(ctx.get_state())
    data = asyncio.run(ctx.get_data())
    assert state == bot.CbtJournal.active.state
    assert data["cstep"] == 0
    assert data["cdata"] == {}

    texts = _common.texts()
    assert expected_crisis_text in texts
    assert bot._journal_hub_text("ru") not in texts


# ── the exclusion filter must be narrow: ordinary journal free text (any
# text NOT equal to one of the exact control labels) must still advance the
# journal exactly as before, through this SAME real-dispatch path. ─────────
def test_ordinary_emotion_journal_text_still_advances_via_real_dispatch(_common):
    async def run():
        ctx = await _seed_emotion_journal()
        await bot.dp.feed_update(
            bot.bot, _make_text_update("Обычный рабочий день, ничего особенного."))
        return ctx
    ctx = asyncio.run(run())

    assert asyncio.run(ctx.get_state()) == bot.EmotionJournal.active.state
    data = asyncio.run(ctx.get_data())
    assert data["jstep"] == 1
    assert data["jdata"]["event"] == "Обычный рабочий день, ничего особенного."


def test_ordinary_cbt_journal_text_still_advances_via_real_dispatch(_common):
    async def run():
        ctx = await _seed_cbt_journal()
        await bot.dp.feed_update(
            bot.bot, _make_text_update("Коллега не ответил на сообщение."))
        return ctx
    ctx = asyncio.run(run())

    assert asyncio.run(ctx.get_state()) == bot.CbtJournal.active.state
    data = asyncio.run(ctx.get_data())
    assert data["cstep"] == 1
    assert data["cdata"]["situation"] == "Коллега не ответил на сообщение."


# ── after the escape, a normal follow-up message must reach the ordinary
# pipeline, never the abandoned journal (Section 17). pipeline() itself
# (LLM call, risk detection, ...) is out of scope here -- bot.pipeline is
# stubbed to a recorder so this stays a pure routing proof. ────────────────
def test_post_navigation_text_reaches_pipeline_not_abandoned_journal(monkeypatch, _common):
    calls = []

    async def _fake_pipeline(message, text, state):
        calls.append(text)
    monkeypatch.setattr(bot, "pipeline", _fake_pipeline)

    async def run():
        await _seed_emotion_journal()
        await bot.dp.feed_update(bot.bot, _make_text_update("🧠 Психологические тесты"))
        await bot.dp.feed_update(bot.bot, _make_text_update("привет, как дела"))
    asyncio.run(run())

    # If the abandoned journal had still been "active", this text would have
    # been consumed as an emotion_step answer instead of reaching pipeline().
    assert calls == ["привет, как дела"]


# ── non-journal FSM state must be provably untouched by the helper. ────────
def test_non_journal_fsm_state_is_not_touched_by_lower_menu_navigation(_common):
    async def run():
        ctx = bot.dp.fsm.get_context(bot.bot, chat_id=USER_ID, user_id=USER_ID)
        await ctx.set_state("SomeOtherFlow:step")
        await ctx.set_data({"unrelated": "keep-me"})
        await bot.dp.feed_update(bot.bot, _make_text_update("📊 Мои результаты"))
        return ctx
    ctx = asyncio.run(run())

    # Not an EmotionJournal/CbtJournal state, so _clear_active_journal_if_leaving
    # must be a complete no-op here -- state and data both survive untouched.
    assert asyncio.run(ctx.get_state()) == "SomeOtherFlow:step"
    assert asyncio.run(ctx.get_data()) == {"unrelated": "keep-me"}
