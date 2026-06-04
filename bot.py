"""
X20 Bot — Основной файл

Полный pipeline:
  Risk → Language → Stage → State → Readiness → Capacity → Scenario → 
  RelationshipMonitor → PracticeSelect → Memory → LLM → SafetyValidator → 
  Notifications → OutcomeTracking → User
"""
import asyncio
from html import escape as _he
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from openai import AsyncOpenAI

from config import BOT_TOKEN, OPENAI_API_KEY, ADMIN_USER_IDS, AB_VARIANTS, ROUTER_VERSION, PRACTICE_VERSION
from prompts import get_system_prompt, get_crisis_text, get_onboarding
from risk_detector import detect_risk
from language_detector import detect_language
from stage_detector import detect_stage
from state_engine import DEFAULT_STATE, update_state, choose_scenario
from readiness_engine import assess_readiness
from cognitive_capacity import get_capacity
from relationship_monitor import monitor_relationship
from practice_registry import select_practice, get_practice_by_id
from safety_validator import validate_response, get_fallback
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
)

class InterventionStates(StatesGroup):
    awaiting_after   = State()
    awaiting_quality = State()

bot                = Bot(token=BOT_TOKEN)
dp                 = Dispatcher(storage=MemoryStorage())
client             = AsyncOpenAI(api_key=OPENAI_API_KEY)
dependency_monitor = DependencyMonitor()

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

async def pipeline(message: Message, user_text: str, fsm_state: FSMContext | None = None) -> None:
    """Complete X20 pipeline"""
    uid, username, first_name = message.from_user.id, message.from_user.username or "", message.from_user.first_name or ""
    
    # 1. Detect language
    lang = detect_language(user_text)
    await upsert_user(uid, username, first_name, lang)
    
    # 2. Risk detection
    risk = detect_risk(user_text, lang)
    
    # 3. Log if medium+
    if risk["level"] in ("medium", "high", "critical"):
        await log_moderation(uid, username, first_name, risk["level"], risk["score"],
                              risk["categories"], user_text, "pending", risk["implicit"])
    
    # 4. Crisis override
    if "aggression" in risk["categories"]:
        await push_alert("Aggression Detected", uid, username, risk["level"],
                         risk["score"], risk["categories"], user_text)

    if "suicide" in risk["categories"] or "self_harm" in risk["categories"]:
        await push_alert("Critical Risk", uid, username, risk["level"], risk["score"],
                         risk["categories"], user_text)
        for admin_id in ADMIN_USER_IDS:
            try:
                await bot.send_message(admin_id,
                    f"🚨 Critical:\nUser {uid}\nLevel: {risk['level']}\nScore: {risk['score']}\nMsg: {user_text[:200]}")
            except Exception:
                pass
        await message.answer(get_crisis_text(lang), parse_mode="HTML")
        return
    
    # 3.5 Dependency monitor
    dep_msg = await dependency_monitor.check_dependency(uid, lang)
    if dep_msg:
        await message.answer(dep_msg)
    await dependency_monitor.record_message(uid)

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
    scenario = choose_scenario(state, risk["categories"], stage, readiness, capacity, variant)
    
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
    response = await client.chat.completions.create(
        model="gpt-4o-mini", messages=messages, temperature=0.65, max_tokens=300,
    )
    answer = response.choices[0].message.content
    
    # 16. Safety validator
    is_safe, reason = validate_response(answer, lang)
    if not is_safe:
        await log_validator_block(uid, reason, answer)
        answer = get_fallback(lang)
    
    # 17. Save & send
    await save_message(uid, "user", user_text, scenario, lang)
    await save_message(uid, "assistant", answer, scenario, lang)
    await message.answer(answer)
    
    # 18. Start outcome tracking (if appropriate scenario)
    if scenario not in ("crisis", "open_chat"):
        # persist routing context so cb_before can record real stage/readiness/capacity
        if fsm_state is not None:
            await fsm_state.update_data(stage=stage, readiness=readiness, capacity=capacity)
        await message.answer(f"Как ты себя чувствуешь прямо сейчас? (1=плохо, 10=хорошо)" if lang == "ru"
                             else "How do you feel right now? (1=bad, 10=good)",
                             reply_markup=score_kb(f"before:{practice['id']}:{scenario}:{lang}"))

# ────────────────────────────────────────────────────────────────────────────

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
    uid = message.from_user.id
    await upsert_user(uid, message.from_user.username or "", message.from_user.first_name or "")
    lang = await get_user_language(uid)
    text, buttons = get_onboarding(lang)
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=b)] for b in buttons], resize_keyboard=True, one_time_keyboard=True)
    await message.answer(text + "\n\n⚠️ " + ("Я не терапевт." if lang == "ru" else "I'm not a therapist."), 
                         reply_markup=kb)

@dp.message(Command("help"))
async def cmd_help(message: Message):
    lang = await get_user_language(message.from_user.id)
    await message.answer(
        ("/start • /checkin • /help" if lang == "en" else "/start • /checkin • /help"),
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
