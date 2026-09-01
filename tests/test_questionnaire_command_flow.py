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


# ── UI polish V1 supersedes the round-3 contract above: Help no longer
# exposes Questionnaire Core (or any other main-navigation shortcut) --
# see tests/test_help_command.py for the exhaustive Help-button-set
# assertion (About + Privacy only, in that order). Psychological tests
# remain reachable through the REAL persistent-lower-menu dispatcher
# handler, bot.lower_menu_tests, which delegates to cmd_questionnaire
# completely unchanged. ─────────────────────────────────────────────────────
def test_questionnaire_not_reachable_from_help():
    msg = FakeMessage(FakeUser(1))
    asyncio.run(bot.cmd_help(msg))
    text, kw = msg.answers[0]
    assert "/questionnaire" not in text
    kb = kw["reply_markup"]
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "q:l" not in datas
    assert datas == ["about:hub", "privacy:hub"]


def test_questionnaire_reachable_from_persistent_lower_menu():
    # Exercises the REAL dispatcher-registered lower-menu handler
    # (bot.lower_menu_tests) directly -- not a hand-rolled equivalent --
    # confirming psychological tests are still one tap away from the
    # persistent lower menu's "🧠 Психологические тесты" button, exactly as
    # before this UI change.
    msg = FakeMessage(FakeUser(1))
    asyncio.run(bot.lower_menu_tests(msg, None))
    assert msg.answers
    assert msg.answers[-1][0] == bot.questionnaire_ux.list_text("ru")


# ── product gate ─────────────────────────────────────────────────────────────
def test_questionnaire_requires_product_gate():
    user = FakeUser(424242)   # UNKNOWN under personal_use (OWNER_USER_ID=1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    assert msg.answers
    assert "Опросники" not in msg.answers[0][0]
    rows = asyncio.run(_sessions_for(424242))
    assert rows == []


def test_public_ordinary_user_reaches_questionnaire_core(monkeypatch):
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "public")
    msg = FakeMessage(FakeUser(424242))
    asyncio.run(bot.cmd_questionnaire(msg))
    assert msg.answers[-1][0] == bot.questionnaire_ux.list_text("ru")


def test_public_generic_result_keyboard_hides_discussion(monkeypatch):
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "public")
    callback_data = [
        button.callback_data
        for row in bot._questionnaire_result_keyboard(7, "ru").inline_keyboard
        for button in row
    ]
    assert "q:m:7" not in callback_data


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
def test_questionnaire_list_hides_categories_without_proven_ready_instruments():
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    text, kw = msg.answers[0]
    assert "Психологические тесты" in text
    kb = kw["reply_markup"]
    callback_datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert callback_datas == ["menu:back"]


# NOTE: synthetic registry demos now live under the professional catalog's
# "self_observation" section (categories anxiety/stress/etc. render governance-
# manifest INFO entries, not startable registry demos). These four tests were
# updated to press q:c:self_observation; the registry hide/show invariants are
# unchanged.
def _button_texts(kw):
    kb = kw["reply_markup"]
    return [btn.text for row in kb.inline_keyboard for btn in row]


def test_unproven_active_registry_questionnaire_is_not_publicly_catalogued():
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="q:c:anxiety")
    asyncio.run(bot.cb_questionnaire_category(cb))
    _, kw = msg.answers[-1]
    assert "Demo Anxiety Check" not in _button_texts(kw)
    kb = kw["reply_markup"]
    callback_datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "q:d:demo_anxiety_v1" not in callback_datas


def test_archived_questionnaire_hidden_from_category_list():
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="q:c:self_observation")
    asyncio.run(bot.cb_questionnaire_category(cb))
    text, kw = msg.answers[-1]
    assert "Demo Archived Check" not in text
    assert "Demo Archived Check" not in _button_texts(kw)


def test_restricted_questionnaire_hidden_from_category_list():
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="q:c:self_observation")
    asyncio.run(bot.cb_questionnaire_category(cb))
    text, kw = msg.answers[-1]
    assert "Demo Restricted Check" not in text
    assert "Demo Restricted Check" not in _button_texts(kw)


def test_draft_questionnaire_hidden_from_category_list():
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="q:c:self_observation")
    asyncio.run(bot.cb_questionnaire_category(cb))
    text, kw = msg.answers[-1]
    assert "Demo Draft Check" not in text
    assert "Demo Draft Check" not in _button_texts(kw)


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
    assert "Мне было непросто отдохнуть вечером (синтетический вопрос)" in text


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
    # Owner-review UX correction: "Другой опросник"/q:l and "🏠 В меню"/
    # menu:back are gone from the completion card -- q:l edits in place
    # (would overwrite this very card) and menu:back opens Help, not a
    # questionnaire "home". q:t sends the catalog as a new message instead.
    assert "q:t" in callback_datas
    assert "q:l" not in callback_datas and "menu:back" not in callback_datas


# ── session ownership ─────────────────────────────────────────────────────────
def test_answer_callback_rejects_wrong_user_in_public_mode(monkeypatch):
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "public")
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


# ── owner-review UX correction: pause is state-preserving, restart is the
# only intentional destructive reset ─────────────────────────────────────────

async def _responses_for(session_id):
    import sqlite3
    con = sqlite3.connect(database.DB)
    rows = con.execute(
        "SELECT item_id, answer_id FROM questionnaire_responses WHERE session_id=? "
        "ORDER BY item_id", (session_id,)).fetchall()
    con.close()
    return rows


def test_pause_keeps_session_active_and_preserves_current_index():
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data="q:s:demo_anxiety_v1")))
    session_id = asyncio.run(_sessions_for(1))[0][0]
    asyncio.run(bot.cb_questionnaire_answer(FakeCallback(user, msg, data=f"q:a:{session_id}:0:a1")))
    asyncio.run(bot.cb_questionnaire_answer(FakeCallback(user, msg, data=f"q:a:{session_id}:1:a1")))

    asyncio.run(bot.cb_questionnaire_pause(FakeCallback(user, msg, data=f"q:p:{session_id}")))

    session = asyncio.run(database.get_questionnaire_session(session_id))
    assert session["status"] == "active"
    assert session["current_index"] == 2
    # Owner-review UX correction: pause transforms the card in place -- no
    # "type /questionnaire" hint, no separate confirmation message.
    assert msg.answers[-1][0] == bot.questionnaire_ux.paused_text("ru")
    assert "/questionnaire" not in msg.answers[-1][0]


def test_resume_after_pause_renders_question_n_not_one():
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data="q:s:demo_anxiety_v1")))
    session_id = asyncio.run(_sessions_for(1))[0][0]
    asyncio.run(bot.cb_questionnaire_answer(FakeCallback(user, msg, data=f"q:a:{session_id}:0:a1")))
    asyncio.run(bot.cb_questionnaire_answer(FakeCallback(user, msg, data=f"q:a:{session_id}:1:a1")))
    asyncio.run(bot.cb_questionnaire_pause(FakeCallback(user, msg, data=f"q:p:{session_id}")))

    resume_msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, resume_msg, data="q:s:demo_anxiety_v1")))

    text, _ = resume_msg.answers[-1]
    assert "Вопрос 3 из 5" in text   # current_index==2 -> question 3, not 1
    assert len(asyncio.run(_sessions_for(1))) == 1   # resumed, no duplicate session


def test_answers_survive_pause_and_resume():
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data="q:s:demo_anxiety_v1")))
    session_id = asyncio.run(_sessions_for(1))[0][0]
    asyncio.run(bot.cb_questionnaire_answer(FakeCallback(user, msg, data=f"q:a:{session_id}:0:a1")))
    before = asyncio.run(_responses_for(session_id))

    asyncio.run(bot.cb_questionnaire_pause(FakeCallback(user, msg, data=f"q:p:{session_id}")))
    asyncio.run(bot.cb_questionnaire_start(
        FakeCallback(user, FakeMessage(user), data="q:s:demo_anxiety_v1")))

    assert asyncio.run(_responses_for(session_id)) == before
    assert len(before) == 1


def test_back_revise_survives_pause_and_resume():
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data="q:s:demo_anxiety_v1")))
    session_id = asyncio.run(_sessions_for(1))[0][0]
    asyncio.run(bot.cb_questionnaire_answer(FakeCallback(user, msg, data=f"q:a:{session_id}:0:a1")))
    asyncio.run(bot.cb_questionnaire_back(FakeCallback(user, msg, data=f"q:b:{session_id}")))
    asyncio.run(bot.cb_questionnaire_answer(FakeCallback(user, msg, data=f"q:a:{session_id}:0:a2")))

    asyncio.run(bot.cb_questionnaire_pause(FakeCallback(user, msg, data=f"q:p:{session_id}")))
    asyncio.run(bot.cb_questionnaire_start(
        FakeCallback(user, FakeMessage(user), data="q:s:demo_anxiety_v1")))

    responses = asyncio.run(_responses_for(session_id))
    assert len(responses) == 1     # revision replaced the prior answer, not duplicated
    assert responses[0][1] == "a2"  # latest value wins, even across a pause/resume


def test_detail_screen_offers_continue_and_restart_with_active_session():
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data="q:s:demo_anxiety_v1")))
    session_id = asyncio.run(_sessions_for(1))[0][0]
    asyncio.run(bot.cb_questionnaire_answer(FakeCallback(user, msg, data=f"q:a:{session_id}:0:a1")))
    asyncio.run(bot.cb_questionnaire_pause(FakeCallback(user, msg, data=f"q:p:{session_id}")))

    detail_msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_detail(FakeCallback(user, detail_msg, data="q:d:demo_anxiety_v1")))
    text, kw = detail_msg.answers[-1]
    kb = kw["reply_markup"]
    button_texts = [b.text for row in kb.inline_keyboard for b in row]
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]

    assert any("вопрос 2 из 5" in t.lower() for t in button_texts)
    assert "q:s:demo_anxiety_v1" in datas   # Continue still routes through the existing resume path
    assert f"q:n:{session_id}" in datas
    assert "q:l" in datas
    assert str(session_id) not in text
    assert all(str(session_id) not in t for t in button_texts)


def test_restart_creates_fresh_session_at_question_one():
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data="q:s:demo_anxiety_v1")))
    old_session_id = asyncio.run(_sessions_for(1))[0][0]
    asyncio.run(bot.cb_questionnaire_answer(FakeCallback(user, msg, data=f"q:a:{old_session_id}:0:a1")))
    asyncio.run(bot.cb_questionnaire_answer(FakeCallback(user, msg, data=f"q:a:{old_session_id}:1:a1")))

    restart_msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_restart(
        FakeCallback(user, restart_msg, data=f"q:n:{old_session_id}")))

    text, _ = restart_msg.answers[-1]
    assert "Вопрос 1 из 5" in text

    rows = asyncio.run(_sessions_for(1))
    assert len(rows) == 2
    old_row = next(r for r in rows if r[0] == old_session_id)
    assert old_row[3] == "cancelled"
    new_row = next(r for r in rows if r[0] != old_session_id)
    assert new_row[3] == "active" and new_row[4] == 0
    assert asyncio.run(_responses_for(new_row[0])) == []
    assert len(asyncio.run(_responses_for(old_session_id))) == 2   # old answers untouched


def test_old_session_callbacks_after_restart_do_not_mutate_new_session():
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data="q:s:demo_anxiety_v1")))
    old_session_id = asyncio.run(_sessions_for(1))[0][0]
    asyncio.run(bot.cb_questionnaire_answer(FakeCallback(user, msg, data=f"q:a:{old_session_id}:0:a1")))

    asyncio.run(bot.cb_questionnaire_restart(
        FakeCallback(user, FakeMessage(user), data=f"q:n:{old_session_id}")))
    new_session_id = next(r[0] for r in asyncio.run(_sessions_for(1)) if r[0] != old_session_id)

    stale_msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_answer(
        FakeCallback(user, stale_msg, data=f"q:a:{old_session_id}:0:a1")))
    asyncio.run(bot.cb_questionnaire_back(
        FakeCallback(user, stale_msg, data=f"q:b:{old_session_id}")))
    asyncio.run(bot.cb_questionnaire_pause(
        FakeCallback(user, stale_msg, data=f"q:p:{old_session_id}")))
    asyncio.run(bot.cb_questionnaire_restart(
        FakeCallback(user, stale_msg, data=f"q:n:{old_session_id}")))

    new_session = asyncio.run(database.get_questionnaire_session(new_session_id))
    assert new_session["current_index"] == 0
    assert new_session["status"] == "active"
    assert asyncio.run(_responses_for(new_session_id)) == []
    assert len(asyncio.run(_sessions_for(1))) == 2   # no third session sneaked in


def test_live_question_keyboard_has_back_and_pause_not_cancel():
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data="q:s:demo_anxiety_v1")))
    session_id = asyncio.run(_sessions_for(1))[0][0]
    _, kw = msg.answers[-1]
    buttons = [(b.text, b.callback_data)
               for row in kw["reply_markup"].inline_keyboard for b in row]
    datas = [callback_data for _, callback_data in buttons]
    assert any(d.startswith("q:b:") for d in datas)
    assert any(d.startswith("q:p:") for d in datas)
    assert not any(d.startswith("q:x:") for d in datas)
    assert buttons[-2:] == [
        ("⬅️ Назад", f"q:b:{session_id}"),
        ("⏸ Отложить", f"q:p:{session_id}"),
    ]


def test_pause_produces_paused_card_with_continue_and_cancel():
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data="q:s:demo_anxiety_v1")))
    session_id = asyncio.run(_sessions_for(1))[0][0]
    asyncio.run(bot.cb_questionnaire_pause(FakeCallback(user, msg, data=f"q:p:{session_id}")))

    text, kw = msg.answers[-1]
    assert text == bot.questionnaire_ux.paused_text("ru")
    buttons = [(b.text, b.callback_data) for row in kw["reply_markup"].inline_keyboard for b in row]
    assert buttons == [
        ("▶️ Продолжить", "q:s:demo_anxiety_v1"),
        ("✖️ Прервать", f"q:x:{session_id}"),
    ]


def test_continue_button_on_paused_card_resumes_exact_saved_question():
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data="q:s:demo_anxiety_v1")))
    session_id = asyncio.run(_sessions_for(1))[0][0]
    asyncio.run(bot.cb_questionnaire_answer(FakeCallback(user, msg, data=f"q:a:{session_id}:0:a1")))
    asyncio.run(bot.cb_questionnaire_answer(FakeCallback(user, msg, data=f"q:a:{session_id}:1:a1")))
    asyncio.run(bot.cb_questionnaire_pause(FakeCallback(user, msg, data=f"q:p:{session_id}")))
    _, kw = msg.answers[-1]
    continue_data = next(b.callback_data for row in kw["reply_markup"].inline_keyboard
                          for b in row if b.text == "▶️ Продолжить")

    # Tapping the paused card's own Continue button -- not a fresh /questionnaire.
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data=continue_data)))

    text, _ = msg.answers[-1]
    assert "Вопрос 3 из 5" in text   # current_index==2 -> question 3, not 1
    assert len(asyncio.run(_sessions_for(1))) == 1   # still the one resumed session


def test_cancel_button_on_paused_card_cancels_only_the_callers_session():
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data="q:s:demo_anxiety_v1")))
    session_id = asyncio.run(_sessions_for(1))[0][0]
    asyncio.run(bot.cb_questionnaire_pause(FakeCallback(user, msg, data=f"q:p:{session_id}")))
    _, kw = msg.answers[-1]
    cancel_data = next(b.callback_data for row in kw["reply_markup"].inline_keyboard
                        for b in row if b.text == "✖️ Прервать")
    assert cancel_data == f"q:x:{session_id}"

    other = FakeUser(2)
    other_msg = FakeMessage(other)
    # personal_use mode (see _common) only grants OWNER_USER_ID (1) full
    # access automatically; a second genuine user needs the same permanent
    # invite grant real users get via cmd_start's deep-link handling.
    asyncio.run(database.grant_user_access(2))
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(other, other_msg, data="q:s:demo_anxiety_v1")))
    other_session_id = asyncio.run(_sessions_for(2))[0][0]

    # Tapping the paused card's own Cancel button -- reuses q:x unchanged.
    asyncio.run(bot.cb_questionnaire_cancel(FakeCallback(user, msg, data=cancel_data)))

    assert asyncio.run(database.get_questionnaire_session(session_id))["status"] == "cancelled"
    assert asyncio.run(database.get_questionnaire_session(other_session_id))["status"] == "active"


def test_wrong_user_continue_button_never_resumes_someone_elses_session():
    owner = FakeUser(1)
    msg = FakeMessage(owner)
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(owner, msg, data="q:s:demo_anxiety_v1")))
    session_id = asyncio.run(_sessions_for(1))[0][0]
    asyncio.run(bot.cb_questionnaire_answer(FakeCallback(owner, msg, data=f"q:a:{session_id}:0:a1")))
    asyncio.run(bot.cb_questionnaire_pause(FakeCallback(owner, msg, data=f"q:p:{session_id}")))

    # The paused card's "Продолжить" is q:s:<qid> -- an attacker who somehow
    # sends the SAME callback_data can only ever resume THEIR OWN active
    # session, never the owner's: q:s looks up the active session by the
    # TAPPING user's id, never by a session id carried in callback_data.
    attacker = FakeUser(999)
    attacker_msg = FakeMessage(attacker)
    # personal_use mode (see _common) only grants OWNER_USER_ID (1) full
    # access automatically; the attacker needs the same permanent invite
    # grant a real second user gets via cmd_start's deep-link handling --
    # otherwise this test would pass for the wrong reason (blocked by the
    # access gate rather than by session-ownership scoping, which is what
    # this test exists to prove).
    asyncio.run(database.grant_user_access(999))
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(attacker, attacker_msg, data="q:s:demo_anxiety_v1")))

    attacker_rows = asyncio.run(_sessions_for(999))
    assert len(attacker_rows) == 1
    assert attacker_rows[0][0] != session_id
    owner_session = asyncio.run(database.get_questionnaire_session(session_id))
    assert owner_session["status"] == "active" and owner_session["current_index"] == 1


def test_wrong_user_cannot_pause_or_restart_session():
    owner = FakeUser(1)
    msg = FakeMessage(owner)
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(owner, msg, data="q:s:demo_anxiety_v1")))
    session_id = asyncio.run(_sessions_for(1))[0][0]

    attacker = FakeUser(999)
    attacker_msg = FakeMessage(attacker)
    asyncio.run(bot.cb_questionnaire_pause(
        FakeCallback(attacker, attacker_msg, data=f"q:p:{session_id}")))
    asyncio.run(bot.cb_questionnaire_restart(
        FakeCallback(attacker, attacker_msg, data=f"q:n:{session_id}")))

    session = asyncio.run(database.get_questionnaire_session(session_id))
    assert session["status"] == "active"
    assert session["current_index"] == 0
    assert len(asyncio.run(_sessions_for(1))) == 1   # untouched; no session created either


def test_pause_edit_failure_falls_back_to_new_message_with_paused_card():
    from aiogram.exceptions import TelegramBadRequest
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data="q:s:demo_anxiety_v1")))
    session_id = asyncio.run(_sessions_for(1))[0][0]

    async def _raise_bad_request(text, **kw):
        raise TelegramBadRequest(method=None, message="message can't be edited")
    msg.edit_text = _raise_bad_request

    asyncio.run(bot.cb_questionnaire_pause(FakeCallback(user, msg, data=f"q:p:{session_id}")))
    assert msg.answers[-1][0] == bot.questionnaire_ux.paused_text("ru")


def test_pause_edit_unexpected_error_is_not_swallowed():
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data="q:s:demo_anxiety_v1")))
    session_id = asyncio.run(_sessions_for(1))[0][0]

    async def _raise_runtime_error(text, **kw):
        raise RuntimeError("boom")
    msg.edit_text = _raise_runtime_error

    with pytest.raises(RuntimeError):
        asyncio.run(bot.cb_questionnaire_pause(FakeCallback(user, msg, data=f"q:p:{session_id}")))


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
    for fmt in (f"q:b:{session_id}", f"q:p:{session_id}", f"q:x:{session_id}", f"q:n:{session_id}"):
        assert len(fmt.encode("utf-8")) <= 64
