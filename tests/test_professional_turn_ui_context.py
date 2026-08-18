"""Tests for professional_turn_ui_context.py -- Professional Core V2 Trusted
UI Action Context V1 (offline trust boundary).
"""
from __future__ import annotations

import ast
import inspect

import pytest

import professional_turn_ui_context as ui
from professional_reply_affordances import (
    EntryFollowupFocus,
    EntryTriageCategory,
    ReplyAffordanceAction,
    derive_reply_affordances,
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


# ── Entry triage: canonicalization (items 1-11) ──────────────────────────────

@pytest.mark.parametrize("category", list(EntryTriageCategory))
def test_each_entry_category_enum_member_canonicalizes(category):
    result = ui.canonicalize_entry_triage_selection(
        ui.UntrustedEntryTriageSelection(category))
    assert result.status is ui.EntryTriageSelectionStatus.ACCEPTED
    assert result.directive.category is category


@pytest.mark.parametrize("category", list(EntryTriageCategory))
def test_each_entry_category_value_string_canonicalizes_to_same_member(category):
    result = ui.canonicalize_entry_triage_selection(
        ui.UntrustedEntryTriageSelection(category.value))
    assert result.status is ui.EntryTriageSelectionStatus.ACCEPTED
    assert result.directive.category is category


_EXPECTED_FOLLOWUP_MATRIX = {
    EntryTriageCategory.ANXIETY_STRESS: EntryFollowupFocus.RECENT_HIGH_INTENSITY_EPISODE,
    EntryTriageCategory.RELATIONSHIPS_LONELINESS: EntryFollowupFocus.RECENT_RELATIONAL_EPISODE,
    EntryTriageCategory.LOW_ENERGY_LOW_MOOD: EntryFollowupFocus.RECENT_FUNCTIONING_CHANGE,
    EntryTriageCategory.SELF_ESTEEM_SELF_CRITICISM: EntryFollowupFocus.RECENT_SELF_CRITICISM_EPISODE,
    EntryTriageCategory.DIFFICULT_EMOTIONS: EntryFollowupFocus.DIFFICULT_EMOTION_AND_ANTECEDENT,
    EntryTriageCategory.UNSURE_OR_OTHER: EntryFollowupFocus.LOW_PRESSURE_OPENING,
}


@pytest.mark.parametrize(
    "category,expected_focus",
    sorted(_EXPECTED_FOLLOWUP_MATRIX.items(), key=lambda kv: kv[0].value))
def test_accepted_directive_derives_exact_existing_followup_focus(category, expected_focus):
    result = ui.canonicalize_entry_triage_selection(
        ui.UntrustedEntryTriageSelection(category))
    assert result.directive.followup_focus is expected_focus


def test_unknown_category_string_is_rejected():
    result = ui.canonicalize_entry_triage_selection(
        ui.UntrustedEntryTriageSelection("NOT_A_REAL_CATEGORY"))
    assert result.status is ui.EntryTriageSelectionStatus.SEMANTIC_VALUE_INVALID
    assert result.directive is None


def test_unknown_category_is_not_mapped_to_unsure_or_other():
    result = ui.canonicalize_entry_triage_selection(
        ui.UntrustedEntryTriageSelection("SOME_UNKNOWN_VALUE"))
    assert result.status is not ui.EntryTriageSelectionStatus.ACCEPTED
    # explicit: an actual UNSURE_OR_OTHER selection is a different, accepted case
    other = ui.canonicalize_entry_triage_selection(
        ui.UntrustedEntryTriageSelection(EntryTriageCategory.UNSURE_OR_OTHER))
    assert other.status is ui.EntryTriageSelectionStatus.ACCEPTED
    assert other.directive.category is EntryTriageCategory.UNSURE_OR_OTHER


def test_entry_selection_wrong_python_type_fails_closed():
    with pytest.raises(ValueError):
        ui.UntrustedEntryTriageSelection(category=123)
    with pytest.raises(ValueError):
        ui.UntrustedEntryTriageSelection(category=None)


def test_canonicalize_entry_selection_fails_closed_on_wrong_argument_type():
    with pytest.raises(ValueError):
        ui.canonicalize_entry_triage_selection("not a selection")
    with pytest.raises(ValueError):
        ui.canonicalize_entry_triage_selection(None)


def test_trusted_entry_directive_rejects_mismatched_category_followup_pair():
    with pytest.raises(ValueError):
        ui.TrustedEntryTriageDirective(
            category=EntryTriageCategory.ANXIETY_STRESS,
            followup_focus=EntryFollowupFocus.LOW_PRESSURE_OPENING,
        )


def test_trusted_entry_directive_has_no_arbitrary_text_fields():
    fields = {f for f in ui.TrustedEntryTriageDirective.__dataclass_fields__}
    assert fields == {"category", "followup_focus"}


def test_no_entry_category_to_intent_conversion_api():
    public_names = [n for n in dir(ui) if not n.startswith("_")]
    offenders = [n for n in public_names if "intent" in n.lower()]
    assert offenders == []


def test_no_evidence_conversion_api():
    public_names = [n for n in dir(ui) if not n.startswith("_")]
    offenders = [n for n in public_names if "evidence" in n.lower()]
    assert offenders == []


def test_no_user_text_synthesis_api():
    public_names = [n for n in dir(ui) if not n.startswith("_")]
    suspicious = ("user_text", "as_message", "as_user", "quote", "synthetic", "fake_message")
    offenders = [n for n in public_names if any(t in n.lower() for t in suspicious)]
    assert offenders == []


# ── Offer context (items 12-23) ──────────────────────────────────────────────

_EXPECTED_FOCUSED_QUESTION_ACTIONS = (
    ReplyAffordanceAction.NEEDS_SCAFFOLDING,
    ReplyAffordanceAction.SKIP_CURRENT_QUESTION,
)


def _clarify_plan(target=ClarificationTarget.EVENT):
    return ProfessionalTurnPlan(
        objective=ProfessionalObjective.CLARIFY,
        move=PrimaryResponseMove.FOCUSED_QUESTION,
        clarification_target=target,
        question_allowed=True,
    )


def test_focused_question_offer_context_derives_exactly_both_actions():
    offer = ui.build_reply_affordance_offer_context("t1", _clarify_plan())
    assert offer.offered_actions == _EXPECTED_FOCUSED_QUESTION_ACTIONS


@pytest.mark.parametrize("target", list(ClarificationTarget))
def test_all_clarify_targets_derive_same_two_actions(target):
    offer = ui.build_reply_affordance_offer_context("t1", _clarify_plan(target))
    assert offer.offered_actions == _EXPECTED_FOCUSED_QUESTION_ACTIONS


def test_clarify_goal_focused_question_derives_same_two_actions():
    plan = ProfessionalTurnPlan(
        objective=ProfessionalObjective.CLARIFY_GOAL,
        move=PrimaryResponseMove.FOCUSED_QUESTION,
        clarification_target=None,
        question_allowed=True,
    )
    offer = ui.build_reply_affordance_offer_context("t1", plan)
    assert offer.offered_actions == _EXPECTED_FOCUSED_QUESTION_ACTIONS


_NON_QUESTION_ARCHETYPES = (
    (ProfessionalObjective.ESTABLISH_CONTACT, PrimaryResponseMove.OPEN_INVITATION, None),
    (ProfessionalObjective.REPAIR, PrimaryResponseMove.REFLECTIVE_STATEMENT, None),
    (ProfessionalObjective.REPAIR, PrimaryResponseMove.OPEN_INVITATION, None),
    (ProfessionalObjective.CLOSE, PrimaryResponseMove.CLOSING, None),
)


@pytest.mark.parametrize("objective,move,target", _NON_QUESTION_ARCHETYPES)
@pytest.mark.parametrize("question_allowed", [True, False])
def test_every_v1_non_question_archetype_derives_no_actions(
        objective, move, target, question_allowed):
    plan = ProfessionalTurnPlan(
        objective=objective, move=move, clarification_target=target,
        question_allowed=question_allowed)
    offer = ui.build_reply_affordance_offer_context("t1", plan)
    assert offer.offered_actions == ()


def test_offer_context_caller_cannot_inject_fake_additional_action():
    plan = ProfessionalTurnPlan(
        objective=ProfessionalObjective.CLOSE, move=PrimaryResponseMove.CLOSING,
        clarification_target=None, question_allowed=False)
    with pytest.raises(ValueError):
        ui.ReplyAffordanceOfferContext(
            origin_turn_token="t1", plan=plan,
            offered_actions=(ReplyAffordanceAction.NEEDS_SCAFFOLDING,))


def test_offer_context_caller_cannot_remove_a_real_action():
    with pytest.raises(ValueError):
        ui.ReplyAffordanceOfferContext(
            origin_turn_token="t1", plan=_clarify_plan(),
            offered_actions=(ReplyAffordanceAction.NEEDS_SCAFFOLDING,))


def test_offer_context_caller_cannot_reverse_sealed_order():
    with pytest.raises(ValueError):
        ui.ReplyAffordanceOfferContext(
            origin_turn_token="t1", plan=_clarify_plan(),
            offered_actions=(
                ReplyAffordanceAction.SKIP_CURRENT_QUESTION,
                ReplyAffordanceAction.NEEDS_SCAFFOLDING,
            ))


def test_offer_context_uses_real_derive_reply_affordances():
    plan = _clarify_plan()
    offer = ui.build_reply_affordance_offer_context("t1", plan)
    assert offer.offered_actions == derive_reply_affordances(plan).actions


# ── Owner-review hardening: offer context rejects plain strings (items A-C) --
# ReplyAffordanceAction is a str-backed Enum, so a plain str with the same
# textual value compares equal to the real member under naive tuple
# equality. These prove the trusted type rejects that anyway.

def test_offer_context_rejects_plain_string_offered_actions():
    with pytest.raises(ValueError):
        ui.ReplyAffordanceOfferContext(
            origin_turn_token="t1", plan=_clarify_plan(),
            offered_actions=("NEEDS_SCAFFOLDING", "SKIP_CURRENT_QUESTION"))


def test_offer_context_rejects_mixed_string_and_enum_offered_actions():
    with pytest.raises(ValueError):
        ui.ReplyAffordanceOfferContext(
            origin_turn_token="t1", plan=_clarify_plan(),
            offered_actions=(ReplyAffordanceAction.NEEDS_SCAFFOLDING, "SKIP_CURRENT_QUESTION"))


def test_offer_context_rejects_tuple_subclass_offered_actions():
    class _TupleSubclass(tuple):
        pass

    subclassed = _TupleSubclass(
        (ReplyAffordanceAction.NEEDS_SCAFFOLDING, ReplyAffordanceAction.SKIP_CURRENT_QUESTION))
    with pytest.raises(ValueError):
        ui.ReplyAffordanceOfferContext(
            origin_turn_token="t1", plan=_clarify_plan(), offered_actions=subclassed)


def test_origin_turn_token_must_be_exact_str():
    with pytest.raises(ValueError):
        ui.build_reply_affordance_offer_context(123, _clarify_plan())
    with pytest.raises(ValueError):
        ui.build_reply_affordance_offer_context(None, _clarify_plan())


def test_origin_turn_token_empty_rejected():
    with pytest.raises(ValueError):
        ui.build_reply_affordance_offer_context("", _clarify_plan())


def test_origin_turn_token_whitespace_only_rejected():
    with pytest.raises(ValueError):
        ui.build_reply_affordance_offer_context("   ", _clarify_plan())


def test_origin_turn_token_over_limit_rejected():
    too_long = "x" * 129
    with pytest.raises(ValueError):
        ui.build_reply_affordance_offer_context(too_long, _clarify_plan())


def test_origin_turn_token_at_limit_accepted():
    exactly_limit = "x" * 128
    offer = ui.build_reply_affordance_offer_context(exactly_limit, _clarify_plan())
    assert offer.origin_turn_token == exactly_limit


def test_origin_turn_token_stored_exactly_no_normalization():
    token = "  Turn-Token-123  "
    offer = ui.build_reply_affordance_offer_context(token, _clarify_plan())
    assert offer.origin_turn_token == token


# ── Reply selection canonicalization (items 24-36) ───────────────────────────

def test_needs_scaffolding_enum_selection_accepted_when_offered():
    offer = ui.build_reply_affordance_offer_context("t1", _clarify_plan())
    result = ui.canonicalize_reply_affordance_selection(
        offer, ui.UntrustedReplyAffordanceSelection("t1", ReplyAffordanceAction.NEEDS_SCAFFOLDING))
    assert result.status is ui.ReplyAffordanceSelectionStatus.ACCEPTED
    assert result.directive.action is ReplyAffordanceAction.NEEDS_SCAFFOLDING


def test_skip_current_question_enum_selection_accepted_when_offered():
    offer = ui.build_reply_affordance_offer_context("t1", _clarify_plan())
    result = ui.canonicalize_reply_affordance_selection(
        offer, ui.UntrustedReplyAffordanceSelection("t1", ReplyAffordanceAction.SKIP_CURRENT_QUESTION))
    assert result.status is ui.ReplyAffordanceSelectionStatus.ACCEPTED
    assert result.directive.action is ReplyAffordanceAction.SKIP_CURRENT_QUESTION


@pytest.mark.parametrize("action", list(ReplyAffordanceAction))
def test_exact_string_form_of_each_action_canonicalizes(action):
    offer = ui.build_reply_affordance_offer_context("t1", _clarify_plan())
    result = ui.canonicalize_reply_affordance_selection(
        offer, ui.UntrustedReplyAffordanceSelection("t1", action.value))
    assert result.status is ui.ReplyAffordanceSelectionStatus.ACCEPTED
    assert result.directive.action is action


def test_unknown_action_string_is_rejected():
    offer = ui.build_reply_affordance_offer_context("t1", _clarify_plan())
    result = ui.canonicalize_reply_affordance_selection(
        offer, ui.UntrustedReplyAffordanceSelection("t1", "NOT_A_REAL_ACTION"))
    assert result.status is ui.ReplyAffordanceSelectionStatus.SEMANTIC_VALUE_INVALID
    assert result.directive is None


def test_stale_token_returns_stale_origin_turn():
    offer = ui.build_reply_affordance_offer_context("t1", _clarify_plan())
    result = ui.canonicalize_reply_affordance_selection(
        offer, ui.UntrustedReplyAffordanceSelection("t-DIFFERENT", ReplyAffordanceAction.NEEDS_SCAFFOLDING))
    assert result.status is ui.ReplyAffordanceSelectionStatus.STALE_ORIGIN_TURN
    assert result.directive is None


def test_stale_token_takes_precedence_over_unknown_action():
    offer = ui.build_reply_affordance_offer_context("t1", _clarify_plan())
    result = ui.canonicalize_reply_affordance_selection(
        offer, ui.UntrustedReplyAffordanceSelection("t-DIFFERENT", "complete nonsense"))
    assert result.status is ui.ReplyAffordanceSelectionStatus.STALE_ORIGIN_TURN


def test_valid_known_action_not_offered_returns_action_not_offered():
    plan = ProfessionalTurnPlan(
        objective=ProfessionalObjective.CLOSE, move=PrimaryResponseMove.CLOSING,
        clarification_target=None, question_allowed=False)
    offer = ui.build_reply_affordance_offer_context("t1", plan)
    result = ui.canonicalize_reply_affordance_selection(
        offer, ui.UntrustedReplyAffordanceSelection("t1", ReplyAffordanceAction.NEEDS_SCAFFOLDING))
    assert result.status is ui.ReplyAffordanceSelectionStatus.ACTION_NOT_OFFERED


@pytest.mark.parametrize("action", list(ReplyAffordanceAction))
def test_non_question_offer_rejects_both_known_actions_as_not_offered(action):
    plan = ProfessionalTurnPlan(
        objective=ProfessionalObjective.ESTABLISH_CONTACT, move=PrimaryResponseMove.OPEN_INVITATION,
        clarification_target=None, question_allowed=False)
    offer = ui.build_reply_affordance_offer_context("t1", plan)
    result = ui.canonicalize_reply_affordance_selection(
        offer, ui.UntrustedReplyAffordanceSelection("t1", action))
    assert result.status is ui.ReplyAffordanceSelectionStatus.ACTION_NOT_OFFERED


def test_canonicalize_reply_selection_wrong_argument_types_raise():
    offer = ui.build_reply_affordance_offer_context("t1", _clarify_plan())
    selection = ui.UntrustedReplyAffordanceSelection("t1", ReplyAffordanceAction.NEEDS_SCAFFOLDING)
    with pytest.raises(ValueError):
        ui.canonicalize_reply_affordance_selection("not an offer", selection)
    with pytest.raises(ValueError):
        ui.canonicalize_reply_affordance_selection(offer, "not a selection")


_EXPECTED_KIND_MATRIX = {
    ReplyAffordanceAction.NEEDS_SCAFFOLDING: ui.TrustedReplyDirectiveKind.SCAFFOLD_CURRENT_TARGET,
    ReplyAffordanceAction.SKIP_CURRENT_QUESTION: ui.TrustedReplyDirectiveKind.SKIP_EXACT_CURRENT_QUESTION,
}


@pytest.mark.parametrize(
    "action,expected_kind",
    sorted(_EXPECTED_KIND_MATRIX.items(), key=lambda kv: kv[0].value))
def test_trusted_reply_directive_kind_mapping_is_exact(action, expected_kind):
    offer = ui.build_reply_affordance_offer_context("t1", _clarify_plan())
    result = ui.canonicalize_reply_affordance_selection(
        offer, ui.UntrustedReplyAffordanceSelection("t1", action))
    assert result.directive.kind is expected_kind


def test_trusted_reply_directive_preserves_exact_origin_token():
    offer = ui.build_reply_affordance_offer_context("exact-token-xyz", _clarify_plan())
    result = ui.canonicalize_reply_affordance_selection(
        offer, ui.UntrustedReplyAffordanceSelection(
            "exact-token-xyz", ReplyAffordanceAction.NEEDS_SCAFFOLDING))
    assert result.directive.origin_turn_token == "exact-token-xyz"


def test_trusted_reply_directive_preserves_exact_prior_plan():
    plan = _clarify_plan(ClarificationTarget.EMOTION)
    offer = ui.build_reply_affordance_offer_context("t1", plan)
    result = ui.canonicalize_reply_affordance_selection(
        offer, ui.UntrustedReplyAffordanceSelection("t1", ReplyAffordanceAction.SKIP_CURRENT_QUESTION))
    assert result.directive.prior_plan == plan


def test_trusted_reply_directive_has_no_arbitrary_free_text_field():
    fields = set(ui.TrustedReplyDirective.__dataclass_fields__)
    assert fields == {"action", "kind", "origin_turn_token", "prior_plan"}


def test_trusted_reply_directive_rejects_mismatched_action_kind_pair():
    with pytest.raises(ValueError):
        ui.TrustedReplyDirective(
            action=ReplyAffordanceAction.NEEDS_SCAFFOLDING,
            kind=ui.TrustedReplyDirectiveKind.SKIP_EXACT_CURRENT_QUESTION,
            origin_turn_token="t1",
            prior_plan=_clarify_plan(),
        )


# ── Owner-review hardening: directive rejects an action never actually
# offered for prior_plan (items D-F) -- a CLOSE+CLOSING plan offers no reply
# affordances at all, so neither action may be paired with it.

def _non_question_plan():
    return ProfessionalTurnPlan(
        objective=ProfessionalObjective.CLOSE, move=PrimaryResponseMove.CLOSING,
        clarification_target=None, question_allowed=False)


def test_trusted_reply_directive_rejects_needs_scaffolding_with_non_question_plan():
    with pytest.raises(ValueError):
        ui.TrustedReplyDirective(
            action=ReplyAffordanceAction.NEEDS_SCAFFOLDING,
            kind=ui.TrustedReplyDirectiveKind.SCAFFOLD_CURRENT_TARGET,
            origin_turn_token="t1",
            prior_plan=_non_question_plan(),
        )


def test_trusted_reply_directive_rejects_skip_with_non_question_plan():
    with pytest.raises(ValueError):
        ui.TrustedReplyDirective(
            action=ReplyAffordanceAction.SKIP_CURRENT_QUESTION,
            kind=ui.TrustedReplyDirectiveKind.SKIP_EXACT_CURRENT_QUESTION,
            origin_turn_token="t1",
            prior_plan=_non_question_plan(),
        )


def test_trusted_reply_directive_still_accepts_valid_pairs_with_focused_plan():
    needs_scaffolding = ui.TrustedReplyDirective(
        action=ReplyAffordanceAction.NEEDS_SCAFFOLDING,
        kind=ui.TrustedReplyDirectiveKind.SCAFFOLD_CURRENT_TARGET,
        origin_turn_token="t1",
        prior_plan=_clarify_plan(),
    )
    assert needs_scaffolding.action is ReplyAffordanceAction.NEEDS_SCAFFOLDING
    skip = ui.TrustedReplyDirective(
        action=ReplyAffordanceAction.SKIP_CURRENT_QUESTION,
        kind=ui.TrustedReplyDirectiveKind.SKIP_EXACT_CURRENT_QUESTION,
        origin_turn_token="t1",
        prior_plan=_clarify_plan(),
    )
    assert skip.action is ReplyAffordanceAction.SKIP_CURRENT_QUESTION


def test_untrusted_reply_selection_wrong_types_fail_closed():
    with pytest.raises(ValueError):
        ui.UntrustedReplyAffordanceSelection("t1", action=123)
    with pytest.raises(ValueError):
        ui.UntrustedReplyAffordanceSelection(origin_turn_token=None, action=ReplyAffordanceAction.NEEDS_SCAFFOLDING)


# ── Direct semantic boundary tests (items 37-42) ─────────────────────────────

def test_needs_scaffolding_action_does_not_equal_no_questions_signal():
    assert ReplyAffordanceAction.NEEDS_SCAFFOLDING != InteractionSignal.NO_QUESTIONS
    assert not isinstance(ReplyAffordanceAction.NEEDS_SCAFFOLDING, InteractionSignal)


def test_skip_action_does_not_equal_no_questions_signal():
    assert ReplyAffordanceAction.SKIP_CURRENT_QUESTION != InteractionSignal.NO_QUESTIONS
    assert not isinstance(ReplyAffordanceAction.SKIP_CURRENT_QUESTION, InteractionSignal)


def test_skip_directive_kind_is_not_question_overload_constraint():
    assert ui.TrustedReplyDirectiveKind.SKIP_EXACT_CURRENT_QUESTION != RepairConstraint.QUESTION_OVERLOAD
    assert not isinstance(
        ui.TrustedReplyDirectiveKind.SKIP_EXACT_CURRENT_QUESTION, RepairConstraint)


def test_neither_trusted_directive_is_evidence_kind_or_evidence_item():
    offer = ui.build_reply_affordance_offer_context("t1", _clarify_plan())
    reply_result = ui.canonicalize_reply_affordance_selection(
        offer, ui.UntrustedReplyAffordanceSelection("t1", ReplyAffordanceAction.NEEDS_SCAFFOLDING))
    entry_result = ui.canonicalize_entry_triage_selection(
        ui.UntrustedEntryTriageSelection(EntryTriageCategory.ANXIETY_STRESS))
    assert not isinstance(reply_result.directive, EvidenceKind)
    assert not isinstance(entry_result.directive, EvidenceKind)
    assert not isinstance(reply_result.directive, EvidenceItem)
    assert not isinstance(entry_result.directive, EvidenceItem)
    for kind in EvidenceKind:
        assert reply_result.directive != kind
        assert entry_result.directive != kind


def test_entry_selection_does_not_create_intent():
    # No Intent-shaped attribute anywhere on the trusted directive.
    result = ui.canonicalize_entry_triage_selection(
        ui.UntrustedEntryTriageSelection(EntryTriageCategory.ANXIETY_STRESS))
    directive_fields = set(ui.TrustedEntryTriageDirective.__dataclass_fields__)
    assert "intent" not in {f.lower() for f in directive_fields}
    assert not hasattr(result.directive, "intent")


def test_no_synthetic_source_text_produced_by_either_canonicalization():
    entry_result = ui.canonicalize_entry_triage_selection(
        ui.UntrustedEntryTriageSelection(EntryTriageCategory.ANXIETY_STRESS))
    offer = ui.build_reply_affordance_offer_context("t1", _clarify_plan())
    reply_result = ui.canonicalize_reply_affordance_selection(
        offer, ui.UntrustedReplyAffordanceSelection("t1", ReplyAffordanceAction.NEEDS_SCAFFOLDING))
    for directive in (entry_result.directive, reply_result.directive):
        for field_name in directive.__dataclass_fields__:
            value = getattr(directive, field_name)
            # No field on either trusted directive is or contains a plain
            # free-form str carrying arbitrary text. Use exact type() (not
            # isinstance) so a str-backed Enum member (e.g. EntryTriageCategory,
            # which is itself a str subclass) is correctly excluded --
            # origin_turn_token is the sole genuine plain-str field, an
            # opaque correlation value, never synthesized speech.
            if type(value) is str:
                assert field_name == "origin_turn_token"


# ── Immutability / closedness (item 29) ──────────────────────────────────────

_FROZEN_DATACLASSES = (
    ui.UntrustedEntryTriageSelection,
    ui.TrustedEntryTriageDirective,
    ui.EntryTriageSelectionResult,
    ui.ReplyAffordanceOfferContext,
    ui.UntrustedReplyAffordanceSelection,
    ui.TrustedReplyDirective,
    ui.ReplyAffordanceSelectionResult,
)


@pytest.mark.parametrize("cls", _FROZEN_DATACLASSES)
def test_all_trusted_dataclasses_are_frozen(cls):
    assert cls.__dataclass_params__.frozen is True


def test_offered_actions_collection_is_a_tuple():
    offer = ui.build_reply_affordance_offer_context("t1", _clarify_plan())
    assert isinstance(offer.offered_actions, tuple)


def test_no_arbitrary_dict_metadata_or_reason_or_instruction_fields():
    all_field_names = set()
    for cls in _FROZEN_DATACLASSES:
        all_field_names |= set(cls.__dataclass_fields__)
    for banned in ("metadata", "reason", "instruction", "extra", "payload"):
        assert banned not in all_field_names


# ── Static import / purity (item 30) ─────────────────────────────────────────

_EXPECTED_IMPORTS = frozenset({
    ("future", "__future__", "annotations", None, 0),
    ("from", "dataclasses", "dataclass", None, 0),
    ("from", "enum", "Enum", None, 0),
    ("from", "professional_reply_affordances", "EntryFollowupFocus", None, 0),
    ("from", "professional_reply_affordances", "EntryTriageCategory", None, 0),
    ("from", "professional_reply_affordances", "ReplyAffordanceAction", None, 0),
    ("from", "professional_reply_affordances", "derive_reply_affordances", None, 0),
    ("from", "professional_reply_affordances", "followup_focus_for_category", None, 0),
    ("from", "professional_turn_planner", "ProfessionalTurnPlan", None, 0),
})


def _module_tree():
    return ast.parse(inspect.getsource(ui))


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
    "database", "bot", "conversation_controller", "traced_response",
    "risk_detector", "dashboard", "os", "time", "random",
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
    src = inspect.getsource(ui)
    for token in ("os.environ", "getenv", "dotenv", ".env"):
        assert token not in src


def test_production_module_has_no_async_defs():
    tree = _module_tree()
    assert not any(isinstance(node, ast.AsyncFunctionDef) for node in ast.walk(tree))


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
    src = inspect.getsource(ui)
    offenders = [s for s in _LATENT_SOURCE_SUBSTRINGS if s in src]
    assert offenders == [], f"forbidden latent-source substrings present: {offenders}"


# ── Documentation boundary tests ─────────────────────────────────────────────

def test_docstring_states_offline_no_runtime_wiring():
    doc = (ui.__doc__ or "").lower()
    for phrase in ("offline domain/governance", "no runtime wiring in this slice"):
        assert phrase in doc, f"expected docstring to mention: {phrase!r}"


def test_docstring_states_planner_capability_boundary():
    doc = ui.__doc__ or ""
    for phrase in (
        "CURRENT PLANNER CAPABILITY BOUNDARY",
        "does not modify ProfessionalTurnPlan",
    ):
        assert phrase in doc, f"expected docstring to mention: {phrase!r}"


def test_docstring_states_no_analyzer_synthesis():
    doc = ui.__doc__ or ""
    assert "NO ANALYZER SYNTHESIS" in doc
    assert "additional structured context" in doc


def test_docstring_states_no_intent_override():
    doc = ui.__doc__ or ""
    assert "NO INTENT OVERRIDE" in doc
