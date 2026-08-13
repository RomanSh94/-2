"""Structural characterization tests for the Professional Core V2 Stage 2A
primitives (Slice 2A-1) in professional_turn_analysis.py.

These tests prove ONLY structural behavior: exact enum membership,
immutability, intrinsic field invariants (type/bounds/length-consistency),
and the exact field boundary of each primitive type. They do NOT test any
algorithm -- offset-search-by-text location, semantic labeling, candidate
harvesting, and orchestration are not implemented in this slice and are
explicitly out of scope here. No semantic classification (e.g. which
EvidenceKind a phrase "should" get) is asserted anywhere in this file.
"""
import ast
import dataclasses
import pathlib

import pytest

import professional_turn_analysis
from professional_turn_analysis import (
    CONTEXT_AFTER_MAX_CHARS,
    CONTEXT_BEFORE_MAX_CHARS,
    EVIDENCE_CANDIDATE_MAX_CHARS,
    INTERACTION_CANDIDATE_MAX_CHARS,
    MAX_EVIDENCE_CANDIDATES_PER_TURN,
    MAX_INTERACTION_CANDIDATES_PER_TURN,
    AnalysisComponentStatus,
    EvidenceSpanCandidate,
    InteractionApplicability,
    InteractionOccurrenceState,
    InteractionSignalOccurrence,
    InteractionSpanCandidate,
    LocatedEvidenceSpan,
    LocatedInteractionSpan,
    TurnAnalysisStatus,
)
from therapeutic_domain import InteractionSignal


# ── A. Exact enum membership ────────────────────────────────────────────

def test_analysis_component_status_has_exactly_three_members():
    assert {m.value for m in AnalysisComponentStatus} == {
        "VALIDATED", "DEGRADED", "UNAVAILABLE"}


def test_interaction_applicability_has_exactly_two_members():
    assert {m.value for m in InteractionApplicability} == {
        "CURRENT_DIRECTIVE", "NON_CURRENT_CONTEXT"}


def test_interaction_occurrence_state_has_exactly_two_members():
    assert {m.value for m in InteractionOccurrenceState} == {"ACTIVE", "RETRACTED"}


def test_turn_analysis_status_has_exactly_three_members():
    assert {m.value for m in TurnAnalysisStatus} == {"OK", "PARTIAL", "FAILED"}


# ── B. All dataclasses are frozen ───────────────────────────────────────

def test_evidence_span_candidate_is_frozen():
    c = EvidenceSpanCandidate(exact_source_span="устал")
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.exact_source_span = "other"


def test_interaction_span_candidate_is_frozen():
    c = InteractionSpanCandidate(exact_source_span="без советов")
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.exact_source_span = "other"


def test_located_evidence_span_is_frozen():
    s = LocatedEvidenceSpan(
        source_message_row_id=1, span_start=0, span_end=5, exact_source_span="hello")
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.span_start = 1


def test_located_interaction_span_is_frozen():
    s = LocatedInteractionSpan(
        source_message_row_id=1, span_start=0, span_end=5, exact_source_span="hello")
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.span_start = 1


def test_interaction_signal_occurrence_is_frozen():
    o = InteractionSignalOccurrence(
        source_message_row_id=1, signal=InteractionSignal.NO_ADVICE,
        span_start=0, span_end=5, exact_source_span="hello",
        applicability=InteractionApplicability.CURRENT_DIRECTIVE,
        state=InteractionOccurrenceState.ACTIVE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        o.signal = InteractionSignal.JUST_TALK


# ── C. EvidenceSpanCandidate ────────────────────────────────────────────

def test_evidence_span_candidate_valid_construction():
    c = EvidenceSpanCandidate(exact_source_span="немного устал")
    assert c.exact_source_span == "немного устал"
    assert c.context_before is None
    assert c.context_after is None


def test_evidence_span_candidate_max_exactly_300_accepted():
    text = "a" * EVIDENCE_CANDIDATE_MAX_CHARS
    c = EvidenceSpanCandidate(exact_source_span=text)
    assert c.exact_source_span == text


def test_evidence_span_candidate_301_rejected():
    with pytest.raises(ValueError):
        EvidenceSpanCandidate(exact_source_span="a" * (EVIDENCE_CANDIDATE_MAX_CHARS + 1))


def test_evidence_span_candidate_empty_rejected():
    with pytest.raises(ValueError):
        EvidenceSpanCandidate(exact_source_span="")


def test_evidence_span_candidate_whitespace_only_rejected():
    with pytest.raises(ValueError):
        EvidenceSpanCandidate(exact_source_span="   ")


def test_evidence_span_candidate_non_str_rejected():
    with pytest.raises(ValueError):
        EvidenceSpanCandidate(exact_source_span=12345)


def test_evidence_span_candidate_exact_string_preserved():
    text = "  Мне  страшно  "
    c = EvidenceSpanCandidate(exact_source_span=text)
    assert c.exact_source_span == text


def test_evidence_span_candidate_context_none_accepted():
    c = EvidenceSpanCandidate(exact_source_span="устал", context_before=None, context_after=None)
    assert c.context_before is None
    assert c.context_after is None


def test_evidence_span_candidate_valid_60_char_context_accepted():
    ctx = "y" * CONTEXT_BEFORE_MAX_CHARS
    c = EvidenceSpanCandidate(exact_source_span="устал", context_before=ctx, context_after=ctx)
    assert c.context_before == ctx
    assert c.context_after == ctx


def test_evidence_span_candidate_61_char_context_before_rejected():
    with pytest.raises(ValueError):
        EvidenceSpanCandidate(
            exact_source_span="устал", context_before="y" * (CONTEXT_BEFORE_MAX_CHARS + 1))


def test_evidence_span_candidate_61_char_context_after_rejected():
    with pytest.raises(ValueError):
        EvidenceSpanCandidate(
            exact_source_span="устал", context_after="y" * (CONTEXT_AFTER_MAX_CHARS + 1))


def test_evidence_span_candidate_empty_context_rejected():
    with pytest.raises(ValueError):
        EvidenceSpanCandidate(exact_source_span="устал", context_before="")


def test_evidence_span_candidate_whitespace_only_context_rejected():
    with pytest.raises(ValueError):
        EvidenceSpanCandidate(exact_source_span="устал", context_after="   ")


def test_evidence_span_candidate_no_silent_stripping_or_truncation():
    text = "  устал  "
    c = EvidenceSpanCandidate(exact_source_span=text)
    assert c.exact_source_span == text
    assert len(c.exact_source_span) == len(text)


# ── D. InteractionSpanCandidate (same checks, 150-char cap) ────────────

def test_interaction_span_candidate_valid_construction():
    c = InteractionSpanCandidate(exact_source_span="без советов")
    assert c.exact_source_span == "без советов"
    assert c.context_before is None
    assert c.context_after is None


def test_interaction_span_candidate_max_exactly_150_accepted():
    text = "a" * INTERACTION_CANDIDATE_MAX_CHARS
    c = InteractionSpanCandidate(exact_source_span=text)
    assert c.exact_source_span == text


def test_interaction_span_candidate_151_rejected():
    with pytest.raises(ValueError):
        InteractionSpanCandidate(exact_source_span="a" * (INTERACTION_CANDIDATE_MAX_CHARS + 1))


def test_interaction_span_candidate_empty_rejected():
    with pytest.raises(ValueError):
        InteractionSpanCandidate(exact_source_span="")


def test_interaction_span_candidate_whitespace_only_rejected():
    with pytest.raises(ValueError):
        InteractionSpanCandidate(exact_source_span="   ")


def test_interaction_span_candidate_non_str_rejected():
    with pytest.raises(ValueError):
        InteractionSpanCandidate(exact_source_span=["без", "советов"])


def test_interaction_span_candidate_exact_string_preserved():
    text = "  без советов  "
    c = InteractionSpanCandidate(exact_source_span=text)
    assert c.exact_source_span == text


def test_interaction_span_candidate_context_none_accepted():
    c = InteractionSpanCandidate(exact_source_span="без советов")
    assert c.context_before is None
    assert c.context_after is None


def test_interaction_span_candidate_valid_60_char_context_accepted():
    ctx = "z" * CONTEXT_AFTER_MAX_CHARS
    c = InteractionSpanCandidate(
        exact_source_span="без советов", context_before=ctx, context_after=ctx)
    assert c.context_before == ctx
    assert c.context_after == ctx


def test_interaction_span_candidate_61_char_context_rejected():
    with pytest.raises(ValueError):
        InteractionSpanCandidate(
            exact_source_span="без советов", context_before="z" * (CONTEXT_BEFORE_MAX_CHARS + 1))


def test_interaction_span_candidate_empty_context_rejected():
    with pytest.raises(ValueError):
        InteractionSpanCandidate(exact_source_span="без советов", context_after="")


def test_interaction_span_candidate_whitespace_only_context_rejected():
    with pytest.raises(ValueError):
        InteractionSpanCandidate(exact_source_span="без советов", context_before="  ")


def test_interaction_span_candidate_no_silent_stripping_or_truncation():
    text = "  дай совет  "
    c = InteractionSpanCandidate(exact_source_span=text)
    assert c.exact_source_span == text
    assert len(c.exact_source_span) == len(text)


# ── E. LocatedEvidenceSpan ───────────────────────────────────────────────

def test_located_evidence_span_valid():
    s = LocatedEvidenceSpan(
        source_message_row_id=1, span_start=0, span_end=5, exact_source_span="hello")
    assert s.source_message_row_id == 1
    assert (s.span_start, s.span_end) == (0, 5)
    assert s.exact_source_span == "hello"


def test_located_evidence_span_row_id_zero_rejected():
    with pytest.raises(ValueError):
        LocatedEvidenceSpan(
            source_message_row_id=0, span_start=0, span_end=5, exact_source_span="hello")


def test_located_evidence_span_row_id_negative_rejected():
    with pytest.raises(ValueError):
        LocatedEvidenceSpan(
            source_message_row_id=-1, span_start=0, span_end=5, exact_source_span="hello")


def test_located_evidence_span_bool_row_id_rejected():
    with pytest.raises(ValueError):
        LocatedEvidenceSpan(
            source_message_row_id=True, span_start=0, span_end=5, exact_source_span="hello")


def test_located_evidence_span_bool_offsets_rejected():
    with pytest.raises(ValueError):
        LocatedEvidenceSpan(
            source_message_row_id=1, span_start=True, span_end=5, exact_source_span="hello")


def test_located_evidence_span_negative_start_rejected():
    with pytest.raises(ValueError):
        LocatedEvidenceSpan(
            source_message_row_id=1, span_start=-1, span_end=5, exact_source_span="hellox")


def test_located_evidence_span_start_equal_end_rejected():
    with pytest.raises(ValueError):
        LocatedEvidenceSpan(
            source_message_row_id=1, span_start=5, span_end=5, exact_source_span="")


def test_located_evidence_span_start_greater_than_end_rejected():
    with pytest.raises(ValueError):
        LocatedEvidenceSpan(
            source_message_row_id=1, span_start=5, span_end=2, exact_source_span="ab")


def test_located_evidence_span_length_mismatch_rejected():
    with pytest.raises(ValueError):
        LocatedEvidenceSpan(
            source_message_row_id=1, span_start=0, span_end=5, exact_source_span="abcd")


def test_located_evidence_span_whitespace_only_rejected():
    with pytest.raises(ValueError):
        LocatedEvidenceSpan(
            source_message_row_id=1, span_start=0, span_end=3, exact_source_span="   ")


def test_located_evidence_span_300_accepted():
    text = "a" * EVIDENCE_CANDIDATE_MAX_CHARS
    s = LocatedEvidenceSpan(
        source_message_row_id=1, span_start=0, span_end=len(text), exact_source_span=text)
    assert s.exact_source_span == text


def test_located_evidence_span_301_rejected():
    text = "a" * (EVIDENCE_CANDIDATE_MAX_CHARS + 1)
    with pytest.raises(ValueError):
        LocatedEvidenceSpan(
            source_message_row_id=1, span_start=0, span_end=len(text), exact_source_span=text)


def test_located_evidence_span_is_frozen_bis():
    s = LocatedEvidenceSpan(
        source_message_row_id=1, span_start=0, span_end=5, exact_source_span="hello")
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.exact_source_span = "world"


# ── F. LocatedInteractionSpan (same relevant checks, 150-char cap) ─────

def test_located_interaction_span_valid():
    s = LocatedInteractionSpan(
        source_message_row_id=1, span_start=0, span_end=5, exact_source_span="hello")
    assert s.source_message_row_id == 1
    assert (s.span_start, s.span_end) == (0, 5)
    assert s.exact_source_span == "hello"


def test_located_interaction_span_row_id_zero_rejected():
    with pytest.raises(ValueError):
        LocatedInteractionSpan(
            source_message_row_id=0, span_start=0, span_end=5, exact_source_span="hello")


def test_located_interaction_span_row_id_negative_rejected():
    with pytest.raises(ValueError):
        LocatedInteractionSpan(
            source_message_row_id=-1, span_start=0, span_end=5, exact_source_span="hello")


def test_located_interaction_span_bool_row_id_rejected():
    with pytest.raises(ValueError):
        LocatedInteractionSpan(
            source_message_row_id=False, span_start=0, span_end=5, exact_source_span="hello")


def test_located_interaction_span_bool_offsets_rejected():
    with pytest.raises(ValueError):
        LocatedInteractionSpan(
            source_message_row_id=1, span_start=0, span_end=True, exact_source_span="h")


def test_located_interaction_span_start_greater_than_end_rejected():
    with pytest.raises(ValueError):
        LocatedInteractionSpan(
            source_message_row_id=1, span_start=5, span_end=2, exact_source_span="ab")


def test_located_interaction_span_length_mismatch_rejected():
    with pytest.raises(ValueError):
        LocatedInteractionSpan(
            source_message_row_id=1, span_start=0, span_end=5, exact_source_span="abcd")


def test_located_interaction_span_whitespace_only_rejected():
    with pytest.raises(ValueError):
        LocatedInteractionSpan(
            source_message_row_id=1, span_start=0, span_end=3, exact_source_span="   ")


def test_located_interaction_span_150_accepted():
    text = "a" * INTERACTION_CANDIDATE_MAX_CHARS
    s = LocatedInteractionSpan(
        source_message_row_id=1, span_start=0, span_end=len(text), exact_source_span=text)
    assert s.exact_source_span == text


def test_located_interaction_span_151_rejected():
    text = "a" * (INTERACTION_CANDIDATE_MAX_CHARS + 1)
    with pytest.raises(ValueError):
        LocatedInteractionSpan(
            source_message_row_id=1, span_start=0, span_end=len(text), exact_source_span=text)


# ── G. InteractionSignalOccurrence ──────────────────────────────────────

def test_interaction_signal_occurrence_valid_enum_members():
    o = InteractionSignalOccurrence(
        source_message_row_id=1, signal=InteractionSignal.NO_ADVICE,
        span_start=0, span_end=11, exact_source_span="без советов",
        applicability=InteractionApplicability.CURRENT_DIRECTIVE,
        state=InteractionOccurrenceState.ACTIVE)
    assert o.signal is InteractionSignal.NO_ADVICE
    assert o.applicability is InteractionApplicability.CURRENT_DIRECTIVE
    assert o.state is InteractionOccurrenceState.ACTIVE


def test_interaction_signal_occurrence_raw_valid_strings_normalize_to_enum_members():
    o = InteractionSignalOccurrence(
        source_message_row_id=1, signal="JUST_TALK",
        span_start=0, span_end=5, exact_source_span="hello",
        applicability="NON_CURRENT_CONTEXT", state="RETRACTED")
    assert o.signal is InteractionSignal.JUST_TALK
    assert o.applicability is InteractionApplicability.NON_CURRENT_CONTEXT
    assert o.state is InteractionOccurrenceState.RETRACTED


def test_interaction_signal_occurrence_unknown_signal_rejected():
    with pytest.raises(ValueError):
        InteractionSignalOccurrence(
            source_message_row_id=1, signal="NOT_A_REAL_SIGNAL",
            span_start=0, span_end=5, exact_source_span="hello",
            applicability=InteractionApplicability.CURRENT_DIRECTIVE,
            state=InteractionOccurrenceState.ACTIVE)


def test_interaction_signal_occurrence_unknown_applicability_rejected():
    with pytest.raises(ValueError):
        InteractionSignalOccurrence(
            source_message_row_id=1, signal=InteractionSignal.NO_ADVICE,
            span_start=0, span_end=5, exact_source_span="hello",
            applicability="NOT_A_REAL_APPLICABILITY",
            state=InteractionOccurrenceState.ACTIVE)


def test_interaction_signal_occurrence_unknown_state_rejected():
    with pytest.raises(ValueError):
        InteractionSignalOccurrence(
            source_message_row_id=1, signal=InteractionSignal.NO_ADVICE,
            span_start=0, span_end=5, exact_source_span="hello",
            applicability=InteractionApplicability.CURRENT_DIRECTIVE,
            state="NOT_A_REAL_STATE")


def test_interaction_signal_occurrence_row_id_invariant():
    with pytest.raises(ValueError):
        InteractionSignalOccurrence(
            source_message_row_id=0, signal=InteractionSignal.NO_ADVICE,
            span_start=0, span_end=5, exact_source_span="hello",
            applicability=InteractionApplicability.CURRENT_DIRECTIVE,
            state=InteractionOccurrenceState.ACTIVE)


def test_interaction_signal_occurrence_offset_invariant():
    with pytest.raises(ValueError):
        InteractionSignalOccurrence(
            source_message_row_id=1, signal=InteractionSignal.NO_ADVICE,
            span_start=5, span_end=2, exact_source_span="hello",
            applicability=InteractionApplicability.CURRENT_DIRECTIVE,
            state=InteractionOccurrenceState.ACTIVE)


def test_interaction_signal_occurrence_length_invariant():
    with pytest.raises(ValueError):
        InteractionSignalOccurrence(
            source_message_row_id=1, signal=InteractionSignal.NO_ADVICE,
            span_start=0, span_end=5, exact_source_span="abcd",
            applicability=InteractionApplicability.CURRENT_DIRECTIVE,
            state=InteractionOccurrenceState.ACTIVE)


def test_interaction_signal_occurrence_150_accepted():
    text = "a" * INTERACTION_CANDIDATE_MAX_CHARS
    o = InteractionSignalOccurrence(
        source_message_row_id=1, signal=InteractionSignal.NO_ADVICE,
        span_start=0, span_end=len(text), exact_source_span=text,
        applicability=InteractionApplicability.CURRENT_DIRECTIVE,
        state=InteractionOccurrenceState.ACTIVE)
    assert o.exact_source_span == text


def test_interaction_signal_occurrence_151_rejected():
    text = "a" * (INTERACTION_CANDIDATE_MAX_CHARS + 1)
    with pytest.raises(ValueError):
        InteractionSignalOccurrence(
            source_message_row_id=1, signal=InteractionSignal.NO_ADVICE,
            span_start=0, span_end=len(text), exact_source_span=text,
            applicability=InteractionApplicability.CURRENT_DIRECTIVE,
            state=InteractionOccurrenceState.ACTIVE)


def test_interaction_signal_occurrence_is_frozen_bis():
    o = InteractionSignalOccurrence(
        source_message_row_id=1, signal=InteractionSignal.NO_ADVICE,
        span_start=0, span_end=5, exact_source_span="hello",
        applicability=InteractionApplicability.CURRENT_DIRECTIVE,
        state=InteractionOccurrenceState.ACTIVE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        o.state = InteractionOccurrenceState.RETRACTED


def test_interaction_signal_occurrence_source_message_row_id_field_exists():
    fields = {f.name for f in dataclasses.fields(InteractionSignalOccurrence)}
    assert "source_message_row_id" in fields


# ── H. No forbidden semantic/planner fields on these primitive types ───

_FORBIDDEN_FIELDS = {
    "evidence_kind", "intent", "objective", "primary_move",
    "professional_turn_plan", "confidence", "reason", "diagnosis",
    "hypothesis",
}


def test_evidence_span_candidate_exact_field_boundary():
    fields = {f.name for f in dataclasses.fields(EvidenceSpanCandidate)}
    assert fields == {"exact_source_span", "context_before", "context_after"}
    assert not (fields & _FORBIDDEN_FIELDS)


def test_interaction_span_candidate_exact_field_boundary():
    fields = {f.name for f in dataclasses.fields(InteractionSpanCandidate)}
    assert fields == {"exact_source_span", "context_before", "context_after"}
    assert not (fields & _FORBIDDEN_FIELDS)


def test_located_evidence_span_exact_field_boundary():
    fields = {f.name for f in dataclasses.fields(LocatedEvidenceSpan)}
    assert fields == {
        "source_message_row_id", "span_start", "span_end", "exact_source_span"}
    assert not (fields & _FORBIDDEN_FIELDS)


def test_located_interaction_span_exact_field_boundary():
    fields = {f.name for f in dataclasses.fields(LocatedInteractionSpan)}
    assert fields == {
        "source_message_row_id", "span_start", "span_end", "exact_source_span"}
    assert not (fields & _FORBIDDEN_FIELDS)


def test_interaction_signal_occurrence_exact_field_boundary():
    fields = {f.name for f in dataclasses.fields(InteractionSignalOccurrence)}
    assert fields == {
        "source_message_row_id", "signal", "span_start", "span_end",
        "exact_source_span", "applicability", "state"}
    assert not (fields & _FORBIDDEN_FIELDS)


# ── I. All six V1 engineering limits are locked to exact literals ──────

def test_all_six_v1_engineering_limits_are_locked():
    """ENGINEERING_V1_LIMITS -- not clinical thresholds, not empirically
    optimal values, not safety thresholds. Locks the literal sealed V1
    values independently of any test that only builds strings using the
    constants themselves (which would silently adapt to a changed value
    instead of catching it)."""
    assert EVIDENCE_CANDIDATE_MAX_CHARS == 300
    assert INTERACTION_CANDIDATE_MAX_CHARS == 150
    assert CONTEXT_BEFORE_MAX_CHARS == 60
    assert CONTEXT_AFTER_MAX_CHARS == 60
    assert MAX_EVIDENCE_CANDIDATES_PER_TURN == 20
    assert MAX_INTERACTION_CANDIDATES_PER_TURN == 10


# ── J. Automated module import-purity regression ────────────────────────

def test_module_import_allowlist_is_exact():
    """professional_turn_analysis.py must import only __future__.annotations,
    dataclasses.dataclass, enum.Enum, and therapeutic_domain.{EvidenceItem,
    Intent, InteractionRequest, InteractionSignal, as_enum,
    validate_evidence_against_source} -- an exact allowlist, not a
    blacklist, so an unexpected future import (database, bot,
    conversation_controller, interaction_preference, openai, asyncio,
    requests, httpx, aiohttp, os, pathlib, subprocess, sqlite3, aiosqlite,
    aiogram, or anything else) fails this test closed rather than silently
    passing because it wasn't on a blacklist. Updated for Slice 2A-3: the
    module now legitimately needs validate_evidence_against_source for
    TurnAnalysis's own source-provenance check, added here in the same
    mutation -- reused unchanged from Stage 1, never duplicated."""
    module_path = pathlib.Path(professional_turn_analysis.__file__)
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))

    actual_imports = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            actual_imports.append((node.module, tuple(a.name for a in node.names)))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                actual_imports.append((None, (alias.name,)))

    expected_imports = [
        ("__future__", ("annotations",)),
        ("dataclasses", ("dataclass",)),
        ("enum", ("Enum",)),
        ("therapeutic_domain", (
            "EvidenceItem", "Intent", "InteractionRequest", "InteractionSignal", "as_enum",
            "validate_evidence_against_source")),
    ]
    assert actual_imports == expected_imports


# ── K. Structural gap closure (post-implementation audit follow-up) ────
# Each test below closes exactly one MISSING_TEST invariant identified by
# the read-only post-implementation audit. Placed in one clearly-labeled
# section rather than interleaved into C/E/F/G above to keep this as a
# single, precisely reviewable addition; each test name self-identifies
# which public type and which invariant it locks.

def test_interaction_span_candidate_61_char_context_after_rejected():
    """Closes the InteractionSpanCandidate gap: only context_before's
    61-char rejection was previously covered; context_after must be
    locked independently, matching EvidenceSpanCandidate's coverage of
    both directions."""
    with pytest.raises(ValueError):
        InteractionSpanCandidate(
            exact_source_span="без советов",
            context_after="z" * (CONTEXT_AFTER_MAX_CHARS + 1))


def test_located_evidence_span_bool_span_end_rejected():
    """Closes the LocatedEvidenceSpan gap: only bool span_start was
    previously covered; span_end must be locked independently."""
    with pytest.raises(ValueError):
        LocatedEvidenceSpan(
            source_message_row_id=1, span_start=0, span_end=True, exact_source_span="h")


def test_located_interaction_span_bool_span_start_rejected():
    """Closes the LocatedInteractionSpan gap: only bool span_end was
    previously covered; span_start must be locked independently."""
    with pytest.raises(ValueError):
        LocatedInteractionSpan(
            source_message_row_id=1, span_start=True, span_end=5, exact_source_span="hello")


def test_located_interaction_span_start_equal_end_rejected():
    """Closes the LocatedInteractionSpan gap: only start>end was
    previously covered; start==end must be locked independently,
    matching LocatedEvidenceSpan's existing coverage of this case."""
    with pytest.raises(ValueError):
        LocatedInteractionSpan(
            source_message_row_id=1, span_start=5, span_end=5, exact_source_span="")


def test_interaction_signal_occurrence_negative_row_id_rejected():
    """Closes an InteractionSignalOccurrence gap: only row_id==0 was
    previously covered."""
    with pytest.raises(ValueError):
        InteractionSignalOccurrence(
            source_message_row_id=-1, signal=InteractionSignal.NO_ADVICE,
            span_start=0, span_end=5, exact_source_span="hello",
            applicability=InteractionApplicability.CURRENT_DIRECTIVE,
            state=InteractionOccurrenceState.ACTIVE)


def test_interaction_signal_occurrence_bool_row_id_rejected():
    """Closes an InteractionSignalOccurrence gap: bool row id was not
    previously covered for this type (unlike both Located* types)."""
    with pytest.raises(ValueError):
        InteractionSignalOccurrence(
            source_message_row_id=True, signal=InteractionSignal.NO_ADVICE,
            span_start=0, span_end=5, exact_source_span="hello",
            applicability=InteractionApplicability.CURRENT_DIRECTIVE,
            state=InteractionOccurrenceState.ACTIVE)


def test_interaction_signal_occurrence_bool_span_start_rejected():
    """Closes an InteractionSignalOccurrence gap: bool span_start was not
    previously covered for this type."""
    with pytest.raises(ValueError):
        InteractionSignalOccurrence(
            source_message_row_id=1, signal=InteractionSignal.NO_ADVICE,
            span_start=True, span_end=5, exact_source_span="hello",
            applicability=InteractionApplicability.CURRENT_DIRECTIVE,
            state=InteractionOccurrenceState.ACTIVE)


def test_interaction_signal_occurrence_bool_span_end_rejected():
    """Closes an InteractionSignalOccurrence gap: bool span_end was not
    previously covered for this type."""
    with pytest.raises(ValueError):
        InteractionSignalOccurrence(
            source_message_row_id=1, signal=InteractionSignal.NO_ADVICE,
            span_start=0, span_end=True, exact_source_span="h",
            applicability=InteractionApplicability.CURRENT_DIRECTIVE,
            state=InteractionOccurrenceState.ACTIVE)


def test_interaction_signal_occurrence_negative_span_start_rejected():
    """Closes an InteractionSignalOccurrence gap: negative span_start was
    not previously covered for this type."""
    with pytest.raises(ValueError):
        InteractionSignalOccurrence(
            source_message_row_id=1, signal=InteractionSignal.NO_ADVICE,
            span_start=-1, span_end=5, exact_source_span="hellox",
            applicability=InteractionApplicability.CURRENT_DIRECTIVE,
            state=InteractionOccurrenceState.ACTIVE)


def test_interaction_signal_occurrence_start_equal_end_rejected():
    """Closes an InteractionSignalOccurrence gap: only start>end was
    previously covered; start==end must be locked independently."""
    with pytest.raises(ValueError):
        InteractionSignalOccurrence(
            source_message_row_id=1, signal=InteractionSignal.NO_ADVICE,
            span_start=5, span_end=5, exact_source_span="",
            applicability=InteractionApplicability.CURRENT_DIRECTIVE,
            state=InteractionOccurrenceState.ACTIVE)


def test_interaction_signal_occurrence_whitespace_only_span_rejected():
    """Closes an InteractionSignalOccurrence gap: whitespace-only
    exact_source_span was not previously covered for this type (unlike
    both Located* types)."""
    with pytest.raises(ValueError):
        InteractionSignalOccurrence(
            source_message_row_id=1, signal=InteractionSignal.NO_ADVICE,
            span_start=0, span_end=3, exact_source_span="   ",
            applicability=InteractionApplicability.CURRENT_DIRECTIVE,
            state=InteractionOccurrenceState.ACTIVE)


def test_interaction_signal_occurrence_non_str_span_rejected():
    """Closes an InteractionSignalOccurrence gap: non-str exact_source_span
    was not previously covered for this type (unlike both candidate
    types)."""
    with pytest.raises(ValueError):
        InteractionSignalOccurrence(
            source_message_row_id=1, signal=InteractionSignal.NO_ADVICE,
            span_start=0, span_end=5, exact_source_span=12345,
            applicability=InteractionApplicability.CURRENT_DIRECTIVE,
            state=InteractionOccurrenceState.ACTIVE)


# ── L. Final structural test matrix closure (per-invariant isolation) ──
# Each fixture below holds every OTHER field valid so the asserted
# ValueError is provably attributable to the ONE invariant under test,
# not merely to an earlier, unrelated invariant failing first (e.g. a
# start==end fixture would mask an empty-text check; these use start<end
# with a genuinely empty/wrong-typed text instead).

# -- EvidenceSpanCandidate: context type + non-normalizing preservation --

def test_evidence_span_candidate_context_before_non_str_rejected():
    with pytest.raises(ValueError):
        EvidenceSpanCandidate(exact_source_span="устал", context_before=123)


def test_evidence_span_candidate_context_after_non_str_rejected():
    with pytest.raises(ValueError):
        EvidenceSpanCandidate(exact_source_span="устал", context_after=[])


def test_evidence_span_candidate_context_with_internal_whitespace_preserved_exactly():
    """Proves .strip() is validation-only, never normalization: a
    non-whitespace-only context with leading/trailing whitespace is
    stored character-for-character as given."""
    c = EvidenceSpanCandidate(
        exact_source_span="устал", context_before="  до  ", context_after="  после  ")
    assert c.context_before == "  до  "
    assert c.context_after == "  после  "


# -- InteractionSpanCandidate: same equivalent context coverage ---------

def test_interaction_span_candidate_context_before_non_str_rejected():
    with pytest.raises(ValueError):
        InteractionSpanCandidate(exact_source_span="без советов", context_before=123)


def test_interaction_span_candidate_context_after_non_str_rejected():
    with pytest.raises(ValueError):
        InteractionSpanCandidate(exact_source_span="без советов", context_after=[])


def test_interaction_span_candidate_context_with_internal_whitespace_preserved_exactly():
    c = InteractionSpanCandidate(
        exact_source_span="без советов", context_before="  до  ", context_after="  после  ")
    assert c.context_before == "  до  "
    assert c.context_after == "  после  "


# -- LocatedEvidenceSpan: text/type invariants isolated from bounds -----

def test_located_evidence_span_empty_text_rejected_with_valid_offsets():
    """span_end=1 (not 0) keeps 0 <= span_start < span_end TRUE, so this
    fails specifically because the text is empty, not because
    span_start == span_end."""
    with pytest.raises(ValueError):
        LocatedEvidenceSpan(
            source_message_row_id=1, span_start=0, span_end=1, exact_source_span="")


def test_located_evidence_span_non_str_text_rejected():
    with pytest.raises(ValueError):
        LocatedEvidenceSpan(
            source_message_row_id=1, span_start=0, span_end=5, exact_source_span=12345)


def test_located_evidence_span_negative_end_rejected():
    """span_start=0 is itself valid in isolation, so a negative span_end
    is the only possible cause of the failure -- distinct from the
    existing negative-span_start and start>end fixtures."""
    with pytest.raises(ValueError):
        LocatedEvidenceSpan(
            source_message_row_id=1, span_start=0, span_end=-1, exact_source_span="")


def test_located_evidence_span_non_int_row_id_rejected():
    """A plain non-int, non-bool row id (e.g. a str) is a distinct case
    from the existing bool-row-id test."""
    with pytest.raises(ValueError):
        LocatedEvidenceSpan(
            source_message_row_id="1", span_start=0, span_end=5, exact_source_span="hello")


# -- LocatedInteractionSpan: complete bound matrix -----------------------

def test_located_interaction_span_negative_start_rejected():
    with pytest.raises(ValueError):
        LocatedInteractionSpan(
            source_message_row_id=1, span_start=-1, span_end=5, exact_source_span="hellox")


def test_located_interaction_span_negative_end_rejected():
    with pytest.raises(ValueError):
        LocatedInteractionSpan(
            source_message_row_id=1, span_start=0, span_end=-1, exact_source_span="")


def test_located_interaction_span_empty_text_rejected_with_valid_offsets():
    with pytest.raises(ValueError):
        LocatedInteractionSpan(
            source_message_row_id=1, span_start=0, span_end=1, exact_source_span="")


def test_located_interaction_span_non_str_text_rejected():
    with pytest.raises(ValueError):
        LocatedInteractionSpan(
            source_message_row_id=1, span_start=0, span_end=5, exact_source_span=12345)


def test_located_interaction_span_non_int_row_id_rejected():
    with pytest.raises(ValueError):
        LocatedInteractionSpan(
            source_message_row_id="1", span_start=0, span_end=5, exact_source_span="hello")


# -- InteractionSignalOccurrence: remaining matrix cells ------------------

def test_interaction_signal_occurrence_negative_span_end_rejected():
    with pytest.raises(ValueError):
        InteractionSignalOccurrence(
            source_message_row_id=1, signal=InteractionSignal.NO_ADVICE,
            span_start=0, span_end=-1, exact_source_span="",
            applicability=InteractionApplicability.CURRENT_DIRECTIVE,
            state=InteractionOccurrenceState.ACTIVE)


def test_interaction_signal_occurrence_empty_text_rejected_with_valid_offsets():
    with pytest.raises(ValueError):
        InteractionSignalOccurrence(
            source_message_row_id=1, signal=InteractionSignal.NO_ADVICE,
            span_start=0, span_end=1, exact_source_span="",
            applicability=InteractionApplicability.CURRENT_DIRECTIVE,
            state=InteractionOccurrenceState.ACTIVE)


def test_interaction_signal_occurrence_non_int_row_id_rejected():
    with pytest.raises(ValueError):
        InteractionSignalOccurrence(
            source_message_row_id="1", signal=InteractionSignal.NO_ADVICE,
            span_start=0, span_end=5, exact_source_span="hello",
            applicability=InteractionApplicability.CURRENT_DIRECTIVE,
            state=InteractionOccurrenceState.ACTIVE)


# -- Durable regression guard for the read-only I/O-escape-hatch audit --

def test_module_contains_no_io_or_dynamic_escape_hatch_builtins():
    """Regression guard, test-file-only: professional_turn_analysis.py
    must never reference open/__import__/exec/eval/input/print as a Name
    or Attribute. Does not restrict ordinary safe builtins (len, type,
    object, ValueError, etc.) -- this is a narrow, named watchlist, not a
    broad production restriction."""
    module_path = pathlib.Path(professional_turn_analysis.__file__)
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    watched = {"open", "__import__", "exec", "eval", "input", "print"}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in watched:
            found.append(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in watched:
            found.append(node.attr)
    assert found == []
