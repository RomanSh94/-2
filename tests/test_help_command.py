"""Round 3 — /help is a normal in-chat navigation/help card with user-facing
RU/EN labels, not a raw technical slash-command list. Old slash-command
handlers stay registered and callable manually, but are no longer advertised
in /help. This supersedes the pre-round-3 version of this file, which
asserted the OLD raw-command-list behavior that this round intentionally
replaces.
"""
import asyncio
import types

import pytest

import bot
import navigation


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


def _async(value=None):
    async def _f(*a, **kw):
        return value
    return _f


@pytest.fixture(autouse=True)
def _lang(monkeypatch):
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))


def _button_texts_and_data(kw):
    kb = kw["reply_markup"]
    return [(b.text, b.callback_data) for row in kb.inline_keyboard for b in row]


def test_help_does_not_list_raw_slash_commands():
    msg = FakeMessage(FakeUser(1))
    asyncio.run(bot.cmd_help(msg))
    text = msg.answers[0][0]
    for cmd in ("/menu", "/questionnaire", "/journal", "/format", "/checkin",
                "/time", "/profile", "/forget_all", "/privacy_export_all",
                "/privacy_delete_all"):
        assert cmd not in text


def test_help_card_exact_ru_text():
    msg = FakeMessage(FakeUser(1))
    asyncio.run(bot.cmd_help(msg))
    assert msg.answers[0][0] == navigation.help_text("ru")
    assert msg.answers[0][0] == (
        "ℹ️ Помощь\n\n"
        "Здесь можно посмотреть информацию о боте и настройках приватности.\n\n"
        "Если хочешь поговорить — просто напиши сообщение.")


def test_help_card_en_equivalent(monkeypatch):
    monkeypatch.setattr(bot, "get_user_language", _async("en"))
    msg = FakeMessage(FakeUser(1))
    asyncio.run(bot.cmd_help(msg))
    assert msg.answers[0][0] == navigation.help_text("en")
    assert "Помощь" not in msg.answers[0][0]


def test_help_card_has_no_x20():
    msg = FakeMessage(FakeUser(1))
    asyncio.run(bot.cmd_help(msg))
    text, kw = msg.answers[0]
    assert "X20" not in text and "x20" not in text
    for label, _ in _button_texts_and_data(kw):
        assert "X20" not in label and "x20" not in label


def test_help_buttons_route_through_existing_safe_callbacks():
    # UI polish V1: Help exposes ONLY About and Privacy, in that order --
    # every other section (talk/tests/journals/results/settings) already
    # lives on the persistent lower menu (or, for talk, is superseded by
    # the always-available text field) and was dropped to stop duplicating
    # it. Destinations are the exact same pre-existing callbacks.
    msg = FakeMessage(FakeUser(1))
    asyncio.run(bot.cmd_help(msg))
    _, kw = msg.answers[0]
    datas = [d for _, d in _button_texts_and_data(kw)]
    assert datas == ["about:hub", "privacy:hub"]


def test_help_never_shows_feedback_button_regardless_of_config(monkeypatch):
    # Feedback is one of the "must contain ONLY About and Privacy" removals
    # too -- must stay absent whether or not a feedback chat URL is
    # configured.
    for url in ("https://t.me/x20_feedback", ""):
        monkeypatch.setattr(bot.config, "FEEDBACK_CHAT_URL", url)
        msg = FakeMessage(FakeUser(1))
        asyncio.run(bot.cmd_help(msg))
        _, kw = msg.answers[0]
        datas = [d for _, d in _button_texts_and_data(kw)]
        assert "feedback:hub" not in datas


def test_help_removed_labels_are_absent():
    # _lang fixture forces lang="ru" -- compare against the RU labels.
    labels = {key: ru for key, ru, en in navigation.MENU_SECTIONS}
    removed = {labels["talk"], labels["tests"], labels["journals"],
               labels["results"], labels["settings"]}
    msg = FakeMessage(FakeUser(1))
    asyncio.run(bot.cmd_help(msg))
    _, kw = msg.answers[0]
    present_labels = {label for label, _ in _button_texts_and_data(kw)}
    assert present_labels.isdisjoint(removed)
    assert present_labels == {labels["about"], labels["privacy"]}
