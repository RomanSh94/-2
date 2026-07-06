"""Questionnaire Result Screens — PR B, hard kill switch (default OFF).

Handler-level tests against the REAL bot.py handlers and a REAL tmp sqlite DB,
following the exact conventions of tests/test_questionnaire_command_flow.py
(FakeUser/FakeMessage/FakeCallback, bot._load_registry_fresh monkeypatched to
the tests/fixtures/registry/ synthetic-only directory).

Scope reminder (see CLAUDE.md): this PR is NOT activation. The flag defaults
to False and no .env change enables it. When False, user-visible behavior
must be byte-for-byte identical to PR A's completion screen -- tested
explicitly below.
"""
import asyncio
import pathlib
import types

import pytest

import bot
import config
import database
import questionnaires
import questionnaire_ux
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
    # Flag defaults false in every test unless a test explicitly flips it.
    monkeypatch.setattr(config, "QUESTIONNAIRE_INTERPRETATION_ENABLED", False)


async def _sessions_for(uid):
    import sqlite3
    con = sqlite3.connect(database.DB)
    rows = con.execute(
        "SELECT id, questionnaire_id, questionnaire_version, status, current_index "
        "FROM questionnaire_sessions WHERE user_id=?", (uid,)).fetchall()
    con.close()
    return rows


def _complete_flow(user, msg, qid="demo_anxiety_v1", answer="a1", n_items=5):
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data=f"q:s:{qid}")))
    session_id = asyncio.run(_sessions_for(user.id))[-1][0]
    for step in range(n_items):
        cb = FakeCallback(user, msg, data=f"q:a:{session_id}:{step}:{answer}")
        asyncio.run(bot.cb_questionnaire_answer(cb))
    return session_id


# ── 1. config default is false ───────────────────────────────────────────────
def test_config_flag_defaults_false(monkeypatch):
    monkeypatch.delenv("QUESTIONNAIRE_INTERPRETATION_ENABLED", raising=False)
    import importlib
    reloaded = importlib.reload(config)
    assert reloaded.QUESTIONNAIRE_INTERPRETATION_ENABLED is False
    importlib.reload(config)  # restore for subsequent tests using real config


# ── 2. flag-false completion is byte-identical to PR A ───────────────────────
def test_flag_off_completion_is_byte_identical_to_pr_a():
    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg)
    final_text, kw = msg.answers[-1]
    assert final_text == bot.questionnaire_ux.completion_text("ru")
    kb = kw["reply_markup"]
    callback_datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert callback_datas == ["q:l", "menu:back"]
    session = asyncio.run(database.get_questionnaire_session(session_id))
    assert session["status"] == "completed"


# ── flag-false: q:r/q:k/q:e reveal nothing ───────────────────────────────────
def test_flag_off_q_r_reveals_nothing():
    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg, qid="demo_result_eligible_v1", n_items=3)
    cb = FakeCallback(user, msg, data=f"q:r:{session_id}")
    asyncio.run(bot.cb_questionnaire_result(cb))
    text, _ = msg.answers[-1]
    assert text == questionnaire_ux.not_available_text("ru")
    assert "/" not in text  # no "score / max" anywhere


def test_flag_off_q_k_reveals_nothing():
    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg, qid="demo_result_eligible_v1", n_items=3)
    cb = FakeCallback(user, msg, data=f"q:k:{session_id}")
    asyncio.run(bot.cb_questionnaire_calculations(cb))
    text, _ = msg.answers[-1]
    assert text == questionnaire_ux.not_available_text("ru")


def test_flag_off_q_e_reveals_nothing():
    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg, qid="demo_result_eligible_v1", n_items=3)
    cb = FakeCallback(user, msg, data=f"q:e:{session_id}")
    asyncio.run(bot.cb_questionnaire_explanation(cb))
    text, _ = msg.answers[-1]
    assert text == questionnaire_ux.not_available_text("ru")


# ── 5. flag-true + eligible shows result with score + bar ────────────────────
def test_flag_on_eligible_completion_shows_result_screen(monkeypatch):
    monkeypatch.setattr(config, "QUESTIONNAIRE_INTERPRETATION_ENABLED", True)
    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg, qid="demo_result_eligible_v1", answer="a1", n_items=3)
    text, kw = msg.answers[-1]
    assert "Результат готов" in text
    assert "3 / 9" in text  # 3 items x value 1 = 3; max = 3 items x 3 = 9
    assert "🟩" in text or "🟨" in text or "🟧" in text or "🟥" in text
    assert "⬜" in text


def test_flag_on_eligible_q_r_shows_result(monkeypatch):
    monkeypatch.setattr(config, "QUESTIONNAIRE_INTERPRETATION_ENABLED", True)
    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg, qid="demo_result_eligible_v1", answer="a2", n_items=3)
    cb = FakeCallback(user, msg, data=f"q:r:{session_id}")
    asyncio.run(bot.cb_questionnaire_result(cb))
    text, kw = msg.answers[-1]
    assert "6 / 9" in text
    assert "Это не диагноз" in text


# ── result screen has no diagnosis/disorder/probability/treatment wording ────
FORBIDDEN_WORDS = ["диагноз лечения", "расстройство", "вероятность заболевания",
                    "норма", "патология", "опасность"]


def test_result_screen_has_no_forbidden_wording(monkeypatch):
    monkeypatch.setattr(config, "QUESTIONNAIRE_INTERPRETATION_ENABLED", True)
    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg, qid="demo_result_eligible_v1", answer="a1", n_items=3)
    text, _ = msg.answers[-1]
    for word in FORBIDDEN_WORDS:
        assert word not in text.lower()
    assert "не диагноз" in text


# ── result screen has exactly the 4 specified buttons, not discuss/specialist ─
def test_result_screen_has_exactly_four_expected_buttons(monkeypatch):
    monkeypatch.setattr(config, "QUESTIONNAIRE_INTERPRETATION_ENABLED", True)
    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg, qid="demo_result_eligible_v1", answer="a1", n_items=3)
    _, kw = msg.answers[-1]
    kb = kw["reply_markup"]
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert len(texts) == 4
    assert any("Расчёты" in t for t in texts)
    assert any("шкал" in t for t in texts)
    assert any("Другой опросник" in t for t in texts)
    assert any("В меню" in t for t in texts)
    assert not any("Обсудить" in t for t in texts)
    assert not any("специалист" in t.lower() for t in texts)


# ── 6. calculations screen shows raw sum/max only, no norm/sten/percentile ───
def test_calculations_screen_shows_raw_sum_and_max(monkeypatch):
    monkeypatch.setattr(config, "QUESTIONNAIRE_INTERPRETATION_ENABLED", True)
    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg, qid="demo_result_eligible_v1", answer="a1", n_items=3)
    cb = FakeCallback(user, msg, data=f"q:k:{session_id}")
    asyncio.run(bot.cb_questionnaire_calculations(cb))
    text, kw = msg.answers[-1]
    assert "1 + 1 + 1 = 3" in text
    assert "3 / 9" in text
    for banned in ("норма", "стен", "перцентил", "процентил"):
        assert banned not in text.lower()
    kb = kw["reply_markup"]
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("результату" in t for t in texts)
    assert any("В меню" in t for t in texts)


# ── 7. explanation screen shows the original synthetic text ─────────────────
def test_explanation_screen_shows_synthetic_text_verbatim(monkeypatch):
    monkeypatch.setattr(config, "QUESTIONNAIRE_INTERPRETATION_ENABLED", True)
    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg, qid="demo_result_eligible_v1", answer="a1", n_items=3)
    cb = FakeCallback(user, msg, data=f"q:e:{session_id}")
    asyncio.run(bot.cb_questionnaire_explanation(cb))
    text, kw = msg.answers[-1]
    registry = questionnaires.load_registry(FIXTURE_DIR)
    expected = registry.get("demo_result_eligible_v1")["scale_explanations"]["main"]
    assert expected in text
    assert "Это не диагноз и не медицинское заключение" in text
    for banned in ("STAI", "GAD", "PHQ", "манифест"):
        assert banned not in text


# ── no_score/specialist_only/restricted/draft/archived/non-synthetic never
#    show score even with flag true ──────────────────────────────────────────
def _force_completed_session(uid, qid, version="1"):
    session_id = asyncio.run(database.start_questionnaire_session(uid, qid, version))
    asyncio.run(database.complete_questionnaire_session(session_id))
    return session_id


@pytest.mark.parametrize("qid", [
    "demo_no_score_v1", "demo_specialist_only_v1", "demo_restricted_v1",
    "demo_draft_v1", "demo_archived_v1", "demo_licensed_full_v1",
])
def test_ineligible_definitions_never_show_score_even_with_flag_true(monkeypatch, qid):
    monkeypatch.setattr(config, "QUESTIONNAIRE_INTERPRETATION_ENABLED", True)
    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _force_completed_session(1, qid)
    cb = FakeCallback(user, msg, data=f"q:r:{session_id}")
    asyncio.run(bot.cb_questionnaire_result(cb))
    text, _ = msg.answers[-1]
    assert text == questionnaire_ux.not_available_text("ru")
    assert "/" not in text


# ── incomplete or inconsistent responses fail closed ─────────────────────────
def test_incomplete_responses_fail_closed(monkeypatch):
    monkeypatch.setattr(config, "QUESTIONNAIRE_INTERPRETATION_ENABLED", True)
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data="q:s:demo_result_eligible_v1")))
    session_id = asyncio.run(_sessions_for(1))[-1][0]
    # answer only 1 of 3 items, then force-complete the session directly
    asyncio.run(bot.cb_questionnaire_answer(
        FakeCallback(user, msg, data=f"q:a:{session_id}:0:a1")))
    asyncio.run(database.complete_questionnaire_session(session_id))

    cb = FakeCallback(user, msg, data=f"q:r:{session_id}")
    asyncio.run(bot.cb_questionnaire_result(cb))
    text, _ = msg.answers[-1]
    assert text == questionnaire_ux.not_available_text("ru")


def test_inconsistent_answer_id_fails_closed(monkeypatch):
    monkeypatch.setattr(config, "QUESTIONNAIRE_INTERPRETATION_ENABLED", True)
    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg, qid="demo_result_eligible_v1", answer="a1", n_items=3)
    # Directly insert a bogus response referencing an option id that doesn't
    # exist in the definition, simulating drift/corruption.
    asyncio.run(database.record_questionnaire_response(
        1, session_id, "demo_result_eligible_v1", "q1", "does_not_exist", "99"))
    cb = FakeCallback(user, msg, data=f"q:r:{session_id}")
    asyncio.run(bot.cb_questionnaire_result(cb))
    text, _ = msg.answers[-1]
    assert text == questionnaire_ux.not_available_text("ru")


# ── q:r/q:k/q:e enforce session ownership ────────────────────────────────────
@pytest.mark.parametrize("handler_name,fmt", [
    ("cb_questionnaire_result", "q:r:{sid}"),
    ("cb_questionnaire_calculations", "q:k:{sid}"),
    ("cb_questionnaire_explanation", "q:e:{sid}"),
])
def test_new_callbacks_reject_wrong_user(monkeypatch, handler_name, fmt):
    monkeypatch.setattr(config, "QUESTIONNAIRE_INTERPRETATION_ENABLED", True)
    owner = FakeUser(1)
    msg = FakeMessage(owner)
    session_id = _complete_flow(owner, msg, qid="demo_result_eligible_v1", answer="a1", n_items=3)

    # attacker is also given full access (owner-equivalent) so the ownership
    # check itself -- not the unrelated product-access gate -- is what's being
    # exercised here.
    monkeypatch.setattr(ac, "OWNER_USER_ID", 999)
    attacker = FakeUser(999)
    n_answers_before = len(msg.answers)
    cb = FakeCallback(attacker, msg, data=fmt.format(sid=session_id))
    asyncio.run(getattr(bot, handler_name)(cb))
    # ownership check is a silent no-op (session belongs to uid 1, not 999):
    # no NEW message is appended, so no score/calculations/explanation leaks.
    assert len(msg.answers) == n_answers_before


# ── q:r/q:k/q:e follow the full gate order (parametrized) ────────────────────
NEW_CALLBACKS = [
    ("cb_questionnaire_result", "q:r"),
    ("cb_questionnaire_calculations", "q:k"),
    ("cb_questionnaire_explanation", "q:e"),
]


@pytest.mark.parametrize("handler_name,prefix", NEW_CALLBACKS)
def test_new_callbacks_gated_by_active_crisis_first(monkeypatch, handler_name, prefix):
    monkeypatch.setattr(config, "QUESTIONNAIRE_INTERPRETATION_ENABLED", True)
    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg, qid="demo_result_eligible_v1", answer="a1", n_items=3)

    monkeypatch.setattr(bot, "get_active_crisis", _async((7, 0, "ru")))
    cb = FakeCallback(user, msg, data=f"{prefix}:{session_id}")
    asyncio.run(getattr(bot, handler_name)(cb))
    from crisis_protocol import get_hotline
    assert get_hotline("ru")["primary"] in msg.answers[-1][0]


@pytest.mark.parametrize("handler_name,prefix", NEW_CALLBACKS)
def test_new_callbacks_gated_by_product_access(monkeypatch, handler_name, prefix):
    monkeypatch.setattr(config, "QUESTIONNAIRE_INTERPRETATION_ENABLED", True)
    owner = FakeUser(1)
    msg = FakeMessage(owner)
    session_id = _complete_flow(owner, msg, qid="demo_result_eligible_v1", answer="a1", n_items=3)
    n_answers_before = len(msg.answers)

    unknown = FakeUser(424242)
    cb = FakeCallback(unknown, msg, data=f"{prefix}:{session_id}")
    asyncio.run(getattr(bot, handler_name)(cb))
    # product gate blocks unknown users under personal_use -- session belongs
    # to a different uid anyway so this is also covered by ownership, but the
    # important invariant is: no new "result" content leaks.
    for text, _ in msg.answers[n_answers_before:]:
        assert "Результат" not in text and "Расчёты" not in text and "шкал" not in text


# ── all new callback_data <=64 bytes, no item_id ─────────────────────────────
def test_new_callback_formats_stay_under_64_bytes_and_no_item_id():
    session_id = 123456789
    for fmt in (f"q:r:{session_id}", f"q:k:{session_id}", f"q:e:{session_id}"):
        assert len(fmt.encode("utf-8")) <= 64
        assert "q1" not in fmt and "item" not in fmt


# ── no questionnaire_scores table ────────────────────────────────────────────
def test_no_questionnaire_scores_table_in_schema():
    assert "questionnaire_scores" not in database.SCHEMA


# ── no traced_response/safety_validator import/reference ────────────────────
def test_no_traced_response_or_safety_validator_reference():
    import inspect
    src_ux = inspect.getsource(questionnaire_ux)
    src_q = inspect.getsource(questionnaires)
    for src in (src_ux, src_q):
        assert "traced_response" not in src
        assert "safety_validator" not in src


# ── no copyrighted/manual content strings ────────────────────────────────────
BANNED_INSTRUMENT_TOKENS = [
    "STAI", "GAD-7", "PHQ-9", "PHQ-2", "YSQ", "SMI", "EPI", "LSI",
    "SVF-120", "BPNSS", "CSIS",
]


def test_fixtures_contain_no_real_instrument_tokens():
    for path in FIXTURE_DIR.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        for token in BANNED_INSTRUMENT_TOKENS:
            assert token not in text, f"{token} found in {path.name}"


def test_ux_module_contains_no_real_instrument_tokens():
    import inspect
    src = inspect.getsource(questionnaire_ux)
    for token in BANNED_INSTRUMENT_TOKENS:
        assert token not in src


# ── render_intensity_bar unit tests (pure/deterministic) ─────────────────────
def test_render_intensity_bar_all_empty_at_zero():
    bar = questionnaire_ux.render_intensity_bar(0, 10, segments=7)
    assert bar == "⬜" * 7


def test_render_intensity_bar_all_filled_at_max():
    bar = questionnaire_ux.render_intensity_bar(10, 10, segments=7)
    assert "⬜" not in bar
    assert len(bar) == 7  # 7 emoji chars (each counts as 1+ code point but str len per glyph is 1-2; check no crash)


def test_render_intensity_bar_deterministic():
    bar1 = questionnaire_ux.render_intensity_bar(4, 9, segments=7)
    bar2 = questionnaire_ux.render_intensity_bar(4, 9, segments=7)
    assert bar1 == bar2


def test_intensity_label_only_four_values():
    for score in range(0, 11):
        label = questionnaire_ux.intensity_label(score, 10, "ru")
        assert label in ("низкая", "умеренная", "заметная", "высокая")


# ── existing PR A tests / navigation / emotion-map untouched (smoke) ─────────
def test_pr_a_completion_keyboard_unchanged_shape():
    user = FakeUser(1)
    msg = FakeMessage(user)
    _complete_flow(user, msg)  # flag is False (autouse fixture default)
    _, kw = msg.answers[-1]
    kb = kw["reply_markup"]
    callback_datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert callback_datas == ["q:l", "menu:back"]
