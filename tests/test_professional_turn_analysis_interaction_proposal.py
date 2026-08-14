"""Behavioral characterization tests for the Professional Core V2 Stage 2A
interaction proposal canonicalization boundary (Slice 2A-6) in
professional_turn_analysis.py: InteractionOccurrenceProposal,
canonicalize_interaction_proposal.

These tests exercise ONLY the public InteractionOccurrenceProposal type and
canonicalize_interaction_proposal function -- no private helper
(_require_source_text) is imported or called directly. They prove:
InteractionOccurrenceProposal is a genuinely untrusted, unvalidated
transport container (construction never raises, even for malformed runtime
values); the frozen validation order (structural/provenance checks always
run before proposal is ever inspected, and proposal's own type is checked
before any of its three fields is parsed); exact raw enum parsing per axis
via as_enum with no preprocessing; exact raw source-slice provenance
revalidation (including case-sensitivity and no-Unicode-normalization);
and output field preservation. This function performs NO semantic
classification -- it never inspects exact_source_span's words to decide a
signal, applicability, or state, and no analyzer/keyword/regex/retraction
logic is implemented anywhere in this module. No test in this file asserts
that any particular natural-language phrase "should" produce a particular
signal/applicability/state.
"""
import dataclasses

import pytest

from professional_turn_analysis import (
    InteractionApplicability,
    InteractionOccurrenceProposal,
    InteractionOccurrenceState,
    InteractionSignalOccurrence,
    LocatedEvidenceSpan,
    LocatedInteractionSpan,
    canonicalize_interaction_proposal,
)
from therapeutic_domain import InteractionSignal

_SOURCE_TEXT = "no advice needed"


def _located(row_id=1, start=3, end=9, text="advice"):
    return LocatedInteractionSpan(
        source_message_row_id=row_id, span_start=start, span_end=end, exact_source_span=text)


def _valid_proposal(**overrides):
    kwargs = dict(
        signal=InteractionSignal.NO_ADVICE,
        applicability=InteractionApplicability.CURRENT_DIRECTIVE,
        state=InteractionOccurrenceState.ACTIVE)
    kwargs.update(overrides)
    return InteractionOccurrenceProposal(**kwargs)


# ── InteractionOccurrenceProposal: untrusted transport container ───────

def test_interaction_occurrence_proposal_is_frozen():
    p = _valid_proposal()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.signal = InteractionSignal.JUST_TALK


def test_interaction_occurrence_proposal_exact_field_boundary():
    fields = tuple(f.name for f in dataclasses.fields(InteractionOccurrenceProposal))
    assert fields == ("signal", "applicability", "state")


def test_interaction_occurrence_proposal_fields_have_no_defaults():
    for f in dataclasses.fields(InteractionOccurrenceProposal):
        assert f.default is dataclasses.MISSING
        assert f.default_factory is dataclasses.MISSING


def test_interaction_occurrence_proposal_has_no_post_init():
    assert "__post_init__" not in InteractionOccurrenceProposal.__dict__


def test_interaction_occurrence_proposal_missing_argument_raises_type_error():
    with pytest.raises(TypeError):
        InteractionOccurrenceProposal(
            signal=InteractionSignal.NO_ADVICE,
            applicability=InteractionApplicability.CURRENT_DIRECTIVE)


def test_interaction_occurrence_proposal_construction_allows_malformed_runtime_values():
    """Proves the transport boundary is genuinely untrusted: even wildly
    invalid runtime values construct successfully, since this type
    performs no validation of its own -- only canonicalize_interaction_
    proposal validates, and only after provenance."""
    p = InteractionOccurrenceProposal(
        signal=None,
        applicability="NOT_A_REAL_APPLICABILITY",
        state=12345)
    assert p.signal is None
    assert p.applicability == "NOT_A_REAL_APPLICABILITY"
    assert p.state == 12345


# ── Full member Cartesian: structural representability only ────────────

@pytest.mark.parametrize("signal", list(InteractionSignal))
@pytest.mark.parametrize("applicability", list(InteractionApplicability))
@pytest.mark.parametrize("state", list(InteractionOccurrenceState))
def test_canonicalize_interaction_proposal_full_member_cartesian(signal, applicability, state):
    located = _located()
    proposal = InteractionOccurrenceProposal(
        signal=signal, applicability=applicability, state=state)
    result = canonicalize_interaction_proposal(
        located, source_text=_SOURCE_TEXT, proposal=proposal)
    assert isinstance(result, InteractionSignalOccurrence)
    assert result.source_message_row_id == 1
    assert result.span_start == 3
    assert result.span_end == 9
    assert result.signal is signal
    assert result.applicability is applicability
    assert result.state is state
    assert result.exact_source_span == "advice"


# ── Raw enum value acceptance: independent per-axis sweeps ─────────────

@pytest.mark.parametrize("signal", list(InteractionSignal))
def test_canonicalize_interaction_proposal_accepts_raw_signal_value(signal):
    located = _located()
    proposal = _valid_proposal(signal=signal.value)
    result = canonicalize_interaction_proposal(
        located, source_text=_SOURCE_TEXT, proposal=proposal)
    assert result.signal is signal


@pytest.mark.parametrize("applicability", list(InteractionApplicability))
def test_canonicalize_interaction_proposal_accepts_raw_applicability_value(applicability):
    located = _located()
    proposal = _valid_proposal(applicability=applicability.value)
    result = canonicalize_interaction_proposal(
        located, source_text=_SOURCE_TEXT, proposal=proposal)
    assert result.applicability is applicability


@pytest.mark.parametrize("state", list(InteractionOccurrenceState))
def test_canonicalize_interaction_proposal_accepts_raw_state_value(state):
    located = _located()
    proposal = _valid_proposal(state=state.value)
    result = canonicalize_interaction_proposal(
        located, source_text=_SOURCE_TEXT, proposal=proposal)
    assert result.state is state


# ── Explicit abstention; must not mask malformed wiring/provenance ─────

def test_canonicalize_interaction_proposal_none_returns_none():
    located = _located()
    result = canonicalize_interaction_proposal(
        located, source_text=_SOURCE_TEXT, proposal=None)
    assert result is None


def test_canonicalize_interaction_proposal_none_does_not_mask_wrong_located_type():
    wrong = LocatedEvidenceSpan(
        source_message_row_id=1, span_start=3, span_end=9, exact_source_span="advice")
    with pytest.raises(ValueError):
        canonicalize_interaction_proposal(
            wrong, source_text=_SOURCE_TEXT, proposal=None)


@pytest.mark.parametrize("bad_source_text", ["", "   ", 12345])
def test_canonicalize_interaction_proposal_none_does_not_mask_malformed_source_text(bad_source_text):
    located = _located()
    with pytest.raises(ValueError):
        canonicalize_interaction_proposal(
            located, source_text=bad_source_text, proposal=None)


def test_canonicalize_interaction_proposal_none_does_not_mask_provenance_mismatch():
    """located is independently valid; source_text is well within bounds
    but differs at the located span -- isolates the failure to the raw
    slice mismatch, not ambiguity or bounds."""
    located = _located(start=0, end=2, text="no")
    mismatched_source = "xx advice needed"
    with pytest.raises(ValueError):
        canonicalize_interaction_proposal(
            located, source_text=mismatched_source, proposal=None)


def test_canonicalize_interaction_proposal_none_does_not_mask_out_of_bounds():
    """Proves None does not bypass provenance validation when source_text
    is too short to even contain the located span. NOTE: for a valid
    LocatedInteractionSpan, out-of-bounds necessarily also implies the raw
    slice cannot match, so this does not independently prove the explicit
    bounds branch in isolation from the slice-equality check -- that
    ordering is a code/diff-audit invariant, not something this
    behavioral test alone can distinguish."""
    located = _located()
    with pytest.raises(ValueError):
        canonicalize_interaction_proposal(
            located, source_text="no", proposal=None)


# ── Wrong proposal container type ───────────────────────────────────────

@pytest.mark.parametrize("bad_proposal", [
    {"signal": "NO_ADVICE", "applicability": "CURRENT_DIRECTIVE", "state": "ACTIVE"},
    ("NO_ADVICE", "CURRENT_DIRECTIVE", "ACTIVE"),
])
def test_canonicalize_interaction_proposal_wrong_container_type_rejected(bad_proposal):
    located = _located()
    with pytest.raises(ValueError):
        canonicalize_interaction_proposal(
            located, source_text=_SOURCE_TEXT, proposal=bad_proposal)


# ── Invalid field matrix: each axis independently, no defaulting ───────

@pytest.mark.parametrize("bad_signal", [
    None,
    "NOT_A_REAL_SIGNAL",
    "no_advice",
    " NO_ADVICE ",
    12345,
])
def test_canonicalize_interaction_proposal_invalid_signal_rejected(bad_signal):
    located = _located()
    proposal = _valid_proposal(signal=bad_signal)
    with pytest.raises(ValueError):
        canonicalize_interaction_proposal(
            located, source_text=_SOURCE_TEXT, proposal=proposal)


@pytest.mark.parametrize("bad_applicability", [
    None,
    "NOT_A_REAL_APPLICABILITY",
    "current_directive",
    " CURRENT_DIRECTIVE ",
    12345,
])
def test_canonicalize_interaction_proposal_invalid_applicability_rejected(bad_applicability):
    located = _located()
    proposal = _valid_proposal(applicability=bad_applicability)
    with pytest.raises(ValueError):
        canonicalize_interaction_proposal(
            located, source_text=_SOURCE_TEXT, proposal=proposal)


@pytest.mark.parametrize("bad_state", [
    None,
    "NOT_A_REAL_STATE",
    "active",
    " ACTIVE ",
    12345,
])
def test_canonicalize_interaction_proposal_invalid_state_rejected(bad_state):
    located = _located()
    proposal = _valid_proposal(state=bad_state)
    with pytest.raises(ValueError):
        canonicalize_interaction_proposal(
            located, source_text=_SOURCE_TEXT, proposal=proposal)


# ── Provenance precedence over an invalid semantic proposal value ──────

def test_canonicalize_interaction_proposal_provenance_precedence_over_invalid_proposal():
    """Stale/mismatched provenance plus an invalid semantic proposal value
    together: the provenance-specific ValueError must fire, proving
    semantic enum parsing is never reached."""
    located = _located(start=0, end=2, text="no")
    mismatched_source = "xx advice needed"
    proposal = _valid_proposal(signal="NOT_A_REAL_SIGNAL")
    with pytest.raises(ValueError, match="does not match source_text"):
        canonicalize_interaction_proposal(
            located, source_text=mismatched_source, proposal=proposal)


# ── Provenance revalidation on the ordinary (non-abstention) path ──────

def test_canonicalize_interaction_proposal_valid_proposal_provenance_mismatch_rejected():
    located = _located(start=0, end=2, text="no")
    mismatched_source = "nx advice needed"
    proposal = _valid_proposal()
    with pytest.raises(ValueError):
        canonicalize_interaction_proposal(
            located, source_text=mismatched_source, proposal=proposal)


def test_canonicalize_interaction_proposal_case_sensitive_provenance_mismatch_rejected():
    located = _located(start=0, end=3, text="Foo")
    proposal = _valid_proposal()
    with pytest.raises(ValueError):
        canonicalize_interaction_proposal(
            located, source_text="foo bar", proposal=proposal)


def test_canonicalize_interaction_proposal_no_unicode_normalization():
    """located.exact_source_span is U+2126 OHM SIGN (1 code point);
    source_text supplies the canonically-equivalent but code-point-distinct
    U+03A9 GREEK CAPITAL LETTER OMEGA at the same offset. NFC-normalizing
    either side before comparison would wrongly consider these equal; raw
    comparison must reject this as a provenance mismatch."""
    located = _located(start=0, end=1, text="\u2126")
    source_text = "\u03A9 world"
    proposal = _valid_proposal()
    with pytest.raises(ValueError):
        canonicalize_interaction_proposal(
            located, source_text=source_text, proposal=proposal)


# ── Raw source preservation / no cap ─────────────────────────────────────

def test_canonicalize_interaction_proposal_source_text_not_stripped_offsets_preserved():
    located = LocatedInteractionSpan(
        source_message_row_id=1, span_start=2, span_end=5, exact_source_span="foo")
    proposal = _valid_proposal()
    result = canonicalize_interaction_proposal(
        located, source_text="  foo  ", proposal=proposal)
    assert result.source_message_row_id == 1
    assert result.span_start == 2
    assert result.span_end == 5
    assert result.exact_source_span == "foo"


def test_canonicalize_interaction_proposal_source_text_no_arbitrary_cap():
    """source_text is comfortably larger than every Stage 2A char cap
    (300/150) -- catches accidental reuse of _require_bounded_text against
    source_text itself."""
    source_text = "x" * 1200 + "value"
    located = LocatedInteractionSpan(
        source_message_row_id=1, span_start=1200, span_end=1205, exact_source_span="value")
    proposal = _valid_proposal()
    result = canonicalize_interaction_proposal(
        located, source_text=source_text, proposal=proposal)
    assert isinstance(result, InteractionSignalOccurrence)
    assert result.span_start == 1200
    assert result.span_end == 1205
    assert result.exact_source_span == "value"


# ── Output observability ──────────────────────────────────────────────

def test_canonicalize_interaction_proposal_output_matches_source_slice_and_located_text():
    """Observable-only invariant: does not (and cannot, black-box) prove
    whether exact_source_span was populated from source_text or from
    located.exact_source_span -- under a successful call the two are
    always equal, so only their equality is asserted here. Construction
    origin is a diff-audit concern."""
    located = _located()
    proposal = _valid_proposal()
    result = canonicalize_interaction_proposal(
        located, source_text=_SOURCE_TEXT, proposal=proposal)
    assert result.exact_source_span == _SOURCE_TEXT[result.span_start:result.span_end]
    assert result.exact_source_span == located.exact_source_span
