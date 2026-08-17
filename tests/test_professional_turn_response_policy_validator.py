"""Tests for professional_turn_response_policy_validator (Professional Core V2)."""
from __future__ import annotations

import ast
import inspect

import pytest

import professional_turn_response_policy_validator as policy_mod
from professional_turn_planner import ProfessionalTurnPlan
from professional_turn_response_policy_validator import (
    PolicyRejectionReason,
    PolicyResult,
    PolicyStatus,
    validate_response_policy,
)
from therapeutic_domain import ClarificationTarget, PrimaryResponseMove, ProfessionalObjective


# -- Fixture plans, one per V1-supported move --------------------------------

def _plan(move: PrimaryResponseMove) -> ProfessionalTurnPlan:
    if move is PrimaryResponseMove.OPEN_INVITATION:
        return ProfessionalTurnPlan(
            objective=ProfessionalObjective.ESTABLISH_CONTACT, move=move,
            clarification_target=None, question_allowed=False)
    if move is PrimaryResponseMove.FOCUSED_QUESTION:
        return ProfessionalTurnPlan(
            objective=ProfessionalObjective.CLARIFY, move=move,
            clarification_target=ClarificationTarget.EVENT, question_allowed=True)
    if move is PrimaryResponseMove.REFLECTIVE_STATEMENT:
        return ProfessionalTurnPlan(
            objective=ProfessionalObjective.REPAIR, move=move,
            clarification_target=None, question_allowed=False)
    if move is PrimaryResponseMove.CLOSING:
        return ProfessionalTurnPlan(
            objective=ProfessionalObjective.CLOSE, move=move,
            clarification_target=None, question_allowed=False)
    raise AssertionError(f"no fixture for move {move!r}")


ALL_V1_MOVES = (
    PrimaryResponseMove.OPEN_INVITATION,
    PrimaryResponseMove.FOCUSED_QUESTION,
    PrimaryResponseMove.REFLECTIVE_STATEMENT,
    PrimaryResponseMove.CLOSING,
)


# -- §23 mandatory PASS cases -------------------------------------------------

PASS_CASES = [
    ("What do you need right now?", PrimaryResponseMove.FOCUSED_QUESTION),
    ("Should we continue?", PrimaryResponseMove.FOCUSED_QUESTION),
    ("Что тебе нужно сейчас?", PrimaryResponseMove.FOCUSED_QUESTION),
    ("Что тебе нужно, чтобы стало легче понять ситуацию?", PrimaryResponseMove.FOCUSED_QUESTION),
    ("Ты сказал, что тебе нужно время, чтобы всё обдумать.", PrimaryResponseMove.REFLECTIVE_STATEMENT),
    ("Попробуй, если хочешь, рассказать чуть подробнее.", PrimaryResponseMove.OPEN_INVITATION),
    ("Расскажи мне больше.", PrimaryResponseMove.OPEN_INVITATION),
    ("Напиши, если захочешь продолжить.", PrimaryResponseMove.OPEN_INVITATION),
    ("Tell me more.", PrimaryResponseMove.OPEN_INVITATION),
    ("Continue if you want.", PrimaryResponseMove.OPEN_INVITATION),
    ("You could be feeling overwhelmed.", PrimaryResponseMove.REFLECTIVE_STATEMENT),
    ("You said you breathed slowly for two minutes.", PrimaryResponseMove.REFLECTIVE_STATEMENT),
    ("He breathes slowly when he is anxious.", PrimaryResponseMove.REFLECTIVE_STATEMENT),
    ("Всё будет хорошо.", PrimaryResponseMove.REFLECTIVE_STATEMENT),
    ("Everything will be fine.", PrimaryResponseMove.REFLECTIVE_STATEMENT),
]


@pytest.mark.parametrize("text,move", PASS_CASES)
def test_mandatory_pass_cases(text, move):
    result = validate_response_policy(_plan(move), text)
    assert result.status is PolicyStatus.PASS
    assert result.reason is None


# -- §23 mandatory ACTION/ADVICE REJECT cases --------------------------------

ACTION_ADVICE_REJECT_CASES = [
    "Тебе нужно расстаться с ним.",
    "Тебе нужно расстаться с ним, не так ли?",
    "Тебе стоит написать ему.",
    "Ты должен уйти.",
    "Ты должна уйти.",
    "Советую тебе закончить эти отношения.",
    "Рекомендую тебе прекратить это общение.",
    "Попробуй сегодня написать ему и поставить границу.",
    "Уйди от него.",
    "Расстанься с ним.",
    "Разорви эти отношения.",
    "Прекрати с ним общаться.",
    "Позвони ему.",
    "Напиши ему.",
    "You should leave him.",
    "You should leave him, right?",
    "You need to end this relationship.",
    "You must call her.",
    "I recommend ending the relationship.",
    "Leave him.",
    "End the relationship.",
    "Break up with him.",
    "Cut him off.",
    "Call her.",
    "Message him.",
]


@pytest.mark.parametrize("text", ACTION_ADVICE_REJECT_CASES)
@pytest.mark.parametrize("move", ALL_V1_MOVES)
def test_mandatory_action_advice_reject_cases_all_moves(text, move):
    """No FOCUSED_QUESTION (or any other move) bypass: every one of these
    must REJECT under every V1-supported move, tag-question phrasing
    included."""
    result = validate_response_policy(_plan(move), text)
    assert result.status is PolicyStatus.REJECT
    assert result.reason is PolicyRejectionReason.UNPLANNED_ACTION_OR_ADVICE_CUE


# -- §23 mandatory INTERVENTION REJECT cases ---------------------------------

INTERVENTION_REJECT_CASES = [
    "Сделай дыхательное упражнение на пять минут.",
    "Попробуй технику заземления.",
    "Подыши медленно пару минут.",
    "Подышите медленно одну минуту.",
    "Сосредоточься на дыхании на минуту.",
    "Try a breathing exercise.",
    "Use a grounding technique.",
    "Breathe slowly for a minute.",
    "Please breathe slowly for one minute.",
    "First breathe for two minutes.",
    "Focus on your breathing for one minute.",
    "You could breathe slowly for a minute.",
]


@pytest.mark.parametrize("text", INTERVENTION_REJECT_CASES)
@pytest.mark.parametrize("move", ALL_V1_MOVES)
def test_mandatory_intervention_reject_cases_all_moves(text, move):
    result = validate_response_policy(_plan(move), text)
    assert result.status is PolicyStatus.REJECT
    assert result.reason is PolicyRejectionReason.UNPLANNED_INTERVENTION_OR_EXERCISE_CUE


# -- §12 mandatory "must NOT reject" breathing-reporting cases ---------------

BREATHING_REPORT_PASS_CASES = [
    "Ты говорил, что раньше дышал медленно две минуты.",
    "Ты сказал, что после прогулки подышал и успокоился.",
]


@pytest.mark.parametrize("text", BREATHING_REPORT_PASS_CASES)
def test_breathing_report_morphology_passes(text):
    result = validate_response_policy(_plan(PrimaryResponseMove.REFLECTIVE_STATEMENT), text)
    assert result.status is PolicyStatus.PASS


# -- Owner-review precision-correction regressions: mandatory PASS cases ----
# (embedded reflection/reporting must not be caught by any bounded proxy).

BLOCKER_PASS_REGRESSION_CASES = [
    # Blocker 1: EN embedded action/reflection.
    "You said you want to leave him.",
    "It sounds like you want to end the relationship.",
    "You said you might call her.",
    "I hear that part of you wants to message him.",
    # Blocker 1 (RU extension): reported/quoted direct-imperative context.
    "Он сказал: «Позвони ему».",
    "Ты вспоминаешь его слова: «Уйди от него».",
    # Blocker 2: RU focus reporting (past/reported morphology, not the exact
    # imperative "сосредоточься"/"сосредоточьтесь").
    "Ты сказал, что сосредоточился на дыхании и стало легче.",
    "Она рассказывала, что сосредоточилась на дыхании.",
    "Ты уже сосредоточивался на дыхании раньше.",
    # Blocker 3A: EN focus reporting (gerund "focusing", not exact "focus").
    "You said focusing on your breathing helped.",
    "Focusing on your breathing used to make you uncomfortable.",
    # Blocker 3B: EN breathe reporting (exact "breathe", but embedded, not
    # at a directive position).
    "I notice you breathe slowly when anxious.",
    "You say you breathe slowly when you feel tense.",
    "I notice that you breathe deeply when you relax.",
    # Blocker 4: named-technique discussion is not a proposal.
    "Ты говоришь, что техника заземления тебе не помогла.",
    "Ты сказал, что дыхательное упражнение было неприятным.",
    "Раньше техника заземления только раздражала тебя.",
    "You said the grounding technique did not help.",
    "You said the breathing exercise made you uncomfortable.",
    "The grounding exercise was something you tried before.",
]


@pytest.mark.parametrize("text", BLOCKER_PASS_REGRESSION_CASES)
@pytest.mark.parametrize("move", ALL_V1_MOVES)
def test_blocker_pass_regressions_all_moves(text, move):
    result = validate_response_policy(_plan(move), text)
    assert result.status is PolicyStatus.PASS, f"{text!r} unexpectedly REJECTed: {result.reason}"
    assert result.reason is None


# -- Owner-review precision-correction regressions: directive REJECT ---------
# controls (proving the precision correction did not destroy recall).

DIRECTIVE_CONTROL_REJECT_CASES = [
    ("I hear you. Leave him.", PolicyRejectionReason.UNPLANNED_ACTION_OR_ADVICE_CUE),
    ("That sounds painful. Call her.", PolicyRejectionReason.UNPLANNED_ACTION_OR_ADVICE_CUE),
    ("Слышу тебя. Позвони ему.", PolicyRejectionReason.UNPLANNED_ACTION_OR_ADVICE_CUE),
    ("Сосредоточься на дыхании на минуту.",
     PolicyRejectionReason.UNPLANNED_INTERVENTION_OR_EXERCISE_CUE),
    ("Сосредоточьтесь на дыхании на минуту.",
     PolicyRejectionReason.UNPLANNED_INTERVENTION_OR_EXERCISE_CUE),
    ("Focus on your breathing for one minute.",
     PolicyRejectionReason.UNPLANNED_INTERVENTION_OR_EXERCISE_CUE),
    ("Please focus on your breathing for one minute.",
     PolicyRejectionReason.UNPLANNED_INTERVENTION_OR_EXERCISE_CUE),
    ("Breathe slowly for a minute.",
     PolicyRejectionReason.UNPLANNED_INTERVENTION_OR_EXERCISE_CUE),
    ("Please breathe slowly for one minute.",
     PolicyRejectionReason.UNPLANNED_INTERVENTION_OR_EXERCISE_CUE),
    ("First breathe for two minutes.",
     PolicyRejectionReason.UNPLANNED_INTERVENTION_OR_EXERCISE_CUE),
    ("No rush. Breathe slowly for a minute.",
     PolicyRejectionReason.UNPLANNED_INTERVENTION_OR_EXERCISE_CUE),
    ("You could breathe slowly for a minute.",
     PolicyRejectionReason.UNPLANNED_INTERVENTION_OR_EXERCISE_CUE),
    ("No rush — you could breathe slowly, write the situation down, and then "
     "message someone you trust.",
     PolicyRejectionReason.UNPLANNED_INTERVENTION_OR_EXERCISE_CUE),
    ("Попробуй технику заземления.",
     PolicyRejectionReason.UNPLANNED_INTERVENTION_OR_EXERCISE_CUE),
    ("Сделай дыхательное упражнение на пять минут.",
     PolicyRejectionReason.UNPLANNED_INTERVENTION_OR_EXERCISE_CUE),
    ("Попробуйте упражнение на заземление.",
     PolicyRejectionReason.UNPLANNED_INTERVENTION_OR_EXERCISE_CUE),
    ("Try a breathing exercise.",
     PolicyRejectionReason.UNPLANNED_INTERVENTION_OR_EXERCISE_CUE),
    ("Use a grounding technique.",
     PolicyRejectionReason.UNPLANNED_INTERVENTION_OR_EXERCISE_CUE),
    ("Do a grounding exercise.",
     PolicyRejectionReason.UNPLANNED_INTERVENTION_OR_EXERCISE_CUE),
]


@pytest.mark.parametrize("text,expected_reason", DIRECTIVE_CONTROL_REJECT_CASES)
@pytest.mark.parametrize("move", ALL_V1_MOVES)
def test_directive_control_reject_cases_all_moves(text, expected_reason, move):
    result = validate_response_policy(_plan(move), text)
    assert result.status is PolicyStatus.REJECT
    assert result.reason is expected_reason


# -- Second precision-correction round: mandatory PASS regressions ----------
# (defect A: embedded "you could breathe...";
#  defect B: reported/quoted named-technique and breathing-imperative
#  proposals).

ROUND_2_PASS_REGRESSION_CASES = [
    # Defect A: EN reported "you could breathe".
    "You said you could breathe slowly when you felt overwhelmed.",
    "You mentioned that you could breathe deeply when it got difficult.",
    "You told me you could breathe slowly for a minute when anxiety rose.",
    # Defect A control: an em dash alone must not become a generic
    # suggestion boundary.
    "You said — you could breathe slowly.",
    # Defect B: EN reported named-technique suggestion.
    "You said you could try a breathing exercise.",
    "You remember that your therapist told you to use a grounding technique.",
    "He suggested that you try a breathing exercise.",
    "You said you tried a breathing exercise before.",
    # Defect B: RU reported named-technique proposal.
    "Ты сказал, что мог попробовать технику заземления.",
    "Ты вспоминаешь, как психолог сказал: «Попробуй технику заземления».",
    "Он советовал тебе: «Сделай дыхательное упражнение».",
    # Defect B: RU reported breathing/focus imperative.
    "Он сказал: «Подыши минуту».",
    "Ты вспоминаешь его совет: «Сосредоточься на дыхании».",
    "Психолог говорил: «Вдохни медленно и выдохни».",
]


@pytest.mark.parametrize("text", ROUND_2_PASS_REGRESSION_CASES)
@pytest.mark.parametrize("move", ALL_V1_MOVES)
def test_round_2_pass_regressions_all_moves(text, move):
    result = validate_response_policy(_plan(move), text)
    assert result.status is PolicyStatus.PASS, f"{text!r} unexpectedly REJECTed: {result.reason}"
    assert result.reason is None


# -- Second precision-correction round: mandatory REJECT controls -----------
# (proving the round-2 fix did not destroy recall).

ROUND_2_REJECT_CONTROL_CASES = [
    "You could breathe slowly for a minute.",
    "You could breathe deeply for one minute.",
    "No rush. You could breathe slowly for a minute.",
    "No rush — you could breathe slowly, write the situation down, and then "
    "message someone you trust.",
    "Try a breathing exercise.",
    "Use a grounding technique.",
    "Do a grounding exercise.",
    "Please try a breathing exercise.",
    "First try a grounding technique.",
    "You could try a breathing exercise.",
    "No rush. You could try a grounding technique.",
    "Попробуй технику заземления.",
    "Попробуйте упражнение на заземление.",
    "Сделай дыхательное упражнение на пять минут.",
    "Используй технику заземления.",
    "Примени технику заземления.",
    "Подыши медленно пару минут.",
    "Подышите медленно одну минуту.",
    "Сосредоточься на дыхании на минуту.",
    "Сосредоточьтесь на дыхании на минуту.",
    "Сначала подыши две минуты, потом запиши мысли и напиши другу.",
]


@pytest.mark.parametrize("text", ROUND_2_REJECT_CONTROL_CASES)
@pytest.mark.parametrize("move", ALL_V1_MOVES)
def test_round_2_reject_controls_all_moves(text, move):
    result = validate_response_policy(_plan(move), text)
    assert result.status is PolicyStatus.REJECT
    assert result.reason is PolicyRejectionReason.UNPLANNED_INTERVENTION_OR_EXERCISE_CUE


# -- §13 mandatory multi-step REJECT cases -----------------------------------

MULTI_STEP_CASES = [
    "Сначала подыши две минуты, потом запиши мысли и напиши другу.",
    "First breathe for two minutes, then write down your thoughts and call a friend.",
    "No rush — you could breathe slowly, write the situation down, and then message someone you trust.",
]


@pytest.mark.parametrize("text", MULTI_STEP_CASES)
def test_multi_step_examples_caught_by_intervention_family(text):
    """No third MULTI_STEP reason exists; all three worked examples are
    caught by the existing intervention/exercise family alone."""
    result = validate_response_policy(_plan(PrimaryResponseMove.REFLECTIVE_STATEMENT), text)
    assert result.status is PolicyStatus.REJECT
    assert result.reason is PolicyRejectionReason.UNPLANNED_INTERVENTION_OR_EXERCISE_CUE


# -- §24 move matrix: clean / action / intervention under every move --------

@pytest.mark.parametrize("move", ALL_V1_MOVES)
def test_move_matrix_clean_candidate_passes(move):
    result = validate_response_policy(_plan(move), "Слышу тебя, это правда тяжело.")
    assert result.status is PolicyStatus.PASS


@pytest.mark.parametrize("move", ALL_V1_MOVES)
def test_move_matrix_action_advice_candidate_rejects(move):
    result = validate_response_policy(_plan(move), "You should leave him.")
    assert result.status is PolicyStatus.REJECT
    assert result.reason is PolicyRejectionReason.UNPLANNED_ACTION_OR_ADVICE_CUE


@pytest.mark.parametrize("move", ALL_V1_MOVES)
def test_move_matrix_intervention_candidate_rejects(move):
    result = validate_response_policy(_plan(move), "Подыши медленно пару минут.")
    assert result.status is PolicyStatus.REJECT
    assert result.reason is PolicyRejectionReason.UNPLANNED_INTERVENTION_OR_EXERCISE_CUE


# -- §25 precedence: intervention wins on a genuine dual match --------------

def test_precedence_intervention_wins_over_action_advice_when_both_match():
    # Both "тебе нужно" + "позвонить" (action/advice, not directive-position
    # gated) and the exact imperative breathing word "подыши" (intervention,
    # directive-position gated -- placed at the start of its own sentence
    # here, after ". ") match this text.
    text = "Тебе нужно позвонить. Подыши минуту."
    result = validate_response_policy(
        _plan(PrimaryResponseMove.REFLECTIVE_STATEMENT), text)
    assert result.status is PolicyStatus.REJECT
    assert result.reason is PolicyRejectionReason.UNPLANNED_INTERVENTION_OR_EXERCISE_CUE


def test_precedence_owner_example_reports_intervention():
    # Under the final bounded action/advice patterns, "Сделай..." alone is
    # not a matched trigger (bare imperatives outside the closed
    # direct-imperative set are a documented false negative -- see the
    # production module's docstring), so this example is in practice caught
    # by the intervention family alone; the precedence order is still
    # exercised and frozen here regardless.
    text = "Сделай дыхательное упражнение на пять минут."
    result = validate_response_policy(
        _plan(PrimaryResponseMove.OPEN_INVITATION), text)
    assert result.status is PolicyStatus.REJECT
    assert result.reason is PolicyRejectionReason.UNPLANNED_INTERVENTION_OR_EXERCISE_CUE


# -- §10 documented, locked, known residual false-positive -------------------

def test_known_residual_false_positive_reflection_of_users_own_action_intent():
    """Documented limitation, not a bug: this module never receives
    source_text and cannot distinguish the bot asserting advice from the bot
    echoing the user's own already-stated words. Locked here so the
    limitation stays visible instead of silently drifting."""
    text = "Ты сказал, что тебе нужно ему позвонить."
    result = validate_response_policy(
        _plan(PrimaryResponseMove.REFLECTIVE_STATEMENT), text)
    assert result.status is PolicyStatus.REJECT
    assert result.reason is PolicyRejectionReason.UNPLANNED_ACTION_OR_ADVICE_CUE


# -- §14/§9 non-goals: unsupported outcome reassurance passes unchanged -----

@pytest.mark.parametrize("text", ["Всё будет хорошо.", "Everything will be fine."])
def test_unsupported_outcome_reassurance_is_not_a_policy_concern(text):
    result = validate_response_policy(
        _plan(PrimaryResponseMove.REFLECTIVE_STATEMENT), text)
    assert result.status is PolicyStatus.PASS


# -- Caller-defect / invariant tests -----------------------------------------

def test_wrong_plan_type_raises_value_error():
    with pytest.raises(ValueError):
        validate_response_policy("not a plan", "Some text.")


def test_wrong_candidate_text_type_raises_value_error():
    with pytest.raises(ValueError):
        validate_response_policy(_plan(PrimaryResponseMove.CLOSING), 123)


def test_empty_candidate_text_raises_value_error():
    with pytest.raises(ValueError):
        validate_response_policy(_plan(PrimaryResponseMove.CLOSING), "")


def test_whitespace_only_candidate_text_raises_value_error():
    with pytest.raises(ValueError):
        validate_response_policy(_plan(PrimaryResponseMove.CLOSING), "   \n\t  ")


def test_unsupported_future_move_fails_closed():
    plan = _plan(PrimaryResponseMove.CLOSING)
    object.__setattr__(plan, "move", PrimaryResponseMove.STRUCTURED_SUMMARY)
    with pytest.raises(ValueError):
        validate_response_policy(plan, "Some text.")


def test_policy_result_rejects_raw_string_status():
    with pytest.raises(ValueError):
        PolicyResult(status="REJECT", reason=PolicyRejectionReason.UNPLANNED_ACTION_OR_ADVICE_CUE)


def test_policy_result_rejects_raw_string_reason():
    with pytest.raises(ValueError):
        PolicyResult(status=PolicyStatus.REJECT, reason="UNPLANNED_ACTION_OR_ADVICE_CUE")


def test_policy_result_pass_requires_none_reason():
    with pytest.raises(ValueError):
        PolicyResult(status=PolicyStatus.PASS, reason=PolicyRejectionReason.UNPLANNED_ACTION_OR_ADVICE_CUE)


def test_policy_result_reject_requires_reason():
    with pytest.raises(ValueError):
        PolicyResult(status=PolicyStatus.REJECT, reason=None)


def test_candidate_length_is_not_revalidated_here():
    """Policy V1 does not own candidate length -- an arbitrarily long benign
    string is evaluated on content alone, never rejected for length."""
    text = ("Слышу тебя. " * 500).strip()
    result = validate_response_policy(_plan(PrimaryResponseMove.REFLECTIVE_STATEMENT), text)
    assert result.status is PolicyStatus.PASS


# -- Public signature lock ----------------------------------------------------

def test_validate_response_policy_public_signature_is_exact():
    sig = inspect.signature(validate_response_policy)
    params = list(sig.parameters.values())
    assert [p.name for p in params] == ["plan", "candidate_text"]
    for p in params:
        assert p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert p.default is inspect.Parameter.empty
    assert sig.return_annotation is not inspect.Signature.empty


# -- Static architecture: exact frozen import surface ------------------------

_EXPECTED_IMPORTS = frozenset({
    ("future", "__future__", "annotations", None, 0),
    ("import", "re", None, None, 0),
    ("from", "dataclasses", "dataclass", None, 0),
    ("from", "enum", "Enum", None, 0),
    ("from", "professional_turn_planner", "ProfessionalTurnPlan", None, 0),
    ("from", "therapeutic_domain", "PrimaryResponseMove", None, 0),
})


def _module_tree():
    source = inspect.getsource(policy_mod)
    return ast.parse(source)


def test_production_module_has_exact_frozen_import_surface():
    tree = _module_tree()
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(("import", alias.name, None, alias.asname, 0))
        elif isinstance(node, ast.ImportFrom):
            kind = "future" if node.module == "__future__" else "from"
            for alias in node.names:
                found.add((kind, node.module, alias.name, alias.asname, node.level or 0))
    assert found == _EXPECTED_IMPORTS


_FORBIDDEN_CALLABLE_NAMES = frozenset({
    "validate_response", "validate_response_with_context", "select_fallback",
    "get_fallback", "get_safe_fallback_high_risk", "validate_response_fidelity",
    "render_turn_response", "govern_turn_plan", "call_turn_plan_proposer",
    "call_turn_analyzer", "produce_turn_analysis", "traced_response_builder",
    "persist_influence_trace", "classify", "execute", "commit", "create",
    "eval", "exec", "open", "__import__",
})


def test_production_module_contains_no_forbidden_call_sites():
    tree = _module_tree()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)
        assert name not in _FORBIDDEN_CALLABLE_NAMES, f"forbidden call site: {name}"


def test_production_module_has_no_async_defs():
    tree = _module_tree()
    assert not any(isinstance(node, ast.AsyncFunctionDef) for node in ast.walk(tree))


def test_module_docstring_states_non_goals_and_known_limitations():
    doc = (policy_mod.__doc__ or "").lower()
    for phrase in (
        "no semantic comprehension",
        "does not prove",
        "residual",
        "known",
    ):
        assert phrase in doc, f"expected docstring to mention: {phrase!r}"
