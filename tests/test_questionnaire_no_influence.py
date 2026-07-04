"""Questionnaire Core PR #1 — no A1 influence, no product-reply wiring.

This PR is storage-only: questionnaire data must never be traced as an A1
latent-influence source, and must never affect router/tone/prompt/LLM
selection. Both a source-level scan of the questionnaire code path and a
behavioral proof (real DB, real flow, empty influence_trace afterward) are
included.
"""
import ast
import asyncio
import json
import pathlib
import types

import pytest

import bot
import database

FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "synthetic_questionnaire.json"
ROOT = pathlib.Path(__file__).resolve().parent.parent


def _definition() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


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


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    return database


@pytest.fixture(autouse=True)
def _common(monkeypatch, tmp_db):
    import access_control as ac
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    monkeypatch.setattr(bot, "get_active_crisis", _async(None))
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(bot.questionnaires, "get_validated_definition",
                        lambda *a, **kw: (_definition(), None))


# ── behavioral: real flow leaves influence_trace untouched ─────────────────────
def test_questionnaire_flow_does_not_write_influence_trace(tmp_db):
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    import sqlite3
    con = sqlite3.connect(database.DB)
    session_id = con.execute(
        "SELECT id FROM questionnaire_sessions WHERE user_id=1").fetchone()[0]
    con.close()

    cb1 = FakeCallback(user, msg, data=f"q:a:{session_id}:mid")
    asyncio.run(bot.cb_questionnaire_answer(cb1))
    cb2 = FakeCallback(user, msg, data=f"q:a:{session_id}:ok")
    asyncio.run(bot.cb_questionnaire_answer(cb2))

    trace_rows = asyncio.run(database.get_influence_trace_for_user(1))
    assert trace_rows == []


def test_questionnaire_completion_does_not_use_traced_response(monkeypatch, tmp_db):
    calls = {"n": 0}

    async def _spy(*a, **kw):
        calls["n"] += 1
    # If bot.py ever imported traced_response_builder under this name, this
    # patch would catch a call; today bot.py doesn't import it at all (see
    # source-scan test below), so this is a belt-and-suspenders behavioral
    # check that stays meaningful if that ever changes.
    monkeypatch.setattr("traced_response.traced_response_builder", _spy, raising=False)

    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    import sqlite3
    con = sqlite3.connect(database.DB)
    session_id = con.execute(
        "SELECT id FROM questionnaire_sessions WHERE user_id=1").fetchone()[0]
    con.close()
    cb1 = FakeCallback(user, msg, data=f"q:a:{session_id}:mid")
    asyncio.run(bot.cb_questionnaire_answer(cb1))
    cb2 = FakeCallback(user, msg, data=f"q:a:{session_id}:ok")
    asyncio.run(bot.cb_questionnaire_answer(cb2))

    assert calls["n"] == 0


# ── source-level scan: the questionnaire code path never touches A1/router ────
_QUESTIONNAIRE_FUNCS = (
    "cmd_questionnaire", "cb_questionnaire_answer", "cb_questionnaire_cancel",
    "_send_questionnaire_step", "_questionnaire_item_keyboard",
    "_questionnaire_consent_text", "_questionnaire_not_configured_text",
    "_questionnaire_completion_text", "_questionnaire_cancelled_text",
)
_FORBIDDEN_SYMBOLS = (
    "traced_response", "influence_trace", "choose_scenario",
    "get_system_prompt", "client.chat.completions",
)


def test_questionnaire_data_not_used_for_router_or_prompt_selection():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in _QUESTIONNAIRE_FUNCS:
            seg = ast.get_source_segment(src, node) or ""
            offenders += [f"{node.name} -> {s}" for s in _FORBIDDEN_SYMBOLS if s in seg]
    assert not offenders, (
        "a questionnaire handler references a router/prompt/A1 symbol -- "
        "this PR must be storage-only:\n  " + "\n  ".join(offenders))
