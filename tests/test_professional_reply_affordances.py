"""Tests for professional_reply_affordances.py -- Professional Core V2 Reply
Affordances + Entry Triage V1 (offline UX contract).

Exact user-facing strings (the entry prompt, the six category labels) are
independently frozen here, never constructed from the production module's
own constants -- a production drift must fail this test file, not silently
agree with itself.
"""
from __future__ import annotations

import ast
import inspect

import pytest

import professional_reply_affordances as m
from professional_turn_planner import ProfessionalTurnPlan
from therapeutic_domain import (
    ClarificationTarget,
    ConsentState,
    EvidenceKind,
    InteractionSignal,
    MemoryCategory,
    PrimaryResponseMove,
    ProfessionalObjective,
)


# ── A/B/C: Entry Triage category set, order, and exact Russian labels ───────

def test_entry_triage_has_exactly_six_categories():
    assert len(list(m.EntryTriageCategory)) == 6


_EXPECTED_CATEGORY_ORDER = (
    m.EntryTriageCategory.ANXIETY_STRESS,
    m.EntryTriageCategory.RELATIONSHIPS_LONELINESS,
    m.EntryTriageCategory.LOW_ENERGY_LOW_MOOD,
    m.EntryTriageCategory.SELF_ESTEEM_SELF_CRITICISM,
    m.EntryTriageCategory.DIFFICULT_EMOTIONS,
    m.EntryTriageCategory.UNSURE_OR_OTHER,
)


def test_entry_triage_exact_category_order():
    assert tuple(o.category for o in m.ENTRY_TRIAGE_CONTRACT_V1.options) == _EXPECTED_CATEGORY_ORDER


_EXPECTED_RU_LABELS_IN_ORDER = (
    "Тревога / стресс",
    "Отношения / одиночество",
    "Нет сил / подавленность",
    "Самооценка / самокритика",
    "Сильные эмоции",
    "Не знаю / другое",
)


def test_entry_triage_exact_russian_labels_in_order():
    actual = tuple(o.label_ru for o in m.ENTRY_TRIAGE_CONTRACT_V1.options)
    assert actual == _EXPECTED_RU_LABELS_IN_ORDER


# ── D/E: exact owner-approved entry prompt, independently frozen ────────────

_EXPECTED_ENTRY_PROMPT_RU = (
    "С чего тебе было бы легче начать?\n"
    "Выбери то, что сейчас ближе, или напиши как получается.\n"
    "Не обязательно сразу всё понимать и объяснять."
)


def test_entry_prompt_matches_owner_approved_wording_exactly():
    assert m.ENTRY_TRIAGE_CONTRACT_V1.prompt_ru == _EXPECTED_ENTRY_PROMPT_RU


def test_entry_prompt_is_not_the_previous_wording():
    previous = "С чем сейчас тяжелее всего?"
    assert previous not in m.ENTRY_TRIAGE_CONTRACT_V1.prompt_ru


# ── F: free text is always allowed ───────────────────────────────────────────

def test_free_text_always_allowed():
    assert m.ENTRY_TRIAGE_CONTRACT_V1.free_text_allowed is True


def test_entry_triage_contract_rejects_free_text_allowed_false():
    with pytest.raises(ValueError):
        m.EntryTriageContract(
            prompt_ru="x", options=m.ENTRY_TRIAGE_CONTRACT_V1.options, free_text_allowed=False)


# ── G: category -> follow-up-focus matrix, independently frozen ─────────────

_EXPECTED_FOLLOWUP_MATRIX = {
    m.EntryTriageCategory.ANXIETY_STRESS: m.EntryFollowupFocus.RECENT_HIGH_INTENSITY_EPISODE,
    m.EntryTriageCategory.RELATIONSHIPS_LONELINESS: m.EntryFollowupFocus.RECENT_RELATIONAL_EPISODE,
    m.EntryTriageCategory.LOW_ENERGY_LOW_MOOD: m.EntryFollowupFocus.RECENT_FUNCTIONING_CHANGE,
    m.EntryTriageCategory.SELF_ESTEEM_SELF_CRITICISM: m.EntryFollowupFocus.RECENT_SELF_CRITICISM_EPISODE,
    m.EntryTriageCategory.DIFFICULT_EMOTIONS: m.EntryFollowupFocus.DIFFICULT_EMOTION_AND_ANTECEDENT,
    m.EntryTriageCategory.UNSURE_OR_OTHER: m.EntryFollowupFocus.LOW_PRESSURE_OPENING,
}


def test_every_category_maps_to_exactly_one_followup_focus():
    assert set(_EXPECTED_FOLLOWUP_MATRIX) == set(m.EntryTriageCategory)
    for category in m.EntryTriageCategory:
        focus = m.followup_focus_for_category(category)
        assert isinstance(focus, m.EntryFollowupFocus)


@pytest.mark.parametrize(
    "category,expected_focus",
    sorted(_EXPECTED_FOLLOWUP_MATRIX.items(), key=lambda kv: kv[0].value))
def test_followup_focus_matrix_exact(category, expected_focus):
    assert m.followup_focus_for_category(category) is expected_focus


def test_followup_focus_lookup_fails_closed_on_non_member():
    with pytest.raises(ValueError):
        m.followup_focus_for_category("ANXIETY_STRESS")
    with pytest.raises(ValueError):
        m.followup_focus_for_category(None)


# ── H: no EntryTriageCategory -> Intent conversion API exists ───────────────

def test_module_does_not_import_intent():
    tree = ast.parse(inspect.getsource(m))
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported_names.update(alias.name for alias in node.names)
    assert "Intent" not in imported_names


def test_no_public_intent_conversion_function_exists():
    public_names = [name for name in dir(m) if not name.startswith("_")]
    offenders = [name for name in public_names if "intent" in name.lower()]
    assert offenders == []


# ── I: entry categories are not diagnosis/consent/evidence values ───────────

def test_entry_categories_are_not_consent_or_evidence_or_memory_category_values():
    for category in m.EntryTriageCategory:
        assert not isinstance(category, ConsentState)
        assert not isinstance(category, EvidenceKind)
        assert not isinstance(category, MemoryCategory)
        assert category.value not in {c.value for c in ConsentState}
        assert category.value not in {e.value for e in EvidenceKind}


# ── J/K/L/M/N/O: deterministic reply-affordance availability rule ───────────

_EXPECTED_FOCUSED_QUESTION_ACTIONS = (
    m.ReplyAffordanceAction.NEEDS_SCAFFOLDING,
    m.ReplyAffordanceAction.SKIP_CURRENT_QUESTION,
)


def test_focused_question_with_question_allowed_exposes_exactly_two_in_order():
    plan = ProfessionalTurnPlan(
        objective=ProfessionalObjective.CLARIFY,
        move=PrimaryResponseMove.FOCUSED_QUESTION,
        clarification_target=ClarificationTarget.EVENT,
        question_allowed=True,
    )
    result = m.derive_reply_affordances(plan)
    assert result.actions == _EXPECTED_FOCUSED_QUESTION_ACTIONS


@pytest.mark.parametrize("target", list(ClarificationTarget))
def test_every_clarify_target_focused_question_receives_both_affordances(target):
    plan = ProfessionalTurnPlan(
        objective=ProfessionalObjective.CLARIFY,
        move=PrimaryResponseMove.FOCUSED_QUESTION,
        clarification_target=target,
        question_allowed=True,
    )
    assert m.derive_reply_affordances(plan).actions == _EXPECTED_FOCUSED_QUESTION_ACTIONS


def test_clarify_goal_focused_question_receives_both_affordances():
    plan = ProfessionalTurnPlan(
        objective=ProfessionalObjective.CLARIFY_GOAL,
        move=PrimaryResponseMove.FOCUSED_QUESTION,
        clarification_target=None,
        question_allowed=True,
    )
    assert m.derive_reply_affordances(plan).actions == _EXPECTED_FOCUSED_QUESTION_ACTIONS


def test_establish_contact_open_invitation_exposes_none():
    plan = ProfessionalTurnPlan(
        objective=ProfessionalObjective.ESTABLISH_CONTACT,
        move=PrimaryResponseMove.OPEN_INVITATION,
        clarification_target=None,
        question_allowed=False,
    )
    assert m.derive_reply_affordances(plan).actions == ()


def test_repair_reflective_statement_exposes_none():
    plan = ProfessionalTurnPlan(
        objective=ProfessionalObjective.REPAIR,
        move=PrimaryResponseMove.REFLECTIVE_STATEMENT,
        clarification_target=None,
        question_allowed=False,
    )
    assert m.derive_reply_affordances(plan).actions == ()


def test_repair_open_invitation_exposes_none():
    plan = ProfessionalTurnPlan(
        objective=ProfessionalObjective.REPAIR,
        move=PrimaryResponseMove.OPEN_INVITATION,
        clarification_target=None,
        question_allowed=False,
    )
    assert m.derive_reply_affordances(plan).actions == ()


def test_close_closing_exposes_none():
    plan = ProfessionalTurnPlan(
        objective=ProfessionalObjective.CLOSE,
        move=PrimaryResponseMove.CLOSING,
        clarification_target=None,
        question_allowed=False,
    )
    assert m.derive_reply_affordances(plan).actions == ()


def test_derive_reply_affordances_fails_closed_on_non_plan_input():
    with pytest.raises(ValueError):
        m.derive_reply_affordances("not a plan")
    with pytest.raises(ValueError):
        m.derive_reply_affordances(None)


# ── P/Q: exact structured meaning of each action ─────────────────────────────

def test_needs_scaffolding_meaning():
    meaning = m.meaning_for_action(m.ReplyAffordanceAction.NEEDS_SCAFFOLDING)
    assert meaning.conversation_continues is True
    assert meaning.declines_current_question is False
    assert meaning.declines_whole_conversation is False
    assert meaning.requests_scaffolding is True
    assert meaning.produces_substantive_answer is False


def test_skip_current_question_meaning():
    meaning = m.meaning_for_action(m.ReplyAffordanceAction.SKIP_CURRENT_QUESTION)
    assert meaning.conversation_continues is True
    assert meaning.declines_current_question is True
    assert meaning.requests_scaffolding is False
    assert meaning.declines_whole_conversation is False


def test_meaning_for_action_fails_closed_on_non_member():
    with pytest.raises(ValueError):
        m.meaning_for_action("NEEDS_SCAFFOLDING")
    with pytest.raises(ValueError):
        m.meaning_for_action(None)


# ── R: neither action equals or converts to InteractionSignal.NO_QUESTIONS ──

def test_neither_action_equals_no_questions_signal():
    assert m.ReplyAffordanceAction.NEEDS_SCAFFOLDING != InteractionSignal.NO_QUESTIONS
    assert m.ReplyAffordanceAction.SKIP_CURRENT_QUESTION != InteractionSignal.NO_QUESTIONS
    assert not isinstance(m.ReplyAffordanceAction.NEEDS_SCAFFOLDING, InteractionSignal)
    assert not isinstance(m.ReplyAffordanceAction.SKIP_CURRENT_QUESTION, InteractionSignal)
    for action in m.ReplyAffordanceAction:
        assert m.meaning_for_action(action).equals_no_questions_signal is False


# ── S: neither action becomes evidence ───────────────────────────────────────

def test_neither_action_becomes_evidence():
    for action in m.ReplyAffordanceAction:
        assert m.meaning_for_action(action).becomes_evidence is False


# ── T: no entry category synthesizes fake user text ──────────────────────────

def test_no_user_text_synthesis_function_exists():
    public_names = [name for name in dir(m) if not name.startswith("_")]
    suspicious_terms = ("user_text", "as_message", "as_user", "quote", "synthesize", "fabricate")
    offenders = [
        name for name in public_names
        if any(term in name.lower() for term in suspicious_terms)]
    assert offenders == []


def test_docstring_states_entry_selection_provenance_boundary():
    doc = m.__doc__ or ""
    for phrase in (
        "USER_FREE_TEXT",
        "ENTRY_TRIAGE_SELECTION",
        "must never be converted into",
        "fabricated quoted or free-text user speech",
    ):
        assert phrase in doc, f"expected docstring to mention: {phrase!r}"


# ── U: invalid enum/value construction fails closed ──────────────────────────

def test_entry_triage_category_fails_closed_on_unknown_value():
    with pytest.raises(ValueError):
        m.EntryTriageCategory("NOT_A_REAL_CATEGORY")


def test_reply_affordance_action_fails_closed_on_unknown_value():
    with pytest.raises(ValueError):
        m.ReplyAffordanceAction("NOT_A_REAL_ACTION")


def test_entry_triage_option_fails_closed_on_bad_category():
    with pytest.raises(ValueError):
        m.EntryTriageOption(category="ANXIETY_STRESS", label_ru="x")


def test_entry_triage_option_fails_closed_on_empty_label():
    with pytest.raises(ValueError):
        m.EntryTriageOption(category=m.EntryTriageCategory.ANXIETY_STRESS, label_ru="")


def test_reply_affordance_option_fails_closed_on_bad_action():
    with pytest.raises(ValueError):
        m.ReplyAffordanceOption(action="NEEDS_SCAFFOLDING", label_ru="x")


def test_entry_triage_contract_fails_closed_on_incomplete_options():
    incomplete = tuple(o for o in m.ENTRY_TRIAGE_CONTRACT_V1.options[:-1])
    with pytest.raises(ValueError):
        m.EntryTriageContract(prompt_ru="x", options=incomplete, free_text_allowed=True)


def test_reply_affordance_plan_fails_closed_on_bad_option_type():
    with pytest.raises(ValueError):
        m.ReplyAffordancePlan(options=("not an option",))


# ── V: returned collections are immutable/caller-nonmutable ─────────────────

def test_entry_triage_contract_options_is_a_tuple():
    assert isinstance(m.ENTRY_TRIAGE_CONTRACT_V1.options, tuple)


def test_reply_affordance_plan_actions_is_a_tuple():
    plan = ProfessionalTurnPlan(
        objective=ProfessionalObjective.CLARIFY_GOAL,
        move=PrimaryResponseMove.FOCUSED_QUESTION,
        clarification_target=None,
        question_allowed=True,
    )
    result = m.derive_reply_affordances(plan)
    assert isinstance(result.actions, tuple)
    assert isinstance(result.options, tuple)


def test_dataclasses_are_frozen():
    option = m.ENTRY_TRIAGE_CONTRACT_V1.options[0]
    with pytest.raises(Exception):
        option.label_ru = "mutated"
    with pytest.raises(Exception):
        m.ENTRY_TRIAGE_CONTRACT_V1.free_text_allowed = False


# ── W/X/Y: static architecture guards ────────────────────────────────────────

_EXPECTED_IMPORTS = frozenset({
    ("future", "__future__", "annotations", None, 0),
    ("from", "dataclasses", "dataclass", None, 0),
    ("from", "enum", "Enum", None, 0),
    ("from", "professional_turn_planner", "ProfessionalTurnPlan", None, 0),
    ("from", "therapeutic_domain", "PrimaryResponseMove", None, 0),
})


def _module_tree():
    return ast.parse(inspect.getsource(m))


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
    "openai", "anthropic", "httpx", "aiohttp", "aiogram", "requests",
    "socket", "urllib", "os", "sqlite3", "aiosqlite",
    "database", "bot", "conversation_controller", "traced_response",
    "risk_detector", "dashboard",
})


def test_production_module_has_no_network_model_db_telegram_or_runtime_imports():
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


def test_production_module_has_no_async_defs():
    tree = _module_tree()
    assert not any(isinstance(node, ast.AsyncFunctionDef) for node in ast.walk(tree))


def test_production_module_has_no_environment_or_secret_access():
    src = inspect.getsource(m)
    for token in ("os.environ", "getenv", "os.environ["):
        assert token not in src


# Same registry as tests/test_clinical_boundary.py's LATENT_SOURCE_SYMBOLS
# (deliberately duplicated here rather than imported -- tests/ has no
# __init__.py, and this slice must not modify test_clinical_boundary.py).
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
    src = inspect.getsource(m)
    offenders = [s for s in _LATENT_SOURCE_SUBSTRINGS if s in src]
    assert offenders == [], f"forbidden latent-source substrings present: {offenders}"


# ── Z: documentation does not claim Entry Triage is the permanent depth ceiling

def test_docstring_states_entry_triage_is_not_the_depth_ceiling():
    doc = m.__doc__ or ""
    for phrase in (
        "NOT the final depth",
        "working understanding",
        "governed method",
    ):
        assert phrase in doc, f"expected docstring to mention: {phrase!r}"


def test_docstring_states_turn_scoping_requirement():
    doc = m.__doc__ or ""
    for phrase in (
        "TURN-SCOPING REQUIREMENT",
        "must be bound to the exact assistant",
    ):
        assert phrase in doc, f"expected docstring to mention: {phrase!r}"


def test_docstring_states_why_both_affordances_exist():
    doc = m.__doc__ or ""
    for phrase in (
        "accessibility/autonomy affordances, not",
        "answer-content buttons",
        "by itself, turn the conversation into a multiple-choice questionnaire",
    ):
        assert phrase in doc, f"expected docstring to mention: {phrase!r}"


def test_docstring_states_offline_no_runtime_wiring():
    doc = (m.__doc__ or "").lower()
    for phrase in (
        "offline domain/governance",
        "no runtime wiring in this slice",
        "imported by bot.py",
    ):
        assert phrase in doc, f"expected docstring to mention: {phrase!r}"


# ── Independent freeze of the mid-conversation Russian labels ───────────────
# (owner exact-review finding: "Пропустить" previously appeared nowhere in
# this test file, so a production label drift would not have been caught.)

def test_focused_question_reply_affordance_ru_labels_frozen_independently():
    plan = ProfessionalTurnPlan(
        objective=ProfessionalObjective.CLARIFY_GOAL,
        move=PrimaryResponseMove.FOCUSED_QUESTION,
        clarification_target=None,
        question_allowed=True,
    )
    result = m.derive_reply_affordances(plan)
    labels_by_action = {option.action: option.label_ru for option in result.options}
    assert labels_by_action[m.ReplyAffordanceAction.NEEDS_SCAFFOLDING] == "Не знаю"
    assert labels_by_action[m.ReplyAffordanceAction.SKIP_CURRENT_QUESTION] == "Пропустить"


# ── Negative trust-boundary tests (owner exact-review hardening pass) ───────
# Each proves the public constructor now REJECTS a previously-accepted
# arbitrary/contradictory state -- not merely that the module's own
# global singletons happen to be correct.

def test_entry_triage_option_rejects_arbitrary_wrong_label_for_valid_category():
    with pytest.raises(ValueError):
        m.EntryTriageOption(m.EntryTriageCategory.ANXIETY_STRESS, "arbitrary text")


def test_entry_triage_contract_rejects_arbitrary_prompt():
    with pytest.raises(ValueError):
        m.EntryTriageContract(
            prompt_ru="arbitrary prompt text",
            options=m.ENTRY_TRIAGE_CONTRACT_V1.options,
            free_text_allowed=True,
        )


def test_entry_triage_contract_rejects_six_valid_options_plus_a_duplicate():
    duplicated = m.ENTRY_TRIAGE_CONTRACT_V1.options + (m.ENTRY_TRIAGE_CONTRACT_V1.options[0],)
    with pytest.raises(ValueError):
        m.EntryTriageContract(
            prompt_ru=m.ENTRY_TRIAGE_CONTRACT_V1.prompt_ru,
            options=duplicated,
            free_text_allowed=True,
        )


def test_entry_triage_contract_rejects_correct_six_options_in_wrong_order():
    reversed_options = tuple(reversed(m.ENTRY_TRIAGE_CONTRACT_V1.options))
    assert reversed_options != m.ENTRY_TRIAGE_CONTRACT_V1.options  # sanity: actually reordered
    with pytest.raises(ValueError):
        m.EntryTriageContract(
            prompt_ru=m.ENTRY_TRIAGE_CONTRACT_V1.prompt_ru,
            options=reversed_options,
            free_text_allowed=True,
        )


def test_reply_affordance_option_rejects_arbitrary_wrong_label_for_valid_action():
    with pytest.raises(ValueError):
        m.ReplyAffordanceOption(m.ReplyAffordanceAction.NEEDS_SCAFFOLDING, "arbitrary text")
    with pytest.raises(ValueError):
        m.ReplyAffordanceOption(m.ReplyAffordanceAction.SKIP_CURRENT_QUESTION, "arbitrary text")


def _needs_scaffolding_option():
    return m.ReplyAffordanceOption(m.ReplyAffordanceAction.NEEDS_SCAFFOLDING, "Не знаю")


def _skip_current_question_option():
    return m.ReplyAffordanceOption(m.ReplyAffordanceAction.SKIP_CURRENT_QUESTION, "Пропустить")


def test_reply_affordance_plan_rejects_only_needs_scaffolding():
    with pytest.raises(ValueError):
        m.ReplyAffordancePlan(options=(_needs_scaffolding_option(),))


def test_reply_affordance_plan_rejects_only_skip_current_question():
    with pytest.raises(ValueError):
        m.ReplyAffordancePlan(options=(_skip_current_question_option(),))


def test_reply_affordance_plan_rejects_reversed_order():
    with pytest.raises(ValueError):
        m.ReplyAffordancePlan(options=(_skip_current_question_option(), _needs_scaffolding_option()))


def test_reply_affordance_plan_rejects_duplicate_option():
    with pytest.raises(ValueError):
        m.ReplyAffordancePlan(options=(_needs_scaffolding_option(), _needs_scaffolding_option()))


def test_reply_affordance_meaning_rejects_contradictory_needs_scaffolding_semantics():
    with pytest.raises(ValueError):
        m.ReplyAffordanceMeaning(
            action=m.ReplyAffordanceAction.NEEDS_SCAFFOLDING,
            conversation_continues=True,
            declines_current_question=True,  # contradicts sealed NEEDS_SCAFFOLDING semantics
            declines_whole_conversation=False,
            requests_scaffolding=True,
            produces_substantive_answer=False,
            equals_no_questions_signal=False,
            becomes_evidence=False,
        )


def test_reply_affordance_meaning_rejects_contradictory_skip_current_question_semantics():
    with pytest.raises(ValueError):
        m.ReplyAffordanceMeaning(
            action=m.ReplyAffordanceAction.SKIP_CURRENT_QUESTION,
            conversation_continues=True,
            declines_current_question=True,
            declines_whole_conversation=False,
            requests_scaffolding=True,  # contradicts sealed SKIP_CURRENT_QUESTION semantics
            produces_substantive_answer=False,
            equals_no_questions_signal=False,
            becomes_evidence=False,
        )


def test_reply_affordance_meaning_rejects_integer_bool_substitutes():
    with pytest.raises(ValueError):
        m.ReplyAffordanceMeaning(
            action=m.ReplyAffordanceAction.NEEDS_SCAFFOLDING,
            conversation_continues=1,  # int, not exactly bool
            declines_current_question=0,
            declines_whole_conversation=0,
            requests_scaffolding=1,
            produces_substantive_answer=0,
            equals_no_questions_signal=0,
            becomes_evidence=0,
        )


# ── Complete non-question availability coverage across both question_allowed
#    states, for every reachable non-FOCUSED_QUESTION V1 archetype ──────────

_NON_QUESTION_ARCHETYPES = (
    (ProfessionalObjective.ESTABLISH_CONTACT, PrimaryResponseMove.OPEN_INVITATION, None),
    (ProfessionalObjective.REPAIR, PrimaryResponseMove.REFLECTIVE_STATEMENT, None),
    (ProfessionalObjective.REPAIR, PrimaryResponseMove.OPEN_INVITATION, None),
    (ProfessionalObjective.CLOSE, PrimaryResponseMove.CLOSING, None),
)


@pytest.mark.parametrize("objective,move,target", _NON_QUESTION_ARCHETYPES)
@pytest.mark.parametrize("question_allowed", [True, False])
def test_non_focused_question_archetypes_expose_none_regardless_of_question_allowed(
        objective, move, target, question_allowed):
    plan = ProfessionalTurnPlan(
        objective=objective, move=move, clarification_target=target,
        question_allowed=question_allowed)
    assert m.derive_reply_affordances(plan).actions == ()
