"""Tests for professional_turn_runtime_context.py -- Professional Core V2
Runtime Context Envelope V1.

Pure, offline, no I/O anywhere in this module or the one under test.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

import professional_turn_runtime_context as rc
from professional_case_context import CanonicalCaseContext, EMPTY_CANONICAL_CASE_CONTEXT
from professional_turn_conversation_context import ProfessionalConversationContext
from professional_turn_runtime_context import ProfessionalTurnRuntimeContext


def _context(*turns):
    return ProfessionalConversationContext(turns=tuple(turns))


# ── Construction ─────────────────────────────────────────────────────────

def test_accepts_a_valid_conversation_context():
    context = _context()
    runtime_context = ProfessionalTurnRuntimeContext(conversation=context)
    assert runtime_context.conversation is context


def test_accepts_a_non_empty_conversation_context():
    from professional_turn_conversation_context import ConversationTurn, ConversationTurnRole
    turn = ConversationTurn(
        message_row_id=1, role=ConversationTurnRole.USER, content="hi")
    context = _context(turn)
    runtime_context = ProfessionalTurnRuntimeContext(conversation=context)
    assert runtime_context.conversation.turns == (turn,)


def test_rejects_wrong_type_for_conversation():
    with pytest.raises(ValueError):
        ProfessionalTurnRuntimeContext(conversation=[])
    with pytest.raises(ValueError):
        ProfessionalTurnRuntimeContext(conversation="not a context")
    with pytest.raises(ValueError):
        ProfessionalTurnRuntimeContext(conversation=None)


def test_is_immutable():
    runtime_context = ProfessionalTurnRuntimeContext(conversation=_context())
    with pytest.raises(Exception):
        runtime_context.conversation = _context()


def test_has_exactly_the_three_documented_fields():
    fields = {f for f in ProfessionalTurnRuntimeContext.__dataclass_fields__}
    assert fields == {"conversation", "first_turn_entry_active", "case_context"}


# ── Phase 2A: case_context field ─────────────────────────────────────────

def test_case_context_defaults_to_the_empty_constant():
    runtime_context = ProfessionalTurnRuntimeContext(conversation=_context())
    assert runtime_context.case_context is EMPTY_CANONICAL_CASE_CONTEXT


def test_accepts_a_non_empty_case_context():
    from professional_case_context import CanonicalCaseItem
    from therapeutic_domain import MemoryCategory, MemoryLifecycle
    item = CanonicalCaseItem(
        memory_item_id=1, category=MemoryCategory.EXPLICIT_FACT,
        lifecycle=MemoryLifecycle.CONFIRMED, content="a confirmed fact",
        source_event_ids=(1,))
    case_context = CanonicalCaseContext(items=(item,))
    runtime_context = ProfessionalTurnRuntimeContext(
        conversation=_context(), case_context=case_context)
    assert runtime_context.case_context is case_context
    assert runtime_context.case_context.items == (item,)


def test_rejects_wrong_type_for_case_context():
    with pytest.raises(ValueError):
        ProfessionalTurnRuntimeContext(conversation=_context(), case_context=[])
    with pytest.raises(ValueError):
        ProfessionalTurnRuntimeContext(conversation=_context(), case_context="not a context")
    with pytest.raises(ValueError):
        ProfessionalTurnRuntimeContext(conversation=_context(), case_context=None)


def test_case_context_never_merged_into_conversation():
    # Structural separateness: constructing with a non-empty case_context
    # must never mutate or extend `conversation` in any way.
    from professional_case_context import CanonicalCaseItem
    from therapeutic_domain import MemoryCategory, MemoryLifecycle
    item = CanonicalCaseItem(
        memory_item_id=1, category=MemoryCategory.EXPLICIT_FACT,
        lifecycle=MemoryLifecycle.CONFIRMED, content="a confirmed fact",
        source_event_ids=(1,))
    empty_conversation = _context()
    runtime_context = ProfessionalTurnRuntimeContext(
        conversation=empty_conversation, case_context=CanonicalCaseContext(items=(item,)))
    assert runtime_context.conversation is empty_conversation
    assert runtime_context.conversation.turns == ()


def test_backwards_compatible_construction_without_case_context():
    # Existing call sites that never pass case_context must keep working
    # byte-for-byte, with first_turn_entry_active semantics unchanged.
    runtime_context = ProfessionalTurnRuntimeContext(
        conversation=_context(), first_turn_entry_active=True)
    assert runtime_context.first_turn_entry_active is True
    assert runtime_context.case_context is EMPTY_CANONICAL_CASE_CONTEXT


# ── Scope discipline (V1 SCOPE / PHASE 2A ADDITION documented) ───────────

def test_module_docstring_documents_case_context_and_deferred_consumer_wiring():
    doc = rc.__doc__
    assert "PHASE 2A ADDITION" in doc
    assert "case_context" in doc
    assert "Phase 2B" in doc
    assert "FUTURE EXTENSION POINT" in doc


def test_module_performs_no_io():
    source = pathlib.Path(rc.__file__).read_text(encoding="utf-8")
    assert "open(" not in source
    assert "requests" not in source
    assert "aiosqlite" not in source
    assert "sqlite3" not in source


def test_module_imports_only_allowed_roots():
    source = pathlib.Path(rc.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    allowed_roots = {
        "__future__", "dataclasses",
        "professional_turn_conversation_context", "professional_case_context",
    }
    found_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            found_roots.add(node.module.split(".")[0])
    assert found_roots <= allowed_roots, found_roots


def test_module_imports_no_bot_no_database_no_telegram():
    source = pathlib.Path(rc.__file__).read_text(encoding="utf-8")
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
