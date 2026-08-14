"""Behavioral characterization tests for the Professional Core V2 Stage 2A
evidence proposal canonicalization boundary (Slice 2A-5) in
professional_turn_analysis.py: canonicalize_evidence_proposal.

These tests exercise ONLY the public canonicalize_evidence_proposal
function -- no private helper (_require_source_text) is imported or called
directly. They prove structural/provenance canonicalization behavior only:
exact raw enum parsing, exact raw source-slice provenance revalidation
(including its case-sensitivity and no-Unicode-normalization guarantees),
the frozen validation order (structural/provenance checks always run
before an explicit proposed_kind=None abstention is ever honored), and
output field preservation. This function performs NO semantic
classification -- it never inspects exact_source_span's words to decide a
kind, and no analyzer/keyword/regex logic is implemented anywhere in this
module. No test in this file asserts that any particular natural-language
phrase "should" receive a particular EvidenceKind.
"""
import pytest

from professional_turn_analysis import (
    LocatedEvidenceSpan,
    LocatedInteractionSpan,
    canonicalize_evidence_proposal,
)
from therapeutic_domain import EvidenceItem, EvidenceKind

_SOURCE_TEXT = "hello world"


def _located(row_id=1, start=6, end=11, text="world"):
    return LocatedEvidenceSpan(
        source_message_row_id=row_id, span_start=start, span_end=end, exact_source_span=text)


# -- Valid canonicalization: all 7 members, both member and raw-string form

@pytest.mark.parametrize("kind", list(EvidenceKind))
def test_canonicalize_evidence_proposal_accepts_every_kind_member(kind):
    located = _located()
    result = canonicalize_evidence_proposal(
        located, source_text=_SOURCE_TEXT, proposed_kind=kind)
    assert isinstance(result, EvidenceItem)
    assert result.ref.source_message_row_id == 1
    assert result.ref.span_start == 6
    assert result.ref.span_end == 11
    assert result.ref.evidence_kind is kind
    assert result.exact_source_span == "world"


@pytest.mark.parametrize("kind", list(EvidenceKind))
def test_canonicalize_evidence_proposal_accepts_every_kind_raw_value(kind):
    located = _located()
    result = canonicalize_evidence_proposal(
        located, source_text=_SOURCE_TEXT, proposed_kind=kind.value)
    assert isinstance(result, EvidenceItem)
    assert result.ref.evidence_kind is kind


# -- Explicit abstention --------------------------------------------------

def test_canonicalize_evidence_proposal_none_returns_none():
    located = _located()
    result = canonicalize_evidence_proposal(
        located, source_text=_SOURCE_TEXT, proposed_kind=None)
    assert result is None


# -- Abstention must not mask malformed wiring/provenance -----------------

def test_canonicalize_evidence_proposal_none_does_not_mask_wrong_located_type():
    wrong = LocatedInteractionSpan(
        source_message_row_id=1, span_start=6, span_end=11, exact_source_span="world")
    with pytest.raises(ValueError):
        canonicalize_evidence_proposal(
            wrong, source_text=_SOURCE_TEXT, proposed_kind=None)


@pytest.mark.parametrize("bad_source_text", ["", "   ", 12345])
def test_canonicalize_evidence_proposal_none_does_not_mask_malformed_source_text(bad_source_text):
    located = _located()
    with pytest.raises(ValueError):
        canonicalize_evidence_proposal(
            located, source_text=bad_source_text, proposed_kind=None)


def test_canonicalize_evidence_proposal_none_does_not_mask_provenance_mismatch():
    """located is independently valid; source_text is well within bounds
    but differs at the located span -- isolates the failure to the raw
    slice mismatch, not ambiguity or bounds."""
    located = _located(start=0, end=5, text="hello")
    mismatched_source = "xxxxx world"
    with pytest.raises(ValueError):
        canonicalize_evidence_proposal(
            located, source_text=mismatched_source, proposed_kind=None)


def test_canonicalize_evidence_proposal_none_does_not_mask_out_of_bounds():
    """Proves None does not bypass provenance validation when source_text
    is too short to even contain the located span. NOTE: for a valid
    LocatedEvidenceSpan, out-of-bounds necessarily also implies the raw
    slice cannot match (len(exact_source_span) == span_end - span_start),
    so this does not independently prove the explicit bounds branch in
    isolation from the slice-equality check -- that ordering is a
    code/diff-audit invariant, not something this behavioral test alone
    can distinguish."""
    located = _located(start=6, end=11, text="world")
    with pytest.raises(ValueError):
        canonicalize_evidence_proposal(
            located, source_text="hi", proposed_kind=None)


# -- Invalid raw label: no alias/casefold/strip/coercion before as_enum --

@pytest.mark.parametrize("bad_kind", [
    "NOT_A_REAL_EVIDENCE_KIND",
    "user_reported_fact",
    " USER_REPORTED_FACT ",
    12345,
])
def test_canonicalize_evidence_proposal_invalid_proposed_kind_rejected(bad_kind):
    located = _located()
    with pytest.raises(ValueError):
        canonicalize_evidence_proposal(
            located, source_text=_SOURCE_TEXT, proposed_kind=bad_kind)


# -- Provenance revalidation on the ordinary (non-abstention) path -------

def test_canonicalize_evidence_proposal_valid_kind_provenance_mismatch_rejected():
    """Same-length source slice differs by one raw character -- proves
    provenance revalidation applies on the ordinary non-abstention path
    too, not only when proposed_kind is None."""
    located = _located(start=0, end=5, text="hello")
    mismatched_source = "hellp world"
    with pytest.raises(ValueError):
        canonicalize_evidence_proposal(
            located, source_text=mismatched_source,
            proposed_kind=EvidenceKind.USER_REPORTED_FACT)


def test_canonicalize_evidence_proposal_case_sensitive_provenance_mismatch_rejected():
    located = _located(start=0, end=3, text="Foo")
    with pytest.raises(ValueError):
        canonicalize_evidence_proposal(
            located, source_text="foo bar",
            proposed_kind=EvidenceKind.USER_REPORTED_FACT)


def test_canonicalize_evidence_proposal_no_unicode_normalization():
    """located.exact_source_span is U+2126 OHM SIGN (1 code point);
    source_text supplies the canonically-equivalent but code-point-distinct
    U+03A9 GREEK CAPITAL LETTER OMEGA at the same offset. NFC-normalizing
    either side before comparison would wrongly consider these equal; raw
    comparison must reject this as a provenance mismatch."""
    located = _located(start=0, end=1, text="Ω")
    source_text = "Ω world"
    with pytest.raises(ValueError):
        canonicalize_evidence_proposal(
            located, source_text=source_text,
            proposed_kind=EvidenceKind.USER_REPORTED_FACT)


# -- Raw source preservation / no cap -------------------------------------

def test_canonicalize_evidence_proposal_source_text_not_stripped_offsets_preserved():
    located = LocatedEvidenceSpan(
        source_message_row_id=1, span_start=2, span_end=5, exact_source_span="foo")
    result = canonicalize_evidence_proposal(
        located, source_text="  foo  ", proposed_kind=EvidenceKind.USER_REPORTED_FACT)
    assert result.ref.source_message_row_id == 1
    assert result.ref.span_start == 2
    assert result.ref.span_end == 5
    assert result.exact_source_span == "foo"


def test_canonicalize_evidence_proposal_source_text_no_arbitrary_cap():
    """source_text is comfortably larger than every Stage 2A char cap
    (300/150) -- catches accidental reuse of _require_bounded_text against
    source_text itself."""
    source_text = "x" * 1200 + "value"
    located = LocatedEvidenceSpan(
        source_message_row_id=1, span_start=1200, span_end=1205, exact_source_span="value")
    result = canonicalize_evidence_proposal(
        located, source_text=source_text, proposed_kind=EvidenceKind.USER_REPORTED_FACT)
    assert isinstance(result, EvidenceItem)
    assert result.ref.span_start == 1200
    assert result.ref.span_end == 1205
    assert result.exact_source_span == "value"


# -- Output observability --------------------------------------------------

def test_canonicalize_evidence_proposal_output_matches_source_slice_and_located_text():
    """Observable-only invariant: does not (and cannot, black-box) prove
    whether exact_source_span was populated from source_text or from
    located.exact_source_span -- under a successful call the two are
    always equal, so only their equality is asserted here. Construction
    origin is a diff-audit concern."""
    located = _located()
    result = canonicalize_evidence_proposal(
        located, source_text=_SOURCE_TEXT, proposed_kind=EvidenceKind.USER_REPORTED_FACT)
    assert result.exact_source_span == _SOURCE_TEXT[result.ref.span_start:result.ref.span_end]
    assert result.exact_source_span == located.exact_source_span
