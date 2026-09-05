"""Tests for professional_turn_runtime_context.py -- Professional Core V2
Runtime Context Envelope V1.

Pure, offline, no I/O anywhere in this module or the one under test.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

import professional_turn_runtime_context as rc
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


def test_has_exactly_the_one_documented_field():
    fields = {f for f in ProfessionalTurnRuntimeContext.__dataclass_fields__}
    assert fields == {"conversation"}


# ── Scope discipline (V1 SCOPE / FUTURE EXTENSION POINT documented) ──────

def test_module_docstring_states_no_case_context_field_yet():
    doc = rc.__doc__
    # The actual docstring line-wraps this phrase mid-sentence, so it is
    # split across two assertions rather than one combined substring check.
    assert "This module does" in doc
    assert "NOT define, store, or reserve a schema for" in doc
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
    allowed_roots = {"__future__", "dataclasses", "professional_turn_conversation_context"}
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
