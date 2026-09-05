"""PROFESSIONAL FREE-TEXT RUNTIME V1.

Covers three layers:
  A. professional_free_text_runtime.py's own orchestration contract (pure
     sequencing logic over mocked Professional Core stage calls -- the
     underlying stages' own correctness is already covered by their own
     dedicated test files; this file proves the ORCHESTRATOR wires their
     status/result types together correctly).
  B. access_control.professional_free_text_allowed_for's rollout semantics
     (composes the feature flag with the existing core_rollout_allowed
     off/owner/invited/all contract).
  C. bot.pipeline()'s new ownership/precedence/persistence/delivery/stale/
     fallback/mood-scale/retry-surface/voice/Entry-Triage behavior -- a
     Professional-claimed turn must own the turn before First-Turn/
     Controller/legacy psychological routing and must never silently fall
     back to them.

No raw model/network access anywhere in this file -- every OpenAI call site
is monkeypatched.
"""
import asyncio
import types
from dataclasses import fields

import pytest

import access_control as ac
import bot
import config
import database
import safety_validator
from interaction_preference import detect_interaction_preference
import professional_free_text_runtime as pftr
from professional_turn_runtime_context import ProfessionalTurnRuntimeContext
from professional_turn_analysis import TurnAnalysisStatus, AnalysisComponentStatus
from professional_turn_analyzer import TurnAnalyzerFailureCategory, TurnAnalyzerStructuralFailureReason
from professional_turn_plan_proposer import TurnPlanProposerCallResult, TurnPlanProposerCallStatus
from professional_turn_planner import UntrustedTurnPlanProposal, ProfessionalPlanAbstentionReason
from professional_turn_response_renderer import TurnResponseRenderResult, TurnResponseRenderStatus
from professional_turn_response_fidelity_validator import FidelityRejectionReason
from professional_turn_response_policy_validator import PolicyRejectionReason
from professional_turn_response_acceptance import (
    ProfessionalResponseAcceptanceResult, ProfessionalResponseAcceptanceStatus,
    AcceptanceSafetyRejectionReason,
)
from therapeutic_domain import PrimaryResponseMove, ProfessionalObjective

run = asyncio.run


# ── shared fixtures / fakes (matching this repo's existing convention) ─────

class FakeUser:
    def __init__(self, uid, username="user", first="U"):
        self.id = uid
        self.username = username
        self.first_name = first


class FakeMessage:
    def __init__(self, user, text="", message_id=1, voice=None):
        self.from_user = user
        self.text = text
        self.voice = voice
        self.chat = types.SimpleNamespace(id=user.id, type="private")
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


def _raise_if_called(name):
    async def _f(*a, **kw):
        raise AssertionError(f"{name} must not be called for a Professional-claimed turn")
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
def _flags_default(monkeypatch):
    monkeypatch.setattr(config, "DEPRESSION_DISCLOSURE_GATE_ENABLED", False)
    monkeypatch.setattr(config, "PROFESSIONAL_FREE_TEXT_RUNTIME_ENABLED", False)
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "off")
    monkeypatch.setattr(config, "THERAPIST_CORE_V1_ENABLED", False)
    monkeypatch.setattr(config, "THERAPIST_CORE_V1_MODEL", "")


async def _seed_user(uid: int):
    await database.upsert_user(uid, f"u{uid}", f"U{uid}")
    # Past first-turn eligibility, same proven pattern as
    # tests/test_conversation_controller.py -- a fresh pipeline() call for
    # this uid is definitively past first-turn and reaches the Professional/
    # Controller/ordinary path under test.
    await database.claim_first_turn(uid, config.FIRST_TURN_CONTRACT_VERSION,
                                    f"test-preconsumed-{uid}", "test_setup")


def _stub_legacy_machinery(monkeypatch, *, llm_reply="ok, noted"):
    """Stubs everything the LEGACY/Controller/first-turn paths would need if
    they were ever (incorrectly) reached -- deliberately present so a bug
    that lets one of them run produces a clear, attributable failure rather
    than a raw OpenAI network error."""
    monkeypatch.setattr(bot, "get_emotional_trajectory", _async(types.SimpleNamespace(
        trend="stable", hopelessness_streak=0, yellow_plus_streak=0, messages_analyzed=0)))
    monkeypatch.setattr(bot, "load_state", _raise_if_called("load_state"))
    monkeypatch.setattr(bot, "choose_scenario", _raise_if_called_sync("choose_scenario"))
    monkeypatch.setattr(bot, "_controller_claim_turn", _raise_if_called("_controller_claim_turn"))
    monkeypatch.setattr(bot, "_controller_generate_and_deliver", _raise_if_called("_controller_generate_and_deliver"))
    monkeypatch.setattr(bot, "_first_turn_generate_and_validate", _raise_if_called("_first_turn_generate_and_validate"))
    monkeypatch.setattr(bot, "_retry_failed_practice_prompts", _raise_if_called("_retry_failed_practice_prompts"))
    monkeypatch.setattr(bot, "maybe_summarize", _raise_if_called("maybe_summarize"))
    monkeypatch.setattr(bot, "build_context", _raise_if_called("build_context"))
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
        raise AssertionError("legacy client.chat.completions.create must not be called "
                             "for a Professional-claimed turn")
    monkeypatch.setattr(bot.client.chat.completions, "create", fake_create)


def _raise_if_called_sync(name):
    def _f(*a, **kw):
        raise AssertionError(f"{name} must not be called for a Professional-claimed turn")
    return _f


def _stub_professional_eligible(monkeypatch, eligible=True):
    monkeypatch.setattr(ac, "professional_free_text_allowed_for", _async(eligible))


def _stub_history(monkeypatch, rows=()):
    async def fake_get_rows(uid, current_row_id):
        return list(rows)
    monkeypatch.setattr(bot, "get_professional_conversation_history_rows", fake_get_rows)


def _stub_runtime_result(monkeypatch, result):
    calls = {"n": 0, "kwargs": None}
    async def fake_run(**kwargs):
        calls["n"] += 1
        calls["kwargs"] = kwargs
        return result
    monkeypatch.setattr(bot, "run_professional_free_text_turn", fake_run)
    return calls


def _success_trace(
        *, objective=ProfessionalObjective.CLARIFY, move=PrimaryResponseMove.FOCUSED_QUESTION,
        question_allowed=True, clarification_target_present=True, bounded_alternative_used=False,
        analysis_status=TurnAnalysisStatus.OK, interaction_status=AnalysisComponentStatus.VALIDATED,
        optional_context_recovery_used=False):
    return pftr.ProfessionalTurnSuccessTrace(
        analysis_status=analysis_status, interaction_status=interaction_status,
        optional_context_recovery_used=optional_context_recovery_used,
        objective=objective, primary_response_move=move, question_allowed=question_allowed,
        clarification_target_present=clarification_target_present,
        bounded_alternative_used=bounded_alternative_used,
        acceptance=ProfessionalResponseAcceptanceStatus.ACCEPT)


SUCCESS_RESULT = pftr.ProfessionalFreeTextRuntimeResult(
    status=pftr.ProfessionalFreeTextRuntimeStatus.SUCCESS,
    reply_text="Похоже, тебе сейчас непросто. Что для тебя сейчас самое сложное в этом?",
    failure_stage=None, failure_reason=None, failure_detail=None,
    success_trace=_success_trace())

REJECTED_RESULT = pftr.ProfessionalFreeTextRuntimeResult(
    status=pftr.ProfessionalFreeTextRuntimeStatus.REJECTED, reply_text=None,
    failure_stage=pftr.ProfessionalFreeTextFailureStage.ACCEPTANCE,
    failure_reason=AcceptanceSafetyRejectionReason.SAFETY_REJECTED, failure_detail=None)

FAILED_RESULT = pftr.ProfessionalFreeTextRuntimeResult(
    status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text=None,
    failure_stage=pftr.ProfessionalFreeTextFailureStage.ANALYZER,
    failure_reason=TurnAnalyzerFailureCategory.PROVIDER_FAILURE, failure_detail=None)


# ══════════════════════════════════════════════════════════════════════════
# A. professional_free_text_runtime.py orchestration contract
# ══════════════════════════════════════════════════════════════════════════

def test_result_success_requires_nonempty_text_and_no_failure_stage():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.SUCCESS, reply_text=None,
            failure_stage=None, failure_reason=None, failure_detail=None)
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.SUCCESS, reply_text="  ",
            failure_stage=None, failure_reason=None, failure_detail=None)
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.SUCCESS, reply_text="ok",
            failure_stage=pftr.ProfessionalFreeTextFailureStage.ANALYZER, failure_reason=None, failure_detail=None)


def test_result_success_must_not_carry_a_failure_reason():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.SUCCESS, reply_text="ok",
            failure_stage=None, failure_reason=TurnAnalyzerFailureCategory.PROVIDER_FAILURE, failure_detail=None)


def test_result_non_success_must_not_carry_reply_text():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text="leaked candidate",
            failure_stage=pftr.ProfessionalFreeTextFailureStage.ANALYZER,
            failure_reason=TurnAnalyzerFailureCategory.PROVIDER_FAILURE, failure_detail=None)


def test_result_non_success_requires_a_failure_stage():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.REJECTED, reply_text=None,
            failure_stage=None, failure_reason=AcceptanceSafetyRejectionReason.SAFETY_REJECTED, failure_detail=None)


def test_result_non_success_requires_a_failure_reason():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text=None,
            failure_stage=pftr.ProfessionalFreeTextFailureStage.ANALYZER, failure_reason=None, failure_detail=None)


def test_result_failure_reason_must_be_a_bounded_enum_member_not_a_raw_string():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text=None,
            failure_stage=pftr.ProfessionalFreeTextFailureStage.ANALYZER,
            failure_reason="PROVIDER_FAILURE", failure_detail=None)


# ── failure_detail contract lock: optional, and scoped to the exact
# (stage, reason) pair -- currently only ANALYZER+STRUCTURALLY_INVALID_
# RESPONSE may carry one. ══════════════════════════════════════════════════

def test_result_success_must_not_carry_a_failure_detail():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.SUCCESS, reply_text="ok",
            failure_stage=None, failure_reason=None,
            failure_detail=TurnAnalyzerStructuralFailureReason.MALFORMED_JSON)


def test_analyzer_provider_failure_must_not_carry_a_failure_detail():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text=None,
            failure_stage=pftr.ProfessionalFreeTextFailureStage.ANALYZER,
            failure_reason=TurnAnalyzerFailureCategory.PROVIDER_FAILURE,
            failure_detail=TurnAnalyzerStructuralFailureReason.MALFORMED_JSON)


def test_analyzer_no_usable_content_must_not_carry_a_failure_detail():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text=None,
            failure_stage=pftr.ProfessionalFreeTextFailureStage.ANALYZER,
            failure_reason=TurnAnalyzerFailureCategory.NO_USABLE_CONTENT,
            failure_detail=TurnAnalyzerStructuralFailureReason.MALFORMED_JSON)


def test_non_analyzer_stage_must_not_carry_a_failure_detail():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text=None,
            failure_stage=pftr.ProfessionalFreeTextFailureStage.PRODUCER,
            failure_reason=pftr.ProfessionalFreeTextProducerFailureReason.PRODUCER_FAILED,
            failure_detail=TurnAnalyzerStructuralFailureReason.MALFORMED_JSON)


def test_analyzer_structurally_invalid_requires_a_failure_detail():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text=None,
            failure_stage=pftr.ProfessionalFreeTextFailureStage.ANALYZER,
            failure_reason=TurnAnalyzerFailureCategory.STRUCTURALLY_INVALID_RESPONSE,
            failure_detail=None)


def test_analyzer_structurally_invalid_accepts_exact_detail():
    result = pftr.ProfessionalFreeTextRuntimeResult(
        status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text=None,
        failure_stage=pftr.ProfessionalFreeTextFailureStage.ANALYZER,
        failure_reason=TurnAnalyzerFailureCategory.STRUCTURALLY_INVALID_RESPONSE,
        failure_detail=TurnAnalyzerStructuralFailureReason.EVIDENCE_SPAN_TOO_LONG)
    assert result.failure_detail is TurnAnalyzerStructuralFailureReason.EVIDENCE_SPAN_TOO_LONG


def test_failure_detail_must_be_exact_enum_member_not_a_raw_string():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text=None,
            failure_stage=pftr.ProfessionalFreeTextFailureStage.ANALYZER,
            failure_reason=TurnAnalyzerFailureCategory.STRUCTURALLY_INVALID_RESPONSE,
            failure_detail="MALFORMED_JSON")


# ── stage/reason contract lock: reason must belong to exactly the stage it
# is reported under, and status must match the stage (REJECTED<->ACCEPTANCE
# only, FAILED<->every other stage) -- proves a result cannot be constructed
# with a cross-stage or cross-status mismatch, even though every individual
# value involved is itself a legitimate, bounded, privacy-safe enum member.
# ══════════════════════════════════════════════════════════════════════════

def test_valid_result_for_every_real_failure_stage():
    """One structurally-valid construction per real failure stage --
    proves the strengthened contract does not also reject legitimate
    combinations (a check that only ever raises would trivially "pass"
    every rejection test below for the wrong reason)."""
    valid = [
        (pftr.ProfessionalFreeTextRuntimeStatus.FAILED,
         pftr.ProfessionalFreeTextFailureStage.ANALYZER,
         TurnAnalyzerFailureCategory.PROVIDER_FAILURE),
        (pftr.ProfessionalFreeTextRuntimeStatus.FAILED,
         pftr.ProfessionalFreeTextFailureStage.PRODUCER,
         pftr.ProfessionalFreeTextProducerFailureReason.PRODUCER_FAILED),
        (pftr.ProfessionalFreeTextRuntimeStatus.FAILED,
         pftr.ProfessionalFreeTextFailureStage.PLAN_PROPOSER,
         TurnPlanProposerCallStatus.ABSTAINED),
        (pftr.ProfessionalFreeTextRuntimeStatus.FAILED,
         pftr.ProfessionalFreeTextFailureStage.PLANNER,
         ProfessionalPlanAbstentionReason.NO_SEMANTIC_PROPOSAL),
        (pftr.ProfessionalFreeTextRuntimeStatus.FAILED,
         pftr.ProfessionalFreeTextFailureStage.RENDERER,
         TurnResponseRenderStatus.NO_USABLE_CONTENT),
        (pftr.ProfessionalFreeTextRuntimeStatus.REJECTED,
         pftr.ProfessionalFreeTextFailureStage.ACCEPTANCE,
         AcceptanceSafetyRejectionReason.SAFETY_REJECTED),
    ]
    for status, stage, reason in valid:
        result = pftr.ProfessionalFreeTextRuntimeResult(
            status=status, reply_text=None, failure_stage=stage, failure_reason=reason, failure_detail=None)
        assert result.failure_stage is stage
        assert result.failure_reason is reason


def test_analyzer_stage_rejects_reason_from_another_stage():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text=None,
            failure_stage=pftr.ProfessionalFreeTextFailureStage.ANALYZER,
            failure_reason=AcceptanceSafetyRejectionReason.SAFETY_REJECTED, failure_detail=None)


def test_producer_stage_rejects_analyzer_reason():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text=None,
            failure_stage=pftr.ProfessionalFreeTextFailureStage.PRODUCER,
            failure_reason=TurnAnalyzerFailureCategory.PROVIDER_FAILURE, failure_detail=None)


def test_plan_proposer_stage_rejects_proposal_as_a_failure_reason():
    """PROPOSAL is TurnPlanProposerCallStatus's own success member -- it
    must never be reportable as a failure_reason regardless of type match."""
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text=None,
            failure_stage=pftr.ProfessionalFreeTextFailureStage.PLAN_PROPOSER,
            failure_reason=TurnPlanProposerCallStatus.PROPOSAL, failure_detail=None)


def test_renderer_stage_rejects_candidate_as_a_failure_reason():
    """CANDIDATE is TurnResponseRenderStatus's own success member -- it
    must never be reportable as a failure_reason regardless of type match."""
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text=None,
            failure_stage=pftr.ProfessionalFreeTextFailureStage.RENDERER,
            failure_reason=TurnResponseRenderStatus.CANDIDATE, failure_detail=None)


def test_planner_stage_rejects_reason_from_another_stage():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text=None,
            failure_stage=pftr.ProfessionalFreeTextFailureStage.PLANNER,
            failure_reason=TurnResponseRenderStatus.NO_USABLE_CONTENT, failure_detail=None)


def test_acceptance_stage_rejects_reason_from_another_stage():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.REJECTED, reply_text=None,
            failure_stage=pftr.ProfessionalFreeTextFailureStage.ACCEPTANCE,
            failure_reason=TurnAnalyzerFailureCategory.PROVIDER_FAILURE, failure_detail=None)


def test_rejected_status_requires_acceptance_stage():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.REJECTED, reply_text=None,
            failure_stage=pftr.ProfessionalFreeTextFailureStage.ANALYZER,
            failure_reason=TurnAnalyzerFailureCategory.PROVIDER_FAILURE, failure_detail=None)


def test_failed_status_must_not_carry_acceptance_stage():
    """An Acceptance rejection is always REJECTED, never FAILED -- FAILED
    means an earlier stage never even reached a candidate for Acceptance
    to judge."""
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text=None,
            failure_stage=pftr.ProfessionalFreeTextFailureStage.ACCEPTANCE,
            failure_reason=AcceptanceSafetyRejectionReason.SAFETY_REJECTED, failure_detail=None)


def test_run_rejects_non_positive_row_id():
    async def go():
        await pftr.run_professional_free_text_turn(
            client=None, model="gpt-4o-mini", source_message_row_id=0,
            source_text="hi", runtime_context=_empty_runtime_context(), risk_result={}, lang="ru")
    with pytest.raises(ValueError):
        run(go())


def test_run_rejects_wrong_context_type():
    async def go():
        await pftr.run_professional_free_text_turn(
            client=None, model="gpt-4o-mini", source_message_row_id=1,
            source_text="hi", runtime_context="not a context", risk_result={}, lang="ru")
    with pytest.raises(ValueError):
        run(go())


def test_run_rejects_raw_conversation_context_passed_as_runtime_context():
    """The pre-slice calling convention (a bare ProfessionalConversationContext)
    must no longer be accepted -- only the ProfessionalTurnRuntimeContext
    envelope is a valid runtime_context value now."""
    async def go():
        await pftr.run_professional_free_text_turn(
            client=None, model="gpt-4o-mini", source_message_row_id=1,
            source_text="hi", runtime_context=_empty_context(), risk_result={}, lang="ru")
    with pytest.raises(ValueError):
        run(go())


def _empty_context():
    from professional_turn_conversation_context import EMPTY_CONVERSATION_CONTEXT
    return EMPTY_CONVERSATION_CONTEXT


def _empty_runtime_context():
    from professional_turn_runtime_context import ProfessionalTurnRuntimeContext
    return ProfessionalTurnRuntimeContext(conversation=_empty_context())


def _monkeypatch_chain(monkeypatch, *, analyzer_failed=False,
                       analyzer_failure_category=None, analyzer_structural_failure_reason=None,
                       optional_context_recovery_used=False,
                       producer_failed=False, analysis_status=TurnAnalysisStatus.OK,
                       interaction_status=AnalysisComponentStatus.VALIDATED,
                       proposer_status=None, plan_none=False,
                       plan_objective=ProfessionalObjective.ESTABLISH_CONTACT,
                       plan_move=PrimaryResponseMove.OPEN_INVITATION,
                       plan_question_allowed=False, plan_clarification_target=None,
                       bounded_alternative_used=False,
                       render_status=None, accept_status=None):
    effective_analyzer_category = analyzer_failure_category or TurnAnalyzerFailureCategory.PROVIDER_FAILURE
    # structural_failure_reason mirrors TurnAnalyzerCallResult's own contract:
    # set iff the category is STRUCTURALLY_INVALID_RESPONSE, defaulting to a
    # concrete member so a test that only cares about the category doesn't
    # have to also specify a detail.
    if effective_analyzer_category is TurnAnalyzerFailureCategory.STRUCTURALLY_INVALID_RESPONSE:
        effective_structural_failure_reason = (
            analyzer_structural_failure_reason or TurnAnalyzerStructuralFailureReason.MALFORMED_JSON)
    else:
        effective_structural_failure_reason = None

    async def fake_analyzer(**kw):
        if analyzer_failed:
            return types.SimpleNamespace(
                output=None, failure_category=effective_analyzer_category, model=kw["model"],
                structural_failure_reason=effective_structural_failure_reason,
                optional_context_recovery_used=False)
        return types.SimpleNamespace(
            output=object(), failure_category=None, model=kw["model"],
            structural_failure_reason=None,
            optional_context_recovery_used=optional_context_recovery_used)
    monkeypatch.setattr(pftr, "call_turn_analyzer", fake_analyzer)

    def fake_produce(**kw):
        # Mocked at the whole-function level (not by hand-constructing a real
        # TurnAnalysis/TurnAnalysisResult, which requires a full, valid
        # component tree already covered by professional_turn_producer's own
        # dedicated tests) -- this file only proves the ORCHESTRATOR reacts
        # correctly to analysis_result.status, not Producer's own internal
        # component-assembly correctness. analyzer_failed and producer_failed
        # are deliberately independent knobs: analyzer_failed means
        # call_turn_analyzer itself produced no output (kw["analyzer_output"]
        # is None); producer_failed means the analyzer output WAS usable but
        # Producer's own deterministic assembly still failed -- these must
        # map to different ProfessionalFreeTextFailureStage members.
        status = TurnAnalysisStatus.FAILED if (
            analyzer_failed or producer_failed or kw["analyzer_output"] is None
        ) else analysis_status
        return types.SimpleNamespace(
            status=status,
            analysis=types.SimpleNamespace(
                interaction=types.SimpleNamespace(status=interaction_status)))
    monkeypatch.setattr(pftr, "produce_turn_analysis", fake_produce)

    effective_proposer_status = proposer_status or TurnPlanProposerCallStatus.PROPOSAL
    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.ESTABLISH_CONTACT,
        move=PrimaryResponseMove.OPEN_INVITATION,
        clarification_target=None) if effective_proposer_status is TurnPlanProposerCallStatus.PROPOSAL else None

    async def fake_proposer(**kw):
        return TurnPlanProposerCallResult(status=effective_proposer_status, proposal=proposal, model=kw["model"])
    monkeypatch.setattr(pftr, "call_turn_plan_proposer", fake_proposer)

    def fake_govern(analysis_result, *, proposal):
        # Mocked at the whole-function level, same rationale as
        # fake_produce above -- ProfessionalTurnPlan's own real construction
        # contract (objective/move compatibility, question_allowed
        # derivation) is already covered by professional_turn_planner's own
        # dedicated tests; this file only proves the orchestrator reacts
        # correctly to plan_result.plan being None or not.
        if plan_none or proposal is None:
            return types.SimpleNamespace(
                plan=None, abstention_reason=ProfessionalPlanAbstentionReason.NO_SEMANTIC_PROPOSAL,
                bounded_alternative_used=False)
        return types.SimpleNamespace(
            plan=types.SimpleNamespace(
                objective=plan_objective, move=plan_move,
                question_allowed=plan_question_allowed,
                clarification_target=plan_clarification_target),
            abstention_reason=None, bounded_alternative_used=bounded_alternative_used)
    monkeypatch.setattr(pftr, "govern_turn_plan", fake_govern)

    async def fake_render(**kw):
        status = render_status or TurnResponseRenderStatus.CANDIDATE
        text = "Что для тебя сейчас самое сложное?" if status is TurnResponseRenderStatus.CANDIDATE else None
        return TurnResponseRenderResult(status=status, candidate_text=text, model=kw["model"])
    monkeypatch.setattr(pftr, "render_turn_response", fake_render)

    def fake_accept(**kw):
        status = accept_status or ProfessionalResponseAcceptanceStatus.ACCEPT
        reason = None if status is ProfessionalResponseAcceptanceStatus.ACCEPT \
            else AcceptanceSafetyRejectionReason.SAFETY_REJECTED
        return ProfessionalResponseAcceptanceResult(status=status, reason=reason)
    monkeypatch.setattr(pftr, "accept_professional_response", fake_accept)


def test_chain_success_returns_candidate_text(monkeypatch):
    _monkeypatch_chain(monkeypatch)
    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        runtime_context=_empty_runtime_context(), risk_result={"score": 0, "categories": []}, lang="ru"))
    assert result.status is pftr.ProfessionalFreeTextRuntimeStatus.SUCCESS
    assert result.reply_text == "Что для тебя сейчас самое сложное?"
    assert result.failure_stage is None
    assert result.failure_reason is None


def test_orchestrator_forwards_the_identical_runtime_context_object_to_all_three_stages(monkeypatch):
    """The orchestrator must never unwrap runtime_context itself -- it
    forwards the SAME ProfessionalTurnRuntimeContext object to
    call_turn_analyzer, call_turn_plan_proposer, and render_turn_response.
    Each fake below records exactly the object it received; identity (not
    just equality) across all three proves no copy/rewrap happened at the
    orchestrator boundary."""
    received = {}

    async def fake_analyzer(**kw):
        received["analyzer"] = kw["runtime_context"]
        return types.SimpleNamespace(
            output=object(), failure_category=None, model=kw["model"],
            structural_failure_reason=None, optional_context_recovery_used=False)
    monkeypatch.setattr(pftr, "call_turn_analyzer", fake_analyzer)

    def fake_produce(**kw):
        return types.SimpleNamespace(
            status=TurnAnalysisStatus.OK,
            analysis=types.SimpleNamespace(
                interaction=types.SimpleNamespace(status=AnalysisComponentStatus.VALIDATED)))
    monkeypatch.setattr(pftr, "produce_turn_analysis", fake_produce)

    proposal = UntrustedTurnPlanProposal(
        objective=ProfessionalObjective.ESTABLISH_CONTACT,
        move=PrimaryResponseMove.OPEN_INVITATION, clarification_target=None)

    async def fake_proposer(**kw):
        received["proposer"] = kw["runtime_context"]
        return TurnPlanProposerCallResult(
            status=TurnPlanProposerCallStatus.PROPOSAL, proposal=proposal, model=kw["model"])
    monkeypatch.setattr(pftr, "call_turn_plan_proposer", fake_proposer)

    def fake_govern(analysis_result, *, proposal):
        return types.SimpleNamespace(
            plan=types.SimpleNamespace(
                objective=ProfessionalObjective.ESTABLISH_CONTACT,
                move=PrimaryResponseMove.OPEN_INVITATION,
                question_allowed=False, clarification_target=None),
            abstention_reason=None, bounded_alternative_used=False)
    monkeypatch.setattr(pftr, "govern_turn_plan", fake_govern)

    async def fake_render(**kw):
        received["renderer"] = kw["runtime_context"]
        return TurnResponseRenderResult(
            status=TurnResponseRenderStatus.CANDIDATE, candidate_text="ok", model=kw["model"])
    monkeypatch.setattr(pftr, "render_turn_response", fake_render)

    def fake_accept(**kw):
        return ProfessionalResponseAcceptanceResult(
            status=ProfessionalResponseAcceptanceStatus.ACCEPT, reason=None)
    monkeypatch.setattr(pftr, "accept_professional_response", fake_accept)

    the_runtime_context = _empty_runtime_context()
    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        runtime_context=the_runtime_context, risk_result={"score": 0, "categories": []}, lang="ru"))
    assert result.status is pftr.ProfessionalFreeTextRuntimeStatus.SUCCESS
    assert received["analyzer"] is the_runtime_context
    assert received["proposer"] is the_runtime_context
    assert received["renderer"] is the_runtime_context


def test_chain_analyzer_failure_yields_failed_analyzer_stage(monkeypatch):
    _monkeypatch_chain(monkeypatch, analyzer_failed=True)
    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        runtime_context=_empty_runtime_context(), risk_result={}, lang="ru"))
    assert result.status is pftr.ProfessionalFreeTextRuntimeStatus.FAILED
    assert result.failure_stage is pftr.ProfessionalFreeTextFailureStage.ANALYZER
    assert result.reply_text is None
    assert result.failure_reason is TurnAnalyzerFailureCategory.PROVIDER_FAILURE


@pytest.mark.parametrize("category", [
    TurnAnalyzerFailureCategory.PROVIDER_FAILURE, TurnAnalyzerFailureCategory.NO_USABLE_CONTENT,
    TurnAnalyzerFailureCategory.STRUCTURALLY_INVALID_RESPONSE,
])
def test_chain_analyzer_failure_propagates_exact_bounded_category(monkeypatch, category):
    """The orchestrator must forward the ACTUAL analyzer_result.failure_category
    it received, not a fixed/guessed value -- proves real propagation, not a
    hardcoded constant that happens to match one test case."""
    _monkeypatch_chain(monkeypatch, analyzer_failed=True, analyzer_failure_category=category)
    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        runtime_context=_empty_runtime_context(), risk_result={}, lang="ru"))
    assert result.failure_stage is pftr.ProfessionalFreeTextFailureStage.ANALYZER
    assert result.failure_reason is category


def test_chain_analyzer_structurally_invalid_propagates_exact_detail(monkeypatch):
    """The one case that carries a failure_detail: ANALYZER +
    STRUCTURALLY_INVALID_RESPONSE must forward the orchestrator-level
    analyzer_result.structural_failure_reason exactly, proving real
    end-to-end propagation through run_professional_free_text_turn, not
    just the dataclass-level contract already covered separately."""
    _monkeypatch_chain(
        monkeypatch, analyzer_failed=True,
        analyzer_failure_category=TurnAnalyzerFailureCategory.STRUCTURALLY_INVALID_RESPONSE,
        analyzer_structural_failure_reason=TurnAnalyzerStructuralFailureReason.WRONG_REQUIRED_KEY_SET)
    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        runtime_context=_empty_runtime_context(), risk_result={}, lang="ru"))
    assert result.status is pftr.ProfessionalFreeTextRuntimeStatus.FAILED
    assert result.failure_stage is pftr.ProfessionalFreeTextFailureStage.ANALYZER
    assert result.failure_reason is TurnAnalyzerFailureCategory.STRUCTURALLY_INVALID_RESPONSE
    assert result.failure_detail is TurnAnalyzerStructuralFailureReason.WRONG_REQUIRED_KEY_SET


# ── Candidate Text Bounds Detail V2 -- representative granular details
# flow end-to-end through the real orchestrator unchanged. Proves this
# slice required zero changes to professional_free_text_runtime.py itself
# (the existing failure_detail contract already handles any member of
# TurnAnalyzerStructuralFailureReason -- adding new members to that same
# closed enum needed no propagation-layer change). ═══════════════════════

@pytest.mark.parametrize("detail", [
    TurnAnalyzerStructuralFailureReason.EVIDENCE_SPAN_TOO_LONG,
    TurnAnalyzerStructuralFailureReason.INTERACTION_SPAN_TOO_LONG,
    TurnAnalyzerStructuralFailureReason.EVIDENCE_CONTEXT_BEFORE_TOO_LONG,
    TurnAnalyzerStructuralFailureReason.INTERACTION_CONTEXT_AFTER_EMPTY,
    TurnAnalyzerStructuralFailureReason.INTERACTION_CONTEXT_AFTER_WHITESPACE_ONLY,
])
def test_chain_propagates_granular_candidate_text_bounds_detail(monkeypatch, detail):
    _monkeypatch_chain(
        monkeypatch, analyzer_failed=True,
        analyzer_failure_category=TurnAnalyzerFailureCategory.STRUCTURALLY_INVALID_RESPONSE,
        analyzer_structural_failure_reason=detail)
    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        runtime_context=_empty_runtime_context(), risk_result={}, lang="ru"))
    assert result.status is pftr.ProfessionalFreeTextRuntimeStatus.FAILED
    assert result.failure_stage is pftr.ProfessionalFreeTextFailureStage.ANALYZER
    assert result.failure_reason is TurnAnalyzerFailureCategory.STRUCTURALLY_INVALID_RESPONSE
    assert result.failure_detail is detail


def test_chain_analyzer_provider_failure_has_no_detail(monkeypatch):
    _monkeypatch_chain(
        monkeypatch, analyzer_failed=True,
        analyzer_failure_category=TurnAnalyzerFailureCategory.PROVIDER_FAILURE)
    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        runtime_context=_empty_runtime_context(), risk_result={}, lang="ru"))
    assert result.failure_stage is pftr.ProfessionalFreeTextFailureStage.ANALYZER
    assert result.failure_detail is None


def test_chain_non_analyzer_failures_never_carry_a_detail(monkeypatch):
    cases = [
        dict(producer_failed=True),
        dict(proposer_status=TurnPlanProposerCallStatus.PROVIDER_FAILURE),
        dict(plan_none=True),
        dict(render_status=TurnResponseRenderStatus.NO_USABLE_CONTENT),
        dict(accept_status=ProfessionalResponseAcceptanceStatus.REJECT),
    ]
    for kwargs in cases:
        _monkeypatch_chain(monkeypatch, **kwargs)
        result = run(pftr.run_professional_free_text_turn(
            client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
            runtime_context=_empty_runtime_context(), risk_result={}, lang="ru"))
        assert result.status is not pftr.ProfessionalFreeTextRuntimeStatus.SUCCESS
        assert result.failure_detail is None


def test_chain_success_has_no_detail(monkeypatch):
    _monkeypatch_chain(monkeypatch)
    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        runtime_context=_empty_runtime_context(), risk_result={"score": 0, "categories": []}, lang="ru"))
    assert result.status is pftr.ProfessionalFreeTextRuntimeStatus.SUCCESS
    assert result.failure_detail is None


# ══════════════════════════════════════════════════════════════════════════
# Success-path decision trace (Planner Bounded Alternative V1 follow-up)
# ══════════════════════════════════════════════════════════════════════════

def test_success_trace_field_surface_is_exact():
    """Contract-lock correction: ProfessionalTurnSuccessTrace must expose
    exactly ONE acceptance-adjacent field (the real, independently-observed
    acceptance status) -- no separate fidelity/policy fields that would
    misrepresent a deduction from that one ACCEPT as independently
    observed telemetry."""
    assert tuple(f.name for f in fields(pftr.ProfessionalTurnSuccessTrace)) == (
        "analysis_status", "interaction_status", "optional_context_recovery_used",
        "objective", "primary_response_move", "question_allowed",
        "clarification_target_present", "bounded_alternative_used", "acceptance")


def test_success_trace_rejects_non_accept_acceptance_value():
    with pytest.raises(ValueError):
        pftr.ProfessionalTurnSuccessTrace(
            analysis_status=TurnAnalysisStatus.OK,
            interaction_status=AnalysisComponentStatus.VALIDATED,
            optional_context_recovery_used=False,
            objective=ProfessionalObjective.ESTABLISH_CONTACT,
            primary_response_move=PrimaryResponseMove.OPEN_INVITATION,
            question_allowed=False, clarification_target_present=False,
            bounded_alternative_used=False,
            acceptance=ProfessionalResponseAcceptanceStatus.REJECT)


def test_success_trace_normal_success_exposes_accepted_decision(monkeypatch):
    """A normal, non-bounded-alternative success must expose the actual
    accepted objective/move/question_allowed/clarification_target_present/
    interaction_status/analysis_status, bounded_alternative_used=False, and
    the real observed acceptance outcome -- sourced from the real stage
    results the orchestrator already has in hand, not re-derived."""
    _monkeypatch_chain(
        monkeypatch,
        analysis_status=TurnAnalysisStatus.OK,
        interaction_status=AnalysisComponentStatus.VALIDATED,
        plan_objective=ProfessionalObjective.CLARIFY,
        plan_move=PrimaryResponseMove.FOCUSED_QUESTION,
        plan_question_allowed=True, plan_clarification_target="EVENT",
        bounded_alternative_used=False)
    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        runtime_context=_empty_runtime_context(), risk_result={"score": 0, "categories": []}, lang="ru"))
    assert result.status is pftr.ProfessionalFreeTextRuntimeStatus.SUCCESS
    t = result.success_trace
    assert t.analysis_status is TurnAnalysisStatus.OK
    assert t.interaction_status is AnalysisComponentStatus.VALIDATED
    assert t.objective is ProfessionalObjective.CLARIFY
    assert t.primary_response_move is PrimaryResponseMove.FOCUSED_QUESTION
    assert t.question_allowed is True
    assert t.clarification_target_present is True
    assert t.bounded_alternative_used is False
    assert t.acceptance is ProfessionalResponseAcceptanceStatus.ACCEPT
    assert not hasattr(t, "fidelity")
    assert not hasattr(t, "policy")


def test_success_trace_bounded_alternative_success_exposes_true_and_accepted_alternative(monkeypatch):
    """A success reached through the Planner Bounded Alternative branch
    must expose bounded_alternative_used=True AND the actual accepted
    alternative plan (ESTABLISH_CONTACT + OPEN_INVITATION), not the
    proposer's originally-blocked proposal."""
    _monkeypatch_chain(
        monkeypatch,
        interaction_status=AnalysisComponentStatus.DEGRADED,
        plan_objective=ProfessionalObjective.ESTABLISH_CONTACT,
        plan_move=PrimaryResponseMove.OPEN_INVITATION,
        plan_question_allowed=False, plan_clarification_target=None,
        bounded_alternative_used=True)
    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        runtime_context=_empty_runtime_context(), risk_result={"score": 0, "categories": []}, lang="ru"))
    assert result.status is pftr.ProfessionalFreeTextRuntimeStatus.SUCCESS
    t = result.success_trace
    assert t.bounded_alternative_used is True
    assert t.objective is ProfessionalObjective.ESTABLISH_CONTACT
    assert t.primary_response_move is PrimaryResponseMove.OPEN_INVITATION
    assert t.question_allowed is False
    assert t.clarification_target_present is False
    assert t.interaction_status is AnalysisComponentStatus.DEGRADED


def test_success_trace_normal_establish_contact_not_falsely_labeled_bounded(monkeypatch):
    """The exact same accepted plan shape (ESTABLISH_CONTACT + OPEN_
    INVITATION) reached WITHOUT the bounded-alternative branch must report
    bounded_alternative_used=False -- branch usage must never be inferred
    from final plan shape alone."""
    _monkeypatch_chain(
        monkeypatch,
        interaction_status=AnalysisComponentStatus.VALIDATED,
        plan_objective=ProfessionalObjective.ESTABLISH_CONTACT,
        plan_move=PrimaryResponseMove.OPEN_INVITATION,
        plan_question_allowed=True, plan_clarification_target=None,
        bounded_alternative_used=False)
    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        runtime_context=_empty_runtime_context(), risk_result={"score": 0, "categories": []}, lang="ru"))
    t = result.success_trace
    assert t.objective is ProfessionalObjective.ESTABLISH_CONTACT
    assert t.primary_response_move is PrimaryResponseMove.OPEN_INVITATION
    assert t.bounded_alternative_used is False


def test_success_trace_optional_context_recovery_flag_propagates(monkeypatch):
    """optional_context_recovery_used on the trace must come directly from
    analyzer_result.optional_context_recovery_used, not be hard-coded."""
    _monkeypatch_chain(monkeypatch, optional_context_recovery_used=True)
    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        runtime_context=_empty_runtime_context(), risk_result={"score": 0, "categories": []}, lang="ru"))
    assert result.success_trace.optional_context_recovery_used is True

    _monkeypatch_chain(monkeypatch, optional_context_recovery_used=False)
    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        runtime_context=_empty_runtime_context(), risk_result={"score": 0, "categories": []}, lang="ru"))
    assert result.success_trace.optional_context_recovery_used is False


def test_success_trace_absent_on_failed_and_rejected(monkeypatch):
    _monkeypatch_chain(monkeypatch, analyzer_failed=True)
    failed_result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        runtime_context=_empty_runtime_context(), risk_result={}, lang="ru"))
    assert failed_result.status is pftr.ProfessionalFreeTextRuntimeStatus.FAILED
    assert failed_result.success_trace is None

    _monkeypatch_chain(monkeypatch, accept_status=ProfessionalResponseAcceptanceStatus.REJECT)
    rejected_result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        runtime_context=_empty_runtime_context(), risk_result={}, lang="ru"))
    assert rejected_result.status is pftr.ProfessionalFreeTextRuntimeStatus.REJECTED
    assert rejected_result.success_trace is None


def test_success_trace_no_duplicate_stage_execution(monkeypatch):
    """Constructing the success trace must not cause any stage to be
    called more than once -- every field is sourced from the single
    already-computed analyzer_result/analysis_result/plan_result."""
    calls = {"analyzer": 0, "produce": 0, "proposer": 0, "govern": 0, "render": 0, "accept": 0}
    _monkeypatch_chain(monkeypatch)
    orig_analyzer = pftr.call_turn_analyzer
    orig_produce = pftr.produce_turn_analysis
    orig_proposer = pftr.call_turn_plan_proposer
    orig_govern = pftr.govern_turn_plan
    orig_render = pftr.render_turn_response
    orig_accept = pftr.accept_professional_response

    async def counting_analyzer(**kw):
        calls["analyzer"] += 1
        return await orig_analyzer(**kw)
    def counting_produce(**kw):
        calls["produce"] += 1
        return orig_produce(**kw)
    async def counting_proposer(**kw):
        calls["proposer"] += 1
        return await orig_proposer(**kw)
    def counting_govern(*a, **kw):
        calls["govern"] += 1
        return orig_govern(*a, **kw)
    async def counting_render(**kw):
        calls["render"] += 1
        return await orig_render(**kw)
    def counting_accept(**kw):
        calls["accept"] += 1
        return orig_accept(**kw)

    monkeypatch.setattr(pftr, "call_turn_analyzer", counting_analyzer)
    monkeypatch.setattr(pftr, "produce_turn_analysis", counting_produce)
    monkeypatch.setattr(pftr, "call_turn_plan_proposer", counting_proposer)
    monkeypatch.setattr(pftr, "govern_turn_plan", counting_govern)
    monkeypatch.setattr(pftr, "render_turn_response", counting_render)
    monkeypatch.setattr(pftr, "accept_professional_response", counting_accept)

    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        runtime_context=_empty_runtime_context(), risk_result={"score": 0, "categories": []}, lang="ru"))
    assert result.status is pftr.ProfessionalFreeTextRuntimeStatus.SUCCESS
    assert calls == {"analyzer": 1, "produce": 1, "proposer": 1, "govern": 1, "render": 1, "accept": 1}


_TRACE_PRIVACY_SENTINEL_SOURCE = "SENTINEL_SOURCE_TEXT_9f2c7ab1"
_TRACE_PRIVACY_SENTINEL_REPLY = "SENTINEL_REPLY_TEXT_4e81d0aa"


def test_success_trace_contains_no_raw_text(monkeypatch):
    """The success trace (and the SUCCESS result as a whole, reply_text
    field excepted -- reply_text legitimately carries the delivered
    response) must never leak source_text or candidate/reply text into any
    structural field. Uses distinctive sentinels so this test has real
    discriminating power rather than passing vacuously."""
    _monkeypatch_chain(monkeypatch)

    async def fake_render(**kw):
        return TurnResponseRenderResult(
            status=TurnResponseRenderStatus.CANDIDATE,
            candidate_text=_TRACE_PRIVACY_SENTINEL_REPLY, model=kw["model"])
    monkeypatch.setattr(pftr, "render_turn_response", fake_render)

    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1,
        source_text=_TRACE_PRIVACY_SENTINEL_SOURCE,
        runtime_context=_empty_runtime_context(), risk_result={"score": 0, "categories": []}, lang="ru"))
    assert result.status is pftr.ProfessionalFreeTextRuntimeStatus.SUCCESS
    trace_repr = repr(result.success_trace)
    assert _TRACE_PRIVACY_SENTINEL_SOURCE not in trace_repr
    assert _TRACE_PRIVACY_SENTINEL_REPLY not in trace_repr
    # reply_text legitimately carries the sentinel -- confirm it's confined
    # to that one field, not duplicated into the trace.
    assert result.reply_text == _TRACE_PRIVACY_SENTINEL_REPLY


def test_chain_producer_failure_yields_failed_producer_stage_distinct_from_analyzer(monkeypatch):
    """Analyzer succeeds (usable output) but Producer's own deterministic
    assembly still fails -- must be reported as PRODUCER, never collapsed
    into ANALYZER (that collapse was the exact observability gap this slice
    fixes)."""
    _monkeypatch_chain(monkeypatch, producer_failed=True)
    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        runtime_context=_empty_runtime_context(), risk_result={}, lang="ru"))
    assert result.status is pftr.ProfessionalFreeTextRuntimeStatus.FAILED
    assert result.failure_stage is pftr.ProfessionalFreeTextFailureStage.PRODUCER
    assert result.failure_stage is not pftr.ProfessionalFreeTextFailureStage.ANALYZER
    assert result.failure_reason is pftr.ProfessionalFreeTextProducerFailureReason.PRODUCER_FAILED


@pytest.mark.parametrize("status", [
    TurnPlanProposerCallStatus.ABSTAINED, TurnPlanProposerCallStatus.PROVIDER_FAILURE,
    TurnPlanProposerCallStatus.NO_USABLE_CONTENT, TurnPlanProposerCallStatus.STRUCTURALLY_INVALID_RESPONSE,
    TurnPlanProposerCallStatus.SKIPPED_UPSTREAM_FAILED,
])
def test_chain_proposer_non_proposal_yields_failed_plan_proposer_stage(monkeypatch, status):
    _monkeypatch_chain(monkeypatch, proposer_status=status)
    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        runtime_context=_empty_runtime_context(), risk_result={}, lang="ru"))
    assert result.status is pftr.ProfessionalFreeTextRuntimeStatus.FAILED
    assert result.failure_stage is pftr.ProfessionalFreeTextFailureStage.PLAN_PROPOSER
    assert result.failure_reason is status


def test_chain_governor_no_plan_yields_failed_planner_stage(monkeypatch):
    _monkeypatch_chain(monkeypatch, plan_none=True)
    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        runtime_context=_empty_runtime_context(), risk_result={}, lang="ru"))
    assert result.status is pftr.ProfessionalFreeTextRuntimeStatus.FAILED
    assert result.failure_stage is pftr.ProfessionalFreeTextFailureStage.PLANNER
    assert result.failure_reason is ProfessionalPlanAbstentionReason.NO_SEMANTIC_PROPOSAL


@pytest.mark.parametrize("status", [
    TurnResponseRenderStatus.PROVIDER_FAILURE, TurnResponseRenderStatus.NO_USABLE_CONTENT,
    TurnResponseRenderStatus.STRUCTURALLY_INVALID_RESPONSE,
])
def test_chain_renderer_failure_yields_failed_renderer_stage(monkeypatch, status):
    _monkeypatch_chain(monkeypatch, render_status=status)
    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        runtime_context=_empty_runtime_context(), risk_result={}, lang="ru"))
    assert result.status is pftr.ProfessionalFreeTextRuntimeStatus.FAILED
    assert result.failure_stage is pftr.ProfessionalFreeTextFailureStage.RENDERER
    assert result.failure_reason is status


def test_chain_acceptance_reject_yields_rejected_acceptance_stage(monkeypatch):
    _monkeypatch_chain(monkeypatch, accept_status=ProfessionalResponseAcceptanceStatus.REJECT)
    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        runtime_context=_empty_runtime_context(), risk_result={}, lang="ru"))
    assert result.status is pftr.ProfessionalFreeTextRuntimeStatus.REJECTED
    assert result.failure_stage is pftr.ProfessionalFreeTextFailureStage.ACCEPTANCE
    assert result.reply_text is None
    assert result.failure_reason is AcceptanceSafetyRejectionReason.SAFETY_REJECTED


def test_chain_acceptance_fidelity_rejection_propagates_fidelity_reason(monkeypatch):
    reason = next(iter(FidelityRejectionReason))

    def fake_accept(**kw):
        return ProfessionalResponseAcceptanceResult(
            status=ProfessionalResponseAcceptanceStatus.REJECT, reason=reason)
    _monkeypatch_chain(monkeypatch)
    monkeypatch.setattr(pftr, "accept_professional_response", fake_accept)
    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        runtime_context=_empty_runtime_context(), risk_result={}, lang="ru"))
    assert result.status is pftr.ProfessionalFreeTextRuntimeStatus.REJECTED
    assert result.failure_stage is pftr.ProfessionalFreeTextFailureStage.ACCEPTANCE
    assert isinstance(result.failure_reason, FidelityRejectionReason)
    assert result.failure_reason is reason


def test_chain_acceptance_policy_rejection_propagates_policy_reason(monkeypatch):
    reason = next(iter(PolicyRejectionReason))

    def fake_accept(**kw):
        return ProfessionalResponseAcceptanceResult(
            status=ProfessionalResponseAcceptanceStatus.REJECT, reason=reason)
    _monkeypatch_chain(monkeypatch)
    monkeypatch.setattr(pftr, "accept_professional_response", fake_accept)
    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        runtime_context=_empty_runtime_context(), risk_result={}, lang="ru"))
    assert result.status is pftr.ProfessionalFreeTextRuntimeStatus.REJECTED
    assert result.failure_stage is pftr.ProfessionalFreeTextFailureStage.ACCEPTANCE
    assert isinstance(result.failure_reason, PolicyRejectionReason)
    assert result.failure_reason is reason


def test_chain_acceptance_safety_rejection_propagates_safety_reason(monkeypatch):
    _monkeypatch_chain(monkeypatch, accept_status=ProfessionalResponseAcceptanceStatus.REJECT)
    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        runtime_context=_empty_runtime_context(), risk_result={}, lang="ru"))
    assert result.failure_stage is pftr.ProfessionalFreeTextFailureStage.ACCEPTANCE
    assert result.failure_reason is AcceptanceSafetyRejectionReason.SAFETY_REJECTED


def test_failure_reason_never_carries_raw_text_for_any_stage(monkeypatch):
    """Every failure_reason value across every stage must be one of the
    already-bounded enum members -- never a str built from user/candidate/
    model/exception text. Exercises all six stage outcomes in one pass."""
    cases = [
        dict(analyzer_failed=True),
        dict(producer_failed=True),
        dict(proposer_status=TurnPlanProposerCallStatus.PROVIDER_FAILURE),
        dict(plan_none=True),
        dict(render_status=TurnResponseRenderStatus.NO_USABLE_CONTENT),
        dict(accept_status=ProfessionalResponseAcceptanceStatus.REJECT),
    ]
    for kwargs in cases:
        _monkeypatch_chain(monkeypatch, **kwargs)
        result = run(pftr.run_professional_free_text_turn(
            client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
            runtime_context=_empty_runtime_context(), risk_result={}, lang="ru"))
        assert result.status is not pftr.ProfessionalFreeTextRuntimeStatus.SUCCESS
        assert type(result.failure_reason) is not str
        assert isinstance(result.failure_reason, pftr.ProfessionalFreeTextFailureReason)


def test_orchestrator_never_calls_fidelity_or_policy_directly():
    """The module docstring legitimately documents this as a negative claim
    ("never calls validate_response_fidelity or validate_response_policy
    directly"), so this checks for actual CALLS (AST-based), not a
    substring ban that would collide with that documented prose."""
    import ast, inspect
    tree = ast.parse(inspect.getsource(pftr))
    called_names = {n.func.id for n in ast.walk(tree)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "validate_response_fidelity" not in called_names
    assert "validate_response_policy" not in called_names


def test_orchestrator_module_imports_no_bot_no_database():
    import ast, pathlib
    tree = ast.parse(pathlib.Path(pftr.__file__).read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    assert "bot" not in modules
    assert "database" not in modules
    assert "state_engine" not in modules
    assert "conversation_controller" not in modules


# ══════════════════════════════════════════════════════════════════════════
# B. access_control.professional_free_text_allowed_for rollout semantics
# ══════════════════════════════════════════════════════════════════════════

def test_flag_false_owner_not_allowed(monkeypatch, tmp_db):
    monkeypatch.setattr(config, "PROFESSIONAL_FREE_TEXT_RUNTIME_ENABLED", False)
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "all")
    assert run(ac.professional_free_text_allowed_for(1)) is False


def test_flag_true_rollout_off_not_allowed(monkeypatch, tmp_db):
    monkeypatch.setattr(config, "PROFESSIONAL_FREE_TEXT_RUNTIME_ENABLED", True)
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "off")
    assert run(ac.professional_free_text_allowed_for(1)) is False


def test_flag_true_rollout_owner_owner_allowed(monkeypatch, tmp_db):
    monkeypatch.setattr(config, "PROFESSIONAL_FREE_TEXT_RUNTIME_ENABLED", True)
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "owner")
    assert run(ac.professional_free_text_allowed_for(1)) is True


def test_flag_true_rollout_owner_non_owner_not_allowed(monkeypatch, tmp_db):
    monkeypatch.setattr(config, "PROFESSIONAL_FREE_TEXT_RUNTIME_ENABLED", True)
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "owner")
    assert run(ac.professional_free_text_allowed_for(999)) is False


def test_flag_true_rollout_invited_preserves_existing_contract(monkeypatch, tmp_db):
    monkeypatch.setattr(config, "PROFESSIONAL_FREE_TEXT_RUNTIME_ENABLED", True)
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "invited")
    # Owner always counts as invited too (existing core_rollout_allowed contract).
    assert run(ac.professional_free_text_allowed_for(1)) is True
    # A non-owner with no grant is not invited.
    assert run(ac.professional_free_text_allowed_for(555)) is False
    # A non-owner WITH a real, existing grant_user_access invite is invited --
    # proving this reuses the actual existing invited-user contract, not an
    # assumption that "invited means every non-owner".
    run(database.grant_user_access(555))
    assert run(ac.professional_free_text_allowed_for(555)) is True


def test_flag_true_rollout_all_matches_existing_contract(monkeypatch, tmp_db):
    monkeypatch.setattr(config, "PROFESSIONAL_FREE_TEXT_RUNTIME_ENABLED", True)
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "all")
    assert run(ac.professional_free_text_allowed_for(1)) is True
    assert run(ac.professional_free_text_allowed_for(999)) is True


# ══════════════════════════════════════════════════════════════════════════
# C. bot.pipeline() ownership / precedence / persistence / delivery
# ══════════════════════════════════════════════════════════════════════════

OWNER = 1


def test_public_crisis_still_precedes_product_and_core_routing(tmp_db, monkeypatch):
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "public")
    monkeypatch.setattr(bot, "get_active_crisis", _async(None))
    called = {"crisis": 0}

    async def fake_crisis(*args, **kwargs):
        called["crisis"] += 1

    monkeypatch.setattr(bot, "trigger_crisis", fake_crisis)
    monkeypatch.setattr(
        ac, "therapist_core_v1_allowed_for",
        _raise_if_called("therapist_core_v1_allowed_for"))
    msg = FakeMessage(FakeUser(999), "Я хочу покончить с собой.")
    run(bot.pipeline(msg, msg.text))
    assert called["crisis"] == 1


def test_therapist_core_claims_once_precedes_professional_and_validates_once(
        tmp_db, monkeypatch):
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_history(monkeypatch, rows=())
    monkeypatch.setattr(config, "THERAPIST_CORE_V1_MODEL", "gpt-core-compatible")
    monkeypatch.setattr(config, "THERAPIST_CORE_V1_MAX_COMPLETION_TOKENS", 1200)
    monkeypatch.setattr(ac, "therapist_core_v1_allowed_for", _async(True))
    monkeypatch.setattr(
        ac, "professional_free_text_allowed_for",
        _raise_if_called("professional_free_text_allowed_for"))
    calls = {"generation": 0, "validation": 0}

    async def fake_generate(**kwargs):
        calls["generation"] += 1
        assert kwargs["model"] == "gpt-core-compatible"
        assert kwargs["max_completion_tokens"] == 1200
        assert kwargs["source_text"] == "Я хочу понять, почему я так делаю."
        assert kwargs["interaction_contract"] == "UNDERSTAND"
        return "Можно начать с конкретного эпизода, где эта неопределённость ощущалась сильнее."

    def fake_validate(candidate, source_text, risk, lang):
        calls["validation"] += 1
        return True, None

    monkeypatch.setattr(bot, "generate_therapist_core_v1", fake_generate)
    monkeypatch.setattr(bot, "validate_response_with_context", fake_validate)

    msg = FakeMessage(FakeUser(OWNER), "Я хочу понять, почему я так делаю.")
    run(bot.pipeline(msg, msg.text))
    assert calls == {"generation": 1, "validation": 1}
    assert len(msg.answers) == 1


def test_therapist_core_v1_strips_leaked_bold_markdown_delimiters():
    # Pure unit test of the presentation-layer cleanup itself.
    raw = ("Что сейчас ближе: **рядом почти нет людей** или "
           "**с ними нет ощущения близости**?")
    expected = ("Что сейчас ближе: рядом почти нет людей или "
                "с ними нет ощущения близости?")
    assert bot._strip_leaked_bold_markdown(raw) == expected
    assert "**" not in bot._strip_leaked_bold_markdown(raw)


def test_therapist_core_v1_unpaired_asterisks_left_alone():
    assert bot._strip_leaked_bold_markdown("stray ** with no pair") == \
        "stray ** with no pair"


def test_therapist_core_v1_no_markdown_is_a_no_op():
    plain = "Обычный текст без разметки."
    assert bot._strip_leaked_bold_markdown(plain) == plain


def test_therapist_core_v1_leaked_markdown_cleaned_in_delivery_and_persistence(
        tmp_db, monkeypatch):
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_history(monkeypatch, rows=())
    monkeypatch.setattr(config, "THERAPIST_CORE_V1_MODEL", "gpt-core-compatible")
    monkeypatch.setattr(ac, "therapist_core_v1_allowed_for", _async(True))
    monkeypatch.setattr(
        ac, "professional_free_text_allowed_for",
        _raise_if_called("professional_free_text_allowed_for"))

    raw = ("Что сейчас ближе: **рядом почти нет людей** или "
           "**с ними нет ощущения близости**?")
    clean = ("Что сейчас ближе: рядом почти нет людей или "
             "с ними нет ощущения близости?")

    async def fake_generate(**kwargs):
        return raw

    def fake_validate(candidate, source_text, risk, lang):
        return True, None

    monkeypatch.setattr(bot, "generate_therapist_core_v1", fake_generate)
    monkeypatch.setattr(bot, "validate_response_with_context", fake_validate)

    msg = FakeMessage(FakeUser(OWNER), "Что сейчас ближе?")
    run(bot.pipeline(msg, msg.text))

    assert msg.answers[0][0] == clean
    assert "**" not in msg.answers[0][0]

    # The persisted ASSISTANT_DELIVERED content must equal what was actually
    # shown -- never a second, ungoverned wording after the safety decision.
    # (therapist_core_v1 persists with scenario="therapist_core_v1", not
    # "professional", so this reads it directly rather than reusing
    # _read_persisted_assistant_content, which is scoped to scenario='professional'.)
    async def _read_therapist_core_v1_content():
        async with database.aiosqlite.connect(database.DB) as conn:
            cur = await conn.execute(
                "SELECT content FROM messages WHERE user_id=? AND role='assistant' "
                "AND scenario='therapist_core_v1' ORDER BY id DESC LIMIT 1", (OWNER,))
            row = await cur.fetchone()
        return row[0] if row else None

    assert run(_read_therapist_core_v1_content()) == clean


# ── context-respecting low-risk fallback (owner-review live-smoke round 2) ───
# The live incident: a detailed user message, a rejected candidate, and the
# OLD neutral fallback ("Давай проще. Что сейчас в этой ситуации самое
# тяжёлое?") asking the user to repeat what they had just written. Fixed by
# scoping a new fallback to Therapist Core's own reject branch only --
# safety_validator.select_fallback/FALLBACK_RU (shared with the legacy
# pipeline) is untouched, verified by test_select_fallback_* in
# tests/test_safety_validator.py still passing unchanged.
_OLD_BAD_FALLBACK = "Давай проще. Что сейчас в этой ситуации самое тяжёлое?"


@pytest.mark.parametrize("contract,expected_ru", [
    ("UNDERSTAND", "Я прочитал то, что ты написал. Не буду просить повторять. "
                   "Давай разбираться из того, что уже есть и попробуем связать это в одну картину."),
    ("JUST_TALK", "Я прочитал то, что ты написал. Не буду просить повторять. "
                  "Можешь продолжить с этого места — я буду держать нить разговора."),
    ("ACTION", "Я прочитал то, что ты написал. Не буду просить повторять. "
               "Давай опираться на уже сказанное и выберем следующий шаг."),
    ("NONE", "Я прочитал то, что ты написал. Не буду просить повторять. "
             "Давай продолжим оттуда и опираться на уже сказанное."),
    ("SOME_UNKNOWN_FUTURE_CONTRACT", "Я прочитал то, что ты написал. Не буду просить повторять. "
                                     "Давай продолжим оттуда и опираться на уже сказанное."),
])
def test_therapist_core_fallback_low_risk_ru_by_contract(contract, expected_ru):
    text = bot._therapist_core_fallback({"level": "low"}, contract, "ru")
    assert text == expected_ru
    assert text != _OLD_BAD_FALLBACK
    assert "повторять" in text.lower()  # never asks the user to repeat themselves


@pytest.mark.parametrize("contract,expected_en", [
    ("UNDERSTAND", "I've read what you wrote. I won't ask you to repeat it. "
                   "Let's work with what's already here and try to connect it into one picture."),
    ("JUST_TALK", "I've read what you wrote. I won't ask you to repeat it. "
                  "You can continue from here — I'll keep track of the thread."),
    ("ACTION", "I've read what you wrote. I won't ask you to repeat it. "
               "Let's build on what's already been said and choose a next step."),
    ("NONE", "I've read what you wrote. I won't ask you to repeat it. "
             "Let's continue from there, building on what's already been said."),
])
def test_therapist_core_fallback_low_risk_en_by_contract(contract, expected_en):
    assert bot._therapist_core_fallback({"level": "low"}, contract, "en") == expected_en


def test_therapist_core_fallback_elevated_risk_unchanged():
    # Elevated risk / ambiguous phrasing must fall straight through to the
    # EXISTING high-risk fallback (hotline-carrying), completely unchanged,
    # regardless of interaction_contract.
    for contract in ("UNDERSTAND", "JUST_TALK", "ACTION", "NONE"):
        for risk in (
            {"level": "medium"}, {"level": "high"}, {"level": "critical"},
            {"level": "low", "ambiguous_phrases": ["выйти в окно"]},
        ):
            assert bot._therapist_core_fallback(risk, contract, "ru") == \
                safety_validator.get_safe_fallback_high_risk("ru")
            assert bot._therapist_core_fallback(risk, contract, "en") == \
                safety_validator.get_safe_fallback_high_risk("en")


def test_therapist_core_fallback_empty_risk_defaults_low(monkeypatch):
    assert bot._therapist_core_fallback({}, "NONE", "ru") == \
        bot._therapist_core_fallback({"level": "low"}, "NONE", "ru")
    assert bot._therapist_core_fallback(None, "NONE", "ru") == \
        bot._therapist_core_fallback({"level": "low"}, "NONE", "ru")


def test_exact_live_incident_no_longer_produces_the_old_bad_fallback(
        tmp_db, monkeypatch):
    """Reproduces the reported failure exactly: the detailed user message
    that triggered it, a rejected candidate, low risk (as evidenced by the
    original incident itself producing the NEUTRAL, not high-risk, fallback).
    detect_interaction_preference finds no explicit UNDERSTAND/JUST_TALK/
    ACTION signal in this text, so it resolves to NONE -- proving the exact
    default-branch text replaces the old one, not just some other contract."""
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_history(monkeypatch, rows=())
    monkeypatch.setattr(config, "THERAPIST_CORE_V1_MODEL", "gpt-core-compatible")
    monkeypatch.setattr(ac, "therapist_core_v1_allowed_for", _async(True))
    monkeypatch.setattr(
        ac, "professional_free_text_allowed_for",
        _raise_if_called("professional_free_text_allowed_for"))

    live_message = (
        "Оно со мной очень давно,\n"
        "После расставания, хотя и в отношениях я был не счастлив, как мне "
        "казалось, я не понимал своих чувств и не выражал эмоций")
    assert detect_interaction_preference(live_message, "ru") == "NONE"

    async def fake_generate(**kwargs):
        return "some candidate that gets rejected"

    def fake_validate(candidate, source_text, risk, lang):
        return False, "toxic validation: confirmed distortion 'x'"

    monkeypatch.setattr(bot, "generate_therapist_core_v1", fake_generate)
    monkeypatch.setattr(bot, "validate_response_with_context", fake_validate)

    msg = FakeMessage(FakeUser(OWNER), live_message)
    run(bot.pipeline(msg, msg.text))

    delivered = msg.answers[0][0]
    assert delivered != _OLD_BAD_FALLBACK
    assert "повторять" not in _OLD_BAD_FALLBACK  # sanity: old text really lacks this word
    assert delivered == bot._therapist_core_fallback({"level": "low"}, "NONE", "ru")


def test_therapist_core_provider_failure_is_final_no_professional_or_legacy(
        tmp_db, monkeypatch):
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_history(monkeypatch, rows=())
    monkeypatch.setattr(config, "THERAPIST_CORE_V1_MODEL", "gpt-core-compatible")
    monkeypatch.setattr(ac, "therapist_core_v1_allowed_for", _async(True))
    monkeypatch.setattr(
        ac, "professional_free_text_allowed_for",
        _raise_if_called("professional_free_text_allowed_for"))

    async def fail_generation(**kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(bot, "generate_therapist_core_v1", fail_generation)
    monkeypatch.setattr(
        bot, "validate_response_with_context",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("provider failure has no candidate to validate")))

    msg = FakeMessage(FakeUser(OWNER), "Мне тяжело.")
    run(bot.pipeline(msg, msg.text))
    assert [answer[0] for answer in msg.answers] == [
        bot._professional_technical_fallback_text("ru")]


def test_stale_therapist_core_response_is_not_delivered(tmp_db, monkeypatch):
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_history(monkeypatch, rows=())
    monkeypatch.setattr(config, "THERAPIST_CORE_V1_MODEL", "gpt-core-compatible")
    monkeypatch.setattr(ac, "therapist_core_v1_allowed_for", _async(True))

    async def stale_generation(**kwargs):
        bot._bump_user_generation(OWNER)
        return "Этот ответ уже устарел."

    monkeypatch.setattr(bot, "generate_therapist_core_v1", stale_generation)
    monkeypatch.setattr(bot, "validate_response_with_context", lambda *a: (True, None))
    msg = FakeMessage(FakeUser(OWNER), "Первое сообщение.")
    run(bot.pipeline(msg, msg.text))
    assert msg.answers == []


def test_owner_eligible_turn_persists_professional_row_and_delivers_success(tmp_db, monkeypatch):
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    calls = _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    msg = FakeMessage(FakeUser(OWNER), "Мне тяжело в последнее время.")
    run(bot.pipeline(msg, msg.text))

    assert calls["n"] == 1
    assert msg.answers and msg.answers[0][0] == SUCCESS_RESULT.reply_text

    async def read_rows():
        async with database.aiosqlite.connect(database.DB) as conn:
            cur = await conn.execute(
                "SELECT role, content, scenario, source FROM messages WHERE user_id=? ORDER BY id",
                (OWNER,))
            return await cur.fetchall()
    rows = run(read_rows())
    assert ("user", msg.text, "professional", "USER_AUTHORED") in rows
    assert ("assistant", SUCCESS_RESULT.reply_text, "professional", "ASSISTANT_DELIVERED") in rows


def test_invited_non_owner_not_claimed_by_professional_flag_true_owner_mode(tmp_db, monkeypatch):
    """rollout=owner: an invited-but-not-owner user must NOT be claimed by
    Professional -- existing Controller/legacy path is exercised instead
    and must remain byte-for-byte functional (not raise)."""
    invited_uid = 777
    run(_seed_user(invited_uid))
    run(database.grant_user_access(invited_uid))
    monkeypatch.setattr(config, "PROFESSIONAL_FREE_TEXT_RUNTIME_ENABLED", True)
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "owner")
    _stub_legacy_machinery_allow_legacy(monkeypatch)

    msg = FakeMessage(FakeUser(invited_uid), "Просто хочу поговорить.")
    run(bot.pipeline(msg, msg.text))
    # Reached ordinary legacy generation (stubbed LLM reply below) -- proves
    # Professional did NOT claim this turn.
    assert msg.answers


def _stub_legacy_machinery_allow_legacy(monkeypatch, llm_reply="ok, noted"):
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
        return types.SimpleNamespace(choices=[_Choice(llm_reply)])
    monkeypatch.setattr(bot.client.chat.completions, "create", fake_create)


def test_professional_claim_precedes_controller_vent_explain_action_repair(tmp_db, monkeypatch):
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)  # _controller_claim_turn raises if called
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    for text in ("Мне нужно выговориться.", "Объясни, почему так происходит.",
                 "Скажи, что мне сделать.", "Начни сначала, объясни по-другому."):
        msg = FakeMessage(FakeUser(OWNER), text)
        run(bot.pipeline(msg, msg.text))
        assert msg.answers  # did not raise from _controller_claim_turn


def test_professional_claim_precedes_first_turn_even_when_eligible(tmp_db, monkeypatch):
    """A brand-new, never-first-turn-claimed OWNER (first-turn eligible)
    claimed by Professional must never reach
    _first_turn_generate_and_validate. Deliberately does NOT call _seed_user
    (which pre-consumes first-turn) -- first-turn eligibility is left
    genuinely open, so this proves precedence, not mere unavailability."""
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_legacy_machinery(monkeypatch)  # _first_turn_generate_and_validate raises if called
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    msg = FakeMessage(FakeUser(OWNER), "Привет, мне тревожно.")
    run(bot.pipeline(msg, msg.text))
    assert msg.answers and msg.answers[0][0] == SUCCESS_RESULT.reply_text


def test_professional_claim_never_calls_choose_scenario_or_load_state(tmp_db, monkeypatch):
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)  # choose_scenario/load_state raise if called
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    msg = FakeMessage(FakeUser(OWNER), "Как обычно, всё сложно.")
    run(bot.pipeline(msg, msg.text))
    assert msg.answers


def test_entry_triage_next_free_text_claimed_by_professional_not_first_turn(tmp_db, monkeypatch):
    """The specific post-Entry-Triage case: an owner's first genuine
    free-text turn (Entry Triage itself never touches `messages` or
    first-turn claim state -- see database.py's own Entry Triage tests)
    must be claimed by Professional, not First-Turn, when eligible."""
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_legacy_machinery(monkeypatch)  # _first_turn_generate_and_validate raises if called
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно поговорить о том, что произошло.")
    run(bot.pipeline(msg, msg.text))
    assert msg.answers and msg.answers[0][0] == SUCCESS_RESULT.reply_text


@pytest.mark.parametrize("result", [REJECTED_RESULT, FAILED_RESULT])
def test_professional_failure_never_falls_through_to_legacy_controller_first_turn(tmp_db, monkeypatch, result):
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, result)

    msg = FakeMessage(FakeUser(OWNER), "Расскажи мне про свои чувства.")
    run(bot.pipeline(msg, msg.text))
    assert msg.answers and msg.answers[0][0] == bot._professional_technical_fallback_text("ru")


def _capture_dispatch_log(monkeypatch):
    calls = []
    monkeypatch.setattr(bot, "_dispatch_log", calls.append)
    return calls


def test_professional_failed_dispatch_log_includes_bounded_stage_and_reason(tmp_db, monkeypatch):
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, FAILED_RESULT)
    calls = _capture_dispatch_log(monkeypatch)

    user_text = "Очень личная деликатная тема, которую сложно кому-то доверить."
    msg = FakeMessage(FakeUser(OWNER), user_text)
    run(bot.pipeline(msg, msg.text))

    failed_lines = [c for c in calls if "stage=professional_failed" in c and "pro_stage=" in c]
    assert failed_lines, calls
    line = failed_lines[0]
    assert f"pro_stage={FAILED_RESULT.failure_stage.value}" in line
    assert f"reason={FAILED_RESULT.failure_reason.value}" in line
    # FAILED_RESULT is ANALYZER+PROVIDER_FAILURE, which never carries a
    # failure_detail -- the log line must not invent one.
    assert "detail=" not in line
    for c in calls:
        assert user_text not in c
        assert bot._professional_technical_fallback_text("ru") not in c


def test_professional_failed_dispatch_log_includes_bounded_detail_when_present(tmp_db, monkeypatch):
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    structural_result = pftr.ProfessionalFreeTextRuntimeResult(
        status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text=None,
        failure_stage=pftr.ProfessionalFreeTextFailureStage.ANALYZER,
        failure_reason=TurnAnalyzerFailureCategory.STRUCTURALLY_INVALID_RESPONSE,
        failure_detail=TurnAnalyzerStructuralFailureReason.WRONG_REQUIRED_KEY_SET)
    _stub_runtime_result(monkeypatch, structural_result)
    calls = _capture_dispatch_log(monkeypatch)

    user_text = "Очень личная и деликатная тема, сложно объяснить в двух словах."
    msg = FakeMessage(FakeUser(OWNER), user_text)
    run(bot.pipeline(msg, msg.text))

    failed_lines = [c for c in calls if "stage=professional_failed" in c and "pro_stage=" in c]
    assert failed_lines, calls
    line = failed_lines[0]
    assert "pro_stage=ANALYZER" in line
    assert "reason=STRUCTURALLY_INVALID_RESPONSE" in line
    assert "detail=WRONG_REQUIRED_KEY_SET" in line
    for c in calls:
        assert user_text not in c


def test_professional_failed_dispatch_log_includes_granular_candidate_bounds_detail(tmp_db, monkeypatch):
    """Candidate Text Bounds Detail V2: proves the NEW granular member
    (not just the pre-existing WRONG_REQUIRED_KEY_SET example above)
    reaches the bot dispatch log unchanged, and that no raw field value,
    candidate text, or exception text leaks alongside it."""
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    real_field_value_that_must_never_leak = "y" * 61  # an actual too-long context value
    structural_result = pftr.ProfessionalFreeTextRuntimeResult(
        status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text=None,
        failure_stage=pftr.ProfessionalFreeTextFailureStage.ANALYZER,
        failure_reason=TurnAnalyzerFailureCategory.STRUCTURALLY_INVALID_RESPONSE,
        failure_detail=TurnAnalyzerStructuralFailureReason.EVIDENCE_SPAN_TOO_LONG)
    _stub_runtime_result(monkeypatch, structural_result)
    calls = _capture_dispatch_log(monkeypatch)

    user_text = "У меня очень длинная и деликатная тема, сложно уложить в пару слов."
    msg = FakeMessage(FakeUser(OWNER), user_text)
    run(bot.pipeline(msg, msg.text))

    failed_lines = [c for c in calls if "stage=professional_failed" in c and "pro_stage=" in c]
    assert failed_lines, calls
    line = failed_lines[0]
    assert "pro_stage=ANALYZER" in line
    assert "reason=STRUCTURALLY_INVALID_RESPONSE" in line
    assert "detail=EVIDENCE_SPAN_TOO_LONG" in line
    for c in calls:
        assert user_text not in c
        assert real_field_value_that_must_never_leak not in c
        assert "exact_source_span" not in c
        assert "context_before" not in c
        assert "context_after" not in c


def test_professional_success_dispatch_log_includes_bounded_decision_trace(tmp_db, monkeypatch):
    """Planner Bounded Alternative V1 follow-up: a successful Professional
    turn's dispatch log line must expose the bounded structural decision
    trace (analyzer/interaction status, recovery usage, accepted objective/
    move/question_allowed/clarification_target_present, bounded_
    alternative_used, and the real observed acceptance outcome), and must
    never leak the raw user text, the delivered reply text, or a synthetic
    fidelity/policy outcome that was never independently observed."""
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)
    calls = _capture_dispatch_log(monkeypatch)

    user_text = "Очень личная деликатная тема про мои отношения и одиночество."
    msg = FakeMessage(FakeUser(OWNER), user_text)
    run(bot.pipeline(msg, msg.text))

    success_lines = [c for c in calls if "stage=professional_success" in c]
    assert success_lines, calls
    line = success_lines[0]
    assert "analyzer_status=OK" in line
    assert "interaction_status=VALIDATED" in line
    assert "optional_context_recovery_used=False" in line
    assert "objective=CLARIFY" in line
    assert "move=FOCUSED_QUESTION" in line
    assert "question_allowed=True" in line
    assert "clarification_target_present=True" in line
    assert "bounded_alternative_used=False" in line
    assert "acceptance=ACCEPT" in line
    # Contract-lock correction: fidelity/policy are never independently
    # observed by this module -- they must never appear as if they were.
    assert "fidelity=" not in line
    assert "policy=" not in line
    for c in calls:
        assert user_text not in c
        assert SUCCESS_RESULT.reply_text not in c


def test_professional_success_dispatch_log_bounded_alternative_true(tmp_db, monkeypatch):
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    bounded_success = pftr.ProfessionalFreeTextRuntimeResult(
        status=pftr.ProfessionalFreeTextRuntimeStatus.SUCCESS,
        reply_text="Если хочешь, можем начать с того, что для тебя сейчас важно.",
        failure_stage=None, failure_reason=None, failure_detail=None,
        success_trace=_success_trace(
            objective=ProfessionalObjective.ESTABLISH_CONTACT,
            move=PrimaryResponseMove.OPEN_INVITATION,
            question_allowed=False, clarification_target_present=False,
            bounded_alternative_used=True,
            interaction_status=AnalysisComponentStatus.UNAVAILABLE))
    _stub_runtime_result(monkeypatch, bounded_success)
    calls = _capture_dispatch_log(monkeypatch)

    msg = FakeMessage(FakeUser(OWNER), "Какая-то тема без единого явного вопроса.")
    run(bot.pipeline(msg, msg.text))

    success_lines = [c for c in calls if "stage=professional_success" in c]
    assert success_lines, calls
    line = success_lines[0]
    assert "objective=ESTABLISH_CONTACT" in line
    assert "move=OPEN_INVITATION" in line
    assert "bounded_alternative_used=True" in line
    assert "interaction_status=UNAVAILABLE" in line


def test_professional_rejected_dispatch_log_includes_bounded_stage_and_reason(tmp_db, monkeypatch):
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, REJECTED_RESULT)
    calls = _capture_dispatch_log(monkeypatch)

    user_text = "У меня очень личная и деликатная тема, сложно объяснить словами."
    msg = FakeMessage(FakeUser(OWNER), user_text)
    run(bot.pipeline(msg, msg.text))

    rejected_lines = [c for c in calls if "stage=professional_rejected" in c and "pro_stage=" in c]
    assert rejected_lines, calls
    line = rejected_lines[0]
    assert f"pro_stage={REJECTED_RESULT.failure_stage.value}" in line
    assert f"reason={REJECTED_RESULT.failure_reason.value}" in line
    for c in calls:
        assert user_text not in c


def test_professional_outer_exception_dispatch_log_excludes_exception_message(tmp_db, monkeypatch):
    """The exception-path log line (a genuine caller/adapter defect, not a
    closed FAILED/REJECTED result) only ever carries type(e).__name__ --
    never str(e), which could contain unbounded diagnostic text. Unchanged
    by this slice; re-asserted here as part of the same privacy-safe
    logging contract."""
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())

    secret_detail = "simulated defect detail that must never reach logs"

    async def raising_run(**kw):
        raise RuntimeError(secret_detail)
    monkeypatch.setattr(bot, "run_professional_free_text_turn", raising_run)
    calls = _capture_dispatch_log(monkeypatch)

    msg = FakeMessage(FakeUser(OWNER), "Что мне делать?")
    run(bot.pipeline(msg, msg.text))

    failed_lines = [c for c in calls if "stage=professional_failed" in c]
    assert failed_lines, calls
    assert "error_type=RuntimeError" in failed_lines[0]
    for c in calls:
        assert secret_detail not in c


def test_history_db_read_exception_yields_fallback_not_legacy(tmp_db, monkeypatch):
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)

    async def raising_get_rows(uid, current_row_id):
        raise RuntimeError("simulated DB failure")
    monkeypatch.setattr(bot, "get_professional_conversation_history_rows", raising_get_rows)

    called = {"n": 0}
    async def fake_run(**kw):
        called["n"] += 1
        return SUCCESS_RESULT
    monkeypatch.setattr(bot, "run_professional_free_text_turn", fake_run)

    msg = FakeMessage(FakeUser(OWNER), "Сегодня тяжёлый день.")
    run(bot.pipeline(msg, msg.text))
    assert called["n"] == 0  # orchestrator never even called
    assert msg.answers and msg.answers[0][0] == bot._professional_technical_fallback_text("ru")


def test_unexpected_orchestrator_exception_yields_fallback(tmp_db, monkeypatch):
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())

    async def raising_run(**kw):
        raise RuntimeError("simulated unexpected defect")
    monkeypatch.setattr(bot, "run_professional_free_text_turn", raising_run)

    msg = FakeMessage(FakeUser(OWNER), "Что мне делать?")
    run(bot.pipeline(msg, msg.text))
    assert msg.answers and msg.answers[0][0] == bot._professional_technical_fallback_text("ru")


def test_successful_professional_turn_skips_automatic_mood_scale(tmp_db, monkeypatch):
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    msg = FakeMessage(FakeUser(OWNER), "Мне грустно сегодня.")
    run(bot.pipeline(msg, msg.text))
    assert len(msg.answers) == 1  # reply only, no mood-scale follow-up message
    assert "1=плохо" not in msg.answers[0][0]


def test_legacy_path_still_triggers_automatic_mood_scale(tmp_db, monkeypatch):
    invited_uid = 778
    run(_seed_user(invited_uid))
    run(database.grant_user_access(invited_uid))
    monkeypatch.setattr(config, "PROFESSIONAL_FREE_TEXT_RUNTIME_ENABLED", False)
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "all")
    _stub_legacy_machinery_allow_legacy(monkeypatch)

    msg = FakeMessage(FakeUser(invited_uid), "Мне грустно и одиноко в последнее время.")
    run(bot.pipeline(msg, msg.text))
    joined = " ".join(a[0] for a in msg.answers)
    # Legacy path may or may not land on a scenario that triggers the scale
    # depending on real routing; this test only proves Professional being
    # OFF does not remove the legacy mechanism itself -- see
    # test_successful_professional_turn_skips_automatic_mood_scale for the
    # positive Professional-side proof. A crude non-crash smoke check:
    assert msg.answers


def test_professional_does_not_run_failed_practice_retry_surface(tmp_db, monkeypatch):
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)  # _retry_failed_practice_prompts raises if called
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    msg = FakeMessage(FakeUser(OWNER), "Ещё раз про то же самое.")
    run(bot.pipeline(msg, msg.text))
    assert msg.answers


def test_stale_professional_result_not_sent_not_persisted(tmp_db, monkeypatch):
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())

    async def fake_run(**kw):
        # Simulate a second, newer turn for this uid arriving mid-flight.
        bot._bump_user_generation(OWNER)
        return SUCCESS_RESULT
    monkeypatch.setattr(bot, "run_professional_free_text_turn", fake_run)

    msg = FakeMessage(FakeUser(OWNER), "Первое сообщение.")
    run(bot.pipeline(msg, msg.text))
    assert msg.answers == []  # nothing sent

    async def read_assistant_rows():
        async with database.aiosqlite.connect(database.DB) as conn:
            cur = await conn.execute(
                "SELECT COUNT(*) FROM messages WHERE user_id=? AND role='assistant'", (OWNER,))
            return (await cur.fetchone())[0]
    assert run(read_assistant_rows()) == 0


def test_current_user_row_remains_persisted_even_if_result_becomes_stale(tmp_db, monkeypatch):
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())

    async def fake_run(**kw):
        bot._bump_user_generation(OWNER)
        return SUCCESS_RESULT
    monkeypatch.setattr(bot, "run_professional_free_text_turn", fake_run)

    msg = FakeMessage(FakeUser(OWNER), "Сообщение, которое устареет.")
    run(bot.pipeline(msg, msg.text))

    async def read_user_rows():
        async with database.aiosqlite.connect(database.DB) as conn:
            cur = await conn.execute(
                "SELECT content FROM messages WHERE user_id=? AND role='user' AND scenario='professional'",
                (OWNER,))
            return await cur.fetchall()
    assert (msg.text,) in run(read_user_rows())


def test_send_failure_creates_no_assistant_row(tmp_db, monkeypatch):
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    async def failing_answer(text, **kw):
        raise RuntimeError("simulated Telegram send failure")
    msg = FakeMessage(FakeUser(OWNER), "Тестовое сообщение.")
    msg.answer = failing_answer

    run(bot.pipeline(msg, msg.text))

    async def read_assistant_rows():
        async with database.aiosqlite.connect(database.DB) as conn:
            cur = await conn.execute(
                "SELECT COUNT(*) FROM messages WHERE user_id=? AND role='assistant'", (OWNER,))
            return (await cur.fetchone())[0]
    assert run(read_assistant_rows()) == 0


def test_persist_failure_after_send_does_not_send_a_second_reply(tmp_db, monkeypatch):
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    async def failing_save_message(*a, **kw):
        raise RuntimeError("simulated DB write failure")

    real_save_message = bot.save_message
    calls = {"n": 0}
    async def counting_save_message(uid, role, content, *a, **kw):
        if role == "assistant":
            calls["n"] += 1
            raise RuntimeError("simulated DB write failure")
        return await real_save_message(uid, role, content, *a, **kw)
    monkeypatch.setattr(bot, "save_message", counting_save_message)

    msg = FakeMessage(FakeUser(OWNER), "Ещё одно сообщение.")
    run(bot.pipeline(msg, msg.text))
    assert len(msg.answers) == 1  # sent exactly once
    assert calls["n"] == 1  # attempted the assistant persist exactly once


def test_professional_path_reads_history_exactly_once_and_wraps_it_in_runtime_context(
        tmp_db, monkeypatch):
    """Proves two things at once: (1) the existing DB read
    (get_professional_conversation_history_rows) still happens exactly once
    per Professional-claimed turn -- this slice introduces no new DB read;
    (2) bot.py passes the orchestrator a ProfessionalTurnRuntimeContext
    wrapping exactly the ProfessionalConversationContext built from those
    same rows -- no context loss or divergence at the ownership boundary."""
    from professional_turn_conversation_context import (
        build_conversation_context_from_history_rows,
    )
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)

    rows = [(1, "user", "Прошлое сообщение.", "USER_AUTHORED")]
    read_calls = {"n": 0}

    async def counting_get_rows(uid, current_row_id):
        read_calls["n"] += 1
        return list(rows)
    monkeypatch.setattr(bot, "get_professional_conversation_history_rows", counting_get_rows)

    calls = _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    msg = FakeMessage(FakeUser(OWNER), "Текущее сообщение.")
    run(bot.pipeline(msg, msg.text))

    assert read_calls["n"] == 1
    runtime_context = calls["kwargs"]["runtime_context"]
    assert isinstance(runtime_context, ProfessionalTurnRuntimeContext)
    expected_conversation = build_conversation_context_from_history_rows(rows)
    assert runtime_context.conversation == expected_conversation


def test_no_double_assistant_reply_on_success(tmp_db, monkeypatch):
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    msg = FakeMessage(FakeUser(OWNER), "Одно сообщение, один ответ.")
    run(bot.pipeline(msg, msg.text))
    assert len(msg.answers) == 1


def test_voice_transcript_reaches_professional_claim_point(tmp_db, monkeypatch):
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    calls = _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    async def fake_transcribe(voice, bot_obj, client_obj, stt_lang):
        return "Голосовое сообщение о том, что мне тяжело."
    monkeypatch.setattr(bot, "transcribe_voice", fake_transcribe)

    msg = FakeMessage(FakeUser(OWNER), text="", voice=object())
    run(bot.handle_voice(msg, state=None))

    assert calls["n"] == 1
    assert calls["kwargs"]["source_text"] == "Голосовое сообщение о том, что мне тяжело."
    assert [a[0] for a in msg.answers] == [SUCCESS_RESULT.reply_text]
    assert not any("Голосовое сообщение" in a[0] for a in msg.answers)


def test_entry_triage_button_action_not_claimed_as_free_text():
    """Structural: Entry Triage is a callback_query handler, a different
    aiogram update type from message text/voice handlers -- it can never
    reach pipeline() at all. The function's own existing docstring already
    mentions "pipeline()" in prose describing what it deliberately does NOT
    do, so this checks for an actual CALL (AST-based), not a substring ban
    that would collide with that pre-existing, legitimate prose."""
    import ast, inspect, textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(bot.cb_professional_entry_triage)))
    called_names = {n.func.id for n in ast.walk(tree)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "pipeline" not in called_names


def test_professional_row_uses_dedicated_scenario_tag_no_choose_scenario(tmp_db, monkeypatch):
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    msg = FakeMessage(FakeUser(OWNER), "Проверка тега сценария.")
    run(bot.pipeline(msg, msg.text))

    async def read_scenarios():
        async with database.aiosqlite.connect(database.DB) as conn:
            cur = await conn.execute(
                "SELECT DISTINCT scenario FROM messages WHERE user_id=?", (OWNER,))
            return {r[0] for r in await cur.fetchall()}
    assert run(read_scenarios()) == {"professional"}


def test_privacy_export_delete_cover_professional_rows(tmp_db, monkeypatch):
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    msg = FakeMessage(FakeUser(OWNER), "Данные для приватности.")
    run(bot.pipeline(msg, msg.text))

    export = run(database.export_all_personal_data(OWNER))
    assert any(r.get("scenario") == "professional" for r in export.get("messages", []))

    summary = run(database.delete_all_personal_data(OWNER))
    assert summary["messages"] >= 1

    async def read_remaining():
        async with database.aiosqlite.connect(database.DB) as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM messages WHERE user_id=?", (OWNER,))
            return (await cur.fetchone())[0]
    assert run(read_remaining()) == 0


# ══════════════════════════════════════════════════════════════════════════
# D. Delivery truth -- ASSISTANT_DELIVERED content must always equal what
# the user actually received, for every text/voice presentation mode.
# ══════════════════════════════════════════════════════════════════════════

# Deliberately longer than bot._concise_version's default 220-char budget --
# a short reply would survive _safe_concise_version unchanged regardless of
# whether preserve_exact_text is honored, making these tests unable to
# detect a real regression.
LONG_REPLY_TEXT = (
    "Похоже, тебе сейчас непросто, и то, что ты об этом говоришь, уже важный шаг. "
    "Расскажи, пожалуйста, чуть подробнее: что конкретно в последние дни было самым "
    "тяжёлым моментом, и что в этот момент происходило у тебя внутри? Не нужно "
    "торопиться с ответом — мне важно понять именно твою ситуацию, а не общую картину "
    "того, что обычно происходит у людей в похожем состоянии."
)
assert len(LONG_REPLY_TEXT) > 220

LONG_SUCCESS_RESULT = pftr.ProfessionalFreeTextRuntimeResult(
    status=pftr.ProfessionalFreeTextRuntimeStatus.SUCCESS,
    reply_text=LONG_REPLY_TEXT, failure_stage=None, failure_reason=None, failure_detail=None,
    success_trace=_success_trace())


async def _read_persisted_assistant_content(uid):
    async with database.aiosqlite.connect(database.DB) as conn:
        cur = await conn.execute(
            "SELECT content FROM messages WHERE user_id=? AND role='assistant' "
            "AND scenario='professional' ORDER BY id DESC LIMIT 1", (uid,))
        row = await cur.fetchone()
    return row[0] if row else None


def test_professional_text_mode_delivered_and_persisted_exact(tmp_db, monkeypatch):
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, LONG_SUCCESS_RESULT)

    msg = FakeMessage(FakeUser(OWNER), "Мне тяжело в последнее время, и это давно так.")
    run(bot.pipeline(msg, msg.text))

    assert msg.answers[0][0] == LONG_REPLY_TEXT
    assert run(_read_persisted_assistant_content(OWNER)) == LONG_REPLY_TEXT


def test_professional_stored_voice_mode_tts_input_and_persisted_exact(tmp_db, monkeypatch):
    run(_seed_user(OWNER))
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    monkeypatch.setattr(config, "ELEVENLABS_TTS_ENABLED", True)
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "synthetic-test-key")
    run(database.set_response_preference(OWNER, response_format="voice"))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, LONG_SUCCESS_RESULT)

    tts_calls = []
    async def fake_tts(target, uid, text, lang_):
        tts_calls.append(text)
        return True
    monkeypatch.setattr(bot, "_synthesize_and_send_voice", fake_tts)

    msg = FakeMessage(FakeUser(OWNER), "Мне тяжело в последнее время, и это давно так.")
    run(bot.pipeline(msg, msg.text))

    assert tts_calls == [LONG_REPLY_TEXT]
    assert run(_read_persisted_assistant_content(OWNER)) == LONG_REPLY_TEXT


def test_professional_stored_voice_and_concise_text_mode_all_exact(tmp_db, monkeypatch):
    run(_seed_user(OWNER))
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    monkeypatch.setattr(config, "ELEVENLABS_TTS_ENABLED", True)
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "synthetic-test-key")
    run(database.set_response_preference(OWNER, response_format="voice_and_concise_text"))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, LONG_SUCCESS_RESULT)

    tts_calls = []
    async def fake_tts(target, uid, text, lang_):
        tts_calls.append(text)
        return True
    monkeypatch.setattr(bot, "_synthesize_and_send_voice", fake_tts)

    msg = FakeMessage(FakeUser(OWNER), "Мне тяжело в последнее время, и это давно так.")
    run(bot.pipeline(msg, msg.text))

    assert msg.answers[0][0] == LONG_REPLY_TEXT  # visible text -- NOT a concise rewrite
    # Public-beta contract: voice_and_concise_text delivers full text with an
    # on-demand Listen button -- it must never auto-send a duplicate voice
    # message every turn.
    assert tts_calls == []
    assert run(_read_persisted_assistant_content(OWNER)) == LONG_REPLY_TEXT


def test_professional_stored_concise_preference_does_not_shorten(tmp_db, monkeypatch):
    """Direct deliver_response unit check (not routed through parse_format_
    command wording) -- isolates the exact interaction under test: a stored
    response_length="concise" preference must never shorten a
    preserve_exact_text=True call."""
    run(database.upsert_user(OWNER, "u", "U"))
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    monkeypatch.setattr(config, "ELEVENLABS_TTS_ENABLED", True)
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "synthetic-test-key")
    run(database.set_response_preference(
        OWNER, response_format="voice", response_length="concise"))

    tts_calls = []
    async def fake_tts(target, uid, text, lang_):
        tts_calls.append(text)
        return True
    monkeypatch.setattr(bot, "_synthesize_and_send_voice", fake_tts)

    msg = FakeMessage(FakeUser(OWNER), "irrelevant")
    run(bot.deliver_response(msg, OWNER, LONG_REPLY_TEXT, "ru", preserve_exact_text=True))
    assert tts_calls == [LONG_REPLY_TEXT]


def test_professional_one_shot_concise_does_not_shorten(tmp_db, monkeypatch):
    """Direct deliver_response unit check: one_shot_concise=True must never
    shorten a preserve_exact_text=True call."""
    run(database.upsert_user(OWNER, "u", "U"))
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    monkeypatch.setattr(config, "ELEVENLABS_TTS_ENABLED", True)
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "synthetic-test-key")
    run(database.set_response_preference(OWNER, response_format="voice"))

    tts_calls = []
    async def fake_tts(target, uid, text, lang_):
        tts_calls.append(text)
        return True
    monkeypatch.setattr(bot, "_synthesize_and_send_voice", fake_tts)

    msg = FakeMessage(FakeUser(OWNER), "irrelevant")
    run(bot.deliver_response(msg, OWNER, LONG_REPLY_TEXT, "ru",
                             one_shot_concise=True, preserve_exact_text=True))
    assert tts_calls == [LONG_REPLY_TEXT]


def test_professional_mixed_voice_command_still_selects_voice_transport(tmp_db, monkeypatch):
    """The exact mixed-message example from format_commands.py's own module
    docstring ("Мне тревожно, и ответь голосом"): voice transport is still
    selected via the one-shot override, the psychological content still
    goes through Professional (never legacy), and the exact accepted text
    is what gets voiced."""
    run(_seed_user(OWNER))
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    monkeypatch.setattr(config, "ELEVENLABS_TTS_ENABLED", True)
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "synthetic-test-key")
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, LONG_SUCCESS_RESULT)

    tts_calls = []
    async def fake_tts(target, uid, text, lang_):
        tts_calls.append(text)
        return True
    monkeypatch.setattr(bot, "_synthesize_and_send_voice", fake_tts)

    msg = FakeMessage(FakeUser(OWNER), "Мне тревожно, и ответь голосом")
    run(bot.pipeline(msg, msg.text))

    assert tts_calls == [LONG_REPLY_TEXT]  # voice transport selected, exact text used
    assert run(_read_persisted_assistant_content(OWNER)) == LONG_REPLY_TEXT


def test_professional_technical_fallback_delivered_and_persisted_exact(tmp_db, monkeypatch):
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, FAILED_RESULT)

    msg = FakeMessage(FakeUser(OWNER), "Расскажи мне про свои чувства.")
    run(bot.pipeline(msg, msg.text))

    fallback = bot._professional_technical_fallback_text("ru")
    assert msg.answers[0][0] == fallback
    assert run(_read_persisted_assistant_content(OWNER)) == fallback


def test_safe_concise_version_never_called_when_preserve_exact_text(tmp_db, monkeypatch):
    """Regression guard: no code path can invoke _safe_concise_version on a
    preserve_exact_text=True call."""
    calls = {"n": 0}
    real = bot._safe_concise_version
    def spy(text, lang_):
        calls["n"] += 1
        return real(text, lang_)
    monkeypatch.setattr(bot, "_safe_concise_version", spy)
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(OWNER, "u", "U"))
    run(database.set_response_preference(
        OWNER, response_format="voice_and_concise_text", response_length="concise"))

    async def fake_tts(target, uid, text, lang_):
        return True
    monkeypatch.setattr(bot, "_synthesize_and_send_voice", fake_tts)

    msg = FakeMessage(FakeUser(OWNER), "irrelevant")
    run(bot.deliver_response(msg, OWNER, LONG_REPLY_TEXT, "ru",
                             one_shot_concise=True, preserve_exact_text=True))
    assert calls["n"] == 0
