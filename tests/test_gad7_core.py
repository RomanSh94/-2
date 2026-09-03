"""Focused GAD-7 CORE v1 definition, scoring, flow, history, and report tests."""
import asyncio
import json
import pathlib
import sqlite3
import types
from unittest.mock import ANY

import pytest

import access_control
import bot
import clinical_scoring
import config
import database
import gad7_core
import gad7_ux
import questionnaires
import questionnaire_ux


QID = gad7_core.GAD7_DEFINITION_ID
DEFINITION_PATH = (
    pathlib.Path(__file__).parents[1]
    / "questionnaire_definitions" / "gad7_ru_zolotareva_2023.json")
SYNTHETIC_DEFINITION_PATH = (
    pathlib.Path(__file__).parent / "fixtures" / "registry" / "demo_anxiety_v1.json")

ITEMS = [
    "Чувство тревоги или раздражения.",
    "Неспособность справиться со своим беспокойством.",
    "Чрезмерное беспокойство по разным поводам.",
    "Неспособность расслабляться.",
    "Ощущение такого беспокойства, что трудно найти себе место.",
    "Склонность быстро испытывать злость или раздражительность.",
    "Чувство страха, как будто может случиться что-то ужасное.",
]
OPTIONS = [
    "0 — Совсем нет",
    "1 — В течение нескольких дней",
    "2 — Более, чем половину этого времени",
    "3 — Почти каждый день",
]


class FakeUser:
    def __init__(self, uid):
        self.id = uid
        self.username = "user"


class FakeCardMessage:
    def __init__(self, user):
        self.from_user = user
        self.chat = types.SimpleNamespace(id=user.id)
        self.text = ""
        self.answers = []
        self.edits = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))

    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs))

    async def edit_reply_markup(self, **kwargs):
        pass


class FakeCallback:
    def __init__(self, user, message, data):
        self.from_user = user
        self.message = message
        self.data = data

    async def answer(self, *args, **kwargs):
        pass


def _async(value=None):
    async def _call(*args, **kwargs):
        return value
    return _call


def _definition():
    return json.loads(DEFINITION_PATH.read_text(encoding="utf-8"))


def _buttons(payload):
    keyboard = payload[1].get("reply_markup")
    if keyboard is None:
        return []
    return [(button.text, button.callback_data)
            for row in keyboard.inline_keyboard for button in row]


def _press(handler, uid, data, message=None):
    user = FakeUser(uid)
    message = message or FakeCardMessage(user)
    asyncio.run(handler(FakeCallback(user, message, data)))
    return message


def _active_session(uid):
    return asyncio.run(database.get_active_questionnaire_session(uid))


def _session_rows(uid):
    with sqlite3.connect(database.DB) as connection:
        return connection.execute(
            "SELECT id, questionnaire_id, questionnaire_version, status, current_index "
            "FROM questionnaire_sessions WHERE user_id=? ORDER BY id", (uid,)).fetchall()


def _score(values):
    definition = _definition()
    responses = [clinical_scoring.ClinicalResponse(
        item["id"], f"a{value}", value)
        for item, value in zip(definition["items"], values)]
    registry = clinical_scoring.ClinicalScorerRegistry()
    registry.register(gad7_core.Gad7Scorer())
    return clinical_scoring.score_validated_clinical_definition(
        definition, bot._load_catalog_document(), responses, registry)


def _create_completed(uid, values, completed_at=None):
    definition = _definition()
    session_id = asyncio.run(database.start_questionnaire_session(
        uid, definition["id"], definition["version"]))
    for item, value in zip(definition["items"], values):
        asyncio.run(database.record_questionnaire_response(
            uid, session_id, definition["id"], item["id"],
            f"a{value}", str(value)))
    asyncio.run(database.complete_questionnaire_session(session_id))
    if completed_at:
        with sqlite3.connect(database.DB) as connection:
            connection.execute(
                "UPDATE questionnaire_sessions SET completed_at=? WHERE id=?",
                (completed_at, session_id))
            connection.commit()
    return session_id


@pytest.fixture
def flow(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "gad7.db"))
    asyncio.run(database.init_db())
    private = tmp_path / "private_questionnaires"
    private.mkdir()
    (private / SYNTHETIC_DEFINITION_PATH.name).write_text(
        SYNTHETIC_DEFINITION_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(questionnaires, "PRIVATE_QUESTIONNAIRES_DIR", private)
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    monkeypatch.setattr(bot, "journal_guard", _async(("ok", None)))
    monkeypatch.setattr(bot, "ensure_full_access_or_closed_test", _async(True))
    monkeypatch.setattr(bot, "CallbackQuery", FakeCallback)
    monkeypatch.setattr(access_control, "DEPLOYMENT_MODE", "public")
    return _definition()


def _start_synthetic_with_answer(uid=1):
    message = _press(bot.cb_questionnaire_start, uid, "q:s:demo_anxiety_v1")
    session = _active_session(uid)
    _press(bot.cb_questionnaire_answer, uid,
           f"q:a:{session['id']}:0:a1", message)
    return session["id"], message


def test_gad7_category_button_uses_short_ru_and_en_labels(flow):
    entry = {
        "instrument_id": "gad7",
        "title_ru": flow["title"],
        "title_en": "Generalized Anxiety Disorder-7 (GAD-7)",
        "definition_id": QID,
    }
    assert _buttons(("", {"reply_markup": bot._questionnaire_category_keyboard(
        [entry], "ru")}))[0] == ("ГТР-7 (GAD-7)", f"q:d:{QID}")
    assert _buttons(("", {"reply_markup": bot._questionnaire_category_keyboard(
        [entry], "en")}))[0] == ("GAD-7", f"q:d:{QID}")


def test_stale_gad_start_with_different_active_test_shows_conflict(flow):
    source_id, _ = _start_synthetic_with_answer()

    message = _press(bot.cb_questionnaire_start, 1, f"q:s:{QID}")

    text, kwargs = message.edits[-1]
    assert text == (
        "У тебя есть незавершённый тест\n\n"
        "Продолжи его или начни новый. В этом случае незавершённый тест будет отменён.")
    buttons = _buttons((text, kwargs))
    assert buttons == [
        ("▶️ Продолжить тест", f"q:v:{source_id}"),
        ("✖️ Отменить и начать новый", ANY),
        ("← К тестам", "q:l"),
    ]
    assert "опросник" not in text.lower()
    assert "опросник" not in " ".join(label.lower() for label, _data in buttons)
    assert questionnaire_ux.not_available_text("ru") not in text
    assert _active_session(1)["id"] == source_id


def test_different_active_test_detail_shows_conflict_without_cancelling(flow):
    source_id, _ = _start_synthetic_with_answer()

    message = _press(bot.cb_questionnaire_detail, 1, f"q:d:{QID}")

    text, kwargs = message.edits[-1]
    assert text == questionnaire_ux.active_test_conflict_text("ru")
    assert _buttons((text, kwargs)) == [
        ("▶️ Продолжить тест", f"q:v:{source_id}"),
        ("✖️ Отменить и начать новый", ANY),
        ("← К тестам", "q:l"),
    ]
    source = asyncio.run(database.get_questionnaire_session(source_id))
    assert source["status"] == "active" and source["current_index"] == 1
    assert questionnaire_ux.active_test_conflict_text("en") == (
        "You have an unfinished test\n\n"
        "Continue it or start a new one. In that case, the unfinished test will be cancelled.")


def test_same_gad_session_preserves_existing_resume_and_restart_detail(flow):
    _press(bot.cb_questionnaire_start, 1, f"q:s:{QID}")
    session_id = _active_session(1)["id"]
    message = _press(bot.cb_questionnaire_detail, 1, f"q:d:{QID}")

    assert message.edits[-1][0] == gad7_ux.detail_text("ru")
    assert _buttons(message.edits[-1]) == [
        ("▶️ Продолжить — вопрос 1 из 7", f"q:s:{QID}"),
        ("🔄 Начать заново", f"q:n:{session_id}"),
        ("← К тестам", "q:l"),
    ]


def test_conflict_continue_reuses_session_index_and_answers(flow):
    source_id, _ = _start_synthetic_with_answer()
    before = asyncio.run(database.get_questionnaire_responses(source_id))
    conflict = _press(bot.cb_questionnaire_detail, 1, f"q:d:{QID}")
    continue_data = _buttons(conflict.edits[-1])[0][1]

    _press(bot.cb_questionnaire_resume_session, 1, continue_data, conflict)

    active = _active_session(1)
    assert active["id"] == source_id
    assert active["current_index"] == 1
    assert asyncio.run(database.get_questionnaire_responses(source_id)) == before
    assert "Вопрос 2 из 5" in conflict.edits[-1][0]
    assert len(_session_rows(1)) == 1


def test_conflict_continue_old_version_fails_closed_without_loop(flow):
    source_id = asyncio.run(database.start_questionnaire_session(
        1, QID, "obsolete-version"))
    conflict = _press(bot.cb_questionnaire_detail, 1, f"q:d:{QID}")
    continue_data = _buttons(conflict.edits[-1])[0][1]
    assert continue_data == f"q:v:{source_id}"

    _press(bot.cb_questionnaire_resume_session, 1, continue_data, conflict)

    assert conflict.edits[-1][0] == questionnaire_ux.not_available_text("ru")
    source = asyncio.run(database.get_questionnaire_session(source_id))
    assert source["status"] == "active" and source["current_index"] == 0
    assert asyncio.run(database.get_questionnaire_responses(source_id)) == []
    assert len(_session_rows(1)) == 1


@pytest.mark.parametrize("terminal_status", ["cancelled", "completed"])
def test_conflict_continue_wrong_user_and_terminal_session_are_silent(
        flow, terminal_status):
    source_id, _ = _start_synthetic_with_answer()
    conflict = _press(bot.cb_questionnaire_detail, 1, f"q:d:{QID}")
    continue_data = _buttons(conflict.edits[-1])[0][1]

    wrong_user = _press(bot.cb_questionnaire_resume_session, 2, continue_data)
    assert wrong_user.edits == [] and wrong_user.answers == []
    source = asyncio.run(database.get_questionnaire_session(source_id))
    assert source["status"] == "active" and source["current_index"] == 1

    if terminal_status == "cancelled":
        asyncio.run(database.cancel_questionnaire_session(source_id))
    else:
        asyncio.run(database.complete_questionnaire_session(source_id))
    stale = _press(bot.cb_questionnaire_resume_session, 1, continue_data)
    assert stale.edits == [] and stale.answers == []
    assert len(_session_rows(1)) == 1


def test_old_continue_card_cannot_resume_a_newer_active_session(flow):
    source_id, _ = _start_synthetic_with_answer()
    conflict = _press(bot.cb_questionnaire_detail, 1, f"q:d:{QID}")
    continue_data = _buttons(conflict.edits[-1])[0][1]
    asyncio.run(database.cancel_questionnaire_session(source_id))
    newer_id = asyncio.run(database.start_questionnaire_session(
        1, "demo_anxiety_v1", "1"))

    stale = _press(bot.cb_questionnaire_resume_session, 1, continue_data)

    assert stale.edits == [] and stale.answers == []
    newer = asyncio.run(database.get_questionnaire_session(newer_id))
    assert newer["status"] == "active" and newer["current_index"] == 0
    assert len(_session_rows(1)) == 2


def test_successful_switch_cancels_source_starts_one_gad_and_renders_first_item(flow):
    source_id, _ = _start_synthetic_with_answer()
    conflict = _press(bot.cb_questionnaire_detail, 1, f"q:d:{QID}")
    switch_data = _buttons(conflict.edits[-1])[1][1]
    assert len(switch_data.encode("utf-8")) <= 64

    _press(bot.cb_questionnaire_switch, 1, switch_data, conflict)

    rows = _session_rows(1)
    source = next(row for row in rows if row[0] == source_id)
    assert source[3] == "cancelled"
    active = [row for row in rows if row[3] == "active"]
    assert len(active) == 1
    assert active[0][1:3] == (QID, flow["version"])
    assert active[0][4] == 0
    assert conflict.edits[-1][0].startswith("ГТР-7 · 1 из 7\n\n" + ITEMS[0])


def test_concurrent_double_switch_creates_exactly_one_active_target(flow):
    source_id, _ = _start_synthetic_with_answer()
    conflict = _press(bot.cb_questionnaire_detail, 1, f"q:d:{QID}")
    switch_data = _buttons(conflict.edits[-1])[1][1]

    async def _double_tap():
        user = FakeUser(1)
        callbacks = [
            FakeCallback(user, FakeCardMessage(user), switch_data),
            FakeCallback(user, FakeCardMessage(user), switch_data),
        ]
        await asyncio.gather(
            *(bot.cb_questionnaire_switch(callback) for callback in callbacks))

    asyncio.run(_double_tap())

    rows = _session_rows(1)
    source = next(row for row in rows if row[0] == source_id)
    targets = [row for row in rows if row[1] == QID]
    active = [row for row in rows if row[3] == "active"]
    assert source[3] == "cancelled"
    assert len(targets) == 1
    assert len(active) == 1
    assert active[0][0] == targets[0][0]
    assert active[0][1:3] == (QID, flow["version"])
    assert active[0][4] == 0


def test_atomic_switch_rolls_back_source_when_target_insert_fails(flow):
    source_id, _ = _start_synthetic_with_answer()
    before = asyncio.run(database.get_questionnaire_responses(source_id))
    with sqlite3.connect(database.DB) as connection:
        connection.execute(
            "CREATE TRIGGER force_target_insert_failure "
            "BEFORE INSERT ON questionnaire_sessions "
            f"WHEN NEW.questionnaire_id='{QID}' "
            "BEGIN SELECT RAISE(ABORT, 'forced target insert failure'); END")
        connection.commit()

    with pytest.raises(sqlite3.IntegrityError):
        asyncio.run(database.switch_active_questionnaire_session(
            1, source_id, QID, flow["version"]))

    source = asyncio.run(database.get_questionnaire_session(source_id))
    rows = _session_rows(1)
    assert source["status"] == "active"
    assert source["current_index"] == 1
    assert asyncio.run(database.get_questionnaire_responses(source_id)) == before
    assert not any(row[1] == QID for row in rows)
    assert len([row for row in rows if row[3] == "active"]) == 1


def test_atomic_switch_fails_closed_with_multiple_preexisting_active_sessions(flow):
    source_id, _ = _start_synthetic_with_answer()
    second_id = asyncio.run(database.start_questionnaire_session(
        1, "demo_anxiety_v1", "1"))

    result = asyncio.run(database.switch_active_questionnaire_session(
        1, source_id, QID, flow["version"]))

    rows = _session_rows(1)
    assert result is None
    assert [row[0] for row in rows if row[3] == "active"] == [source_id, second_id]
    assert not any(row[1] == QID for row in rows)


def test_switch_wrong_user_and_stale_source_are_non_destructive(flow):
    source_id, _ = _start_synthetic_with_answer()
    conflict = _press(bot.cb_questionnaire_detail, 1, f"q:d:{QID}")
    switch_data = _buttons(conflict.edits[-1])[1][1]

    _press(bot.cb_questionnaire_switch, 2, switch_data)
    assert asyncio.run(database.get_questionnaire_session(source_id))["status"] == "active"
    assert _active_session(2) is None

    asyncio.run(database.cancel_questionnaire_session(source_id))
    _press(bot.cb_questionnaire_switch, 1, switch_data)
    assert _active_session(1) is None
    assert len(_session_rows(1)) == 1


def test_stale_switch_cannot_cancel_newer_active_session(flow):
    source_id, _ = _start_synthetic_with_answer()
    conflict = _press(bot.cb_questionnaire_detail, 1, f"q:d:{QID}")
    switch_data = _buttons(conflict.edits[-1])[1][1]
    asyncio.run(database.cancel_questionnaire_session(source_id))
    newer_id = asyncio.run(database.start_questionnaire_session(
        1, "demo_anxiety_v1", "1"))

    _press(bot.cb_questionnaire_switch, 1, switch_data)

    newer = asyncio.run(database.get_questionnaire_session(newer_id))
    assert newer["status"] == "active"
    assert newer["current_index"] == 0
    assert _active_session(1)["id"] == newer_id


def test_switch_rechecks_current_session_after_target_validation(flow, monkeypatch):
    source_id, _ = _start_synthetic_with_answer()
    conflict = _press(bot.cb_questionnaire_detail, 1, f"q:d:{QID}")
    switch_data = _buttons(conflict.edits[-1])[1][1]
    registry = bot._load_registry_fresh()
    original_combined_can_start = registry.combined_can_start
    changed = {"newer_id": None}

    def _change_current_during_validation(qid, manifest):
        if qid == QID and changed["newer_id"] is None:
            with sqlite3.connect(database.DB) as connection:
                connection.execute(
                    "UPDATE questionnaire_sessions SET status='cancelled' WHERE id=?",
                    (source_id,))
                cursor = connection.execute(
                    "INSERT INTO questionnaire_sessions "
                    "(user_id, questionnaire_id, questionnaire_version, status, current_index) "
                    "VALUES (?, ?, ?, 'active', 0)",
                    (1, "demo_anxiety_v1", "1"))
                changed["newer_id"] = cursor.lastrowid
                connection.commit()
        return original_combined_can_start(qid, manifest)

    registry.combined_can_start = _change_current_during_validation
    monkeypatch.setattr(bot, "_load_registry_fresh", lambda: registry)

    _press(bot.cb_questionnaire_switch, 1, switch_data)

    assert _active_session(1)["id"] == changed["newer_id"]
    assert asyncio.run(database.get_questionnaire_session(
        changed["newer_id"]))["status"] == "active"
    assert not any(row[1] == QID for row in _session_rows(1))


def test_switch_callback_is_version_aware_and_within_telegram_limit(flow):
    active = {
        "id": 9223372036854775807,
        "questionnaire_id": "demo_anxiety_v1",
        "questionnaire_version": "1",
    }
    keyboard = bot._questionnaire_active_conflict_keyboard(active, flow, "ru")
    buttons = _buttons(("", {"reply_markup": keyboard}))
    switch_data = buttons[1][1]
    changed_version = dict(flow, version="next-version")

    assert len(switch_data.encode("utf-8")) == 64
    assert len(buttons[0][1].encode("utf-8")) <= 64
    assert buttons[0][1] == f"q:v:{active['id']}"
    assert (bot._questionnaire_switch_target_token(flow)
            != bot._questionnaire_switch_target_token(changed_version))


def test_invalid_switch_target_preserves_current_session(flow, monkeypatch):
    source_id, _ = _start_synthetic_with_answer()
    conflict = _press(bot.cb_questionnaire_detail, 1, f"q:d:{QID}")
    switch_data = _buttons(conflict.edits[-1])[1][1]
    registry_without_target = bot._load_registry_fresh()
    registry_without_target.by_id.pop(QID)
    monkeypatch.setattr(bot, "_load_registry_fresh",
                        lambda: registry_without_target)

    message = _press(bot.cb_questionnaire_switch, 1, switch_data)

    source = asyncio.run(database.get_questionnaire_session(source_id))
    assert source["status"] == "active" and source["current_index"] == 1
    assert message.edits[-1][0] == questionnaire_ux.not_available_text("ru")
    assert len(_session_rows(1)) == 1


def test_governance_invalid_switch_target_preserves_current_session(flow, monkeypatch):
    source_id, _ = _start_synthetic_with_answer()
    conflict = _press(bot.cb_questionnaire_detail, 1, f"q:d:{QID}")
    switch_data = _buttons(conflict.edits[-1])[1][1]
    monkeypatch.setattr(bot, "_load_catalog_document", lambda: None)

    async def _unexpected_atomic_switch(*args, **kwargs):
        raise AssertionError("target validation must finish before the DB switch")

    monkeypatch.setattr(bot, "switch_active_questionnaire_session",
                        _unexpected_atomic_switch)

    message = _press(bot.cb_questionnaire_switch, 1, switch_data)

    source = asyncio.run(database.get_questionnaire_session(source_id))
    assert source["status"] == "active" and source["current_index"] == 1
    assert message.edits[-1][0] == questionnaire_ux.not_available_text("ru")
    assert len(_session_rows(1)) == 1


def test_definition_preserves_exact_content_options_and_reference_period(flow):
    assert flow["title"] == (
        "Опросник генерализованного тревожного расстройства — ГТР-7 (GAD-7)")
    assert flow["instruction"] == (
        "Оцените, пожалуйста, насколько часто следующие проблемы беспокоили вас\n"
        "в течение прошедших двух недель. Для ответов используйте следующую шкалу:")
    assert [item["text"] for item in flow["items"]] == ITEMS
    assert flow["reference_period"] == "past_2_weeks"
    for item in flow["items"]:
        assert [option["label"] for option in item["options"]] == OPTIONS
        assert [option["value"] for option in item["options"]] == ["0", "1", "2", "3"]
    registry = questionnaires.load_registry()
    assert registry.combined_can_start(QID, bot._load_catalog_document()) is True


@pytest.mark.parametrize(("values", "expected"), [
    ([0] * 7, 0),
    ([3] * 7, 21),
    ([0, 1, 2, 3, 1, 2, 3], 12),
])
def test_validated_total_is_simple_sum(flow, values, expected):
    result = _score(values)
    assert result.raw_total == expected
    assert result.transformed_total is None
    assert dict(result.subscales) == {}


@pytest.mark.parametrize(("score", "band"), [
    (0, "minimal"), (4, "minimal"),
    (5, "mild"), (9, "mild"),
    (10, "moderate"), (14, "moderate"),
    (15, "severe"), (21, "severe"),
])
def test_exact_score_bands(score, band):
    assert gad7_core.band_for_score(score) == band


def test_exact_visible_ru_band_labels():
    assert [gad7_core.band_label_ru(score) for score in (0, 5, 10, 15)] == [
        "минимальная", "лёгкая", "умеренная", "тяжёлая"]


def test_incomplete_and_invalid_responses_fail_closed(flow):
    definition = _definition()
    registry = clinical_scoring.ClinicalScorerRegistry()
    registry.register(gad7_core.Gad7Scorer())
    incomplete = [clinical_scoring.ClinicalResponse("gad7_01", "a0", 0)]
    with pytest.raises(clinical_scoring.ClinicalScoringError):
        clinical_scoring.score_validated_clinical_definition(
            definition, bot._load_catalog_document(), incomplete, registry)
    invalid = [clinical_scoring.ClinicalResponse(
        item["id"], ("a9" if index == 0 else "a0"), (9 if index == 0 else 0))
        for index, item in enumerate(definition["items"])]
    with pytest.raises(clinical_scoring.ClinicalScoringError):
        clinical_scoring.score_validated_clinical_definition(
            definition, bot._load_catalog_document(), invalid, registry)


def test_detail_and_question_use_exact_single_card_contract(flow):
    detail = _press(bot.cb_questionnaire_detail, 1, f"q:d:{QID}")
    assert detail.edits[-1][0] == gad7_ux.detail_text("ru")
    assert _buttons(detail.edits[-1]) == [
        ("Начать", f"q:s:{QID}"), ("← К тестам", "q:l")]

    question = _press(bot.cb_questionnaire_start, 1, f"q:s:{QID}")
    text, kwargs = question.edits[-1]
    assert text == ("ГТР-7 · 1 из 7\n\n"
                    f"{ITEMS[0]}\n\nЗа последние две недели:\n\n"
                    + "\n".join(OPTIONS))
    assert _buttons((text, kwargs)) == [
        ("0", f"q:a:{_active_session(1)['id']}:0:a0"),
        ("1", f"q:a:{_active_session(1)['id']}:0:a1"),
        ("2", f"q:a:{_active_session(1)['id']}:0:a2"),
        ("3", f"q:a:{_active_session(1)['id']}:0:a3"),
        ("⬅️ Назад", f"q:b:{_active_session(1)['id']}"),
        ("⏸ Отложить", f"q:p:{_active_session(1)['id']}"),
    ]


def test_back_pause_resume_and_abort_reuse_existing_session_flow(flow):
    message = _press(bot.cb_questionnaire_start, 1, f"q:s:{QID}")
    session_id = _active_session(1)["id"]
    _press(bot.cb_questionnaire_answer, 1, f"q:a:{session_id}:0:a1", message)
    assert _active_session(1)["current_index"] == 1
    _press(bot.cb_questionnaire_back, 1, f"q:b:{session_id}", message)
    assert _active_session(1)["current_index"] == 0
    assert message.edits[-1][0].startswith("ГТР-7 · 1 из 7")

    _press(bot.cb_questionnaire_pause, 1, f"q:p:{session_id}", message)
    assert message.edits[-1][0] == questionnaire_ux.paused_text("ru")
    assert _buttons(message.edits[-1]) == [
        ("▶️ Продолжить", f"q:s:{QID}"),
        ("✖️ Прервать", f"q:x:{session_id}"),
    ]
    _press(bot.cb_questionnaire_start, 1, f"q:s:{QID}", message)
    assert _active_session(1)["id"] == session_id
    _press(bot.cb_questionnaire_cancel, 1, f"q:x:{session_id}", message)
    assert _active_session(1) is None


def test_completion_replaces_card_has_no_discussion_and_no_score_crisis(flow, monkeypatch):
    async def unexpected_crisis(*args, **kwargs):
        raise AssertionError("score must not trigger crisis routing")

    monkeypatch.setattr(bot, "send_crisis", unexpected_crisis)
    message = _press(bot.cb_questionnaire_start, 1, f"q:s:{QID}")
    session_id = _active_session(1)["id"]
    for step in range(7):
        _press(bot.cb_questionnaire_answer, 1,
               f"q:a:{session_id}:{step}:a3", message)
    session = asyncio.run(database.get_questionnaire_session(session_id))
    assert session["status"] == "completed"
    assert message.answers == []
    assert message.edits[-1][0] == gad7_ux.result_text(21, "тяжёлая", "ru")
    callback_data = [data for _text, data in _buttons(message.edits[-1])]
    assert f"q:o:{session_id}" in callback_data
    assert not any(data.startswith("q:m:") for data in callback_data)


def test_result_copy_low_and_high_is_non_diagnostic():
    low = gad7_ux.result_text(4, "минимальная", "ru")
    high = gad7_ux.result_text(10, "умеренная", "ru")
    assert "Общий балл — 4 из 21" in low
    assert "более подробной оценки" not in low
    assert "Общий балл — 10 из 21" in high
    assert "Такой результат может быть основанием" in high
    for text in (low, high):
        assert text.endswith("Результат не является диагнозом.")
        assert "у вас ГТР" not in text
        assert "вероятно ГТР" not in text
        assert "клиническая тревога" not in text


def test_owned_completed_result_reopens_and_other_user_is_silent(flow):
    session_id = _create_completed(1, [1] * 7)
    owner = _press(bot.cb_questionnaire_result, 1, f"q:r:{session_id}")
    assert "Общий балл — 7 из 21" in owner.edits[-1][0]
    other = _press(bot.cb_questionnaire_result, 2, f"q:r:{session_id}")
    assert other.edits == []
    assert other.answers == []


def test_active_result_and_manual_discussion_entry_fail_closed(flow):
    message = _press(bot.cb_questionnaire_start, 1, f"q:s:{QID}")
    session_id = _active_session(1)["id"]
    result = _press(bot.cb_questionnaire_result, 1, f"q:r:{session_id}")
    assert result.edits[-1][0] == questionnaire_ux.not_available_text("ru")
    discussion = _press(bot.cb_questionnaire_discuss_menu, 1, f"q:m:{session_id}")
    assert discussion.answers[-1][0] == questionnaire_ux.not_available_text("ru")


def test_history_is_completed_owner_scoped_newest_first_and_reloads(flow):
    old_id = _create_completed(1, [0] * 7, "2026-01-01 10:00:00")
    new_id = _create_completed(1, [2] * 7, "2026-02-01 10:00:00")
    _create_completed(2, [3] * 7, "2026-03-01 10:00:00")
    cancelled_id = asyncio.run(database.start_questionnaire_session(
        1, QID, flow["version"]))
    asyncio.run(database.cancel_questionnaire_session(cancelled_id))
    active_id = asyncio.run(database.start_questionnaire_session(
        1, QID, flow["version"]))

    history = _press(bot.cb_results_tests, 1, "results:tests")
    buttons = _buttons(history.edits[-1])
    assert buttons[:2] == [
        ("GAD-7 · 2026-02-01", f"results:test:{new_id}"),
        ("GAD-7 · 2026-01-01", f"results:test:{old_id}"),
    ]
    assert all(str(cancelled_id) not in data and str(active_id) not in data
               for _text, data in buttons)
    assert all("2026-03-01" not in text for text, _data in buttons)

    _press(bot.cb_results_test, 1, f"results:test:{new_id}", history)
    assert "Общий балл — 14 из 21" in history.edits[-1][0]
    assert not any(data.startswith("q:m:")
                   for _text, data in _buttons(history.edits[-1]))


def test_specialist_report_uses_validated_total_band_and_reference_period(flow):
    session_id = _create_completed(1, [2] * 7)
    report = _press(bot.cb_questionnaire_specialist_report, 1, f"q:o:{session_id}")
    text = report.answers[-1][0]
    assert "ГТР-7 (GAD-7)" in text
    assert "Общий балл: 14 / 21" in text
    assert "Выраженность тревожных симптомов: умеренная" in text
    assert "Период оценки: последние 2 недели" in text
    assert "Это не диагноз" in text


def test_gad7_is_not_exposed_as_a_dass_recommendation(flow, monkeypatch):
    monkeypatch.setattr(bot, "_available_questionnaire_catalog", _async({
        "anxiety": [{"instrument_id": "gad7", "definition_id": QID}],
        "depression": [], "stress_burnout": [],
    }))
    assert asyncio.run(bot._dass21_recommendation_options(1)) == {}


def test_existing_dass_result_renderer_is_unchanged():
    assert questionnaire_ux.dass21_result_text(
        {"depression": 2, "anxiety": 4, "stress": 6}, "ru").startswith(
            "DASS-21 — результат\n\nДепрессия — 2\nТревога — 4\nСтресс — 6")
