"""Navigation Hub — /menu + section callbacks.

Handler-level tests against the REAL bot.py handlers, following the same
pattern as tests/test_questionnaire_command_flow.py. The core safety claim
under test: /menu and every navigation callback reuse the EXACT SAME two
gates as other product entrypoints (journal_guard for active-crisis,
ensure_full_access_or_closed_test for product access), in the same order --
this project has twice previously lost a gate during refactors, so this is
tested explicitly rather than assumed.
"""
import asyncio
import types

import pytest

import bot
import navigation
import access_control as ac
from crisis_protocol import get_hotline


class FakeUser:
    def __init__(self, uid, username="user"):
        self.id = uid
        self.username = username


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


def _async(value=None):
    async def _f(*a, **kw):
        return value
    return _f


@pytest.fixture(autouse=True)
def _common(monkeypatch):
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    monkeypatch.setattr(bot, "get_active_crisis", _async(None))
    monkeypatch.setattr(bot, "log_crisis_delivery", _async(None))
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})
    # ensure_full_access_or_closed_test / _nav_gate / _answer_target branch on
    # isinstance(entity, CallbackQuery) -- swap bot.py's own reference to the
    # real aiogram class for our lightweight FakeCallback so those isinstance
    # checks route correctly against the fakes used here (this only affects
    # the name inside bot.py's module namespace, not the real aiogram class).
    monkeypatch.setattr(bot, "CallbackQuery", FakeCallback)


NAV_CALLBACKS = {
    "tests:hub": bot.cb_tests_hub,
    "journals:hub": bot.cb_journals_hub,
    "results:hub": bot.cb_results_hub,
    "privacy:hub": bot.cb_privacy_hub,
    "about:hub": bot.cb_about_hub,
    "menu:back": bot.cb_menu_back,
}


# ── /menu renders the hub ────────────────────────────────────────────────────
def test_menu_renders_main_menu_with_all_sections():
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_menu(msg))
    assert msg.answers
    text, kw = msg.answers[0]
    assert "Главное меню" in text
    kb = kw["reply_markup"]
    callback_datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert callback_datas == [f"{key}:hub" for key, _, _ in navigation.MENU_SECTIONS]
    assert callback_datas == ["tests:hub", "journals:hub", "results:hub", "privacy:hub", "about:hub"]


def test_tests_hub_has_non_diagnostic_framing():
    text = navigation.tests_hub_text("ru")
    assert "не ставит диагнозы" in text
    assert "не заменяет" in text


def test_results_hub_does_not_show_scores_or_diagnosis():
    # "уровни"/"диагнозы" legitimately appear in the sentence DENYING them
    # ("Мы не показываем оценки, диагнозы или уровни выраженности") -- so this
    # test checks for actual verdict-shaped phrases, not bare words that are
    # part of the explicit denial itself.
    text = navigation.results_hub_text("ru")
    for forbidden in ("результат выше нормы", "лёгкая депрессия", "умеренная депрессия",
                      "тяжёлая депрессия", "у тебя депрессия", "высокая тревожность"):
        assert forbidden not in text.lower()
    assert "не показываем оценки" in text   # explicit denial present, as intended


def test_navigation_text_has_no_dependency_wording():
    all_text = "\n".join([
        navigation.menu_text("ru"), navigation.menu_text("en"),
        navigation.tests_hub_text("ru"), navigation.tests_hub_text("en"),
        navigation.journals_hub_text("ru"), navigation.journals_hub_text("en"),
        navigation.privacy_hub_text("ru"), navigation.privacy_hub_text("en"),
        navigation.results_hub_text("ru"), navigation.results_hub_text("en"),
        navigation.about_hub_text("ru"), navigation.about_hub_text("en"),
    ]).lower()
    for phrase in ("я рядом", "я всегда рядом", "я тебя не брошу"):
        assert phrase not in all_text


def test_about_section_does_not_deny_crisis_behavior():
    text = navigation.about_hub_text("ru")
    assert "не заменяет" in text
    # Must NOT claim the bot does nothing for emergencies.
    assert "не используется для экстренной помощи" not in text.lower()
    assert "не помогает в экстренных" not in text.lower()
    # Must reference the REAL configured hotline, not an invented one.
    assert get_hotline("ru")["primary"] in text


# ── product access gate ──────────────────────────────────────────────────────
def test_menu_requires_product_gate():
    user = FakeUser(424242)   # UNKNOWN under personal_use (OWNER_USER_ID=1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_menu(msg))
    assert msg.answers
    assert "Главное меню" not in msg.answers[0][0]


@pytest.mark.parametrize("data,handler", list(NAV_CALLBACKS.items()))
def test_nav_callback_requires_product_gate(data, handler):
    user = FakeUser(424242)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data=data)
    asyncio.run(handler(cb))
    assert msg.answers
    assert "Главное меню" not in msg.answers[0][0]
    for hub_text in (navigation.tests_hub_text("ru"), navigation.journals_hub_text("ru"),
                     navigation.results_hub_text("ru"), navigation.privacy_hub_text("ru"),
                     navigation.about_hub_text("ru")):
        assert msg.answers[0][0] != hub_text


# ── active-crisis gate ────────────────────────────────────────────────────────
def test_menu_respects_active_crisis_gate(monkeypatch):
    monkeypatch.setattr(bot, "get_active_crisis", _async((7, 0, "ru")))
    user = FakeUser(1)   # OWNER, full access -- crisis must still intercept
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_menu(msg))
    assert len(msg.answers) == 1
    assert get_hotline("ru")["primary"] in msg.answers[0][0]
    assert "Главное меню" not in msg.answers[0][0]


@pytest.mark.parametrize("data,handler", list(NAV_CALLBACKS.items()))
def test_nav_callback_respects_active_crisis_gate(monkeypatch, data, handler):
    monkeypatch.setattr(bot, "get_active_crisis", _async((7, 0, "ru")))
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data=data)
    asyncio.run(handler(cb))
    assert len(msg.answers) == 1
    assert get_hotline("ru")["primary"] in msg.answers[0][0]


# ── /help ─────────────────────────────────────────────────────────────────────
def test_menu_is_in_help():
    msg = FakeMessage(FakeUser(1))
    asyncio.run(bot.cmd_help(msg))
    assert "/menu" in msg.answers[0][0]


# ── source-level scan: navigation must not touch A1/review-pack/scoring ───────
def test_navigation_module_has_no_forbidden_imports():
    import pathlib
    src = pathlib.Path(navigation.__file__).read_text(encoding="utf-8")
    for forbidden in ("traced_response", "review_pack", "influence_trace",
                      "choose_scenario", "get_system_prompt"):
        assert forbidden not in src
