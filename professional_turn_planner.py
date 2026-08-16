"""Professional Core V2 -- Stage 2B Planner Governor V1: deterministic,
offline governance of one untrusted turn-plan proposal against one
authoritative TurnAnalysisResult, for a fixed, narrow 5-objective V1
vocabulary (ESTABLISH_CONTACT, CLARIFY, CLARIFY_GOAL, REPAIR, CLOSE).

The core design constraint from CLAUDE.md applies here too: model output
never decides safety; deterministic code does. An external proposer may
name an objective/move/clarification-target, but every field of the
resulting plan -- including whether a question move is even permitted --
is governed here from the authoritative TurnAnalysisResult, never taken
on the proposer's word. There is no grounding, no free-text payload, no
evidence reference, no method/hypothesis/mechanism field: this V1 plan
carries only a professional objective, a primary response move, an
optional clarification target, and a trusted question-capability
constraint for downstream rendering.

Pure, offline, no I/O: this module owns no runtime wiring, no response
rendering, no persistence, and no model call. Production of the untrusted
proposal itself belongs to a later, separately authorized slice.

Only imports: __future__, dataclasses, enum, professional_turn_analysis,
and therapeutic_domain. Python 3.10 target (prod 3.10.12).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from professional_turn_analysis import (
    AnalysisComponentStatus,
    TurnAnalysisResult,
    TurnAnalysisStatus,
)
from therapeutic_domain import (
    PROFESSIONAL_OBJECTIVE_MOVE_COMPATIBILITY,
    ClarificationTarget,
    Intent,
    InteractionSignal,
    PrimaryResponseMove,
    ProfessionalObjective,
    as_enum,
)

# -- V1 sealed policy (module-private -- not part of the public API) -----

_SUPPORTED_OBJECTIVES: frozenset[ProfessionalObjective] = frozenset({
    ProfessionalObjective.ESTABLISH_CONTACT,
    ProfessionalObjective.CLARIFY,
    ProfessionalObjective.CLARIFY_GOAL,
    ProfessionalObjective.REPAIR,
    ProfessionalObjective.CLOSE,
})

# The two supported objectives whose only sealed move is FOCUSED_QUESTION --
# gated by trusted question_allowed, never by the proposer.
_QUESTION_OBJECTIVES: frozenset[ProfessionalObjective] = frozenset({
    ProfessionalObjective.CLARIFY,
    ProfessionalObjective.CLARIFY_GOAL,
})

# Hard Intent -> allowed-objective eligibility (frozen V1 matrix). This is
# eligibility, not automatic selection, and not an Intent == Objective map.
_INTENT_ALLOWED_OBJECTIVES: dict[Intent, frozenset[ProfessionalObjective]] = {
    Intent.VENT: frozenset({
        ProfessionalObjective.ESTABLISH_CONTACT, ProfessionalObjective.CLARIFY}),
    Intent.EXPLAIN: frozenset({ProfessionalObjective.CLARIFY}),
    Intent.ACTION: frozenset({
        ProfessionalObjective.CLARIFY, ProfessionalObjective.CLARIFY_GOAL}),
    Intent.CHANGE_PATTERN: frozenset({
        ProfessionalObjective.CLARIFY, ProfessionalObjective.CLARIFY_GOAL}),
    Intent.DECISION_SUPPORT: frozenset({
        ProfessionalObjective.CLARIFY, ProfessionalObjective.CLARIFY_GOAL}),
    Intent.PRACTICE: frozenset({
        ProfessionalObjective.CLARIFY, ProfessionalObjective.CLARIFY_GOAL}),
    Intent.REPAIR: frozenset({ProfessionalObjective.REPAIR}),
    Intent.PROBLEM_SOLVING: frozenset({
        ProfessionalObjective.CLARIFY, ProfessionalObjective.CLARIFY_GOAL}),
    Intent.RELATIONSHIP_SUPPORT: frozenset({
        ProfessionalObjective.ESTABLISH_CONTACT, ProfessionalObjective.CLARIFY,
        ProfessionalObjective.CLARIFY_GOAL}),
    Intent.JOURNAL_WORK: frozenset({
        ProfessionalObjective.CLARIFY, ProfessionalObjective.CLARIFY_GOAL}),
    Intent.QUESTIONNAIRE_DISCUSSION: frozenset({
        ProfessionalObjective.CLARIFY, ProfessionalObjective.CLARIFY_GOAL}),
    Intent.CONTINUE_PREVIOUS_WORK: frozenset({ProfessionalObjective.CLARIFY}),
    Intent.CLOSE_CONVERSATION: frozenset({ProfessionalObjective.CLOSE}),
    Intent.UNKNOWN: frozenset({
        ProfessionalObjective.ESTABLISH_CONTACT, ProfessionalObjective.CLARIFY,
        ProfessionalObjective.CLARIFY_GOAL}),
}


class ProfessionalPlanAbstentionReason(str, Enum):
    """Exactly why govern_turn_plan produced no plan for this turn. Closed
    vocabulary -- there is no catch-all member; an unanticipated failure is
    a caller/structural ValueError, never silently mapped onto one of
    these."""
    UPSTREAM_ANALYSIS_FAILED = "UPSTREAM_ANALYSIS_FAILED"
    NO_ELIGIBLE_OBJECTIVE_REMAINS = "NO_ELIGIBLE_OBJECTIVE_REMAINS"
    NO_SEMANTIC_PROPOSAL = "NO_SEMANTIC_PROPOSAL"
    PROPOSAL_SEMANTIC_VALUE_INVALID = "PROPOSAL_SEMANTIC_VALUE_INVALID"
    OBJECTIVE_UNSUPPORTED_IN_V1 = "OBJECTIVE_UNSUPPORTED_IN_V1"
    INTENT_OBJECTIVE_HARD_MISMATCH = "INTENT_OBJECTIVE_HARD_MISMATCH"
    OBJECTIVE_MOVE_INCOMPATIBLE = "OBJECTIVE_MOVE_INCOMPATIBLE"
    MOVE_UNSUPPORTED_IN_V1 = "MOVE_UNSUPPORTED_IN_V1"
    CLARIFICATION_TARGET_PRESENCE_INVALID = "CLARIFICATION_TARGET_PRESENCE_INVALID"
    INTERACTION_NOT_VALIDATED_FOR_QUESTION_MOVE = "INTERACTION_NOT_VALIDATED_FOR_QUESTION_MOVE"
    NO_QUESTIONS_BLOCKS_QUESTION_OBJECTIVE = "NO_QUESTIONS_BLOCKS_QUESTION_OBJECTIVE"


# -- Untrusted transport tier (one external proposer's claim) ------------

@dataclass(frozen=True)
class UntrustedTurnPlanProposal:
    """One untrusted proposer's claim for this turn's plan: an objective,
    a primary move, and an optional clarification target. __post_init__
    performs STRUCTURAL (Python-shape) validation only -- a syntactically
    well-formed but semantically unknown enum string (e.g.
    objective="NOT_A_REAL_OBJECTIVE") survives construction unchanged and
    is untrusted content for govern_turn_plan's own narrow semantic
    canonicalization boundary. No question_allowed field -- that trusted
    derivation belongs exclusively to govern_turn_plan, never to the
    proposer."""
    objective: ProfessionalObjective | str
    move: PrimaryResponseMove | str
    clarification_target: ClarificationTarget | str | None

    def __post_init__(self):
        if not (isinstance(self.objective, ProfessionalObjective)
                or type(self.objective) is str):
            raise ValueError(
                "UntrustedTurnPlanProposal.objective must be a "
                f"ProfessionalObjective or str, got {type(self.objective)!r}")
        if not (isinstance(self.move, PrimaryResponseMove)
                or type(self.move) is str):
            raise ValueError(
                "UntrustedTurnPlanProposal.move must be a PrimaryResponseMove "
                f"or str, got {type(self.move)!r}")
        if not (self.clarification_target is None
                or isinstance(self.clarification_target, ClarificationTarget)
                or type(self.clarification_target) is str):
            raise ValueError(
                "UntrustedTurnPlanProposal.clarification_target must be None, "
                "a ClarificationTarget, or str, got "
                f"{type(self.clarification_target)!r}")


# -- Trusted plan tier -----------------------------------------------------

@dataclass(frozen=True)
class ProfessionalTurnPlan:
    """The trusted, governed plan for one turn: a canonical, V1-supported
    objective/move pair, its clarification target (present iff the
    objective is CLARIFY), and question_allowed -- a trusted downstream
    rendering constraint, not merely a copy of one raw interaction signal.
    __post_init__ enforces only local, context-free invariants; Intent
    eligibility and interaction-component-status lookups require external
    TurnAnalysis context and belong exclusively to govern_turn_plan."""
    objective: ProfessionalObjective
    move: PrimaryResponseMove
    clarification_target: ClarificationTarget | None
    question_allowed: bool

    def __post_init__(self):
        objective = as_enum(ProfessionalObjective, self.objective)
        move = as_enum(PrimaryResponseMove, self.move)
        target = (
            None if self.clarification_target is None
            else as_enum(ClarificationTarget, self.clarification_target))
        if objective not in _SUPPORTED_OBJECTIVES:
            raise ValueError(
                "ProfessionalTurnPlan.objective must be one of the "
                f"V1-supported objectives, got {objective!r}")
        if (objective, move) not in PROFESSIONAL_OBJECTIVE_MOVE_COMPATIBILITY:
            raise ValueError(
                "ProfessionalTurnPlan: objective/move pair is not in the "
                f"sealed Stage 1 compatibility matrix: {objective!r} + {move!r}")
        if (objective is ProfessionalObjective.REPAIR
                and move is PrimaryResponseMove.STRUCTURED_SUMMARY):
            raise ValueError(
                "ProfessionalTurnPlan: REPAIR + STRUCTURED_SUMMARY is not "
                "supported in Planner V1")
        target_required = objective is ProfessionalObjective.CLARIFY
        if target_required and target is None:
            raise ValueError(
                "ProfessionalTurnPlan: CLARIFY requires a non-None "
                "clarification_target")
        if not target_required and target is not None:
            raise ValueError(
                "ProfessionalTurnPlan: clarification_target must be None for "
                f"objective {objective!r}")
        if type(self.question_allowed) is not bool:
            raise ValueError(
                "ProfessionalTurnPlan.question_allowed must be exactly bool, "
                f"got {type(self.question_allowed)!r}")
        if move is PrimaryResponseMove.FOCUSED_QUESTION and self.question_allowed is False:
            raise ValueError(
                "ProfessionalTurnPlan: FOCUSED_QUESTION requires "
                "question_allowed=True")
        object.__setattr__(self, "objective", objective)
        object.__setattr__(self, "move", move)
        object.__setattr__(self, "clarification_target", target)


@dataclass(frozen=True)
class ProfessionalTurnPlanResult:
    """The outcome envelope for one govern_turn_plan call: either a trusted
    ProfessionalTurnPlan, or an explicit abstention reason -- never both,
    never neither."""
    plan: ProfessionalTurnPlan | None
    abstention_reason: ProfessionalPlanAbstentionReason | None

    def __post_init__(self):
        if self.plan is not None and not isinstance(self.plan, ProfessionalTurnPlan):
            raise ValueError(
                "ProfessionalTurnPlanResult.plan must be None or a "
                f"ProfessionalTurnPlan, got {type(self.plan)!r}")
        if self.abstention_reason is not None and not isinstance(
                self.abstention_reason, ProfessionalPlanAbstentionReason):
            raise ValueError(
                "ProfessionalTurnPlanResult.abstention_reason must be None or "
                "a ProfessionalPlanAbstentionReason, got "
                f"{type(self.abstention_reason)!r}")
        if (self.plan is None) == (self.abstention_reason is None):
            raise ValueError(
                "ProfessionalTurnPlanResult requires exactly one of plan/"
                "abstention_reason to be set")


# -- Governor ---------------------------------------------------------------

def govern_turn_plan(
        analysis_result: TurnAnalysisResult,
        proposal: UntrustedTurnPlanProposal | None,
) -> ProfessionalTurnPlanResult:
    """Deterministically govern one untrusted turn-plan proposal against one
    authoritative TurnAnalysisResult under the frozen Stage 2B Planner
    Governor V1 policy. proposal=None is the sole proposer-abstention
    representation. Wrong public-argument Python types are caller/adapter
    defects and raise ValueError; an unexpected ValueError raised while
    building the final trusted plan is a deterministic programming defect
    and propagates -- it is never caught and relabeled as an abstention."""
    if not isinstance(analysis_result, TurnAnalysisResult):
        raise ValueError(
            "govern_turn_plan: analysis_result must be a TurnAnalysisResult, "
            f"got {type(analysis_result)!r}")
    if proposal is not None and not isinstance(proposal, UntrustedTurnPlanProposal):
        raise ValueError(
            "govern_turn_plan: proposal must be None or an "
            f"UntrustedTurnPlanProposal, got {type(proposal)!r}")

    # STEP 1 -- upstream failure wins before any proposer content is inspected.
    if analysis_result.status is TurnAnalysisStatus.FAILED:
        return ProfessionalTurnPlanResult(
            plan=None,
            abstention_reason=ProfessionalPlanAbstentionReason.UPSTREAM_ANALYSIS_FAILED)

    analysis = analysis_result.analysis

    # STEP 2 -- eligible objective set: supported ∩ Intent-allowed, then drop
    # question objectives when question_allowed is not trustworthy.
    intent_allowed = _INTENT_ALLOWED_OBJECTIVES[analysis.intent.analyzer_intent]
    question_allowed = (
        analysis.interaction.status is AnalysisComponentStatus.VALIDATED
        and InteractionSignal.NO_QUESTIONS not in analysis.interaction.request.signals)
    eligible = _SUPPORTED_OBJECTIVES & intent_allowed
    if not question_allowed:
        eligible = eligible - _QUESTION_OBJECTIVES
    if not eligible:
        return ProfessionalTurnPlanResult(
            plan=None,
            abstention_reason=ProfessionalPlanAbstentionReason.NO_ELIGIBLE_OBJECTIVE_REMAINS)

    # STEP 3 -- proposer abstention.
    if proposal is None:
        return ProfessionalTurnPlanResult(
            plan=None,
            abstention_reason=ProfessionalPlanAbstentionReason.NO_SEMANTIC_PROPOSAL)

    # STEP 4 -- semantic enum normalization at the narrow untrusted->canonical
    # boundary; only an unknown-value ValueError from as_enum is expected here.
    try:
        objective = as_enum(ProfessionalObjective, proposal.objective)
        move = as_enum(PrimaryResponseMove, proposal.move)
        target = (
            None if proposal.clarification_target is None
            else as_enum(ClarificationTarget, proposal.clarification_target))
    except ValueError:
        return ProfessionalTurnPlanResult(
            plan=None,
            abstention_reason=ProfessionalPlanAbstentionReason.PROPOSAL_SEMANTIC_VALUE_INVALID)

    # STEP 5 -- supported objective (before Intent hard eligibility).
    if objective not in _SUPPORTED_OBJECTIVES:
        return ProfessionalTurnPlanResult(
            plan=None,
            abstention_reason=ProfessionalPlanAbstentionReason.OBJECTIVE_UNSUPPORTED_IN_V1)

    # STEP 6 -- Intent hard eligibility, against the full (unfiltered) allowed
    # set -- not the question-filtered eligible set from Step 2.
    if objective not in intent_allowed:
        return ProfessionalTurnPlanResult(
            plan=None,
            abstention_reason=ProfessionalPlanAbstentionReason.INTENT_OBJECTIVE_HARD_MISMATCH)

    # STEP 7 -- sealed Stage 1 objective/move pair.
    if (objective, move) not in PROFESSIONAL_OBJECTIVE_MOVE_COMPATIBILITY:
        return ProfessionalTurnPlanResult(
            plan=None,
            abstention_reason=ProfessionalPlanAbstentionReason.OBJECTIVE_MOVE_INCOMPATIBLE)

    # STEP 8 -- V1-only move narrowing.
    if objective is ProfessionalObjective.REPAIR and move is PrimaryResponseMove.STRUCTURED_SUMMARY:
        return ProfessionalTurnPlanResult(
            plan=None,
            abstention_reason=ProfessionalPlanAbstentionReason.MOVE_UNSUPPORTED_IN_V1)

    # STEP 9 -- clarification target presence.
    target_required = objective is ProfessionalObjective.CLARIFY
    if target_required != (target is not None):
        return ProfessionalTurnPlanResult(
            plan=None,
            abstention_reason=(
                ProfessionalPlanAbstentionReason.CLARIFICATION_TARGET_PRESENCE_INVALID))

    # STEP 10 -- precise question gate. Status precedence: a non-VALIDATED
    # component always yields the "not validated" reason, even if it also
    # happens to carry a preserved NO_QUESTIONS occurrence.
    if move is PrimaryResponseMove.FOCUSED_QUESTION:
        if analysis.interaction.status is not AnalysisComponentStatus.VALIDATED:
            return ProfessionalTurnPlanResult(
                plan=None,
                abstention_reason=(
                    ProfessionalPlanAbstentionReason
                    .INTERACTION_NOT_VALIDATED_FOR_QUESTION_MOVE))
        if InteractionSignal.NO_QUESTIONS in analysis.interaction.request.signals:
            return ProfessionalTurnPlanResult(
                plan=None,
                abstention_reason=(
                    ProfessionalPlanAbstentionReason
                    .NO_QUESTIONS_BLOCKS_QUESTION_OBJECTIVE))

    # STEP 11 -- build the trusted plan. Any unexpected ValueError here is a
    # deterministic programming defect and is deliberately left uncaught.
    plan = ProfessionalTurnPlan(
        objective=objective,
        move=move,
        clarification_target=target,
        question_allowed=question_allowed,
    )

    # STEP 12 -- result.
    return ProfessionalTurnPlanResult(plan=plan, abstention_reason=None)
