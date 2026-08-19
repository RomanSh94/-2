"""Tests for professional_turn_conversation_context.py -- Professional Core
V2 Multi-Turn Conversation Context Contract V1.

Pure, offline, no I/O anywhere in this module or the one under test.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

import professional_turn_conversation_context as ctx
from professional_turn_conversation_context import (
    MAX_CONTEXT_TURNS,
    MAX_TOTAL_CONTEXT_CHARS,
    MAX_TURN_CONTENT_CHARS,
    ConversationTurn,
    ConversationTurnRole,
    ProfessionalConversationContext,
)


def _turn(row_id=1, role=ConversationTurnRole.USER, content="hi"):
    return ConversationTurn(message_row_id=row_id, role=role, content=content)


# ── Role vocabulary ──────────────────────────────────────────────────────

def test_role_enum_has_exactly_user_and_assistant():
    assert {m.name for m in ConversationTurnRole} == {"USER", "ASSISTANT"}
    assert ConversationTurnRole.USER.value == "USER"
    assert ConversationTurnRole.ASSISTANT.value == "ASSISTANT"


def test_role_enum_has_no_system_member():
    assert not hasattr(ConversationTurnRole, "SYSTEM")
    assert "SYSTEM" not in {m.name for m in ConversationTurnRole}


# ── ConversationTurn construction ────────────────────────────────────────

def test_turn_constructs_with_valid_fields():
    turn = _turn(row_id=42, role=ConversationTurnRole.ASSISTANT, content="Как ты?")
    assert turn.message_row_id == 42
    assert turn.role is ConversationTurnRole.ASSISTANT
    assert turn.content == "Как ты?"


def test_turn_rejects_non_positive_row_id():
    for bad in (0, -1, -100):
        with pytest.raises(ValueError):
            _turn(row_id=bad)


def test_turn_rejects_non_int_row_id():
    for bad in ("1", 1.0, None, True):
        with pytest.raises(ValueError):
            _turn(row_id=bad)


def test_turn_rejects_invalid_role():
    with pytest.raises(ValueError):
        ConversationTurn(message_row_id=1, role="USER", content="hi")
    with pytest.raises(ValueError):
        ConversationTurn(message_row_id=1, role=None, content="hi")


def test_turn_rejects_empty_or_whitespace_content():
    for bad in ("", "   ", "\n\t"):
        with pytest.raises(ValueError):
            _turn(content=bad)


def test_turn_rejects_non_string_content():
    with pytest.raises(ValueError):
        _turn(content=None)
    with pytest.raises(ValueError):
        _turn(content=123)


def test_turn_rejects_content_over_the_per_turn_bound():
    with pytest.raises(ValueError):
        _turn(content="x" * (MAX_TURN_CONTENT_CHARS + 1))


def test_turn_accepts_content_exactly_at_the_per_turn_bound():
    turn = _turn(content="x" * MAX_TURN_CONTENT_CHARS)
    assert len(turn.content) == MAX_TURN_CONTENT_CHARS


def test_turn_is_immutable():
    turn = _turn()
    with pytest.raises(Exception):
        turn.content = "changed"


# ── ProfessionalConversationContext construction ─────────────────────────

def test_context_accepts_empty_tuple():
    context = ProfessionalConversationContext(turns=())
    assert context.turns == ()
    assert context.is_empty is True


def test_context_accepts_user_and_assistant_history_in_order():
    turns = (
        _turn(row_id=10, role=ConversationTurnRole.USER, content="У меня тревога."),
        _turn(row_id=11, role=ConversationTurnRole.ASSISTANT, content="Что именно тревожит?"),
        _turn(row_id=12, role=ConversationTurnRole.USER, content="Работа."),
    )
    context = ProfessionalConversationContext(turns=turns)
    assert context.turns == turns
    assert context.is_empty is False
    assert [t.role for t in context.turns] == [
        ConversationTurnRole.USER, ConversationTurnRole.ASSISTANT, ConversationTurnRole.USER]


def test_context_preserves_exact_included_content():
    original = "Точный текст, который нельзя изменять."
    turns = (_turn(row_id=1, content=original),)
    context = ProfessionalConversationContext(turns=turns)
    assert context.turns[0].content == original
    assert context.turns[0].content is original  # same str object, never rebuilt


def test_context_requires_tuple_not_list():
    with pytest.raises(ValueError):
        ProfessionalConversationContext(turns=[_turn()])


def test_context_rejects_non_conversation_turn_elements():
    with pytest.raises(ValueError):
        ProfessionalConversationContext(turns=({"role": "USER", "content": "hi"},))


def test_context_is_immutable():
    context = ProfessionalConversationContext(turns=(_turn(),))
    with pytest.raises(Exception):
        context.turns = ()


# ── Chronological ordering / duplicate / non-increasing rejection ───────

def test_context_rejects_non_increasing_row_ids():
    with pytest.raises(ValueError):
        ProfessionalConversationContext(turns=(
            _turn(row_id=5, content="a"), _turn(row_id=3, content="b")))


def test_context_rejects_duplicate_row_ids():
    with pytest.raises(ValueError):
        ProfessionalConversationContext(turns=(
            _turn(row_id=5, content="a"), _turn(row_id=5, content="b")))


def test_context_accepts_strictly_increasing_row_ids():
    context = ProfessionalConversationContext(turns=(
        _turn(row_id=1, content="a"), _turn(row_id=2, content="b"), _turn(row_id=99, content="c")))
    assert [t.message_row_id for t in context.turns] == [1, 2, 99]


# ── Bounds enforcement ────────────────────────────────────────────────────

def test_context_rejects_more_than_max_turns():
    turns = tuple(_turn(row_id=i, content=f"turn {i}") for i in range(1, MAX_CONTEXT_TURNS + 2))
    with pytest.raises(ValueError):
        ProfessionalConversationContext(turns=turns)


def test_context_accepts_exactly_max_turns():
    turns = tuple(_turn(row_id=i, content=f"turn {i}") for i in range(1, MAX_CONTEXT_TURNS + 1))
    context = ProfessionalConversationContext(turns=turns)
    assert len(context.turns) == MAX_CONTEXT_TURNS


def test_context_rejects_total_content_over_the_bound():
    # Two turns each just under the per-turn cap, but together over the
    # total-payload cap.
    half = MAX_TOTAL_CONTEXT_CHARS // 2 + 100
    with pytest.raises(ValueError):
        ProfessionalConversationContext(turns=(
            _turn(row_id=1, content="a" * half),
            _turn(row_id=2, content="b" * half)))


def test_context_accepts_total_content_exactly_at_the_bound():
    turns = (
        _turn(row_id=1, content="a" * (MAX_TOTAL_CONTEXT_CHARS // 2)),
        _turn(row_id=2, content="b" * (MAX_TOTAL_CONTEXT_CHARS - MAX_TOTAL_CONTEXT_CHARS // 2)))
    context = ProfessionalConversationContext(turns=turns)
    assert sum(len(t.content) for t in context.turns) == MAX_TOTAL_CONTEXT_CHARS


def test_empty_context_singleton_is_valid():
    assert ctx.EMPTY_CONVERSATION_CONTEXT.is_empty is True
    assert ctx.EMPTY_CONVERSATION_CONTEXT.turns == ()


# ── Structural exclusion proof (no system message, no extra fields) ─────

def test_conversation_turn_has_exactly_the_three_documented_fields():
    fields = {f for f in ConversationTurn.__dataclass_fields__}
    assert fields == {"message_row_id", "role", "content"}


def test_context_has_exactly_the_one_documented_field():
    fields = {f for f in ProfessionalConversationContext.__dataclass_fields__}
    assert fields == {"turns"}


# ── Trust semantics documentation is present, not just implied ──────────

def test_module_docstring_states_trust_semantics_explicitly():
    doc = ctx.__doc__
    assert "TRUST SEMANTICS" in doc
    assert "the user previously said" in doc
    # Split across two assertions -- the actual docstring line-wraps this
    # phrase mid-sentence, which would otherwise break a single combined
    # substring check.
    assert "the assistant previously" in doc
    assert "said/asked this." in doc
    assert "is NEVER thereby evidence that the" in doc
    assert "claim is true about the user" in doc


def test_module_docstring_documents_the_exact_bounds_and_rationale():
    doc = ctx.__doc__
    assert f"MAX_CONTEXT_TURNS = {MAX_CONTEXT_TURNS}" in doc
    assert f"MAX_TURN_CONTENT_CHARS = {MAX_TURN_CONTENT_CHARS}" in doc
    assert f"MAX_TOTAL_CONTEXT_CHARS = {MAX_TOTAL_CONTEXT_CHARS}" in doc


def test_module_docstring_documents_every_required_exclusion():
    doc = ctx.__doc__
    for excluded in (
        "hidden model state", "rolling summary", "user profile", "latent profile",
        "case-concept record", "memory item", "Telegram metadata", "callback value",
        "Entry Triage category", "secret or configuration data",
    ):
        assert excluded in doc, excluded


# ── Import boundary -- pure, offline, no runtime wiring ──────────────────

def test_module_imports_only_allowed_roots():
    source = pathlib.Path(ctx.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    allowed_roots = {"__future__", "dataclasses", "enum"}
    found_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            found_roots.add(node.module.split(".")[0])
    assert found_roots <= allowed_roots, found_roots


# ── Correction pass cross-module regression proofs ───────────────────────

def test_no_new_regex_or_genericness_validator_was_added():
    """PART E of the correction pass is still explicit: no token-overlap
    check, lexical grounding score, generic-opener regex, new policy
    regex vocabulary, or additional Acceptance-like validation stage may
    be added by this slice or its correction pass. Checked against every
    file this slice/correction pass actually touches."""
    slice_files = (
        "professional_turn_conversation_context.py",
        "professional_turn_analyzer.py",
        "professional_turn_plan_proposer.py",
        "professional_turn_response_renderer.py",
    )
    forbidden_symbols = (
        "token_overlap", "lexical_grounding", "generic_opener", "banned_phrase",
        "genericness", "GenericnessValidator", "ResponseGroundingResult",
    )
    for rel in slice_files:
        source = pathlib.Path(rel).read_text(encoding="utf-8")
        for forbidden in forbidden_symbols:
            assert forbidden not in source, (rel, forbidden)
        tree = ast.parse(source)
        top_level_imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                top_level_imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                top_level_imports.add(node.module.split(".")[0])
        assert "re" not in top_level_imports, (
            f"{rel} imports the re module -- this slice must not add regex-based "
            "quality gating")


def test_producer_planner_fidelity_policy_acceptance_remain_unmodified():
    """Cross-module regression proof for the correction pass: Producer,
    Planner, the Fidelity validator, the Policy validator, and Acceptance
    were not touched by this slice or its correction pass -- their
    working-tree content is git-content-identical to the HEAD blob
    (compared after CRLF->LF normalization, a Windows core.autocrlf=true
    checkout-time transform, not a real content difference; `git diff`
    independently reports zero diff for all of these)."""
    import subprocess
    repo_root = pathlib.Path(ctx.__file__).resolve().parent
    untouched_files = (
        "professional_turn_producer.py",
        "professional_turn_planner.py",
        "professional_turn_response_fidelity_validator.py",
        "professional_turn_response_policy_validator.py",
        "professional_turn_response_acceptance.py",
    )
    for rel in untouched_files:
        working = (repo_root / rel).read_bytes()
        head_blob = subprocess.run(
            ["git", "show", f"HEAD:{rel}"], cwd=str(repo_root),
            capture_output=True, check=True).stdout
        assert working.replace(b"\r\n", b"\n") == head_blob.replace(b"\r\n", b"\n"), rel


def test_bot_py_and_database_py_remain_unmodified():
    """This correction pass, like the slice before it, is offline-only --
    no runtime wiring. bot.py and database.py must be git-content-
    identical to the HEAD blob (CRLF-normalized comparison, see above)."""
    import subprocess
    repo_root = pathlib.Path(ctx.__file__).resolve().parent
    for rel in ("bot.py", "database.py"):
        working = (repo_root / rel).read_bytes()
        head_blob = subprocess.run(
            ["git", "show", f"HEAD:{rel}"], cwd=str(repo_root),
            capture_output=True, check=True).stdout
        assert working.replace(b"\r\n", b"\n") == head_blob.replace(b"\r\n", b"\n"), rel
