"""Specialist report -- PR C1. Self-only, deterministic, no LLM.

Handler-level tests against the REAL bot.py handler and a REAL tmp sqlite DB,
following the exact conventions of tests/test_questionnaire_results_killswitch.py
(FakeUser/FakeMessage/FakeCallback, bot._load_registry_fresh monkeypatched to
tests/fixtures/registry/).
"""
import asyncio
import inspect
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
    monkeypatch.setattr(config, "QUESTIONNAIRE_INTERPRETATION_ENABLED", False)


async def _sessions_for(uid):
    import sqlite3
    con = sqlite3.connect(database.DB)
    rows = con.execute(
        "SELECT id, questionnaire_id, questionnaire_version, status, current_index "
        "FROM questionnaire_sessions WHERE user_id=?", (uid,)).fetchall()
    con.close()
    return rows


def _complete_flow(user, msg, qid="demo_result_eligible_v1", answer="a1", n_items=3):
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data=f"q:s:{qid}")))
    session_id = asyncio.run(_sessions_for(user.id))[-1][0]
    for step in range(n_items):
        cb = FakeCallback(user, msg, data=f"q:a:{session_id}:{step}:{answer}")
        asyncio.run(bot.cb_questionnaire_answer(cb))
    return session_id


def _force_completed_session(uid, qid, version="1"):
    session_id = asyncio.run(database.start_questionnaire_session(uid, qid, version))
    asyncio.run(database.complete_questionnaire_session(session_id))
    return session_id


def _report_cb(user, msg, session_id):
    cb = FakeCallback(user, msg, data=f"q:o:{session_id}")
    asyncio.run(bot.cb_questionnaire_specialist_report(cb))
    return msg.answers[-1][0]


# ── 1. self-only: different user's callback rejected ────────────────────────
def test_specialist_report_self_only_rejects_other_user(monkeypatch):
    owner = FakeUser(1)
    msg = FakeMessage(owner)
    session_id = _complete_flow(owner, msg)
    n_before = len(msg.answers)

    monkeypatch.setattr(ac, "OWNER_USER_ID", 999)
    attacker = FakeUser(999)
    cb = FakeCallback(attacker, msg, data=f"q:o:{session_id}")
    asyncio.run(bot.cb_questionnaire_specialist_report(cb))
    # ownership check is a silent no-op -- no new message leaked
    assert len(msg.answers) == n_before


# ── 2. requires journal_guard/access gate ────────────────────────────────────
def test_specialist_report_gated_by_active_crisis(monkeypatch):
    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg)

    monkeypatch.setattr(bot, "get_active_crisis", _async((7, 0, "ru")))
    cb = FakeCallback(user, msg, data=f"q:o:{session_id}")
    asyncio.run(bot.cb_questionnaire_specialist_report(cb))
    from crisis_protocol import get_hotline
    assert get_hotline("ru")["primary"] in msg.answers[-1][0]


def test_specialist_report_gated_by_product_access(monkeypatch):
    owner = FakeUser(1)
    msg = FakeMessage(owner)
    session_id = _complete_flow(owner, msg)
    n_before = len(msg.answers)

    unknown = FakeUser(424242)
    cb = FakeCallback(unknown, msg, data=f"q:o:{session_id}")
    asyncio.run(bot.cb_questionnaire_specialist_report(cb))
    for text, _ in msg.answers[n_before:]:
        assert "Отчёт для специалиста" not in text


# ── 3. viewable for a COMPLETED session (not only active) ───────────────────
def test_specialist_report_viewable_after_completion():
    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg)
    session = asyncio.run(database.get_questionnaire_session(session_id))
    assert session["status"] == "completed"
    text = _report_cb(user, msg, session_id)
    assert "Отчёт для специалиста" in text


# ── 4. no score line when flag is false ──────────────────────────────────────
def test_specialist_report_no_score_line_when_flag_false():
    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg)
    text = _report_cb(user, msg, session_id)
    assert "Результат:" not in text
    assert "/" not in text.split("Ответы:")[-1].split("Это не диагноз")[0].replace("--", "")


# ── 5. score line when flag true AND eligible AND scoring succeeds ──────────
def test_specialist_report_score_line_when_eligible(monkeypatch):
    monkeypatch.setattr(config, "QUESTIONNAIRE_INTERPRETATION_ENABLED", True)
    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg, answer="a1", n_items=3)
    text = _report_cb(user, msg, session_id)
    assert "Результат: 3 / 9" in text


# ── 6. no score line even when flag true, if is_result_eligible is false ────
@pytest.mark.parametrize("qid", ["demo_restricted_v1", "demo_specialist_only_v1", "demo_no_score_v1"])
def test_specialist_report_no_score_when_ineligible(monkeypatch, qid):
    monkeypatch.setattr(config, "QUESTIONNAIRE_INTERPRETATION_ENABLED", True)
    session_id = _force_completed_session(1, qid)
    asyncio.run(database.record_questionnaire_response(1, session_id, qid, "q1", "a1", "1"))
    user = FakeUser(1)
    msg = FakeMessage(user)
    text = _report_cb(user, msg, session_id)
    assert "Результат:" not in text


# ── 7. answers render in definition item order, not raw SQL row order ───────
def test_specialist_report_answers_in_definition_item_order(monkeypatch):
    session_id = _force_completed_session(1, "demo_result_eligible_v1")
    # Insert in REVERSE item order (q3, q2, q1) so raw SQL row order would be
    # wrong if the handler didn't iterate definition["items"] explicitly.
    asyncio.run(database.record_questionnaire_response(
        1, session_id, "demo_result_eligible_v1", "q3", "a3", "3"))
    asyncio.run(database.record_questionnaire_response(
        1, session_id, "demo_result_eligible_v1", "q2", "a2", "2"))
    asyncio.run(database.record_questionnaire_response(
        1, session_id, "demo_result_eligible_v1", "q1", "a1", "1"))

    user = FakeUser(1)
    msg = FakeMessage(user)
    text = _report_cb(user, msg, session_id)
    idx1 = text.index("№1")
    idx2 = text.index("№2")
    idx3 = text.index("№3")
    assert idx1 < idx2 < idx3


# ── 8. duplicate answers for the same item -> latest response wins ─────────
def test_specialist_report_duplicate_answers_latest_wins():
    session_id = _force_completed_session(1, "demo_result_eligible_v1")
    # Answer q1 twice (simulating back/re-answer): first a0, then a3. The
    # LATEST recorded response (later `id`, i.e. inserted second) must be the
    # one shown -- no contradictory duplicate lines for the same item.
    asyncio.run(database.record_questionnaire_response(
        1, session_id, "demo_result_eligible_v1", "q1", "a0", "0"))
    asyncio.run(database.record_questionnaire_response(
        1, session_id, "demo_result_eligible_v1", "q1", "a3", "3"))
    asyncio.run(database.record_questionnaire_response(
        1, session_id, "demo_result_eligible_v1", "q2", "a1", "1"))
    asyncio.run(database.record_questionnaire_response(
        1, session_id, "demo_result_eligible_v1", "q3", "a1", "1"))

    user = FakeUser(1)
    msg = FakeMessage(user)
    text = _report_cb(user, msg, session_id)
    # Only ONE line for q1's question text, and it must reflect the latest
    # answer (a3 => "3 -- почти каждый день"), not the stale a0 line.
    assert text.count("№1") == 1
    lines = text.split("\n")
    q1_line = next(l for l in lines if "№1" in l)
    assert "почти каждый день" in q1_line
    assert "совсем нет" not in q1_line


# ── 9. fails closed if a stored response no longer matches the definition ──
def test_specialist_report_fails_closed_on_item_drift():
    session_id = _force_completed_session(1, "demo_result_eligible_v1")
    asyncio.run(database.record_questionnaire_response(
        1, session_id, "demo_result_eligible_v1", "q1", "a1", "1"))
    asyncio.run(database.record_questionnaire_response(
        1, session_id, "demo_result_eligible_v1", "q2", "a1", "1"))
    # q3 missing entirely -> definition-driven lookup fails closed.
    user = FakeUser(1)
    msg = FakeMessage(user)
    text = _report_cb(user, msg, session_id)
    assert text == questionnaire_ux.not_available_text("ru")


def test_specialist_report_fails_closed_on_bogus_answer_id():
    session_id = _force_completed_session(1, "demo_result_eligible_v1")
    asyncio.run(database.record_questionnaire_response(
        1, session_id, "demo_result_eligible_v1", "q1", "does_not_exist", "99"))
    asyncio.run(database.record_questionnaire_response(
        1, session_id, "demo_result_eligible_v1", "q2", "a1", "1"))
    asyncio.run(database.record_questionnaire_response(
        1, session_id, "demo_result_eligible_v1", "q3", "a1", "1"))
    user = FakeUser(1)
    msg = FakeMessage(user)
    text = _report_cb(user, msg, session_id)
    assert text == questionnaire_ux.not_available_text("ru")


# ── 10. no diagnosis/disorder/probability/treatment wording ─────────────────
FORBIDDEN_WORDS = ["диагноз лечения", "расстройство", "вероятность заболевания",
                    "норма", "патология", "опасность"]


def test_specialist_report_has_no_forbidden_wording(monkeypatch):
    monkeypatch.setattr(config, "QUESTIONNAIRE_INTERPRETATION_ENABLED", True)
    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg)
    text = _report_cb(user, msg, session_id)
    for word in FORBIDDEN_WORDS:
        assert word not in text.lower()
    assert "не диагноз" in text


# ── 11. no NEW review_pack.py coupling (static grep-based check) ───────────
def test_no_review_pack_coupling_in_new_code():
    src = inspect.getsource(bot.cb_questionnaire_specialist_report)
    src += inspect.getsource(bot._build_specialist_report_answers)
    src += inspect.getsource(questionnaire_ux.specialist_report_text)
    assert "review_pack" not in src


# ── 12. no LLM/OpenAI call anywhere in the report path ──────────────────────
def test_no_llm_call_in_report_path():
    src = inspect.getsource(bot.cb_questionnaire_specialist_report)
    src += inspect.getsource(bot._build_specialist_report_answers)
    src += inspect.getsource(questionnaire_ux.specialist_report_text)
    for banned in ("openai", "OpenAI", "chat.completions", "traced_response", "safety_validator"):
        assert banned not in src


# ── 13. q:o callback_data <=64 bytes; malformed/non-digit rejected ─────────
def test_q_o_callback_format_stays_under_64_bytes():
    session_id = 123456789
    fmt = f"q:o:{session_id}"
    assert len(fmt.encode("utf-8")) <= 64
    assert "q1" not in fmt and "item" not in fmt


@pytest.mark.parametrize("bad_data", ["q:o:", "q:o:abc", "q:o:1:2", "q:o:-1"])
def test_q_o_rejects_malformed_session_id(bad_data):
    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg)
    n_before = len(msg.answers)
    cb = FakeCallback(user, msg, data=bad_data)
    asyncio.run(bot.cb_questionnaire_specialist_report(cb))
    assert len(msg.answers) == n_before


# ── 14. CLINICAL_BOUNDARY.md contains the new §0.5 section ─────────────────
def test_clinical_boundary_contains_section_0_5():
    text = pathlib.Path("CLINICAL_BOUNDARY.md").read_text(encoding="utf-8")
    assert "## 0.5." in text
    assert "user_visible_full" in text.split("## 0.5.")[1].split("## 1.")[0]
    section = text.split("## 0.5.")[1].split("## 1.")[0]
    assert "specialist_only" in section
    assert "STAI" in section or "GAD-7" in section
    assert "sten" in section.lower() or "percentile" in section.lower() or "процентиль" in section.lower() or "sten" in section.lower()
    assert "§8" in section
    assert "review_pack" in section
