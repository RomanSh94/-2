"""Professional Core V2 -- Stage 2A pure structural primitives and
component wrappers (Slices 2A-1/2A-2).

Pure, offline, no I/O: this module defines the untrusted-candidate,
deterministically-located, canonically-classified primitive types, and the
per-component status wrappers (EvidenceAnalysis/InteractionAnalysis/
IntentAnalysis) for Stage 2A turn analysis. It contains NO offset-search
algorithm, NO semantic labeler, NO candidate harvesting, NO orchestration,
NO LLM/network calls, and NO database/bot/Telegram integration -- those are
later, separately authorized slices. A candidate/exact span stored here is
literal untrusted or deterministically-located text; nothing in this
module proves that a span's assigned EvidenceKind would be correct beyond
what the type system itself enforces, and nothing here classifies or
labels any span's semantic content. The component wrappers REPRESENT a
component's AnalysisComponentStatus; they do not compute it -- status
computation belongs to later, not-yet-implemented orchestration.

Only imports: __future__, dataclasses, enum, and therapeutic_domain
(reusing EvidenceItem, Intent, InteractionRequest, InteractionSignal, and
as_enum rather than duplicating them). Python 3.10 target (prod 3.10.12).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from therapeutic_domain import (
    EvidenceItem,
    Intent,
    InteractionRequest,
    InteractionSignal,
    as_enum,
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


def _require_bounded_text(value: str, *, field_name: str, max_chars: int) -> None:
    """Shared, non-mutating validation for an untrusted/located text field:
    must be exactly `str`, non-empty, not whitespace-only, and within the
    given character cap. Never strips, casefolds, or truncates -- the
    caller's own field always stores the exact original value."""
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a str, got {type(value)!r}")
    if not value:
        raise ValueError(f"{field_name} must be non-empty")
    if not value.strip():
        raise ValueError(f"{field_name} must not be whitespace-only")
    if len(value) > max_chars:
        raise ValueError(
            f"{field_name} exceeds max length {max_chars} (got {len(value)})")


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
    that is a later, not-yet-implemented location algorithm's job."""
    exact_source_span: str
    context_before: str | None = None
    context_after: str | None = None

    def __post_init__(self):
        _require_bounded_text(
            self.exact_source_span,
            field_name="EvidenceSpanCandidate.exact_source_span",
            max_chars=EVIDENCE_CANDIDATE_MAX_CHARS)
        if self.context_before is not None:
            _require_bounded_text(
                self.context_before,
                field_name="EvidenceSpanCandidate.context_before",
                max_chars=CONTEXT_BEFORE_MAX_CHARS)
        if self.context_after is not None:
            _require_bounded_text(
                self.context_after,
                field_name="EvidenceSpanCandidate.context_after",
                max_chars=CONTEXT_AFTER_MAX_CHARS)


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
            max_chars=INTERACTION_CANDIDATE_MAX_CHARS)
        if self.context_before is not None:
            _require_bounded_text(
                self.context_before,
                field_name="InteractionSpanCandidate.context_before",
                max_chars=CONTEXT_BEFORE_MAX_CHARS)
        if self.context_after is not None:
            _require_bounded_text(
                self.context_after,
                field_name="InteractionSpanCandidate.context_after",
                max_chars=CONTEXT_AFTER_MAX_CHARS)


# -- Deterministic located tier (code-owned offsets; the actual
# location-by-search algorithm is a later, not-yet-implemented slice) ----

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


# -- Canonical, already-classified occurrence tier -----------------------

@dataclass(frozen=True)
class InteractionSignalOccurrence:
    """One already-classified interaction-signal occurrence: which
    message, which exact span, which InteractionSignal, and its
    applicability/retraction state. This type only REPRESENTS an
    already-classified occurrence -- it does not derive InteractionRequest,
    resolve policy conflicts, or implement retraction detection; those are
    later Stage 2A/2B concerns."""
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
