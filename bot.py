"""
X20 Bot — Основной файл

Полный pipeline:
  Risk → Language → Stage → State → Readiness → Capacity → Scenario → 
  RelationshipMonitor → PracticeSelect → Memory → LLM → SafetyValidator → 
  Notifications → OutcomeTracking → User
"""
import asyncio
import hmac
import logging
import pathlib
import secrets
import sys

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
from aiogram.types import Message, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.filters import Command
from aiogram.exceptions import (
    TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter,
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
from config import BOT_TOKEN, OPENAI_API_KEY, ADMIN_USER_IDS, AB_VARIANTS, ROUTER_VERSION, PRACTICE_VERSION
from prompts import get_system_prompt, get_crisis_text, get_onboarding
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
from practice_registry import select_practice, get_production_practice_by_id
from safety_validator import (
    validate_response,
    validate_response_with_context, select_fallback,
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
from database import (
    init_db, upsert_user, save_message, load_state, save_state,
    log_moderation, log_validator_block, log_router_decision,
    log_adverse_event, update_user_profile,
    start_intervention, finish_intervention,
    get_user_language,
    set_checkin, get_checkin_users, update_last_checkin,
    log_crisis_event, set_crisis_response, set_crisis_protective_factors,
    get_active_crisis, bump_crisis_stage, resolve_crisis, set_stage3_at, get_crisis_stage,
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
    start_questionnaire_session, get_active_questionnaire_session,
    get_questionnaire_session, record_questionnaire_response,
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

# ────────────────────────────────────────────────────────────────────────────

def _minimal_reviewer_payload(uid: int, eid, note: str) -> str:
    """PR 1B-1: the ONLY payload a CLINICIAN_REVIEWER ever receives — no message
    text, no username, no risk categories beyond the fixed `note` label. Enough
    to know a clinical review is needed, nothing more."""
    return f"🔔 Clinical review needed\ntester_id: {uid}\nevent_id: {eid}\nnote: {note}"


_CLOSED_TEST_TEXT = {
    "ru": "Сейчас доступ к X20 ограничен приглашёнными участниками закрытого "
          "тестирования. Если тебе тяжело прямо сейчас — напиши это здесь, "
          "экстренная поддержка работает для всех.",
    "en": "X20 access is currently limited to invited participants of a closed "
          "test. If you're struggling right now, write it here — crisis support "
          "still works for everyone.",
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
      - anything else without full access (UNKNOWN, CLINICIAN_REVIEWER, an
        acknowledged tester with no reviewer mapping, an invalid/public mode,
        etc.) -> the generic closed-test message.

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
                         user_text: str, risk: dict, lang: str) -> None:
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
    the message body itself, so no button is needed to deliver the number."""
    eid = None
    try:
        # Create the event first — its id is baked into the crisis screen buttons.
        eid = await log_crisis_event(uid, RED, risk["score"], risk["categories"],
                                     user_text[:300], lang, admin_notified=bool(ADMIN_USER_IDS))
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
        if role != access_control.UNKNOWN:
            await save_message(uid, "user", user_text, "crisis", lang,
                               risk["score"], risk["categories"])
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


async def pipeline(message: Message, user_text: str, fsm_state: FSMContext | None = None,
                   tg_user=None) -> None:
    """Complete X20 pipeline.

    tg_user: при вызове из callback-кнопки message.from_user — это бот,
    поэтому реальный пользователь передаётся явно (callback.from_user)."""
    u = tg_user or message.from_user
    uid, username, first_name = u.id, u.username or "", u.first_name or ""

    # 1. Detect language (pure, no I/O — safe to run before any access check)
    lang = detect_language(user_text)

    # 2. Risk detection (pure, no I/O)
    risk = detect_risk(user_text, lang)

    # 3.9 Active-crisis gate — while a recent crisis event is unresolved, the LLM
    # is OFF and we don't return to normal chat. Free text either keeps the crisis
    # screen (RED/ORANGE) or gently offers "Я в безопасности" (calm). The 24h
    # recency window in get_active_crisis bounds this so nobody is stuck forever.
    # Crisis-adjacent like the RED branch below — runs regardless of role/access,
    # structurally BEFORE the product-access gate.
    active = await get_active_crisis(uid)
    if active and not (tg_user is not None):
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
                                   risk["score"], risk["categories"])
            text, kb = crisis_screen(stage, lang, event_id)
            await send_crisis(message.answer, text, kb, lang, uid, event_id, "screen")
        return

    # 4. Crisis override (Epic 1 — Crisis Protocol; LLM is NEVER called here).
    # RED bypasses the product-access gate below entirely, for ANY role — the
    # crisis path must never be gated by access control.
    if classify(risk) == RED:
        await trigger_crisis(message, uid, username, user_text, risk, lang)
        return

    # 4.1 Product access gate — strictly AFTER both crisis paths above, and
    # BEFORE any ordinary product persistence (upsert_user/log_moderation/state/
    # profile/memory/LLM). UNKNOWN, CLINICIAN_REVIEWER, an unacknowledged
    # CLINICIAN_TESTER, or an acknowledged tester with no reviewer mapping all
    # get the closed-test/tester-acknowledgment screen instead, and NOTHING
    # ordinary is written about them.
    if not await ensure_full_access_or_closed_test(message, uid):
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
        await _resume_onboarding_card(message.chat.id, uid)
        return

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
                               risk["score"], risk["categories"])
            await save_message(uid, "assistant", disambig, "disambiguation", lang)
            await message.answer(disambig)
            await log_disambiguation(uid, user_text, phrase, signal)
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
                               variant, trajectory=trajectory)
    
    # 11. Select practice
    severity = "high" if risk["score"] >= 70 else ("low" if risk["score"] < 40 else "medium")
    practice = select_practice(scenario, stage, severity, lang)
    
    # 12. Log router decision
    await log_router_decision(uid, state, risk["score"], risk["categories"],
                               stage, readiness, capacity, scenario,
                               practice["id"], variant, ROUTER_VERSION)
    
    # 13. Memory
    await maybe_summarize(uid, client)
    summary, recent = await build_context(uid)
    
    # 14. Build messages
    system_prompt = get_system_prompt(scenario, lang)
    messages = [{"role": "system", "content": system_prompt}]
    if summary:
        messages.append({"role": "system", "content": f"Context:\n{summary}"})
    for role, content in recent:
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_text})
    
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

    # 17. Save & send (with a human-feeling typing pause, §7.2). The user
    # message carries its risk snapshot — the deterministic source for §4/§5.
    await save_message(uid, "user", user_text, scenario, lang,
                       risk["score"], risk["categories"])
    await save_message(uid, "assistant", answer, scenario, lang)
    await asyncio.sleep(typing_delay(answer))
    await message.answer(answer)

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


@dp.callback_query(F.data.startswith("crisis:"))
async def cb_crisis(callback: CallbackQuery):
    """Staged crisis escalation. 'still'/'cant_call' raise the monotonic stage in
    the DB (idempotent: a stale/double tap is a no-op); 'safe' resolves; 'call'/
    'contact'/'safe_place'/'contacted' help without changing the stage."""
    uid = callback.from_user.id
    username = callback.from_user.username or ""
    parts = callback.data.split(":")
    action = parts[1]
    lang = await get_user_language(uid)

    # event_id from callback (new 3-part) or resolve the active event (old 2-part
    # legacy buttons from messages sent before this deploy → backward compatible).
    event_id = None
    if len(parts) >= 3 and parts[2].isdigit():
        event_id = int(parts[2])
    if event_id is None:
        # checkpoint-2 round 3 item 1B: this DB-resolve is the ONLY part of
        # cb_crisis wrapped here -- the staged (3-part) path below is left
        # unwrapped so a real bug there still surfaces normally. A degraded-
        # fallback screen (item 1A) sends no buttons at all, but pre-existing
        # legacy 2-part callback_data ("crisis:safe"/"crisis:still") can still
        # arrive from messages sent before this deploy, and the DB may still
        # be unstable when the user taps it -- get_active_crisis must not be
        # allowed to raise past this handler.
        try:
            active = await get_active_crisis(uid)
        except Exception as e:
            print(f"[crisis] cb_crisis legacy-resolve FAILED: {type(e).__name__}: {e}")
            await callback.answer()
            return
        event_id = active[0] if active else None
    if event_id is None:
        await callback.answer()
        return

    if action == "safe":
        await resolve_crisis(event_id)
        await set_crisis_response(uid, "safe")
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
        changed = await bump_crisis_stage(event_id, target)   # atomic once-only
        if not changed:
            await callback.answer(); return                   # stale/double tap → no-op
        if target == 3:
            await set_stage3_at(event_id)
        await _send_admin_crisis_alert(uid, username, target, event_id)  # once
        await _show_stage(callback, target, lang, event_id)
        await callback.answer()
        return

    if action == "cant_call":
        changed = await bump_crisis_stage(event_id, 2)
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
    await callback.answer()

# ────────────────────────────────────────────────────────────────────────────

async def _render_onboarding_card(uid: int, chat_id: int, step: int, lang: str, *,
                                  message_id: int | None) -> None:
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
        privacy_policy_url=config.PRIVACY_POLICY_URL)
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


@dp.message(Command("start"))
async def cmd_start(message: Message):
    from datetime import datetime, timezone
    uid = message.from_user.id
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
                        message_id=active_state.get("card_message_id"))
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
                uid, message.chat.id, FIRST_STEP, lang, message_id=None)
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
    await _send_mood_entry(message, lang, text)


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


async def _send_mood_entry(target, lang: str, text: str) -> None:
    """Render the existing mood-selection entry (mood buttons + emotion-map row +
    the '⚠️ Я не терапевт.' line). Shared verbatim by cmd_start and the
    first-user onboarding Start button, so the mood entry stays byte-identical
    whichever path opened it."""
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
        await _send_mood_entry(callback.message, lang, text)
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
        # Open the existing mood-selection entry (first-time greeting text). No
        # therapeutic response is generated until the user chooses/writes.
        text, _ = get_onboarding(lang)
        await _send_mood_entry(callback.message, lang, text)
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
async def cmd_profile(message: Message):
    """§5 — show the user the deterministic profile the bot has built (no diagnoses)."""
    uid = message.from_user.id
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
    retained = [(t, v["row_count"]) for t, v in preview.items() if v["policy"] == "RETAIN"]
    retained_names = ", ".join(t for t, _ in retained)
    retained_rows = sum(n for _, n in retained)
    if lang == "ru":
        lines = [
            f"Будет удалено/анонимизировано записей: {to_delete} "
            "(переписка, профиль, дневники, журнал влияния и др.)."]
        if retained_rows:
            lines.append(
                f"\nЗаписи безопасности ({retained_names}): {retained_rows} шт. "
                "СОХРАНЯЮТСЯ — это требование политики безопасности/аудита "
                "кризисных событий, а не сбой удаления.")
        lines.append("\nПродолжить?")
        return "\n".join(lines)
    lines = [
        f"Rows to be deleted/anonymized: {to_delete} "
        "(messages, profile, journals, influence trace, etc.)."]
    if retained_rows:
        lines.append(
            f"\nSafety-audit records ({retained_names}): {retained_rows} row(s) are "
            "RETAINED by policy — that's not a deletion failure.")
    lines.append("\nContinue?")
    return "\n".join(lines)


def _privacy_delete_done_text(lang: str) -> str:
    retained = ", ".join(_privacy_retained_tables())
    if lang == "ru":
        return (
            "Готово. Личные данные удалены согласно политике конфиденциальности.\n"
            f"Записи безопасности ({retained}) сохранены — это требование политики "
            "безопасности, а не сбой удаления.")
    return (
        "Done. Personal data deleted per the privacy policy.\n"
        f"Safety-audit records ({retained}) are retained by policy — not a deletion "
        "failure.")


def _privacy_delete_kb(prefix: str, lang: str, uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=("🗑 Да, стереть всё" if lang == "ru" else "🗑 Yes, erase everything"),
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
        msg = _privacy_delete_done_text(lang)
    else:
        msg = "Отменено." if lang == "ru" else "Cancelled."
    await callback.message.answer(msg)
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
    import json, io
    from aiogram.types import BufferedInputFile
    uid = message.from_user.id
    scoped_access.assert_can_read_user_data(uid, uid, "privacy_export")
    lang = await get_user_language(uid)
    data = await export_all_personal_data(uid)
    if not any(data.values()):
        await message.answer(
            "Персональных данных пока нет." if lang == "ru" else "No personal data yet.")
        return
    retained = ", ".join(_privacy_retained_tables())
    note = (
        f"\n\nПримечание: записи безопасности ({retained}) включены в этот экспорт, "
        "но сохраняются по политике безопасности и НЕ удаляются командой "
        "/privacy_delete_all или /forget_all." if lang == "ru" else
        f"\n\nNote: safety-audit records ({retained}) are included in this export but "
        "are RETAINED by policy — they are NOT removed by /privacy_delete_all or "
        "/forget_all.")
    buf = io.BytesIO(json.dumps(data, ensure_ascii=False, indent=2, default=str).encode("utf-8"))
    await message.answer_document(
        BufferedInputFile(buf.getvalue(), filename="x20_privacy_export.json"),
        caption=("Полный экспорт твоих данных (JSON)." if lang == "ru" else
                 "Full export of your data (JSON).") + note)


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
async def cmd_help(message: Message):
    """Static, role-unaware by design (PR 1B post-stabilization cleanup):
    /privacy_export_all and /privacy_delete_all are real self-service rights
    every caller has regardless of role, so they belong here. /review_pack is
    deliberately NOT listed -- it's a reviewer/owner tool, not normal
    self-service; safe to call (denial is generic) but unnecessary UX noise
    for the ordinary-user audience this static string is written for.
    Reviewers are briefed out-of-band. Role-aware /help can be revisited
    later if that stops being sufficient.

    /menu IS listed (unlike /questionnaire, which stays hidden because it has
    no configured content yet) -- /menu is the discoverable navigation hub
    and hiding it would defeat its purpose."""
    lang = await get_user_language(message.from_user.id)
    await message.answer(
        ("/start • /menu • /checkin • /time • /memory • /profile • /forget_all • "
         "/privacy_export_all • /privacy_delete_all • /mute • /unmute • /help"),
        reply_markup=ReplyKeyboardRemove())

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
    intro = ("📝 Дневник эмоций. Отвечай как есть, в любой момент — /journal_cancel.\n\n"
             if lang == "ru" else
             "📝 Emotion journal. Answer freely; stop anytime with /journal_cancel.\n\n")
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


@dp.message(EmotionJournal.active, F.text)
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
    intro = ("📘 КПТ-дневник. Ты сам(а) формулируешь мысли — я только записываю. "
             "Остановиться — /journal_cancel.\n\n" if lang == "ru" else
             "📘 CBT journal. You reframe your own thought — I just record. "
             "Stop with /journal_cancel.\n\n")
    await message.answer(intro + journals.cbt_prompt("situation", lang))


@dp.message(CbtJournal.active, F.text)
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
async def cmd_report(message: Message, state: FSMContext):
    uid = message.from_user.id
    if not await ensure_full_access_or_closed_test(message, uid):
        return
    lang = await get_user_language(uid)
    emo = await get_emotion_entries_since(uid, 7)
    chk = await get_checkin_logs_since(uid, 7)
    await message.answer(journals.build_weekly_report(emo, chk, lang))


@dp.message(Command("journal"))
async def cmd_journal(message: Message, state: FSMContext):
    if not await ensure_full_access_or_closed_test(message, message.from_user.id):
        return
    lang = await get_user_language(message.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Дневник эмоций", callback_data="jhub:emotion")],
        [InlineKeyboardButton(text="📘 КПТ-дневник", callback_data="jhub:cbt")],
        [InlineKeyboardButton(text="📊 Мой отчёт", callback_data="jhub:report")],
        [InlineKeyboardButton(text="⚙️ Напоминания", callback_data="jhub:settings")],
        [InlineKeyboardButton(text="🚨 Срочно плохо", callback_data="jhub:crisis")],
    ])
    await message.answer("Дневники X20. Что откроем?" if lang == "ru"
                         else "X20 journals. What shall we open?", reply_markup=kb)


@dp.callback_query(F.data.startswith("jhub:"))
async def cb_jhub(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split(":")[1]
    await callback.answer()
    if action == "emotion":
        await cmd_emotion(callback.message, state, tg_user=callback.from_user)
    elif action == "cbt":
        await cmd_cbt(callback.message, state, tg_user=callback.from_user)
    elif action == "report":
        await cmd_report(callback.message, state)
    elif action == "settings":
        await cmd_journal_settings(callback.message, state)
    elif action == "crisis":
        lang = await get_user_language(callback.from_user.id)
        # Legacy manual-crisis screen (no staged event → eid=None).
        await send_crisis(callback.message.answer, get_crisis_text(lang),
                          crisis_keyboard(lang), lang, callback.from_user.id,
                          None, "manual")


@dp.message(Command("journal_settings"))
async def cmd_journal_settings(message: Message, state: FSMContext):
    uid = message.from_user.id
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
    await message.answer(
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
        await cmd_journal_settings(callback.message, state)
    elif what == "tz":
        await callback.message.answer("Выбери свой часовой пояс:",
                                      reply_markup=tz_picker_keyboard())
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
    await callback.message.answer(f"Ок, твой пояс: UTC{offset:+d}.")


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
    buf.name = "x20_journals.json"
    from aiogram.types import BufferedInputFile
    await message.answer_document(BufferedInputFile(buf.getvalue(), filename="x20_journals.json"),
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
#   q:a:<sid>:<step>:<aid> answer
#   q:b:<sid>              back
#   q:p:<sid>              pause/continue later
#   q:x:<sid>              cancel
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
    return [
        InlineKeyboardButton(text=("⬅️ Назад" if lang == "ru" else "⬅️ Back"),
                             callback_data=f"q:b:{session_id}"),
        InlineKeyboardButton(text=("✖️ Прервать" if lang == "ru" else "✖️ Cancel"),
                             callback_data=f"q:x:{session_id}"),
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
    rows.append([
        InlineKeyboardButton(text=("⬅️ Назад" if lang == "ru" else "⬅️ Back"),
                             callback_data=f"q:b:{session_id}"),
        InlineKeyboardButton(text=("✖️ Прервать" if lang == "ru" else "✖️ Cancel"),
                             callback_data=f"q:x:{session_id}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _questionnaire_list_keyboard(lang: str) -> InlineKeyboardMarkup:
    # Professional catalog root: 6 categories, one button per row, then a
    # back-to-menu row. Category ids are short (well under 64 bytes).
    rows = [[InlineKeyboardButton(text=questionnaire_ux.catalog_category_label(key, lang),
                                  callback_data=f"q:c:{key}")]
            for key, _, _ in questionnaire_ux.CATALOG_CATEGORIES]
    rows.append([InlineKeyboardButton(text=("⬅️ Назад" if lang == "ru" else "⬅️ Back"),
                                      callback_data="menu:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _questionnaire_category_keyboard(category: str, definitions: list, lang: str) -> InlineKeyboardMarkup:
    # self_observation: synthetic registry demos -> real detail/start flow.
    rows = [[InlineKeyboardButton(text=d["title"], callback_data=f"q:d:{d['id']}")]
            for d in definitions]
    rows.append([InlineKeyboardButton(text=("⬅️ Назад" if lang == "ru" else "⬅️ Back"),
                                      callback_data="q:l")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _catalog_manifest_category_keyboard(instruments, lang: str) -> InlineKeyboardMarkup:
    # Manifest categories 1-4: each instrument is an INFO entry (q:i:<id>),
    # never a start path. One button per row, then back to the catalog root.
    rows = [[InlineKeyboardButton(
                text=(ci.title_ru if lang == "ru" else ci.title_en) or ci.abbreviation,
                callback_data=f"q:i:{ci.instrument_id}")]
            for ci in instruments]
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


def _questionnaire_detail_keyboard(qid: str, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=("▶️ Начать" if lang == "ru" else "▶️ Start"),
                              callback_data=f"q:s:{qid}")],
        [InlineKeyboardButton(text=("⬅️ К списку" if lang == "ru" else "⬅️ To the list"),
                              callback_data="q:l")],
    ])


def _questionnaire_completion_keyboard(session_id: int, lang: str) -> InlineKeyboardMarkup:
    # PR C1.1: added the specialist-report button (q:o:<sid>) as its own row,
    # ahead of the navigation buttons -- this is the flag-off/ineligible path,
    # where the report still renders (all answers, no score line; see q:o's
    # own conditional-score-line logic in cb_questionnaire_specialist_report).
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=("🧾 Отчёт специалисту" if lang == "ru" else "🧾 Specialist report"),
                              callback_data=f"q:o:{session_id}")],
        [InlineKeyboardButton(text=("⬅️ Другой опросник" if lang == "ru" else "⬅️ Another questionnaire"),
                              callback_data="q:l")],
        [InlineKeyboardButton(text=("🏠 В меню" if lang == "ru" else "🏠 To the menu"),
                              callback_data="menu:back")],
    ])


def _dass21_completion_keyboard(session_id: int, lang: str) -> InlineKeyboardMarkup:
    # Workstream B — same specialist-report row as _questionnaire_completion_
    # keyboard, plus the discuss-result row (q:m:<sid>), gated entirely by
    # config.DASS21_DISCUSSION_ENABLED at the _send_dass21_result call site
    # (default off -- the plain _questionnaire_completion_keyboard is used
    # instead, byte-for-byte unchanged from before this PR).
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=("🧾 Отчёт специалисту" if lang == "ru" else "🧾 Specialist report"),
                              callback_data=f"q:o:{session_id}")],
        [InlineKeyboardButton(text=("💬 Обсудить результат" if lang == "ru" else "💬 Discuss the result"),
                              callback_data=f"q:m:{session_id}")],
        [InlineKeyboardButton(text=("⬅️ Другой опросник" if lang == "ru" else "⬅️ Another questionnaire"),
                              callback_data="q:l")],
        [InlineKeyboardButton(text=("🏠 В меню" if lang == "ru" else "🏠 To the menu"),
                              callback_data="menu:back")],
    ])


# ── PR B — result / calculations / explanation screens (dormant unless
# config.QUESTIONNAIRE_INTERPRETATION_ENABLED is true AND the definition is
# eligible; see questionnaires.is_result_eligible). PR C1.1 added the
# specialist-report button (q:o:<sid>) below. PR C2.1 wires the
# discuss-with-bot entry point (q:m:<sid>, bare menu format) into this
# keyboard only -- see cb_questionnaire_discuss_menu, unchanged from C2.

def _questionnaire_result_keyboard(session_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=("📊 Расчёты" if lang == "ru" else "📊 Calculations"),
                              callback_data=f"q:k:{session_id}"),
         InlineKeyboardButton(text=("🧠 Что значат шкалы" if lang == "ru" else "🧠 What scales mean"),
                              callback_data=f"q:e:{session_id}")],
        [InlineKeyboardButton(text=("🧾 Отчёт специалисту" if lang == "ru" else "🧾 Specialist report"),
                              callback_data=f"q:o:{session_id}")],
        [InlineKeyboardButton(text=("💬 Обсудить результат" if lang == "ru" else "💬 Discuss result"),
                              callback_data=f"q:m:{session_id}")],
        [InlineKeyboardButton(text=("⬅️ Другой опросник" if lang == "ru" else "⬅️ Another questionnaire"),
                              callback_data="q:l")],
        [InlineKeyboardButton(text=("🏠 В меню" if lang == "ru" else "🏠 To the menu"),
                              callback_data="menu:back")],
    ])


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
        keyboard = (_dass21_completion_keyboard(session_id, lang)
                    if config.DASS21_DISCUSSION_ENABLED
                    else _questionnaire_completion_keyboard(session_id, lang))
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


async def _send_dass21_back_to_result(send, session: dict, lang: str) -> None:
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
    keyboard = (_dass21_completion_keyboard(session["id"], lang)
                if config.DASS21_DISCUSSION_ENABLED
                else _questionnaire_completion_keyboard(session["id"], lang))
    await send(questionnaire_ux.dass21_result_text(result.subscales, lang), reply_markup=keyboard)


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
    text = questionnaire_ux.question_text(step, total, item["text"], lang,
                                          options=item.get("options"))
    keyboard = _questionnaire_item_keyboard(definition, session_id, step, item, lang)
    if len(text) > _QUESTIONNAIRE_CARD_MAXLEN:
        # Deterministic safe fallback (never a silent truncation of the
        # protected wording): drop the in-card legend and show the FULL labels
        # on the buttons instead -- the pre-#57 layout.
        text = questionnaire_ux.question_text(step, total, item["text"], lang)
        keyboard = _questionnaire_full_label_keyboard(definition, session_id, step, item, lang)
    await send(text, reply_markup=keyboard)


@dp.message(Command("dass21"))
async def cmd_dass21(message: Message):
    """PR #55 — owner-only entry to the exact DASS-21 flow. Routes to the
    EXISTING q:d detail screen (never creates a session directly); every
    downstream step re-runs the same fresh gates. Disabled feature and
    non-owner get the SAME neutral text -- no existence disclosure."""
    uid = message.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(message, uid, lang):
        return
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
    await message.answer(questionnaire_ux.detail_text(definition, lang),
                         reply_markup=_questionnaire_detail_keyboard(qid, lang))


@dp.message(Command("questionnaire"))
async def cmd_questionnaire(message: Message):
    uid = message.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(message, uid, lang):
        return
    await message.answer(questionnaire_ux.list_text(lang), reply_markup=_questionnaire_list_keyboard(lang))


@dp.callback_query(F.data == "q:l")
async def cb_questionnaire_list(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(callback, uid, lang):
        return
    await callback.message.answer(questionnaire_ux.list_text(lang), reply_markup=_questionnaire_list_keyboard(lang))
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

    # ── Categories 1-4: governance-manifest instruments as INFO entries ──
    if category in questionnaire_ux.CATALOG_MANIFEST_CATEGORY_IDS:
        document = _load_catalog_document()
        instruments = (
            clinical_instrument_catalog.catalog_instruments_by_category(document, category)
            if document is not None else ())
        # PR #59 — PER-USER conditional DASS entry under "Стресс". Shown ONLY
        # when the invited rollout flag is on AND this exact user is
        # authorized (owner or active invited). DASS never becomes globally
        # public: public_catalog_visible stays false, unknown/blocked users
        # see nothing, and the q:d/q:s/q:a/q:b gates re-authorize anyway.
        extra_rows = []
        if (category == "stress" and config.DASS21_INVITED_USERS_ENABLED):
            decision = await dass21_access.authorize_dass21_user(uid)
            if decision.allowed:
                extra_rows.append([InlineKeyboardButton(
                    text=("DASS-21 — депрессия, тревога, стресс" if lang == "ru"
                          else "DASS-21 — depression, anxiety, stress"),
                    callback_data=f"q:d:{dass21_runtime.DASS21_DEFINITION_ID}")])
        if not instruments and not extra_rows:
            await callback.message.answer(
                questionnaire_ux.catalog_empty_text(category, lang),
                reply_markup=_catalog_nav_only_keyboard(lang))
            await callback.answer()
            return
        keyboard = _catalog_manifest_category_keyboard(instruments, lang)
        if extra_rows:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=extra_rows + keyboard.inline_keyboard)
        await callback.message.answer(
            questionnaire_ux.catalog_category_text(category, lang),
            reply_markup=keyboard)
        await callback.answer()
        return

    # ── Category 6: consultation report (user-owned, never auto-sent) ──
    if category == "consultation_report":
        await callback.message.answer(
            questionnaire_ux.consultation_report_text(lang),
            reply_markup=_catalog_nav_only_keyboard(lang))
        await callback.answer()
        return

    # ── Category 5: self_observation -> ONLY definitions explicitly assigned
    # to the self-observation product surface (registry category "selfobs").
    # Deliberately NOT unfiltered list_active(): a future real clinical
    # definition (category anxiety/mood/depression/etc.) must never surface
    # here and bypass the manifest catalog's availability double-gate via the
    # old q:d/q:s route. "restricted" legal_status stays hidden from listings
    # (blocked at start/answer time too).
    if category == "self_observation":
        registry = _load_registry_fresh()
        # Ordinary nonclinical self-observation surface ONLY. A definition
        # carrying clinical_instrument metadata must never appear here even if
        # it is (accidentally or maliciously) tagged category="selfobs" -- it
        # is reachable only through the manifest-driven clinical catalog after
        # all combined gates pass. Exclude any clinical-metadata-bearing
        # definition, in addition to the existing category + restricted filter.
        definitions = [d for d in registry.list_active("selfobs")
                       if d.get("legal_status") != "restricted"
                       and not isinstance(d.get("clinical_instrument"), dict)]
        if not definitions:
            await callback.message.answer(
                questionnaire_ux.catalog_empty_text(category, lang),
                reply_markup=_catalog_nav_only_keyboard(lang))
            await callback.answer()
            return
        await callback.message.answer(
            questionnaire_ux.catalog_category_text(category, lang),
            reply_markup=_questionnaire_category_keyboard(category, definitions, lang))
        await callback.answer()
        return

    # Unknown/stale category id: neutral empty screen, never a dead end.
    await callback.message.answer(
        questionnaire_ux.catalog_empty_text(category, lang),
        reply_markup=_catalog_nav_only_keyboard(lang))
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
    ci = (clinical_instrument_catalog.get_catalog_instrument(document, instrument_id)
          if document is not None else None)
    if ci is None:
        # Unknown id, or a hidden instrument (JAPS/STAS, identity incomplete/
        # conflict) -- never rendered. Neutral fail-closed message.
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return

    # q:i is PERMANENTLY read-only: it NEVER creates a session, never saves
    # answers, never calls start_questionnaire_session/_send_questionnaire_step.
    # The availability double-gate only decides whether to render a "Пройти"
    # button that routes into the EXISTING q:d:<definition_id> detail flow --
    # those existing handlers remain the only code that creates sessions.
    # catalog_start_definition_id returns None unless the manifest entry is
    # fully activatable AND carries an explicit questionnaire_definition_id
    # that the registry can start; no entry is 'ready' in this PR, so this is
    # always None here.
    start_definition_id = None
    if ci.availability == clinical_instrument_catalog.AVAILABILITY_AVAILABLE:
        raw = next((i for i in document.get("instruments", [])
                    if i.get("instrument_id") == instrument_id), None)
        if raw is not None:
            # catalog_start_definition_id now performs the FULL combined gate
            # (manifest activatable + Core can_start + clinical linkage VALID)
            # against this same fresh manifest `document` -- no second
            # authorization check is needed here. Fails closed to no button;
            # q:i stays read-only regardless.
            start_definition_id = clinical_instrument_catalog.catalog_start_definition_id(
                raw, _load_registry_fresh(), document)

    await callback.message.answer(
        questionnaire_ux.instrument_info_text(ci, lang),
        reply_markup=_catalog_info_keyboard(ci.category_id, lang,
                                            start_definition_id=start_definition_id))
    await callback.answer()


@dp.callback_query(F.data.startswith("q:d:"))
async def cb_questionnaire_detail(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(callback, uid, lang):
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer()
        return
    qid = parts[2]
    registry = _load_registry_fresh()
    definition = registry.get(qid)
    if definition is None or definition.get("status") != "active" or definition.get("legal_status") == "restricted":
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
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
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return
    if await _dass21_blocked(qid, uid):
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return
    await callback.message.answer(questionnaire_ux.detail_text(definition, lang),
                                  reply_markup=_questionnaire_detail_keyboard(qid, lang))
    await callback.answer()


@dp.callback_query(F.data.startswith("q:s:"))
async def cb_questionnaire_start(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _questionnaire_gate(callback, uid, lang):
        return
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
    if active:
        if (active["questionnaire_id"] != definition["id"]
                or active["questionnaire_version"] != definition["version"]):
            await callback.message.answer(questionnaire_ux.not_available_text(lang))
            await callback.answer()
            return
        await _send_questionnaire_step(_edit_or_answer(callback.message), definition, active["id"],
                                       active["current_index"], lang)
        await callback.answer()
        return

    session_id = await start_questionnaire_session(uid, definition["id"], definition["version"])
    await _send_questionnaire_step(_edit_or_answer(callback.message), definition, session_id, 0, lang)
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


@dp.callback_query(F.data.startswith("q:p:"))
async def cb_questionnaire_pause(callback: CallbackQuery):
    """Pause / continue later: no-op on session state (current_index already
    persists the resume point on every answer) -- just acknowledges and shows
    a neutral confirmation, without ending the session like cancel does."""
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

    await callback.message.answer(
        "Опрос сохранён, можно продолжить позже через /questionnaire." if lang == "ru"
        else "Progress saved -- continue later with /questionnaire.")
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
async def cb_questionnaire_result(callback: CallbackQuery):
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
        await _send_dass21_back_to_result(callback.message.answer, session, lang)
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

    completed_at = session.get("completed_at") if isinstance(session, dict) else None

    report = questionnaire_ux.specialist_report_text(
        definition["title"], completed_at, answer_lines, score_line, lang)
    await callback.message.answer(report)
    await callback.answer()


# ── PR C2 — discuss-with-bot via A1 / traced_response_builder ───────────────
# Callback format: q:m:<sid> bare menu (NO LLM call at all), q:m:<sid>:<topic>
# ("why"|"next"|"specialist") ONE bounded traced LLM reply, no continuation.
# The user never types free text in this flow -- each topic button sends
# exactly one fixed, template-driven prompt (built from title/score/
# intensity_label/topic_id, never raw stored answer text) and ends the flow.
# Tapping another topic is a fresh independent callback. NO FSM, no multi-turn
# state.
#
# Because there is no user-typed text anywhere in this flow, this code
# deliberately does NOT call risk_detector.detect_risk anywhere -- a future PR
# adding free-text continuation would need to add RED/ORANGE risk handling
# THEN, not now. See CLAUDE.md / this PR's description for this scope note.
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


def _dass21_discuss_menu_keyboard(session_id: int, lang: str) -> InlineKeyboardMarkup:
    # Workstream B corrective pass — DASS-specific labels: unlike the generic
    # "Why did this come out this way?" button, none of these imply the
    # questionnaire result ESTABLISHES a cause (see questionnaire_ux.
    # dass21_discuss_topic_prompt's non-causal boundary instruction).
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=("📊 Что измеряют эти шкалы?" if lang == "ru" else "📊 What do these scales measure?"),
            callback_data=f"q:m:{session_id}:measures")],
        [InlineKeyboardButton(
            text=("🔎 Как это может быть связано с последней неделей?" if lang == "ru"
                  else "🔎 How might this relate to the past week?"),
            callback_data=f"q:m:{session_id}:relate")],
        [InlineKeyboardButton(
            text=("➡️ Что можно сделать дальше?" if lang == "ru" else "➡️ What can I do next?"),
            callback_data=f"q:m:{session_id}:next")],
        [InlineKeyboardButton(
            text=("👩‍⚕️ Вопросы специалисту" if lang == "ru" else "👩‍⚕️ Questions for a specialist"),
            callback_data=f"q:m:{session_id}:specialist")],
        [InlineKeyboardButton(
            text=("⬅️ Назад к результату" if lang == "ru" else "⬅️ Back to result"),
            callback_data=f"q:r:{session_id}")],
        [InlineKeyboardButton(
            text=("🏠 В меню" if lang == "ru" else "🏠 To the menu"),
            callback_data="menu:back")],
    ])


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
async def cb_questionnaire_discuss_menu(callback: CallbackQuery):
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
    if is_dass:
        ok = (await _dass21_discuss_gate_and_load(session, lang)) is not None
    else:
        ok = (await _discuss_gate_and_load(session, lang)) is not None    # 4-6
    if not ok:
        await callback.message.answer(questionnaire_ux.not_available_text(lang))
        await callback.answer()
        return

    # Deterministic menu -- NO LLM call at all.
    keyboard = (_dass21_discuss_menu_keyboard(session_id, lang) if is_dass
                else _discuss_menu_keyboard(session_id, lang))
    await callback.message.answer(questionnaire_ux.discuss_menu_text(lang), reply_markup=keyboard)
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
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _hub_back_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=("⬅️ В меню" if lang == "ru" else "⬅️ Back to menu"), callback_data="menu:back")]])


async def _answer_target(entity, text: str, **kw) -> None:
    target = entity.message if isinstance(entity, CallbackQuery) else entity
    await target.answer(text, **kw)


@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    uid = message.from_user.id
    lang = await get_user_language(uid)
    if not await _nav_gate(message, uid, lang):
        return
    await message.answer(navigation.menu_text(lang), reply_markup=_menu_keyboard(lang))


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
async def cb_journals_hub(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _nav_gate(callback, uid, lang):
        return
    await _answer_target(callback, navigation.journals_hub_text(lang), reply_markup=_hub_back_keyboard(lang))
    await callback.answer()


@dp.callback_query(F.data == "results:hub")
async def cb_results_hub(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _nav_gate(callback, uid, lang):
        return
    await _answer_target(callback, navigation.results_hub_text(lang), reply_markup=_hub_back_keyboard(lang))
    await callback.answer()


@dp.callback_query(F.data == "privacy:hub")
async def cb_privacy_hub(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _nav_gate(callback, uid, lang):
        return
    await _answer_target(callback, navigation.privacy_hub_text(lang), reply_markup=_hub_back_keyboard(lang))
    await callback.answer()


@dp.callback_query(F.data == "about:hub")
async def cb_about_hub(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _nav_gate(callback, uid, lang):
        return
    await _answer_target(callback, navigation.about_hub_text(lang), reply_markup=_hub_back_keyboard(lang))
    await callback.answer()


@dp.callback_query(F.data == "menu:back")
async def cb_menu_back(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if not await _nav_gate(callback, uid, lang):
        return
    await _answer_target(callback, navigation.menu_text(lang), reply_markup=_menu_keyboard(lang))
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
    lang = await get_user_language(message.from_user.id)   # needed for Whisper lang hint
    await bot.send_chat_action(message.chat.id, "typing")
    try:
        text = await transcribe_voice(message.voice, bot, client, lang)
        await message.answer(f"🎤 <i>{_he(text)}</i>", parse_mode="HTML")
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
    start_dashboard()
    scheduler = setup_scheduler(bot)
    scheduler.start()
    print("✅ X20 Bot started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
