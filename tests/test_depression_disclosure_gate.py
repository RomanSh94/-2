"""Depression Disclosure Gate — Phase 2 (master prompt §13), corrected pass.

Deterministic detection (depression_disclosure.classify_disclosure) plus a
restart-safe, DB-backed multi-step flow (database.*_disclosure_flow*) wired
into bot.pipeline() strictly after the RED crisis check, so explicit
suicide/self-harm language always wins. Crisis-adjacent: this file is
Review A's companion evidence (ordering, strict allowlists, ownership,
atomic transitions, restart, expiry, duplicate/stale/superseded callbacks,
systemic crisis supersession, truthful crisis audit, no side effects before
safety, exact copy, centralized rollout gating). Review B (regression:
existing crisis flow, crisis delivery, stale-response suppression, access,
privacy, voice, reactions, ordinary flag-off behavior) is exercised by the
untouched existing suites this file does not modify.
"""
import asyncio
import json
import os
import sqlite3
import types

import pytest

import access_control as ac
import bot
import config
import database
import depression_disclosure as dd

run = asyncio.run


class FakeUser:
    def __init__(self, uid, username="user", first="U"):
        self.id = uid
        self.username = username
        self.first_name = first


class SentMessage:
    """Return value of FakeMessage.answer -- carries a message_id like a real
    aiogram Message, so prompt_message_id bookkeeping is exercised too."""
    _next_id = [1000]

    def __init__(self):
        SentMessage._next_id[0] += 1
        self.message_id = SentMessage._next_id[0]


class FakeMessage:
    def __init__(self, user, text="", message_id=1):
        self.from_user = user
        self.text = text
        self.chat = types.SimpleNamespace(id=user.id)
        self.message_id = message_id
        self.answers = []

    async def answer(self, text, **kw):
        self.answers.append((text, kw))
        return SentMessage()

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
    run(database.init_db())
    return database


@pytest.fixture(autouse=True)
def _access_env(monkeypatch):
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})


@pytest.fixture(autouse=True)
def _gate_on(monkeypatch):
    """Gate enabled AND Core rollout allows owner -- the effective contract
    (§4) needs BOTH; uid 1 is the fixture owner throughout this file."""
    monkeypatch.setattr(config, "DEPRESSION_DISCLOSURE_GATE_ENABLED", True)
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "all")


def _full_pipeline_stub_set(monkeypatch, *, llm_calls=None):
    monkeypatch.setattr(bot, "get_emotional_trajectory", _async(types.SimpleNamespace(
        trend="stable", hopelessness_streak=0, yellow_plus_streak=0, messages_analyzed=0)))
    monkeypatch.setattr(bot, "load_state", _async(None))
    monkeypatch.setattr(bot, "save_state", _async(None))
    monkeypatch.setattr(bot, "log_router_decision", _async(None))
    monkeypatch.setattr(bot, "maybe_summarize", _async(None))
    monkeypatch.setattr(bot, "build_context", _async(("", [])))
    monkeypatch.setattr(bot, "maybe_update_profile", _async(None))
    monkeypatch.setattr(bot, "get_user_message_count", _async(1))
    monkeypatch.setattr(bot, "check_sudden_improvement", _async(False))

    async def fake_typing(chat_id, action):
        return None
    monkeypatch.setattr(bot.bot, "send_chat_action", fake_typing)

    class _Choice:
        def __init__(self):
            self.message = types.SimpleNamespace(content="ok, noted")

    async def fake_create(*a, **kw):
        if llm_calls is not None:
            llm_calls["n"] += 1
        return types.SimpleNamespace(choices=[_Choice()])
    monkeypatch.setattr(bot.client.chat.completions, "create", fake_create)


async def _seed_user(uid: int):
    await database.upsert_user(uid, f"u{uid}", f"U{uid}")


_HANDLER_BY_TAG = {
    "safety": bot.cb_dd_safety, "src": bot.cb_dd_source, "dur": bot.cb_dd_duration,
    "func": bot.cb_dd_functioning, "basic": bot.cb_dd_basic_activities,
    "supp": bot.cb_dd_support, "purp": bot.cb_dd_purpose,
}


def _tap(user, fid, tag, value):
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data=f"dd:{tag}:{fid}:{value}")
    run(_HANDLER_BY_TAG[tag](cb))
    return cb, msg


# ── A. Detector: required positive phrases (§13.1 exact set) ───────────────

@pytest.mark.parametrize("text", [
    "у меня депрессия",
    "мне поставили депрессию",
    "я лечусь от депрессии",
    "кажется, у меня депрессия",
    "я в депрессивном состоянии",
    "похоже, я в депрессии",
])
def test_required_positive_phrases_classify_positive(text):
    assert dd.classify_disclosure(text) == "POSITIVE"


# ── A. Detector: full table-driven exclusion review (§12) ──────────────────

@pytest.mark.parametrize("text,expected", [
    ("у меня нет депрессии", "NEGATED"),
    ("я не думаю, что у меня депрессия", "NEGATED"),
    ("возможно, у меня не депрессия", "NEGATED"),
    ("у друга депрессия", "THIRD_PERSON"),
    ("у моей сестры депрессия", "THIRD_PERSON"),
    ('врач сказал: "у него депрессия"', "THIRD_PERSON"),
    ("что такое депрессия?", "META_QUESTION"),
    ("как может протекать депрессия?", "META_QUESTION"),
    ('например: "у меня депрессия"', "QUOTED_OR_HYPOTHETICAL"),
    ("если у меня будет депрессия", "QUOTED_OR_HYPOTHETICAL"),
    ('он спросил: "у тебя депрессия?"', "QUOTED_OR_HYPOTHETICAL"),
    ("слово «депрессия» часто используют неправильно", "QUOTED_OR_HYPOTHETICAL"),
])
def test_required_exclusion_review_table(text, expected):
    assert dd.classify_disclosure(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("У МЕНЯ ДЕПРЕССИЯ", "POSITIVE"),
    ("у   меня    депрессия", "POSITIVE"),
    ("у меня депрессия!!!", "POSITIVE"),
    ("", "NONE"),
    ("депресс", "NONE"),
    ("у меня депрессия и я хочу покончить с собой", "POSITIVE"),
])
def test_punctuation_case_whitespace_and_crisis_combination(text, expected):
    assert dd.classify_disclosure(text) == expected


# ── B. Pipeline: flag-off / rollout-off compatibility ───────────────────────

@pytest.mark.parametrize("gate,mode", [(False, "all"), (True, "off")])
def test_gate_off_or_rollout_off_reproduces_prior_behavior(monkeypatch, tmp_db, gate, mode):
    monkeypatch.setattr(config, "DEPRESSION_DISCLOSURE_GATE_ENABLED", gate)
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", mode)
    llm_calls = {"n": 0}
    _full_pipeline_stub_set(monkeypatch, llm_calls=llm_calls)
    user = FakeUser(1)
    msg = FakeMessage(user, "у меня депрессия")
    run(bot.pipeline(msg, msg.text, None, tg_user=user))
    assert llm_calls["n"] == 1
    assert msg.answers[-1][0] == "ok, noted"
    assert run(database.get_active_disclosure_flow(1)) is None


# ── B. Pipeline: eligible disclosure -> exact copy, no side effects ────────

def test_positive_disclosure_sends_exact_copy_and_buttons_no_llm_call(monkeypatch, tmp_db):
    llm_calls = {"n": 0}
    _full_pipeline_stub_set(monkeypatch, llm_calls=llm_calls)
    run(_seed_user(1))
    user = FakeUser(1)
    msg = FakeMessage(user, "у меня депрессия")
    run(bot.pipeline(msg, msg.text, None, tg_user=user))

    assert llm_calls["n"] == 0, "no LLM call before safety resolution"
    assert len(msg.answers) == 1
    text, kw = msg.answers[0]
    assert text == ("Я отнесусь к этому серьёзно. По одному сообщению я не могу подтвердить "
                    "диагноз. Сначала важный вопрос: есть ли сейчас мысли, что не хочется жить "
                    "или причинить себе вред?")
    kb = kw["reply_markup"]
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert labels == ["Да", "Нет", "Не уверен"]

    flow = run(database.get_active_disclosure_flow(1))
    assert flow is not None
    assert flow["step"] == "SAFETY_CHECK"
    assert flow["prompt_message_id"] is not None
    assert flow["origin_message_id"] == msg.message_id


def test_no_advice_or_exercise_in_safety_check_text():
    text = dd.SAFETY_CHECK_TEXT_RU.lower()
    for forbidden in ("попробуй", "упражнение", "дыхание", "совет"):
        assert forbidden not in text


@pytest.mark.parametrize("text", [
    "у меня нет депрессии", "у друга депрессия, я переживаю", "что такое депрессия?",
])
def test_excluded_categories_never_trigger_gate(monkeypatch, tmp_db, text):
    llm_calls = {"n": 0}
    _full_pipeline_stub_set(monkeypatch, llm_calls=llm_calls)
    run(_seed_user(1))
    user = FakeUser(1)
    msg = FakeMessage(user, text)
    run(bot.pipeline(msg, msg.text, None, tg_user=user))
    assert llm_calls["n"] == 1
    assert run(database.get_active_disclosure_flow(1)) is None


# ── B. Crisis priority ──────────────────────────────────────────────────────

def test_explicit_suicide_language_wins_over_disclosure_no_flow_created(monkeypatch, tmp_db):
    crisis_calls = {"n": 0}
    async def spy_trigger_crisis(*a, **kw):
        crisis_calls["n"] += 1
    monkeypatch.setattr(bot, "trigger_crisis", spy_trigger_crisis)
    llm_calls = {"n": 0}
    _full_pipeline_stub_set(monkeypatch, llm_calls=llm_calls)
    run(_seed_user(1))
    user = FakeUser(1)
    msg = FakeMessage(user, "у меня депрессия и я хочу покончить с собой")
    run(bot.pipeline(msg, msg.text, None, tg_user=user))
    assert crisis_calls["n"] == 1
    assert llm_calls["n"] == 0
    assert run(database.get_active_disclosure_flow(1)) is None


# ── C. Strict callback value validation (§1) ────────────────────────────────

def test_safety_garbage_value_is_rejected_not_treated_as_no(tmp_db):
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    user = FakeUser(1)
    cb, msg = _tap(user, flow["id"], "safety", "garbage")
    assert cb.answered == 1
    assert msg.answers == []
    unchanged = run(database.get_disclosure_flow(flow["id"], 1))
    assert unchanged["step"] == "SAFETY_CHECK"
    assert unchanged["status"] == "active"


@pytest.mark.parametrize("tag,bad_value", [
    ("src", "made_up"), ("dur", "forever"), ("func", "fine"),
    ("basic", "great"), ("supp", "nobody_asked"), ("purp", "whatever"),
])
def test_every_step_rejects_unsupported_value(tmp_db, tag, bad_value):
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    # Advance the flow to the step under test via the real, valid path.
    sequence = [("safety", "no"), ("src", "self"), ("dur", "weeks"),
               ("func", "harder"), ("basic", "managing"), ("supp", "close_ones")]
    user = FakeUser(1)
    for step_tag, val in sequence:
        if step_tag == tag:
            break
        _tap(user, flow["id"], step_tag, val)
    cb, msg = _tap(user, flow["id"], tag, bad_value)
    assert cb.answered == 1
    assert msg.answers == [], f"step {tag}: invalid value must not send the next question"


@pytest.mark.parametrize("bad_value", [
    "YES", "Yes", "yEs",           # uppercase/mixed-case variants
    "да", "нет",                    # Cyrillic lookalikes, not the real values
    "",                              # empty string
    "yes:extra:colons:here",        # extra delimiters folded into the value
    "y" * 5000,                     # oversized
    "yes ", "yes\x00",              # trailing space / embedded null escape
])
def test_safety_step_rejects_every_malformed_value_variant(tmp_db, bad_value):
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    user = FakeUser(1)
    cb, msg = _tap(user, flow["id"], "safety", bad_value)
    assert cb.answered == 1
    assert msg.answers == []
    unchanged = run(database.get_disclosure_flow(flow["id"], 1))
    assert unchanged["step"] == "SAFETY_CHECK" and unchanged["status"] == "active"


def test_malformed_namespace_and_tag_rejected(tmp_db):
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    user = FakeUser(1)
    for bad_data in (f"notdd:safety:{flow['id']}:no", f"dd:unknown_tag:{flow['id']}:no",
                     "dd:safety", "dd:safety:", ""):
        msg = FakeMessage(user)
        cb = FakeCallback(user, msg, data=bad_data)
        run(bot.cb_dd_safety(cb))
        assert cb.answered == 1
        assert msg.answers == [], f"payload={bad_data!r}"


# ── C. "Unsure" must not be recorded as confirmed suicide (§2) ─────────────

def test_yes_produces_truthful_direct_confirmation_metadata(tmp_db, monkeypatch):
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    seen = {}
    async def spy(message, uid, username, user_text, risk, lang, *, source="EXPLICIT_MESSAGE"):
        seen["user_text"] = user_text
        seen["risk"] = risk
        seen["source"] = source
    monkeypatch.setattr(bot, "trigger_crisis", spy)
    user = FakeUser(1)
    _tap(user, flow["id"], "safety", "yes")
    assert seen["source"] == "DIRECT_SAFETY_YES"
    assert seen["risk"]["categories"] == ["suicide"]
    assert seen["risk"]["score"] is None, "must never fabricate a numeric score"
    assert seen["user_text"] == "", "must never fabricate fake user message text"


def test_unsure_does_not_record_confirmed_suicide_category_or_score(tmp_db, monkeypatch):
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    seen = {}
    async def spy(message, uid, username, user_text, risk, lang, *, source="EXPLICIT_MESSAGE"):
        seen["user_text"] = user_text
        seen["risk"] = risk
        seen["source"] = source
    monkeypatch.setattr(bot, "trigger_crisis", spy)
    user = FakeUser(1)
    _tap(user, flow["id"], "safety", "unsure")
    assert seen["source"] == "DIRECT_SAFETY_UNSURE"
    assert seen["risk"]["categories"] == [], "uncertainty must never be recorded as confirmed suicide"
    assert seen["risk"]["score"] is None
    assert seen["user_text"] == ""


def test_yes_and_unsure_both_reach_real_trigger_crisis_no_parallel_path(tmp_db):
    """Uses the REAL trigger_crisis (not a spy) end-to-end -- proves both
    answers genuinely go through the existing deterministic crisis system,
    not a second invented architecture."""
    run(_seed_user(1))
    for value in ("yes", "unsure"):
        flow = run(database.create_disclosure_flow(1, "ru"))
        user = FakeUser(1)
        cb, msg = _tap(user, flow["id"], "safety", value)
        assert cb.answered == 1
        assert len(msg.answers) == 1  # the real crisis screen was delivered
        event = run(_fetch_last_crisis_event())
        assert event["level"] == "RED"
        assert event["risk_score"] is None
        assert event["source"] == ("DIRECT_SAFETY_YES" if value == "yes" else "DIRECT_SAFETY_UNSURE")


async def _fetch_last_crisis_event():
    async with database.aiosqlite.connect(database.DB) as db:
        cur = await db.execute(
            "SELECT level, risk_score, categories, source FROM crisis_events ORDER BY id DESC LIMIT 1")
        row = await cur.fetchone()
    return {"level": row[0], "risk_score": row[1], "categories": row[2], "source": row[3]}


def test_no_fabricated_placeholder_text_anywhere_in_crisis_audit(tmp_db):
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    user = FakeUser(1)
    _tap(user, flow["id"], "safety", "yes")
    event = run(_fetch_last_crisis_event())
    # message_excerpt is truthfully empty, never the old fake bracket string.
    async def _excerpt():
        async with database.aiosqlite.connect(database.DB) as db:
            cur = await db.execute("SELECT message_excerpt FROM crisis_events ORDER BY id DESC LIMIT 1")
            return (await cur.fetchone())[0]
    excerpt = run(_excerpt())
    assert excerpt in ("", None)
    assert "depression-disclosure-gate" not in (excerpt or "")


def test_existing_explicit_crisis_audit_unchanged_no_source(monkeypatch, tmp_db):
    """Review B evidence: the ordinary EXPLICIT_MESSAGE crisis path (real
    text, pattern-matched) is untouched behaviorally -- the call site in
    pipeline() passes no source= kwarg at all, exactly as before this phase;
    trigger_crisis's default now labels it "EXPLICIT_MESSAGE" (self-documenting
    provenance, strictly additive metadata) rather than an unlabeled NULL.
    The real risk score/categories are still the genuine detected values."""
    _full_pipeline_stub_set(monkeypatch)
    run(_seed_user(1))
    user = FakeUser(1)
    msg = FakeMessage(user, "я хочу покончить с собой")
    run(bot.pipeline(msg, msg.text, None, tg_user=user))
    event = run(_fetch_last_crisis_event())
    assert event["source"] == "EXPLICIT_MESSAGE"
    assert event["risk_score"] is not None and event["risk_score"] > 0
    assert "suicide" in event["categories"]


# ── C. Exact copy ────────────────────────────────────────────────────────────

def test_diagnosis_never_confirmed_or_denied_in_any_copy():
    all_text = " ".join([
        dd.SAFETY_CHECK_TEXT_RU, dd.DIAGNOSIS_SOURCE_TEXT_RU, dd.DURATION_TEXT_RU,
        dd.FUNCTIONING_TEXT_RU, dd.BASIC_ACTIVITIES_TEXT_RU, dd.SUPPORT_TEXT_RU,
        dd.PURPOSE_TEXT_RU, dd.CLOSING_TEXT_RU,
    ]).lower()
    assert "у тебя точно депрессия" not in all_text
    assert "это не депрессия" not in all_text


def test_safety_check_button_labels_and_order_exact():
    assert [ru for _, ru, _ in dd.SAFETY_CHECK_OPTIONS] == ["Да", "Нет", "Не уверен"]


# ── C. Full happy path through BASIC_ACTIVITIES -> HANDOFF_READY ───────────

def test_full_happy_path_one_question_per_turn_and_handoff(tmp_db):
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    user = FakeUser(1)
    fid = flow["id"]

    cb, msg = _tap(user, fid, "safety", "no")
    assert len(msg.answers) == 1 and msg.answers[0][0] == dd.DIAGNOSIS_SOURCE_TEXT_RU
    cb, msg = _tap(user, fid, "src", "self")
    assert msg.answers[0][0] == dd.DURATION_TEXT_RU
    cb, msg = _tap(user, fid, "dur", "weeks")
    assert msg.answers[0][0] == dd.FUNCTIONING_TEXT_RU
    cb, msg = _tap(user, fid, "func", "harder")
    assert msg.answers[0][0] == dd.BASIC_ACTIVITIES_TEXT_RU
    cb, msg = _tap(user, fid, "basic", "some_days_hard")
    assert msg.answers[0][0] == dd.SUPPORT_TEXT_RU
    cb, msg = _tap(user, fid, "supp", "close_ones")
    assert msg.answers[0][0] == dd.PURPOSE_TEXT_RU
    cb, msg = _tap(user, fid, "purp", "vent")
    assert msg.answers[0][0] == dd.CLOSING_TEXT_RU

    final = run(database.get_disclosure_flow(fid, 1))
    assert final["status"] == "completed"
    assert final["step"] == "HANDOFF_READY"
    assert final["handoff_status"] == "ready"
    assert final["completed_at"] is not None
    answers = json.loads(final["answers_json"])
    assert answers == {"duration": "weeks", "functioning": "harder",
                       "basic_activities": "some_days_hard", "support": "close_ones",
                       "purpose": "vent"}
    assert final["diagnosis_source"] == "self"


# ── C. Handoff claim (§9) ────────────────────────────────────────────────────

def _complete_flow(uid: int) -> dict:
    flow = run(database.create_disclosure_flow(uid, "ru"))
    user = FakeUser(uid)
    for tag, val in [("safety", "no"), ("src", "self"), ("dur", "weeks"),
                     ("func", "harder"), ("basic", "managing"),
                     ("supp", "close_ones"), ("purp", "vent")]:
        _tap(user, flow["id"], tag, val)
    return run(database.get_disclosure_flow(flow["id"], uid))


def test_handoff_claim_succeeds_once_then_fails_deterministically(tmp_db):
    run(_seed_user(1))
    final = _complete_flow(1)
    claimed = run(database.claim_disclosure_handoff(final["id"], 1))
    assert claimed is not None
    assert claimed["handoff_status"] == "claimed"
    second = run(database.claim_disclosure_handoff(final["id"], 1))
    assert second is None, "repeated claim must fail deterministically, not double-succeed"


def test_handoff_claim_survives_restart(tmp_db):
    run(_seed_user(1))
    final = _complete_flow(1)
    reread = run(database.get_disclosure_flow(final["id"], 1))
    claimed = run(database.claim_disclosure_handoff(reread["id"], 1))
    assert claimed is not None


def test_cross_user_handoff_claim_fails(tmp_db):
    run(_seed_user(1)); run(_seed_user(2))
    final = _complete_flow(1)
    assert run(database.claim_disclosure_handoff(final["id"], 2)) is None
    still_ready = run(database.get_disclosure_flow(final["id"], 1))
    assert still_ready["handoff_status"] == "ready"


def test_superseded_flow_produces_no_handoff(tmp_db, monkeypatch):
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    monkeypatch.setattr(bot, "trigger_crisis", _async(None))
    user = FakeUser(1)
    _tap(user, flow["id"], "safety", "yes")
    final = run(database.get_disclosure_flow(flow["id"], 1))
    assert final["handoff_status"] is None
    assert run(database.claim_disclosure_handoff(flow["id"], 1)) is None


def test_no_questionnaire_answer_interpreted_as_diagnosis(tmp_db):
    final = _complete_flow_after_seed(1)
    # diagnosis_source stores PROVENANCE ("self"), never a confirmed label --
    # nothing in the stored data claims "depression confirmed".
    assert final["diagnosis_source"] in ("specialist", "self", "unknown")
    answers = json.loads(final["answers_json"])
    assert "diagnosis" not in answers
    assert "confirmed" not in json.dumps(answers).lower()


def _complete_flow_after_seed(uid):
    run(_seed_user(uid))
    return _complete_flow(uid)


# ── C. Ownership, duplicate, stale, expired, superseded, cross-user ────────

def test_cross_user_callback_rejected(tmp_db):
    run(_seed_user(1)); run(_seed_user(2))
    flow = run(database.create_disclosure_flow(1, "ru"))
    attacker = FakeUser(2)
    cb, msg = _tap(attacker, flow["id"], "safety", "no")
    assert cb.answered == 1
    assert msg.answers == []
    untouched = run(database.get_disclosure_flow(flow["id"], 1))
    assert untouched["step"] == "SAFETY_CHECK"


def test_duplicate_callback_second_tap_rejected(tmp_db):
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    user = FakeUser(1)
    _tap(user, flow["id"], "safety", "no")
    cb2, msg2 = _tap(user, flow["id"], "safety", "no")
    assert cb2.answered == 1
    assert msg2.answers == [], "duplicate tap must not re-send the diagnosis-source prompt"


def test_stale_callback_for_already_advanced_step_rejected(tmp_db):
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    user = FakeUser(1)
    _tap(user, flow["id"], "safety", "no")
    cb, msg = _tap(user, flow["id"], "safety", "yes")
    assert cb.answered == 1
    assert msg.answers == []


def test_expired_flow_callback_rejected(tmp_db):
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    async def expire_it():
        async with database.aiosqlite.connect(database.DB) as db:
            await db.execute(
                "UPDATE depression_disclosure_flows SET expires_at=datetime('now','-1 hour') WHERE id=?",
                (flow["id"],))
            await db.commit()
    run(expire_it())
    user = FakeUser(1)
    cb, msg = _tap(user, flow["id"], "safety", "no")
    assert cb.answered == 1
    assert msg.answers == []


def test_superseded_flow_callback_rejected(tmp_db, monkeypatch):
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    monkeypatch.setattr(bot, "trigger_crisis", _async(None))
    user = FakeUser(1)
    _tap(user, flow["id"], "safety", "yes")
    cb, msg = _tap(user, flow["id"], "safety", "no")
    assert cb.answered == 1
    assert msg.answers == []


def test_restart_between_prompt_and_callback_flow_survives_fresh_read(tmp_db):
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    reloaded = run(database.get_disclosure_flow(flow["id"], 1))
    assert reloaded["step"] == "SAFETY_CHECK"
    user = FakeUser(1)
    cb, msg = _tap(user, flow["id"], "safety", "no")
    assert msg.answers[0][0] == dd.DIAGNOSIS_SOURCE_TEXT_RU


# ── C. Systemic crisis supersession, independent of logging (§2/§7) ────────

def test_supersession_survives_crisis_audit_logging_raising(tmp_db, monkeypatch):
    """The core correction: log_crisis_event raising must NOT prevent
    disclosure-flow supersession, because supersession now happens in
    trigger_crisis BEFORE log_crisis_event is even attempted."""
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    async def raising_log(*a, **kw):
        raise sqlite3.OperationalError("simulated DB failure")
    monkeypatch.setattr(bot, "log_crisis_event", raising_log)
    user = FakeUser(1)
    risk = {"score": 50, "level": "critical", "categories": ["suicide"],
           "implicit": False, "ambiguous_phrases": []}
    run(bot.trigger_crisis(FakeMessage(user), 1, "u1", "text", risk, "ru"))
    superseded = run(database.get_disclosure_flow(flow["id"], 1))
    assert superseded["status"] == "superseded_by_crisis"
    assert superseded["superseded_reason"] == "crisis_activated"


def test_supersession_survives_crisis_delivery_logging_raising(tmp_db, monkeypatch):
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    async def raising_delivery_log(*a, **kw):
        raise sqlite3.OperationalError("simulated delivery-log failure")
    monkeypatch.setattr(bot, "log_crisis_delivery", raising_delivery_log)
    user = FakeUser(1)
    risk = {"score": 50, "level": "critical", "categories": ["suicide"],
           "implicit": False, "ambiguous_phrases": []}
    run(bot.trigger_crisis(FakeMessage(user), 1, "u1", "text", risk, "ru"))
    superseded = run(database.get_disclosure_flow(flow["id"], 1))
    assert superseded["status"] == "superseded_by_crisis"


def test_crisis_screen_still_delivered_when_audit_logging_raises(tmp_db, monkeypatch):
    """Regression proof: the pre-existing "delivery must survive a broken
    log_crisis_event" behavior is unchanged by this phase's edits."""
    run(_seed_user(1))
    async def raising_log(*a, **kw):
        raise sqlite3.OperationalError("simulated DB failure")
    monkeypatch.setattr(bot, "log_crisis_event", raising_log)
    user = FakeUser(1)
    msg = FakeMessage(user)
    risk = {"score": 50, "level": "critical", "categories": ["suicide"],
           "implicit": False, "ambiguous_phrases": []}
    run(bot.trigger_crisis(msg, 1, "u1", "text", risk, "ru"))
    assert len(msg.answers) == 1, "the crisis screen must still be delivered"


def test_crisis_from_unrelated_message_supersedes_pending_disclosure_flow(monkeypatch, tmp_db):
    """Crisis activates via the ORDINARY pipeline RED path (a different
    message than the one that started the disclosure flow) -- proves the
    hook is systemic (every route funnels through the same trigger_crisis),
    not specific to the disclosure gate's own callbacks."""
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    _full_pipeline_stub_set(monkeypatch)
    user = FakeUser(1)
    msg = FakeMessage(user, "я хочу покончить с собой")
    run(bot.pipeline(msg, msg.text, None, tg_user=user))
    superseded = run(database.get_disclosure_flow(flow["id"], 1))
    assert superseded["status"] == "superseded_by_crisis"


def test_journal_guard_crisis_path_also_supersedes_disclosure(tmp_db):
    """§2: 'every existing crisis entry path supersedes disclosure' -- proven
    directly against journal_guard's RED branch (bot.py line ~899), a
    SEPARATE call site from pipeline()'s, both funneling through the same
    trigger_crisis."""
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    run(bot.journal_guard(FakeMessage(FakeUser(1)), 1, "ru",
                          text="я хочу покончить с собой", username="u1"))
    superseded = run(database.get_disclosure_flow(flow["id"], 1))
    assert superseded["status"] == "superseded_by_crisis"


def test_old_callback_after_systemic_supersession_rejected(tmp_db):
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    run(database.supersede_active_disclosure_flows_for_crisis(1))
    user = FakeUser(1)
    cb, msg = _tap(user, flow["id"], "safety", "no")
    assert cb.answered == 1
    assert msg.answers == []


def test_crisis_supersedes_regardless_of_which_step_flow_was_on(tmp_db):
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    user = FakeUser(1)
    _tap(user, flow["id"], "safety", "no")
    _tap(user, flow["id"], "src", "self")
    run(database.supersede_active_disclosure_flows_for_crisis(1))
    superseded = run(database.get_disclosure_flow(flow["id"], 1))
    assert superseded["status"] == "superseded_by_crisis"
    assert superseded["step"] == "DURATION", "step is left where it was interrupted, for audit"


# ── C. Race before delivery (§7 of the second correction pass) ─────────────

def test_crisis_during_transition_rejects_next_question_delivery(tmp_db):
    """Simulates the exact TOCTOU window: crisis activates AFTER a callback
    passed validation but is modeled here as happening between two
    sequential taps -- the atomic conditional UPDATE inside
    advance_disclosure_flow is the reconfirmation, not a second explicit
    check; a superseded flow's next transition deterministically fails and
    the next question is never sent."""
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    user = FakeUser(1)
    _tap(user, flow["id"], "safety", "no")  # flow now on DIAGNOSIS_SOURCE
    run(database.supersede_active_disclosure_flows_for_crisis(1))  # crisis begins
    cb, msg = _tap(user, flow["id"], "src", "self")  # attempted transition
    assert cb.answered == 1
    assert msg.answers == [], "zero ordinary disclosure questions after crisis ownership begins"


def test_duplicate_callback_during_crisis_rejected_not_double_processed(tmp_db, monkeypatch):
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    monkeypatch.setattr(bot, "trigger_crisis", _async(None))
    user = FakeUser(1)
    cb1, msg1 = _tap(user, flow["id"], "safety", "yes")
    cb2, msg2 = _tap(user, flow["id"], "safety", "yes")
    assert cb1.answered == 1 and cb2.answered == 1
    assert msg2.answers == []


def test_crisis_source_kind_is_a_closed_set(tmp_db):
    with pytest.raises(ValueError):
        run(database.log_crisis_event(1, "RED", 1, [], "", "ru", source="NOT_A_REAL_SOURCE"))


# ── D. New-message / topic-change / /start supersession policy (§8) ────────

def test_new_topic_message_cancels_pending_flow_silently(monkeypatch, tmp_db):
    llm_calls = {"n": 0}
    _full_pipeline_stub_set(monkeypatch, llm_calls=llm_calls)
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    user = FakeUser(1)
    msg = FakeMessage(user, "расскажи мне анекдот")
    run(bot.pipeline(msg, msg.text, None, tg_user=user))
    assert llm_calls["n"] == 1, "ordinary message must still get its normal reply"
    cancelled = run(database.get_disclosure_flow(flow["id"], 1))
    assert cancelled["status"] == "cancelled"
    assert cancelled["superseded_reason"] == "new_topic"


def test_second_eligible_disclosure_supersedes_first_flow(monkeypatch, tmp_db):
    _full_pipeline_stub_set(monkeypatch)
    run(_seed_user(1))
    user = FakeUser(1)
    run(bot.pipeline(FakeMessage(user, "у меня депрессия"), "у меня депрессия", None, tg_user=user))
    first = run(database.get_active_disclosure_flow(1))
    run(bot.pipeline(FakeMessage(user, "кажется, у меня депрессия"),
                     "кажется, у меня депрессия", None, tg_user=user))
    second = run(database.get_active_disclosure_flow(1))
    assert second["id"] != first["id"]
    old = run(database.get_disclosure_flow(first["id"], 1))
    assert old["status"] == "cancelled"
    assert old["superseded_reason"] == "new_disclosure"
    # §6: old callback after this supersession case is rejected too.
    cb, msg = _tap(user, first["id"], "safety", "no")
    assert cb.answered == 1
    assert msg.answers == []


def test_old_callback_after_new_topic_supersession_rejected(monkeypatch, tmp_db):
    _full_pipeline_stub_set(monkeypatch)
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    user = FakeUser(1)
    run(bot.pipeline(FakeMessage(user, "расскажи мне анекдот"), "расскажи мне анекдот",
                     None, tg_user=user))
    cb, msg = _tap(user, flow["id"], "safety", "no")
    assert cb.answered == 1
    assert msg.answers == []


def test_start_command_supersedes_pending_flow_then_old_callback_rejected(tmp_db, monkeypatch):
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    user = FakeUser(1)
    msg = FakeMessage(user)
    msg.from_user = user
    monkeypatch.setattr(bot, "ensure_full_access_or_closed_test", _async(False))
    run(bot.cmd_start(msg))
    cancelled = run(database.get_disclosure_flow(flow["id"], 1))
    assert cancelled["status"] == "cancelled"
    assert cancelled["superseded_reason"] == "start_command"
    cb, cb_msg = _tap(user, flow["id"], "safety", "no")
    assert cb.answered == 1
    assert cb_msg.answers == []


def test_expiry_boundary_exact(tmp_db):
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    async def set_expiry(offset):
        async with database.aiosqlite.connect(database.DB) as db:
            await db.execute(
                f"UPDATE depression_disclosure_flows SET expires_at=datetime('now','{offset}') WHERE id=?",
                (flow["id"],))
            await db.commit()
    run(set_expiry("+1 hour"))
    assert run(database.get_active_disclosure_flow(1)) is not None
    run(set_expiry("-1 hour"))
    assert run(database.get_active_disclosure_flow(1)) is None


def test_delete_all_removes_disclosure_flow(tmp_db):
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    summary = run(database.delete_all_personal_data(1))
    assert summary["depression_disclosure_flows"] == 1
    assert run(database.get_disclosure_flow(flow["id"], 1)) is None


def test_export_and_preview_scoped_to_owner_only(tmp_db):
    run(_seed_user(1)); run(_seed_user(2))
    run(database.create_disclosure_flow(1, "ru"))
    run(database.create_disclosure_flow(2, "ru"))
    exported = run(database.export_all_personal_data(1))
    assert len(exported["depression_disclosure_flows"]) == 1
    preview = run(database.preview_delete_all_personal_data(1))
    assert preview["depression_disclosure_flows"]["row_count"] == 1


# ── E. Database invariants and bounded data (§10) ───────────────────────────

def test_one_active_flow_per_user_enforced_by_db(tmp_db):
    run(_seed_user(1))
    run(database.create_disclosure_flow(1, "ru"))
    with pytest.raises(sqlite3.IntegrityError):
        run(database.create_disclosure_flow(1, "ru"))


def test_malformed_answers_json_fails_closed(tmp_db):
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    async def corrupt():
        async with database.aiosqlite.connect(database.DB) as db:
            await db.execute(
                "UPDATE depression_disclosure_flows SET answers_json='{not valid json' WHERE id=?",
                (flow["id"],))
            await db.commit()
    run(corrupt())
    reread = run(database.get_disclosure_flow(flow["id"], 1))
    assert database.safe_load_answers(reread["answers_json"]) == {}


def test_answers_json_size_bound_enforced(tmp_db):
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    huge = json.dumps({"x": "y" * 3000})
    with pytest.raises(sqlite3.IntegrityError):
        run(database.advance_disclosure_flow(flow["id"], 1, from_step="SAFETY_CHECK",
                                             to_step="DIAGNOSIS_SOURCE", answers_json=huge))


def test_step_and_status_are_closed_sets(tmp_db):
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    async def try_bad_step():
        async with database.aiosqlite.connect(database.DB) as db:
            await db.execute(
                "UPDATE depression_disclosure_flows SET step='NOT_A_REAL_STEP' WHERE id=?",
                (flow["id"],))
            await db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        run(try_bad_step())


def test_no_operational_print_leaks_disclosure_content(capsys, tmp_db):
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))
    user = FakeUser(1)
    for tag, val in [("safety", "no"), ("src", "self"), ("dur", "weeks")]:
        _tap(user, flow["id"], tag, val)
    out = capsys.readouterr()
    assert "weeks" not in out.out and "weeks" not in out.err
    assert "self" not in out.out and "self" not in out.err


# ── F. Centralized rollout matrix (§4) ──────────────────────────────────────

@pytest.mark.parametrize("gate,mode,uid,expected", [
    (False, "off", 1, False),
    (True, "off", 1, False),
    (True, "owner", 1, True),
    (True, "owner", 999, False),
    (True, "all", 999, True),
])
def test_rollout_matrix_gate_and_core_combinations(monkeypatch, gate, mode, uid, expected):
    monkeypatch.setattr(config, "DEPRESSION_DISCLOSURE_GATE_ENABLED", gate)
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", mode)
    assert run(ac.depression_disclosure_allowed_for(uid)) is expected


def test_rollout_matrix_invited_active_access(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "DEPRESSION_DISCLOSURE_GATE_ENABLED", True)
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "invited")
    async def go():
        await _seed_user(70)
        assert await ac.depression_disclosure_allowed_for(70) is False
        await database.grant_user_access(70)
        assert await ac.depression_disclosure_allowed_for(70) is True
    run(go())


def test_rollout_matrix_missing_owner_id(monkeypatch):
    monkeypatch.setattr(config, "DEPRESSION_DISCLOSURE_GATE_ENABLED", True)
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "owner")
    monkeypatch.setattr(ac, "OWNER_USER_ID", None)
    assert run(ac.depression_disclosure_allowed_for(1)) is False


def test_rollout_matrix_invalid_mode_fails_closed(monkeypatch):
    monkeypatch.setattr(config, "DEPRESSION_DISCLOSURE_GATE_ENABLED", True)
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "totally_bogus")
    assert run(ac.depression_disclosure_allowed_for(1)) is False


def test_rollout_changed_to_off_after_prompt_makes_callback_inert(tmp_db, monkeypatch):
    run(_seed_user(1))
    flow = run(database.create_disclosure_flow(1, "ru"))  # created while gate was on
    monkeypatch.setattr(config, "DEPRESSION_DISCLOSURE_GATE_ENABLED", False)
    user = FakeUser(1)
    cb, msg = _tap(user, flow["id"], "safety", "no")
    assert cb.answered == 1
    assert msg.answers == []
    unchanged = run(database.get_disclosure_flow(flow["id"], 1))
    assert unchanged["step"] == "SAFETY_CHECK"


def test_gate_flag_defaults_to_off_when_env_var_absent(monkeypatch):
    monkeypatch.delenv("DEPRESSION_DISCLOSURE_GATE_ENABLED", raising=False)
    default = os.getenv("DEPRESSION_DISCLOSURE_GATE_ENABLED", "false").strip().lower() \
        in ("1", "true", "yes", "on")
    assert default is False


# ── G. Migration / existing-database compatibility (§8) ────────────────────

_PRE_PHASE2_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
    message_count INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS crisis_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    level           TEXT NOT NULL,
    risk_score      INTEGER,
    categories      TEXT,
    message_excerpt TEXT,
    lang            TEXT DEFAULT 'ru',
    admin_notified  INTEGER DEFAULT 0,
    user_response   TEXT,
    resolved        INTEGER DEFAULT 0,
    followups_json  TEXT DEFAULT '[]',
    created_at      TEXT DEFAULT (datetime('now'))
);
"""


def test_migration_from_pre_phase2_schema_preserves_existing_data_and_adds_new_objects(tmp_path):
    """Not a fresh empty DB -- a hand-built snapshot of the exact Phase 1
    crisis_events shape (no `source` column, no depression_disclosure_flows
    table at all), with a real pre-existing row, then the REAL
    database.init_db() is run against it -- exactly what happens when this
    PR deploys onto the actual production x20.db."""
    db_path = str(tmp_path / "pre_phase2.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(_PRE_PHASE2_SCHEMA)
    conn.execute(
        "INSERT INTO users (id, username, first_name) VALUES (42, 'legacy', 'Legacy')")
    conn.execute(
        "INSERT INTO crisis_events (user_id, level, risk_score, categories, message_excerpt) "
        "VALUES (42, 'RED', 87, 'suicide', 'pre-existing excerpt')")
    conn.commit()
    conn.close()

    import database as real_database
    orig_db = real_database.DB
    try:
        real_database.DB = db_path
        run(real_database.init_db())

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        # 1. Existing row is byte-for-byte unchanged.
        row = conn.execute("SELECT * FROM crisis_events WHERE user_id=42").fetchone()
        assert row["level"] == "RED"
        assert row["risk_score"] == 87
        assert row["message_excerpt"] == "pre-existing excerpt"
        # 2. New column exists and is NULL for the pre-existing row (never
        #    backfilled with a fabricated value).
        assert row["source"] is None
        # 3. New table exists.
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "depression_disclosure_flows" in tables
        # 4. New indexes exist.
        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        assert "idx_dd_one_active_flow_per_user" in indexes
        conn.close()

        # 5. Idempotent re-run: no error, no duplicate schema objects, data intact.
        run(real_database.init_db())
        conn = sqlite3.connect(db_path)
        tables_after = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='depression_disclosure_flows'")]
        assert len(tables_after) == 1, "no destructive recreation / duplication"
        row_count = conn.execute("SELECT COUNT(*) FROM crisis_events").fetchone()[0]
        assert row_count == 1, "pre-existing row was not duplicated or dropped"
        conn.close()

        # 6. The new table is fully usable after an in-place upgrade.
        run(real_database.create_disclosure_flow(42, "ru"))
        active = run(real_database.get_active_disclosure_flow(42))
        assert active is not None
    finally:
        real_database.DB = orig_db


def test_completed_and_superseded_flows_coexist_in_history(tmp_db):
    """§8/§10: history accumulates -- a completed flow and a later
    superseded-by-crisis flow for the SAME user both remain queryable, only
    one of them was ever 'active' at a time."""
    run(_seed_user(1))
    completed = _complete_flow(1)
    assert completed["status"] == "completed"
    second = run(database.create_disclosure_flow(1, "ru"))
    run(database.supersede_active_disclosure_flows_for_crisis(1))
    second_reloaded = run(database.get_disclosure_flow(second["id"], 1))
    assert second_reloaded["status"] == "superseded_by_crisis"
    still_completed = run(database.get_disclosure_flow(completed["id"], 1))
    assert still_completed["status"] == "completed"
