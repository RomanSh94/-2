"""Professional Core V2 -- Turn Response Acceptance Governor V1.

Deterministic, offline composer of three already-existing/sibling boundaries
into one typed ACCEPT/REJECT decision for an already-rendered candidate
reply: professional_turn_response_fidelity_validator.validate_response_fidelity,
professional_turn_response_policy_validator.validate_response_policy, and the
existing safety_validator.validate_response_with_context. It contains NO
regex or cue vocabulary of its own -- every actual content rule lives in one
of those three called components, never here.

Acceptance V1 is invoked ONLY after professional_turn_response_renderer has
already returned TurnResponseRenderStatus.CANDIDATE. It does not accept a
TurnResponseRenderResult and does not classify PROVIDER_FAILURE,
NO_USABLE_CONTENT, or STRUCTURALLY_INVALID_RESPONSE -- Renderer transport/
failure statuses remain an upstream orchestrator's responsibility, never
this module's. It receives an already-extracted candidate_text string.

Ownership boundaries, verified against current repository code, not assumed:
  - Fallback selection/text/delivery: NOT owned here. The existing
    safety_validator.select_fallback is the risk-aware selection helper a
    future caller invokes directly after a REJECT; this module does not
    import or call it.
  - Crisis: NOT owned here. bot.py's trigger_crisis / risk_detector.classify
    path runs earlier and separately in the live runtime and is never
    invoked, reclassified, or reasoned about by this module.
  - Trace: NOT owned here. traced_response.traced_response_builder already
    fails closed on any build_response() exception; a future latent-path
    caller may convert a REJECT from this module into such an exception from
    its own build_response callable. This module does not import
    traced_response.
  - risk_result: consumed strictly read-only, passed through unchanged to
    validate_response_with_context, never mutated, never recomputed, never
    used to decide crisis status.
  - source_text: passed through solely to satisfy
    validate_response_with_context's existing user_last_message parameter.
    This module does not itself inspect, interpret, normalize, derive from,
    log, persist, or expose it -- and the current safety_validator
    implementation does not consume its content either (verified: its
    user_last_message parameter is never referenced in that function's
    body).
  - lang: pass-through only to the existing safety API. Verified that
    neither validate_response nor validate_response_with_context currently
    branches any check on it (their forbidden-phrase/certainty lists mix
    RU+EN unconditionally); this module does not interpret it, and the
    called Policy validator's own patterns are RU+EN unconditional
    regardless of lang.

Semantic non-guarantees (unchanged from every called component): semantic
objective fidelity, semantic move fidelity beyond deterministic surface
proxies, clarification-target fidelity, source-grounded reflection, and
absence of a second semantic move remain UNPROVEN by this module or by
anything it calls. This is not a blocker for offline use or shadow
evaluation with no user-visible effect; it is a blocker for any claim that
broad user-facing rollout is cleared by this contract alone.

Only imports: __future__, dataclasses, enum, professional_turn_planner
(ProfessionalTurnPlan only), professional_turn_response_fidelity_validator
(FidelityRejectionReason, ResponseFidelityStatus, validate_response_fidelity),
professional_turn_response_policy_validator (PolicyRejectionReason,
PolicyStatus, validate_response_policy), safety_validator
(validate_response_with_context only). No re, no model, no network, no DB,
no persistence, no Telegram, no runtime wiring, no fallback, no trace.
Python 3.10 target (prod 3.10.12).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from professional_turn_planner import ProfessionalTurnPlan
from professional_turn_response_fidelity_validator import (
    FidelityRejectionReason,
    ResponseFidelityStatus,
    validate_response_fidelity,
)
from professional_turn_response_policy_validator import (
    PolicyRejectionReason,
    PolicyStatus,
    validate_response_policy,
)
from safety_validator import validate_response_with_context


class ProfessionalResponseAcceptanceStatus(str, Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"


class AcceptanceSafetyRejectionReason(str, Enum):
    """A single, deliberately coarse member. The existing
    safety_validator.validate_response_with_context returns a free-form
    diagnostic string on rejection (e.g. an f-string naming the matched
    forbidden phrase) -- that string is never designed as a public contract
    and is never propagated across this boundary; every safety rejection is
    reported uniformly as SAFETY_REJECTED. Detailed diagnostics remain
    available through this codebase's existing validator-block logging path
    at the caller's own layer, not through this result type."""
    SAFETY_REJECTED = "SAFETY_REJECTED"


# A frozen closed union of the three possible rejection-reason enum classes.
# Python 3.10+: type.__or__/type.__ror__ (PEP 604) make `X | Y` between class
# objects a real runtime types.UnionType, and isinstance()/issubclass()
# against such a UnionType are supported starting in 3.10 -- so
# ProfessionalResponseAcceptanceResult.__post_init__ below can enforce
# membership with a single isinstance check. A plain str that happens to
# equal one of the wrapped enums' values is NOT an instance of any of the
# three classes, so it correctly fails that check (raw strings are
# rejected), exactly like every other closed result type in this codebase.
AcceptanceRejectionReason = (
    FidelityRejectionReason | PolicyRejectionReason | AcceptanceSafetyRejectionReason
)


@dataclass(frozen=True)
class ProfessionalResponseAcceptanceResult:
    """status is always one of the two closed ProfessionalResponseAcceptanceStatus
    members. reason is None if and only if status is ACCEPT; otherwise reason
    is exactly one member of FidelityRejectionReason, PolicyRejectionReason,
    or AcceptanceSafetyRejectionReason. No user text, no candidate/model
    text, and no free-form safety_validator string ever crosses this
    boundary."""
    status: ProfessionalResponseAcceptanceStatus
    reason: AcceptanceRejectionReason | None

    def __post_init__(self):
        if not isinstance(self.status, ProfessionalResponseAcceptanceStatus):
            raise ValueError(
                "ProfessionalResponseAcceptanceResult.status must be a "
                f"ProfessionalResponseAcceptanceStatus, got {type(self.status)!r}")
        if self.status is ProfessionalResponseAcceptanceStatus.ACCEPT:
            if self.reason is not None:
                raise ValueError(
                    "ProfessionalResponseAcceptanceResult: status ACCEPT requires reason=None")
        else:
            if not isinstance(self.reason, AcceptanceRejectionReason):
                raise ValueError(
                    "ProfessionalResponseAcceptanceResult: status REJECT requires reason "
                    "to be exactly one FidelityRejectionReason, PolicyRejectionReason, "
                    f"or AcceptanceSafetyRejectionReason member, got {self.reason!r}")


def accept_professional_response(
        *,
        plan: ProfessionalTurnPlan,
        candidate_text: str,
        source_text: str,
        risk_result: dict,
        lang: str,
) -> ProfessionalResponseAcceptanceResult:
    """Deterministic, synchronous, offline composition of Fidelity V1 ->
    Policy V1 -> the existing validate_response_with_context, in that exact
    first-fail order. Called only for an already-successful Renderer
    CANDIDATE; never accepts or classifies a TurnResponseRenderResult. Wrong
    public argument types, an invalid plan, or an empty/whitespace-only
    candidate_text/source_text/lang are caller/adapter defects and raise
    ValueError before any downstream validator is invoked. validate_response
    is never called directly -- validate_response_with_context already calls
    it as its own first step, and calling both would duplicate that scan."""
    if not isinstance(plan, ProfessionalTurnPlan):
        raise ValueError(
            "accept_professional_response: plan must be a ProfessionalTurnPlan, "
            f"got {type(plan)!r}")
    if type(candidate_text) is not str:
        raise ValueError(
            "accept_professional_response: candidate_text must be a str, "
            f"got {type(candidate_text)!r}")
    if not candidate_text or not candidate_text.strip():
        raise ValueError(
            "accept_professional_response: candidate_text must be non-empty "
            "and not whitespace-only")
    if type(source_text) is not str:
        raise ValueError(
            "accept_professional_response: source_text must be a str, "
            f"got {type(source_text)!r}")
    if not source_text or not source_text.strip():
        raise ValueError(
            "accept_professional_response: source_text must be non-empty "
            "and not whitespace-only")
    if type(risk_result) is not dict:
        raise ValueError(
            "accept_professional_response: risk_result must be a dict, "
            f"got {type(risk_result)!r}")
    if type(lang) is not str:
        raise ValueError(
            f"accept_professional_response: lang must be a str, got {type(lang)!r}")
    if not lang or not lang.strip():
        raise ValueError(
            "accept_professional_response: lang must be non-empty and not whitespace-only")

    fidelity_result = validate_response_fidelity(plan, candidate_text)
    if fidelity_result.status is ResponseFidelityStatus.REJECT:
        return ProfessionalResponseAcceptanceResult(
            status=ProfessionalResponseAcceptanceStatus.REJECT,
            reason=fidelity_result.reason)

    policy_result = validate_response_policy(plan, candidate_text)
    if policy_result.status is PolicyStatus.REJECT:
        return ProfessionalResponseAcceptanceResult(
            status=ProfessionalResponseAcceptanceStatus.REJECT,
            reason=policy_result.reason)

    # validate_response_with_context already calls validate_response as its
    # own first line -- calling validate_response separately here would
    # duplicate that scan and risk the two call sites silently drifting.
    is_safe, _free_form_reason = validate_response_with_context(
        candidate_text, source_text, risk_result, lang)
    if not is_safe:
        return ProfessionalResponseAcceptanceResult(
            status=ProfessionalResponseAcceptanceStatus.REJECT,
            reason=AcceptanceSafetyRejectionReason.SAFETY_REJECTED)

    return ProfessionalResponseAcceptanceResult(
        status=ProfessionalResponseAcceptanceStatus.ACCEPT, reason=None)
