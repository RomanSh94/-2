"""Professional Core V2 -- Professional Turn Producer V1.

Deterministic, offline assembly of one turn's `TurnAnalysisResult` from one
already-parsed, semantically untrusted analyzer output. Consumes the
existing Stage 2A deterministic locators/canonicalizers in
`professional_turn_analysis.py` (unmodified, imported only) -- this module
owns no offsets of its own, performs no semantic classification, and does
no candidate harvesting. It contains NO LLM/network call, NO JSON parsing,
NO database/bot/Telegram integration, and NO planner logic: turning a raw
model response into `UntrustedTurnAnalyzerOutput` is a separate, later,
not-yet-authorized slice.

Two failure classes are kept structurally distinct throughout: structurally
malformed input (a wrong container/element type on one of the transport
dataclasses below) raises `ValueError` at construction, loud, and is a
caller/adapter defect. Semantically untrustworthy content (an unknown enum
value, an ungrounded span, a duplicate/conflicting location) degrades the
affected component's `AnalysisComponentStatus` quietly and never crashes
`produce_turn_analysis`. An unexpected `ValueError` raised by the
deterministic core itself (`EvidenceAnalysis`/`InteractionAnalysis`/
`IntentAnalysis`/`TurnAnalysis`/the locators/canonicalizers) after this
module's own pre-validation is a programming defect, not analyzer
unreliability, and is deliberately left to propagate rather than being
caught and reinterpreted as FAILED.

Only imports: __future__, dataclasses, professional_turn_analysis, and
therapeutic_domain. Python 3.10 target (prod 3.10.12), matching
professional_turn_analysis.py.
"""
from __future__ import annotations

from dataclasses import dataclass

from professional_turn_analysis import (
    AnalysisComponentStatus,
    EvidenceAnalysis,
    EvidenceSpanCandidate,
    IntentAnalysis,
    InteractionAnalysis,
    InteractionApplicability,
    InteractionOccurrenceProposal,
    InteractionOccurrenceState,
    InteractionSignalOccurrence,
    InteractionSpanCandidate,
    LocatedEvidenceSpan,
    LocatedInteractionSpan,
    MAX_EVIDENCE_CANDIDATES_PER_TURN,
    MAX_INTERACTION_CANDIDATES_PER_TURN,
    TurnAnalysis,
    TurnAnalysisResult,
    canonicalize_evidence_proposal,
    canonicalize_interaction_proposal,
    locate_evidence_candidate,
    locate_interaction_candidate,
)
from therapeutic_domain import (
    EvidenceItem,
    EvidenceKind,
    Intent,
    InteractionRequest,
    InteractionSignal,
    as_enum,
)

# Internal per-candidate outcome tags -- never exposed outside this module.
_VALID = "VALID"
_ABSTAIN = "ABSTAIN"
_MALFORMED = "MALFORMED"


# -- Untrusted transport tier (one external analyzer response) -----------

@dataclass(frozen=True)
class EvidenceCandidateProposal:
    """One untrusted analyzer proposal: an untrusted candidate span plus an
    untrusted EvidenceKind claim for it (or None for explicit per-candidate
    abstention). `candidate`'s type is checked here (structural);
    `proposed_kind`'s validity is NOT -- that is deferred entirely to
    processing, mirroring InteractionOccurrenceProposal's own established
    "untrusted, unvalidated claim" contract in professional_turn_analysis.py."""
    candidate: EvidenceSpanCandidate
    proposed_kind: EvidenceKind | str | None

    def __post_init__(self):
        if not isinstance(self.candidate, EvidenceSpanCandidate):
            raise ValueError(
                "EvidenceCandidateProposal.candidate must be an "
                f"EvidenceSpanCandidate, got {type(self.candidate)!r}")


@dataclass(frozen=True)
class InteractionCandidateProposal:
    """One untrusted analyzer proposal: an untrusted candidate span plus an
    untrusted, all-three-axes-or-nothing interaction proposal (or None for
    explicit abstention). `candidate` and `proposal`'s OUTER type are
    checked here (structural); `proposal`'s inner signal/applicability/
    state axes are NOT -- that stays InteractionOccurrenceProposal's own
    "untrusted, unvalidated claim" territory, deferred to processing."""
    candidate: InteractionSpanCandidate
    proposal: InteractionOccurrenceProposal | None

    def __post_init__(self):
        if not isinstance(self.candidate, InteractionSpanCandidate):
            raise ValueError(
                "InteractionCandidateProposal.candidate must be an "
                f"InteractionSpanCandidate, got {type(self.candidate)!r}")
        if self.proposal is not None and not isinstance(
                self.proposal, InteractionOccurrenceProposal):
            raise ValueError(
                "InteractionCandidateProposal.proposal must be None or an "
                f"InteractionOccurrenceProposal, got {type(self.proposal)!r}")


@dataclass(frozen=True)
class UntrustedTurnAnalyzerOutput:
    """One external analyzer's full untrusted response for one turn:
    evidence-span proposals, interaction-span proposals, and an intent
    proposal, already parsed into Python types by a caller/adapter this
    slice does not implement. Container shape is checked here (structural);
    `intent_proposal`'s validity is NOT -- deferred to processing. Never
    accepts dict/list duck typing in place of the declared tuple/dataclass
    shapes."""
    evidence_candidates: tuple[EvidenceCandidateProposal, ...]
    interaction_candidates: tuple[InteractionCandidateProposal, ...]
    intent_proposal: Intent | str | None

    def __post_init__(self):
        if type(self.evidence_candidates) is not tuple:
            raise ValueError(
                "UntrustedTurnAnalyzerOutput.evidence_candidates must be a "
                f"tuple, got {type(self.evidence_candidates)!r}")
        for item in self.evidence_candidates:
            if not isinstance(item, EvidenceCandidateProposal):
                raise ValueError(
                    "UntrustedTurnAnalyzerOutput.evidence_candidates must "
                    f"contain only EvidenceCandidateProposal, got {type(item)!r}")
        if type(self.interaction_candidates) is not tuple:
            raise ValueError(
                "UntrustedTurnAnalyzerOutput.interaction_candidates must be "
                f"a tuple, got {type(self.interaction_candidates)!r}")
        for item in self.interaction_candidates:
            if not isinstance(item, InteractionCandidateProposal):
                raise ValueError(
                    "UntrustedTurnAnalyzerOutput.interaction_candidates must "
                    f"contain only InteractionCandidateProposal, got {type(item)!r}")


# -- Per-candidate semantic evaluation (pre-parse, then canonicalize) ----

def _evaluate_evidence_member(
        proposal: EvidenceCandidateProposal, located: LocatedEvidenceSpan,
        *, source_text: str,
) -> tuple[str, EvidenceItem | None]:
    """Resolve one already-located evidence candidate to (_VALID, item),
    (_ABSTAIN, None), or (_MALFORMED, None). `proposed_kind` is pre-parsed
    with the public as_enum before the existing canonicalizer is ever
    called with it, so a ValueError past this point can only mean a
    producer-side provenance bug, never analyzer content -- never caught
    broadly."""
    if proposal.proposed_kind is None:
        abstained = canonicalize_evidence_proposal(
            located, source_text=source_text, proposed_kind=None)
        return (_ABSTAIN, abstained)
    try:
        canonical_kind = as_enum(EvidenceKind, proposal.proposed_kind)
    except ValueError:
        return (_MALFORMED, None)
    item = canonicalize_evidence_proposal(
        located, source_text=source_text, proposed_kind=canonical_kind)
    return (_VALID, item)


def _evaluate_interaction_member(
        proposal: InteractionCandidateProposal, located: LocatedInteractionSpan,
        *, source_text: str,
) -> tuple[str, InteractionSignalOccurrence | None]:
    """Same contract as _evaluate_evidence_member for one already-located
    interaction candidate: all three axes (signal/applicability/state) are
    pre-parsed with as_enum before a fresh, already-canonical
    InteractionOccurrenceProposal is built and handed to the existing
    canonicalizer -- any one bad axis makes the whole proposal _MALFORMED,
    never a partial result."""
    if proposal.proposal is None:
        abstained = canonicalize_interaction_proposal(
            located, source_text=source_text, proposal=None)
        return (_ABSTAIN, abstained)
    try:
        canonical_signal = as_enum(InteractionSignal, proposal.proposal.signal)
        canonical_applicability = as_enum(
            InteractionApplicability, proposal.proposal.applicability)
        canonical_state = as_enum(
            InteractionOccurrenceState, proposal.proposal.state)
    except ValueError:
        return (_MALFORMED, None)
    canonical_proposal = InteractionOccurrenceProposal(
        signal=canonical_signal,
        applicability=canonical_applicability,
        state=canonical_state)
    occurrence = canonicalize_interaction_proposal(
        located, source_text=source_text, proposal=canonical_proposal)
    return (_VALID, occurrence)


# -- Component assembly (overflow gate, location grouping, status) -------

def _build_evidence_analysis(
        candidates: tuple[EvidenceCandidateProposal, ...],
        *, source_message_row_id: int, source_text: str,
) -> EvidenceAnalysis:
    """Overflow (over MAX_EVIDENCE_CANDIDATES_PER_TURN) forces the whole
    component UNAVAILABLE/empty with zero candidates processed -- no
    prefix/first-K selection. Otherwise every candidate is located first;
    only candidates sharing an exact location are grouped, and a group is
    evaluated as a whole (identical canonical results collapse to one
    survivor with a defect; anything else at a shared location -- mixed
    valid/abstain/malformed/differing results -- drops the entire group
    with no survivor) so a malformed sibling proposal can never be quietly
    dropped in favor of a clean one at the same span."""
    if len(candidates) > MAX_EVIDENCE_CANDIDATES_PER_TURN:
        return EvidenceAnalysis(status=AnalysisComponentStatus.UNAVAILABLE, items=())

    had_defect = False
    groups: dict[tuple[int, int, int], list[
        tuple[EvidenceCandidateProposal, LocatedEvidenceSpan]]] = {}
    for proposal in candidates:
        located = locate_evidence_candidate(
            proposal.candidate,
            source_message_row_id=source_message_row_id,
            source_text=source_text)
        if located is None:
            had_defect = True
            continue
        key = (source_message_row_id, located.span_start, located.span_end)
        groups.setdefault(key, []).append((proposal, located))

    survivors: list[EvidenceItem] = []
    for members in groups.values():
        outcomes = [
            _evaluate_evidence_member(p, loc, source_text=source_text)
            for p, loc in members]
        if len(members) == 1:
            kind, item = outcomes[0]
            if kind == _VALID:
                survivors.append(item)
            elif kind == _MALFORMED:
                had_defect = True
            # _ABSTAIN: neither a survivor nor a defect.
        else:
            kinds = {kind for kind, _payload in outcomes}
            if kinds == {_ABSTAIN}:
                had_defect = True
            elif kinds == {_VALID} and len({item for _kind, item in outcomes}) == 1:
                survivors.append(outcomes[0][1])
                had_defect = True
            else:
                had_defect = True  # conflicting group: drop entirely, no winner

    survivors.sort(key=lambda item: (
        item.ref.span_start, item.ref.span_end, item.ref.evidence_kind.value))

    if not had_defect:
        status = AnalysisComponentStatus.VALIDATED
    elif survivors:
        status = AnalysisComponentStatus.DEGRADED
    else:
        status = AnalysisComponentStatus.UNAVAILABLE
    return EvidenceAnalysis(status=status, items=tuple(survivors))


def _build_interaction_analysis(
        candidates: tuple[InteractionCandidateProposal, ...],
        *, source_message_row_id: int, source_text: str,
) -> InteractionAnalysis:
    """Same overflow/grouping/status discipline as _build_evidence_analysis
    for the interaction component. InteractionRequest is derived only from
    surviving CURRENT_DIRECTIVE+ACTIVE occurrences -- conflicting
    simultaneously-active signals (e.g. NO_ADVICE and ADVICE_REQUESTED) are
    preserved, never resolved; that stays later planner work."""
    if len(candidates) > MAX_INTERACTION_CANDIDATES_PER_TURN:
        return InteractionAnalysis(
            status=AnalysisComponentStatus.UNAVAILABLE,
            request=InteractionRequest(), occurrences=())

    had_defect = False
    groups: dict[tuple[int, int, int], list[
        tuple[InteractionCandidateProposal, LocatedInteractionSpan]]] = {}
    for proposal in candidates:
        located = locate_interaction_candidate(
            proposal.candidate,
            source_message_row_id=source_message_row_id,
            source_text=source_text)
        if located is None:
            had_defect = True
            continue
        key = (source_message_row_id, located.span_start, located.span_end)
        groups.setdefault(key, []).append((proposal, located))

    survivors: list[InteractionSignalOccurrence] = []
    for members in groups.values():
        outcomes = [
            _evaluate_interaction_member(p, loc, source_text=source_text)
            for p, loc in members]
        if len(members) == 1:
            kind, occurrence = outcomes[0]
            if kind == _VALID:
                survivors.append(occurrence)
            elif kind == _MALFORMED:
                had_defect = True
        else:
            kinds = {kind for kind, _payload in outcomes}
            if kinds == {_ABSTAIN}:
                had_defect = True
            elif kinds == {_VALID} and len({occ for _kind, occ in outcomes}) == 1:
                survivors.append(outcomes[0][1])
                had_defect = True
            else:
                had_defect = True

    survivors.sort(key=lambda o: (
        o.span_start, o.span_end, o.signal.value, o.applicability.value, o.state.value))

    request = InteractionRequest(signals=frozenset(
        o.signal for o in survivors
        if o.applicability is InteractionApplicability.CURRENT_DIRECTIVE
        and o.state is InteractionOccurrenceState.ACTIVE))

    if not had_defect:
        status = AnalysisComponentStatus.VALIDATED
    elif survivors:
        status = AnalysisComponentStatus.DEGRADED
    else:
        status = AnalysisComponentStatus.UNAVAILABLE
    return InteractionAnalysis(status=status, request=request, occurrences=tuple(survivors))


def _build_intent_analysis(intent_proposal: Intent | str | None) -> IntentAnalysis:
    """None (explicit abstention) and a valid Intent/raw value are both
    VALIDATED -- UNKNOWN is a legitimate non-failure result, not an error.
    An unparseable proposal is UNAVAILABLE/UNKNOWN; intent never carries a
    partial payload, so DEGRADED is not used here."""
    if intent_proposal is None:
        return IntentAnalysis(
            status=AnalysisComponentStatus.VALIDATED, analyzer_intent=Intent.UNKNOWN)
    try:
        canonical_intent = as_enum(Intent, intent_proposal)
    except ValueError:
        return IntentAnalysis(
            status=AnalysisComponentStatus.UNAVAILABLE, analyzer_intent=Intent.UNKNOWN)
    return IntentAnalysis(
        status=AnalysisComponentStatus.VALIDATED, analyzer_intent=canonical_intent)


# -- Public entry point ---------------------------------------------------

def produce_turn_analysis(
        *,
        source_message_row_id: int,
        source_text: str,
        analyzer_output: UntrustedTurnAnalyzerOutput | None,
) -> TurnAnalysisResult:
    """Deterministically assemble one turn's TurnAnalysisResult from one
    already-parsed, untrusted analyzer output. source_message_row_id/
    source_text are validated before analyzer_output is ever inspected, so
    a caller bug is never masked as "the analyzer had nothing to say".
    analyzer_output=None means no usable analyzer output exists at all and
    short-circuits to TurnAnalysisResult(analysis=None) (FAILED) without
    constructing any component. Any other non-UntrustedTurnAnalyzerOutput
    value is a caller/adapter defect and raises ValueError.

    Each component (evidence/interaction/intent) is built independently --
    a defect in one never downgrades another. If all three end up
    UNAVAILABLE, TurnAnalysis is never even attempted (its own contract
    forbids that combination); otherwise TurnAnalysis/TurnAnalysisResult
    are constructed directly, with no try/except around them -- an
    unexpected ValueError there is a programming defect and propagates."""
    if type(source_message_row_id) is not int or source_message_row_id <= 0:
        raise ValueError(
            "produce_turn_analysis: source_message_row_id must be a "
            f"positive int, got {source_message_row_id!r}")
    if type(source_text) is not str:
        raise ValueError(
            f"produce_turn_analysis: source_text must be a str, got {type(source_text)!r}")
    if not source_text:
        raise ValueError("produce_turn_analysis: source_text must be non-empty")
    if not source_text.strip():
        raise ValueError("produce_turn_analysis: source_text must not be whitespace-only")

    if analyzer_output is None:
        return TurnAnalysisResult(analysis=None)
    if not isinstance(analyzer_output, UntrustedTurnAnalyzerOutput):
        raise ValueError(
            "produce_turn_analysis: analyzer_output must be None or an "
            f"UntrustedTurnAnalyzerOutput, got {type(analyzer_output)!r}")

    evidence = _build_evidence_analysis(
        analyzer_output.evidence_candidates,
        source_message_row_id=source_message_row_id, source_text=source_text)
    interaction = _build_interaction_analysis(
        analyzer_output.interaction_candidates,
        source_message_row_id=source_message_row_id, source_text=source_text)
    intent = _build_intent_analysis(analyzer_output.intent_proposal)

    if (evidence.status is AnalysisComponentStatus.UNAVAILABLE
            and interaction.status is AnalysisComponentStatus.UNAVAILABLE
            and intent.status is AnalysisComponentStatus.UNAVAILABLE):
        return TurnAnalysisResult(analysis=None)

    analysis = TurnAnalysis(
        source_message_row_id=source_message_row_id,
        source_text=source_text,
        evidence=evidence,
        interaction=interaction,
        intent=intent)
    return TurnAnalysisResult(analysis=analysis)
