"""Conversation Controller — Phase 3 (master prompt §10/§15).

Deterministic intent recognition (conversation_controller.classify_intent),
ResponsePlan construction, a Controller Fidelity Validator, restart-safe
session state reusing Phase 1's core_sessions storage, and Phase 2 handoff
consumption -- wired into bot.pipeline() strictly after the RED crisis check
and the Depression Disclosure Gate, gated by the same centralized
access_control.core_rollout_allowed() contract Phase 1 defined (no second,
competing rollout switch).

Scope boundary tested explicitly: a message with no deterministically-
recognized explicit intent classifies UNKNOWN and the controller returns
False, leaving the existing ordinary scenario pipeline completely unchanged
-- there is no ambiguous-turn LLM classifier call in this phase.
"""
import asyncio
import types

import pytest

import access_control as ac
import bot
import config
import conversation_controller as controller
import database
from therapeutic_domain import Intent, RepairConstraint, LifecycleStatus, ConsentState, ResponsePlan

run = asyncio.run


class FakeUser:
    def __init__(self, uid, username="user", first="U"):
        self.id = uid
        self.username = username
        self.first_name = first


class FakeMessage:
    def __init__(self, user, text="", message_id=1):
        self.from_user = user
        self.text = text
        self.chat = types.SimpleNamespace(id=user.id)
        self.message_id = message_id
        self.answers = []

    async def answer(self, text, **kw):
        self.answers.append((text, kw))
        return types.SimpleNamespace(message_id=self.message_id + 1)

    async def edit_reply_markup(self, **kw):
        pass


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
def _rollout_all(monkeypatch):
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "all")
    monkeypatch.setattr(config, "DEPRESSION_DISCLOSURE_GATE_ENABLED", False)


def _full_pipeline_stub_set(monkeypatch, *, llm_reply="ok, noted", llm_calls=None):
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
        def __init__(self, content):
            self.message = types.SimpleNamespace(content=content)

    async def fake_create(*a, **kw):
        if llm_calls is not None:
            llm_calls["n"] += 1
            llm_calls.setdefault("prompts", []).append(kw.get("messages"))
        return types.SimpleNamespace(choices=[_Choice(llm_reply)])
    monkeypatch.setattr(bot.client.chat.completions, "create", fake_create)


async def _seed_user(uid: int):
    await database.upsert_user(uid, f"u{uid}", f"U{uid}")


# ── A. Intent routing: every required explicit phrase ───────────────────────

@pytest.mark.parametrize("text", [
    "Мне нужно выговориться.", "Просто послушай.", "Без советов.", "Хочу рассказать.",
])
def test_vent_phrases(text):
    assert controller.classify_intent(text) == Intent.VENT


@pytest.mark.parametrize("text", [
    "Объясни, почему так происходит.", "Почему я так себя чувствую?", "Помоги понять.",
])
def test_explain_phrases(text):
    assert controller.classify_intent(text) == Intent.EXPLAIN


@pytest.mark.parametrize("text", [
    "Скажи, что мне сделать.", "Выбери сам один шаг.", "Мне нужен конкретный шаг.",
])
def test_action_phrases(text):
    assert controller.classify_intent(text) == Intent.ACTION


@pytest.mark.parametrize("text", [
    "Это постоянно повторяется.", "Хочу изменить этот сценарий.",
    "Почему я опять делаю то же самое?",
])
def test_change_pattern_phrases(text):
    assert controller.classify_intent(text) == Intent.CHANGE_PATTERN


@pytest.mark.parametrize("text", [
    "Не могу принять решение.", "Помоги разобраться с выбором.",
    "Не понимаю, какой вариант выбрать.",
])
def test_decision_support_phrases(text):
    assert controller.classify_intent(text) == Intent.DECISION_SUPPORT


@pytest.mark.parametrize("text", [
    "Дай упражнение.", "Хочу попробовать технику.", "Проведи практику.",
])
def test_practice_phrases(text):
    assert controller.classify_intent(text) == Intent.PRACTICE


@pytest.mark.parametrize("text", [
    "Ты меня не понимаешь.", "Ты повторяешься.", "Ты задаёшь одни вопросы.",
    "Я просил объяснить.", "Не давай совет.", "Хватит спрашивать.",
    "Это не помогает.", "Ты опять предлагаешь дыхание.",
])
def test_repair_phrases(text):
    assert controller.classify_intent(text) == Intent.REPAIR


@pytest.mark.parametrize("text", [
    "Сегодня хорошая погода.", "", "Как дела?",
])
def test_unknown_fallback(text):
    assert controller.classify_intent(text) == Intent.UNKNOWN


def test_negation_excludes_explicit_match():
    assert controller.classify_intent('Я не говорил "дай упражнение".') == Intent.UNKNOWN


def test_quotation_excludes_explicit_match():
    assert controller.classify_intent('Кто-то сказал: "объясни, почему так происходит".') \
        == Intent.UNKNOWN


def test_third_person_excludes_explicit_match():
    assert controller.classify_intent("Она сказала: дай упражнение.") == Intent.UNKNOWN


def test_ambiguous_language_stays_unknown():
    assert controller.classify_intent("Мне сегодня тяжело.") == Intent.UNKNOWN


# ── ResponsePlan / validator unit tests ─────────────────────────────────────

def test_vent_plan_forbids_advice_and_intervention():
    plan = controller.build_response_plan(Intent.VENT)
    assert plan.advice_allowed is False
    assert plan.intervention_allowed is False
    assert plan.listening_only is True


def test_action_plan_forbids_questions():
    plan = controller.build_response_plan(Intent.ACTION)
    assert plan.question_allowed is False
    assert plan.max_questions == 0


def test_practice_plan_requires_consent():
    plan = controller.build_response_plan(Intent.PRACTICE)
    assert plan.consent_required is True


def test_question_overload_repair_forces_zero_questions():
    plan = controller.build_response_plan(Intent.REPAIR, {RepairConstraint.QUESTION_OVERLOAD})
    assert plan.question_allowed is False
    assert plan.max_questions == 0


@pytest.mark.parametrize("text,plan_kwargs,expected_reason", [
    ("Попробуй начать с малого шага.", dict(intent=Intent.VENT, listening_only=True, advice_allowed=False, intervention_allowed=False), "advice_during_vent"),
    ("Есть простое дыхательное упражнение.", dict(intent=Intent.VENT, listening_only=True, advice_allowed=False, intervention_allowed=False), "exercise_not_allowed"),
    ("Что скажешь? И как тебе такое? И что думаешь?", dict(intent=Intent.EXPLAIN, explanation_required=True, direct_answer_required=True, max_questions=1), "too_many_questions"),
    ("Как тебе такое решение?", dict(intent=Intent.REPAIR, question_allowed=False, max_questions=0, repair_constraints={RepairConstraint.QUESTION_OVERLOAD}), "question_not_allowed"),
    ("Упражнение поможет.", dict(intent=Intent.REPAIR, repair_constraints={RepairConstraint.EXERCISE_REJECTED}), "repeated_rejected_exercise"),
    ("Окей.", dict(intent=Intent.EXPLAIN, explanation_required=True, direct_answer_required=True), "explanation_too_short"),
    ("Выбери сам, что тебе кажется правильным?", dict(intent=Intent.ACTION, question_allowed=False, max_questions=0), "choice_delegation_during_action"),
])
def test_validator_catches_each_violation(text, plan_kwargs, expected_reason):
    from therapeutic_domain import ResponsePlan
    plan = ResponsePlan(**plan_kwargs)
    ok, reason = controller.validate_controller_response(text, plan)
    assert ok is False
    assert reason == expected_reason


def test_validator_accepts_compliant_vent_response():
    from therapeutic_domain import ResponsePlan
    plan = ResponsePlan(intent=Intent.VENT, listening_only=True, advice_allowed=False,
                        intervention_allowed=False)
    text = "Похоже, тебе сейчас правда тяжело из-за того, что произошло на работе."
    ok, reason = controller.validate_controller_response(text, plan)
    assert ok is True and reason is None


# ── B. Pipeline integration: rollout off / no explicit intent ──────────────

def test_rollout_off_reproduces_prior_behavior_byte_for_byte(monkeypatch, tmp_db):
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "off")
    llm_calls = {"n": 0}
    _full_pipeline_stub_set(monkeypatch, llm_calls=llm_calls)
    run(_seed_user(1))
    user = FakeUser(1)
    msg = FakeMessage(user, "Мне нужно выговориться.")
    run(bot.pipeline(msg, msg.text, None, tg_user=user))
    assert llm_calls["n"] == 1  # ordinary scenario pipeline, not the controller
    assert run(database.list_core_sessions(1)) == []


def test_unknown_intent_falls_through_to_ordinary_pipeline(monkeypatch, tmp_db):
    llm_calls = {"n": 0}
    _full_pipeline_stub_set(monkeypatch, llm_calls=llm_calls)
    run(_seed_user(1))
    user = FakeUser(1)
    msg = FakeMessage(user, "Сегодня хорошая погода.")
    run(bot.pipeline(msg, msg.text, None, tg_user=user))
    assert llm_calls["n"] == 1
    assert run(database.list_core_sessions(1)) == []


def test_explicit_crisis_wins_over_controller(monkeypatch, tmp_db):
    crisis_calls = {"n": 0}
    async def spy(*a, **kw):
        crisis_calls["n"] += 1
    monkeypatch.setattr(bot, "trigger_crisis", spy)
    llm_calls = {"n": 0}
    _full_pipeline_stub_set(monkeypatch, llm_calls=llm_calls)
    run(_seed_user(1))
    user = FakeUser(1)
    msg = FakeMessage(user, "Объясни, почему так происходит, я хочу покончить с собой")
    run(bot.pipeline(msg, msg.text, None, tg_user=user))
    assert crisis_calls["n"] == 1
    assert llm_calls["n"] == 0
    assert run(database.list_core_sessions(1)) == []


# ── A2. Hardening §4: the Controller must never bypass ambiguity or
#        dependency boundary handling for an explicit-intent message ──────

def test_explain_plus_ambiguous_selfharm_phrase_disambiguation_wins(monkeypatch, tmp_db):
    """'Объясни, почему я хочу выйти в окно' -- EXPLAIN intent is present,
    but 'выйти в окно' is a double-meaning phrase; the deterministic
    disambiguation check must win, not the Controller's own LLM call."""
    llm_calls = {"n": 0}
    _full_pipeline_stub_set(monkeypatch, llm_calls=llm_calls)
    run(_seed_user(1))
    user = FakeUser(1)
    msg = FakeMessage(user, "Объясни, почему мне хочется выйти в окно.")
    run(bot.pipeline(msg, msg.text, None, tg_user=user))
    assert llm_calls["n"] == 0, "no Controller LLM call when ambiguity handling must win"
    assert run(database.list_core_sessions(1)) == [], "no Core state advancement"
    assert len(msg.answers) == 1  # the deterministic disambiguation question


def test_vent_plus_ambiguous_selfharm_phrase_disambiguation_wins(monkeypatch, tmp_db):
    llm_calls = {"n": 0}
    _full_pipeline_stub_set(monkeypatch, llm_calls=llm_calls)
    run(_seed_user(1))
    user = FakeUser(1)
    msg = FakeMessage(user, "Мне нужно выговориться, хочется выйти в окно.")
    run(bot.pipeline(msg, msg.text, None, tg_user=user))
    assert llm_calls["n"] == 0
    assert run(database.list_core_sessions(1)) == []


def test_action_plus_dependency_signal_dependency_redirect_wins(monkeypatch, tmp_db):
    llm_calls = {"n": 0}
    _full_pipeline_stub_set(monkeypatch, llm_calls=llm_calls)
    monkeypatch.setattr(bot.dependency_monitor, "record_message", _async(None))
    monkeypatch.setattr(bot.dependency_monitor, "assess", _async("Мягкий отклик о зависимости."))
    run(_seed_user(1))
    user = FakeUser(1)
    msg = FakeMessage(user, "Скажи сам, какой мне сделать шаг.")
    run(bot.pipeline(msg, msg.text, None, tg_user=user))
    assert llm_calls["n"] == 0, "no Controller LLM call when the dependency boundary must win"
    assert run(database.list_core_sessions(1)) == []
    assert msg.answers == [("Мягкий отклик о зависимости.", {})]


def test_practice_consent_callback_non_actionable_during_active_crisis(tmp_db):
    """Realistic path: an active crisis pauses the session via
    supersede_active_core_sessions_for_crisis (the canonical, logging-
    independent hook called from trigger_crisis) -- that PAUSED status,
    not a separate get_active_crisis check inside the callback itself, is
    what makes the stale consent tap non-actionable."""
    run(_seed_user(1))
    session = run(database.create_core_session(1, intent=Intent.PRACTICE))
    session.consent = ConsentState.PENDING
    run(database.update_core_session(session))
    run(database.supersede_active_core_sessions_for_crisis(1))
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = _fake_callback(user, msg, f"cc:consent:{session.session_id}:yes")
    run(bot.cb_cc_consent(cb))
    assert msg.answers == [], "active crisis must make the consent callback non-actionable"


# ── A3. Hardening §7: the Controller is continuous across turns, not a
#        one-turn phrase detector ────────────────────────────────────────────

def test_controller_continues_across_ordinary_followup_turns(monkeypatch, tmp_db):
    """Turn 1 has an explicit VENT phrase. Turns 2-3 do NOT match any
    explicit phrase (plain follow-up sentences) but must remain governed by
    the SAME VENT session -- continuation via the active OPEN session, not
    a fresh classifier decision each turn."""
    llm_calls = {"n": 0}
    _full_pipeline_stub_set(monkeypatch, llm_calls=llm_calls)
    run(_seed_user(1))
    user = FakeUser(1)

    run(bot.pipeline(FakeMessage(user, "Мне нужно выговориться."),
                     "Мне нужно выговориться.", None, tg_user=user))
    run(bot.pipeline(FakeMessage(user, "Сегодня начальник снова на меня накричал."),
                     "Сегодня начальник снова на меня накричал.", None, tg_user=user))
    run(bot.pipeline(FakeMessage(user, "А потом я весь вечер прокручивал это в голове."),
                     "А потом я весь вечер прокручивал это в голове.", None, tg_user=user))

    assert llm_calls["n"] == 3, "the Controller (not the legacy pipeline) handled all 3 turns"
    sessions = run(database.list_core_sessions(1))
    assert len(sessions) == 1, "continuation reuses the same session, never forks a new one"
    assert sessions[0].intent is Intent.VENT


def test_new_explicit_intent_switches_active_session(monkeypatch, tmp_db):
    """Turn 2 explicitly asks for an explanation -- an explicit signal always
    overrides plain continuation of the previous intent."""
    llm_calls = {"n": 0}
    _full_pipeline_stub_set(monkeypatch, llm_calls=llm_calls)
    run(_seed_user(1))
    user = FakeUser(1)
    run(bot.pipeline(FakeMessage(user, "Мне нужно выговориться."),
                     "Мне нужно выговориться.", None, tg_user=user))
    run(bot.pipeline(FakeMessage(user, "Теперь объясни, почему я так реагирую."),
                     "Теперь объясни, почему я так реагирую.", None, tg_user=user))
    sessions = run(database.list_core_sessions(1))
    assert len(sessions) == 1
    assert sessions[0].intent is Intent.EXPLAIN


def test_no_active_session_and_no_explicit_signal_falls_through(monkeypatch, tmp_db):
    """Without any prior explicit-intent turn, an ordinary ambiguous message
    has nothing to continue -- the Controller correctly declines and the
    existing ordinary pipeline runs unchanged (not a bug, the documented
    UNKNOWN/no-active-session boundary)."""
    llm_calls = {"n": 0}
    _full_pipeline_stub_set(monkeypatch, llm_calls=llm_calls)
    run(_seed_user(1))
    user = FakeUser(1)
    run(bot.pipeline(FakeMessage(user, "Сегодня был тяжёлый день."),
                     "Сегодня был тяжёлый день.", None, tg_user=user))
    assert llm_calls["n"] == 1
    assert run(database.list_core_sessions(1)) == []


def test_continuation_uses_bounded_known_facts_across_turns(monkeypatch, tmp_db):
    """§8/§11: facts from a claimed Phase 2 handoff must reach the prompt on
    EVERY subsequent turn (re-fetched via the linked session), not just the
    turn that originally claimed it."""
    llm_calls = {"n": 0}
    _full_pipeline_stub_set(monkeypatch, llm_calls=llm_calls)
    run(_seed_user(1))
    run(_complete_disclosure_flow_with_purpose(1, "vent"))
    user = FakeUser(1)
    run(bot.pipeline(FakeMessage(user, "мне тяжело"), "мне тяжело", None, tg_user=user))
    run(bot.pipeline(FakeMessage(user, "и так весь день"), "и так весь день", None, tg_user=user))
    second_turn_prompt = llm_calls["prompts"][1][0]["content"]
    assert "duration=weeks" in second_turn_prompt, \
        "known facts must still reach the prompt on a pure-continuation turn"


# ── B. VENT / EXPLAIN / ACTION full-turn behavior ───────────────────────────

def test_vent_turn_no_advice_creates_session(monkeypatch, tmp_db):
    llm_calls = {"n": 0}
    _full_pipeline_stub_set(
        monkeypatch, llm_reply="Похоже, это был очень тяжёлый разговор для тебя.",
        llm_calls=llm_calls)
    run(_seed_user(1))
    user = FakeUser(1)
    msg = FakeMessage(user, "Мне нужно выговориться.")
    run(bot.pipeline(msg, msg.text, None, tg_user=user))
    assert llm_calls["n"] == 1
    assert msg.answers[0][0] == "Похоже, это был очень тяжёлый разговор для тебя."
    sessions = run(database.list_core_sessions(1))
    assert len(sessions) == 1 and sessions[0].intent is Intent.VENT


def test_vent_turn_advice_violation_triggers_fallback(monkeypatch, tmp_db):
    _full_pipeline_stub_set(monkeypatch, llm_reply="Попробуй просто выспаться сегодня.")
    run(_seed_user(1))
    user = FakeUser(1)
    msg = FakeMessage(user, "Мне нужно выговориться.")
    run(bot.pipeline(msg, msg.text, None, tg_user=user))
    assert msg.answers[0][0] == controller.fallback_text("ru", Intent.VENT)


def test_action_turn_one_step_no_menu(monkeypatch, tmp_db):
    _full_pipeline_stub_set(monkeypatch, llm_reply="Напиши одно сообщение другу сегодня вечером.")
    run(_seed_user(1))
    user = FakeUser(1)
    msg = FakeMessage(user, "Скажи, что мне сделать.")
    run(bot.pipeline(msg, msg.text, None, tg_user=user))
    assert "Напиши одно сообщение" in msg.answers[0][0]
    sessions = run(database.list_core_sessions(1))
    assert sessions[0].intent is Intent.ACTION


def test_practice_turn_requests_consent(monkeypatch, tmp_db):
    _full_pipeline_stub_set(
        monkeypatch, llm_reply="Это простое упражнение на 5 чувств, займёт 3 минуты. Хочешь попробовать?")
    run(_seed_user(1))
    user = FakeUser(1)
    msg = FakeMessage(user, "Дай упражнение.")
    run(bot.pipeline(msg, msg.text, None, tg_user=user))
    sessions = run(database.list_core_sessions(1))
    assert sessions[0].intent is Intent.PRACTICE


# ── B. REPAIR: applies immediately, persists one-shot, then clears ─────────

def test_repair_question_overload_produces_no_question(monkeypatch, tmp_db):
    _full_pipeline_stub_set(monkeypatch, llm_reply="Ты прав, я снова спрашивал. Вот что я понял из сказанного.")
    run(_seed_user(1))
    user = FakeUser(1)
    msg = FakeMessage(user, "Хватит спрашивать.")
    run(bot.pipeline(msg, msg.text, None, tg_user=user))
    assert "?" not in msg.answers[0][0]
    sessions = run(database.list_core_sessions(1))
    assert RepairConstraint.QUESTION_OVERLOAD in sessions[0].repair_constraints


def test_repair_constraint_persists_bounded_then_expires(monkeypatch, tmp_db):
    """§9 (hardening): a repair constraint is neither one-shot NOR permanent
    -- it persists for a small bounded window of subsequent turns, then
    expires deterministically."""
    _full_pipeline_stub_set(monkeypatch, llm_reply="Понял, больше не буду.")
    run(_seed_user(1))
    user = FakeUser(1)
    run(bot.pipeline(FakeMessage(user, "Хватит спрашивать."), "Хватит спрашивать.", None, tg_user=user))
    s = run(database.list_core_sessions(1))[0]
    assert RepairConstraint.QUESTION_OVERLOAD in s.repair_constraints
    assert s.repair_turns_remaining == 3

    for expected_remaining in (2, 1, 0):
        run(bot.pipeline(FakeMessage(user, "Объясни, почему так происходит."),
                         "Объясни, почему так происходит.", None, tg_user=user))
        s = run(database.list_core_sessions(1))[0]
        assert s.repair_turns_remaining == expected_remaining
        if expected_remaining > 0:
            assert RepairConstraint.QUESTION_OVERLOAD in s.repair_constraints
        else:
            assert RepairConstraint.QUESTION_OVERLOAD not in s.repair_constraints, \
                "must expire deterministically, not persist forever"


def test_advice_rejected_constraint_reflected_in_next_prompt(monkeypatch, tmp_db):
    llm_calls = {"n": 0}
    _full_pipeline_stub_set(monkeypatch, llm_reply="Хорошо.", llm_calls=llm_calls)
    run(_seed_user(1))
    user = FakeUser(1)
    run(bot.pipeline(FakeMessage(user, "Не давай совет."), "Не давай совет.", None, tg_user=user))
    system_prompt = llm_calls["prompts"][0][0]["content"]
    assert "совет" in system_prompt.lower()


# ── C. Repair signal classifier detail ──────────────────────────────────────

def test_repair_signals_can_be_multiple():
    signals = controller.classify_repair_signals("Хватит спрашивать, ты повторяешься")
    assert RepairConstraint.QUESTION_OVERLOAD in signals
    assert RepairConstraint.BOT_REPEATS in signals


def test_generic_repair_defaults_to_bot_repeats():
    assert controller.classify_repair_signals("Это не помогает") == {RepairConstraint.BOT_REPEATS}


# ── D. Phase 2 handoff consumption (§6) ─────────────────────────────────────

async def _complete_disclosure_flow_with_purpose(uid: int, purpose: str) -> dict:
    flow = await database.create_disclosure_flow(uid, "ru")
    await database.advance_disclosure_flow(flow["id"], uid, from_step="SAFETY_CHECK",
                                           to_step="DIAGNOSIS_SOURCE")
    await database.advance_disclosure_flow(flow["id"], uid, from_step="DIAGNOSIS_SOURCE",
                                           to_step="DURATION", diagnosis_source="self")
    await database.advance_disclosure_flow(flow["id"], uid, from_step="DURATION",
                                           to_step="FUNCTIONING", answers_json='{"duration":"weeks"}')
    await database.advance_disclosure_flow(flow["id"], uid, from_step="FUNCTIONING",
                                           to_step="BASIC_ACTIVITIES",
                                           answers_json='{"duration":"weeks","functioning":"harder"}')
    await database.advance_disclosure_flow(
        flow["id"], uid, from_step="BASIC_ACTIVITIES", to_step="SUPPORT",
        answers_json='{"duration":"weeks","functioning":"harder","basic_activities":"managing"}')
    await database.advance_disclosure_flow(
        flow["id"], uid, from_step="SUPPORT", to_step="PURPOSE",
        answers_json='{"duration":"weeks","functioning":"harder","basic_activities":"managing","support":"close_ones"}')
    await database.close_disclosure_flow(
        flow["id"], uid, from_step="PURPOSE", status="completed",
        answers_json=('{"duration":"weeks","functioning":"harder","basic_activities":"managing",'
                     f'"support":"close_ones","purpose":"{purpose}"}}'))
    return await database.get_disclosure_flow(flow["id"], uid)


def test_handoff_vent_purpose_maps_to_vent_intent_no_reasking(monkeypatch, tmp_db):
    llm_calls = {"n": 0}
    _full_pipeline_stub_set(monkeypatch, llm_reply="Я рядом, расскажи, что происходит.",
                            llm_calls=llm_calls)
    run(_seed_user(1))
    run(_complete_disclosure_flow_with_purpose(1, "vent"))
    user = FakeUser(1)
    msg = FakeMessage(user, "мне просто тяжело сегодня")
    run(bot.pipeline(msg, msg.text, None, tg_user=user))
    assert len(msg.answers) == 1, "exactly one answer -- no disclosure questions asked again"
    sessions = run(database.list_core_sessions(1))
    assert sessions[0].intent is Intent.VENT
    assert sessions[0].handoff_flow_id is not None
    claimed = run(database.get_disclosure_flow(int(sessions[0].handoff_flow_id), 1))
    assert claimed["handoff_status"] == "claimed"


def test_handoff_known_facts_reach_the_prompt(monkeypatch, tmp_db):
    llm_calls = {"n": 0}
    _full_pipeline_stub_set(monkeypatch, llm_calls=llm_calls)
    run(_seed_user(1))
    run(_complete_disclosure_flow_with_purpose(1, "vent"))
    user = FakeUser(1)
    run(bot.pipeline(FakeMessage(user, "мне тяжело"), "мне тяжело", None, tg_user=user))
    system_prompt = llm_calls["prompts"][0][0]["content"]
    assert "duration=weeks" in system_prompt


def test_handoff_claimed_only_once_no_duplicate_session(monkeypatch, tmp_db):
    _full_pipeline_stub_set(monkeypatch)
    run(_seed_user(1))
    run(_complete_disclosure_flow_with_purpose(1, "vent"))
    user = FakeUser(1)
    run(bot.pipeline(FakeMessage(user, "мне тяжело"), "мне тяжело", None, tg_user=user))
    run(bot.pipeline(FakeMessage(user, "и правда тяжело"), "и правда тяжело", None, tg_user=user))
    sessions = run(database.list_core_sessions(1))
    assert len(sessions) == 1, "repeated claim must not create a second session"


def test_incomplete_disclosure_flow_produces_no_handoff(monkeypatch, tmp_db):
    _full_pipeline_stub_set(monkeypatch)
    run(_seed_user(1))
    run(database.create_disclosure_flow(1, "ru"))  # never completed
    user = FakeUser(1)
    msg = FakeMessage(user, "Объясни, почему так происходит.")
    run(bot.pipeline(msg, msg.text, None, tg_user=user))
    sessions = run(database.list_core_sessions(1))
    assert sessions[0].handoff_flow_id is None


def test_crisis_superseded_disclosure_flow_produces_no_handoff(tmp_db):
    run(_seed_user(1))
    flow = run(_complete_disclosure_flow_with_purpose(1, "vent"))
    run(database.supersede_active_disclosure_flows_for_crisis(1))
    assert run(database.get_unclaimed_handoff(1)) is None


def test_diagnosis_source_preserved_not_confirmed_or_denied(monkeypatch, tmp_db):
    _full_pipeline_stub_set(monkeypatch)
    run(_seed_user(1))
    run(_complete_disclosure_flow_with_purpose(1, "vent"))
    user = FakeUser(1)
    msg = FakeMessage(user, "мне тяжело")
    run(bot.pipeline(msg, msg.text, None, tg_user=user))
    sessions = run(database.list_core_sessions(1))
    handoff = run(database.get_disclosure_flow(int(sessions[0].handoff_flow_id), 1))
    assert handoff["diagnosis_source"] == "self"


# ── D. Restart safety and cross-user isolation ──────────────────────────────

def test_session_state_survives_restart(monkeypatch, tmp_db):
    _full_pipeline_stub_set(monkeypatch, llm_reply="Хорошо, расскажи ещё.")
    run(_seed_user(1))
    user = FakeUser(1)
    run(bot.pipeline(FakeMessage(user, "Мне нужно выговориться."),
                     "Мне нужно выговориться.", None, tg_user=user))
    reloaded = run(database.list_core_sessions(1))
    assert len(reloaded) == 1
    assert reloaded[0].intent is Intent.VENT


def test_cross_user_sessions_isolated(monkeypatch, tmp_db):
    _full_pipeline_stub_set(monkeypatch)
    run(_seed_user(1)); run(_seed_user(2))
    run(bot.pipeline(FakeMessage(FakeUser(1), "Мне нужно выговориться."),
                     "Мне нужно выговориться.", None, tg_user=FakeUser(1)))
    assert run(database.list_core_sessions(2)) == []
    assert len(run(database.list_core_sessions(1))) == 1


# ── D. /start pauses and resumes controller sessions ────────────────────────

def test_start_pauses_active_session(monkeypatch, tmp_db):
    _full_pipeline_stub_set(monkeypatch)
    run(_seed_user(1))
    user = FakeUser(1)
    run(bot.pipeline(FakeMessage(user, "Мне нужно выговориться."),
                     "Мне нужно выговориться.", None, tg_user=user))
    run(bot.cmd_start(FakeMessage(user)))
    sessions = run(database.list_core_sessions(1))
    assert sessions[0].lifecycle_status is LifecycleStatus.PAUSED


def test_new_explicit_intent_resumes_paused_session(monkeypatch, tmp_db):
    _full_pipeline_stub_set(monkeypatch)
    run(_seed_user(1))
    user = FakeUser(1)
    run(bot.pipeline(FakeMessage(user, "Мне нужно выговориться."),
                     "Мне нужно выговориться.", None, tg_user=user))
    run(bot.cmd_start(FakeMessage(user)))
    run(bot.pipeline(FakeMessage(user, "Объясни, почему так происходит."),
                     "Объясни, почему так происходит.", None, tg_user=user))
    sessions = run(database.list_core_sessions(1))
    assert len(sessions) == 1
    assert sessions[0].lifecycle_status is LifecycleStatus.OPEN
    assert sessions[0].intent is Intent.EXPLAIN


# ── E. Rollout matrix ────────────────────────────────────────────────────────

@pytest.mark.parametrize("mode,uid,expected", [
    ("off", 1, False), ("owner", 1, True), ("owner", 999, False),
    ("all", 999, True),
])
def test_rollout_matrix(monkeypatch, mode, uid, expected):
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", mode)
    assert run(ac.core_rollout_allowed(uid)) is expected


def test_rollout_invalid_mode_fails_closed(monkeypatch):
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "totally_bogus")
    assert run(ac.core_rollout_allowed(1)) is False


def test_no_second_llm_call_for_explicit_intent_turn(monkeypatch, tmp_db):
    llm_calls = {"n": 0}
    _full_pipeline_stub_set(monkeypatch, llm_calls=llm_calls)
    run(_seed_user(1))
    user = FakeUser(1)
    run(bot.pipeline(FakeMessage(user, "Мне нужно выговориться."),
                     "Мне нужно выговориться.", None, tg_user=user))
    assert llm_calls["n"] == 1, "exactly one primary LLM completion per controller turn"


def test_privacy_export_delete_covers_core_sessions(tmp_db):
    run(_seed_user(1))
    run(database.create_core_session(1, intent=Intent.VENT))
    exported = run(database.export_all_personal_data(1))
    assert len(exported["core_sessions"]) == 1
    summary = run(database.delete_all_personal_data(1))
    assert summary["core_sessions"] == 1
    assert run(database.list_core_sessions(1)) == []


# ── F. ResponsePlan fails closed on inconsistent construction (§9) ─────────

def test_vent_plan_rejects_advice_allowed_true():
    with pytest.raises(ValueError):
        ResponsePlan(intent=Intent.VENT, listening_only=True, advice_allowed=True,
                    intervention_allowed=False)


def test_explain_plan_rejects_missing_direct_answer_required():
    with pytest.raises(ValueError):
        ResponsePlan(intent=Intent.EXPLAIN, explanation_required=True,
                    direct_answer_required=False)


def test_action_plan_rejects_question_allowed_true():
    with pytest.raises(ValueError):
        ResponsePlan(intent=Intent.ACTION, question_allowed=True)


def test_practice_plan_rejects_consent_not_required():
    with pytest.raises(ValueError):
        ResponsePlan(intent=Intent.PRACTICE, consent_required=False)


def test_valid_plans_construct_without_error():
    controller.build_response_plan(Intent.VENT)
    controller.build_response_plan(Intent.EXPLAIN)
    controller.build_response_plan(Intent.ACTION)
    controller.build_response_plan(Intent.PRACTICE)
    controller.build_response_plan(Intent.REPAIR, {RepairConstraint.QUESTION_OVERLOAD})


# ── G. Crisis supersedes ALL Core work, not just disclosure (§6) ───────────

def test_crisis_pauses_open_core_session_and_withdraws_consent(tmp_db):
    run(_seed_user(1))
    session = run(database.create_core_session(1, intent=Intent.PRACTICE))
    session.consent = ConsentState.PENDING
    run(database.update_core_session(session))
    run(database.supersede_active_core_sessions_for_crisis(1))
    reloaded = run(database.get_core_session(session.session_id, 1))
    assert reloaded.lifecycle_status is LifecycleStatus.PAUSED
    assert reloaded.consent is ConsentState.WITHDRAWN


def test_crisis_via_real_trigger_crisis_pauses_core_session(monkeypatch, tmp_db):
    run(_seed_user(1))
    session = run(database.create_core_session(1, intent=Intent.VENT))
    risk = {"score": 50, "level": "critical", "categories": ["suicide"],
           "implicit": False, "ambiguous_phrases": []}
    run(bot.trigger_crisis(FakeMessage(FakeUser(1)), 1, "u1", "text", risk, "ru"))
    reloaded = run(database.get_core_session(session.session_id, 1))
    assert reloaded.lifecycle_status is LifecycleStatus.PAUSED


def test_core_session_supersession_survives_audit_logging_raising(tmp_db, monkeypatch):
    run(_seed_user(1))
    session = run(database.create_core_session(1, intent=Intent.VENT))
    async def raising_log(*a, **kw):
        raise Exception("simulated DB failure")
    monkeypatch.setattr(bot, "log_crisis_event", raising_log)
    risk = {"score": 50, "level": "critical", "categories": ["suicide"],
           "implicit": False, "ambiguous_phrases": []}
    run(bot.trigger_crisis(FakeMessage(FakeUser(1)), 1, "u1", "text", risk, "ru"))
    reloaded = run(database.get_core_session(session.session_id, 1))
    assert reloaded.lifecycle_status is LifecycleStatus.PAUSED


def test_pending_practice_consent_non_actionable_after_crisis(monkeypatch, tmp_db):
    run(_seed_user(1))
    session = run(database.create_core_session(1, intent=Intent.PRACTICE))
    session.consent = ConsentState.PENDING
    run(database.update_core_session(session))
    run(database.supersede_active_core_sessions_for_crisis(1))
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = types.SimpleNamespace(from_user=user, message=msg,
                               data=f"cc:consent:{session.session_id}:yes", answered=0)
    async def _answer(*a, **kw):
        cb.answered += 1
    cb.answer = _answer
    run(bot.cb_cc_consent(cb))
    assert cb.answered == 1
    assert msg.answers == []


# ── H. Atomic handoff claim: concurrency (§5) ───────────────────────────────

def test_concurrent_handoff_claims_only_one_succeeds(tmp_db):
    async def go():
        await _seed_user(1)
        await _complete_disclosure_flow_with_purpose(1, "vent")
        results = await asyncio.gather(
            database.claim_handoff_and_get_or_create_session(1),
            database.claim_handoff_and_get_or_create_session(1),
        )
        successes = [r for r in results if r[0] is not None]
        assert len(successes) == 1, "exactly one of two concurrent claims must succeed"
        sessions = await database.list_core_sessions(1)
        assert len(sessions) == 1, "concurrent claims must not create two sessions"
    run(go())


def test_repeated_atomic_claim_call_is_idempotent_failure(tmp_db):
    run(_seed_user(1))
    run(_complete_disclosure_flow_with_purpose(1, "vent"))
    first = run(database.claim_handoff_and_get_or_create_session(1))
    assert first[0] is not None
    second = run(database.claim_handoff_and_get_or_create_session(1))
    assert second == (None, None)


def test_atomic_claim_cross_user_isolated(tmp_db):
    run(_seed_user(1)); run(_seed_user(2))
    run(_complete_disclosure_flow_with_purpose(1, "vent"))
    result = run(database.claim_handoff_and_get_or_create_session(2))
    assert result == (None, None)


def test_atomic_claim_does_not_set_intent_caller_must(tmp_db):
    """Direct proof of the §2/§3 correction: the atomic claim function alone
    never persists an intent -- it stays UNKNOWN until a caller (bot.py)
    explicitly sets and persists it via its own stale-guarded update_core_
    session() call."""
    run(_seed_user(1))
    run(_complete_disclosure_flow_with_purpose(1, "vent"))
    handoff, session = run(database.claim_handoff_and_get_or_create_session(1))
    assert handoff is not None
    assert session.intent is Intent.UNKNOWN
    persisted = run(database.get_core_session(session.session_id, 1))
    assert persisted.intent is Intent.UNKNOWN


def test_crisis_superseded_handoff_cannot_be_claimed(tmp_db):
    run(_seed_user(1))
    run(_complete_disclosure_flow_with_purpose(1, "vent"))
    run(database.supersede_active_disclosure_flows_for_crisis(1))
    result = run(database.claim_handoff_and_get_or_create_session(1))
    assert result == (None, None)


def test_incomplete_flow_handoff_cannot_be_claimed(tmp_db):
    run(_seed_user(1))
    run(database.create_disclosure_flow(1, "ru"))  # never completed
    result = run(database.claim_handoff_and_get_or_create_session(1))
    assert result == (None, None)


def test_handoff_link_survives_restart(tmp_db):
    run(_seed_user(1))
    run(_complete_disclosure_flow_with_purpose(1, "vent"))
    handoff, session = run(database.claim_handoff_and_get_or_create_session(1))
    reloaded = run(database.get_core_session(session.session_id, 1))
    assert reloaded.handoff_flow_id == str(handoff["id"])


# ── I. PRACTICE consent flow (§12) ──────────────────────────────────────────

def _fake_callback(user, msg, data):
    cb = types.SimpleNamespace(from_user=user, message=msg, data=data, answered=0)
    async def _answer(*a, **kw):
        cb.answered += 1
    cb.answer = _answer
    return cb


def test_practice_no_content_before_consent(monkeypatch, tmp_db):
    _full_pipeline_stub_set(monkeypatch, llm_reply="Есть простая практика на 5 чувств. Хочешь попробовать?")
    run(_seed_user(1))
    user = FakeUser(1)
    msg = FakeMessage(user, "Дай упражнение.")
    run(bot.pipeline(msg, msg.text, None, tg_user=user))
    assert "grounding" not in msg.answers[0][0].lower()
    session = run(database.list_core_sessions(1))[0]
    assert session.consent is ConsentState.PENDING
    kb = msg.answers[0][1]["reply_markup"]
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert labels == ["Да", "Нет"]


def test_practice_consent_yes_delivers_practice_content(monkeypatch, tmp_db):
    _full_pipeline_stub_set(monkeypatch, llm_reply="Хочешь попробовать практику?")
    run(_seed_user(1))
    user = FakeUser(1)
    run(bot.pipeline(FakeMessage(user, "Дай упражнение."), "Дай упражнение.", None, tg_user=user))
    session = run(database.list_core_sessions(1))[0]
    msg2 = FakeMessage(user)
    cb = _fake_callback(user, msg2, f"cc:consent:{session.session_id}:yes")
    run(bot.cb_cc_consent(cb))
    assert cb.answered == 1
    assert len(msg2.answers) == 1
    reloaded = run(database.get_core_session(session.session_id, 1))
    assert reloaded.consent is ConsentState.GRANTED


def test_practice_consent_no_declines_no_content(monkeypatch, tmp_db):
    _full_pipeline_stub_set(monkeypatch, llm_reply="Хочешь попробовать практику?")
    run(_seed_user(1))
    user = FakeUser(1)
    run(bot.pipeline(FakeMessage(user, "Дай упражнение."), "Дай упражнение.", None, tg_user=user))
    session = run(database.list_core_sessions(1))[0]
    msg2 = FakeMessage(user)
    cb = _fake_callback(user, msg2, f"cc:consent:{session.session_id}:no")
    run(bot.cb_cc_consent(cb))
    reloaded = run(database.get_core_session(session.session_id, 1))
    assert reloaded.consent is ConsentState.DECLINED
    assert "grounding" not in msg2.answers[0][0].lower()


def test_practice_duplicate_consent_tap_does_not_deliver_twice(monkeypatch, tmp_db):
    _full_pipeline_stub_set(monkeypatch, llm_reply="Хочешь попробовать практику?")
    run(_seed_user(1))
    user = FakeUser(1)
    run(bot.pipeline(FakeMessage(user, "Дай упражнение."), "Дай упражнение.", None, tg_user=user))
    session = run(database.list_core_sessions(1))[0]
    run(bot.cb_cc_consent(_fake_callback(user, FakeMessage(user), f"cc:consent:{session.session_id}:yes")))
    msg2 = FakeMessage(user)
    cb2 = _fake_callback(user, msg2, f"cc:consent:{session.session_id}:yes")
    run(bot.cb_cc_consent(cb2))
    assert cb2.answered == 1
    assert msg2.answers == [], "a second consent tap must not re-deliver the practice"


def test_practice_consent_cross_user_rejected(monkeypatch, tmp_db):
    _full_pipeline_stub_set(monkeypatch, llm_reply="Хочешь попробовать практику?")
    run(_seed_user(1)); run(_seed_user(2))
    user = FakeUser(1)
    run(bot.pipeline(FakeMessage(user, "Дай упражнение."), "Дай упражнение.", None, tg_user=user))
    session = run(database.list_core_sessions(1))[0]
    attacker = FakeUser(2)
    msg2 = FakeMessage(attacker)
    cb = _fake_callback(attacker, msg2, f"cc:consent:{session.session_id}:yes")
    run(bot.cb_cc_consent(cb))
    assert msg2.answers == []
    reloaded = run(database.get_core_session(session.session_id, 1))
    assert reloaded.consent is ConsentState.PENDING


def test_practice_consent_rollout_off_non_actionable(monkeypatch, tmp_db):
    _full_pipeline_stub_set(monkeypatch, llm_reply="Хочешь попробовать практику?")
    run(_seed_user(1))
    user = FakeUser(1)
    run(bot.pipeline(FakeMessage(user, "Дай упражнение."), "Дай упражнение.", None, tg_user=user))
    session = run(database.list_core_sessions(1))[0]
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "off")
    msg2 = FakeMessage(user)
    cb = _fake_callback(user, msg2, f"cc:consent:{session.session_id}:yes")
    run(bot.cb_cc_consent(cb))
    assert msg2.answers == []


# ── J. Stale-response suppression for controller turns (§3) ────────────────

def test_stale_controller_response_suppressed_by_newer_turn(monkeypatch, tmp_db):
    """Two-phase API (hardening §5): _controller_claim_turn is the FAST,
    always-allowed-before-staleness half (inbound user-message persistence +
    idempotent session claim -- explicitly allowed even for a turn that will
    turn out stale); _controller_generate_and_deliver is the SLOW half where
    the stale check actually happens, gating the assistant response only."""
    _full_pipeline_stub_set(monkeypatch, llm_reply="Слышу тебя.")
    run(_seed_user(1))
    user = FakeUser(1)
    msg = FakeMessage(user, "Мне нужно выговориться.")
    claim = run(bot._controller_claim_turn(1, msg.text, "ru"))
    assert claim is not None
    stale_gen = bot._bump_user_generation(1)
    bot._bump_user_generation(1)  # a newer turn has since started
    risk = {"score": 0, "level": "low", "categories": [], "implicit": False, "ambiguous_phrases": []}
    run(bot._controller_generate_and_deliver(msg, 1, claim, stale_gen, risk))
    # Delivery and the assistant response for THIS turn are suppressed
    # (matching PR#67's existing ordinary-path contract exactly):
    assert msg.answers == [], "a superseded controller turn must not be delivered"

    async def _messages():
        rows = await database.export_all_personal_data(1)
        return [m for m in rows["messages"] if m.get("scenario") == "controller"]
    msgs = run(_messages())
    # Inbound user-message persistence IS allowed before stale detection
    # (explicitly, per the hardening correction) -- only the assistant side
    # of a stale turn is suppressed.
    assert [m["role"] for m in msgs] == ["user"], \
        "the inbound user message persists even for a turn that turns out stale; no assistant row"
    sessions = run(database.list_core_sessions(1))
    assert len(sessions) == 1


# ── K2. Real concurrency: a slower turn must not overwrite a faster newer
#        turn's canonical state (§2 of the second correction round) ────────

def test_slower_turn_never_overwrites_faster_newer_turns_canonical_state(monkeypatch, tmp_db):
    """Turn A (VENT) starts first but is slow. Turn B (ACTION) starts after
    A and finishes first. Required: final canonical intent is ACTION, A's
    response is never delivered, A contributes no repair constraint/consent/
    requested action to the session."""
    run(_seed_user(1))
    user = FakeUser(1)

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
        def __init__(self, content):
            self.message = types.SimpleNamespace(content=content)

    async def fake_create(*a, **kw):
        user_msg = kw["messages"][1]["content"]
        if "выговориться" in user_msg:
            await asyncio.sleep(0.05)  # turn A: slower
            return types.SimpleNamespace(choices=[_Choice("Похоже, тебе сейчас тяжело.")])
        await asyncio.sleep(0.01)  # turn B: faster, but started later
        return types.SimpleNamespace(choices=[_Choice("Сделай сегодня один маленький шаг.")])
    monkeypatch.setattr(bot.client.chat.completions, "create", fake_create)

    msg_a = FakeMessage(user, "Мне нужно выговориться.")
    msg_b = FakeMessage(user, "Скажи, что мне сделать.")

    async def run_both():
        task_a = asyncio.create_task(bot.pipeline(msg_a, msg_a.text, None, tg_user=user))
        await asyncio.sleep(0.01)  # A has captured its generation before B starts
        task_b = asyncio.create_task(bot.pipeline(msg_b, msg_b.text, None, tg_user=user))
        await asyncio.gather(task_a, task_b)
    run(run_both())

    assert msg_a.answers == [], "no late response from the superseded turn A"
    assert len(msg_b.answers) == 1
    sessions = run(database.list_core_sessions(1))
    assert len(sessions) == 1, "A must not create a competing session"
    assert sessions[0].intent is Intent.ACTION, "final canonical intent must be B's (ACTION)"
    assert sessions[0].repair_constraints == set(), "A contributes no repair constraint"
    assert sessions[0].consent is ConsentState.ABSENT, "A contributes no consent state"


def test_stale_turn_cannot_add_repair_constraint(monkeypatch, tmp_db):
    run(_seed_user(1))
    user = FakeUser(1)
    _full_pipeline_stub_set(monkeypatch, llm_reply="Понял.")
    msg = FakeMessage(user, "Ты задаёшь одни вопросы.")
    claim = run(bot._controller_claim_turn(1, msg.text, "ru"))
    assert claim is not None
    # The repair constraint IS present on the in-memory claim bundle (it was
    # correctly recognized) -- the question is whether it reaches the DB.
    assert RepairConstraint.QUESTION_OVERLOAD in claim["session"].repair_constraints
    stale_gen = bot._bump_user_generation(1)
    bot._bump_user_generation(1)
    risk = {"score": 0, "level": "low", "categories": [], "implicit": False, "ambiguous_phrases": []}
    run(bot._controller_generate_and_deliver(msg, 1, claim, stale_gen, risk))
    sessions = run(database.list_core_sessions(1))
    assert len(sessions) == 1
    assert sessions[0].repair_constraints == set(), \
        "a stale turn must not persist a repair constraint to the session"


# ── K. SessionState backward compatibility (§8) ─────────────────────────────

def test_session_state_deserializes_pre_phase3_json_without_handoff_field():
    import json as _json
    from therapeutic_domain import SessionState
    old_json = _json.dumps({
        "session_id": "1", "user_id": 1, "intent": "VENT", "phase": "OPENING",
        "lifecycle_status": "OPEN", "consent": "ABSENT", "active_goal": None,
        "active_intervention_id": None, "pending_outcome": False, "repair_constraints": [],
    })
    state = SessionState.from_dict(_json.loads(old_json))
    assert state.handoff_flow_id is None
    assert state.intent is Intent.VENT
    reserialized = state.to_dict()
    assert reserialized["handoff_flow_id"] is None
    roundtrip = SessionState.from_dict(_json.loads(_json.dumps(reserialized)))
    assert roundtrip.to_dict() == reserialized


# ── L. Bounded recent context feeds repair turns (§7/§8 hardening) ─────────

def test_repair_signals_catch_opyat_vodrosy_and_nichego_ne_obyasnyaesh():
    """The exact hardening acceptance phrase must trigger BOTH the
    question-overload and the missed-explanation repair constraints --
    without this, the scenario below could never reach REPAIR at all."""
    signals = controller.classify_repair_signals(
        "Ты опять задаёшь вопросы и ничего не объясняешь.")
    assert RepairConstraint.QUESTION_OVERLOAD in signals
    assert RepairConstraint.MISSED_EXPLANATION in signals


def test_bounded_recent_context_repair_scenario(monkeypatch, tmp_db):
    """Hardening §7 exact acceptance scenario: 'Я почти не спал.' / 'На
    работе всё валится из рук.' / 'Ты опять задаёшь вопросы и ничего не
    объясняешь.' The final REPAIR turn's system prompt must carry the sleep
    and work facts from the first two turns (bounded recent_context, fetched
    before this turn's own message is saved) so the model can reuse them
    without asking the user to repeat anything, and the delivered response
    must contain no question (QUESTION_OVERLOAD)."""
    llm_calls = {"n": 0}
    replies = [
        "Слышу тебя.",
        "Понимаю, это тяжело.",
        "Ты прав, я снова задавал вопросы вместо объяснений. Вижу: ты почти "
        "не спал, и на работе всё валится из рук. Давай разберём это по "
        "порядку.",
    ]

    class _Choice:
        def __init__(self, content):
            self.message = types.SimpleNamespace(content=content)

    async def fake_create(*a, **kw):
        i = llm_calls["n"]
        llm_calls["n"] += 1
        llm_calls.setdefault("prompts", []).append(kw.get("messages"))
        return types.SimpleNamespace(choices=[_Choice(replies[min(i, len(replies) - 1)])])

    _full_pipeline_stub_set(monkeypatch, llm_calls=llm_calls)
    monkeypatch.setattr(bot.client.chat.completions, "create", fake_create)

    run(_seed_user(1))
    user = FakeUser(1)

    run(bot.pipeline(FakeMessage(user, "Мне нужно выговориться. Я почти не спал."),
                     "Мне нужно выговориться. Я почти не спал.", None, tg_user=user))
    run(bot.pipeline(FakeMessage(user, "На работе всё валится из рук."),
                     "На работе всё валится из рук.", None, tg_user=user))
    msg3 = FakeMessage(user, "Ты опять задаёшь вопросы и ничего не объясняешь.")
    run(bot.pipeline(msg3, msg3.text, None, tg_user=user))

    assert llm_calls["n"] == 3, "one primary LLM call per controller turn, no extra classifier calls"
    system_prompt_3 = llm_calls["prompts"][2][0]["content"]
    assert "не спал" in system_prompt_3, "recent_context must carry turn 1's fact into turn 3's prompt"
    assert "работе" in system_prompt_3, "recent_context must carry turn 2's fact into turn 3's prompt"

    sessions = run(database.list_core_sessions(1))
    assert RepairConstraint.QUESTION_OVERLOAD in sessions[0].repair_constraints
    assert RepairConstraint.MISSED_EXPLANATION in sessions[0].repair_constraints

    final = msg3.answers[0][0]
    assert "?" not in final, "QUESTION_OVERLOAD must be enforced on the delivered response"
    assert "спал" in final and "работ" in final, \
        "the delivered response must actually reuse the known recent facts"


def test_recent_context_excludes_the_current_turns_own_message(tmp_db):
    """recent_context is fetched (and this turn's own message saved) inside
    _controller_claim_turn -- the current turn's own text must never appear
    in its own prompt's recent-context section (only PRIOR turns)."""
    run(_seed_user(1))
    run(bot._controller_claim_turn(1, "Мне нужно выговориться.", "ru"))
    claim2 = run(bot._controller_claim_turn(1, "Второе сообщение.", "ru"))
    assert claim2["recent_context"] == ["Мне нужно выговориться."]


# ── M. Atomic consent CAS concurrency (§4 hardening) ────────────────────────

def test_concurrent_consent_taps_exactly_one_wins(tmp_db):
    """Two concurrent 'yes' taps on the SAME pending consent race against
    database.transition_core_session_consent directly -- the atomic
    optimistic-concurrency CAS (WHERE state_json=?) must let exactly one
    succeed, never both, regardless of interleaving."""
    async def go():
        await _seed_user(1)
        session = await database.create_core_session(1, intent=Intent.PRACTICE)
        session.consent = ConsentState.PENDING
        await database.update_core_session(session)
        results = await asyncio.gather(
            database.transition_core_session_consent(
                session.session_id, 1, from_consent="PENDING", to_consent="GRANTED"),
            database.transition_core_session_consent(
                session.session_id, 1, from_consent="PENDING", to_consent="GRANTED"),
        )
        return session, results
    session, results = run(go())
    assert sorted(results) == [False, True], "exactly one concurrent transition must win"
    reloaded = run(database.get_core_session(session.session_id, 1))
    assert reloaded.consent is ConsentState.GRANTED


def test_consent_transition_rejects_wrong_from_state(tmp_db):
    run(_seed_user(1))
    session = run(database.create_core_session(1, intent=Intent.PRACTICE))
    session.consent = ConsentState.GRANTED
    run(database.update_core_session(session))
    ok = run(database.transition_core_session_consent(
        session.session_id, 1, from_consent="PENDING", to_consent="DECLINED"))
    assert ok is False
    reloaded = run(database.get_core_session(session.session_id, 1))
    assert reloaded.consent is ConsentState.GRANTED, "no-op on a from_consent mismatch"


def test_consent_transition_rejects_paused_session(tmp_db):
    run(_seed_user(1))
    session = run(database.create_core_session(1, intent=Intent.PRACTICE))
    session.consent = ConsentState.PENDING
    run(database.update_core_session(session))
    run(database.supersede_active_core_sessions_for_crisis(1))
    ok = run(database.transition_core_session_consent(
        session.session_id, 1, from_consent="PENDING", to_consent="GRANTED"))
    assert ok is False, "a paused (crisis-superseded) session must reject the transition"


# ── N. Ingestion contract, exact validator args, adversarial output ────────

def test_controller_llm_call_never_runs_while_ingestion_lock_held(monkeypatch, tmp_db):
    """Explicit lock-state assertion (hardening §2), not just absence of
    deadlock: the per-user ingestion lock must be fully released -- not
    merely non-blocking -- before the Controller's LLM call fires."""
    observed = {}

    class _Choice:
        def __init__(self, content):
            self.message = types.SimpleNamespace(content=content)

    async def fake_create(*a, **kw):
        holder = bot._ingest_registry.get(1)
        observed["locked"] = holder.lock.locked() if holder else False
        return types.SimpleNamespace(choices=[_Choice("Понял.")])

    _full_pipeline_stub_set(monkeypatch)
    monkeypatch.setattr(bot.client.chat.completions, "create", fake_create)
    run(_seed_user(1))
    user = FakeUser(1)
    run(bot.pipeline(FakeMessage(user, "Мне нужно выговориться."),
                     "Мне нужно выговориться.", None, tg_user=user))
    assert "locked" in observed, "the LLM call must actually have fired"
    assert observed["locked"] is False, \
        "the ingestion lock must be released before the Controller's LLM call"


def test_controller_claimed_turn_skips_legacy_router_logging_but_keeps_state_save(monkeypatch, tmp_db):
    """Zero-legacy-side-effect spy (hardening §1): once the Controller
    claims a turn, the ordinary pipeline's scenario router-decision logging
    must not run for it -- that log is a per-scenario research snapshot and
    would misrepresent which system actually decided the turn (hardening
    §6). The rolling emotional-state update is DELIBERATELY exempt:
    state_engine is cross-cutting -- it feeds crisis/stage/capacity
    detection on FUTURE turns regardless of which subsystem (Controller or
    legacy pipeline) handles any given turn, so it must keep advancing even
    while the Controller owns this one."""
    calls = {"log_router_decision": 0, "save_state": 0}

    async def spy_log(*a, **kw):
        calls["log_router_decision"] += 1

    async def spy_save_state(*a, **kw):
        calls["save_state"] += 1

    _full_pipeline_stub_set(monkeypatch)
    monkeypatch.setattr(bot, "log_router_decision", spy_log)
    monkeypatch.setattr(bot, "save_state", spy_save_state)
    run(_seed_user(1))
    user = FakeUser(1)
    run(bot.pipeline(FakeMessage(user, "Мне нужно выговориться."),
                     "Мне нужно выговориться.", None, tg_user=user))
    assert calls["log_router_decision"] == 0, \
        "a Controller-claimed turn must not log a legacy router decision"
    assert calls["save_state"] == 1, \
        "the rolling emotional-state update is cross-cutting and must still run"


def test_ordinary_turn_still_runs_legacy_router_logging_and_state_save(monkeypatch, tmp_db):
    """The inverse of the spy test above: an ORDINARY (non-explicit-intent)
    message must still exercise the legacy side effects unchanged -- proving
    the bypass is intent-gated, not a global regression."""
    calls = {"log_router_decision": 0, "save_state": 0}

    async def spy_log(*a, **kw):
        calls["log_router_decision"] += 1

    async def spy_save_state(*a, **kw):
        calls["save_state"] += 1

    _full_pipeline_stub_set(monkeypatch)
    monkeypatch.setattr(bot, "log_router_decision", spy_log)
    monkeypatch.setattr(bot, "save_state", spy_save_state)
    run(_seed_user(1))
    user = FakeUser(1)
    run(bot.pipeline(FakeMessage(user, "Сегодня был обычный день."),
                     "Сегодня был обычный день.", None, tg_user=user))
    assert calls["log_router_decision"] >= 1
    assert calls["save_state"] >= 1


def test_controller_calls_validate_response_with_context_using_exact_args(monkeypatch, tmp_db):
    """Hardening §3: prove the Controller uses the SAME stronger,
    context-aware validator the ordinary pipeline uses, called with the
    real candidate text, the real user text, the real risk dict, and the
    real language -- not a weaker or stubbed-out check."""
    captured = {}

    def spy(candidate, user_text, risk, lang):
        captured["args"] = (candidate, user_text, risk, lang)
        return True, None

    _full_pipeline_stub_set(monkeypatch, llm_reply="Слышу тебя, я рядом.")
    monkeypatch.setattr(bot, "validate_response_with_context", spy)
    run(_seed_user(1))
    user = FakeUser(1)
    msg = FakeMessage(user, "Мне нужно выговориться.")
    run(bot.pipeline(msg, msg.text, None, tg_user=user))

    assert "args" in captured, "validate_response_with_context must actually be called"
    candidate, user_text, risk, lang = captured["args"]
    assert candidate == "Слышу тебя, я рядом."
    assert user_text == "Мне нужно выговориться."
    assert isinstance(risk, dict) and "score" in risk and "level" in risk
    assert lang == "ru"


def test_adversarial_llm_question_under_question_overload_falls_back(monkeypatch, tmp_db):
    """Adversarial model output (hardening §12): the model asks a question
    despite an active QUESTION_OVERLOAD constraint. The Controller Fidelity
    Validator must catch it and substitute the intent-specific fallback --
    the adversarial text must never reach the user."""
    _full_pipeline_stub_set(monkeypatch, llm_reply="Ты прав, но как тебе такое решение?")
    run(_seed_user(1))
    user = FakeUser(1)
    msg = FakeMessage(user, "Хватит спрашивать.")
    run(bot.pipeline(msg, msg.text, None, tg_user=user))
    delivered = msg.answers[0][0]
    assert "?" not in delivered, \
        "an adversarial LLM question must never reach the user under QUESTION_OVERLOAD"
    assert delivered == controller.fallback_text("ru", Intent.REPAIR)


def test_decision_support_continuation_across_followup_turns(monkeypatch, tmp_db):
    """Continuity must hold for intents beyond VENT/EXPLAIN too (hardening
    §6): an explicit DECISION_SUPPORT phrase followed by a plain follow-up
    with no new signal stays governed by the SAME DECISION_SUPPORT session."""
    llm_calls = {"n": 0}
    _full_pipeline_stub_set(monkeypatch, llm_calls=llm_calls)
    run(_seed_user(1))
    user = FakeUser(1)
    run(bot.pipeline(FakeMessage(user, "Не могу принять решение."),
                     "Не могу принять решение.", None, tg_user=user))
    run(bot.pipeline(FakeMessage(user, "С одной стороны одно, с другой стороны другое."),
                     "С одной стороны одно, с другой стороны другое.", None, tg_user=user))
    assert llm_calls["n"] == 2
    sessions = run(database.list_core_sessions(1))
    assert len(sessions) == 1
    assert sessions[0].intent is Intent.DECISION_SUPPORT
