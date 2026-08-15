"""Behavioral tests for the Professional Turn Producer V1
(professional_turn_producer.py): produce_turn_analysis and the three
untrusted transport dataclasses (EvidenceCandidateProposal,
InteractionCandidateProposal, UntrustedTurnAnalyzerOutput).

These tests exercise the public produce_turn_analysis boundary wherever
practical. Two tests (over-limit "never calls the locator" and "unexpected
invariant error propagates") use pytest's standard monkeypatch mechanism on
public names imported into professional_turn_producer's own namespace --
this is not reaching into a private helper, it is the only way to prove a
negative (something was never called / something was not silently caught)
through the module's own observable behavior.
"""
import pytest

from professional_turn_analysis import (
    AnalysisComponentStatus,
    EvidenceSpanCandidate,
    InteractionApplicability,
    InteractionOccurrenceProposal,
    InteractionOccurrenceState,
    InteractionSpanCandidate,
    MAX_EVIDENCE_CANDIDATES_PER_TURN,
    MAX_INTERACTION_CANDIDATES_PER_TURN,
    TurnAnalysisStatus,
)
from therapeutic_domain import EvidenceKind, Intent, InteractionSignal

from professional_turn_producer import (
    EvidenceCandidateProposal,
    InteractionCandidateProposal,
    UntrustedTurnAnalyzerOutput,
    produce_turn_analysis,
)

_TWENTY_WORDS = [
    "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel",
    "india", "juliet", "kilo", "lima", "mike", "november", "oscar", "papa",
    "quebec", "romeo", "sierra", "tango",
]
_TEN_WORDS = _TWENTY_WORDS[:10]


def _empty_output(intent_proposal=None):
    return UntrustedTurnAnalyzerOutput(
        evidence_candidates=(), interaction_candidates=(), intent_proposal=intent_proposal)


# ── §1/§2/§3/§4: public input validation ─────────────────────────────────

@pytest.mark.parametrize("bad_row_id", [0, -1, True, "1", 1.5, None])
def test_bad_row_id_rejected(bad_row_id):
    with pytest.raises(ValueError):
        produce_turn_analysis(
            source_message_row_id=bad_row_id, source_text="hello", analyzer_output=None)


@pytest.mark.parametrize("bad_text", ["", "   ", 12345, None])
def test_bad_source_text_rejected(bad_text):
    with pytest.raises(ValueError):
        produce_turn_analysis(
            source_message_row_id=1, source_text=bad_text, analyzer_output=None)


def test_wrong_analyzer_output_type_rejected():
    with pytest.raises(ValueError):
        produce_turn_analysis(
            source_message_row_id=1, source_text="hello", analyzer_output="not valid")


def test_analyzer_output_none_yields_failed():
    result = produce_turn_analysis(
        source_message_row_id=1, source_text="hello", analyzer_output=None)
    assert result.analysis is None
    assert result.status is TurnAnalysisStatus.FAILED


def test_row_id_validated_before_analyzer_output_inspected():
    # A malformed analyzer_output type would itself raise -- but the bad
    # row id must win, proving validation order.
    with pytest.raises(ValueError):
        produce_turn_analysis(
            source_message_row_id=0, source_text="hello", analyzer_output="also bad")


# ── Transport structural contract (§3/§5) ────────────────────────────────

def test_evidence_candidate_proposal_rejects_wrong_candidate_type():
    with pytest.raises(ValueError):
        EvidenceCandidateProposal(candidate="not a candidate", proposed_kind=None)


def test_evidence_candidate_proposal_does_not_validate_proposed_kind():
    proposal = EvidenceCandidateProposal(
        candidate=EvidenceSpanCandidate(exact_source_span="hello"),
        proposed_kind="NOT_A_REAL_KIND")
    assert proposal.proposed_kind == "NOT_A_REAL_KIND"


def test_interaction_candidate_proposal_rejects_wrong_candidate_type():
    with pytest.raises(ValueError):
        InteractionCandidateProposal(candidate="not a candidate", proposal=None)


def test_interaction_candidate_proposal_rejects_wrong_proposal_type():
    with pytest.raises(ValueError):
        InteractionCandidateProposal(
            candidate=InteractionSpanCandidate(exact_source_span="hello"),
            proposal="not a proposal")


def test_interaction_candidate_proposal_does_not_validate_inner_axes():
    proposal = InteractionCandidateProposal(
        candidate=InteractionSpanCandidate(exact_source_span="hello"),
        proposal=InteractionOccurrenceProposal(
            signal="GARBAGE", applicability="GARBAGE", state="GARBAGE"))
    assert proposal.proposal.signal == "GARBAGE"


def test_untrusted_output_rejects_list_for_evidence_candidates():
    with pytest.raises(ValueError):
        UntrustedTurnAnalyzerOutput(
            evidence_candidates=[], interaction_candidates=(), intent_proposal=None)


def test_untrusted_output_rejects_wrong_evidence_member_type():
    with pytest.raises(ValueError):
        UntrustedTurnAnalyzerOutput(
            evidence_candidates=("not a proposal",), interaction_candidates=(),
            intent_proposal=None)


def test_untrusted_output_rejects_list_for_interaction_candidates():
    with pytest.raises(ValueError):
        UntrustedTurnAnalyzerOutput(
            evidence_candidates=(), interaction_candidates=[], intent_proposal=None)


def test_untrusted_output_rejects_wrong_interaction_member_type():
    with pytest.raises(ValueError):
        UntrustedTurnAnalyzerOutput(
            evidence_candidates=(), interaction_candidates=("not a proposal",),
            intent_proposal=None)


def test_untrusted_output_does_not_validate_intent_proposal():
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=(), interaction_candidates=(), intent_proposal="GARBAGE")
    assert raw.intent_proposal == "GARBAGE"


# ── Empty analyzer output (§17 item 5) ───────────────────────────────────

def test_empty_analyzer_output_yields_ok():
    result = produce_turn_analysis(
        source_message_row_id=1, source_text="hello there", analyzer_output=_empty_output())
    assert result.status is TurnAnalysisStatus.OK
    a = result.analysis
    assert a.evidence.status is AnalysisComponentStatus.VALIDATED and a.evidence.items == ()
    assert a.interaction.status is AnalysisComponentStatus.VALIDATED
    assert a.interaction.occurrences == ()
    assert a.intent.status is AnalysisComponentStatus.VALIDATED
    assert a.intent.analyzer_intent is Intent.UNKNOWN


# ── Clean evidence / interaction success (items 6-7) ─────────────────────

def test_clean_evidence_success():
    text = "I feel very anxious about this."
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=(EvidenceCandidateProposal(
            candidate=EvidenceSpanCandidate(exact_source_span="anxious"),
            proposed_kind=EvidenceKind.USER_REPORTED_EMOTION),),
        interaction_candidates=(), intent_proposal=None)
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    ea = result.analysis.evidence
    assert ea.status is AnalysisComponentStatus.VALIDATED
    assert len(ea.items) == 1
    assert ea.items[0].exact_source_span == "anxious"
    assert ea.items[0].ref.evidence_kind is EvidenceKind.USER_REPORTED_EMOTION


def test_clean_interaction_success():
    text = "Please just listen, no advice right now."
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=(),
        interaction_candidates=(InteractionCandidateProposal(
            candidate=InteractionSpanCandidate(exact_source_span="no advice"),
            proposal=InteractionOccurrenceProposal(
                signal=InteractionSignal.NO_ADVICE,
                applicability=InteractionApplicability.CURRENT_DIRECTIVE,
                state=InteractionOccurrenceState.ACTIVE)),),
        intent_proposal=None)
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    ia = result.analysis.interaction
    assert ia.status is AnalysisComponentStatus.VALIDATED
    assert len(ia.occurrences) == 1
    assert ia.request.signals == frozenset({InteractionSignal.NO_ADVICE})


# ── Explicit abstention is not a defect (items 8-9) ──────────────────────

def test_evidence_explicit_abstention_not_a_defect():
    text = "Something happened yesterday."
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=(EvidenceCandidateProposal(
            candidate=EvidenceSpanCandidate(exact_source_span="Something"),
            proposed_kind=None),),
        interaction_candidates=(), intent_proposal=None)
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    ea = result.analysis.evidence
    assert ea.status is AnalysisComponentStatus.VALIDATED
    assert ea.items == ()


def test_interaction_explicit_abstention_not_a_defect():
    text = "Something happened yesterday."
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=(),
        interaction_candidates=(InteractionCandidateProposal(
            candidate=InteractionSpanCandidate(exact_source_span="Something"),
            proposal=None),),
        intent_proposal=None)
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    ia = result.analysis.interaction
    assert ia.status is AnalysisComponentStatus.VALIDATED
    assert ia.occurrences == ()


# ── Unlocated candidates (items 10-11) ───────────────────────────────────

def test_unlocated_evidence_candidate_yields_unavailable():
    text = "This does not contain the phrase."
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=(EvidenceCandidateProposal(
            candidate=EvidenceSpanCandidate(exact_source_span="nonexistent phrase"),
            proposed_kind=EvidenceKind.USER_REPORTED_FACT),),
        interaction_candidates=(), intent_proposal=None)
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    assert result.analysis.evidence.status is AnalysisComponentStatus.UNAVAILABLE
    assert result.analysis.evidence.items == ()


def test_unlocated_interaction_candidate_yields_unavailable():
    text = "This does not contain the phrase."
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=(),
        interaction_candidates=(InteractionCandidateProposal(
            candidate=InteractionSpanCandidate(exact_source_span="nonexistent phrase"),
            proposal=None),),
        intent_proposal=None)
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    assert result.analysis.interaction.status is AnalysisComponentStatus.UNAVAILABLE
    assert result.analysis.interaction.occurrences == ()


# ── Malformed semantic values never escape as exceptions (items 12-15) ──

def test_malformed_evidence_kind_is_analyzer_defect():
    text = "I feel anxious about this."
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=(EvidenceCandidateProposal(
            candidate=EvidenceSpanCandidate(exact_source_span="anxious"),
            proposed_kind="NOT_A_REAL_KIND"),),
        interaction_candidates=(), intent_proposal=None)
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    assert result.analysis.evidence.status is AnalysisComponentStatus.UNAVAILABLE
    assert result.analysis.evidence.items == ()


@pytest.mark.parametrize("bad_field,proposal_kwargs", [
    ("signal", dict(signal="GARBAGE", applicability=InteractionApplicability.CURRENT_DIRECTIVE,
                     state=InteractionOccurrenceState.ACTIVE)),
    ("applicability", dict(signal=InteractionSignal.NO_ADVICE, applicability="GARBAGE",
                            state=InteractionOccurrenceState.ACTIVE)),
    ("state", dict(signal=InteractionSignal.NO_ADVICE,
                    applicability=InteractionApplicability.CURRENT_DIRECTIVE, state="GARBAGE")),
])
def test_malformed_interaction_axis_is_analyzer_defect(bad_field, proposal_kwargs):
    text = "Please just listen right now."
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=(),
        interaction_candidates=(InteractionCandidateProposal(
            candidate=InteractionSpanCandidate(exact_source_span="listen"),
            proposal=InteractionOccurrenceProposal(**proposal_kwargs)),),
        intent_proposal=None)
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    assert result.analysis.interaction.status is AnalysisComponentStatus.UNAVAILABLE
    assert result.analysis.interaction.occurrences == ()


# ── Intent policy (item 16) ──────────────────────────────────────────────

def test_intent_valid_value():
    result = produce_turn_analysis(
        source_message_row_id=1, source_text="hi",
        analyzer_output=_empty_output(intent_proposal=Intent.VENT))
    assert result.analysis.intent.status is AnalysisComponentStatus.VALIDATED
    assert result.analysis.intent.analyzer_intent is Intent.VENT


def test_intent_none_is_validated_unknown():
    result = produce_turn_analysis(
        source_message_row_id=1, source_text="hi", analyzer_output=_empty_output())
    assert result.analysis.intent.status is AnalysisComponentStatus.VALIDATED
    assert result.analysis.intent.analyzer_intent is Intent.UNKNOWN


def test_intent_malformed_is_unavailable_unknown():
    result = produce_turn_analysis(
        source_message_row_id=1, source_text="hi",
        analyzer_output=_empty_output(intent_proposal="NOT_REAL_INTENT"))
    assert result.analysis.intent.status is AnalysisComponentStatus.UNAVAILABLE
    assert result.analysis.intent.analyzer_intent is Intent.UNKNOWN


# ── Duplicate-location policy (items 17-22) ──────────────────────────────

def test_identical_evidence_duplicate_collapses_to_degraded():
    text = "I feel anxious about this."
    dup = (
        EvidenceCandidateProposal(
            candidate=EvidenceSpanCandidate(exact_source_span="anxious"),
            proposed_kind=EvidenceKind.USER_REPORTED_EMOTION),
        EvidenceCandidateProposal(
            candidate=EvidenceSpanCandidate(exact_source_span="anxious"),
            proposed_kind=EvidenceKind.USER_REPORTED_EMOTION))
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=dup, interaction_candidates=(), intent_proposal=None)
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    ea = result.analysis.evidence
    assert ea.status is AnalysisComponentStatus.DEGRADED
    assert len(ea.items) == 1
    assert ea.items[0].ref.evidence_kind is EvidenceKind.USER_REPORTED_EMOTION


def test_identical_interaction_duplicate_collapses_to_degraded():
    text = "Please just listen right now."
    proposal = InteractionOccurrenceProposal(
        signal=InteractionSignal.JUST_TALK,
        applicability=InteractionApplicability.CURRENT_DIRECTIVE,
        state=InteractionOccurrenceState.ACTIVE)
    dup = (
        InteractionCandidateProposal(
            candidate=InteractionSpanCandidate(exact_source_span="listen"), proposal=proposal),
        InteractionCandidateProposal(
            candidate=InteractionSpanCandidate(exact_source_span="listen"), proposal=proposal))
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=(), interaction_candidates=dup, intent_proposal=None)
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    ia = result.analysis.interaction
    assert ia.status is AnalysisComponentStatus.DEGRADED
    assert len(ia.occurrences) == 1


def test_all_abstention_duplicate_is_unavailable():
    text = "Something happened yesterday."
    dup = (
        EvidenceCandidateProposal(
            candidate=EvidenceSpanCandidate(exact_source_span="Something"), proposed_kind=None),
        EvidenceCandidateProposal(
            candidate=EvidenceSpanCandidate(exact_source_span="Something"), proposed_kind=None))
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=dup, interaction_candidates=(), intent_proposal=None)
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    ea = result.analysis.evidence
    assert ea.status is AnalysisComponentStatus.UNAVAILABLE
    assert ea.items == ()


def test_conflicting_evidence_duplicate_drops_entire_group():
    text = "I feel anxious about this."
    dup = (
        EvidenceCandidateProposal(
            candidate=EvidenceSpanCandidate(exact_source_span="anxious"),
            proposed_kind=EvidenceKind.USER_REPORTED_EMOTION),
        EvidenceCandidateProposal(
            candidate=EvidenceSpanCandidate(exact_source_span="anxious"),
            proposed_kind=EvidenceKind.USER_REPORTED_FACT))
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=dup, interaction_candidates=(), intent_proposal=None)
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    ea = result.analysis.evidence
    assert ea.status is AnalysisComponentStatus.UNAVAILABLE
    assert ea.items == ()


def test_conflicting_interaction_duplicate_drops_entire_group():
    text = "Please just listen right now."
    dup = (
        InteractionCandidateProposal(
            candidate=InteractionSpanCandidate(exact_source_span="listen"),
            proposal=InteractionOccurrenceProposal(
                signal=InteractionSignal.JUST_TALK,
                applicability=InteractionApplicability.CURRENT_DIRECTIVE,
                state=InteractionOccurrenceState.ACTIVE)),
        InteractionCandidateProposal(
            candidate=InteractionSpanCandidate(exact_source_span="listen"),
            proposal=InteractionOccurrenceProposal(
                signal=InteractionSignal.NO_ADVICE,
                applicability=InteractionApplicability.CURRENT_DIRECTIVE,
                state=InteractionOccurrenceState.ACTIVE)))
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=(), interaction_candidates=dup, intent_proposal=None)
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    ia = result.analysis.interaction
    assert ia.status is AnalysisComponentStatus.UNAVAILABLE
    assert ia.occurrences == ()


def test_interaction_duplicate_valid_plus_malformed_drops_entire_group():
    """CRITICAL: a valid proposal and a malformed proposal at the exact same
    location must be recognized as ONE conflicting group and dropped
    together -- never a valid NO_ADVICE survivor plus an unrelated defect."""
    text = "Please just listen, no advice right now."
    valid_proposal = InteractionOccurrenceProposal(
        signal=InteractionSignal.NO_ADVICE,
        applicability=InteractionApplicability.CURRENT_DIRECTIVE,
        state=InteractionOccurrenceState.ACTIVE)
    malformed_proposal = InteractionOccurrenceProposal(
        signal="NOT_A_REAL_SIGNAL",
        applicability=InteractionApplicability.CURRENT_DIRECTIVE,
        state=InteractionOccurrenceState.ACTIVE)
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=(),
        interaction_candidates=(
            InteractionCandidateProposal(
                candidate=InteractionSpanCandidate(exact_source_span="no advice"),
                proposal=valid_proposal),
            InteractionCandidateProposal(
                candidate=InteractionSpanCandidate(exact_source_span="no advice"),
                proposal=malformed_proposal)),
        intent_proposal=None)
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    ia = result.analysis.interaction
    assert ia.occurrences == ()
    assert ia.request.signals == frozenset()
    assert ia.status is AnalysisComponentStatus.UNAVAILABLE


# ── Mixed survivors/defects (items 23-24) ────────────────────────────────

def test_valid_and_defective_different_locations_is_degraded():
    text = "I feel anxious and something else happened."
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=(
            EvidenceCandidateProposal(
                candidate=EvidenceSpanCandidate(exact_source_span="anxious"),
                proposed_kind=EvidenceKind.USER_REPORTED_EMOTION),
            EvidenceCandidateProposal(
                candidate=EvidenceSpanCandidate(exact_source_span="nonexistent phrase"),
                proposed_kind=EvidenceKind.USER_REPORTED_FACT)),
        interaction_candidates=(), intent_proposal=None)
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    ea = result.analysis.evidence
    assert ea.status is AnalysisComponentStatus.DEGRADED
    assert len(ea.items) == 1
    assert ea.items[0].exact_source_span == "anxious"


def test_defective_only_mixed_defect_types_is_unavailable():
    text = "Please just listen right now."
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=(),
        interaction_candidates=(
            InteractionCandidateProposal(
                candidate=InteractionSpanCandidate(exact_source_span="does not occur"),
                proposal=None),
            InteractionCandidateProposal(
                candidate=InteractionSpanCandidate(exact_source_span="listen"),
                proposal=InteractionOccurrenceProposal(
                    signal="GARBAGE",
                    applicability=InteractionApplicability.CURRENT_DIRECTIVE,
                    state=InteractionOccurrenceState.ACTIVE))),
        intent_proposal=None)
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    assert result.analysis.interaction.status is AnalysisComponentStatus.UNAVAILABLE
    assert result.analysis.interaction.occurrences == ()


# ── Overflow policy (items 25-28) ────────────────────────────────────────

def test_exactly_max_evidence_candidates_processable():
    text = " ".join(_TWENTY_WORDS)
    proposals = tuple(
        EvidenceCandidateProposal(
            candidate=EvidenceSpanCandidate(exact_source_span=word),
            proposed_kind=EvidenceKind.USER_REPORTED_FACT)
        for word in _TWENTY_WORDS)
    assert len(proposals) == MAX_EVIDENCE_CANDIDATES_PER_TURN
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=proposals, interaction_candidates=(), intent_proposal=None)
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    ea = result.analysis.evidence
    assert ea.status is AnalysisComponentStatus.VALIDATED
    assert len(ea.items) == MAX_EVIDENCE_CANDIDATES_PER_TURN


def test_over_max_evidence_candidates_entire_component_unavailable():
    words = _TWENTY_WORDS + ["uniform"]
    text = " ".join(words)
    proposals = tuple(
        EvidenceCandidateProposal(
            candidate=EvidenceSpanCandidate(exact_source_span=word),
            proposed_kind=EvidenceKind.USER_REPORTED_FACT)
        for word in words)
    assert len(proposals) == MAX_EVIDENCE_CANDIDATES_PER_TURN + 1
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=proposals, interaction_candidates=(), intent_proposal=None)
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    ea = result.analysis.evidence
    assert ea.status is AnalysisComponentStatus.UNAVAILABLE
    assert ea.items == ()


def test_over_max_evidence_candidates_never_calls_locator(monkeypatch):
    import professional_turn_producer as producer_module
    calls = []
    monkeypatch.setattr(
        producer_module, "locate_evidence_candidate",
        lambda *a, **k: calls.append((a, k)))
    words = _TWENTY_WORDS + ["uniform"]
    text = " ".join(words)
    proposals = tuple(
        EvidenceCandidateProposal(
            candidate=EvidenceSpanCandidate(exact_source_span=word),
            proposed_kind=EvidenceKind.USER_REPORTED_FACT)
        for word in words)
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=proposals, interaction_candidates=(), intent_proposal=None)
    produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    assert calls == []


def test_exactly_max_interaction_candidates_processable():
    text = " ".join(_TEN_WORDS)
    proposals = tuple(
        InteractionCandidateProposal(
            candidate=InteractionSpanCandidate(exact_source_span=word),
            proposal=InteractionOccurrenceProposal(
                signal=InteractionSignal.JUST_TALK,
                applicability=InteractionApplicability.NON_CURRENT_CONTEXT,
                state=InteractionOccurrenceState.ACTIVE))
        for word in _TEN_WORDS)
    assert len(proposals) == MAX_INTERACTION_CANDIDATES_PER_TURN
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=(), interaction_candidates=proposals, intent_proposal=None)
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    ia = result.analysis.interaction
    assert ia.status is AnalysisComponentStatus.VALIDATED
    assert len(ia.occurrences) == MAX_INTERACTION_CANDIDATES_PER_TURN


def test_over_max_interaction_candidates_entire_component_unavailable():
    words = _TEN_WORDS + ["uniform"]
    text = " ".join(words)
    proposals = tuple(
        InteractionCandidateProposal(
            candidate=InteractionSpanCandidate(exact_source_span=word),
            proposal=InteractionOccurrenceProposal(
                signal=InteractionSignal.JUST_TALK,
                applicability=InteractionApplicability.NON_CURRENT_CONTEXT,
                state=InteractionOccurrenceState.ACTIVE))
        for word in words)
    assert len(proposals) == MAX_INTERACTION_CANDIDATES_PER_TURN + 1
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=(), interaction_candidates=proposals, intent_proposal=None)
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    ia = result.analysis.interaction
    assert ia.status is AnalysisComponentStatus.UNAVAILABLE
    assert ia.occurrences == ()


def test_over_max_interaction_candidates_never_calls_locator(monkeypatch):
    import professional_turn_producer as producer_module
    calls = []
    monkeypatch.setattr(
        producer_module, "locate_interaction_candidate",
        lambda *a, **k: calls.append((a, k)))
    words = _TEN_WORDS + ["uniform"]
    text = " ".join(words)
    proposals = tuple(
        InteractionCandidateProposal(
            candidate=InteractionSpanCandidate(exact_source_span=word),
            proposal=None)
        for word in words)
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=(), interaction_candidates=proposals, intent_proposal=None)
    produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    assert calls == []


# ── InteractionRequest projection (items 29-30) ──────────────────────────

def test_interaction_request_projection_current_directive_active_only():
    text = "alpha bravo charlie delta"
    def _make(word, applicability, state):
        return InteractionCandidateProposal(
            candidate=InteractionSpanCandidate(exact_source_span=word),
            proposal=InteractionOccurrenceProposal(
                signal=InteractionSignal.NO_ADVICE, applicability=applicability, state=state))
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=(),
        interaction_candidates=(
            _make("alpha", InteractionApplicability.CURRENT_DIRECTIVE,
                  InteractionOccurrenceState.ACTIVE),
            _make("bravo", InteractionApplicability.CURRENT_DIRECTIVE,
                  InteractionOccurrenceState.RETRACTED),
            _make("charlie", InteractionApplicability.NON_CURRENT_CONTEXT,
                  InteractionOccurrenceState.ACTIVE),
            _make("delta", InteractionApplicability.NON_CURRENT_CONTEXT,
                  InteractionOccurrenceState.RETRACTED)),
        intent_proposal=None)
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    ia = result.analysis.interaction
    assert ia.status is AnalysisComponentStatus.VALIDATED
    assert len(ia.occurrences) == 4
    assert ia.request.signals == frozenset({InteractionSignal.NO_ADVICE})


def test_conflicting_active_signals_preserved_in_request():
    text = "alpha bravo"
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=(),
        interaction_candidates=(
            InteractionCandidateProposal(
                candidate=InteractionSpanCandidate(exact_source_span="alpha"),
                proposal=InteractionOccurrenceProposal(
                    signal=InteractionSignal.NO_ADVICE,
                    applicability=InteractionApplicability.CURRENT_DIRECTIVE,
                    state=InteractionOccurrenceState.ACTIVE)),
            InteractionCandidateProposal(
                candidate=InteractionSpanCandidate(exact_source_span="bravo"),
                proposal=InteractionOccurrenceProposal(
                    signal=InteractionSignal.ADVICE_REQUESTED,
                    applicability=InteractionApplicability.CURRENT_DIRECTIVE,
                    state=InteractionOccurrenceState.ACTIVE))),
        intent_proposal=None)
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    ia = result.analysis.interaction
    assert ia.status is AnalysisComponentStatus.VALIDATED
    assert ia.request.signals == frozenset(
        {InteractionSignal.NO_ADVICE, InteractionSignal.ADVICE_REQUESTED})


# ── Component isolation (items 31-33) ────────────────────────────────────

def test_evidence_defect_does_not_downgrade_interaction_or_intent():
    text = "alpha bravo"
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=(EvidenceCandidateProposal(
            candidate=EvidenceSpanCandidate(exact_source_span="nonexistent"),
            proposed_kind=EvidenceKind.USER_REPORTED_FACT),),
        interaction_candidates=(InteractionCandidateProposal(
            candidate=InteractionSpanCandidate(exact_source_span="alpha"),
            proposal=InteractionOccurrenceProposal(
                signal=InteractionSignal.JUST_TALK,
                applicability=InteractionApplicability.CURRENT_DIRECTIVE,
                state=InteractionOccurrenceState.ACTIVE)),),
        intent_proposal=Intent.VENT)
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    assert result.analysis.evidence.status is AnalysisComponentStatus.UNAVAILABLE
    assert result.analysis.interaction.status is AnalysisComponentStatus.VALIDATED
    assert result.analysis.intent.status is AnalysisComponentStatus.VALIDATED


def test_interaction_defect_does_not_downgrade_evidence_or_intent():
    text = "alpha bravo"
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=(EvidenceCandidateProposal(
            candidate=EvidenceSpanCandidate(exact_source_span="alpha"),
            proposed_kind=EvidenceKind.USER_REPORTED_FACT),),
        interaction_candidates=(InteractionCandidateProposal(
            candidate=InteractionSpanCandidate(exact_source_span="nonexistent"),
            proposal=None),),
        intent_proposal=Intent.VENT)
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    assert result.analysis.evidence.status is AnalysisComponentStatus.VALIDATED
    assert result.analysis.interaction.status is AnalysisComponentStatus.UNAVAILABLE
    assert result.analysis.intent.status is AnalysisComponentStatus.VALIDATED


def test_malformed_intent_does_not_downgrade_evidence_or_interaction():
    text = "alpha bravo"
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=(EvidenceCandidateProposal(
            candidate=EvidenceSpanCandidate(exact_source_span="alpha"),
            proposed_kind=EvidenceKind.USER_REPORTED_FACT),),
        interaction_candidates=(InteractionCandidateProposal(
            candidate=InteractionSpanCandidate(exact_source_span="bravo"),
            proposal=InteractionOccurrenceProposal(
                signal=InteractionSignal.JUST_TALK,
                applicability=InteractionApplicability.CURRENT_DIRECTIVE,
                state=InteractionOccurrenceState.ACTIVE)),),
        intent_proposal="GARBAGE")
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    assert result.analysis.evidence.status is AnalysisComponentStatus.VALIDATED
    assert result.analysis.interaction.status is AnalysisComponentStatus.VALIDATED
    assert result.analysis.intent.status is AnalysisComponentStatus.UNAVAILABLE


# ── Turn-level result (items 34-37) ──────────────────────────────────────

def test_all_three_components_unavailable_yields_failed():
    text = "alpha bravo"
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=(EvidenceCandidateProposal(
            candidate=EvidenceSpanCandidate(exact_source_span="nonexistent"),
            proposed_kind=EvidenceKind.USER_REPORTED_FACT),),
        interaction_candidates=(InteractionCandidateProposal(
            candidate=InteractionSpanCandidate(exact_source_span="nonexistent"),
            proposal=None),),
        intent_proposal="NOT_REAL")
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    assert result.analysis is None
    assert result.status is TurnAnalysisStatus.FAILED


def test_mixed_component_state_yields_partial():
    text = "alpha bravo"
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=(EvidenceCandidateProposal(
            candidate=EvidenceSpanCandidate(exact_source_span="alpha"),
            proposed_kind=EvidenceKind.USER_REPORTED_FACT),),
        interaction_candidates=(InteractionCandidateProposal(
            candidate=InteractionSpanCandidate(exact_source_span="nonexistent"),
            proposal=None),),
        intent_proposal=Intent.VENT)
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    assert result.status is TurnAnalysisStatus.PARTIAL
    assert result.analysis.evidence.status is AnalysisComponentStatus.VALIDATED
    assert result.analysis.interaction.status is AnalysisComponentStatus.UNAVAILABLE
    assert result.analysis.intent.status is AnalysisComponentStatus.VALIDATED


def test_fully_validated_state_yields_ok():
    text = "alpha bravo"
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=(EvidenceCandidateProposal(
            candidate=EvidenceSpanCandidate(exact_source_span="alpha"),
            proposed_kind=EvidenceKind.USER_REPORTED_FACT),),
        interaction_candidates=(InteractionCandidateProposal(
            candidate=InteractionSpanCandidate(exact_source_span="bravo"),
            proposal=InteractionOccurrenceProposal(
                signal=InteractionSignal.JUST_TALK,
                applicability=InteractionApplicability.CURRENT_DIRECTIVE,
                state=InteractionOccurrenceState.ACTIVE)),),
        intent_proposal=Intent.VENT)
    result = produce_turn_analysis(source_message_row_id=1, source_text=text, analyzer_output=raw)
    assert result.status is TurnAnalysisStatus.OK


def test_unexpected_turn_analysis_error_propagates(monkeypatch):
    """Proves there is no broad try/except around TurnAnalysis construction:
    if there were, this would come back as FAILED instead of raising."""
    import professional_turn_producer as producer_module

    def _boom(*args, **kwargs):
        raise ValueError("simulated invariant failure")

    monkeypatch.setattr(producer_module, "TurnAnalysis", _boom)
    raw = UntrustedTurnAnalyzerOutput(
        evidence_candidates=(EvidenceCandidateProposal(
            candidate=EvidenceSpanCandidate(exact_source_span="alpha"),
            proposed_kind=EvidenceKind.USER_REPORTED_FACT),),
        interaction_candidates=(), intent_proposal=None)
    with pytest.raises(ValueError):
        produce_turn_analysis(source_message_row_id=1, source_text="alpha", analyzer_output=raw)
