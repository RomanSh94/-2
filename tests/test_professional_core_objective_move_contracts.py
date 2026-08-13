"""Structural characterization tests for the Professional Core V2
objective/target/move contracts (ProfessionalObjective, ClarificationTarget,
PrimaryResponseMove, PROFESSIONAL_OBJECTIVE_MOVE_COMPATIBILITY) in
therapeutic_domain.py.

These tests prove the Stage 1C public contract only: exact vocabulary,
fail-closed coercion, and the exact closed compatibility matrix. These
tests do NOT cover Stage 2 planner behavior, evidence sufficiency, future
InteractionSignal consumption, or the existing legacy regex classifier.
Stage 2 planner/InteractionSignal consumption is not implemented by this
contract; the legacy classifier already exists but is intentionally
outside Stage 1C.
"""
import pytest

from therapeutic_domain import (
    PROFESSIONAL_OBJECTIVE_MOVE_COMPATIBILITY,
    ClarificationTarget,
    PrimaryResponseMove,
    ProfessionalObjective,
    as_enum,
)


# ── 1-3. Exact membership ───────────────────────────────────────────────────

def test_professional_objective_has_exactly_eleven_members():
    assert {m.value for m in ProfessionalObjective} == {
        "ESTABLISH_CONTACT", "CLARIFY", "MAP_EPISODE", "CLARIFY_GOAL",
        "TEST_HYPOTHESIS", "CHECK_FORMULATION", "EXPLAIN_MECHANISM",
        "OFFER_ACTION", "REVIEW_OUTCOME", "REPAIR", "CLOSE",
    }


def test_clarification_target_has_exactly_eight_members():
    assert {m.value for m in ClarificationTarget} == {
        "EVENT", "INTERPRETATION", "EMOTION", "BODY", "URGE", "BEHAVIOR",
        "CONSEQUENCE", "PATTERN",
    }


def test_primary_response_move_has_exactly_eight_members():
    assert {m.value for m in PrimaryResponseMove} == {
        "OPEN_INVITATION", "REFLECTIVE_STATEMENT", "FOCUSED_QUESTION",
        "STRUCTURED_SUMMARY", "EXPLANATION", "HYPOTHESIS_CHECK",
        "ACTION_PROPOSAL", "CLOSING",
    }


# ── 4. Unknown values fail closed ───────────────────────────────────────────

def test_unknown_professional_objective_fails_closed():
    with pytest.raises(ValueError):
        as_enum(ProfessionalObjective, "NOT_A_REAL_OBJECTIVE")


def test_unknown_clarification_target_fails_closed():
    with pytest.raises(ValueError):
        as_enum(ClarificationTarget, "NOT_A_REAL_TARGET")


def test_unknown_primary_response_move_fails_closed():
    with pytest.raises(ValueError):
        as_enum(PrimaryResponseMove, "NOT_A_REAL_MOVE")


# ── 5-6. Matrix is immutable and has exactly 15 pairs ───────────────────────

def test_compatibility_matrix_is_frozenset():
    assert isinstance(PROFESSIONAL_OBJECTIVE_MOVE_COMPATIBILITY, frozenset)
    with pytest.raises(AttributeError):
        PROFESSIONAL_OBJECTIVE_MOVE_COMPATIBILITY.add(
            (ProfessionalObjective.CLARIFY, PrimaryResponseMove.REFLECTIVE_STATEMENT))


def test_compatibility_matrix_has_exactly_fifteen_pairs():
    assert len(PROFESSIONAL_OBJECTIVE_MOVE_COMPATIBILITY) == 15


# ── 7. Matrix equals the exact canonical pair set ───────────────────────────

def test_compatibility_matrix_equals_exact_canonical_set():
    O, M = ProfessionalObjective, PrimaryResponseMove
    expected = frozenset({
        (O.ESTABLISH_CONTACT, M.OPEN_INVITATION),
        (O.CLARIFY, M.FOCUSED_QUESTION),
        (O.MAP_EPISODE, M.STRUCTURED_SUMMARY),
        (O.CLARIFY_GOAL, M.FOCUSED_QUESTION),
        (O.TEST_HYPOTHESIS, M.HYPOTHESIS_CHECK),
        (O.TEST_HYPOTHESIS, M.FOCUSED_QUESTION),
        (O.CHECK_FORMULATION, M.HYPOTHESIS_CHECK),
        (O.EXPLAIN_MECHANISM, M.EXPLANATION),
        (O.OFFER_ACTION, M.ACTION_PROPOSAL),
        (O.REVIEW_OUTCOME, M.FOCUSED_QUESTION),
        (O.REVIEW_OUTCOME, M.REFLECTIVE_STATEMENT),
        (O.REPAIR, M.REFLECTIVE_STATEMENT),
        (O.REPAIR, M.OPEN_INVITATION),
        (O.REPAIR, M.STRUCTURED_SUMMARY),
        (O.CLOSE, M.CLOSING),
    })
    assert PROFESSIONAL_OBJECTIVE_MOVE_COMPATIBILITY == expected


def _allowed_moves(objective: ProfessionalObjective) -> set:
    return {move for (obj, move) in PROFESSIONAL_OBJECTIVE_MOVE_COMPATIBILITY
            if obj is objective}


# ── 8-18. Per-objective exact allowed-move sets ─────────────────────────────

def test_establish_contact_allows_only_open_invitation():
    assert _allowed_moves(ProfessionalObjective.ESTABLISH_CONTACT) == {
        PrimaryResponseMove.OPEN_INVITATION}


def test_clarify_allows_only_focused_question():
    assert _allowed_moves(ProfessionalObjective.CLARIFY) == {
        PrimaryResponseMove.FOCUSED_QUESTION}


def test_map_episode_allows_only_structured_summary():
    assert _allowed_moves(ProfessionalObjective.MAP_EPISODE) == {
        PrimaryResponseMove.STRUCTURED_SUMMARY}


def test_clarify_goal_allows_only_focused_question():
    assert _allowed_moves(ProfessionalObjective.CLARIFY_GOAL) == {
        PrimaryResponseMove.FOCUSED_QUESTION}


def test_test_hypothesis_allows_exactly_hypothesis_check_and_focused_question():
    assert _allowed_moves(ProfessionalObjective.TEST_HYPOTHESIS) == {
        PrimaryResponseMove.HYPOTHESIS_CHECK, PrimaryResponseMove.FOCUSED_QUESTION}


def test_check_formulation_allows_only_hypothesis_check():
    assert _allowed_moves(ProfessionalObjective.CHECK_FORMULATION) == {
        PrimaryResponseMove.HYPOTHESIS_CHECK}


def test_explain_mechanism_allows_only_explanation():
    assert _allowed_moves(ProfessionalObjective.EXPLAIN_MECHANISM) == {
        PrimaryResponseMove.EXPLANATION}


def test_offer_action_allows_only_action_proposal():
    assert _allowed_moves(ProfessionalObjective.OFFER_ACTION) == {
        PrimaryResponseMove.ACTION_PROPOSAL}


def test_review_outcome_allows_exactly_focused_question_and_reflective_statement():
    assert _allowed_moves(ProfessionalObjective.REVIEW_OUTCOME) == {
        PrimaryResponseMove.FOCUSED_QUESTION, PrimaryResponseMove.REFLECTIVE_STATEMENT}


def test_repair_allows_exactly_reflective_open_and_structured():
    assert _allowed_moves(ProfessionalObjective.REPAIR) == {
        PrimaryResponseMove.REFLECTIVE_STATEMENT,
        PrimaryResponseMove.OPEN_INVITATION,
        PrimaryResponseMove.STRUCTURED_SUMMARY,
    }


def test_close_allows_only_closing():
    assert _allowed_moves(ProfessionalObjective.CLOSE) == {PrimaryResponseMove.CLOSING}


# ── 19-23. Forbidden pairs are absent ───────────────────────────────────────

def test_clarify_plus_reflective_statement_forbidden():
    assert (ProfessionalObjective.CLARIFY, PrimaryResponseMove.REFLECTIVE_STATEMENT) \
        not in PROFESSIONAL_OBJECTIVE_MOVE_COMPATIBILITY


def test_map_episode_plus_focused_question_forbidden():
    assert (ProfessionalObjective.MAP_EPISODE, PrimaryResponseMove.FOCUSED_QUESTION) \
        not in PROFESSIONAL_OBJECTIVE_MOVE_COMPATIBILITY


def test_map_episode_plus_reflective_statement_forbidden():
    assert (ProfessionalObjective.MAP_EPISODE, PrimaryResponseMove.REFLECTIVE_STATEMENT) \
        not in PROFESSIONAL_OBJECTIVE_MOVE_COMPATIBILITY


def test_clarify_goal_plus_reflective_statement_forbidden():
    assert (ProfessionalObjective.CLARIFY_GOAL, PrimaryResponseMove.REFLECTIVE_STATEMENT) \
        not in PROFESSIONAL_OBJECTIVE_MOVE_COMPATIBILITY


def test_check_formulation_plus_structured_summary_forbidden():
    assert (ProfessionalObjective.CHECK_FORMULATION, PrimaryResponseMove.STRUCTURED_SUMMARY) \
        not in PROFESSIONAL_OBJECTIVE_MOVE_COMPATIBILITY


# ── 24-25. A move legitimately serves multiple objectives ──────────────────

def _objectives_for(move: PrimaryResponseMove) -> set:
    return {obj for (obj, m) in PROFESSIONAL_OBJECTIVE_MOVE_COMPATIBILITY if m is move}


def test_focused_question_serves_four_distinct_objectives():
    assert _objectives_for(PrimaryResponseMove.FOCUSED_QUESTION) == {
        ProfessionalObjective.CLARIFY,
        ProfessionalObjective.CLARIFY_GOAL,
        ProfessionalObjective.TEST_HYPOTHESIS,
        ProfessionalObjective.REVIEW_OUTCOME,
    }


def test_hypothesis_check_serves_two_distinct_objectives():
    assert _objectives_for(PrimaryResponseMove.HYPOTHESIS_CHECK) == {
        ProfessionalObjective.TEST_HYPOTHESIS,
        ProfessionalObjective.CHECK_FORMULATION,
    }
