"""Pure, structural characterization tests for the Professional Core V2
evidence/provenance contracts (EvidenceKind, EvidenceRef, EvidenceItem,
validate_evidence_against_source) in therapeutic_domain.py.

No DB, no LLM, no network, no Telegram -- these types have zero I/O by
design, and these tests only ever construct plain Python values.
"""
import dataclasses

import pytest

from therapeutic_domain import (
    EvidenceItem,
    EvidenceKind,
    EvidenceRef,
    validate_evidence_against_source,
)


# ── 1-3. EvidenceKind membership ────────────────────────────────────────────

def test_evidence_kind_has_exactly_the_approved_seven_members():
    assert {m.value for m in EvidenceKind} == {
        "USER_REPORTED_FACT",
        "USER_INTERPRETATION",
        "USER_REPORTED_EMOTION",
        "USER_REPORTED_BODY",
        "USER_REPORTED_URGE",
        "USER_REPORTED_BEHAVIOR",
        "USER_REPORTED_PATTERN",
    }


def test_evidence_kind_has_no_system_hypothesis_member():
    assert "SYSTEM_HYPOTHESIS" not in {m.name for m in EvidenceKind}


def test_evidence_kind_has_no_explicit_fact_member():
    assert "EXPLICIT_FACT" not in {m.name for m in EvidenceKind}


# ── 4-12. EvidenceRef structural invariants ─────────────────────────────────

def _ref(**overrides):
    kwargs = dict(source_message_row_id=1, span_start=0, span_end=5,
                  evidence_kind=EvidenceKind.USER_REPORTED_FACT)
    kwargs.update(overrides)
    return EvidenceRef(**kwargs)


def test_valid_evidence_ref_constructs():
    ref = _ref()
    assert ref.source_message_row_id == 1
    assert ref.span_start == 0
    assert ref.span_end == 5
    assert ref.evidence_kind == EvidenceKind.USER_REPORTED_FACT


def test_zero_source_message_row_id_rejected():
    with pytest.raises(ValueError):
        _ref(source_message_row_id=0)


def test_negative_source_message_row_id_rejected():
    with pytest.raises(ValueError):
        _ref(source_message_row_id=-1)


def test_bool_source_message_row_id_rejected():
    with pytest.raises(ValueError):
        _ref(source_message_row_id=True)


def test_bool_span_start_or_span_end_rejected():
    with pytest.raises(ValueError):
        _ref(span_start=True, span_end=5)
    with pytest.raises(ValueError):
        _ref(span_start=0, span_end=True)


def test_negative_span_start_rejected():
    with pytest.raises(ValueError):
        _ref(span_start=-1, span_end=5)


def test_span_start_equal_span_end_rejected():
    with pytest.raises(ValueError):
        _ref(span_start=3, span_end=3)


def test_span_start_greater_than_span_end_rejected():
    with pytest.raises(ValueError):
        _ref(span_start=5, span_end=2)


def test_unknown_evidence_kind_rejected():
    with pytest.raises(ValueError):
        _ref(evidence_kind="NOT_A_REAL_KIND")


# ── 13-18. EvidenceRef identity / equality / hashing ────────────────────────

def test_same_components_produce_equal_refs():
    a = _ref()
    b = _ref()
    assert a == b


def test_identity_independent_of_extraction_ordering():
    a = _ref()
    _unrelated_1 = _ref(source_message_row_id=99, span_start=10, span_end=20)
    b = _ref()
    _unrelated_2 = _ref(source_message_row_id=5, span_start=1, span_end=2,
                        evidence_kind=EvidenceKind.USER_REPORTED_EMOTION)
    assert a == b
    assert a != _unrelated_1
    assert b != _unrelated_2


def test_different_source_message_row_id_gives_different_ref():
    assert _ref(source_message_row_id=1) != _ref(source_message_row_id=2)


def test_different_span_gives_different_ref():
    assert _ref(span_start=0, span_end=5) != _ref(span_start=0, span_end=6)


def test_different_evidence_kind_gives_different_ref():
    assert (_ref(evidence_kind=EvidenceKind.USER_REPORTED_FACT)
           != _ref(evidence_kind=EvidenceKind.USER_INTERPRETATION))


def test_evidence_ref_is_hashable_and_equal_refs_have_equal_hashes():
    a = _ref()
    b = _ref()
    assert hash(a) == hash(b)
    assert len({a, b}) == 1


# ── 19-22. EvidenceItem structural invariants ───────────────────────────────

def test_valid_evidence_item_constructs():
    ref = _ref(span_start=0, span_end=5)
    item = EvidenceItem(ref=ref, exact_source_span="Хочу.")
    assert item.exact_source_span == "Хочу."


def test_evidence_item_rejects_non_string_exact_source_span():
    ref = _ref(span_start=0, span_end=5)
    with pytest.raises(ValueError):
        EvidenceItem(ref=ref, exact_source_span=12345)


def test_evidence_item_rejects_empty_span():
    ref = _ref(span_start=0, span_end=1)
    with pytest.raises(ValueError):
        EvidenceItem(ref=ref, exact_source_span="")


def test_evidence_item_rejects_length_mismatch():
    ref = _ref(span_start=0, span_end=5)
    with pytest.raises(ValueError):
        EvidenceItem(ref=ref, exact_source_span="this string is far too long")


# ── 23-29. validate_evidence_against_source ─────────────────────────────────

def test_validator_accepts_exact_raw_slice():
    source = "Я не боюсь разговора"
    phrase = "не боюсь"
    start = source.index(phrase)
    end = start + len(phrase)
    ref = _ref(span_start=start, span_end=end)
    item = EvidenceItem(ref=ref, exact_source_span=phrase)
    assert validate_evidence_against_source(item, source) is True


def test_validator_rejects_changed_negation():
    source = "Я не боюсь разговора"
    altered_source = "Я боюсь разговора"
    phrase = "не боюсь"
    start = source.index(phrase)
    end = start + len(phrase)
    ref = _ref(span_start=start, span_end=end)
    item = EvidenceItem(ref=ref, exact_source_span=phrase)
    assert validate_evidence_against_source(item, source) is True
    # the same item, checked against a source where the negated phrase no
    # longer occurs at those offsets, must never validate
    assert validate_evidence_against_source(item, altered_source) is False


def test_validator_rejects_changed_punctuation():
    source = "Мне нужно выговориться."
    phrase = "Мне нужно выговориться."  # span includes the trailing period
    ref = _ref(span_start=0, span_end=len(phrase))
    correct = EvidenceItem(ref=ref, exact_source_span=phrase)
    # same code-point length as the real slice (period -> "!"), so it
    # passes EvidenceItem's own length check but changes ONLY the
    # punctuation -- must still fail exact validation
    altered_text = phrase[:-1] + "!"
    altered = EvidenceItem(ref=ref, exact_source_span=altered_text)
    assert len(altered_text) == len(phrase)
    assert altered_text[:-1] == phrase[:-1]  # everything but the punctuation is identical
    assert validate_evidence_against_source(correct, source) is True
    assert validate_evidence_against_source(altered, source) is False


def test_validator_rejects_changed_quote_glyph():
    source = 'Она сказала «привет»'
    start = source.index('«')
    end = source.index('»') + 1
    ref = _ref(span_start=start, span_end=end)
    correct = EvidenceItem(ref=ref, exact_source_span='«привет»')
    altered = EvidenceItem(ref=ref, exact_source_span='"привет"')
    assert validate_evidence_against_source(correct, source) is True
    assert validate_evidence_against_source(altered, source) is False


def test_validator_rejects_changed_case():
    source = "мне нужно выговориться"
    ref = _ref(span_start=0, span_end=len("мне"))
    correct = EvidenceItem(ref=ref, exact_source_span="мне")
    altered = EvidenceItem(ref=ref, exact_source_span="Мне")
    assert validate_evidence_against_source(correct, source) is True
    assert validate_evidence_against_source(altered, source) is False


def test_validator_rejects_changed_whitespace():
    source = "мне нужно выговориться"
    span_text = "мне нужно"
    ref = _ref(span_start=0, span_end=len(span_text))
    correct = EvidenceItem(ref=ref, exact_source_span=span_text)
    # same code-point length as the real slice (space -> tab), so it passes
    # EvidenceItem's own length check but must still fail exact validation
    altered = EvidenceItem(ref=ref, exact_source_span="мне\tнужно")
    assert len(altered.exact_source_span) == len(correct.exact_source_span)
    assert validate_evidence_against_source(correct, source) is True
    assert validate_evidence_against_source(altered, source) is False


def test_validator_rejects_out_of_bounds_span():
    source = "коротко"
    ref = _ref(span_start=0, span_end=len(source) + 5)
    item = EvidenceItem(ref=ref, exact_source_span="x" * (len(source) + 5))
    assert validate_evidence_against_source(item, source) is False


# ── 30. Unicode code-point offset semantics ─────────────────────────────────

def test_validator_uses_code_point_offsets_not_utf16_or_utf8():
    """'😀' is a single Python str character (one code point, U+1F600)
    despite sitting outside the Basic Multilingual Plane and requiring a
    UTF-16 surrogate pair / 4 UTF-8 bytes. If offsets were ever UTF-16-code-
    unit or UTF-8-byte based instead of Python code-point based, this span
    would silently resolve to the wrong slice."""
    source = "Привет 😀 мир, не боюсь ничего"
    emoji = "😀"
    start = source.index(emoji)
    end = start + len(emoji)
    assert end - start == 1  # exactly one Python code point
    ref = _ref(span_start=start, span_end=end)
    item = EvidenceItem(ref=ref, exact_source_span=emoji)
    assert validate_evidence_against_source(item, source) is True

    # a neighboring Cyrillic word must still resolve correctly at its own
    # code-point offsets despite the preceding non-BMP character
    word = "мир"
    wstart = source.index(word)
    wend = wstart + len(word)
    wref = _ref(span_start=wstart, span_end=wend)
    witem = EvidenceItem(ref=wref, exact_source_span=word)
    assert validate_evidence_against_source(witem, source) is True


# ── 31. No separate canonical evidence_id field anywhere ───────────────────

def test_no_separate_canonical_evidence_id_field():
    ref = _ref()
    item = EvidenceItem(ref=ref, exact_source_span="Хочу.")
    assert not hasattr(ref, "evidence_id")
    assert not hasattr(item, "evidence_id")
    ref_fields = {f.name for f in dataclasses.fields(EvidenceRef)}
    item_fields = {f.name for f in dataclasses.fields(EvidenceItem)}
    assert ref_fields == {"source_message_row_id", "span_start", "span_end", "evidence_kind"}
    assert item_fields == {"ref", "exact_source_span"}


# ── Immutability: both types are frozen canonical provenance, not mutable
#    session state -- ordinary attribute assignment must be rejected. ──────

def test_evidence_item_exact_source_span_reassignment_rejected():
    item = EvidenceItem(ref=_ref(), exact_source_span="Хочу.")
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.exact_source_span = "другой текст"


def test_evidence_item_ref_reassignment_rejected():
    item = EvidenceItem(ref=_ref(), exact_source_span="Хочу.")
    another_ref = _ref(source_message_row_id=2)
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.ref = another_ref


def test_evidence_ref_field_reassignment_rejected():
    ref = _ref()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ref.span_start = 1
