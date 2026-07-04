"""Questionnaire Core PR #1 — /questionnaire command flow (storage-only).

Handler-level tests against the REAL bot.py handlers and a REAL tmp sqlite DB
(so session/response storage is exercised for real, not mocked), following
the same pattern as tests/test_privacy_commands.py and
tests/test_journal_crisis_gate.py. questionnaires.get_validated_definition is
monkeypatched per-test to control which definition (if any) is "available",
without ever touching the real private_questionnaires/ directory.
"""
import asyncio
import json
import pathlib
import types

import pytest

import bot
import database
import access_control as ac


FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "synthetic_questionnaire.json"


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
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    monkeypatch.setattr(bot, "get_active_crisis", _async(None))
    monkeypatch.setattr(bot, "log_crisis_delivery", _async(None))
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})


def _mock_definition(monkeypatch, definition=None, error=None):
    monkeypatch.setattr(bot.questionnaires, "get_validated_definition", lambda *a, **kw: (definition, error))


async def _sessions_for(uid):
    import sqlite3
    con = sqlite3.connect(database.DB)
    rows = con.execute(
        "SELECT id, questionnaire_id, questionnaire_version, status, current_index "
        "FROM questionnaire_sessions WHERE user_id=?", (uid,)).fetchall()
    con.close()
    return rows


# ── /help must not expose it ──────────────────────────────────────────────────
def test_questionnaire_not_in_help():
    msg = FakeMessage(FakeUser(1))
    asyncio.run(bot.cmd_help(msg))
    assert "/questionnaire" not in msg.answers[0][0]


# ── product gate ─────────────────────────────────────────────────────────────
def test_questionnaire_requires_product_gate(monkeypatch):
    _mock_definition(monkeypatch, _definition(), None)
    user = FakeUser(424242)   # UNKNOWN under personal_use (OWNER_USER_ID=1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    assert msg.answers
    assert "самоопрос" not in msg.answers[0][0]   # not the consent text
    rows = asyncio.run(_sessions_for(424242))
    assert rows == []


# ── active-crisis gate ────────────────────────────────────────────────────────
def test_questionnaire_refuses_to_start_during_active_crisis(monkeypatch):
    monkeypatch.setattr(bot, "get_active_crisis", _async((7, 0, "ru")))
    _mock_definition(monkeypatch, _definition(), None)
    user = FakeUser(1)   # OWNER, full access
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    rows = asyncio.run(_sessions_for(1))
    assert rows == []   # no session created -- crisis screen shown instead


def test_questionnaire_active_crisis_gate_runs_before_product_gate(monkeypatch):
    # UNKNOWN uid (would fail the product gate) AND an active crisis -- the
    # crisis screen must still be shown (order proof: crisis check first).
    monkeypatch.setattr(bot, "get_active_crisis", _async((7, 0, "ru")))
    _mock_definition(monkeypatch, _definition(), None)
    user = FakeUser(424242)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    assert len(msg.answers) == 1
    from crisis_protocol import get_hotline
    assert get_hotline("ru")["primary"] in msg.answers[0][0]
    rows = asyncio.run(_sessions_for(424242))
    assert rows == []


# ── loader outcomes ───────────────────────────────────────────────────────────
def test_questionnaire_no_private_definition_sends_not_configured(monkeypatch):
    _mock_definition(monkeypatch, None, "not_configured")
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    assert msg.answers[0][0] == bot._questionnaire_not_configured_text("ru")


def test_questionnaire_invalid_definition_sends_same_not_configured_message(monkeypatch):
    _mock_definition(monkeypatch, None, "invalid")
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    # Byte-identical to the not_configured message -- internal codes stay
    # distinct, the user-facing text must not. Same uid (1, OWNER) both
    # times so only the loader outcome differs, not the access gate result.
    not_configured_msg = FakeMessage(FakeUser(1))

    async def _not_configured():
        _mock_definition(monkeypatch, None, "not_configured")
        await bot.cmd_questionnaire(not_configured_msg)
    asyncio.run(_not_configured())
    assert msg.answers[0][0] == not_configured_msg.answers[0][0]


# ── start / session lifecycle ────────────────────────────────────────────────
def test_questionnaire_start_stores_session(monkeypatch):
    _mock_definition(monkeypatch, _definition(), None)
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    rows = asyncio.run(_sessions_for(1))
    assert len(rows) == 1
    _, qid, version, status, index = rows[0]
    assert qid == "synthetic_demo_v1" and version == "1" and status == "active" and index == 0
    # consent text sent, then the first question
    assert "самоопрос" in msg.answers[0][0]
    assert msg.answers[1][0] == _definition()["items"][0]["text"]


def test_questionnaire_answer_stores_response(monkeypatch):
    _mock_definition(monkeypatch, _definition(), None)
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    rows = asyncio.run(_sessions_for(1))
    session_id = rows[0][0]

    cb = FakeCallback(user, msg, data=f"q:a:{session_id}:mid")
    asyncio.run(bot.cb_questionnaire_answer(cb))

    data = asyncio.run(database.export_all_personal_data(1))
    assert len(data["questionnaire_responses"]) == 1
    resp = data["questionnaire_responses"][0]
    assert resp["item_id"] == "energy" and resp["answer_id"] == "mid" and resp["answer_value"] == "2"
    # advanced to the next item
    session = asyncio.run(database.get_questionnaire_session(session_id))
    assert session["current_index"] == 1


def test_questionnaire_callback_rejects_wrong_user(monkeypatch):
    _mock_definition(monkeypatch, _definition(), None)
    owner = FakeUser(1)
    msg = FakeMessage(owner)
    asyncio.run(bot.cmd_questionnaire(msg))
    session_id = asyncio.run(_sessions_for(1))[0][0]

    attacker = FakeUser(999)
    cb = FakeCallback(attacker, msg, data=f"q:a:{session_id}:mid")
    asyncio.run(bot.cb_questionnaire_answer(cb))

    data = asyncio.run(database.export_all_personal_data(1))
    assert data["questionnaire_responses"] == []


def test_questionnaire_callback_rejects_wrong_answer_for_current_item(monkeypatch):
    _mock_definition(monkeypatch, _definition(), None)
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    session_id = asyncio.run(_sessions_for(1))[0][0]

    cb = FakeCallback(user, msg, data=f"q:a:{session_id}:does_not_exist")
    asyncio.run(bot.cb_questionnaire_answer(cb))

    data = asyncio.run(database.export_all_personal_data(1))
    assert data["questionnaire_responses"] == []


def test_questionnaire_callback_rejects_mismatched_definition_version(monkeypatch):
    _mock_definition(monkeypatch, _definition(), None)
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    session_id = asyncio.run(_sessions_for(1))[0][0]

    other = _definition()
    other["version"] = "2"
    _mock_definition(monkeypatch, other, None)

    cb = FakeCallback(user, msg, data=f"q:a:{session_id}:mid")
    asyncio.run(bot.cb_questionnaire_answer(cb))

    data = asyncio.run(database.export_all_personal_data(1))
    assert data["questionnaire_responses"] == []


# ── active-crisis gate on the answer callback (round-2 safety fix) ──────────────
def test_questionnaire_answer_callback_blocks_during_active_crisis(monkeypatch):
    _mock_definition(monkeypatch, _definition(), None)
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    session_id = asyncio.run(_sessions_for(1))[0][0]

    monkeypatch.setattr(bot, "get_active_crisis", _async((7, 0, "ru")))
    cb = FakeCallback(user, msg, data=f"q:a:{session_id}:mid")
    asyncio.run(bot.cb_questionnaire_answer(cb))

    # crisis screen shown via the existing journal_guard -> send_crisis path
    from crisis_protocol import get_hotline
    assert get_hotline("ru")["primary"] in msg.answers[-1][0]


def test_questionnaire_answer_callback_active_crisis_does_not_store_response(monkeypatch):
    _mock_definition(monkeypatch, _definition(), None)
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    session_id = asyncio.run(_sessions_for(1))[0][0]

    monkeypatch.setattr(bot, "get_active_crisis", _async((7, 0, "ru")))
    cb = FakeCallback(user, msg, data=f"q:a:{session_id}:mid")
    asyncio.run(bot.cb_questionnaire_answer(cb))

    data = asyncio.run(database.export_all_personal_data(1))
    assert data["questionnaire_responses"] == []


def test_questionnaire_answer_callback_active_crisis_does_not_advance_session(monkeypatch):
    _mock_definition(monkeypatch, _definition(), None)
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    session_id = asyncio.run(_sessions_for(1))[0][0]

    monkeypatch.setattr(bot, "get_active_crisis", _async((7, 0, "ru")))
    cb = FakeCallback(user, msg, data=f"q:a:{session_id}:mid")
    asyncio.run(bot.cb_questionnaire_answer(cb))

    session = asyncio.run(database.get_questionnaire_session(session_id))
    assert session["current_index"] == 0
    assert session["status"] == "active"


# ── neutral message for the same user's own session on config problems ─────────
def test_questionnaire_callback_invalid_definition_sends_neutral_message_for_owner_session(monkeypatch):
    _mock_definition(monkeypatch, _definition(), None)
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    session_id = asyncio.run(_sessions_for(1))[0][0]

    _mock_definition(monkeypatch, None, "invalid")
    cb = FakeCallback(user, msg, data=f"q:a:{session_id}:mid")
    asyncio.run(bot.cb_questionnaire_answer(cb))

    assert msg.answers[-1][0] == bot._questionnaire_not_configured_text("ru")
    data = asyncio.run(database.export_all_personal_data(1))
    assert data["questionnaire_responses"] == []


def test_questionnaire_callback_mismatched_definition_sends_neutral_message_for_owner_session(monkeypatch):
    _mock_definition(monkeypatch, _definition(), None)
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    session_id = asyncio.run(_sessions_for(1))[0][0]

    other = _definition()
    other["version"] = "2"
    _mock_definition(monkeypatch, other, None)

    cb = FakeCallback(user, msg, data=f"q:a:{session_id}:mid")
    asyncio.run(bot.cb_questionnaire_answer(cb))

    assert msg.answers[-1][0] == bot._questionnaire_not_configured_text("ru")
    data = asyncio.run(database.export_all_personal_data(1))
    assert data["questionnaire_responses"] == []


def test_questionnaire_active_session_resumes(monkeypatch):
    _mock_definition(monkeypatch, _definition(), None)
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    session_id = asyncio.run(_sessions_for(1))[0][0]
    cb = FakeCallback(user, msg, data=f"q:a:{session_id}:mid")
    asyncio.run(bot.cb_questionnaire_answer(cb))

    msg2 = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg2))

    rows = asyncio.run(_sessions_for(1))
    assert len(rows) == 1                       # no second session created
    assert rows[0][4] == 1                      # resumed at current_index=1
    assert msg2.answers[0][0] == _definition()["items"][1]["text"]   # second item, not consent


def test_questionnaire_cancel_marks_session_cancelled(monkeypatch):
    _mock_definition(monkeypatch, _definition(), None)
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    session_id = asyncio.run(_sessions_for(1))[0][0]

    cb = FakeCallback(user, msg, data=f"q:c:{session_id}")
    asyncio.run(bot.cb_questionnaire_cancel(cb))

    session = asyncio.run(database.get_questionnaire_session(session_id))
    assert session["status"] == "cancelled"


def test_questionnaire_completion_message_is_non_diagnostic(monkeypatch):
    _mock_definition(monkeypatch, _definition(), None)
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    session_id = asyncio.run(_sessions_for(1))[0][0]

    cb1 = FakeCallback(user, msg, data=f"q:a:{session_id}:mid")
    asyncio.run(bot.cb_questionnaire_answer(cb1))
    cb2 = FakeCallback(user, msg, data=f"q:a:{session_id}:ok")
    asyncio.run(bot.cb_questionnaire_answer(cb2))

    final_text = msg.answers[-1][0]
    assert final_text == bot._questionnaire_completion_text("ru")
    assert "диагноз" not in final_text or "не диагноз" in final_text
    session = asyncio.run(database.get_questionnaire_session(session_id))
    assert session["status"] == "completed"


def test_questionnaire_callbacks_stay_under_64_bytes(monkeypatch):
    _mock_definition(monkeypatch, _definition(), None)
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    kb = msg.answers[1][1]["reply_markup"]
    for row in kb.inline_keyboard:
        for btn in row:
            assert len(btn.callback_data.encode("utf-8")) <= 64
