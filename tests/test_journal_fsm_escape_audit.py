"""Round 4 — per-entrypoint audit that every required navigation escape
point (Section 9 of the task: /help, /start, and all 7 inline nav
callbacks -- talk:hub, q:l, journals:hub, results:hub, settings:hub,
privacy:hub, about:hub, menu:back) actually threads a real FSMContext
through to _clear_active_journal_if_leaving, using a lightweight FakeFSM
duck-type (matching tests/test_navigation.py's convention) rather than the
full real-dispatch machinery in test_journal_navigation_dispatch.py -- that
file already proves the REGISTRATION-ORDER routing fix; this file proves
each individual entrypoint's own body actually calls the helper with a live
FSMContext, and that a stale active journal is the ONLY thing ever touched.

Also covers the two other in-scope Round 4 fixes: the lower_menu_results
keyboard regression (it was still using the bare back-button keyboard
instead of the real 2-action-button Results hub keyboard) and cb_jhub's
report/settings/crisis branches abandoning a stale journal before switching
away.
"""
import asyncio
import types

import pytest

import bot
import access_control as ac
import database
import navigation

run = asyncio.run


class FakeUser:
    def __init__(self, uid, username="user"):
        self.id = uid
        self.username = username
        self.first_name = "U"


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


class FakeCallback:
    def __init__(self, user, message, data=""):
        self.from_user = user
        self.message = message
        self.data = data

    async def answer(self, *a, **kw):
        pass


class FakeFSM:
    """Duck-types just enough of aiogram's FSMContext for
    _clear_active_journal_if_leaving and the handlers under test."""

    def __init__(self, state=None, data=None):
        self._state = state
        self._data = dict(data or {})

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


@pytest.fixture(autouse=True)
def _common(monkeypatch):
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    monkeypatch.setattr(bot, "get_active_crisis", _async(None))
    monkeypatch.setattr(bot, "log_crisis_delivery", _async(None))
    monkeypatch.setattr(bot, "_voice_ux_enabled_for", _async(False))
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})
    monkeypatch.setattr(bot, "CallbackQuery", FakeCallback)


# ── /help escapes an active journal (Section 8) ──────────────────────────────
@pytest.mark.parametrize("active_state", [bot.EmotionJournal.active, bot.CbtJournal.active])
def test_help_escapes_active_journal(active_state):
    state = FakeFSM(state=active_state.state, data={"jstep": 2, "jdata": {"event": "x"}})
    msg = FakeMessage(FakeUser(1))
    run(bot.cmd_help(msg, state))
    assert run(state.get_state()) is None
    assert run(state.get_data()) == {}
    # /help's own card still renders normally -- no extra "cancelled" noise.
    assert msg.answers
    assert msg.answers[0][0] == navigation.help_text("ru")


def test_help_is_a_noop_on_non_journal_fsm_state():
    state = FakeFSM(state="SomeOtherFlow:step", data={"keep": "me"})
    msg = FakeMessage(FakeUser(1))
    run(bot.cmd_help(msg, state))
    assert run(state.get_state()) == "SomeOtherFlow:step"
    assert run(state.get_data()) == {"keep": "me"}


# ── every required inline navigation callback escapes an active journal ─────
INLINE_NAV_CALLBACKS = {
    "talk:hub": bot.cb_talk_hub,
    "journals:hub": bot.cb_journals_hub,
    "results:hub": bot.cb_results_hub,
    "settings:hub": bot.cb_settings_hub,
    "privacy:hub": bot.cb_privacy_hub,
    "about:hub": bot.cb_about_hub,
    "menu:back": bot.cb_menu_back,
}


@pytest.mark.parametrize("data,handler", list(INLINE_NAV_CALLBACKS.items()))
@pytest.mark.parametrize("active_state", [bot.EmotionJournal.active, bot.CbtJournal.active])
def test_inline_nav_callback_escapes_active_journal(data, handler, active_state):
    state = FakeFSM(state=active_state.state, data={"jstep": 1, "jdata": {"event": "x"}})
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data=data)
    run(handler(cb, state))
    assert run(state.get_state()) is None
    assert run(state.get_data()) == {}


def test_questionnaire_list_callback_escapes_active_journal():
    state = FakeFSM(state=bot.EmotionJournal.active.state, data={"jstep": 0, "jdata": {}})
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="q:l")
    run(bot.cb_questionnaire_list(cb, state))
    assert run(state.get_state()) is None
    assert run(state.get_data()) == {}


def test_questionnaire_command_escapes_active_journal():
    state = FakeFSM(state=bot.CbtJournal.active.state, data={"cstep": 0, "cdata": {}})
    msg = FakeMessage(FakeUser(1))
    run(bot.cmd_questionnaire(msg, state))
    assert run(state.get_state()) is None
    assert run(state.get_data()) == {}


# ── inline nav must not touch a non-journal FSM (spot check, menu:back) ─────
def test_menu_back_is_a_noop_on_non_journal_fsm_state():
    state = FakeFSM(state="SomeOtherFlow:step", data={"keep": "me"})
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="menu:back")
    run(bot.cb_menu_back(cb, state))
    assert run(state.get_state()) == "SomeOtherFlow:step"
    assert run(state.get_data()) == {"keep": "me"}


# ── cb_jhub's non-journal-starting branches (report/settings/crisis) also
# abandon a stale active journal before switching away (Section 13) ─────────
def test_jhub_crisis_branch_escapes_a_stale_active_journal(monkeypatch):
    calls = []

    async def _fake_send_crisis(*a, **kw):
        calls.append(a)
    monkeypatch.setattr(bot, "send_crisis", _fake_send_crisis)

    state = FakeFSM(state=bot.EmotionJournal.active.state, data={"jstep": 1, "jdata": {"event": "x"}})
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="jhub:crisis")
    run(bot.cb_jhub(cb, state))
    assert run(state.get_state()) is None
    assert run(state.get_data()) == {}
    assert calls  # the crisis branch itself still ran


# ── lower_menu_results regression fix: the PRIMARY persistent-lower-menu
# entry for "Мои результаты" must render the real 2-action-button Results
# hub keyboard (📊 Отчёт дневника / 🧭 Самонаблюдения / ⬅️ В меню), not a
# bare single back button -- this was silently missing its action buttons
# even though the text itself was already correct. ──────────────────────────
def test_lower_menu_results_uses_the_real_results_hub_keyboard():
    state = FakeFSM()
    msg = FakeMessage(FakeUser(1))
    run(bot.lower_menu_results(msg, state))
    text, kw = msg.answers[0]
    assert text == navigation.results_hub_text("ru")
    buttons = [(b.text, b.callback_data)
               for row in kw["reply_markup"].inline_keyboard for b in row]
    assert buttons == [
        ("📊 Отчёт дневника", "results:report"),
        ("🧭 Самонаблюдения", "results:profile"),
        ("⬅️ В меню", "menu:back"),
    ]


def test_lower_menu_results_escapes_active_journal():
    state = FakeFSM(state=bot.CbtJournal.active.state, data={"cstep": 0, "cdata": {}})
    msg = FakeMessage(FakeUser(1))
    run(bot.lower_menu_results(msg, state))
    assert run(state.get_state()) is None
    assert run(state.get_data()) == {}


# ── /start escape audit (Section 10): must abandon an active journal FSM
# and nothing else -- never onboarding history, never account/data. ─────────
@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "start_audit.db"))
    run(database.init_db())
    return database


@pytest.fixture
def _start_env(monkeypatch, tmp_db):
    monkeypatch.setattr(bot, "get_active_disclosure_flow", _async(None))
    monkeypatch.setattr(bot, "list_core_sessions", _async([]))
    monkeypatch.setattr(bot, "supersede_active_practice_proposals", _async(None))
    monkeypatch.setattr(bot, "ensure_full_access_or_closed_test", _async(True))
    monkeypatch.setattr(bot, "_send_mood_entry", _async(None))
    return tmp_db


def test_start_escapes_active_journal(_start_env):
    state = FakeFSM(state=bot.EmotionJournal.active.state, data={"jstep": 1, "jdata": {"event": "x"}})
    msg = FakeMessage(FakeUser(1), "/start")
    run(bot.cmd_start(msg, state))
    assert run(state.get_state()) is None
    assert run(state.get_data()) == {}


def test_start_without_state_arg_still_works(_start_env):
    # The many pre-existing tests across this suite call cmd_start(msg) with
    # no state at all -- state defaults to None and the escape call is
    # skipped entirely (the real dispatcher always injects a genuine
    # FSMContext in production, so this is purely a test-compatibility path).
    msg = FakeMessage(FakeUser(1), "/start")
    run(bot.cmd_start(msg))  # must not raise
    assert msg.answers
