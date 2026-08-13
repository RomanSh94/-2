"""Behavioral characterization tests for the Professional Core V2 Stage 2A
deterministic exact candidate locator (Slice 2A-4) in
professional_turn_analysis.py: locate_evidence_candidate,
locate_interaction_candidate.

These tests exercise ONLY the two public locator functions -- no private
helper (_require_source_text, _locate_unique_exact_span) is imported or
called directly. They prove exact raw Python code-point matching,
overlap-aware ambiguity detection, exact immediate-adjacency context
filtering (including its positive boundary-fit cases), the absence of any
normalization (strip/casefold/Unicode), malformed-input rejection for both
public APIs, and the absence of any arbitrary source_text length cap. They
do NOT test semantic labeling, candidate harvesting, or orchestration --
none of that is implemented in this slice. No EvidenceKind/InteractionSignal
value is referenced anywhere in this file.
"""
import pytest

from professional_turn_analysis import (
    EvidenceSpanCandidate,
    InteractionSpanCandidate,
    LocatedEvidenceSpan,
    LocatedInteractionSpan,
    locate_evidence_candidate,
    locate_interaction_candidate,
)

_WRAPPER_CANDIDATE_PAIRS = [
    (locate_evidence_candidate, EvidenceSpanCandidate),
    (locate_interaction_candidate, InteractionSpanCandidate),
]

_WRAPPER_CANDIDATE_LOCATED_TRIPLES = [
    (locate_evidence_candidate, EvidenceSpanCandidate, LocatedEvidenceSpan),
    (locate_interaction_candidate, InteractionSpanCandidate, LocatedInteractionSpan),
]


# ── Group 1: shared malformed-input contract (both public wrappers) ─────

@pytest.mark.parametrize("locate_fn,candidate_cls", _WRAPPER_CANDIDATE_PAIRS)
@pytest.mark.parametrize("bad_row_id", [0, -1, True, "1"])
def test_locate_candidate_bad_row_id_rejected(locate_fn, candidate_cls, bad_row_id):
    candidate = candidate_cls(exact_source_span="hello")
    with pytest.raises(ValueError):
        locate_fn(candidate, source_message_row_id=bad_row_id, source_text="hello world")


@pytest.mark.parametrize("locate_fn,candidate_cls", _WRAPPER_CANDIDATE_PAIRS)
@pytest.mark.parametrize("bad_source_text", ["", "   ", 12345])
def test_locate_candidate_bad_source_text_rejected(locate_fn, candidate_cls, bad_source_text):
    candidate = candidate_cls(exact_source_span="hello")
    with pytest.raises(ValueError):
        locate_fn(candidate, source_message_row_id=1, source_text=bad_source_text)


def test_locate_evidence_candidate_wrong_sibling_type_rejected():
    """Uses the actual sibling dataclass (InteractionSpanCandidate), not a
    string/dict, to prove locate_evidence_candidate does not accidentally
    duck-type two structurally similar candidate types."""
    candidate = InteractionSpanCandidate(exact_source_span="hello")
    with pytest.raises(ValueError):
        locate_evidence_candidate(candidate, source_message_row_id=1, source_text="hello world")


def test_locate_interaction_candidate_wrong_sibling_type_rejected():
    candidate = EvidenceSpanCandidate(exact_source_span="hello")
    with pytest.raises(ValueError):
        locate_interaction_candidate(candidate, source_message_row_id=1, source_text="hello world")


@pytest.mark.parametrize("locate_fn,candidate_cls,located_cls", _WRAPPER_CANDIDATE_LOCATED_TRIPLES)
def test_locate_candidate_source_text_no_arbitrary_cap(locate_fn, candidate_cls, located_cls):
    """source_text is comfortably larger than every Stage 2A char cap
    (300/150) -- catches accidental reuse of _require_bounded_text against
    source_text itself."""
    source_text = "x" * 1000 + "unique_target_phrase"
    candidate = candidate_cls(exact_source_span="unique_target_phrase")
    result = locate_fn(candidate, source_message_row_id=1, source_text=source_text)
    assert isinstance(result, located_cls)
    assert result.span_start == 1000
    assert result.span_end == len(source_text)
    assert result.exact_source_span == "unique_target_phrase"


# ── Group 2: evidence-side core exact-match/context/overlap behavior ────

def test_locate_evidence_candidate_unique_exact_match_located():
    candidate = EvidenceSpanCandidate(exact_source_span="world")
    result = locate_evidence_candidate(
        candidate, source_message_row_id=1, source_text="hello world")
    assert result == LocatedEvidenceSpan(
        source_message_row_id=1, span_start=6, span_end=11, exact_source_span="world")


def test_locate_evidence_candidate_no_match_returns_none():
    candidate = EvidenceSpanCandidate(exact_source_span="xyz")
    result = locate_evidence_candidate(
        candidate, source_message_row_id=1, source_text="hello world")
    assert result is None


def test_locate_evidence_candidate_duplicate_exact_match_returns_none():
    candidate = EvidenceSpanCandidate(exact_source_span="foo")
    result = locate_evidence_candidate(
        candidate, source_message_row_id=1, source_text="foo bar foo")
    assert result is None


def test_locate_evidence_candidate_overlapping_ambiguous_match_returns_none():
    """source_text="ababa"/candidate="aba" has two overlapping raw
    occurrences ([0:3] and [2:5]) that a naive advance-by-len(candidate)
    scan would miss entirely -- this only passes under a genuinely
    overlap-aware search."""
    candidate = EvidenceSpanCandidate(exact_source_span="aba")
    result = locate_evidence_candidate(
        candidate, source_message_row_id=1, source_text="ababa")
    assert result is None


def test_locate_evidence_candidate_context_before_disambiguates_uniquely():
    """"tired" occurs at [8:13] ("morning tired...") and [23:28]
    ("...evening tired") -- context_before="evening " matches only the
    text immediately before the second occurrence."""
    candidate = EvidenceSpanCandidate(exact_source_span="tired", context_before="evening ")
    result = locate_evidence_candidate(
        candidate, source_message_row_id=1, source_text="morning tired, evening tired")
    assert result == LocatedEvidenceSpan(
        source_message_row_id=1, span_start=23, span_end=28, exact_source_span="tired")


def test_locate_evidence_candidate_context_after_disambiguates_uniquely():
    """"tired" occurs at [0:5] and [15:20] -- context_after=" later"
    matches only the text immediately after the second occurrence."""
    candidate = EvidenceSpanCandidate(exact_source_span="tired", context_after=" later")
    result = locate_evidence_candidate(
        candidate, source_message_row_id=1, source_text="tired now, but tired later")
    assert result == LocatedEvidenceSpan(
        source_message_row_id=1, span_start=15, span_end=20, exact_source_span="tired")


def test_locate_evidence_candidate_both_contexts_required_for_conjunction():
    """source_text="AfooC AfooD BfooC" has three raw "foo" occurrences:
    [1:4], [7:10], [13:16]. context_before="A" ALONE survives at [1:4]
    and [7:10] (2 -> None). context_after="C" ALONE survives at [1:4] and
    [13:16] (2 -> None). Only supplying BOTH narrows to exactly [1:4].
    All three calls are asserted so neither constraint alone is proven
    sufficient."""
    source_text = "AfooC AfooD BfooC"

    before_only = EvidenceSpanCandidate(exact_source_span="foo", context_before="A")
    assert locate_evidence_candidate(
        before_only, source_message_row_id=1, source_text=source_text) is None

    after_only = EvidenceSpanCandidate(exact_source_span="foo", context_after="C")
    assert locate_evidence_candidate(
        after_only, source_message_row_id=1, source_text=source_text) is None

    both = EvidenceSpanCandidate(exact_source_span="foo", context_before="A", context_after="C")
    result = locate_evidence_candidate(both, source_message_row_id=1, source_text=source_text)
    assert result == LocatedEvidenceSpan(
        source_message_row_id=1, span_start=1, span_end=4, exact_source_span="foo")


def test_locate_evidence_candidate_context_still_ambiguous_returns_none():
    """Both occurrences of "tired" are identically preceded by
    "context " -- the supplied context matches both, so it does not
    disambiguate anything and the result remains None."""
    candidate = EvidenceSpanCandidate(exact_source_span="tired", context_before="context ")
    result = locate_evidence_candidate(
        candidate, source_message_row_id=1, source_text="context tired context tired")
    assert result is None


def test_locate_evidence_candidate_wrong_context_before_case_mismatch_returns_none():
    """"tired" occurs exactly once (no ambiguity); the supplied
    context_before differs from the actual preceding text ONLY in case
    ("before " vs "Before "), proving context comparison is case-exact,
    not casefolded."""
    candidate = EvidenceSpanCandidate(exact_source_span="tired", context_before="before ")
    result = locate_evidence_candidate(
        candidate, source_message_row_id=1, source_text="Before tired")
    assert result is None


def test_locate_evidence_candidate_wrong_context_after_case_mismatch_returns_none():
    """"tired" occurs exactly once; supplied context_after differs from
    the actual following text ONLY in case ("later" vs "Later")."""
    candidate = EvidenceSpanCandidate(exact_source_span="tired", context_after=" later")
    result = locate_evidence_candidate(
        candidate, source_message_row_id=1, source_text="tired Later")
    assert result is None


def test_locate_evidence_candidate_context_before_underflow_returns_none():
    """"tired" occurs exactly once at source position 0 -- context_before
    cannot possibly fit before it, isolating the failure to the boundary
    constraint with no ambiguity involved."""
    candidate = EvidenceSpanCandidate(exact_source_span="tired", context_before="XX")
    result = locate_evidence_candidate(
        candidate, source_message_row_id=1, source_text="tired")
    assert result is None


def test_locate_evidence_candidate_context_after_overflow_returns_none():
    """"tired" occurs exactly once and ends exactly at source_text's end
    -- context_after cannot possibly fit after it."""
    candidate = EvidenceSpanCandidate(exact_source_span="tired", context_after="XX")
    result = locate_evidence_candidate(
        candidate, source_message_row_id=1, source_text="tired")
    assert result is None


def test_locate_evidence_candidate_context_before_exactly_fits_source_start():
    """context_before="A" (1 char) immediately precedes "foo" starting at
    index 1 -- proves the boundary check uses start >= len(context_before),
    not the stricter start > len(context_before)."""
    candidate = EvidenceSpanCandidate(exact_source_span="foo", context_before="A")
    result = locate_evidence_candidate(
        candidate, source_message_row_id=1, source_text="Afoo")
    assert result == LocatedEvidenceSpan(
        source_message_row_id=1, span_start=1, span_end=4, exact_source_span="foo")


def test_locate_evidence_candidate_context_after_exactly_fits_source_end():
    """context_after="Z" (1 char) immediately follows "foo" ending exactly
    at source_text's length -- proves the boundary check uses
    end + len(context_after) <= len(source_text), not the stricter <."""
    candidate = EvidenceSpanCandidate(exact_source_span="foo", context_after="Z")
    result = locate_evidence_candidate(
        candidate, source_message_row_id=1, source_text="fooZ")
    assert result == LocatedEvidenceSpan(
        source_message_row_id=1, span_start=0, span_end=3, exact_source_span="foo")


def test_locate_evidence_candidate_whole_message_match_located():
    candidate = EvidenceSpanCandidate(exact_source_span="hello")
    result = locate_evidence_candidate(
        candidate, source_message_row_id=1, source_text="hello")
    assert result == LocatedEvidenceSpan(
        source_message_row_id=1, span_start=0, span_end=5, exact_source_span="hello")


def test_locate_evidence_candidate_match_at_source_start_located():
    candidate = EvidenceSpanCandidate(exact_source_span="foo")
    result = locate_evidence_candidate(
        candidate, source_message_row_id=1, source_text="foo bar")
    assert result == LocatedEvidenceSpan(
        source_message_row_id=1, span_start=0, span_end=3, exact_source_span="foo")


def test_locate_evidence_candidate_match_at_source_end_located():
    candidate = EvidenceSpanCandidate(exact_source_span="foo")
    result = locate_evidence_candidate(
        candidate, source_message_row_id=1, source_text="bar foo")
    assert result == LocatedEvidenceSpan(
        source_message_row_id=1, span_start=4, span_end=7, exact_source_span="foo")


def test_locate_evidence_candidate_non_bmp_unicode_offsets_correct():
    """U+1F600 sits outside the Basic Multilingual Plane; a UTF-16-based
    (rather than Python code-point based) implementation would misalign
    this offset."""
    candidate = EvidenceSpanCandidate(exact_source_span="\U0001F600")
    result = locate_evidence_candidate(
        candidate, source_message_row_id=1, source_text="hi \U0001F600 there")
    assert result == LocatedEvidenceSpan(
        source_message_row_id=1, span_start=3, span_end=4, exact_source_span="\U0001F600")


def test_locate_evidence_candidate_output_span_matches_source_slice_and_candidate_text():
    """Observable-only invariant: does NOT (and cannot, black-box) prove
    whether the implementation constructs exact_source_span from
    source_text or from candidate.exact_source_span -- under a successful
    match the two are always equal, so only their equality is asserted
    here. Construction origin is an implementation/diff-audit concern."""
    candidate = EvidenceSpanCandidate(exact_source_span="world")
    source_text = "hello world"
    result = locate_evidence_candidate(
        candidate, source_message_row_id=1, source_text=source_text)
    assert result.exact_source_span == source_text[result.span_start:result.span_end]
    assert result.exact_source_span == candidate.exact_source_span


def test_locate_evidence_candidate_returns_located_evidence_span_type():
    candidate = EvidenceSpanCandidate(exact_source_span="world")
    result = locate_evidence_candidate(
        candidate, source_message_row_id=1, source_text="hello world")
    assert isinstance(result, LocatedEvidenceSpan)
    assert not isinstance(result, LocatedInteractionSpan)


def test_locate_evidence_candidate_case_sensitive_no_casefold_match():
    candidate = EvidenceSpanCandidate(exact_source_span="Hello")
    result = locate_evidence_candidate(
        candidate, source_message_row_id=1, source_text="hello world")
    assert result is None


def test_locate_evidence_candidate_no_strip_of_candidate_text():
    """candidate text carries deliberate leading/trailing whitespace (not
    whitespace-only, so construction succeeds); source_text has none.
    A implementation that strips candidate text before searching would
    wrongly find "foo" -- this must remain None."""
    candidate = EvidenceSpanCandidate(exact_source_span=" foo ")
    result = locate_evidence_candidate(
        candidate, source_message_row_id=1, source_text="foo")
    assert result is None


def test_locate_evidence_candidate_source_text_not_stripped_offsets_preserved():
    """source_text carries leading whitespace; an implementation that
    strips source_text before searching would silently shift every
    offset. Offsets must reflect the ORIGINAL, unstripped source_text."""
    candidate = EvidenceSpanCandidate(exact_source_span="foo")
    result = locate_evidence_candidate(
        candidate, source_message_row_id=1, source_text="  foo")
    assert result == LocatedEvidenceSpan(
        source_message_row_id=1, span_start=2, span_end=5, exact_source_span="foo")


def test_locate_evidence_candidate_no_unicode_normalization():
    """"é" (precomposed e-acute, 1 code point) and "é" (bare e
    + combining acute, 2 code points) are canonically NFC-equivalent but
    code-point-distinct. An NFC/NFD-normalizing implementation would
    wrongly find a match; raw code-point search must not."""
    candidate = EvidenceSpanCandidate(exact_source_span="é")
    result = locate_evidence_candidate(
        candidate, source_message_row_id=1, source_text="é")
    assert result is None


# ── Group 3: interaction-side shared-core confirmation (not a duplicate
#    sweep -- Group 2 already proves the shared search/context/overlap
#    core; these confirm the same core executes correctly through the
#    interaction wrapper and returns the correct sibling type) ──────────

def test_locate_interaction_candidate_unique_exact_match_located():
    candidate = InteractionSpanCandidate(exact_source_span="advice")
    result = locate_interaction_candidate(
        candidate, source_message_row_id=1, source_text="no advice needed")
    assert result == LocatedInteractionSpan(
        source_message_row_id=1, span_start=3, span_end=9, exact_source_span="advice")


def test_locate_interaction_candidate_no_match_returns_none():
    candidate = InteractionSpanCandidate(exact_source_span="xyz")
    result = locate_interaction_candidate(
        candidate, source_message_row_id=1, source_text="no advice needed")
    assert result is None


def test_locate_interaction_candidate_context_before_disambiguates_uniquely():
    """"advice" occurs at [6:12] ("early advice...") and [20:26]
    ("...later advice") -- context_before="later " matches only the text
    immediately before the second occurrence."""
    candidate = InteractionSpanCandidate(exact_source_span="advice", context_before="later ")
    result = locate_interaction_candidate(
        candidate, source_message_row_id=1, source_text="early advice, later advice")
    assert result == LocatedInteractionSpan(
        source_message_row_id=1, span_start=20, span_end=26, exact_source_span="advice")


def test_locate_interaction_candidate_overlapping_ambiguous_match_returns_none():
    candidate = InteractionSpanCandidate(exact_source_span="aba")
    result = locate_interaction_candidate(
        candidate, source_message_row_id=1, source_text="ababa")
    assert result is None


def test_locate_interaction_candidate_returns_located_interaction_span_type():
    candidate = InteractionSpanCandidate(exact_source_span="advice")
    result = locate_interaction_candidate(
        candidate, source_message_row_id=1, source_text="no advice needed")
    assert isinstance(result, LocatedInteractionSpan)
    assert not isinstance(result, LocatedEvidenceSpan)


def test_locate_interaction_candidate_output_span_matches_source_slice_and_candidate_text():
    candidate = InteractionSpanCandidate(exact_source_span="advice")
    source_text = "no advice needed"
    result = locate_interaction_candidate(
        candidate, source_message_row_id=1, source_text=source_text)
    assert result.exact_source_span == source_text[result.span_start:result.span_end]
    assert result.exact_source_span == candidate.exact_source_span
