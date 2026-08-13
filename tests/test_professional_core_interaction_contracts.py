"""Structural characterization tests for the Professional Core V2
interaction-signal contracts (InteractionSignal, InteractionRequest) in
therapeutic_domain.py.

These tests prove the Stage 1B contract only: that supplied signal
categories are structurally preserved, that conflicting supplied categories
can coexist, and that InteractionRequest itself resolves nothing. They do
NOT test any runtime detector or extraction completeness -- interaction_
preference.py's extraction logic is untouched and out of scope here, and no
claim is made that a future extractor will find every signal in a real
user turn.
"""
import dataclasses

import pytest

from therapeutic_domain import InteractionRequest, InteractionSignal


# ── 1. Exact membership ─────────────────────────────────────────────────────

def test_interaction_signal_has_exactly_six_members():
    assert {m.value for m in InteractionSignal} == {
        "JUST_TALK",
        "NO_ADVICE",
        "ADVICE_ALLOWED",
        "ADVICE_REQUESTED",
        "NO_EXERCISE",
        "NO_QUESTIONS",
    }


# ── 2-9. Independence between signal categories ─────────────────────────────

def test_just_talk_independent_from_no_advice():
    req = InteractionRequest(signals=frozenset({InteractionSignal.JUST_TALK}))
    assert InteractionSignal.NO_ADVICE not in req.signals
    assert InteractionSignal.JUST_TALK in req.signals


def test_no_advice_independent_from_just_talk():
    req = InteractionRequest(signals=frozenset({InteractionSignal.NO_ADVICE}))
    assert InteractionSignal.JUST_TALK not in req.signals
    assert InteractionSignal.NO_ADVICE in req.signals


def test_advice_allowed_is_not_advice_requested():
    assert InteractionSignal.ADVICE_ALLOWED != InteractionSignal.ADVICE_REQUESTED


def test_just_talk_plus_advice_allowed_is_representable():
    req = InteractionRequest(
        signals=frozenset({InteractionSignal.JUST_TALK, InteractionSignal.ADVICE_ALLOWED}))
    assert req.signals == {InteractionSignal.JUST_TALK, InteractionSignal.ADVICE_ALLOWED}


def test_no_exercise_independent_from_no_advice():
    req = InteractionRequest(signals=frozenset({InteractionSignal.NO_EXERCISE}))
    assert InteractionSignal.NO_ADVICE not in req.signals
    assert InteractionSignal.NO_EXERCISE in req.signals


def test_no_questions_independent_from_no_advice():
    req = InteractionRequest(signals=frozenset({InteractionSignal.NO_QUESTIONS}))
    assert InteractionSignal.NO_ADVICE not in req.signals
    assert InteractionSignal.NO_QUESTIONS in req.signals


def test_no_exercise_plus_advice_requested_is_representable():
    req = InteractionRequest(
        signals=frozenset({InteractionSignal.NO_EXERCISE, InteractionSignal.ADVICE_REQUESTED}))
    assert req.signals == {InteractionSignal.NO_EXERCISE, InteractionSignal.ADVICE_REQUESTED}


def test_no_questions_plus_advice_requested_is_representable():
    req = InteractionRequest(
        signals=frozenset({InteractionSignal.NO_QUESTIONS, InteractionSignal.ADVICE_REQUESTED}))
    assert req.signals == {InteractionSignal.NO_QUESTIONS, InteractionSignal.ADVICE_REQUESTED}


# ── 10-12. Conflicting signals are accepted and not collapsed ──────────────

def test_conflicting_no_advice_and_advice_requested_preserves_both():
    req = InteractionRequest(
        signals=frozenset({InteractionSignal.NO_ADVICE, InteractionSignal.ADVICE_REQUESTED}))
    assert InteractionSignal.NO_ADVICE in req.signals
    assert InteractionSignal.ADVICE_REQUESTED in req.signals
    assert len(req.signals) == 2


def test_conflicting_no_advice_and_advice_allowed_preserves_both():
    req = InteractionRequest(
        signals=frozenset({InteractionSignal.NO_ADVICE, InteractionSignal.ADVICE_ALLOWED}))
    assert InteractionSignal.NO_ADVICE in req.signals
    assert InteractionSignal.ADVICE_ALLOWED in req.signals
    assert len(req.signals) == 2


def test_interaction_request_does_not_resolve_conflicting_signals():
    """Construction must not raise, drop, or collapse a conflicting pair --
    there is no validation step that treats NO_ADVICE + ADVICE_REQUESTED as
    an error or reduces it to one member. This is the meaningful public
    guarantee: both conflicting members survive construction unchanged."""
    req = InteractionRequest(
        signals=frozenset({InteractionSignal.NO_ADVICE, InteractionSignal.ADVICE_REQUESTED}))
    assert req.signals == {InteractionSignal.NO_ADVICE, InteractionSignal.ADVICE_REQUESTED}


# ── 13. Empty request ────────────────────────────────────────────────────────

def test_empty_signals_is_valid():
    req = InteractionRequest()
    assert req.signals == frozenset()
    req2 = InteractionRequest(signals=frozenset())
    assert req2.signals == frozenset()


# ── 14-15. Coercion / fail-closed ───────────────────────────────────────────

def test_raw_string_values_normalize_to_interaction_signal_members():
    req = InteractionRequest(signals=frozenset({"NO_ADVICE", "ADVICE_REQUESTED"}))
    assert req.signals == {InteractionSignal.NO_ADVICE, InteractionSignal.ADVICE_REQUESTED}
    for s in req.signals:
        assert isinstance(s, InteractionSignal)


def test_unknown_signal_string_raises_value_error():
    with pytest.raises(ValueError):
        InteractionRequest(signals=frozenset({"NOT_A_REAL_SIGNAL"}))


# ── 16. Immutability ─────────────────────────────────────────────────────────

def test_signals_field_cannot_be_reassigned():
    req = InteractionRequest(signals=frozenset({InteractionSignal.JUST_TALK}))
    with pytest.raises(dataclasses.FrozenInstanceError):
        req.signals = frozenset({InteractionSignal.NO_ADVICE})


# ── 17-21. Structural isolation ─────────────────────────────────────────────

def test_exact_structural_field_boundary():
    fields = {f.name for f in dataclasses.fields(InteractionRequest)}
    assert fields == {"signals"}


def test_no_repair_constraint_field_exists():
    req = InteractionRequest(signals=frozenset({InteractionSignal.NO_ADVICE}))
    assert not hasattr(req, "repair_constraints")


def test_no_response_plan_policy_fields_exist():
    req = InteractionRequest()
    assert not hasattr(req, "response_constraints")
    assert not hasattr(req, "advice_allowed")
    assert not hasattr(req, "question_allowed")


def test_no_serialization_methods_are_added():
    assert not hasattr(InteractionRequest, "to_dict")
    assert not hasattr(InteractionRequest, "from_dict")
    assert not hasattr(InteractionSignal, "to_dict")


def test_no_raw_source_text_span_order_scope_fields_exist():
    fields = {f.name for f in dataclasses.fields(InteractionRequest)}
    forbidden = {"source_text", "source_span", "span_start", "span_end",
                "order", "scope", "confidence", "reason"}
    assert not (fields & forbidden)
