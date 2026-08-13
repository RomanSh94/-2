"""Structural characterization tests for the Professional Core V2 Stage 2A
turn-level aggregate (Slice 2A-3) in professional_turn_analysis.py:
TurnAnalysis, TurnAnalysisResult.

These tests prove ONLY structural/provenance/status-derivation behavior:
frozen immutability, exact field boundaries, source identity invariants,
cross-component row provenance, source-text re-validation (reusing Stage
1's validate_evidence_against_source unchanged, and exercising the new
private _validate_occurrence_against_source exclusively through
TurnAnalysis construction, never imported directly), the all-UNAVAILABLE
construction block, the OK/PARTIAL/FAILED status derivation, DEGRADED
payload preservation, and the no-silent-repair guarantee. They do NOT test
any algorithm -- offset-search, semantic labeling, candidate harvesting,
and orchestration are not implemented in this slice. EvidenceKind/
InteractionSignal values below are arbitrary structural fixture data, not
semantic claims about what any real phrase "should" classify as.
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
    TurnAnalysis,
    TurnAnalysisResult,
    TurnAnalysisStatus,
)
from therapeutic_domain import (
    EvidenceItem,
    EvidenceKind,
    EvidenceRef,
    InteractionRequest,
    InteractionSignal,
)

_SOURCE_TEXT = "hello world"


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


def _empty_evidence(status=AnalysisComponentStatus.VALIDATED):
    return EvidenceAnalysis(status=status)


def _empty_interaction(status=AnalysisComponentStatus.VALIDATED):
    return InteractionAnalysis(status=status)


def _unknown_intent(status=AnalysisComponentStatus.VALIDATED):
    return IntentAnalysis(status=status)


def _minimal_turn_analysis(**overrides):
    kwargs = dict(
        source_message_row_id=1,
        source_text=_SOURCE_TEXT,
        evidence=_empty_evidence(),
        interaction=_empty_interaction(),
        intent=_unknown_intent(),
    )
    kwargs.update(overrides)
    return TurnAnalysis(**kwargs)


# ── TurnAnalysis: structural ─────────────────────────────────────────────

def test_turn_analysis_is_frozen():
    ta = _minimal_turn_analysis()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ta.source_text = "other"


def test_turn_analysis_exact_field_boundary():
    fields = {f.name for f in dataclasses.fields(TurnAnalysis)}
    assert fields == {
        "source_message_row_id", "source_text", "evidence", "interaction", "intent"}


def test_turn_analysis_valid_minimal_construction_status_ok():
    evidence = _empty_evidence()
    interaction = _empty_interaction()
    intent = _unknown_intent()
    ta = TurnAnalysis(
        source_message_row_id=1, source_text=_SOURCE_TEXT,
        evidence=evidence, interaction=interaction, intent=intent)
    assert ta.source_message_row_id == 1
    assert ta.source_text == _SOURCE_TEXT
    assert ta.evidence is evidence
    assert ta.interaction is interaction
    assert ta.intent is intent
    assert TurnAnalysisResult(analysis=ta).status is TurnAnalysisStatus.OK


def test_turn_analysis_row_id_zero_rejected():
    with pytest.raises(ValueError):
        _minimal_turn_analysis(source_message_row_id=0)


def test_turn_analysis_row_id_negative_rejected():
    with pytest.raises(ValueError):
        _minimal_turn_analysis(source_message_row_id=-1)


def test_turn_analysis_row_id_bool_rejected():
    with pytest.raises(ValueError):
        _minimal_turn_analysis(source_message_row_id=True)


def test_turn_analysis_row_id_non_int_rejected():
    with pytest.raises(ValueError):
        _minimal_turn_analysis(source_message_row_id="1")


# ── TurnAnalysis: source_text ─────────────────────────────────────────────

def test_turn_analysis_source_text_whitespace_preserved_exactly():
    ta = _minimal_turn_analysis(source_text="  hello world  ")
    assert ta.source_text == "  hello world  "


def test_turn_analysis_source_text_empty_rejected():
    with pytest.raises(ValueError):
        _minimal_turn_analysis(source_text="")


def test_turn_analysis_source_text_whitespace_only_rejected():
    with pytest.raises(ValueError):
        _minimal_turn_analysis(source_text="   ")


def test_turn_analysis_source_text_non_str_rejected():
    with pytest.raises(ValueError):
        _minimal_turn_analysis(source_text=12345)


def test_turn_analysis_source_text_no_arbitrary_max_length_cap():
    long_text = "a" * 1000
    ta = _minimal_turn_analysis(source_text=long_text)
    assert ta.source_text == long_text


# ── TurnAnalysis: component type boundaries ─────────────────────────────

def test_turn_analysis_wrong_evidence_type_rejected():
    with pytest.raises(ValueError):
        _minimal_turn_analysis(evidence="not an EvidenceAnalysis")


def test_turn_analysis_wrong_interaction_type_rejected():
    with pytest.raises(ValueError):
        _minimal_turn_analysis(interaction="not an InteractionAnalysis")


def test_turn_analysis_wrong_intent_type_rejected():
    with pytest.raises(ValueError):
        _minimal_turn_analysis(intent="not an IntentAnalysis")


# ── TurnAnalysis: evidence provenance + source validation ───────────────

def test_turn_analysis_evidence_valid_item_accepted():
    item = _evidence_item(row_id=1, start=0, end=5, text="hello")
    evidence = EvidenceAnalysis(status=AnalysisComponentStatus.VALIDATED, items=(item,))
    ta = _minimal_turn_analysis(evidence=evidence)
    assert ta.evidence.items == (item,)


def test_turn_analysis_evidence_row_mismatch_rejected():
    """exact_source_span genuinely matches source_text at its span (would
    pass source validation if the row matched) -- isolates this fixture to
    the row-mismatch invariant only."""
    item = _evidence_item(row_id=2, start=0, end=5, text="hello")
    evidence = EvidenceAnalysis(status=AnalysisComponentStatus.VALIDATED, items=(item,))
    with pytest.raises(ValueError):
        _minimal_turn_analysis(source_message_row_id=1, evidence=evidence)


def test_turn_analysis_evidence_source_slice_mismatch_rejected():
    """row_id genuinely matches the aggregate -- isolates this fixture to
    the source-slice-mismatch invariant only."""
    item = _evidence_item(row_id=1, start=0, end=5, text="xxxxx")
    evidence = EvidenceAnalysis(status=AnalysisComponentStatus.VALIDATED, items=(item,))
    with pytest.raises(ValueError):
        _minimal_turn_analysis(source_message_row_id=1, evidence=evidence)


def test_turn_analysis_evidence_empty_does_not_invent_provenance_and_is_valid():
    ta = _minimal_turn_analysis(evidence=_empty_evidence())
    assert ta.evidence.items == ()


def test_turn_analysis_evidence_out_of_bounds_span_rejected_through_aggregate():
    item = _evidence_item(row_id=1, start=8, end=20, text="x" * 12)
    evidence = EvidenceAnalysis(status=AnalysisComponentStatus.VALIDATED, items=(item,))
    with pytest.raises(ValueError):
        _minimal_turn_analysis(source_message_row_id=1, evidence=evidence)


# ── TurnAnalysis: interaction provenance + source validation ────────────

def test_turn_analysis_interaction_valid_occurrence_accepted():
    occ = _occurrence(row_id=1, start=0, end=5, signal=InteractionSignal.NO_ADVICE, text="hello")
    request = InteractionRequest(signals=frozenset({InteractionSignal.NO_ADVICE}))
    interaction = InteractionAnalysis(
        status=AnalysisComponentStatus.VALIDATED, request=request, occurrences=(occ,))
    ta = _minimal_turn_analysis(interaction=interaction)
    assert ta.interaction.occurrences == (occ,)


def test_turn_analysis_interaction_row_mismatch_rejected():
    occ = _occurrence(row_id=2, start=0, end=5, signal=InteractionSignal.NO_ADVICE, text="hello")
    request = InteractionRequest(signals=frozenset({InteractionSignal.NO_ADVICE}))
    interaction = InteractionAnalysis(
        status=AnalysisComponentStatus.VALIDATED, request=request, occurrences=(occ,))
    with pytest.raises(ValueError):
        _minimal_turn_analysis(source_message_row_id=1, interaction=interaction)


def test_turn_analysis_interaction_source_slice_mismatch_rejected():
    occ = _occurrence(row_id=1, start=0, end=5, signal=InteractionSignal.NO_ADVICE, text="xxxxx")
    request = InteractionRequest(signals=frozenset({InteractionSignal.NO_ADVICE}))
    interaction = InteractionAnalysis(
        status=AnalysisComponentStatus.VALIDATED, request=request, occurrences=(occ,))
    with pytest.raises(ValueError):
        _minimal_turn_analysis(source_message_row_id=1, interaction=interaction)


def test_turn_analysis_interaction_empty_does_not_invent_provenance_and_is_valid():
    ta = _minimal_turn_analysis(interaction=_empty_interaction())
    assert ta.interaction.occurrences == ()


def test_turn_analysis_interaction_span_beyond_source_length_rejected():
    occ = _occurrence(row_id=1, start=8, end=20, signal=InteractionSignal.NO_ADVICE, text="x" * 12)
    request = InteractionRequest(signals=frozenset({InteractionSignal.NO_ADVICE}))
    interaction = InteractionAnalysis(
        status=AnalysisComponentStatus.VALIDATED, request=request, occurrences=(occ,))
    with pytest.raises(ValueError):
        _minimal_turn_analysis(source_message_row_id=1, interaction=interaction)


def test_turn_analysis_interaction_unicode_non_bmp_slice_matches_code_point_offsets():
    """The only test in the entire suite exercising
    _validate_occurrence_against_source's Unicode behavior. Unlike the
    evidence side (where validate_evidence_against_source already has a
    sealed Stage 1 code-point test), this new private helper has no other
    test surface at all, so this case adds real coverage, not redundancy.
    A UTF-16-based (rather than Python code-point) implementation would
    misalign this offset, since U+1F600 is outside the BMP."""
    source_text = "hi \U0001F600 there"
    occ = _occurrence(
        row_id=1, start=3, end=4, signal=InteractionSignal.JUST_TALK, text="\U0001F600")
    request = InteractionRequest(signals=frozenset({InteractionSignal.JUST_TALK}))
    interaction = InteractionAnalysis(
        status=AnalysisComponentStatus.VALIDATED, request=request, occurrences=(occ,))
    ta = TurnAnalysis(
        source_message_row_id=1, source_text=source_text,
        evidence=_empty_evidence(), interaction=interaction, intent=_unknown_intent())
    assert ta.interaction.occurrences == (occ,)


# ── TurnAnalysis: all-UNAVAILABLE matrix ────────────────────────────────

def test_turn_analysis_all_three_unavailable_rejected():
    evidence = _empty_evidence(status=AnalysisComponentStatus.UNAVAILABLE)
    interaction = _empty_interaction(status=AnalysisComponentStatus.UNAVAILABLE)
    intent = _unknown_intent(status=AnalysisComponentStatus.UNAVAILABLE)
    with pytest.raises(ValueError):
        _minimal_turn_analysis(evidence=evidence, interaction=interaction, intent=intent)


def test_turn_analysis_validated_unavailable_unavailable_accepted_status_partial():
    evidence = _empty_evidence(status=AnalysisComponentStatus.VALIDATED)
    interaction = _empty_interaction(status=AnalysisComponentStatus.UNAVAILABLE)
    intent = _unknown_intent(status=AnalysisComponentStatus.UNAVAILABLE)
    ta = _minimal_turn_analysis(evidence=evidence, interaction=interaction, intent=intent)
    assert TurnAnalysisResult(analysis=ta).status is TurnAnalysisStatus.PARTIAL


def test_turn_analysis_unavailable_validated_unavailable_accepted_status_partial():
    evidence = _empty_evidence(status=AnalysisComponentStatus.UNAVAILABLE)
    interaction = _empty_interaction(status=AnalysisComponentStatus.VALIDATED)
    intent = _unknown_intent(status=AnalysisComponentStatus.UNAVAILABLE)
    ta = _minimal_turn_analysis(evidence=evidence, interaction=interaction, intent=intent)
    assert TurnAnalysisResult(analysis=ta).status is TurnAnalysisStatus.PARTIAL


def test_turn_analysis_unavailable_unavailable_validated_accepted_status_partial():
    evidence = _empty_evidence(status=AnalysisComponentStatus.UNAVAILABLE)
    interaction = _empty_interaction(status=AnalysisComponentStatus.UNAVAILABLE)
    intent = _unknown_intent(status=AnalysisComponentStatus.VALIDATED)
    ta = _minimal_turn_analysis(evidence=evidence, interaction=interaction, intent=intent)
    assert TurnAnalysisResult(analysis=ta).status is TurnAnalysisStatus.PARTIAL


def test_turn_analysis_one_degraded_rest_validated_accepted_status_partial():
    evidence = _empty_evidence(status=AnalysisComponentStatus.DEGRADED)
    interaction = _empty_interaction(status=AnalysisComponentStatus.VALIDATED)
    intent = _unknown_intent(status=AnalysisComponentStatus.VALIDATED)
    ta = _minimal_turn_analysis(evidence=evidence, interaction=interaction, intent=intent)
    assert TurnAnalysisResult(analysis=ta).status is TurnAnalysisStatus.PARTIAL


# ── TurnAnalysisResult: structural ──────────────────────────────────────

def test_turn_analysis_result_exact_field_boundary():
    fields = {f.name for f in dataclasses.fields(TurnAnalysisResult)}
    assert fields == {"analysis"}


def test_turn_analysis_result_analysis_none_status_failed():
    result = TurnAnalysisResult(analysis=None)
    assert result.status is TurnAnalysisStatus.FAILED


def test_turn_analysis_result_wrong_analysis_type_rejected():
    with pytest.raises(ValueError):
        TurnAnalysisResult(analysis="not a TurnAnalysis")


def test_turn_analysis_result_status_not_a_constructor_argument():
    with pytest.raises(TypeError):
        TurnAnalysisResult(analysis=None, status=TurnAnalysisStatus.OK)


def test_turn_analysis_result_is_frozen():
    result = TurnAnalysisResult(analysis=None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.analysis = _minimal_turn_analysis()


def test_turn_analysis_result_analysis_argument_required():
    with pytest.raises(TypeError):
        TurnAnalysisResult()


def test_turn_analysis_result_exactly_one_unavailable_status_partial():
    evidence = _empty_evidence(status=AnalysisComponentStatus.UNAVAILABLE)
    interaction = _empty_interaction(status=AnalysisComponentStatus.VALIDATED)
    intent = _unknown_intent(status=AnalysisComponentStatus.VALIDATED)
    ta = _minimal_turn_analysis(evidence=evidence, interaction=interaction, intent=intent)
    assert TurnAnalysisResult(analysis=ta).status is TurnAnalysisStatus.PARTIAL


# ── DEGRADED payload semantics ───────────────────────────────────────────

def test_turn_analysis_evidence_degraded_item_preserved_status_partial():
    """DEGRADED (not VALIDATED) evidence plus UNAVAILABLE interaction and
    UNAVAILABLE intent -- zero VALIDATED components, yet the aggregate is
    still authoritative because not all three are UNAVAILABLE. Proves
    DEGRADED != UNAVAILABLE, that zero VALIDATED components does not imply
    rejection, that only all-three-UNAVAILABLE is forbidden, and that
    accepted DEGRADED payload is preserved unchanged."""
    item = _evidence_item(row_id=1, start=0, end=5, text="hello")
    evidence = EvidenceAnalysis(status=AnalysisComponentStatus.DEGRADED, items=(item,))
    interaction = _empty_interaction(status=AnalysisComponentStatus.UNAVAILABLE)
    intent = _unknown_intent(status=AnalysisComponentStatus.UNAVAILABLE)
    ta = _minimal_turn_analysis(evidence=evidence, interaction=interaction, intent=intent)
    assert ta.evidence is evidence
    assert ta.evidence.items == (item,)
    assert ta.evidence.items[0] is item
    assert TurnAnalysisResult(analysis=ta).status is TurnAnalysisStatus.PARTIAL


def test_turn_analysis_interaction_degraded_occurrence_preserved_status_partial():
    occ = _occurrence(row_id=1, start=0, end=5, signal=InteractionSignal.NO_ADVICE, text="hello")
    request = InteractionRequest(signals=frozenset({InteractionSignal.NO_ADVICE}))
    interaction = InteractionAnalysis(
        status=AnalysisComponentStatus.DEGRADED, request=request, occurrences=(occ,))
    ta = _minimal_turn_analysis(interaction=interaction)
    assert ta.interaction is interaction
    assert ta.interaction.occurrences == (occ,)
    assert ta.interaction.occurrences[0] is occ
    assert TurnAnalysisResult(analysis=ta).status is TurnAnalysisStatus.PARTIAL


# ── No silent child repair ───────────────────────────────────────────────

def test_turn_analysis_multi_item_one_bad_evidence_rejects_whole_aggregate():
    """good is fully valid on every axis (row, source-slice, canonical
    order, no duplicate location); bad only violates source-slice
    validation -- proves TurnAnalysis rejects the WHOLE aggregate rather
    than silently keeping good and dropping bad."""
    good = _evidence_item(row_id=1, start=0, end=5, text="hello")
    bad = _evidence_item(row_id=1, start=6, end=11, text="xxxxx")
    evidence = EvidenceAnalysis(status=AnalysisComponentStatus.VALIDATED, items=(good, bad))
    with pytest.raises(ValueError):
        _minimal_turn_analysis(evidence=evidence)


def test_turn_analysis_multi_occurrence_one_bad_interaction_rejects_whole_aggregate():
    good = _occurrence(row_id=1, start=0, end=5, signal=InteractionSignal.NO_ADVICE, text="hello")
    bad = _occurrence(row_id=1, start=6, end=11, signal=InteractionSignal.JUST_TALK, text="xxxxx")
    request = InteractionRequest(
        signals=frozenset({InteractionSignal.NO_ADVICE, InteractionSignal.JUST_TALK}))
    interaction = InteractionAnalysis(
        status=AnalysisComponentStatus.VALIDATED, request=request, occurrences=(good, bad))
    with pytest.raises(ValueError):
        _minimal_turn_analysis(interaction=interaction)


def test_turn_analysis_preserves_child_component_object_identity():
    evidence = _empty_evidence()
    interaction = _empty_interaction()
    intent = _unknown_intent()
    ta = TurnAnalysis(
        source_message_row_id=1, source_text=_SOURCE_TEXT,
        evidence=evidence, interaction=interaction, intent=intent)
    assert ta.evidence is evidence
    assert ta.interaction is interaction
    assert ta.intent is intent
