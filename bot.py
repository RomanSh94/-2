"""
X20 Bot — Основной файл

Полный pipeline:
  Risk → Language → Stage → State → Readiness → Capacity → Scenario → 
  RelationshipMonitor → PracticeSelect → Memory → LLM → SafetyValidator → 
  Notifications → OutcomeTracking → User
"""
import asyncio
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
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from openai import AsyncOpenAI

import access_control
import scoped_access
import review_pack
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
from language_detector import detect_language
from stage_detector import detect_stage
from state_engine import (
    DEFAULT_STATE, update_state, choose_scenario, get_emotional_trajectory,
    check_sudden_improvement,
)
from psychology_profile import maybe_update_profile, format_profile_for_user
from readiness_engine import assess_readiness
from cognitive_capacity import get_capacity
from relationship_monitor import monitor_relationship
from practice_registry import select_practice, get_practice_by_id
from safety_validator import (
    validate_response,
    validate_response_with_context, select_fallback,
)
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
    cancel_questionnaire_session,
)
import questionnaires
import navigation
import emotion_map

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

    # 3.5 Dependency monitor
    # record_message MUST come first so the current message is counted
    # before the threshold check — otherwise the 100th message never triggers.
    await dependency_monitor.record_message(uid)
    dep_msg = await dependency_monitor.check_dependency(uid, lang)
    if dep_msg:
        await message.answer(dep_msg)

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
    
    # 10. Check dependency
    dep_resp = monitor_relationship(user_text, lang)
    if dep_resp:
        await message.answer(dep_resp)
        return
    
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
                             reply_markup=score_kb(f"before:{practice['id']}:{scenario}:{lang}"))

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
    parts = callback.data.split(":")
    practice_id, scenario, lang, score = parts[1], parts[2], parts[3], int(parts[4])

    state = await load_state(uid) or dict(DEFAULT_STATE)
    fdata = await fsm_state.get_data()
    intervention_id = await start_intervention(
        uid, scenario, scenario, practice_id, PRACTICE_VERSION,
        {"state": state}, score,
        fdata.get("stage", "OPEN"),
        fdata.get("readiness", "MEDIUM"),
        fdata.get("capacity", get_capacity(state)),
        get_variant(uid), ROUTER_VERSION,
    )
    await fsm_state.update_data(
        intervention_id=intervention_id,
        practice_id=practice_id,
        lang=lang,
        scenario=scenario,
        before_score=score,
    )
    await fsm_state.set_state(InterventionStates.awaiting_after)

    practice = get_practice_by_id(practice_id, lang)
    if practice:
        steps = "\n".join(f"{i}. {s}" for i, s in enumerate(practice["steps"], 1))
        await callback.message.answer(f"<b>{_he(practice['name'])}</b>\n\n{_he(steps)}", parse_mode="HTML")
        await asyncio.sleep(1)
        await callback.message.answer(
            ("Как теперь?" if lang == "ru" else "How now?"),
            reply_markup=score_kb("after"))
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

@dp.message(Command("start"))
async def cmd_start(message: Message):
    from datetime import datetime, timezone
    uid = message.from_user.id
    if not await ensure_full_access_or_closed_test(message, uid):
        return
    overview = await get_memory_overview(uid)          # before upsert: 0 msgs == first time
    is_first = overview["message_count"] == 0
    await upsert_user(uid, message.from_user.username or "", message.from_user.first_name or "")
    lang = await get_user_language(uid)
    text, buttons = get_onboarding(lang)
    # §7.1 returning users get a time-varied greeting — in their LOCAL time, not
    # UTC (otherwise a daytime user gets a "поздно, не спится?" night line).
    if not is_first:
        tz_off, tz_set, ulang = await get_user_tz(uid)
        local_hour = (datetime.now(timezone.utc).hour + effective_tz(tz_off, tz_set, ulang)) % 24
        text = pick_greeting(False, local_hour, lang)
    # Inline-кнопки вместо reply-клавиатуры: iOS прячет reply-клавиатуру за
    # иконкой у поля ввода, и пользователи её не видят. Inline видна везде.
    # Onboarding asks "как ты себя чувствуешь" -- Emotion Map helper row added
    # (deterministic vocabulary aid, not a new gate/flow; opening it never
    # stores anything, see cb_emotion_map).
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=b, callback_data=f"mood:{i}")]
        for i, b in enumerate(buttons)
    ] + [[InlineKeyboardButton(
        text=("🗺 Карта эмоций" if lang == "ru" else "🗺 Emotion map"), callback_data="emotion:map")]])
    await message.answer(text + "\n\n⚠️ " + ("Я не терапевт." if lang == "ru" else "I'm not a therapist."),
                         reply_markup=kb)


@dp.callback_query(F.data.startswith("mood:"))
async def cb_mood(callback: CallbackQuery, state: FSMContext):
    """Кнопка состояния из онбординга → обычный проход по pipeline."""
    lang = await get_user_language(callback.from_user.id)
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


# ── Questionnaire Core PR #1 — storage-only, non-diagnostic self-report ────────
# Deliberately NOT in /help (infrastructure-first, not a discoverability
# feature yet). NOT self-service like the privacy commands -- this is an
# ordinary product feature, gated the same way as /emotion, /cbt: the active-
# crisis check (journal_guard, text=None) runs BEFORE the product-access gate,
# exactly like cmd_emotion, so a crisis is never blocked by access status.
# Callback format is deliberately short ("q:a:<session_id>:<answer_id>" /
# "q:c:<session_id>") to stay comfortably under Telegram's 64-byte
# callback_data limit -- the current item is looked up from the session's
# current_index in the DB, never encoded in the callback itself.

def _questionnaire_consent_text(lang: str) -> str:
    if lang == "ru":
        return ("Это структурированный самоопрос — не диагноз и не замена специалиста. "
                "Можно остановиться в любой момент. Ответы сохраняются: их можно "
                "экспортировать через /privacy_export_all и удалить через /privacy_delete_all.")
    return ("This is a structured self-check — not a diagnosis and not a substitute "
            "for a professional. You can stop anytime. Answers are saved: you can "
            "export them with /privacy_export_all and delete them with /privacy_delete_all.")


def _questionnaire_not_configured_text(lang: str) -> str:
    # Deliberately identical for BOTH "not_configured" and "invalid" loader
    # outcomes -- malformed JSON / multiple private files / a risk-bearing
    # definition being rejected / a plain missing directory must never be
    # distinguishable to the Telegram user; those are internal/test facts only.
    return "Опросники пока не настроены." if lang == "ru" else "Questionnaires are not configured yet."


def _questionnaire_completion_text(lang: str) -> str:
    # Fixed generic text -- deliberately NOT the private definition's own
    # completion_message, which has not been validated against the forbidden
    # diagnosis/dependency wording list. Using it unvalidated here would be
    # exactly the kind of interpretation-adjacent claim this PR must not make.
    if lang == "ru":
        return "Спасибо, ответы сохранены. Это не диагноз — просто структурированная самооценка."
    return "Thanks, your answers are saved. This is not a diagnosis — just structured self-reflection."


def _questionnaire_cancelled_text(lang: str) -> str:
    return "Опрос отменён." if lang == "ru" else "Questionnaire cancelled."


def _questionnaire_item_keyboard(session_id: int, item: dict, lang: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=opt["label"], callback_data=f"q:a:{session_id}:{opt['id']}")]
            for opt in item["options"]]
    rows.append([InlineKeyboardButton(
        text=("Отмена" if lang == "ru" else "Cancel"), callback_data=f"q:c:{session_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_questionnaire_step(send, session_id: int, index: int, definition: dict, lang: str) -> None:
    """Send the item at `index`, or complete the session if none remains.
    `send` is message.answer / callback.message.answer, matching the existing
    project convention (see send_crisis's `send` parameter)."""
    item = questionnaires.get_item(definition, index)
    if item is None:
        await complete_questionnaire_session(session_id)
        await send(_questionnaire_completion_text(lang))
        return
    await send(item["text"], reply_markup=_questionnaire_item_keyboard(session_id, item, lang))


@dp.message(Command("questionnaire"))
async def cmd_questionnaire(message: Message):
    uid = message.from_user.id
    lang = await get_user_language(uid)
    # Active-crisis check BEFORE the product gate -- same order as cmd_emotion.
    decision, _ = await journal_guard(message, uid, lang)
    if decision == "crisis":
        return
    if not await ensure_full_access_or_closed_test(message, uid):
        return

    definition, error = questionnaires.get_validated_definition()
    if error is not None:
        await message.answer(_questionnaire_not_configured_text(lang))
        return

    active = await get_active_questionnaire_session(uid)
    if active:
        # Resume ONLY if the current definition still matches the session's
        # recorded id/version exactly -- any mismatch (private file changed,
        # removed, or replaced since the session started) fails closed rather
        # than silently resuming against a different definition, and never
        # creates a second active session.
        if (active["questionnaire_id"] != definition["id"]
                or active["questionnaire_version"] != definition["version"]):
            await message.answer(_questionnaire_not_configured_text(lang))
            return
        await _send_questionnaire_step(
            message.answer, active["id"], active["current_index"], definition, lang)
        return

    await message.answer(_questionnaire_consent_text(lang))
    session_id = await start_questionnaire_session(uid, definition["id"], definition["version"])
    await _send_questionnaire_step(message.answer, session_id, 0, definition, lang)


@dp.callback_query(F.data.startswith("q:a:"))
async def cb_questionnaire_answer(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    # Active-crisis gate FIRST -- before format/session/definition checks,
    # before storing anything, before advancing the session. An answer
    # callback is a STEP of an in-progress flow, same class as
    # emotion_step/cbt_step; the project invariant is that an active crisis
    # blocks the step before any ordinary behavior continues, so this must
    # run here too, not only at /questionnaire's own start.
    decision, _ = await journal_guard(callback.message, uid, lang)
    if decision == "crisis":
        await callback.answer()
        return

    parts = callback.data.split(":")
    if len(parts) != 4 or not parts[2].isdigit():
        await callback.answer()
        return
    session_id, answer_id = int(parts[2]), parts[3]

    session = await get_questionnaire_session(session_id)
    if not session or session["user_id"] != uid or session["status"] != "active":
        # Wrong user / unknown / non-active session: silent no-op. Showing
        # ANY message here (even the generic one) would confirm to an
        # attacker that a session with this id exists at all.
        await callback.answer()
        return

    definition, error = questionnaires.get_validated_definition()
    if error is not None:
        # Same-user, own session, but the private definition itself is now
        # missing/invalid -- an internal configuration problem, not a tamper
        # attempt. Show the SAME neutral text as /questionnaire's own
        # not-configured path; never mention malformed JSON, multiple
        # private files, or risk-bearing rejection to the user.
        await callback.message.answer(_questionnaire_not_configured_text(lang))
        await callback.answer()
        return
    if (definition["id"] != session["questionnaire_id"]
            or definition["version"] != session["questionnaire_version"]):
        await callback.message.answer(_questionnaire_not_configured_text(lang))
        await callback.answer()
        return

    item = questionnaires.get_item(definition, session["current_index"])
    if item is None:
        # The private definition changed underneath an in-progress session
        # (fewer items than before) -- same neutral text, not a stack trace
        # or a "definition mismatch" explanation.
        await callback.message.answer(_questionnaire_not_configured_text(lang))
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
    next_index = session["current_index"] + 1
    await advance_questionnaire_session(session_id, next_index)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await _send_questionnaire_step(callback.message.answer, session_id, next_index, definition, lang)
    await callback.answer()


@dp.callback_query(F.data.startswith("q:c:"))
async def cb_questionnaire_cancel(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer()
        return
    session_id = int(parts[2])

    session = await get_questionnaire_session(session_id)
    if not session or session["user_id"] != uid or session["status"] != "active":
        await callback.answer()
        return

    await cancel_questionnaire_session(session_id)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(_questionnaire_cancelled_text(lang))
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
    rows = [[InlineKeyboardButton(text=(ru if lang == "ru" else en), callback_data=f"{key}:hub")]
            for key, ru, en in navigation.MENU_SECTIONS]
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
