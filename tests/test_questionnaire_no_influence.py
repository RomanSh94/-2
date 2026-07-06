"""Questionnaire Registry (PR A) — no A1 influence, no product-reply wiring.

This PR is storage-only: questionnaire data must never be traced as an A1
latent-influence source, and must never affect router/tone/prompt/LLM
selection. Both a source-level scan of the questionnaire code path and a
behavioral proof (real DB, real flow, empty influence_trace afterward) are
included.
"""
import ast
import asyncio
import types

import pytest

import bot
import database
import questionnaires

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "registry"


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
    monkeypatch.setattr(bot, "log_crisis_delivery", _async(None))
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(bot, "CallbackQuery", FakeCallback)
    monkeypatch.setattr(bot, "_load_registry_fresh",
                        lambda: questionnaires.load_registry(FIXTURE_DIR))


def _start_and_answer_all(user, msg):
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data="q:s:demo_anxiety_v1")))
    import sqlite3
    con = sqlite3.connect(database.DB)
    session_id = con.execute(
        "SELECT id FROM questionnaire_sessions WHERE user_id=?", (user.id,)).fetchone()[0]
    con.close()
    for step in range(5):
        cb = FakeCallback(user, msg, data=f"q:a:{session_id}:{step}:a1")
        asyncio.run(bot.cb_questionnaire_answer(cb))
    return session_id


# ── behavioral: real flow leaves influence_trace untouched ─────────────────────
def test_questionnaire_flow_does_not_write_influence_trace(tmp_db):
    user = FakeUser(1)
    msg = FakeMessage(user)
    _start_and_answer_all(user, msg)

    trace_rows = asyncio.run(database.get_influence_trace_for_user(1))
    assert trace_rows == []


def test_questionnaire_completion_does_not_use_traced_response(monkeypatch, tmp_db):
    calls = {"n": 0}

    async def _spy(*a, **kw):
        calls["n"] += 1
    monkeypatch.setattr("traced_response.traced_response_builder", _spy, raising=False)

    user = FakeUser(1)
    msg = FakeMessage(user)
    _start_and_answer_all(user, msg)

    assert calls["n"] == 0


# ── source-level scan: the questionnaire code path never touches A1/router ────
_QUESTIONNAIRE_FUNCS = (
    "cmd_questionnaire", "cb_questionnaire_list", "cb_questionnaire_category",
    "cb_questionnaire_detail", "cb_questionnaire_start", "cb_questionnaire_answer",
    "cb_questionnaire_back", "cb_questionnaire_pause", "cb_questionnaire_cancel",
    "_send_questionnaire_step", "_questionnaire_item_keyboard", "_questionnaire_gate",
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
