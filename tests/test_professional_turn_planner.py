"""Behavioral tests for Stage 2B Planner Governor V1 (professional_turn_planner.py):
transport structure, trusted-plan local invariants, and the full deterministic
govern_turn_plan algorithm (eligibility, semantic normalization, the sealed
Stage 1 objective/move matrix, V1 narrowing, clarification-target presence,
and the precise question-capability gate).
"""
import ast
import pathlib

import pytest
from dataclasses import fields

import professional_turn_planner
from professional_turn_analysis import (
    AnalysisComponentStatus,
    EvidenceAnalysis,
    IntentAnalysis,
    InteractionAnalysis,
    InteractionApplicability,
    InteractionOccurrenceState,
    InteractionSignalOccurrence,
    TurnAnalysis,
    TurnAnalysisResult,
    TurnAnalysisStatus,
)
from professional_turn_planner import (
    ProfessionalPlanAbstentionReason,
    ProfessionalTurnPlan,
    ProfessionalTurnPlanResult,
    UntrustedTurnPlanProposal,
    govern_turn_plan,
)
from therapeutic_domain import (
    ClarificationTarget,
    Intent,
    InteractionRequest,
    InteractionSignal,
    PrimaryResponseMove,
    ProfessionalObjective,
)


# ── Fixtures / helpers ───────────────────────────────────────────────────────

def _interaction_analysis(status, signals=(), row_id=1, base_text="User turn text."):
    """Build a real, invariant-satisfying InteractionAnalysis (and its matching
    source_text) -- never fakes request.signals independently of occurrences."""
    if status is AnalysisComponentStatus.UNAVAILABLE:
        assert not signals, "UNAVAILABLE interaction cannot carry signals"
        return InteractionAnalysis(status=status), base_text
    text = base_text
    occurrences = []
    for signal in signals:
        tag = f" [{signal.value}]"
        start = len(text)
        text += tag
        end = len(text)
        occurrences.append(InteractionSignalOccurrence(
            source_message_row_id=row_id,
            signal=signal,
            span_start=start,
            span_end=end,
            exact_source_span=text[start:end],
            applicability=InteractionApplicability.CURRENT_DIRECTIVE,
            state=InteractionOccurrenceState.ACTIVE))
    request = InteractionRequest(signals=frozenset(signals))
    interaction = InteractionAnalysis(
        status=status, request=request, occurrences=tuple(occurrences))
    return interaction, text


def _turn_analysis_result(
        *,
        intent=Intent.UNKNOWN,
        intent_status=AnalysisComponentStatus.VALIDATED,
        interaction_status=AnalysisComponentStatus.VALIDATED,
        interaction_signals=(),
        evidence_status=AnalysisComponentStatus.VALIDATED,
        row_id=1):
    interaction, text = _interaction_analysis(interaction_status, interaction_signals, row_id=row_id)
    evidence = EvidenceAnalysis(status=evidence_status)
    resolved_intent = intent if intent_status is AnalysisComponentStatus.VALIDATED else Intent.UNKNOWN
    intent_analysis = IntentAnalysis(status=intent_status, analyzer_intent=resolved_intent)
    analysis = TurnAnalysis(
        source_message_row_id=row_id,
        source_text=text,
        evidence=evidence,
        interaction=interaction,
        intent=intent_analysis)
    return TurnAnalysisResult(analysis=analysis)


# Independent mirror of the frozen V1 Intent -> allowed-objective matrix
# (CLAUDE.md §8 of the frozen contract) -- written here separately from the
# production module so this test actually LOCKS the matrix instead of just
# re-checking the production table against itself.
_EXPECTED_INTENT_ALLOWED = {
    Intent.VENT: {ProfessionalObjective.ESTABLISH_CONTACT, ProfessionalObjective.CLARIFY},
    Intent.EXPLAIN: {ProfessionalObjective.CLARIFY},
    Intent.ACTION: {ProfessionalObjective.CLARIFY, ProfessionalObjective.CLARIFY_GOAL},
    Intent.CHANGE_PATTERN: {ProfessionalObjective.CLARIFY, ProfessionalObjective.CLARIFY_GOAL},
    Intent.DECISION_SUPPORT: {ProfessionalObjective.CLARIFY, ProfessionalObjective.CLARIFY_GOAL},
    Intent.PRACTICE: {ProfessionalObjective.CLARIFY, ProfessionalObjective.CLARIFY_GOAL},
    Intent.REPAIR: {ProfessionalObjective.REPAIR},
    Intent.PROBLEM_SOLVING: {ProfessionalObjective.CLARIFY, ProfessionalObjective.CLARIFY_GOAL},
    Intent.RELATIONSHIP_SUPPORT: {
        ProfessionalObjective.ESTABLISH_CONTACT, ProfessionalObjective.CLARIFY,
        ProfessionalObjective.CLARIFY_GOAL},
    Intent.JOURNAL_WORK: {ProfessionalObjective.CLARIFY, ProfessionalObjective.CLARIFY_GOAL},
    Intent.QUESTIONNAIRE_DISCUSSION: {ProfessionalObjective.CLARIFY, ProfessionalObjective.CLARIFY_GOAL},
    Intent.CONTINUE_PREVIOUS_WORK: {ProfessionalObjective.CLARIFY},
    Intent.CLOSE_CONVERSATION: {ProfessionalObjective.CLOSE},
    Intent.UNKNOWN: {
        ProfessionalObjective.ESTABLISH_CONTACT, ProfessionalObjective.CLARIFY,
        ProfessionalObjective.CLARIFY_GOAL},
}

_CANONICAL_OBJECTIVE_MOVE = {
    ProfessionalObjective.ESTABLISH_CONTACT: (PrimaryResponseMove.OPEN_INVITATION, None),
    ProfessionalObjective.CLARIFY: (PrimaryResponseMove.FOCUSED_QUESTION, ClarificationTarget.EVENT),
    ProfessionalObjective.CLARIFY_GOAL: (PrimaryResponseMove.FOCUSED_QUESTION, None),
    ProfessionalObjective.REPAIR: (PrimaryResponseMove.REFLECTIVE_STATEMENT, None),
    ProfessionalObjective.CLOSE: (PrimaryResponseMove.CLOSING, None),
}

_OBJECTIVE_TEST_INTENT = {
    ProfessionalObjective.ESTABLISH_CONTACT: Intent.UNKNOWN,
    ProfessionalObjective.CLARIFY: Intent.UNKNOWN,
    ProfessionalObjective.CLARIFY_GOAL: Intent.UNKNOWN,
    ProfessionalObjective.REPAIR: Intent.REPAIR,
    ProfessionalObjective.CLOSE: Intent.CLOSE_CONVERSATION,
}

_UNSUPPORTED_OBJECTIVES = (
    ProfessionalObjective.MAP_EPISODE,
    ProfessionalObjective.TEST_HYPOTHESIS,
    ProfessionalObjective.CHECK_FORMULATION,
    ProfessionalObjective.EXPLAIN_MECHANISM,
    ProfessionalObjective.OFFER_ACTION,
    ProfessionalObjective.REVIEW_OUTCOME,
)


# ── Transport structure (UntrustedTurnPlanProposal) ─────────────────────────

def test_transport_accepts_real_enum_values():
    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.CLARIFY, move=PrimaryResponseMove.FOCUSED_QUESTION,
        clarification_target=ClarificationTarget.EVENT)
    assert proposal.objective is ProfessionalObjective.CLARIFY
    assert proposal.move is PrimaryResponseMove.FOCUSED_QUESTION
    assert proposal.clarification_target is ClarificationTarget.EVENT


def test_transport_accepts_raw_strings_unchanged():
    proposal = UntrustedTurnPlanProposal(
        objective="CLARIFY", move="FOCUSED_QUESTION", clarification_target="EVENT")
    assert proposal.objective == "CLARIFY" and type(proposal.objective) is str
    assert proposal.move == "FOCUSED_QUESTION" and type(proposal.move) is str
    assert proposal.clarification_target == "EVENT"


def test_transport_survives_unknown_semantic_strings_unchanged():
    proposal = UntrustedTurnPlanProposal(
        objective="NOT_A_REAL_OBJECTIVE", move="NOT_A_REAL_MOVE",
        clarification_target="NOT_A_REAL_TARGET")
    assert proposal.objective == "NOT_A_REAL_OBJECTIVE"
    assert proposal.move == "NOT_A_REAL_MOVE"
    assert proposal.clarification_target == "NOT_A_REAL_TARGET"


def test_transport_rejects_objective_none():
    with pytest.raises(ValueError):
        UntrustedTurnPlanProposal(
            objective=None, move=PrimaryResponseMove.OPEN_INVITATION, clarification_target=None)


def test_transport_rejects_move_none():
    with pytest.raises(ValueError):
        UntrustedTurnPlanProposal(
            objective=ProfessionalObjective.CLOSE, move=None, clarification_target=None)


@pytest.mark.parametrize("bad", [True, False, 1, 1.5, [], (), {}, object()])
def test_transport_rejects_wrong_types_for_objective(bad):
    with pytest.raises(ValueError):
        UntrustedTurnPlanProposal(
            objective=bad, move=PrimaryResponseMove.OPEN_INVITATION, clarification_target=None)


@pytest.mark.parametrize("bad", [True, False, 1, 1.5, [], (), {}, object()])
def test_transport_rejects_wrong_types_for_move(bad):
    with pytest.raises(ValueError):
        UntrustedTurnPlanProposal(
            objective=ProfessionalObjective.CLOSE, move=bad, clarification_target=None)


@pytest.mark.parametrize("bad", [True, False, 1, 1.5, [], (), {}, object()])
def test_transport_rejects_wrong_types_for_clarification_target(bad):
    with pytest.raises(ValueError):
        UntrustedTurnPlanProposal(
            objective=ProfessionalObjective.CLARIFY, move=PrimaryResponseMove.FOCUSED_QUESTION,
            clarification_target=bad)


def test_transport_objective_and_move_are_required():
    with pytest.raises(TypeError):
        UntrustedTurnPlanProposal(move=PrimaryResponseMove.OPEN_INVITATION, clarification_target=None)
    with pytest.raises(TypeError):
        UntrustedTurnPlanProposal(objective=ProfessionalObjective.CLOSE, clarification_target=None)


# ── Public governor argument type boundary ──────────────────────────────────

def test_governor_rejects_wrong_analysis_result_type():
    with pytest.raises(ValueError):
        govern_turn_plan("not a result", None)


def test_governor_rejects_wrong_proposal_outer_type():
    result_analysis = _turn_analysis_result(intent=Intent.UNKNOWN)
    with pytest.raises(ValueError):
        govern_turn_plan(result_analysis, "not a proposal")


# ── Result invariants ────────────────────────────────────────────────────────

def _sample_plan():
    return ProfessionalTurnPlan(
        objective=ProfessionalObjective.CLOSE, move=PrimaryResponseMove.CLOSING,
        clarification_target=None, question_allowed=False)


def test_result_accepts_plan_only():
    plan = _sample_plan()
    result = ProfessionalTurnPlanResult(plan=plan, abstention_reason=None)
    assert result.plan is plan and result.abstention_reason is None


def test_result_accepts_reason_only():
    result = ProfessionalTurnPlanResult(
        plan=None, abstention_reason=ProfessionalPlanAbstentionReason.NO_SEMANTIC_PROPOSAL)
    assert result.abstention_reason is ProfessionalPlanAbstentionReason.NO_SEMANTIC_PROPOSAL


def test_result_rejects_both_none():
    with pytest.raises(ValueError):
        ProfessionalTurnPlanResult(plan=None, abstention_reason=None)


def test_result_rejects_both_set():
    with pytest.raises(ValueError):
        ProfessionalTurnPlanResult(
            plan=_sample_plan(),
            abstention_reason=ProfessionalPlanAbstentionReason.NO_SEMANTIC_PROPOSAL)


def test_result_rejects_wrong_plan_type():
    with pytest.raises(ValueError):
        ProfessionalTurnPlanResult(plan="not a plan", abstention_reason=None)


def test_result_rejects_wrong_abstention_reason_type():
    with pytest.raises(ValueError):
        ProfessionalTurnPlanResult(plan=None, abstention_reason="NOT_A_REAL_REASON")


# ── Trusted plan local invariants ───────────────────────────────────────────

def test_trusted_plan_normalizes_raw_canonical_strings():
    plan = ProfessionalTurnPlan(
        objective="CLOSE", move="CLOSING", clarification_target=None, question_allowed=False)
    assert plan.objective is ProfessionalObjective.CLOSE
    assert plan.move is PrimaryResponseMove.CLOSING


def test_trusted_plan_rejects_unsupported_objective():
    with pytest.raises(ValueError):
        ProfessionalTurnPlan(
            objective=ProfessionalObjective.OFFER_ACTION, move=PrimaryResponseMove.ACTION_PROPOSAL,
            clarification_target=None, question_allowed=False)


def test_trusted_plan_rejects_incompatible_objective_move():
    with pytest.raises(ValueError):
        ProfessionalTurnPlan(
            objective=ProfessionalObjective.CLARIFY, move=PrimaryResponseMove.REFLECTIVE_STATEMENT,
            clarification_target=ClarificationTarget.EVENT, question_allowed=True)


def test_trusted_plan_rejects_repair_structured_summary():
    with pytest.raises(ValueError):
        ProfessionalTurnPlan(
            objective=ProfessionalObjective.REPAIR, move=PrimaryResponseMove.STRUCTURED_SUMMARY,
            clarification_target=None, question_allowed=False)


def test_trusted_plan_rejects_clarify_without_target():
    with pytest.raises(ValueError):
        ProfessionalTurnPlan(
            objective=ProfessionalObjective.CLARIFY, move=PrimaryResponseMove.FOCUSED_QUESTION,
            clarification_target=None, question_allowed=True)


def test_trusted_plan_rejects_clarify_goal_with_target():
    with pytest.raises(ValueError):
        ProfessionalTurnPlan(
            objective=ProfessionalObjective.CLARIFY_GOAL, move=PrimaryResponseMove.FOCUSED_QUESTION,
            clarification_target=ClarificationTarget.EVENT, question_allowed=True)


@pytest.mark.parametrize("objective,move", [
    (ProfessionalObjective.ESTABLISH_CONTACT, PrimaryResponseMove.OPEN_INVITATION),
    (ProfessionalObjective.REPAIR, PrimaryResponseMove.REFLECTIVE_STATEMENT),
    (ProfessionalObjective.CLOSE, PrimaryResponseMove.CLOSING),
])
def test_trusted_plan_rejects_target_for_non_clarify_objectives(objective, move):
    with pytest.raises(ValueError):
        ProfessionalTurnPlan(
            objective=objective, move=move, clarification_target=ClarificationTarget.EVENT,
            question_allowed=False)


@pytest.mark.parametrize("bad", [0, 1, None, "True", "false"])
def test_trusted_plan_rejects_non_bool_question_allowed(bad):
    with pytest.raises(ValueError):
        ProfessionalTurnPlan(
            objective=ProfessionalObjective.CLOSE, move=PrimaryResponseMove.CLOSING,
            clarification_target=None, question_allowed=bad)


def test_trusted_plan_rejects_focused_question_with_question_allowed_false():
    with pytest.raises(ValueError):
        ProfessionalTurnPlan(
            objective=ProfessionalObjective.CLARIFY, move=PrimaryResponseMove.FOCUSED_QUESTION,
            clarification_target=ClarificationTarget.EVENT, question_allowed=False)


def test_trusted_plan_non_question_move_with_question_allowed_false_is_valid():
    plan = ProfessionalTurnPlan(
        objective=ProfessionalObjective.CLOSE, move=PrimaryResponseMove.CLOSING,
        clarification_target=None, question_allowed=False)
    assert plan.question_allowed is False


# ── Upstream failure ─────────────────────────────────────────────────────────

def test_upstream_analysis_failed_when_analysis_is_none():
    result_analysis = TurnAnalysisResult(analysis=None)
    assert result_analysis.status is TurnAnalysisStatus.FAILED
    result = govern_turn_plan(result_analysis, None)
    assert result.abstention_reason is ProfessionalPlanAbstentionReason.UPSTREAM_ANALYSIS_FAILED


def test_upstream_failure_wins_before_proposal_semantic_processing():
    result_analysis = TurnAnalysisResult(analysis=None)
    proposal = UntrustedTurnPlanProposal(
        objective="NOT_A_REAL_OBJECTIVE", move="NOT_A_REAL_MOVE", clarification_target=None)
    result = govern_turn_plan(result_analysis, proposal)
    assert result.abstention_reason is ProfessionalPlanAbstentionReason.UPSTREAM_ANALYSIS_FAILED


# ── Eligible-set precedence ──────────────────────────────────────────────────

def test_explain_with_degraded_interaction_has_no_eligible_objective():
    result_analysis = _turn_analysis_result(
        intent=Intent.EXPLAIN, interaction_status=AnalysisComponentStatus.DEGRADED)
    result = govern_turn_plan(result_analysis, None)
    assert result.abstention_reason is ProfessionalPlanAbstentionReason.NO_ELIGIBLE_OBJECTIVE_REMAINS


def test_explain_with_unavailable_interaction_has_no_eligible_objective():
    result_analysis = _turn_analysis_result(
        intent=Intent.EXPLAIN, interaction_status=AnalysisComponentStatus.UNAVAILABLE)
    result = govern_turn_plan(result_analysis, None)
    assert result.abstention_reason is ProfessionalPlanAbstentionReason.NO_ELIGIBLE_OBJECTIVE_REMAINS
    # Precedence: this must be the eligibility abstention, not proposer abstention,
    # even though proposal is None in both cases.
    assert result.abstention_reason is not ProfessionalPlanAbstentionReason.NO_SEMANTIC_PROPOSAL


def test_explain_with_validated_no_questions_has_no_eligible_objective():
    result_analysis = _turn_analysis_result(
        intent=Intent.EXPLAIN,
        interaction_status=AnalysisComponentStatus.VALIDATED,
        interaction_signals=(InteractionSignal.NO_QUESTIONS,))
    result = govern_turn_plan(result_analysis, None)
    assert result.abstention_reason is ProfessionalPlanAbstentionReason.NO_ELIGIBLE_OBJECTIVE_REMAINS


def test_action_with_non_validated_interaction_has_no_eligible_objective():
    result_analysis = _turn_analysis_result(
        intent=Intent.ACTION, interaction_status=AnalysisComponentStatus.DEGRADED)
    result = govern_turn_plan(result_analysis, None)
    assert result.abstention_reason is ProfessionalPlanAbstentionReason.NO_ELIGIBLE_OBJECTIVE_REMAINS


# ── Proposer abstention ──────────────────────────────────────────────────────

def test_proposal_none_with_eligible_objective_abstains_no_semantic_proposal():
    result_analysis = _turn_analysis_result(intent=Intent.UNKNOWN)
    result = govern_turn_plan(result_analysis, None)
    assert result.abstention_reason is ProfessionalPlanAbstentionReason.NO_SEMANTIC_PROPOSAL


# ── Semantic enum rejection ──────────────────────────────────────────────────

def test_unknown_objective_string_is_semantic_value_invalid():
    result_analysis = _turn_analysis_result(intent=Intent.UNKNOWN)
    proposal = UntrustedTurnPlanProposal(
        objective="NOT_A_REAL_OBJECTIVE", move=PrimaryResponseMove.OPEN_INVITATION,
        clarification_target=None)
    result = govern_turn_plan(result_analysis, proposal)
    assert result.abstention_reason is ProfessionalPlanAbstentionReason.PROPOSAL_SEMANTIC_VALUE_INVALID


def test_unknown_move_string_is_semantic_value_invalid():
    result_analysis = _turn_analysis_result(intent=Intent.UNKNOWN)
    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.ESTABLISH_CONTACT, move="NOT_A_REAL_MOVE",
        clarification_target=None)
    result = govern_turn_plan(result_analysis, proposal)
    assert result.abstention_reason is ProfessionalPlanAbstentionReason.PROPOSAL_SEMANTIC_VALUE_INVALID


def test_unknown_clarification_target_string_is_semantic_value_invalid():
    result_analysis = _turn_analysis_result(intent=Intent.UNKNOWN)
    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.CLARIFY, move=PrimaryResponseMove.FOCUSED_QUESTION,
        clarification_target="NOT_A_REAL_TARGET")
    result = govern_turn_plan(result_analysis, proposal)
    assert result.abstention_reason is ProfessionalPlanAbstentionReason.PROPOSAL_SEMANTIC_VALUE_INVALID


def test_semantic_normalization_does_not_repair_case_or_alias():
    result_analysis = _turn_analysis_result(intent=Intent.UNKNOWN)
    proposal = UntrustedTurnPlanProposal(
        objective="clarify", move=PrimaryResponseMove.FOCUSED_QUESTION,
        clarification_target=ClarificationTarget.EVENT)
    result = govern_turn_plan(result_analysis, proposal)
    assert result.abstention_reason is ProfessionalPlanAbstentionReason.PROPOSAL_SEMANTIC_VALUE_INVALID


# ── Supported / unsupported objectives ──────────────────────────────────────

@pytest.mark.parametrize("objective", _UNSUPPORTED_OBJECTIVES)
def test_each_unsupported_objective_rejected_in_v1(objective):
    result_analysis = _turn_analysis_result(intent=Intent.UNKNOWN)
    proposal = UntrustedTurnPlanProposal(
        objective=objective, move=PrimaryResponseMove.EXPLANATION, clarification_target=None)
    result = govern_turn_plan(result_analysis, proposal)
    assert result.abstention_reason is ProfessionalPlanAbstentionReason.OBJECTIVE_UNSUPPORTED_IN_V1


# ── Intent -> objective matrix (exhaustive lock) ────────────────────────────

def test_intent_objective_matrix_is_locked_exactly():
    for intent in Intent:
        allowed = _EXPECTED_INTENT_ALLOWED[intent]
        for objective, (move, target) in _CANONICAL_OBJECTIVE_MOVE.items():
            result_analysis = _turn_analysis_result(intent=intent)
            proposal = UntrustedTurnPlanProposal(
                objective=objective, move=move, clarification_target=target)
            result = govern_turn_plan(result_analysis, proposal)
            if objective in allowed:
                assert result.plan is not None, (intent, objective, result.abstention_reason)
                assert result.plan.objective is objective
            else:
                assert result.abstention_reason is (
                    ProfessionalPlanAbstentionReason.INTENT_OBJECTIVE_HARD_MISMATCH), (
                    intent, objective, result.abstention_reason)


# ── Objective/move matrix ────────────────────────────────────────────────────

def test_every_supported_canonical_pair_is_accepted():
    for objective, (move, target) in _CANONICAL_OBJECTIVE_MOVE.items():
        intent = _OBJECTIVE_TEST_INTENT[objective]
        result_analysis = _turn_analysis_result(intent=intent)
        proposal = UntrustedTurnPlanProposal(objective=objective, move=move, clarification_target=target)
        result = govern_turn_plan(result_analysis, proposal)
        assert result.plan is not None, (objective, move, result.abstention_reason)


def test_representative_incompatible_objective_move_pairs_rejected():
    cases = [
        (Intent.UNKNOWN, ProfessionalObjective.CLARIFY, PrimaryResponseMove.REFLECTIVE_STATEMENT, None),
        (Intent.UNKNOWN, ProfessionalObjective.ESTABLISH_CONTACT, PrimaryResponseMove.CLOSING, None),
        (Intent.CLOSE_CONVERSATION, ProfessionalObjective.CLOSE, PrimaryResponseMove.OPEN_INVITATION, None),
    ]
    for intent, objective, move, target in cases:
        result_analysis = _turn_analysis_result(intent=intent)
        proposal = UntrustedTurnPlanProposal(objective=objective, move=move, clarification_target=target)
        result = govern_turn_plan(result_analysis, proposal)
        assert result.abstention_reason is (
            ProfessionalPlanAbstentionReason.OBJECTIVE_MOVE_INCOMPATIBLE), (objective, move)


def test_repair_with_non_sealed_move_is_incompatible_not_v1_unsupported():
    # FOCUSED_QUESTION is not in REPAIR's sealed Stage 1 pairs at all -- this
    # must be caught by the general sealed-pair check (Step 7), never reach
    # the REPAIR-specific V1 narrowing (Step 8).
    result_analysis = _turn_analysis_result(intent=Intent.REPAIR)
    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.REPAIR, move=PrimaryResponseMove.FOCUSED_QUESTION,
        clarification_target=None)
    result = govern_turn_plan(result_analysis, proposal)
    assert result.abstention_reason is ProfessionalPlanAbstentionReason.OBJECTIVE_MOVE_INCOMPATIBLE


# ── REPAIR V1 move narrowing ─────────────────────────────────────────────────

def test_repair_reflective_statement_accepted():
    result_analysis = _turn_analysis_result(intent=Intent.REPAIR)
    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.REPAIR, move=PrimaryResponseMove.REFLECTIVE_STATEMENT,
        clarification_target=None)
    result = govern_turn_plan(result_analysis, proposal)
    assert result.plan is not None and result.plan.move is PrimaryResponseMove.REFLECTIVE_STATEMENT


def test_repair_open_invitation_accepted():
    result_analysis = _turn_analysis_result(intent=Intent.REPAIR)
    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.REPAIR, move=PrimaryResponseMove.OPEN_INVITATION,
        clarification_target=None)
    result = govern_turn_plan(result_analysis, proposal)
    assert result.plan is not None and result.plan.move is PrimaryResponseMove.OPEN_INVITATION


def test_repair_structured_summary_unsupported_in_v1():
    result_analysis = _turn_analysis_result(intent=Intent.REPAIR)
    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.REPAIR, move=PrimaryResponseMove.STRUCTURED_SUMMARY,
        clarification_target=None)
    result = govern_turn_plan(result_analysis, proposal)
    assert result.abstention_reason is ProfessionalPlanAbstentionReason.MOVE_UNSUPPORTED_IN_V1


# ── Clarification target presence ───────────────────────────────────────────

@pytest.mark.parametrize("target", list(ClarificationTarget))
def test_clarify_accepts_every_real_clarification_target(target):
    result_analysis = _turn_analysis_result(intent=Intent.UNKNOWN)
    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.CLARIFY, move=PrimaryResponseMove.FOCUSED_QUESTION,
        clarification_target=target)
    result = govern_turn_plan(result_analysis, proposal)
    assert result.plan is not None and result.plan.clarification_target is target


def test_clarify_without_target_is_presence_invalid():
    result_analysis = _turn_analysis_result(intent=Intent.UNKNOWN)
    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.CLARIFY, move=PrimaryResponseMove.FOCUSED_QUESTION,
        clarification_target=None)
    result = govern_turn_plan(result_analysis, proposal)
    assert result.abstention_reason is (
        ProfessionalPlanAbstentionReason.CLARIFICATION_TARGET_PRESENCE_INVALID)


def test_clarify_goal_without_target_accepted():
    result_analysis = _turn_analysis_result(intent=Intent.UNKNOWN)
    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.CLARIFY_GOAL, move=PrimaryResponseMove.FOCUSED_QUESTION,
        clarification_target=None)
    result = govern_turn_plan(result_analysis, proposal)
    assert result.plan is not None


def test_clarify_goal_with_target_is_presence_invalid():
    result_analysis = _turn_analysis_result(intent=Intent.UNKNOWN)
    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.CLARIFY_GOAL, move=PrimaryResponseMove.FOCUSED_QUESTION,
        clarification_target=ClarificationTarget.EVENT)
    result = govern_turn_plan(result_analysis, proposal)
    assert result.abstention_reason is (
        ProfessionalPlanAbstentionReason.CLARIFICATION_TARGET_PRESENCE_INVALID)


@pytest.mark.parametrize("intent,objective,move", [
    (Intent.UNKNOWN, ProfessionalObjective.ESTABLISH_CONTACT, PrimaryResponseMove.OPEN_INVITATION),
    (Intent.REPAIR, ProfessionalObjective.REPAIR, PrimaryResponseMove.REFLECTIVE_STATEMENT),
    (Intent.CLOSE_CONVERSATION, ProfessionalObjective.CLOSE, PrimaryResponseMove.CLOSING),
])
def test_non_clarify_objectives_with_target_are_presence_invalid(intent, objective, move):
    result_analysis = _turn_analysis_result(intent=intent)
    proposal = UntrustedTurnPlanProposal(
        objective=objective, move=move, clarification_target=ClarificationTarget.EVENT)
    result = govern_turn_plan(result_analysis, proposal)
    assert result.abstention_reason is (
        ProfessionalPlanAbstentionReason.CLARIFICATION_TARGET_PRESENCE_INVALID)


# ── Precise question interaction gate ───────────────────────────────────────

def test_clarify_validated_no_no_questions_accepted_with_question_allowed_true():
    result_analysis = _turn_analysis_result(intent=Intent.UNKNOWN)
    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.CLARIFY, move=PrimaryResponseMove.FOCUSED_QUESTION,
        clarification_target=ClarificationTarget.EVENT)
    result = govern_turn_plan(result_analysis, proposal)
    assert result.plan is not None and result.plan.question_allowed is True


def test_clarify_validated_no_questions_blocked_when_non_question_objective_eligible():
    result_analysis = _turn_analysis_result(
        intent=Intent.RELATIONSHIP_SUPPORT,
        interaction_status=AnalysisComponentStatus.VALIDATED,
        interaction_signals=(InteractionSignal.NO_QUESTIONS,))
    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.CLARIFY, move=PrimaryResponseMove.FOCUSED_QUESTION,
        clarification_target=ClarificationTarget.EVENT)
    result = govern_turn_plan(result_analysis, proposal)
    assert result.abstention_reason is (
        ProfessionalPlanAbstentionReason.NO_QUESTIONS_BLOCKS_QUESTION_OBJECTIVE)


def test_clarify_degraded_interaction_blocked_when_non_question_objective_eligible():
    result_analysis = _turn_analysis_result(
        intent=Intent.RELATIONSHIP_SUPPORT, interaction_status=AnalysisComponentStatus.DEGRADED)
    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.CLARIFY, move=PrimaryResponseMove.FOCUSED_QUESTION,
        clarification_target=ClarificationTarget.EVENT)
    result = govern_turn_plan(result_analysis, proposal)
    assert result.abstention_reason is (
        ProfessionalPlanAbstentionReason.INTERACTION_NOT_VALIDATED_FOR_QUESTION_MOVE)


def test_clarify_unavailable_interaction_blocked_when_non_question_objective_eligible():
    result_analysis = _turn_analysis_result(
        intent=Intent.RELATIONSHIP_SUPPORT, interaction_status=AnalysisComponentStatus.UNAVAILABLE)
    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.CLARIFY, move=PrimaryResponseMove.FOCUSED_QUESTION,
        clarification_target=ClarificationTarget.EVENT)
    result = govern_turn_plan(result_analysis, proposal)
    assert result.abstention_reason is (
        ProfessionalPlanAbstentionReason.INTERACTION_NOT_VALIDATED_FOR_QUESTION_MOVE)


def test_clarify_degraded_with_preserved_no_questions_signal_still_not_validated_reason():
    result_analysis = _turn_analysis_result(
        intent=Intent.RELATIONSHIP_SUPPORT,
        interaction_status=AnalysisComponentStatus.DEGRADED,
        interaction_signals=(InteractionSignal.NO_QUESTIONS,))
    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.CLARIFY, move=PrimaryResponseMove.FOCUSED_QUESTION,
        clarification_target=ClarificationTarget.EVENT)
    result = govern_turn_plan(result_analysis, proposal)
    # Status precedence: DEGRADED wins over the preserved NO_QUESTIONS occurrence.
    assert result.abstention_reason is (
        ProfessionalPlanAbstentionReason.INTERACTION_NOT_VALIDATED_FOR_QUESTION_MOVE)


def test_clarify_goal_follows_same_question_capability_rule():
    result_analysis = _turn_analysis_result(
        intent=Intent.RELATIONSHIP_SUPPORT, interaction_status=AnalysisComponentStatus.DEGRADED)
    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.CLARIFY_GOAL, move=PrimaryResponseMove.FOCUSED_QUESTION,
        clarification_target=None)
    result = govern_turn_plan(result_analysis, proposal)
    assert result.abstention_reason is (
        ProfessionalPlanAbstentionReason.INTERACTION_NOT_VALIDATED_FOR_QUESTION_MOVE)


# ── Trusted question_allowed handoff for accepted non-question plans ───────

_HANDOFF_CASES = [
    (AnalysisComponentStatus.VALIDATED, (), True),
    (AnalysisComponentStatus.VALIDATED, (InteractionSignal.NO_QUESTIONS,), False),
    (AnalysisComponentStatus.DEGRADED, (), False),
    (AnalysisComponentStatus.UNAVAILABLE, (), False),
]


@pytest.mark.parametrize("interaction_status,signals,expected", _HANDOFF_CASES)
def test_question_allowed_handoff_for_establish_contact(interaction_status, signals, expected):
    result_analysis = _turn_analysis_result(
        intent=Intent.UNKNOWN, interaction_status=interaction_status, interaction_signals=signals)
    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.ESTABLISH_CONTACT, move=PrimaryResponseMove.OPEN_INVITATION,
        clarification_target=None)
    result = govern_turn_plan(result_analysis, proposal)
    assert result.plan is not None and result.plan.question_allowed is expected


@pytest.mark.parametrize("interaction_status,signals,expected", _HANDOFF_CASES)
def test_question_allowed_handoff_for_repair(interaction_status, signals, expected):
    result_analysis = _turn_analysis_result(
        intent=Intent.REPAIR, interaction_status=interaction_status, interaction_signals=signals)
    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.REPAIR, move=PrimaryResponseMove.REFLECTIVE_STATEMENT,
        clarification_target=None)
    result = govern_turn_plan(result_analysis, proposal)
    assert result.plan is not None and result.plan.question_allowed is expected


@pytest.mark.parametrize("interaction_status,signals,expected", _HANDOFF_CASES)
def test_question_allowed_handoff_for_close(interaction_status, signals, expected):
    result_analysis = _turn_analysis_result(
        intent=Intent.CLOSE_CONVERSATION, interaction_status=interaction_status,
        interaction_signals=signals)
    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.CLOSE, move=PrimaryResponseMove.CLOSING,
        clarification_target=None)
    result = govern_turn_plan(result_analysis, proposal)
    assert result.plan is not None and result.plan.question_allowed is expected


# ── Non-operative interaction signal regressions ────────────────────────────

@pytest.mark.parametrize("signal", [
    InteractionSignal.JUST_TALK, InteractionSignal.NO_ADVICE, InteractionSignal.ADVICE_ALLOWED,
    InteractionSignal.ADVICE_REQUESTED, InteractionSignal.NO_EXERCISE,
])
def test_non_operative_signals_do_not_block_clarify(signal):
    result_analysis = _turn_analysis_result(
        intent=Intent.UNKNOWN, interaction_status=AnalysisComponentStatus.VALIDATED,
        interaction_signals=(signal,))
    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.CLARIFY, move=PrimaryResponseMove.FOCUSED_QUESTION,
        clarification_target=ClarificationTarget.EVENT)
    result = govern_turn_plan(result_analysis, proposal)
    assert result.plan is not None and result.plan.question_allowed is True


def test_all_non_operative_signals_together_do_not_block_clarify():
    result_analysis = _turn_analysis_result(
        intent=Intent.UNKNOWN, interaction_status=AnalysisComponentStatus.VALIDATED,
        interaction_signals=(
            InteractionSignal.JUST_TALK, InteractionSignal.NO_ADVICE,
            InteractionSignal.ADVICE_ALLOWED, InteractionSignal.ADVICE_REQUESTED,
            InteractionSignal.NO_EXERCISE))
    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.CLARIFY, move=PrimaryResponseMove.FOCUSED_QUESTION,
        clarification_target=ClarificationTarget.EVENT)
    result = govern_turn_plan(result_analysis, proposal)
    assert result.plan is not None and result.plan.question_allowed is True


def test_conflicting_no_advice_and_advice_requested_does_not_block_clarify():
    result_analysis = _turn_analysis_result(
        intent=Intent.UNKNOWN, interaction_status=AnalysisComponentStatus.VALIDATED,
        interaction_signals=(InteractionSignal.NO_ADVICE, InteractionSignal.ADVICE_REQUESTED))
    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.CLARIFY, move=PrimaryResponseMove.FOCUSED_QUESTION,
        clarification_target=ClarificationTarget.EVENT)
    result = govern_turn_plan(result_analysis, proposal)
    assert result.plan is not None


# ── Evidence status does not gate V1 ─────────────────────────────────────────

@pytest.mark.parametrize("evidence_status", list(AnalysisComponentStatus))
def test_evidence_status_alone_does_not_block_a_supported_plan(evidence_status):
    result_analysis = _turn_analysis_result(intent=Intent.UNKNOWN, evidence_status=evidence_status)
    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.ESTABLISH_CONTACT, move=PrimaryResponseMove.OPEN_INVITATION,
        clarification_target=None)
    result = govern_turn_plan(result_analysis, proposal)
    assert result.plan is not None


# ── PARTIAL is not FAILED ────────────────────────────────────────────────────

def test_partial_turn_analysis_can_still_plan():
    result_analysis = _turn_analysis_result(
        intent=Intent.UNKNOWN, evidence_status=AnalysisComponentStatus.DEGRADED)
    assert result_analysis.status is TurnAnalysisStatus.PARTIAL
    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.ESTABLISH_CONTACT, move=PrimaryResponseMove.OPEN_INVITATION,
        clarification_target=None)
    result = govern_turn_plan(result_analysis, proposal)
    assert result.plan is not None


# ── Programming-defect propagation ──────────────────────────────────────────

def test_unexpected_value_error_during_trusted_plan_construction_propagates(monkeypatch):
    def _boom(self):
        raise ValueError("simulated invariant defect")

    monkeypatch.setattr(professional_turn_planner.ProfessionalTurnPlan, "__post_init__", _boom)

    result_analysis = _turn_analysis_result(intent=Intent.UNKNOWN)
    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.ESTABLISH_CONTACT, move=PrimaryResponseMove.OPEN_INVITATION,
        clarification_target=None)
    with pytest.raises(ValueError, match="simulated invariant defect"):
        govern_turn_plan(result_analysis, proposal)


# ── Abstention reason vocabulary ────────────────────────────────────────────

def test_abstention_reason_has_exactly_eleven_members():
    assert len(ProfessionalPlanAbstentionReason) == 11


def test_abstention_reason_exact_membership():
    assert {m.value for m in ProfessionalPlanAbstentionReason} == {
        "UPSTREAM_ANALYSIS_FAILED", "NO_ELIGIBLE_OBJECTIVE_REMAINS", "NO_SEMANTIC_PROPOSAL",
        "PROPOSAL_SEMANTIC_VALUE_INVALID", "OBJECTIVE_UNSUPPORTED_IN_V1",
        "INTENT_OBJECTIVE_HARD_MISMATCH", "OBJECTIVE_MOVE_INCOMPATIBLE", "MOVE_UNSUPPORTED_IN_V1",
        "CLARIFICATION_TARGET_PRESENCE_INVALID", "INTERACTION_NOT_VALIDATED_FOR_QUESTION_MOVE",
        "NO_QUESTIONS_BLOCKS_QUESTION_OBJECTIVE",
    }


# ── Shape locks ───────────────────────────────────────────────────────────────

def test_untrusted_proposal_field_surface_is_exact():
    assert tuple(f.name for f in fields(UntrustedTurnPlanProposal)) == (
        "objective", "move", "clarification_target")


def test_trusted_plan_field_surface_is_exact():
    assert tuple(f.name for f in fields(ProfessionalTurnPlan)) == (
        "objective", "move", "clarification_target", "question_allowed")


def test_plan_result_field_surface_is_exact():
    assert tuple(f.name for f in fields(ProfessionalTurnPlanResult)) == (
        "plan", "abstention_reason")


# ── Purity / import boundary ─────────────────────────────────────────────────

def test_planner_module_imports_only_allowed_modules():
    source = pathlib.Path(professional_turn_planner.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    allowed_roots = {
        "__future__", "dataclasses", "enum", "professional_turn_analysis", "therapeutic_domain"}
    found_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            found_roots.add(node.module.split(".")[0])
    assert found_roots <= allowed_roots, found_roots
