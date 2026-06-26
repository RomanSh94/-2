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

from config import BOT_TOKEN, OPENAI_API_KEY, ADMIN_USER_IDS, AB_VARIANTS, ROUTER_VERSION, PRACTICE_VERSION
from prompts import get_system_prompt, get_crisis_text, get_onboarding
from crisis_protocol import (
    classify, crisis_keyboard, admin_alert_text, RED, ORANGE,
    crisis_screen, safe_only_keyboard, crisis_call_text, crisis_contact_template,
    crisis_safe_place_ack, crisis_resolved_text, is_reassuring,
)
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
    validate_response, get_fallback,
    validate_response_with_context, get_safe_fallback_high_risk,
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
    get_memory_overview, forget_all,
    set_mute, reset_unanswered,
    get_recent_messages, log_disambiguation,
    get_user_message_count, get_profile, delete_profile,
    log_review_flag, log_toxic_validation_block,
    save_emotion_entry, save_cbt_entry,
    get_emotion_entries_since, get_checkin_logs_since, log_checkin,
    set_tz_offset, get_user_tz, get_journal_settings, set_journal_settings,
    export_journals, delete_journals,
)

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
    same *class* as the original P0 (detection ok, decision ok, delivery lost)."""
    # Create the event first — its id is baked into the crisis screen buttons.
    eid = await log_crisis_event(uid, RED, risk["score"], risk["categories"],
                                 user_text[:300], lang, admin_notified=bool(ADMIN_USER_IDS))
    # DELIVER the crisis screen to the user before anything non-essential.
    text, kb = crisis_screen(0, lang, eid)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

    # Everything below is admin/research context — important, but it must NEVER
    # block or undo the delivered screen. Each block is isolated and logged.
    try:
        # Persist the crisis message's risk snapshot + force a profile refresh
        # (§5 trigger #2) so crisis_risk/themes reflect this turn immediately.
        await save_message(uid, "user", user_text, "crisis", lang,
                           risk["score"], risk["categories"])
        await maybe_update_profile(uid, await get_user_message_count(uid), force=True)
    except Exception as e:
        print(f"[crisis] post-screen persist failed uid={uid}: {e}")
    try:
        # Epic A — protective factors: CONTEXT ONLY for the admin. Detected AFTER
        # the screen is delivered; never alters risk or the user's message.
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
        for admin_id in ADMIN_USER_IDS:
            try:
                await bot.send_message(admin_id, alert)
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
        await message.answer(scr, parse_mode="HTML", reply_markup=kb)
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
    
    # 1. Detect language
    lang = detect_language(user_text)
    await upsert_user(uid, username, first_name, lang)
    await reset_unanswered(uid)   # user re-engaged → clear ignored-push backoff
    
    # 2. Risk detection
    risk = detect_risk(user_text, lang)
    
    # 3. Log if medium+
    if risk["level"] in ("medium", "high", "critical"):
        await log_moderation(uid, username, first_name, risk["level"], risk["score"],
                              risk["categories"], user_text, "pending", risk["implicit"])
    
    # 3.9 Active-crisis gate — while a recent crisis event is unresolved, the LLM
    # is OFF and we don't return to normal chat. Free text either keeps the crisis
    # screen (RED/ORANGE) or gently offers "Я в безопасности" (calm). The 24h
    # recency window in get_active_crisis bounds this so nobody is stuck forever.
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
            await save_message(uid, "user", user_text, "crisis", lang,
                               risk["score"], risk["categories"])
            text, kb = crisis_screen(stage, lang, event_id)
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
        return

    # 4. Crisis override (Epic 1 — Crisis Protocol; LLM is NEVER called here)
    if "aggression" in risk["categories"]:
        await push_alert("Aggression Detected", uid, username, risk["level"],
                         risk["score"], risk["categories"], user_text)

    if classify(risk) == RED:
        await trigger_crisis(message, uid, username, user_text, risk, lang)
        return

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
            answer = candidate if ok2 else get_fallback(lang)
        except Exception as e:
            print(f"[anti-toxic] retry failed uid={uid}: {type(e).__name__}: {e}")
            answer = get_fallback(lang)
    elif not is_safe:
        await log_validator_block(uid, reason, answer)
        # At elevated risk use the deterministic high-risk fallback; otherwise
        # the neutral fallback. NEVER re-prompt the LLM here.
        answer = (get_safe_fallback_high_risk(lang)
                  if risk.get("level") in ("medium", "high", "critical")
                  or risk.get("ambiguous_phrases")
                  else get_fallback(lang))

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
    risk = {"level": "critical", "score": "—", "categories": ["suicide"]}
    alert = admin_alert_text(uid, username, stage, risk, "", event_id)
    for admin_id in ADMIN_USER_IDS:
        try:
            await bot.send_message(admin_id, alert)
        except Exception:
            pass


async def _show_stage(callback: CallbackQuery, stage: int, lang: str, event_id) -> None:
    """Gate the OLD screen's buttons (with fallback) then show the new stage."""
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass  # Telegram may refuse to edit; the DB stage still prevents a loop.
    text, kb = crisis_screen(stage, lang, event_id)
    await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)


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
    # buttons from messages sent before this deploy → backward compatible).
    event_id = None
    if len(parts) >= 3 and parts[2].isdigit():
        event_id = int(parts[2])
    if event_id is None:
        active = await get_active_crisis(uid)
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
        await callback.message.answer(crisis_resolved_text(lang))
        await callback.answer()
        return

    if action == "call":
        await callback.message.answer(crisis_call_text(lang), parse_mode="HTML")
        await callback.answer()
        return

    if action in ("contact",):
        await callback.message.answer(crisis_contact_template(lang))
        await callback.answer()
        return

    if action in ("safe_place", "contacted"):
        await callback.message.answer(crisis_safe_place_ack(lang),
                                      reply_markup=safe_only_keyboard(event_id, lang))
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
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=b, callback_data=f"mood:{i}")]
        for i, b in enumerate(buttons)
    ])
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
    lang = await get_user_language(message.from_user.id)
    await delete_profile(message.from_user.id)
    await message.answer(
        "Готово. Профиль стёрт — начнём с чистого листа." if lang == "ru"
        else "Done. Your profile is erased — fresh start.")


@dp.callback_query(F.data == "profile:reset")
async def cb_profile_reset(callback: CallbackQuery):
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


@dp.message(Command("forget_all"))
async def cmd_forget_all(message: Message):
    """GDPR right-to-erasure — ask for explicit confirmation first."""
    lang = await get_user_language(message.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=("🗑 Да, стереть всё" if lang == "ru" else "🗑 Yes, erase everything"),
            callback_data="forget:yes"),
        InlineKeyboardButton(
            text=("Отмена" if lang == "ru" else "Cancel"),
            callback_data="forget:no"),
    ]])
    await message.answer(
        ("Это удалит всю переписку, резюме и профиль безвозвратно. Продолжить?"
         if lang == "ru"
         else "This permanently deletes all your messages, summary and profile. Continue?"),
        reply_markup=kb)


@dp.callback_query(F.data.startswith("forget:"))
async def cb_forget(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    if callback.data.split(":")[1] == "yes":
        await forget_all(uid)
        msg = "Готово. Я всё стёр." if lang == "ru" else "Done. Everything is erased."
    else:
        msg = "Отменено." if lang == "ru" else "Cancelled."
    await callback.message.answer(msg)
    await callback.answer()

@dp.message(Command("mute"))
async def cmd_mute(message: Message):
    lang = await get_user_language(message.from_user.id)
    await set_mute(message.from_user.id, "forever")
    await message.answer("Пуши отключены. /unmute — включить обратно." if lang == "ru"
                         else "Pushes off. /unmute to turn them back on.")


@dp.message(Command("mute_today"))
async def cmd_mute_today(message: Message):
    from datetime import datetime, timezone, timedelta
    lang = await get_user_language(message.from_user.id)
    until = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    await set_mute(message.from_user.id, "until", until.strftime("%Y-%m-%d %H:%M:%S"))
    await message.answer("Тихо до конца дня." if lang == "ru" else "Quiet for the rest of today.")


@dp.message(Command("mute_week"))
async def cmd_mute_week(message: Message):
    from datetime import datetime, timezone, timedelta
    lang = await get_user_language(message.from_user.id)
    until = datetime.now(timezone.utc) + timedelta(days=7)
    await set_mute(message.from_user.id, "until", until.strftime("%Y-%m-%d %H:%M:%S"))
    await message.answer("Тихо на неделю." if lang == "ru" else "Quiet for a week.")


@dp.message(Command("unmute"))
async def cmd_unmute(message: Message):
    lang = await get_user_language(message.from_user.id)
    await set_mute(message.from_user.id, "none")
    await message.answer("Пуши снова включены." if lang == "ru" else "Pushes back on.")


@dp.message(Command("help"))
async def cmd_help(message: Message):
    lang = await get_user_language(message.from_user.id)
    await message.answer(
        ("/start • /checkin • /time • /memory • /profile • /forget_all • /mute • /unmute • /help"),
        reply_markup=ReplyKeyboardRemove())

@dp.message(Command("checkin"))
async def cmd_checkin(message: Message):
    lang = await get_user_language(message.from_user.id)
    text = ("Выбери время check-in (UTC):" if lang == "ru" else "Choose check-in time (UTC):")
    await message.answer(text + "\n/checkin_8 • /checkin_10 • /checkin_12 • /checkin_18 • /checkin_20\n/checkin_off")

async def _enable_ci(message: Message, hour: int):
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
    decision, _ = await journal_guard(message, uid, lang)
    if decision == "crisis":
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
        done = ("Сохранил. Спасибо, что побыл(а) с этим." if lang == "ru"
                else "Saved. Thank you for staying with this.")
        await message.answer(prefix + done)
        return

    await state.update_data(jstep=nxt, jdata=jdata, orange=orange, nudged=nudged)
    await message.answer(prefix + journals.emotion_prompt(journals.EMOTION_FIELDS[nxt], lang))


# ── Epic 8: CBT journal (deep) — aborts at ORANGE, not just RED ───────────────

@dp.message(Command("cbt"))
async def cmd_cbt(message: Message, state: FSMContext, tg_user=None):
    # tg_user: real user when reached via callback (see cmd_emotion note).
    uid = (tg_user or message.from_user).id
    lang = await get_user_language(uid)
    decision, _ = await journal_guard(message, uid, lang)
    if decision == "crisis":
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
        await message.answer("Записал. Это твоя работа с мыслью — спасибо."
                             if lang == "ru" else "Saved. That was your own work — thank you.")
        return
    await state.update_data(cstep=nxt, cdata=cdata)
    await message.answer(journals.cbt_prompt(journals.CBT_FIELDS[nxt], lang))


# ── Epic 8: weekly report (deterministic), settings, GDPR ─────────────────────

@dp.message(Command("report"))
async def cmd_report(message: Message, state: FSMContext):
    uid = message.from_user.id
    lang = await get_user_language(uid)
    emo = await get_emotion_entries_since(uid, 7)
    chk = await get_checkin_logs_since(uid, 7)
    await message.answer(journals.build_weekly_report(emo, chk, lang))


@dp.message(Command("journal"))
async def cmd_journal(message: Message, state: FSMContext):
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
        await callback.message.answer(get_crisis_text(lang), parse_mode="HTML",
                                      reply_markup=crisis_keyboard(lang))


@dp.message(Command("journal_settings"))
async def cmd_journal_settings(message: Message, state: FSMContext):
    uid = message.from_user.id
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
    lang = await get_user_language(message.from_user.id)
    await message.answer(
        "В каком часовом поясе ты сейчас? Это нужно, чтобы приветствия и "
        "напоминания приходили по твоему местному времени." if lang == "ru" else
        "What's your timezone? So greetings and reminders arrive in your local time.",
        reply_markup=tz_picker_keyboard())


@dp.callback_query(F.data.startswith("jtz:"))
async def cb_jtz(callback: CallbackQuery):
    offset = int(callback.data.split(":")[1])
    await set_tz_offset(callback.from_user.id, offset)
    await callback.answer("Часовой пояс сохранён")
    await callback.message.answer(f"Ок, твой пояс: UTC{offset:+d}.")


@dp.callback_query(F.data.startswith("checkin:"))
async def cb_checkin(callback: CallbackQuery, state: FSMContext):
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
        await callback.message.answer("Спасибо, что отметил(а).")


@dp.message(Command("journal_export"))
async def cmd_journal_export(message: Message, state: FSMContext):
    import json, io
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
    await delete_journals(message.from_user.id)
    lang = await get_user_language(message.from_user.id)
    await message.answer("Готово. Все журнальные записи стёрты."
                         if lang == "ru" else "Done. All journal entries erased.")


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
