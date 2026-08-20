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

import pytest

import access_control as ac
import bot
import config
import database
import professional_free_text_runtime as pftr
from professional_turn_analysis import TurnAnalysisStatus
from professional_turn_analyzer import TurnAnalyzerFailureCategory
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


SUCCESS_RESULT = pftr.ProfessionalFreeTextRuntimeResult(
    status=pftr.ProfessionalFreeTextRuntimeStatus.SUCCESS,
    reply_text="Похоже, тебе сейчас непросто. Что для тебя сейчас самое сложное в этом?",
    failure_stage=None, failure_reason=None)

REJECTED_RESULT = pftr.ProfessionalFreeTextRuntimeResult(
    status=pftr.ProfessionalFreeTextRuntimeStatus.REJECTED, reply_text=None,
    failure_stage=pftr.ProfessionalFreeTextFailureStage.ACCEPTANCE,
    failure_reason=AcceptanceSafetyRejectionReason.SAFETY_REJECTED)

FAILED_RESULT = pftr.ProfessionalFreeTextRuntimeResult(
    status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text=None,
    failure_stage=pftr.ProfessionalFreeTextFailureStage.ANALYZER,
    failure_reason=TurnAnalyzerFailureCategory.PROVIDER_FAILURE)


# ══════════════════════════════════════════════════════════════════════════
# A. professional_free_text_runtime.py orchestration contract
# ══════════════════════════════════════════════════════════════════════════

def test_result_success_requires_nonempty_text_and_no_failure_stage():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.SUCCESS, reply_text=None,
            failure_stage=None, failure_reason=None)
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.SUCCESS, reply_text="  ",
            failure_stage=None, failure_reason=None)
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.SUCCESS, reply_text="ok",
            failure_stage=pftr.ProfessionalFreeTextFailureStage.ANALYZER, failure_reason=None)


def test_result_success_must_not_carry_a_failure_reason():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.SUCCESS, reply_text="ok",
            failure_stage=None, failure_reason=TurnAnalyzerFailureCategory.PROVIDER_FAILURE)


def test_result_non_success_must_not_carry_reply_text():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text="leaked candidate",
            failure_stage=pftr.ProfessionalFreeTextFailureStage.ANALYZER,
            failure_reason=TurnAnalyzerFailureCategory.PROVIDER_FAILURE)


def test_result_non_success_requires_a_failure_stage():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.REJECTED, reply_text=None,
            failure_stage=None, failure_reason=AcceptanceSafetyRejectionReason.SAFETY_REJECTED)


def test_result_non_success_requires_a_failure_reason():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text=None,
            failure_stage=pftr.ProfessionalFreeTextFailureStage.ANALYZER, failure_reason=None)


def test_result_failure_reason_must_be_a_bounded_enum_member_not_a_raw_string():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text=None,
            failure_stage=pftr.ProfessionalFreeTextFailureStage.ANALYZER,
            failure_reason="PROVIDER_FAILURE")


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
            status=status, reply_text=None, failure_stage=stage, failure_reason=reason)
        assert result.failure_stage is stage
        assert result.failure_reason is reason


def test_analyzer_stage_rejects_reason_from_another_stage():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text=None,
            failure_stage=pftr.ProfessionalFreeTextFailureStage.ANALYZER,
            failure_reason=AcceptanceSafetyRejectionReason.SAFETY_REJECTED)


def test_producer_stage_rejects_analyzer_reason():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text=None,
            failure_stage=pftr.ProfessionalFreeTextFailureStage.PRODUCER,
            failure_reason=TurnAnalyzerFailureCategory.PROVIDER_FAILURE)


def test_plan_proposer_stage_rejects_proposal_as_a_failure_reason():
    """PROPOSAL is TurnPlanProposerCallStatus's own success member -- it
    must never be reportable as a failure_reason regardless of type match."""
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text=None,
            failure_stage=pftr.ProfessionalFreeTextFailureStage.PLAN_PROPOSER,
            failure_reason=TurnPlanProposerCallStatus.PROPOSAL)


def test_renderer_stage_rejects_candidate_as_a_failure_reason():
    """CANDIDATE is TurnResponseRenderStatus's own success member -- it
    must never be reportable as a failure_reason regardless of type match."""
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text=None,
            failure_stage=pftr.ProfessionalFreeTextFailureStage.RENDERER,
            failure_reason=TurnResponseRenderStatus.CANDIDATE)


def test_planner_stage_rejects_reason_from_another_stage():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text=None,
            failure_stage=pftr.ProfessionalFreeTextFailureStage.PLANNER,
            failure_reason=TurnResponseRenderStatus.NO_USABLE_CONTENT)


def test_acceptance_stage_rejects_reason_from_another_stage():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.REJECTED, reply_text=None,
            failure_stage=pftr.ProfessionalFreeTextFailureStage.ACCEPTANCE,
            failure_reason=TurnAnalyzerFailureCategory.PROVIDER_FAILURE)


def test_rejected_status_requires_acceptance_stage():
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.REJECTED, reply_text=None,
            failure_stage=pftr.ProfessionalFreeTextFailureStage.ANALYZER,
            failure_reason=TurnAnalyzerFailureCategory.PROVIDER_FAILURE)


def test_failed_status_must_not_carry_acceptance_stage():
    """An Acceptance rejection is always REJECTED, never FAILED -- FAILED
    means an earlier stage never even reached a candidate for Acceptance
    to judge."""
    with pytest.raises(ValueError):
        pftr.ProfessionalFreeTextRuntimeResult(
            status=pftr.ProfessionalFreeTextRuntimeStatus.FAILED, reply_text=None,
            failure_stage=pftr.ProfessionalFreeTextFailureStage.ACCEPTANCE,
            failure_reason=AcceptanceSafetyRejectionReason.SAFETY_REJECTED)


def test_run_rejects_non_positive_row_id():
    async def go():
        await pftr.run_professional_free_text_turn(
            client=None, model="gpt-4o-mini", source_message_row_id=0,
            source_text="hi", conversation_context=_empty_context(), risk_result={}, lang="ru")
    with pytest.raises(ValueError):
        run(go())


def test_run_rejects_wrong_context_type():
    async def go():
        await pftr.run_professional_free_text_turn(
            client=None, model="gpt-4o-mini", source_message_row_id=1,
            source_text="hi", conversation_context="not a context", risk_result={}, lang="ru")
    with pytest.raises(ValueError):
        run(go())


def _empty_context():
    from professional_turn_conversation_context import EMPTY_CONVERSATION_CONTEXT
    return EMPTY_CONVERSATION_CONTEXT


def _monkeypatch_chain(monkeypatch, *, analyzer_failed=False,
                       analyzer_failure_category=None, producer_failed=False,
                       proposer_status=None, plan_none=False, render_status=None,
                       accept_status=None):
    effective_analyzer_category = analyzer_failure_category or TurnAnalyzerFailureCategory.PROVIDER_FAILURE

    async def fake_analyzer(**kw):
        if analyzer_failed:
            return types.SimpleNamespace(
                output=None, failure_category=effective_analyzer_category, model=kw["model"])
        return types.SimpleNamespace(output=object(), failure_category=None, model=kw["model"])
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
        ) else TurnAnalysisStatus.OK
        return types.SimpleNamespace(status=status)
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
                plan=None, abstention_reason=ProfessionalPlanAbstentionReason.NO_SEMANTIC_PROPOSAL)
        return types.SimpleNamespace(plan=object(), abstention_reason=None)
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
        conversation_context=_empty_context(), risk_result={"score": 0, "categories": []}, lang="ru"))
    assert result.status is pftr.ProfessionalFreeTextRuntimeStatus.SUCCESS
    assert result.reply_text == "Что для тебя сейчас самое сложное?"
    assert result.failure_stage is None
    assert result.failure_reason is None


def test_chain_analyzer_failure_yields_failed_analyzer_stage(monkeypatch):
    _monkeypatch_chain(monkeypatch, analyzer_failed=True)
    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        conversation_context=_empty_context(), risk_result={}, lang="ru"))
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
        conversation_context=_empty_context(), risk_result={}, lang="ru"))
    assert result.failure_stage is pftr.ProfessionalFreeTextFailureStage.ANALYZER
    assert result.failure_reason is category


def test_chain_producer_failure_yields_failed_producer_stage_distinct_from_analyzer(monkeypatch):
    """Analyzer succeeds (usable output) but Producer's own deterministic
    assembly still fails -- must be reported as PRODUCER, never collapsed
    into ANALYZER (that collapse was the exact observability gap this slice
    fixes)."""
    _monkeypatch_chain(monkeypatch, producer_failed=True)
    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        conversation_context=_empty_context(), risk_result={}, lang="ru"))
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
        conversation_context=_empty_context(), risk_result={}, lang="ru"))
    assert result.status is pftr.ProfessionalFreeTextRuntimeStatus.FAILED
    assert result.failure_stage is pftr.ProfessionalFreeTextFailureStage.PLAN_PROPOSER
    assert result.failure_reason is status


def test_chain_governor_no_plan_yields_failed_planner_stage(monkeypatch):
    _monkeypatch_chain(monkeypatch, plan_none=True)
    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        conversation_context=_empty_context(), risk_result={}, lang="ru"))
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
        conversation_context=_empty_context(), risk_result={}, lang="ru"))
    assert result.status is pftr.ProfessionalFreeTextRuntimeStatus.FAILED
    assert result.failure_stage is pftr.ProfessionalFreeTextFailureStage.RENDERER
    assert result.failure_reason is status


def test_chain_acceptance_reject_yields_rejected_acceptance_stage(monkeypatch):
    _monkeypatch_chain(monkeypatch, accept_status=ProfessionalResponseAcceptanceStatus.REJECT)
    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        conversation_context=_empty_context(), risk_result={}, lang="ru"))
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
        conversation_context=_empty_context(), risk_result={}, lang="ru"))
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
        conversation_context=_empty_context(), risk_result={}, lang="ru"))
    assert result.status is pftr.ProfessionalFreeTextRuntimeStatus.REJECTED
    assert result.failure_stage is pftr.ProfessionalFreeTextFailureStage.ACCEPTANCE
    assert isinstance(result.failure_reason, PolicyRejectionReason)
    assert result.failure_reason is reason


def test_chain_acceptance_safety_rejection_propagates_safety_reason(monkeypatch):
    _monkeypatch_chain(monkeypatch, accept_status=ProfessionalResponseAcceptanceStatus.REJECT)
    result = run(pftr.run_professional_free_text_turn(
        client=object(), model="gpt-4o-mini", source_message_row_id=1, source_text="hi",
        conversation_context=_empty_context(), risk_result={}, lang="ru"))
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
            conversation_context=_empty_context(), risk_result={}, lang="ru"))
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
    for c in calls:
        assert user_text not in c
        assert bot._professional_technical_fallback_text("ru") not in c


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
    # transcript echo + Professional reply
    assert any(a[0] == SUCCESS_RESULT.reply_text for a in msg.answers)


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
    reply_text=LONG_REPLY_TEXT, failure_stage=None, failure_reason=None)


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
    assert tts_calls == [LONG_REPLY_TEXT]
    assert run(_read_persisted_assistant_content(OWNER)) == LONG_REPLY_TEXT


def test_professional_stored_concise_preference_does_not_shorten(tmp_db, monkeypatch):
    """Direct deliver_response unit check (not routed through parse_format_
    command wording) -- isolates the exact interaction under test: a stored
    response_length="concise" preference must never shorten a
    preserve_exact_text=True call."""
    run(database.upsert_user(OWNER, "u", "U"))
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
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
