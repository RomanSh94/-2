"""Professional Free-Text Runtime V1 -- model/professional orchestration only.

This module sequences the already-merged Professional Core V2 chain
(Analyzer -> Producer -> Plan Proposer -> Planner Governor -> Renderer ->
Acceptance) for exactly one already-prepared turn, and nothing else. It owns
NONE of: Telegram send, DB persistence, access control, crisis routing,
legacy routing, reactions, TTS, privacy, or rollout -- those all remain
bot.py's responsibility (or the responsibility of the modules bot.py already
calls for them). This module never reads or writes the database, never
imports bot.py, and never imports any legacy routing module (state_engine,
conversation_controller, prompts).

WHY THIS EXISTS -- Professional Free-Text Runtime V1 makes Professional Core
V2 the sole psychological conversation owner for an eligible claimed turn.
bot.py decides eligibility, persists the current USER row, builds the
trusted prior ProfessionalConversationContext (via database.py and
professional_turn_conversation_context.py, both unmodified by this slice),
and then calls run_professional_free_text_turn exactly once with everything
already prepared. This module's only job is to run the chain and return one
of exactly three closed outcomes.

PLANNER V1 AUTHORITY BOUNDARY -- this module does not expand the Planner. It
only ever consumes whatever move/plan professional_turn_planner.govern_
turn_plan already authoritatively decides to hand back; it adds no new
outcome, no FORMULATE/CHECK_HYPOTHESIS/SELECT_TARGET/DEEPEN/METHOD_
SELECTION/PROPOSE_INTERVENTION/DELIVER_INTERVENTION/PRACTICE_SELECTION
logic of any kind.

ACCEPTANCE ALREADY COMPOSES FIDELITY + POLICY + THE EXISTING SAFETY
VALIDATOR -- professional_turn_response_acceptance.accept_professional_
response's own docstring states it is "Deterministic, synchronous, offline
composition of Fidelity V1 -> Policy V1 -> the existing validate_response_
with_context, in that exact first-fail order." This module therefore never
calls validate_response_fidelity or validate_response_policy directly --
doing so would duplicate accept_professional_response's own internal
composition.

FAILURE POLICY -- exactly three closed outcomes, never a fourth:
  SUCCESS  -- carries exactly one accepted reply_text, failure_stage and
             failure_reason are both None.
  REJECTED -- Acceptance itself said REJECT (Fidelity/Policy/Safety rejected
             an otherwise-structurally-valid candidate). No reply_text.
  FAILED   -- any earlier stage could not produce a usable candidate at all
             (Analyzer/Producer/Plan-Proposer/Planner/Renderer). No reply_text.
Neither REJECTED nor FAILED ever carries a model candidate bot.py could
accidentally deliver -- ProfessionalFreeTextRuntimeResult's own __post_init__
enforces this structurally. There is no fallback-to-legacy-generation logic
anywhere in this module; a caller (bot.py) that gets REJECTED or FAILED is
expected to use its own bounded neutral technical fallback, never a legacy
psychological path.

OBSERVABILITY -- every non-SUCCESS result also carries a failure_reason:
exactly one member of the originating stage's own already-bounded,
already-privacy-safe status/reason enum (never raw user text, candidate
text, model output, provider response body, prompt, or exception message).
ANALYZER and PRODUCER are deliberately distinct stages: ANALYZER means
call_turn_analyzer itself produced no usable output; PRODUCER means the
analyzer call succeeded but produce_turn_analysis's own deterministic
assembly still failed -- see ProfessionalFreeTextFailureStage's own
docstring for why collapsing these would be observability-inaccurate.

Only imports: __future__, dataclasses, enum, and the already-merged
Professional Core V2 modules (professional_turn_analyzer,
professional_turn_producer, professional_turn_analysis,
professional_turn_plan_proposer, professional_turn_planner,
professional_turn_response_renderer, professional_turn_response_acceptance,
professional_turn_conversation_context). No bot.py import, no database
import, no Telegram import, no legacy-routing import (state_engine,
conversation_controller, prompts). Python 3.10 target (prod 3.10.12).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from professional_turn_analyzer import call_turn_analyzer, TurnAnalyzerFailureCategory
from professional_turn_analysis import TurnAnalysisStatus
from professional_turn_producer import produce_turn_analysis
from professional_turn_plan_proposer import call_turn_plan_proposer, TurnPlanProposerCallStatus
from professional_turn_planner import govern_turn_plan, ProfessionalPlanAbstentionReason
from professional_turn_response_renderer import render_turn_response, TurnResponseRenderStatus
from professional_turn_response_acceptance import (
    accept_professional_response,
    ProfessionalResponseAcceptanceStatus,
    AcceptanceRejectionReason,
)
from professional_turn_conversation_context import ProfessionalConversationContext


class ProfessionalFreeTextRuntimeStatus(str, Enum):
    """Closed V1 outcome vocabulary -- exactly three members."""
    SUCCESS = "SUCCESS"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class ProfessionalFreeTextFailureStage(str, Enum):
    """Which stage produced a REJECTED/FAILED outcome -- bounded, privacy-
    safe metadata only (no candidate text, no raw model/provider content,
    no exception message). Set if and only if status is not SUCCESS.

    ANALYZER vs PRODUCER -- these are deliberately distinct, not aliases.
    ANALYZER means call_turn_analyzer itself produced no usable output
    (provider/parse failure). PRODUCER means the analyzer call succeeded
    (usable output) but produce_turn_analysis's own deterministic
    evidence/interaction/intent assembly still could not construct an
    authoritative TurnAnalysis. Collapsing the two into one label would be
    observability-inaccurate: one is a provider/parse problem, the other is
    a deterministic contract outcome given valid analyzer output."""
    ANALYZER = "ANALYZER"
    PRODUCER = "PRODUCER"
    PLAN_PROPOSER = "PLAN_PROPOSER"
    PLANNER = "PLANNER"
    RENDERER = "RENDERER"
    ACCEPTANCE = "ACCEPTANCE"


class ProfessionalFreeTextProducerFailureReason(str, Enum):
    """professional_turn_analysis.TurnAnalysisResult carries no bounded
    sub-reason for FAILED by its own design (status is derived solely from
    whether an authoritative TurnAnalysis was constructed) -- this single
    member exists only so a PRODUCER-stage failure_reason is never silently
    absent, without inventing any semantics Producer itself does not
    expose. Producer's own module/behavior is untouched by this."""
    PRODUCER_FAILED = "PRODUCER_FAILED"


# The complete closed set of values failure_reason may ever hold -- one
# already-bounded, already-privacy-safe enum per possible failure_stage.
# Every member class is already `(str, Enum)`; PEP 604 unions of enum
# classes are real runtime types.UnionType objects on 3.10+, and
# isinstance()/issubclass() against them work exactly as for
# AcceptanceRejectionReason below (whose own definition is itself one such
# union, and simply flattens into this larger one).
ProfessionalFreeTextFailureReason = (
    TurnAnalyzerFailureCategory
    | ProfessionalFreeTextProducerFailureReason
    | TurnPlanProposerCallStatus
    | ProfessionalPlanAbstentionReason
    | TurnResponseRenderStatus
    | AcceptanceRejectionReason
)

# Exactly which reason-enum type each failure_stage may carry -- this is
# what makes an ANALYZER-stage result carrying a Safety rejection reason
# (or any other cross-stage mismatch) structurally impossible, not merely
# a value that happens to satisfy the broader ProfessionalFreeTextFailureReason
# union. Closed and exhaustive over every ProfessionalFreeTextFailureStage
# member -- deliberately not a fallback/default branch, so adding a new
# stage without adding its entry here is a KeyError, not a silent gap.
_STAGE_REASON_TYPE: dict[ProfessionalFreeTextFailureStage, type] = {
    ProfessionalFreeTextFailureStage.ANALYZER: TurnAnalyzerFailureCategory,
    ProfessionalFreeTextFailureStage.PRODUCER: ProfessionalFreeTextProducerFailureReason,
    ProfessionalFreeTextFailureStage.PLAN_PROPOSER: TurnPlanProposerCallStatus,
    ProfessionalFreeTextFailureStage.PLANNER: ProfessionalPlanAbstentionReason,
    ProfessionalFreeTextFailureStage.RENDERER: TurnResponseRenderStatus,
    ProfessionalFreeTextFailureStage.ACCEPTANCE: AcceptanceRejectionReason,
}

# Members of a stage's own reason-enum type that mean "not a failure" for
# that stage (e.g. TurnPlanProposerCallStatus.PROPOSAL, TurnResponseRender
# Status.CANDIDATE) -- isinstance() alone cannot exclude these since they
# belong to the correct type, so they must be excluded explicitly. A stage
# absent from this dict has no such member (its whole reason-enum type is
# failure-only, e.g. ANALYZER/PRODUCER/PLANNER/ACCEPTANCE).
_STAGE_FORBIDDEN_REASON_MEMBERS: dict[ProfessionalFreeTextFailureStage, frozenset] = {
    ProfessionalFreeTextFailureStage.PLAN_PROPOSER: frozenset({TurnPlanProposerCallStatus.PROPOSAL}),
    ProfessionalFreeTextFailureStage.RENDERER: frozenset({TurnResponseRenderStatus.CANDIDATE}),
}


def _validate_failure_reason_for_stage(
        stage: ProfessionalFreeTextFailureStage,
        reason: ProfessionalFreeTextFailureReason,
) -> None:
    """Fail-closed check that `reason` is a member of exactly the reason-
    enum type `stage` is allowed to carry, and is not one of that type's
    own success-shaped members (PROPOSAL/CANDIDATE). Raises ValueError on
    any mismatch; never returns a boolean for a caller to (mis)handle."""
    expected_type = _STAGE_REASON_TYPE[stage]
    if not isinstance(reason, expected_type):
        raise ValueError(
            "ProfessionalFreeTextRuntimeResult: failure_stage="
            f"{stage.value} requires a failure_reason that is a member of "
            f"{expected_type!r}, got {reason!r}")
    forbidden = _STAGE_FORBIDDEN_REASON_MEMBERS.get(stage)
    if forbidden is not None and reason in forbidden:
        raise ValueError(
            "ProfessionalFreeTextRuntimeResult: failure_stage="
            f"{stage.value} must not carry {reason!r} as a failure_reason -- "
            "that member means the stage did NOT fail")


@dataclass(frozen=True)
class ProfessionalFreeTextRuntimeResult:
    """The outcome envelope for one run_professional_free_text_turn call.
    reply_text is set if and only if status is SUCCESS; failure_stage and
    failure_reason are set if and only if status is not SUCCESS -- enforced
    structurally, not just by convention, so a self-contradictory result
    (e.g. SUCCESS with no text, or FAILED carrying a candidate) is
    impossible to construct. failure_reason is always exactly one member of
    the originating stage's own already-bounded status/reason enum -- never
    raw user text, candidate text, model output, provider response body,
    prompt, or exception message."""
    status: ProfessionalFreeTextRuntimeStatus
    reply_text: str | None
    failure_stage: ProfessionalFreeTextFailureStage | None
    failure_reason: ProfessionalFreeTextFailureReason | None

    def __post_init__(self):
        if type(self.status) is not ProfessionalFreeTextRuntimeStatus:
            raise ValueError(
                "ProfessionalFreeTextRuntimeResult.status must be exactly a "
                f"ProfessionalFreeTextRuntimeStatus, got {type(self.status)!r}")
        if self.status is ProfessionalFreeTextRuntimeStatus.SUCCESS:
            if type(self.reply_text) is not str or not self.reply_text.strip():
                raise ValueError(
                    "ProfessionalFreeTextRuntimeResult: SUCCESS requires a "
                    "non-empty, non-whitespace-only reply_text")
            if self.failure_stage is not None:
                raise ValueError(
                    "ProfessionalFreeTextRuntimeResult: SUCCESS must not carry "
                    "a failure_stage")
            if self.failure_reason is not None:
                raise ValueError(
                    "ProfessionalFreeTextRuntimeResult: SUCCESS must not carry "
                    "a failure_reason")
        else:
            if self.reply_text is not None:
                raise ValueError(
                    "ProfessionalFreeTextRuntimeResult: a non-SUCCESS status "
                    "must never carry a reply_text")
            if type(self.failure_stage) is not ProfessionalFreeTextFailureStage:
                raise ValueError(
                    "ProfessionalFreeTextRuntimeResult: a non-SUCCESS status "
                    "requires exactly a ProfessionalFreeTextFailureStage, got "
                    f"{type(self.failure_stage)!r}")
            # status/stage compatibility: REJECTED means Acceptance itself
            # rejected an otherwise-valid candidate -- the only stage that
            # can ever REJECT; every earlier stage can only FAILED (it never
            # reached a candidate for Acceptance to judge).
            if self.status is ProfessionalFreeTextRuntimeStatus.REJECTED:
                if self.failure_stage is not ProfessionalFreeTextFailureStage.ACCEPTANCE:
                    raise ValueError(
                        "ProfessionalFreeTextRuntimeResult: REJECTED requires "
                        "failure_stage=ACCEPTANCE, got "
                        f"{self.failure_stage!r}")
            else:  # FAILED
                if self.failure_stage is ProfessionalFreeTextFailureStage.ACCEPTANCE:
                    raise ValueError(
                        "ProfessionalFreeTextRuntimeResult: FAILED must not carry "
                        "failure_stage=ACCEPTANCE -- an Acceptance rejection is "
                        "always REJECTED, never FAILED")
            # stage/reason compatibility: reason must belong to exactly the
            # reason-enum type this stage is allowed to carry, and must not
            # be that type's own success-shaped member (PROPOSAL/CANDIDATE).
            _validate_failure_reason_for_stage(self.failure_stage, self.failure_reason)


def _failed(
        stage: ProfessionalFreeTextFailureStage,
        reason: ProfessionalFreeTextFailureReason,
) -> ProfessionalFreeTextRuntimeResult:
    return ProfessionalFreeTextRuntimeResult(
        status=ProfessionalFreeTextRuntimeStatus.FAILED, reply_text=None,
        failure_stage=stage, failure_reason=reason)


async def run_professional_free_text_turn(
        *,
        client,
        model: str,
        source_message_row_id: int,
        source_text: str,
        conversation_context: ProfessionalConversationContext,
        risk_result: dict,
        lang: str,
) -> ProfessionalFreeTextRuntimeResult:
    """Runs the full Analyzer -> Producer -> Plan Proposer -> Planner
    Governor -> Renderer -> Acceptance chain for exactly one turn and
    returns exactly one of SUCCESS / REJECTED / FAILED. client is never
    constructed here (injected, same convention as every stage it calls);
    OPENAI_API_KEY/environment/config are never read by this module.

    Deliberately does NOT catch every exception: a genuine caller/adapter
    type defect (e.g. conversation_context of the wrong type) is expected to
    raise ValueError here exactly as it would inside the stage that detects
    it, per each stage's own documented contract -- this is a programming
    defect, not an ordinary runtime failure, and must not be silently
    absorbed into a bounded FAILED result. An ordinary runtime failure
    (provider error, no usable content, structurally invalid response,
    abstention, rejection) never raises anywhere in this chain -- every
    stage already reports it through its own closed status/result type, so
    this function's own control flow is a plain, exception-free sequence of
    status checks. A caller wanting a hard boundary against a truly
    unexpected exception (a defect in this orchestration itself) should wrap
    its own call to this function -- that boundary intentionally lives in
    the caller, not swallowed here, so it stays visible during development
    and testing rather than silently becoming a generic FAILED."""
    if type(source_message_row_id) is not int or source_message_row_id <= 0:
        raise ValueError(
            "run_professional_free_text_turn: source_message_row_id must be a "
            f"positive int, got {source_message_row_id!r}")
    if type(source_text) is not str or not source_text.strip():
        raise ValueError(
            "run_professional_free_text_turn: source_text must be a non-empty, "
            "non-whitespace-only str")
    if type(conversation_context) is not ProfessionalConversationContext:
        raise ValueError(
            "run_professional_free_text_turn: conversation_context must be "
            f"exactly a ProfessionalConversationContext, got {type(conversation_context)!r}")

    analyzer_result = await call_turn_analyzer(
        client=client, model=model, source_text=source_text,
        conversation_context=conversation_context)
    analysis_result = produce_turn_analysis(
        source_message_row_id=source_message_row_id, source_text=source_text,
        analyzer_output=analyzer_result.output)
    if analysis_result.status is TurnAnalysisStatus.FAILED:
        if analyzer_result.output is None:
            # analyzer_result.failure_category is structurally guaranteed
            # non-None here (TurnAnalyzerCallResult.__post_init__ enforces
            # exactly one of output/failure_category set).
            return _failed(ProfessionalFreeTextFailureStage.ANALYZER, analyzer_result.failure_category)
        # Analyzer produced usable output, but Producer's own deterministic
        # assembly still could not construct an authoritative TurnAnalysis --
        # a distinct outcome from an analyzer provider/parse failure.
        return _failed(
            ProfessionalFreeTextFailureStage.PRODUCER,
            ProfessionalFreeTextProducerFailureReason.PRODUCER_FAILED)

    proposer_result = await call_turn_plan_proposer(
        client=client, model=model, analysis_result=analysis_result,
        conversation_context=conversation_context)
    if proposer_result.status is not TurnPlanProposerCallStatus.PROPOSAL:
        return _failed(ProfessionalFreeTextFailureStage.PLAN_PROPOSER, proposer_result.status)

    plan_result = govern_turn_plan(analysis_result, proposal=proposer_result.proposal)
    if plan_result.plan is None:
        # plan_result.abstention_reason is structurally guaranteed non-None
        # here (ProfessionalTurnPlanResult.__post_init__ enforces exactly
        # one of plan/abstention_reason set).
        return _failed(ProfessionalFreeTextFailureStage.PLANNER, plan_result.abstention_reason)

    render_result = await render_turn_response(
        client=client, model=model, plan=plan_result.plan, source_text=source_text,
        conversation_context=conversation_context)
    if render_result.status is not TurnResponseRenderStatus.CANDIDATE:
        return _failed(ProfessionalFreeTextFailureStage.RENDERER, render_result.status)

    acceptance_result = accept_professional_response(
        plan=plan_result.plan, candidate_text=render_result.candidate_text,
        source_text=source_text, risk_result=risk_result, lang=lang)
    if acceptance_result.status is not ProfessionalResponseAcceptanceStatus.ACCEPT:
        return ProfessionalFreeTextRuntimeResult(
            status=ProfessionalFreeTextRuntimeStatus.REJECTED, reply_text=None,
            failure_stage=ProfessionalFreeTextFailureStage.ACCEPTANCE,
            failure_reason=acceptance_result.reason)

    return ProfessionalFreeTextRuntimeResult(
        status=ProfessionalFreeTextRuntimeStatus.SUCCESS,
        reply_text=render_result.candidate_text, failure_stage=None, failure_reason=None)
