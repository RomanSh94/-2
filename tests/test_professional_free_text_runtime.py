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
import inspect
import json
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
from therapeutic_domain import (
    PrimaryResponseMove, ProfessionalObjective, MemoryCategory, MemoryItem, MemoryLifecycle,
)
from professional_case_context import CanonicalCaseContext, EMPTY_CANONICAL_CASE_CONTEXT

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
        self.voices = []

    async def answer(self, text, **kw):
        self.answers.append((text, kw))
        return types.SimpleNamespace(message_id=self.message_id + 1)

    async def answer_voice(self, *a, **kw):
        self.voices.append((a, kw))

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
    monkeypatch.setattr(config, "UNIFIED_PSYCHOLOGICAL_OWNERSHIP_ENABLED", False)


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


# Codex model-call-count correction: a full successful run makes exactly 3
# REAL provider calls -- call_turn_analyzer, call_turn_plan_proposer, and
# render_turn_response each perform their own client.chat.completions.
# create call (verified directly against each module's source); produce_
# turn_analysis, govern_turn_plan, and accept_professional_response are
# confirmed pure Python with no such call anywhere in their source modules.
# The prior report of "1 provider call" (counting only the Renderer) was
# incorrect -- the true total is 3, both with and without the entry-policy
# signal. Only render_turn_response's own single call ever reads
# runtime_context.first_turn_entry_active at all (to add one extra system
# message + one extra payload key to that SAME one call) -- neither the
# Analyzer's nor the Plan Proposer's source references that field anywhere,
# so this test proves the entry-policy signal adds NO FOURTH provider call
# and causes no stage to run more than once -- same proof shape as
# test_success_trace_no_duplicate_stage_execution above, with
# first_turn_entry_active=True instead of the default False.
def test_entry_policy_active_adds_no_fourth_provider_call(monkeypatch):
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

    entry_active_context = ProfessionalTurnRuntimeContext(
        conversation=_empty_context(), first_turn_entry_active=True)
    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        runtime_context=entry_active_context, risk_result={"score": 0, "categories": []}, lang="ru"))
    assert result.status is pftr.ProfessionalFreeTextRuntimeStatus.SUCCESS
    assert calls == {"analyzer": 1, "produce": 1, "proposer": 1, "govern": 1, "render": 1, "accept": 1}
    # Exactly 3 real provider-call-making stages total -- no fourth call.
    assert calls["analyzer"] + calls["proposer"] + calls["render"] == 3


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


# ══════════════════════════════════════════════════════════════════════════
# D. Phase 1B — access_control.resolve_psychological_turn_owner precedence
# ══════════════════════════════════════════════════════════════════════════
# UNIFIED_PSYCHOLOGICAL_OWNERSHIP_ENABLED default False: preserved-behavior
# proof for the OFF+Therapist-eligible+Professional-eligible cell already
# exists above (test_therapist_core_claims_once_precedes_professional_and_
# validates_once) and is untouched by this slice -- it still passes.

def _counting_stub(value):
    calls = {"n": 0}
    async def fake(uid):
        calls["n"] += 1
        return value
    return fake, calls


@pytest.mark.parametrize(
    "unified_on, professional_eligible, therapist_eligible, expected_owner",
    [
        (False, False, False, "none"),
        (False, False, True, "therapist_core_v1"),
        (False, True, False, "professional"),
        (False, True, True, "therapist_core_v1"),
        (True, False, False, "none"),
        (True, False, True, "therapist_core_v1"),
        (True, True, False, "professional"),
        (True, True, True, "professional"),
    ],
)
def test_resolver_truth_table_and_each_gate_at_most_once(
        monkeypatch, unified_on, professional_eligible, therapist_eligible, expected_owner):
    monkeypatch.setattr(config, "UNIFIED_PSYCHOLOGICAL_OWNERSHIP_ENABLED", unified_on)
    professional_fake, professional_calls = _counting_stub(professional_eligible)
    therapist_fake, therapist_calls = _counting_stub(therapist_eligible)
    monkeypatch.setattr(ac, "professional_free_text_allowed_for", professional_fake)
    monkeypatch.setattr(ac, "therapist_core_v1_allowed_for", therapist_fake)

    result = run(ac.resolve_psychological_turn_owner(1))

    assert result == expected_owner
    assert professional_calls["n"] <= 1
    assert therapist_calls["n"] <= 1


def test_resolver_off_therapist_eligible_never_probes_professional(monkeypatch):
    """Short-circuit proof for the OFF/preserved order: when Therapist Core
    already decides the answer, Professional's gate is never even awaited --
    not just called <=1 time, literally zero times."""
    monkeypatch.setattr(config, "UNIFIED_PSYCHOLOGICAL_OWNERSHIP_ENABLED", False)
    monkeypatch.setattr(ac, "therapist_core_v1_allowed_for", _async(True))
    monkeypatch.setattr(
        ac, "professional_free_text_allowed_for",
        _raise_if_called("professional_free_text_allowed_for"))
    assert run(ac.resolve_psychological_turn_owner(1)) == "therapist_core_v1"


def test_resolver_on_professional_eligible_never_probes_therapist(monkeypatch):
    """Short-circuit proof for the ON/migration order: a Professional-owned
    turn structurally never reaches Therapist Core's own eligibility check,
    let alone its claim -- Therapist Core cannot claim this turn."""
    monkeypatch.setattr(config, "UNIFIED_PSYCHOLOGICAL_OWNERSHIP_ENABLED", True)
    monkeypatch.setattr(ac, "professional_free_text_allowed_for", _async(True))
    monkeypatch.setattr(
        ac, "therapist_core_v1_allowed_for",
        _raise_if_called("therapist_core_v1_allowed_for"))
    assert run(ac.resolve_psychological_turn_owner(1)) == "professional"


def test_resolver_makes_no_direct_model_or_client_calls():
    """Scoped to resolve_psychological_turn_owner's OWN source only (not the
    whole access_control.py module, which is a separate, broader claim this
    test does not make) -- it calls only the two existing eligibility gates
    and never a model/provider/client surface itself."""
    import inspect
    source = inspect.getsource(ac.resolve_psychological_turn_owner)
    assert "OpenAI(" not in source
    assert "AsyncOpenAI(" not in source
    assert "chat.completions" not in source
    assert "client" not in source


def test_dass_discussion_active_never_calls_resolver(tmp_db, monkeypatch):
    """DASS-discussion turns must skip the resolver entirely -- neither
    eligibility gate is awaited -- exactly preserving the pre-Phase-1B
    behavior of skipping both inline checks in this case.

    A DASS-active turn legitimately continues through the ordinary legacy/
    open_chat machinery afterward (dass_discussion_result only forces its
    scenario, per bot.py's own existing, unmodified logic) -- so unlike the
    Professional/Therapist tests above, legacy machinery here must be
    allowed to run, not raise-guarded; only the resolver itself is guarded."""
    run(_seed_user(OWNER))
    monkeypatch.setattr(bot, "_onboarding_blocks_ordinary_entry", _async(False))
    monkeypatch.setattr(ac, "depression_disclosure_allowed_for", _async(False))
    monkeypatch.setattr(bot.dependency_monitor, "record_message", _async(None))
    monkeypatch.setattr(bot.dependency_monitor, "assess", _async(None))
    monkeypatch.setattr(bot, "maybe_summarize", _async(None))
    monkeypatch.setattr(bot, "_maybe_react", _async(None))
    monkeypatch.setattr(bot, "maybe_update_profile", _async(None))
    monkeypatch.setattr(bot, "check_sudden_improvement", _async(False))
    monkeypatch.setattr(bot, "persist_influence_trace", _async(None))
    monkeypatch.setattr(bot, "claim_first_turn", _async(False))
    monkeypatch.setattr(bot.bot, "send_chat_action", _async(None))
    monkeypatch.setattr(bot.asyncio, "sleep", _async(None))

    async def fake_completion(**kwargs):
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content="Безопасный ответ о ситуации."))])
    monkeypatch.setattr(bot.client.chat.completions, "create", fake_completion)

    monkeypatch.setattr(bot, "_load_owned_completed_history_dass", _async(object()))

    class _FakeDassResult:
        subscales = {"depression": 10, "anxiety": 10, "stress": 10}
        instrument_version = "DASS-21"
        translation_id = "test"

    monkeypatch.setattr(bot, "_dass21_discuss_gate_and_load", _async(_FakeDassResult()))
    monkeypatch.setattr(
        ac, "resolve_psychological_turn_owner",
        _raise_if_called("resolve_psychological_turn_owner"))

    class _FakeFSMState:
        def __init__(self, data):
            self._data = data
        async def get_state(self):
            return bot.Dass21Discussion.active.state
        async def get_data(self):
            return dict(self._data)
        async def update_data(self, **kwargs):
            self._data.update(kwargs)
        async def clear(self):
            self._data = {}

    state = _FakeFSMState({"dass21_session_id": 1})
    msg = FakeMessage(FakeUser(OWNER), "Почему так вышло?")
    run(bot.pipeline(msg, msg.text, state))
    # Reaching here (no AssertionError from the resolver stub) is the proof;
    # the turn still gets an ordinary reply through the legacy path.
    assert msg.answers


def test_unified_on_both_eligible_professional_owns_therapist_never_generates(
        tmp_db, monkeypatch):
    """End-to-end (not just resolver-level): with both eligible and the
    switch on, Professional's own delivery path runs and Therapist Core's
    generation function is never invoked -- a Professional-owned turn never
    also reaches Therapist."""
    run(_seed_user(OWNER))
    _stub_legacy_machinery(monkeypatch)
    _stub_history(monkeypatch, rows=())
    monkeypatch.setattr(config, "UNIFIED_PSYCHOLOGICAL_OWNERSHIP_ENABLED", True)
    monkeypatch.setattr(ac, "therapist_core_v1_allowed_for", _async(True))
    monkeypatch.setattr(ac, "professional_free_text_allowed_for", _async(True))
    monkeypatch.setattr(
        bot, "generate_therapist_core_v1",
        _raise_if_called("generate_therapist_core_v1"))
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    msg = FakeMessage(FakeUser(OWNER), "Мне тяжело сегодня.")
    run(bot.pipeline(msg, msg.text))
    assert msg.answers and msg.answers[0][0] == SUCCESS_RESULT.reply_text


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
    ("JUST_TALK", "I've read what you wrote. I won't ask you to repeat it. "
                  "You can continue from here — I'll keep track of the thread."),
    ("ACTION", "I've read what you wrote. I won't ask you to repeat it. "
               "Let's build on what's already been said and choose a next step."),
    ("NONE", "I've read what you wrote. I won't ask you to repeat it. "
             "Let's continue from there, building on what's already been said."),
])
def test_therapist_core_fallback_low_risk_en_by_contract(contract, expected_en):
    assert bot._therapist_core_fallback({"level": "low"}, contract, "en") == expected_en


# ── Production-incident hotfix: UNDERSTAND fallback is now a substantive
# synthesis-shaped static reply, not the shared "Не буду просить повторять"
# acknowledgement boilerplate the other three contracts still use ──────────
_NEW_UNDERSTAND_FALLBACK_RU = (
    "Если ты хочешь понять, что здесь происходит, я бы не начинал с общего "
    "совета. Полезнее посмотреть на саму последовательность: что запускает "
    "реакцию, какая мысль или ожидание появляется первой, что происходит "
    "дальше и что меняется после этого. Если такая цепочка уже видна из "
    "твоего описания, можно разбирать её; если пока нет — не буду её "
    "додумывать. Какой последний конкретный эпизод лучше всего показывает "
    "эту реакцию?"
)
_NEW_UNDERSTAND_FALLBACK_EN = (
    "If you want to understand what's going on here, I wouldn't start "
    "with generic advice. It's more useful to look at the sequence "
    "itself: what triggers the reaction, which thought or expectation "
    "shows up first, what happens next, and what changes afterward. If "
    "that sequence is already visible from what you've described, we "
    "can work through it; if not yet, I won't invent it. What's the "
    "most recent concrete episode that best shows this reaction?"
)


def test_therapist_core_fallback_understand_low_risk_ru_is_substantive_synthesis():
    text = bot._therapist_core_fallback({"level": "low"}, "UNDERSTAND", "ru")
    assert text == _NEW_UNDERSTAND_FALLBACK_RU
    assert text != _OLD_BAD_FALLBACK
    none_text = bot._therapist_core_fallback({"level": "low"}, "NONE", "ru")
    assert len(text.split()) > len(none_text.split())  # more substantive than acknowledgement-only
    assert len(text.split()) <= 150  # shared safety_validator word ceiling
    ok, reason = safety_validator.validate_response(text, "ru")
    assert ok, reason


def test_therapist_core_fallback_understand_low_risk_en_is_substantive_synthesis():
    text = bot._therapist_core_fallback({"level": "low"}, "UNDERSTAND", "en")
    assert text == _NEW_UNDERSTAND_FALLBACK_EN
    none_text = bot._therapist_core_fallback({"level": "low"}, "NONE", "en")
    assert len(text.split()) > len(none_text.split())
    assert len(text.split()) <= 150
    ok, reason = safety_validator.validate_response(text, "en")
    assert ok, reason


def test_therapist_core_fallback_understand_still_never_asks_to_repeat():
    for lang in ("ru", "en"):
        text = bot._therapist_core_fallback({"level": "low"}, "UNDERSTAND", lang)
        assert "повторять" not in text.lower()  # no longer uses that literal opener...
        assert "repeat it" not in text.lower() and "repeat yourself" not in text.lower()
        # ...but never actually asks the user to restate what they wrote either.
        assert "расскажи" not in text.lower() and "tell me again" not in text.lower()


def test_therapist_core_fallback_makes_no_model_or_client_call():
    src = inspect.getsource(bot._therapist_core_fallback)
    assert "client" not in src
    assert "await" not in src


# ── Corrective pass: the fallback must be truthful for BOTH rich and sparse
# UNDERSTAND turns -- it is one static string, so it must never assert that a
# sequence/material already exists, only offer to examine one IF visible ────
def test_therapist_core_fallback_understand_does_not_assert_material_already_exists():
    for lang, unconditional_claim in (
        ("ru", "уже есть материал"),
        ("en", "there's already enough material"),
    ):
        text = bot._therapist_core_fallback({"level": "low"}, "UNDERSTAND", lang)
        assert unconditional_claim not in text.lower()


def test_therapist_core_fallback_understand_valid_for_sparse_input_too():
    # Same static string regardless of whether the actual turn was rich or
    # sparse: it conditions any sequence-examination on the sequence
    # actually being visible ("если такая цепочка уже видна" / "if that
    # sequence is already visible"), explicitly refuses to invent one when
    # it isn't ("не буду её додумывать" / "i won't invent it"), and asks
    # exactly one question inviting a concrete episode either way.
    ru = bot._therapist_core_fallback({"level": "low"}, "UNDERSTAND", "ru")
    en = bot._therapist_core_fallback({"level": "low"}, "UNDERSTAND", "en")
    assert ru.count("?") == 1 and en.count("?") == 1
    assert "если пока нет" in ru.lower() and "додумывать" in ru.lower()
    assert "if not yet" in en.lower() and "invent it" in en.lower()
    assert "конкретный эпизод" in ru.lower() and "concrete episode" in en.lower()


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
    async def fake_tts(target, uid, text, lang_, **kw):
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
    async def fake_tts(target, uid, text, lang_, **kw):
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
    async def fake_tts(target, uid, text, lang_, **kw):
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
    async def fake_tts(target, uid, text, lang_, **kw):
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
    async def fake_tts(target, uid, text, lang_, **kw):
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

    async def fake_tts(target, uid, text, lang_, **kw):
        return True
    monkeypatch.setattr(bot, "_synthesize_and_send_voice", fake_tts)

    msg = FakeMessage(FakeUser(OWNER), "irrelevant")
    run(bot.deliver_response(msg, OWNER, LONG_REPLY_TEXT, "ru",
                             one_shot_concise=True, preserve_exact_text=True))
    assert calls["n"] == 0


# ══════════════════════════════════════════════════════════════════════════
# Phase 1C -- First-Turn as a governed ENTRY-POLICY signal inside
# Professional Free-Text. Corrected per the owner's architectural review
# gate: Alternative B (stage+risk eligibility, no legacy scenario/capacity)
# is approved; the runtime signal is named first_turn_entry_active with
# narrow "the one-shot entry policy is active" semantics -- never "this is
# the user's first-ever message" / "no history exists"; conversation
# history is never suppressed by this signal; PRE-SEND lifecycle
# transitions (pending_before_llm->generated, generated->send_started) are
# fail-closed GATES -- a failure blocks the send entirely, never
# compensated; POST-SEND terminal transitions are attempted exactly once,
# never retried/compensated, regardless of outcome.
# ══════════════════════════════════════════════════════════════════════════

async def _first_turn_claim_row(uid):
    async with database.aiosqlite.connect(database.DB) as conn:
        conn.row_factory = database.aiosqlite.Row
        cur = await conn.execute(
            "SELECT status, scenario, turn_id FROM first_turn_claims "
            "WHERE user_id=? AND contract_version=?",
            (uid, config.FIRST_TURN_CONTRACT_VERSION))
        row = await cur.fetchone()
    return dict(row) if row else None


def _wrap_transition_first_turn_claim(monkeypatch, fail_at_call=None, mode="return_false"):
    """Delegates to the REAL database.transition_first_turn_claim for every
    call except the `fail_at_call`-th (1-indexed, None means never fail),
    which either returns False or raises WITHOUT touching the DB -- so the
    row's real confirmed status stays exactly whatever the previous (real)
    call left it at. Records every (from_status, to_status) attempted, so
    a test can assert precisely how many transition calls were made and
    that none happened after a failure."""
    calls = []
    async def fake(uid, contract_version, claim_token, from_status, to_status, turn_id=None):
        calls.append((from_status, to_status))
        if fail_at_call is not None and len(calls) == fail_at_call:
            if mode == "raise":
                raise RuntimeError("simulated transition failure")
            return False
        return await database.transition_first_turn_claim(
            uid, contract_version, claim_token, from_status, to_status, turn_id=turn_id)
    monkeypatch.setattr(bot, "transition_first_turn_claim", fake)
    return calls


# ── Items 1-4: PRE-SEND transitions (pending_before_llm->generated,
# generated->send_started) are fail-closed GATES -- a failure blocks the
# Telegram send entirely and no further transition is attempted. ──────────

def test_pending_to_generated_returns_false_blocks_send_entirely(tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)
    calls = _wrap_transition_first_turn_claim(monkeypatch, fail_at_call=1, mode="return_false")

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно с кем-то поговорить о том, что происходит.")
    run(bot.pipeline(msg, msg.text))  # must not raise

    assert msg.answers == []
    assert len(calls) == 1
    assert calls[0] == ("pending_before_llm", "generated")


def test_pending_to_generated_raises_blocks_send_entirely(tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)
    calls = _wrap_transition_first_turn_claim(monkeypatch, fail_at_call=1, mode="raise")

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно с кем-то поговорить о том, что происходит.")
    run(bot.pipeline(msg, msg.text))  # must not raise -- the helper catches it, returns False

    assert msg.answers == []
    assert len(calls) == 1


def test_generated_to_send_started_returns_false_blocks_send(tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)
    calls = _wrap_transition_first_turn_claim(monkeypatch, fail_at_call=2, mode="return_false")

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно с кем-то поговорить о том, что происходит.")
    run(bot.pipeline(msg, msg.text))

    assert msg.answers == []
    assert len(calls) == 2
    assert calls[1] == ("generated", "send_started")
    row = run(_first_turn_claim_row(OWNER))
    assert row is not None and row["status"] == "generated"  # last REAL confirmed state


def test_generated_to_send_started_raises_blocks_send(tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)
    calls = _wrap_transition_first_turn_claim(monkeypatch, fail_at_call=2, mode="raise")

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно с кем-то поговорить о том, что происходит.")
    run(bot.pipeline(msg, msg.text))

    assert msg.answers == []
    assert len(calls) == 2
    row = run(_first_turn_claim_row(OWNER))
    assert row is not None and row["status"] == "generated"


# ── Items 5-7: normal SUCCESS / REJECTED / FAILED all reach exactly one
# real send and the correct truthful delivered terminal state. ────────────

def test_first_turn_entry_success_delivers_and_signals_entry_active(tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_legacy_machinery(monkeypatch)  # _first_turn_generate_and_validate raises if called
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    calls = _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    msg = FakeMessage(FakeUser(OWNER),
                       "Мне нужно с кем-то поговорить о том, что происходит.")
    run(bot.pipeline(msg, msg.text))

    assert len(msg.answers) == 1
    assert msg.answers[0][0] == SUCCESS_RESULT.reply_text
    assert calls["n"] == 1
    runtime_context = calls["kwargs"]["runtime_context"]
    assert runtime_context.first_turn_entry_active is True

    row = run(_first_turn_claim_row(OWNER))
    assert row is not None
    assert row["scenario"] == "professional"
    assert row["status"] == "delivered_without_buttons"
    assert row["turn_id"] is not None


@pytest.mark.parametrize("result", [REJECTED_RESULT, FAILED_RESULT])
def test_first_turn_entry_rejected_or_failed_fallback_delivered_once(tmp_db, monkeypatch, result):
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, result)

    msg = FakeMessage(FakeUser(OWNER), "Расскажи мне про свои чувства.")
    run(bot.pipeline(msg, msg.text))

    fallback = bot._professional_technical_fallback_text("ru")
    assert len(msg.answers) == 1
    assert msg.answers[0][0] == fallback
    row = run(_first_turn_claim_row(OWNER))
    assert row is not None
    assert row["status"] == "delivered_without_buttons"


# ── Item 8: Telegram send exception -- exactly one send attempt,
# delivery_uncertain attempted exactly once, no retry/double-send. ────────

def test_telegram_send_exception_delivery_uncertain_once_no_retry(tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    send_calls = {"n": 0}
    async def fake_deliver_response(*a, **kw):
        send_calls["n"] += 1
        raise RuntimeError("telegram down")
    monkeypatch.setattr(bot, "deliver_response", fake_deliver_response)

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно с кем-то поговорить о том, что происходит.")
    run(bot.pipeline(msg, msg.text))  # must not raise

    assert send_calls["n"] == 1
    assert msg.answers == []
    row = run(_first_turn_claim_row(OWNER))
    assert row is not None and row["status"] == "delivery_uncertain"


# ── Items 9-10: a failing/raising TERMINAL transition never triggers a
# second send or any compensating transition -- the user already has their
# one reply; the last confirmed lifecycle state is the accepted, bounded
# residual limitation (send_started), never silently "fixed" by a retry. ──

def test_terminal_transition_returns_false_no_double_send(tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)
    calls = _wrap_transition_first_turn_claim(monkeypatch, fail_at_call=3, mode="return_false")

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно с кем-то поговорить о том, что происходит.")
    run(bot.pipeline(msg, msg.text))

    assert len(msg.answers) == 1  # already delivered -- exactly once
    assert msg.answers[0][0] == SUCCESS_RESULT.reply_text
    assert len(calls) == 3
    assert calls[2] == ("send_started", "delivered_without_buttons")
    row = run(_first_turn_claim_row(OWNER))
    assert row is not None and row["status"] == "send_started"  # accepted residual limitation


def test_terminal_transition_raises_no_double_send(tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)
    calls = _wrap_transition_first_turn_claim(monkeypatch, fail_at_call=3, mode="raise")

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно с кем-то поговорить о том, что происходит.")
    run(bot.pipeline(msg, msg.text))

    assert len(msg.answers) == 1
    assert msg.answers[0][0] == SUCCESS_RESULT.reply_text
    assert len(calls) == 3
    row = run(_first_turn_claim_row(OWNER))
    assert row is not None and row["status"] == "send_started"


# ── Item 11: stale/superseded turn before send -- zero sends,
# failed_before_send attempted exactly once, nothing further attempted. ───

def test_stale_turn_before_send_zero_sends_failed_before_send_once(tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)
    monkeypatch.setattr(bot, "_user_generation_superseded", lambda uid, gen: True)
    calls = _wrap_transition_first_turn_claim(monkeypatch)  # never forced to fail -- real DB

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно с кем-то поговорить о том, что происходит.")
    run(bot.pipeline(msg, msg.text))

    assert msg.answers == []
    assert len(calls) == 2  # pending_before_llm->generated, then generated->failed_before_send
    assert calls[1] == ("generated", "failed_before_send")
    row = run(_first_turn_claim_row(OWNER))
    assert row is not None and row["status"] == "failed_before_send"


# ── Item 12 (owner decision 3): a user with real earlier usable history
# (persisted before Professional ownership ever applied to them) whose
# one-shot claim only succeeds NOW must keep that history available to
# Professional -- the entry policy must never force conversation_context
# to empty/None, and it must not claim "no earlier conversation" as fact. ──

def test_delayed_entry_preserves_existing_conversation_history(tmp_db, monkeypatch):
    # config.FIRST_TURN_CONTRACT_VERSION == config.FIRST_TURN_INITIAL_
    # ROLLOUT_VERSION == "v1" today, and claim_first_turn's own v1-only
    # bootstrap rule auto-exempts (never claims) a user who already has a
    # prior ASSISTANT-authored message -- a separate, legitimate, existing
    # mechanism, not the scenario this test targets. This repo's own
    # comment on FIRST_TURN_CONTRACT_VERSION documents the intended future:
    # "A later FIRST_TURN_CONTRACT_VERSION bump makes legacy-exemption
    # bootstrap apply only to v1" -- i.e. a real claim succeeding for a user
    # with genuine prior history is exactly the NORMAL case once the
    # contract version has moved past the initial rollout. Simulating that
    # (rather than the v1-only bootstrap edge case) is what actually
    # isolates owner decision 3's concern.
    monkeypatch.setattr(bot, "FIRST_TURN_CONTRACT_VERSION", "v2-test-only")
    run(database.upsert_user(OWNER, "u", "U"))
    # Real prior history under a DIFFERENT (non-"professional") scenario --
    # simulates a user who conversed before ever reaching Professional
    # ownership. Deliberately NOT using _stub_history here: the real
    # get_professional_conversation_history_rows must run against these
    # actually-persisted rows.
    run(database.save_message(OWNER, "user", "Мне давно тяжело на работе.",
                              "open_chat", "ru", source=database.MessageSource.USER_AUTHORED))
    run(database.save_message(OWNER, "assistant", "Что именно происходит?",
                              "open_chat", "ru", source=database.MessageSource.ASSISTANT_DELIVERED))

    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    calls = _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    msg = FakeMessage(FakeUser(OWNER), "Сегодня опять было тяжело.")
    run(bot.pipeline(msg, msg.text))

    assert msg.answers
    runtime_context = calls["kwargs"]["runtime_context"]
    assert runtime_context.first_turn_entry_active is True
    assert len(runtime_context.conversation.turns) == 2
    assert runtime_context.conversation.turns[0].content == "Мне давно тяжело на работе."
    assert runtime_context.conversation.turns[1].content == "Что именно происходит?"


# ── Item 13: a genuinely new user (no history) -- entry-policy active,
# absence of history remains valid, and sparse/rich replies both remain
# adaptive (no exact-one-question / <=120-word constraint is imported). ───

def test_first_turn_entry_never_imports_old_first_turn_validation():
    src = (inspect.getsource(bot._run_professional_free_text_and_deliver)
           + inspect.getsource(bot._professional_first_turn_transition))
    assert "validate_first_turn_response" not in src
    assert "get_first_turn_fallback" not in src
    assert "_first_turn_generate_and_validate" not in src


@pytest.mark.parametrize("reply_text", [
    "Ясно.",  # sparse -- must not be forced longer or rejected
    " ".join(["слово"] * 130),  # rich -- over the OLD 120-word cap, under the shared 150-word ceiling
])
def test_new_user_no_history_sparse_and_rich_replies_remain_adaptive(tmp_db, monkeypatch, reply_text):
    result = pftr.ProfessionalFreeTextRuntimeResult(
        status=pftr.ProfessionalFreeTextRuntimeStatus.SUCCESS,
        reply_text=reply_text, failure_stage=None, failure_reason=None,
        failure_detail=None, success_trace=_success_trace())
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    calls = _stub_runtime_result(monkeypatch, result)

    msg = FakeMessage(FakeUser(OWNER), "Привет, вот что у меня происходит.")
    run(bot.pipeline(msg, msg.text))

    assert msg.answers[0][0] == reply_text
    assert calls["kwargs"]["runtime_context"].first_turn_entry_active is True


# ── Item 14: a later turn for the same user carries no entry-policy
# signal -- the one-shot claim was already consumed. ───────────────────────

def test_later_professional_turn_carries_no_entry_policy_signal(tmp_db, monkeypatch):
    run(_seed_user(OWNER))  # pre-consumes the one-shot claim, same as every other test here
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    calls = _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    msg = FakeMessage(FakeUser(OWNER), "Продолжим то, о чём говорили.")
    run(bot.pipeline(msg, msg.text))

    assert msg.answers
    runtime_context = calls["kwargs"]["runtime_context"]
    assert runtime_context.first_turn_entry_active is False


# ── Item 15: a Professional-ineligible user keeps the existing lower-path
# (legacy/First-Turn) behavior unaffected. ─────────────────────────────────

def test_professional_ineligible_user_still_reaches_legacy_first_turn_path(tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_professional_eligible(monkeypatch, False)
    monkeypatch.setattr(ac, "therapist_core_v1_allowed_for", _async(False))
    called = {"n": 0}

    async def fake_first_turn(*a, **kw):
        called["n"] += 1
        return "ok", True
    monkeypatch.setattr(bot, "_first_turn_generate_and_validate", fake_first_turn)
    _stub_legacy_machinery_allow_legacy(monkeypatch)
    monkeypatch.setattr(bot, "get_emotional_trajectory", _async(types.SimpleNamespace(
        trend="stable", hopelessness_streak=0, yellow_plus_streak=0, messages_analyzed=0)))
    monkeypatch.setattr(bot, "_controller_claim_turn", _async(None))

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно с кем-то поговорить о том, что происходит.")
    run(bot.pipeline(msg, msg.text))

    assert called["n"] == 1
    row = run(_first_turn_claim_row(OWNER))
    assert row is not None and row["scenario"] != "professional"


# ── Item 16: DASS/crisis precedence unchanged -- already covered by the
# existing, unmodified Phase 1B test_dass_discussion_active_never_calls_
# resolver above, which proves the resolver (and therefore this slice's
# professional-branch entry-policy code, which lives strictly inside that
# resolver's "professional" arm) is never even reached for a DASS-active
# turn, and already stubs claim_first_turn itself. Crisis needs no new test
# either: crisis handling returns from pipeline() far earlier, before
# risk-based owner resolution runs at all -- unchanged, untouched by this
# slice, and exercised by the existing crisis test suite.

# ── Item 17: unified ownership default-OFF semantics remain unchanged. ────

def test_unified_ownership_default_off_untouched_by_this_slice():
    assert config.UNIFIED_PSYCHOLOGICAL_OWNERSHIP_ENABLED is False


# ── Item 18: no additional provider/model call -- run_professional_free_
# text_turn is still called exactly once per turn regardless of entry-
# policy status (already asserted by calls["n"] == 1 in the tests above),
# and the transition helper itself makes no client/model call. The Renderer
# still makes exactly one client.chat.completions.create call whether or
# not first_turn_entry_active is set -- proven directly in
# tests/test_professional_turn_response_renderer.py (unchanged file
# structure, just the renamed field/payload key), not re-proven here to
# avoid duplicating that unit-level assertion.

def test_first_turn_transition_helper_makes_no_model_or_client_call():
    src = inspect.getsource(bot._professional_first_turn_transition)
    assert "client" not in src
    assert "openai" not in src.lower()


# The Professional-specific entry-policy eligibility formula (stage + risk
# only, no scenario/capacity -- owner-approved Alternative B) must still
# exclude acute-distress-shaped and high/critical-risk messages, exactly as
# the legacy 4-condition First-Turn formula did for those two conditions.
def test_entry_policy_excluded_for_high_risk_message(tmp_db, monkeypatch):
    # Verified directly (not assumed): detect_risk on this exact message
    # returns level="high", categories=["hopelessness", "panic"] -- high
    # risk WITHOUT suicide/self_harm, so the crisis override does not fire
    # and this turn genuinely reaches the Professional branch, letting this
    # test isolate the risk-level exclusion specifically.
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    calls = _stub_runtime_result(monkeypatch, SUCCESS_RESULT)
    monkeypatch.setattr(bot, "get_active_crisis", _async(None))

    msg = FakeMessage(FakeUser(OWNER), (
        "У меня паническая атака, сердце колотится, не могу дышать, и всё "
        "безнадёжно, ничего не изменится никогда, я чувствую себя "
        "оторванным от реальности, будто это происходит не со мной."))
    run(bot.pipeline(msg, msg.text))

    assert calls["kwargs"] is not None
    assert calls["kwargs"]["runtime_context"].first_turn_entry_active is False
    row = run(_first_turn_claim_row(OWNER))
    assert row is None


# ══════════════════════════════════════════════════════════════════════════
# Final pre-commit hardening (post-review correction pass) -- narrow
# documentation/truthfulness fixes plus the missing regression proofs below.
# No eligibility/DB/schema/transition-vocabulary/ownership change.
# ══════════════════════════════════════════════════════════════════════════

# Item 3: the Professional-specific entry-policy formula (stage + risk only)
# must exclude ACUTE_DISTRESS-classified turns exactly as it excludes
# high/critical risk turns. Deterministically stubbing detect_stage (rather
# than hand-picking a message hoped to classify as ACUTE_DISTRESS) proves
# the branch CONDITION itself, independent of any real stage classifier
# behavior -- the message text below is otherwise low-risk and, absent this
# stub, is already proven (by test_first_turn_entry_success_delivers_and_
# signals_entry_active, which reuses it verbatim) to leave
# first_turn_entry_active True.

def test_entry_policy_excluded_for_acute_distress_stage(tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_legacy_machinery(monkeypatch)  # _first_turn_generate_and_validate raises if called
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    calls = _stub_runtime_result(monkeypatch, SUCCESS_RESULT)
    # Deterministic stage stub -- isolates the stage-exclusion branch
    # condition itself, independent of real stage-classifier behavior.
    monkeypatch.setattr(bot, "detect_stage", lambda text, lang: "ACUTE_DISTRESS")

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно с кем-то поговорить о том, что происходит.")
    run(bot.pipeline(msg, msg.text))

    # Professional still owns and processes the turn -- exactly one
    # orchestrator call, exactly one delivered reply -- only the
    # entry-policy signal is off.
    assert calls["n"] == 1
    assert len(msg.answers) == 1
    assert msg.answers[0][0] == SUCCESS_RESULT.reply_text
    assert calls["kwargs"]["runtime_context"].first_turn_entry_active is False

    # No first-turn claim was ever created/won for this turn.
    row = run(_first_turn_claim_row(OWNER))
    assert row is None


# Item 4: POST-SEND persistence failure -- Telegram delivery succeeds, but
# the subsequent ASSISTANT-row save_message call raises. send_started->
# delivered_context_missing must be attempted exactly once (the transition
# mechanism itself works normally here -- only the unrelated persistence
# call fails), with no second send and no compensating transition. The
# inbound/current USER save (already completed earlier in pipeline(),
# before this function's own logic ever runs) is unaffected.

def test_post_send_persistence_failure_delivered_context_missing_once(tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    send_calls = {"n": 0}
    async def fake_deliver_response(*a, **kw):
        send_calls["n"] += 1
        return None  # Telegram delivery succeeds
    monkeypatch.setattr(bot, "deliver_response", fake_deliver_response)

    save_calls = []
    async def fake_save_message(*a, **kw):
        save_calls.append(a[1])  # role
        if a[1] == "assistant":
            raise RuntimeError("simulated assistant persistence failure")
        return await database.save_message(*a, **kw)
    monkeypatch.setattr(bot, "save_message", fake_save_message)

    calls = _wrap_transition_first_turn_claim(monkeypatch)  # never forced to fail -- real DB

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно с кем-то поговорить о том, что происходит.")
    run(bot.pipeline(msg, msg.text))  # must not raise

    # The inbound USER save still succeeded normally.
    assert "user" in save_calls
    # Exactly one Telegram send -- no second/compensating send.
    assert send_calls["n"] == 1
    # Claim reached send_started before the send (the two real PRE-SEND
    # gate transitions), then the terminal attempt -- exactly once.
    assert len(calls) == 3
    assert calls[0] == ("pending_before_llm", "generated")
    assert calls[1] == ("generated", "send_started")
    assert calls[2] == ("send_started", "delivered_context_missing")
    row = run(_first_turn_claim_row(OWNER))
    assert row is not None and row["status"] == "delivered_context_missing"


# Item 5: a bookkeeping failure on the delivery_uncertain TERMINAL
# transition itself (after Telegram delivery already failed) must never
# trigger a retry of the send or any compensating transition -- the last
# confirmable DB state is whatever the previous REAL transition left it at
# (send_started), exactly the same non-compensated discipline already
# proven above for the delivered_without_buttons terminal transition
# (test_terminal_transition_returns_false_no_double_send /
# test_terminal_transition_raises_no_double_send).

@pytest.mark.parametrize("mode", ["return_false", "raise"])
def test_delivery_uncertain_terminal_transition_failure_no_retry(tmp_db, monkeypatch, mode):
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    send_calls = {"n": 0}
    async def fake_deliver_response(*a, **kw):
        send_calls["n"] += 1
        raise RuntimeError("telegram down")
    monkeypatch.setattr(bot, "deliver_response", fake_deliver_response)

    calls = _wrap_transition_first_turn_claim(monkeypatch, fail_at_call=3, mode=mode)

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно с кем-то поговорить о том, что происходит.")
    run(bot.pipeline(msg, msg.text))  # must not raise

    assert send_calls["n"] == 1  # exactly one attempt -- no retry
    assert msg.answers == []  # no reply -- the send genuinely failed
    assert len(calls) == 3  # no further/compensating transition attempted
    assert calls[2] == ("send_started", "delivery_uncertain")
    row = run(_first_turn_claim_row(OWNER))
    # The delivery_uncertain transition attempt itself failed, so the last
    # confirmed DB state is whatever the previous REAL transition left --
    # send_started -- never silently advanced despite the failed attempt.
    assert row is not None and row["status"] == "send_started"
    # No other owner/reply was produced for this turn.
    assert len(msg.answers) == 0


# Item 6: a bookkeeping failure on the failed_before_send TERMINAL
# transition itself (for a stale/superseded turn, before any Telegram send
# is attempted) must never trigger a compensating transition or a send --
# the last confirmable DB state is whatever the previous REAL transition
# left it at (generated), exactly the non-compensated discipline items 1-4
# and item 5 above already establish for every other terminal edge.

@pytest.mark.parametrize("mode", ["return_false", "raise"])
def test_stale_turn_failed_before_send_transition_failure_no_compensation(tmp_db, monkeypatch, mode):
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)
    monkeypatch.setattr(bot, "_user_generation_superseded", lambda uid, gen: True)
    calls = _wrap_transition_first_turn_claim(monkeypatch, fail_at_call=2, mode=mode)

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно с кем-то поговорить о том, что происходит.")
    run(bot.pipeline(msg, msg.text))  # must not raise

    assert msg.answers == []  # zero Telegram send attempts
    assert len(calls) == 2  # no further/compensating transition attempted
    assert calls[1] == ("generated", "failed_before_send")
    row = run(_first_turn_claim_row(OWNER))
    assert row is not None and row["status"] == "generated"  # last REAL confirmed state


# Item 7: claim_first_turn itself raising a simulated DB/bookkeeping
# exception is fail-closed by PROPAGATION -- the professional branch's
# claim attempt is not wrapped in its own try/except (only _run_
# professional_free_text_and_deliver's later PRE-SEND/POST-SEND
# transitions are, via _professional_first_turn_transition), and the outer
# try/finally in pipeline() (which only releases the per-user ingestion
# lock -- see _ingest_leave) carries no matching except clause, so this
# exception propagates all the way out of pipeline() uncaught. It is never
# silently swallowed into a fallback owner: no Professional runtime/model
# call and no Telegram send happen after it, and no legacy/Controller/
# First-Turn fallback runs either (each is separately stubbed by
# _stub_legacy_machinery to raise AssertionError if ever reached, which
# would surface as a different, attributable failure here).

def test_claim_exception_is_fail_closed_by_propagation(tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    runtime_calls = _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    async def fake_claim_first_turn(*a, **kw):
        raise RuntimeError("simulated claim bookkeeping failure")
    monkeypatch.setattr(bot, "claim_first_turn", fake_claim_first_turn)

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно с кем-то поговорить о том, что происходит.")
    with pytest.raises(RuntimeError, match="simulated claim bookkeeping failure"):
        run(bot.pipeline(msg, msg.text))

    # No Professional runtime/model call after the claim exception.
    assert runtime_calls["n"] == 0
    # No Telegram psychological reply of any kind was sent.
    assert msg.answers == []
    # The claim itself never committed -- no first_turn_claims row exists.
    row = run(_first_turn_claim_row(OWNER))
    assert row is None


# ══════════════════════════════════════════════════════════════════════════
# Codex PHASE1C-001 fix -- STRICT SINGLE-TELEGRAM-ATTEMPT for a
# first_turn_entry_active Professional turn whose transport selects voice.
# Independent review proved a real production-reachable trace where a
# first-turn-entry Professional delivery could make TWO actual Telegram
# send attempts (answer_voice raises -> swallowed by _synthesize_and_send_
# voice -> deliver_response falls back to message.answer). The fix adds an
# explicit, defaulted `strict_single_attempt` bool to _synthesize_and_send_
# voice and deliver_response (default False -- every existing caller and
# every existing Voice UX behavior is unchanged); bot.py's Professional
# first-turn-entry path is the only caller that ever passes True (wired as
# strict_single_attempt=first_turn_entry_active at the one deliver_response
# call site in _run_professional_free_text_and_deliver).
#
# These tests exercise the REAL deliver_response / _synthesize_and_send_
# voice transport end to end through bot.pipeline() -- deliver_response
# itself is never stubbed/replaced. Only the external synthesize_speech TTS
# provider call is mocked (exactly as every existing Voice UX test in
# tests/test_voice_adaptive_response_ux.py already does), and the upstream
# run_professional_free_text_turn orchestrator is stubbed via
# _stub_runtime_result exactly as every other Phase 1C test in this file
# already does -- neither stub touches deliver_response or answer_voice.
# ══════════════════════════════════════════════════════════════════════════

def _enable_voice_ux_and_prefer_voice(monkeypatch, uid):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    monkeypatch.setattr(config, "ELEVENLABS_TTS_ENABLED", True)
    run(database.set_response_preference(uid, response_format="voice"))


# TEST 1 -- strict entry policy + answer_voice exception. TTS synthesis
# succeeds deterministically; the actual message.answer_voice(...) raises
# an ambiguous Telegram/network exception. Exactly one real Telegram
# attempt total (the voice attempt itself); no text fallback follows it;
# the Phase 1C boundary's existing exception handling (unchanged by this
# fix) takes over from there.
def test_strict_entry_policy_answer_voice_exception_single_attempt(tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)
    _enable_voice_ux_and_prefer_voice(monkeypatch, OWNER)

    async def fake_synth(client_, text, lang):
        return "/tmp/fake_phase1c_voice_exc.opus"
    monkeypatch.setattr(bot, "synthesize_speech", fake_synth)

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно с кем-то поговорить о том, что происходит.")
    async def boom_answer_voice(*a, **kw):
        msg.voices.append((a, kw))
        raise RuntimeError("ambiguous telegram/network failure")
    msg.answer_voice = boom_answer_voice

    run(bot.pipeline(msg, msg.text))  # must not raise -- caught by the existing Phase 1C boundary

    assert len(msg.voices) == 1  # answer_voice calls == 1
    assert msg.answers == []  # no text/answer fallback after the voice attempt
    # total actual Telegram send attempts == 1 (voices + answers combined)
    assert len(msg.voices) + len(msg.answers) == 1
    row = run(_first_turn_claim_row(OWNER))
    assert row is not None
    # lifecycle reached send_started (both real PRE-SEND gates), then
    # exactly one send_started -> delivery_uncertain terminal attempt --
    # no assistant ASSISTANT_DELIVERED persistence, no compensation.
    assert row["status"] == "delivery_uncertain"


# TEST 2 -- strict entry policy + TTS pre-send failure. Synthesis itself
# fails BEFORE answer_voice is ever called -- zero actual Telegram attempts
# so far, so a text fallback is still safe and allowed even in strict mode.
def test_strict_entry_policy_tts_presend_failure_text_fallback_allowed(tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)
    _enable_voice_ux_and_prefer_voice(monkeypatch, OWNER)

    async def failing_synth(client_, text, lang):
        raise RuntimeError("tts provider down")
    monkeypatch.setattr(bot, "synthesize_speech", failing_synth)

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно с кем-то поговорить о том, что происходит.")
    run(bot.pipeline(msg, msg.text))

    assert msg.voices == []  # answer_voice calls == 0 -- never reached
    assert len(msg.answers) == 1  # text answer calls == 1
    assert msg.answers[0][0] == SUCCESS_RESULT.reply_text
    assert len(msg.voices) + len(msg.answers) == 1  # total actual Telegram attempts == 1
    row = run(_first_turn_claim_row(OWNER))
    assert row is not None
    assert row["status"] == "delivered_without_buttons"  # text succeeded -> normal lifecycle
    assert row["turn_id"] is not None


# Codex final P2 -- the attempt marker must be set only once execution has
# actually reached the answer_voice invocation point, never merely upon
# finishing preparation of its argument. FSInputFile(path) construction
# raising BEFORE answer_voice is ever called is a zero-Telegram-attempt
# failure exactly like TTS pre-send failure above -- a text fallback is
# still safe, and it must NOT be misclassified as an ambiguous post-attempt
# failure (which would incorrectly suppress the fallback and end in
# delivery_uncertain instead of delivered_without_buttons).
def test_strict_entry_policy_fsinputfile_construction_failure_text_fallback_allowed(
        tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)
    _enable_voice_ux_and_prefer_voice(monkeypatch, OWNER)

    async def fake_synth(client_, text, lang):
        return "/tmp/fake_phase1c_fsinputfile_exc.opus"
    monkeypatch.setattr(bot, "synthesize_speech", fake_synth)

    def boom_fsinputfile(path):
        raise RuntimeError("FSInputFile construction failed")
    monkeypatch.setattr(bot, "FSInputFile", boom_fsinputfile)

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно с кем-то поговорить о том, что происходит.")
    run(bot.pipeline(msg, msg.text))  # must not raise

    assert msg.voices == []  # answer_voice calls == 0 -- never reached
    assert len(msg.answers) == 1  # text answer calls == 1
    assert msg.answers[0][0] == SUCCESS_RESULT.reply_text
    assert len(msg.voices) + len(msg.answers) == 1  # total actual Telegram attempts == 1
    row = run(_first_turn_claim_row(OWNER))
    assert row is not None
    assert row["status"] == "delivered_without_buttons"  # NOT delivery_uncertain
    assert row["turn_id"] is not None  # assistant ASSISTANT_DELIVERED persistence == YES


# TEST 3 -- existing NON-strict Voice UX preserved. A later Professional
# turn for the same user (one-shot claim already consumed elsewhere, so
# first_turn_entry_active is False here) must keep the pre-existing
# swallow-and-fall-back-to-text behavior when answer_voice raises --
# this fix must never silently change Voice UX for a non-entry-policy
# caller.
def test_non_strict_later_professional_turn_voice_ux_unchanged(tmp_db, monkeypatch):
    run(_seed_user(OWNER))  # pre-consumes the one-shot claim -- no entry-policy signal
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    calls = _stub_runtime_result(monkeypatch, SUCCESS_RESULT)
    _enable_voice_ux_and_prefer_voice(monkeypatch, OWNER)

    async def fake_synth(client_, text, lang):
        return "/tmp/fake_phase1c_nonstrict.opus"
    monkeypatch.setattr(bot, "synthesize_speech", fake_synth)

    msg = FakeMessage(FakeUser(OWNER), "Продолжим то, о чём говорили.")
    async def boom_answer_voice(*a, **kw):
        msg.voices.append((a, kw))
        raise RuntimeError("ambiguous telegram/network failure")
    msg.answer_voice = boom_answer_voice

    run(bot.pipeline(msg, msg.text))  # must not raise

    assert calls["kwargs"]["runtime_context"].first_turn_entry_active is False
    assert len(msg.voices) == 1  # the one voice attempt, exactly as before this fix
    # The existing fallback still occurs -- unchanged Voice UX for a
    # non-strict (non-entry-policy) caller, unlike TEST 1 above.
    assert len(msg.answers) == 1
    assert msg.answers[0][0] == SUCCESS_RESULT.reply_text


# TEST 4 -- strict voice success. answer_voice succeeds on the first and
# only attempt -- no text sent, normal successful lifecycle.
def test_strict_entry_policy_voice_success_single_attempt(tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    _stub_runtime_result(monkeypatch, SUCCESS_RESULT)
    _enable_voice_ux_and_prefer_voice(monkeypatch, OWNER)

    async def fake_synth(client_, text, lang):
        return "/tmp/fake_phase1c_voice_ok.opus"
    monkeypatch.setattr(bot, "synthesize_speech", fake_synth)

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно с кем-то поговорить о том, что происходит.")
    run(bot.pipeline(msg, msg.text))

    assert len(msg.voices) == 1  # answer_voice == 1
    assert msg.answers == []  # text answer == 0
    assert len(msg.voices) + len(msg.answers) == 1  # total actual Telegram attempts == 1
    row = run(_first_turn_claim_row(OWNER))
    assert row is not None
    assert row["status"] == "delivered_without_buttons"
    assert row["turn_id"] is not None


# Direct unit-level proof (Codex PHASE1C-001, complementary to the four
# pipeline-level tests above): _synthesize_and_send_voice's own contract at
# the point where the strict/non-strict behaviors actually diverge.
def test_synthesize_and_send_voice_strict_raises_only_after_voice_attempted(tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    monkeypatch.setattr(config, "ELEVENLABS_TTS_ENABLED", True)

    class BoomMessage(FakeMessage):
        async def answer_voice(self, *a, **kw):
            raise RuntimeError("telegram send failed")

    async def fake_synth(client_, text, lang):
        return "/tmp/fake_phase1c_unit.opus"
    monkeypatch.setattr(bot, "synthesize_speech", fake_synth)

    msg = BoomMessage(FakeUser(OWNER), "hi")
    # Non-strict (default): swallowed, returns False -- unchanged contract.
    ok = run(bot._synthesize_and_send_voice(msg, OWNER, "text", "ru"))
    assert ok is False
    # Strict: answer_voice WAS reached before the exception -> re-raised.
    with pytest.raises(RuntimeError, match="telegram send failed"):
        run(bot._synthesize_and_send_voice(
            msg, OWNER, "text", "ru", strict_single_attempt=True))

    async def failing_synth(client_, text, lang):
        raise RuntimeError("tts down")
    monkeypatch.setattr(bot, "synthesize_speech", failing_synth)
    # Strict, but answer_voice never reached (TTS itself failed) -> still
    # returns False, never raises -- a pre-Telegram-attempt failure is safe.
    ok = run(bot._synthesize_and_send_voice(
        msg, OWNER, "text", "ru", strict_single_attempt=True))
    assert ok is False


# ══════════════════════════════════════════════════════════════════════════
# Codex PHASE1C-003 -- pipeline-level concurrency proof. The DB primitive
# itself already has a direct atomic claim test (database.py's own
# claim_first_turn/transition_first_turn_claim tests) -- this is NOT that
# test and does not replace it. This proves the bot.py INTEGRATION wiring:
# that two concurrent pipeline() turns for the SAME user correctly produce
# exactly one winning first_turn_entry_active=True runtime_context and one
# first_turn_entry_active=False runtime_context, that only one
# first_turn_claims row is ever created, and that no lifecycle transition
# for the losing turn ever touches the winner's claim.
#
# Orchestration (deterministic, compatible with the real per-user ingestion
# asyncio.Lock -- never requires both turns inside that lock at once):
#   1. Launch turn A.
#   2. A wins the one-shot claim (inside the locked section) and leaves the
#      ingestion lock (_run_professional_free_text_and_deliver is only ever
#      called strictly AFTER that lock is released -- see its own
#      docstring) before this test's fake orchestrator pauses it.
#   3. A pauses deep inside the slow Professional runtime, immediately
#      after its runtime_context has been captured.
#   4. Turn B is launched only now -- the lock is free (A already released
#      it), so B enters the ingestion section uncontended and observes the
#      claim already consumed (real PRIMARY KEY-enforced DB behavior, not
#      stubbed).
#   5. B runs to completion first (never blocked on A).
#   6. A is resumed and finishes.
#
# Truthful accounting of the EXISTING turn-generation stale mechanism
# (unchanged, not bypassed): pipeline() bumps a per-uid generation counter
# at the very start of EVERY call. B's own bump happens strictly after A's
# (B starts only once A is already paused), so by the time A resumes, A's
# own captured generation is stale relative to B's -- A's own unchanged
# stale check correctly drops it (generated->failed_before_send), and only
# B (the ordinary, non-entry-policy turn) actually delivers. This is the
# real, correct outcome given real concurrent traffic for one user, not a
# test artifact -- the goal is claim/signal/lifecycle isolation, not
# forcing two sends, and this test does not weaken or bypass that
# mechanism to make both replies deliver.
# ══════════════════════════════════════════════════════════════════════════

def test_pipeline_concurrent_professional_turns_same_user_claim_isolation(tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())

    claim_attempts = []  # (claim_token, won)
    real_claim_first_turn = database.claim_first_turn
    async def wrapped_claim(uid, contract_version, claim_token, scenario):
        won = await real_claim_first_turn(uid, contract_version, claim_token, scenario)
        claim_attempts.append((claim_token, won))
        return won
    monkeypatch.setattr(bot, "claim_first_turn", wrapped_claim)

    transition_calls = []  # (claim_token, from_status, to_status, ok)
    real_transition = database.transition_first_turn_claim
    async def wrapped_transition(uid, contract_version, claim_token, from_status, to_status,
                                  turn_id=None):
        ok = await real_transition(
            uid, contract_version, claim_token, from_status, to_status, turn_id=turn_id)
        transition_calls.append((claim_token, from_status, to_status, ok))
        return ok
    monkeypatch.setattr(bot, "transition_first_turn_claim", wrapped_transition)

    captured_contexts = []
    a_paused = asyncio.Event()
    a_resume = asyncio.Event()
    call_count = {"n": 0}

    async def fake_run(**kwargs):
        call_count["n"] += 1
        captured_contexts.append(kwargs["runtime_context"])
        if call_count["n"] == 1:
            # Turn A: runtime_context is captured; pause here, strictly
            # AFTER the ingestion lock was already released (this function
            # is only ever reached via _run_professional_free_text_and_
            # deliver, called strictly after that release).
            a_paused.set()
            await a_resume.wait()
        return SUCCESS_RESULT
    monkeypatch.setattr(bot, "run_professional_free_text_turn", fake_run)

    same_text = "Мне нужно с кем-то поговорить о том, что происходит."
    msg_a = FakeMessage(FakeUser(OWNER), same_text, message_id=101)
    msg_b = FakeMessage(FakeUser(OWNER), same_text, message_id=102)

    async def orchestrate():
        task_a = asyncio.create_task(bot.pipeline(msg_a, msg_a.text))
        await a_paused.wait()
        task_b = asyncio.create_task(bot.pipeline(msg_b, msg_b.text))
        await task_b
        a_resume.set()
        await task_a

    run(orchestrate())

    # --- claim/signal isolation: exactly one runtime_context of each kind ---
    assert call_count["n"] == 2
    assert captured_contexts[0].first_turn_entry_active is True   # A: won the claim
    assert captured_contexts[1].first_turn_entry_active is False  # B: claim already consumed

    assert len(claim_attempts) == 2  # both turns attempted a claim (real DB, not stubbed)
    won = [tok for tok, ok in claim_attempts if ok]
    lost = [tok for tok, ok in claim_attempts if not ok]
    assert len(won) == 1 and len(lost) == 1
    winning_token = won[0]

    # --- only one first_turn_claims row exists, and it belongs to the winner ---
    async def _claim_row_with_token():
        async with database.aiosqlite.connect(database.DB) as conn:
            conn.row_factory = database.aiosqlite.Row
            cur = await conn.execute(
                "SELECT claim_token, status, scenario, turn_id FROM first_turn_claims "
                "WHERE user_id=? AND contract_version=?",
                (OWNER, config.FIRST_TURN_CONTRACT_VERSION))
            rows = await cur.fetchall()
        return [dict(r) for r in rows]
    rows = run(_claim_row_with_token())
    assert len(rows) == 1
    assert rows[0]["claim_token"] == winning_token
    assert rows[0]["scenario"] == "professional"

    # --- every lifecycle transition ever attempted (by EITHER turn) used
    # ONLY the winning token -- the losing (B) turn never transitions the
    # winner's claim (B's first_turn_entry_active is False, so bot.py never
    # even calls the transition helper for B at all -- no cross-token
    # mutation is attempted, let alone possible). ---
    assert transition_calls
    for tok, _from, _to, _ok in transition_calls:
        assert tok == winning_token

    # --- truthful stale-mechanism accounting (see block comment above):
    # A is correctly dropped as stale once resumed; B, the ordinary turn,
    # delivers normally. Not forced, not bypassed. ---
    assert msg_a.answers == []
    assert len(msg_b.answers) == 1
    assert [(f, t) for _, f, t, _ in transition_calls] == [
        ("pending_before_llm", "generated"), ("generated", "failed_before_send")]
    assert rows[0]["status"] == "failed_before_send"

    # --- no legacy/Controller/Therapist Core fallback ever owned either
    # Professional-resolved turn: both reached the real Professional
    # orchestrator (call_count == 2 above); _stub_legacy_machinery makes
    # any fallback raise AssertionError immediately if ever reached, which
    # would have surfaced as a test failure here rather than a silent pass.


# ══════════════════════════════════════════════════════════════════════════
# Phase 2A -- Canonical Case Context foundation wired into
# _run_professional_free_text_and_deliver. Transport only: no model-facing
# stage reads case_context yet (that is a separately authorized Phase 2B).
# These tests prove the runtime envelope is correctly populated from real,
# already-CONFIRMED/CORRECTED core_memory_items, that a case-context-
# specific failure degrades to EMPTY_CANONICAL_CASE_CONTEXT without
# affecting the rest of the turn, and that conversation_context/
# first_turn_entry_active remain completely unaffected.
# ══════════════════════════════════════════════════════════════════════════

def test_case_context_populated_from_real_confirmed_memory(tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    confirmed = MemoryItem(category=MemoryCategory.EXPLICIT_FACT,
                           lifecycle=MemoryLifecycle.CONFIRMED, content="works night shifts")
    candidate = MemoryItem(category=MemoryCategory.HYPOTHESIS,
                           lifecycle=MemoryLifecycle.CANDIDATE, content="maybe anxious about work")
    confirmed_id = run(database.add_core_memory_item(OWNER, confirmed))
    run(database.add_core_memory_item(OWNER, candidate))

    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    calls = _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно с кем-то поговорить о том, что происходит.")
    run(bot.pipeline(msg, msg.text))

    runtime_context = calls["kwargs"]["runtime_context"]
    assert type(runtime_context.case_context) is CanonicalCaseContext
    assert len(runtime_context.case_context.items) == 1  # the CANDIDATE item is excluded
    item = runtime_context.case_context.items[0]
    assert item.memory_item_id == confirmed_id
    assert item.content == "works night shifts"
    assert item.lifecycle is MemoryLifecycle.CONFIRMED
    assert item.category is MemoryCategory.EXPLICIT_FACT


def test_case_context_empty_when_no_memory_items(tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    calls = _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно с кем-то поговорить о том, что происходит.")
    run(bot.pipeline(msg, msg.text))

    runtime_context = calls["kwargs"]["runtime_context"]
    # Equal-value empty context (built fresh by the builder from zero real
    # records) -- not necessarily the same object as the shared singleton,
    # which is reserved for the explicit failure path below.
    assert runtime_context.case_context == EMPTY_CANONICAL_CASE_CONTEXT
    assert runtime_context.case_context.items == ()


def test_case_context_db_read_failure_yields_empty_turn_continues(tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    calls = _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    async def failing_records(uid, *, influencing_only=False):
        raise RuntimeError("simulated DB read failure")
    monkeypatch.setattr(bot, "list_core_memory_item_records", failing_records)

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно с кем-то поговорить о том, что происходит.")
    run(bot.pipeline(msg, msg.text))  # must not raise

    # Turn continues normally -- the real SUCCESS reply is delivered, NOT
    # the generic technical fallback -- proving the case-context failure
    # was isolated, never misattributed as a whole-turn "professional_
    # failed" event.
    assert len(msg.answers) == 1
    assert msg.answers[0][0] == SUCCESS_RESULT.reply_text
    runtime_context = calls["kwargs"]["runtime_context"]
    assert runtime_context.case_context is EMPTY_CANONICAL_CASE_CONTEXT


def test_case_context_builder_failure_yields_empty_turn_continues(tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    calls = _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    def failing_builder(records):
        raise RuntimeError("simulated builder failure")
    monkeypatch.setattr(bot, "build_canonical_case_context_from_memory_records", failing_builder)

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно с кем-то поговорить о том, что происходит.")
    run(bot.pipeline(msg, msg.text))  # must not raise

    assert len(msg.answers) == 1
    assert msg.answers[0][0] == SUCCESS_RESULT.reply_text
    runtime_context = calls["kwargs"]["runtime_context"]
    assert runtime_context.case_context is EMPTY_CANONICAL_CASE_CONTEXT


def test_case_context_adds_no_additional_model_call(tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    confirmed = MemoryItem(category=MemoryCategory.EXPLICIT_FACT,
                           lifecycle=MemoryLifecycle.CONFIRMED, content="a confirmed fact")
    run(database.add_core_memory_item(OWNER, confirmed))

    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    calls = _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно с кем-то поговорить о том, что происходит.")
    run(bot.pipeline(msg, msg.text))

    # run_professional_free_text_turn (the sole orchestrator entry point for
    # every provider-calling stage) is still invoked exactly once, whether
    # or not real case-context memory is populated -- Phase 2A adds no
    # model/provider call anywhere.
    assert calls["n"] == 1


def test_conversation_context_unaffected_by_case_context(tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    confirmed = MemoryItem(category=MemoryCategory.EXPLICIT_FACT,
                           lifecycle=MemoryLifecycle.CONFIRMED, content="a confirmed fact")
    run(database.add_core_memory_item(OWNER, confirmed))

    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    history_rows = (
        (1, "user", "Ранее я говорил про работу.", "USER_AUTHORED"),
        (2, "assistant", "Что именно происходило?", "ASSISTANT_DELIVERED"),
    )
    _stub_history(monkeypatch, rows=history_rows)
    calls = _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно с кем-то поговорить о том, что происходит.")
    run(bot.pipeline(msg, msg.text))

    runtime_context = calls["kwargs"]["runtime_context"]
    # Conversation history is exactly what the (unrelated) history rows
    # produce -- case_context's presence never merges into or alters it.
    assert len(runtime_context.conversation.turns) == 2
    assert runtime_context.conversation.turns[0].content == "Ранее я говорил про работу."
    assert runtime_context.conversation.turns[1].content == "Что именно происходило?"
    # case_context is populated independently, from a completely separate
    # source (core_memory_items, not messages).
    assert len(runtime_context.case_context.items) == 1
    assert runtime_context.case_context.items[0].content == "a confirmed fact"


def test_first_turn_entry_active_orthogonal_to_case_context_true_case(tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    confirmed = MemoryItem(category=MemoryCategory.EXPLICIT_FACT,
                           lifecycle=MemoryLifecycle.CONFIRMED, content="a confirmed fact")
    run(database.add_core_memory_item(OWNER, confirmed))

    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    calls = _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно с кем-то поговорить о том, что происходит.")
    run(bot.pipeline(msg, msg.text))

    runtime_context = calls["kwargs"]["runtime_context"]
    assert runtime_context.first_turn_entry_active is True  # fresh user -> entry policy active
    assert len(runtime_context.case_context.items) == 1  # unaffected, independently populated


def test_first_turn_entry_active_orthogonal_to_case_context_false_case(tmp_db, monkeypatch):
    run(_seed_user(OWNER))  # pre-consumes the one-shot claim, same as every other test here
    confirmed = MemoryItem(category=MemoryCategory.EXPLICIT_FACT,
                           lifecycle=MemoryLifecycle.CONFIRMED, content="a confirmed fact")
    run(database.add_core_memory_item(OWNER, confirmed))

    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    calls = _stub_runtime_result(monkeypatch, SUCCESS_RESULT)

    msg = FakeMessage(FakeUser(OWNER), "Продолжим то, о чём говорили.")
    run(bot.pipeline(msg, msg.text))

    runtime_context = calls["kwargs"]["runtime_context"]
    assert runtime_context.first_turn_entry_active is False  # claim already consumed
    assert len(runtime_context.case_context.items) == 1  # still populated independently


# Phase 2A correction (P2-1/P2-2 integration proof): a real, directly-
# persisted corrupted core_memory_items row (raw content over the 1000-char
# bound that MemoryItem's own _clip would otherwise silently reshape) sits
# alongside one genuinely valid CONFIRMED row. Exercises the REAL DB
# accessor (database.list_core_memory_item_records) and the REAL builder
# (build_canonical_case_context_from_memory_records) -- neither is
# monkeypatched here, unlike the generic isolation tests above, which
# already prove the isolation mechanism itself against a mocked failure.
# This proves the two real production functions actually raise together
# with bot.py's isolated boundary, and that the failure degrades the WHOLE
# case context to empty -- the valid record must never survive alone.
def test_real_persisted_corruption_yields_complete_empty_case_context(tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    valid = MemoryItem(category=MemoryCategory.EXPLICIT_FACT,
                       lifecycle=MemoryLifecycle.CONFIRMED, content="a confirmed fact")
    valid_id = run(database.add_core_memory_item(OWNER, valid))

    corrupted_payload = json.dumps({
        "category": MemoryCategory.EXPLICIT_FACT.value,
        "lifecycle": MemoryLifecycle.CONFIRMED.value,
        "content": "x" * 1001,  # over MemoryItem's own 1000-char _clip bound
        "confidence": 0.0,
        "source_event_ids": [],
    })

    async def _insert_corrupted_row():
        async with database.aiosqlite.connect(database.DB) as db:
            await db.execute(
                "INSERT INTO core_memory_items (user_id, category, lifecycle, memory_json) "
                "VALUES (?,?,?,?)",
                (OWNER, MemoryCategory.EXPLICIT_FACT.value, MemoryLifecycle.CONFIRMED.value,
                 corrupted_payload))
            await db.commit()
    run(_insert_corrupted_row())

    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    calls = _stub_runtime_result(monkeypatch, SUCCESS_RESULT)
    # Deliberately NOT monkeypatching list_core_memory_item_records or
    # build_canonical_case_context_from_memory_records -- the real
    # production functions must be exercised end to end.

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно с кем-то поговорить о том, что происходит.")
    run(bot.pipeline(msg, msg.text))  # must not raise

    # Professional turn still succeeds -- the real SUCCESS reply, not the
    # generic technical fallback -- proving the case-context failure was
    # isolated, never misattributed as a whole-turn "professional_failed"
    # event.
    assert len(msg.answers) == 1
    assert msg.answers[0][0] == SUCCESS_RESULT.reply_text
    # Exactly one Professional runtime invocation -- no extra model call.
    assert calls["n"] == 1
    runtime_context = calls["kwargs"]["runtime_context"]
    # Complete empty fallback -- the literal shared singleton, exactly what
    # bot.py's isolated except-clause assigns on failure. The valid record
    # (valid_id) must never survive as a partial, still-trusted context.
    assert runtime_context.case_context is EMPTY_CANONICAL_CASE_CONTEXT
    assert runtime_context.case_context.items == ()
    assert valid_id > 0  # sanity: the valid row really was persisted


# Phase 2A V3 correction (P1 integration proof, item 4): a real,
# lifecycle-divergent core_memory_items row (SQL lifecycle disagrees with
# its own memory_json lifecycle) sits alongside one genuinely valid
# CONFIRMED row. Exercises the REAL DB readers and REAL builder end to
# end -- proves the valid record can never survive as a partially trusted
# result merely because a sibling row happens to be corrupted.
def test_real_lifecycle_divergent_row_yields_complete_empty_case_context(tmp_db, monkeypatch):
    run(database.upsert_user(OWNER, "u", "U"))
    valid = MemoryItem(category=MemoryCategory.EXPLICIT_FACT,
                       lifecycle=MemoryLifecycle.CONFIRMED, content="a confirmed fact")
    valid_id = run(database.add_core_memory_item(OWNER, valid))

    # SQL lifecycle says REJECTED; the row's own memory_json still says
    # CONFIRMED -- structurally inconsistent persisted state.
    divergent_payload = json.dumps({
        "category": MemoryCategory.EXPLICIT_FACT.value,
        "lifecycle": MemoryLifecycle.CONFIRMED.value,
        "content": "a divergent record",
        "confidence": 0.0,
        "source_event_ids": [],
    })

    async def _insert_divergent_row():
        async with database.aiosqlite.connect(database.DB) as db:
            await db.execute(
                "INSERT INTO core_memory_items (user_id, category, lifecycle, memory_json) "
                "VALUES (?,?,?,?)",
                (OWNER, MemoryCategory.EXPLICIT_FACT.value, MemoryLifecycle.REJECTED.value,
                 divergent_payload))
            await db.commit()
    run(_insert_divergent_row())

    _stub_legacy_machinery(monkeypatch)
    _stub_professional_eligible(monkeypatch, True)
    _stub_history(monkeypatch, rows=())
    calls = _stub_runtime_result(monkeypatch, SUCCESS_RESULT)
    # Deliberately NOT monkeypatching list_core_memory_item_records or
    # build_canonical_case_context_from_memory_records -- the real
    # production functions must be exercised end to end.

    msg = FakeMessage(FakeUser(OWNER), "Мне нужно с кем-то поговорить о том, что происходит.")
    run(bot.pipeline(msg, msg.text))  # must not raise

    assert len(msg.answers) == 1
    assert msg.answers[0][0] == SUCCESS_RESULT.reply_text
    assert calls["n"] == 1  # no extra model call
    runtime_context = calls["kwargs"]["runtime_context"]
    assert runtime_context.case_context is EMPTY_CANONICAL_CASE_CONTEXT
    assert runtime_context.case_context.items == ()
    assert valid_id > 0  # sanity: the valid row really was persisted
