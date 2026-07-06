"""Questionnaire Registry (PR A) — /questionnaire command flow, list/category/
detail/start/answer/back/cancel screens (storage-only, no scoring).

Handler-level tests against the REAL bot.py handlers and a REAL tmp sqlite DB
(so session/response storage is exercised for real, not mocked), following
the same pattern as tests/test_privacy_commands.py and
tests/test_navigation.py. bot._load_registry_fresh is monkeypatched per-test
to point at tests/fixtures/registry/ (synthetic-only fixtures), never the
real gitignored private_questionnaires/ directory.
"""
import asyncio
import pathlib
import types

import pytest

import bot
import database
import questionnaires
import access_control as ac

FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures" / "registry"


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
        self.answered = 0

    async def answer(self, *a, **kw):
        self.answered += 1


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
    monkeypatch.setattr(bot, "CallbackQuery", FakeCallback)
    monkeypatch.setattr(bot, "_load_registry_fresh",
                        lambda: questionnaires.load_registry(FIXTURE_DIR))


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
def test_questionnaire_requires_product_gate():
    user = FakeUser(424242)   # UNKNOWN under personal_use (OWNER_USER_ID=1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    assert msg.answers
    assert "Опросники" not in msg.answers[0][0]
    rows = asyncio.run(_sessions_for(424242))
    assert rows == []


# ── active-crisis gate ────────────────────────────────────────────────────────
def test_questionnaire_refuses_to_start_during_active_crisis(monkeypatch):
    monkeypatch.setattr(bot, "get_active_crisis", _async((7, 0, "ru")))
    user = FakeUser(1)   # OWNER, full access
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    from crisis_protocol import get_hotline
    assert get_hotline("ru")["primary"] in msg.answers[0][0]
    assert "Опросники" not in msg.answers[0][0]


def test_questionnaire_active_crisis_gate_runs_before_product_gate(monkeypatch):
    monkeypatch.setattr(bot, "get_active_crisis", _async((7, 0, "ru")))
    user = FakeUser(424242)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    assert len(msg.answers) == 1
    from crisis_protocol import get_hotline
    assert get_hotline("ru")["primary"] in msg.answers[0][0]


# ── list / category / detail screens ─────────────────────────────────────────
def test_questionnaire_list_shows_categories():
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    text, kw = msg.answers[0]
    assert "Опросники" in text
    kb = kw["reply_markup"]
    callback_datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "q:c:anxiety" in callback_datas


def test_active_questionnaire_appears_in_category_list():
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="q:c:anxiety")
    asyncio.run(bot.cb_questionnaire_category(cb))
    text, kw = msg.answers[-1]
    assert "Demo Anxiety Check" in text
    kb = kw["reply_markup"]
    callback_datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "q:d:demo_anxiety_v1" in callback_datas


def test_archived_questionnaire_hidden_from_category_list():
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="q:c:anxiety")
    asyncio.run(bot.cb_questionnaire_category(cb))
    text, _ = msg.answers[-1]
    assert "Demo Archived Check" not in text


def test_restricted_questionnaire_hidden_from_category_list():
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="q:c:mood")
    asyncio.run(bot.cb_questionnaire_category(cb))
    text, _ = msg.answers[-1]
    assert "Demo Restricted Check" not in text


def test_draft_questionnaire_hidden_from_category_list():
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="q:c:mood")
    asyncio.run(bot.cb_questionnaire_category(cb))
    text, _ = msg.answers[-1]
    assert "Demo Draft Check" not in text


def test_detail_screen_shows_start_and_back_buttons():
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="q:d:demo_anxiety_v1")
    asyncio.run(bot.cb_questionnaire_detail(cb))
    text, kw = msg.answers[-1]
    assert "Demo Anxiety Check" in text
    assert "Это не диагноз" in text
    kb = kw["reply_markup"]
    callback_datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "q:s:demo_anxiety_v1" in callback_datas
    assert "q:l" in callback_datas


def test_default_requires_gender_and_age_are_false():
    registry = questionnaires.load_registry(FIXTURE_DIR)
    d = registry.get("demo_anxiety_v1")
    assert d["requires_gender"] is False
    assert d["requires_age"] is False


# ── draft/restricted cannot be started or answered ───────────────────────────
def test_draft_questionnaire_cannot_be_started():
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="q:s:demo_draft_v1")
    asyncio.run(bot.cb_questionnaire_start(cb))
    rows = asyncio.run(_sessions_for(1))
    assert rows == []
    assert msg.answers[-1][0] == bot.questionnaire_ux.not_available_text("ru")


def test_restricted_questionnaire_cannot_be_started():
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="q:s:demo_restricted_v1")
    asyncio.run(bot.cb_questionnaire_start(cb))
    rows = asyncio.run(_sessions_for(1))
    assert rows == []


def test_archived_questionnaire_cannot_be_started():
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="q:s:demo_archived_v1")
    asyncio.run(bot.cb_questionnaire_start(cb))
    rows = asyncio.run(_sessions_for(1))
    assert rows == []


def test_draft_questionnaire_detail_card_unavailable():
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="q:d:demo_draft_v1")
    asyncio.run(bot.cb_questionnaire_detail(cb))
    assert msg.answers[-1][0] == bot.questionnaire_ux.not_available_text("ru")


def _force_session_on_draft(uid):
    # Simulate a session that references a now-draft/invalid definition id
    # directly (as if it had been active and was later demoted) -- used to
    # test that answering fails closed even if a session row exists.
    return asyncio.run(database.start_questionnaire_session(uid, "demo_draft_v1", "1"))


def test_draft_questionnaire_cannot_be_answered():
    uid = 1
    session_id = _force_session_on_draft(uid)
    user = FakeUser(uid)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data=f"q:a:{session_id}:0:a0")
    asyncio.run(bot.cb_questionnaire_answer(cb))
    data = asyncio.run(database.export_all_personal_data(uid))
    assert data["questionnaire_responses"] == []


# ── start / question-by-question flow ────────────────────────────────────────
def test_questionnaire_start_stores_session_and_sends_first_question():
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="q:s:demo_anxiety_v1")
    asyncio.run(bot.cb_questionnaire_start(cb))
    rows = asyncio.run(_sessions_for(1))
    assert len(rows) == 1
    _, qid, version, status, index = rows[0]
    assert qid == "demo_anxiety_v1" and version == "1" and status == "active" and index == 0
    text, kw = msg.answers[-1]
    assert "Вопрос 1 из 5" in text
    assert "Мне было трудно расслабиться" in text


def test_answer_flow_moves_question_by_question():
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data="q:s:demo_anxiety_v1")))
    session_id = asyncio.run(_sessions_for(1))[0][0]

    cb = FakeCallback(user, msg, data=f"q:a:{session_id}:0:a2")
    asyncio.run(bot.cb_questionnaire_answer(cb))

    data = asyncio.run(database.export_all_personal_data(1))
    assert len(data["questionnaire_responses"]) == 1
    resp = data["questionnaire_responses"][0]
    assert resp["item_id"] == "q1" and resp["answer_id"] == "a2" and resp["answer_value"] == "2"
    session = asyncio.run(database.get_questionnaire_session(session_id))
    assert session["current_index"] == 1
    text, _ = msg.answers[-1]
    assert "Вопрос 2 из 5" in text


def test_full_flow_reaches_completion_screen_with_no_score():
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data="q:s:demo_anxiety_v1")))
    session_id = asyncio.run(_sessions_for(1))[0][0]
    for step in range(5):
        cb = FakeCallback(user, msg, data=f"q:a:{session_id}:{step}:a1")
        asyncio.run(bot.cb_questionnaire_answer(cb))
    final_text, kw = msg.answers[-1]
    assert final_text == bot.questionnaire_ux.completion_text("ru")
    assert "не диагноз" in final_text
    session = asyncio.run(database.get_questionnaire_session(session_id))
    assert session["status"] == "completed"
    kb = kw["reply_markup"]
    callback_datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "q:l" in callback_datas and "menu:back" in callback_datas


# ── session ownership ─────────────────────────────────────────────────────────
def test_answer_callback_rejects_wrong_user():
    owner = FakeUser(1)
    msg = FakeMessage(owner)
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(owner, msg, data="q:s:demo_anxiety_v1")))
    session_id = asyncio.run(_sessions_for(1))[0][0]

    attacker = FakeUser(999)
    cb = FakeCallback(attacker, msg, data=f"q:a:{session_id}:0:a1")
    asyncio.run(bot.cb_questionnaire_answer(cb))

    data = asyncio.run(database.export_all_personal_data(1))
    assert data["questionnaire_responses"] == []


def test_cancel_callback_rejects_wrong_user():
    owner = FakeUser(1)
    msg = FakeMessage(owner)
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(owner, msg, data="q:s:demo_anxiety_v1")))
    session_id = asyncio.run(_sessions_for(1))[0][0]

    attacker = FakeUser(999)
    cb = FakeCallback(attacker, msg, data=f"q:x:{session_id}")
    asyncio.run(bot.cb_questionnaire_cancel(cb))

    session = asyncio.run(database.get_questionnaire_session(session_id))
    assert session["status"] == "active"


def test_answer_callback_rejects_wrong_answer_for_current_item():
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data="q:s:demo_anxiety_v1")))
    session_id = asyncio.run(_sessions_for(1))[0][0]

    cb = FakeCallback(user, msg, data=f"q:a:{session_id}:0:does_not_exist")
    asyncio.run(bot.cb_questionnaire_answer(cb))

    data = asyncio.run(database.export_all_personal_data(1))
    assert data["questionnaire_responses"] == []


# ── stale-callback protection ─────────────────────────────────────────────────
def test_stale_answer_callback_does_not_save_or_advance_and_reshows_question():
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data="q:s:demo_anxiety_v1")))
    session_id = asyncio.run(_sessions_for(1))[0][0]

    # advance once legitimately (now current_index=1)
    asyncio.run(bot.cb_questionnaire_answer(FakeCallback(user, msg, data=f"q:a:{session_id}:0:a1")))

    # stale: re-press an option from step=0 again (already answered)
    stale_cb = FakeCallback(user, msg, data=f"q:a:{session_id}:0:a2")
    asyncio.run(bot.cb_questionnaire_answer(stale_cb))

    data = asyncio.run(database.export_all_personal_data(1))
    assert len(data["questionnaire_responses"]) == 1   # still just the first answer
    session = asyncio.run(database.get_questionnaire_session(session_id))
    assert session["current_index"] == 1   # not advanced further

    stale_text, _ = msg.answers[-2]
    assert stale_text == bot.questionnaire_ux.stale_answer_text("ru")
    reshown_text, _ = msg.answers[-1]
    assert "Вопрос 2 из 5" in reshown_text   # re-shows CURRENT question


# ── back / cancel ─────────────────────────────────────────────────────────────
def test_back_returns_to_previous_question():
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data="q:s:demo_anxiety_v1")))
    session_id = asyncio.run(_sessions_for(1))[0][0]
    asyncio.run(bot.cb_questionnaire_answer(FakeCallback(user, msg, data=f"q:a:{session_id}:0:a1")))

    back_cb = FakeCallback(user, msg, data=f"q:b:{session_id}")
    asyncio.run(bot.cb_questionnaire_back(back_cb))

    session = asyncio.run(database.get_questionnaire_session(session_id))
    assert session["current_index"] == 0
    text, _ = msg.answers[-1]
    assert "Вопрос 1 из 5" in text


def test_cancel_clears_session():
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data="q:s:demo_anxiety_v1")))
    session_id = asyncio.run(_sessions_for(1))[0][0]

    cb = FakeCallback(user, msg, data=f"q:x:{session_id}")
    asyncio.run(bot.cb_questionnaire_cancel(cb))

    session = asyncio.run(database.get_questionnaire_session(session_id))
    assert session["status"] == "cancelled"
    assert msg.answers[-1][0] == bot.questionnaire_ux.cancelled_text("ru")


# ── mid-session invalidation (continuous validity re-check) ──────────────────
def test_answer_rejected_when_definition_invalidated_mid_session(monkeypatch):
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data="q:s:demo_anxiety_v1")))
    session_id = asyncio.run(_sessions_for(1))[0][0]

    # First answer succeeds normally.
    asyncio.run(bot.cb_questionnaire_answer(FakeCallback(user, msg, data=f"q:a:{session_id}:0:a1")))
    assert asyncio.run(database.get_questionnaire_session(session_id))["current_index"] == 1

    # Simulate the definition becoming archived between session start and the
    # next answer callback -- bot._load_registry_fresh must observe this on
    # the VERY NEXT call (no caching across calls).
    archived_registry = questionnaires.load_registry(FIXTURE_DIR)
    archived_registry.by_id["demo_anxiety_v1"]["status"] = "archived"
    monkeypatch.setattr(bot, "_load_registry_fresh", lambda: archived_registry)

    cb = FakeCallback(user, msg, data=f"q:a:{session_id}:1:a1")
    asyncio.run(bot.cb_questionnaire_answer(cb))

    data = asyncio.run(database.export_all_personal_data(1))
    # Still only the ONE response from before invalidation -- no corruption,
    # no partial/duplicate write.
    assert len(data["questionnaire_responses"]) == 1
    session = asyncio.run(database.get_questionnaire_session(session_id))
    assert session["current_index"] == 1   # not advanced
    assert session["status"] == "active"   # not silently completed/corrupted
    assert msg.answers[-1][0] == bot.questionnaire_ux.not_available_text("ru")


# ── callback_data length (<=64 bytes) for every format ────────────────────────
def test_all_callback_formats_stay_under_64_bytes():
    user = FakeUser(1)
    msg = FakeMessage(user)

    # q:l
    asyncio.run(bot.cmd_questionnaire(msg))
    kb = msg.answers[-1][1]["reply_markup"]
    for row in kb.inline_keyboard:
        for btn in row:
            assert len(btn.callback_data.encode("utf-8")) <= 64

    # q:c:<cat>
    cb = FakeCallback(user, msg, data="q:c:anxiety")
    asyncio.run(bot.cb_questionnaire_category(cb))
    kb = msg.answers[-1][1]["reply_markup"]
    for row in kb.inline_keyboard:
        for btn in row:
            assert len(btn.callback_data.encode("utf-8")) <= 64

    # q:d:<qid>
    cb = FakeCallback(user, msg, data="q:d:demo_anxiety_v1")
    asyncio.run(bot.cb_questionnaire_detail(cb))
    kb = msg.answers[-1][1]["reply_markup"]
    for row in kb.inline_keyboard:
        for btn in row:
            assert len(btn.callback_data.encode("utf-8")) <= 64

    # q:s:<qid> (button embedded above already covered "q:s:..."); q:a/.b/.x/.p
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data="q:s:demo_anxiety_v1")))
    kb = msg.answers[-1][1]["reply_markup"]
    for row in kb.inline_keyboard:
        for btn in row:
            assert len(btn.callback_data.encode("utf-8")) <= 64
            assert btn.callback_data.encode("utf-8").__len__() <= 64

    session_id = asyncio.run(_sessions_for(1))[0][0]
    for fmt in (f"q:b:{session_id}", f"q:p:{session_id}", f"q:x:{session_id}"):
        assert len(fmt.encode("utf-8")) <= 64
