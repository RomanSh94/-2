"""Professional Core V2 -- Trusted UI Action Context V1 (offline trust boundary).

OFFLINE DOMAIN/GOVERNANCE MODULE ONLY. This module defines a deterministic,
pure, closed boundary between untrusted UI-transport values (what a future
Telegram callback would carry) and the trusted, closed vocabulary the
Professional Core may reason about. It performs no I/O of any kind: no
network call, no model call, no database access, no Telegram delivery, no
environment reads, no secret access, no filesystem access, no time/random
behavior. It is not imported by bot.py, is not wired into
conversation_controller.py, and does not register any callback handler.
There is no runtime wiring in this slice.

CORE PROBLEM THIS SLICE SOLVES -- a button tap is not user free text. It
must never be converted into fabricated user speech and sent through a text
Analyzer as though the user literally typed it (for example, an
ANXIETY_STRESS entry tap becoming a synthetic sentence, or a
NEEDS_SCAFFOLDING tap becoming a synthetic "I don't know"). A UI action must
remain a structurally distinct provenance channel. The target architecture:

    UNTRUSTED UI TRANSPORT VALUE
            -> DETERMINISTIC CANONICALIZATION
            -> TRUSTED UI EVENT / NEXT-TURN CONTROL DIRECTIVE
            -> [FUTURE, separately reviewed Planner/response integration]

This module implements only the first three layers.

TWO DISTINCT TRUST BOUNDARIES -- entry-triage selections and mid-conversation
reply-affordance selections have different semantics and different
validation requirements, and are deliberately kept as two separate families
of types rather than one arbitrary dict/string structure:
  - ENTRY_TRIAGE_SELECTION: UntrustedEntryTriageSelection ->
    canonicalize_entry_triage_selection -> EntryTriageSelectionResult /
    TrustedEntryTriageDirective.
  - REPLY_AFFORDANCE_SELECTION: UntrustedReplyAffordanceSelection, checked
    against a ReplyAffordanceOfferContext -> canonicalize_reply_affordance_
    selection -> ReplyAffordanceSelectionResult / TrustedReplyDirective.

WHY THIS IS REQUIRED BEFORE TELEGRAM -- the existing Professional Core V2
pipeline is source_text -> Analyzer -> TurnAnalysisResult, and
TurnAnalysisResult + an untrusted plan proposal -> the Planner Governor ->
ProfessionalTurnPlan. The current Planner has no trusted UI-action input.
This module does not solve that gap by injecting synthetic source_text,
manufacturing fake evidence, manufacturing InteractionSignal values,
converting SKIP_CURRENT_QUESTION into InteractionSignal.NO_QUESTIONS, or
converting NEEDS_SCAFFOLDING into user evidence -- none of that happens
here, and this module does not modify ProfessionalTurnPlan. The new UI
control channel is deliberately a parallel, explicit channel.

CURRENT PLANNER CAPABILITY BOUNDARY -- Planner V1 has only its existing
narrow objective/move vocabulary (professional_turn_planner.py). This
module must not pretend every SKIP_EXACT_CURRENT_QUESTION directive can
already be represented as a new ProfessionalTurnPlan. It does not force a
skip into REPAIR, does not force it into NO_QUESTIONS, does not
automatically reinterpret it as CLOSE or ESTABLISH_CONTACT, and does not
add a new Planner objective/move. TrustedReplyDirective is a parallel
control input that a future, separately reviewed integration slice must
account for explicitly -- this prevents the product from claiming the
current Planner already knows what to do with a skip.

NO ANALYZER SYNTHESIS -- nothing in this module converts an
EntryTriageCategory, a ReplyAffordanceAction, a TrustedEntryTriageDirective,
or a TrustedReplyDirective into source_text, and nothing here constructs an
EvidenceItem, an EvidenceRef, or an InteractionRequest from a UI selection.
A future Analyzer may still analyze actual user-typed free text
independently; a UI selection is additional structured context, never
replacement speech.

NO INTENT OVERRIDE -- entry-triage and reply-affordance selections do not
create or override therapeutic_domain.Intent. There is no
category-to-intent conversion, no reply-action-to-intent conversion, and no
generic intent-override API anywhere in this module.

NEEDS_SCAFFOLDING semantics (SCAFFOLD_CURRENT_TARGET) -- the conversation
continues; the user has not substantively answered and has not declined the
current professional target; the user is asking for a lower-effort way to
respond; the prior professional target remains available; this event is not
evidence, not an Intent, not InteractionSignal.NO_QUESTIONS, and not
resistance. Future response logic (not implemented here) may scaffold the
answering task; this module never generates that scaffolded language and
never adds contextual answer-choice buttons.

SKIP_CURRENT_QUESTION semantics (SKIP_EXACT_CURRENT_QUESTION) -- the
conversation continues; only the exact current question is declined, not
the whole conversation; the professional target is not automatically
rejected; the reason for skipping is unknown; this action is not evidence,
not resistance, not InteractionSignal.NO_QUESTIONS, and not
RepairConstraint.QUESTION_OVERLOAD. A future integration must not simply
repeat the same question, and must not coercively paraphrase the same
question merely to force an answer -- this module does not choose the
replacement professional move; that is later Planner/response-integration
work. The fuller structured meaning of both actions already lives in
professional_reply_affordances.meaning_for_action -- this module does not
duplicate it.

Only imports: __future__, dataclasses, enum, professional_reply_affordances
(EntryTriageCategory, EntryFollowupFocus, ReplyAffordanceAction,
followup_focus_for_category, derive_reply_affordances), and
professional_turn_planner.ProfessionalTurnPlan. No bot.py import, no
conversation_controller.py import, no Telegram import, no database import.

Python 3.10 target (prod 3.10.12): `str, Enum` mix-ins, not `StrEnum` (3.11+).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from professional_reply_affordances import (
    EntryFollowupFocus,
    EntryTriageCategory,
    ReplyAffordanceAction,
    derive_reply_affordances,
    followup_focus_for_category,
)
from professional_turn_planner import ProfessionalTurnPlan


# ── Shared opaque correlation token -------------------------------------------

# Conservative engineering bound -- not a business rule, just a sane ceiling
# on an opaque correlation string.
_ORIGIN_TURN_TOKEN_MAX_LENGTH = 128


def _validate_origin_turn_token(token) -> None:
    """origin_turn_token is an opaque correlation value only -- it carries no
    Telegram/DB/business semantics and is never authentication. It is
    stored exactly as supplied: no stripping, casefolding, or truncation."""
    if type(token) is not str:
        raise ValueError(f"origin_turn_token must be exactly str, got {type(token)!r}")
    if not token:
        raise ValueError("origin_turn_token must be non-empty")
    if token.strip() == "":
        raise ValueError("origin_turn_token must not be whitespace-only")
    if len(token) > _ORIGIN_TURN_TOKEN_MAX_LENGTH:
        raise ValueError(
            "origin_turn_token exceeds the "
            f"{_ORIGIN_TURN_TOKEN_MAX_LENGTH}-character bound")


# ── Entry triage: untrusted transport -> trusted directive --------------------

@dataclass(frozen=True)
class UntrustedEntryTriageSelection:
    """Untrusted UI transport value carrying an entry-triage category
    selection. It is not trusted merely because it originated from a UI
    callback. category may be an EntryTriageCategory member or an ordinary
    str -- an unknown string survives this construction and is rejected at
    the deterministic semantic boundary (canonicalize_entry_triage_
    selection), never here. Wrong Python types fail construction. Carries
    nothing else: no user text, no Intent, no diagnosis, no evidence, no
    goal, no consent, no method, no treatment, no Telegram callback data,
    no database ID."""
    category: EntryTriageCategory | str

    def __post_init__(self):
        if not isinstance(self.category, str):
            raise ValueError(
                "UntrustedEntryTriageSelection.category must be an "
                f"EntryTriageCategory or str, got {type(self.category)!r}")


@dataclass(frozen=True)
class TrustedEntryTriageDirective:
    """The trusted outcome of a canonicalized entry-triage selection. Means
    only: the user voluntarily selected this approximate entry area, and
    this is the already-approved first exploration focus associated with it
    (see professional_reply_affordances.followup_focus_for_category, which
    this type's constructor validates against -- a mismatched
    category/followup_focus pair cannot be constructed). It is NOT an
    Intent, NOT evidence, NOT a diagnosis, NOT a factual claim about cause,
    NOT a treatment selection, and NOT synthetic user speech."""
    category: EntryTriageCategory
    followup_focus: EntryFollowupFocus

    def __post_init__(self):
        if not isinstance(self.category, EntryTriageCategory):
            raise ValueError(
                "TrustedEntryTriageDirective.category must be an "
                f"EntryTriageCategory, got {type(self.category)!r}")
        if not isinstance(self.followup_focus, EntryFollowupFocus):
            raise ValueError(
                "TrustedEntryTriageDirective.followup_focus must be an "
                f"EntryFollowupFocus, got {type(self.followup_focus)!r}")
        expected_focus = followup_focus_for_category(self.category)
        if self.followup_focus is not expected_focus:
            raise ValueError(
                "TrustedEntryTriageDirective.followup_focus does not match "
                f"the sealed mapping for {self.category.value}: expected "
                f"{expected_focus.value}, got {self.followup_focus.value}")


class EntryTriageSelectionStatus(str, Enum):
    """Closed deterministic outcome vocabulary for one
    canonicalize_entry_triage_selection call."""
    ACCEPTED = "ACCEPTED"
    SEMANTIC_VALUE_INVALID = "SEMANTIC_VALUE_INVALID"


@dataclass(frozen=True)
class EntryTriageSelectionResult:
    """The outcome envelope for one canonicalize_entry_triage_selection
    call: either a trusted directive (status=ACCEPTED) or an explicit
    rejection status with no directive -- never both, never neither."""
    status: EntryTriageSelectionStatus
    directive: TrustedEntryTriageDirective | None

    def __post_init__(self):
        if not isinstance(self.status, EntryTriageSelectionStatus):
            raise ValueError(
                "EntryTriageSelectionResult.status must be an "
                f"EntryTriageSelectionStatus, got {type(self.status)!r}")
        if self.status is EntryTriageSelectionStatus.ACCEPTED:
            if not isinstance(self.directive, TrustedEntryTriageDirective):
                raise ValueError(
                    "EntryTriageSelectionResult: ACCEPTED requires a "
                    "TrustedEntryTriageDirective")
        elif self.directive is not None:
            raise ValueError(
                "EntryTriageSelectionResult: a rejection status must carry "
                "directive=None")


def canonicalize_entry_triage_selection(
        selection: UntrustedEntryTriageSelection) -> EntryTriageSelectionResult:
    """Pure, deterministic entry-triage canonicalization. An existing
    EntryTriageCategory member, or its exact `.value` string, is ACCEPTED
    and produces a trusted directive. Any other string is a deterministic
    SEMANTIC_VALUE_INVALID rejection -- it is NEVER silently mapped to
    UNSURE_OR_OTHER, which is a real explicit user choice, not an error
    bucket. Wrong Python argument types raise ValueError."""
    if not isinstance(selection, UntrustedEntryTriageSelection):
        raise ValueError(
            "canonicalize_entry_triage_selection requires an "
            f"UntrustedEntryTriageSelection, got {type(selection)!r}")
    try:
        category = EntryTriageCategory(selection.category)
    except ValueError:
        return EntryTriageSelectionResult(
            status=EntryTriageSelectionStatus.SEMANTIC_VALUE_INVALID, directive=None)
    directive = TrustedEntryTriageDirective(
        category=category, followup_focus=followup_focus_for_category(category))
    return EntryTriageSelectionResult(
        status=EntryTriageSelectionStatus.ACCEPTED, directive=directive)


# ── Reply affordances: offer context, untrusted transport, trusted directive -

@dataclass(frozen=True)
class ReplyAffordanceOfferContext:
    """The trusted, exact record of which reply-affordance actions were
    actually offered for one specific assistant turn, plus the opaque
    correlation token identifying that turn. offered_actions must be
    obtained exclusively from professional_reply_affordances.
    derive_reply_affordances -- see build_reply_affordance_offer_context,
    the only sanctioned factory. The public constructor here fails closed if
    offered_actions does not exactly equal derive_reply_affordances(plan).
    actions, so a caller can never claim an action set, order, or count the
    plan did not actually offer. Because ReplyAffordanceAction is a
    str-backed Enum, a plain str with the same textual value would compare
    equal under naive tuple equality -- every element is therefore checked
    with exact `type(...) is ReplyAffordanceAction` before the tuple
    comparison runs, so this trusted type never accepts an ordinary string,
    a mixed tuple, or a tuple subclass standing in for canonical enum
    members."""
    origin_turn_token: str
    plan: ProfessionalTurnPlan
    offered_actions: tuple[ReplyAffordanceAction, ...]

    def __post_init__(self):
        _validate_origin_turn_token(self.origin_turn_token)
        if not isinstance(self.plan, ProfessionalTurnPlan):
            raise ValueError(
                "ReplyAffordanceOfferContext.plan must be a "
                f"ProfessionalTurnPlan, got {type(self.plan)!r}")
        if type(self.offered_actions) is not tuple:
            raise ValueError(
                "ReplyAffordanceOfferContext.offered_actions must be exactly "
                f"tuple, got {type(self.offered_actions)!r}")
        for element in self.offered_actions:
            if type(element) is not ReplyAffordanceAction:
                raise ValueError(
                    "ReplyAffordanceOfferContext.offered_actions elements must "
                    f"be exactly ReplyAffordanceAction, got {type(element)!r}")
        expected = derive_reply_affordances(self.plan).actions
        if self.offered_actions != expected:
            raise ValueError(
                "ReplyAffordanceOfferContext.offered_actions must exactly "
                "equal derive_reply_affordances(plan).actions: expected "
                f"{[a.value for a in expected]}, got "
                f"{[getattr(a, 'value', a) for a in self.offered_actions]}")


def build_reply_affordance_offer_context(
        origin_turn_token: str, plan: ProfessionalTurnPlan) -> ReplyAffordanceOfferContext:
    """Pure factory: the only sanctioned way to construct a trusted
    ReplyAffordanceOfferContext. Offered actions come exclusively from the
    already-merged derive_reply_affordances(plan) -- a caller cannot inject,
    omit, or reorder an action through this factory."""
    _validate_origin_turn_token(origin_turn_token)
    if not isinstance(plan, ProfessionalTurnPlan):
        raise ValueError(
            "build_reply_affordance_offer_context requires a "
            f"ProfessionalTurnPlan, got {type(plan)!r}")
    offered_actions = derive_reply_affordances(plan).actions
    return ReplyAffordanceOfferContext(
        origin_turn_token=origin_turn_token, plan=plan, offered_actions=offered_actions)


@dataclass(frozen=True)
class UntrustedReplyAffordanceSelection:
    """Untrusted UI transport value carrying a reply-affordance selection
    correlated to a specific assistant turn. It is not trusted merely
    because it originated from a UI callback. action may be a
    ReplyAffordanceAction member or an ordinary str -- an unknown string
    survives this construction and is rejected at the deterministic
    semantic boundary (canonicalize_reply_affordance_selection), never
    here. Carries no arbitrary user text."""
    origin_turn_token: str
    action: ReplyAffordanceAction | str

    def __post_init__(self):
        _validate_origin_turn_token(self.origin_turn_token)
        if not isinstance(self.action, str):
            raise ValueError(
                "UntrustedReplyAffordanceSelection.action must be a "
                f"ReplyAffordanceAction or str, got {type(self.action)!r}")


class ReplyAffordanceSelectionStatus(str, Enum):
    """Closed deterministic outcome vocabulary for one
    canonicalize_reply_affordance_selection call. No catch-all generic
    member -- every rejection is one specific, closed reason."""
    ACCEPTED = "ACCEPTED"
    SEMANTIC_VALUE_INVALID = "SEMANTIC_VALUE_INVALID"
    STALE_ORIGIN_TURN = "STALE_ORIGIN_TURN"
    ACTION_NOT_OFFERED = "ACTION_NOT_OFFERED"


class TrustedReplyDirectiveKind(str, Enum):
    """Closed V1 vocabulary of trusted next-turn control directive kinds."""
    SCAFFOLD_CURRENT_TARGET = "SCAFFOLD_CURRENT_TARGET"
    SKIP_EXACT_CURRENT_QUESTION = "SKIP_EXACT_CURRENT_QUESTION"


_TRUSTED_REPLY_DIRECTIVE_KIND_BY_ACTION: dict[ReplyAffordanceAction, TrustedReplyDirectiveKind] = {
    ReplyAffordanceAction.NEEDS_SCAFFOLDING: TrustedReplyDirectiveKind.SCAFFOLD_CURRENT_TARGET,
    ReplyAffordanceAction.SKIP_CURRENT_QUESTION: TrustedReplyDirectiveKind.SKIP_EXACT_CURRENT_QUESTION,
}


@dataclass(frozen=True)
class TrustedReplyDirective:
    """The trusted next-turn control directive produced by an accepted
    reply-affordance selection. No arbitrary free-text instruction field,
    no model-authored planner command, no Telegram data, no DB state --
    only closed/bounded fields. kind is validated against the sealed
    action->kind mapping, so a mismatched action/kind pair cannot be
    constructed. action is also validated against
    derive_reply_affordances(prior_plan).actions -- a caller cannot
    directly construct an internally-impossible directive whose action was
    never actually offered for prior_plan (for example SKIP_CURRENT_QUESTION
    paired with a CLOSE+CLOSING plan, which offers no reply affordances at
    all). This deliberately reuses the same trusted derive_reply_affordances
    function rather than duplicating the affordance-availability matrix."""
    action: ReplyAffordanceAction
    kind: TrustedReplyDirectiveKind
    origin_turn_token: str
    prior_plan: ProfessionalTurnPlan

    def __post_init__(self):
        if not isinstance(self.action, ReplyAffordanceAction):
            raise ValueError(
                "TrustedReplyDirective.action must be a ReplyAffordanceAction, "
                f"got {type(self.action)!r}")
        expected_kind = _TRUSTED_REPLY_DIRECTIVE_KIND_BY_ACTION[self.action]
        if self.kind is not expected_kind:
            raise ValueError(
                f"TrustedReplyDirective.kind for {self.action.value} must be "
                f"exactly {expected_kind.value}, got {self.kind!r}")
        _validate_origin_turn_token(self.origin_turn_token)
        if not isinstance(self.prior_plan, ProfessionalTurnPlan):
            raise ValueError(
                "TrustedReplyDirective.prior_plan must be a "
                f"ProfessionalTurnPlan, got {type(self.prior_plan)!r}")
        allowed_actions = derive_reply_affordances(self.prior_plan).actions
        if self.action not in allowed_actions:
            raise ValueError(
                f"TrustedReplyDirective.action {self.action.value} was not "
                "actually offered for prior_plan: "
                f"derive_reply_affordances(prior_plan).actions="
                f"{[a.value for a in allowed_actions]}")


@dataclass(frozen=True)
class ReplyAffordanceSelectionResult:
    """The outcome envelope for one canonicalize_reply_affordance_selection
    call: either a trusted directive (status=ACCEPTED) or an explicit
    rejection status with no directive -- never both, never neither."""
    status: ReplyAffordanceSelectionStatus
    directive: TrustedReplyDirective | None

    def __post_init__(self):
        if not isinstance(self.status, ReplyAffordanceSelectionStatus):
            raise ValueError(
                "ReplyAffordanceSelectionResult.status must be a "
                f"ReplyAffordanceSelectionStatus, got {type(self.status)!r}")
        if self.status is ReplyAffordanceSelectionStatus.ACCEPTED:
            if not isinstance(self.directive, TrustedReplyDirective):
                raise ValueError(
                    "ReplyAffordanceSelectionResult: ACCEPTED requires a "
                    "TrustedReplyDirective")
        elif self.directive is not None:
            raise ValueError(
                "ReplyAffordanceSelectionResult: a rejection status must "
                "carry directive=None")


def canonicalize_reply_affordance_selection(
        active_offer: ReplyAffordanceOfferContext,
        selection: UntrustedReplyAffordanceSelection) -> ReplyAffordanceSelectionResult:
    """Pure, deterministic reply-affordance canonicalization, in this exact
    order: (1) validate public argument types, raising ValueError on a
    structurally wrong call; (2) exact origin-token comparison -- a
    mismatch returns STALE_ORIGIN_TURN immediately and the selection's
    action is never semantically inspected after a stale mismatch;
    (3) canonicalize the action -- an unknown semantic string returns
    SEMANTIC_VALUE_INVALID; (4) require the action to be one of
    active_offer.offered_actions -- otherwise ACTION_NOT_OFFERED;
    (5) produce the exact trusted directive. No model call, no user-text
    inspection anywhere in this function."""
    if not isinstance(active_offer, ReplyAffordanceOfferContext):
        raise ValueError(
            "canonicalize_reply_affordance_selection requires a "
            f"ReplyAffordanceOfferContext, got {type(active_offer)!r}")
    if not isinstance(selection, UntrustedReplyAffordanceSelection):
        raise ValueError(
            "canonicalize_reply_affordance_selection requires an "
            f"UntrustedReplyAffordanceSelection, got {type(selection)!r}")

    if selection.origin_turn_token != active_offer.origin_turn_token:
        return ReplyAffordanceSelectionResult(
            status=ReplyAffordanceSelectionStatus.STALE_ORIGIN_TURN, directive=None)

    try:
        action = ReplyAffordanceAction(selection.action)
    except ValueError:
        return ReplyAffordanceSelectionResult(
            status=ReplyAffordanceSelectionStatus.SEMANTIC_VALUE_INVALID, directive=None)

    if action not in active_offer.offered_actions:
        return ReplyAffordanceSelectionResult(
            status=ReplyAffordanceSelectionStatus.ACTION_NOT_OFFERED, directive=None)

    directive = TrustedReplyDirective(
        action=action,
        kind=_TRUSTED_REPLY_DIRECTIVE_KIND_BY_ACTION[action],
        origin_turn_token=active_offer.origin_turn_token,
        prior_plan=active_offer.plan,
    )
    return ReplyAffordanceSelectionResult(
        status=ReplyAffordanceSelectionStatus.ACCEPTED, directive=directive)
