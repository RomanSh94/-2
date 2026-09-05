"""
X20 Bot — Основной файл

Полный pipeline:
  Risk → Language → Stage → State → Readiness → Capacity → Scenario → 
  RelationshipMonitor → PracticeSelect → Memory → LLM → SafetyValidator → 
  Notifications → OutcomeTracking → User
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import pathlib
import re
import secrets
import sys
import time

# Windows consoles default to a legacy codepage (e.g. cp1251) that cannot encode
# the emoji used in our log/print statements, which crashes startup with
# UnicodeEncodeError. Force UTF-8 on stdout/stderr before anything prints.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from html import escape as _he
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, ReplyKeyboardRemove, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, FSInputFile, ReactionTypeEmoji, BotCommand,
)
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from collections import OrderedDict
from aiogram.filters import Command
from aiogram.exceptions import (
    TelegramAPIError, TelegramBadRequest, TelegramForbiddenError,
    TelegramNetworkError, TelegramRetryAfter,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import openai
from openai import AsyncOpenAI

import access_control
import scoped_access
import review_pack
import config
from config import (
    BOT_TOKEN, OPENAI_API_KEY, ADMIN_USER_IDS, AB_VARIANTS, ROUTER_VERSION, PRACTICE_VERSION,
    FIRST_TURN_CONTRACT_VERSION,
)
from prompts import get_system_prompt, get_crisis_text, get_onboarding
from prompts import (
    get_first_turn_contract_text,
    UNIVERSAL_CONTINUATION_BUTTONS_RU, UNIVERSAL_CONTINUATION_BUTTONS_EN,
    get_elaborate_fallback, get_clarify_fallback,
    build_continuation_system_prompt, build_continuation_user_message,
    get_hard_menu_text, HARD_MENU_BUTTONS_RU, HARD_MENU_BUTTONS_EN,
    get_regulate_skill_text, get_regulate_alt_text,
    HARDREG_OUTCOME_BUTTONS_RU, HARDREG_OUTCOME_BUTTONS_EN, get_hardreg_ack,
    HARDREG_EASIER_NEXT_RU, HARDREG_EASIER_NEXT_EN,
    HARDREG_SAME_NEXT_RU, HARDREG_SAME_NEXT_EN,
    HARDREG_HARDER_NEXT_RU, HARDREG_HARDER_NEXT_EN,
    get_understand_menu_text, HARDSTATE_BUTTONS_RU, HARDSTATE_BUTTONS_EN,
    get_hardstate_text, HARDSTATE_NEXT_RU, HARDSTATE_NEXT_EN,
    get_quiet_text, QUIET_NEXT_RU, QUIET_NEXT_EN,
)
from crisis_protocol import (
    classify, crisis_keyboard, admin_alert_text, RED, ORANGE,
    crisis_screen, safe_only_keyboard, crisis_call_text, crisis_contact_template,
    crisis_safe_place_ack, crisis_resolved_text, is_reassuring,
)
from crisis_delivery import deliver_crisis
from humanization import (
    pick_greeting, typing_delay, has_robotic_phrase, rephrase_instruction,
)
from risk_detector import detect_risk, amplify_ambiguity_by_context, detect_protective_factors
from language_detector import detect_language, normalize_telegram_language_code
from stage_detector import detect_stage
from state_engine import (
    DEFAULT_STATE, update_state, choose_scenario, get_emotional_trajectory,
    check_sudden_improvement,
)
from psychology_profile import maybe_update_profile, format_profile_for_user
from readiness_engine import assess_readiness
from cognitive_capacity import get_capacity
from interaction_preference import detect_interaction_preference
from practice_registry import select_practice, get_production_practice_by_id
from safety_validator import (
    validate_response,
    validate_response_with_context, select_fallback,
    is_elevated_risk, classify_rejection_reason,
    validate_first_turn_response, get_first_turn_fallback,
    validate_continuation_response,
    validate_response_without_current_user,
)
from traced_response import Influence, traced_response_builder, persist_influence_trace
from prompts import get_disambiguation_message
from tz import effective_tz
import journals
from memory import maybe_summarize, build_context
from voice import transcribe_voice
from notifications import push_alert
from scheduler import setup_scheduler
from dashboard import start_dashboard
from ab_testing import get_variant
from dependency_monitor import DependencyMonitor
from format_commands import parse_format_command, is_pure_format_command
from reaction_selector import ReactionCategory, select_reaction_category, pick_supported_emoji
from tts import synthesize_speech, TTSError
from database import (
    init_db, upsert_user, save_message, MessageSource, load_state, save_state,
    log_moderation, log_validator_block, log_router_decision,
    log_adverse_event, update_user_profile,
    start_intervention, finish_intervention,
    get_user_language,
    set_checkin, get_checkin_users, update_last_checkin,
    log_crisis_event, set_crisis_response, set_crisis_protective_factors,
    CRISIS_RESPONSE_UPDATED, CRISIS_RESPONSE_ALREADY_SAFE,
    CRISIS_RESPONSE_NOT_ACTIONABLE, CRISIS_RESPONSE_NOT_FOUND_OR_NOT_OWNED,
    get_active_crisis, bump_crisis_stage, set_stage3_at, get_crisis_stage, crisis_event_owner,
    get_memory_overview,
    export_all_personal_data, delete_all_personal_data, preview_delete_all_personal_data,
    set_mute, reset_unanswered,
    get_recent_messages, log_disambiguation,
    get_user_message_count, get_profile, delete_profile,
    log_review_flag, log_toxic_validation_block,
    save_emotion_entry, save_cbt_entry,
    get_emotion_entries_since, get_checkin_logs_since, log_checkin,
    set_tz_offset, get_user_tz, get_journal_settings, set_journal_settings,
    export_journals, delete_journals,
    log_crisis_delivery,
    get_tester_acknowledged, set_tester_acknowledged,
    start_questionnaire_session, start_questionnaire_session_if_none_active,
    get_active_questionnaire_session,
    switch_active_questionnaire_session,
    get_questionnaire_session, get_completed_questionnaire_sessions,
    record_questionnaire_response,
    advance_questionnaire_session, complete_questionnaire_session,
    cancel_questionnaire_session, get_questionnaire_responses,
    claim_dass21_discuss_reply, transition_dass21_discuss_claim,
    grant_user_access,
    unblock_user_access,
    get_onboarding_state, get_active_onboarding_state,
    start_or_get_onboarding, mark_onboarding_legacy_exempt,
    supersede_onboarding_version, advance_onboarding_step, skip_onboarding_to_privacy,
    complete_onboarding, set_onboarding_card_ref, get_onboarding_eligibility,
    get_stored_user_language, has_privacy_notice_ack,
    record_notice_acknowledgement,
    bump_user_revision, get_user_revision,
    claim_first_turn, transition_first_turn_claim,
    create_keyboard_batch_if_current, consume_interaction_binding,
    finalize_callback_reply, mark_event_besteffort, normalized_action_text,
    get_last_user_message_before, count_quiet_events,
    SEND_EXCEPTION, SAVE_EXCEPTION, FINALIZE_EXCEPTION,
    get_response_preferences, set_response_preference,
    create_disclosure_flow, get_active_disclosure_flow, get_disclosure_flow,
    advance_disclosure_flow, close_disclosure_flow, disclosure_flow_is_live,
    claim_disclosure_handoff, safe_load_answers, set_disclosure_prompt_message_id,
    supersede_active_disclosure_flows_for_crisis,
)
from depression_disclosure import (
    classify_disclosure, safety_check_text, diagnosis_source_text, duration_text,
    functioning_text, basic_activities_text, support_text, purpose_text, closing_text,
    DURATION_OPTIONS, FUNCTIONING_OPTIONS, BASIC_ACTIVITIES_OPTIONS, SUPPORT_OPTIONS,
    PURPOSE_OPTIONS, DIAGNOSIS_SOURCE_OPTIONS, SAFETY_CHECK_OPTIONS,
    STEP_ALLOWED_VALUES, STEP_TAG_TO_DB_STEP, STEP_ANSWER_KEY,
)
from database import (
    create_core_session, get_core_session, list_core_sessions, update_core_session,
    claim_handoff_and_get_or_create_session, supersede_active_core_sessions_for_crisis,
    update_core_session_authoritative,
    session_json_snapshot, create_practice_proposal, get_practice_proposal,
    transition_practice_proposal, mark_proposal_delivered,
    supersede_active_practice_proposals, record_practice_outcome,
    get_latest_outcome_for_practice,
    mark_prompt_delivered, mark_prompt_failed,
    get_proposals_with_failed_prompts, claim_prompt_send,
)
# Professional Core V2 -- Entry Triage runtime V1.
from professional_reply_affordances import ENTRY_TRIAGE_CONTRACT_V1
from professional_turn_ui_context import (
    UntrustedEntryTriageSelection, canonicalize_entry_triage_selection,
    EntryTriageSelectionStatus,
)
from professional_turn_ui_immediate_response import build_trusted_ui_immediate_response
from database import (
    create_professional_entry_triage_bindings, consume_professional_entry_triage_binding,
    supersede_professional_entry_triage_bindings,
)
# Push V1 -- Continue/New-topic binding consumption (send side lives in
# scheduler.py). touch_last_seen backs ActivityTouchMiddleware below.
from database import (
    consume_push_action_binding, supersede_push_action_bindings,
    turn_belongs_to_user, touch_last_seen, get_unresolved_crisis,
    final_push_action_reply_delivery_guard, record_push_action_reply_delivery,
    PUSH_UI_SCENARIO,
)
from prompts import (
    PUSH_V1_CONTINUE_REPLY_RU, PUSH_V1_CONTINUE_REPLY_EN,
    PUSH_V1_NO_ANCHOR_REPLY_RU, PUSH_V1_NO_ANCHOR_REPLY_EN,
    PUSH_V1_NEW_TOPIC_REPLY_RU, PUSH_V1_NEW_TOPIC_REPLY_EN,
)
# Professional Free-Text Runtime V1 -- bot.py imports ONLY the dedicated
# orchestrator module (professional_free_text_runtime) plus the already-
# merged, offline history read/build primitives; it never imports the
# Analyzer/Producer/Plan-Proposer/Planner/Renderer/Acceptance symbols
# directly (see professional_free_text_runtime.py's own module docstring --
# it is the sole place those are imported in this codebase's runtime path).
from database import get_professional_conversation_history_rows
from database import get_trusted_conversation_history_through_anchor
from professional_turn_conversation_context import (
    build_conversation_context_from_history_rows, ConversationTurnRole,
)
from professional_turn_runtime_context import ProfessionalTurnRuntimeContext
from professional_free_text_runtime import (
    run_professional_free_text_turn,
    ProfessionalFreeTextRuntimeStatus,
    ProfessionalFreeTextFailureStage,
)
from therapist_core_v1 import generate_therapist_core_v1
import push_contextual_continue
import conversation_controller as controller
from therapeutic_domain import (
    Intent, RepairConstraint, LifecycleStatus, ConsentState, PracticeProposalStatus,
    PracticeOutcome, UX_PENDING_NOT_COMPLETED_REASON, UX_PENDING_OUTCOME_DETAIL,
)
import onboarding
import onboarding_content
from onboarding_content import ONBOARDING_VERSION, PRIVACY_NOTICE_VERSION, FIRST_STEP, LAST_STEP
import questionnaires
import questionnaire_ux
import clinical_instrument_catalog
import clinical_definition_validator
import clinical_scoring
import dass21_runtime
import dass21_access
import dass21_scorer
import gad7_core
import gad7_ux
import discussion_adapters
import aiosqlite
import navigation
import emotion_map

_CLINICAL_MANIFEST_PATH = pathlib.Path(__file__).with_name("clinical_instruments_manifest.json")


def _load_catalog_document():
    """Re-reads + validates the governance manifest from disk on each call
    (never memoized), mirroring _load_registry_fresh's fail-closed contract.
    Returns the validated document, or None on any manifest problem so callers
    fail closed to a neutral 'not available' screen rather than crashing."""
    try:
        return clinical_instrument_catalog.load_instrument_manifest(_CLINICAL_MANIFEST_PATH)
    except clinical_instrument_catalog.InstrumentManifestError:
        return None

class InterventionStates(StatesGroup):
    awaiting_after   = State()
    awaiting_quality = State()

class EmotionJournal(StatesGroup):
    active = State()

class CbtJournal(StatesGroup):
    active = State()

class Dass21Discussion(StatesGroup):
    active = State()

bot                = Bot(token=BOT_TOKEN)
dp                 = Dispatcher(storage=MemoryStorage())
client             = AsyncOpenAI(api_key=OPENAI_API_KEY)
dependency_monitor = DependencyMonitor()

def tz_picker_keyboard() -> InlineKeyboardMarkup:
    """Single timezone picker reused by /time and /journal_settings → 🌍.
    Tapping a button → cb_jtz → set_tz_offset (which sets tz_set=1)."""
    row = [InlineKeyboardButton(text=("UTC" if o == 0 else f"UTC{o:+d}"),
                                callback_data=f"jtz:{o}") for o in (-1, 0, 1, 2, 3, 4, 5)]
    return InlineKeyboardMarkup(inline_keyboard=[row[:4], row[4:],
        [InlineKeyboardButton(text="МСК (UTC+3)", callback_data="jtz:3")]])


# Human-readable RU labels for protective-factor categories (admin alert).
_PF_LABELS = {
    "children": "дети", "pets": "питомцы", "close_people": "близкие",
    "future_plans": "планы на будущее", "responsibility": "обязательства",
    "meaning_faith": "смысл/вера", "reasons_to_live": "причины жить",
}


def score_kb(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=str(i), callback_data=f"{prefix}:{i}") for i in range(1, 6)],
        [InlineKeyboardButton(text=str(i), callback_data=f"{prefix}:{i}") for i in range(6, 11)],
    ])


def before_score_kb(practice_id: str, scenario: str, lang: str) -> InlineKeyboardMarkup:
    """Same 1-10 buttons as score_kb, plus an explicit "skip rating" action
    when Therapeutic Core Foundation is enabled -- lets the user proceed
    straight to the practice content without fabricating a baseline (see
    cb_before_skip). Flag OFF reproduces score_kb's exact prior keyboard,
    byte-for-byte -- no user-visible change."""
    base = score_kb(f"before:{practice_id}:{scenario}:{lang}")
    if not config.THERAPEUTIC_CORE_FOUNDATION_ENABLED:
        return base
    rows = list(base.inline_keyboard) + [[InlineKeyboardButton(
        text=("Пропустить оценку" if lang == "ru" else "Skip rating"),
        callback_data=f"before_skip:{practice_id}:{scenario}:{lang}")]]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def quality_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="👍 Помогло", callback_data="quality:1"),
        InlineKeyboardButton(text="➖ Частично", callback_data="quality:0"),
        InlineKeyboardButton(text="👎 Не помогло", callback_data="quality:-1"),
    ]])


# ── Generic first-turn contract (Phase 2) ──────────────────────────────────
# Eligibility is scenario/stage/capacity/risk driven only -- no lexical
# topic detection, no per-topic template.
FIRST_TURN_ALLOWED_SCENARIOS = {"open_chat", "reflective", "cbt_thought", "act_acceptance"}
FIRST_TURN_EXCLUDED_STAGES = {"ACUTE_DISTRESS"}
FIRST_TURN_MIN_CAPACITY = 0.3
FIRST_TURN_EXCLUDED_RISK_LEVELS = {"high", "critical"}
_FIRST_TURN_BINDING_LEASE_HOURS = 24


def _binding_expiry() -> str:
    from datetime import datetime, timezone, timedelta
    return (datetime.now(timezone.utc)
            + timedelta(hours=_FIRST_TURN_BINDING_LEASE_HOURS)).strftime("%Y-%m-%d %H:%M:%S")


# ── Professional Core V2 -- Entry Triage runtime V1 (isolated) ──────────────
# Its own lease constant, deliberately not shared with
# _FIRST_TURN_BINDING_LEASE_HOURS above -- that constant belongs to the
# unrelated first-turn continuation-button feature; decoupling the two
# means a future change to either lease duration cannot silently affect
# the other.
_PROFESSIONAL_ENTRY_TRIAGE_BINDING_LEASE_HOURS = 24


def _professional_entry_triage_expiry() -> str:
    from datetime import datetime, timezone, timedelta
    return (datetime.now(timezone.utc)
            + timedelta(hours=_PROFESSIONAL_ENTRY_TRIAGE_BINDING_LEASE_HOURS)
            ).strftime("%Y-%m-%d %H:%M:%S")


def _professional_entry_triage_keyboard(tokens: dict) -> InlineKeyboardMarkup:
    """One row per sealed V1 Entry Triage option, in the sealed V1 order.
    callback_data carries only the opaque per-category token -- category,
    lang, and revision never travel in callback_data."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=option.label_ru,
                              callback_data=f"pucbtn:{tokens[option.category]}")]
        for option in ENTRY_TRIAGE_CONTRACT_V1.options
    ])


async def _send_professional_entry_triage(target, uid: int) -> None:
    """Professional Entry Triage V1 render path -- replaces (not duplicates)
    the legacy mood-entry buttons for RU users under
    access_control.core_rollout_allowed (see _send_mood_entry). The
    delivered Telegram message text is EXACTLY
    ENTRY_TRIAGE_CONTRACT_V1.prompt_ru -- no legacy greeting prefix, no
    legacy mood question, no "⚠️ Я не терапевт." disclaimer suffix, no
    extra newline. Professional Entry Triage owns its own exact sealed
    entry surface; the legacy greeting/disclaimer belong only to the
    legacy mood-entry branch in _send_mood_entry, never to this one.

    V2->V3 correction: the interaction revision is captured BEFORE the
    prompt is sent, not after. Capturing it after would let a genuine free
    text message the user sends in the gap between delivery and this
    function's own DB write bump the live revision first -- FREE TEXT
    BEATS BUTTONS means that race must make the offer stale, not
    actionable; create_professional_entry_triage_bindings independently
    re-verifies the LIVE revision still equals the value captured here
    before writing anything, inside its own transaction, so this is a
    genuine close of the race, not just an earlier read.

    Strict revision-capture-then-send-then-bind-then-attach-keyboard
    ordering, mirroring _publish_continuation_options: the message is sent
    WITHOUT a keyboard first, the bindings are created against the real
    sent message_id, and the keyboard is attached only after the bindings
    are durably written -- this removes the fast-tap race where a keyboard
    could be tappable before its bindings exist. Rendering this prompt
    never itself bumps the revision."""
    revision = await get_user_revision(uid)
    sent = await target.answer(ENTRY_TRIAGE_CONTRACT_V1.prompt_ru)
    tokens = {option.category: secrets.token_urlsafe(9)
              for option in ENTRY_TRIAGE_CONTRACT_V1.options}
    expires_at = _professional_entry_triage_expiry()
    bindings = [{"token": tokens[option.category], "category": option.category,
                 "expires_at": expires_at}
                for option in ENTRY_TRIAGE_CONTRACT_V1.options]
    try:
        batch_ok = await create_professional_entry_triage_bindings(
            uid, sent.chat.id, sent.message_id, revision, bindings)
    except Exception as e:
        print(f"[professional-entry-triage] bind FAILED uid={uid}: {type(e).__name__}")
        return
    if not batch_ok:
        return
    try:
        await bot.edit_message_reply_markup(
            chat_id=sent.chat.id, message_id=sent.message_id,
            reply_markup=_professional_entry_triage_keyboard(tokens))
    except Exception:
        pass


def _continuation_options(lang: str, buttons_ru: list, buttons_en: list) -> list:
    """Zips the project's (label, action) RU/EN pairs into the (label_ru,
    label_en, action) triples _continuation_kb/_publish_continuation_options
    expect."""
    return [(ru_label, en_label, action)
            for (ru_label, action), (en_label, _) in zip(buttons_ru, buttons_en)]


def _continuation_kb(lang: str, options: list, tokens: dict) -> InlineKeyboardMarkup:
    """Generic single-column keyboard builder for ANY continuation option
    list. options: list of (label_ru, label_en, action). callback_data
    carries only the opaque token -- action/topic/uid/turn_id/scenario/
    lang/revision never travel in callback_data."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=(en if lang == "en" else ru), callback_data=f"ucbtn:{tokens[action]}")]
        for ru, en, action in options
    ])


async def _first_turn_generate_and_validate(messages: list, user_text: str, lang: str) -> tuple[str, bool]:
    """Returns (answer, buttons_allowed). Never raises: an LLM error or a
    failed validate_first_turn_response both resolve to the existing
    deterministic first-turn fallback, buttons_allowed=False. The LLM is
    never called a second time after a first-turn validation failure."""
    contract_messages = [{"role": "system", "content": messages[0]["content"] + get_first_turn_contract_text(lang)}]
    contract_messages.extend(messages[1:])
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini", messages=contract_messages, temperature=0.65, max_tokens=300,
        )
        candidate = response.choices[0].message.content
    except Exception as e:
        print(f"[first-turn] LLM error: {type(e).__name__}: {e}")
        return get_first_turn_fallback(lang), False

    valid, _reason = validate_first_turn_response(candidate, user_text, lang)
    if valid:
        return candidate, True
    return get_first_turn_fallback(lang), False


async def _publish_continuation_options(message, uid: int, turn_id: int, source_message_id: int,
                                        response_revision: int, lang: str,
                                        buttons_ru: list, buttons_en: list) -> bool:
    """Generic keyboard publisher for ANY continuation option list -- binds
    opaque tokens to turn_id for this exact (label, action) set.
    response_revision MUST be the exact revision captured at the point this
    turn's reply was generated -- never re-read here. A fresh read would
    race a newer user action landing between Telegram delivery and button
    publication, incorrectly binding the buttons to a revision the user has
    already moved past."""
    options = _continuation_options(lang, buttons_ru, buttons_en)
    tokens = {action: secrets.token_urlsafe(9) for _, _, action in options}
    expires_at = _binding_expiry()
    rows = [{"token": tokens[action], "turn_id": turn_id, "chat_id": message.chat.id,
             "source_message_id": source_message_id, "action": action, "expires_at": expires_at}
            for _, _, action in options]
    try:
        batch_ok = await create_keyboard_batch_if_current(uid, response_revision, rows)
    except Exception:
        return False
    if not batch_ok:
        return False
    try:
        await bot.edit_message_reply_markup(chat_id=message.chat.id, message_id=source_message_id,
                                            reply_markup=_continuation_kb(lang, options, tokens))
    except Exception:
        return False
    return True


async def _publish_universal_buttons(message, uid: int, turn_id: int, source_message_id: int,
                                     response_revision: int, lang: str) -> bool:
    """response_revision MUST be the exact user_revision captured for this
    turn in pipeline() -- never re-read here (see _publish_continuation_options)."""
    return await _publish_continuation_options(message, uid, turn_id, source_message_id,
                                               response_revision, lang,
                                               UNIVERSAL_CONTINUATION_BUTTONS_RU,
                                               UNIVERSAL_CONTINUATION_BUTTONS_EN)


async def _continuation_generate_and_validate(action: str, user_text: str, assistant_text: str,
                                              scenario: str, lang: str) -> str:
    """Single LLM call for elaborate/clarify, grounded in the actual source
    exchange via a clearly delimited USER message -- the system message
    carries only the immutable instruction contract and an explicit warning
    that the source material is untrusted content, never a source of
    instructions (see prompts.build_continuation_system_prompt /
    build_continuation_user_message; this is the prompt-injection isolation
    boundary). Never raises, never calls the LLM a second time: any
    exception, a failed action-specific structural validator
    (validate_continuation_response), or a failed production safety
    validator (validate_response_with_context -- the same one every other
    LLM-generated reply in this app goes through) all resolve to the
    deterministic localized fallback. Never logs source text, generated
    text, or the constructed messages.

    The production safety validator gets the REAL risk context for
    user_text, never a hardcoded neutral/low default: messages.risk_score/
    risk_categories (the only stored per-message risk columns) do not carry
    'level' or 'ambiguous_phrases' -- the fields validate_response_with_context
    actually reads -- so there is no complete stored snapshot to prefer.
    Re-running the SAME deterministic detect_risk the ordinary pipeline runs
    on every message, on this exact source text, matches production
    structure exactly and can never silently downgrade a real medium/high/
    critical message to 'low' (including the degenerate user_text=="" case
    from a failed prior-message lookup -- detect_risk("", lang) is the real,
    unmodified detector's own answer for empty input, not an assumption we
    hardcoded)."""
    system_prompt = build_continuation_system_prompt(action, lang)
    user_message = build_continuation_user_message(action, user_text, assistant_text, scenario, lang)
    fallback = get_elaborate_fallback if action == "elaborate" else get_clarify_fallback
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": system_prompt},
                     {"role": "user", "content": user_message}],
            temperature=0.6, max_tokens=200,
        )
        candidate = response.choices[0].message.content
    except Exception:
        return fallback(lang)

    valid, _reason = validate_continuation_response(
        candidate, action, lang, source_user_text=user_text, source_assistant_text=assistant_text)
    if not valid:
        return fallback(lang)

    risk = detect_risk(user_text, lang)
    is_safe, _reason = validate_response_with_context(candidate, user_text, risk, lang)
    if not is_safe:
        return fallback(lang)
    return candidate


async def _continuation_reply_and_next(action: str, uid: int, lang: str):
    """Deterministic (reply_text, next_buttons_ru, next_buttons_en) for every
    non-LLM continuation action EXCEPT hardreg:unsafe -- the whole
    hard:*/hardreg:*/hardstate:* graph minus that one action, which is
    special-cased and returns before this function is ever called (it needs
    the real safety-delivery path, not a plain callback.message.answer --
    see _handle_hardreg_unsafe). next_buttons_* is None only for the
    unreachable default at the bottom (every action in
    ALLOWED_INTERACTION_ACTIONS is handled above it)."""
    if action == "hard":
        return get_hard_menu_text(lang), HARD_MENU_BUTTONS_RU, HARD_MENU_BUTTONS_EN
    if action in ("hard:regulate", "hardreg:repeat"):
        return get_regulate_skill_text(lang), HARDREG_OUTCOME_BUTTONS_RU, HARDREG_OUTCOME_BUTTONS_EN
    if action == "hardreg:alt":
        return get_regulate_alt_text(lang), HARDREG_OUTCOME_BUTTONS_RU, HARDREG_OUTCOME_BUTTONS_EN
    if action == "hardreg:easier":
        return get_hardreg_ack("easier", lang), HARDREG_EASIER_NEXT_RU, HARDREG_EASIER_NEXT_EN
    if action == "hardreg:same":
        return get_hardreg_ack("same", lang), HARDREG_SAME_NEXT_RU, HARDREG_SAME_NEXT_EN
    if action == "hardreg:harder":
        return get_hardreg_ack("harder", lang), HARDREG_HARDER_NEXT_RU, HARDREG_HARDER_NEXT_EN
    if action == "hard:understand":
        return get_understand_menu_text(lang), HARDSTATE_BUTTONS_RU, HARDSTATE_BUTTONS_EN
    if action.startswith("hardstate:"):
        value = action.split(":", 1)[1]
        return get_hardstate_text(value, lang), HARDSTATE_NEXT_RU, HARDSTATE_NEXT_EN
    if action == "hard:quiet":
        step = max(0, await count_quiet_events(uid) - 1)
        return get_quiet_text(step, lang), QUIET_NEXT_RU, QUIET_NEXT_EN
    return "", None, None


async def _deliver_first_turn_response(message, uid: int, answer: str, buttons_allowed: bool,
                                       scenario: str, lang: str, claim_token: str,
                                       response_revision: int) -> None:
    """The single primary delivery point for a claimed first-turn turn.
    Exactly one message.answer(...) call; every path (success, LLM failure,
    validator rejection) reaches this same function with one resolved
    answer. Guarded transitions only -- a transition's own failure means the
    caller leaves the claim in its last confirmed state and attempts no
    further DB mutation to compensate."""
    ok = await transition_first_turn_claim(uid, FIRST_TURN_CONTRACT_VERSION, claim_token,
                                           "pending_before_llm", "generated")
    if not ok:
        return
    ok = await transition_first_turn_claim(uid, FIRST_TURN_CONTRACT_VERSION, claim_token,
                                           "generated", "send_started")
    if not ok:
        return

    try:
        sent = await message.answer(answer)
    except Exception:
        await transition_first_turn_claim(uid, FIRST_TURN_CONTRACT_VERSION, claim_token,
                                          "send_started", "delivery_uncertain")
        return

    try:
        turn_id = await save_message(uid, "assistant", answer, scenario, lang,
                                     source=MessageSource.ASSISTANT_DELIVERED)
    except Exception:
        await transition_first_turn_claim(uid, FIRST_TURN_CONTRACT_VERSION, claim_token,
                                          "send_started", "delivered_context_missing")
        return

    if not buttons_allowed:
        await transition_first_turn_claim(uid, FIRST_TURN_CONTRACT_VERSION, claim_token,
                                          "send_started", "delivered_without_buttons", turn_id=turn_id)
        return

    ok = await transition_first_turn_claim(uid, FIRST_TURN_CONTRACT_VERSION, claim_token,
                                           "send_started", "reply_delivered", turn_id=turn_id)
    if not ok:
        return

    batch_ok = await _publish_universal_buttons(message, uid, turn_id, sent.message_id,
                                                response_revision, lang)
    if batch_ok:
        await transition_first_turn_claim(uid, FIRST_TURN_CONTRACT_VERSION, claim_token,
                                          "reply_delivered", "delivered")
    else:
        await transition_first_turn_claim(uid, FIRST_TURN_CONTRACT_VERSION, claim_token,
                                          "reply_delivered", "delivered_without_buttons")

# ── Depression Disclosure Gate (Phase 2) ────────────────────────────────────
# callback_data namespace "dd:<step>:<flow_id>:<value>" -- flow_id alone is
# NEVER treated as proof of ownership; every handler re-fetches the row by
# (flow_id, callback.from_user.id) and lets the DB's ownership/step/status/
# expiry filter (database.advance_disclosure_flow's WHERE clause) be the
# actual authority, exactly like the rest of this file's callback handlers.

def _dd_options_kb(step: str, flow_id, options, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=(ru if lang != "en" else en),
                              callback_data=f"dd:{step}:{flow_id}:{val}")]
        for val, ru, en in options])


def _dd_safety_check_kb(flow_id, lang: str) -> InlineKeyboardMarkup:
    # Single row (not one-per-row like the other steps) -- exact approved
    # copy/order lives ONLY in depression_disclosure.SAFETY_CHECK_OPTIONS.
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=(ru if lang != "en" else en),
                             callback_data=f"dd:safety:{flow_id}:{val}")
        for val, ru, en in SAFETY_CHECK_OPTIONS]])


def _dd_diagnosis_source_kb(flow_id, lang: str) -> InlineKeyboardMarkup:
    return _dd_options_kb("src", flow_id, DIAGNOSIS_SOURCE_OPTIONS, lang)


def _dd_duration_kb(flow_id, lang: str) -> InlineKeyboardMarkup:
    return _dd_options_kb("dur", flow_id, DURATION_OPTIONS, lang)


def _dd_functioning_kb(flow_id, lang: str) -> InlineKeyboardMarkup:
    return _dd_options_kb("func", flow_id, FUNCTIONING_OPTIONS, lang)


def _dd_basic_activities_kb(flow_id, lang: str) -> InlineKeyboardMarkup:
    return _dd_options_kb("basic", flow_id, BASIC_ACTIVITIES_OPTIONS, lang)


def _dd_support_kb(flow_id, lang: str) -> InlineKeyboardMarkup:
    return _dd_options_kb("supp", flow_id, SUPPORT_OPTIONS, lang)


def _dd_purpose_kb(flow_id, lang: str) -> InlineKeyboardMarkup:
    return _dd_options_kb("purp", flow_id, PURPOSE_OPTIONS, lang)


async def _dd_reject_stale_callback(callback: CallbackQuery, lang: str) -> None:
    await callback.answer(
        "Этот вопрос уже неактуален." if lang != "en" else "This question is no longer active.",
        show_alert=False)


async def _dd_validate_callback(callback: CallbackQuery, expected_step: str):
    """The full 7-point validation chain (Phase 2 correction §1): parse exact
    namespace/step tag -> validate step tag -> validate ownership -> validate
    current DB step -> validate status+expiry -> validate value against the
    step's closed allowlist -> [caller performs the atomic transition]. Any
    failure answers the callback (no state change, no next question) and
    returns None -- ownership failures, duplicate/stale/expired/superseded
    taps, and unsupported values are ALL rejected the same safe way, never
    coerced into a different meaning (e.g. an unknown SAFETY_CHECK value is
    never treated as "no").

    Crisis supersession is NOT re-checked here separately: trigger_crisis
    (the one canonical crisis-entry point) supersedes every active flow for
    the user UNCONDITIONALLY, before it even attempts audit logging (Phase 2
    correction §2 -- logging must never be the sole mechanism enforcing this
    invariant), so a flow's own `status` is already truthful the instant ANY
    crisis route runs -- disclosure_flow_is_live() below already returns
    False for it. Duplicating a second get_active_crisis() lookup here would
    be exactly the kind of per-handler duplication the correction asked NOT
    to have."""
    uid = callback.from_user.id
    parts = callback.data.split(":", 3)
    if len(parts) != 4 or parts[0] != "dd":
        await callback.answer()
        return None
    _, tag, flow_id, value = parts
    if STEP_TAG_TO_DB_STEP.get(tag) != expected_step:
        await callback.answer()
        return None
    flow = await get_disclosure_flow(flow_id, uid)
    if flow is None:
        await callback.answer()
        return None
    lang = flow["lang"]
    if not await access_control.depression_disclosure_allowed_for(uid):
        await _dd_reject_stale_callback(callback, lang)
        return None
    if flow["step"] != expected_step or not disclosure_flow_is_live(flow):
        await _dd_reject_stale_callback(callback, lang)
        return None
    if value not in STEP_ALLOWED_VALUES[expected_step]:
        await _dd_reject_stale_callback(callback, lang)
        return None
    return flow, value

# ── Voice and Adaptive Response UX ──────────────────────────────────────────
# Everything below is inert while VOICE_REPLIES_ENABLED / EMOTIONAL_REACTIONS_
# ENABLED are false (the default). deliver_response is the ONE shared point
# where a final, Safety-Validator-approved ordinary response is actually
# delivered -- text, voice, or voice+concise-text -- reused by the listen
# button and the "much text/lazy to read" meta-command path below.
#
# Owner-only canary gate: both flags remain global rollout switches (default
# false), but even once true, the two helpers below additionally require an
# exact match against the EXISTING access_control.OWNER_USER_ID (a single
# immutable Telegram numeric id, already fail-closed to None on a missing/
# invalid environment value -- see access_control.py). No new allowlist, no
# new role system, no new DB table: reuses the same mechanism this codebase
# already trusts elsewhere (e.g. dass21_access.authorize_dass21_user). If
# OWNER_USER_ID is unset/invalid, these always return False for everyone,
# including whoever happens to hold that uid in a misconfigured deployment.

_FMT_KB_VERSION = "fmt1"
_LISTEN_KB_VERSION = "listen1"
_LISTEN_TAP_COOLDOWN_SECONDS = 5
_reaction_last_sent: dict[int, float] = {}
_listen_last_tap: dict[int, float] = {}


# Legacy reply-keyboard eviction (UX defect found during the owner canary).
#
# Before commit 214ba15 the mood entry was a ReplyKeyboardMarkup(6 rows,
# one_time_keyboard=True); it is now an InlineKeyboardMarkup. But a Telegram
# reply keyboard lives CLIENT-SIDE and survives indefinitely -- switching the
# bot to inline does not retract it, and one_time_keyboard only collapses it
# (the user can re-open it from the input field, and several clients re-expand
# it on their own). So every account that used the bot before that commit
# still carries the old 6-row keyboard, which covers a large part of the
# screen and makes ordinary typing awkward. Only two rarely-reached call
# sites (/help, the post-practice rating) ever sent ReplyKeyboardRemove, so
# an ordinary "/start -> talk" session never cleared it.
#
# Fix: piggy-back a ReplyKeyboardRemove on the FIRST ordinary reply we send
# each user, then never again. deliver_response is the single shared delivery
# point, so one insertion covers all three required triggers -- choosing an
# emotion (cb_mood -> pipeline), typing free text, and sending a voice
# message (handle_voice -> pipeline). No extra message is sent, no visible
# change for users who never had the legacy keyboard, and no DB schema.
#
# In-memory set (same convention as _reaction_last_sent/_listen_last_tap): a
# restart simply re-sends one harmless no-op removal per user. Nothing in the
# current codebase constructs a ReplyKeyboardMarkup at all (it is not even
# imported), so this can never retract a keyboard some other live flow needs
# -- and ReplyKeyboardRemove does not touch inline keyboards.
#
# BOUNDED, and deliberately so: one int per distinct user would otherwise grow
# for the process lifetime. At the cap the whole set is dropped rather than
# evicting an arbitrary member -- the only consequence of forgetting a user is
# one extra no-op removal on their next reply, so a cheap full reset is
# preferable to maintaining insertion order for a value this inconsequential.
_LEGACY_KB_MAX_TRACKED = 10_000
_legacy_kb_cleared: set[int] = set()


_LOWER_MENU = {
    # Privacy row removed (owner product decision): Data & Privacy already
    # lives in Help (see _help_keyboard) -- the persistent lower menu no
    # longer duplicates it. lower_menu_privacy (the F.text handler below)
    # and privacy:hub/_privacy_hub_keyboard/navigation.privacy_hub_text are
    # all untouched -- Privacy is still fully reachable from Help, only the
    # duplicate entry point here is gone.
    "ru": (
        ("🧠 Психологические тесты", "📊 Мои результаты"),
        ("📝 Дневники", "🎛 Как отвечать"),
    ),
    "en": (
        ("🧠 Psychological tests", "📊 My results"),
        ("📝 Diaries", "🎛 How to reply"),
    ),
}

# Round 4: the ONE canonical source of navigation-control labels, derived
# from _LOWER_MENU itself so there is never a second, independently
# maintained copy that could silently drift. Used to keep persistent-menu
# button presses from ever being consumed as journal free-text answers (see
# emotion_step/cbt_step's filter chain below).
#
# Privacy row removed from _LOWER_MENU's rendering above, but its label
# must stay recognized here: emotion_step/cbt_step's exclusion filter is
# the ONLY thing standing between an in-progress journal FSM and silently
# swallowing that exact text as journal content. A user's Telegram client
# can still show an already-cached copy of the OLD keyboard (Telegram
# caches the last-sent ReplyKeyboardMarkup) for a while after this
# deploy, so a tap on that stale button -- or the same text typed
# manually -- must still escape the FSM and reach lower_menu_privacy
# (still fully registered, unchanged) exactly as before, not get treated
# as journal free-text.
_LOWER_MENU_CONTROL_LABELS = frozenset(
    label for rows in _LOWER_MENU.values() for row in rows for label in row
) | {"🔒 Данные и приватность", "🔒 Data and privacy"}


async def _clear_active_journal_if_leaving(state: FSMContext = None) -> None:
    """Abandon an unfinished journal or DASS discussion when the
    user explicitly navigates away (a persistent-menu control, /help,
    /start, or inline navigation). Touches ONLY the two journal states and
    the ephemeral DASS discussion state --
    never onboarding, questionnaire sessions, InterventionStates,
    disclosure flows, or any other product FSM -- and never deletes an
    already-SAVED journal entry or questionnaire result; it only abandons
    unsaved journal progress or the in-memory DASS session binding.
    state=None is a harmless no-op (some call sites are
    exercised directly in tests without a real FSMContext); aiogram always
    supplies a real one in production."""
    if state is None:
        return
    current = await state.get_state()
    if current in (EmotionJournal.active.state, CbtJournal.active.state,
                   Dass21Discussion.active.state):
        await state.clear()


async def _clear_dass21_discussion(state: FSMContext = None) -> None:
    if state is not None and await state.get_state() == Dass21Discussion.active.state:
        await state.clear()


def persistent_lower_menu_kb(lang: str) -> ReplyKeyboardMarkup:
    rows = _LOWER_MENU["ru" if lang == "ru" else "en"]
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=label) for label in row] for row in rows],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=("Напиши сообщение…" if lang == "ru" else "Type a message…"),
    )


async def _answer_evicting_legacy_kb(
        message: Message, uid: int, text: str, lang: str) -> Message:
    """Replace any legacy keyboard with the permanent public-beta lower menu."""
    if len(_legacy_kb_cleared) >= _LEGACY_KB_MAX_TRACKED:
        _legacy_kb_cleared.clear()
    _legacy_kb_cleared.add(uid)
    return await message.answer(text, reply_markup=persistent_lower_menu_kb(lang))


async def _voice_ux_enabled_for(uid: int) -> bool:
    return (
        config.VOICE_REPLIES_ENABLED
        and config.ELEVENLABS_TTS_ENABLED
        and await access_control.has_full_access(uid)
    )


async def _reactions_enabled_for(uid: int) -> bool:
    return (
        config.EMOTIONAL_REACTIONS_ENABLED
        and await access_control.has_full_access(uid)
    )


def format_selector_kb(lang: str) -> InlineKeyboardMarkup:
    ru = [
        [InlineKeyboardButton(text="💬 Текстом", callback_data=f"{_FMT_KB_VERSION}:format:text"),
         InlineKeyboardButton(text="🎙 Голосом", callback_data=f"{_FMT_KB_VERSION}:format:voice")],
        [InlineKeyboardButton(text="🎧 Текст + голос",
                              callback_data=f"{_FMT_KB_VERSION}:format:voice_and_concise_text")],
    ]
    en = [
        [InlineKeyboardButton(text="💬 Text", callback_data=f"{_FMT_KB_VERSION}:format:text"),
         InlineKeyboardButton(text="🎙 Voice", callback_data=f"{_FMT_KB_VERSION}:format:voice")],
        [InlineKeyboardButton(text="🎧 Text + voice",
                              callback_data=f"{_FMT_KB_VERSION}:format:voice_and_concise_text")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=(ru if lang == "ru" else en))


def _response_format_setup_text(lang: str) -> str:
    if lang == "ru":
        return "Кстати, вот как я звучу 🎧\n\nКак тебе удобнее получать ответы?"
    return "By the way, this is how I sound 🎧\n\nHow would you like to receive replies?"


async def _send_persistent_lower_menu(target, lang: str) -> None:
    await target.answer(
        "Готово. Можешь написать, что сейчас происходит, или выбрать раздел ниже."
        if lang == "ru" else
        "All set. You can write what's going on right now, or choose a section below.",
        reply_markup=persistent_lower_menu_kb(lang),
    )


async def _send_response_format_setup(target, uid: int, lang: str) -> None:
    if await _voice_ux_enabled_for(uid):
        await target.answer(
            _response_format_setup_text(lang),
            reply_markup=format_selector_kb(lang),
        )
    await _send_persistent_lower_menu(target, lang)


def _listen_kb(uid: int, lang: str) -> InlineKeyboardMarkup:
    label = "🔊 Прослушать" if lang == "ru" else "🔊 Listen"
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, callback_data=f"{_LISTEN_KB_VERSION}:{uid}")]])


def _format_ack_text(cmd, lang: str) -> str:
    ru = {
        "voice_persistent": "Хорошо, буду отвечать голосом.",
        "text_persistent": "Хорошо, буду отвечать текстом.",
        "concise_persistent": "Хорошо, буду отвечать короче.",
        "concise_oneshot": "Постараюсь короче в следующий раз.",
        "detailed_oneshot": "Хорошо, в следующий раз отвечу подробнее.",
    }
    en = {
        "voice_persistent": "Got it — I'll reply with voice from now on.",
        "text_persistent": "Got it — I'll reply with text from now on.",
        "concise_persistent": "Got it — I'll keep replies shorter.",
        "concise_oneshot": "I'll keep the next reply shorter.",
        "detailed_oneshot": "Got it — I'll go into more detail next time.",
    }
    table = ru if lang == "ru" else en
    return table.get(cmd.kind, "Хорошо." if lang == "ru" else "Got it.")


def _concise_version(text: str, max_chars: int = 220) -> str:
    """Deterministic, bounded shortening -- NOT a second LLM interpretation.
    Takes leading sentences up to a char budget; never fabricates content."""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out = ""
    for s in sentences:
        if out and len(out) + len(s) + 1 > max_chars:
            break
        out = (out + " " + s).strip()
    return out or (text[:max_chars].rstrip() + "…")


def _safe_concise_version(text: str, lang: str) -> str:
    """The concise text is re-validated through Safety Validator (§9):
    truncation cannot introduce a new claim, but it COULD cut off a
    qualifying safety caveat, so the shortened text is checked again before
    ever reaching TTS. Falls back to the full (already-approved) text if
    the shortened version fails validation."""
    candidate = _concise_version(text)
    if candidate == text.strip():
        return text
    is_safe, _ = validate_response(candidate, lang)
    return candidate if is_safe else text


async def _synthesize_and_send_voice(target, uid: int, text: str, lang: str) -> bool:
    """Synthesizes `text` (already Safety-Validator-approved) and sends ONE
    voice message via `target` (a Message, exposing .answer_voice). Returns
    True on success, False on ANY failure (TTS or Telegram send) -- never
    raises, and the temporary audio file is always removed."""
    if not await _voice_ux_enabled_for(uid):
        return False
    path = None
    try:
        path = await synthesize_speech(client, text, lang)
        await target.answer_voice(FSInputFile(path))
        return True
    except Exception as e:
        print(f"[tts] uid={uid}: {type(e).__name__}")
        return False
    finally:
        if path:
            try:
                os.remove(path)
            except OSError:
                pass


async def deliver_response(message: Message, uid: int, answer: str, lang: str,
                            *, one_shot_voice: bool = False,
                            one_shot_concise: bool = False,
                            reply_markup=None,
                            preserve_exact_text: bool = False) -> Message | None:
    """The SINGLE shared point where a final, Safety-Validator-approved
    response is delivered — text / voice / voice_and_concise_text, from the
    user's stored preference or a one-shot meta-command override. Flag OFF
    => always plain text, byte-for-byte the prior
    `await message.answer(answer)` behavior. Also private-chat-only (§4):
    even if a preference were somehow set, delivery from a non-private chat
    always falls back to plain text with no listen button -- defense in
    depth alongside the pipeline()-level guard that never lets a group
    message reach the format-command/override code at all.

    Hardening §12: `reply_markup` (used for the Conversation Controller's
    PRACTICE consent buttons) always forces plain text -- a consent prompt
    is never voiced or condensed, matching its existing dedicated contract
    exactly, byte-for-byte. Returns the sent Telegram Message (or None when
    voice delivery consumed it) so a caller needing the message id (e.g. to
    record a PracticeProposal's delivered message) can read it -- every
    other existing caller already ignored the previous None return, so this
    is additive, not a breaking change.

    ``preserve_exact_text`` remains accepted for call-site compatibility.
    Public-beta delivery no longer shortens an already-approved answer in the
    presentation layer: both full-text modes and voice synthesis receive the
    exact complete answer."""
    is_private = getattr(message.chat, "type", "private") == "private"
    if reply_markup is not None:
        return await message.answer(answer, reply_markup=reply_markup)
    if not await _voice_ux_enabled_for(uid) or not is_private:
        return await _answer_evicting_legacy_kb(message, uid, answer, lang)

    prefs = await get_response_preferences(uid)
    fmt = "voice" if one_shot_voice else prefs["response_format"]
    if fmt == "text":
        return await message.answer(answer, reply_markup=persistent_lower_menu_kb(lang))

    if fmt == "voice_and_concise_text":
        # Historical DB value retained for migration compatibility; the
        # public-beta meaning is now full text + an on-demand Listen button.
        # Never auto-send a duplicate voice message in this mode.
        return await message.answer(answer, reply_markup=_listen_kb(uid, lang))

    # fmt == "voice"
    ok = await _synthesize_and_send_voice(message, uid, answer, lang)
    if not ok:
        return await message.answer(
            answer, reply_markup=persistent_lower_menu_kb(lang))
    return None  # delivered as a voice message -- no text Message object to return


async def _maybe_react(message: Message, uid: int, category: ReactionCategory,
                        confidence: float) -> None:
    """Best-effort Telegram message reaction -- never blocks or delays the
    actual response, never raises, never persists `category` anywhere.

    Emits a privacy-safe decision line so a canary is diagnosable without
    reading anyone's chat: category, confidence and a fixed skip-reason
    token only. Never the message text, the response, the transcript, the
    username or the Telegram user id -- during the owner canary the silent
    outcome was indistinguishable from a broken flag, which is exactly what
    this makes visible. _log is deliberately exception-proof: observability
    must never be able to affect text delivery."""
    def _log(decision: str, reason: str = "-") -> None:
        try:
            print(f"[reaction] decision={decision} reason={reason} "
                  f"category={category.value} confidence={confidence:.2f}")
        except Exception:
            pass

    if not await _reactions_enabled_for(uid):
        _log("skipped", "not_authorized")
        return
    if category == ReactionCategory.NONE:
        _log("skipped", "no_match")
        return
    if confidence < config.EMOTIONAL_REACTION_MIN_CONFIDENCE:
        _log("skipped", "low_confidence")
        return
    # NOTE: deliberately no chat-type gate here. Reactions have never been
    # private-chat-only (unlike Voice UX), and narrowing that now would be an
    # unrequested behavior change -- see
    # test_owner_gate_reactions_owner_in_group_unaffected.
    now = time.time()
    if now - _reaction_last_sent.get(uid, 0) < config.EMOTIONAL_REACTION_COOLDOWN_SECONDS:
        _log("skipped", "cooldown")
        return
    try:
        chat = await bot.get_chat(message.chat.id)
        available = None
        if chat.available_reactions is not None:
            available = [r.emoji for r in chat.available_reactions if hasattr(r, "emoji")]
        emoji = pick_supported_emoji(category, available)
        if not emoji:
            _log("skipped", "unsupported_in_chat")
            return
        await bot.set_message_reaction(
            chat_id=message.chat.id, message_id=message.message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)])
        _reaction_last_sent[uid] = now
        _log("selected", "sent")
    except Exception as e:
        _log("failed", type(e).__name__)

# ────────────────────────────────────────────────────────────────────────────

def _minimal_reviewer_payload(uid: int, eid, note: str) -> str:
    """PR 1B-1: the ONLY payload a CLINICIAN_REVIEWER ever receives — no message
    text, no username, no risk categories beyond the fixed `note` label. Enough
    to know a clinical review is needed, nothing more."""
    return f"🔔 Clinical review needed\ntester_id: {uid}\nevent_id: {eid}\nnote: {note}"


_CLOSED_TEST_TEXT = {
    "ru": "Сейчас доступ ограничен приглашёнными участниками закрытого "
          "тестирования. Если тебе тяжело прямо сейчас — напиши это здесь, "
          "экстренная поддержка работает для всех.",
    "en": "Access is currently limited to invited participants of a closed "
          "test. If you're struggling right now, write it here — crisis support "
          "still works for everyone.",
}

_REVIEW_ONLY_TEXT = {
    "ru": "Для этой учётной записи доступен только контур клинического ревью; обычный продукт не открыт.",
    "en": "This account is limited to the clinical-review surface; ordinary product access is unavailable.",
}

_TESTER_WAITING_TEXT = {
    "ru": "Спасибо, отмечено. Доступ откроется, как только за тобой закрепят "
          "куратора-ревьюера.",
    "en": "Thanks, noted. Access will open once a reviewer is assigned to you.",
}

# Owner-specified verbatim RU text; EN is a plain translation, not a separate
# legal/consent document.
_TESTER_ACK_TEXT = {
    "ru": "Вы приглашены как clinical tester. Бот может использовать данные "
          "ваших собственных опросников/дневников/паттернов для ответов через "
          "traced A1 mechanism. Ваши данные изолированы от владельца и других "
          "тестеров. Это тестовый режим, не публичный продукт.",
    "en": "You are invited as a clinical tester. The bot may use your own "
          "questionnaire/journal/pattern data to shape replies via the traced "
          "A1 mechanism. Your data is isolated from the owner and other "
          "testers. This is a test mode, not a public product.",
}


def _tester_ack_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=("✅ Я согласен(на)" if lang == "ru" else "✅ I agree"),
            callback_data="tester_ack:yes")]])


async def ensure_full_access_or_closed_test(entity, uid: int) -> bool:
    """PR 1B-1 checkpoint-2 item 6 — the ONE gate every product entrypoint calls.

    Returns True if the caller may proceed with its ordinary product behavior.
    Returns False (after sending the appropriate screen) otherwise:
      - CLINICIAN_TESTER in controlled_clinical_test, not yet acknowledged ->
        the tester-acknowledgment notice + an inline "I agree" button.
      - a review-only account in public mode -> a review-only notice;
      - anything else without full access -> the generic closed-test message.

    `entity` is a Message or a CallbackQuery — both are used as real bot
    entrypoints. This function never touches the crisis path; callers are
    expected to have already run the RED / active-crisis checks first."""
    if await access_control.has_full_access(uid):
        return True
    lang = await get_user_language(uid)
    target = entity.message if isinstance(entity, CallbackQuery) else entity
    role = access_control.resolve_role_safe(uid)
    if (role == access_control.CLINICIAN_TESTER
            and access_control.DEPLOYMENT_MODE == "controlled_clinical_test"
            and not await get_tester_acknowledged(uid)):
        await target.answer(_TESTER_ACK_TEXT[lang if lang in _TESTER_ACK_TEXT else "ru"],
                            reply_markup=_tester_ack_keyboard(lang))
    elif role == access_control.CLINICIAN_TESTER:
        # Acknowledged already, but no (valid) reviewer mapping yet.
        await target.answer(_TESTER_WAITING_TEXT[lang if lang in _TESTER_WAITING_TEXT else "ru"])
    elif access_control.DEPLOYMENT_MODE == "public":
        await target.answer(_REVIEW_ONLY_TEXT[lang if lang in _REVIEW_ONLY_TEXT else "ru"])
    else:
        await target.answer(_CLOSED_TEST_TEXT[lang if lang in _CLOSED_TEST_TEXT else "ru"])
    if isinstance(entity, CallbackQuery):
        await entity.answer()
    return False


@dp.callback_query(F.data == "tester_ack:yes")
async def cb_tester_ack(callback: CallbackQuery):
    uid = callback.from_user.id
    await set_tester_acknowledged(uid)
    lang = await get_user_language(uid)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    if await access_control.has_full_access(uid):
        msg = ("Спасибо. Доступ открыт — можно продолжать, напиши /start."
               if lang == "ru" else
               "Thanks. Access granted — you can continue, try /start.")
    else:
        msg = _TESTER_WAITING_TEXT[lang if lang in _TESTER_WAITING_TEXT else "ru"]
    await callback.message.answer(msg)
    await callback.answer()


async def _crisis_delivery_alert(uid, eid, kind, error) -> None:
    """v6 §6.3 — P0 alarm when a crisis message could not be delivered at ANY
    ladder level. This is the silent-delivery guard: an undelivered crisis screen
    must never pass unnoticed. PR 1B-1: routed the same way as every other
    crisis alert — "none" (UNKNOWN / unmapped tester / resolver failure) means
    nobody is alerted, per the isolation model, not a silent bug."""
    routed_kind, targets = access_control.crisis_alert_targets(uid)
    if routed_kind == "owner":
        msg = (f"🚨🚨 P0 CRISIS UNDELIVERED — uid={uid} event={eid} kind={kind}\n"
               f"Все уровни доставки кризисного сообщения упали. err={error}")
        for admin_id in targets:
            try:
                await bot.send_message(admin_id, msg)
            except Exception:
                pass
    elif routed_kind == "reviewer":
        payload = _minimal_reviewer_payload(uid, eid, "crisis message delivery FAILED (P0)")
        for reviewer_id in targets:
            try:
                await bot.send_message(reviewer_id, payload)
            except Exception:
                pass


async def send_crisis(send, text, kb, lang, uid, eid, kind) -> str:
    """Bind crisis_delivery.deliver_crisis to this app's delivery-log + P0 alert.
    `send` is message.answer / callback.message.answer / partial(bot.send_message,
    uid). Returns the delivered level (rich/plain/minimal/none)."""
    return await deliver_crisis(send, text=text, kb=kb, lang=lang, uid=uid, eid=eid,
                                kind=kind, log=log_crisis_delivery,
                                on_total_failure=_crisis_delivery_alert)


async def trigger_crisis(message: Message, uid: int, username: str,
                         user_text: str, risk: dict, lang: str, *,
                         source: str = "EXPLICIT_MESSAGE") -> None:
    """Deterministic Crisis Protocol (LLM is NEVER called here). Extracted from
    pipeline so other entry points (e.g. the journals risk-gate) can REUSE the
    exact same flow instead of duplicating it.

    DELIVERY-FIRST: safety = detection + decision + *delivery*. The crisis screen
    is sent as soon as we have the event id (needed for the callback buttons),
    BEFORE any of the non-delivery bookkeeping (message log, profile refresh,
    protective factors, admin alert). All of that runs afterwards inside a
    try/except so a single failing await (DB timeout/lock, a dead webhook, etc.)
    can never suppress the screen the person in crisis must see. This closes the
    same *class* as the original P0 (detection ok, decision ok, delivery lost).

    PR 1B-1 checkpoint-2 Priority 0: the event-creating write itself
    (log_crisis_event) is now inside its own try/except. crisis_events.user_id
    has no FOREIGN KEY constraint (verified against the schema — no PRAGMA
    foreign_keys, no FK clause), so an unknown/never-upserted uid is not an FK
    failure; but the GENERAL invariant is broader than that one cause — ANY
    pre-delivery DB error (lock timeout, disk full, corruption) must not block
    the screen either. On failure, eid stays None and the screen degrades to
    PLAIN TEXT ONLY, no buttons at all — checkpoint-2 round 3, item 1A: a
    degraded fallback still must not send ANY stateful "crisis:*" button,
    because DB instability that broke log_crisis_event may still be broken
    when the user taps a button a moment later (cb_crisis's own DB reads can
    then raise — see cb_crisis's own try/except around that resolve, item 1B).
    get_crisis_text already contains the hotline/plain emergency guidance in
    the message body itself, so no button is needed to deliver the number.

    `source` (Phase 2 correction §3): truthful provenance for the audit trail.
    EXPLICIT_MESSAGE (default, unchanged) means risk/categories came from
    risk_detector pattern-matching real message text. DIRECT_SAFETY_YES/
    DIRECT_SAFETY_UNSURE (Depression Disclosure Gate) mean a direct button
    answer -- `risk["score"]` is None (never a fabricated number) and
    `user_text` is "" (never invented placeholder text) for those.

    Phase 2 correction §2 -- disclosure-flow supersession is NOT a side
    effect of logging: THIS function is the one canonical crisis-entry point
    every route in the codebase funnels through (pipeline()'s RED branch,
    journal_guard's RED branch, the Depression Disclosure Gate's own
    "yes"/"unsure" callback), so it supersedes any active disclosure flow
    FIRST -- unconditionally, in its own try/except, BEFORE even attempting
    log_crisis_event -- so the invariant holds even if audit logging raises,
    times out, or is skipped entirely."""
    try:
        await supersede_active_disclosure_flows_for_crisis(uid)
    except Exception as e:
        print(f"[crisis] disclosure-flow supersession FAILED uid={uid}: {type(e).__name__}: {e}")
    # Phase 3 correction §6: crisis supersedes ALL ordinary Core work, not
    # just the Depression Disclosure Gate -- same unconditional, logging-
    # independent placement, its own try/except.
    try:
        await supersede_active_core_sessions_for_crisis(uid)
    except Exception as e:
        print(f"[crisis] core-session supersession FAILED uid={uid}: {type(e).__name__}: {e}")
    # Professional Entry Triage correction pass: an offer that existed
    # BEFORE this crisis became active must never become actionable again
    # once the crisis resolves (bump_user_revision has not necessarily run
    # yet at this point in the pipeline, so the offer's binding_revision
    # alone cannot be trusted to have gone stale). Same unconditional,
    # logging-independent, best-effort placement as the two supersession
    # calls above -- a failure here NEVER blocks, delays, or degrades
    # crisis delivery below. No token/category/user text in the log line.
    try:
        await supersede_professional_entry_triage_bindings(uid)
    except Exception as e:
        print(f"[crisis] entry-triage supersession FAILED uid={uid}: {type(e).__name__}")
    # Push V1: same reasoning as the entry-triage supersession immediately
    # above -- a Continue/New-topic offer that existed BEFORE this crisis
    # became active must never become actionable again once the crisis
    # resolves. cb_push_action's own active-crisis check is the primary
    # safety property (it re-checks live at tap time regardless of this
    # call); this is defense-in-depth cleanup only.
    try:
        await supersede_push_action_bindings(uid)
    except Exception as e:
        print(f"[crisis] push-binding supersession FAILED uid={uid}: {type(e).__name__}")

    eid = None
    try:
        # Create the event first — its id is baked into the crisis screen buttons.
        eid = await log_crisis_event(uid, RED, risk["score"], risk["categories"],
                                     user_text[:300], lang, admin_notified=bool(ADMIN_USER_IDS),
                                     source=source)
    except Exception as e:
        # Sanitized: no raw user_text/username in this log line.
        print(f"[crisis] log_crisis_event FAILED: {type(e).__name__}: {e}")
        eid = None

    if eid is not None:
        text, kb = crisis_screen(0, lang, eid)
    else:
        # Degraded delivery: no event row exists, so NO buttons are sent at
        # all -- not even the eid-less "manual" crisis:safe/crisis:still pair.
        # The hotline number is already in the plain text body.
        text, kb = get_crisis_text(lang), None
    # DELIVER the crisis screen to the user before anything non-essential.
    await send_crisis(message.answer, text, kb, lang, uid, eid, "screen")

    if eid is None:
        # No crisis_events row exists to attach bookkeeping/alerts to, and the
        # DB is evidently degraded — every remaining step below either needs a
        # real eid or is itself a DB write. Stop here; the screen is what
        # mattered and it was delivered.
        return

    # PR 1B-1: role is resolved ONLY here, strictly AFTER delivery above. A broken
    # resolver (or any exception) resolves to UNKNOWN (resolve_role_safe) and can
    # therefore never affect whether the screen was sent — that already happened.
    role = access_control.resolve_role_safe(uid)

    # Everything below is admin/research context — important, but it must NEVER
    # block or undo the delivered screen. Each block is isolated and logged.
    try:
        # Persist the crisis message's risk snapshot + force a profile refresh
        # (§5 trigger #2) so crisis_risk/themes reflect this turn immediately.
        # UNKNOWN (uninvited, not onboarded) does NOT get ordinary memory/profile
        # building — only the deterministic crisis_events audit row above exists.
        # `and user_text` (Phase 2 correction §3): a direct-safety-answer
        # trigger has NO original message text (user_text == "") -- saving an
        # empty "user" message would fabricate a phantom entry in the
        # conversation history, so it is skipped. Every EXPLICIT_MESSAGE call
        # site always has non-empty user_text (risk_detector only matches
        # non-empty text), so this is unchanged behavior for them.
        if role != access_control.UNKNOWN and user_text:
            await save_message(uid, "user", user_text, "crisis", lang,
                               risk["score"], risk["categories"],
                               source=MessageSource.USER_AUTHORED)
            await maybe_update_profile(uid, await get_user_message_count(uid), force=True)
    except Exception as e:
        print(f"[crisis] post-screen persist failed uid={uid}: {e}")
    try:
        # PR 1B-1 checkpoint-2 item 1: single routing decision FIRST. Protective-
        # factor detection is context ONLY for the owner's alert text — it must
        # not be computed/persisted at all for a CLINICIAN_TESTER event (reviewer
        # only ever gets _minimal_reviewer_payload, which never includes it), so
        # gate strictly on kind == "owner" rather than merely role != UNKNOWN.
        kind, targets = access_control.crisis_alert_targets(uid)
        protective = None
        if kind == "owner":
            # Epic A — protective factors: CONTEXT ONLY for a human reviewer.
            # Detected AFTER the screen is delivered; never alters risk or the
            # user's message.
            recent_for_pf = await get_recent_messages(uid, limit=10)
            pf_text = user_text + " " + " ".join(c for _, c in recent_for_pf)
            protective = detect_protective_factors(pf_text)
            if protective:
                await set_crisis_protective_factors(eid, protective)
            await push_alert("Critical Risk", uid, username, risk["level"], risk["score"],
                             risk["categories"], user_text)
            alert = admin_alert_text(uid, username, 0, risk, user_text, eid)
            if protective:
                alert += "\n🛟 Возможные опоры: " + ", ".join(_PF_LABELS.get(p, p) for p in protective)
            for admin_id in targets:
                try:
                    await bot.send_message(admin_id, alert)
                except Exception:
                    pass
        elif kind == "reviewer":
            payload = _minimal_reviewer_payload(uid, eid, "critical risk (RED)")
            for reviewer_id in targets:
                try:
                    await bot.send_message(reviewer_id, payload)
                except Exception:
                    pass
    except Exception as e:
        print(f"[crisis] post-screen alert failed uid={uid}: {e}")


async def journal_guard(message: Message, uid: int, lang: str,
                        text: str | None = None, username: str = "") -> tuple[str, dict]:
    """Single safety gate for every journal free-text point (§2: RED → no
    journaling). Combines two checks:

      1. Active-crisis check — while a recent crisis event is unresolved, no
         journaling happens; we re-show the CURRENT crisis screen (reusing the
         existing event id/stage — never spawning a second crisis_event).
      2. Per-text risk gate (journals.gate over the real detector).

    Returns (decision, risk):
      "crisis"    — active crisis OR RED text; crisis screen already sent, abort
      "ambiguous" — double-meaning phrase; clarifier sent, abort the journal
      "orange"    — elevated; caller must not deepen (skip body / stop CBT)
      "ok"        — proceed with the journal

    Entry points (cmd_emotion/cmd_cbt) pass text=None → active-crisis check only.
    Step handlers pass the user's text → both checks."""
    active = await get_active_crisis(uid)
    if active:
        event_id, stage, _alang = active
        scr, kb = crisis_screen(stage, lang, event_id)
        # §6.1: this crisis screen goes through the delivery ladder too. It is the
        # one crisis send that exists only once PR1 (journal_guard) and §6.1 are
        # both present, so neither PR could wrap it on its own branch.
        await send_crisis(message.answer, scr, kb, lang, uid, event_id, "screen")
        return "crisis", {}
    if text is None:
        return "ok", {}
    level, risk = journals.gate(text, lang)
    if level == RED:
        await trigger_crisis(message, uid, username, text, risk, lang)
        return "crisis", risk
    if risk.get("ambiguous_phrases"):
        await message.answer(get_disambiguation_message(
            risk["ambiguous_phrases"][0], lang, with_hotline=True))
        return "ambiguous", risk
    if level == "ORANGE":
        return "orange", risk
    return "ok", risk


# Hardening §3: MUST be a member of practice_registry.PRODUCTION_PRACTICE_IDS
# -- "grounding_5senses_v1" (the pre-hardening constant here) is CATALOG_ONLY,
# not production-approved, so get_production_practice_by_id() for it always
# returned None; every Controller-issued PRACTICE consent silently fell back
# to generic filler text instead of real steps.
#
# Final closure §6: box breathing is NOT claimed here as universal, suitable
# for every user, or contraindication-free -- it is a temporary, production-
# approved, low-complexity practice used to validate the consent/lifecycle
# infrastructure itself (proposal -> consent -> delivery -> outcome) for an
# explicit PRACTICE request, pending the real Method Registry/selection logic
# a later phase builds. Trains a specific, named skill (slowing attention,
# observing breathing rhythm, practising deliberate regulation) -- it is not
# framed as treating the user's underlying problem.
_PRACTICE_ID = "breathing_box_v1"


def _practice_consent_kb(session_id, proposal_id, lang: str) -> InlineKeyboardMarkup:
    # Hardening §3: callback data carries the exact PROPOSAL identity, not
    # just the session -- a stale/superseded proposal's buttons can never be
    # mistaken for a newer one's, even within the same session.
    labels = [("yes", "Да", "Yes"), ("no", "Нет", "No")]
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=(ru if lang != "en" else en),
                             callback_data=f"cc:consent:{session_id}:{proposal_id}:{val}")
        for val, ru, en in labels]])


def _known_facts_from_handoff(flow: dict) -> list[str]:
    facts = []
    if flow.get("diagnosis_source"):
        facts.append(f"diagnosis_source={flow['diagnosis_source']}")
    answers = safe_load_answers(flow.get("answers_json"))
    for key in ("duration", "functioning", "basic_activities", "support"):
        if answers.get(key):
            facts.append(f"{key}={answers[key]}")
    return facts


def _linked_handoff_is_valid(flow: dict | None) -> bool:
    """Hardening §13: handoff_flow_id existing on a session is NOT enough on
    its own -- re-verified on EVERY turn (a link claimed while healthy can
    go stale later, e.g. a crisis superseding the flow after the fact).
    Only a still-completed, still-claimed, well-formed flow may feed known
    facts into the Controller's prompt."""
    if flow is None:
        return False
    if flow.get("status") != "completed" or flow.get("handoff_status") != "claimed":
        return False
    try:
        answers = safe_load_answers(flow.get("answers_json"))
    except Exception:
        return False
    return isinstance(answers, dict)


async def _controller_claim_turn(uid: int, user_text: str, lang: str, risk: dict) -> dict | None:
    """Phase 3 hardening §5: the FAST, DB-only half of a controller turn --
    called from INSIDE the per-user ingestion lock, exactly where the
    ordinary path does its own inbound persistence, and containing no LLM
    call whatsoever. Returns None when the controller does not own this
    turn (no repair signal, no explicit intent, no active session to
    continue, no handoff) -- the caller falls through to the legacy
    scenario pipeline unchanged. Otherwise returns a bundle for the second,
    slow half (_controller_generate_and_deliver, called AFTER the lock is
    released) to consume.

    Handoff consumption (§5 of round 2): claim_handoff_and_get_or_create_
    session is ONE atomic transaction (claim + session link only --
    interpretation-independent, safe to do unconditionally). The purpose->
    intent mapping happens HERE and is not persisted by that call.

    Continuity (§7 of round 3): an explicit repair signal or a freshly
    classified explicit intent always overrides, in that priority order,
    the handoff-derived intent, which in turn overrides plain continuation
    of an already-OPEN session's existing intent. Only when NONE of those
    apply -- no signal at all, no active session with a real base intent --
    does the controller decline the turn.

    Hardening §6: REPAIR never becomes the persisted base intent (`intent`
    on SessionState) -- it is an overlay, computed fresh into `turn_intent`
    for THIS turn's ResponsePlan only. `base_intent` is what gets written to
    the session."""
    from datetime import datetime, timezone
    handoff, handoff_session = await claim_handoff_and_get_or_create_session(uid)
    known_facts: list[str] = []
    handoff_intent = None
    if handoff is not None and handoff_session is not None:
        known_facts = _known_facts_from_handoff(handoff)
        purpose = safe_load_answers(handoff["answers_json"]).get("purpose")
        handoff_intent = controller.HANDOFF_PURPOSE_TO_INTENT.get(purpose, Intent.UNKNOWN)

    repair_now = controller.classify_repair_signals(user_text)
    overrides = controller.classify_repair_overrides(user_text)
    text_intent = controller.classify_intent(user_text, lang)

    existing_session = handoff_session
    if existing_session is None:
        active_sessions = await list_core_sessions(uid, active_only=True)
        existing_session = active_sessions[0] if active_sessions else None

    has_base = existing_session is not None and existing_session.intent is not Intent.UNKNOWN
    if repair_now:
        turn_intent = Intent.REPAIR
    elif text_intent is not Intent.UNKNOWN:
        turn_intent = text_intent
    elif handoff_intent is not None and handoff_intent is not Intent.UNKNOWN:
        turn_intent = handoff_intent
    elif existing_session is not None and existing_session.lifecycle_status is LifecycleStatus.OPEN and has_base:
        turn_intent = existing_session.intent  # continuation: no new signal, keep governing
    else:
        return None

    base_intent = (existing_session.intent if (repair_now and has_base)
                   else Intent.UNKNOWN if repair_now else turn_intent)
    is_topic_change = (not repair_now and text_intent is not Intent.UNKNOWN
                       and has_base and existing_session.intent is not turn_intent)

    session = existing_session
    if session is None:
        # §2: never an interpretation-dependent intent at creation -- stays
        # UNKNOWN until the SAME authoritative, CAS-protected write this
        # turn's mutations go through in _controller_generate_and_deliver.
        session = await create_core_session(uid)

    # §2: snapshot BEFORE ANY of this turn's own mutations (including the
    # PAUSED->OPEN resume just below) -- must be the exact value still on
    # the DB row right now, or update_core_session_authoritative's CAS would
    # reject this turn's own write as if it were already stale.
    base_state_json = session_json_snapshot(session)

    if session.lifecycle_status is LifecycleStatus.PAUSED:
        # §17: a new explicit-intent message (or a continuation decision,
        # which by construction only fires for an OPEN session) resumes a
        # /start-paused session -- engaging IS the continuation signal.
        session.lifecycle_status = LifecycleStatus.OPEN

    session.intent = base_intent
    if handoff is not None:
        session.handoff_flow_id = str(handoff["id"])

    if not known_facts and session.handoff_flow_id:
        linked = await get_disclosure_flow(session.handoff_flow_id, uid)
        if _linked_handoff_is_valid(linked):
            known_facts = _known_facts_from_handoff(linked)

    # §7: per-constraint repair lifecycle -- decay every currently-active
    # record ONE turn FIRST, then apply this turn's fresh signal (refreshes
    # ONLY the constraints it names) and any explicit override (clears ONLY
    # the constraint it names). An unrelated already-active constraint's own
    # countdown is never touched by either.
    session.decay_repair_turns()
    if repair_now:
        session.add_repair_signal(repair_now, source_turn_id=None,
                                  created_at=datetime.now(timezone.utc).isoformat(),
                                  window_turns=controller.REPAIR_WINDOW_TURNS)
    for cleared in overrides:
        session.clear_repair_constraint(cleared, "explicit_override")
    if turn_intent is Intent.PRACTICE:
        session.clear_repair_constraint(RepairConstraint.EXERCISE_REJECTED, "explicit_practice_request")

    plan = controller.build_response_plan(turn_intent, session.active_repair_constraints)

    # §8: an explicit topic change invalidates a standing PRACTICE proposal
    # -- old consent must not survive a real subject change.
    if is_topic_change:
        await supersede_active_practice_proposals(uid, "topic_change")

    # §3: the exact practice is selected deterministically NOW, before any
    # LLM call, as a persisted proposal -- this turn's prompt AND the
    # eventual consent buttons both reference it by proposal_id, so the LLM
    # can never describe one practice while the callback delivers another.
    #
    # PR #73 request-changes §7: the minimum adverse-history guard -- if the
    # user's LATEST recorded outcome for this exact practice was WORSE,
    # never automatically re-propose it. No alternative-selection logic
    # exists at this layer (that is Phase 5's Method Registry, deliberately
    # out of scope here), so the honest response is to say so and offer
    # nothing else automatically -- never silently repeat what already made
    # things worse, never invent a substitute.
    #
    # External review F2 follow-up: PRACTICE-intent continuation must never
    # automatically propose ANOTHER practice while a progressive refinement
    # is still pending (superseded_reason is a UX_PENDING_* marker and the
    # reporting window is still ACTIVE) -- the user hasn't finished answering
    # the current one yet. `block_if_refinement_pending` below makes this
    # atomic AT THE INSERT ITSELF (create_practice_proposal's own same-
    # connection INSERT...WHERE NOT EXISTS...RETURNING) -- there is no
    # separate pre-read SELECT and therefore no TOCTOU gap for a concurrent
    # callback to establish the marker in between a check and an insert.
    # This does not touch the pending proposal itself (no write, no marker
    # clear, no window change) and does not suppress crisis/disclosure/
    # rollout/topic-change handling, all of which already ran (or already
    # invalidated this session's window) before this point.
    proposal = None
    adverse_guard = False
    if turn_intent is Intent.PRACTICE:
        practice = get_production_practice_by_id(_PRACTICE_ID, lang)
        if practice:
            latest_outcome = await get_latest_outcome_for_practice(uid, practice["id"])
            adverse_guard = latest_outcome == PracticeOutcome.WORSE.value
            # PR #73 ATOMIC CLOSURE §4: still no AUTOMATIC re-proposal text
            # (a generic "дай упражнение" always lands here and the LLM is
            # never called) -- but a REAL, brand-new PENDING proposal is
            # created either way now, so the warning message's consent
            # buttons carry an ordinary, persisted proposal_id through the
            # SAME cb_cc_consent contract every other PRACTICE proposal
            # uses, instead of an unpersisted "cc:worseover" callback
            # payload. is_worse_override records that this exact proposal is
            # an informed repeat, never reusing the old WORSE-outcome row.
            proposal = await create_practice_proposal(
                uid, session.session_id, practice["id"], practice.get("version", "v1"),
                purpose=practice.get("name", _PRACTICE_ID),
                expected_duration=f"{practice.get('duration_min', 5)} минут",
                is_worse_override=adverse_guard,
                block_if_refinement_pending=(
                    UX_PENDING_NOT_COMPLETED_REASON, UX_PENDING_OUTCOME_DETAIL))

    # §7/§8/§9: bounded recent context -- this user's own last few turns (any
    # scenario, not just prior controller ones), fetched BEFORE this turn's
    # own message is saved below so it reflects PRIOR turns only. Small,
    # bounded (get_recent_messages already caps at `limit`), never the full
    # unrestricted history, never treated as instructions (see
    # conversation_controller.build_system_prompt's injection guard).
    recent_rows = await get_recent_messages(uid, limit=6)
    recent_context = [c for r, c in recent_rows if r == "user"]

    # §1: the ACTUAL risk score/categories for this turn, not a hardcoded
    # placeholder -- a Controller-owned turn is still a real inbound message
    # for research/audit purposes.
    await save_message(uid, "user", user_text, "controller", lang,
                       risk.get("score", 0), risk.get("categories", []),
                       source=MessageSource.USER_AUTHORED)
    return {"session": session, "plan": plan, "known_facts": known_facts,
           "recent_context": recent_context, "user_text": user_text, "lang": lang,
           "base_state_json": base_state_json, "proposal": proposal,
           "adverse_guard": adverse_guard}


async def _controller_generate_and_deliver(message: Message, uid: int, claim: dict,
                                            turn_gen: int, risk: dict) -> None:
    """Phase 3 hardening §5: the SLOW half of a controller turn -- called
    strictly AFTER the per-user ingestion lock has been released (never
    holds it across the LLM call, matching PR#67's contract exactly)."""
    session, plan, known_facts = claim["session"], claim["plan"], claim["known_facts"]
    recent_context = claim.get("recent_context")
    user_text, lang = claim["user_text"], claim["lang"]

    proposal = claim.get("proposal")

    # PR #73 request-changes §7 / ATOMIC CLOSURE §4: adverse-history guard
    # fired during claim -- skip the LLM entirely and answer with a fixed,
    # honest, non-inviting warning naming the exact practice, the user's
    # own prior WORSE report (never claiming the practice caused it), its
    # purpose, and its approximate duration. A brand-new PENDING proposal
    # (claim["proposal"], is_worse_override=True, never the old WORSE-
    # outcome row) carries the consent buttons through the ORDINARY
    # cb_cc_consent contract -- same ownership/expiry/session/supersession
    # rules as any other PRACTICE proposal, no parallel callback contract.
    if claim.get("adverse_guard") and proposal is not None:
        name, duration = proposal.purpose, proposal.expected_duration
        text = (f"В прошлый раз ты сообщил(а), что практика «{name}» ({duration}) "
                f"— судя по твоему отклику — не помогла, стало хуже. Это не значит, "
                f"что дело точно в ней. Хочешь всё равно попробовать ещё раз?"
                if lang != "en" else
                f"Last time you reported that the practice \"{name}\" ({duration}) "
                f"made things worse, based on what you told me. That doesn't "
                f"necessarily mean it was the cause. Do you still want to try it again?")
        if _user_generation_superseded(uid, turn_gen):
            return
        session.consent = ConsentState.PENDING
        if not await update_core_session_authoritative(session, claim["base_state_json"]):
            return
        if not await transition_practice_proposal(
                proposal.proposal_id, uid, from_status="PROPOSED", to_status="PENDING"):
            return
        reply_markup = _practice_consent_kb(session.session_id, proposal.proposal_id, lang)
        try:
            sent = await deliver_response(message, uid, text, lang, reply_markup=reply_markup)
        except Exception as e:
            print(f"[controller] adverse-guard delivery failed uid={uid}: {type(e).__name__}: {e}")
            await transition_practice_proposal(
                proposal.proposal_id, uid, from_status="PENDING",
                to_status="DELIVERY_FAILED", reason="send_exception")
            return
        await mark_proposal_delivered(proposal.proposal_id, uid, sent.message_id)
        await save_message(uid, "assistant", text, "controller", lang,
                           source=MessageSource.ASSISTANT_DELIVERED)
        return
    practice_name = proposal.purpose if proposal is not None else None
    expected_duration = proposal.expected_duration if proposal is not None else None
    system_prompt = controller.build_system_prompt(plan, lang, known_facts, recent_context)
    if proposal is not None:
        # §3: tell the model the EXACT proposal it must describe -- the
        # consent buttons below reference this SAME proposal_id, so a
        # mismatch between "what the model described" and "what gets
        # delivered on GRANTED" is structurally impossible, not just unlikely.
        system_prompt += (
            f" Точная практика для описания (назови именно её, не другую): "
            f"{practice_name} ({expected_duration})." if lang != "en" else
            f" Exact practice to describe (name it exactly, not a different "
            f"one): {practice_name} ({expected_duration}).")

    async def _generate() -> str:
        try:
            completion = await client.chat.completions.create(
                model="gpt-4o-mini", temperature=0.65, max_tokens=300,
                messages=[{"role": "system", "content": system_prompt},
                         {"role": "user", "content": user_text}])
            out = (completion.choices[0].message.content or "").strip()
            return out or controller.fallback_text(
                lang, plan.intent, known_facts=known_facts,
                practice_name=practice_name, expected_duration=expected_duration)
        except Exception as e:
            print(f"[controller] LLM call failed uid={uid}: {type(e).__name__}: {e}")
            return controller.fallback_text(
                lang, plan.intent, known_facts=known_facts,
                practice_name=practice_name, expected_duration=expected_duration)

    def _valid(candidate: str) -> bool:
        ok, _ = controller.validate_controller_response(
            candidate, plan, known_facts=known_facts, practice_name=practice_name)
        if not ok:
            return False
        # §12: the STRONGER context-aware validator, not the plain one --
        # never a weaker safety path than the ordinary pipeline uses.
        ok, _ = validate_response_with_context(candidate, user_text, risk, lang)
        return ok

    text = await _generate()
    if not _valid(text):
        text = controller.fallback_text(
            lang, plan.intent, known_facts=known_facts,
            practice_name=practice_name, expected_duration=expected_duration)
        if not _valid(text):
            # §11: fall back to the STATIC per-intent table entry -- no
            # dynamic known_facts/practice_name substitution that could
            # itself be what made the previous attempt fail. Proven to pass
            # validate_controller_response for EVERY Intent, in both
            # languages, by test_static_per_intent_fallbacks_pass_validation.
            text = controller.fallback_text(lang, plan.intent)
            if not _valid(text):
                # Absolute last resort -- kept as a real (validated, not
                # assumed) fourth tier rather than delivering unvalidated
                # text; unreachable in practice given the tier above is
                # proven to always pass, but never trusted blindly either.
                text = controller.fallback_text(lang)

    # Stale-response guard (§3 of round 2): the SAME suppression the
    # ordinary LLM path uses (PR#67) -- a newer turn for this user
    # superseded this one while the LLM call above was in flight. No state
    # mutation, no persistence, no delivery.
    if _user_generation_superseded(uid, turn_gen):
        return

    # PRACTICE requires explicit consent (§3/§10) -- deterministic Да/Нет
    # buttons tied to the exact proposal, never LLM-parsed free text. The
    # ENTIRE session write (including the best-effort consent=PENDING
    # mirror) happens in ONE authoritative, CAS-protected write BEFORE the
    # buttons are ever shown, closing the fast-tap race: a callback arriving
    # the instant the buttons render always finds a durably PENDING row.
    reply_markup = None
    if plan.intent is Intent.PRACTICE and proposal is not None:
        # session.consent mirrors the proposal's status for older readers;
        # the proposal's OWN status (see below) is the real gate now (§3).
        session.consent = ConsentState.PENDING
        reply_markup = _practice_consent_kb(session.session_id, proposal.proposal_id, lang)
    elif plan.intent is Intent.CLOSE_CONVERSATION:
        # §8: an explicit close is a deliberate, user-initiated end -- a
        # terminal status, distinct from PAUSED (which is for /start/crisis
        # interruptions the user did not ask to end).
        session.lifecycle_status = LifecycleStatus.COMPLETED

    # §2: authoritative CAS write -- rejects this turn if ANYTHING else
    # (a newer turn's own write, /start's pause, a crisis supersession)
    # touched this row since claim time, closing the TOCTOU gap between the
    # generation check above and this write reaching the database.
    if not await update_core_session_authoritative(session, claim["base_state_json"]):
        return  # superseded -- do not persist assistant output, do not deliver

    if plan.intent is Intent.PRACTICE and proposal is not None:
        # §4: the proposal itself may have been superseded/invalidated
        # between claim time and now (e.g. a concurrent topic-change turn's
        # claim ran and superseded it) -- its own CAS is the authority.
        if not await transition_practice_proposal(
                proposal.proposal_id, uid, from_status="PROPOSED", to_status="PENDING"):
            return
    elif plan.intent is Intent.CLOSE_CONVERSATION:
        # §8: closing the conversation invalidates any standing proposal too.
        await supersede_active_practice_proposals(uid, "conversation_closed")

    # §5/§12: persist PENDING before the send attempt (already done above),
    # then handle a real Telegram delivery failure explicitly -- never leave
    # a proposal PENDING forever with no way for the user to act on it.
    # Delivery goes through the ONE shared delivery contract (deliver_response)
    # -- reply_markup forces plain text there, matching this flow's existing
    # "consent is never voiced" contract byte-for-byte, while still keeping
    # stale-suppression (already checked above, before any write) and future
    # voice/reaction compatibility for the non-PRACTICE case.
    try:
        sent = await deliver_response(message, uid, text, lang, reply_markup=reply_markup)
    except Exception as e:
        print(f"[controller] delivery failed uid={uid}: {type(e).__name__}: {e}")
        if plan.intent is Intent.PRACTICE and proposal is not None:
            await transition_practice_proposal(
                proposal.proposal_id, uid, from_status="PENDING",
                to_status="DELIVERY_FAILED", reason="send_exception")
        return
    if plan.intent is Intent.PRACTICE and proposal is not None:
        await mark_proposal_delivered(proposal.proposal_id, uid, sent.message_id)
    await save_message(uid, "assistant", text, "controller", lang,
                       source=MessageSource.ASSISTANT_DELIVERED)


# Professional Free-Text Runtime V1 -- bounded, deterministic, technical-only.
# Never pseudo-therapeutic, never advice, never a diagnosis, never an
# emotional interpretation, never a question beyond "try again". Never routed
# through the legacy generator or the Professional Renderer -- it is fixed
# copy, not a model candidate, so it can never carry a rejected/unsafe answer.
_PROFESSIONAL_TECHNICAL_FALLBACK_TEXT = {
    "ru": "Не получилось корректно сформировать ответ. Попробуй отправить сообщение ещё раз.",
    "en": "I couldn't generate a reliable response. Please send your message again.",
}


def _professional_technical_fallback_text(lang: str) -> str:
    return _PROFESSIONAL_TECHNICAL_FALLBACK_TEXT.get(lang, _PROFESSIONAL_TECHNICAL_FALLBACK_TEXT["en"])


# Telegram delivery in this path is plain text (no parse_mode), so a model
# candidate that used **bold** markdown leaks the literal asterisks to the
# user. This is a pure, deterministic presentation-layer cleanup: it removes
# only a matched, paired **...** delimiter, keeping the exact text between
# them untouched -- an unpaired/stray "**" is left alone rather than guessed
# at. Not a Markdown renderer (no parse_mode is ever enabled), so there is no
# broader formatting-injection surface to introduce.
_LEAKED_BOLD_MARKDOWN_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def _strip_leaked_bold_markdown(text: str) -> str:
    return _LEAKED_BOLD_MARKDOWN_RE.sub(r"\1", text)


# Therapist Core V1's own low-risk validator-rejection fallback (owner-review
# live-smoke round 2). safety_validator.FALLBACK_RU/get_fallback() is shared
# with the legacy pipeline (see bot.py's other select_fallback call sites), so
# it is intentionally NOT changed here -- this is a Therapist-Core-scoped
# override, selected by the already-known, already-deterministic
# interaction_contract, that never asks the user to repeat what they already
# wrote this turn. Elevated risk / an ambiguous user message is UNCHANGED:
# is_elevated_risk() reuses safety_validator's own single-sourced predicate,
# so the two can never silently drift apart, and falls straight through to
# select_fallback's existing high-risk (hotline-carrying) text.
_THERAPIST_CORE_LOW_RISK_FALLBACK = {
    "ru": {
        "UNDERSTAND": (
            "Если ты хочешь понять, что здесь происходит, я бы не начинал с общего "
            "совета. Полезнее посмотреть на саму последовательность: что запускает "
            "реакцию, какая мысль или ожидание появляется первой, что происходит "
            "дальше и что меняется после этого. Если такая цепочка уже видна из "
            "твоего описания, можно разбирать её; если пока нет — не буду её "
            "додумывать. Какой последний конкретный эпизод лучше всего показывает "
            "эту реакцию?"),
        "JUST_TALK": (
            "Я прочитал то, что ты написал. Не буду просить повторять. "
            "Можешь продолжить с этого места — я буду держать нить разговора."),
        "ACTION": (
            "Я прочитал то, что ты написал. Не буду просить повторять. "
            "Давай опираться на уже сказанное и выберем следующий шаг."),
        "NONE": (
            "Я прочитал то, что ты написал. Не буду просить повторять. "
            "Давай продолжим оттуда и опираться на уже сказанное."),
    },
    "en": {
        "UNDERSTAND": (
            "If you want to understand what's going on here, I wouldn't start "
            "with generic advice. It's more useful to look at the sequence "
            "itself: what triggers the reaction, which thought or expectation "
            "shows up first, what happens next, and what changes afterward. If "
            "that sequence is already visible from what you've described, we "
            "can work through it; if not yet, I won't invent it. What's the "
            "most recent concrete episode that best shows this reaction?"),
        "JUST_TALK": (
            "I've read what you wrote. I won't ask you to repeat it. "
            "You can continue from here — I'll keep track of the thread."),
        "ACTION": (
            "I've read what you wrote. I won't ask you to repeat it. "
            "Let's build on what's already been said and choose a next step."),
        "NONE": (
            "I've read what you wrote. I won't ask you to repeat it. "
            "Let's continue from there, building on what's already been said."),
    },
}


def _therapist_core_fallback(risk: dict, interaction_contract: str, lang: str) -> str:
    if is_elevated_risk(risk):
        return select_fallback(risk, lang)
    by_lang = _THERAPIST_CORE_LOW_RISK_FALLBACK.get(lang, _THERAPIST_CORE_LOW_RISK_FALLBACK["en"])
    return by_lang.get(interaction_contract, by_lang["NONE"])


async def _run_therapist_core_v1_and_deliver(
        message: Message, uid: int, current_row_id: int, user_text: str,
        risk: dict, lang: str, interaction_contract: str,
        turn_gen: int, cid: str,
        reaction_category: ReactionCategory, reaction_confidence: float,
        one_shot_voice: bool = False, one_shot_concise: bool = False) -> None:
    """Final lifecycle for a Core-owned turn: one call, one safety decision."""
    _dispatch_log(f"cid={cid} stage=therapist_core_v1_claimed")
    await _maybe_react(message, uid, reaction_category, reaction_confidence)
    try:
        rows = await get_professional_conversation_history_rows(uid, current_row_id)
        context = build_conversation_context_from_history_rows(rows)
        candidate = await generate_therapist_core_v1(
            client=client,
            model=config.THERAPIST_CORE_V1_MODEL,
            source_text=user_text,
            conversation_context=context,
            risk_result=risk,
            lang=lang,
            interaction_contract=interaction_contract,
            max_completion_tokens=config.THERAPIST_CORE_V1_MAX_COMPLETION_TOKENS,
        )
        accepted, reason = validate_response_with_context(candidate, user_text, risk, lang)
        if accepted:
            reply_text = candidate
            _dispatch_log(f"cid={cid} stage=therapist_core_v1_accepted")
        else:
            reply_text = _therapist_core_fallback(risk, interaction_contract, lang)
            _dispatch_log(
                f"cid={cid} stage=therapist_core_v1_rejected "
                f"validator_rejection={classify_rejection_reason(reason)}")
    except Exception as exc:
        reply_text = _professional_technical_fallback_text(lang)
        _dispatch_log(
            f"cid={cid} stage=therapist_core_v1_failed "
            f"error_type={type(exc).__name__}")

    # Applied to every reply_text source (accepted candidate, validator
    # fallback, technical fallback) uniformly -- a no-op on the static
    # fallback/crisis copy that never contains "**", so it is safe regardless
    # of source. Runs BEFORE delivery and persistence so the persisted
    # ASSISTANT_DELIVERED content always equals what the user actually saw.
    reply_text = _strip_leaked_bold_markdown(reply_text)

    if _user_generation_superseded(uid, turn_gen):
        _dispatch_log(f"cid={cid} stage=therapist_core_v1_stale_dropped")
        return
    try:
        await deliver_response(
            message, uid, reply_text, lang,
            one_shot_voice=one_shot_voice,
            one_shot_concise=one_shot_concise,
            preserve_exact_text=True,
        )
    except Exception as exc:
        _dispatch_log(
            f"cid={cid} stage=therapist_core_v1_send_failed "
            f"error_type={type(exc).__name__}")
        return
    try:
        await save_message(
            uid, "assistant", reply_text, "therapist_core_v1", lang,
            source=MessageSource.ASSISTANT_DELIVERED,
        )
    except Exception as exc:
        _dispatch_log(
            f"cid={cid} stage=therapist_core_v1_persist_failed "
            f"error_type={type(exc).__name__}")
        return


async def _run_professional_free_text_and_deliver(
        message: Message, uid: int, current_row_id: int, user_text: str,
        risk: dict, lang: str, turn_gen: int, cid: str,
        one_shot_voice: bool = False, one_shot_concise: bool = False) -> None:
    """The SLOW half of a Professional-claimed turn -- called strictly AFTER
    the per-user ingestion lock has been released (same contract as
    _controller_generate_and_deliver: Professional's up-to-three model calls
    must never hold that lock). The current USER row is already persisted
    (source=USER_AUTHORED, scenario="professional") by the time this runs.

    Structural no-silent-legacy-fallback guarantee: every branch below ends
    in `return` with no path back into pipeline()'s legacy/first-turn/
    Controller code -- this function is the entire remainder of a
    Professional-claimed turn's lifecycle.

    one_shot_voice/one_shot_concise: forwarded from pipeline()'s own format-
    meta-command handling (e.g. "мне тревожно, ответь голосом" mixed with
    genuine psychological content still selects voice transport for a
    Professional turn) -- but deliver_response is always called with
    preserve_exact_text=True below, so one_shot_concise (and any stored
    response_length="concise" preference) can never shorten the accepted
    Professional text or the technical fallback; the concise PRESENTATION
    request is silently inert for V1, per the frozen exact-content rule,
    while the psychological content is still fully handled by Professional
    (never diverted to legacy)."""
    _dispatch_log(f"cid={cid} stage=professional_claimed")
    try:
        rows = await get_professional_conversation_history_rows(uid, current_row_id)
        context = build_conversation_context_from_history_rows(rows)
        runtime_context = ProfessionalTurnRuntimeContext(conversation=context)
        result = await run_professional_free_text_turn(
            client=client, model="gpt-4o-mini",
            source_message_row_id=current_row_id, source_text=user_text,
            runtime_context=runtime_context, risk_result=risk, lang=lang)
    except Exception as e:
        _dispatch_log(f"cid={cid} stage=professional_failed error_type={type(e).__name__}")
        result = None

    if result is None:
        reply_text = _professional_technical_fallback_text(lang)
    elif result.status is ProfessionalFreeTextRuntimeStatus.SUCCESS:
        t = result.success_trace
        _dispatch_log(
            f"cid={cid} stage=professional_success "
            f"analyzer_status={t.analysis_status.value} "
            f"interaction_status={t.interaction_status.value} "
            f"optional_context_recovery_used={t.optional_context_recovery_used} "
            f"objective={t.objective.value} move={t.primary_response_move.value} "
            f"question_allowed={t.question_allowed} "
            f"clarification_target_present={t.clarification_target_present} "
            f"bounded_alternative_used={t.bounded_alternative_used} "
            f"acceptance={t.acceptance.value}")
        reply_text = result.reply_text
    elif result.status is ProfessionalFreeTextRuntimeStatus.REJECTED:
        _detail_part = f" detail={result.failure_detail.value}" if result.failure_detail is not None else ""
        _dispatch_log(
            f"cid={cid} stage=professional_rejected "
            f"pro_stage={result.failure_stage.value} reason={result.failure_reason.value}{_detail_part}")
        reply_text = _professional_technical_fallback_text(lang)
    else:
        _detail_part = f" detail={result.failure_detail.value}" if result.failure_detail is not None else ""
        _dispatch_log(
            f"cid={cid} stage=professional_failed "
            f"pro_stage={result.failure_stage.value} reason={result.failure_reason.value}{_detail_part}")
        reply_text = _professional_technical_fallback_text(lang)

    # Final stale check, as late as possible -- right before the point of no
    # return (send) -- same timing convention the legacy path already uses
    # (see the identical check in pipeline() before its own assistant save).
    if _user_generation_superseded(uid, turn_gen):
        _dispatch_log(f"cid={cid} stage=professional_stale_dropped")
        return

    try:
        await deliver_response(message, uid, reply_text, lang,
                               one_shot_voice=one_shot_voice, one_shot_concise=one_shot_concise,
                               preserve_exact_text=True)
    except Exception as e:
        _dispatch_log(f"cid={cid} stage=professional_send_failed error_type={type(e).__name__}")
        return

    try:
        await save_message(uid, "assistant", reply_text, "professional", lang,
                           source=MessageSource.ASSISTANT_DELIVERED)
    except Exception as e:
        _dispatch_log(f"cid={cid} stage=professional_persist_failed error_type={type(e).__name__}")
        return


async def pipeline(message: Message, user_text: str, fsm_state: FSMContext | None = None,
                   tg_user=None) -> None:
    """Complete X20 pipeline.

    tg_user: при вызове из callback-кнопки message.from_user — это бот,
    поэтому реальный пользователь передаётся явно (callback.from_user)."""
    u = tg_user or message.from_user
    uid, username, first_name = u.id, u.username or "", u.first_name or ""
    # New user turn: bump the generation (also done for crisis, which returns
    # earlier) so any older ordinary answer still in flight is superseded, and
    # capture this turn's generation to check just before ordinary delivery.
    _turn_gen = _bump_user_generation(uid)
    _cid = _new_correlation_id()
    # Voice and Adaptive Response UX is private-chat-only in V1 (§4): this
    # bot has no ChatType filtering anywhere else either, so this is a new,
    # narrow, explicit boundary for just this feature, not a bot-wide policy
    # change. `chat.type` is a standard field on every real aiogram Message.
    is_private_chat = getattr(message.chat, "type", "private") == "private"

    # 1. Detect language (pure, no I/O — safe to run before any access check)
    lang = detect_language(user_text)

    dass_discussion_result = None
    dass_discussion_session_id = None

    # 2. Risk detection (pure, no I/O)
    risk = detect_risk(user_text, lang)

    # 3.9 Active-crisis gate — while a recent crisis event is unresolved, the LLM
    # is OFF and we don't return to normal chat. Free text either keeps the crisis
    # screen (RED/ORANGE) or gently offers "Я в безопасности" (calm). The 24h
    # recency window in get_active_crisis bounds this so nobody is stuck forever.
    # Crisis-adjacent like the RED branch below — runs regardless of role/access,
    # structurally BEFORE the product-access gate.
    # Ingestion lock: acquired here (only pure language/risk detection
    # runs before this point, so acquire order == entry order). Held
    # through the user-row save only; released before summarization,
    # the answer LLM, reaction sending, TTS and delivery.
    _ingest = await _ingest_enter(uid)
    try:
        active = await get_active_crisis(uid)
        if active and not (tg_user is not None):
            await _clear_dass21_discussion(fsm_state)
            event_id, stage, alang = active
            lvl = classify(risk)
            # Default to the crisis screen. Only EXPLICITLY reassuring text (and not
            # RED/ORANGE) gets the gentle "I'm safe" offer — anything with distress
            # ("мне плохо, я не в безопасности") or anything unclear keeps the crisis
            # screen. Never assume safety.
            if lvl not in (RED, ORANGE) and is_reassuring(user_text):
                await message.answer(
                    "Я рядом. Если ты сейчас в большей безопасности — нажми ниже, "
                    "и мы спокойно продолжим." if lang == "ru" else
                    "I'm here. If you're safer now, tap below and we'll continue gently.",
                    reply_markup=safe_only_keyboard(event_id, lang))
            else:
                # PR 1B-1: same role-gated bookkeeping as trigger_crisis — an UNKNOWN
                # (uninvited) uid does not get ordinary message/profile persistence.
                if access_control.resolve_role_safe(uid) != access_control.UNKNOWN:
                    await save_message(uid, "user", user_text, "crisis", lang,
                                       risk["score"], risk["categories"],
                                       source=MessageSource.USER_AUTHORED)
                text, kb = crisis_screen(stage, lang, event_id)
                await send_crisis(message.answer, text, kb, lang, uid, event_id, "screen")
            return

        # 4. Crisis override (Epic 1 — Crisis Protocol; LLM is NEVER called here).
        # RED bypasses the product-access gate below entirely, for ANY role — the
        # crisis path must never be gated by access control.
        if classify(risk) == RED:
            await _clear_dass21_discussion(fsm_state)
            await trigger_crisis(message, uid, username, user_text, risk, lang)
            return

        # 4.1 Product access gate — strictly AFTER both crisis paths above, and
        # BEFORE any ordinary product persistence (upsert_user/log_moderation/state/
        # profile/memory/LLM). UNKNOWN, CLINICIAN_REVIEWER, an unacknowledged
        # CLINICIAN_TESTER, or an acknowledged tester with no reviewer mapping all
        # get the closed-test/tester-acknowledgment screen instead, and NOTHING
        # ordinary is written about them.
        if not await ensure_full_access_or_closed_test(message, uid):
            await _clear_dass21_discussion(fsm_state)
            return

        # 4.2 Mandatory onboarding gate (spec item A) — strictly AFTER both crisis
        # paths AND the access gate, and BEFORE any ordinary product persistence.
        # A user with an ACTIVE first-user onboarding must not reach ordinary text/
        # voice conversation by typing through it — this re-shows their current
        # onboarding card (editing it in place, never flooding the chat) instead of
        # silently dropping the message or letting it fall into the pipeline.
        # Unconditional (not skipped when called from cb_mood, which already runs
        # this same check before ever calling pipeline()) -- a second read of the
        # same DB state here is a harmless no-op when already blocked/cleared
        # upstream, and this way pipeline() is safe to call from ANY entrypoint
        # without relying on a caller-specific signal to know whether the gate was
        # already checked.
        if await _onboarding_blocks_ordinary_entry(uid):
            await _clear_dass21_discussion(fsm_state)
            await _resume_onboarding_card(message.chat.id, uid)
            return

        # 4.3 Per-user monotonic revision (spec item B) -- bumped exactly once
        # per ordinary turn, strictly after the crisis/access/onboarding gates
        # above (a gate-rejected request never reaches here) and before any
        # long pipeline work. Used as response_revision for the first-turn
        # continuation-button binding below.
        user_revision = await bump_user_revision(uid)

        # 5. Ordinary persistence — only now that access is confirmed.
        await upsert_user(uid, username, first_name, lang)
        await reset_unanswered(uid)   # user re-engaged → clear ignored-push backoff

        # 3. Log if medium+
        if risk["level"] in ("medium", "high", "critical"):
            await log_moderation(uid, username, first_name, risk["level"], risk["score"],
                                  risk["categories"], user_text, "pending", risk["implicit"])

        # Aggression signal — checkpoint-2 item 2: routed through access_control
        # instead of an unconditional push_alert. By construction we only reach here
        # for a role that already has full product access (OWNER, or an
        # acknowledged+mapped CLINICIAN_TESTER); should_alert_owner is False for a
        # tester, so no owner alert and no raw-text leak happens for them. RED+
        # aggression never reaches here (RED already returned above), so there is
        # never a duplicate owner alert.
        if "aggression" in risk["categories"] and access_control.should_alert_owner(uid):
            await push_alert("Aggression Detected", uid, username, risk["level"],
                             risk["score"], risk["categories"], user_text)

        # 4.3 Depression Disclosure Gate (Phase 2, master prompt §13). Deterministic
        # only -- no LLM call. Runs strictly AFTER the RED crisis check near the top
        # of this function, so explicit suicide/self-harm language always wins: a
        # message containing BOTH crisis language AND a depression disclosure never
        # reaches this line at all (RED already returned). classify_disclosure()
        # itself excludes negation/third-person/meta-question/quoted-or-hypothetical
        # -- only a genuinely eligible first-person disclosure reaches here.
        # Gated by the ONE centralized rollout helper (Phase 2 correction §4) --
        # gate flag AND core_rollout_allowed -- entirely inert while either is off
        # (byte-for-byte prior behavior, nothing created).
        if await access_control.depression_disclosure_allowed_for(uid):
            active_flow = await get_active_disclosure_flow(uid)
            if classify_disclosure(user_text, lang) == "POSITIVE":
                # §8: a new eligible disclosure supersedes an older pending flow
                # (rather than being silently skipped) before creating the new one.
                if active_flow is not None:
                    await close_disclosure_flow(active_flow["id"], uid,
                                                from_step=active_flow["step"],
                                                status="cancelled",
                                                superseded_reason="new_disclosure")
                # Hardening §4: a new Depression Disclosure flow (a fresh
                # safety re-assessment) also invalidates any standing
                # PRACTICE proposal for this user.
                await supersede_active_practice_proposals(uid, "new_disclosure_flow")
                flow = await create_disclosure_flow(uid, lang, origin_message_id=message.message_id)
                await save_message(uid, "user", user_text, "depression_disclosure", lang,
                                   risk["score"], risk["categories"],
                                   source=MessageSource.USER_AUTHORED)
                text = safety_check_text(lang)
                sent = await message.answer(text, reply_markup=_dd_safety_check_kb(flow["id"], lang))
                prompt_mid = getattr(sent, "message_id", None)
                if prompt_mid is not None:
                    await set_disclosure_prompt_message_id(flow["id"], uid, prompt_mid)
                await save_message(uid, "assistant", text, "depression_disclosure", lang,
                                   source=MessageSource.ASSISTANT_DELIVERED)
                await _clear_dass21_discussion(fsm_state)
                return
            if active_flow is not None:
                # §8: an unrelated ordinary message while a flow is pending is a
                # topic change -- the old flow's buttons become inert (silently;
                # the ordinary message below still gets its normal reply, the
                # user is never forced back to the old topic).
                await close_disclosure_flow(active_flow["id"], uid,
                                            from_step=active_flow["step"],
                                            status="cancelled", superseded_reason="new_topic")

        # 4.4 Emotional trajectory (§4) — deterministic aggregate of PRIOR messages
        # (current one not saved yet). Used to amplify ambiguity and bias routing.
        trajectory = await get_emotional_trajectory(uid, window_hours=24)

        # 4.5 Ambiguity check (v3 hotfix) — runs BEFORE any LLM call.
        # A double-meaning phrase ("выйти в окно") must trigger a deterministic
        # clarifying question, never an LLM guess. With recent risk history we also
        # surface the hotline. This is the direct fix for the endorsement incident.
        if risk.get("ambiguous_phrases"):
            recent = await get_recent_messages(uid, limit=10)
            signal = amplify_ambiguity_by_context(risk["ambiguous_phrases"], recent)
            # §4: trajectory upgrades a soft "force_disambiguation" to "force_crisis"
            # when aggregated dynamics show deterioration or a chronic risk streak —
            # closing the gap where raw last-message scanning would miss it.
            if signal and (trajectory.trend == "deteriorating"
                           or trajectory.hopelessness_streak >= 3
                           or trajectory.yellow_plus_streak >= 5):
                signal = "force_crisis"
            if signal:
                phrase = risk["ambiguous_phrases"][0]
                disambig = get_disambiguation_message(
                    phrase, lang, with_hotline=(signal == "force_crisis"))
                await save_message(uid, "user", user_text, "disambiguation", lang,
                                   risk["score"], risk["categories"],
                                   source=MessageSource.USER_AUTHORED)
                await save_message(uid, "assistant", disambig, "disambiguation", lang,
                                   source=MessageSource.ASSISTANT_DELIVERED)
                await message.answer(disambig)
                await log_disambiguation(uid, user_text, phrase, signal)
                await _clear_dass21_discussion(fsm_state)
                return

        # 3.5 Dependency monitor -- the ONE deterministic authority (Therapeutic
        # Core Foundation): consolidates the behavioural-pattern signals (this
        # module) and the explicit-phrase signal (relationship_monitor) behind a
        # single shared cooldown gate. record_message MUST come first so the
        # current message is counted before the threshold check -- otherwise the
        # 100th message never triggers. A non-None result is a soft, narrow
        # redirect that REPLACES the ordinary reply for this turn (never both),
        # matching CLINICAL_BOUNDARY.md §2.3 -- it is never crisis protocol, and
        # this check always runs strictly after the crisis/RED checks above.
        await dependency_monitor.record_message(uid)
        dep_msg = await dependency_monitor.assess(uid, user_text, lang)
        if dep_msg:
            await message.answer(dep_msg)
            return

        if (fsm_state is not None
                and await fsm_state.get_state() == Dass21Discussion.active.state):
            discussion_data = await fsm_state.get_data()
            candidate_session_id = discussion_data.get("dass21_session_id")
            session = None
            if isinstance(candidate_session_id, int) and candidate_session_id > 0:
                session = await _load_owned_completed_history_dass(candidate_session_id, uid)
            if session is not None:
                dass_discussion_result = await _dass21_discuss_gate_and_load(session, lang)
            if dass_discussion_result is None:
                await fsm_state.clear()
                await message.answer(questionnaire_ux.not_available_text(lang))
                return
            dass_discussion_session_id = candidate_session_id

        # 4.3 Format meta-command detection (Voice and Adaptive Response UX) --
        # AFTER crisis/dependency handling, BEFORE ordinary therapeutic routing.
        # Private-chat-only in V1 (§4): a group/supergroup message never even
        # enters this block, so nothing here can be armed, consumed, or replayed
        # from a non-private chat -- ordinary text handling for a group message
        # is completely unaffected (not a bot-wide policy change).
        # A MIXED message ("Мне тревожно, и ответь голосом") falls through to
        # the ordinary pipeline below unchanged -- only delivery of the eventual
        # answer is affected. A PURE command never enters therapeutic routing.
        one_shot_voice = False
        one_shot_concise = False
        voice_ux_active = await _voice_ux_enabled_for(uid) and is_private_chat

        # Consume a one-shot voice override armed by a PRIOR Telegram update (see
        # the "no previous response yet" branch below) -- a plain local variable
        # cannot survive past the end of THIS function call, so the override is
        # persisted in FSM state. aiogram's default FSMStrategy is USER_IN_CHAT
        # (confirmed: bot.py's Dispatcher(storage=MemoryStorage()) never
        # overrides fsm_strategy), so `fsm_state` is ALREADY scoped per (chat,
        # user) -- the same user in a different chat gets a completely separate
        # FSM entry. Cleared the instant it is read (whether still within TTL or
        # already expired): it can apply to at most ONE subsequent ordinary
        # reply, is never written to the DB, and is never a permanent
        # preference. Crisis and dependency both return earlier in pipeline()
        # than this point, so an intervening crisis/dependency message leaves an
        # armed override untouched -- PRESERVED for the next ordinary message,
        # the chosen deterministic rule (not silently dropped, not consumed by
        # a non-ordinary reply).
        if voice_ux_active and fsm_state is not None:
            pending = await fsm_state.get_data()
            if pending.get("one_shot_voice_pending"):
                armed_at = pending.get("one_shot_voice_pending_at") or 0
                if time.time() - armed_at <= config.VOICE_ONE_SHOT_OVERRIDE_TTL_SECONDS:
                    one_shot_voice = True
                await fsm_state.update_data(one_shot_voice_pending=False,
                                            one_shot_voice_pending_at=None)

        # Detection itself is flag-gated ONLY (cheap, stateless, no I/O) -- but
        # every ACTION (persistence, replay, override arming, voice delivery)
        # below remains private-chat-only. This matters specifically for a PURE
        # command outside a private chat: it must be recognized and short-
        # circuited with a neutral notice, never silently sent to the
        # therapeutic LLM just because chat.type != "private" made detection
        # itself unavailable (the earlier, narrower gate did exactly that).
        fmt_cmd = (
            parse_format_command(user_text, lang)
            if config.VOICE_REPLIES_ENABLED and config.ELEVENLABS_TTS_ENABLED
            else None
        )
        if fmt_cmd:
            pure = is_pure_format_command(user_text, lang)
            if pure and not voice_ux_active:
                # §5 (private-chat boundary) + owner-only canary gate: a pure
                # meta-command from a non-private chat OR a non-owner user (even
                # in their own private chat) must never replay, never arm an
                # override, never touch a preference, and never enter
                # therapeutic routing -- a short neutral notice, then stop.
                # voice_ux_active already folds in flag + owner + private-chat.
                await message.answer(
                    "Настройки формата и озвучивание доступны в личном чате с ботом." if lang == "ru"
                    else "Format settings and voice replies are only available in a private chat with me.")
                return

            if voice_ux_active:
                if fmt_cmd.kind == "voice_persistent":
                    await set_response_preference(uid, response_format="voice")
                elif fmt_cmd.kind == "text_persistent":
                    await set_response_preference(uid, response_format="text")
                elif fmt_cmd.kind == "concise_persistent":
                    await set_response_preference(uid, response_length="concise")
                elif fmt_cmd.kind == "voice_oneshot":
                    one_shot_voice = True
                elif fmt_cmd.kind == "concise_oneshot":
                    one_shot_concise = True

                if pure and fmt_cmd.persistent:
                    await message.answer(_format_ack_text(fmt_cmd, lang))
                    return
                if pure and fmt_cmd.kind == "voice_oneshot":
                    # §8: "много текста"/"лень читать" etc. -- voice-ify the LAST
                    # SUCCESSFULLY DELIVERED ordinary response instead of
                    # generating a new therapeutic interpretation, when one is
                    # still available. Sourced from FSM state
                    # (last_delivered_response), NOT the database -- FSM is
                    # scoped per (chat, user) by aiogram itself, so a
                    # private-chat reply can never be replayed from a different
                    # chat the same Telegram user happens to also be in.
                    fdata = await fsm_state.get_data() if fsm_state is not None else {}
                    last = fdata.get("last_delivered_response")
                    last_at = fdata.get("last_delivered_response_at") or 0
                    if last and (time.time() - last_at <= config.VOICE_LAST_RESPONSE_TTL_SECONDS):
                        spoken = _safe_concise_version(last, lang)
                        ok = await _synthesize_and_send_voice(message, uid, spoken, lang)
                        if not ok:
                            await message.answer(last)
                        return
                    # No usable previous response (none stored, or past its
                    # TTL): this message itself must NEVER be treated as
                    # therapeutic content. Clear any stale value, arm the
                    # override for the NEXT ordinary reply (consumed above, on
                    # that future update), and stop here.
                    if fsm_state is not None:
                        await fsm_state.update_data(
                            last_delivered_response=None, last_delivered_response_at=None,
                            one_shot_voice_pending=True, one_shot_voice_pending_at=time.time())
                    await message.answer(
                        "Хорошо, следующий ответ озвучу." if lang == "ru"
                        else "Okay, I'll voice the next reply.")
                    return
                elif pure and fmt_cmd.kind in ("concise_oneshot", "detailed_oneshot"):
                    await message.answer(_format_ack_text(fmt_cmd, lang))
                    return
            # else: a MIXED message. When voice_ux_active (owner, private chat,
            # flag on), falls through with the one-shot flags armed above.
            # Otherwise -- non-private chat OR non-owner -- voice_ux_active was
            # False so the `if voice_ux_active:` block never ran -- the format
            # fragment is silently ignored (no preference write, no override
            # armed) and ordinary routing proceeds UNCHANGED for the emotional
            # content, exactly as it did before this feature existed.

        # 5a. Professional Free-Text Runtime V1 claim -- evaluated BEFORE any
        # lower-precedence psychological conversation owner runs (legacy
        # state/scenario routing, first-turn, Controller), so a claimed turn
        # never lets any of them obtain authority; see
        # professional_free_text_runtime.py's own module docstring for the
        # full chain contract. controller_claim/ft_claimed/claim_token are
        # initialized here (not only inside the else: branch below) because
        # they are read again after the `finally` releases the lock,
        # regardless of which branch ran this turn.
        psychological_owner = "current"
        current_row_id = None
        controller_claim = None
        ft_claimed = False
        claim_token = None
        core_reaction_category = ReactionCategory.NONE
        core_reaction_confidence = 0.0
        interaction_contract = detect_interaction_preference(user_text, lang)
        # Phase 1B -- the resolver is not even called for a DASS-discussion
        # turn, exactly preserving the pre-Phase-1B behavior of skipping
        # both checks in that case (neither gate is awaited at all here).
        turn_owner_resolution = (
            await access_control.resolve_psychological_turn_owner(uid)
            if dass_discussion_result is None else "none")
        if turn_owner_resolution == "therapist_core_v1":
            current_row_id = await save_message(
                uid, "user", user_text, "therapist_core_v1", lang,
                risk["score"], risk["categories"], source=MessageSource.USER_AUTHORED)
            psychological_owner = "therapist_core_v1"
            core_reaction_category, core_reaction_confidence = select_reaction_category(
                user_text, risk["categories"], detect_stage(user_text, lang), lang,
                is_meta_command=False, is_dependency_redirect=False)
        elif turn_owner_resolution == "professional":
            current_row_id = await save_message(
                uid, "user", user_text, "professional", lang,
                risk["score"], risk["categories"], source=MessageSource.USER_AUTHORED)
            psychological_owner = "professional"
        else:
            # 5. Update state
            state = await load_state(uid) or dict(DEFAULT_STATE)
            state = update_state(state, user_text)
            await save_state(uid, state)

            # 6. Detect stage
            stage = detect_stage(user_text, lang)

            # 7. Assess readiness
            readiness = assess_readiness(user_text, lang)

            # 8. Cognitive capacity
            capacity = get_capacity(state)

            # 9. Select scenario
            variant = get_variant(uid)
            scenario = choose_scenario(state, risk["categories"], stage, readiness, capacity,
                                       variant, trajectory=trajectory,
                                       interaction_preference=interaction_contract)
            if dass_discussion_result is not None:
                scenario = "open_chat"

            # 9.4 First-turn eligibility (spec item D) -- computed only now that
            # scenario/stage/capacity/risk are all known; no lexical/topic
            # detection anywhere in this check. claim_first_turn succeeds AT MOST
            # ONCE per (user_id, contract_version) ever (PRIMARY KEY-enforced), so
            # a successfully claimed first-turn owns this turn's entire response
            # lifecycle and takes precedence over the Conversation Controller
            # below -- and costs nothing on any later turn, since the claim
            # attempt then fails instantly and falls through to the Controller
            # unaffected.
            is_ftm_eligible = (
                scenario in FIRST_TURN_ALLOWED_SCENARIOS
                and stage not in FIRST_TURN_EXCLUDED_STAGES
                and capacity >= FIRST_TURN_MIN_CAPACITY
                and risk["level"] not in FIRST_TURN_EXCLUDED_RISK_LEVELS
            )
            if is_ftm_eligible and dass_discussion_result is None:
                claim_token = secrets.token_urlsafe(16)
                ft_claimed = await claim_first_turn(uid, FIRST_TURN_CONTRACT_VERSION, claim_token, scenario)

            # 5.5 Conversation Controller (Phase 3, master prompt §10/§15) -- FAST
            # claim only, still inside the ingestion lock (matches the ordinary
            # path's own inbound-persistence timing exactly). Runs strictly AFTER
            # the RED crisis check, the Depression Disclosure Gate, the ambiguity
            # check, and the dependency boundary above -- all already returned for
            # this turn if triggered, so none of those deterministic safety/
            # boundary routes can ever be bypassed by an explicit Controller
            # intent (hardening §4). Attempted ONLY when first-turn did not
            # already claim this turn above (see 9.4) -- a successfully claimed
            # first-turn owns the turn outright. The LLM call itself happens
            # later, OUTSIDE this lock (hardening §5) -- see
            # _controller_generate_and_deliver, invoked right after the `finally`
            # below releases it.
            if (dass_discussion_result is None and not ft_claimed
                    and await access_control.core_rollout_allowed(uid)):
                controller_claim = await _controller_claim_turn(uid, user_text, lang, risk)

            # 9.5 Emotional reaction (Voice and Adaptive Response UX) -- best-effort,
            # deterministic, fires only for genuine (non-format-only) messages: a
            # PURE format command already returned above and never reaches here.
            cat, conf = select_reaction_category(user_text, risk["categories"], stage, lang,
                                                 is_meta_command=False, is_dependency_redirect=False)

            # 11. Select practice
            severity = "high" if risk["score"] >= 70 else ("low" if risk["score"] < 40 else "medium")
            practice = select_practice(scenario, stage, severity, lang)

            # 12. Log router decision -- and 12.5's inbound persistence -- SKIPPED
            # for a Controller-owned turn: _controller_claim_turn already persisted
            # the inbound user message itself (tagged "controller", not a legacy
            # scenario), and logging a legacy routing decision for a turn the
            # Controller actually owns would misrepresent research/analytics data
            # (hardening §6 -- the legacy router must not act as if it decided
            # this turn). stage/readiness/capacity/scenario/practice above still
            # ran (pure computation, no side effects, nothing delivered) -- a
            # deliberate, documented minor inefficiency traded for not reindenting
            # this entire legacy block, rather than a correctness gap: nothing
            # computed here is used, persisted, or delivered for a Controller turn.
            if controller_claim is None:
                await log_router_decision(uid, state, risk["score"], risk["categories"],
                                           stage, readiness, capacity, scenario,
                                           practice["id"], variant, ROUTER_VERSION)

                # 12.5 Persist the USER message HERE -- before BOTH long LLM awaits
                # (maybe_summarize below AND the answer call). Memory loads recent messages
                # by autoincrement id (get_recent_messages ORDER BY id DESC), so a row's
                # arrival ORDER is its id order. Every await before this point is a fast
                # local/DB op; the two multi-second awaits (summarization, answer) come
                # after. So a slow turn can no longer pause on an LLM call while a newer,
                # faster turn's user row lands first and makes the slow turn look like the
                # newest active context (P1 EARLY PERSISTENCE ORDER RACE -- previously this
                # save sat after both LLM awaits). The assistant row is still saved later,
                # and only if this turn was not superseded. A duplicate Telegram update
                # never reaches here (DuplicateUpdateGuard drops it), so no duplicate row.
                await save_message(uid, "user", user_text, scenario, lang,
                                   risk["score"], risk["categories"],
                                   source=MessageSource.USER_AUTHORED)
    finally:
        _ingest_leave(uid, _ingest)

    if dass_discussion_result is not None:
        influence = Influence(
            "questionnaire_result", dass_discussion_session_id,
            f"reply drew on DASS-21 session {dass_discussion_session_id} subscales "
            f"depression={dass_discussion_result.subscales['depression']} "
            f"anxiety={dass_discussion_result.subscales['anxiety']} "
            f"stress={dass_discussion_result.subscales['stress']}")
        try:
            await persist_influence_trace(
                secrets.token_hex(16), uid,
                [(influence.influence_type, influence.source_id, influence.human_readable)])
        except Exception:
            await message.answer(questionnaire_ux.not_available_text(lang))
            return

    # Professional Free-Text Runtime V1: the SLOW half (up to three model
    # calls, validation, delivery) -- strictly AFTER the ingestion lock above
    # is released, and authoritative for this turn: none of the failed-
    # practice retry surface, Controller, first-turn, or legacy reaction/
    # memory/LLM/delivery code below ever runs for a Professional-claimed
    # turn (no second reply surface, no silent legacy fallback -- see
    # _run_professional_free_text_and_deliver's own docstring).
    if psychological_owner == "therapist_core_v1":
        await _run_therapist_core_v1_and_deliver(
            message, uid, current_row_id, user_text, risk, lang,
            interaction_contract, _turn_gen, _cid,
            core_reaction_category, core_reaction_confidence,
            one_shot_voice, one_shot_concise)
        return

    if psychological_owner == "professional":
        await _run_professional_free_text_and_deliver(
            message, uid, current_row_id, user_text, risk, lang, _turn_gen, _cid,
            one_shot_voice, one_shot_concise)
        return

    # PR #73 request-changes §6: best-effort, restart-safe recovery for any
    # post-practice follow-up prompt that previously failed to deliver --
    # runs on every real inbound turn (independent of whether THIS turn is
    # Controller-claimed), never holds the ingestion lock, and is fully
    # idempotent (mark_prompt_delivered's own CAS prevents a duplicate
    # active prompt even if this fires more than once).
    if await access_control.core_rollout_allowed(uid):
        await _retry_failed_practice_prompts(message, uid)

    # Conversation Controller: the SLOW half (LLM call, validation, delivery)
    # -- strictly AFTER the ingestion lock above is released (hardening §5),
    # and authoritative for this turn: none of the ordinary reaction/memory/
    # LLM/delivery code below ever runs for a Controller-owned turn
    # (hardening §6 -- no second ordinary-response pipeline running alongside).
    if controller_claim is not None:
        await _controller_generate_and_deliver(message, uid, controller_claim, _turn_gen, risk)
        return

    # 9.4b First-turn: the single primary delivery lifecycle for a claimed
    # first-turn turn (spec items A/E) -- authoritative for this turn, same
    # as the Controller lifecycle above: no reaction is sent (see
    # _maybe_react below, skipped entirely on this path), and none of the
    # ordinary memory/LLM/delivery code below ever runs for it. The user
    # message was already persisted inside the ingestion lock above, so
    # build_context's `recent` already contains it -- no explicit append.
    if ft_claimed:
        await maybe_summarize(uid, client)
        summary, recent = await build_context(uid)
        system_prompt = get_system_prompt(scenario, lang)
        messages = [{"role": "system", "content": system_prompt}]
        if summary:
            messages.append({"role": "system", "content": f"Context:\n{summary}"})
        for role, content in recent:
            messages.append({"role": role, "content": content})
        await bot.send_chat_action(message.chat.id, "typing")
        answer, buttons_allowed = await _first_turn_generate_and_validate(messages, user_text, lang)
        # Stale-response guard: the SAME suppression the Controller/ordinary
        # paths use (_user_generation_superseded) -- a newer turn for this
        # user superseded this one while the LLM call above was in flight.
        # No delivery, no persistence; the claim is left in its last
        # confirmed state, never compensated with a follow-up transition
        # (see transition_first_turn_claim's own contract).
        if _user_generation_superseded(uid, _turn_gen):
            return
        await _deliver_first_turn_response(message, uid, answer, buttons_allowed, scenario, lang,
                                           claim_token, user_revision)
        return

    # 9.5 reaction: moved OUT of the ingestion lock (never hold the
    # lock across reaction sending). cat/conf were computed above.
    await _maybe_react(message, uid, cat, conf)

    # 13. Memory. build_context now returns the just-saved current user message
    # as the newest item in `recent`, so it is NOT appended again below.
    await maybe_summarize(uid, client)
    summary, recent = await build_context(uid)

    # 14. Build messages (recent already ends with the current user message)
    system_prompt = get_system_prompt(scenario, lang)
    if dass_discussion_result is not None:
        system_prompt += _dass21_free_text_system_context(dass_discussion_result, lang)
    messages = [{"role": "system", "content": system_prompt}]
    if summary:
        messages.append({"role": "system", "content": f"Context:\n{summary}"})
    for role, content in recent:
        messages.append({"role": role, "content": content})
    # 15. LLM call
    await bot.send_chat_action(message.chat.id, "typing")
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini", messages=messages, temperature=0.65, max_tokens=300,
        )
        answer = response.choices[0].message.content
        # 15.1 Anti-robot (§7.4): one retry if the reply leans on burned-out clichés
        if has_robotic_phrase(answer, lang):
            retry_messages = messages + [
                {"role": "assistant", "content": answer},
                {"role": "system", "content": rephrase_instruction(lang)},
            ]
            retry = await client.chat.completions.create(
                model="gpt-4o-mini", messages=retry_messages,
                temperature=0.8, max_tokens=300,
            )
            answer = retry.choices[0].message.content
    except Exception as e:
        print(f"[LLM] error uid={uid}: {type(e).__name__}: {e}")
        # LLM-failure fallback ONLY (this whole block runs on an LLM error).
        # Honest, no false promise of "I'll be right back" / no timer; give a
        # soft direction instead. Crisis path is separate and never reaches here.
        await message.answer(
            "Сейчас я не могу ответить как обычно. Если тебе тяжело — не оставайся "
            "с этим один(одна): можно написать близкому человеку или обратиться в "
            "поддержку." if lang == "ru"
            else "I can't reply the way I usually do right now. If things are hard, "
            "please don't stay with it alone — reach out to someone you trust or a "
            "support line."
        )
        return

    # 16. Safety validator (context-aware — blocks approval/risky-suggestion
    # replies given the user's last message and risk level; v3 hotfix).
    is_safe, reason = validate_response_with_context(answer, user_text, risk, lang)
    if not is_safe and reason and reason.startswith("toxic validation"):
        # Epic C: the reply confirmed an absolutist distortion. ONE regeneration
        # asking to validate the feeling but NOT the distortion; else fallback.
        await log_toxic_validation_block(uid, reason, answer)
        instr = ("В прошлом ответе ты подтвердил искажение («все/никто/никогда»). "
                 "Подтверди чувство, но НЕ искажение. Например: вместо «да, все тебя бросают» "
                 "→ «то, что ты сейчас так одинок — это правда тяжело»." if lang == "ru" else
                 "Your previous reply confirmed an absolutist distortion. Validate the "
                 "feeling but NOT the distortion.")
        try:
            retry = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages + [{"role": "assistant", "content": answer},
                                     {"role": "system", "content": instr}],
                temperature=0.7, max_tokens=300)
            candidate = retry.choices[0].message.content
            ok2, _ = validate_response_with_context(candidate, user_text, risk, lang)
            # Risk-aware: at elevated risk the failed-regen path must NOT drop to the
            # neutral line — it routes to the high-risk fallback like the elif below.
            answer = candidate if ok2 else select_fallback(risk, lang)
        except Exception as e:
            print(f"[anti-toxic] retry failed uid={uid}: {type(e).__name__}: {e}")
            answer = select_fallback(risk, lang)
    elif not is_safe:
        await log_validator_block(uid, reason, answer)
        # At elevated risk the deterministic high-risk fallback; otherwise neutral.
        # NEVER re-prompt the LLM here.
        answer = select_fallback(risk, lang)

    # DASS-21 free-text adds a narrow factual contract on top of the unchanged
    # global safety validator. A DASS rejection never triggers a repair LLM call:
    # it uses a deterministic, globally revalidated fallback instead. Comparison
    # openings are likewise computed from trusted subscale integers, not by the LLM.
    if dass_discussion_result is not None:
        dass_safe, _dass_reason = _validate_dass21_free_text_response(
            answer, dass_discussion_result, user_text, lang)
        comparison_intent = _dass21_has_comparison_intent(user_text, lang)
        if dass_safe:
            candidate = answer
            if comparison_intent:
                candidate = (_dass21_comparison_prefix(dass_discussion_result, lang)
                             + "\n\n" + answer)
        else:
            candidate = _dass21_safe_fallback(dass_discussion_result, user_text, lang)
        if _dass21_delivery_candidate_is_safe(
                candidate, dass_discussion_result, user_text, risk, lang):
            answer = candidate
        else:
            fallback = _dass21_safe_fallback(
                dass_discussion_result, user_text, lang)
            if _dass21_delivery_candidate_is_safe(
                    fallback, dass_discussion_result, user_text, risk, lang):
                answer = fallback
            else:
                emergency = _dass21_emergency_fallback(lang)
                if _dass21_delivery_candidate_is_safe(
                        emergency, dass_discussion_result, user_text, risk, lang):
                    answer = emergency
                else:
                    await message.answer(questionnaire_ux.not_available_text(lang))
                    return

    # 17. Send (with a human-feeling typing pause, §7.2). The user message was
    # already persisted before the LLM call (step 14.5) to preserve arrival
    # order; only the assistant row + delivery remain, and both are gated on
    # this turn not having been superseded.
    await asyncio.sleep(typing_delay(answer))
    # Stale-response guard: if a NEWER turn for this user started while this
    # ordinary answer was being generated (aiogram runs updates as concurrent
    # tasks), this answer is stale -- skip persisting the assistant row AND
    # delivering it, so an older reply can never arrive after a newer one, and
    # a stale assistant answer never enters memory. Checked as late as possible
    # (after the typing pause) to catch a turn that arrived during it.
    # Deterministic safety replies (crisis/dependency/disambiguation) returned
    # earlier and are never subject to this guard.
    if _user_generation_superseded(uid, _turn_gen):
        _dispatch_log(f"cid={_cid} stage=skipped_stale source=normal")
        return
    await save_message(uid, "assistant", answer, scenario, lang,
                       source=MessageSource.ASSISTANT_DELIVERED)
    await deliver_response(message, uid, answer, lang,
                           one_shot_voice=one_shot_voice, one_shot_concise=one_shot_concise)
    # Only reached if deliver_response did not raise -- an exception during
    # actual Telegram delivery leaves NO last_delivered_response write, so a
    # failed send is never later replayed as if it had succeeded. Stored in
    # FSM state (chat+user scoped, never the DB) -- never crisis/dependency
    # text, since both of those paths already returned earlier in pipeline()
    # and never reach this line at all.
    if voice_ux_active and fsm_state is not None:
        await fsm_state.update_data(last_delivered_response=answer,
                                    last_delivered_response_at=time.time())

    # 17.5 Profile refresh (§5) — deterministic, every 5th user message.
    await maybe_update_profile(uid, await get_user_message_count(uid))

    # 17.6 Sudden-improvement review flag (Epic B) — quiet signal for a human,
    # NOT a crisis. Never changes the user's experience; rate-limited to 1/week.
    try:
        if await check_sudden_improvement(uid):
            if await log_review_flag(uid, "sudden_improvement",
                                     "Резкий переход от длительной безнадёжности к спокойствию."):
                # PR 1B-1: not a crisis event, so no reviewer variant here — this
                # is a quiet human-review signal about the OWNER's own userbase.
                # For CLINICIAN_TESTER/UNKNOWN it is simply suppressed, not
                # rerouted (nothing analogous to crisis "clinical review" applies).
                if access_control.should_alert_owner(uid):
                    note = (f"🟦 На ревью: пользователь {uid} (@{username or '—'}) — резкий переход "
                            f"от длительной безнадёжности к спокойствию. Стоит глянуть.")
                    for admin_id in ADMIN_USER_IDS:
                        try:
                            await bot.send_message(admin_id, note)
                        except Exception:
                            pass
    except Exception as e:
        print(f"[review-flag] uid={uid}: {type(e).__name__}: {e}")
    
    # 18. Start outcome tracking (if appropriate scenario)
    if scenario not in ("crisis", "open_chat"):
        # persist routing context so cb_before can record real stage/readiness/capacity
        if fsm_state is not None:
            await fsm_state.update_data(stage=stage, readiness=readiness, capacity=capacity)
        await message.answer(f"Как ты себя чувствуешь прямо сейчас? (1=плохо, 10=хорошо)" if lang == "ru"
                             else "How do you feel right now? (1=bad, 10=good)",
                             reply_markup=before_score_kb(practice['id'], scenario, lang))

# ────────────────────────────────────────────────────────────────────────────

async def _send_admin_crisis_alert(uid: int, username: str, stage: int, event_id) -> None:
    # PR 1B-1: routed like every other crisis alert (single decision point).
    kind, targets = access_control.crisis_alert_targets(uid)
    if kind == "owner":
        risk = {"level": "critical", "score": "—", "categories": ["suicide"]}
        alert = admin_alert_text(uid, username, stage, risk, "", event_id)
        for admin_id in targets:
            try:
                await bot.send_message(admin_id, alert)
            except Exception:
                pass
    elif kind == "reviewer":
        payload = _minimal_reviewer_payload(uid, event_id, f"stage escalated to {stage}")
        for reviewer_id in targets:
            try:
                await bot.send_message(reviewer_id, payload)
            except Exception:
                pass


async def _show_stage(callback: CallbackQuery, stage: int, lang: str, event_id) -> None:
    """Gate the OLD screen's buttons (with fallback) then show the new stage."""
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass  # Telegram may refuse to edit; the DB stage still prevents a loop.
    text, kb = crisis_screen(stage, lang, event_id)
    await send_crisis(callback.message.answer, text, kb, lang,
                      callback.from_user.id, event_id, "screen")


# SQLite's INTEGER storage class is a signed 64-bit int -- any bound value
# outside this range raises OverflowError. Used to reject an out-of-range
# 3-part crisis:*:<id> event id before any DB query is ever attempted.
_CRISIS_EVENT_ID_MAX_SQLITE = 2**63 - 1


@dp.callback_query(F.data.startswith("crisis:"))
async def cb_crisis(callback: CallbackQuery):
    """Staged crisis escalation. 'still'/'cant_call' raise the monotonic stage in
    the DB (idempotent: a stale/double tap is a no-op); 'safe' resolves; 'call'/
    'contact'/'safe_place'/'contacted' help without changing the stage."""
    uid = callback.from_user.id
    username = callback.from_user.username or ""
    parts = callback.data.split(":")
    action = parts[1]

    # Strict parsing contract, checked BEFORE any I/O (no get_user_language,
    # no get_active_crisis, no crisis_event_owner, no DB call of any kind
    # for a malformed callback):
    #   len(parts) == 2  ("crisis:<action>")           -> legacy resolution
    #   len(parts) == 3  ("crisis:<action>:<event_id>") -> exact numeric id
    #   anything else                                   -> hard parse failure
    #
    # A malformed 3-part id (oversized, zero, negative, non-numeric) must
    # NEVER fall through to legacy resolution: an earlier version did
    # exactly that (treating "no valid embedded id" the same as "no id
    # segment at all"), and if the tapping user happened to have a real
    # active crisis, the malformed callback would silently resolve to and
    # operate on THAT real event instead of failing closed -- not fail-
    # closed at all. Each shape is handled in its own exclusive branch so
    # an invalid 3-part callback can only ever reach the final "else: fail
    # closed" branch, never the legacy path.
    if len(parts) == 2:
        try:
            active = await get_active_crisis(uid)
        except Exception as e:
            # Redacted: fixed event name + uid + exception CLASS only --
            # never the raw exception message (could echo a DB path or SQL).
            print(f"event=crisis_legacy_active_event_lookup_failed uid={uid} "
                  f"exc_type={type(e).__name__}")
            await callback.answer()
            return
        event_id = active[0] if active else None
        if event_id is None:
            await callback.answer()
            return
    elif len(parts) == 3:
        # ASCII decimal digits only -- str.isdigit() alone is NOT enough:
        # it also accepts non-ASCII "digit" characters (e.g. superscript
        # '²') that int() then rejects with an uncaught ValueError. isascii()
        # rules those out before int() ever runs.
        id_segment = parts[2]
        if not (id_segment.isascii() and id_segment.isdigit()):
            await callback.answer()
            return
        parsed_id = int(id_segment)
        if not (1 <= parsed_id <= _CRISIS_EVENT_ID_MAX_SQLITE):
            await callback.answer()
            return
        event_id = parsed_id
    else:
        # Zero-, one-, or four-or-more-part callback_data -- not a shape
        # this handler ever produces itself; fail closed with no I/O.
        await callback.answer()
        return

    # Owner-scope the ENTIRE crisis:* callback surface -- ONE gate, before
    # any action-specific Telegram send, keyboard replacement, delivery-log
    # write, database mutation, or admin/reviewer alert. Previously only
    # "safe" enforced this (in set_crisis_response's own SQL); call/contact/
    # safe_place/contacted/still/cant_call had none, so a forged or replayed
    # 3-part callback_data carrying a foreign event_id could log delivery
    # rows against someone else's event, or -- for still/cant_call --
    # actually escalate their crisis stage and fire an admin alert. The
    # legacy 2-part path is already owner-safe by construction
    # (get_active_crisis(uid)'s own query is WHERE user_id=?), but is
    # re-verified here too so both paths run through one uniform gate.
    # Nonexistent and wrong-owner produce the IDENTICAL fail-closed result
    # (callback.answer() only) -- neither is ever revealed to a non-owner.
    #
    # The lookup itself is wrapped: an exception here (a DB error, for
    # example) must never escape cb_crisis and leave the Telegram callback
    # unanswered -- it fails exactly like a wrong-owner/nonexistent result:
    # answer cleanly, send nothing, mutate nothing, alert nothing.
    try:
        owner_id = await crisis_event_owner(event_id)
    except Exception as e:
        # Redacted: fixed event name + uid + exception CLASS only -- never
        # the raw exception message, DB path, SQL, callback_data, event_id,
        # or any content.
        print(f"event=crisis_event_owner_lookup_failed uid={uid} exc_type={type(e).__name__}")
        await callback.answer()
        return
    if owner_id != uid:
        await callback.answer()
        return

    # Language resolution is real I/O too -- deferred until AFTER shape/id
    # validation, event resolution, and ownership verification, so an
    # invalid or non-owned callback never triggers it either.
    lang = await get_user_language(uid)

    if action == "safe":
        # set_crisis_response(event_id, uid, "safe") enforces ownership in
        # SQL itself (WHERE id=? AND user_id=?) and sets BOTH resolved=1 and
        # user_response='safe' on this exact event in one UPDATE -- a
        # separate resolve_crisis(event_id) call is no longer needed.
        result = await set_crisis_response(event_id, uid, "safe")
        if result not in (CRISIS_RESPONSE_UPDATED, CRISIS_RESPONSE_ALREADY_SAFE):
            # Explicit SUCCESS allowlist, not a denylist: only these two
            # exact values may proceed. Anything else -- including
            # NOT_FOUND_OR_NOT_OWNED, NOT_ACTIONABLE, None, or any future/
            # unexpected value this function might ever return -- fails
            # closed here: no crisis row touched, no keyboard removed, no
            # confirmation sent.
            await callback.answer()
            return
        # Only UPDATED and ALREADY_SAFE reach here -- a genuine duplicate
        # 'safe' tap by the correct owner is idempotent and still shows the
        # same confirmation.
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await send_crisis(callback.message.answer, crisis_resolved_text(lang), None,
                          lang, uid, event_id, "resolved")
        await callback.answer()
        return

    if action == "call":
        await send_crisis(callback.message.answer, crisis_call_text(lang), None,
                          lang, uid, event_id, "call_text")
        await callback.answer()
        return

    if action in ("contact",):
        await send_crisis(callback.message.answer, crisis_contact_template(lang), None,
                          lang, uid, event_id, "contact")
        await callback.answer()
        return

    if action in ("safe_place", "contacted"):
        await send_crisis(callback.message.answer, crisis_safe_place_ack(lang),
                          safe_only_keyboard(event_id, lang), lang, uid, event_id,
                          "safe_place")
        await callback.answer()
        return

    # Escalations — the ONLY actions that change the stage.
    if action == "still":
        stage = await get_crisis_stage(event_id)   # stage of THIS event (pt 5)
        target = 1 if stage == 0 else (3 if stage == 2 else None)
        if target is None:
            await callback.answer(); return
        # Owner-scoped in the SQL mutation itself (defense in depth beyond
        # the early gate above) -- atomic once-only.
        changed = await bump_crisis_stage(event_id, target, uid)
        if not changed:
            await callback.answer(); return                   # stale/double tap → no-op
        if target == 3:
            await set_stage3_at(event_id, uid)   # also owner-scoped in SQL
        await _send_admin_crisis_alert(uid, username, target, event_id)  # once
        await _show_stage(callback, target, lang, event_id)
        await callback.answer()
        return

    if action == "cant_call":
        changed = await bump_crisis_stage(event_id, 2, uid)   # owner-scoped in SQL
        if changed:
            # Alert on the FIRST escalation too (pt 3) — every actual stage rise
            # notifies an admin exactly once (atomic bump guarantees once).
            await _send_admin_crisis_alert(uid, username, 2, event_id)
            await _show_stage(callback, 2, lang, event_id)
        await callback.answer()
        return

    await callback.answer()


@dp.callback_query(F.data.startswith("before:"))
async def cb_before(callback: CallbackQuery, fsm_state: FSMContext):
    uid = callback.from_user.id
    try:
        parts = callback.data.split(":")
        practice_id, scenario, lang, score = parts[1], parts[2], parts[3], int(parts[4])
    except (IndexError, ValueError):
        # Malformed callback data (forged/truncated) -- fail closed, no DB write.
        await callback.answer()
        return

    # Fail closed BEFORE any DB write: a forged, stale-version, or
    # catalog-only practice_id must never create an intervention_results row,
    # let alone display content. This is the ONLY lookup used with
    # callback-supplied (untrusted) data -- get_practice_by_id itself has no
    # such guard.
    practice = get_production_practice_by_id(practice_id, lang)
    if not practice:
        await callback.answer()
        return

    # Idempotency: a duplicate tap of the SAME offer (double-tap, or the
    # button remaining clickable after the first tap) must create exactly
    # one baseline row, never a second one, and never let a later tap
    # overwrite the first score.
    fdata = await fsm_state.get_data()
    if fdata.get("practice_id") == practice_id and fdata.get("intervention_id") is not None:
        await callback.answer()
        return

    state = await load_state(uid) or dict(DEFAULT_STATE)
    intervention_id = await start_intervention(
        uid, scenario, scenario, practice_id, PRACTICE_VERSION,
        {"state": state}, score,
        fdata.get("stage", "OPEN"),
        fdata.get("readiness", "MEDIUM"),
        fdata.get("capacity", get_capacity(state)),
        get_variant(uid), ROUTER_VERSION,
        source_chat_id=callback.message.chat.id,
        source_message_id=callback.message.message_id,
    )
    if intervention_id is None:
        # Lost the atomic claim: a genuinely concurrent (or duplicate) tap on
        # this EXACT card already won -- idx_intervention_one_baseline_per_card
        # rejected this insert. The in-memory fdata check above is only a
        # cheap fast path for the common sequential case; this is the real,
        # engine-enforced guarantee. No second row, no overwrite, no content.
        await callback.answer()
        return
    await fsm_state.update_data(
        intervention_id=intervention_id,
        practice_id=practice_id,
        lang=lang,
        scenario=scenario,
        before_score=score,
    )
    await fsm_state.set_state(InterventionStates.awaiting_after)

    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(practice["steps"], 1))
    await callback.message.answer(f"<b>{_he(practice['name'])}</b>\n\n{_he(steps)}", parse_mode="HTML")
    await asyncio.sleep(1)
    await callback.message.answer(
        ("Как теперь?" if lang == "ru" else "How now?"),
        reply_markup=score_kb("after"))
    await callback.answer()


@dp.callback_query(F.data.startswith("before_skip:"))
async def cb_before_skip(callback: CallbackQuery, fsm_state: FSMContext):
    """Explicit baseline-skip (Therapeutic Core Foundation, flag-gated): the
    user proceeds straight to the practice content WITHOUT providing a
    before-score. Deliberately does NOT call start_intervention (no baseline
    is fabricated, no row is created -- non-evaluable by absence, the same
    convention the existing schema already uses for any offer the user never
    engages with) and does NOT enter the after/quality rating loop (nothing
    exists to compare a later value against, so no improvement claim is
    ever possible for this episode)."""
    if not config.THERAPEUTIC_CORE_FOUNDATION_ENABLED:
        await callback.answer()
        return
    try:
        parts = callback.data.split(":")
        practice_id, scenario, lang = parts[1], parts[2], parts[3]
    except IndexError:
        await callback.answer()
        return
    practice = get_production_practice_by_id(practice_id, lang)
    if not practice:
        await callback.answer()
        return
    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(practice["steps"], 1))
    await callback.message.answer(f"<b>{_he(practice['name'])}</b>\n\n{_he(steps)}", parse_mode="HTML")
    await callback.answer()

# ── Depression Disclosure Gate callbacks (Phase 2) ──────────────────────────
# Every handler: parse+validate via _dd_validate_callback (§1's 7-point chain)
# -> perform ONE atomic transition -> answer -> send the next question. A
# transition returning False (lost the race -- e.g. superseded by a crisis
# event logged between validation and this call) means "do not send the next
# question", which is exactly what §7's TOCTOU requirement asks for; it falls
# out of the atomic UPDATE's own WHERE clause, not a second explicit check.

async def _dd_record_answer_and_advance(flow: dict, uid: int, key: str, value: str,
                                        from_step: str, to_step: str) -> bool:
    answers = safe_load_answers(flow["answers_json"])
    answers[key] = value
    return await advance_disclosure_flow(flow["id"], uid, from_step=from_step,
                                         to_step=to_step, answers_json=json.dumps(answers))


@dp.callback_query(F.data.startswith("dd:safety:"))
async def cb_dd_safety(callback: CallbackQuery):
    result = await _dd_validate_callback(callback, "SAFETY_CHECK")
    if result is None:
        return
    flow, value = result
    uid, lang = callback.from_user.id, flow["lang"]

    if value in ("yes", "unsure"):
        # §2/§6: "Да" is a direct, truthful positive answer to the explicit
        # safety question -> routes to the SAME deterministic crisis system
        # ("EXPLICIT_MESSAGE"'s sibling, not a parallel one) with truthful
        # source metadata: DIRECT_SAFETY_YES, risk_score=None (never a
        # fabricated 100), categories=["suicide"] (truthful -- this genuinely
        # IS a direct confirmed report). "Не уверен" gets the SAME crisis-
        # support UI/priority (this codebase's existing, documented
        # philosophy: "anything unclear keeps the crisis screen, never assume
        # safety" -- see the active-crisis reassurance gate near the top of
        # pipeline()) but source=DIRECT_SAFETY_UNSURE and categories=[] --
        # uncertainty is never recorded as a confirmed suicide statement.
        # user_text="" (not the fabricated bracket placeholder): there is no
        # original message for a button tap, and trigger_crisis/log_crisis_
        # event now handle an empty user_text truthfully (no fabricated
        # "user" message is saved -- see trigger_crisis's own comment).
        if not await close_disclosure_flow(flow["id"], uid, from_step=flow["step"],
                                           status="superseded_by_crisis",
                                           superseded_reason=f"direct_safety_{value}"):
            await _dd_reject_stale_callback(callback, lang)
            return
        await callback.answer()
        source = "DIRECT_SAFETY_YES" if value == "yes" else "DIRECT_SAFETY_UNSURE"
        categories = ["suicide"] if value == "yes" else []
        risk = {"score": None, "level": "critical", "categories": categories,
               "implicit": False, "ambiguous_phrases": []}
        await trigger_crisis(callback.message, uid, callback.from_user.username or "",
                             "", risk, lang, source=source)
        return

    # value == "no"
    if not await advance_disclosure_flow(flow["id"], uid, from_step="SAFETY_CHECK",
                                         to_step="DIAGNOSIS_SOURCE"):
        await _dd_reject_stale_callback(callback, lang)
        return
    await callback.answer()
    await callback.message.answer(diagnosis_source_text(lang),
                                  reply_markup=_dd_diagnosis_source_kb(flow["id"], lang))


@dp.callback_query(F.data.startswith("dd:src:"))
async def cb_dd_source(callback: CallbackQuery):
    result = await _dd_validate_callback(callback, "DIAGNOSIS_SOURCE")
    if result is None:
        return
    flow, value = result
    uid, lang = callback.from_user.id, flow["lang"]
    # value in {"specialist","self","unknown"} -- never used to confirm OR deny
    # a diagnosis (§13.3/§8), only stored as reported provenance.
    if not await advance_disclosure_flow(flow["id"], uid, from_step="DIAGNOSIS_SOURCE",
                                         to_step="DURATION", diagnosis_source=value):
        await _dd_reject_stale_callback(callback, lang)
        return
    await callback.answer()
    await callback.message.answer(duration_text(lang),
                                  reply_markup=_dd_duration_kb(flow["id"], lang))


@dp.callback_query(F.data.startswith("dd:dur:"))
async def cb_dd_duration(callback: CallbackQuery):
    result = await _dd_validate_callback(callback, "DURATION")
    if result is None:
        return
    flow, value = result
    uid, lang = callback.from_user.id, flow["lang"]
    if not await _dd_record_answer_and_advance(flow, uid, "duration", value,
                                               "DURATION", "FUNCTIONING"):
        await _dd_reject_stale_callback(callback, lang)
        return
    await callback.answer()
    await callback.message.answer(functioning_text(lang),
                                  reply_markup=_dd_functioning_kb(flow["id"], lang))


@dp.callback_query(F.data.startswith("dd:func:"))
async def cb_dd_functioning(callback: CallbackQuery):
    result = await _dd_validate_callback(callback, "FUNCTIONING")
    if result is None:
        return
    flow, value = result
    uid, lang = callback.from_user.id, flow["lang"]
    if not await _dd_record_answer_and_advance(flow, uid, "functioning", value,
                                               "FUNCTIONING", "BASIC_ACTIVITIES"):
        await _dd_reject_stale_callback(callback, lang)
        return
    await callback.answer()
    await callback.message.answer(basic_activities_text(lang),
                                  reply_markup=_dd_basic_activities_kb(flow["id"], lang))


@dp.callback_query(F.data.startswith("dd:basic:"))
async def cb_dd_basic_activities(callback: CallbackQuery):
    result = await _dd_validate_callback(callback, "BASIC_ACTIVITIES")
    if result is None:
        return
    flow, value = result
    uid, lang = callback.from_user.id, flow["lang"]
    if not await _dd_record_answer_and_advance(flow, uid, "basic_activities", value,
                                               "BASIC_ACTIVITIES", "SUPPORT"):
        await _dd_reject_stale_callback(callback, lang)
        return
    await callback.answer()
    await callback.message.answer(support_text(lang),
                                  reply_markup=_dd_support_kb(flow["id"], lang))


@dp.callback_query(F.data.startswith("dd:supp:"))
async def cb_dd_support(callback: CallbackQuery):
    result = await _dd_validate_callback(callback, "SUPPORT")
    if result is None:
        return
    flow, value = result
    uid, lang = callback.from_user.id, flow["lang"]
    if not await _dd_record_answer_and_advance(flow, uid, "support", value,
                                               "SUPPORT", "PURPOSE"):
        await _dd_reject_stale_callback(callback, lang)
        return
    await callback.answer()
    await callback.message.answer(purpose_text(lang),
                                  reply_markup=_dd_purpose_kb(flow["id"], lang))


@dp.callback_query(F.data.startswith("dd:purp:"))
async def cb_dd_purpose(callback: CallbackQuery):
    result = await _dd_validate_callback(callback, "PURPOSE")
    if result is None:
        return
    flow, value = result
    uid, lang = callback.from_user.id, flow["lang"]
    answers = safe_load_answers(flow["answers_json"])
    answers["purpose"] = value
    # §9: PURPOSE is the last question, not a discard point -- close_disclosure_
    # flow(status="completed") is what actually produces the typed HANDOFF_READY
    # step + handoff_status='ready' for Phase 3 to later claim_disclosure_handoff().
    if not await close_disclosure_flow(flow["id"], uid, from_step="PURPOSE", status="completed",
                                       answers_json=json.dumps(answers)):
        await _dd_reject_stale_callback(callback, lang)
        return
    await callback.answer()
    await callback.message.answer(closing_text(lang))


# ── Conversation Controller: PRACTICE consent (Phase 3 §12, hardening §3/§4) ─
# callback_data "cc:consent:<session_id>:<proposal_id>:<yes|no>" -- carries
# the exact PROPOSAL identity, not just the session, so consent applies to
# one exact practice proposal. Deterministic Да/Нет, never LLM-parsed free
# text. Every check below is independent and additive -- a callback that
# fails any one of them is rejected the same way (non-actionable, no delivery).

@dp.callback_query(F.data.startswith("cc:consent:"))
async def cb_cc_consent(callback: CallbackQuery):
    uid = callback.from_user.id
    parts = callback.data.split(":", 4)
    if len(parts) != 5:
        await callback.answer()
        return
    _, _tag, session_id, proposal_id, value = parts
    if value not in ("yes", "no"):
        await callback.answer()
        return
    if not await access_control.core_rollout_allowed(uid):
        await callback.answer()
        return
    # Hardening §4: defense-in-depth. supersede_active_core_sessions_for_crisis
    # (called from trigger_crisis) already PAUSES the session and supersedes
    # any actionable proposal, which the atomic transition below independently
    # rejects -- this direct get_active_crisis check is a SECOND, independent
    # guard so a crisis is caught even if that supersession step itself failed.
    if await get_active_crisis(uid) is not None:
        await callback.answer()
        return
    # §4: a new Depression Disclosure flow (a fresh safety re-assessment)
    # also makes a standing PRACTICE proposal non-actionable, independent of
    # the proposal's own status.
    if await get_active_disclosure_flow(uid) is not None:
        await callback.answer()
        return
    session = await get_core_session(session_id, uid)
    if session is None or session.lifecycle_status is not LifecycleStatus.OPEN:
        await callback.answer()
        return
    proposal = await get_practice_proposal(proposal_id, uid)
    # §4: exact proposal AND session identity must both match -- ownership is
    # already enforced by get_practice_proposal's own WHERE, this additionally
    # rejects a forged/mismatched session_id paired with a real proposal_id.
    if proposal is None or str(proposal.session_id) != str(session_id):
        await callback.answer()
        return
    lang = await get_user_language(uid) or "ru"
    to_status = (PracticeProposalStatus.DECLINED if value == "no"
                else PracticeProposalStatus.GRANTED).value

    # Atomic CAS (database.transition_practice_proposal): PENDING -> to_status,
    # with expiry enforced INSIDE the same conditional UPDATE (require_
    # unexpired=True). Ownership, current status, and freshness are all
    # verified in the ONE atomic write -- a duplicate tap, a stale tap, an
    # expired proposal, or two concurrent taps racing each other all resolve
    # to exactly one winner, never a lost-update or a double delivery.
    if not await transition_practice_proposal(
            proposal_id, uid, from_status=PracticeProposalStatus.PENDING.value,
            to_status=to_status, require_unexpired=True):
        await callback.answer()
        return
    # Best-effort mirror on the session for older readers -- the proposal's
    # own status (just transitioned above) is the real, authoritative gate.
    prior_json = session_json_snapshot(session)
    session.consent = ConsentState.DECLINED if value == "no" else ConsentState.GRANTED
    await update_core_session_authoritative(session, prior_json)
    await callback.answer()

    if value == "no":
        await callback.message.answer(
            "Хорошо, не будем. Если захочешь попробовать позже — просто скажи." if lang != "en"
            else "Okay, we won't. Just say the word if you want to try later.")
        return

    await _deliver_granted_practice(callback, uid, session_id, proposal_id, proposal.practice_id, lang)


async def _deliver_granted_practice(callback: CallbackQuery, uid: int, session_id, proposal_id,
                                    practice_id: str, lang: str) -> None:
    """Shared GRANTED->...->STARTED delivery pipeline for cb_cc_consent's
    "yes" tap. PR #73 ATOMIC CLOSURE §4: an informed repeat of a WORSE-
    outcome practice now flows through this SAME ordinary consent contract
    too (a real, brand-new is_worse_override proposal, ordinary Да/Нет
    buttons) -- there is no separate callback contract or delivery path to
    drift apart from anymore. The caller is responsible for the PENDING ->
    GRANTED transition itself; this function owns everything from GRANTED
    onward."""
    practice = get_production_practice_by_id(practice_id, lang)
    if not practice:  # pragma: no cover -- defensive, practice_id is always a real production id
        await callback.message.answer(controller.fallback_text(lang, Intent.PRACTICE))
        return

    # PR #73 request-changes §3: a real delivery-claim state machine closes
    # the consent-to-delivery crisis race. GRANTED alone was not enough --
    # a crisis/​start beginning between GRANTED and the actual Telegram send
    # could not be caught (supersede_active_practice_proposals now DOES
    # cover GRANTED/DELIVERING too, but only a fresh safety recheck right
    # here, immediately before claiming delivery, closes the narrow window
    # between "the crisis write landed" and "this callback re-reads it").
    if (await get_active_crisis(uid) is not None
            or await get_active_disclosure_flow(uid) is not None
            or not await access_control.core_rollout_allowed(uid)):
        return
    resession = await get_core_session(session_id, uid)
    if resession is None or resession.lifecycle_status is not LifecycleStatus.OPEN:
        return
    # Atomic delivery claim: GRANTED -> DELIVERING. Only ONE callback
    # invocation can ever win this (the CAS's own ownership+status+expiry
    # checks), so a duplicate/racing tap here is rejected the same way a
    # duplicate PENDING->GRANTED tap already is.
    if not await transition_practice_proposal(
            proposal_id, uid, from_status=PracticeProposalStatus.GRANTED.value,
            to_status=PracticeProposalStatus.DELIVERING.value, require_unexpired=True):
        return
    # Recheck safety ONE more time, immediately before the actual external
    # (unsendable-boundary) Telegram call -- the narrowest possible window
    # is between this check and the send itself, which cannot be closed
    # further without controlling Telegram's own delivery latency. Honest
    # guarantee: crisis/​start beginning BEFORE this line is always caught;
    # crisis/​start beginning strictly AFTER this line and DURING the network
    # call itself is not (no system can close an external I/O boundary).
    if await get_active_crisis(uid) is not None:
        await transition_practice_proposal(
            proposal_id, uid, from_status=PracticeProposalStatus.DELIVERING.value,
            to_status=PracticeProposalStatus.SUPERSEDED.value, reason="crisis_before_send")
        return
    # PR #73 FINAL REQUEST CHANGES §3: the crisis-only recheck above was not
    # enough -- a new disclosure flow or rollout turning off between the
    # DELIVERING claim and the send need the exact same guarantee.
    if (await get_active_disclosure_flow(uid) is not None
            or not await access_control.core_rollout_allowed(uid)):
        await transition_practice_proposal(
            proposal_id, uid, from_status=PracticeProposalStatus.DELIVERING.value,
            to_status=PracticeProposalStatus.SUPERSEDED.value, reason="unsafe_before_send")
        return
    # Owning session no longer OPEN (e.g. /start paused it between the claim
    # and here) -- /start's own supersede_active_practice_proposals call
    # already flipped this proposal away from DELIVERING, so no further
    # write is needed, just stop.
    resession2 = await get_core_session(session_id, uid)
    if resession2 is None or resession2.lifecycle_status is not LifecycleStatus.OPEN:
        return
    # The proposal itself may already be superseded (by /start, a topic
    # change, a new disclosure flow, or a racing duplicate claim) even if
    # none of the checks above caught it directly -- this is the final,
    # authoritative recheck immediately before the external Telegram call.
    reproposal = await get_practice_proposal(proposal_id, uid)
    if reproposal is None or reproposal.status is not PracticeProposalStatus.DELIVERING:
        return

    # §3 final closure: STARTED means the exact steps were SUCCESSFULLY
    # delivered -- the send is attempted FIRST, and DELIVERING->STARTED only
    # happens once it actually succeeded. A send failure records
    # DELIVERY_FAILED (never leaves the proposal stuck in DELIVERING, never
    # falsely STARTED, never shows outcome buttons for content the user
    # never received).
    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(practice["steps"], 1))
    try:
        await callback.message.answer(f"<b>{_he(practice['name'])}</b>\n\n{_he(steps)}",
                                      parse_mode="HTML")
    except Exception as e:
        print(f"[controller] practice steps delivery failed uid={uid}: {type(e).__name__}: {e}")
        await transition_practice_proposal(
            proposal_id, uid, from_status=PracticeProposalStatus.DELIVERING.value,
            to_status=PracticeProposalStatus.DELIVERY_FAILED.value, reason="send_exception")
        return
    # Only the delivery owner (this exact callback invocation, having won
    # the GRANTED->DELIVERING CAS above) may mark STARTED. PR #73 FINAL
    # REQUEST CHANGES §1: the reporting window opens in this SAME atomic
    # write -- the cc:outcome buttons about to be shown become actionable
    # exactly when STARTED becomes true, never before.
    if not await transition_practice_proposal(
            proposal_id, uid, from_status=PracticeProposalStatus.DELIVERING.value,
            to_status=PracticeProposalStatus.STARTED.value, open_reporting_window=True):
        return  # superseded between the send and this write -- steps went out, but
                # were also invalidated moments later; the outcome UI stays silent
    # PR #73 request-changes §6 / FINAL REQUEST CHANGES §2 / ATOMIC CLOSURE
    # §2/§3: restart-safe, claim-first delivery tracking with a persisted,
    # unforgeable claim_id -- claim_prompt_send's atomic CAS to RETRYING is
    # what makes this idempotent under concurrency (a post-send CAS alone
    # only dedupes the DB write, not a second Telegram message), and the
    # claim_id is what stops a stale prior claimant from finalizing a claim
    # a second caller legitimately reclaimed. A lost claim (another caller
    # already owns this send, or it is already terminal) means this call
    # must not attempt to send at all.
    outcome_claim_id = await claim_prompt_send(proposal_id, uid, "outcome", "STARTED")
    if outcome_claim_id:
        # §3: re-verify EVERYTHING immediately before the send -- winning
        # the claim does not guarantee crisis/disclosure/rollout/session/
        # window state hasn't changed in the interim.
        if await _prompt_claim_still_safe(uid, proposal_id, "outcome", outcome_claim_id, "STARTED"):
            try:
                sent = await callback.message.answer(
                    ("Получилось выполнить практику?\n\n"
                     "Можно выбрать вариант или ответить своими словами.") if lang != "en" else
                    ("Were you able to do the practice?\n\n"
                     "You can choose an option or reply in your own words."),
                    reply_markup=_practice_did_kb(proposal_id, lang))
                await mark_prompt_delivered(proposal_id, uid, "outcome", sent.message_id, outcome_claim_id)
            except Exception as e:
                print(f"[controller] outcome-prompt delivery failed uid={uid}: {type(e).__name__}: {e}")
                await mark_prompt_failed(proposal_id, uid, "outcome", outcome_claim_id)
        else:
            # Release the claim immediately rather than block it for the
            # full stale-claim timeout -- whatever invalidated the world
            # (crisis/start/disclosure/rollout/topic-change/close) already
            # makes this prompt permanently uninteresting to resurrect.
            await mark_prompt_failed(proposal_id, uid, "outcome", outcome_claim_id)


async def _prompt_claim_still_safe(uid: int, proposal_id, prompt_kind: str,
                                   claim_id: str, expected_status: str) -> bool:
    """PR #73 ATOMIC CLOSURE §3: revalidate everything AFTER claim_prompt_
    send wins the claim but BEFORE the actual Telegram call -- winning the
    claim only proves nobody else currently owns this send; it does not
    prove the world hasn't changed since (a /start, topic change, new
    disclosure, crisis, or conversation close could all land in the
    interval). Shared by every prompt-send site so they cannot drift apart
    on which checks matter."""
    if (await get_active_crisis(uid) is not None
            or await get_active_disclosure_flow(uid) is not None
            or not await access_control.core_rollout_allowed(uid)):
        return False
    proposal = await get_practice_proposal(proposal_id, uid)
    if proposal is None or proposal.status.value != expected_status:
        return False
    if proposal.reporting_window_status != "ACTIVE":
        return False
    current_claim = (proposal.outcome_prompt_claim_id if prompt_kind == "outcome"
                     else proposal.helped_prompt_claim_id)
    if current_claim != claim_id:
        return False
    owning_session = await get_core_session(proposal.session_id, uid)
    if owning_session is None or owning_session.lifecycle_status is not LifecycleStatus.OPEN:
        return False
    return True


def _practice_outcome_kb(proposal_id, lang: str) -> InlineKeyboardMarkup:
    labels = [
        ("done", "✅ Выполнил(а)", "✅ Done"),
        ("stopped", "⏸ Остановился(ась)", "⏸ Stopped"),
        ("refused", "❌ Не хочу продолжать", "❌ Don't want to continue"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=(ru if lang != "en" else en),
                              callback_data=f"cc:outcome:{proposal_id}:{val}")]
        for val, ru, en in labels])


def _practice_helped_kb(proposal_id, lang: str) -> InlineKeyboardMarkup:
    labels = [
        ("helped", "Помогло", "Helped"),
        ("partly", "Помогло отчасти", "Partly helped"),
        ("none", "Не изменилось", "No change"),
        ("worse", "Стало хуже", "Became worse"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=(ru if lang != "en" else en),
                              callback_data=f"cc:helped:{proposal_id}:{val}")]
        for val, ru, en in labels])


# ── Progressive two-button practice UX (replaces the 3-then-4-button legacy
# flow above for every NEWLY delivered proposal). The legacy cc:outcome/
# cc:helped handlers and keyboards above are kept completely unchanged --
# any proposal already carrying an old-style keyboard (a message sent
# before this change) continues through that exact same, already-tested
# path end to end, never switched mid-flow to the new one. Product
# principle: never more than two buttons per message; free text always
# remains a valid answer at every step (it flows through the ordinary
# conversation pipeline, unchanged); an unfinished practice is never framed
# as failure or met with pressure to continue.

def _practice_did_kb(proposal_id, lang: str) -> InlineKeyboardMarkup:
    labels = [("yes", "Получилось", "I did it"), ("no", "Не получилось", "I couldn't")]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=(ru if lang != "en" else en),
                              callback_data=f"cc:practdone:{proposal_id}:{val}")]
        for val, ru, en in labels])


def _practice_notdone_kb(proposal_id, lang: str) -> InlineKeyboardMarkup:
    labels = [("stopped", "Начал, но остановился", "I started but stopped"),
             ("never", "Не начал", "I didn't start")]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=(ru if lang != "en" else en),
                              callback_data=f"cc:practwhy:{proposal_id}:{val}")]
        for val, ru, en in labels])


def _practice_help_kb(proposal_id, lang: str) -> InlineKeyboardMarkup:
    labels = [("yes", "Помогло", "It helped"), ("no", "Не помогло", "It didn't help")]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=(ru if lang != "en" else en),
                              callback_data=f"cc:practhelp:{proposal_id}:{val}")]
        for val, ru, en in labels])


def _practice_helpwhy_kb(proposal_id, lang: str) -> InlineKeyboardMarkup:
    labels = [("same", "Без изменений", "No change"), ("worse", "Стало хуже", "I feel worse")]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=(ru if lang != "en" else en),
                              callback_data=f"cc:practhelpwhy:{proposal_id}:{val}")]
        for val, ru, en in labels])


async def _retry_failed_practice_prompts(message: Message, uid: int) -> None:
    """PR #73 request-changes §6 / FINAL REQUEST CHANGES §2/§4: restart-safe
    recovery, called on every real inbound turn. Resends whichever post-
    practice follow-up prompt previously failed (or is stuck in a timed-out
    RETRYING claim -- a prior attempt crashed between claiming and sending),
    exactly once each -- claim_prompt_send's atomic CAS is what makes this
    idempotent under concurrency, including two overlapping inbound turns
    both running this sweep for the same user.

    Never retried unless every one of the stale-prompt guards holds: rollout
    allowed, no active crisis, no active disclosure flow, the owning session
    still exists and is OPEN, and -- critically -- the reporting window is
    still ACTIVE. That last check is what stops a failed prompt from an old
    topic reappearing in a new conversation: a topic change, /start, a new
    disclosure flow, a conversation close, or a crisis all invalidate the
    window (database.supersede_active_practice_proposals), so a proposal
    whose prompt failed before one of those events simply drops out of this
    sweep for good, exactly once it becomes stale."""
    if await get_active_crisis(uid) is not None:
        return
    if await get_active_disclosure_flow(uid) is not None:
        return
    if not await access_control.core_rollout_allowed(uid):
        return
    try:
        pending = await get_proposals_with_failed_prompts(uid)
    except Exception:
        return
    if not pending:
        return
    lang = await get_user_language(uid) or "ru"
    for p in pending:
        if p.reporting_window_status != "ACTIVE":
            continue  # stale -- invalidated since the send failed, never resurrect it
        owning_session = await get_core_session(p.session_id, uid)
        if owning_session is None or owning_session.lifecycle_status is not LifecycleStatus.OPEN:
            continue
        if (p.status is PracticeProposalStatus.STARTED
                and p.outcome_prompt_status in ("FAILED", "RETRYING")):
            claim_id = await claim_prompt_send(p.proposal_id, uid, "outcome", "STARTED")
            if not claim_id:
                continue
            if not await _prompt_claim_still_safe(uid, p.proposal_id, "outcome", claim_id, "STARTED"):
                await mark_prompt_failed(p.proposal_id, uid, "outcome", claim_id)
                continue
            try:
                sent = await message.answer(
                    ("Получилось выполнить практику?\n\n"
                     "Можно выбрать вариант или ответить своими словами.") if lang != "en" else
                    ("Were you able to do the practice?\n\n"
                     "You can choose an option or reply in your own words."),
                    reply_markup=_practice_did_kb(p.proposal_id, lang))
                await mark_prompt_delivered(p.proposal_id, uid, "outcome", sent.message_id, claim_id)
            except Exception as e:
                print(f"[controller] outcome-prompt retry failed uid={uid}: {type(e).__name__}: {e}")
                await mark_prompt_failed(p.proposal_id, uid, "outcome", claim_id)
        elif (p.status is PracticeProposalStatus.COMPLETED
              and p.helped_prompt_status in ("FAILED", "RETRYING")):
            claim_id = await claim_prompt_send(p.proposal_id, uid, "helped", "COMPLETED")
            if not claim_id:
                continue
            if not await _prompt_claim_still_safe(uid, p.proposal_id, "helped", claim_id, "COMPLETED"):
                await mark_prompt_failed(p.proposal_id, uid, "helped", claim_id)
                continue
            try:
                sent = await message.answer(
                    "Помогла ли практика хотя бы немного?" if lang != "en" else
                    "Did the practice help at least a little?",
                    reply_markup=_practice_help_kb(p.proposal_id, lang))
                await mark_prompt_delivered(p.proposal_id, uid, "helped", sent.message_id, claim_id)
            except Exception as e:
                print(f"[controller] helped-prompt retry failed uid={uid}: {type(e).__name__}: {e}")
                await mark_prompt_failed(p.proposal_id, uid, "helped", claim_id)


# ── Conversation Controller: post-practice outcome (Phase 3 final closure §3
# /§4/§5) -- callback_data "cc:outcome:<proposal_id>:<done|stopped|refused>".
# STARTED is the only actionable prior state; every check below is
# independent and additive, matching cb_cc_consent's discipline exactly.

@dp.callback_query(F.data.startswith("cc:outcome:"))
async def cb_cc_outcome(callback: CallbackQuery):
    uid = callback.from_user.id
    parts = callback.data.split(":", 3)
    if len(parts) != 4:
        await callback.answer()
        return
    _, _tag, proposal_id, value = parts
    if value not in ("done", "stopped", "refused"):
        await callback.answer()
        return
    if not await access_control.core_rollout_allowed(uid):
        await callback.answer()
        return
    if await get_active_crisis(uid) is not None:
        await callback.answer()
        return
    if await get_active_disclosure_flow(uid) is not None:
        await callback.answer()
        return
    proposal = await get_practice_proposal(proposal_id, uid)
    if proposal is None:
        await callback.answer()
        return
    # PR #73 FINAL REQUEST CHANGES §1 / ATOMIC CLOSURE §5: a stale callback
    # (topic change, new disclosure, /start, close, or crisis already
    # invalidated the reporting window) must not mutate the proposal and
    # must not show any user-visible message -- but it MUST still answer
    # the callback (silently, no text) so the Telegram client's loading
    # spinner clears. `status` itself (STARTED etc.) stays a truthful,
    # untouched historical record; only the window governs whether the
    # BUTTON is still actionable right now. This non-atomic read is a fast
    # path only -- ATOMIC CLOSURE §1's require_active_reporting_window on
    # the actual CAS below is the real, race-proof authority.
    if proposal.reporting_window_status != "ACTIVE":
        await callback.answer()
        return
    # Final closure §7 boundary: a /start (or any other) reset that PAUSED
    # the owning session makes a dangling outcome-report button inert too --
    # same discipline as cb_cc_consent, reusing the existing session-pause
    # mechanism rather than adding a second, competing invalidation path.
    owning_session = await get_core_session(proposal.session_id, uid)
    if owning_session is None or owning_session.lifecycle_status is not LifecycleStatus.OPEN:
        await callback.answer()
        return
    lang = await get_user_language(uid) or "ru"

    if value == "done":
        # §3: COMPLETED means the user explicitly reported completion --
        # never inferred from delivery. Atomic CAS: STARTED -> COMPLETED.
        # The reporting window STAYS ACTIVE here -- the helped-prompt report
        # is still pending, closed only once THAT is answered or withdrawn.
        if not await transition_practice_proposal(
                proposal_id, uid, from_status=PracticeProposalStatus.STARTED.value,
                to_status=PracticeProposalStatus.COMPLETED.value,
                require_active_reporting_window=True):
            await callback.answer()
            return
        await callback.answer()
        # PR #73 FINAL REQUEST CHANGES §2 / ATOMIC CLOSURE §2/§3: claim-first
        # send with a persisted claim_id, revalidated immediately before the
        # send -- see cb_cc_consent's outcome-prompt send for the full
        # rationale (shared via _prompt_claim_still_safe).
        helped_claim_id = await claim_prompt_send(proposal_id, uid, "helped", "COMPLETED")
        if helped_claim_id:
            if await _prompt_claim_still_safe(uid, proposal_id, "helped", helped_claim_id, "COMPLETED"):
                try:
                    sent = await callback.message.answer(
                        "Хорошо. Как это подействовало?" if lang != "en" else "Good. How did that go?",
                        reply_markup=_practice_helped_kb(proposal_id, lang))
                    await mark_prompt_delivered(proposal_id, uid, "helped", sent.message_id, helped_claim_id)
                except Exception as e:
                    print(f"[controller] helped-prompt delivery failed uid={uid}: {type(e).__name__}: {e}")
                    await mark_prompt_failed(proposal_id, uid, "helped", helped_claim_id)
            else:
                await mark_prompt_failed(proposal_id, uid, "helped", helped_claim_id)
        return

    # "stopped" (paused, no pressure to retry) and "refused" (explicit
    # decline to continue) both WITHDRAW -- §3: withdrawal is never treated
    # as resistance or failure, only distinguished by whether
    # EXERCISE_REJECTED gets persisted (§4's "refusal prevents automatic
    # re-proposal" -- there is no automatic re-proposal path at all, but a
    # LATER unprompted LLM offer is additionally blocked by this constraint).
    # No further report is expected after a withdrawal, so the reporting
    # window closes in the same atomic write (§1).
    reason = "user_stopped" if value == "stopped" else "user_refused"
    if not await transition_practice_proposal(
            proposal_id, uid, from_status=PracticeProposalStatus.STARTED.value,
            to_status=PracticeProposalStatus.WITHDRAWN.value, reason=reason,
            close_reporting_window=True, require_active_reporting_window=True):
        await callback.answer()
        return
    await callback.answer()
    if value == "refused":
        # PR #73 request-changes §8: check the CAS result -- never claim a
        # write persisted when it did not, and never overwrite a NEWER
        # turn's session state with a stale snapshot. One bounded retry from
        # a fresh snapshot covers the common case (no other write landed in
        # between); if that also loses, the truthful copy below reflects
        # what actually happened instead of promising something moot.
        persisted = False
        for _ in range(2):
            session = await get_core_session(proposal.session_id, uid)
            if session is None or session.lifecycle_status is not LifecycleStatus.OPEN:
                break
            from datetime import datetime, timezone
            prior_json = session_json_snapshot(session)
            session.add_repair_signal(
                {RepairConstraint.EXERCISE_REJECTED}, source_turn_id=None,
                created_at=datetime.now(timezone.utc).isoformat(),
                window_turns=controller.REPAIR_WINDOW_TURNS)
            if await update_core_session_authoritative(session, prior_json):
                persisted = True
                break
        # Truthful, scope-accurate copy either way: EXERCISE_REJECTED is a
        # bounded-turn-window constraint, explicitly overrideable by a later
        # explicit request (conversation_controller.classify_repair_
        # overrides) -- never "never again", and never claimed as recorded
        # if the write actually did not land.
        if persisted:
            text = ("Понял. Не буду предлагать упражнения без твоего явного "
                    "запроса в ближайшем продолжении разговора." if lang != "en" else
                    "Got it. I won't suggest exercises without your explicit "
                    "request for the next while.")
        else:
            text = ("Понял, что не хочешь продолжать сейчас." if lang != "en" else
                    "Got it, you don't want to continue right now.")
    else:
        text = ("Хорошо, останавливаемся. Ты не обязан(а) продолжать." if lang != "en"
               else "Okay, let's stop. You don't have to continue.")
    await callback.message.answer(text)


_HELPED_TO_OUTCOME = {
    "helped": PracticeOutcome.HELPED.value,
    "partly": PracticeOutcome.PARTLY_HELPED.value,
    "none": PracticeOutcome.NO_CHANGE.value,
    "worse": PracticeOutcome.WORSE.value,
}


@dp.callback_query(F.data.startswith("cc:helped:"))
async def cb_cc_outcome_detail(callback: CallbackQuery):
    """§5: purely qualitative, explicit, self-reported outcome -- no before
    score, none estimated. Recorded once (the DB write is a no-op past the
    first answer); worsening is recorded truthfully with no causality claim
    and no repeat-practice/crisis side effect (crisis detection stays fully
    independent, driven only by message-text risk scoring, never by this
    button)."""
    uid = callback.from_user.id
    parts = callback.data.split(":", 3)
    if len(parts) != 4:
        await callback.answer()
        return
    _, _tag, proposal_id, value = parts
    outcome = _HELPED_TO_OUTCOME.get(value)
    if outcome is None:
        await callback.answer()
        return
    if not await access_control.core_rollout_allowed(uid):
        await callback.answer()
        return
    if await get_active_crisis(uid) is not None:
        await callback.answer()
        return
    if await get_active_disclosure_flow(uid) is not None:
        await callback.answer()
        return
    # PR #73 request-changes §4: cc:helped must independently verify
    # ownership/existence/status/session-lifecycle exactly like cc:outcome
    # does -- not rely on record_practice_outcome's own CAS alone (that CAS
    # already enforces ownership+COMPLETED+not-yet-recorded atomically, but
    # the session-lifecycle check below is a SEPARATE axis it cannot see).
    proposal = await get_practice_proposal(proposal_id, uid)
    if proposal is None or proposal.status is not PracticeProposalStatus.COMPLETED:
        await callback.answer()
        return
    # PR #73 FINAL REQUEST CHANGES §1 / ATOMIC CLOSURE §5: same stale-window
    # discipline as cb_cc_outcome -- no mutation, no user-visible message,
    # but the callback IS still answered (silently) so the client's loading
    # spinner clears. `status`/`outcome` left untouched. Fast-path only --
    # require_active_reporting_window on the write below is the real
    # authority.
    if proposal.reporting_window_status != "ACTIVE":
        await callback.answer()
        return
    owning_session = await get_core_session(proposal.session_id, uid)
    if owning_session is None or owning_session.lifecycle_status is not LifecycleStatus.OPEN:
        await callback.answer()
        return
    # Legacy handler predates the progressive refinement flow -- it must fail
    # closed (not silently overwrite) once a pending UX_PENDING_OUTCOME_DETAIL
    # marker exists, so an untouched old Telegram message can never race past
    # a refinement already in progress.
    ok = await record_practice_outcome(proposal_id, uid, outcome,
                                       require_active_reporting_window=True,
                                       require_prior_reason_null=True)
    await callback.answer()
    if not ok:
        return
    lang = await get_user_language(uid) or "ru"
    if outcome == PracticeOutcome.WORSE.value:
        text = ("Спасибо, что сказал(а) честно — я это записал(а). Если станет тяжелее, "
                "напиши мне об этом." if lang != "en" else
                "Thanks for being honest — I've noted that. If things get harder, tell me.")
    else:
        text = "Спасибо, что рассказал(а)." if lang != "en" else "Thanks for telling me."
    await callback.message.answer(text)


# ── Progressive two-button practice UX callbacks ────────────────────────────
# Every handler below follows the exact same independent/additive safety
# discipline as cb_cc_outcome/cb_cc_outcome_detail above: rollout, crisis,
# disclosure, proposal existence+ownership, reporting-window ACTIVE (fast
# path; the atomic CAS below is the real authority), owning-session OPEN.
# A stale callback never mutates anything and never sends a visible
# message, but is still answered (silently) so the Telegram client's
# loading spinner clears.

@dp.callback_query(F.data.startswith("cc:practdone:"))
async def cb_cc_practdone(callback: CallbackQuery):
    """Step A: 'Получилось' / 'Не получилось' -- replaces the legacy
    3-button 'Как прошло?' prompt. 'Получилось' is exactly the old 'done'
    transition (STARTED->COMPLETED), just relabeled. 'Не получилось' is
    NOT yet a terminal answer -- it withdraws truthfully (reason=
    UX_PENDING_NOT_COMPLETED_REASON, an internal marker, never a real
    withdrawal cause) but keeps the reporting window ACTIVE, then asks one
    more question to distinguish an attempt from never starting."""
    uid = callback.from_user.id
    parts = callback.data.split(":", 3)
    if len(parts) != 4:
        await callback.answer()
        return
    _, _tag, proposal_id, value = parts
    if value not in ("yes", "no"):
        await callback.answer()
        return
    if not await access_control.core_rollout_allowed(uid):
        await callback.answer()
        return
    if await get_active_crisis(uid) is not None:
        await callback.answer()
        return
    if await get_active_disclosure_flow(uid) is not None:
        await callback.answer()
        return
    proposal = await get_practice_proposal(proposal_id, uid)
    if proposal is None:
        await callback.answer()
        return
    if proposal.reporting_window_status != "ACTIVE":
        await callback.answer()
        return
    owning_session = await get_core_session(proposal.session_id, uid)
    if owning_session is None or owning_session.lifecycle_status is not LifecycleStatus.OPEN:
        await callback.answer()
        return
    lang = await get_user_language(uid) or "ru"

    if value == "yes":
        if not await transition_practice_proposal(
                proposal_id, uid, from_status=PracticeProposalStatus.STARTED.value,
                to_status=PracticeProposalStatus.COMPLETED.value,
                require_active_reporting_window=True):
            await callback.answer()
            return
        await callback.answer()
        helped_claim_id = await claim_prompt_send(proposal_id, uid, "helped", "COMPLETED")
        if helped_claim_id:
            if await _prompt_claim_still_safe(uid, proposal_id, "helped", helped_claim_id, "COMPLETED"):
                try:
                    sent = await callback.message.answer(
                        "Помогла ли практика хотя бы немного?" if lang != "en" else
                        "Did the practice help at least a little?",
                        reply_markup=_practice_help_kb(proposal_id, lang))
                    await mark_prompt_delivered(proposal_id, uid, "helped", sent.message_id, helped_claim_id)
                except Exception as e:
                    print(f"[controller] help-prompt delivery failed uid={uid}: {type(e).__name__}: {e}")
                    await mark_prompt_failed(proposal_id, uid, "helped", helped_claim_id)
            else:
                await mark_prompt_failed(proposal_id, uid, "helped", helped_claim_id)
        return

    # "no": not terminal yet. Reporting window stays ACTIVE (no
    # close_reporting_window) -- one more answer is still expected.
    if not await transition_practice_proposal(
            proposal_id, uid, from_status=PracticeProposalStatus.STARTED.value,
            to_status=PracticeProposalStatus.WITHDRAWN.value, reason=UX_PENDING_NOT_COMPLETED_REASON,
            require_active_reporting_window=True):
        await callback.answer()
        return
    await callback.answer()
    try:
        await callback.message.answer(
            ("Понял. Что произошло ближе всего?\n\n"
             "Можно выбрать вариант или написать своими словами.") if lang != "en" else
            ("Understood. What happened most closely?\n\n"
             "You can choose an option or reply in your own words."),
            reply_markup=_practice_notdone_kb(proposal_id, lang))
    except Exception as e:
        print(f"[controller] practwhy prompt delivery failed uid={uid}: {type(e).__name__}: {e}")


@dp.callback_query(F.data.startswith("cc:practwhy:"))
async def cb_cc_practwhy(callback: CallbackQuery):
    """Step A2: 'Начал, но остановился' / 'Не начал'. Refines an already-
    WITHDRAWN(reason=UX_PENDING_NOT_COMPLETED_REASON) proposal into its truthful specific
    reason via a SAME-STATUS CAS additionally gated on the CURRENT reason
    (require_prior_reason) -- this is what makes a duplicate/racing tap
    lose, since `status` alone never changes across this step. Neither
    answer persists EXERCISE_REJECTED: an attempt-then-stop and a never-
    started are both truthful non-completions, not proof of refusal or
    dislike."""
    uid = callback.from_user.id
    parts = callback.data.split(":", 3)
    if len(parts) != 4:
        await callback.answer()
        return
    _, _tag, proposal_id, value = parts
    if value not in ("stopped", "never"):
        await callback.answer()
        return
    if not await access_control.core_rollout_allowed(uid):
        await callback.answer()
        return
    if await get_active_crisis(uid) is not None:
        await callback.answer()
        return
    if await get_active_disclosure_flow(uid) is not None:
        await callback.answer()
        return
    proposal = await get_practice_proposal(proposal_id, uid)
    if proposal is None:
        await callback.answer()
        return
    if proposal.reporting_window_status != "ACTIVE":
        await callback.answer()
        return
    owning_session = await get_core_session(proposal.session_id, uid)
    if owning_session is None or owning_session.lifecycle_status is not LifecycleStatus.OPEN:
        await callback.answer()
        return
    reason = "user_stopped" if value == "stopped" else "user_did_not_start"
    if not await transition_practice_proposal(
            proposal_id, uid, from_status=PracticeProposalStatus.WITHDRAWN.value,
            to_status=PracticeProposalStatus.WITHDRAWN.value,
            require_prior_reason=UX_PENDING_NOT_COMPLETED_REASON, reason=reason,
            close_reporting_window=True, require_active_reporting_window=True):
        await callback.answer()
        return
    await callback.answer()
    lang = await get_user_language(uid) or "ru"
    try:
        # Open question, no keyboard -- free text (or a topic change) is
        # always the next valid move, not another forced choice.
        await callback.message.answer(
            "Что помешало больше всего?" if lang != "en" else "What got in the way most?")
    except Exception as e:
        print(f"[controller] practwhy followup delivery failed uid={uid}: {type(e).__name__}: {e}")


@dp.callback_query(F.data.startswith("cc:practhelp:"))
async def cb_cc_practhelp(callback: CallbackQuery):
    """Step B: 'Помогло' / 'Не помогло' -- replaces the legacy 4-button
    'Как это подействовало?' prompt. 'Помогло' records HELPED exactly like
    the legacy 'helped' answer. 'Не помогло' is NOT recorded as NO_CHANGE
    yet (the state may have worsened) -- a same-status CAS additionally
    gated on superseded_reason being NULL (require_prior_reason_null) makes
    this refinement step exactly-once without ever touching `outcome`
    (whose CHECK constraint has no 'pending' value)."""
    uid = callback.from_user.id
    parts = callback.data.split(":", 3)
    if len(parts) != 4:
        await callback.answer()
        return
    _, _tag, proposal_id, value = parts
    if value not in ("yes", "no"):
        await callback.answer()
        return
    if not await access_control.core_rollout_allowed(uid):
        await callback.answer()
        return
    if await get_active_crisis(uid) is not None:
        await callback.answer()
        return
    if await get_active_disclosure_flow(uid) is not None:
        await callback.answer()
        return
    proposal = await get_practice_proposal(proposal_id, uid)
    if proposal is None or proposal.status is not PracticeProposalStatus.COMPLETED:
        await callback.answer()
        return
    if proposal.reporting_window_status != "ACTIVE":
        await callback.answer()
        return
    owning_session = await get_core_session(proposal.session_id, uid)
    if owning_session is None or owning_session.lifecycle_status is not LifecycleStatus.OPEN:
        await callback.answer()
        return
    lang = await get_user_language(uid) or "ru"

    if value == "yes":
        # Mutually exclusive with the "no" refinement branch below:
        # both paths require an ACTIVE reporting window and
        # superseded_reason still NULL.
        # If "yes" commits first, record_practice_outcome records HELPED and
        # closes the reporting window, so the later "no" CAS fails.
        # If "no" commits first, transition_practice_proposal atomically sets
        # UX_PENDING_OUTCOME_DETAIL while keeping the proposal COMPLETED and
        # the reporting window ACTIVE, so a later "yes" fails
        # require_prior_reason_null.
        ok = await record_practice_outcome(proposal_id, uid, PracticeOutcome.HELPED.value,
                                           require_active_reporting_window=True,
                                           require_prior_reason_null=True)
        await callback.answer()
        if not ok:
            return
        await callback.message.answer(
            "Понял. Отмечу, что в этот раз практика оказалась полезной." if lang != "en" else
            "Understood. I'll record that this practice was useful this time.")
        return

    # "no": not terminal yet -- never write NO_CHANGE prematurely.
    if not await transition_practice_proposal(
            proposal_id, uid, from_status=PracticeProposalStatus.COMPLETED.value,
            to_status=PracticeProposalStatus.COMPLETED.value,
            require_prior_reason_null=True, reason=UX_PENDING_OUTCOME_DETAIL,
            require_active_reporting_window=True):
        await callback.answer()
        return
    await callback.answer()
    try:
        await callback.message.answer(
            "Что ближе к твоему состоянию сейчас?" if lang != "en" else
            "Which is closer to how you feel now?",
            reply_markup=_practice_helpwhy_kb(proposal_id, lang))
    except Exception as e:
        print(f"[controller] practhelpwhy prompt delivery failed uid={uid}: {type(e).__name__}: {e}")


@dp.callback_query(F.data.startswith("cc:practhelpwhy:"))
async def cb_cc_practhelpwhy(callback: CallbackQuery):
    """Step C: 'Без изменений' / 'Стало хуже'. record_practice_outcome's
    own `outcome IS NULL` CAS is what makes this exactly-once (unaffected
    by superseded_reason). WORSE still writes to the same `outcome` column
    the informed-repeat guard (_controller_claim_turn's get_latest_outcome_
    for_practice check) already reads -- that guard is completely
    unaffected by which callback wrote the value."""
    uid = callback.from_user.id
    parts = callback.data.split(":", 3)
    if len(parts) != 4:
        await callback.answer()
        return
    _, _tag, proposal_id, value = parts
    outcome = {"same": PracticeOutcome.NO_CHANGE.value, "worse": PracticeOutcome.WORSE.value}.get(value)
    if outcome is None:
        await callback.answer()
        return
    if not await access_control.core_rollout_allowed(uid):
        await callback.answer()
        return
    if await get_active_crisis(uid) is not None:
        await callback.answer()
        return
    if await get_active_disclosure_flow(uid) is not None:
        await callback.answer()
        return
    proposal = await get_practice_proposal(proposal_id, uid)
    if proposal is None or proposal.status is not PracticeProposalStatus.COMPLETED:
        await callback.answer()
        return
    if proposal.reporting_window_status != "ACTIVE":
        await callback.answer()
        return
    owning_session = await get_core_session(proposal.session_id, uid)
    if owning_session is None or owning_session.lifecycle_status is not LifecycleStatus.OPEN:
        await callback.answer()
        return
    # Finalizes the pending refinement -- the terminal outcome write and the
    # removal of UX_PENDING_OUTCOME_DETAIL happen in the SAME atomic UPDATE
    # (clear_superseded_reason=True), never a separate clearing write. The
    # require_prior_reason CAS also means this can only ever finalize a row
    # that genuinely went through "Не помогло" -- a stray/duplicate tap
    # after the marker is already cleared loses cleanly.
    ok = await record_practice_outcome(proposal_id, uid, outcome,
                                       require_active_reporting_window=True,
                                       require_prior_reason=UX_PENDING_OUTCOME_DETAIL,
                                       clear_superseded_reason=True)
    await callback.answer()
    if not ok:
        return
    lang = await get_user_language(uid) or "ru"
    if outcome == PracticeOutcome.WORSE.value:
        # No causality claim -- the user's own report is noted, not
        # attributed to the practice.
        text = ("Ты отметил(а), что после практики стало хуже. Я не буду автоматически "
                "предлагать её снова." if lang != "en" else
                "You noted that you felt worse after the practice. I won't automatically "
                "suggest it again.")
    else:
        text = "Понял, спасибо, что рассказал(а)." if lang != "en" else "Understood, thanks for telling me."
    await callback.message.answer(text)


@dp.callback_query(F.data.startswith("after:"))
async def cb_after(callback: CallbackQuery, fsm_state: FSMContext):
    score = int(callback.data.split(":")[1])
    data = await fsm_state.get_data()
    if data:
        await fsm_state.update_data(after_score=score)
        await fsm_state.set_state(InterventionStates.awaiting_quality)
        await callback.message.answer(
            ("Как оценить практику?" if data.get("lang") == "ru" else "Rate the practice?"),
            reply_markup=quality_kb())
    await callback.answer()

@dp.callback_query(F.data.startswith("quality:"))
async def cb_quality(callback: CallbackQuery, fsm_state: FSMContext):
    uid, rating = callback.from_user.id, int(callback.data.split(":")[1])
    data = await fsm_state.get_data()
    await fsm_state.clear()

    if data and "after_score" in data:
        before = data.get("before_score", 5)
        after  = data["after_score"]
        await finish_intervention(
            data["intervention_id"],
            after_score=after,
            feedback_rating=rating,
            confidence_score=1.0,
            engagement_metrics={"quality_rating": rating},
        )
        delta = after - before   # positive = improvement (10=good scale)
        await update_user_profile(uid, data.get("scenario", "open_chat"), delta, rating >= 0)
        if after < before - 2:   # worsening: score dropped significantly
            await log_adverse_event(uid, data["intervention_id"], data["practice_id"],
                                   PRACTICE_VERSION, "worsening",
                                   f"After {after} < before {before}", delta, "medium")
        msg = ("Рад помочь 🙂" if data.get("lang") == "ru" else "Glad to help 🙂") if rating >= 0 else \
              ("Спасибо за честность" if data.get("lang") == "ru" else "Thanks for honesty")
        await callback.message.answer(msg, reply_markup=ReplyKeyboardRemove())
        await _maybe_react(callback.message, uid, ReactionCategory.PRACTICE_COMPLETED, 1.0)
    await callback.answer()

# Not RED (reserved for an actual risk_detector suicide/self_harm text
# classification -- trigger_crisis's only other caller) and not ORANGE
# (reserved for a risk_detector high/critical SCORE classification -- also
# never computed here). This is an honest, distinct descriptor of the
# SOURCE of the event: the user tapped a button, no text was scored.
# crisis_events.level has no CHECK constraint (verified against the schema),
# and nothing in the crisis subsystem branches on its value (crisis_screen,
# send_crisis, cb_crisis, get_active_crisis, resolve_crisis, bump_crisis_stage
# all key off event_id/resolved/crisis_stage -- never level) -- so this is
# purely a truthful record-keeping label, not a classification with side
# effects, and not a diagnosis.
HARDREG_UNSAFE_SELF_REPORT_LEVEL = "SELF_REPORTED"


async def _handle_hardreg_unsafe(callback: CallbackQuery, result, uid: int, lang: str) -> None:
    """hardreg:unsafe -- the user self-reported 'Мне небезопасно' via a
    button tap, not a risk_detector text classification.

    A REAL crisis_events row is required here, not merely a bare crisis
    message: cb_crisis's "safe"/"still" buttons only do anything when they
    can resolve an event_id (either embedded in 3-part callback_data, or via
    get_active_crisis(uid) for the legacy 2-part form) -- with no event at
    all, tapping either button is a silent no-op and the user is left at a
    dead keyboard. So this reuses the existing official event-creation API
    (log_crisis_event) with the honest, non-RED/non-ORANGE level above, then
    the existing staged crisis_screen(0, lang, eid) -- the SAME screen/
    keyboard/escalation machinery trigger_crisis uses -- so "safe"/"still"/
    "call"/"cant_call" are all genuinely functional afterward, not a second,
    parallel safety system. Delivered through the real send_crisis ladder +
    delivery logging + P0-alert-on-total-failure path. Sent at most once,
    never retried after an uncertain outcome.

    If log_crisis_event itself fails, degrades EXACTLY like trigger_crisis's
    own established precedent for the same failure: plain crisis text, NO
    buttons at all (never a stateful crisis:* button with no event behind
    it -- the DB instability that broke event creation could still be broken
    the moment the user taps a button).

    Resolves the consumed interaction event honestly by reusing
    finalize_callback_reply (never invents a new event status): 'delivered'
    only when send_crisis actually confirmed delivery at some ladder level,
    'delivery_uncertain' (the existing honest failure state) otherwise."""
    eid = None
    try:
        eid = await log_crisis_event(
            uid, HARDREG_UNSAFE_SELF_REPORT_LEVEL, 0, ["self_reported_unsafe"],
            normalized_action_text("hardreg:unsafe", lang), lang, admin_notified=False)
    except Exception as e:
        # Redacted: fixed event name + uid + exception class only -- never
        # the raw exception message (same convention as
        # finalize_callback_reply's persistence-failure diagnostic).
        print(f"event=hardreg_unsafe_crisis_event_create_failed uid={uid} "
              f"exc_type={type(e).__name__}")
        eid = None

    if eid is not None:
        text, kb = crisis_screen(0, lang, eid)
    else:
        text, kb = get_crisis_text(lang), None

    level_delivered = await send_crisis(callback.message.answer, text, kb, lang, uid,
                                        eid, "hardreg_unsafe")
    if level_delivered != "none":
        await finalize_callback_reply(result.event_id, uid, text)
    else:
        await mark_event_besteffort(result.event_id, uid, "delivery_uncertain")


@dp.callback_query(F.data.startswith("ucbtn:"))
async def cb_universal_continuation(callback: CallbackQuery):
    """Single handler for every DB-owned universal-continuation action --
    elaborate/clarify (LLM-generated, source-grounded) and the deterministic
    hard:*/hardreg:*/hardstate:* graph. The accepted action always comes
    from consume_interaction_binding — never trusted from callback_data,
    which carries only the opaque token."""
    uid = callback.from_user.id
    token = callback.data[len("ucbtn:"):]
    chat_id = callback.message.chat.id
    source_message_id = callback.message.message_id

    result = await consume_interaction_binding(token, uid, chat_id, source_message_id)
    if result is None:
        # Stale/expired/duplicate/wrong-user/wrong-message: no assistant
        # message, no second interaction event, no restart of any flow —
        # just a short localized notice on the callback popup itself. No
        # source binding was accepted here, so the stored profile language
        # is the only option available.
        popup_lang = await get_stored_user_language(uid) or "ru"
        await callback.answer(
            "Кнопка больше не активна." if popup_lang == "ru" else "This button is no longer active.")
        return
    await callback.answer()

    # The reply, the nested keyboard, and the persisted metadata are all
    # source-turn-owned -- never the current stored profile language, which
    # may have changed since the source turn.
    lang = result.lang
    scenario = result.scenario

    if result.action == "hardreg:unsafe":
        # Terminal safety-reuse leaf: its own send path (send_crisis, real
        # keyboard, real delivery logging), never the generic single
        # callback.message.answer(answer) + finalize_callback_reply route
        # below -- see _handle_hardreg_unsafe.
        await _handle_hardreg_unsafe(callback, result, uid, lang)
        return

    if result.action in ("elaborate", "clarify"):
        user_text = await get_last_user_message_before(uid, result.turn_id)
        answer = await _continuation_generate_and_validate(
            result.action, user_text, result.source_text, scenario, lang)
        # H: the binding was consumed BEFORE this (possibly slow) LLM call.
        # Re-confirm the live revision still matches the post-consumption
        # revision immediately before any Telegram send -- if it moved, a
        # newer user action already superseded this one: send nothing,
        # persist nothing, publish no keyboard, mark the event superseded,
        # return cleanly, never retry, never log the source or generated
        # text.
        live_revision = await get_user_revision(uid)
        if live_revision != result.post_consumption_revision:
            await mark_event_besteffort(result.event_id, uid, "no_reply_required")
            return
        next_ru = next_en = None
    else:
        answer, next_ru, next_en = await _continuation_reply_and_next(result.action, uid, lang)

    try:
        sent = await callback.message.answer(answer)
    except Exception:
        await mark_event_besteffort(result.event_id, uid, "delivery_uncertain", SEND_EXCEPTION)
        return

    # finalize_callback_reply derives scenario/lang itself from the source
    # assistant turn — the handler passes neither — and returns the real
    # inserted row id, never located by matching reply text.
    fin = await finalize_callback_reply(result.event_id, uid, answer)

    if fin.status == "delivered" and next_ru is not None:
        # Exactly the post_consumption_revision returned by the consume
        # transaction above — never a freshly-read revision.
        await _publish_continuation_options(callback.message, uid, fin.assistant_turn_id,
                                            sent.message_id, result.post_consumption_revision,
                                            lang, next_ru, next_en)

# ── /format — response-delivery preference selector ─────────────────────────

@dp.message(Command("format"))
async def cmd_format(message: Message):
    uid = message.from_user.id
    lang = await get_user_language(uid)
    if not await _voice_ux_enabled_for(uid):
        return
    if getattr(message.chat, "type", "private") != "private":
        # §4: private-chat-only in V1 -- a short neutral notice, never the
        # selector itself (never repeats/exposes anything private).
        await message.answer(
            "Эта настройка доступна только в личном чате со мной." if lang == "ru"
            else "This setting is only available in a private chat with me.")
        return
    await message.answer(
        _response_format_setup_text(lang),
        reply_markup=format_selector_kb(lang))


@dp.callback_query(F.data.startswith(f"{_FMT_KB_VERSION}:"))
async def cb_format_select(callback: CallbackQuery):
    uid = callback.from_user.id
    if not await _voice_ux_enabled_for(uid):
        # Flag off, OR flag on but not the owner: fail closed -- a stale
        # button from before rollback, or a non-owner during canary, must
        # never silently save anything.
        await callback.answer()
        return
    if getattr(callback.message.chat, "type", "private") != "private":
        # §4: a group callback must never modify a persistent preference.
        await callback.answer()
        return
    try:
        _, kind, value = callback.data.split(":")
    except ValueError:
        await callback.answer()
        return
    if kind == "format" and value in ("text", "voice", "voice_and_concise_text"):
        await set_response_preference(uid, response_format=value)
    elif kind == "length" and value in ("concise", "normal"):
        await set_response_preference(uid, response_length=value)
    else:
        # Malformed/forged value outside the closed vocabulary -- fail closed.
        await callback.answer()
        return
    lang = await get_user_language(uid)
    await _edit_or_answer(callback.message)(
        ("Сохранено ✅\n\n" if lang == "ru" else "Saved ✅\n\n")
        + _response_format_setup_text(lang),
        reply_markup=format_selector_kb(lang),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith(f"{_LISTEN_KB_VERSION}:"))
async def cb_listen(callback: CallbackQuery):
    """"🔊 Прослушать" — synthesizes the EXACT visible text of the message
    the button is attached to. No new LLM call, no deeper memory lookup, no
    DB row created. Ownership is checked because the owning uid is encoded
    in callback_data (a stateless action, unlike cb_before's FSM-scoped
    flow) -- a forged/cross-user/malformed callback all fail closed."""
    uid = callback.from_user.id
    if not await _voice_ux_enabled_for(uid):
        # Flag off, OR flag on but not the owner -- defense in depth: the
        # listen button is only ever attached by deliver_response for an
        # eligible owner turn, but a forged/replayed callback_data must
        # still fail closed here regardless.
        await callback.answer()
        return
    if getattr(callback.message.chat, "type", "private") != "private":
        # §4: the listen button is never attached outside a private chat
        # (deliver_response only attaches it when is_private) -- this is a
        # defense-in-depth fail-closed for a forged/replayed callback_data.
        await callback.answer()
        return
    try:
        owner_uid = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer()
        return
    if owner_uid != uid:
        await callback.answer()
        return
    text = callback.message.text
    if not text:
        await callback.answer()
        return
    now = time.time()
    if now - _listen_last_tap.get(uid, 0) < _LISTEN_TAP_COOLDOWN_SECONDS:
        await callback.answer()
        return
    _listen_last_tap[uid] = now
    lang = await get_user_language(uid)
    # Re-validate before TTS even though this text was already sent once --
    # defense in depth, same "only Safety-Validator-approved text may enter
    # TTS" contract as deliver_response, applied consistently at every TTS
    # call site rather than assumed safe because it was validated once.
    is_safe, _ = validate_response(text, lang)
    if not is_safe:
        await callback.answer(
            "Не получилось озвучить" if lang == "ru" else "Couldn't create voice")
        return
    ok = await _synthesize_and_send_voice(callback.message, uid, text, lang)
    await callback.answer(
        None if ok else ("Не получилось озвучить" if lang == "ru" else "Couldn't create voice"))

# ────────────────────────────────────────────────────────────────────────────

async def _render_onboarding_card(uid: int, chat_id: int, step: int, lang: str, *,
                                  message_id: int | None,
                                  first_name: str = "") -> None:
    """Render+persist ONE onboarding card (spec items G/H): delivers `step` by
    editing `message_id` if given (falls back to a fresh card on
    TelegramBadRequest — see onboarding.send_or_edit_onboarding_card), then
    persists the (chat_id, message_id, step) that is now actually visible.

    Recovery contract: the caller's DB transition (start/advance/skip/retire)
    is ALREADY committed before this runs. If delivery itself raises (a real
    network error), that exception propagates uncaught (never swallowed by a
    blanket except) — current_step is already correct in the DB but the card
    reference is NOT updated, so the next /start or gate-hit naturally retries
    delivering the same already-decided step by editing the same old card."""
    ref = await onboarding.send_or_edit_onboarding_card(
        bot, chat_id, step, lang, message_id=message_id,
        privacy_policy_url=config.PRIVACY_POLICY_URL,
        first_name=first_name)
    if ref is not None:
        await set_onboarding_card_ref(uid, ONBOARDING_VERSION, step, ref[0], ref[1])


async def _render_privacy_notice_only_card(uid: int, chat_id: int, lang: str, *,
                                           message_id: int | None) -> None:
    """Render the PRIVACY_NOTICE_ONLY screen (determine_onboarding_requirement)
    for a user who does not need full onboarding but has not acknowledged the
    CURRENT privacy notice. Deliberately does NOT create or touch any
    user_onboarding_state row and does NOT call set_onboarding_card_ref --
    there is no row to persist a card reference to. This means this screen is
    best-effort, not restart-resumable-by-edit like full onboarding: if
    delivery fails or the user simply /starts again before acknowledging, the
    next attempt sends a fresh card rather than editing a remembered one.
    That is an accepted, explicitly documented trade-off (not exactly-once
    delivery) for avoiding a fake onboarding row -- see
    docs/first_user_onboarding.md."""
    await onboarding.send_or_edit_onboarding_card(
        bot, chat_id, LAST_STEP, lang, message_id=message_id,
        privacy_policy_url=config.PRIVACY_POLICY_URL,
        keyboard=onboarding.build_keyboard_privacy_only(
            PRIVACY_NOTICE_VERSION, lang, config.PRIVACY_POLICY_URL))


async def _onboarding_blocks_ordinary_entry(uid: int) -> bool:
    """True iff the mandatory onboarding gate (spec item A) must block
    ordinary product entry (text/voice/mood, AND any other product command
    like /dass21 or a q:m callback) for uid right now. Reuses the EXACT same
    decision as bot.cmd_start (onboarding_content.determine_onboarding_requirement)
    -- NOT only "has an active row": a user who owes the CURRENT privacy
    notice (PRIVACY_NOTICE_ONLY) is blocked too, even though that flow
    deliberately creates no onboarding row. Fixed gap found during DASS
    integration: the previous version only checked for an active row, so a
    user who bypassed /start (going straight to another product command)
    while owing an independent privacy re-acknowledgment was never gated at
    all -- this closes that."""
    if not config.FIRST_USER_ONBOARDING_ENABLED:
        return False
    active_state = await get_active_onboarding_state(uid)
    if active_state is not None:
        return True
    current_version_row = await get_onboarding_state(uid, ONBOARDING_VERSION)
    eligibility = await get_onboarding_eligibility(uid)
    notice_acked = await has_privacy_notice_ack(uid, PRIVACY_NOTICE_VERSION)
    requirement = onboarding_content.determine_onboarding_requirement(
        eligibility=eligibility, has_active_state=False,
        has_current_version_row=current_version_row is not None,
        notice_acknowledged=notice_acked)
    return requirement != onboarding_content.NOT_REQUIRED


async def _resume_onboarding_card(chat_id: int, uid: int) -> None:
    """Re-show the user's current onboarding/privacy-only card IN PLACE (edit
    if possible, else send exactly one replacement) instead of silently
    dropping their message and instead of flooding the chat with a new card
    per message. Never advances state — only re-renders the current step (or,
    for a privacy-only-pending user with no row at all, the privacy-only
    screen). Uses the onboarding's OWN stored language (not whatever the
    blocked message happened to be written in)."""
    state = await get_active_onboarding_state(uid)
    if state is not None:
        lang = await get_user_language(uid)
        await _render_onboarding_card(uid, chat_id, state["current_step"], lang,
                                      message_id=state.get("card_message_id"))
        return
    if not await _onboarding_blocks_ordinary_entry(uid):
        return  # gate raced away (e.g. just settled) -- nothing to show
    # Blocked with no active row -> a privacy-only acknowledgment is owed.
    lang = await get_user_language(uid)
    await _render_privacy_notice_only_card(uid, chat_id, lang, message_id=None)


# ── C: ONE reusable guard for the WHOLE ordinary product surface ────────────
# Registered as OUTER middleware on both dp.message and dp.callback_query.
# Outer middleware runs BEFORE any specific handler's filters are evaluated
# (aiogram's TelegramEventObserver.wrap_outer_middleware wraps
# Router.propagate_event, which resolves filters/handlers only afterward) --
# so this is a single, non-scattered interception point in front of every
# command and every callback in the bot: mood, emotion map, menu navigation,
# questionnaires, journals, profile, reports, the specialist report, the
# discuss-with-bot topics, check-ins, mute settings, timezone settings, the
# practice before/after/quality flow -- everything.
#
# Classification is DEFAULT-DENY for commands and callbacks: anything not
# explicitly exempted below is blocked while onboarding is active. This fails
# closed for any future command/callback added later without being added to
# the exempt list, rather than silently leaking through an unmaintained
# blocklist.
#
# Free-text and voice MESSAGES are the one deliberate exception: they are
# EXEMPT from this middleware's own judgment (always passed through), and are
# instead gated inside bot.pipeline() itself, AFTER its active-crisis and RED
# checks -- see bot.pipeline's "4.2 Mandatory onboarding gate" comment.
# Whether a plain-text message is an active-crisis reply is content/state
# dependent (get_active_crisis + risk detection); a static, content-blind
# middleware classifier cannot safely make that judgment BEFORE pipeline()
# runs its own crisis checks, so blocking free text here would risk silently
# swallowing a genuine crisis disclosure. A command name or callback data
# string, by contrast, is never itself a crisis disclosure, so every other
# entrypoint IS fully covered here, uniformly, with no such exception needed.

_ONBOARDING_EXEMPT_COMMANDS = {
    "start",               # the onboarding entry/resume mechanism itself
    "forget_all",          # privacy self-service
    "privacy_export_all",  # privacy self-service
    "privacy_delete_all",  # privacy self-service
    "help",                # deterministic help information
    "unblock",             # owner-only access-control admin action
    "review_pack",         # reviewer-facing crisis review pack (crisis-adjacent)
}

_ONBOARDING_EXEMPT_CALLBACK_PREFIXES = (
    "crisis:",          # active-crisis callbacks
    "onb:",             # onboarding's own namespace
    "tester_ack:",      # access/tester acknowledgment
    "forget:",          # /forget_all confirm step
    "privacy_delete:",  # /privacy_delete_all confirm step
    "privacy:hub",      # deterministic privacy information menu entry
)


def _command_name(text: str | None) -> str | None:
    """"/start payload" -> "start"; "/start@BotName" -> "start"; None for any
    non-command text (including None, e.g. a voice message has no .text)."""
    if not text or not text.startswith("/"):
        return None
    first = text.split(maxsplit=1)[0][1:]
    return first.split("@")[0].lower() or None


def _message_is_onboarding_exempt(message) -> bool:
    """Commands are judged here (default-deny against
    _ONBOARDING_EXEMPT_COMMANDS). Non-command messages (plain text, voice --
    .text is None for a voice message) are always "exempt" from THIS
    middleware -- they are gated inside pipeline() instead, see module note
    above."""
    cmd = _command_name(getattr(message, "text", None))
    if cmd is None:
        return True
    return cmd in _ONBOARDING_EXEMPT_COMMANDS


def _callback_is_onboarding_exempt(callback) -> bool:
    data = getattr(callback, "data", None) or ""
    if not data:
        return False
    return any(data == p or data.startswith(p) for p in _ONBOARDING_EXEMPT_CALLBACK_PREFIXES)


class OnboardingGateMiddleware(BaseMiddleware):
    """Blocks ordinary product commands/callbacks while the caller has an
    ACTIVE first-user onboarding, re-rendering their current onboarding card
    instead of running the real handler. `is_exempt(event)` classifies the
    event using the static tables above. `kind` ("message" or "callback")
    picks how to answer/locate the chat to render into -- deliberately NOT an
    `isinstance(event, CallbackQuery)` check, which would only work against
    real aiogram objects and silently do nothing for the duck-typed Fake
    doubles this whole test suite uses (and unit-testing this middleware
    without real Telegram objects is the entire point)."""

    def __init__(self, is_exempt, kind):
        self._is_exempt = is_exempt
        self._kind = kind

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        uid = user.id if user is not None else None
        if uid is None or self._is_exempt(event):
            return await handler(event, data)
        if not await _onboarding_blocks_ordinary_entry(uid):
            return await handler(event, data)
        # Blocked: neutralize the event and resume the onboarding card.
        if self._kind == "callback":
            answer = getattr(event, "answer", None)
            if answer is not None:
                await answer()
            message = getattr(event, "message", None)
            chat = getattr(message, "chat", None) if message is not None else None
        else:
            chat = getattr(event, "chat", None)
        if chat is not None:
            await _resume_onboarding_card(chat.id, uid)
        return None


dp.message.outer_middleware(
    OnboardingGateMiddleware(_message_is_onboarding_exempt, kind="message"))
dp.callback_query.outer_middleware(
    OnboardingGateMiddleware(_callback_is_onboarding_exempt, kind="callback"))


# ── Duplicate-update guard + privacy-safe dispatch trace ───────────────────
# Investigation of a "one message -> a second, stale-context answer ~1 min
# later" report could not be closed because (a) the bot logged NOTHING per
# update, so the two sends could not be correlated after the fact, and (b)
# start_polling ran with neither drop_pending_updates NOR any update_id
# deduplication -- so a redelivered/replayed update (restart backlog, a
# reconnect) reprocesses as a fresh answer that leans on now-stale memory,
# exactly the observed shape. The exact trigger of that specific event was
# NOT proven; this closes the diagnosability gap and the one real latent
# duplicate vector, without changing any normal behavior.
#
# Telegram update_ids are unique and monotonic under normal operation, so
# this guard NEVER drops a legitimate update -- it fires only on an exact
# duplicate id. Bounded FIFO; a restart simply starts with an empty window.
# LIMITATION: this is IN-MEMORY -- it does NOT survive a process restart and
# is not durable idempotency; it only defends a single continuous run against
# a redelivered/replayed identical update. The trace logs the update_id (a
# Telegram-internal sequence number) and the decision only -- never message
# text, user id, username or any content.
_SEEN_UPDATE_IDS_MAX = 4096
_seen_update_ids: "OrderedDict[int, None]" = OrderedDict()


def _dispatch_log(msg: str) -> None:
    """Privacy-safe dispatch trace. Exception-proof by contract: observability
    must never be able to break message delivery (§9)."""
    try:
        print(f"[dispatch] {msg}")
    except Exception:
        pass


class DuplicateUpdateGuard(BaseMiddleware):
    async def __call__(self, handler, event, data):
        update_id = getattr(event, "update_id", None)
        if update_id is not None:
            # check-and-add is a read-modify-write with no await between, so it
            # is atomic under asyncio's single thread (concurrent duplicate ->
            # exactly one passes).
            if update_id in _seen_update_ids:
                _dispatch_log(f"update_id={update_id} decision=duplicate_dropped")
                return None
            _seen_update_ids[update_id] = None
            if len(_seen_update_ids) > _SEEN_UPDATE_IDS_MAX:
                _seen_update_ids.popitem(last=False)
            _dispatch_log(f"update_id={update_id} decision=accepted")
        return await handler(event, data)


dp.update.outer_middleware(DuplicateUpdateGuard())


class ActivityTouchMiddleware(BaseMiddleware):
    """Push V1 §5: refreshes users.last_seen for ANY real inbound Message or
    CallbackQuery, independent of which handler eventually processes it --
    the Silence Engine's inactivity signal must reflect real product use
    (persistent lower-menu taps, inline navigation, journal/questionnaire
    UI), not only ordinary free-text turns through pipeline()/upsert_user.

    database.touch_last_seen is UPDATE-only (never INSERTs), so an unknown/
    never-messaged user never gets a `users` row created merely by this
    middleware running, and it never mutates access, role, onboarding,
    crisis state, message_count, or privacy/deletion state -- exactly one
    column, on an already-existing row. Best-effort: a failure here must
    never block real handler processing. One centralized hook instead of
    duplicating a touch call across dozens of handlers."""
    async def __call__(self, handler, event, data):
        uid = None
        if event.message is not None and event.message.from_user is not None:
            uid = event.message.from_user.id
        elif event.callback_query is not None and event.callback_query.from_user is not None:
            uid = event.callback_query.from_user.id
        if uid is not None:
            try:
                await touch_last_seen(uid)
            except Exception:
                pass
        return await handler(event, data)


dp.update.outer_middleware(ActivityTouchMiddleware())


# ── Stale-response guard: suppress an ordinary answer superseded mid-flight ─
# Reproduced race (see tests): aiogram runs updates as concurrent tasks
# (handle_as_tasks=True), so two turns from the SAME user can run pipeline()
# at once. If an earlier turn's LLM call is slow and a later turn finishes
# first, the earlier turn then delivers its now-stale answer AFTER the newer
# one -- "two answers, the second referencing older context". This is the
# actual mechanism behind the owner report; the DB timestamps are pipeline-
# COMPLETION times, not arrival times, so they never proved two user sends.
#
# Fix: a per-user monotonic "generation". Every new user turn (ordinary
# pipeline entry, crisis pipeline entry, and /start) bumps it. An ordinary
# answer captures the generation at entry and, immediately before delivery,
# skips if a newer turn has since bumped it. Deterministic safety responses
# (crisis / dependency / disambiguation) return earlier and are never guarded
# -- they are always delivered. The bump is a read-modify-write with no await
# between, so it is atomic under asyncio's single thread; no lock needed.
# Bounded FIFO; an evicted user fails OPEN (delivers), the pre-existing
# behavior. In-memory only -- it does not, and need not, survive a restart.
_USER_GEN_MAX = 4096
_user_generation: "OrderedDict[int, int]" = OrderedDict()


def _bump_user_generation(uid: int) -> int:
    g = _user_generation.get(uid, 0) + 1
    _user_generation[uid] = g
    if len(_user_generation) > _USER_GEN_MAX:
        _user_generation.popitem(last=False)
    return g


def _user_generation_superseded(uid: int, captured: int) -> bool:
    """True iff a newer turn for uid started after `captured` was taken."""
    return _user_generation.get(uid, captured) > captured


def _new_correlation_id() -> str:
    """Random per-turn id for privacy-safe log correlation. NOT derived from
    any user identifier -- it exists only to stitch one turn's log lines
    together, never to identify a person."""
    return secrets.token_hex(4)


# ── Per-user ingestion lock: preserve arrival order of user-row persistence ──
# Generation suppression already stops a stale ANSWER from being delivered or
# persisted. This closes the remaining pre-persistence race: two turns from the
# same user run pipeline() as concurrent aiogram tasks, and the older one can
# pause on a fast pre-save DB await while the newer one saves its user row
# first -- and memory loads by autoincrement id (get_recent_messages ORDER BY
# id DESC), so the older row would then look like the newest active context.
#
# Fix: each turn acquires this user's lock at entry (the only code before the
# acquire is pure/sync -- language + risk detection -- so acquire order equals
# entry order, and asyncio.Lock grants FIFO). It is held ONLY through the
# pre-persistence work and the user-row save, then released BEFORE the two
# multi-second awaits (summarization, answer) and before reaction sending /
# TTS / delivery. So it orders persistence without serializing conversations.
#
# Registry: per-user, bounded LRU. A holder is evicted ONLY when refs == 0
# (no acquirer and no waiter) -- never one that is held or awaited. In-memory;
# a restart terminates all in-flight coroutines, so no cross-process claim is
# needed. No user id is ever logged.
class _IngestHolder:
    __slots__ = ("lock", "refs", "seq")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.refs = 0    # acquirers + current waiters referencing this holder
        self.seq = 0     # last-used monotonic sequence (LRU tiebreak)


_INGEST_MAX = 4096
_ingest_registry: "OrderedDict[int, _IngestHolder]" = OrderedDict()
_ingest_seq = 0


def _ingest_get_or_create(uid: int) -> _IngestHolder:
    """Sync (no await): bump refs and LRU, evicting only unheld/unwaited
    holders. Atomic under asyncio's single thread."""
    global _ingest_seq
    holder = _ingest_registry.get(uid)
    if holder is None:
        holder = _IngestHolder()
        _ingest_registry[uid] = holder
    _ingest_seq += 1
    holder.seq = _ingest_seq
    holder.refs += 1
    _ingest_registry.move_to_end(uid)
    if len(_ingest_registry) > _INGEST_MAX:
        for k in list(_ingest_registry):
            if k != uid and _ingest_registry[k].refs == 0:
                del _ingest_registry[k]
                if len(_ingest_registry) <= _INGEST_MAX:
                    break
    return holder


async def _ingest_enter(uid: int) -> _IngestHolder:
    holder = _ingest_get_or_create(uid)
    try:
        await holder.lock.acquire()
    except BaseException:
        _ingest_leave(uid, holder, acquired=False)
        raise
    return holder


def _ingest_leave(uid: int, holder: _IngestHolder, acquired: bool = True) -> None:
    if acquired:
        holder.lock.release()
    holder.refs -= 1
    if holder.refs <= 0 and _ingest_registry.get(uid) is holder:
        del _ingest_registry[uid]


@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext = None):
    from datetime import datetime, timezone
    uid = message.from_user.id
    # /start is a new turn: bump the generation so any ordinary answer still
    # being generated for this user is superseded and not delivered afterward.
    _bump_user_generation(uid)
    # Round 4: /start is an explicit navigation escape -- abandon a stale
    # active EmotionJournal/CbtJournal FSM only (never onboarding history,
    # never a data reset); everything below this line is unrelated existing
    # /start behavior, untouched. `state` defaults to None (not required)
    # purely for backward compatibility with the many pre-existing tests
    # that call cmd_start(msg) without it; the real dispatcher always
    # injects a genuine FSMContext.
    if state is not None:
        await _clear_active_journal_if_leaving(state)
    # Phase 2 correction §8: /start supersedes any pending Depression
    # Disclosure Gate flow -- old buttons become inert, the new /start menu
    # is never blocked by it. Best-effort (never blocks /start itself).
    try:
        active_dd_flow = await get_active_disclosure_flow(uid)
        if active_dd_flow is not None:
            await close_disclosure_flow(active_dd_flow["id"], uid,
                                        from_step=active_dd_flow["step"],
                                        status="cancelled", superseded_reason="start_command")
    except Exception as e:
        print(f"[depression-disclosure] /start supersession failed uid={uid}: {e}")
    # Phase 3 §17: /start PAUSES (not cancels) any active Conversation
    # Controller session -- unlike a Phase 2 safety flow, ordinary
    # conversational work is resumable, so lifecycle_status becomes PAUSED,
    # not a terminal status. Best-effort (never blocks /start itself).
    try:
        active_sessions = await list_core_sessions(uid, active_only=True)
        for s in active_sessions:
            if s.lifecycle_status is LifecycleStatus.OPEN:
                s.lifecycle_status = LifecycleStatus.PAUSED
                await update_core_session(s)
        # Hardening §4: /start also invalidates any standing PRACTICE
        # proposal -- old consent must not survive a /start reset.
        await supersede_active_practice_proposals(uid, "start_command")
    except Exception as e:
        print(f"[controller] /start session pause failed uid={uid}: {e}")
    # PR C3a.1 -- parse a /start deep-link payload BEFORE the access gate.
    # This is the critical ordering: a temp-invite-code holder has no prior
    # access, so if we ran ensure_full_access_or_closed_test first they'd be
    # blocked before ever reaching the code that grants them access, making
    # the whole feature a dead branch. This codebase has no existing deep-link
    # parsing helper (verified: no `deep_link`/`start_param` hits anywhere),
    # so we do plain string parsing on message.text ourselves.
    # Language resolution (spec item B correction), done ONCE, up front,
    # before the invite-grant messages below (which fire before upsert_user
    # ever runs, so a "read it back after upsert" call would see either the
    # pre-upsert "ru" default for a brand-new user, or -- worse -- clobber an
    # existing explicit preference). Policy: PRESERVE a valid existing stored
    # preference; only resolve fresh from Telegram's language_code for a
    # brand-new user (no `users` row yet) or an invalid/malformed stored
    # value (deterministic repair). This is what makes both the invite/grant
    # messages AND upsert_user's write use the SAME, correctly-resolved value.
    stored_lang = await get_stored_user_language(uid)
    if stored_lang in ("ru", "en"):
        lang = stored_lang
    else:
        lang = normalize_telegram_language_code(
            getattr(message.from_user, "language_code", None))

    parts = (message.text or "").split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""
    if payload:
        try:
            cfg = access_control.temp_test_invite_config()
        except Exception:
            cfg = {"valid": False, "code": None}
        # Non-disclosure: never reveal whether the payload was close/correct/
        # expired -- a wrong or inactive payload just falls through silently
        # to the existing closed-test behavior below, exactly as before.
        if cfg.get("valid") and cfg.get("code") is not None and payload == cfg["code"] \
                and access_control.is_temp_test_invite_active():
            access_control.grant_temp_test_access(uid)
            end_str = cfg["end"].strftime("%Y-%m-%d %H:%M UTC")
            grant_msg = (f"✅ Временный тестовый доступ выдан до {end_str}."
                         if lang == "ru" else
                         f"✅ Temporary test access granted until {end_str}.")
            await message.answer(grant_msg)
        # PR A — ordinary-user private invite access. A separate, permanent
        # production mechanism (not test-instance-scoped, not time-boxed) --
        # independent of the temp-invite branch above. In a real deployment
        # the two codes differ, so at most one branch ever matches; both are
        # tried without either taking precedence over the other. Uses
        # hmac.compare_digest (not ==) since this is reachable by any
        # stranger holding the link, not just a controlled test cohort.
        # Non-disclosure: a wrong/disabled code falls through silently to the
        # existing closed-test behavior below -- never reveals close/correct.
        elif access_control.user_invite_active() and hmac.compare_digest(
                payload.encode("utf-8"), config.USER_INVITE_CODE.encode("utf-8")):
            await grant_user_access(uid, source="invite")
            grant_msg = "✅ Доступ открыт." if lang == "ru" else "✅ Access granted."
            await message.answer(grant_msg)
    if not await ensure_full_access_or_closed_test(message, uid):
        return
    overview = await get_memory_overview(uid)          # before upsert: 0 msgs == first time
    is_first = overview["message_count"] == 0
    # Onboarding eligibility (spec item C) MUST be inspected BEFORE upsert_user
    # creates/touches the `users` row below -- otherwise "does a users row
    # already exist" is meaningless (upsert would have just created it).
    # Computed unconditionally (cheap indexed lookups) so flag-off/flag-on
    # ordering can never silently diverge depending on which branch runs first.
    eligibility = await get_onboarding_eligibility(uid)
    await upsert_user(uid, message.from_user.username or "", message.from_user.first_name or "",
                      lang)
    # First-user illustrated onboarding (flag-gated). This whole block is entered
    # ONLY when config.FIRST_USER_ONBOARDING_ENABLED is on -> with the flag off,
    # /start behaves byte-for-byte as before. It runs AFTER the access gate and
    # the upsert, so a blocked/unauthorized user never reaches it, and it can
    # never itself grant access (access is granted only in the invite branches
    # above). See onboarding.py / onboarding_content.py.
    if config.FIRST_USER_ONBOARDING_ENABLED:
        # Real versioning policy (spec item F): VERSION EQUALITY IS THE GATE.
        # A user is "settled" for onboarding purposes only once they have a
        # row for the CURRENT ONBOARDING_VERSION specifically (completed,
        # legacy_exempt, or superseded all count as settled -- only 'active'
        # keeps them mid-flow). This is deliberately NOT "has this user ever
        # touched onboarding, any version" -- that older policy would make a
        # completed OLD version a PERMANENT exemption from every future
        # MANDATORY version bump (e.g. a new required privacy notice), which
        # is exactly the bug being corrected here.
        active_state = await get_active_onboarding_state(uid)
        current_version_row = (
            None if active_state is not None
            else await get_onboarding_state(uid, ONBOARDING_VERSION))
        # Independent privacy-notice acknowledgement (spec item F correction):
        # backed solely by user_notice_acknowledgements, never by
        # onboarding_version/status/completed_at/legacy_exempt/superseded/
        # active-row bookkeeping (see database.has_privacy_notice_ack /
        # database.record_notice_acknowledgement). This is what lets a future
        # PRIVACY_NOTICE_VERSION bump reach a settled user even if
        # ONBOARDING_VERSION never changes.
        notice_acked = await has_privacy_notice_ack(uid, PRIVACY_NOTICE_VERSION)
        requirement = onboarding_content.determine_onboarding_requirement(
            eligibility=eligibility,
            has_active_state=active_state is not None,
            has_current_version_row=current_version_row is not None,
            notice_acknowledged=notice_acked)

        if requirement == onboarding_content.FULL_ONBOARDING:
            if active_state is not None:
                if active_state["onboarding_version"] == ONBOARDING_VERSION:
                    # Resume in-progress onboarding at the stored step, editing
                    # the persisted card in place when possible (spec item G)
                    # instead of always sending a fresh one.
                    await _render_onboarding_card(
                        uid, message.chat.id, active_state["current_step"], lang,
                        message_id=active_state.get("card_message_id"),
                        first_name=message.from_user.first_name or "")
                    return
                # An ACTIVE row for an OLDER version means a deployment bumped
                # ONBOARDING_VERSION (a mandatory update) while this user's
                # onboarding was in flight. This is NOT "completed" and NOT
                # "legacy_exempt" -- the user did not finish it, and they were
                # not exempt from it either. Supersede the stale row, then
                # ALWAYS start the new version's active flow immediately --
                # no further eligibility re-check (they were already actively
                # engaging with onboarding).
                await supersede_onboarding_version(uid, active_state["onboarding_version"])
            await start_or_get_onboarding(uid, ONBOARDING_VERSION)
            await _render_onboarding_card(
                uid, message.chat.id, FIRST_STEP, lang, message_id=None,
                first_name=message.from_user.first_name or "")
            return

        if requirement == onboarding_content.PRIVACY_NOTICE_ONLY:
            # Renders the privacy-notice-only screen WITHOUT creating or
            # touching any user_onboarding_state row -- there is no
            # onboarding-content settling to do here, only the CURRENT
            # privacy notice is missing. The acknowledgement itself is
            # recorded independently by cb_onboarding's CB_PRIVACY_ONLY_START
            # branch via database.record_notice_acknowledgement, never by
            # complete_onboarding (there is no row to complete).
            await _render_privacy_notice_only_card(
                uid, message.chat.id, lang, message_id=None)
            return

        # NOT_REQUIRED: settle bookkeeping if this exact version row doesn't
        # exist yet (a legacy user who already independently acknowledged the
        # current privacy notice but never got a row for THIS
        # onboarding_version) -- purely a bookkeeping completion, never shown.
        if current_version_row is None:
            await mark_onboarding_legacy_exempt(uid, ONBOARDING_VERSION)
        # settled for the current onboarding_version AND the current privacy
        # notice is acknowledged -> fall through to the ordinary greeting.

    text, _ = get_onboarding(lang)
    # §7.1 returning users get a time-varied greeting — in their LOCAL time, not
    # UTC (otherwise a daytime user gets a "поздно, не спится?" night line).
    if not is_first:
        tz_off, tz_set, ulang = await get_user_tz(uid)
        local_hour = (datetime.now(timezone.utc).hour + effective_tz(tz_off, tz_set, ulang)) % 24
        text = pick_greeting(False, local_hour, lang)
    await _send_mood_entry(message, uid, lang, text)
    await _send_persistent_lower_menu(message, lang)


def _mood_entry_keyboard(lang: str, buttons: list) -> InlineKeyboardMarkup:
    # Inline-кнопки вместо reply-клавиатуры: iOS прячет reply-клавиатуру за
    # иконкой у поля ввода, и пользователи её не видят. Inline видна везде.
    # Onboarding asks "как ты себя чувствуешь" -- Emotion Map helper row added
    # (deterministic vocabulary aid, not a new gate/flow; opening it never
    # stores anything, see cb_emotion_map).
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=b, callback_data=f"mood:{i}")]
        for i, b in enumerate(buttons)
    ] + [[InlineKeyboardButton(
        text=("🗺 Карта эмоций" if lang == "ru" else "🗺 Emotion map"), callback_data="emotion:map")]])


async def _send_mood_entry(target, uid: int, lang: str, text: str) -> None:
    """Render the conversation-entry surface: Professional Entry Triage V1
    for RU users under access_control.core_rollout_allowed (replaces, not
    duplicates, the legacy mood buttons for those users -- see
    _send_professional_entry_triage); the existing legacy mood-selection
    entry (mood buttons + emotion-map row + the '⚠️ Я не терапевт.' line)
    otherwise. Shared verbatim by cmd_start and the first-user onboarding
    Start button, so whichever surface renders stays byte-identical
    regardless of which caller opened it."""
    if lang == "ru" and await access_control.core_rollout_allowed(uid):
        await _send_professional_entry_triage(target, uid)
        return
    _, buttons = get_onboarding(lang)
    kb = _mood_entry_keyboard(lang, buttons)
    await target.answer(
        text + "\n\n⚠️ " + ("Я не терапевт." if lang == "ru" else "I'm not a therapist."),
        reply_markup=kb)


@dp.callback_query(F.data.startswith("mood:"))
async def cb_mood(callback: CallbackQuery, state: FSMContext):
    """Кнопка состояния из онбординга → обычный проход по pipeline.

    An old/leftover mood button cannot reach this handler at all while
    onboarding is active -- OnboardingGateMiddleware (spec item C) intercepts
    every callback_query BEFORE handler dispatch and re-renders the onboarding
    card instead. No inline gate check needed here (a redundant one would be
    exactly the "scattered check" the middleware exists to avoid)."""
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    # Professional Entry Triage migration bridge: a HISTORICAL legacy mood:*
    # tap (rendered before this rollout, or before the user became eligible)
    # from a user who is NOW RU + core_rollout_allowed-eligible must not
    # fall through to pipeline() below with a fabricated free-text choice --
    # that would create a real role='user' message from a button tap, which
    # is exactly what Entry Triage must never do. A non-eligible or non-RU
    # user's tap is completely unaffected by this branch and reaches the
    # unchanged legacy pipeline() call below exactly as before.
    #
    # V4 correction: once RU + core_rollout_allowed eligibility is
    # established, this function ALWAYS returns from within this block --
    # it never falls through to the legacy synthetic-mood pipeline() call
    # below under any outcome. A fresh access_control.has_full_access(uid)
    # check decides only WHICH terminal outcome: has_full_access=True
    # redirects to the real Entry Triage surface; has_full_access=False, or
    # an exception raised while checking it, is a neutral no-op (callback
    # answered, nothing rendered, nothing persisted) -- NOT a signal to use
    # the legacy mood pipeline. A revoked/unknown access state for a
    # Professional-rollout-eligible user must never be reinterpreted as
    # permission to route a stale button label through pipeline() as if it
    # were the user's own free text.
    if lang == "ru" and await access_control.core_rollout_allowed(uid):
        try:
            currently_has_access = await access_control.has_full_access(uid)
        except Exception:
            await callback.answer()
            return
        await callback.answer()
        if not currently_has_access:
            return
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await _send_professional_entry_triage(callback.message, uid)
        return
    _, buttons = get_onboarding(lang)
    try:
        choice = buttons[int(callback.data.split(":")[1])]
    except (ValueError, IndexError):
        await callback.answer()
        return
    await callback.answer()
    # Убираем кнопки, чтобы не нажали повторно; текст приветствия оставляем.
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await pipeline(callback.message, choice, state, tg_user=callback.from_user)


@dp.callback_query(F.data.startswith("pucbtn:"))
async def cb_professional_entry_triage(callback: CallbackQuery):
    """Professional Entry Triage tap -- a trusted UI selection, never user
    free text. Isolated from the legacy ucbtn/mood systems: never calls
    consume_interaction_binding, normalized_action_text, or pipeline();
    never reaches the Analyzer/Planner/Proposer/Renderer/Acceptance or any
    model call. This tap itself NEVER writes a fabricated role='user'
    message -- the category selection is never persisted as if the user
    had typed it. It DOES intentionally persist the exact sealed
    role='assistant' response as a real `messages` row, but ONLY after
    that response has actually been delivered to Telegram successfully --
    see the delivery-then-persist block below. `user_interaction_events`
    is never written by this tap under any outcome.

    Every check below is independent defense-in-depth, mirroring
    cb_cc_consent -- a token could only ever have been created under the
    same RU + core_rollout_allowed gate, but that is rechecked live here
    rather than trusted from the token's mere existence. Crisis is checked
    BEFORE any binding consumption -- on an active crisis, the category is
    never consumed, never canonicalized, and no Professional response is
    sent; this user's still-open Professional Entry Triage offers are
    best-effort superseded instead (never consumption -- see
    database.supersede_professional_entry_triage_bindings), which is what
    stops a pre-crisis offer from becoming actionable again once the
    crisis resolves. A failure of that cleanup is swallowed and never
    surfaced -- crisis safety does not depend on Entry Triage bookkeeping.

    V3 correction: a fresh access_control.has_full_access(uid) recheck
    runs AFTER the crisis check but BEFORE any binding consumption -- a
    token minted while access was valid must not remain a capability that
    survives a later revocation of ordinary product access. A lookup
    exception fails closed (no consumption, no response) exactly like a
    False result."""
    uid = callback.from_user.id
    token = callback.data[len("pucbtn:"):]
    await callback.answer()
    lang = await get_user_language(uid)
    if not (lang == "ru" and await access_control.core_rollout_allowed(uid)):
        return
    if await get_active_crisis(uid) is not None:
        try:
            await supersede_professional_entry_triage_bindings(uid)
        except Exception:
            pass
        return
    try:
        currently_has_access = await access_control.has_full_access(uid)
    except Exception:
        currently_has_access = False
    if not currently_has_access:
        return
    result = await consume_professional_entry_triage_binding(
        token, uid, callback.message.chat.id, callback.message.message_id)
    if result is None:
        return
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    canon = canonicalize_entry_triage_selection(
        UntrustedEntryTriageSelection(category=result.category))
    if canon.status is not EntryTriageSelectionStatus.ACCEPTED:
        return
    response = build_trusted_ui_immediate_response(canon.directive)
    valid, _reason = validate_response(response.text_ru, "ru")
    if not valid:
        return
    # Delivery BEFORE persistence: a Telegram send failure must leave zero
    # assistant rows -- never save first and pretend delivery succeeded.
    # The callback has already been answered above, so a failure here
    # produces no second callback-popup response either -- only a fixed,
    # redacted diagnostic (exception class only; never token, category,
    # response text, or callback_data).
    try:
        await callback.message.answer(response.text_ru)
    except Exception as e:
        print(f"[professional-entry-triage] delivery FAILED uid={uid}: {type(e).__name__}")
        return
    # Delivery succeeded. Persist the exact sealed assistant response for
    # conversation continuity (build_context can see what was asked), so a
    # genuine free-text reply the user types next has the right context.
    # A save failure here must not trigger a duplicate Telegram send, must
    # not roll the already-consumed binding back into an actionable state,
    # and must not fabricate any user text -- delivery already happened
    # and that is the honest, final state either way.
    try:
        await save_message(uid, "assistant", response.text_ru, "open_chat", "ru",
                           source=MessageSource.ASSISTANT_DELIVERED)
    except Exception as e:
        print(f"[professional-entry-triage] assistant persistence FAILED uid={uid}: "
              f"{type(e).__name__}")
        return


# ── Push V1 -- Continue / New-topic (§11, §12) ───────────────────────────────
# Contextual Continue V1: the anchor-existence gate below is UNCHANGED from
# the original deterministic-only design (turn_belongs_to_user re-verified
# at callback time, exactly like before). What changed is what happens
# once a real anchor is confirmed: instead of always returning the fixed
# PUSH_V1_CONTINUE_REPLY_* string, this now ATTEMPTS one real, anchor-
# fenced contextual generation first, falling back to that exact same
# fixed string on ANY failure (empty trusted context, provider failure,
# validator rejection) -- see _try_push_contextual_continue. The no-anchor
# path is completely untouched: no model call, no context fetch, same
# PUSH_V1_NO_ANCHOR_REPLY_* fallback as before.
async def _try_push_contextual_continue(uid: int, lang: str, anchor_turn_id: int) -> str | None:
    """Attempt one real Contextual Continue V1 generation, fenced to the
    EXACT push-send-time anchor_turn_id via database.get_trusted_
    conversation_history_through_anchor -- a DEDICATED primitive, not
    get_professional_conversation_history_rows reused: that function's own
    source-only filter would let a prior Push V1 UI reply (genuinely
    persisted as source=ASSISTANT_DELIVERED) leak into this context as if
    it were real discourse, so a separate primitive that ALSO excludes
    scenario=PUSH_UI_SCENARIO is required -- see that function's own
    docstring.

    Two mandatory pre-generation legality checks, in addition to the
    empty-context check, before the model is ever called (owner P1
    correction):

    (1) EXACT-ANCHOR-SURVIVES: the bounded context's FINAL turn must be
    the exact anchor_turn_id, as an ASSISTANT turn. turn_belongs_to_user
    (called by the caller before this function runs) only proves the row
    exists/same-uid/role='assistant' -- it does NOT prove source=
    ASSISTANT_DELIVERED, scenario!=push_ui, or that the anchor survived
    bounded-context construction (e.g. individually exceeding
    professional_turn_conversation_context.MAX_TURN_CONTENT_CHARS gets it
    silently omitted by build_conversation_context_from_history_rows).
    get_trusted_conversation_history_through_anchor's own WHERE clause
    already excludes a source-mismatched or push_ui anchor row from the
    query results entirely; the assertion here is what detects that
    exclusion (or an oversized-turn omission) happened, rather than
    silently continuing with an OLDER conversation and treating it as if
    it were the exact anchored one. context.turns is ordered by strictly
    increasing message_row_id with the anchor being the query's own upper
    bound, so the anchor -- if present at all -- can only ever be the
    last entry; no separate DB round-trip is needed to prove this.

    (2) TRUSTED-USER-EVIDENCE-REQUIRED: at least one ConversationTurnRole.
    USER turn must survive in the bounded context. Prior ASSISTANT content
    is discourse history only (see push_contextual_continue.py's own
    module docstring) -- it is NEVER sufficient by itself to establish
    what the conversation is actually about, so an assistant-only context
    must degrade exactly like an empty one.

    Returns the validator-accepted candidate text, or None on ANY failure
    (empty trusted context, exact anchor not surviving, no trusted USER
    turn, a provider/network failure, or Safety Validator rejection).
    Never raises to its caller and never itself returns or constructs a
    fallback string -- _push_continue_reply_text alone owns the fallback
    decision on None."""
    try:
        rows = await get_trusted_conversation_history_through_anchor(uid, anchor_turn_id)
        context = build_conversation_context_from_history_rows(rows)
    except Exception as e:
        print(f"[push-v1] contextual continue context-build FAILED uid={uid}: {type(e).__name__}")
        return None
    if context.is_empty:
        return None
    final_turn = context.turns[-1]
    if (final_turn.message_row_id != anchor_turn_id
            or final_turn.role is not ConversationTurnRole.ASSISTANT):
        # The exact anchor did not survive as the final trusted turn --
        # never fall back to an older conversation and pretend it is the
        # exact anchored one.
        return None
    if not any(turn.role is ConversationTurnRole.USER for turn in context.turns):
        # Assistant discourse alone never establishes what the
        # conversation is about.
        return None
    try:
        candidate = await push_contextual_continue.generate_push_contextual_continue(
            client=client, model="gpt-4o-mini", conversation_context=context,
            lang=lang, max_tokens=300,
        )
    except Exception as e:
        print(f"[push-v1] contextual continue generation FAILED uid={uid}: {type(e).__name__}")
        return None
    is_safe, reason = validate_response_without_current_user(candidate, lang)
    if not is_safe:
        print(f"[push-v1] contextual continue REJECTED uid={uid}: "
              f"{classify_rejection_reason(reason)}")
        return None
    return candidate


async def _push_continue_reply_text(uid: int, lang: str, anchor_turn_id: int | None) -> tuple[str, str]:
    """If the anchored assistant turn from push-send time still exists and
    still belongs to this user, ATTEMPT a real contextual continuation
    first (see _try_push_contextual_continue); on any failure there, or if
    there is no anchor at all, degrade to the existing deterministic
    reply -- never an invented continuation, and the model is NEVER
    consulted at all when there is no anchor. Returns (text, scenario):
    scenario is push_contextual_continue.SCENARIO (a genuine conversational
    turn) only for a successful contextual generation; every fallback path
    keeps the original PUSH_UI_SCENARIO tagging unchanged, since those
    really are sealed, non-conversational UI acknowledgements."""
    has_anchor = False
    if anchor_turn_id is not None:
        try:
            has_anchor = await turn_belongs_to_user(anchor_turn_id, uid)
        except Exception:
            has_anchor = False
    if not has_anchor:
        return (PUSH_V1_NO_ANCHOR_REPLY_EN if lang == "en" else PUSH_V1_NO_ANCHOR_REPLY_RU,
                PUSH_UI_SCENARIO)
    contextual = await _try_push_contextual_continue(uid, lang, anchor_turn_id)
    if contextual is not None:
        return contextual, push_contextual_continue.SCENARIO
    return (PUSH_V1_CONTINUE_REPLY_EN if lang == "en" else PUSH_V1_CONTINUE_REPLY_RU,
            PUSH_UI_SCENARIO)


@dp.callback_query(F.data.startswith("pushbtn:"))
async def cb_push_action(callback: CallbackQuery, state: FSMContext = None):
    """Push V1 Continue/New-topic tap -- a trusted UI selection, never user
    free text. Gate order: unresolved-crisis check FIRST (for the user's
    ENTIRE unresolved lifecycle, not just get_active_crisis()'s 24h
    interactive window -- see get_unresolved_crisis; best-effort supersede
    any open push offers, then RE-SHOW the existing crisis safety surface
    for that exact event via the normal delivery ladder, never creating a
    second crisis_events row), THEN a live product-access recheck (silent
    -- no closed-test screen; this is a stale-offer tap, not a fresh
    command entrypoint), THEN a mandatory-onboarding recheck, THEN atomic
    single-use binding consumption (which itself re-verifies the live
    user_interaction_revision, rejecting a stale tap from before a newer
    ordinary user turn, and advances the revision by 1 on success). This
    tap never writes a fabricated role='user' message and never touches
    interaction_button_bindings/user_interaction_events -- entirely
    isolated from the first-turn continuation graph. An active unfinished
    journal FSM (if any) is abandoned only AFTER every gate above has
    passed, using the same narrow helper every other navigation entrypoint
    uses.

    Owner P0 correction (freshness-fenced delivery, generalized to BOTH
    push actions): action="push_continue" may await a real provider/
    network call (_push_continue_reply_text); push_new_topic awaits
    nothing extra, but shares the exact same final lifecycle fence below
    on the reasoning that ANY awaited work in this handler -- including the
    crisis/access/onboarding gate lookups and binding consumption
    themselves -- opens the same kind of window. Everything from binding
    consumption onward funnels into ONE shared final check before
    delivery: database.final_push_action_reply_delivery_guard, which binds
    to the EXACT opaque token this tap consumed (not merely a numeric
    revision -- see that function's own docstring for the delete-all/
    bump_user_revision ABA gap this closes), the exact post_consume_
    revision, live unresolved-crisis state, and (only when the reply
    genuinely depends on it) the exact anchor. Immediately before the
    Telegram send -- and again immediately after it returns, before
    persistence begins -- a SEPARATE, purely in-memory, non-awaited check
    (_user_generation_superseded) catches a newer ordinary turn (e.g.
    /start, which calls _bump_user_generation) that the DB-only fence
    cannot see because an ordinary turn does not, by itself, move
    user_interaction_revision. Post-send persistence is likewise fenced
    (database.record_push_action_reply_delivery) to close the residual
    window where an invalidating event commits WHILE the Telegram send
    itself is in flight -- SQLite and Telegram cannot be made globally
    atomic. New Topic's user-visible product behavior is unchanged: no
    model call, no conversation context, the exact existing fixed reply,
    PUSH_UI_SCENARIO -- only its pre-send freshness and post-send
    persistence safety are now shared with Continue."""
    uid = callback.from_user.id
    token = callback.data[len("pushbtn:"):]
    # Captured BEFORE any awaited work in this handler (owner P1-3
    # correction), so a newer ordinary turn that starts executing during
    # ANY later await here -- the crisis/access/onboarding lookups, binding
    # consumption, or Contextual Continue's provider call -- can be
    # detected via the EXISTING _user_generation mechanism the ordinary
    # pipeline already relies on. A Push callback never bumps the
    # generation itself: this is about detecting a newer turn, not
    # manufacturing another generation event.
    captured_generation = _user_generation.get(uid, 0)
    await callback.answer()
    lang = await get_user_language(uid)

    lookup_failed = False
    unresolved = None
    try:
        unresolved = await get_unresolved_crisis(uid)
    except Exception:
        lookup_failed = True
    if lookup_failed or unresolved is not None:
        # Fail closed: an unresolved-crisis lookup FAILURE blocks normal
        # continuation exactly like a genuine unresolved crisis would, even
        # though (having no data) it cannot re-show a screen.
        try:
            await supersede_push_action_bindings(uid)
        except Exception:
            pass
        if unresolved is not None:
            eid, stage, clang = unresolved
            try:
                text, kb = crisis_screen(stage, clang, eid)
                await send_crisis(callback.message.answer, text, kb, clang, uid, eid, "screen")
            except Exception as e:
                print(f"[push-v1] crisis re-show FAILED uid={uid}: {type(e).__name__}")
        return

    try:
        has_access = await access_control.has_full_access(uid)
    except Exception:
        has_access = False
    if not has_access:
        return

    if await _onboarding_blocks_ordinary_entry(uid):
        return

    chat_id = callback.message.chat.id
    source_message_id = callback.message.message_id
    result = await consume_push_action_binding(token, uid, chat_id, source_message_id)
    if result is None:
        return

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await _clear_active_journal_if_leaving(state)

    if result.action == "push_continue":
        # May await a real provider/network call -- see
        # _try_push_contextual_continue. Every check below re-verifies the
        # user's lifecycle live, because it may have changed during that
        # await exactly like during any other awaited prerequisite.
        text, scenario = await _push_continue_reply_text(uid, lang, result.anchor_turn_id)
    else:
        # New Topic: no context, no model call, always the fixed reply,
        # always PUSH_UI_SCENARIO -- product behavior unchanged.
        text = PUSH_V1_NEW_TOPIC_REPLY_EN if lang == "en" else PUSH_V1_NEW_TOPIC_REPLY_RU
        scenario = PUSH_UI_SCENARIO

    try:
        still_has_access = await access_control.has_full_access(uid)
    except Exception:
        still_has_access = False
    if not still_has_access:
        return
    if await _onboarding_blocks_ordinary_entry(uid):
        return

    # The anchor is only "required" for THIS response when it is a real,
    # anchor-grounded contextual generation -- every deterministic fallback
    # string (no-anchor / empty-context / assistant-only / oversized-anchor
    # / provider-failure / validator-rejection) and every New Topic reply
    # does not depend on the anchor's content at all, so its own
    # disappearance must not block an otherwise-current deterministic
    # reply. (result.action != "push_continue" alone already makes
    # scenario != push_contextual_continue.SCENARIO, so this one
    # expression is correct for both actions without a separate branch.)
    guard_anchor = result.anchor_turn_id if scenario == push_contextual_continue.SCENARIO else None
    try:
        guard_ok = await final_push_action_reply_delivery_guard(
            uid, chat_id, source_message_id, token, result.action,
            result.post_consume_revision, guard_anchor)
    except Exception as e:
        print(f"[push-v1] final guard FAILED uid={uid}: {type(e).__name__}")
        guard_ok = False
    if not guard_ok:
        # A newer lifecycle event has won: send nothing (no candidate, no
        # generic fallback), persist nothing, never retry the model, never
        # send a second message.
        return

    # Synchronous, non-awaited generation fence -- NO await between this
    # check and the Telegram send call, exactly like the ordinary pipeline
    # already requires of its own pre-delivery check.
    if _user_generation_superseded(uid, captured_generation):
        return

    # Delivery BEFORE persistence -- same discipline as
    # cb_professional_entry_triage: a Telegram send failure must leave zero
    # assistant rows. No other database/network await happens between the
    # guard above and this call.
    try:
        await callback.message.answer(text)
    except Exception as e:
        print(f"[push-v1] delivery FAILED uid={uid}: {type(e).__name__}")
        return

    # Re-check the SAME synchronous fence immediately after the send:
    # Telegram may already have delivered the text once (an unavoidable
    # cross-system residual); if a newer turn started WHILE that send was
    # in flight, persist nothing and never resend.
    if _user_generation_superseded(uid, captured_generation):
        print(f"[push-v1] generation superseded after delivery uid={uid}")
        return

    # scenario is PUSH_UI_SCENARIO for every deterministic UI
    # acknowledgement (New Topic always; Continue whenever it fell back) --
    # this deterministic reply must never itself become a future push's
    # "real conversation anchor" -- see database.get_last_assistant_
    # message_id. A successful Contextual Continue is tagged push_
    # contextual_continue.SCENARIO instead: it is a genuine conversational
    # assistant turn, not a sealed UI reply, and must remain eligible as a
    # future anchor. Persistence is lifecycle-fenced to the SAME
    # (token, expected_revision, guard_anchor) tuple the pre-send guard
    # just verified, closing the residual window where a delete-all (or
    # any other invalidating event) commits WHILE the Telegram send above
    # was itself in flight -- SQLite and Telegram cannot be made globally
    # atomic, so this is the one thing the pre-send guard alone cannot
    # close.
    try:
        persisted = await record_push_action_reply_delivery(
            uid, chat_id, source_message_id, token, result.action,
            text, scenario, lang, result.post_consume_revision, guard_anchor)
    except Exception as e:
        print(f"[push-v1] post-delivery persistence FAILED uid={uid}: {type(e).__name__}")
        return
    if not persisted:
        # Telegram already delivered the text; that cross-system residual
        # is unavoidable. The lifecycle moved during/after the send, so
        # nothing is written -- never resend, never recreate deleted state.
        print(f"[push-v1] post-delivery lifecycle invalidated uid={uid}")


@dp.callback_query(F.data.startswith("onb:"))
async def cb_onboarding(callback: CallbackQuery):
    """First-user illustrated onboarding navigation (Continue / Skip / Start /
    Privacy Policy). One handler for the WHOLE "onb:" namespace regardless of
    version (spec item D) — NOT just the current ONBOARDING_VERSION — so that
    a callback carrying an old (or, after a downgrade, a future) version
    always reaches a handler that answers it and no-ops, rather than being
    left completely unmatched (which would leave Telegram's client-side
    loading spinner hanging on the button with no answer ever sent).

    Every branch: (1) answers the callback; (2) rejects any version other than
    the CURRENT ONBOARDING_VERSION as a safe no-op (old-version callbacks fail
    safely — never try to interpret content from an unknown version's
    namespace); (3) rechecks access — onboarding is a product surface, and it
    can never GRANT access (that only happens in cmd_start's invite branches);
    (4) loads state by callback.from_user.id and NEVER trusts callback data as
    identity/ownership; (5) verifies the expected step; (6) mutates state
    through an atomic, guarded UPDATE so stale taps, double taps and
    concurrent taps are no-ops rather than corruption or backward movement;
    (7) never leaks internal failure detail to the user.
    """
    uid = callback.from_user.id
    data = callback.data or ""
    await callback.answer()
    if not config.FIRST_USER_ONBOARDING_ENABLED:
        return
    if not data.startswith(onboarding_content.CB_PREFIX):
        return  # old/future-version callback -- answered above, safe no-op
    # Access recheck (defense in depth). Fail closed & neutral — no error text.
    try:
        if not await access_control.has_full_access(uid):
            return
    except Exception:
        return
    lang = await get_user_language(uid)

    if data.startswith(onboarding_content.CB_PRIVACY_ONLY_START_PREFIX):
        # Privacy-notice-only acknowledgement (spec item F correction): this
        # screen is NOT backed by any user_onboarding_state row (see
        # bot.cmd_start / determine_onboarding_requirement), so it is answered
        # here, independently of the active-onboarding-state gate below.
        # Identity is callback.from_user.id (never trusted from callback
        # data). notice_id is a fixed literal ("privacy_notice"), never read
        # from the callback payload, so a forged callback cannot name an
        # arbitrary notice. The notice VERSION, however, IS embedded in the
        # callback (baked in at render time) and MUST be compared against the
        # CURRENT PRIVACY_NOTICE_VERSION here -- otherwise a stale card left
        # open across a version bump (or a hand-crafted future/forged
        # version) could silently acknowledge a notice the user never saw.
        # A mismatch is a safe, silent no-op: no ack recorded, no mood entry
        # opened, no error text (never confirms/denies whether the version
        # was "close").
        rendered_version = data[len(onboarding_content.CB_PRIVACY_ONLY_START_PREFIX):]
        if rendered_version != PRIVACY_NOTICE_VERSION:
            return
        # record_notice_acknowledgement is idempotent (INSERT OR IGNORE) and
        # returns False on a double tap, which must not re-open mood entry a
        # second time.
        if not await record_notice_acknowledgement(uid, "privacy_notice", PRIVACY_NOTICE_VERSION):
            return
        chat_id = callback.message.chat.id
        try:
            await bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=callback.message.message_id, reply_markup=None)
        except TelegramBadRequest:
            pass
        text, _ = get_onboarding(lang)
        await _send_mood_entry(callback.message, uid, lang, text)
        return

    state = await get_active_onboarding_state(uid)
    # Neutral no-op unless there's an ACTIVE onboarding of the current version.
    if state is None or state["onboarding_version"] != ONBOARDING_VERSION:
        return
    step = state["current_step"]
    chat_id = callback.message.chat.id
    card_message_id = state.get("card_message_id") or callback.message.message_id

    if data == onboarding_content.CB_SKIP:
        # Skip informational screens 1–4 -> the privacy screen (5). Never
        # completes onboarding and never bypasses the privacy notice.
        if await skip_onboarding_to_privacy(uid, ONBOARDING_VERSION, LAST_STEP):
            await _render_onboarding_card(uid, chat_id, LAST_STEP, lang,
                                          message_id=card_message_id)
        return

    if data == onboarding_content.CB_PRIVACY:
        # Informational only: deterministic in-bot privacy summary. Does NOT
        # change state and does NOT complete onboarding. (When a real
        # PRIVACY_POLICY_URL is configured, this is a URL button and the handler
        # is never reached.)
        await callback.message.answer(onboarding_content.privacy_summary(lang))
        return

    if data == onboarding_content.CB_START:
        # Valid only on the final privacy step; completes exactly once.
        if step != LAST_STEP:
            return
        if not await complete_onboarding(uid, ONBOARDING_VERSION, LAST_STEP,
                                         privacy_notice_version=PRIVACY_NOTICE_VERSION):
            return  # double tap / already completed -> do NOT re-open mood entry
        try:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=card_message_id,
                                                reply_markup=None)
        except TelegramBadRequest:
            pass
        # Public-beta setup is optional: the user may ignore the selector and
        # type immediately; onboarding is already complete and text remains
        # the persisted default until a format callback succeeds.
        await _send_response_format_setup(callback.message, uid, lang)
        return

    if data.startswith(onboarding_content.CB_PREFIX + "next:"):
        try:
            target = int(data.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            return
        # Valid advance targets are 2..LAST_STEP, each from exactly target-1. A
        # stale/replayed tap whose from-step no longer matches is a silent no-op.
        if target < FIRST_STEP + 1 or target > LAST_STEP:
            return
        if await advance_onboarding_step(uid, ONBOARDING_VERSION, target - 1, target):
            await _render_onboarding_card(uid, chat_id, target, lang,
                                          message_id=card_message_id)
        return


@dp.message(Command("profile"))
async def cmd_profile(message: Message, tg_user=None):
    """§5 — show the user the deterministic profile the bot has built (no diagnoses).

    tg_user: when reached via a callback (results:profile) message.from_user
    is the BOT -- the real user must be passed explicitly (same tg_user
    contract as cmd_emotion/cmd_cbt/cmd_journal_settings/cmd_report)."""
    uid = (tg_user or message.from_user).id
    if not await ensure_full_access_or_closed_test(message, uid):
        return
    lang = await get_user_language(uid)
    prof = await get_profile(uid)
    if not prof:
        await message.answer(
            "У меня пока нет профиля по тебе — давай поговорим побольше." if lang == "ru"
            else "I don't have a profile for you yet — let's talk a bit more.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=("🗑 Стереть профиль" if lang == "ru" else "🗑 Erase profile"),
            callback_data="profile:reset")],
    ])
    # format_profile_for_user is RU plain-language; keep as-is for both for now.
    await message.answer(format_profile_for_user(prof), reply_markup=kb)


@dp.message(Command("profile_reset"))
async def cmd_profile_reset(message: Message):
    if not await ensure_full_access_or_closed_test(message, message.from_user.id):
        return
    lang = await get_user_language(message.from_user.id)
    await delete_profile(message.from_user.id)
    await message.answer(
        "Готово. Профиль стёрт — начнём с чистого листа." if lang == "ru"
        else "Done. Your profile is erased — fresh start.")


@dp.callback_query(F.data == "profile:reset")
async def cb_profile_reset(callback: CallbackQuery):
    if not await ensure_full_access_or_closed_test(callback, callback.from_user.id):
        return
    lang = await get_user_language(callback.from_user.id)
    await delete_profile(callback.from_user.id)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(
        "Готово. Профиль стёрт — начнём с чистого листа." if lang == "ru"
        else "Done. Your profile is erased — fresh start.")
    await callback.answer()


@dp.message(Command("memory"))
async def cmd_memory(message: Message):
    """GDPR §6.3 — show the user what the bot remembers about them."""
    uid = message.from_user.id
    if not await ensure_full_access_or_closed_test(message, uid):
        return
    lang = await get_user_language(uid)
    o = await get_memory_overview(uid)
    if lang == "en":
        lines = [
            "<b>What I remember</b>",
            f"• Messages stored: {o['message_count']}",
            f"• Sessions: {o['total_sessions']}",
            f"• Running emotional state: {'yes' if o['has_state'] else 'no'}",
        ]
        if o["summary"]:
            lines.append(f"• Summary: {_he(o['summary'])}")
        lines.append("\nTo erase everything: /forget_all")
    else:
        lines = [
            "<b>Что я помню</b>",
            f"• Сохранённых сообщений: {o['message_count']}",
            f"• Сессий: {o['total_sessions']}",
            f"• Текущее эмоц. состояние: {'есть' if o['has_state'] else 'нет'}",
        ]
        if o["summary"]:
            lines.append(f"• Резюме: {_he(o['summary'])}")
        lines.append("\nСтереть всё: /forget_all")
    await message.answer("\n".join(lines), parse_mode="HTML")


# ── PR 1B-2: privacy self-service (registry-driven, NOT product-gated) ────────
# /forget_all, /privacy_export_all, /privacy_delete_all all implement the
# user's own privacy rights and therefore must work even when ordinary product
# access is blocked (UNKNOWN, unmapped/unacknowledged tester, etc.) -- none of
# them call ensure_full_access_or_closed_test / has_full_access. Permission is
# scoped_access (requester_uid == target_uid, always true here since none of
# these commands accept a target argument) -- called explicitly anyway for a
# single, auditable enforcement point rather than "trusting" that every
# handler derived uid correctly.

def _privacy_retained_tables() -> list[str]:
    import privacy_registry as pr
    return sorted(t for t, e in pr.PRIVACY_REGISTRY.items() if e.delete_policy == "RETAIN")


async def _privacy_delete_preview_text(uid: int, lang: str) -> str:
    """PR 1B-2 round 2, blocker 3: built from the REAL, registry-driven
    preview_delete_all_personal_data(uid) -- actual row counts for THIS uid,
    not a static category list. No raw content, only counts/policy/reason."""
    preview = await preview_delete_all_personal_data(uid)
    to_delete = sum(v["row_count"] for v in preview.values() if v["policy"] != "RETAIN")
    retained_rows = sum(v["row_count"] for v in preview.values()
                        if v["policy"] == "RETAIN")
    if lang == "ru":
        return (
            f"Предварительный просмотр: будет удалено или обезличено записей — {to_delete}; "
            f"отдельно сохраняемых записей безопасности — {retained_rows}.\n\n"
            "🗑 Удалить данные аккаунта?\n\n"
            "Будут удалены данные, которые можно удалить из аккаунта: история разговоров, "
            "профиль, дневники, настройки, ответы и результаты тестов и другие связанные данные.\n\n"
            "Некоторые записи, связанные с безопасностью и критическими ситуациями, не "
            "удаляются этой операцией и хранятся отдельно в соответствии с правилами "
            "хранения данных.\n\n"
            "Это действие нельзя отменить."
        )
    return (
        f"Preview: {to_delete} record(s) will be deleted or anonymized; "
        f"{retained_rows} safety record(s) are retained separately.\n\n"
        "🗑 Delete account data?\n\n"
        "Data eligible for deletion will be removed, including conversation history, profile, "
        "diaries, settings, questionnaire answers and results, and other related data.\n\n"
        "Some safety- and crisis-related records are not deleted by this operation and are "
        "stored separately under the applicable retention rules.\n\n"
        "This action cannot be undone."
    )


def _privacy_delete_done_text(lang: str) -> str:
    if lang == "ru":
        return (
            "Данные аккаунта удалены\n\n"
            "Данные, подлежащие удалению, были удалены или обезличены.\n\n"
            "Некоторые записи, связанные с безопасностью и критическими ситуациями, "
            "не удаляются этой операцией и могут сохраняться в соответствии с правилами "
            "хранения данных."
        )
    return (
        "Account data deleted\n\n"
        "Data eligible for deletion was deleted or anonymized.\n\n"
        "Some safety- and crisis-related records are not deleted by this operation and may "
        "be retained under the applicable retention rules."
    )


def _privacy_delete_kb(prefix: str, lang: str, uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=("🗑 Да, удалить" if lang == "ru" else "🗑 Yes, delete"),
            callback_data=f"{prefix}:yes:{uid}"),
        InlineKeyboardButton(
            text=("Отмена" if lang == "ru" else "Cancel"),
            callback_data=f"{prefix}:no:{uid}"),
    ]])


async def _handle_privacy_delete_callback(callback: CallbackQuery) -> None:
    """Shared confirm/execute logic for BOTH forget:* and privacy_delete:*
    callback_data prefixes -- one underlying flow, two entry points (see
    cmd_forget_all's docstring for why the prefix stays separate).

    PR 1B-2 (round 2): this is a DESTRUCTIVE-DELETE confirmation, so it fails
    CLOSED on any malformed callback_data -- unlike the crisis path's legacy
    2-part callback (which only ever READS state), there is no backward-
    compatible "no embedded uid" case here anymore. A callback missing the
    uid segment, with a non-numeric uid segment, or with a uid that doesn't
    match the presser, is treated identically: no delete, no cancel message,
    pure no-op besides acknowledging the tap."""
    parts = callback.data.split(":")
    uid = callback.from_user.id
    if len(parts) < 3 or not parts[2].isdigit() or int(parts[2]) != uid:
        await callback.answer()
        return
    action = parts[1]
    lang = await get_user_language(uid)
    if action == "yes":
        scoped_access.assert_can_read_user_data(uid, uid, "privacy_delete")
        await delete_all_personal_data(uid)
        await callback.message.answer(_privacy_delete_done_text(lang))
    elif action == "no":
        await _edit_or_answer(callback.message)(
            navigation.privacy_hub_text(lang),
            reply_markup=_privacy_hub_keyboard(lang))
    else:
        await callback.answer()
        return
    await callback.answer()


@dp.message(Command("forget_all"))
async def cmd_forget_all(message: Message):
    """GDPR right-to-erasure. PR 1B-2: now a thin alias over the same
    registry-driven flow as /privacy_delete_all (delete_all_personal_data) —
    the old hand-written database.forget_all (an 8-table partial list) has
    been removed entirely, not left as a parallel/deprecated path."""
    uid = message.from_user.id
    scoped_access.assert_can_read_user_data(uid, uid, "privacy_delete")
    lang = await get_user_language(uid)
    await message.answer(await _privacy_delete_preview_text(uid, lang),
                         reply_markup=_privacy_delete_kb("forget", lang, uid))


@dp.callback_query(F.data.startswith("forget:"))
async def cb_forget(callback: CallbackQuery):
    await _handle_privacy_delete_callback(callback)


@dp.message(Command("privacy_export_all"))
async def cmd_privacy_export_all(message: Message):
    """PR 1B-2: self-service GDPR export. Not gated by ordinary product
    access — a person's right to their own data doesn't depend on whether
    they currently have product access. No target-uid argument exists; the
    scoped_access call below is requester==target by construction, kept for a
    single explicit/auditable enforcement point."""
    uid = message.from_user.id
    scoped_access.assert_can_read_user_data(uid, uid, "privacy_export")
    lang = await get_user_language(uid)
    await _send_privacy_export(message, uid, lang)


async def _send_privacy_export(message: Message, uid: int, lang: str) -> None:
    """One authoritative export delivery used by command and privacy UI."""
    import json, io
    from aiogram.types import BufferedInputFile
    data = await export_all_personal_data(uid)
    if not any(data.values()):
        await message.answer(
            "Персональных данных пока нет." if lang == "ru" else "No personal data yet.")
        return
    note = (
        "\n\nНекоторые записи, связанные с безопасностью и критическими ситуациями, "
        "могут храниться отдельно по правилам хранения данных." if lang == "ru" else
        "\n\nSome safety- and crisis-related records may be retained separately under "
        "the applicable retention rules.")
    await message.answer(
        "📥 Копия твоих данных готова\n\nВ файле собраны данные, связанные с твоим "
        "аккаунтом и использованием сервиса." if lang == "ru" else
        "📥 Your data copy is ready\n\nThe file contains data associated with your "
        "account and use of the service.")
    buf = io.BytesIO(json.dumps(data, ensure_ascii=False, indent=2, default=str).encode("utf-8"))
    await message.answer_document(
        BufferedInputFile(buf.getvalue(), filename="privacy_export.json"),
        caption=("Копия данных аккаунта (JSON)." if lang == "ru" else
                 "Account data copy (JSON).") + note)


@dp.message(Command("privacy_delete_all"))
async def cmd_privacy_delete_all(message: Message):
    """PR 1B-2: self-service GDPR delete, identical flow to /forget_all (see
    _handle_privacy_delete_callback) under its own command name/prefix."""
    uid = message.from_user.id
    scoped_access.assert_can_read_user_data(uid, uid, "privacy_delete")
    lang = await get_user_language(uid)
    await message.answer(await _privacy_delete_preview_text(uid, lang),
                         reply_markup=_privacy_delete_kb("privacy_delete", lang, uid))


@dp.callback_query(F.data.startswith("privacy_delete:"))
async def cb_privacy_delete(callback: CallbackQuery):
    await _handle_privacy_delete_callback(callback)


# ── PR 1B-2: reviewer/owner tool — NOT a product command, NOT privacy self-
# service. Permission is EXACTLY access_control.can_request_review_pack, which
# review_pack.generate_review_pack already enforces internally; this handler
# adds no additional gate and must never call ensure_full_access_or_closed_test
# (a CLINICIAN_REVIEWER has zero ordinary product access but must still be
# able to use this for a mapped tester). Denial text is deliberately generic —
# no raw data, no confirmation the target exists, no role/mapping detail.
@dp.message(Command("unblock"))
async def cmd_unblock(message: Message):
    """Owner-only reactivation of a previously blocked user_access row (the
    canonical revoke->reactivate completion). Non-owner gets a neutral denial
    with no feature disclosure. Uses the same raw-uid owner workflow as
    /review_pack. Never grants access to an unknown/never-invited user
    (unblock_user_access only flips an EXISTING blocked row). Result codes are
    sanitized; no user id is echoed beyond the owner's own argument."""
    uid = message.from_user.id
    lang = await get_user_language(uid)
    if access_control.OWNER_USER_ID is None or uid != access_control.OWNER_USER_ID:
        # Neutral denial -- same class as any other unauthorized command.
        await message.answer(
            "Команда недоступна." if lang == "ru" else "Command not available.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().lstrip("-").isdigit():
        await message.answer(
            "Использование: /unblock <user_id>" if lang == "ru" else
            "Usage: /unblock <user_id>")
        return
    target_uid = int(parts[1].strip())
    result = await unblock_user_access(target_uid)
    messages = {
        "reactivated": ("✅ Доступ восстановлен." if lang == "ru"
                        else "✅ Access reactivated."),
        "already-active": ("Доступ уже активен." if lang == "ru"
                           else "Access is already active."),
        "no-existing-access": ("У пользователя нет записи доступа (не приглашён)."
                               if lang == "ru" else
                               "No access record for this user (never invited)."),
    }
    await message.answer(messages[result])


@dp.message(Command("review_pack"))
async def cmd_review_pack(message: Message):
    import json, io
    from aiogram.types import BufferedInputFile
    requester_uid = message.from_user.id
    lang = await get_user_language(requester_uid)
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().lstrip("-").isdigit():
        await message.answer(
            "Использование: /review_pack <user_id>" if lang == "ru" else
            "Usage: /review_pack <user_id>")
        return
    target_uid = int(parts[1].strip())
    try:
        pack = await review_pack.generate_review_pack(target_uid, requester_uid=requester_uid)
    except review_pack.ReviewPackNotAllowed:
        await message.answer(
            "Недостаточно прав для этого запроса." if lang == "ru" else
            "Not authorized for this request.")
        return
    buf = io.BytesIO(json.dumps(pack, ensure_ascii=False, indent=2, default=str).encode("utf-8"))
    await message.answer_document(
        BufferedInputFile(buf.getvalue(), filename=f"review_pack_{target_uid}.json"),
        caption="Review pack")

@dp.message(Command("mute"))
async def cmd_mute(message: Message):
    if not await ensure_full_access_or_closed_test(message, message.from_user.id):
        return
    lang = await get_user_language(message.from_user.id)
    await set_mute(message.from_user.id, "forever")
    await message.answer("Пуши отключены. /unmute — включить обратно." if lang == "ru"
                         else "Pushes off. /unmute to turn them back on.")


@dp.message(Command("mute_today"))
async def cmd_mute_today(message: Message):
    from datetime import datetime, timezone, timedelta
    if not await ensure_full_access_or_closed_test(message, message.from_user.id):
        return
    lang = await get_user_language(message.from_user.id)
    until = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    await set_mute(message.from_user.id, "until", until.strftime("%Y-%m-%d %H:%M:%S"))
    await message.answer("Тихо до конца дня." if lang == "ru" else "Quiet for the rest of today.")


@dp.message(Command("mute_week"))
async def cmd_mute_week(message: Message):
    from datetime import datetime, timezone, timedelta
    if not await ensure_full_access_or_closed_test(message, message.from_user.id):
        return
    lang = await get_user_language(message.from_user.id)
    until = datetime.now(timezone.utc) + timedelta(days=7)
    await set_mute(message.from_user.id, "until", until.strftime("%Y-%m-%d %H:%M:%S"))
    await message.answer("Тихо на неделю." if lang == "ru" else "Quiet for a week.")


@dp.message(Command("unmute"))
async def cmd_unmute(message: Message):
    if not await ensure_full_access_or_closed_test(message, message.from_user.id):
        return
    lang = await get_user_language(message.from_user.id)
    await set_mute(message.from_user.id, "none")
    await message.answer("Пуши снова включены." if lang == "ru" else "Pushes back on.")


@dp.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext = None):
    """Round 3: /help is a normal in-chat navigation/help card with
    user-facing RU/EN labels, not a raw technical slash-command list. Old
    slash-command handlers (/menu, /questionnaire, /journal, /format,
    /checkin, /time, /profile, /forget_all, /privacy_export_all,
    /privacy_delete_all) remain fully registered and callable manually for
    backward compatibility -- they are simply no longer listed or advertised
    here. Static/role-unaware by design, same as before this round; every
    button below reuses an EXISTING gated callback (see _help_keyboard).

    Round 4: /help was already reachable while an EmotionJournal/CbtJournal
    FSM was active (Command filters are matched before the journal's F.text
    filter), but the stale journal state itself was never cleared, so the
    NEXT ordinary message would still be silently consumed by the old
    journal. Abandoning it here closes that gap without any extra "journal
    cancelled" noise."""
    if state is not None:
        await _clear_active_journal_if_leaving(state)
    lang = await get_user_language(message.from_user.id)
    await message.answer(navigation.help_text(lang), reply_markup=_help_keyboard(lang))

@dp.message(Command("checkin"))
async def cmd_checkin(message: Message):
    if not await ensure_full_access_or_closed_test(message, message.from_user.id):
        return
    lang = await get_user_language(message.from_user.id)
    text = ("Выбери время check-in (UTC):" if lang == "ru" else "Choose check-in time (UTC):")
    await message.answer(text + "\n/checkin_8 • /checkin_10 • /checkin_12 • /checkin_18 • /checkin_20\n/checkin_off")

async def _enable_ci(message: Message, hour: int):
    if not await ensure_full_access_or_closed_test(message, message.from_user.id):
        return
    lang = await get_user_language(message.from_user.id)
    await set_checkin(message.from_user.id, message.from_user.username or "",
                      message.from_user.first_name or "", True, hour, lang)
    await message.answer(f"✅ Check-in в {hour:02d}:00 UTC" if lang == "ru" else f"✅ Check-in at {hour:02d}:00 UTC")

@dp.message(Command("checkin_8"))
async def ci_8(m: Message): await _enable_ci(m, 8)
@dp.message(Command("checkin_10"))
async def ci_10(m: Message): await _enable_ci(m, 10)
@dp.message(Command("checkin_12"))
async def ci_12(m: Message): await _enable_ci(m, 12)
@dp.message(Command("checkin_18"))
async def ci_18(m: Message): await _enable_ci(m, 18)
@dp.message(Command("checkin_20"))
async def ci_20(m: Message): await _enable_ci(m, 20)

@dp.message(Command("checkin_off"))
async def ci_off(message: Message):
    if not await ensure_full_access_or_closed_test(message, message.from_user.id):
        return
    await set_checkin(message.from_user.id, "", "", False, 10, "ru")
    lang = await get_user_language(message.from_user.id)
    await message.answer("Check-in отключён" if lang == "ru" else "Check-in disabled")

# ── Epic 8: Journals — emotion journal FSM (registered ABOVE the catch-all so
# journal steps take priority over the generic pipeline text handler) ─────────

@dp.message(Command("emotion"))
async def cmd_emotion(message: Message, state: FSMContext, tg_user=None):
    # tg_user: when reached via a callback (cb_checkin / cb_jhub) message.from_user
    # is the BOT — the real user must be passed explicitly, like pipeline does.
    uid = (tg_user or message.from_user).id
    lang = await get_user_language(uid)
    # §2: no journaling while a crisis is unresolved — show the crisis screen.
    # This must run BEFORE the access gate: an active crisis is crisis-adjacent
    # and must be re-shown regardless of role/access.
    decision, _ = await journal_guard(message, uid, lang)
    if decision == "crisis":
        return
    if not await ensure_full_access_or_closed_test(message, uid):
        return
    await state.clear()
    await state.set_state(EmotionJournal.active)
    await state.update_data(jstep=0, jdata={}, orange=False, nudged=False)
    intro = (
        "📝 Дневник эмоций\n\n"
        "Отвечай как есть. Если захочешь остановиться — можно выйти в меню в любой момент.\n\n"
        if lang == "ru" else
        "📝 Emotion journal\n\n"
        "Answer as it feels. If you want to stop, you can leave to the menu at any time.\n\n"
    )
    await message.answer(intro + journals.emotion_prompt("event", lang))


@dp.message(Command("journal_cancel"))
@dp.message(F.text.in_({"/cancel", "/Cancel"}))
async def cmd_journal_cancel(message: Message, state: FSMContext):
    cur = await state.get_state()
    await state.clear()
    if cur:
        lang = await get_user_language(message.from_user.id)
        await message.answer("Окей, остановились. Ничего не записал."
                             if lang == "ru" else "Okay, stopped. Nothing saved.")


@dp.message(EmotionJournal.active, F.text, ~F.text.in_(_LOWER_MENU_CONTROL_LABELS))
async def emotion_step(message: Message, state: FSMContext):
    uid = message.from_user.id
    username = message.from_user.username or ""
    text = message.text.strip()
    lang = await get_user_language(uid)

    # Single safety gate: active-crisis check (re-show current screen, no second
    # event) + per-field risk gate (RED → crisis, ambiguous → clarifier). Any of
    # these aborts the journal and wipes the FSM.
    decision, risk = await journal_guard(message, uid, lang, text, username)
    if decision in ("crisis", "ambiguous"):
        await state.clear()
        return

    data = await state.get_data()
    step = data["jstep"]
    jdata = data["jdata"]
    field = journals.EMOTION_FIELDS[step]
    if field == "intensity":
        digits = "".join(ch for ch in text if ch.isdigit())
        jdata[field] = min(10, int(digits[:2])) if digits else None
    else:
        jdata[field] = text

    orange = data.get("orange", False) or (decision == "orange")
    nudged = data.get("nudged", False)
    prefix = ""
    if orange and not nudged:
        prefix = journals.hotline_nudge(lang).strip() + "\n\n"
        nudged = True

    # Advance, skipping the somatic 'body' step when risk is elevated/sensitive.
    nxt = step + 1
    while nxt < len(journals.EMOTION_FIELDS) and \
            journals.EMOTION_FIELDS[nxt] == "body" and \
            journals.should_skip_body("ORANGE" if orange else "GREEN", risk):
        nxt += 1

    if nxt >= len(journals.EMOTION_FIELDS):
        await save_emotion_entry(uid, jdata, lang)
        await state.clear()
        await message.answer(prefix + journals.emotion_saved_text(lang))
        return

    await state.update_data(jstep=nxt, jdata=jdata, orange=orange, nudged=nudged)
    # "feeling" is the one field that asks the user to NAME an emotion --
    # offer the deterministic Emotion Map helper there only.
    next_kb = _emotion_map_keyboard(lang) if journals.EMOTION_FIELDS[nxt] == "feeling" else None
    await message.answer(prefix + journals.emotion_prompt(journals.EMOTION_FIELDS[nxt], lang),
                         reply_markup=next_kb)


# ── Epic 8: CBT journal (deep) — aborts at ORANGE, not just RED ───────────────

@dp.message(Command("cbt"))
async def cmd_cbt(message: Message, state: FSMContext, tg_user=None):
    # tg_user: real user when reached via callback (see cmd_emotion note).
    uid = (tg_user or message.from_user).id
    lang = await get_user_language(uid)
    decision, _ = await journal_guard(message, uid, lang)
    if decision == "crisis":
        return
    if not await ensure_full_access_or_closed_test(message, uid):
        return
    await state.clear()
    await state.set_state(CbtJournal.active)
    await state.update_data(cstep=0, cdata={})
    intro = (
        "📘 КПТ-дневник\n\n"
        "Ты сам формулируешь мысли — я только помогаю их записать. "
        "Если захочешь остановиться — можно выйти в меню в любой момент.\n\n"
        if lang == "ru" else
        "📘 CBT journal\n\n"
        "You formulate your own thoughts — I just help write them down. "
        "If you want to stop, you can leave to the menu at any time.\n\n"
    )
    await message.answer(intro + journals.cbt_prompt("situation", lang))


@dp.message(CbtJournal.active, F.text, ~F.text.in_(_LOWER_MENU_CONTROL_LABELS))
async def cbt_step(message: Message, state: FSMContext):
    uid = message.from_user.id
    username = message.from_user.username or ""
    text = message.text.strip()
    lang = await get_user_language(uid)

    # Single safety gate (active crisis + per-text risk). Deep CBT is also
    # contraindicated at ORANGE, so we stop gently there too.
    decision, risk = await journal_guard(message, uid, lang, text, username)
    if decision in ("crisis", "ambiguous"):
        await state.clear()
        return
    if decision == "orange":
        await state.clear()
        msg = ("Давай пока не будем углубляться в разбор мыслей — сейчас важнее "
               "немного стабилизироваться. Я рядом." if lang == "ru" else
               "Let's not dig into the thoughts right now — steadying is more "
               "important at the moment. I'm here.")
        await message.answer(msg + journals.hotline_nudge(lang))
        return

    data = await state.get_data()
    step = data["cstep"]; cdata = data["cdata"]
    field = journals.CBT_FIELDS[step]
    if field == "intensity":
        digits = "".join(ch for ch in text if ch.isdigit())
        cdata[field] = min(10, int(digits[:2])) if digits else None
    else:
        cdata[field] = text

    nxt = step + 1
    if nxt >= len(journals.CBT_FIELDS):
        await save_cbt_entry(uid, cdata, lang)
        await state.clear()
        await message.answer(journals.cbt_saved_text(lang))
        return
    await state.update_data(cstep=nxt, cdata=cdata)
    # "emotion" is the field that asks the user to NAME a feeling -- same
    # Emotion Map helper as the emotion-journal "feeling" step.
    next_kb = _emotion_map_keyboard(lang) if journals.CBT_FIELDS[nxt] == "emotion" else None
    await message.answer(journals.cbt_prompt(journals.CBT_FIELDS[nxt], lang), reply_markup=next_kb)


# ── Epic 8: weekly report (deterministic), settings, GDPR ─────────────────────

@dp.message(Command("report"))
async def cmd_report(message: Message, state: FSMContext, tg_user=None):
    # tg_user: when reached via a callback (cb_jhub) message.from_user is the
    # BOT -- the real user must be passed explicitly (same tg_user contract
    # as cmd_emotion/cmd_cbt/cmd_journal_settings).
    uid = (tg_user or message.from_user).id
    if not await ensure_full_access_or_closed_test(message, uid):
        return
    lang = await get_user_language(uid)
    emo = await get_emotion_entries_since(uid, 7)
    chk = await get_checkin_logs_since(uid, 7)
    await message.answer(journals.build_weekly_report(emo, chk, lang))


def _journal_hub_text(lang: str) -> str:
    if lang == "ru":
        return "📝 Дневники\n\nВыбери, что хочешь открыть:"
    return "📝 Diaries\n\nChoose what you'd like to open:"


def _journal_hub_keyboard(lang: str) -> InlineKeyboardMarkup:
    # Single source for the journal hub's real action buttons -- reused by
    # BOTH the persistent-lower-menu entry (cmd_journal) and the /help entry
    # (cb_journals_hub, round-3 correction) so there is exactly ONE journal
    # navigation UX, not a real card on one path and a raw slash-command list
    # on the other.
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Дневник эмоций", callback_data="jhub:emotion")],
        [InlineKeyboardButton(text="📘 КПТ-дневник", callback_data="jhub:cbt")],
        [InlineKeyboardButton(text="📊 Мой отчёт", callback_data="jhub:report")],
        [InlineKeyboardButton(text="⚙️ Напоминания", callback_data="jhub:settings")],
        [InlineKeyboardButton(text="🚨 Срочно плохо", callback_data="jhub:crisis")],
        [InlineKeyboardButton(
            text=("⬅️ В меню" if lang == "ru" else "⬅️ Back to menu"),
            callback_data="menu:back")],
    ])


@dp.message(Command("journal"))
async def cmd_journal(message: Message, state: FSMContext = None):
    # Round 4 final correction: the journal-hub entry must use the SAME
    # navigation safety contract as every other nav entrypoint (journal_guard
    # / active-crisis check THEN ordinary access gate, via _nav_gate) rather
    # than the bare access gate alone. This matters now specifically because
    # the persistent-lower-menu "📝 Дневники" button reaches this function
    # (via lower_menu_journals) even while an EmotionJournal/CbtJournal FSM
    # is active -- before this fix, an active crisis would have been caught
    # by emotion_step/cbt_step's own journal_guard call instead; the Round 4
    # routing fix means that no longer happens, so this entrypoint must catch
    # it itself. The unfinished journal is abandoned only AFTER the gate
    # permits normal navigation, so a blocked crisis screen is never
    # accompanied by a lost journal session.
    uid = message.from_user.id
    lang = await get_user_language(uid)
    if not await _nav_gate(message, uid, lang):
        return
    await _clear_active_journal_if_leaving(state)
    await message.answer(_journal_hub_text(lang), reply_markup=_journal_hub_keyboard(lang))


@dp.callback_query(F.data.startswith("jhub:"))
async def cb_jhub(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split(":")[1]
    await callback.answer()
    # Round 4: report/settings/crisis don't start a fresh journal FSM
    # themselves (unlike emotion/cbt, which already clear it via
    # cmd_emotion/cmd_cbt), so a stale active journal must be abandoned here
    # too. Harmless no-op for emotion/cbt -- they clear it again right after.
    await _clear_active_journal_if_leaving(state)
    if action == "emotion":
        await cmd_emotion(callback.message, state, tg_user=callback.from_user)
    elif action == "cbt":
        await cmd_cbt(callback.message, state, tg_user=callback.from_user)
    elif action == "report":
        await cmd_report(callback.message, state, tg_user=callback.from_user)
    elif action == "settings":
        await cmd_journal_settings(callback.message, state, tg_user=callback.from_user,
                                   send=_edit_or_answer(callback.message))
    elif action == "crisis":
        lang = await get_user_language(callback.from_user.id)
        # Legacy manual-crisis screen (no staged event → eid=None).
        await send_crisis(callback.message.answer, get_crisis_text(lang),
                          crisis_keyboard(lang), lang, callback.from_user.id,
                          None, "manual")


@dp.message(Command("journal_settings"))
async def cmd_journal_settings(message: Message, state: FSMContext, tg_user=None, send=None):
    """`tg_user`/`send`: when reached via a callback (cb_jhub / cb_jset)
    message.from_user is the BOT -- the real user must be passed explicitly
    (same tg_user contract as cmd_emotion/cmd_cbt) -- and the existing
    navigation card must be edited in place rather than appended to. A plain
    /journal_settings command entry has no existing card to edit, so both
    default to the direct-message behavior unchanged."""
    uid = (tg_user or message.from_user).id
    if not await ensure_full_access_or_closed_test(message, uid):
        return
    lang = await get_user_language(uid)
    s = await get_journal_settings(uid)
    m = "✅" if s["morning_enabled"] else "❌"
    e = "✅" if s["evening_enabled"] else "❌"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{m} Утро ({s['morning_hour']}:00)", callback_data="jset:morning")],
        [InlineKeyboardButton(text=f"{e} Вечер ({s['evening_hour']}:00)", callback_data="jset:evening")],
        [InlineKeyboardButton(text="🌍 Часовой пояс", callback_data="jset:tz")],
    ])
    await (send or message.answer)(
        ("Напоминания приходят в твоём местном времени. По умолчанию выключены — "
         "включай что нужно. Это не обязаловка, выключить можно одной кнопкой."
         if lang == "ru" else
         "Reminders arrive in your local time. Off by default — turn on what you want."),
        reply_markup=kb)


@dp.callback_query(F.data.startswith("jset:"))
async def cb_jset(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    if not await ensure_full_access_or_closed_test(callback, uid):
        return
    what = callback.data.split(":")[1]
    if what in ("morning", "evening"):
        s = await get_journal_settings(uid)
        key = f"{what}_enabled"
        await set_journal_settings(uid, **{key: 0 if s[key] else 1})
        await callback.answer("Готово")
        await cmd_journal_settings(callback.message, state, tg_user=callback.from_user,
                                   send=_edit_or_answer(callback.message))
    elif what == "tz":
        await _edit_or_answer(callback.message)(
            "Выбери свой часовой пояс:", reply_markup=tz_picker_keyboard())
        await callback.answer()


@dp.message(Command("time"))
async def cmd_time(message: Message, state: FSMContext):
    """Discoverable entry to the SAME tz picker as /journal_settings → 🌍."""
    if not await ensure_full_access_or_closed_test(message, message.from_user.id):
        return
    lang = await get_user_language(message.from_user.id)
    await message.answer(
        "В каком часовом поясе ты сейчас? Это нужно, чтобы приветствия и "
        "напоминания приходили по твоему местному времени." if lang == "ru" else
        "What's your timezone? So greetings and reminders arrive in your local time.",
        reply_markup=tz_picker_keyboard())


@dp.callback_query(F.data.startswith("jtz:"))
async def cb_jtz(callback: CallbackQuery):
    if not await ensure_full_access_or_closed_test(callback, callback.from_user.id):
        return
    offset = int(callback.data.split(":")[1])
    await set_tz_offset(callback.from_user.id, offset)
    await callback.answer("Часовой пояс сохранён")
    await _edit_or_answer(callback.message)(f"Ок, твой пояс: UTC{offset:+d}.")


@dp.callback_query(F.data.startswith("checkin:"))
async def cb_checkin(callback: CallbackQuery, state: FSMContext):
    if not await ensure_full_access_or_closed_test(callback, callback.from_user.id):
        return
    _, kind, value = callback.data.split(":", 2)
    await log_checkin(callback.from_user.id, kind, value)
    await callback.answer("Отметил")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    if value == "emotion_journal":
        await cmd_emotion(callback.message, state, tg_user=callback.from_user)
    elif value == "cbt_journal":
        await cmd_cbt(callback.message, state, tg_user=callback.from_user)
    else:
        # Statement only: the check-in mark is saved (checkin_logs) but there is
        # no user-facing trend/graph, so we promise nothing beyond "noted".
        lang = await get_user_language(callback.from_user.id)
        await callback.message.answer(journals.checkin_ack_text(lang))


@dp.message(Command("journal_export"))
async def cmd_journal_export(message: Message, state: FSMContext):
    import json, io
    if not await ensure_full_access_or_closed_test(message, message.from_user.id):
        return
    data = await export_journals(message.from_user.id)
    if not any(data.values()):
        await message.answer("Журнальных записей пока нет.")
        return
    buf = io.BytesIO(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
    buf.name = "journals.json"
    from aiogram.types import BufferedInputFile
    await message.answer_document(BufferedInputFile(buf.getvalue(), filename="journals.json"),
                                  caption="Твои журналы (JSON).")


@dp.message(Command("journal_delete"))
async def cmd_journal_delete(message: Message, state: FSMContext):
    if not await ensure_full_access_or_closed_test(message, message.from_user.id):
        return
    await delete_journals(message.from_user.id)
    lang = await get_user_language(message.from_user.id)
    await message.answer("Готово. Все журнальные записи стёрты."
                         if lang == "ru" else "Done. All journal entries erased.")


# ── Questionnaire Registry UX (PR A) — in-chat skeleton, storage-only ──────────
# FULLY REPLACES the earlier Questionnaire Core PR #1 single-definition
# handlers (there is no parallel/coexisting old loader path -- see
# questionnaires.py module docstring for the behavioral-parity write-up).
#
# Deliberately NOT in /help yet (infrastructure-first). Gated the same way as
# /emotion, /cbt, and the Navigation Hub: journal_guard (active-crisis) runs
# BEFORE ensure_full_access_or_closed_test (product access) on every single
# new entrypoint below -- see _questionnaire_gate, which layers a session-
# ownership check on top of the exact same two gates _nav_gate uses, in the
# same order.
#
# Callback format (all <=64 bytes -- see test_questionnaire_registry.py):
#   q:l                    list
#   q:c:<cat>              category
#   q:d:<qid>              detail card
#   q:s:<qid>              start
#   q:v:<sid>              resume this exact owned/current active session
#   q:w:<sid>:<target>     explicitly cancel the current active session and
#                          switch to the validated target definition/version
#   q:a:<sid>:<step>:<aid> answer
#   q:b:<sid>              back
#   q:p:<sid>              pause/continue later (the ONLY exit action exposed
#                          in the live question keyboard as of the owner-
#                          review UX correction below)
#   q:x:<sid>              cancel -- destructive; no longer exposed as a
#                          standalone user-facing button, but kept registered
#                          (a pre-deploy client may still show a cached old
#                          keyboard) and reused internally by q:n's restart
#   q:n:<sid>              explicit restart: cancel this session, start a
#                          fresh one for the SAME questionnaire at step 0
# item_id is NEVER embedded in callback_data -- the current item is derived
# from session.current_index (aliased here as "step"), read fresh from the
# DB on every callback.
#
# Mid-session re-validation: the registry is reloaded FRESH FROM DISK on every
# q:a callback (see _load_registry_fresh) rather than cached for the process
# lifetime, specifically so a definition that becomes archived/draft/
# restricted/invalid between session start and a later answer is caught by
# can_answer() on the very next callback, not just at session start.

def _load_registry_fresh() -> questionnaires.Registry:
    """Always re-reads the directory from disk. Deliberately NOT memoized at
    module/process scope: PR A's spec requires that a definition invalidated
    *after* a session starts (archived/draft/restricted/schema-broken) is
    caught on the next q:a callback, not only at session start. A cached
    long-lived Registry instance would make that re-check decorative (it
    would keep answering against the stale in-memory copy) -- so every
    gate/handler that needs current validity calls this, not a stored
    instance."""
    return questionnaires.load_registry()


async def _available_questionnaire_catalog(uid: int) -> dict[str, list[dict]]:
    """Build the per-user public catalog from fresh, executable evidence.

    Generic instruments must pass the existing manifest + definition linkage
    gate. DASS-21 retains its pre-existing stress-only discovery rule and adds
    a fresh definition/startability check so a missing private file never
    renders a dead button.
    """
    catalog = {key: [] for key, _, _ in questionnaire_ux.CATALOG_CATEGORIES}
    document = _load_catalog_document()
    registry = _load_registry_fresh()

    if document is not None:
        for instrument in clinical_instrument_catalog.available_public_instruments(
                document, registry):
            entry = {
                "instrument_id": instrument.instrument_id,
                "title_ru": instrument.title_ru,
                "title_en": instrument.title_en,
                "definition_id": instrument.definition_id,
            }
            for category_id in instrument.category_ids:
                catalog[category_id].append(entry)

    # Preserve, but do not broaden, the existing DASS discovery contract: one
    # entry under stress only, after the same per-user authorization plus the
    # private-definition/combined-start gates.
    if (document is not None
            and (config.DASS21_INVITED_USERS_ENABLED
                 or access_control.DEPLOYMENT_MODE == "public")):
        decision = await dass21_access.authorize_dass21_user(uid)
        qid = dass21_runtime.DASS21_DEFINITION_ID
        try:
            dass_startable = decision.allowed and registry.combined_can_start(qid, document)
        except Exception:
            dass_startable = False
        if dass_startable:
            catalog["stress_burnout"].append({
                "instrument_id": "dass",
                "title_ru": "DASS-21 — депрессия, тревога, стресс",
                "title_en": "DASS-21 — depression, anxiety, stress",
                "definition_id": qid,
            })

    return catalog


_DASS21_RECOMMENDATION_AREAS = (
    ("anxiety", "anxiety", "Тревога", "Anxiety"),
    ("mood", "depression", "Настроение", "Mood"),
    ("stress", "stress_burnout", "Стресс и напряжение", "Stress and tension"),
)


async def _dass21_recommendation_options(uid: int) -> dict[str, dict]:
    """Return at most one real downstream instrument per product area.

    The existing availability catalog is the sole source of truth. DASS-21
    itself is excluded so the router cannot recommend a loop back to the
    questionnaire whose result the user is already viewing.
    """
    try:
        catalog = await _available_questionnaire_catalog(uid)
    except Exception as exc:
        logging.warning("DASS recommendation availability failed (error_type=%s)",
                        type(exc).__name__)
        return {}
    options = {}
    for area_id, category_id, _ru, _en in _DASS21_RECOMMENDATION_AREAS:
        downstream = [entry for entry in catalog.get(category_id, [])
                      if entry.get("instrument_id") not in {"dass", "gad7"}]
        if downstream:
            options[area_id] = downstream[0]
    return options


# Telegram message text hard limit is 4096 chars; stay safely below it.
_QUESTIONNAIRE_CARD_MAXLEN = 3900
_COMPACT_BUTTON_MAXLEN = 16


def _compact_button_token(value) -> str | None:
    """A value is eligible as a SHORT answer button only if it is a plain,
    non-empty, non-padded string of bounded length. None/bool/numbers/padded
    or overlong strings return None -> the keyboard falls back to full-label
    buttons (callback_data always uses the answer id, never this token)."""
    if not isinstance(value, str) or isinstance(value, bool):
        return None
    if not value or value != value.strip():
        return None
    if len(value) > _COMPACT_BUTTON_MAXLEN:
        return None
    return value


def _questionnaire_nav_row(session_id: int, lang: str) -> list:
    # Owner-review UX correction: the visible exit action is now the existing
    # state-preserving pause path (q:p), not the destructive cancel (q:x) --
    # current_index and all recorded answers survive; see cb_questionnaire_pause.
    return [
        InlineKeyboardButton(text=("⬅️ Назад" if lang == "ru" else "⬅️ Back"),
                             callback_data=f"q:b:{session_id}"),
        InlineKeyboardButton(text=("⏸ Отложить" if lang == "ru" else "⏸ Continue later"),
                             callback_data=f"q:p:{session_id}"),
    ]


def _questionnaire_full_label_keyboard(definition: dict, session_id: int, step: int, item: dict, lang: str) -> InlineKeyboardMarkup:
    """Pre-#57 layout: one FULL-label button per row. Used as the deterministic
    fallback when compact values are unsafe or the legend card is too long."""
    rows = [[InlineKeyboardButton(text=opt["label"],
                                  callback_data=f"q:a:{session_id}:{step}:{opt['id']}")]
            for opt in item["options"]]
    rows.append(_questionnaire_nav_row(session_id, lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _questionnaire_item_keyboard(definition: dict, session_id: int, step: int, item: dict, lang: str) -> InlineKeyboardMarkup:
    # PR #57 single-card UX: SHORT buttons (the option's numeric value) in one
    # row -- Telegram truncates long labels on inline buttons. The FULL answer
    # wording lives in the card text legend (questionnaire_ux.question_text).
    # Falls back to one full-label button per row when values are missing or
    # not unique (never two identical buttons for different answers).
    values = [_compact_button_token(opt.get("value")) for opt in item["options"]]
    if all(v is not None for v in values) and len(set(values)) == len(values):
        rows = [[InlineKeyboardButton(text=v,
                                      callback_data=f"q:a:{session_id}:{step}:{opt['id']}")
                 for v, opt in zip(values, item["options"])]]
    else:
        rows = [[InlineKeyboardButton(text=opt["label"],
                                      callback_data=f"q:a:{session_id}:{step}:{opt['id']}")]
                for opt in item["options"]]
    rows.append(_questionnaire_nav_row(session_id, lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _questionnaire_list_keyboard(lang: str, catalog: dict[str, list]) -> InlineKeyboardMarkup:
    # Render only categories that currently contain at least one fully
    # authorized, startable entity. Empty roadmap categories never become
    # fake/dead buttons.
    rows = [[InlineKeyboardButton(text=questionnaire_ux.catalog_category_label(key, lang),
                                  callback_data=f"q:c:{key}")]
            for key, _, _ in questionnaire_ux.CATALOG_CATEGORIES
            if catalog.get(key)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _questionnaire_category_keyboard(entries: list[dict], lang: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(
                text=(gad7_ux.catalog_button_label(lang)
                      if entry.get("instrument_id") == "gad7"
                      else entry["title_ru"] if lang == "ru" else entry["title_en"]),
                callback_data=f"q:d:{entry['definition_id']}")]
            for entry in entries]
    rows.append([InlineKeyboardButton(text=("⬅️ Назад" if lang == "ru" else "⬅️ Back"),
                                      callback_data="q:l")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _catalog_nav_only_keyboard(lang: str) -> InlineKeyboardMarkup:
    # Used for empty categories and the consultation_report info screen: never
    # a dead end -- always a way back to the catalog root and the main menu.
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=("⬅️ Назад" if lang == "ru" else "⬅️ Back"),
                              callback_data="q:l")],
        [InlineKeyboardButton(text=("🏠 В меню" if lang == "ru" else "🏠 To the menu"),
                              callback_data="menu:back")],
    ])


def _catalog_info_keyboard(category_id: str, lang: str,
                           *, start_definition_id: str | None = None) -> InlineKeyboardMarkup:
    # Instrument information screen. If (and only if) the availability
    # double-gate resolved an explicit startable definition id, a "Пройти"
    # button is shown that routes into the EXISTING q:d:<definition_id> detail
    # flow -- q:i itself never starts anything. Never fires in this PR (no
    # instrument is ready). Then back-to-category and home-to-menu.
    rows: list = []
    if start_definition_id:
        rows.append([InlineKeyboardButton(
            text=("▶️ Пройти" if lang == "ru" else "▶️ Start"),
            callback_data=f"q:d:{start_definition_id}")])
    rows.append([InlineKeyboardButton(text=("⬅️ Назад" if lang == "ru" else "⬅️ Back"),
                                      callback_data=f"q:c:{category_id}")])
    rows.append([InlineKeyboardButton(text=("🏠 В меню" if lang == "ru" else "🏠 To the menu"),
                                      callback_data="menu:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _questionnaire_detail_keyboard(qid: str, lang: str, *, active_session: dict | None = None,
                                   total_items: int | None = None) -> InlineKeyboardMarkup:
    """`active_session` is the caller's OWN compatible (same id+version) active
    session, if any -- see _compatible_active_session. When present, the
    detail screen must not misleadingly offer only "Начать": it offers
    Continue (routes to the existing q:s resume path, unchanged) and the one
    intentional destructive reset, q:n. Session ids are never rendered as
    text, only embedded in callback_data like every other questionnaire
    button."""
    if active_session is not None:
        current = active_session["current_index"] + 1
        total = total_items if total_items is not None else "?"
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=(f"▶️ Продолжить — вопрос {current} из {total}" if lang == "ru"
                      else f"▶️ Continue -- question {current} of {total}"),
                callback_data=f"q:s:{qid}")],
            [InlineKeyboardButton(text=("🔄 Начать заново" if lang == "ru" else "🔄 Start over"),
                                  callback_data=f"q:n:{active_session['id']}")],
            [InlineKeyboardButton(text=("← К тестам" if lang == "ru" else "← Back to tests"),
                                  callback_data="q:l")],
        ])
    start_text = (("Начать" if lang == "ru" else "Start")
                  if gad7_core.is_gad7_definition_id(qid)
                  else ("▶️ Начать" if lang == "ru" else "▶️ Start"))
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=start_text,
                              callback_data=f"q:s:{qid}")],
        [InlineKeyboardButton(text=("← К тестам" if lang == "ru" else "← Back to tests"),
                              callback_data="q:l")],
    ])


def _questionnaire_switch_target_token(definition: dict) -> str:
    """Compact, version-aware identity for q:w callbacks.

    The target id itself can already consume most of Telegram's 64-byte
    callback_data limit. A deterministic digest keeps the destructive switch
    callback bounded while fresh resolution below still requires one exact
    current definition id+version match.
    """
    identity = f"{definition['id']}\0{definition['version']}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest()[:40]


def _resolve_questionnaire_switch_target(registry: questionnaires.Registry,
                                         token: str) -> dict | None:
    matches = [definition for definition in registry.by_id.values()
               if _questionnaire_switch_target_token(definition) == token]
    return matches[0] if len(matches) == 1 else None


def _questionnaire_active_conflict_keyboard(active_session: dict, target: dict,
                                            lang: str) -> InlineKeyboardMarkup:
    switch_data = (
        f"q:w:{active_session['id']}:{_questionnaire_switch_target_token(target)}")
    if len(switch_data.encode("utf-8")) > 64:
        raise ValueError("questionnaire switch callback exceeds Telegram limit")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=("▶️ Продолжить тест" if lang == "ru" else "▶️ Continue test"),
            callback_data=f"q:v:{active_session['id']}")],
        [InlineKeyboardButton(
            text=("✖️ Отменить и начать новый" if lang == "ru"
                  else "✖️ Cancel and start new"),
            callback_data=switch_data)],
        [InlineKeyboardButton(text=("← К тестам" if lang == "ru" else "← Back to tests"),
                              callback_data="q:l")],
    ])


async def _send_questionnaire_active_conflict(send, active_session: dict,
                                              target: dict, lang: str) -> None:
    await send(
        questionnaire_ux.active_test_conflict_text(lang),
        reply_markup=_questionnaire_active_conflict_keyboard(
            active_session, target, lang))


def _my_results_button(lang: str) -> InlineKeyboardButton:
    """Shared by every completed-questionnaire keyboard (generic + DASS-21)
    so the label/callback can't drift between them -- reuses the existing
    results:tests route (cb_results_tests) rather than adding a second one."""
    return InlineKeyboardButton(
        text=("📊 Мои результаты" if lang == "ru" else "📊 My results"),
        callback_data="results:tests")


def _questionnaire_completion_keyboard(session_id: int, lang: str) -> InlineKeyboardMarkup:
    # Owner-review UX correction: a completed result is a historical artifact
    # (it must stay visible in chat), so "another questionnaire" no longer
    # routes to q:l -- q:l edits callback.message in place via _edit_or_
    # answer, which would silently overwrite this very card. q:t (below)
    # sends the SAME catalog as a brand-new message instead. "🏠 В меню"
    # is dropped too: menu:back renders the Help card, which is not what a
    # questionnaire completion screen's home button should mean.
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=("🧾 Отчёт для специалиста" if lang == "ru" else "🧾 Specialist report"),
                              callback_data=f"q:o:{session_id}")],
        [InlineKeyboardButton(text=("🧠 Другой тест" if lang == "ru" else "🧠 Another test"),
                              callback_data="q:t")],
        [_my_results_button(lang)],
    ])


def _dass21_completion_keyboard(session_id: int, lang: str, *,
                                recommendation_available: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if config.DASS21_DISCUSSION_ENABLED:
        rows.append([InlineKeyboardButton(
            text=("💬 Разобрать результат" if lang == "ru" else "💬 Explore the result"),
            callback_data=f"q:m:{session_id}")])
    if recommendation_available:
        rows.append([InlineKeyboardButton(
            text=("🧪 Подобрать тест" if lang == "ru" else "🧪 Choose a questionnaire"),
            callback_data=f"q:pick:{session_id}")])
    rows.append([InlineKeyboardButton(
        text=("🧾 Отчёт для специалиста" if lang == "ru" else "🧾 Specialist report"),
        callback_data=f"q:o:{session_id}")])
    rows.append([_my_results_button(lang)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── PR B — result / calculations / explanation screens (dormant unless
# config.QUESTIONNAIRE_INTERPRETATION_ENABLED is true AND the definition is
# eligible; see questionnaires.is_result_eligible). PR C1.1 added the
# specialist-report button (q:o:<sid>) below. PR C2.1 wires the
# discuss-with-bot entry point (q:m:<sid>, bare menu format) into this
# keyboard only -- see cb_questionnaire_discuss_menu, unchanged from C2.

def _questionnaire_result_keyboard(session_id: int, lang: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=("📊 Расчёты" if lang == "ru" else "📊 Calculations"),
                              callback_data=f"q:k:{session_id}"),
         InlineKeyboardButton(text=("🧠 Что значат шкалы" if lang == "ru" else "🧠 What scales mean"),
                              callback_data=f"q:e:{session_id}")],
        [InlineKeyboardButton(text=("🧾 Отчёт специалисту" if lang == "ru" else "🧾 Specialist report"),
                              callback_data=f"q:o:{session_id}")],
    ]
    if access_control.DEPLOYMENT_MODE != "public":
        rows.append([InlineKeyboardButton(
            text=("💬 Обсудить результат" if lang == "ru" else "💬 Discuss result"),
            callback_data=f"q:m:{session_id}")])
    rows.extend([
        [InlineKeyboardButton(text=("⬅️ Другой опросник" if lang == "ru" else "⬅️ Another questionnaire"),
                              callback_data="q:l")],
        [InlineKeyboardButton(text=("🏠 В меню" if lang == "ru" else "🏠 To the menu"),
                              callback_data="menu:back")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _questionnaire_back_to_result_keyboard(session_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=("⬅️ К результату" if lang == "ru" else "⬅️ Back to result"),
                              callback_data=f"q:r:{session_id}")],
        [InlineKeyboardButton(text=("🏠 В меню" if lang == "ru" else "🏠 To the menu"),
                              callback_data="menu:back")],
    ])


async def _questionnaire_sum_or_none(definition: dict, session_id: int):
    """Returns (score, max_score, ordered_values) or None on any failure
    (incomplete/inconsistent responses, ineligible definition, non-sum
    scoring). Callers must fail closed to questionnaire_ux.not_available_text
    on None -- never guess, never show a partial score."""
    if not questionnaires.is_result_eligible(definition):
        return None
    responses = await get_questionnaire_responses(session_id)
    try:
        return questionnaires.compute_sum_score(definition, responses)
    except questionnaires.ScoringError:
        return None


async def _send_questionnaire_result(send, definition: dict, session_id: int, lang: str) -> None:
    result = await _questionnaire_sum_or_none(definition, session_id)
    if result is None:
        await send(questionnaire_ux.not_available_text(lang))
        return
    score, max_score, _values = result
    segments = definition.get("visualization", {}).get("segments", 7)
    await send(questionnaire_ux.result_text(score, max_score, lang, segments),
              reply_markup=_questionnaire_result_keyboard(session_id, lang))


async def _dass21_blocked(qid, uid: int) -> bool:
    """PR #55/#59 — extra FRESH gate for the exact DASS-21 definition:
    integrity (feature flag + file hash + identity) AND product authorization
    (owner OR active invited user behind DASS21_INVITED_USERS_ENABLED),
    re-checked on every touch -- no cached authorization, so revoking an
    invited user's access blocks the very next write/back/result. Non-DASS
    definitions are never affected. Failure is neutral: callers show the same
    not_available_text as every other refusal."""
    if not dass21_runtime.is_dass21_definition_id(qid):
        return False
    decision = await dass21_access.authorize_dass21_user(uid)
    return not decision.allowed


async def _send_dass21_result(send, definition: dict, session_id: int, lang: str) -> None:
    """PR #55 — exact DASS-21 completion: recompute the three subscale values
    from the owned stored responses through the validated clinical scoring
    path (explicit registry containing ONLY Dass21Scorer). Nothing is
    persisted; no overall total, no cutoffs/severity/diagnosis, no LLM. On any
    failure: no partial output, neutral unavailable text, internal log without
    question content."""
    try:
        # 1-4: owned session -> fresh gate -> complete validated responses ->
        # all three scores computed and validated. NOTHING is marked
        # completed until every step succeeds.
        session = await get_questionnaire_session(session_id)
        uid = session["user_id"]
        decision = await dass21_access.authorize_dass21_user(uid)
        if not decision.allowed:
            await send(questionnaire_ux.not_available_text(lang))
            return
        rows = await get_questionnaire_responses(session_id)
        responses = [clinical_scoring.ClinicalResponse(
            r["item_id"], r["answer_id"], int(r["answer_value"])) for r in rows]
        registry = clinical_scoring.ClinicalScorerRegistry()
        registry.register(dass21_scorer.Dass21Scorer())
        result = clinical_scoring.score_validated_clinical_definition(
            definition, _load_catalog_document(), responses, registry)
        # 5-6: only now mark completed, then render the complete result.
        await complete_questionnaire_session(session_id)
        recommendations = await _dass21_recommendation_options(uid)
        keyboard = _dass21_completion_keyboard(
            session_id, lang, recommendation_available=bool(recommendations))
        await send(questionnaire_ux.dass21_result_text(result.subscales, lang),
                   reply_markup=keyboard)
    except Exception:
        # Fail closed: session NOT completed (stays active/recoverable), no
        # partial output, neutral text, log without question content.
        logging.exception("dass21 scoring failed (session_id=%s)", session_id)
        await send(questionnaire_ux.not_available_text(lang))


async def _dass21_recompute_result_or_none(session: dict):
    """Workstream B (final pass) — the ONE shared registry-reload + fresh-
    authorization + validated-clinical-scoring recompute used by BOTH the
    DASS-21 discuss gate and the read-only back-to-result path, so the
    fail-closed DB-error boundary lives in exactly one place instead of two
    duplicated try/except blocks. Returns a discussion_adapters.
    DiscussionResult on success, or None on ANY failure -- including a real
    aiosqlite.Error from the authorization read (database.
    user_has_active_access) or the response fetch (get_questionnaire_
    responses). questionnaires.Registry._load already catches per-FILE
    problems (json.JSONDecodeError/OSError/DefinitionError, never raised),
    but its directory-level enumeration (Path.exists/Path.glob) is NOT
    wrapped there -- a real filesystem failure at that level (permission
    denied, a network-drive glitch, the directory vanishing mid-scan) can
    still raise OSError, so it is caught here too, at the ONE DASS-specific
    boundary, without touching the shared questionnaires.py module."""
    try:
        registry = _load_registry_fresh()
        definition = registry.get(session["questionnaire_id"])
        if definition is None or definition.get("version") != session["questionnaire_version"]:
            return None
        adapter = discussion_adapters.Dass21DiscussionAdapter()
        if not adapter.supports(definition):
            return None
        auth = await adapter.authorize(session)
        if not auth.allowed:
            return None
        responses = await get_questionnaire_responses(session_id=session["id"])
        return adapter.recompute_result(definition, _load_catalog_document(), responses, session)
    except (aiosqlite.Error, OSError):
        return None


async def _send_dass21_back_to_result(send, session: dict, lang: str,
                                      *, reply_markup_override=None) -> None:
    """Workstream B — read-only DASS-21 "back to result" (q:r on an already-
    completed DASS-21 session). Recomputes the three subscales fresh through
    the SAME Dass21DiscussionAdapter the discuss flow uses (fresh
    authorization + integrity + validated clinical-scoring recompute).
    NEVER mutates the session (no complete_questionnaire_session call --
    calling it again would be a second, spurious completion write) and NEVER
    calls the LLM. On any failure: neutral text, no partial output."""
    result = await _dass21_recompute_result_or_none(session)
    if result is None:
        await send(questionnaire_ux.not_available_text(lang))
        return
    keyboard = reply_markup_override
    if keyboard is None:
        recommendations = await _dass21_recommendation_options(session["user_id"])
        keyboard = _dass21_completion_keyboard(
            session["id"], lang, recommendation_available=bool(recommendations))
    await send(questionnaire_ux.dass21_result_text(result.subscales, lang), reply_markup=keyboard)


def _gad7_score(definition: dict, response_rows: list[dict]):
    """Run the exact GAD-7 scorer through the shared clinical validator."""
    responses = [clinical_scoring.ClinicalResponse(
        row["item_id"], row["answer_id"], int(row["answer_value"]))
        for row in response_rows]
    registry = clinical_scoring.ClinicalScorerRegistry()
    registry.register(gad7_core.Gad7Scorer())
    return clinical_scoring.score_validated_clinical_definition(
        definition, _load_catalog_document(), responses, registry)


async def _send_gad7_result(send, definition: dict, session_id: int, lang: str) -> None:
    """Validate, score, complete, and render one GAD-7 session fail-closed."""
    try:
        rows = await get_questionnaire_responses(session_id)
        result = _gad7_score(definition, rows)
        score = int(result.raw_total)
        await complete_questionnaire_session(session_id)
        await send(
            gad7_ux.result_text(
                score, gad7_core.band_label_ru(score), lang),
            reply_markup=_questionnaire_completion_keyboard(session_id, lang))
    except Exception:
        logging.exception("gad7 scoring failed (session_id=%s)", session_id)
        await send(questionnaire_ux.not_available_text(lang))


async def _gad7_recompute_result_or_none(session: dict):
    """Read-only exact-version GAD-7 reconstruction for history/report."""
    if session.get("status") != "completed":
        return None
    try:
        registry = _load_registry_fresh()
        definition = registry.get(session["questionnaire_id"])
        if (not gad7_core.is_gad7_definition(definition)
                or definition.get("version") != session["questionnaire_version"]):
            return None
        rows = await get_questionnaire_responses(session["id"])
        return definition, _gad7_score(definition, rows)
    except (aiosqlite.Error, OSError, ValueError, TypeError,
            clinical_scoring.ClinicalScoringError):
        return None


async def _send_gad7_historical_result(send, session: dict, lang: str,
                                       *, reply_markup=None) -> None:
    reconstructed = await _gad7_recompute_result_or_none(session)
    if reconstructed is None:
        await send(questionnaire_ux.not_available_text(lang))
        return
    _definition, result = reconstructed
    score = int(result.raw_total)
    await send(
        gad7_ux.result_text(
            score, gad7_core.band_label_ru(score), lang),
        reply_markup=(reply_markup
                      if reply_markup is not None
                      else _questionnaire_completion_keyboard(session["id"], lang)))


async def _questionnaire_gate(entity, uid: int, lang: str) -> bool:
    """Same two gates as _nav_gate (journal_guard THEN
    ensure_full_access_or_closed_test), in the same order. A separate
    function (not a call to _nav_gate itself) only so this module stays
    self-contained/greppable as its own evidence trail; behavior is
    identical."""
    target_message = entity.message if isinstance(entity, CallbackQuery) else entity
    decision, _ = await journal_guard(target_message, uid, lang)
    if decision == "crisis":
        if isinstance(entity, CallbackQuery):
            await entity.answer()
        return False
    if not await ensure_full_access_or_closed_test(entity, uid):
        return False
    return True


def _edit_or_answer(message):
    """PR #57 single-card UX: a `send` callable that EDITS the existing card
    in place (one editable message per questionnaire run -- old questions do
    not pile up in the chat).

    Exception contract (deliberately narrow -- unexpected failures must
    PROPAGATE, never be swallowed):
    - TelegramBadRequest "message is not modified": treated as success (the
      card already shows this content) -- no duplicate message is sent;
    - any other TelegramBadRequest (too old / can't be edited / not found):
      fall back to a fresh message, after best-effort disabling the stale
      card's keyboard so old buttons don't linger active;
    - anything else (network errors, programming errors): propagates.
    A failure of the FALLBACK send also propagates. Logs carry only the
    sanitized exception reason -- never the card/question text."""
    async def _send(text, **kw):
        edit_text = getattr(message, "edit_text", None)
        if edit_text is None:
            # capability detection, not error handling: some call sites (and
            # test fakes) hand in a message that cannot be edited at all.
            await message.answer(text, **kw)
            return
        try:
            await edit_text(text, **kw)
            return
        except TelegramBadRequest as exc:
            reason = str(exc)
            if "message is not modified" in reason.lower():
                return  # same content already on the card -- success, no-op
            logging.info("questionnaire card edit failed (%s); sending a new card",
                         type(exc).__name__)
        try:
            await message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass  # best-effort only: the old keyboard may already be gone
        await message.answer(text, **kw)
    return _send


async def _send_questionnaire_step(send, definition: dict, session_id: int, step: int, lang: str) -> None:
    """Send the item at `step`, or complete the session if none remains.
    `send` is message.answer / callback.message.answer, matching the existing
    project convention (see send_crisis's `send` parameter)."""
    item = questionnaires.get_item(definition, step)
    if item is None:
        if dass21_runtime.is_dass21_definition(definition):
            # PR #55: exact DASS-21 completion. Ordering is load-bearing --
            # the fresh gate, the complete validated responses and all three
            # scores are computed FIRST; only on full success does
            # _send_dass21_result mark the session completed. On any failure
            # the session stays active (recoverable/cancellable), no partial
            # result, neutral text. Never reaches the generic PR B path.
            await _send_dass21_result(send, definition, session_id, lang)
            return
        if gad7_core.is_gad7_definition(definition):
            await _send_gad7_result(send, definition, session_id, lang)
            return
        await complete_questionnaire_session(session_id)
        # PR B: kill-switch + eligibility gate on the completion branch. When
        # the flag is off (default) or the definition isn't eligible, this is
        # BYTE-FOR-BYTE PR A's completion screen -- never a score, never a
        # different keyboard.
        if config.QUESTIONNAIRE_INTERPRETATION_ENABLED and questionnaires.is_result_eligible(definition):
            await _send_questionnaire_result(send, definition, session_id, lang)
            return
        await send(questionnaire_ux.completion_text(lang),
                   reply_markup=_questionnaire_completion_keyboard(session_id, lang))
        return
    total = len(definition.get("items", []))
    if gad7_core.is_gad7_definition(definition):
        text = gad7_ux.question_text(
            step, total, item["text"], item.get("options", []), lang)
    else:
        text = questionnaire_ux.question_text(step, total, item["text"], lang,
                                              options=item.get("options"))
    keyboard = _questionnaire_item_keyboard(definition, session_id, step, item, lang)
    if len(text) > _QUESTIONNAIRE_CARD_MAXLEN:
        # Deterministic safe fallback (never a silent truncation of the
        # protected wording): drop the in-card legend and show the FULL labels
        # on the buttons instead -- the pre-#57 layout.
        text = (gad7_ux.question_text(
                    step, total, item["text"], [], lang)
                if gad7_core.is_gad7_definition(definition)
                else questionnaire_ux.question_text(step, total, item["text"], lang))
        keyboard = _questionnaire_full_label_keyboard(definition, session_id, step, item, lang)
    await send(text, reply_markup=keyboard)


async def _compatible_active_session(uid: int, definition: dict) -> dict | None:
    """The caller's own active session for this EXACT definition id+version,
    or None. Same compatibility rule cb_questionnaire_start already uses to
    decide resume-vs-refuse; shared here so every detail-screen entry point
    (cmd_dass21, cb_questionnaire_detail) offers Continue/Start-over
    identically instead of re-deriving the rule."""
    active = await get_active_questionnaire_session(uid)
    if (active and active["questionnaire_id"] == definition["id"]
            and active["questionnaire_version"] == definition["version"]):
        return active
    return None


@dp.message(Command("dass21"))
async def cmd_dass21(message: Message, state: FSMContext = None):
    """PR #55 — owner-only entry to the exact DASS-21 flow. Routes to the
    EXISTING q:d detail screen (never creates a session directly); every
    downstream step re-runs the same fresh gates. Disabled feature and
    non-owner get the SAME neutral text -- no existence disclosure."""
    uid = message.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(message, uid, lang):
        return
    await _clear_active_journal_if_leaving(state)
    qid = dass21_runtime.DASS21_DEFINITION_ID
    if await _dass21_blocked(qid, uid):
        await message.answer(questionnaire_ux.not_available_text(lang))
        return
    registry = _load_registry_fresh()
    definition = registry.get(qid)
    if (definition is None
            or not registry.combined_can_start(qid, _load_catalog_document())):
        await message.answer(questionnaire_ux.not_available_text(lang))
        return
    active_session = await _compatible_active_session(uid, definition)
    await message.answer(questionnaire_ux.detail_text(definition, lang),
                         reply_markup=_questionnaire_detail_keyboard(
                             qid, lang, active_session=active_session,
                             total_items=len(definition.get("items", []))))


@dp.message(Command("questionnaire"))
async def cmd_questionnaire(message: Message, state: FSMContext = None):
    uid = message.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(message, uid, lang):
        return
    if state is not None:
        await _clear_active_journal_if_leaving(state)
    catalog = await _available_questionnaire_catalog(uid)
    await message.answer(
        questionnaire_ux.list_text(lang),
        reply_markup=_questionnaire_list_keyboard(lang, catalog))


@dp.callback_query(F.data == "q:l")
async def cb_questionnaire_list(callback: CallbackQuery, state: FSMContext = None):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(callback, uid, lang):
        return
    if state is not None:
        await _clear_active_journal_if_leaving(state)
    catalog = await _available_questionnaire_catalog(uid)
    await _edit_or_answer(callback.message)(
        questionnaire_ux.list_text(lang),
        reply_markup=_questionnaire_list_keyboard(lang, catalog))
    await callback.answer()


@dp.callback_query(F.data == "q:t")
async def cb_questionnaire_another(callback: CallbackQuery, state: FSMContext = None):
    """"Другой тест" from a COMPLETED result/report card. Deliberately does
    NOT reuse q:l's behavior of editing callback.message in place -- that
    would silently overwrite/destroy the very completion card this button is
    attached to. Sends the identical catalog content, through the identical
    gates and business logic as q:l (_questionnaire_gate,
    _available_questionnaire_catalog, _questionnaire_list_keyboard), as a
    brand-new message instead, so the completed result stays in chat
    history untouched."""
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(callback, uid, lang):
        return
    if state is not None:
        await _clear_active_journal_if_leaving(state)
    catalog = await _available_questionnaire_catalog(uid)
    await callback.message.answer(
        questionnaire_ux.list_text(lang),
        reply_markup=_questionnaire_list_keyboard(lang, catalog))
    await callback.answer()


@dp.callback_query(F.data.startswith("q:c:"))
async def cb_questionnaire_category(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(callback, uid, lang):
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer()
        return
    category = parts[2]
    catalog = await _available_questionnaire_catalog(uid)
    entries = catalog.get(category, [])
    if category not in questionnaire_ux.CATALOG_CATEGORY_IDS or not entries:
        # Stale/forged/now-empty category: return to the fresh root. Never
        # render an empty category or disclose why an instrument disappeared.
        await _edit_or_answer(callback.message)(
            questionnaire_ux.list_text(lang),
            reply_markup=_questionnaire_list_keyboard(lang, catalog))
        await callback.answer()
        return

    await _edit_or_answer(callback.message)(
        questionnaire_ux.catalog_category_text(category, lang),
        reply_markup=_questionnaire_category_keyboard(entries, lang))
    await callback.answer()


@dp.callback_query(F.data.startswith("q:i:"))
async def cb_questionnaire_info(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(callback, uid, lang):
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer()
        return
    instrument_id = parts[2]
    document = _load_catalog_document()
    registry = _load_registry_fresh()
    available = next((entry for entry in
                      clinical_instrument_catalog.available_public_instruments(
                          document, registry)
                      if entry.instrument_id == instrument_id), None)
    ci = (clinical_instrument_catalog.get_catalog_instrument(document, instrument_id)
          if document is not None else None)
    if ci is None or available is None:
        # Old/stale info callbacks for blocked or removed methods fail closed;
        # the public catalog never advertises unavailable instruments.
        await _edit_or_answer(callback.message)(
            questionnaire_ux.not_available_text(lang),
            reply_markup=_catalog_nav_only_keyboard(lang))
        await callback.answer()
        return

    await _edit_or_answer(callback.message)(
        questionnaire_ux.instrument_info_text(ci, lang),
        reply_markup=_catalog_info_keyboard(
            available.category_ids[0], lang,
            start_definition_id=available.definition_id))
    await callback.answer()


@dp.callback_query(F.data.startswith("q:d:"))
async def cb_questionnaire_detail(callback: CallbackQuery, state: FSMContext = None):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(callback, uid, lang):
        return
    await _clear_active_journal_if_leaving(state)
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer()
        return
    qid = parts[2]
    registry = _load_registry_fresh()
    definition = registry.get(qid)
    if definition is None or definition.get("status") != "active" or definition.get("legal_status") == "restricted":
        await _edit_or_answer(callback.message)(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return
    # Clinical definitions (carrying clinical_instrument metadata OR mapped by a
    # manifest entry) must additionally pass the FRESH combined manifest-linkage
    # gate before their detail/start screen renders. Ordinary nonclinical
    # definitions are unaffected (validation returns NOT_CLINICAL). No internal
    # reason is ever disclosed -- same neutral not_available_text.
    manifest_document = _load_catalog_document()
    validation = registry.get_clinical_validation(qid, manifest_document)
    if (validation.status != clinical_definition_validator.ClinicalDefinitionStatus.NOT_CLINICAL
            and not registry.combined_can_start(qid, manifest_document)):
        await _edit_or_answer(callback.message)(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return
    if await _dass21_blocked(qid, uid):
        await _edit_or_answer(callback.message)(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return
    active = await get_active_questionnaire_session(uid)
    active_session = (active if active
                      and active["questionnaire_id"] == definition["id"]
                      and active["questionnaire_version"] == definition["version"]
                      else None)
    if active is not None and active_session is None:
        await _send_questionnaire_active_conflict(
            _edit_or_answer(callback.message), active, definition, lang)
        await callback.answer()
        return
    detail = (gad7_ux.detail_text(lang)
              if gad7_core.is_gad7_definition(definition)
              else questionnaire_ux.detail_text(definition, lang))
    await _edit_or_answer(callback.message)(
        detail,
        reply_markup=_questionnaire_detail_keyboard(
            qid, lang, active_session=active_session,
            total_items=len(definition.get("items", []))))
    await callback.answer()


@dp.callback_query(F.data.startswith("q:s:"))
async def cb_questionnaire_start(callback: CallbackQuery, state: FSMContext = None):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(callback, uid, lang):
        return
    await _clear_active_journal_if_leaving(state)
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer()
        return
    qid = parts[2]
    registry = _load_registry_fresh()
    manifest_document = _load_catalog_document()
    if not registry.combined_can_start(qid, manifest_document):
        # Covers: unknown id, draft, archived, restricted, or an invalid
        # (schema-broken/risk-bearing) definition -- AND, for a clinical /
        # manifest-linked definition, any non-VALID linkage (blocked/demoted
        # manifest, mapping/version/translation mismatch). All fail closed with
        # the SAME neutral message, never distinguishing the internal reason.
        # Ordinary nonclinical definitions behave exactly as before (combined
        # returns can_start for NOT_CLINICAL; a missing manifest is harmless).
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return
    if await _dass21_blocked(qid, uid):
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return
    definition = registry.get(qid)

    active = await get_active_questionnaire_session(uid)
    if active is None:
        session_id = await start_questionnaire_session_if_none_active(
            uid, definition["id"], definition["version"])
        if session_id is not None:
            await _send_questionnaire_step(_edit_or_answer(callback.message), definition, session_id, 0, lang)
            await callback.answer()
            return
        # Lost a concurrent race to create this user's first active session
        # (e.g. a double-tap dispatched as two overlapping updates) -- re-
        # resolve against whichever session actually won, exactly as if this
        # request had observed it as already active from the start.
        active = await get_active_questionnaire_session(uid)

    if active:
        if (active["questionnaire_id"] != definition["id"]
                or active["questionnaire_version"] != definition["version"]):
            await _send_questionnaire_active_conflict(
                _edit_or_answer(callback.message), active, definition, lang)
            await callback.answer()
            return
        await _send_questionnaire_step(_edit_or_answer(callback.message), definition, active["id"],
                                       active["current_index"], lang)
        await callback.answer()
        return

    # Exceptionally unlikely: the session that won the race was itself
    # cancelled/completed again before this re-read. Silent no-op, the same
    # convention every other "nothing left to safely act on" case in this
    # handler family already uses (e.g. _load_owned_active_session).
    await callback.answer()


async def _load_owned_active_session(session_id: int, uid: int):
    """Session-ownership check: load session; return None (silent no-op
    upstream) if it doesn't exist, belongs to a different user, or isn't
    active. Never distinguishes these cases to the caller -- same
    non-disclosure rule as the original PR #1 handler."""
    session = await get_questionnaire_session(session_id)
    if not session or session["user_id"] != uid or session["status"] != "active":
        return None
    return session


@dp.callback_query(F.data.startswith("q:v:"))
async def cb_questionnaire_resume_session(callback: CallbackQuery,
                                          state: FSMContext = None):
    """Resume one exact owned/current active session without creating state."""
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(callback, uid, lang):
        return
    await _clear_active_journal_if_leaving(state)
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer()
        return
    session_id = int(parts[2])
    session = await _load_owned_active_session(session_id, uid)
    current = await get_active_questionnaire_session(uid)
    if session is None or current is None or current["id"] != session_id:
        await callback.answer()
        return

    registry = _load_registry_fresh()
    manifest_document = _load_catalog_document()
    definition = registry.get(session["questionnaire_id"])
    if (definition is None
            or definition["version"] != session["questionnaire_version"]
            or not registry.combined_can_start(definition["id"], manifest_document)):
        await _edit_or_answer(callback.message)(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return
    if await _dass21_blocked(definition["id"], uid):
        await _edit_or_answer(callback.message)(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return

    session = await _load_owned_active_session(session_id, uid)
    current = await get_active_questionnaire_session(uid)
    if session is None or current is None or current["id"] != session_id:
        await callback.answer()
        return
    await _send_questionnaire_step(
        _edit_or_answer(callback.message), definition, session_id,
        session["current_index"], lang)
    await callback.answer()


@dp.callback_query(F.data.startswith("q:w:"))
async def cb_questionnaire_switch(callback: CallbackQuery, state: FSMContext = None):
    """Explicitly cancel the supplied current session and start a fresh target.

    Target governance is revalidated before the old session is touched. The
    source is then re-read and compared with the user's current active session
    immediately before cancellation, so stale cards cannot cancel a newer
    session.
    """
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(callback, uid, lang):
        return
    await _clear_active_journal_if_leaving(state)
    parts = callback.data.split(":")
    if (len(parts) != 4 or not parts[2].isdigit()
            or len(parts[3]) != 40
            or any(char not in "0123456789abcdef" for char in parts[3])):
        await callback.answer()
        return
    source_session_id = int(parts[2])
    target_token = parts[3]

    source = await _load_owned_active_session(source_session_id, uid)
    current = await get_active_questionnaire_session(uid)
    if source is None or current is None or current["id"] != source_session_id:
        await callback.answer()
        return

    registry = _load_registry_fresh()
    manifest_document = _load_catalog_document()
    target = _resolve_questionnaire_switch_target(registry, target_token)
    if target is None or not registry.combined_can_start(target["id"], manifest_document):
        await _edit_or_answer(callback.message)(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return
    if await _dass21_blocked(target["id"], uid):
        await _edit_or_answer(callback.message)(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return
    if (source["questionnaire_id"] == target["id"]
            and source["questionnaire_version"] == target["version"]):
        await callback.answer()
        return

    new_session_id = await switch_active_questionnaire_session(
        uid, source_session_id, target["id"], target["version"])
    if new_session_id is None:
        await callback.answer()
        return
    await _send_questionnaire_step(
        _edit_or_answer(callback.message), target, new_session_id, 0, lang)
    await callback.answer()


@dp.callback_query(F.data.startswith("q:a:"))
async def cb_questionnaire_answer(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    # Active-crisis gate FIRST -- before format/session/definition checks,
    # before storing anything, before advancing the session. Same invariant
    # as every other in-progress-flow step (emotion_step/cbt_step).
    decision, _ = await journal_guard(callback.message, uid, lang)
    if decision == "crisis":
        await callback.answer()
        return

    parts = callback.data.split(":")
    if len(parts) != 5 or not parts[2].isdigit() or not parts[3].isdigit():
        await callback.answer()
        return
    session_id, callback_step, answer_id = int(parts[2]), int(parts[3]), parts[4]

    session = await _load_owned_active_session(session_id, uid)
    if session is None:
        # Wrong user / unknown / non-active session: silent no-op -- showing
        # ANY message here would confirm to an attacker that a session with
        # this id exists at all.
        await callback.answer()
        return

    # Stale-callback protection: the callback's OWN step must match the
    # session's current step. A mismatch means the user pressed an option on
    # an older/already-answered screen (e.g. double-tap, or went back and the
    # old inline keyboard is still visible) -- do NOT save/advance; show the
    # neutral "no longer current" message and re-show the CURRENT question.
    if callback_step != session["current_index"]:
        registry = _load_registry_fresh()
        definition = registry.get(session["questionnaire_id"])
        if (definition is None
                or not registry.combined_can_answer(
                    session["questionnaire_id"], _load_catalog_document())
                or await _dass21_blocked(session["questionnaire_id"], uid)):
            await callback.message.answer(questionnaire_ux.not_available_text(lang))
            await callback.answer()
            return
        await callback.message.answer(questionnaire_ux.stale_answer_text(lang))
        await _send_questionnaire_step(_edit_or_answer(callback.message), definition, session_id,
                                       session["current_index"], lang)
        await callback.answer()
        return

    # Continuous validity re-check (not just stale-step detection): re-verify
    # on EVERY answer callback that the definition is still active/valid --
    # not only at session start. A definition's status can change between
    # session start and a later answer (archived/draft/restricted/schema
    # invalidated) -- fail closed: don't save, don't advance, end gracefully.
    registry = _load_registry_fresh()
    # Fresh combined re-check: Core validity AND (for clinical/manifest-linked
    # definitions) a still-VALID manifest linkage. A mid-session manifest
    # demotion / mapping change / version or translation change fails closed
    # here -- no answer saved, no advance, neutral message, no reason disclosed.
    if (not registry.combined_can_answer(session["questionnaire_id"], _load_catalog_document())
            or await _dass21_blocked(session["questionnaire_id"], uid)):
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return
    definition = registry.get(session["questionnaire_id"])
    if definition["version"] != session["questionnaire_version"]:
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return

    item = questionnaires.get_item(definition, session["current_index"])
    if item is None:
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return
    option = questionnaires.find_option(item, answer_id)
    if option is None:
        # answer_id doesn't belong to the current item: malformed/tampered
        # callback_data, same silent-no-op class as the wrong-user case.
        await callback.answer()
        return

    await record_questionnaire_response(
        uid, session_id, definition["id"], item["id"], option["id"], option["value"])
    next_step = session["current_index"] + 1
    await advance_questionnaire_session(session_id, next_step)
    # PR #57: the next question EDITS this same card, which also replaces the
    # old keyboard -- no separate edit_reply_markup(None) call needed.
    await _send_questionnaire_step(_edit_or_answer(callback.message), definition, session_id, next_step, lang)
    await callback.answer()


@dp.callback_query(F.data.startswith("q:b:"))
async def cb_questionnaire_back(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(callback, uid, lang):
        return
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer()
        return
    session_id = int(parts[2])

    session = await _load_owned_active_session(session_id, uid)
    if session is None:
        await callback.answer()
        return

    # Back changes persisted session state (current_index), so for a clinical/
    # manifest-linked session it must pass the FRESH combined gate (Core
    # can_answer AND still-VALID linkage) before moving. A mid-session manifest
    # demotion / mapping / version / translation change fails closed here: no
    # backward movement, session stays active, neutral message, no reason
    # disclosed. Ordinary nonclinical sessions behave exactly as before
    # (combined returns can_answer for NOT_CLINICAL).
    registry = _load_registry_fresh()
    if (not registry.combined_can_answer(session["questionnaire_id"], _load_catalog_document())
            or await _dass21_blocked(session["questionnaire_id"], uid)):
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return
    definition = registry.get(session["questionnaire_id"])

    prev_step = max(0, session["current_index"] - 1)
    await advance_questionnaire_session(session_id, prev_step)
    await _send_questionnaire_step(_edit_or_answer(callback.message), definition, session_id, prev_step, lang)
    await callback.answer()


def _questionnaire_paused_keyboard(qid: str, session_id: int, lang: str) -> InlineKeyboardMarkup:
    # "Продолжить" reuses q:s:<qid> UNCHANGED -- cb_questionnaire_start
    # already resumes from get_active_questionnaire_session(uid) when a
    # compatible active session exists, so pausing doesn't need (and must
    # not invent) a second resume mechanism. "Прервать" reuses q:x:<sid>
    # UNCHANGED -- the ONLY place q:x is still user-reachable, since the
    # live question card no longer offers it (see _questionnaire_nav_row).
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=("▶️ Продолжить" if lang == "ru" else "▶️ Continue"),
                              callback_data=f"q:s:{qid}")],
        [InlineKeyboardButton(text=("✖️ Прервать" if lang == "ru" else "✖️ Cancel"),
                              callback_data=f"q:x:{session_id}")],
    ])


@dp.callback_query(F.data.startswith("q:p:"))
async def cb_questionnaire_pause(callback: CallbackQuery):
    """Pause / continue later: no-op on session state (current_index already
    persists the resume point on every answer) -- transforms the LIVE
    question card in place into a paused-state card via the shared
    _edit_or_answer path, the SAME edit-with-fallback mechanism every other
    questionnaire screen already uses (its own docstring documents the exact
    narrow-TelegramBadRequest-then-new-message contract; not reimplemented
    here). Never requires the user to type a command -- the paused card's
    own buttons are the only continue/cancel path."""
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(callback, uid, lang):
        return
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer()
        return
    session_id = int(parts[2])

    session = await _load_owned_active_session(session_id, uid)
    if session is None:
        await callback.answer()
        return

    await _edit_or_answer(callback.message)(
        questionnaire_ux.paused_text(lang),
        reply_markup=_questionnaire_paused_keyboard(
            session["questionnaire_id"], session_id, lang))
    await callback.answer()


@dp.callback_query(F.data.startswith("q:x:"))
async def cb_questionnaire_cancel(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(callback, uid, lang):
        return
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer()
        return
    session_id = int(parts[2])

    session = await _load_owned_active_session(session_id, uid)
    if session is None:
        await callback.answer()
        return

    await cancel_questionnaire_session(session_id)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(questionnaire_ux.cancelled_text(lang))
    await callback.answer()


@dp.callback_query(F.data.startswith("q:n:"))
async def cb_questionnaire_restart(callback: CallbackQuery):
    """Начать заново -- the ONE intentional destructive reset of an
    unfinished questionnaire (owner-review UX correction). Re-runs exactly
    the same access/clinical/DASS gates cb_questionnaire_start uses to begin
    a session, so a restart can never create a session the ordinary start
    path itself would refuse. The gate check happens BEFORE the existing
    session is cancelled -- a failed gate leaves the original session
    untouched (active, same current_index), never destroyed for nothing.

    Only after that does it replace the caller's OWN session with a fresh one
    at step 0, via switch_active_questionnaire_session -- the SAME atomic
    (BEGIN IMMEDIATE, re-verified) primitive cb_questionnaire_switch (q:w:)
    uses, here targeting the same questionnaire the source session was
    already for. This closes the race a plain cancel-then-start would leave
    open under concurrent dispatch (two overlapping restarts, or a restart
    racing a fresh q:s: start), the same way q:w: is already race-safe.
    Old-session ownership isolation falls out of reusing this primitive --
    once cancelled, the old session_id fails _load_owned_active_session's
    status=='active' check, so any stale q:a/q:b/q:p/q:x/q:n callback still
    carrying it is a silent no-op, same as any other superseded session
    today."""
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(callback, uid, lang):
        return
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer()
        return
    session_id = int(parts[2])

    session = await _load_owned_active_session(session_id, uid)
    if session is None:
        await callback.answer()
        return

    qid = session["questionnaire_id"]
    registry = _load_registry_fresh()
    manifest_document = _load_catalog_document()
    if not registry.combined_can_start(qid, manifest_document):
        await _edit_or_answer(callback.message)(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return
    if await _dass21_blocked(qid, uid):
        await _edit_or_answer(callback.message)(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return
    definition = registry.get(qid)

    new_session_id = await switch_active_questionnaire_session(
        uid, session_id, definition["id"], definition["version"])
    if new_session_id is None:
        # Lost a concurrent race for this exact session (e.g. it was already
        # switched/cancelled/restarted by another in-flight callback) --
        # silent no-op, the same convention cb_questionnaire_switch already
        # uses for this exact return value.
        await callback.answer()
        return
    # The immediate re-render below (_edit_or_answer -> edit_text) replaces
    # this card's text AND keyboard together -- no separate best-effort
    # edit_reply_markup(None) needed first; see _edit_or_answer's own narrow
    # TelegramBadRequest-then-fallback contract, unchanged.
    await _send_questionnaire_step(_edit_or_answer(callback.message), definition, new_session_id, 0, lang)
    await callback.answer()


# ── PR B — result / calculations / explanation callbacks ────────────────────
# Callback format: q:r:<sid> result, q:k:<sid> calculations, q:e:<sid> scale
# explanation -- all <=64 bytes, no item_id embedded (same convention as
# q:b/q:p/q:x above). Gate order for each, identical structure to every other
# questionnaire handler:
#   1. journal_guard (via _questionnaire_gate)
#   2. ensure_full_access_or_closed_test (via _questionnaire_gate)
#   3. session ownership (_load_owned_active_session... but result screens are
#      reachable AFTER completion, so ownership is checked against the
#      session row directly, not "active" status -- see _load_owned_session)
#   4. kill-switch check (config.QUESTIONNAIRE_INTERPRETATION_ENABLED)
#   5. definition validity (reload fresh from disk via _load_registry_fresh)
#   6. eligibility check (legal_status/result_policy via
#      questionnaires.is_result_eligible)
#   7. only then render/send content

async def _load_owned_session(session_id: int, uid: int):
    """Like _load_owned_active_session, but does NOT require status=='active'
    -- result/calculations/explanation screens are shown AFTER a session is
    completed, so they must still work post-completion. Still enforces
    ownership (same silent no-op non-disclosure convention). A real
    aiosqlite.Error from the session read (shared by q:r/q:k/q:e/q:o and
    both q:m entry points, generic and DASS alike) is treated identically to
    "not found" -- every caller already fails closed to a silent no-op or
    not_available_text on None, so this is a uniform hardening, not a new
    behavior branch."""
    try:
        session = await get_questionnaire_session(session_id)
    except aiosqlite.Error:
        return None
    if not session or session["user_id"] != uid:
        return None
    return session


@dp.callback_query(F.data.startswith("q:r:"))
async def cb_questionnaire_result(callback: CallbackQuery, state: FSMContext = None):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(callback, uid, lang):          # 1, 2
        return
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer()
        return
    session_id = int(parts[2])

    session = await _load_owned_session(session_id, uid)            # 3
    if session is None:
        await callback.answer()
        return

    if dass21_runtime.is_dass21_definition_id(session["questionnaire_id"]):
        await _clear_active_journal_if_leaving(state)
        await _send_dass21_back_to_result(_edit_or_answer(callback.message), session, lang)
        await callback.answer()
        return

    if gad7_core.is_gad7_definition_id(session["questionnaire_id"]):
        await _clear_dass21_discussion(state)
        await _send_gad7_historical_result(
            _edit_or_answer(callback.message), session, lang)
        await callback.answer()
        return

    await _clear_dass21_discussion(state)

    if not config.QUESTIONNAIRE_INTERPRETATION_ENABLED:              # 4
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return

    registry = _load_registry_fresh()                                # 5
    definition = registry.get(session["questionnaire_id"])
    if definition is None:
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return

    if not questionnaires.is_result_eligible(definition):             # 6
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return

    await _send_questionnaire_result(callback.message.answer, definition, session_id, lang)  # 7
    await callback.answer()


@dp.callback_query(F.data.startswith("q:k:"))
async def cb_questionnaire_calculations(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(callback, uid, lang):          # 1, 2
        return
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer()
        return
    session_id = int(parts[2])

    session = await _load_owned_session(session_id, uid)            # 3
    if session is None:
        await callback.answer()
        return

    if not config.QUESTIONNAIRE_INTERPRETATION_ENABLED:              # 4
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return

    registry = _load_registry_fresh()                                # 5
    definition = registry.get(session["questionnaire_id"])
    if definition is None:
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return

    if not questionnaires.is_result_eligible(definition):             # 6
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return

    result = await _questionnaire_sum_or_none(definition, session_id)  # 7
    if result is None:
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return
    score, max_score, values = result
    await callback.message.answer(
        questionnaire_ux.calculations_text(values, score, max_score, lang),
        reply_markup=_questionnaire_back_to_result_keyboard(session_id, lang))
    await callback.answer()


@dp.callback_query(F.data.startswith("q:e:"))
async def cb_questionnaire_explanation(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(callback, uid, lang):          # 1, 2
        return
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer()
        return
    session_id = int(parts[2])

    session = await _load_owned_session(session_id, uid)            # 3
    if session is None:
        await callback.answer()
        return

    if not config.QUESTIONNAIRE_INTERPRETATION_ENABLED:              # 4
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return

    registry = _load_registry_fresh()                                # 5
    definition = registry.get(session["questionnaire_id"])
    if definition is None:
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return

    if not questionnaires.is_result_eligible(definition):             # 6
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return

    main_text = definition.get("scale_explanations", {}).get("main")  # 7
    if not main_text:
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return
    await callback.message.answer(
        questionnaire_ux.explanation_text(main_text, lang),
        reply_markup=_questionnaire_back_to_result_keyboard(session_id, lang))
    await callback.answer()


# ── PR C1 — specialist report (self-only, no LLM) ────────────────────────────
# Callback format: q:o:<sid> -- <=64 bytes, no item_id embedded (same
# convention as q:r/q:k/q:e/q:b/q:p/q:x). Gate order, identical structure to
# q:r/q:k/q:e:
#   1. journal_guard (via _questionnaire_gate)
#   2. ensure_full_access_or_closed_test (via _questionnaire_gate)
#   3. session ownership (_load_owned_session -- NOT _load_owned_active_session,
#      since the report must still be viewable after the session completes)
#   4. definition validity (reload fresh from disk via _load_registry_fresh)
#   5. answers assembled in DEFINITION item order (not raw SQL row order),
#      latest response per item wins on duplicates, fail closed to
#      questionnaire_ux.not_available_text on any item/answer id drift
#   6. score line included ONLY if config.QUESTIONNAIRE_INTERPRETATION_ENABLED
#      AND questionnaires.is_result_eligible(definition) AND
#      questionnaires.compute_sum_score succeeds -- otherwise the report still
#      renders all answers, just without a score line
#
# No LLM call anywhere in this path -- pure deterministic string building from
# already-stored data. This is a SEPARATE, self-only (requester_uid ==
# target_uid) mechanism from review_pack.py's reviewer-initiated, role-gated
# path -- see CLINICAL_BOUNDARY.md §0.5 point 6. No new review_pack coupling
# is introduced here.

def _build_specialist_report_answers(definition: dict, responses: list[dict]) -> list[str] | None:
    """Returns one rendered "question -- answer" line per item, in DEFINITION
    item order, using the LATEST recorded response for an item if duplicates
    exist (later rows in `responses`, which is already oldest-first from
    get_questionnaire_responses, overwrite earlier ones in this dict so the
    last write for a given item_id wins). Returns None (fail closed) if any
    item has no response, or a response's item_id/answer_id no longer matches
    the current definition -- never guesses."""
    latest_by_item: dict[str, dict] = {}
    for r in responses:
        latest_by_item[r["item_id"]] = r  # later rows overwrite -- latest wins

    lines = []
    for item in definition.get("items", []):
        item_id = item["id"]
        response = latest_by_item.get(item_id)
        if response is None:
            return None
        option = questionnaires.find_option(item, response["answer_id"])
        if option is None:
            return None
        lines.append(f"{item['text']} -- {option['label']}")
    return lines


@dp.callback_query(F.data.startswith("q:o:"))
async def cb_questionnaire_specialist_report(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(callback, uid, lang):           # 1, 2
        return
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer()
        return
    session_id = int(parts[2])

    session = await _load_owned_session(session_id, uid)             # 3
    if session is None:
        await callback.answer()
        return

    registry = _load_registry_fresh()                                 # 4
    definition = registry.get(session["questionnaire_id"])
    if definition is None:
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return
    if definition["version"] != session["questionnaire_version"]:
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return

    responses = await get_questionnaire_responses(session_id)
    answer_lines = _build_specialist_report_answers(definition, responses)  # 5
    if answer_lines is None:
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return

    score_line = None                                                  # 6
    if config.QUESTIONNAIRE_INTERPRETATION_ENABLED and questionnaires.is_result_eligible(definition):
        try:
            score, max_score, _values = questionnaires.compute_sum_score(definition, responses)
            if lang == "ru":
                score_line = f"Результат: {score} / {max_score}"
            else:
                score_line = f"Result: {score} / {max_score}"
        except questionnaires.ScoringError:
            score_line = None

    # Owner-review UX correction: DASS-21 doesn't use the generic sum-score
    # path above (is_result_eligible always rejects the real definition by
    # design), so its report had no numeric summary at all. Reuse the SAME
    # validated, freshly-authorized recompute the result/discuss screens use
    # -- never a second scoring algorithm, never persisted. On any failure
    # (auth revoked, integrity broken, not actually completed yet) this is
    # simply None and the report renders exactly as it does today (answers
    # only) -- not a new failure mode, no partial/guessed numbers.
    subscale_lines = None
    if dass21_runtime.is_dass21_definition_id(session["questionnaire_id"]):
        dass_result = await _dass21_recompute_result_or_none(session)
        if dass_result is not None:
            dep = dass_result.subscales["depression"]
            anx = dass_result.subscales["anxiety"]
            stress = dass_result.subscales["stress"]
            subscale_lines = (
                [f"Депрессия: {dep}", f"Тревога: {anx}", f"Стресс: {stress}"] if lang == "ru"
                else [f"Depression: {dep}", f"Anxiety: {anx}", f"Stress: {stress}"])

    if gad7_core.is_gad7_definition_id(session["questionnaire_id"]):
        reconstructed = await _gad7_recompute_result_or_none(session)
        if reconstructed is None:
            await callback.message.answer(questionnaire_ux.not_available_text(lang))
            await callback.answer()
            return
        _gad_definition, gad_result = reconstructed
        gad_score = int(gad_result.raw_total)
        band = gad7_core.band_label_ru(gad_score)
        subscale_lines = (
            [f"Общий балл: {gad_score} / 21",
             f"Выраженность тревожных симптомов: {band}",
             "Период оценки: последние 2 недели"] if lang == "ru"
            else [f"Total score: {gad_score} / 21",
                  f"Anxiety symptom level: {band}",
                  "Reference period: past 2 weeks"])

    completed_at = session.get("completed_at") if isinstance(session, dict) else None

    report = questionnaire_ux.specialist_report_text(
        definition["title"], completed_at, answer_lines, score_line, lang,
        subscale_lines=subscale_lines)
    await callback.message.answer(report)
    await callback.answer()


# ── Questionnaire discussion ────────────────────────────────────────────────
# Generic questionnaire q:m keeps the existing fixed topic flow below.
# Bare q:m:<sid> for DASS now enters an ephemeral multi-turn FSM binding; each
# typed turn then travels through pipeline(), whose crisis/risk checks precede
# a fresh owned-session reload and validated DASS recomputation. Historical
# four-part DASS topic callbacks remain accepted for already-sent keyboards,
# but no current DASS screen renders those buttons.
#
# Gate order (identical structure to q:r/q:k/q:e/q:o above), for BOTH the bare
# menu and every topic callback:
#   1. journal_guard (via _questionnaire_gate)
#   2. ensure_full_access_or_closed_test (via _questionnaire_gate)
#   3. session ownership (_load_owned_session -- reachable after completion)
#   4. kill-switch check (config.QUESTIONNAIRE_INTERPRETATION_ENABLED)
#   5. definition validity (reload fresh from disk via _load_registry_fresh,
#      version must match the session's recorded questionnaire_version)
#   6. eligibility check (questionnaires.is_result_eligible)
# The bare q:m:<sid> menu follows the EXACT same six-step chain as the topic
# callbacks (not a looser check) -- it leads directly into eligible topics, so
# it must be gated as strictly as they are.
#
# This is the FIRST production caller of traced_response_builder (PR #43):
# persist_trace failure, build_response failure (DiscussBuildFailed), and
# validator rejection (DiscussOutputRejected) all degrade to the SAME
# neutral_fallback text -- one shared fallback per caller, by construction.

class DiscussBuildFailed(Exception):
    """Raised by _discuss_build_response when the LLM call itself fails.
    Never caught locally -- propagates to traced_response_builder, which
    routes it to neutral_fallback. No fallback text is ever produced here."""


class DiscussOutputRejected(Exception):
    """Raised by _discuss_build_response when validate_response_with_context
    rejects the generated reply. Never caught locally -- propagates to
    traced_response_builder, which routes it to neutral_fallback."""


_GENERIC_DISCUSS_TOPICS = frozenset({"why", "next", "specialist"})
_DASS21_DISCUSS_TOPICS = frozenset({"measures", "relate", "next", "specialist"})
_ALL_DISCUSS_TOPIC_TOKENS = _GENERIC_DISCUSS_TOPICS | _DASS21_DISCUSS_TOPICS


def _dass21_discuss_keyboard(session_id: int, lang: str, *,
                             recommendation_available: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if recommendation_available:
        rows.append([InlineKeyboardButton(
            text=("🧪 Подобрать тест" if lang == "ru" else "🧪 Choose a questionnaire"),
            callback_data=f"q:pick:{session_id}")])
    rows.append([InlineKeyboardButton(
        text=("⬅️ Назад к результату" if lang == "ru" else "⬅️ Back to result"),
        callback_data=f"q:r:{session_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _discuss_menu_keyboard(session_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=("Почему так вышло?" if lang == "ru" else "Why did this come out this way?"),
            callback_data=f"q:m:{session_id}:why")],
        [InlineKeyboardButton(
            text=("Что можно сделать дальше?" if lang == "ru" else "What can I do next?"),
            callback_data=f"q:m:{session_id}:next")],
        [InlineKeyboardButton(
            text=("Вопросы специалисту" if lang == "ru" else "Questions for a specialist"),
            callback_data=f"q:m:{session_id}:specialist")],
        [InlineKeyboardButton(
            text=("⬅️ Назад к результату" if lang == "ru" else "⬅️ Back to result"),
            callback_data=f"q:r:{session_id}")],
        [InlineKeyboardButton(
            text=("🏠 В меню" if lang == "ru" else "🏠 To the menu"),
            callback_data="menu:back")],
    ])


async def _discuss_gate_and_load(session: dict, lang: str):
    """Steps 4-6 of the gate chain (kill-switch, definition validity,
    eligibility) PLUS scoring, given an ALREADY-loaded, ALREADY-owned session
    (step 3 -- _load_owned_session -- is the caller's responsibility, same as
    every other q:r/q:k/q:e/q:o handler, so that an ownership failure stays a
    SILENT no-op and is never conflated with "not available"). Returns
    (definition, score, max_score, intensity) on success, or None on ANY
    failure -- caller must send questionnaire_ux.not_available_text and
    return. Never calls the LLM or traced_response_builder; this is pure
    gating + scoring, identical in spirit to _questionnaire_sum_or_none."""
    if not config.QUESTIONNAIRE_INTERPRETATION_ENABLED:                   # 4
        return None

    registry = _load_registry_fresh()                                     # 5
    definition = registry.get(session["questionnaire_id"])
    if definition is None:
        return None
    if definition["version"] != session["questionnaire_version"]:
        return None

    if not questionnaires.is_result_eligible(definition):                 # 6
        return None

    responses = await get_questionnaire_responses(session_id=session["id"])
    try:
        score, max_score, _values = questionnaires.compute_sum_score(definition, responses)
    except questionnaires.ScoringError:
        return None
    intensity = questionnaire_ux.intensity_label(score, max_score, lang)
    return definition, score, max_score, intensity


async def _dass21_discuss_gate_and_load(session: dict, lang: str):
    """Workstream B — DASS-21 counterpart to _discuss_gate_and_load.
    questionnaires.is_result_eligible always rejects the real (non-synthetic)
    DASS-21 definition by design, so DASS-21 discussion cannot reuse the
    generic gate above; it needs its own fresh authorization + recompute,
    mirroring _send_dass21_result's ordering: kill-switch -> fresh registry
    reload + version match -> fresh product authorization (integrity + owner/
    invited, re-run on every call, no cache) -> completed-status requirement
    -> the three subscales recomputed through the SAME validated clinical-
    scoring path the completion screen uses (clinical_scoring.score_
    validated_clinical_definition + the sole registered Dass21Scorer).
    Returns a discussion_adapters.DiscussionResult on success, or None on ANY
    failure -- caller must send questionnaire_ux.not_available_text and
    return, never a partial result. The registry/DB fail-closed boundary
    itself lives in the shared _dass21_recompute_result_or_none (used
    identically by _send_dass21_back_to_result)."""
    if not config.DASS21_DISCUSSION_ENABLED:
        return None
    return await _dass21_recompute_result_or_none(session)


def _is_bare_discuss_menu_data(data: str) -> bool:
    """True only for q:m:<sid> (exactly 3 parts, digit session id) -- NOT for
    a topic callback q:m:<sid>:<topic>, so this filter and the topic filter
    below are mutually exclusive and aiogram never has to pick between two
    matching handlers for the same callback_data."""
    if not data.startswith("q:m:"):
        return False
    parts = data.split(":")
    return len(parts) == 3


def _is_discuss_topic_data(data: str) -> bool:
    """Syntax-only filter: q:m:<sid>:<topic> with topic in the UNION of the
    generic and DASS-21 topic sets. This is deliberately loose -- it only
    proves the callback COULD be a discuss-topic callback for SOME adapter.
    Which exact set applies is an adapter-specific decision made after the
    session is loaded (see cb_questionnaire_discuss_topic), so a DASS session
    tapped with a generic-only topic (or vice versa) is rejected there, not
    here."""
    if not data.startswith("q:m:"):
        return False
    parts = data.split(":")
    return len(parts) == 4 and parts[3] in _ALL_DISCUSS_TOPIC_TOKENS


@dp.callback_query(lambda c: _is_bare_discuss_menu_data(c.data or ""))
async def cb_questionnaire_discuss_menu(callback: CallbackQuery, state: FSMContext = None):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(callback, uid, lang):            # 1, 2
        return
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer()
        return
    session_id = int(parts[2])

    session = await _load_owned_session(session_id, uid)                  # 3
    if session is None:
        # Silent no-op -- same non-disclosure convention as q:r/q:k/q:e/q:o.
        await callback.answer()
        return

    is_dass = dass21_runtime.is_dass21_definition_id(session["questionnaire_id"])
    dass_result = None
    if is_dass:
        dass_result = await _dass21_discuss_gate_and_load(session, lang)
        ok = dass_result is not None
    else:
        await _clear_dass21_discussion(state)
        ok = (await _discuss_gate_and_load(session, lang)) is not None    # 4-6
    if not ok:
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return

    if is_dass:
        if state is not None:
            await state.set_state(Dass21Discussion.active)
            await state.update_data(dass21_session_id=session_id)
        recommendations = await _dass21_recommendation_options(uid)
        await _edit_or_answer(callback.message)(
            questionnaire_ux.dass21_discussion_intro_text(dass_result.subscales, lang),
            reply_markup=_dass21_discuss_keyboard(
                session_id, lang, recommendation_available=bool(recommendations)))
        await callback.answer()
        return

    # Generic questionnaire discussion remains the existing deterministic menu.
    await callback.message.answer(
        questionnaire_ux.discuss_menu_text(lang),
        reply_markup=_discuss_menu_keyboard(session_id, lang))
    await callback.answer()


def _dass21_picker_keyboard(session_id: int, options: dict[str, dict],
                            lang: str) -> InlineKeyboardMarkup:
    labels = {area_id: (ru if lang == "ru" else en)
              for area_id, _category, ru, en in _DASS21_RECOMMENDATION_AREAS}
    rows = [[InlineKeyboardButton(
        text=labels[area_id], callback_data=f"q:pick:{session_id}:{area_id}")]
        for area_id in labels if area_id in options]
    if rows:
        rows.append([InlineKeyboardButton(
            text=("Не знаю" if lang == "ru" else "I'm not sure"),
            callback_data=f"q:pick:{session_id}:unknown")])
    rows.append([InlineKeyboardButton(
        text=("⬅️ Назад к результату" if lang == "ru" else "⬅️ Back to result"),
        callback_data=f"q:r:{session_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _dass21_picker_text(lang: str) -> str:
    if lang == "en":
        return ("🧪 What would be useful to explore in more detail?\n\n"
                "DASS-21 provided an overall snapshot of how you have been feeling.\n\n"
                "Choose what concerns you most right now, and I will suggest one suitable "
                "questionnaire to assess that area in more detail.")
    return ("🧪 Что стоит уточнить подробнее?\n\n"
            "DASS-21 дал общий срез твоего состояния.\n\n"
            "Выбери, что сейчас беспокоит тебя больше всего, и я предложу один подходящий "
            "опросник, чтобы подробнее оценить эту область.")


def _dass21_recommendation_keyboard(session_id: int, entry: dict,
                                    lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=(("Начать " + entry["title_ru"]) if lang == "ru"
                  else ("Start " + entry["title_en"])),
            callback_data=f"q:d:{entry['definition_id']}")],
        [InlineKeyboardButton(
            text=("⬅️ Назад" if lang == "ru" else "⬅️ Back"),
            callback_data=f"q:pick:{session_id}")],
    ])


def _dass21_recommendation_text(entry: dict, lang: str) -> str:
    title = entry["title_ru"] if lang == "ru" else entry["title_en"]
    if lang == "en":
        return (f"🧪 {title}\n\nThis approved questionnaire is available to explore the "
                "selected area in more detail. Its result will remain an aid for "
                "self-observation, not a diagnosis.")
    return (f"🧪 {title}\n\nЭтот доступный проверенный опросник поможет подробнее "
            "оценить выбранную область. Его результат остаётся ориентиром для "
            "самонаблюдения, а не диагнозом.")


@dp.callback_query(F.data.startswith("q:pick:"))
async def cb_dass21_pick_questionnaire(callback: CallbackQuery,
                                       state: FSMContext = None):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(callback, uid, lang):
        return
    parts = callback.data.split(":")
    if len(parts) not in (3, 4) or not parts[2].isdigit():
        await callback.answer()
        return
    session_id = int(parts[2])
    session = await _load_owned_completed_history_dass(session_id, uid)
    if session is None:
        await callback.answer()
        return
    dass_result = await _dass21_recompute_result_or_none(session)
    if dass_result is None:
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return
    options = await _dass21_recommendation_options(uid)
    if not options:
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return

    if len(parts) == 3:
        # Conservative V1: DASS scores alone never auto-select an instrument.
        await _edit_or_answer(callback.message)(
            _dass21_picker_text(lang),
            reply_markup=_dass21_picker_keyboard(session_id, options, lang))
        await callback.answer()
        return

    area_id = parts[3]
    if area_id == "unknown":
        if state is not None:
            await state.set_state(Dass21Discussion.active)
            await state.update_data(dass21_session_id=session_id)
        await _edit_or_answer(callback.message)(
            questionnaire_ux.dass21_unknown_area_text(lang),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text=("⬅️ Назад к результату" if lang == "ru" else "⬅️ Back to result"),
                    callback_data=f"q:r:{session_id}")]]))
        await callback.answer()
        return

    entry = options.get(area_id)
    if entry is None:
        await callback.answer()
        return
    await _edit_or_answer(callback.message)(
        _dass21_recommendation_text(entry, lang),
        reply_markup=_dass21_recommendation_keyboard(session_id, entry, lang))
    await callback.answer()


async def _discuss_build_response(title: str, score: int, max_score: int,
                                   intensity: str, topic_id: str, lang: str) -> str:
    """Strictly raise-or-return-valid-text. NEVER sends messages, NEVER
    returns fallback text, NEVER catches its own failures and silently
    converts them to a local fallback string. Only two failure outcomes:
    DiscussBuildFailed (LLM call itself failed) or DiscussOutputRejected
    (validator rejected the generated text) -- both propagate uncaught to
    traced_response_builder (PR #43), which routes both to neutral_fallback."""
    prompt_text = questionnaire_ux.discuss_topic_prompt(
        title, score, max_score, intensity, topic_id, lang)
    messages = [
        {"role": "system", "content": get_system_prompt("open_chat", lang)},
        {"role": "user", "content": prompt_text},
    ]
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini", messages=messages, temperature=0.65, max_tokens=300,
        )
        answer = response.choices[0].message.content
    except Exception as e:
        raise DiscussBuildFailed(f"LLM call failed: {type(e).__name__}") from e

    # Deterministic, neutral risk context -- there is no user-typed text in
    # this flow, so this is NOT detect_risk output; it is a fixed, empty-risk
    # shape matching risk_detector.detect_risk's real return keys
    # (score/level/categories/implicit/ambiguous_phrases), constructed so the
    # validator's risk-gated side-checks (which only fire on
    # level in medium/high/critical, or a truthy ambiguous_phrases) stay
    # dormant -- proven empirically in
    # tests/test_questionnaire_discuss.py::test_discuss_validator_receives_deterministic_context.
    neutral_risk = {"score": 0, "level": "low", "categories": [],
                     "implicit": False, "ambiguous_phrases": []}
    is_safe, reason = validate_response_with_context(answer, prompt_text, neutral_risk, lang)
    if not is_safe:
        raise DiscussOutputRejected(f"validator rejected: {reason}")
    return answer


# Named bound (not a repeated magic number): the ONE hard wall-clock ceiling
# for the whole DASS discuss LLM operation, used both as the per-call SDK
# timeout hint and as asyncio.wait_for's outer timeout. Honest retry note:
# the shared `client` object still has its default max_retries=2 (NOT
# disabled -- an earlier with_options(max_retries=0) attempt was reverted
# because it silently broke every test that monkeypatches
# bot.client.chat.completions.create, since with_options() returns a
# DIFFERENT client instance). Retries may still begin internally, but
# asyncio.wait_for cancels the ENTIRE awaited call -- retries included --
# once this many seconds pass, regardless of what the SDK is doing
# internally. database._DASS21_CLAIM_LEASE_SECONDS (180s) is a 9x margin
# over this bound, and _run_dass21_discuss_topic rechecks claim ownership
# via transition_dass21_discuss_claim immediately before ever contacting
# Telegram, independent of how long the build took.
_DASS21_LLM_TIMEOUT_SECONDS = 20.0


def _dass21_free_text_system_context(dass_result, lang: str) -> str:
    """Data-minimized trusted context, explicitly distinct from user speech."""
    dep = dass_result.subscales["depression"]
    anx = dass_result.subscales["anxiety"]
    stress = dass_result.subscales["stress"]
    ordering = _dass21_score_ordering_text(dass_result, lang)
    if lang == "ru":
        return ("\n\nДОВЕРЕННЫЙ ВНУТРЕННИЙ КОНТЕКСТ ОПРОСНИКА (это не слова пользователя): "
                f"инструмент={dass_result.instrument_version}; "
                f"translation_id={dass_result.translation_id}; "
                f"депрессия={dep}; тревога={anx}; стресс={stress}. "
                f"Доверенные числовые отношения: {ordering}. "
                "Используй показатели только как дополнительный ориентир для обсуждения "
                "проявлений за последнюю неделю. Не выводи из них причины, диагноз, "
                "вероятность расстройства, общий балл, степень тяжести или выбор метода "
                "терапии. Перед любым сравнением арифметически сопоставь три числа; "
                "исправь неверную сравнительную предпосылку пользователя и всегда называй "
                "равные значения равными. «Самый высокий» означает только числовое "
                "сравнение, а не клиническую тяжесть. Не превращай период «последняя "
                "неделя» в постоянное или хроническое состояние. Чётко отличай факт из "
                "DASS от гипотезы, основанной на отдельно описанной пользователем ситуации. "
                "Не утверждай, что пользователь сам сообщил эти числа.")
    return ("\n\nTRUSTED INTERNAL QUESTIONNAIRE CONTEXT (not user-authored): "
            f"instrument={dass_result.instrument_version}; "
            f"translation_id={dass_result.translation_id}; "
            f"depression={dep}; anxiety={anx}; stress={stress}. "
            f"Trusted numeric relationships: {ordering}. "
            "Use these scores only as supporting context for discussing experiences over "
            "the past week. Do not infer causes, diagnosis, disorder probability, a total "
            "score, severity, or a therapy choice. Arithmetically compare all three numbers "
            "before any comparative claim; correct a false comparative premise and always "
            "describe equal values as a tie. 'Highest' means numeric ordering only, not "
            "clinical severity. Do not turn the past-week scope into a persistent or chronic "
            "state. Distinguish a DASS-derived fact from a hypothesis grounded in context the "
            "user separately described. Do not claim the user stated the scores.")


_DASS21_SCALE_KEYS = ("depression", "anxiety", "stress")
_DASS21_SCALE_LABELS = {
    "ru": {"depression": "депрессия", "anxiety": "тревога", "stress": "стресс"},
    "en": {"depression": "depression", "anxiety": "anxiety", "stress": "stress"},
}
_DASS21_SCALE_LABELS_RU_GENITIVE = {
    "depression": "депрессии", "anxiety": "тревоги", "stress": "стресса",
}


def _dass21_score_relations(dass_result) -> dict:
    """Pure arithmetic facts derived only from the three trusted subscales."""
    scores = {key: int(dass_result.subscales[key]) for key in _DASS21_SCALE_KEYS}
    values = sorted(set(scores.values()), reverse=True)
    groups = tuple(
        tuple(key for key in _DASS21_SCALE_KEYS if scores[key] == value)
        for value in values
    )
    return {
        "scores": scores,
        "groups": groups,
        "highest": groups[0],
        "lowest": groups[-1],
    }


def _dass21_join_scale_labels(keys, lang: str) -> str:
    labels = [_DASS21_SCALE_LABELS[lang][key] for key in keys]
    conjunction = " и " if lang == "ru" else " and "
    if len(labels) < 2:
        return labels[0]
    return ", ".join(labels[:-1]) + conjunction + labels[-1]


def _dass21_score_ordering_text(dass_result, lang: str) -> str:
    facts = _dass21_score_relations(dass_result)
    scores = facts["scores"]
    groups = []
    for keys in facts["groups"]:
        groups.append(" = ".join(
            f"{_DASS21_SCALE_LABELS[lang][key]}={scores[key]}" for key in keys))
    ordering = " > ".join(groups)
    highest = _dass21_join_scale_labels(facts["highest"], lang)
    lowest = _dass21_join_scale_labels(facts["lowest"], lang)
    if lang == "ru":
        tie = " (равные значения)" if len(facts["highest"]) > 1 else ""
        return (f"{ordering}; самый высокий числовой показатель: {highest}{tie}; "
                f"самый низкий числовой показатель: {lowest}")
    tie = " (tie)" if len(facts["highest"]) > 1 else ""
    return (f"{ordering}; highest numeric scale: {highest}{tie}; "
            f"lowest numeric scale: {lowest}")


def _dass21_has_comparison_intent(user_text: str, lang: str) -> bool:
    low = (user_text or "").casefold().replace("ё", "е")
    if lang == "ru":
        return bool(re.search(
            r"\b(?:выше|ниже|больше|меньше|равн\w*|одинаков\w*|"
            r"сам\w*\s+(?:высок\w*|низк\w*)|сравн\w*)\b", low))
    return bool(re.search(
        r"\b(?:higher|lower|highest|lowest|more|less|equal|same|tie|tied|"
        r"compare|comparison)\b", low))


def _dass21_comparison_prefix(dass_result, lang: str) -> str:
    """Deterministic factual opening; the LLM never calculates ordering."""
    facts = _dass21_score_relations(dass_result)
    scores = facts["scores"]
    highest = facts["highest"]
    if len(highest) == 3:
        value = scores[highest[0]]
        if lang == "ru":
            return ("По этим результатам ни один показатель не выше остальных: "
                    f"депрессия, тревога и стресс одинаковы — по {value}.")
        return ("In these results, no scale is higher than the others: "
                f"depression, anxiety, and stress are tied at {value} each.")
    if len(highest) == 2:
        first, second = highest
        remaining = next(key for key in _DASS21_SCALE_KEYS if key not in highest)
        if lang == "ru":
            labels = _dass21_join_scale_labels(highest, lang)
            return (f"По этим результатам {_DASS21_SCALE_LABELS['ru'][first]} не выше "
                    f"{_DASS21_SCALE_LABELS_RU_GENITIVE[second]}: {labels} одинаковы — "
                    f"по {scores[first]}, а {_DASS21_SCALE_LABELS['ru'][remaining]} — "
                    f"{scores[remaining]}.")
        labels = _dass21_join_scale_labels(highest, lang)
        return (f"In these results, {_DASS21_SCALE_LABELS['en'][first]} is not higher "
                f"than {_DASS21_SCALE_LABELS['en'][second]}: {labels} are tied at "
                f"{scores[first]}, while {_DASS21_SCALE_LABELS['en'][remaining]} is "
                f"{scores[remaining]}.")
    ordered = [key for group in facts["groups"] for key in group]
    high = ordered[0]
    rest = ordered[1:]
    if lang == "ru":
        details = "; ".join(
            f"{_DASS21_SCALE_LABELS['ru'][key]} — {scores[key]}" for key in rest)
        return ("По этим результатам самый высокий числовой показатель — "
                f"{_DASS21_SCALE_LABELS['ru'][high]}: {scores[high]}; {details}.")
    details = "; ".join(
        f"{_DASS21_SCALE_LABELS['en'][key]} is {scores[key]}" for key in rest)
    return ("In these results, the highest numeric scale is "
            f"{_DASS21_SCALE_LABELS['en'][high]} at {scores[high]}; {details}.")


def _dass21_match_is_negated(low: str, match, lang: str) -> bool:
    token = "не" if lang == "ru" else "not"
    prefix = low[max(0, match.start() - 12):match.start()]
    return (bool(re.search(rf"\b{token}\s*$", prefix))
            or bool(re.search(rf"\b{token}\b", match.group(0))))


def _validate_dass21_free_text_response(text: str, dass_result, user_text: str,
                                        lang: str) -> tuple[bool, str | None]:
    """Narrow DASS-only factual guard; the global safety validator still runs."""
    del user_text  # Narrative remains available to the LLM, never a score fact here.
    low = (text or "").casefold().replace("ё", "е")
    if not low.strip():
        return False, "empty DASS response"

    if lang == "ru":
        scale = r"(?:тревог\w*|тревожност\w*|депресси\w*|стресс\w*)"
        severity = r"(?:повышенн\w*|высок\w*|умеренн\w*|тяжел\w*|низк\w*|легк\w*)"
        severity_patterns = (
            rf"\b{severity}\s+(?:(?:уровень|уровня|показатель|балл)\s+)?{scale}\b",
            rf"\b{scale}(?:\s+(?:показатель|балл|уровень))?\s+"
            rf"(?:является\s+|считается\s+|—\s*)?{severity}\b",
            rf"\b(?:уровень|показатель|балл)\s+{scale}[^.!?\n]{{0,12}}{severity}\b",
        )
    else:
        scale = r"(?:anxiety|depression|stress)"
        severity = r"(?:elevated|high|moderate|severe|low|mild)"
        severity_patterns = (
            rf"\b{severity}\s+(?:(?:level|score)\s+(?:of|for)\s+)?{scale}\b",
            rf"\b{scale}(?:\s+(?:score|level))?\s+"
            rf"(?:is|appears|looks|seems)\s+{severity}\b",
        )
    for pattern in severity_patterns:
        for match in re.finditer(pattern, low):
            if not _dass21_match_is_negated(low, match, lang):
                return False, "unsupported DASS severity classification"

    facts = _dass21_score_relations(dass_result)
    scores = facts["scores"]
    scale_patterns = ({
        "depression": r"депресси\w*",
        "anxiety": r"(?:тревог\w*|тревожност\w*)",
        "stress": r"стресс\w*",
    } if lang == "ru" else {
        "depression": r"depression", "anxiety": r"anxiety", "stress": r"stress",
    })
    relation_words = (r"выше|больше|ниже|меньше" if lang == "ru"
                      else r"higher|greater|more|lower|less")
    greater_words = ({"выше", "больше"} if lang == "ru"
                     else {"higher", "greater", "more"})
    for left in _DASS21_SCALE_KEYS:
        for right in _DASS21_SCALE_KEYS:
            if left == right:
                continue
            pattern = (
                rf"\b{scale_patterns[left]}\b(?P<middle>[^.!?\n]{{0,40}}?)"
                rf"\b(?P<relation>{relation_words})\b"
                rf"(?:\s+(?:чем|than))?[^.!?\n]{{0,12}}\b{scale_patterns[right]}\b"
            )
            for match in re.finditer(pattern, low):
                claimed = (scores[left] > scores[right]
                           if match.group("relation") in greater_words
                           else scores[left] < scores[right])
                negation = (r"\bне\s*$" if lang == "ru" else r"\bnot\s*$")
                if re.search(negation, match.group("middle")):
                    claimed = not claimed
                if not claimed:
                    return False, "incorrect DASS score comparison"

    if lang == "ru":
        other_scales_tail = (
            r"(?:чем\s+)?(?:всех\s+)?остальн\w*"
            r"(?:\s+(?:показател\w*|балл\w*|шкал\w*))?"
        )
    else:
        other_scales_tail = (
            r"than\s+(?:all\s+)?(?:the\s+)?"
            r"(?:others|other\s+(?:scores|scales|indicators))"
        )
    for left in _DASS21_SCALE_KEYS:
        pattern = (
            rf"\b{scale_patterns[left]}\b(?P<middle>[^.!?\n]{{0,24}}?)"
            rf"\b(?P<relation>{relation_words})\b\s+{other_scales_tail}\b"
        )
        for match in re.finditer(pattern, low):
            other_scores = (
                scores[key] for key in _DASS21_SCALE_KEYS if key != left)
            claimed = (all(scores[left] > score for score in other_scores)
                       if match.group("relation") in greater_words
                       else all(scores[left] < score for score in other_scores))
            negation = (r"\bне\s*$" if lang == "ru" else r"\bnot\s*$")
            if re.search(negation, match.group("middle")):
                claimed = not claimed
            if not claimed:
                return False, "incorrect DASS score comparison"

    equality = (r"(?:равн\w*|одинаков\w*)" if lang == "ru"
                else r"(?:equal|same|tied)")
    connector = "и" if lang == "ru" else "and"
    for index, left in enumerate(_DASS21_SCALE_KEYS):
        for right in _DASS21_SCALE_KEYS[index + 1:]:
            patterns = (
                rf"\b{scale_patterns[left]}\b\s+{connector}\s+"
                rf"\b{scale_patterns[right]}\b(?P<middle>[^.!?\n]{{0,30}}?)"
                rf"\b(?P<relation>{equality})\b",
                rf"\b{scale_patterns[left]}\b(?P<middle>[^.!?\n]{{0,24}}?)"
                rf"\b(?P<relation>{equality})\b[^.!?\n]{{0,12}}"
                rf"\b{scale_patterns[right]}\b",
            )
            for pattern in patterns:
                for match in re.finditer(pattern, low):
                    claimed = scores[left] == scores[right]
                    negation = (r"\bне\s*$" if lang == "ru" else r"\bnot\s*$")
                    if re.search(negation, match.group("middle")):
                        claimed = not claimed
                    if not claimed:
                        return False, "incorrect DASS score comparison"

    for key, pattern in scale_patterns.items():
        if lang == "ru":
            highest_patterns = (
                (rf"\b{pattern}\b[^.!?\n]{{0,30}}"
                 rf"сам(?:ый|ая|ое)\s+высок\w+\s+"
                 rf"(?:показател\w*|балл\w*|шкал\w*)"),
                (rf"\bсам(?:ый|ая|ое)\s+высок\w+\s+"
                 rf"(?:числов\w+\s+)?(?:показател\w*|балл\w*|шкал\w*)"
                 rf"[^.!?\n]{{0,30}}\b{pattern}\b"),
            )
        else:
            highest_patterns = (
                (rf"\b{pattern}\b[^.!?\n]{{0,30}}"
                 rf"(?:the\s+)?(?:only\s+)?highest\s+"
                 rf"(?:score|scale|indicator)\b"),
                (rf"\b(?:the\s+)?(?:only\s+)?highest\s+"
                 rf"(?:numeric\s+)?(?:score|scale|indicator)\b"
                 rf"[^.!?\n]{{0,30}}\b{pattern}\b"),
            )
        for highest_pattern in highest_patterns:
            for match in re.finditer(highest_pattern, low):
                if not _dass21_match_is_negated(low, match, lang) \
                        and facts["highest"] != (key,):
                    return False, "incorrect DASS highest-scale claim"

    sentences = [part.strip() for part in re.split(r"(?<=[.!?])|\n", low) if part.strip()]
    if lang == "ru":
        score_marker = r"\b(?:балл\w*|показател\w*|результат\w*|dass-?21)\b"
        attribution = r"\b(?:означа\w*|показыва\w*|свидетельству\w*|видно)\b"
        negated_attribution = r"\bне\s+(?:означа\w*|показыва\w*|свидетельству\w*)\b"
        permanence = r"\b(?:постоянн\w*|хронич\w*|всегда)\b"
        cause = r"\b(?:причин\w*|вызван\w*|обусловлен\w*|из-за)\b"
        cause_assertion = r"\b(?:означа\w*|показыва\w*|указыв\w*|вызван\w*|обусловлен\w*)\b"
    else:
        score_marker = r"\b(?:score|result|indicator|dass-?21)\b"
        attribution = r"\b(?:means?|shows?|indicates?|demonstrates?)\b"
        negated_attribution = r"\b(?:does\s+not|doesn't|cannot|can't)\s+(?:mean|show|indicate)\b"
        permanence = r"\b(?:constant|constantly|chronic|persistent|permanently)\b"
        cause = r"\b(?:cause|caused|because\s+of|due\s+to)\b"
        cause_assertion = r"\b(?:means?|shows?|indicates?|caused|due\s+to)\b"
    for sentence in sentences:
        has_score = re.search(score_marker, sentence)
        negated = re.search(negated_attribution, sentence)
        if has_score and re.search(attribution, sentence) \
                and re.search(permanence, sentence) and not negated:
            return False, "DASS score asserted a persistent state"
        if has_score and re.search(cause, sentence) \
                and re.search(cause_assertion, sentence) and not negated:
            return False, "DASS score asserted a cause"
    return True, None


def _dass21_safe_fallback(dass_result, user_text: str, lang: str) -> str:
    prefix = (_dass21_comparison_prefix(dass_result, lang) + "\n\n"
              if _dass21_has_comparison_intent(user_text, lang) else "")
    if lang == "ru":
        return prefix + (
            "Сам DASS-21 не показывает, почему получились именно такие баллы, и не "
            "позволяет по ним определить диагноз или степень тяжести. Он отражает три "
            "группы проявлений за последнюю неделю.\n\nЕсли хочешь, можем разобраться, "
            "что происходило на этой неделе: было ли больше беспокойства и страха, "
            "телесных реакций, трудностей с расслаблением или раздражительности."
        )
    return prefix + (
        "DASS-21 does not show why these scores occurred and cannot determine a diagnosis "
        "or severity from them. It reflects three groups of experiences over the past "
        "week.\n\nIf you want, we can look at what was happening this week: worry or fear, "
        "physical reactions, difficulty relaxing, or irritability."
    )


def _dass21_delivery_candidate_is_safe(text: str, dass_result, user_text: str,
                                       risk: str, lang: str) -> bool:
    global_safe, _ = validate_response_with_context(text, user_text, risk, lang)
    dass_safe, _ = _validate_dass21_free_text_response(
        text, dass_result, user_text, lang)
    return global_safe and dass_safe


def _dass21_emergency_fallback(lang: str) -> str:
    if lang == "ru":
        return ("Сейчас я не могу надёжно обсудить этот результат. "
                "Попробуй вернуться к нему позже.")
    return ("I cannot reliably discuss this result right now. "
            "Please try returning to it later.")


def _dass21_extract_llm_text(response) -> str:
    """Bounded validation of the OpenAI response SHAPE (not content) --
    rejects None response, non-list/empty choices, a choice with no
    message, and non-string/empty content. Raises DiscussBuildFailed (the
    same fail-closed exception as any other build failure) rather than
    letting an AttributeError/IndexError/TypeError escape as an
    unrelated-looking crash. Never logs the response content."""
    choices = getattr(response, "choices", None) if response is not None else None
    if not choices or not isinstance(choices, (list, tuple)):
        raise DiscussBuildFailed("LLM response had no usable choices")
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None) if message is not None else None
    if not isinstance(content, str) or not content.strip():
        raise DiscussBuildFailed("LLM response had no usable content")
    return content


async def _dass21_discuss_build_response(subscales, instrument_version: str,
                                          translation_id: str, topic_id: str, lang: str) -> str:
    """Workstream B — DASS-21 counterpart to _discuss_build_response. Same
    raise-or-return-valid-text contract (DiscussBuildFailed /
    DiscussOutputRejected, both propagate uncaught to traced_response_
    builder), same deterministic neutral risk shape, same validator call. The
    prompt (questionnaire_ux.dass21_discuss_topic_prompt) never includes raw
    stored answer text, item wording, answer labels, an overall total, or a
    severity/diagnosis label."""
    prompt_text = questionnaire_ux.dass21_discuss_topic_prompt(
        instrument_version, translation_id, subscales, topic_id, lang)
    messages = [
        {"role": "system", "content": get_system_prompt("open_chat", lang)},
        {"role": "user", "content": prompt_text},
    ]
    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model="gpt-4o-mini", messages=messages, temperature=0.65, max_tokens=300,
                timeout=_DASS21_LLM_TIMEOUT_SECONDS),
            timeout=_DASS21_LLM_TIMEOUT_SECONDS)
    except openai.OpenAIError as e:
        # The real SDK exception hierarchy: every operational failure
        # (connection, timeout, rate limit, 4xx/5xx API status) is an
        # openai.OpenAIError subclass -- a precise catch, not a blanket
        # except Exception. Never logs the prompt/messages.
        raise DiscussBuildFailed(f"LLM call failed: {type(e).__name__}") from e
    except asyncio.TimeoutError as e:
        # Outer hard-bound hit (belt-and-suspenders beyond the per-call
        # timeout above) -- same fail-closed contract. No late send follows:
        # wait_for cancels the inner coroutine, so it never returns a value
        # here, and _run_dass21_discuss_topic never learns of a "result" to
        # act on after this point.
        raise DiscussBuildFailed("LLM call exceeded the outer hard timeout") from e

    answer = _dass21_extract_llm_text(response)

    neutral_risk = {"score": 0, "level": "low", "categories": [],
                     "implicit": False, "ambiguous_phrases": []}
    is_safe, reason = validate_response_with_context(answer, prompt_text, neutral_risk, lang)
    if not is_safe:
        raise DiscussOutputRejected(f"validator rejected: {reason}")
    return answer


async def _send_discuss_reply(callback: CallbackQuery, uid: int, influence: Influence,
                               build_response, lang: str, response_id: str | None = None) -> str | None:
    """Shared traced-delivery wiring for BOTH the generic and DASS-21 discuss
    topic replies -- only `influence` and `build_response` differ per
    adapter; the traced_response_builder contract itself (PR #43: fail-closed
    on trace-persistence failure, build failure, or validator rejection, all
    to the SAME neutral_fallback) is never duplicated. Returns the response_id
    on success (latent reply actually sent), or None on any fail-closed
    degrade (callers that need to distinguish success/failure, e.g. the DASS-
    21 delivery-claim finalizer, use this return value)."""
    async def _send(text):
        await callback.message.answer(text)

    async def _neutral_fallback():
        await callback.message.answer(questionnaire_ux.not_available_text(lang))

    try:
        return await traced_response_builder(
            user_id=uid, requester_uid=uid,
            influences=[influence],
            build_response=build_response,
            send=_send,
            persist_trace=persist_influence_trace,
            neutral_fallback=_neutral_fallback,
            response_id=response_id,
        )
    except access_control.A1NotAllowed:
        await _neutral_fallback()
        return None


class _Dass21ClaimNotOwned(Exception):
    """Raised internally by _deliver_dass21_claimed_message's caller closures
    to signal traced_response_builder that NOTHING was sent (lost claim
    ownership, or a Telegram failure already recorded as delivery_uncertain)
    -- never let traced_response_builder treat this worker's send() as a
    success. Always caught locally in _run_dass21_discuss_topic; never
    propagates further."""


async def _deliver_dass21_claimed_message(
        callback: CallbackQuery, uid: int, session_id: int, topic_id: str,
        source_chat_id: int, source_message_id: int, response_id: str,
        text: str, response_kind: str) -> bool:
    """The ONE claim-checked Telegram delivery path for a DASS-21 discuss
    reply -- used for BOTH the real LLM answer and its neutral-fallback
    substitute (response_kind is "answer" or "neutral_fallback", used only
    for log context; the ownership contract is identical for both, so a
    double tap cannot produce two visible replies regardless of which kind
    the first one was).

    A send is IMPOSSIBLE unless the atomic pending_before_send -> send_started
    transition returns True. If it returns False (another worker already
    reclaimed/owns this exact card+topic) or raises aiosqlite.Error, this
    function returns False WITHOUT EVER calling Telegram -- a stale worker
    that has lost claim ownership can never deliver a message. The transition
    call's return value is the ONLY thing that gates the send; nothing here
    proceeds to `callback.message.answer` on a caught exception the way an
    earlier, buggy version of this function did.

    Returns True iff Telegram confirmed the send (state -> delivered)."""
    try:
        owns_send = await transition_dass21_discuss_claim(
            uid, session_id, topic_id, source_chat_id, source_message_id,
            response_id, "pending_before_send", "send_started")
    except aiosqlite.Error:
        owns_send = False
    if not owns_send:
        # Either a DB error, or another response_id now owns this card+topic
        # (a concurrent claim, or a reclaim after this worker's lease
        # expired) -- no Telegram contact at all.
        return False

    try:
        await callback.message.answer(text)
    except (TelegramBadRequest, TelegramForbiddenError,
            TelegramNetworkError, TelegramRetryAfter) as exc:
        logging.warning("dass21 discuss %s send failed (session_id=%s, topic=%s): %s",
                        response_kind, session_id, topic_id, type(exc).__name__)
        try:
            await transition_dass21_discuss_claim(
                uid, session_id, topic_id, source_chat_id, source_message_id,
                response_id, "send_started", "delivery_uncertain")
        except aiosqlite.Error:
            pass  # best-effort bookkeeping only; Telegram's own outcome is
                  # already unknown regardless of whether this write lands
        return False

    try:
        await transition_dass21_discuss_claim(
            uid, session_id, topic_id, source_chat_id, source_message_id,
            response_id, "send_started", "delivered")
    except aiosqlite.Error:
        pass  # Telegram send already succeeded; a bookkeeping failure here
              # only means a future reclaim decision is best-effort -- the
              # message is neither undelivered nor eligible for auto-resend
              # (the row stays non-reclaimable at 'send_started').
    return True


async def _run_dass21_discuss_topic(callback: CallbackQuery, uid: int, session_id: int,
                                    topic_id: str, dass_result, lang: str) -> None:
    """Workstream B (corrective pass) — DASS-21 topic reply delivery.

    Idempotency key is the exact MENU CARD's button:
    (uid, session_id, topic_id, source_chat_id, source_message_id) --
    source_chat_id/source_message_id identify the Telegram message the tapped
    button lives on. A double tap on the SAME card claims at most once;
    reopening the discuss menu sends a NEW Telegram message (a new
    message_id), so tapping the same topic on that new card is a fresh,
    legitimate attempt -- this is NOT a permanent one-topic-per-session lock.

    5-state claim machine (dass21_discuss_claims.status, DB CHECK-
    constrained, see database.py):
      pending_before_send -> send_started -> delivered
                          -> failed_before_send  (any failure BEFORE Telegram
                             is ever contacted -- retryable on the same card,
                             including via an expired-lease reclaim)
      send_started        -> delivery_uncertain  (Telegram raised; unknown
                             whether the message went out -- NEVER auto-
                             reclaimed on this card; a NEW card can retry)

    Both the real answer AND its neutral-fallback substitute go through
    _deliver_dass21_claimed_message -- there is no separate unchecked send
    path for the fallback, so a lost/reclaimed claim cannot deliver ANY
    message, of either kind.

    Telegram and SQLite are two separate systems, not one transaction --
    delivery is therefore best-effort/at-most-once-PER-CARD, never claimed
    exact-once."""
    source_chat_id = callback.message.chat.id
    source_message_id = callback.message.message_id
    response_id = f"dass21-discuss-{session_id}-{topic_id}-{secrets.token_hex(8)}"

    try:
        claimed = await claim_dass21_discuss_reply(
            uid, session_id, topic_id, source_chat_id, source_message_id, response_id)
    except aiosqlite.Error:
        # Claim-insert failure before anything else happened (no DB-backed
        # action exists yet) -- fail closed with NO new chat message (a
        # repeated tap during a DB outage must not flood the chat): a
        # bounded callback alert only, using the SAME existing neutral copy,
        # no internal detail (no "database"/"SQLite"/session id).
        try:
            await callback.answer(questionnaire_ux.not_available_text(lang), show_alert=True)
        except (TelegramBadRequest, TelegramForbiddenError,
                TelegramNetworkError, TelegramRetryAfter):
            pass
        return
    if not claimed:
        # Another delivery already owns this exact card+topic (pending,
        # send_started, delivered, or delivery_uncertain) -- silent no-op,
        # same non-disclosure convention as a stale/cross-user callback.
        await callback.answer()
        return

    influence = Influence(
        "questionnaire_result", session_id,
        f"reply drew on DASS-21 session {session_id} subscales "
        f"depression={dass_result.subscales['depression']} "
        f"anxiety={dass_result.subscales['anxiety']} "
        f"stress={dass_result.subscales['stress']}, topic={topic_id}",
    )

    async def _build():
        return await _dass21_discuss_build_response(
            dass_result.subscales, dass_result.instrument_version,
            dass_result.translation_id, topic_id, lang)

    async def _send(text):
        delivered = await _deliver_dass21_claimed_message(
            callback, uid, session_id, topic_id, source_chat_id, source_message_id,
            response_id, text, "answer")
        if not delivered:
            # Signal upward that nothing was actually sent -- propagates
            # uncaught out of traced_response_builder (it does not inspect
            # send()'s return value), caught once below.
            raise _Dass21ClaimNotOwned()

    async def _neutral_fallback():
        await _deliver_dass21_claimed_message(
            callback, uid, session_id, topic_id, source_chat_id, source_message_id,
            response_id, questionnaire_ux.not_available_text(lang), "neutral_fallback")

    try:
        await traced_response_builder(
            user_id=uid, requester_uid=uid, influences=[influence],
            build_response=_build, send=_send,
            persist_trace=persist_influence_trace, neutral_fallback=_neutral_fallback,
            response_id=response_id,
        )
    except access_control.A1NotAllowed:
        # A1NotAllowed is raised BEFORE _send/_neutral_fallback are ever
        # invoked (traced_response_builder's very first check), so the claim
        # is still pending_before_send -- route through the SAME claim-
        # checked path as every other fallback.
        await _neutral_fallback()
    except _Dass21ClaimNotOwned:
        # Already fully handled inside _deliver_dass21_claimed_message
        # (either no Telegram contact at all, or delivery_uncertain recorded
        # after a Telegram failure). No further send of any kind.
        pass

    try:
        await callback.answer()
    except (TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter):
        pass


@dp.callback_query(lambda c: _is_discuss_topic_data(c.data or ""))
async def cb_questionnaire_discuss_topic(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(callback, uid, lang):            # 1, 2
        return
    parts = callback.data.split(":")
    if len(parts) != 4 or not parts[2].isdigit() or parts[3] not in _ALL_DISCUSS_TOPIC_TOKENS:
        await callback.answer()
        return
    session_id = int(parts[2])
    topic_id = parts[3]

    session = await _load_owned_session(session_id, uid)                  # 3
    if session is None:
        # Silent no-op -- same non-disclosure convention as q:r/q:k/q:e/q:o.
        await callback.answer()
        return

    is_dass = dass21_runtime.is_dass21_definition_id(session["questionnaire_id"])
    # Adapter-exact topic enforcement -- _is_discuss_topic_data only proved the
    # topic is valid for SOME adapter (the union). A DASS session tapped with a
    # generic-only topic (e.g. "why"), or a generic session tapped with a
    # DASS-only topic ("measures"/"relate"), is a forged/cross-adapter
    # callback: silent no-op, BEFORE any gate/claim/trace/LLM call -- same
    # non-disclosure convention as an unowned session.
    if is_dass and topic_id not in _DASS21_DISCUSS_TOPICS:
        await callback.answer()
        return
    if not is_dass and topic_id not in _GENERIC_DISCUSS_TOPICS:
        await callback.answer()
        return

    if is_dass:
        dass_result = await _dass21_discuss_gate_and_load(session, lang)
        if dass_result is None:
            await callback.message.answer(questionnaire_ux.not_available_text(lang))
            await callback.answer()
            return
        await _run_dass21_discuss_topic(callback, uid, session_id, topic_id, dass_result, lang)
        return

    loaded = await _discuss_gate_and_load(session, lang)                  # 4-6
    if loaded is None:
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return
    definition, score, max_score, intensity = loaded

    influence = Influence(
        "questionnaire_result", session_id,
        f"reply drew on questionnaire session {session_id} ({definition['title']}) "
        f"result {score}/{max_score} ({intensity}), topic={topic_id}",
    )

    async def _build():
        return await _discuss_build_response(
            definition["title"], score, max_score, intensity, topic_id, lang)

    await _send_discuss_reply(callback, uid, influence, _build, lang)
    await callback.answer()


# ── Navigation Hub — deterministic menu/catalog, no clinical logic ─────────────
# CRITICAL invariant (this project has already fixed this class of bug twice):
# /menu and EVERY navigation/emotion-map callback below reuse the SAME two
# gates as every other product entrypoint, in the SAME order --
# journal_guard (active-crisis, crisis-adjacent, must run regardless of
# role/access) THEN ensure_full_access_or_closed_test (ordinary product
# access). A stale inline button pressed later, after access/crisis state
# changed, must not bypass either gate -- navigation surfaces don't store
# data, but they still expose product surfaces and must not distract from an
# active crisis screen.
async def _nav_gate(entity, uid: int, lang: str) -> bool:
    """Shared gate for /menu, every navigation callback, and emotion:map.
    Returns True iff the caller may proceed."""
    target_message = entity.message if isinstance(entity, CallbackQuery) else entity
    decision, _ = await journal_guard(target_message, uid, lang)
    if decision == "crisis":
        if isinstance(entity, CallbackQuery):
            await entity.answer()
        return False
    if not await ensure_full_access_or_closed_test(entity, uid):
        return False
    return True


def _menu_keyboard(lang: str) -> InlineKeyboardMarkup:
    # "tests" routes straight to the real Questionnaire Core (q:l /
    # cb_questionnaire_list) instead of the old Navigation Hub placeholder --
    # see cb_tests_hub's comment for why. The other 4 entries keep the
    # original f"{key}:hub" pattern, byte-for-byte unchanged.
    rows = [[InlineKeyboardButton(
        text=(ru if lang == "ru" else en),
        callback_data=("q:l" if key == "tests" else f"{key}:hub"),
    )] for key, ru, en in navigation.MENU_SECTIONS]
    if config.feedback_chat_url():
        rows.append([InlineKeyboardButton(
            text=("💬 Обратная связь" if lang == "ru" else "💬 Feedback"),
            callback_data="feedback:hub")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _hub_back_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=("⬅️ В меню" if lang == "ru" else "⬅️ Back to menu"), callback_data="menu:back")]])


def _help_keyboard(lang: str) -> InlineKeyboardMarkup:
    # /help is the explicit navigation/help card (round 3) -- the persistent
    # lower ReplyKeyboard remains the PRIMARY navigation; this is not a
    # second permanently-visible menu. Reuses the EXACT SAME existing
    # callbacks as the persistent lower menu / legacy hub buttons -- no
    # duplicated business logic, only a different card layout.
    #
    # UI polish V1: talk/tests/journals/results/settings all dropped --
    # each one either duplicates a persistent-lower-menu button (tests,
    # journals, results, settings="🎛 Как отвечать") or is redundant with
    # the always-available text field (talk). Feedback dropped too, so
    # Help now surfaces ONLY About and Privacy, per the product spec.
    # Destinations (about:hub / privacy:hub) are completely unchanged.
    labels = {key: (ru if lang == "ru" else en) for key, ru, en in navigation.MENU_SECTIONS}
    rows = [
        [InlineKeyboardButton(text=labels["about"], callback_data="about:hub")],
        [InlineKeyboardButton(text=labels["privacy"], callback_data="privacy:hub")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _privacy_hub_keyboard(lang: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=("📥 Получить копию данных" if lang == "ru" else "📥 Get a copy of data"),
            callback_data="privacy:export")],
        [InlineKeyboardButton(
            text=("🗑 Удалить данные аккаунта" if lang == "ru" else "🗑 Delete account data"),
            callback_data="privacy:delete")],
        [InlineKeyboardButton(
            text=("ℹ️ Какие данные хранятся" if lang == "ru" else "ℹ️ What data is stored"),
            callback_data="privacy:stored")],
    ]
    if config.PRIVACY_POLICY_URL:
        rows.append([InlineKeyboardButton(
            text=("📄 Политика конфиденциальности" if lang == "ru" else
                  "📄 Privacy Policy"),
            url=config.PRIVACY_POLICY_URL)])
    rows.append([InlineKeyboardButton(
        text=("⬅️ Назад" if lang == "ru" else "⬅️ Back"),
        callback_data="menu:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _privacy_back_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=("⬅️ Назад" if lang == "ru" else "⬅️ Back"),
        callback_data="privacy:hub")]])


async def _answer_target(entity, text: str, **kw) -> None:
    target = entity.message if isinstance(entity, CallbackQuery) else entity
    if isinstance(entity, CallbackQuery):
        await _edit_or_answer(target)(text, **kw)
    else:
        await target.answer(text, **kw)


def _response_settings_keyboard(lang: str) -> InlineKeyboardMarkup:
    rows = [list(row) for row in format_selector_kb(lang).inline_keyboard]
    rows.append([InlineKeyboardButton(
        text=("⬅️ В меню" if lang == "ru" else "⬅️ Back to menu"),
        callback_data="menu:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _feedback_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=("Открыть пространство обратной связи" if lang == "ru" else "Open feedback space"),
            url=config.feedback_chat_url())],
        [InlineKeyboardButton(
            text=("⬅️ В меню" if lang == "ru" else "⬅️ Back to menu"),
            callback_data="menu:back")],
    ])


@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    """Legacy compatibility only: /menu no longer renders its own inline
    navigation hierarchy (that duplicated the persistent lower ReplyKeyboard,
    the one primary menu). It now just re-attaches that same keyboard with a
    single short neutral line."""
    uid = message.from_user.id
    lang = await get_user_language(uid)
    if not await _nav_gate(message, uid, lang):
        return
    await message.answer(
        "Основные разделы — ниже 👇" if lang == "ru"
        else "The main sections are below 👇",
        reply_markup=persistent_lower_menu_kb(lang),
    )


@dp.message(F.text.in_({"🧠 Психологические тесты", "🧠 Psychological tests"}))
async def lower_menu_tests(message: Message, state: FSMContext):
    await cmd_questionnaire(message, state)


@dp.message(F.text.in_({"📊 Мои результаты", "📊 My results"}))
async def lower_menu_results(message: Message, state: FSMContext):
    uid = message.from_user.id
    lang = await get_user_language(uid)
    if not await _nav_gate(message, uid, lang):
        return
    await _clear_active_journal_if_leaving(state)
    await message.answer(
        navigation.results_hub_text(lang),
        reply_markup=_results_hub_keyboard(lang))


@dp.message(F.text.in_({"📝 Дневники", "📝 Diaries"}))
async def lower_menu_journals(message: Message, state: FSMContext):
    await cmd_journal(message, state)


@dp.message(F.text.in_({"🎛 Как отвечать", "🎛 How to reply"}))
async def lower_menu_response_settings(message: Message, state: FSMContext):
    uid = message.from_user.id
    lang = await get_user_language(uid)
    if not await _nav_gate(message, uid, lang):
        return
    await _clear_active_journal_if_leaving(state)
    available = await _voice_ux_enabled_for(uid)
    await message.answer(
        navigation.response_settings_text(lang, available=available),
        reply_markup=(format_selector_kb(lang) if available else None))


@dp.message(F.text.in_({"🔒 Данные и приватность", "🔒 Data and privacy"}))
async def lower_menu_privacy(message: Message, state: FSMContext):
    uid = message.from_user.id
    lang = await get_user_language(uid)
    if not await _nav_gate(message, uid, lang):
        return
    await _clear_active_journal_if_leaving(state)
    await message.answer(
        navigation.privacy_hub_text(lang),
        reply_markup=_privacy_hub_keyboard(lang))


@dp.callback_query(F.data == "talk:hub")
async def cb_talk_hub(callback: CallbackQuery, state: FSMContext = None):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _nav_gate(callback, uid, lang):
        return
    await _clear_active_journal_if_leaving(state)
    await _answer_target(callback, navigation.talk_hub_text(lang),
                         reply_markup=_hub_back_keyboard(lang))
    await callback.answer()


@dp.callback_query(F.data == "settings:hub")
async def cb_settings_hub(callback: CallbackQuery, state: FSMContext = None):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _nav_gate(callback, uid, lang):
        return
    await _clear_active_journal_if_leaving(state)
    available = await _voice_ux_enabled_for(uid)
    keyboard = (_response_settings_keyboard(lang) if available
                else _hub_back_keyboard(lang))
    await _answer_target(callback, navigation.response_settings_text(
        lang, available=available), reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data == "feedback:hub")
async def cb_feedback_hub(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _nav_gate(callback, uid, lang):
        return
    if not config.feedback_chat_url():
        await callback.answer()
        return
    await _answer_target(callback, navigation.feedback_hub_text(lang),
                         reply_markup=_feedback_keyboard(lang))
    await callback.answer()


@dp.callback_query(F.data == "tests:hub")
# No longer reachable from the main menu -- the "tests" button now routes
# directly to q:l / cb_questionnaire_list (see _menu_keyboard). Kept in place
# (not deleted) for a stale/cached client that still holds an old "tests:hub"
# button; removing this dead handler is a candidate for a future, separate
# cleanup PR, not this one.
async def cb_tests_hub(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _nav_gate(callback, uid, lang):
        return
    await _answer_target(callback, navigation.tests_hub_text(lang), reply_markup=_hub_back_keyboard(lang))
    await callback.answer()


@dp.callback_query(F.data == "journals:hub")
async def cb_journals_hub(callback: CallbackQuery, state: FSMContext = None):
    """/help -> "📝 Дневники" (round-3 correction): renders the EXACT SAME
    real journal card/buttons as the persistent-lower-menu entry (cmd_journal)
    -- one journal navigation UX, not a raw slash-command list on this path.
    Edits the existing /help card in place via _answer_target's normal
    CallbackQuery edit-in-place behavior, rather than appending a second
    navigation card. Round 4: also abandons a stale active journal FSM
    (inline navigation must escape it just like the persistent menu does)."""
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _nav_gate(callback, uid, lang):
        return
    await _clear_active_journal_if_leaving(state)
    await _answer_target(callback, _journal_hub_text(lang), reply_markup=_journal_hub_keyboard(lang))
    await callback.answer()


def _results_hub_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=("🧪 Результаты теста" if lang == "ru" else "🧪 Tests"),
            callback_data="results:tests")],
        [InlineKeyboardButton(
            text=("📊 Отчёт дневника" if lang == "ru" else "📊 Diary report"),
            callback_data="results:report"),
         InlineKeyboardButton(
            text=("🧭 Самонаблюдения" if lang == "ru" else "🧭 Self-observations"),
            callback_data="results:profile")],
        [InlineKeyboardButton(
            text=("⬅️ В меню" if lang == "ru" else "⬅️ Back to menu"),
            callback_data="menu:back")],
    ])


def _results_tests_keyboard(sessions: list[dict], lang: str) -> InlineKeyboardMarkup:
    rows = []
    for session in sessions:
        if not (dass21_runtime.is_dass21_definition_id(session["questionnaire_id"])
                or gad7_core.is_gad7_definition_id(session["questionnaire_id"])):
            continue
        completed_at = session.get("completed_at") or ""
        date = completed_at[:10]
        name = ("DASS-21" if dass21_runtime.is_dass21_definition_id(
            session["questionnaire_id"]) else "GAD-7")
        label = f"{name} · {date}" if date else name
        rows.append([InlineKeyboardButton(
            text=label, callback_data=f"results:test:{session['id']}")])
    rows.append([InlineKeyboardButton(
        text=("⬅️ Назад" if lang == "ru" else "⬅️ Back"),
        callback_data="results:hub")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _results_history_result_keyboard(session_id: int, lang: str) -> InlineKeyboardMarkup:
    rows = []
    if config.DASS21_DISCUSSION_ENABLED:
        rows.append([InlineKeyboardButton(
            text=("💬 Разобрать результат" if lang == "ru" else "💬 Explore the result"),
            callback_data=f"q:m:{session_id}")])
    rows.extend([
        [InlineKeyboardButton(
            text=("🧾 Отчёт для специалиста" if lang == "ru" else "🧾 Specialist report"),
            callback_data=f"q:o:{session_id}")],
        [InlineKeyboardButton(
            text=("📌 Оставить в чате" if lang == "ru" else "📌 Leave in chat"),
            callback_data=f"results:pin:{session_id}")],
        [InlineKeyboardButton(
            text=("⬅️ К результатам" if lang == "ru" else "⬅️ Back to results"),
            callback_data="results:tests")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _gad7_history_result_keyboard(session_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=("🧾 Отчёт для специалиста" if lang == "ru" else "🧾 Specialist report"),
            callback_data=f"q:o:{session_id}")],
        [InlineKeyboardButton(
            text=("📌 Оставить в чате" if lang == "ru" else "📌 Leave in chat"),
            callback_data=f"results:pin:{session_id}")],
        [InlineKeyboardButton(
            text=("⬅️ К результатам" if lang == "ru" else "⬅️ Back to results"),
            callback_data="results:tests")],
    ])


async def _load_owned_completed_history_dass(session_id: int, uid: int):
    session = await _load_owned_session(session_id, uid)
    if session is None or session.get("status") != "completed":
        return None
    if not dass21_runtime.is_dass21_definition_id(session["questionnaire_id"]):
        return None
    return session


async def _load_owned_completed_history_result(session_id: int, uid: int):
    session = await _load_owned_session(session_id, uid)
    if session is None or session.get("status") != "completed":
        return None
    if not (dass21_runtime.is_dass21_definition_id(session["questionnaire_id"])
            or gad7_core.is_gad7_definition_id(session["questionnaire_id"])):
        return None
    return session


@dp.callback_query(F.data == "results:hub")
async def cb_results_hub(callback: CallbackQuery, state: FSMContext = None):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _nav_gate(callback, uid, lang):
        return
    if state is not None:
        await _clear_active_journal_if_leaving(state)
    await _answer_target(callback, navigation.results_hub_text(lang), reply_markup=_results_hub_keyboard(lang))
    await callback.answer()


def _is_questionnaire_completion_card(reply_markup) -> bool:
    """Stateless classifier: does the CURRENT keyboard on a message belong to
    a questionnaire completion card (generic/GAD-7 or DASS-21)? Reused by
    cb_results_tests below to decide whether pressing its "My results"
    button (results:tests) may safely edit that message in place, or must
    preserve it as the historical artifact it is (see
    _questionnaire_completion_keyboard's own docstring for that invariant).

    Classifies purely from callback_data namespaces already on the message
    Telegram hands back with every CallbackQuery -- never from visible text,
    never from any stored/transient marker -- so this works for ANY
    completion card still sitting in the chat (old or new, created before or
    after a bot restart) with zero extra state.

    q:o:<sid> (specialist report) is present on EVERY completion keyboard --
    both _questionnaire_completion_keyboard and _dass21_completion_keyboard,
    regardless of which OPTIONAL DASS buttons (q:m:<sid> discuss,
    q:pick:<sid> recommendation) happen to be present -- so its presence
    alone is the one stable, always-true signal; not "the whole keyboard
    equals this exact list", which optional buttons would break.
    results:pin:<sid> is the marker unique to the REOPENED historical-result
    screens (_results_history_result_keyboard / _gad7_history_result_
    keyboard), which also carry q:o: -- excluding it keeps those screens
    (whose own "⬅️ К результатам" -> results:tests is intentionally still an
    in-place edit; that's what the opt-in "📌 Оставить в чате" pin button
    there is for) from being misclassified as completion cards. Neither
    signal is present on the plain Results Hub or the history LIST screen."""
    if reply_markup is None:
        return False
    has_specialist_report = False
    has_pin = False
    for row in reply_markup.inline_keyboard:
        for button in row:
            data = button.callback_data or ""
            if data.startswith("q:o:"):
                has_specialist_report = True
            elif data.startswith("results:pin:"):
                has_pin = True
    return has_specialist_report and not has_pin


@dp.callback_query(F.data == "results:tests")
async def cb_results_tests(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _nav_gate(callback, uid, lang):
        return
    try:
        sessions = await get_completed_questionnaire_sessions(uid)
    except aiosqlite.Error:
        sessions = []
    visible_sessions = [
        session for session in sessions
        if (dass21_runtime.is_dass21_definition_id(session["questionnaire_id"])
            or gad7_core.is_gad7_definition_id(session["questionnaire_id"]))
    ]
    text = navigation.questionnaire_history_text(bool(visible_sessions), lang)
    keyboard = _results_tests_keyboard(visible_sessions, lang)
    if _is_questionnaire_completion_card(callback.message.reply_markup):
        await callback.message.answer(text, reply_markup=keyboard)
    else:
        await _answer_target(callback, text, reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data.startswith("results:test:"))
async def cb_results_test(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(callback, uid, lang):
        return
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer()
        return
    session_id = int(parts[2])
    session = await _load_owned_completed_history_result(session_id, uid)
    if session is None:
        await callback.answer()
        return
    if gad7_core.is_gad7_definition_id(session["questionnaire_id"]):
        await _send_gad7_historical_result(
            _edit_or_answer(callback.message), session, lang,
            reply_markup=_gad7_history_result_keyboard(session_id, lang))
    else:
        await _send_dass21_back_to_result(
            _edit_or_answer(callback.message), session, lang,
            reply_markup_override=_results_history_result_keyboard(session_id, lang))
    await callback.answer()


@dp.callback_query(F.data.startswith("results:pin:"))
async def cb_results_pin(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(callback, uid, lang):
        return
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer()
        return
    session_id = int(parts[2])
    session = await _load_owned_completed_history_result(session_id, uid)
    if session is None:
        await callback.answer()
        return
    if gad7_core.is_gad7_definition_id(session["questionnaire_id"]):
        await _send_gad7_historical_result(callback.message.answer, session, lang)
    else:
        await _send_dass21_back_to_result(callback.message.answer, session, lang)
    await callback.answer()


@dp.callback_query(F.data == "results:report")
async def cb_results_report(callback: CallbackQuery):
    """Reuses the EXISTING /report functionality -- same report content,
    storage and calculations, only reached from a button instead of a raw
    slash command."""
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _nav_gate(callback, uid, lang):
        return
    await cmd_report(callback.message, None, tg_user=callback.from_user)
    await callback.answer()


@dp.callback_query(F.data == "results:profile")
async def cb_results_profile(callback: CallbackQuery):
    """Reuses the EXISTING /profile functionality -- same profile content
    and calculations, only reached from a button instead of a raw slash
    command."""
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _nav_gate(callback, uid, lang):
        return
    await cmd_profile(callback.message, tg_user=callback.from_user)
    await callback.answer()


@dp.callback_query(F.data == "privacy:hub")
async def cb_privacy_hub(callback: CallbackQuery, state: FSMContext = None):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _nav_gate(callback, uid, lang):
        return
    await _clear_active_journal_if_leaving(state)
    await _answer_target(
        callback, navigation.privacy_hub_text(lang),
        reply_markup=_privacy_hub_keyboard(lang))
    await callback.answer()


@dp.callback_query(F.data == "privacy:stored")
async def cb_privacy_stored(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _nav_gate(callback, uid, lang):
        return
    await _answer_target(
        callback, navigation.privacy_stored_data_text(lang),
        reply_markup=_privacy_back_keyboard(lang))
    await callback.answer()


@dp.callback_query(F.data == "privacy:export")
async def cb_privacy_export(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    scoped_access.assert_can_read_user_data(uid, uid, "privacy_export")
    await _send_privacy_export(callback.message, uid, lang)
    await callback.answer()


@dp.callback_query(F.data == "privacy:delete")
async def cb_privacy_delete_open(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    scoped_access.assert_can_read_user_data(uid, uid, "privacy_delete")
    await _answer_target(
        callback, await _privacy_delete_preview_text(uid, lang),
        reply_markup=_privacy_delete_kb("privacy_delete", lang, uid))
    await callback.answer()


@dp.callback_query(F.data == "about:hub")
async def cb_about_hub(callback: CallbackQuery, state: FSMContext = None):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _nav_gate(callback, uid, lang):
        return
    await _clear_active_journal_if_leaving(state)
    await _answer_target(callback, navigation.about_hub_text(lang), reply_markup=_hub_back_keyboard(lang))
    await callback.answer()


@dp.callback_query(F.data == "menu:back")
async def cb_menu_back(callback: CallbackQuery, state: FSMContext = None):
    """Round 3: "⬅️ В меню" returns to the /help-style navigation card, not
    the old legacy "Главное меню / Выберите раздел" hub (_menu_keyboard is
    retained only for the still-live cb_*_hub backward-compat callbacks, see
    test_cb_tests_hub_still_works_if_reached_directly).

    Round 4: menu:back is the primary inline escape hatch out of a stale
    active journal FSM, so it must abandon one here too."""
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _nav_gate(callback, uid, lang):
        return
    if state is not None:
        await _clear_active_journal_if_leaving(state)
    await _answer_target(callback, navigation.help_text(lang), reply_markup=_help_keyboard(lang))
    await callback.answer()


# ── Emotion Map — deterministic, non-diagnostic vocabulary helper ─────────────
def _emotion_map_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=("🗺 Карта эмоций" if lang == "ru" else "🗺 Emotion map"), callback_data="emotion:map")]])


@dp.callback_query(F.data == "emotion:map")
async def cb_emotion_map(callback: CallbackQuery):
    """Read-only helper: shows the map, never stores a selection, never
    touches FSM/journal/questionnaire state. Same gates as everything else."""
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _nav_gate(callback, uid, lang):
        return
    text = emotion_map.emotion_map_text(lang) + "\n\n" + emotion_map.emotion_map_return_hint(lang)
    await callback.message.answer(text)
    await callback.answer()


@dp.message(F.voice)
async def handle_voice(message: Message, state: FSMContext):
    uid = message.from_user.id
    lang = await get_user_language(uid)   # needed for Whisper lang hint
    # Voice and Adaptive Response UX: an explicitly SAVED voice_language
    # preference (only ever settable via the now owner-gated /format, so
    # unreachable for anyone else) overrides the stored UI language hint;
    # "auto" (the untouched default) preserves the exact prior behavior.
    # Gated the same way as every other Voice UX action -- defense in depth
    # in case a preference row ever existed for a non-owner uid.
    stt_lang = lang
    if await _voice_ux_enabled_for(uid):
        prefs = await get_response_preferences(uid)
        if prefs["voice_language"] in ("ru", "en"):
            stt_lang = prefs["voice_language"]
    await bot.send_chat_action(message.chat.id, "typing")
    try:
        text = await transcribe_voice(message.voice, bot, client, stt_lang)
        await pipeline(message, text, state)
    except Exception as e:
        print(f"[voice] {e}")
        await message.answer("Не смог распознать" if lang == "ru" else "Couldn't recognize")

@dp.message(F.text)
async def handle_text(message: Message, state: FSMContext):
    await pipeline(message, message.text, state)

# ────────────────────────────────────────────────────────────────────────────

async def main():
    await init_db()
    try:
        # Public-beta visible command list: the persistent lower ReplyKeyboard
        # is the one primary navigation surface, so the Telegram slash-command
        # list is trimmed to /start and /help only -- it must not duplicate
        # that menu. /menu, /questionnaire, /journal, /format handlers stay
        # fully registered below and remain callable if typed manually; this
        # only changes what Telegram's command autocomplete/side list shows.
        await bot.set_my_commands([
            BotCommand(command="start", description="Start"),
            BotCommand(command="help", description="Help"),
        ])
        await bot.set_my_commands([
            BotCommand(command="start", description="Начать"),
            BotCommand(command="help", description="Помощь"),
        ], language_code="ru")
    except TelegramAPIError as exc:
        logging.warning(
            "bot command registration failed; continuing startup "
            "(error_type=%s)", type(exc).__name__)
    start_dashboard()
    scheduler = setup_scheduler(bot, client)
    scheduler.start()
    print("✅ X20 Bot started")
    # NOTE: no drop_pending_updates here. In aiogram 3.7 it is NOT a
    # start_polling parameter -- it would fall into **kwargs and be injected
    # as workflow_data (a misleading no-op that drops nothing), and the only
    # real way to drop the backlog is bot.delete_webhook(drop_pending_updates
    # =True), which silently discards messages users sent during a restart.
    # That message loss is deliberately NOT wanted: a normal restart must
    # never drop a user's message. The stale-answer defence lives in the
    # per-turn generation guard (see _bump_user_generation) instead.
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
