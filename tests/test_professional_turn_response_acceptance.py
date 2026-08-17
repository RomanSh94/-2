"""Tests for professional_turn_response_acceptance (Professional Core V2)."""
from __future__ import annotations

import ast
import copy
import dataclasses
import inspect

import pytest

import professional_turn_response_acceptance as acceptance_mod
from professional_turn_planner import ProfessionalTurnPlan
from professional_turn_response_acceptance import (
    AcceptanceSafetyRejectionReason,
    ProfessionalResponseAcceptanceResult,
    ProfessionalResponseAcceptanceStatus,
    accept_professional_response,
)
from professional_turn_response_fidelity_validator import FidelityRejectionReason
from professional_turn_response_policy_validator import PolicyRejectionReason
from therapeutic_domain import ClarificationTarget, PrimaryResponseMove, ProfessionalObjective


def _plan(move: PrimaryResponseMove) -> ProfessionalTurnPlan:
    if move is PrimaryResponseMove.OPEN_INVITATION:
        return ProfessionalTurnPlan(
            objective=ProfessionalObjective.ESTABLISH_CONTACT, move=move,
            clarification_target=None, question_allowed=False)
    if move is PrimaryResponseMove.FOCUSED_QUESTION:
        return ProfessionalTurnPlan(
            objective=ProfessionalObjective.CLARIFY, move=move,
            clarification_target=ClarificationTarget.EVENT, question_allowed=True)
    if move is PrimaryResponseMove.REFLECTIVE_STATEMENT:
        return ProfessionalTurnPlan(
            objective=ProfessionalObjective.REPAIR, move=move,
            clarification_target=None, question_allowed=False)
    if move is PrimaryResponseMove.CLOSING:
        return ProfessionalTurnPlan(
            objective=ProfessionalObjective.CLOSE, move=move,
            clarification_target=None, question_allowed=False)
    raise AssertionError(f"no fixture for move {move!r}")


def _reflective_plan() -> ProfessionalTurnPlan:
    return _plan(PrimaryResponseMove.REFLECTIVE_STATEMENT)


def _focused_question_plan() -> ProfessionalTurnPlan:
    return _plan(PrimaryResponseMove.FOCUSED_QUESTION)


# -- Valid ACCEPT --------------------------------------------------------------

def test_accept_valid_candidate():
    result = accept_professional_response(
        plan=_reflective_plan(),
        candidate_text="Слышу тебя, это правда тяжело.",
        source_text="Мне сейчас очень тяжело.",
        risk_result={"level": "low"},
        lang="ru",
    )
    assert result.status is ProfessionalResponseAcceptanceStatus.ACCEPT
    assert result.reason is None


# -- Fidelity rejection reason preserved exactly -----------------------------

def test_fidelity_rejection_reason_preserved_exactly():
    result = accept_professional_response(
        plan=_focused_question_plan(),
        candidate_text="Что тебе нужно? Всё в порядке?",
        source_text="Мне тяжело сегодня.",
        risk_result={"level": "low"},
        lang="ru",
    )
    assert result.status is ProfessionalResponseAcceptanceStatus.REJECT
    assert result.reason is FidelityRejectionReason.TOO_MANY_QUESTION_MARKS


# -- Policy rejection reason preserved exactly -------------------------------

def test_policy_rejection_reason_preserved_exactly():
    result = accept_professional_response(
        plan=_reflective_plan(),
        candidate_text="Тебе нужно расстаться с ним.",
        source_text="Не знаю, что делать с этими отношениями.",
        risk_result={"level": "low"},
        lang="ru",
    )
    assert result.status is ProfessionalResponseAcceptanceStatus.REJECT
    assert result.reason is PolicyRejectionReason.UNPLANNED_ACTION_OR_ADVICE_CUE


# -- Safety rejection becomes only SAFETY_REJECTED, no free-form leak -------

def test_safety_rejection_becomes_only_safety_rejected():
    result = accept_professional_response(
        plan=_reflective_plan(),
        candidate_text="У тебя депрессия, я это точно вижу.",
        source_text="Мне грустно каждый день.",
        risk_result={"level": "low"},
        lang="ru",
    )
    assert result.status is ProfessionalResponseAcceptanceStatus.REJECT
    assert result.reason is AcceptanceSafetyRejectionReason.SAFETY_REJECTED
    # The only text ever stored on the result is the fixed enum member itself.
    assert result.reason.value == "SAFETY_REJECTED"


# -- Fidelity wins over Policy when both would fail --------------------------

def test_fidelity_wins_over_policy_when_both_fail(monkeypatch):
    policy_calls = []
    monkeypatch.setattr(
        acceptance_mod, "validate_response_policy",
        lambda *a, **k: policy_calls.append((a, k)) or (_ for _ in ()).throw(
            AssertionError("validate_response_policy must not be called")))

    # question_allowed=False (REFLECTIVE_STATEMENT) + two "?" -> Fidelity
    # rejects with QUESTION_MARK_NOT_ALLOWED; the text also contains
    # "тебе нужно уйти", which would independently fail Policy.
    result = accept_professional_response(
        plan=_reflective_plan(),
        candidate_text="Тебе нужно уйти? Не так ли?",
        source_text="Не знаю, что делать.",
        risk_result={"level": "low"},
        lang="ru",
    )
    assert result.status is ProfessionalResponseAcceptanceStatus.REJECT
    assert result.reason is FidelityRejectionReason.QUESTION_MARK_NOT_ALLOWED
    assert policy_calls == []


# -- Policy wins over Safety when both would fail ----------------------------

def test_policy_wins_over_safety_when_both_fail(monkeypatch):
    safety_calls = []
    monkeypatch.setattr(
        acceptance_mod, "validate_response_with_context",
        lambda *a, **k: safety_calls.append((a, k)) or (_ for _ in ()).throw(
            AssertionError("validate_response_with_context must not be called")))

    # "тебе нужно уйти" fails Policy; "у тебя депрессия" would independently
    # fail safety_validator.validate_response.
    result = accept_professional_response(
        plan=_reflective_plan(),
        candidate_text="Тебе нужно уйти. У тебя депрессия.",
        source_text="Не знаю, что делать.",
        risk_result={"level": "low"},
        lang="ru",
    )
    assert result.status is ProfessionalResponseAcceptanceStatus.REJECT
    assert result.reason is PolicyRejectionReason.UNPLANNED_ACTION_OR_ADVICE_CUE
    assert safety_calls == []


# -- Safety called only after Fidelity and Policy pass, exactly once --------

def test_safety_called_exactly_once_with_correct_args_on_accept_path(monkeypatch):
    calls = []

    def fake_safety(response_text, user_last_message, risk_result, lang):
        calls.append((response_text, user_last_message, risk_result, lang))
        return True, None

    monkeypatch.setattr(acceptance_mod, "validate_response_with_context", fake_safety)

    risk = {"level": "low"}
    result = accept_professional_response(
        plan=_reflective_plan(),
        candidate_text="Слышу тебя, это правда тяжело.",
        source_text="Мне тяжело.",
        risk_result=risk,
        lang="ru",
    )
    assert result.status is ProfessionalResponseAcceptanceStatus.ACCEPT
    assert len(calls) == 1
    response_text, user_last_message, risk_result, lang = calls[0]
    assert response_text == "Слышу тебя, это правда тяжело."
    assert user_last_message == "Мне тяжело."
    assert risk_result is risk
    assert lang == "ru"


# -- validate_response never called directly (only *_with_context) ----------

def test_validate_response_never_called_directly_ast():
    tree = ast.parse(inspect.getsource(acceptance_mod))
    called_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called_names.add(func.id)
            elif isinstance(func, ast.Attribute):
                called_names.add(func.attr)
    assert "validate_response" not in called_names
    assert "validate_response_with_context" in called_names


# -- source_text / risk_result / lang pass-through, unmodified --------------

def test_source_text_passed_through_unchanged(monkeypatch):
    captured = {}

    def fake_safety(response_text, user_last_message, risk_result, lang):
        captured["user_last_message"] = user_last_message
        return True, None

    monkeypatch.setattr(acceptance_mod, "validate_response_with_context", fake_safety)
    accept_professional_response(
        plan=_reflective_plan(),
        candidate_text="Слышу тебя, это правда тяжело.",
        source_text="Особый исходный текст пользователя.",
        risk_result={"level": "low"},
        lang="ru",
    )
    assert captured["user_last_message"] == "Особый исходный текст пользователя."


def test_risk_result_passed_through_by_identity_and_not_mutated(monkeypatch):
    captured = {}

    def fake_safety(response_text, user_last_message, risk_result, lang):
        captured["risk_result"] = risk_result
        return True, None

    monkeypatch.setattr(acceptance_mod, "validate_response_with_context", fake_safety)
    risk = {"level": "medium", "ambiguous_phrases": False}
    risk_snapshot = copy.deepcopy(risk)
    accept_professional_response(
        plan=_reflective_plan(),
        candidate_text="Слышу тебя, это правда тяжело.",
        source_text="Мне тяжело.",
        risk_result=risk,
        lang="ru",
    )
    assert captured["risk_result"] is risk
    assert risk == risk_snapshot


def test_lang_passed_through_unchanged(monkeypatch):
    captured = {}

    def fake_safety(response_text, user_last_message, risk_result, lang):
        captured["lang"] = lang
        return True, None

    monkeypatch.setattr(acceptance_mod, "validate_response_with_context", fake_safety)
    accept_professional_response(
        plan=_reflective_plan(),
        candidate_text="I hear you, that sounds hard.",
        source_text="I feel awful today.",
        risk_result={"level": "low"},
        lang="en",
    )
    assert captured["lang"] == "en"


# -- Malformed public arguments fail before any downstream validator call ---

def _assert_never_calls_fidelity(monkeypatch):
    monkeypatch.setattr(
        acceptance_mod, "validate_response_fidelity",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("validate_response_fidelity must not be called")))


def test_wrong_plan_type_raises_before_downstream(monkeypatch):
    _assert_never_calls_fidelity(monkeypatch)
    with pytest.raises(ValueError):
        accept_professional_response(
            plan="not a plan", candidate_text="text",
            source_text="text", risk_result={}, lang="ru")


def test_wrong_candidate_text_type_raises_before_downstream(monkeypatch):
    _assert_never_calls_fidelity(monkeypatch)
    with pytest.raises(ValueError):
        accept_professional_response(
            plan=_reflective_plan(), candidate_text=123,
            source_text="text", risk_result={}, lang="ru")


def test_empty_candidate_text_raises_before_downstream(monkeypatch):
    _assert_never_calls_fidelity(monkeypatch)
    with pytest.raises(ValueError):
        accept_professional_response(
            plan=_reflective_plan(), candidate_text="",
            source_text="text", risk_result={}, lang="ru")


def test_whitespace_candidate_text_raises_before_downstream(monkeypatch):
    _assert_never_calls_fidelity(monkeypatch)
    with pytest.raises(ValueError):
        accept_professional_response(
            plan=_reflective_plan(), candidate_text="   ",
            source_text="text", risk_result={}, lang="ru")


def test_wrong_source_text_type_raises_before_downstream(monkeypatch):
    _assert_never_calls_fidelity(monkeypatch)
    with pytest.raises(ValueError):
        accept_professional_response(
            plan=_reflective_plan(), candidate_text="text",
            source_text=None, risk_result={}, lang="ru")


def test_empty_source_text_raises_before_downstream(monkeypatch):
    _assert_never_calls_fidelity(monkeypatch)
    with pytest.raises(ValueError):
        accept_professional_response(
            plan=_reflective_plan(), candidate_text="text",
            source_text="", risk_result={}, lang="ru")


def test_wrong_risk_result_type_raises_before_downstream(monkeypatch):
    _assert_never_calls_fidelity(monkeypatch)
    with pytest.raises(ValueError):
        accept_professional_response(
            plan=_reflective_plan(), candidate_text="text",
            source_text="text", risk_result=[], lang="ru")


def test_wrong_lang_type_raises_before_downstream(monkeypatch):
    _assert_never_calls_fidelity(monkeypatch)
    with pytest.raises(ValueError):
        accept_professional_response(
            plan=_reflective_plan(), candidate_text="text",
            source_text="text", risk_result={}, lang=None)


def test_empty_lang_raises_before_downstream(monkeypatch):
    _assert_never_calls_fidelity(monkeypatch)
    with pytest.raises(ValueError):
        accept_professional_response(
            plan=_reflective_plan(), candidate_text="text",
            source_text="text", risk_result={}, lang="")


# -- No Renderer result argument; exact keyword-only public signature -------

def test_public_signature_is_exact_keyword_only():
    sig = inspect.signature(accept_professional_response)
    params = list(sig.parameters.values())
    names = [p.name for p in params]
    assert names == ["plan", "candidate_text", "source_text", "risk_result", "lang"]
    assert "render_result" not in names
    assert "renderer_result" not in names
    for p in params:
        assert p.kind is inspect.Parameter.KEYWORD_ONLY
        assert p.default is inspect.Parameter.empty
    assert sig.return_annotation is not inspect.Signature.empty


# -- Result enum / raw-string invariants -------------------------------------

def test_result_accept_requires_none_reason():
    with pytest.raises(ValueError):
        ProfessionalResponseAcceptanceResult(
            status=ProfessionalResponseAcceptanceStatus.ACCEPT,
            reason=AcceptanceSafetyRejectionReason.SAFETY_REJECTED)


def test_result_reject_requires_a_typed_reason():
    with pytest.raises(ValueError):
        ProfessionalResponseAcceptanceResult(
            status=ProfessionalResponseAcceptanceStatus.REJECT, reason=None)


def test_result_rejects_raw_string_reason():
    with pytest.raises(ValueError):
        ProfessionalResponseAcceptanceResult(
            status=ProfessionalResponseAcceptanceStatus.REJECT, reason="SAFETY_REJECTED")


def test_result_rejects_raw_string_status():
    with pytest.raises(ValueError):
        ProfessionalResponseAcceptanceResult(
            status="REJECT", reason=AcceptanceSafetyRejectionReason.SAFETY_REJECTED)


@pytest.mark.parametrize("reason", [
    FidelityRejectionReason.TOO_MANY_QUESTION_MARKS,
    PolicyRejectionReason.UNPLANNED_ACTION_OR_ADVICE_CUE,
    AcceptanceSafetyRejectionReason.SAFETY_REJECTED,
])
def test_result_accepts_each_of_the_three_reason_enum_classes(reason):
    result = ProfessionalResponseAcceptanceResult(
        status=ProfessionalResponseAcceptanceStatus.REJECT, reason=reason)
    assert result.reason is reason


def test_result_carries_no_text_fields():
    field_names = {f.name for f in dataclasses.fields(ProfessionalResponseAcceptanceResult)}
    assert field_names == {"status", "reason"}


# -- Static architecture: exact frozen import surface ------------------------

_EXPECTED_IMPORTS = frozenset({
    ("future", "__future__", "annotations", None, 0),
    ("from", "dataclasses", "dataclass", None, 0),
    ("from", "enum", "Enum", None, 0),
    ("from", "professional_turn_planner", "ProfessionalTurnPlan", None, 0),
    ("from", "professional_turn_response_fidelity_validator", "FidelityRejectionReason", None, 0),
    ("from", "professional_turn_response_fidelity_validator", "ResponseFidelityStatus", None, 0),
    ("from", "professional_turn_response_fidelity_validator", "validate_response_fidelity", None, 0),
    ("from", "professional_turn_response_policy_validator", "PolicyRejectionReason", None, 0),
    ("from", "professional_turn_response_policy_validator", "PolicyStatus", None, 0),
    ("from", "professional_turn_response_policy_validator", "validate_response_policy", None, 0),
    ("from", "safety_validator", "validate_response_with_context", None, 0),
})


def _module_tree():
    return ast.parse(inspect.getsource(acceptance_mod))


def test_production_module_has_exact_frozen_import_surface():
    tree = _module_tree()
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(("import", alias.name, None, alias.asname, 0))
        elif isinstance(node, ast.ImportFrom):
            kind = "future" if node.module == "__future__" else "from"
            for alias in node.names:
                found.add((kind, node.module, alias.name, alias.asname, node.level or 0))
    assert found == _EXPECTED_IMPORTS


_FORBIDDEN_CALLABLE_NAMES = frozenset({
    "render_turn_response", "govern_turn_plan", "call_turn_plan_proposer",
    "call_turn_analyzer", "produce_turn_analysis", "classify", "select_fallback",
    "get_fallback", "get_safe_fallback_high_risk", "traced_response_builder",
    "persist_influence_trace", "validate_response", "execute", "commit",
    "create", "eval", "exec", "open", "__import__",
})


def test_production_module_contains_no_forbidden_call_sites():
    tree = _module_tree()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)
        assert name not in _FORBIDDEN_CALLABLE_NAMES, f"forbidden call site: {name}"


def test_production_module_has_no_async_defs():
    tree = _module_tree()
    assert not any(isinstance(node, ast.AsyncFunctionDef) for node in ast.walk(tree))


def test_production_module_defines_no_regex():
    source = inspect.getsource(acceptance_mod)
    assert "import re" not in source
    assert "re.compile" not in source


def test_module_docstring_states_ownership_and_non_guarantees():
    doc = acceptance_mod.__doc__ or ""
    for phrase in (
        "NOT owned here",
        "UNPROVEN",
        "does not itself inspect",
    ):
        assert phrase in doc, f"expected docstring to mention: {phrase!r}"
