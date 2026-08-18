"""Tests for professional_turn_ui_immediate_response.py -- Professional Core
Trusted UI Immediate Response V1 (offline control-response adapter).
"""
from __future__ import annotations

import ast
import inspect

import pytest

import professional_turn_ui_context as ui
import professional_turn_ui_immediate_response as ir
from professional_reply_affordances import (
    EntryFollowupFocus,
    EntryTriageCategory,
    ReplyAffordanceAction,
)
from professional_turn_planner import ProfessionalTurnPlan
from therapeutic_domain import (
    ClarificationTarget,
    EvidenceItem,
    EvidenceKind,
    InteractionSignal,
    PrimaryResponseMove,
    ProfessionalObjective,
    RepairConstraint,
)


# ── Helpers ───────────────────────────────────────────────────────────────

def _clarify_plan(target=ClarificationTarget.EVENT):
    return ProfessionalTurnPlan(
        objective=ProfessionalObjective.CLARIFY,
        move=PrimaryResponseMove.FOCUSED_QUESTION,
        clarification_target=target,
        question_allowed=True,
    )


def _clarify_goal_plan():
    return ProfessionalTurnPlan(
        objective=ProfessionalObjective.CLARIFY_GOAL,
        move=PrimaryResponseMove.FOCUSED_QUESTION,
        clarification_target=None,
        question_allowed=True,
    )


def _entry_directive(category):
    result = ui.canonicalize_entry_triage_selection(ui.UntrustedEntryTriageSelection(category))
    assert result.status is ui.EntryTriageSelectionStatus.ACCEPTED
    return result.directive


def _reply_directive(plan, token, action):
    offer = ui.build_reply_affordance_offer_context(token, plan)
    result = ui.canonicalize_reply_affordance_selection(
        offer, ui.UntrustedReplyAffordanceSelection(token, action))
    assert result.status is ui.ReplyAffordanceSelectionStatus.ACCEPTED
    return result.directive


# ── Section 18: entry tests ──────────────────────────────────────────────

_EXPECTED_ENTRY_TEXT_BY_FOCUS = {
    EntryFollowupFocus.RECENT_HIGH_INTENSITY_EPISODE:
        "Вспомни последний момент, когда тревога или напряжение стали особенно сильными. Что тогда происходило?",
    EntryFollowupFocus.RECENT_RELATIONAL_EPISODE:
        "Вспомни недавний момент — в отношениях или когда особенно чувствовалось одиночество. Что произошло?",
    EntryFollowupFocus.RECENT_FUNCTIONING_CHANGE:
        "Что в последнее время стало даваться тяжелее, хотя раньше было проще?",
    EntryFollowupFocus.RECENT_SELF_CRITICISM_EPISODE:
        "Вспомни недавний момент, когда ты особенно сильно себя критиковал. Что тогда случилось?",
    EntryFollowupFocus.DIFFICULT_EMOTION_AND_ANTECEDENT:
        "Какая эмоция сейчас особенно сильная — и что происходило прямо перед ней?",
    EntryFollowupFocus.LOW_PRESSURE_OPENING:
        "Тогда тему выбирать не нужно. Напиши первую мысль, которая сейчас крутится в голове.",
}

_EXPECTED_ENTRY_QMARK_COUNTS = {
    EntryFollowupFocus.RECENT_HIGH_INTENSITY_EPISODE: 1,
    EntryFollowupFocus.RECENT_RELATIONAL_EPISODE: 1,
    EntryFollowupFocus.RECENT_FUNCTIONING_CHANGE: 1,
    EntryFollowupFocus.RECENT_SELF_CRITICISM_EPISODE: 1,
    EntryFollowupFocus.DIFFICULT_EMOTION_AND_ANTECEDENT: 1,
    EntryFollowupFocus.LOW_PRESSURE_OPENING: 0,
}

_ENTRY_CATEGORY_TO_FOCUS = {
    EntryTriageCategory.ANXIETY_STRESS: EntryFollowupFocus.RECENT_HIGH_INTENSITY_EPISODE,
    EntryTriageCategory.RELATIONSHIPS_LONELINESS: EntryFollowupFocus.RECENT_RELATIONAL_EPISODE,
    EntryTriageCategory.LOW_ENERGY_LOW_MOOD: EntryFollowupFocus.RECENT_FUNCTIONING_CHANGE,
    EntryTriageCategory.SELF_ESTEEM_SELF_CRITICISM: EntryFollowupFocus.RECENT_SELF_CRITICISM_EPISODE,
    EntryTriageCategory.DIFFICULT_EMOTIONS: EntryFollowupFocus.DIFFICULT_EMOTION_AND_ANTECEDENT,
    EntryTriageCategory.UNSURE_OR_OTHER: EntryFollowupFocus.LOW_PRESSURE_OPENING,
}


@pytest.mark.parametrize(
    "category,focus", sorted(_ENTRY_CATEGORY_TO_FOCUS.items(), key=lambda kv: kv[0].value))
def test_each_entry_category_produces_exact_sealed_text(category, focus):
    directive = _entry_directive(category)
    assert directive.followup_focus is focus
    response = ir.build_trusted_ui_immediate_response(directive)
    assert response.text_ru == _EXPECTED_ENTRY_TEXT_BY_FOCUS[focus]


@pytest.mark.parametrize("category", list(EntryTriageCategory))
def test_entry_response_source_directive_is_exact_input_object(category):
    directive = _entry_directive(category)
    response = ir.build_trusted_ui_immediate_response(directive)
    assert response.source_directive is directive


@pytest.mark.parametrize(
    "focus,expected_count", sorted(_EXPECTED_ENTRY_QMARK_COUNTS.items(), key=lambda kv: kv[0].value))
def test_entry_response_exact_question_mark_count(focus, expected_count):
    category = next(c for c, f in _ENTRY_CATEGORY_TO_FOCUS.items() if f is focus)
    directive = _entry_directive(category)
    response = ir.build_trusted_ui_immediate_response(directive)
    assert response.text_ru.count("?") == expected_count
    assert "？" not in response.text_ru


@pytest.mark.parametrize("category", list(EntryTriageCategory))
def test_entry_response_constructor_rejects_arbitrary_text(category):
    directive = _entry_directive(category)
    with pytest.raises(ValueError):
        ir.TrustedUiImmediateResponse(source_directive=directive, text_ru="произвольный текст")


def test_full_boundary_pipeline_canonicalize_then_render_for_every_category():
    for category in EntryTriageCategory:
        selection = ui.UntrustedEntryTriageSelection(category)
        canon = ui.canonicalize_entry_triage_selection(selection)
        assert canon.status is ui.EntryTriageSelectionStatus.ACCEPTED
        response = ir.build_trusted_ui_immediate_response(canon.directive)
        assert response.text_ru == _EXPECTED_ENTRY_TEXT_BY_FOCUS[canon.directive.followup_focus]


def test_immediate_response_module_never_accepts_untrusted_transport():
    with pytest.raises(ValueError):
        ir.build_trusted_ui_immediate_response(ui.UntrustedEntryTriageSelection(EntryTriageCategory.ANXIETY_STRESS))
    with pytest.raises(ValueError):
        ir.build_trusted_ui_immediate_response(
            ui.UntrustedReplyAffordanceSelection("t1", ReplyAffordanceAction.NEEDS_SCAFFOLDING))


@pytest.mark.parametrize("bad", ["a string", {"category": "ANXIETY_STRESS"}, None, 123, EntryTriageCategory.ANXIETY_STRESS])
def test_build_response_fails_closed_on_unknown_or_raw_arguments(bad):
    with pytest.raises(ValueError):
        ir.build_trusted_ui_immediate_response(bad)


# ── Section 19: scaffold tests ───────────────────────────────────────────

_EXPECTED_CLARIFY_SCAFFOLD = (
    "Не ищи идеальный ответ. Напиши первую деталь, которая приходит в голову."
)
_EXPECTED_CLARIFY_GOAL_SCAFFOLD = (
    "Не обязательно сразу понимать точную цель. Что хотелось бы изменить "
    "хотя бы немного после этого разговора?"
)


def test_clarify_scaffold_produces_exact_string():
    plan = _clarify_plan()
    directive = _reply_directive(plan, "t1", ReplyAffordanceAction.NEEDS_SCAFFOLDING)
    response = ir.build_trusted_ui_immediate_response(directive)
    assert response.text_ru == _EXPECTED_CLARIFY_SCAFFOLD


def test_clarify_goal_scaffold_produces_exact_string():
    plan = _clarify_goal_plan()
    directive = _reply_directive(plan, "t1", ReplyAffordanceAction.NEEDS_SCAFFOLDING)
    response = ir.build_trusted_ui_immediate_response(directive)
    assert response.text_ru == _EXPECTED_CLARIFY_GOAL_SCAFFOLD


@pytest.mark.parametrize("plan_factory", [_clarify_plan, _clarify_goal_plan])
def test_scaffold_prior_plan_object_not_modified(plan_factory):
    plan = plan_factory()
    directive = _reply_directive(plan, "t1", ReplyAffordanceAction.NEEDS_SCAFFOLDING)
    ir.build_trusted_ui_immediate_response(directive)
    assert directive.prior_plan == plan
    assert directive.prior_plan is plan


@pytest.mark.parametrize("plan_factory", [_clarify_plan, _clarify_goal_plan])
def test_scaffold_source_directive_preserved_exactly(plan_factory):
    plan = plan_factory()
    directive = _reply_directive(plan, "t1", ReplyAffordanceAction.NEEDS_SCAFFOLDING)
    response = ir.build_trusted_ui_immediate_response(directive)
    assert response.source_directive is directive


_EXPECTED_SCAFFOLD_QMARK_COUNTS = {
    ProfessionalObjective.CLARIFY: 0,
    ProfessionalObjective.CLARIFY_GOAL: 1,
}


@pytest.mark.parametrize("plan_factory,objective", [
    (_clarify_plan, ProfessionalObjective.CLARIFY),
    (_clarify_goal_plan, ProfessionalObjective.CLARIFY_GOAL),
])
def test_scaffold_response_exact_question_mark_count(plan_factory, objective):
    directive = _reply_directive(plan_factory(), "t1", ReplyAffordanceAction.NEEDS_SCAFFOLDING)
    response = ir.build_trusted_ui_immediate_response(directive)
    assert response.text_ru.count("?") == _EXPECTED_SCAFFOLD_QMARK_COUNTS[objective]
    assert "？" not in response.text_ru


@pytest.mark.parametrize("plan_factory", [_clarify_plan, _clarify_goal_plan])
def test_scaffold_creates_no_evidence_or_intent(plan_factory):
    directive = _reply_directive(plan_factory(), "t1", ReplyAffordanceAction.NEEDS_SCAFFOLDING)
    response = ir.build_trusted_ui_immediate_response(directive)
    assert not isinstance(response, EvidenceItem)
    assert not hasattr(response, "intent")
    assert not hasattr(response.source_directive, "intent")


@pytest.mark.parametrize("plan_factory", [_clarify_plan, _clarify_goal_plan])
def test_scaffold_creates_no_new_professional_turn_plan(plan_factory):
    plan = plan_factory()
    directive = _reply_directive(plan, "t1", ReplyAffordanceAction.NEEDS_SCAFFOLDING)
    response = ir.build_trusted_ui_immediate_response(directive)
    # Only field on the response holding a plan-shaped value is the
    # preserved prior_plan reachable via source_directive -- prove it is
    # the exact same object, not a newly constructed one.
    assert response.source_directive.prior_plan is plan


# ── Section 20: skip tests ────────────────────────────────────────────────

_EXPECTED_SKIP_RESPONSE = (
    "Этот вопрос пропустим. Можешь просто продолжить с того, что сейчас у "
    "тебя в голове."
)


@pytest.mark.parametrize("plan_factory", [_clarify_plan, _clarify_goal_plan])
def test_skip_produces_exact_same_response_regardless_of_prior_objective(plan_factory):
    directive = _reply_directive(plan_factory(), "t1", ReplyAffordanceAction.SKIP_CURRENT_QUESTION)
    response = ir.build_trusted_ui_immediate_response(directive)
    assert response.text_ru == _EXPECTED_SKIP_RESPONSE


def test_skip_response_has_no_question_mark():
    directive = _reply_directive(_clarify_plan(), "t1", ReplyAffordanceAction.SKIP_CURRENT_QUESTION)
    response = ir.build_trusted_ui_immediate_response(directive)
    assert "?" not in response.text_ru
    assert "？" not in response.text_ru


def test_skip_response_does_not_ask_why():
    directive = _reply_directive(_clarify_plan(), "t1", ReplyAffordanceAction.SKIP_CURRENT_QUESTION)
    response = ir.build_trusted_ui_immediate_response(directive)
    assert "почему" not in response.text_ru.lower()


def test_skip_prior_plan_unchanged():
    plan = _clarify_plan()
    directive = _reply_directive(plan, "t1", ReplyAffordanceAction.SKIP_CURRENT_QUESTION)
    ir.build_trusted_ui_immediate_response(directive)
    assert directive.prior_plan is plan


def test_skip_creates_no_new_plan():
    plan = _clarify_plan()
    directive = _reply_directive(plan, "t1", ReplyAffordanceAction.SKIP_CURRENT_QUESTION)
    response = ir.build_trusted_ui_immediate_response(directive)
    assert response.source_directive.prior_plan is plan


def test_skip_produces_no_interaction_signal_or_repair_constraint():
    directive = _reply_directive(_clarify_plan(), "t1", ReplyAffordanceAction.SKIP_CURRENT_QUESTION)
    response = ir.build_trusted_ui_immediate_response(directive)
    assert not isinstance(response, InteractionSignal)
    assert not isinstance(response, RepairConstraint)
    assert not isinstance(response.source_directive, InteractionSignal)
    assert not isinstance(response.source_directive, RepairConstraint)
    for field_name in ir.TrustedUiImmediateResponse.__dataclass_fields__:
        value = getattr(response, field_name)
        assert not isinstance(value, InteractionSignal)
        assert not isinstance(value, RepairConstraint)


def test_skip_specifically_not_no_questions_or_question_overload():
    directive = _reply_directive(_clarify_plan(), "t1", ReplyAffordanceAction.SKIP_CURRENT_QUESTION)
    response = ir.build_trusted_ui_immediate_response(directive)
    assert response.source_directive.kind != InteractionSignal.NO_QUESTIONS
    assert response.source_directive.kind != RepairConstraint.QUESTION_OVERLOAD


# ── Section 21: public constructor closedness ────────────────────────────

def test_constructor_rejects_arbitrary_text_for_valid_entry_directive():
    directive = _entry_directive(EntryTriageCategory.ANXIETY_STRESS)
    with pytest.raises(ValueError):
        ir.TrustedUiImmediateResponse(source_directive=directive, text_ru="что угодно")


def test_constructor_rejects_another_valid_entry_text_for_wrong_directive():
    directive = _entry_directive(EntryTriageCategory.ANXIETY_STRESS)
    wrong_text = _EXPECTED_ENTRY_TEXT_BY_FOCUS[EntryFollowupFocus.LOW_PRESSURE_OPENING]
    with pytest.raises(ValueError):
        ir.TrustedUiImmediateResponse(source_directive=directive, text_ru=wrong_text)


def test_constructor_rejects_scaffold_text_paired_with_skip_directive():
    directive = _reply_directive(_clarify_plan(), "t1", ReplyAffordanceAction.SKIP_CURRENT_QUESTION)
    with pytest.raises(ValueError):
        ir.TrustedUiImmediateResponse(source_directive=directive, text_ru=_EXPECTED_CLARIFY_SCAFFOLD)


def test_constructor_rejects_skip_text_paired_with_scaffold_directive():
    directive = _reply_directive(_clarify_plan(), "t1", ReplyAffordanceAction.NEEDS_SCAFFOLDING)
    with pytest.raises(ValueError):
        ir.TrustedUiImmediateResponse(source_directive=directive, text_ru=_EXPECTED_SKIP_RESPONSE)


def test_constructor_rejects_plain_string_instead_of_directive():
    with pytest.raises(ValueError):
        ir.TrustedUiImmediateResponse(source_directive="not a directive", text_ru=_EXPECTED_SKIP_RESPONSE)


def test_constructor_rejects_untrusted_transport_object():
    with pytest.raises(ValueError):
        ir.TrustedUiImmediateResponse(
            source_directive=ui.UntrustedEntryTriageSelection(EntryTriageCategory.ANXIETY_STRESS),
            text_ru=_EXPECTED_ENTRY_TEXT_BY_FOCUS[EntryFollowupFocus.RECENT_HIGH_INTENSITY_EPISODE],
        )


def test_constructor_rejects_dict():
    with pytest.raises(ValueError):
        ir.TrustedUiImmediateResponse(source_directive={"category": "ANXIETY_STRESS"}, text_ru=_EXPECTED_SKIP_RESPONSE)


def test_constructor_rejects_none():
    with pytest.raises(ValueError):
        ir.TrustedUiImmediateResponse(source_directive=None, text_ru=_EXPECTED_SKIP_RESPONSE)


def test_constructor_rejects_non_string_text():
    directive = _entry_directive(EntryTriageCategory.ANXIETY_STRESS)
    with pytest.raises(ValueError):
        ir.TrustedUiImmediateResponse(source_directive=directive, text_ru=123)


def test_constructor_rejects_empty_text():
    directive = _entry_directive(EntryTriageCategory.ANXIETY_STRESS)
    with pytest.raises(ValueError):
        ir.TrustedUiImmediateResponse(source_directive=directive, text_ru="")


def test_constructor_rejects_clarify_scaffold_text_paired_with_clarify_goal_directive():
    directive = _reply_directive(_clarify_goal_plan(), "t1", ReplyAffordanceAction.NEEDS_SCAFFOLDING)
    with pytest.raises(ValueError):
        ir.TrustedUiImmediateResponse(source_directive=directive, text_ru=_EXPECTED_CLARIFY_SCAFFOLD)


def test_constructor_rejects_clarify_goal_scaffold_text_paired_with_clarify_directive():
    directive = _reply_directive(_clarify_plan(), "t1", ReplyAffordanceAction.NEEDS_SCAFFOLDING)
    with pytest.raises(ValueError):
        ir.TrustedUiImmediateResponse(source_directive=directive, text_ru=_EXPECTED_CLARIFY_GOAL_SCAFFOLD)


def test_constructor_rejects_arbitrary_question_text_despite_removed_global_ban():
    # Removing the module-wide zero-question-mark guard must not open a hole
    # allowing a caller to supply their own question -- the exact-text
    # equality check is the only thing gating question shape now.
    directive = _entry_directive(EntryTriageCategory.ANXIETY_STRESS)
    with pytest.raises(ValueError):
        ir.TrustedUiImmediateResponse(source_directive=directive, text_ru="Произвольный вопрос?")


@pytest.mark.parametrize("focus", [
    EntryFollowupFocus.RECENT_HIGH_INTENSITY_EPISODE,
    EntryFollowupFocus.RECENT_FUNCTIONING_CHANGE,
])
def test_question_bearing_entry_response_constructs_successfully(focus):
    category = next(c for c, f in _ENTRY_CATEGORY_TO_FOCUS.items() if f is focus)
    directive = _entry_directive(category)
    response = ir.build_trusted_ui_immediate_response(directive)
    assert response.text_ru == _EXPECTED_ENTRY_TEXT_BY_FOCUS[focus]
    assert response.text_ru.count("?") == 1


def test_question_bearing_clarify_goal_scaffold_constructs_successfully():
    directive = _reply_directive(_clarify_goal_plan(), "t1", ReplyAffordanceAction.NEEDS_SCAFFOLDING)
    response = ir.build_trusted_ui_immediate_response(directive)
    assert response.text_ru == _EXPECTED_CLARIFY_GOAL_SCAFFOLD
    assert response.text_ru.count("?") == 1


def test_total_question_mark_counts_across_all_nine_sealed_responses():
    responses = [ir.build_trusted_ui_immediate_response(_entry_directive(c)).text_ru
                 for c in EntryTriageCategory]
    responses.append(ir.build_trusted_ui_immediate_response(
        _reply_directive(_clarify_plan(), "t1", ReplyAffordanceAction.NEEDS_SCAFFOLDING)).text_ru)
    responses.append(ir.build_trusted_ui_immediate_response(
        _reply_directive(_clarify_goal_plan(), "t2", ReplyAffordanceAction.NEEDS_SCAFFOLDING)).text_ru)
    responses.append(ir.build_trusted_ui_immediate_response(
        _reply_directive(_clarify_plan(), "t3", ReplyAffordanceAction.SKIP_CURRENT_QUESTION)).text_ru)
    assert len(responses) == 9
    total_ascii = sum(text.count("?") for text in responses)
    total_fullwidth = sum(text.count("？") for text in responses)
    assert total_ascii == 6
    assert total_fullwidth == 0


def test_dataclass_fields_are_exactly_source_directive_and_text_ru():
    fields = set(ir.TrustedUiImmediateResponse.__dataclass_fields__)
    assert fields == {"source_directive", "text_ru"}


def test_trusted_response_is_frozen():
    assert ir.TrustedUiImmediateResponse.__dataclass_params__.frozen is True


def test_expected_response_text_fails_closed_on_non_directive_input():
    # The other private-helper branches (unknown TrustedReplyDirectiveKind,
    # unknown prior_plan.objective under SCAFFOLD_CURRENT_TARGET) are
    # genuinely unreachable through the public API: TrustedReplyDirective's
    # own constructor already rejects any kind other than the sealed
    # action->kind pair, and Planner V1 only ever offers FOCUSED_QUESTION
    # (a prerequisite for SCAFFOLD_CURRENT_TARGET) under CLARIFY or
    # CLARIFY_GOAL. Only the outer type-rejection branch is reachable and
    # honestly testable here.
    class _NotADirective:
        pass

    with pytest.raises(ValueError):
        ir._expected_response_text(_NotADirective())


# ── Section 22: static import / purity ────────────────────────────────────

_EXPECTED_IMPORTS = frozenset({
    ("future", "__future__", "annotations", None, 0),
    ("from", "dataclasses", "dataclass", None, 0),
    ("from", "professional_reply_affordances", "EntryFollowupFocus", None, 0),
    ("from", "professional_turn_ui_context", "TrustedEntryTriageDirective", None, 0),
    ("from", "professional_turn_ui_context", "TrustedReplyDirective", None, 0),
    ("from", "professional_turn_ui_context", "TrustedReplyDirectiveKind", None, 0),
    ("from", "therapeutic_domain", "ProfessionalObjective", None, 0),
})


def _module_tree():
    return ast.parse(inspect.getsource(ir))


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


_FORBIDDEN_IMPORT_MODULES = frozenset({
    "openai", "anthropic", "requests", "httpx", "aiohttp", "socket",
    "aiogram", "telegram", "sqlite3", "aiosqlite",
    "database", "bot", "conversation_controller",
    "professional_turn_analyzer", "professional_turn_plan_proposer",
    "professional_turn_response_renderer", "professional_turn_response_acceptance",
    "os", "time", "random",
})


def test_production_module_has_no_forbidden_imports():
    """AST-based, not a raw substring scan -- this module's own docstring
    names related concepts (e.g. "Telegram", "database") in prose, which a
    substring scan would misfire on."""
    tree = _module_tree()
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
    assert not (imported_roots & _FORBIDDEN_IMPORT_MODULES)


def test_production_module_has_no_environment_or_secret_access():
    src = inspect.getsource(ir)
    for token in ("os.environ", "getenv", "dotenv", ".env"):
        assert token not in src


def test_production_module_has_no_async_defs():
    tree = _module_tree()
    assert not any(isinstance(node, ast.AsyncFunctionDef) for node in ast.walk(tree))


_LATENT_SOURCE_SUBSTRINGS = (
    "get_profile", "compute_profile", "format_profile_for_user", "psychology_profile",
    "pattern_hypothes",
    "questionnaire_score",
    "confirmed_episode", "pattern_confirmation",
    "schema_theme",
    "get_active_mode", "get_mode_profile", "get_schema_modes",
    "formulation",
)


def test_production_module_has_zero_clinical_boundary_latent_source_substrings():
    src = inspect.getsource(ir)
    offenders = [s for s in _LATENT_SOURCE_SUBSTRINGS if s in src]
    assert offenders == [], f"forbidden latent-source substrings present: {offenders}"


# ── Section 23: static anti-synthesis test ────────────────────────────────

_FORBIDDEN_CONSTRUCTED_TYPES = frozenset({
    "TurnAnalysisResult", "EvidenceItem", "InteractionRequest",
    "UntrustedTurnPlanProposal", "ProfessionalTurnPlan",
})
_FORBIDDEN_CALLABLE_NAMES = frozenset({
    "govern_turn_plan", "render_turn_response", "accept_professional_response",
})


def test_production_module_never_constructs_forbidden_types_or_calls_forbidden_functions():
    tree = _module_tree()
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None)
            if name in _FORBIDDEN_CONSTRUCTED_TYPES or name in _FORBIDDEN_CALLABLE_NAMES:
                offenders.append(name)
    assert offenders == [], f"forbidden construction/call sites: {offenders}"


def test_no_user_text_synthesis_api_exists():
    public_names = [n for n in dir(ir) if not n.startswith("_")]
    suspicious = (
        "user_text", "as_message", "as_user", "quote", "synthetic",
        "fake_message", "category_to_text", "action_to_text",
    )
    offenders = [n for n in public_names if any(t in n.lower() for t in suspicious)]
    assert offenders == []


# ── Documentation boundary tests ─────────────────────────────────────────

def test_docstring_states_offline_no_runtime_wiring():
    doc = (ir.__doc__ or "").lower()
    for phrase in ("offline domain/governance", "no runtime wiring in this slice"):
        assert phrase in doc, f"expected docstring to mention: {phrase!r}"


def test_docstring_states_response_path_boundary():
    doc = ir.__doc__ or ""
    for phrase in (
        "RESPONSE-PATH BOUNDARY",
        "never calls govern_turn_plan, render_turn_response, or",
    ):
        assert phrase in doc, f"expected docstring to mention: {phrase!r}"


def test_docstring_states_owner_directed_sealed_copy():
    doc = ir.__doc__ or ""
    for phrase in (
        "OWNER-DIRECTED SEALED COPY",
        "Review workflow state is intentionally not encoded here.",
    ):
        assert phrase in doc, f"expected docstring to mention: {phrase!r}"
    assert "DRAFT COPY, NOT YET OWNER-APPROVED" not in doc
    assert "zero question marks" not in doc


def test_docstring_states_question_shape_contract():
    doc = ir.__doc__ or ""
    for phrase in (
        "QUESTION SHAPE CONTRACT",
        "belongs to the exact sealed copy",
    ):
        assert phrase in doc, f"expected docstring to mention: {phrase!r}"
