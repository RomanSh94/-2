"""Tests for professional_turn_conversation_context.py -- Professional Core
V2 Multi-Turn Conversation Context Contract V1.

Pure, offline, no I/O anywhere in this module or the one under test.
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import textwrap

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


_FORBIDDEN_PROFESSIONAL_FREE_TEXT_MODULES = {
    "professional_turn_analyzer",
    "professional_turn_producer",
    "professional_turn_plan_proposer",
    "professional_turn_planner",
    "professional_turn_response_renderer",
    "professional_turn_response_fidelity_validator",
    "professional_turn_response_policy_validator",
    "professional_turn_response_acceptance",
}

_ALLOWED_LIVE_ENTRY_TRIAGE_MODULES = {
    "professional_reply_affordances",
    "professional_turn_ui_context",
    "professional_turn_ui_immediate_response",
}

# PROFESSIONAL FREE-TEXT RUNTIME V1: bot.py legitimately imports the
# dedicated orchestrator, plus the offline, no-model-call, no-DB
# professional_turn_conversation_context contract module (PR #98) directly,
# to shape trusted history before calling the orchestrator -- this is a
# transport/contract module, not part of the raw model-calling chain, the
# same category as the Entry Triage transport modules above.
_ALLOWED_LIVE_FREE_TEXT_RUNTIME_MODULES = {
    "professional_free_text_runtime",
    "professional_turn_conversation_context",
}

_FORBIDDEN_PROFESSIONAL_FREE_TEXT_SYMBOLS = {
    "ProfessionalConversationContext",
    "call_turn_analyzer",
    "produce_turn_analysis",
    "call_turn_plan_proposer",
    "govern_turn_plan",
    "render_turn_response",
    "validate_response_fidelity",
    "validate_response_policy",
    "accept_professional_response",
}


def test_bot_py_imports_only_the_dedicated_professional_free_text_orchestrator():
    """PROFESSIONAL FREE-TEXT RUNTIME V1 is the separately authorized
    runtime-cutover slice this test's own predecessor (test_bot_py_does_
    not_runtime_wire_professional_free_text_pipeline) explicitly anticipated
    and said would need to update or replace it -- this is that update, not
    a weakening. The durable invariant is now: when Professional free-text
    runtime claims an eligible turn, it owns the turn before First-Turn/
    Controller/legacy psychological routing and cannot silently fall back
    to them (see the real behavioral proof of that claim in
    tests/test_professional_free_text_runtime.py -- an AST check alone
    cannot prove control-flow ordering as convincingly as a real runtime
    test that mocks each lower-precedence owner to raise if called). What
    THIS test still proves structurally: bot.py never inlines the raw
    Professional Core Analyzer/Producer/Plan-Proposer/Planner/Renderer/
    Fidelity/Policy/Acceptance symbols directly -- it imports ONLY the
    dedicated orchestrator (professional_free_text_runtime.py), which is
    the sole place those symbols are wired in this codebase's runtime path.
    The already-LIVE Professional Entry Triage / trusted UI immediate-
    response surface remains explicitly allowed, same as before."""
    repo_root = pathlib.Path(ctx.__file__).resolve().parent
    text = (repo_root / "bot.py").read_text(encoding="utf-8")
    tree = ast.parse(text)

    imported_modules = set()
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.add(node.module.split(".")[0])
            imported_names.update(alias.name for alias in node.names)

    forbidden_module_hit = _FORBIDDEN_PROFESSIONAL_FREE_TEXT_MODULES & imported_modules
    assert not forbidden_module_hit, (
        f"bot.py imports offline Professional free-text pipeline module(s) "
        f"directly (must go through professional_free_text_runtime instead): "
        f"{forbidden_module_hit}")

    forbidden_symbol_hit = _FORBIDDEN_PROFESSIONAL_FREE_TEXT_SYMBOLS & imported_names
    assert not forbidden_symbol_hit, (
        f"bot.py imports offline Professional free-text pipeline symbol(s) "
        f"directly: {forbidden_symbol_hit}")

    called_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called_names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called_names.add(node.func.attr)
    forbidden_call_hit = _FORBIDDEN_PROFESSIONAL_FREE_TEXT_SYMBOLS & called_names
    assert not forbidden_call_hit, (
        f"bot.py calls offline Professional free-text pipeline function(s) "
        f"directly: {forbidden_call_hit}")

    # Positive checks: the ALLOWED live surfaces (Entry Triage, and the
    # dedicated Free-Text Runtime orchestrator + its transport contract
    # module) must actually be present -- proving this test distinguishes
    # "forbidden" from "merely professional_turn_*-named", and that this is
    # a deliberate, scoped wiring rather than a coincidental absence.
    missing_allowed = (_ALLOWED_LIVE_ENTRY_TRIAGE_MODULES
                       | _ALLOWED_LIVE_FREE_TEXT_RUNTIME_MODULES) - imported_modules
    assert not missing_allowed, (
        f"expected LIVE Professional surface import(s) missing from bot.py: {missing_allowed}")


# The PROFESSIONAL RUNTIME HISTORY BUILDER V1 slice-scope test that used to
# live here (test_bot_py_remains_unmodified_by_this_slice, asserting bot.py
# stayed git-content-identical to that slice's own HEAD blob) has been
# removed, not weakened: its own docstring explicitly anticipated and
# authorized exactly this -- "expected to be replaced or removed by whatever
# future, separately authorized runtime-cutover slice actually needs to edit
# bot.py". PROFESSIONAL FREE-TEXT RUNTIME V1 is that slice; bot.py is now
# legitimately and permanently a file every future Professional-runtime
# slice may need to touch, so a per-slice "bot.py unmodified" assertion no
# longer has a stable premise to assert. The durable invariant that
# replaces it lives in test_bot_py_imports_only_the_dedicated_professional_
# free_text_orchestrator above.


# ── build_conversation_context_from_history_rows (PROFESSIONAL RUNTIME ─────
# ── HISTORY BUILDER V1 pure builder) ────────────────────────────────────────

from professional_turn_conversation_context import build_conversation_context_from_history_rows


def _row(row_id, role, content, source):
    return (row_id, role, content, source)


def _u_row(row_id, content, source="USER_AUTHORED"):
    return _row(row_id, "user", content, source)


def _a_row(row_id, content, source="ASSISTANT_DELIVERED"):
    return _row(row_id, "assistant", content, source)


def test_empty_rows_yield_empty_conversation_context():
    result = build_conversation_context_from_history_rows([])
    from professional_turn_conversation_context import EMPTY_CONVERSATION_CONTEXT
    assert result == EMPTY_CONVERSATION_CONTEXT
    assert result.is_empty


def test_single_user_turn():
    result = build_conversation_context_from_history_rows([_u_row(1, "hi")])
    assert len(result.turns) == 1
    assert result.turns[0].role is ConversationTurnRole.USER
    assert result.turns[0].content == "hi"


def test_single_assistant_turn():
    result = build_conversation_context_from_history_rows([_a_row(1, "hello")])
    assert len(result.turns) == 1
    assert result.turns[0].role is ConversationTurnRole.ASSISTANT


def test_normal_user_assistant_sequence():
    rows = [_u_row(1, "hi"), _a_row(2, "hello"), _u_row(3, "how are you")]
    result = build_conversation_context_from_history_rows(rows)
    assert [t.message_row_id for t in result.turns] == [1, 2, 3]


def test_assistant_only_sequence_accepted():
    rows = [_a_row(1, "a"), _a_row(2, "b"), _a_row(3, "c")]
    result = build_conversation_context_from_history_rows(rows)
    assert len(result.turns) == 3
    assert all(t.role is ConversationTurnRole.ASSISTANT for t in result.turns)


def test_user_only_sequence_accepted():
    rows = [_u_row(1, "a"), _u_row(2, "b")]
    result = build_conversation_context_from_history_rows(rows)
    assert len(result.turns) == 2
    assert all(t.role is ConversationTurnRole.USER for t in result.turns)


def test_exact_ru_unicode_preserved():
    text = "Последние пару дней я откладываю дела."
    result = build_conversation_context_from_history_rows([_u_row(1, text)])
    assert result.turns[0].content == text


def test_exact_en_preserved():
    text = "I keep procrastinating and then getting angry at myself."
    result = build_conversation_context_from_history_rows([_u_row(1, text)])
    assert result.turns[0].content == text


def test_leading_trailing_whitespace_preserved_when_content_nonempty():
    text = "  hello there  "
    result = build_conversation_context_from_history_rows([_u_row(1, text)])
    assert result.turns[0].content == text


def test_empty_content_excluded():
    rows = [_u_row(1, ""), _a_row(2, "real reply")]
    result = build_conversation_context_from_history_rows(rows)
    assert len(result.turns) == 1
    assert result.turns[0].message_row_id == 2


def test_whitespace_only_content_excluded():
    rows = [_u_row(1, "   \n\t  "), _a_row(2, "real reply")]
    result = build_conversation_context_from_history_rows(rows)
    assert len(result.turns) == 1
    assert result.turns[0].message_row_id == 2


def test_oversized_turn_omitted_whole_never_truncated():
    oversized = "x" * (MAX_TURN_CONTENT_CHARS + 1)
    rows = [_u_row(1, oversized), _a_row(2, "short reply")]
    result = build_conversation_context_from_history_rows(rows)
    assert len(result.turns) == 1
    assert result.turns[0].message_row_id == 2


def test_exactly_at_bound_turn_is_kept():
    exact = "x" * MAX_TURN_CONTENT_CHARS
    result = build_conversation_context_from_history_rows([_u_row(1, exact)])
    assert len(result.turns) == 1
    assert result.turns[0].content == exact


def test_max_eight_newest_whole_turns_oldest_omitted():
    rows = [_u_row(i, f"turn {i}") if i % 2 else _a_row(i, f"turn {i}")
            for i in range(1, 13)]  # 12 rows, ids 1..12
    result = build_conversation_context_from_history_rows(rows)
    assert len(result.turns) == MAX_CONTEXT_TURNS
    assert [t.message_row_id for t in result.turns] == list(range(5, 13))


def test_total_over_bound_drops_oldest_whole_turns():
    per_turn = MAX_TOTAL_CONTEXT_CHARS // 3 + 100  # 3 turns already exceed total bound
    rows = [_u_row(1, "a" * per_turn), _a_row(2, "b" * per_turn), _u_row(3, "c" * per_turn)]
    result = build_conversation_context_from_history_rows(rows)
    total = sum(len(t.content) for t in result.turns)
    assert total <= MAX_TOTAL_CONTEXT_CHARS
    assert result.turns[-1].message_row_id == 3  # newest always survives
    assert result.turns[0].message_row_id != 1   # oldest dropped first


def test_total_bound_never_produces_partial_truncation():
    per_turn = MAX_TOTAL_CONTEXT_CHARS // 3 + 100
    rows = [_u_row(1, "a" * per_turn), _a_row(2, "b" * per_turn), _u_row(3, "c" * per_turn)]
    result = build_conversation_context_from_history_rows(rows)
    survivors = {1: "a" * per_turn, 2: "b" * per_turn, 3: "c" * per_turn}
    for t in result.turns:
        assert t.content == survivors[t.message_row_id]  # whole or absent, never partial


def test_identical_genuine_content_preserved_as_separate_turns():
    rows = [_u_row(1, "мне грустно"), _u_row(3, "мне грустно")]
    result = build_conversation_context_from_history_rows(rows)
    assert len(result.turns) == 2
    assert result.turns[0].content == result.turns[1].content == "мне грустно"
    assert result.turns[0].message_row_id != result.turns[1].message_row_id


def test_synthetic_ui_raw_row_rejected():
    rows = [_u_row(1, "elaborate", source="SYNTHETIC_UI"), _a_row(2, "real reply")]
    result = build_conversation_context_from_history_rows(rows)
    assert len(result.turns) == 1
    assert result.turns[0].message_row_id == 2


def test_null_provenance_raw_row_rejected():
    rows = [_u_row(1, "legacy row", source=None), _a_row(2, "real reply")]
    result = build_conversation_context_from_history_rows(rows)
    assert len(result.turns) == 1
    assert result.turns[0].message_row_id == 2


def test_unknown_future_provenance_value_rejected():
    rows = [_u_row(1, "future row", source="SOME_FUTURE_VALUE"), _a_row(2, "real reply")]
    result = build_conversation_context_from_history_rows(rows)
    assert len(result.turns) == 1
    assert result.turns[0].message_row_id == 2


def test_role_source_mismatch_user_authored_with_assistant_role_rejected():
    rows = [_row(1, "assistant", "spoofed", "USER_AUTHORED"), _a_row(2, "real reply")]
    result = build_conversation_context_from_history_rows(rows)
    assert len(result.turns) == 1
    assert result.turns[0].message_row_id == 2


def test_role_source_mismatch_assistant_delivered_with_user_role_rejected():
    rows = [_row(1, "user", "spoofed", "ASSISTANT_DELIVERED"), _a_row(2, "real reply")]
    result = build_conversation_context_from_history_rows(rows)
    assert len(result.turns) == 1
    assert result.turns[0].message_row_id == 2


def test_non_positive_row_id_excluded():
    rows = [_u_row(0, "bad id"), _u_row(-5, "also bad"), _a_row(2, "real reply")]
    result = build_conversation_context_from_history_rows(rows)
    assert len(result.turns) == 1
    assert result.turns[0].message_row_id == 2


def test_strict_final_ordering_holds():
    rows = [_u_row(1, "a"), _a_row(5, "b"), _u_row(9, "c")]
    result = build_conversation_context_from_history_rows(rows)
    ids = [t.message_row_id for t in result.turns]
    assert ids == sorted(ids)


def test_out_of_order_input_fails_closed_via_context_contract():
    rows = [_u_row(5, "later"), _a_row(2, "earlier")]  # deliberately out of order
    with pytest.raises(ValueError):
        build_conversation_context_from_history_rows(rows)


def test_duplicate_row_id_input_fails_closed_via_context_contract():
    rows = [_u_row(1, "a"), _a_row(1, "b")]  # duplicate id
    with pytest.raises(ValueError):
        build_conversation_context_from_history_rows(rows)


def test_builder_makes_no_model_call_no_summary_no_source_text_param():
    """The docstring itself says "never summarizes" (a documented negative
    claim, not summarization code), so this checks for actual model/
    summarization CALLS -- not a substring ban that would collide with
    that legitimate prose."""
    sig = inspect.signature(build_conversation_context_from_history_rows)
    assert list(sig.parameters) == ["rows"]
    tree = ast.parse(textwrap.dedent(inspect.getsource(build_conversation_context_from_history_rows)))
    called_names = {n.func.id for n in ast.walk(tree)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    called_attrs = {n.func.attr for n in ast.walk(tree)
                     if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not called_names & {"maybe_summarize", "save_summary"}
    assert not called_attrs & {"maybe_summarize", "save_summary", "create", "chat"}
    src = inspect.getsource(build_conversation_context_from_history_rows)
    assert "openai" not in src.lower()


def test_builder_never_concatenates_rows_into_a_single_string():
    src = inspect.getsource(build_conversation_context_from_history_rows)
    assert "+ content" not in src and "content +" not in src
    assert "join(" not in src
