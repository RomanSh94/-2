"""Post-1B stabilization cleanup — /help must surface the real self-service
privacy rights (/privacy_export_all, /privacy_delete_all) alongside the
pre-existing /forget_all, and must NOT list /review_pack (a reviewer/owner
tool, not normal self-service -- safe if called, but unnecessary UX noise for
the ordinary-user audience /help is written for). Deliberately narrow: this
does not test role-awareness, because /help stays static/role-unaware in
this PR.
"""
import asyncio
import types

import pytest

import bot


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


def test_help_includes_privacy_self_service_commands():
    msg = FakeMessage(FakeUser(1))
    asyncio.run(bot.cmd_help(msg))
    text = msg.answers[0][0]
    assert "/privacy_export_all" in text
    assert "/privacy_delete_all" in text
    assert "/forget_all" in text


def test_help_does_not_include_review_pack():
    msg = FakeMessage(FakeUser(1))
    asyncio.run(bot.cmd_help(msg))
    text = msg.answers[0][0]
    assert "/review_pack" not in text
