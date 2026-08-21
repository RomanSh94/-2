"""Professional Core V2 -- Stage 2A pure structural primitives, component
wrappers, the turn-level aggregate, the deterministic exact candidate
locator, and the evidence/interaction proposal canonicalization boundaries
(Slices 2A-1/2A-2/2A-3/2A-4/2A-5/2A-6).

Pure, offline, no I/O: this module defines the untrusted-candidate,
deterministically-located, canonically-classified primitive types, the
per-component status wrappers (EvidenceAnalysis/InteractionAnalysis/
IntentAnalysis), the authoritative turn-level aggregate (TurnAnalysis/
TurnAnalysisResult), locate_evidence_candidate/locate_interaction_candidate
(deterministic exact-match location of one untrusted candidate span inside
one caller-supplied source_text), canonicalize_evidence_proposal
(deterministic canonicalization of one external analyzer's untrusted
EvidenceKind proposal, or its explicit abstention, for one already-located
evidence span), and canonicalize_interaction_proposal (the same
canonicalization boundary for an untrusted InteractionOccurrenceProposal --
signal/applicability/state together, or explicit abstention -- for one
already-located interaction span) for Stage 2A turn analysis. It contains
NO semantic analyzer, NO candidate harvesting, NO orchestration, NO
LLM/network calls, and NO database/bot/Telegram integration -- those are
later, separately authorized slices. A candidate/exact span stored here is
literal untrusted or deterministically-located text; nothing in this
module proves that a span's assigned EvidenceKind or InteractionSignal/
InteractionApplicability/InteractionOccurrenceState triple is correct
beyond what the type system itself enforces, and nothing derives or
classifies a span's semantic content -- both canonicalizers only validate
and structurally record values supplied by something outside this module.
The component wrappers represent their supplied AnalysisComponentStatus
values and do not derive them. TurnAnalysis validates the authoritative
aggregate, while TurnAnalysisResult derives the overall TurnAnalysisStatus
from aggregate existence and component statuses. Production of component
analyses and construction/recovery of aggregate results belong to later
orchestration.

Only imports: __future__, dataclasses, enum, and therapeutic_domain
(reusing EvidenceItem, EvidenceKind, EvidenceRef, Intent,
InteractionRequest, InteractionSignal, as_enum, and
validate_evidence_against_source rather than duplicating them). Python
3.10 target (prod 3.10.12).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from therapeutic_domain import (
    EvidenceItem,
    EvidenceKind,
    EvidenceRef,
    Intent,
    InteractionRequest,
    InteractionSignal,
    as_enum,
    validate_evidence_against_source,
)


# -- Status enums -------------------------------------------------------

class AnalysisComponentStatus(str, Enum):
    VALIDATED = "VALIDATED"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class InteractionApplicability(str, Enum):
    CURRENT_DIRECTIVE = "CURRENT_DIRECTIVE"
    NON_CURRENT_CONTEXT = "NON_CURRENT_CONTEXT"


class InteractionOccurrenceState(str, Enum):
    ACTIVE = "ACTIVE"
    RETRACTED = "RETRACTED"


class TurnAnalysisStatus(str, Enum):
    OK = "OK"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


# -- Engineering V1 limits ------------------------------------------------
# Engineering V1 limits only -- NOT clinical thresholds, NOT safety
# thresholds, NOT empirically optimal values. Tunable in a later revision
# without changing Stage 2A's epistemic semantics. Invalid input always
# raises ValueError; input exceeding a limit is never silently truncated.

EVIDENCE_CANDIDATE_MAX_CHARS = 300
INTERACTION_CANDIDATE_MAX_CHARS = 150
CONTEXT_BEFORE_MAX_CHARS = 60
CONTEXT_AFTER_MAX_CHARS = 60
MAX_EVIDENCE_CANDIDATES_PER_TURN = 20
MAX_INTERACTION_CANDIDATES_PER_TURN = 10
# MAX_EVIDENCE_CANDIDATES_PER_TURN and MAX_INTERACTION_CANDIDATES_PER_TURN
# are declared now as part of the sealed V1 engineering contract but are
# NOT enforced by this slice: a per-turn count is a property of a
# collection of candidates, owned by the (not yet implemented)
# orchestration/harvesting layer, not by any single dataclass here.


class BoundedTextField(str, Enum):
    """Which of a candidate span's three bounded-text fields a
    BoundedTextViolation originated from -- closed, diagnostic-only
    identity. Only ever set by the candidate-tier callers of
    _require_bounded_text (EvidenceSpanCandidate/InteractionSpanCandidate);
    every other caller (LocatedEvidenceSpan/LocatedInteractionSpan/
    InteractionSignalOccurrence) leaves it None, since they are not part
    of the Analyzer's untrusted-candidate structural-failure
    classification and this slice must not alter their behavior."""
    EXACT_SOURCE_SPAN = "EXACT_SOURCE_SPAN"
    CONTEXT_BEFORE = "CONTEXT_BEFORE"
    CONTEXT_AFTER = "CONTEXT_AFTER"


class BoundedTextViolationKind(str, Enum):
    """Which exact bounded-text rule a BoundedTextViolation represents --
    closed, exhaustive over _require_bounded_text's own three content
    checks (the type check is a separate, unrelated defect class -- see
    BoundedTextViolation's own docstring)."""
    EMPTY = "EMPTY"
    WHITESPACE_ONLY = "WHITESPACE_ONLY"
    TOO_LONG = "TOO_LONG"


class BoundedTextViolation(ValueError):
    """A ValueError subclass carrying a typed, closed diagnostic
    (field, kind) pair -- diagnostic-only, does not change acceptance/
    rejection behavior: every existing `except ValueError` caller (inside
    this module and in professional_turn_analyzer.py) continues to catch
    it exactly as before, and str(self) is unchanged from the plain
    ValueError message this replaces. field is None for callers that
    don't identify a specific candidate field (see BoundedTextField's own
    docstring); kind is always set for the three content-rule violations
    this class represents (never for the type-mismatch check, which
    remains a plain ValueError -- see _require_bounded_text)."""

    def __init__(
            self, message: str, *,
            kind: BoundedTextViolationKind,
            field: BoundedTextField | None = None,
    ):
        if type(kind) is not BoundedTextViolationKind:
            raise ValueError(
                "BoundedTextViolation: kind must be exactly a "
                f"BoundedTextViolationKind, got {type(kind)!r}")
        if field is not None and type(field) is not BoundedTextField:
            raise ValueError(
                "BoundedTextViolation: field must be None or exactly a "
                f"BoundedTextField, got {type(field)!r}")
        super().__init__(message)
        self.kind = kind
        self.field = field


def _require_bounded_text(
        value: str, *, field_name: str, max_chars: int,
        field: BoundedTextField | None = None,
) -> None:
    """Shared, non-mutating validation for an untrusted/located text field:
    must be exactly `str`, non-empty, not whitespace-only, and within the
    given character cap. Never strips, casefolds, or truncates -- the
    caller's own field always stores the exact original value. The type
    check is unreachable from the Analyzer's real call path (that field's
    type is already validated earlier, at the parser boundary, before this
    function's caller is ever constructed) and remains a plain ValueError
    with no field/kind -- WRONG_TYPE has no place in a text-CONTENT
    violation taxonomy. `field` is optional and diagnostic-only; passing it
    does not change which check runs or what value is accepted."""
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a str, got {type(value)!r}")
    if not value:
        raise BoundedTextViolation(
            f"{field_name} must be non-empty",
            kind=BoundedTextViolationKind.EMPTY, field=field)
    if not value.strip():
        raise BoundedTextViolation(
            f"{field_name} must not be whitespace-only",
            kind=BoundedTextViolationKind.WHITESPACE_ONLY, field=field)
    if len(value) > max_chars:
        raise BoundedTextViolation(
            f"{field_name} exceeds max length {max_chars} (got {len(value)})",
            kind=BoundedTextViolationKind.TOO_LONG, field=field)


def _require_row_id(value: int, *, field_name: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field_name} must be a positive int, got {value!r}")


def _require_span_bounds(span_start: int, span_end: int) -> None:
    if type(span_start) is not int or type(span_end) is not int:
        raise ValueError("span_start/span_end must be int")
    if not 0 <= span_start < span_end:
        raise ValueError(
            "requires 0 <= span_start < span_end, got "
            f"span_start={span_start}, span_end={span_end}")


# -- Untrusted candidate tier (extractor output, spans only, no labels) --

@dataclass(frozen=True)
class EvidenceSpanCandidate:
    """Untrusted candidate evidence span, exactly as an extractor might
    propose it -- text only, never a kind, never offsets. Nothing here
    validates that exact_source_span actually occurs in any source_text;
    that is locate_evidence_candidate's job, not this type's own
    construction-time validation."""
    exact_source_span: str
    context_before: str | None = None
    context_after: str | None = None

    def __post_init__(self):
        _require_bounded_text(
            self.exact_source_span,
            field_name="EvidenceSpanCandidate.exact_source_span",
            max_chars=EVIDENCE_CANDIDATE_MAX_CHARS,
            field=BoundedTextField.EXACT_SOURCE_SPAN)
        if self.context_before is not None:
            _require_bounded_text(
                self.context_before,
                field_name="EvidenceSpanCandidate.context_before",
                max_chars=CONTEXT_BEFORE_MAX_CHARS,
                field=BoundedTextField.CONTEXT_BEFORE)
        if self.context_after is not None:
            _require_bounded_text(
                self.context_after,
                field_name="EvidenceSpanCandidate.context_after",
                max_chars=CONTEXT_AFTER_MAX_CHARS,
                field=BoundedTextField.CONTEXT_AFTER)


@dataclass(frozen=True)
class InteractionSpanCandidate:
    """Untrusted candidate interaction span -- text only, never a signal,
    never applicability/state, never offsets, never source_message_row_id.
    Deliberately the same shape as EvidenceSpanCandidate except for its
    own, shorter character cap."""
    exact_source_span: str
    context_before: str | None = None
    context_after: str | None = None

    def __post_init__(self):
        _require_bounded_text(
            self.exact_source_span,
            field_name="InteractionSpanCandidate.exact_source_span",
            max_chars=INTERACTION_CANDIDATE_MAX_CHARS,
            field=BoundedTextField.EXACT_SOURCE_SPAN)
        if self.context_before is not None:
            _require_bounded_text(
                self.context_before,
                field_name="InteractionSpanCandidate.context_before",
                max_chars=CONTEXT_BEFORE_MAX_CHARS,
                field=BoundedTextField.CONTEXT_BEFORE)
        if self.context_after is not None:
            _require_bounded_text(
                self.context_after,
                field_name="InteractionSpanCandidate.context_after",
                max_chars=CONTEXT_AFTER_MAX_CHARS,
                field=BoundedTextField.CONTEXT_AFTER)


# -- Deterministic located tier (code-owned offsets, produced by
# locate_evidence_candidate/locate_interaction_candidate below) ---------

@dataclass(frozen=True)
class LocatedEvidenceSpan:
    """A deterministically-located evidence span: code-owned offsets plus
    the exact text at that location. Does NOT check against any
    source_text -- source_text is intentionally not a field of this type;
    only internal self-consistency (claimed length matches claimed span)
    is checkable here, mirroring EvidenceItem's own existing pattern in
    therapeutic_domain.py."""
    source_message_row_id: int
    span_start: int
    span_end: int
    exact_source_span: str

    def __post_init__(self):
        _require_row_id(
            self.source_message_row_id,
            field_name="LocatedEvidenceSpan.source_message_row_id")
        _require_span_bounds(self.span_start, self.span_end)
        _require_bounded_text(
            self.exact_source_span,
            field_name="LocatedEvidenceSpan.exact_source_span",
            max_chars=EVIDENCE_CANDIDATE_MAX_CHARS)
        expected_len = self.span_end - self.span_start
        if len(self.exact_source_span) != expected_len:
            raise ValueError(
                "LocatedEvidenceSpan.exact_source_span length "
                f"({len(self.exact_source_span)}) does not match "
                f"span length ({expected_len})")


@dataclass(frozen=True)
class LocatedInteractionSpan:
    """Same invariants as LocatedEvidenceSpan, using the interaction
    character cap."""
    source_message_row_id: int
    span_start: int
    span_end: int
    exact_source_span: str

    def __post_init__(self):
        _require_row_id(
            self.source_message_row_id,
            field_name="LocatedInteractionSpan.source_message_row_id")
        _require_span_bounds(self.span_start, self.span_end)
        _require_bounded_text(
            self.exact_source_span,
            field_name="LocatedInteractionSpan.exact_source_span",
            max_chars=INTERACTION_CANDIDATE_MAX_CHARS)
        expected_len = self.span_end - self.span_start
        if len(self.exact_source_span) != expected_len:
            raise ValueError(
                "LocatedInteractionSpan.exact_source_span length "
                f"({len(self.exact_source_span)}) does not match "
                f"span length ({expected_len})")


# -- Deterministic exact candidate locator (Slice 2A-4) ------------------
# Deterministic, exact-match location of one untrusted candidate span
# within one caller-supplied source_text. Pure and offline: no semantic
# labeling, no candidate harvesting, no orchestration, no LLM/network/DB
# calls. locate_evidence_candidate/locate_interaction_candidate below are
# the only public additions in this section.

def _require_source_text(value: str, *, field_name: str) -> None:
    """Shared raw source_text validator for pure Professional Turn
    Analysis operations that require a supplied source message: must be
    exactly str, non-empty, not whitespace-only. Deliberately has NO
    maximum length (unlike _require_bounded_text) and never strips,
    casefolds, or otherwise mutates the value -- a raw message can be
    arbitrarily long. Used by locate_evidence_candidate/
    locate_interaction_candidate/canonicalize_evidence_proposal/
    canonicalize_interaction_proposal; TurnAnalysis keeps its own
    separate inline source_text check unchanged."""
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a str, got {type(value)!r}")
    if not value:
        raise ValueError(f"{field_name} must be non-empty")
    if not value.strip():
        raise ValueError(f"{field_name} must not be whitespace-only")


def _locate_unique_exact_span(
        *,
        exact_source_span: str,
        context_before: str | None,
        context_after: str | None,
        source_text: str,
) -> tuple[int, int] | None:
    """Module-private exact-match search core shared by both public
    locators. Enumerates every raw occurrence of exact_source_span in
    source_text allowing overlap (after a candidate occurrence beginning
    at `start`, the next search resumes at start + 1, never
    start + len(exact_source_span)), filters each occurrence by any
    supplied context_before/context_after as an exact immediate-adjacency
    constraint (never a fuzzy/nearby window), and returns the single
    surviving (start, end) span. Returns None if zero or more than one
    occurrence survives -- stops searching as soon as a second surviving
    occurrence is found; it never picks a first/last/nearest match. No
    normalization of any kind (no strip/casefold/Unicode normalization) is
    ever applied to exact_source_span, context_before, context_after, or
    source_text. Assumes its caller already validated argument types/
    emptiness -- this helper itself only implements the search and
    filter, nothing else."""
    matches: list[tuple[int, int]] = []
    start = source_text.find(exact_source_span)
    while start != -1:
        end = start + len(exact_source_span)
        context_ok = True
        if context_before is not None:
            if start < len(context_before) or (
                    source_text[start - len(context_before):start] != context_before):
                context_ok = False
        if context_ok and context_after is not None:
            if end + len(context_after) > len(source_text) or (
                    source_text[end:end + len(context_after)] != context_after):
                context_ok = False
        if context_ok:
            matches.append((start, end))
            if len(matches) > 1:
                return None
        start = source_text.find(exact_source_span, start + 1)
    return matches[0] if len(matches) == 1 else None


def locate_evidence_candidate(
        candidate: EvidenceSpanCandidate,
        *,
        source_message_row_id: int,
        source_text: str,
) -> LocatedEvidenceSpan | None:
    """Deterministically locate one untrusted EvidenceSpanCandidate inside
    source_text using exact Python code-point matching only -- see
    _locate_unique_exact_span for the exact search/context/uniqueness
    contract. Returns a LocatedEvidenceSpan built from source_text itself
    (not from the candidate) when exactly one valid location survives,
    None when zero or more than one location survives. Raises ValueError
    only for malformed API input (wrong candidate type, invalid
    source_message_row_id, invalid source_text) -- never for an honest
    not-found/ambiguous search outcome.

    This function validates that candidate.exact_source_span occurs at a
    specific position inside the given source_text; it has no way to
    authenticate that source_text is actually the persisted message
    content for source_message_row_id -- supplying a genuinely
    corresponding (row id, source_text) pair is the caller's
    responsibility, not something this pure function can check."""
    if not isinstance(candidate, EvidenceSpanCandidate):
        raise ValueError(
            "locate_evidence_candidate: candidate must be an "
            f"EvidenceSpanCandidate, got {type(candidate)!r}")
    _require_row_id(
        source_message_row_id,
        field_name="locate_evidence_candidate.source_message_row_id")
    _require_source_text(source_text, field_name="locate_evidence_candidate.source_text")
    located = _locate_unique_exact_span(
        exact_source_span=candidate.exact_source_span,
        context_before=candidate.context_before,
        context_after=candidate.context_after,
        source_text=source_text)
    if located is None:
        return None
    start, end = located
    return LocatedEvidenceSpan(
        source_message_row_id=source_message_row_id,
        span_start=start,
        span_end=end,
        exact_source_span=source_text[start:end])


def locate_interaction_candidate(
        candidate: InteractionSpanCandidate,
        *,
        source_message_row_id: int,
        source_text: str,
) -> LocatedInteractionSpan | None:
    """Same contract as locate_evidence_candidate for an untrusted
    InteractionSpanCandidate, returning a LocatedInteractionSpan built
    from source_text itself. Raises ValueError only for malformed API
    input, never for an honest not-found/ambiguous outcome, and does not
    authenticate that source_text is the persisted content for
    source_message_row_id."""
    if not isinstance(candidate, InteractionSpanCandidate):
        raise ValueError(
            "locate_interaction_candidate: candidate must be an "
            f"InteractionSpanCandidate, got {type(candidate)!r}")
    _require_row_id(
        source_message_row_id,
        field_name="locate_interaction_candidate.source_message_row_id")
    _require_source_text(source_text, field_name="locate_interaction_candidate.source_text")
    located = _locate_unique_exact_span(
        exact_source_span=candidate.exact_source_span,
        context_before=candidate.context_before,
        context_after=candidate.context_after,
        source_text=source_text)
    if located is None:
        return None
    start, end = located
    return LocatedInteractionSpan(
        source_message_row_id=source_message_row_id,
        span_start=start,
        span_end=end,
        exact_source_span=source_text[start:end])


# -- Evidence proposal canonicalization boundary (Slice 2A-5) -----------
# Deterministic canonicalization of one external analyzer's untrusted
# EvidenceKind proposal (or explicit abstention) for one already-located
# evidence span. Pure and offline: no semantic analyzer, no keyword/regex
# classifier, no negation/quotation parsing, no confidence calculation, no
# LLM/network/DB calls. canonicalize_evidence_proposal below is the only
# public addition in this section; it never inspects the words of
# exact_source_span to decide a kind.

def canonicalize_evidence_proposal(
        located: LocatedEvidenceSpan,
        *,
        source_text: str,
        proposed_kind: EvidenceKind | str | None,
) -> EvidenceItem | None:
    """Deterministically canonicalize one external analyzer's untrusted
    EvidenceKind proposal for an already-located evidence span into a
    provenance-valid EvidenceItem, or honor the analyzer's explicit
    abstention. This function performs NO semantic classification itself
    -- it never inspects exact_source_span's words to decide a kind; the
    external analyzer (not implemented anywhere in this module) alone
    judges which EvidenceKind, if any, applies.

    Structural/provenance validation (located's type, source_text's
    validity, and located's provenance against source_text) always runs
    BEFORE proposed_kind is ever inspected, so proposed_kind=None
    (explicit analyzer abstention) can never mask a wrong located type, a
    malformed source_text, or a stale/mismatched located span -- all of
    those raise ValueError regardless of proposed_kind.

    A returned EvidenceItem means only that exact supplied-source
    provenance was valid and an external analyzer's proposal was parsed
    to one supported EvidenceKind. It does NOT mean objective truth, a
    clinician-confirmed finding, a diagnosis, a confirmed cause, a
    confirmed case conceptualization, or deterministic proof that the
    EvidenceKind is semantically correct -- the classification itself
    remains external analyzer judgment throughout.

    This function validates that located.exact_source_span occurs at its
    recorded position inside the given source_text; it has no way to
    authenticate that source_text is actually the persisted message
    content for located.source_message_row_id -- supplying a genuinely
    corresponding (located span, source_text) pair is the caller's
    responsibility, not something this pure function can check."""
    if not isinstance(located, LocatedEvidenceSpan):
        raise ValueError(
            "canonicalize_evidence_proposal: located must be a "
            f"LocatedEvidenceSpan, got {type(located)!r}")
    _require_source_text(
        source_text, field_name="canonicalize_evidence_proposal.source_text")
    if located.span_end > len(source_text):
        raise ValueError(
            "canonicalize_evidence_proposal: located.span_end exceeds "
            "len(source_text)")
    if source_text[located.span_start:located.span_end] != located.exact_source_span:
        raise ValueError(
            "canonicalize_evidence_proposal: located.exact_source_span "
            "does not match source_text at its recorded span")
    if proposed_kind is None:
        return None
    canonical_kind = as_enum(EvidenceKind, proposed_kind)
    ref = EvidenceRef(
        source_message_row_id=located.source_message_row_id,
        span_start=located.span_start,
        span_end=located.span_end,
        evidence_kind=canonical_kind)
    return EvidenceItem(
        ref=ref,
        exact_source_span=source_text[located.span_start:located.span_end])


# -- Canonical, already-classified occurrence tier -----------------------

@dataclass(frozen=True)
class InteractionSignalOccurrence:
    """One already-classified interaction-signal occurrence: which
    message, which exact span, which InteractionSignal, and its
    applicability/retraction state. A structurally valid instance proves
    only that its fields satisfy the closed shape/enum invariants -- it
    does NOT prove that an external analyzer's signal/applicability/state
    judgments are semantically correct, and does NOT prove a RETRACTED
    state is grounded by another actual retracting span. This type only
    REPRESENTS an already-classified occurrence -- it does not derive
    InteractionRequest, resolve policy conflicts, or implement retraction
    detection; those are later Stage 2A/2B concerns."""
    source_message_row_id: int
    signal: InteractionSignal
    span_start: int
    span_end: int
    exact_source_span: str
    applicability: InteractionApplicability
    state: InteractionOccurrenceState

    def __post_init__(self):
        _require_row_id(
            self.source_message_row_id,
            field_name="InteractionSignalOccurrence.source_message_row_id")
        _require_span_bounds(self.span_start, self.span_end)
        _require_bounded_text(
            self.exact_source_span,
            field_name="InteractionSignalOccurrence.exact_source_span",
            max_chars=INTERACTION_CANDIDATE_MAX_CHARS)
        expected_len = self.span_end - self.span_start
        if len(self.exact_source_span) != expected_len:
            raise ValueError(
                "InteractionSignalOccurrence.exact_source_span length "
                f"({len(self.exact_source_span)}) does not match "
                f"span length ({expected_len})")
        object.__setattr__(self, "signal", as_enum(InteractionSignal, self.signal))
        object.__setattr__(
            self, "applicability", as_enum(InteractionApplicability, self.applicability))
        object.__setattr__(self, "state", as_enum(InteractionOccurrenceState, self.state))


# -- Interaction proposal canonicalization boundary (Slice 2A-6) --------
# Deterministic canonicalization of one external analyzer's untrusted,
# all-three-axes-or-nothing interaction proposal (or explicit abstention)
# for one already-located interaction span. Pure and offline: no semantic
# analyzer, no keyword/regex classifier, no negation/quotation/retraction
# parsing, no confidence calculation, no LLM/network/DB calls.
# canonicalize_interaction_proposal below is the only public function
# addition in this section; InteractionOccurrenceProposal is the only
# public type addition. Neither inspects the words of exact_source_span
# to decide a signal/applicability/state.

@dataclass(frozen=True)
class InteractionOccurrenceProposal:
    """Untrusted external-analyzer transport for one interaction
    occurrence proposal -- signal, applicability, and state together, as a
    single atomic unit. The field annotations describe the INTENDED shape
    of a well-formed proposal; they are not runtime validation, and this
    type performs none of its own: no enum canonicalization, no
    normalization, no source/provenance validation, and no __post_init__
    at all. Merely constructing an instance -- even with a malformed
    runtime value such as signal=None or
    applicability="NOT_A_REAL_APPLICABILITY" -- never raises here; that is
    intentional, since this type only carries an external, not-yet-vetted
    claim. Only canonicalize_interaction_proposal, in this Stage 2A path,
    may convert a well-formed instance into a canonical
    InteractionSignalOccurrence. If the external producer cannot
    responsibly supply all three semantic axes at once, it must represent
    that as total proposal abstention (passing None to
    canonicalize_interaction_proposal) rather than constructing a partial
    or invented instance of this type -- this type's required fields
    prevent omitting constructor arguments, not supplying invalid runtime
    values for them."""
    signal: InteractionSignal | str
    applicability: InteractionApplicability | str
    state: InteractionOccurrenceState | str


def canonicalize_interaction_proposal(
        located: LocatedInteractionSpan,
        *,
        source_text: str,
        proposal: InteractionOccurrenceProposal | None,
) -> InteractionSignalOccurrence | None:
    """Deterministically canonicalize one external analyzer's untrusted
    InteractionOccurrenceProposal for an already-located interaction span
    into a provenance-valid InteractionSignalOccurrence, or honor the
    analyzer's explicit abstention. This function performs NO semantic
    classification itself -- it never inspects exact_source_span's words
    to decide a signal, applicability, or state; the external analyzer
    (not implemented anywhere in this module) alone judges all three axes.

    Structural/provenance validation (located's type, source_text's
    validity, and located's provenance against source_text) always runs
    BEFORE proposal is ever inspected, so proposal=None (explicit analyzer
    abstention) can never mask a wrong located type, a malformed
    source_text, or a stale/mismatched located span -- all of those raise
    ValueError regardless of proposal. Only after provenance passes is
    proposal's own type checked, and only then are its three fields each
    canonicalized independently via as_enum -- an invalid runtime value on
    any single axis (None, an unknown string, an alias, or a wrong scalar
    type) raises ValueError there; it is never defaulted, repaired, or
    treated as equivalent to abstention.

    A returned InteractionSignalOccurrence means only that exact
    supplied-source provenance was valid and an external analyzer's
    proposal was parsed to three supported enum values. It does NOT mean
    the signal, applicability, or state judgment is semantically correct,
    and it does NOT mean a RETRACTED state is grounded by another actual
    retracting span elsewhere in source_text -- those remain external
    analyzer judgment and trust, never verified here. The analyzer may
    inspect the full source_text to judge applicability/state (present vs
    historical/quoted/hypothetical framing, correction, supersession), but
    the signal itself must remain attributable to the located span's own
    clause -- this function has no way to verify that attribution.

    This function validates that located.exact_source_span occurs at its
    recorded position inside the given source_text; it has no way to
    authenticate that source_text is actually the persisted message
    content for located.source_message_row_id -- supplying a genuinely
    corresponding (located span, source_text) pair is the caller's
    responsibility, not something this pure function can check."""
    if not isinstance(located, LocatedInteractionSpan):
        raise ValueError(
            "canonicalize_interaction_proposal: located must be a "
            f"LocatedInteractionSpan, got {type(located)!r}")
    _require_source_text(
        source_text, field_name="canonicalize_interaction_proposal.source_text")
    if located.span_end > len(source_text):
        raise ValueError(
            "canonicalize_interaction_proposal: located.span_end exceeds "
            "len(source_text)")
    if source_text[located.span_start:located.span_end] != located.exact_source_span:
        raise ValueError(
            "canonicalize_interaction_proposal: located.exact_source_span "
            "does not match source_text at its recorded span")
    if proposal is None:
        return None
    if not isinstance(proposal, InteractionOccurrenceProposal):
        raise ValueError(
            "canonicalize_interaction_proposal: proposal must be None or an "
            f"InteractionOccurrenceProposal, got {type(proposal)!r}")
    canonical_signal = as_enum(InteractionSignal, proposal.signal)
    canonical_applicability = as_enum(InteractionApplicability, proposal.applicability)
    canonical_state = as_enum(InteractionOccurrenceState, proposal.state)
    return InteractionSignalOccurrence(
        source_message_row_id=located.source_message_row_id,
        signal=canonical_signal,
        span_start=located.span_start,
        span_end=located.span_end,
        exact_source_span=source_text[located.span_start:located.span_end],
        applicability=canonical_applicability,
        state=canonical_state)


# -- Component-status wrapper tier (represents, does not compute, status) -

@dataclass(frozen=True)
class EvidenceAnalysis:
    """The evidence component's result for one turn: how trustworthy the
    component's own output is (AnalysisComponentStatus) plus the validated
    EvidenceItem tuple it produced. Represents status; does not compute it
    -- status computation is later orchestration's job. Does not contain
    source_text: full slice re-validation belongs to a future TurnAnalysis
    that actually holds the source text."""
    status: AnalysisComponentStatus
    items: tuple[EvidenceItem, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "status", as_enum(AnalysisComponentStatus, self.status))
        if type(self.items) is not tuple:
            raise ValueError(
                f"EvidenceAnalysis.items must be a tuple, got {type(self.items)!r}")
        for item in self.items:
            if not isinstance(item, EvidenceItem):
                raise ValueError(
                    "EvidenceAnalysis.items must contain only EvidenceItem, "
                    f"got {type(item)!r}")
        if self.status is AnalysisComponentStatus.UNAVAILABLE and self.items:
            raise ValueError("EvidenceAnalysis: UNAVAILABLE requires items == ()")
        if self.items:
            row_ids = {item.ref.source_message_row_id for item in self.items}
            if len(row_ids) > 1:
                raise ValueError(
                    "EvidenceAnalysis.items must all share the same "
                    "source_message_row_id -- one EvidenceAnalysis is one-turn "
                    "analysis, not a multi-message ledger")
            locations = [
                (item.ref.source_message_row_id, item.ref.span_start, item.ref.span_end)
                for item in self.items]
            if len(set(locations)) != len(locations):
                raise ValueError(
                    "EvidenceAnalysis.items contains duplicate evidence locations "
                    "-- exactly one EvidenceKind result is canonical per "
                    "deterministically-located span, even if evidence_kind differs")
            canonical = tuple(sorted(
                self.items,
                key=lambda item: (
                    item.ref.span_start, item.ref.span_end, item.ref.evidence_kind.value)))
            if canonical != self.items:
                raise ValueError(
                    "EvidenceAnalysis.items must already be in canonical "
                    "(span_start, span_end, evidence_kind) order -- not silently sorted")


@dataclass(frozen=True)
class InteractionAnalysis:
    """The interaction component's result for one turn: status, the
    deterministic InteractionRequest projection, and the full occurrence
    tuple it was derived from. request is never independently supplied
    information -- it must always equal the CURRENT_DIRECTIVE+ACTIVE
    projection over occurrences, enforced here, not left to caller
    convention. Conflicting simultaneously-active signals (e.g. NO_ADVICE
    and ADVICE_REQUESTED on two different current+active spans) are valid
    and preserved -- this type resolves nothing, matching
    InteractionRequest's own Stage 1 contract."""
    status: AnalysisComponentStatus
    request: InteractionRequest = InteractionRequest()
    occurrences: tuple[InteractionSignalOccurrence, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "status", as_enum(AnalysisComponentStatus, self.status))
        if not isinstance(self.request, InteractionRequest):
            raise ValueError(
                "InteractionAnalysis.request must be an InteractionRequest, "
                f"got {type(self.request)!r}")
        if type(self.occurrences) is not tuple:
            raise ValueError(
                "InteractionAnalysis.occurrences must be a tuple, "
                f"got {type(self.occurrences)!r}")
        for occurrence in self.occurrences:
            if not isinstance(occurrence, InteractionSignalOccurrence):
                raise ValueError(
                    "InteractionAnalysis.occurrences must contain only "
                    f"InteractionSignalOccurrence, got {type(occurrence)!r}")
        if self.status is AnalysisComponentStatus.UNAVAILABLE:
            if self.request != InteractionRequest() or self.occurrences:
                raise ValueError(
                    "InteractionAnalysis: UNAVAILABLE requires an empty "
                    "InteractionRequest and empty occurrences")
        if self.occurrences:
            row_ids = {o.source_message_row_id for o in self.occurrences}
            if len(row_ids) > 1:
                raise ValueError(
                    "InteractionAnalysis.occurrences must all share the same "
                    "source_message_row_id -- one InteractionAnalysis is "
                    "one-turn analysis, not a multi-message ledger")
            locations = [
                (o.source_message_row_id, o.span_start, o.span_end)
                for o in self.occurrences]
            if len(set(locations)) != len(locations):
                raise ValueError(
                    "InteractionAnalysis.occurrences contains duplicate source "
                    "locations -- exactly one interaction-signal result is "
                    "canonical per deterministically-located span")
            canonical = tuple(sorted(
                self.occurrences,
                key=lambda o: (
                    o.span_start, o.span_end, o.signal.value,
                    o.applicability.value, o.state.value)))
            if canonical != self.occurrences:
                raise ValueError(
                    "InteractionAnalysis.occurrences must already be in "
                    "canonical order -- not silently sorted")
        expected_signals = frozenset(
            o.signal for o in self.occurrences
            if o.applicability is InteractionApplicability.CURRENT_DIRECTIVE
            and o.state is InteractionOccurrenceState.ACTIVE)
        if self.request.signals != expected_signals:
            raise ValueError(
                "InteractionAnalysis.request must equal the deterministic "
                "CURRENT_DIRECTIVE+ACTIVE projection of occurrences")


@dataclass(frozen=True)
class IntentAnalysis:
    """The intent component's result for one turn: status plus a
    best-effort analyzer_intent classification. analyzer_intent carries no
    evidentiary weight independent of status -- unlike EvidenceAnalysis/
    InteractionAnalysis, it has no sub-items to partially accept, so
    DEGRADED and UNAVAILABLE both force Intent.UNKNOWN; only VALIDATED may
    carry a specific classification, and VALIDATED + UNKNOWN is itself a
    legitimate, non-failure result ('no sufficiently specific intent
    assigned'), not an error."""
    status: AnalysisComponentStatus
    analyzer_intent: Intent = Intent.UNKNOWN

    def __post_init__(self):
        object.__setattr__(self, "status", as_enum(AnalysisComponentStatus, self.status))
        object.__setattr__(self, "analyzer_intent", as_enum(Intent, self.analyzer_intent))
        if self.status is not AnalysisComponentStatus.VALIDATED \
                and self.analyzer_intent is not Intent.UNKNOWN:
            raise ValueError(
                "IntentAnalysis: DEGRADED/UNAVAILABLE requires "
                "analyzer_intent == Intent.UNKNOWN")


# -- Turn-level aggregate/result tier (validation + derived overall status) --

def _validate_occurrence_against_source(
        occurrence: InteractionSignalOccurrence, source_text: str) -> bool:
    """Deterministic, exact raw-slice provenance check for an
    already-validated InteractionSignalOccurrence and source_text, using
    the same raw Python code-point slice semantics as
    validate_evidence_against_source: no normalization, no fuzzy/semantic
    matching. Argument type validation is owned by the public aggregate/
    component constructors before this private helper is called -- unlike
    its Stage 1 sibling, it does not independently type-guard its own
    arguments. An ordinary mismatch returns False; this never raises for a
    mismatch. Module-private -- InteractionSignalOccurrence has no Stage 1
    equivalent of its own, so this exists only to be called from
    TurnAnalysis's own construction, never as a standalone public API."""
    if occurrence.span_end > len(source_text):
        return False
    return source_text[occurrence.span_start:occurrence.span_end] == occurrence.exact_source_span


@dataclass(frozen=True)
class TurnAnalysis:
    """The authoritative, one-turn aggregate combining all three Stage 2A
    component analyses under one source identity.

    VALIDATED means a component completed successfully. DEGRADED means any
    payload the component actually contains remains accepted/canonical
    payload, but the component's own completeness/reliability is degraded
    -- DEGRADED payload is never dropped here, nor treated as equivalent to
    VALIDATED. UNAVAILABLE means the component produced no usable output,
    with its own wrapper (EvidenceAnalysis/InteractionAnalysis) already
    enforcing an empty payload in that case.

    A TurnAnalysis being "authoritative" means the aggregate and whatever
    payload it actually contains satisfy every structural/provenance
    invariant below. It does NOT mean every component is VALIDATED or
    complete -- an aggregate may be fully authoritative while its eventual
    TurnAnalysisResult.status is PARTIAL.

    IntentAnalysis's analyzer_intent, when present here, is authoritative
    analyzer output that Stage 2B may consume subject to its own component
    status -- it is a turn-level classification signal, never evidence,
    factual truth, a confirmed case conceptualization, or a clinical
    diagnosis.
    """
    source_message_row_id: int
    source_text: str
    evidence: EvidenceAnalysis
    interaction: InteractionAnalysis
    intent: IntentAnalysis

    def __post_init__(self):
        _require_row_id(
            self.source_message_row_id,
            field_name="TurnAnalysis.source_message_row_id")
        if type(self.source_text) is not str:
            raise ValueError(
                f"TurnAnalysis.source_text must be a str, got {type(self.source_text)!r}")
        if not self.source_text:
            raise ValueError("TurnAnalysis.source_text must be non-empty")
        if not self.source_text.strip():
            raise ValueError("TurnAnalysis.source_text must not be whitespace-only")
        if not isinstance(self.evidence, EvidenceAnalysis):
            raise ValueError(
                "TurnAnalysis.evidence must be an EvidenceAnalysis, "
                f"got {type(self.evidence)!r}")
        if not isinstance(self.interaction, InteractionAnalysis):
            raise ValueError(
                "TurnAnalysis.interaction must be an InteractionAnalysis, "
                f"got {type(self.interaction)!r}")
        if not isinstance(self.intent, IntentAnalysis):
            raise ValueError(
                f"TurnAnalysis.intent must be an IntentAnalysis, got {type(self.intent)!r}")
        if (self.evidence.status is AnalysisComponentStatus.UNAVAILABLE
                and self.interaction.status is AnalysisComponentStatus.UNAVAILABLE
                and self.intent.status is AnalysisComponentStatus.UNAVAILABLE):
            raise ValueError(
                "TurnAnalysis: evidence, interaction, and intent must not all be "
                "UNAVAILABLE simultaneously -- no authoritative aggregate exists "
                "in that case")
        for item in self.evidence.items:
            if item.ref.source_message_row_id != self.source_message_row_id:
                raise ValueError(
                    "TurnAnalysis: an evidence item's source_message_row_id does "
                    "not match the aggregate's source_message_row_id")
            if not validate_evidence_against_source(item, self.source_text):
                raise ValueError(
                    "TurnAnalysis: an evidence item's exact_source_span does not "
                    "match source_text at its recorded span")
        for occurrence in self.interaction.occurrences:
            if occurrence.source_message_row_id != self.source_message_row_id:
                raise ValueError(
                    "TurnAnalysis: an interaction occurrence's "
                    "source_message_row_id does not match the aggregate's "
                    "source_message_row_id")
            if not _validate_occurrence_against_source(occurrence, self.source_text):
                raise ValueError(
                    "TurnAnalysis: an interaction occurrence's exact_source_span "
                    "does not match source_text at its recorded span")


@dataclass(frozen=True)
class TurnAnalysisResult:
    """The outcome envelope for one turn's analysis attempt: either an
    authoritative TurnAnalysis, or None if no authoritative aggregate could
    be constructed. status is always derived from analysis, never
    independently supplied -- this makes a self-contradictory result (e.g.
    status=FAILED alongside a real, valid analysis) structurally impossible
    rather than merely checked, not a dataclass field, and not a
    constructor argument.

    FAILED means only that no authoritative TurnAnalysis exists for
    Stage 2B to consume -- it does NOT prove every intermediate component
    that was attempted was unusable (e.g. an otherwise-VALIDATED component
    pair can still yield FAILED via a cross-component provenance mismatch).
    Recovery policy for a FAILED result is future orchestration's
    responsibility, not this type's.
    """
    analysis: TurnAnalysis | None

    def __post_init__(self):
        if self.analysis is not None and not isinstance(self.analysis, TurnAnalysis):
            raise ValueError(
                "TurnAnalysisResult.analysis must be None or a TurnAnalysis, "
                f"got {type(self.analysis)!r}")

    @property
    def status(self) -> TurnAnalysisStatus:
        if self.analysis is None:
            return TurnAnalysisStatus.FAILED
        if (self.analysis.evidence.status is AnalysisComponentStatus.VALIDATED
                and self.analysis.interaction.status is AnalysisComponentStatus.VALIDATED
                and self.analysis.intent.status is AnalysisComponentStatus.VALIDATED):
            return TurnAnalysisStatus.OK
        return TurnAnalysisStatus.PARTIAL
