"""Tests for professional_turn_response_fidelity_validator.py: a pure,
offline, deterministic SURFACE-fidelity validator. No model, no network, no
DB, anywhere in this file -- every check here is synchronous and exercised
directly, with real ProfessionalTurnPlan instances built from the real
merged enums.
"""
import ast
import inspect
import pathlib
from dataclasses import fields

import pytest

import professional_turn_response_fidelity_validator
from professional_turn_planner import ProfessionalTurnPlan
from therapeutic_domain import ClarificationTarget, PrimaryResponseMove, ProfessionalObjective

from professional_turn_response_fidelity_validator import (
    FidelityRejectionReason,
    ResponseFidelityResult,
    ResponseFidelityStatus,
    validate_response_fidelity,
)


# ── Fixture helpers: real ProfessionalTurnPlan instances per reachable move ─

def _open_invitation_plan(question_allowed):
    return ProfessionalTurnPlan(
        objective=ProfessionalObjective.ESTABLISH_CONTACT,
        move=PrimaryResponseMove.OPEN_INVITATION,
        clarification_target=None, question_allowed=question_allowed)


def _focused_question_plan():
    # FOCUSED_QUESTION structurally requires question_allowed=True.
    return ProfessionalTurnPlan(
        objective=ProfessionalObjective.CLARIFY, move=PrimaryResponseMove.FOCUSED_QUESTION,
        clarification_target=ClarificationTarget.EVENT, question_allowed=True)


def _reflective_statement_plan(question_allowed):
    return ProfessionalTurnPlan(
        objective=ProfessionalObjective.REPAIR, move=PrimaryResponseMove.REFLECTIVE_STATEMENT,
        clarification_target=None, question_allowed=question_allowed)


def _closing_plan(question_allowed):
    return ProfessionalTurnPlan(
        objective=ProfessionalObjective.CLOSE, move=PrimaryResponseMove.CLOSING,
        clarification_target=None, question_allowed=question_allowed)


# ── A. Result contract ───────────────────────────────────────────────────────

def test_result_field_surface_is_exact():
    assert tuple(f.name for f in fields(ResponseFidelityResult)) == ("status", "reason")


def test_status_enum_is_exact():
    assert {m.value for m in ResponseFidelityStatus} == {"PASS", "REJECT"}


def test_reason_enum_is_exact():
    assert {m.value for m in FidelityRejectionReason} == {
        "QUESTION_MARK_NOT_ALLOWED", "TOO_MANY_QUESTION_MARKS",
        "FOCUSED_QUESTION_MARK_REQUIRED", "QUESTION_MARK_IN_NONQUESTION_MOVE",
        "LIST_MARKER_DETECTED"}


def test_pass_with_none_reason_accepted():
    result = ResponseFidelityResult(status=ResponseFidelityStatus.PASS, reason=None)
    assert result.reason is None


@pytest.mark.parametrize("reason", list(FidelityRejectionReason))
def test_reject_with_each_real_reason_accepted(reason):
    result = ResponseFidelityResult(status=ResponseFidelityStatus.REJECT, reason=reason)
    assert result.reason is reason


@pytest.mark.parametrize("reason", list(FidelityRejectionReason))
def test_pass_with_reason_set_rejected(reason):
    with pytest.raises(ValueError):
        ResponseFidelityResult(status=ResponseFidelityStatus.PASS, reason=reason)


def test_reject_with_none_reason_rejected():
    with pytest.raises(ValueError):
        ResponseFidelityResult(status=ResponseFidelityStatus.REJECT, reason=None)


def test_raw_string_status_rejected():
    with pytest.raises(ValueError):
        ResponseFidelityResult(status="PASS", reason=None)


def test_raw_string_reason_rejected():
    with pytest.raises(ValueError):
        ResponseFidelityResult(
            status=ResponseFidelityStatus.REJECT, reason="QUESTION_MARK_NOT_ALLOWED")


@pytest.mark.parametrize("bad_status", [None, 1, True, [], {}])
def test_invalid_status_types_rejected(bad_status):
    with pytest.raises(ValueError):
        ResponseFidelityResult(status=bad_status, reason=None)


@pytest.mark.parametrize("bad_reason", [1, True, [], {}])
def test_invalid_reason_types_rejected(bad_reason):
    with pytest.raises(ValueError):
        ResponseFidelityResult(status=ResponseFidelityStatus.REJECT, reason=bad_reason)


# ── B. Caller defects ────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad_plan", [None, "not a plan", 123, object()])
def test_wrong_plan_type_rejected(bad_plan):
    with pytest.raises(ValueError):
        validate_response_fidelity(bad_plan, "hello")


@pytest.mark.parametrize("bad_candidate", [None, 123, [], object()])
def test_wrong_candidate_type_rejected(bad_candidate):
    with pytest.raises(ValueError):
        validate_response_fidelity(_closing_plan(False), bad_candidate)


def test_empty_candidate_rejected():
    with pytest.raises(ValueError):
        validate_response_fidelity(_closing_plan(False), "")


@pytest.mark.parametrize("whitespace", [" ", "\t", "\n", " \t\n "])
def test_whitespace_only_candidate_rejected(whitespace):
    with pytest.raises(ValueError):
        validate_response_fidelity(_closing_plan(False), whitespace)


def test_oversized_candidate_is_not_rejected_by_this_validator():
    # This deliberately violates the normal orchestration precondition (a
    # real candidate would already satisfy the Renderer's own
    # MAX_CANDIDATE_TEXT_CHARS bound before ever reaching this validator).
    # The test exists only to prove this validator does not become a second
    # owner of that length bound -- it neither imports nor duplicates it.
    # The resulting PASS must NOT be read as this validator declaring an
    # oversized candidate a valid orchestrated Renderer result; it only
    # proves length plays no role in this validator's own decision.
    huge = "x" * 100_000
    result = validate_response_fidelity(_closing_plan(False), huge)
    assert result.status is ResponseFidelityStatus.PASS


# ── C/D/E/F. Question-mark policy matrix ────────────────────────────────────

# A. question_allowed=False, OPEN_INVITATION
def test_open_invitation_not_allowed_zero_marks_passes():
    result = validate_response_fidelity(_open_invitation_plan(False), "Хочешь продолжить.")
    assert result.status is ResponseFidelityStatus.PASS


def test_open_invitation_not_allowed_one_mark_rejected():
    result = validate_response_fidelity(_open_invitation_plan(False), "Хочешь продолжить?")
    assert result.status is ResponseFidelityStatus.REJECT
    assert result.reason is FidelityRejectionReason.QUESTION_MARK_NOT_ALLOWED


def test_open_invitation_not_allowed_two_marks_rejected_by_rule_one():
    result = validate_response_fidelity(_open_invitation_plan(False), "Хочешь? Продолжить?")
    assert result.status is ResponseFidelityStatus.REJECT
    assert result.reason is FidelityRejectionReason.QUESTION_MARK_NOT_ALLOWED


# B. FOCUSED_QUESTION (always question_allowed=True by construction)
def test_focused_question_zero_marks_rejected():
    result = validate_response_fidelity(_focused_question_plan(), "Расскажи, что произошло.")
    assert result.status is ResponseFidelityStatus.REJECT
    assert result.reason is FidelityRejectionReason.FOCUSED_QUESTION_MARK_REQUIRED


def test_focused_question_one_mark_passes():
    result = validate_response_fidelity(_focused_question_plan(), "Что произошло перед этим?")
    assert result.status is ResponseFidelityStatus.PASS


def test_focused_question_two_marks_rejected():
    result = validate_response_fidelity(
        _focused_question_plan(), "Что произошло? И что ты почувствовал?")
    assert result.status is ResponseFidelityStatus.REJECT
    assert result.reason is FidelityRejectionReason.TOO_MANY_QUESTION_MARKS


# C. REFLECTIVE_STATEMENT + question_allowed=True
def test_reflective_statement_allowed_zero_marks_passes():
    result = validate_response_fidelity(
        _reflective_statement_plan(True), "Похоже, это было тяжело.")
    assert result.status is ResponseFidelityStatus.PASS


def test_reflective_statement_allowed_one_mark_rejected():
    result = validate_response_fidelity(
        _reflective_statement_plan(True), "Похоже, это было тяжело?")
    assert result.status is ResponseFidelityStatus.REJECT
    assert result.reason is FidelityRejectionReason.QUESTION_MARK_IN_NONQUESTION_MOVE


def test_reflective_statement_allowed_two_marks_rejected_by_rule_two():
    result = validate_response_fidelity(
        _reflective_statement_plan(True), "Правда? Совсем?")
    assert result.status is ResponseFidelityStatus.REJECT
    assert result.reason is FidelityRejectionReason.TOO_MANY_QUESTION_MARKS


# D. REFLECTIVE_STATEMENT + question_allowed=False
def test_reflective_statement_not_allowed_zero_marks_passes():
    result = validate_response_fidelity(
        _reflective_statement_plan(False), "Похоже, это было тяжело.")
    assert result.status is ResponseFidelityStatus.PASS


def test_reflective_statement_not_allowed_one_mark_rejected():
    result = validate_response_fidelity(
        _reflective_statement_plan(False), "Похоже, это было тяжело?")
    assert result.status is ResponseFidelityStatus.REJECT
    assert result.reason is FidelityRejectionReason.QUESTION_MARK_NOT_ALLOWED


def test_reflective_statement_not_allowed_two_marks_rejected():
    result = validate_response_fidelity(_reflective_statement_plan(False), "Правда? Совсем?")
    assert result.status is ResponseFidelityStatus.REJECT
    assert result.reason is FidelityRejectionReason.QUESTION_MARK_NOT_ALLOWED


# E. CLOSING -- same True/False matrix as REFLECTIVE_STATEMENT
def test_closing_allowed_zero_marks_passes():
    result = validate_response_fidelity(_closing_plan(True), "Хорошо, на этом остановимся.")
    assert result.status is ResponseFidelityStatus.PASS


def test_closing_allowed_one_mark_rejected():
    result = validate_response_fidelity(_closing_plan(True), "Всё в порядке?")
    assert result.status is ResponseFidelityStatus.REJECT
    assert result.reason is FidelityRejectionReason.QUESTION_MARK_IN_NONQUESTION_MOVE


def test_closing_allowed_two_marks_rejected_by_rule_two():
    result = validate_response_fidelity(_closing_plan(True), "Всё? Точно?")
    assert result.status is ResponseFidelityStatus.REJECT
    assert result.reason is FidelityRejectionReason.TOO_MANY_QUESTION_MARKS


def test_closing_not_allowed_zero_marks_passes():
    result = validate_response_fidelity(_closing_plan(False), "Хорошо, на этом остановимся.")
    assert result.status is ResponseFidelityStatus.PASS


def test_closing_not_allowed_one_mark_rejected():
    result = validate_response_fidelity(_closing_plan(False), "Всё в порядке?")
    assert result.status is ResponseFidelityStatus.REJECT
    assert result.reason is FidelityRejectionReason.QUESTION_MARK_NOT_ALLOWED


def test_closing_not_allowed_two_marks_rejected():
    result = validate_response_fidelity(_closing_plan(False), "Всё? Точно?")
    assert result.status is ResponseFidelityStatus.REJECT
    assert result.reason is FidelityRejectionReason.QUESTION_MARK_NOT_ALLOWED


# F. OPEN_INVITATION + question_allowed=True
def test_open_invitation_allowed_zero_marks_passes():
    result = validate_response_fidelity(_open_invitation_plan(True), "Хочешь продолжить.")
    assert result.status is ResponseFidelityStatus.PASS


def test_open_invitation_allowed_one_mark_passes():
    result = validate_response_fidelity(_open_invitation_plan(True), "Хочешь продолжить?")
    assert result.status is ResponseFidelityStatus.PASS


def test_open_invitation_allowed_two_marks_rejected():
    result = validate_response_fidelity(_open_invitation_plan(True), "Хочешь? Продолжить?")
    assert result.status is ResponseFidelityStatus.REJECT
    assert result.reason is FidelityRejectionReason.TOO_MANY_QUESTION_MARKS


# ── List markers ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("candidate", [
    "1. Первый вариант",
    "1) Первый вариант",
    "- Первый вариант",
    "• Первый вариант",
    "* Первый вариант",
])
def test_list_marker_line_start_rejected(candidate):
    result = validate_response_fidelity(_reflective_statement_plan(False), candidate)
    assert result.status is ResponseFidelityStatus.REJECT
    assert result.reason is FidelityRejectionReason.LIST_MARKER_DETECTED


def test_list_marker_after_newline_rejected():
    candidate = "Вот мысль.\n1. Первый вариант"
    result = validate_response_fidelity(_reflective_statement_plan(False), candidate)
    assert result.status is ResponseFidelityStatus.REJECT
    assert result.reason is FidelityRejectionReason.LIST_MARKER_DETECTED


def test_ordinary_multi_sentence_prose_not_rejected():
    candidate = "Это обычный текст из нескольких предложений."
    result = validate_response_fidelity(_reflective_statement_plan(False), candidate)
    assert result.status is ResponseFidelityStatus.PASS


def test_inline_numbered_reference_not_rejected():
    candidate = "Есть 1. вариант внутри обычной строки."
    result = validate_response_fidelity(_reflective_statement_plan(False), candidate)
    assert result.status is ResponseFidelityStatus.PASS


def test_inline_enumeration_is_a_known_false_negative():
    # Documented, accepted limitation: inline "firstly ..., secondly ..."
    # enumeration is NOT detected as a list in V1.
    candidate = "Во-первых можно посмотреть на одно, во-вторых — на другое."
    result = validate_response_fidelity(_reflective_statement_plan(False), candidate)
    assert result.status is ResponseFidelityStatus.PASS


# ── Known surface limitations (locked intentionally, not "fixed") ──────────

def test_quoted_question_mark_in_reflective_statement_still_rejected():
    """Proves V1 performs no quote-aware parsing: a literal quoted "?" still
    trips QUESTION_MARK_IN_NONQUESTION_MOVE even though the quote itself is
    reported user speech, not the model asking a question."""
    candidate = 'Ты возвращаешься к мысли: "Почему?"'
    result = validate_response_fidelity(_reflective_statement_plan(True), candidate)
    assert result.status is ResponseFidelityStatus.REJECT
    assert result.reason is FidelityRejectionReason.QUESTION_MARK_IN_NONQUESTION_MOVE


def test_quoted_question_mark_in_focused_question_passes_surface_only():
    """A single quoted ASCII "?" satisfies the surface rule for
    FOCUSED_QUESTION even with no genuine semantic question outside the
    quote. This PASS does not prove semantic question fidelity -- it proves
    only that exactly one literal "?" character is present, which is all
    this validator ever claims."""
    candidate = 'Ты сказал: "Почему?"'
    result = validate_response_fidelity(_focused_question_plan(), candidate)
    assert result.status is ResponseFidelityStatus.PASS


def test_unicode_fullwidth_question_mark_not_counted():
    """Proves ASCII-only V1 scope: a fullwidth "？" is not an ASCII "?", so
    FOCUSED_QUESTION with only a fullwidth mark is treated as containing
    zero question marks."""
    candidate = "Что произошло？"
    result = validate_response_fidelity(_focused_question_plan(), candidate)
    assert result.status is ResponseFidelityStatus.REJECT
    assert result.reason is FidelityRejectionReason.FOCUSED_QUESTION_MARK_REQUIRED


def test_imperative_invitation_without_question_mark_passes():
    """Proves V1 does not classify imperative/invitation semantics beyond
    the frozen move/question surface matrix -- an imperative OPEN_INVITATION
    with zero "?" is accepted exactly like any other zero-mark OPEN_INVITATION."""
    result = validate_response_fidelity(_open_invitation_plan(False), "Расскажи, если захочешь.")
    assert result.status is ResponseFidelityStatus.PASS


# ── V1 move fail-closed boundary ────────────────────────────────────────────

def test_supported_fidelity_moves_is_exactly_the_frozen_four():
    assert professional_turn_response_fidelity_validator._SUPPORTED_FIDELITY_MOVES == frozenset({
        PrimaryResponseMove.OPEN_INVITATION,
        PrimaryResponseMove.FOCUSED_QUESTION,
        PrimaryResponseMove.REFLECTIVE_STATEMENT,
        PrimaryResponseMove.CLOSING,
    })


def test_unsupported_future_move_fails_closed():
    """No production code is modified or monkeypatched here. The current
    real ProfessionalTurnPlan constructor cannot itself produce a move
    outside _SUPPORTED_FIDELITY_MOVES (verified elsewhere in this repo's own
    Planner test suite), so the only way to prove the fail-closed guard
    actually fires -- not just exists in source -- is to force-set an
    already-valid frozen instance's own `move` field via the standard
    frozen-dataclass bypass, simulating a hypothetical future Planner
    version this validator has not yet been updated for."""
    plan = _closing_plan(False)
    object.__setattr__(plan, "move", PrimaryResponseMove.STRUCTURED_SUMMARY)
    with pytest.raises(ValueError):
        validate_response_fidelity(plan, "some text")


# ── Production import / architecture boundary ───────────────────────────────

# Exact frozen import contract: (kind, module, imported-name, asname, level).
# "import X" -> ("import", None, X, asname, 0); "from M import X" ->
# ("from", M, X, asname, level). level > 0 means a relative import.
_EXPECTED_IMPORTS = frozenset({
    ("from", "__future__", "annotations", None, 0),
    ("import", None, "re", None, 0),
    ("from", "dataclasses", "dataclass", None, 0),
    ("from", "enum", "Enum", None, 0),
    ("from", "professional_turn_planner", "ProfessionalTurnPlan", None, 0),
    ("from", "therapeutic_domain", "PrimaryResponseMove", None, 0),
})


def _collect_import_statements(tree):
    statements = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                statements.add(("import", None, alias.name, alias.asname, 0))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                statements.add(("from", node.module, alias.name, alias.asname, node.level))
    return statements


def _production_tree():
    source = pathlib.Path(
        professional_turn_response_fidelity_validator.__file__).read_text(encoding="utf-8")
    return ast.parse(source)


def test_production_module_has_exact_frozen_import_surface():
    """Stronger than a root-set subset check: proves the EXACT set of
    (kind, module, imported name, alias, relative-level) tuples matches the
    frozen V1 contract. This catches cases a root-only check would miss --
    an extra symbol pulled from an already-allowed module (e.g.
    `from professional_turn_planner import govern_turn_plan`), a bare
    `import professional_turn_planner` (which would newly expose that
    module's full attribute surface, enabling
    `professional_turn_planner.govern_turn_plan(...)`), a wildcard import,
    a module/symbol alias, or a relative import."""
    actual = _collect_import_statements(_production_tree())
    assert actual == _EXPECTED_IMPORTS, actual


# Frozen forbidden callable names, matched by their FINAL name regardless of
# whether the call is direct (name(...)) or via attribute access
# (something.name(...)) -- catching only imported *names* (the prior test)
# cannot prove absence of an attribute-style re-entry into another pipeline
# authority.
_FORBIDDEN_CALLABLE_NAMES = frozenset({
    "govern_turn_plan",
    "call_turn_plan_proposer",
    "render_turn_response",
    "validate_response",
    "validate_response_with_context",
})


def _called_names(tree):
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.append(func.id)
            elif isinstance(func, ast.Attribute):
                names.append(func.attr)
    return names


def test_production_module_contains_no_forbidden_call_sites():
    """Proves this module never re-enters another pipeline authority --
    neither as a direct call (govern_turn_plan(...)) nor as an attribute
    call (something.govern_turn_plan(...)) -- by inspecting every ast.Call
    node's final callable name, not just imported symbol names."""
    forbidden_hits = [
        name for name in _called_names(_production_tree())
        if name in _FORBIDDEN_CALLABLE_NAMES]
    assert forbidden_hits == [], forbidden_hits


def test_module_docstring_states_surface_only_and_pre_runtime_gaps():
    """Narrow, architectural documentation guard -- not a prose snapshot
    test. Only proves the three load-bearing framing phrases are present,
    not the surrounding wording."""
    doc = professional_turn_response_fidelity_validator.__doc__
    assert "SURFACE" in doc
    assert "does not prove" in doc
    assert "pre-runtime gap" in doc


def test_production_module_defines_no_async_functions():
    source = pathlib.Path(
        professional_turn_response_fidelity_validator.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    async_defs = [n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)]
    assert async_defs == []


def test_production_module_never_reads_env_or_network():
    source = pathlib.Path(
        professional_turn_response_fidelity_validator.__file__).read_text(encoding="utf-8")
    assert "os.environ" not in source
    assert "os.getenv" not in source
    assert "requests" not in source
    assert "httpx" not in source


_FORBIDDEN_LATENT_SOURCE_SUBSTRINGS = (
    "get_profile", "compute_profile", "format_profile_for_user", "psychology_profile",
    "pattern_hypothes", "questionnaire_score", "confirmed_episode", "pattern_confirmation",
    "schema_theme", "get_active_mode", "get_mode_profile", "get_schema_modes", "formulation",
)


def test_production_module_contains_no_latent_source_symbols():
    source = pathlib.Path(
        professional_turn_response_fidelity_validator.__file__).read_text(encoding="utf-8")
    offenders = [s for s in _FORBIDDEN_LATENT_SOURCE_SUBSTRINGS if s in source]
    assert not offenders, offenders


# ── Public function signature ───────────────────────────────────────────────

def test_validate_response_fidelity_public_signature_is_exact():
    """Fails if the public callable surface is ever widened -- e.g. adding
    an optional source_text=None or a keyword-only history= parameter --
    even if such a change technically preserved backward compatibility for
    existing two-argument callers."""
    sig = inspect.signature(validate_response_fidelity)

    assert tuple(sig.parameters) == ("plan", "candidate_text")

    plan_param = sig.parameters["plan"]
    candidate_param = sig.parameters["candidate_text"]

    assert plan_param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert candidate_param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD

    assert plan_param.default is inspect.Parameter.empty
    assert candidate_param.default is inspect.Parameter.empty

    assert sig.return_annotation is not inspect.Signature.empty

    kinds = {p.kind for p in sig.parameters.values()}
    assert inspect.Parameter.VAR_POSITIONAL not in kinds
    assert inspect.Parameter.VAR_KEYWORD not in kinds
    assert inspect.Parameter.KEYWORD_ONLY not in kinds
