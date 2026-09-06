"""Tests for professional_case_context.py -- Professional Core V2 Canonical
Case Context Foundation V1 (Phase 2A).

Pure, offline, no I/O anywhere in this module or the one under test.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

import professional_case_context as pcc
from professional_case_context import (
    CanonicalCaseContext, CanonicalCaseItem, EMPTY_CANONICAL_CASE_CONTEXT,
    MAX_CASE_ITEMS, MAX_TOTAL_CASE_CHARS,
    build_canonical_case_context_from_memory_records,
)
from therapeutic_domain import MemoryCategory, MemoryItem, MemoryLifecycle


def _memory_item(category=MemoryCategory.EXPLICIT_FACT, lifecycle=MemoryLifecycle.CONFIRMED,
                  content="a confirmed fact", source_event_ids=(1,)):
    return MemoryItem(category=category, lifecycle=lifecycle, content=content,
                       source_event_ids=list(source_event_ids))


def _case_item(memory_item_id=1, category=MemoryCategory.EXPLICIT_FACT,
                lifecycle=MemoryLifecycle.CONFIRMED, content="a confirmed fact",
                source_event_ids=(1,)):
    return CanonicalCaseItem(
        memory_item_id=memory_item_id, category=category, lifecycle=lifecycle,
        content=content, source_event_ids=source_event_ids)


# ══════════════════════════════════════════════════════════════════════════
# A. CanonicalCaseItem validation
# ══════════════════════════════════════════════════════════════════════════

def test_accepts_a_valid_item():
    item = _case_item()
    assert item.memory_item_id == 1
    assert item.category is MemoryCategory.EXPLICIT_FACT
    assert item.lifecycle is MemoryLifecycle.CONFIRMED
    assert item.content == "a confirmed fact"
    assert item.source_event_ids == (1,)


def test_rejects_non_positive_memory_item_id():
    with pytest.raises(ValueError):
        _case_item(memory_item_id=0)
    with pytest.raises(ValueError):
        _case_item(memory_item_id=-1)


def test_rejects_wrong_type_for_memory_item_id():
    with pytest.raises(ValueError):
        _case_item(memory_item_id="1")
    with pytest.raises(ValueError):
        _case_item(memory_item_id=1.0)
    with pytest.raises(ValueError):
        _case_item(memory_item_id=None)


def test_rejects_wrong_type_for_category():
    with pytest.raises(ValueError):
        _case_item(category="EXPLICIT_FACT")
    with pytest.raises(ValueError):
        _case_item(category=None)


def test_rejects_wrong_type_for_lifecycle():
    with pytest.raises(ValueError):
        _case_item(lifecycle="CONFIRMED")
    with pytest.raises(ValueError):
        _case_item(lifecycle=None)


# ── only CONFIRMED/CORRECTED accepted ────────────────────────────────────

@pytest.mark.parametrize("lifecycle", [MemoryLifecycle.CONFIRMED, MemoryLifecycle.CORRECTED])
def test_accepts_only_confirmed_or_corrected_lifecycle(lifecycle):
    item = _case_item(lifecycle=lifecycle)
    assert item.lifecycle is lifecycle


@pytest.mark.parametrize("lifecycle", [
    MemoryLifecycle.CANDIDATE, MemoryLifecycle.PROPOSED, MemoryLifecycle.REJECTED,
    MemoryLifecycle.HISTORICAL, MemoryLifecycle.EXPIRED,
])
def test_rejects_every_non_influencing_lifecycle(lifecycle):
    with pytest.raises(ValueError):
        _case_item(lifecycle=lifecycle)


def test_rejects_empty_or_whitespace_content():
    with pytest.raises(ValueError):
        _case_item(content="")
    with pytest.raises(ValueError):
        _case_item(content="   ")


def test_rejects_wrong_type_for_content():
    with pytest.raises(ValueError):
        _case_item(content=None)
    with pytest.raises(ValueError):
        _case_item(content=123)


def test_rejects_wrong_type_for_source_event_ids():
    with pytest.raises(ValueError):
        _case_item(source_event_ids=[1, 2])  # list, not tuple
    with pytest.raises(ValueError):
        _case_item(source_event_ids=None)


def test_rejects_non_positive_source_event_ids():
    with pytest.raises(ValueError):
        _case_item(source_event_ids=(1, 0))
    with pytest.raises(ValueError):
        _case_item(source_event_ids=(-1,))
    with pytest.raises(ValueError):
        _case_item(source_event_ids=(1, "2"))


def test_accepts_empty_source_event_ids():
    item = _case_item(source_event_ids=())
    assert item.source_event_ids == ()


def test_item_is_immutable():
    item = _case_item()
    with pytest.raises(Exception):
        item.content = "changed"


# Hypothesis category is never upgraded to a fact merely because its
# lifecycle is allowed to influence responses.
def test_hypothesis_category_preserved_even_with_confirmed_lifecycle():
    item = _case_item(category=MemoryCategory.HYPOTHESIS, lifecycle=MemoryLifecycle.CONFIRMED)
    assert item.category is MemoryCategory.HYPOTHESIS


def test_hypothesis_category_preserved_with_corrected_lifecycle_too():
    item = _case_item(category=MemoryCategory.HYPOTHESIS, lifecycle=MemoryLifecycle.CORRECTED)
    assert item.category is MemoryCategory.HYPOTHESIS


# ══════════════════════════════════════════════════════════════════════════
# B. CanonicalCaseContext validation
# ══════════════════════════════════════════════════════════════════════════

def test_empty_context_is_valid_and_is_empty():
    context = CanonicalCaseContext(items=())
    assert context.items == ()
    assert context.is_empty is True


def test_context_with_items_is_not_empty():
    context = CanonicalCaseContext(items=(_case_item(memory_item_id=1),))
    assert context.is_empty is False


def test_rejects_wrong_type_for_items():
    with pytest.raises(ValueError):
        CanonicalCaseContext(items=[_case_item()])
    with pytest.raises(ValueError):
        CanonicalCaseContext(items=None)


def test_rejects_non_canonical_case_item_entries():
    with pytest.raises(ValueError):
        CanonicalCaseContext(items=("not an item",))


def test_context_is_immutable():
    context = CanonicalCaseContext(items=())
    with pytest.raises(Exception):
        context.items = (_case_item(),)


def test_rejects_duplicate_memory_item_ids():
    with pytest.raises(ValueError):
        CanonicalCaseContext(items=(
            _case_item(memory_item_id=1), _case_item(memory_item_id=1)))


def test_rejects_non_monotonic_memory_item_ids():
    with pytest.raises(ValueError):
        CanonicalCaseContext(items=(
            _case_item(memory_item_id=2), _case_item(memory_item_id=1)))


def test_accepts_strictly_increasing_memory_item_ids():
    context = CanonicalCaseContext(items=(
        _case_item(memory_item_id=1), _case_item(memory_item_id=2),
        _case_item(memory_item_id=5)))
    assert [i.memory_item_id for i in context.items] == [1, 2, 5]


def test_rejects_more_than_max_case_items():
    items = tuple(_case_item(memory_item_id=i) for i in range(1, MAX_CASE_ITEMS + 2))
    with pytest.raises(ValueError):
        CanonicalCaseContext(items=items)


def test_accepts_exactly_max_case_items():
    items = tuple(
        _case_item(memory_item_id=i, content="x") for i in range(1, MAX_CASE_ITEMS + 1))
    context = CanonicalCaseContext(items=items)
    assert len(context.items) == MAX_CASE_ITEMS


def test_rejects_total_content_over_max_chars():
    big = "x" * (MAX_TOTAL_CASE_CHARS + 1)
    with pytest.raises(ValueError):
        CanonicalCaseContext(items=(_case_item(memory_item_id=1, content=big),))


def test_accepts_total_content_exactly_at_max_chars():
    exact = "x" * MAX_TOTAL_CASE_CHARS
    context = CanonicalCaseContext(items=(_case_item(memory_item_id=1, content=exact),))
    assert len(context.items[0].content) == MAX_TOTAL_CASE_CHARS


def test_empty_constant_is_the_empty_context():
    assert EMPTY_CANONICAL_CASE_CONTEXT.items == ()
    assert EMPTY_CANONICAL_CASE_CONTEXT.is_empty is True


# ══════════════════════════════════════════════════════════════════════════
# C. build_canonical_case_context_from_memory_records
# ══════════════════════════════════════════════════════════════════════════

def test_builder_empty_records_yields_empty_context():
    context = build_canonical_case_context_from_memory_records([])
    assert context.items == ()


def test_builder_includes_confirmed_and_corrected_only():
    records = [
        (1, _memory_item(lifecycle=MemoryLifecycle.CONFIRMED, content="confirmed one")),
        (2, _memory_item(lifecycle=MemoryLifecycle.CANDIDATE, content="candidate, must be excluded")),
        (3, _memory_item(lifecycle=MemoryLifecycle.CORRECTED, content="corrected one")),
        (4, _memory_item(lifecycle=MemoryLifecycle.PROPOSED, content="proposed, must be excluded")),
        (5, _memory_item(lifecycle=MemoryLifecycle.REJECTED, content="rejected, must be excluded")),
        (6, _memory_item(lifecycle=MemoryLifecycle.HISTORICAL, content="historical, must be excluded")),
        (7, _memory_item(lifecycle=MemoryLifecycle.EXPIRED, content="expired, must be excluded")),
    ]
    context = build_canonical_case_context_from_memory_records(records)
    ids = [item.memory_item_id for item in context.items]
    assert ids == [1, 3]
    assert context.items[0].content == "confirmed one"
    assert context.items[1].content == "corrected one"


def test_builder_defense_in_depth_omits_non_influencing_even_if_caller_forgot_to_filter():
    # Builder must not trust the caller's own filtering -- simulates a
    # caller that bypassed influencing_only=True. A STRUCTURALLY VALID
    # record with a non-influencing lifecycle is expected, ordinary
    # filtering -- omitted, never an error.
    records = [(1, _memory_item(lifecycle=MemoryLifecycle.CANDIDATE))]
    context = build_canonical_case_context_from_memory_records(records)
    assert context.items == ()


def test_builder_preserves_valid_candidate_mixed_with_valid_confirmed_and_corrected():
    # A structurally valid CANDIDATE mixed with structurally valid
    # CONFIRMED/CORRECTED records must be omitted without failing the
    # builder for the whole batch.
    records = [
        (1, _memory_item(lifecycle=MemoryLifecycle.CANDIDATE, content="unconfirmed guess")),
        (2, _memory_item(lifecycle=MemoryLifecycle.CONFIRMED, content="confirmed one")),
        (3, _memory_item(lifecycle=MemoryLifecycle.CORRECTED, content="corrected one")),
    ]
    context = build_canonical_case_context_from_memory_records(records)
    assert [i.memory_item_id for i in context.items] == [2, 3]


# ── P2-2 correction: structurally malformed input must FAIL CLOSED, never
# silently preserve a partial valid subset. ──────────────────────────────

def test_builder_raises_on_non_positive_memory_item_id():
    with pytest.raises(ValueError):
        build_canonical_case_context_from_memory_records([(0, _memory_item())])
    with pytest.raises(ValueError):
        build_canonical_case_context_from_memory_records([(-1, _memory_item())])


def test_builder_raises_on_bool_memory_item_id():
    # bool is an int subclass in Python -- must still never be accepted.
    with pytest.raises(ValueError):
        build_canonical_case_context_from_memory_records([(True, _memory_item())])


def test_builder_raises_on_wrong_type_memory_item_id():
    with pytest.raises(ValueError):
        build_canonical_case_context_from_memory_records([("1", _memory_item())])
    with pytest.raises(ValueError):
        build_canonical_case_context_from_memory_records([(1.0, _memory_item())])
    with pytest.raises(ValueError):
        build_canonical_case_context_from_memory_records([(None, _memory_item())])


def test_builder_raises_on_item_not_exactly_a_memory_item():
    with pytest.raises(ValueError):
        build_canonical_case_context_from_memory_records([(1, "not a memory item")])
    with pytest.raises(ValueError):
        build_canonical_case_context_from_memory_records([(1, None)])


def test_builder_raises_for_mixed_malformed_id_and_valid_no_partial_subset_survives():
    # A malformed record anywhere in the batch must fail the WHOLE batch --
    # the otherwise-valid record at id=2 must never silently survive alone.
    with pytest.raises(ValueError):
        build_canonical_case_context_from_memory_records([(0, _memory_item()), (2, _memory_item())])


def test_builder_raises_for_mixed_non_memory_item_and_valid_no_partial_subset_survives():
    with pytest.raises(ValueError):
        build_canonical_case_context_from_memory_records(
            [(1, "not MemoryItem"), (2, _memory_item())])


def test_builder_malformed_provenance_in_otherwise_valid_record_also_fails_closed():
    # CanonicalCaseItem's own __post_init__ (non-positive source_event_ids
    # entry) propagates the same way -- never caught locally by the builder.
    bad_provenance_item = _memory_item(source_event_ids=(0,))
    with pytest.raises(ValueError):
        build_canonical_case_context_from_memory_records([(1, bad_provenance_item)])


# Item 7 (V3 correction, P2): provenance is validated BEFORE the lifecycle
# filter -- a non-influencing record with malformed provenance must fail
# the whole batch, never be silently dropped by the lifecycle check first
# while a valid sibling record survives as a partially trusted result.
def test_builder_raises_when_non_influencing_record_has_malformed_provenance():
    candidate_bad_provenance = _memory_item(
        lifecycle=MemoryLifecycle.CANDIDATE, source_event_ids=(0,))
    confirmed_valid = _memory_item(lifecycle=MemoryLifecycle.CONFIRMED)
    with pytest.raises(ValueError):
        build_canonical_case_context_from_memory_records(
            [(1, candidate_bad_provenance), (2, confirmed_valid)])


def test_builder_raises_on_bool_source_event_id_even_when_non_influencing():
    candidate_bool_provenance = _memory_item(
        lifecycle=MemoryLifecycle.CANDIDATE, source_event_ids=(True,))
    with pytest.raises(ValueError):
        build_canonical_case_context_from_memory_records([(1, candidate_bool_provenance)])


def test_builder_raises_when_source_event_ids_is_not_a_list_on_memory_item():
    # A MemoryItem constructed normally always has a list -- simulate a
    # corrupted/foreign object whose source_event_ids is some other
    # container entirely. MemoryItem is a plain (non-frozen) dataclass, so
    # this reassignment is a legitimate way to simulate that corruption
    # without touching MemoryItem's own contract.
    malformed = _memory_item(lifecycle=MemoryLifecycle.CANDIDATE)
    malformed.source_event_ids = (1, 2)  # tuple, not the expected list shape
    with pytest.raises(ValueError):
        build_canonical_case_context_from_memory_records([(1, malformed)])


# Item 8 (preserve existing behavior): a structurally valid CANDIDATE with
# VALID provenance mixed with CONFIRMED/CORRECTED remains a normal
# omission -- the builder must NOT raise.
def test_builder_valid_candidate_with_valid_provenance_still_just_omitted():
    candidate_valid = _memory_item(lifecycle=MemoryLifecycle.CANDIDATE, source_event_ids=(1, 2))
    confirmed_valid = _memory_item(lifecycle=MemoryLifecycle.CONFIRMED)
    context = build_canonical_case_context_from_memory_records(
        [(1, candidate_valid), (2, confirmed_valid)])
    assert [i.memory_item_id for i in context.items] == [2]


def test_builder_preserves_hypothesis_category_as_hypothesis():
    records = [(1, _memory_item(category=MemoryCategory.HYPOTHESIS,
                                 lifecycle=MemoryLifecycle.CONFIRMED))]
    context = build_canonical_case_context_from_memory_records(records)
    assert context.items[0].category is MemoryCategory.HYPOTHESIS


def test_builder_preserves_source_event_ids_provenance():
    records = [(1, _memory_item(source_event_ids=(10, 20, 30)))]
    context = build_canonical_case_context_from_memory_records(records)
    assert context.items[0].source_event_ids == (10, 20, 30)
    assert type(context.items[0].source_event_ids) is tuple


def test_builder_preserves_memory_item_id_provenance():
    records = [(42, _memory_item())]
    context = build_canonical_case_context_from_memory_records(records)
    assert context.items[0].memory_item_id == 42


# ── oldest-first omission / max item bound ───────────────────────────────

def test_builder_oldest_first_omission_when_over_max_items():
    total = MAX_CASE_ITEMS + 3
    records = [(i, _memory_item(content=f"item {i}")) for i in range(1, total + 1)]
    context = build_canonical_case_context_from_memory_records(records)
    assert len(context.items) == MAX_CASE_ITEMS
    kept_ids = [item.memory_item_id for item in context.items]
    # The 3 OLDEST (lowest ids) are omitted; the newest MAX_CASE_ITEMS survive.
    assert kept_ids == list(range(4, total + 1))


def test_builder_never_truncates_content_when_omitting_by_item_count():
    total = MAX_CASE_ITEMS + 1
    full_text = "y" * 100
    records = [(i, _memory_item(content=full_text)) for i in range(1, total + 1)]
    context = build_canonical_case_context_from_memory_records(records)
    for item in context.items:
        assert item.content == full_text  # whole content, never truncated


# ── oldest-first omission / total char bound ─────────────────────────────

def test_builder_oldest_first_omission_when_over_total_chars():
    chunk = "z" * 900  # 4 chunks = 3600 > MAX_TOTAL_CASE_CHARS(3000); 3 chunks = 2700, fits
    records = [(i, _memory_item(content=chunk)) for i in range(1, 5)]
    context = build_canonical_case_context_from_memory_records(records)
    kept_ids = [item.memory_item_id for item in context.items]
    # Oldest (id=1) is dropped first to fit under the char bound.
    assert kept_ids == [2, 3, 4]
    assert sum(len(item.content) for item in context.items) <= MAX_TOTAL_CASE_CHARS


def test_builder_item_either_whole_or_excluded_never_partial():
    chunk = "w" * 900
    records = [(i, _memory_item(content=chunk)) for i in range(1, 5)]
    context = build_canonical_case_context_from_memory_records(records)
    for item in context.items:
        assert len(item.content) == 900  # exact original length, never partial


def test_builder_output_is_a_valid_canonical_case_context():
    records = [(1, _memory_item())]
    context = build_canonical_case_context_from_memory_records(records)
    assert type(context) is CanonicalCaseContext


# ══════════════════════════════════════════════════════════════════════════
# D. Purity / import discipline
# ══════════════════════════════════════════════════════════════════════════

def test_module_performs_no_io():
    source = pathlib.Path(pcc.__file__).read_text(encoding="utf-8")
    assert "open(" not in source
    assert "requests" not in source
    assert "aiosqlite" not in source
    assert "sqlite3" not in source


def test_module_imports_only_allowed_roots():
    source = pathlib.Path(pcc.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    allowed_roots = {"__future__", "dataclasses", "therapeutic_domain"}
    found_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            found_roots.add(node.module.split(".")[0])
    assert found_roots <= allowed_roots, found_roots


def test_module_imports_no_bot_no_database_no_telegram_no_model_client():
    source = pathlib.Path(pcc.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    assert "bot" not in modules
    assert "database" not in modules
    assert "aiogram" not in modules
    assert "openai" not in modules
    assert "config" not in modules
    assert "os" not in modules
    assert "requests" not in modules
