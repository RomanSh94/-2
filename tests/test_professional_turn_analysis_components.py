"""Structural characterization tests for the Professional Core V2 Stage 2A
component wrappers (Slice 2A-2) in professional_turn_analysis.py:
EvidenceAnalysis, InteractionAnalysis, IntentAnalysis.

These tests prove ONLY structural behavior: status coercion/fail-closed
validation, container-type/member-type checks, the UNAVAILABLE-forces-empty
invariant, cross-message rejection, canonical-order enforcement, duplicate-
location rejection, the InteractionRequest projection invariant, and exact
field boundaries. They do NOT test any algorithm -- offset-search, semantic
labeling, candidate harvesting, and orchestration are not implemented in
this slice. No EvidenceKind/FACT/BEHAVIOR/PATTERN semantic classification
is asserted anywhere in this file; EvidenceKind values below are chosen
arbitrarily as structural fixture data, not as claims about what any real
phrase "should" classify as.
"""
import dataclasses

import pytest

from professional_turn_analysis import (
    AnalysisComponentStatus,
    EvidenceAnalysis,
    IntentAnalysis,
    InteractionAnalysis,
    InteractionApplicability,
    InteractionOccurrenceState,
    InteractionSignalOccurrence,
)
from therapeutic_domain import (
    EvidenceItem,
    EvidenceKind,
    EvidenceRef,
    Intent,
    InteractionRequest,
    InteractionSignal,
)


# ── Fixture helpers (structural data only, not semantic claims) ─────────

def _evidence_item(row_id=1, start=0, end=5, kind=EvidenceKind.USER_REPORTED_FACT, text="hello"):
    return EvidenceItem(
        ref=EvidenceRef(
            source_message_row_id=row_id, span_start=start, span_end=end, evidence_kind=kind),
        exact_source_span=text)


def _occurrence(row_id=1, start=0, end=5, signal=InteractionSignal.NO_ADVICE, text="hello",
                applicability=InteractionApplicability.CURRENT_DIRECTIVE,
                state=InteractionOccurrenceState.ACTIVE):
    return InteractionSignalOccurrence(
        source_message_row_id=row_id, signal=signal, span_start=start, span_end=end,
        exact_source_span=text, applicability=applicability, state=state)


# ── EvidenceAnalysis ─────────────────────────────────────────────────────

def test_evidence_analysis_is_frozen():
    ea = EvidenceAnalysis(status=AnalysisComponentStatus.VALIDATED)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ea.status = AnalysisComponentStatus.DEGRADED


def test_evidence_analysis_raw_status_string_normalizes():
    ea = EvidenceAnalysis(status="VALIDATED")
    assert ea.status is AnalysisComponentStatus.VALIDATED


def test_evidence_analysis_unknown_status_rejected():
    with pytest.raises(ValueError):
        EvidenceAnalysis(status="NOT_A_REAL_STATUS")


def test_evidence_analysis_list_items_rejected():
    item = _evidence_item()
    with pytest.raises(ValueError):
        EvidenceAnalysis(status=AnalysisComponentStatus.VALIDATED, items=[item])


def test_evidence_analysis_wrong_item_member_rejected():
    with pytest.raises(ValueError):
        EvidenceAnalysis(status=AnalysisComponentStatus.VALIDATED, items=("not an item",))


def test_evidence_analysis_validated_empty_valid():
    ea = EvidenceAnalysis(status=AnalysisComponentStatus.VALIDATED)
    assert ea.status is AnalysisComponentStatus.VALIDATED
    assert ea.items == ()


def test_evidence_analysis_degraded_empty_valid():
    ea = EvidenceAnalysis(status=AnalysisComponentStatus.DEGRADED)
    assert ea.items == ()


def test_evidence_analysis_unavailable_empty_valid():
    ea = EvidenceAnalysis(status=AnalysisComponentStatus.UNAVAILABLE)
    assert ea.items == ()


def test_evidence_analysis_unavailable_non_empty_rejected():
    with pytest.raises(ValueError):
        EvidenceAnalysis(status=AnalysisComponentStatus.UNAVAILABLE, items=(_evidence_item(),))


def test_evidence_analysis_same_message_ordered_items_accepted():
    item1 = _evidence_item(
        row_id=1, start=0, end=5, kind=EvidenceKind.USER_REPORTED_FACT, text="hello")
    item2 = _evidence_item(
        row_id=1, start=10, end=15, kind=EvidenceKind.USER_REPORTED_BODY, text="world")
    ea = EvidenceAnalysis(status=AnalysisComponentStatus.VALIDATED, items=(item1, item2))
    assert ea.items == (item1, item2)


def test_evidence_analysis_cross_message_items_rejected():
    item1 = _evidence_item(row_id=1, start=0, end=5)
    item2 = _evidence_item(row_id=2, start=10, end=15)
    with pytest.raises(ValueError):
        EvidenceAnalysis(status=AnalysisComponentStatus.VALIDATED, items=(item1, item2))


def test_evidence_analysis_unsorted_items_rejected():
    item1 = _evidence_item(row_id=1, start=0, end=5)
    item2 = _evidence_item(row_id=1, start=10, end=15)
    with pytest.raises(ValueError):
        EvidenceAnalysis(status=AnalysisComponentStatus.VALIDATED, items=(item2, item1))


def test_evidence_analysis_duplicate_same_location_same_kind_rejected():
    item1 = _evidence_item(
        row_id=1, start=0, end=5, kind=EvidenceKind.USER_REPORTED_FACT, text="hello")
    item2 = _evidence_item(
        row_id=1, start=0, end=5, kind=EvidenceKind.USER_REPORTED_FACT, text="hello")
    with pytest.raises(ValueError):
        EvidenceAnalysis(status=AnalysisComponentStatus.VALIDATED, items=(item1, item2))


def test_evidence_analysis_duplicate_same_location_different_kind_rejected():
    """item1's kind (BODY) sorts before item2's kind (FACT) alphabetically,
    so with identical (span_start, span_end) the pair is already in
    canonical (span_start, span_end, evidence_kind.value) order -- the
    ONLY invalid condition is the duplicate exact location, isolated
    regardless of validation check order."""
    item1 = _evidence_item(
        row_id=1, start=0, end=5, kind=EvidenceKind.USER_REPORTED_BODY, text="hello")
    item2 = _evidence_item(
        row_id=1, start=0, end=5, kind=EvidenceKind.USER_REPORTED_FACT, text="hello")
    with pytest.raises(ValueError):
        EvidenceAnalysis(status=AnalysisComponentStatus.VALIDATED, items=(item1, item2))


def test_evidence_analysis_overlapping_different_locations_accepted():
    item1 = _evidence_item(
        row_id=1, start=0, end=10, kind=EvidenceKind.USER_REPORTED_FACT, text="0123456789")
    item2 = _evidence_item(
        row_id=1, start=5, end=15, kind=EvidenceKind.USER_REPORTED_PATTERN, text="5678901234")
    ea = EvidenceAnalysis(status=AnalysisComponentStatus.VALIDATED, items=(item1, item2))
    assert ea.items == (item1, item2)


def test_evidence_analysis_exact_field_boundary():
    fields = {f.name for f in dataclasses.fields(EvidenceAnalysis)}
    assert fields == {"status", "items"}


# ── InteractionAnalysis ──────────────────────────────────────────────────

def test_interaction_analysis_is_frozen():
    ia = InteractionAnalysis(status=AnalysisComponentStatus.VALIDATED)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ia.status = AnalysisComponentStatus.DEGRADED


def test_interaction_analysis_raw_status_string_normalizes():
    ia = InteractionAnalysis(status="VALIDATED")
    assert ia.status is AnalysisComponentStatus.VALIDATED


def test_interaction_analysis_unknown_status_rejected():
    with pytest.raises(ValueError):
        InteractionAnalysis(status="NOT_A_REAL_STATUS")


def test_interaction_analysis_wrong_request_type_rejected():
    with pytest.raises(ValueError):
        InteractionAnalysis(status=AnalysisComponentStatus.VALIDATED, request={"signals": []})


def test_interaction_analysis_list_occurrences_rejected():
    """occurrences=[occ] is a list, not a tuple -- the intended violation.
    occ and request are constructed so every other invariant independently
    holds (valid types, same message trivially, no duplicate, canonical
    order trivially, and request exactly matches the projection), so the
    ONLY accidental invalid condition is the container type."""
    occ = _occurrence(
        signal=InteractionSignal.NO_ADVICE,
        applicability=InteractionApplicability.CURRENT_DIRECTIVE,
        state=InteractionOccurrenceState.ACTIVE)
    request = InteractionRequest(signals=frozenset({InteractionSignal.NO_ADVICE}))
    with pytest.raises(ValueError):
        InteractionAnalysis(
            status=AnalysisComponentStatus.VALIDATED, request=request, occurrences=[occ])


def test_interaction_analysis_wrong_occurrence_member_rejected():
    with pytest.raises(ValueError):
        InteractionAnalysis(
            status=AnalysisComponentStatus.VALIDATED, occurrences=("not an occurrence",))


def test_interaction_analysis_validated_empty_valid():
    ia = InteractionAnalysis(status=AnalysisComponentStatus.VALIDATED)
    assert ia.request == InteractionRequest()
    assert ia.occurrences == ()


def test_interaction_analysis_degraded_empty_valid():
    ia = InteractionAnalysis(status=AnalysisComponentStatus.DEGRADED)
    assert ia.occurrences == ()


def test_interaction_analysis_unavailable_empty_valid():
    ia = InteractionAnalysis(status=AnalysisComponentStatus.UNAVAILABLE)
    assert ia.occurrences == ()


def test_interaction_analysis_unavailable_non_empty_occurrence_rejected():
    """applicability=NON_CURRENT_CONTEXT keeps the projection trivially
    empty, isolating this to the UNAVAILABLE-forces-empty-occurrences
    invariant specifically, not the projection invariant."""
    occ = _occurrence(applicability=InteractionApplicability.NON_CURRENT_CONTEXT)
    with pytest.raises(ValueError):
        InteractionAnalysis(status=AnalysisComponentStatus.UNAVAILABLE, occurrences=(occ,))


def test_interaction_analysis_unavailable_non_empty_request_rejected():
    """LOGICALLY_ENTAILED_OVERLAP, not accidental fixture debt: UNAVAILABLE
    requires occurrences == (), and an empty occurrences tuple always
    projects to an empty signal set -- so any non-empty request under
    UNAVAILABLE necessarily also mismatches that (forced-empty) projection.
    There is no fixture with UNAVAILABLE + non-empty request + empty
    occurrences + a matching projection, because an empty projection can
    only ever match an empty request; using non-empty occurrences instead
    would itself violate UNAVAILABLE's own occurrences == () requirement.
    This overlap is intrinsic to the contract, not a coincidental second
    violation, and must not be "fixed" by weakening either invariant."""
    with pytest.raises(ValueError):
        InteractionAnalysis(
            status=AnalysisComponentStatus.UNAVAILABLE,
            request=InteractionRequest(signals=frozenset({InteractionSignal.NO_ADVICE})))


def test_interaction_analysis_same_message_ordered_occurrences_accepted():
    occ1 = _occurrence(row_id=1, start=0, end=5, signal=InteractionSignal.NO_ADVICE, text="hello")
    occ2 = _occurrence(row_id=1, start=10, end=15, signal=InteractionSignal.JUST_TALK, text="world")
    request = InteractionRequest(
        signals=frozenset({InteractionSignal.NO_ADVICE, InteractionSignal.JUST_TALK}))
    ia = InteractionAnalysis(
        status=AnalysisComponentStatus.VALIDATED, request=request, occurrences=(occ1, occ2))
    assert ia.occurrences == (occ1, occ2)


def test_interaction_analysis_cross_message_occurrences_rejected():
    """occ1/occ2 use different source_message_row_id values -- the intended
    violation. Both map to the same signal so the deduplicated projection
    is a single value exactly matching the supplied request, spans are
    already in canonical order, and locations do not collide -- the ONLY
    accidental invalid condition is cross-message provenance."""
    occ1 = _occurrence(
        row_id=1, start=0, end=5, signal=InteractionSignal.NO_ADVICE, text="hello",
        applicability=InteractionApplicability.CURRENT_DIRECTIVE,
        state=InteractionOccurrenceState.ACTIVE)
    occ2 = _occurrence(
        row_id=2, start=10, end=15, signal=InteractionSignal.NO_ADVICE, text="world",
        applicability=InteractionApplicability.CURRENT_DIRECTIVE,
        state=InteractionOccurrenceState.ACTIVE)
    request = InteractionRequest(signals=frozenset({InteractionSignal.NO_ADVICE}))
    with pytest.raises(ValueError):
        InteractionAnalysis(
            status=AnalysisComponentStatus.VALIDATED, request=request,
            occurrences=(occ1, occ2))


def test_interaction_analysis_unsorted_occurrences_rejected():
    """request exactly matches the projection over {early, late} regardless
    of tuple order (frozenset membership is order-independent), and the two
    locations do not collide -- the ONLY invalid condition is that the
    tuple is passed in reverse of the required canonical
    (span_start, span_end, signal.value, applicability.value, state.value)
    order, isolated regardless of validation check order."""
    early = _occurrence(row_id=1, start=0, end=5, signal=InteractionSignal.NO_ADVICE, text="hello")
    late = _occurrence(row_id=1, start=10, end=15, signal=InteractionSignal.JUST_TALK, text="world")
    request = InteractionRequest(
        signals=frozenset({InteractionSignal.NO_ADVICE, InteractionSignal.JUST_TALK}))
    with pytest.raises(ValueError):
        InteractionAnalysis(
            status=AnalysisComponentStatus.VALIDATED, request=request,
            occurrences=(late, early))


def test_interaction_analysis_duplicate_source_location_rejected():
    """occ1 and occ2 carry identical signal/applicability/state, so their
    canonical-order keys tie (Python's stable sort keeps input order) and
    their projection is a single deduplicated signal exactly matching the
    supplied request -- the ONLY invalid condition is the duplicate exact
    source location, isolated regardless of validation check order."""
    occ1 = _occurrence(row_id=1, start=0, end=5, signal=InteractionSignal.NO_ADVICE, text="hello")
    occ2 = _occurrence(row_id=1, start=0, end=5, signal=InteractionSignal.NO_ADVICE, text="hello")
    request = InteractionRequest(signals=frozenset({InteractionSignal.NO_ADVICE}))
    with pytest.raises(ValueError):
        InteractionAnalysis(
            status=AnalysisComponentStatus.VALIDATED, request=request,
            occurrences=(occ1, occ2))


def test_interaction_analysis_overlapping_different_locations_accepted():
    occ1 = _occurrence(
        row_id=1, start=0, end=10, signal=InteractionSignal.NO_ADVICE, text="0123456789")
    occ2 = _occurrence(
        row_id=1, start=5, end=15, signal=InteractionSignal.JUST_TALK, text="5678901234")
    request = InteractionRequest(
        signals=frozenset({InteractionSignal.NO_ADVICE, InteractionSignal.JUST_TALK}))
    ia = InteractionAnalysis(
        status=AnalysisComponentStatus.VALIDATED, request=request, occurrences=(occ1, occ2))
    assert ia.occurrences == (occ1, occ2)


def test_interaction_analysis_current_directive_active_contributes_signal():
    occ = _occurrence(
        signal=InteractionSignal.NO_ADVICE,
        applicability=InteractionApplicability.CURRENT_DIRECTIVE,
        state=InteractionOccurrenceState.ACTIVE)
    ia = InteractionAnalysis(
        status=AnalysisComponentStatus.VALIDATED,
        request=InteractionRequest(signals=frozenset({InteractionSignal.NO_ADVICE})),
        occurrences=(occ,))
    assert ia.request.signals == frozenset({InteractionSignal.NO_ADVICE})


def test_interaction_analysis_current_directive_retracted_excluded():
    occ = _occurrence(
        signal=InteractionSignal.NO_ADVICE,
        applicability=InteractionApplicability.CURRENT_DIRECTIVE,
        state=InteractionOccurrenceState.RETRACTED)
    ia = InteractionAnalysis(
        status=AnalysisComponentStatus.VALIDATED, request=InteractionRequest(),
        occurrences=(occ,))
    assert ia.request.signals == frozenset()


def test_interaction_analysis_non_current_context_active_excluded():
    occ = _occurrence(
        signal=InteractionSignal.NO_ADVICE,
        applicability=InteractionApplicability.NON_CURRENT_CONTEXT,
        state=InteractionOccurrenceState.ACTIVE)
    ia = InteractionAnalysis(
        status=AnalysisComponentStatus.VALIDATED, request=InteractionRequest(),
        occurrences=(occ,))
    assert ia.request.signals == frozenset()


def test_interaction_analysis_non_current_context_retracted_excluded():
    occ = _occurrence(
        signal=InteractionSignal.NO_ADVICE,
        applicability=InteractionApplicability.NON_CURRENT_CONTEXT,
        state=InteractionOccurrenceState.RETRACTED)
    ia = InteractionAnalysis(
        status=AnalysisComponentStatus.VALIDATED, request=InteractionRequest(),
        occurrences=(occ,))
    assert ia.request.signals == frozenset()


def test_interaction_analysis_projection_mismatch_rejected():
    occ = _occurrence(
        signal=InteractionSignal.NO_ADVICE,
        applicability=InteractionApplicability.CURRENT_DIRECTIVE,
        state=InteractionOccurrenceState.ACTIVE)
    with pytest.raises(ValueError):
        InteractionAnalysis(
            status=AnalysisComponentStatus.VALIDATED,
            request=InteractionRequest(signals=frozenset({InteractionSignal.JUST_TALK})),
            occurrences=(occ,))


def test_interaction_analysis_conflicting_active_signals_preserved():
    occ1 = _occurrence(start=0, end=5, signal=InteractionSignal.NO_ADVICE, text="hello")
    occ2 = _occurrence(start=10, end=15, signal=InteractionSignal.ADVICE_REQUESTED, text="world")
    request = InteractionRequest(
        signals=frozenset({InteractionSignal.NO_ADVICE, InteractionSignal.ADVICE_REQUESTED}))
    ia = InteractionAnalysis(
        status=AnalysisComponentStatus.VALIDATED, request=request, occurrences=(occ1, occ2))
    assert ia.request.signals == frozenset(
        {InteractionSignal.NO_ADVICE, InteractionSignal.ADVICE_REQUESTED})


def test_interaction_analysis_conflict_resolution_absent_regardless_of_order():
    """Swapping which conflicting signal has the earlier span still
    preserves both -- proves there is no first-wins/last-wins collapsing
    anywhere in this wrapper (the opposite span-order pairing from the
    conflicting-signals test above)."""
    occ1 = _occurrence(start=0, end=5, signal=InteractionSignal.ADVICE_REQUESTED, text="hello")
    occ2 = _occurrence(start=10, end=15, signal=InteractionSignal.NO_ADVICE, text="world")
    request = InteractionRequest(
        signals=frozenset({InteractionSignal.NO_ADVICE, InteractionSignal.ADVICE_REQUESTED}))
    ia = InteractionAnalysis(
        status=AnalysisComponentStatus.VALIDATED, request=request, occurrences=(occ1, occ2))
    assert len(ia.request.signals) == 2
    assert ia.request.signals == frozenset(
        {InteractionSignal.NO_ADVICE, InteractionSignal.ADVICE_REQUESTED})


def test_interaction_analysis_exact_field_boundary():
    fields = {f.name for f in dataclasses.fields(InteractionAnalysis)}
    assert fields == {"status", "request", "occurrences"}


# ── IntentAnalysis ────────────────────────────────────────────────────────

def test_intent_analysis_is_frozen():
    ia = IntentAnalysis(status=AnalysisComponentStatus.VALIDATED)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ia.status = AnalysisComponentStatus.DEGRADED


def test_intent_analysis_raw_status_string_normalizes():
    ia = IntentAnalysis(status="VALIDATED")
    assert ia.status is AnalysisComponentStatus.VALIDATED


def test_intent_analysis_raw_intent_string_normalizes():
    ia = IntentAnalysis(status=AnalysisComponentStatus.VALIDATED, analyzer_intent="VENT")
    assert ia.analyzer_intent is Intent.VENT


def test_intent_analysis_unknown_status_rejected():
    with pytest.raises(ValueError):
        IntentAnalysis(status="NOT_A_REAL_STATUS")


def test_intent_analysis_unknown_intent_value_rejected():
    with pytest.raises(ValueError):
        IntentAnalysis(
            status=AnalysisComponentStatus.VALIDATED, analyzer_intent="NOT_A_REAL_INTENT")


def test_intent_analysis_validated_specific_intent_valid():
    ia = IntentAnalysis(status=AnalysisComponentStatus.VALIDATED, analyzer_intent=Intent.VENT)
    assert ia.analyzer_intent is Intent.VENT


def test_intent_analysis_validated_unknown_valid():
    ia = IntentAnalysis(status=AnalysisComponentStatus.VALIDATED, analyzer_intent=Intent.UNKNOWN)
    assert ia.analyzer_intent is Intent.UNKNOWN


def test_intent_analysis_degraded_unknown_valid():
    ia = IntentAnalysis(status=AnalysisComponentStatus.DEGRADED, analyzer_intent=Intent.UNKNOWN)
    assert ia.analyzer_intent is Intent.UNKNOWN


def test_intent_analysis_unavailable_unknown_valid():
    ia = IntentAnalysis(status=AnalysisComponentStatus.UNAVAILABLE, analyzer_intent=Intent.UNKNOWN)
    assert ia.analyzer_intent is Intent.UNKNOWN


def test_intent_analysis_degraded_specific_intent_rejected():
    with pytest.raises(ValueError):
        IntentAnalysis(status=AnalysisComponentStatus.DEGRADED, analyzer_intent=Intent.VENT)


def test_intent_analysis_unavailable_specific_intent_rejected():
    with pytest.raises(ValueError):
        IntentAnalysis(status=AnalysisComponentStatus.UNAVAILABLE, analyzer_intent=Intent.VENT)


def test_intent_analysis_exact_field_boundary():
    fields = {f.name for f in dataclasses.fields(IntentAnalysis)}
    assert fields == {"status", "analyzer_intent"}
