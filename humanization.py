"""
X20 Humanization (Epic 2) — deterministic "feels human" layer.

Everything here is rule-based and side-effect free so it can be unit-tested
without the LLM or Telegram. The LLM still only writes the conversational text;
this module decides *how* it is delivered (greeting variety, typing pause) and
*screens* it for burned-out robotic phrasing.

Sections map to MASTER_SPEC_v2 §7:
  7.1 greeting variety   → pick_greeting()
  7.2 typing pause       → typing_delay()
  7.4 anti-robot filter  → has_robotic_phrase()
  7.5 persona voice      → PERSONA_VOICE_* (consumed by prompts.get_system_prompt)
"""
import random

# ── 7.5 Persona Voice ─────────────────────────────────────────────────────────
# Mixed into every system prompt. Kept short so it steers tone without crowding
# out the scenario instructions.
PERSONA_VOICE_RU = (
    "\nГОЛОС:\n"
    "- Тёплый, но не сахарный. Конкретный, не абстрактный.\n"
    "- Не торопишь, не делаешь выводов за человека. Можешь просто побыть рядом.\n"
    "- Чаще 1–3 строки. Один вопрос лучше пяти.\n"
)
PERSONA_VOICE_EN = (
    "\nVOICE:\n"
    "- Warm but not saccharine. Concrete, not abstract.\n"
    "- Don't rush, don't draw conclusions for them. You can simply be present.\n"
    "- Usually 1–3 lines. One question beats five.\n"
)


def persona_voice(lang: str = "ru") -> str:
    return PERSONA_VOICE_EN if lang == "en" else PERSONA_VOICE_RU


# ── 7.1 Greeting variety ──────────────────────────────────────────────────────
# Rotation axes: first /start vs return, and time of day (morning/day/evening/
# night) by the hour passed in. 20+ variants per language overall.
_GREETINGS = {
    "ru": {
        "first": [
            "Привет. Я здесь, чтобы выслушать.",
            "Привет. Рад, что ты написал(а).",
            "Здравствуй. Можешь начать с чего угодно.",
            "Привет. Я рядом — без спешки.",
        ],
        "morning": [
            "Доброе утро. Как просыпаешься?",
            "Утро. Как ты сегодня?",
            "Привет с утра. С чем проснулся(ась)?",
        ],
        "day": [
            "Привет. Как проходит день?",
            "Снова здесь. Как ты?",
            "Привет. Что на душе сейчас?",
        ],
        "evening": [
            "Добрый вечер. Как прошёл день?",
            "Вечер. Как ты к концу дня?",
            "Привет. Чем закончился день?",
        ],
        "night": [
            "Поздно. Не спится?",
            "Ночь. Я тут, если нужно.",
            "Привет. Тяжёлые ночи бывают — я рядом.",
        ],
    },
    "en": {
        "first": [
            "Hi. I'm here to listen.",
            "Hi. Glad you reached out.",
            "Hello. You can start with anything.",
            "Hi. I'm here — no rush.",
        ],
        "morning": [
            "Good morning. How are you waking up?",
            "Morning. How are you today?",
            "Morning. What did you wake up with?",
        ],
        "day": [
            "Hi. How's your day going?",
            "Back again. How are you?",
            "Hi. What's on your mind right now?",
        ],
        "evening": [
            "Good evening. How was your day?",
            "Evening. How are you as the day winds down?",
            "Hi. How did the day end?",
        ],
        "night": [
            "It's late. Can't sleep?",
            "Late night. I'm here if you need it.",
            "Hi. Hard nights happen — I'm here.",
        ],
    },
}


def _time_bucket(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "day"
    if 18 <= hour < 23:
        return "evening"
    return "night"


def pick_greeting(is_first: bool, hour: int, lang: str = "ru",
                  rng: random.Random | None = None) -> str:
    """Return a greeting varied by first-vs-return and time of day.

    `rng` is injectable for deterministic tests.
    """
    lang = "en" if lang == "en" else "ru"
    bucket = "first" if is_first else _time_bucket(hour)
    pool = _GREETINGS[lang][bucket]
    r = rng or random
    return r.choice(pool)


# ── 7.2 Typing pause ──────────────────────────────────────────────────────────
def typing_delay(answer: str) -> float:
    """Seconds to 'type' before sending — long enough to not feel instant,
    capped so it never feels broken. Formula from MASTER_SPEC_v2 §7.2."""
    return 1.5 + min(len(answer) / 200, 2.0) + random.uniform(0, 0.5)


# ── 7.4 Anti-Robot detector ───────────────────────────────────────────────────
# Burned-out support clichés. If the LLM emits one, the pipeline does ONE retry
# asking it to rephrase more like a person.
_ROBOTIC = {
    "ru": [
        "я слышу тебя",
        "это нормально чувствовать",
        "ты не один",
        "ты не одна",
        "давай попробуем технику",
        "расскажи больше",
        "я понимаю что ты чувствуешь",
    ],
    "en": [
        "i hear you",
        "it's normal to feel",
        "it is normal to feel",
        "you're not alone",
        "you are not alone",
        "let's try a technique",
        "tell me more",
        "i understand how you feel",
    ],
}


def has_robotic_phrase(text: str, lang: str = "ru") -> bool:
    """True if the text contains a banned cliché in either language."""
    t = (text or "").lower()
    for l in {lang, "ru", "en"}:
        if any(p in t for p in _ROBOTIC.get(l, [])):
            return True
    return False


_REPHRASE_INSTRUCTION = {
    "ru": "Перепиши ответ живее, своими словами, без шаблонных фраз поддержки.",
    "en": "Rewrite the reply more naturally, in your own words, no canned support phrases.",
}


def rephrase_instruction(lang: str = "ru") -> str:
    return _REPHRASE_INSTRUCTION["en" if lang == "en" else "ru"]
