"""
X20 Journals Engine (Epic 8) — emotion journal, CBT journal, check-ins, weekly
report. The bot RECORDS and REFLECTS the user's own words; it never interprets
("у тебя страх отвержения") — that stays the user's job and is also caught by
safety_validator.

This module holds the deterministic, aiogram-free pieces (step prompts, the
risk-gate over the existing detector, guardrails, the weekly report builder) so
they can be unit-tested without Telegram. FSM handlers live in bot.py (above the
catch-all text handler) and call into here.
"""
from risk_detector import detect_risk
from crisis_protocol import classify, RED


# ── Risk gate (REUSES the existing detector/classifier — no new scoring) ──────
def gate(text: str, lang: str = "ru") -> tuple[str, dict]:
    """Run a free-text journal field through the real risk detector.

    Returns (level, risk) where level is RED/ORANGE/YELLOW/GREEN from
    crisis_protocol.classify(). The caller decides what to do:
      RED    → abort journal, clear FSM, run Crisis Protocol
      ORANGE → don't deepen (skip body step / no deep CBT), offer hotline
      YELLOW → emotion journal ok; deep CBT cautiously
      GREEN  → everything available
    """
    risk = detect_risk(text, lang)
    return classify(risk), risk


# ── Emotion journal: fixed, non-interpretive prompts ──────────────────────────
# Step keys are stored; "body" is the only one we skip at ORANGE (don't deepen
# somatic focus when risk is elevated).
EMOTION_STEPS = [
    ("event",
     "Начнём с факта, без анализа. Что случилось перед тем, как состояние "
     "изменилось? Можно коротко: разговор, сообщение, конфликт, усталость, "
     "день без сил.",
     "Let's start with the fact, no analysis. What happened just before your "
     "state shifted? Keep it short: a conversation, a message, a conflict, "
     "tiredness, a day with no energy."),
    ("feeling",
     "Теперь попробуем назвать эмоцию. Это нужно, чтобы потом увидеть, какие "
     "состояния повторяются. Можно написать одно слово: тревога, обида, злость, "
     "грусть, пустота, не знаю.",
     "Now let's try to name the emotion. This helps you later see which states "
     "repeat. One word is enough: anxiety, hurt, anger, sadness, emptiness, "
     "don't know."),
    ("intensity",
     "Насколько сильно это было, от 1 до 10? Не нужно точности — просто как "
     "ощущалось.",
     "How strong was it, from 1 to 10? No need to be exact — just how it felt."),
    ("body",
     "Иногда тело замечает эмоцию раньше, чем мы понимаем её словами. Где это "
     "сильнее всего чувствовалось? Например: грудь, горло, живот, голова, "
     "плечи, напряжение, пустота.",
     "Sometimes the body notices an emotion before we put it into words. Where "
     "did you feel it most? For example: chest, throat, stomach, head, "
     "shoulders, tension, emptiness."),
    ("need",
     "Чего тебе тогда не хватало — чего ты на самом деле хотел(а) в тот момент? "
     "Можно коротко: чтобы услышали, покоя, поддержки, отдыха, не знаю.",
     "What were you missing then — what did you actually need in that moment? "
     "Keep it short: to be heard, calm, support, rest, don't know."),
    ("action",
     "Что ты сделал(а) после этого? Без оценки, просто как было.",
     "What did you do after that? No judgment, just what happened."),
    ("outcome",
     "И в итоге — стало легче, тяжелее или без изменений?",
     "And in the end — did it get easier, harder, or stay the same?"),
]
EMOTION_FIELDS = [k for k, _, _ in EMOTION_STEPS]


def emotion_prompt(step_key: str, lang: str = "ru") -> str:
    for k, ru, en in EMOTION_STEPS:
        if k == step_key:
            return en if lang == "en" else ru
    return ""


# ── Self-tracking guardrail (Epic 8 §6) ───────────────────────────────────────
# Journals are about EMOTIONS and THOUGHTS, not body/food/weight. We never
# introduce calorie/weight tracking; and for vulnerable signals we avoid pushing
# detailed numeric self-monitoring (it can reinforce harm).
_SELF_TRACK_SENSITIVE = {"self_harm", "eating_disorder"}


def should_skip_body(level: str, risk: dict) -> bool:
    """Skip the somatic ('body') step when risk is elevated or sensitive."""
    if level in (RED, "ORANGE"):
        return True
    cats = set(risk.get("categories", []) or [])
    return bool(cats & _SELF_TRACK_SENSITIVE)


HOTLINE_NUDGE_RU = ("\n\nИ ещё — если тебе сейчас правда тяжело, есть бесплатный "
                    "телефон доверия 8-800-2000-122 (анонимно, 24/7).")
HOTLINE_NUDGE_EN = ("\n\nAnd — if things are really hard right now, please reach "
                    "out to a crisis line near you. You don't have to be alone.")


def hotline_nudge(lang: str = "ru") -> str:
    return HOTLINE_NUDGE_EN if lang == "en" else HOTLINE_NUDGE_RU


# ── CBT journal: user reframes their OWN thought; bot never interprets ─────────
CBT_STEPS = [
    ("situation",
     "Начнём с ситуации, как с факта. Что произошло — коротко и без оценки? "
     "Например: разговор, отказ, ошибка в работе, сообщение.",
     "Let's start with the situation as a fact. What happened — briefly and "
     "without judgment? For example: a conversation, a refusal, a mistake at "
     "work, a message."),
    ("automatic_thought",
     "Теперь попробуем поймать мысль, которая мелькнула первой. Это может быть "
     "короткая внутренняя фраза, не обязательно логичная. Например: «я не "
     "важен», «я всё испортил», «меня отвергли», «я не справлюсь».",
     "Now let's catch the thought that flashed first. It can be a short inner "
     "phrase, not necessarily logical. For example: 'I don't matter', 'I "
     "ruined everything', 'I was rejected', 'I won't cope'."),
    ("emotion",
     "Какое чувство пришло вместе с этой мыслью? Можно одно слово: тревога, "
     "стыд, обида, грусть, злость.",
     "What feeling came with that thought? One word is fine: anxiety, shame, "
     "hurt, sadness, anger."),
    ("intensity",
     "Насколько сильным было это чувство, от 1 до 10?",
     "How strong was that feeling, from 1 to 10?"),
    ("evidence_for",
     "Посмотрим на эту мысль как на гипотезу. Что как будто её подтверждает? "
     "Не нужно доказывать, что мысль правильная — просто запишем, на чём она "
     "держится.",
     "Let's look at this thought as a hypothesis. What seems to support it? You "
     "don't need to prove the thought is right — just note what it rests on."),
    ("evidence_against",
     "Теперь мягко проверим другую сторону. Есть ли что-то, что не полностью "
     "совпадает с этой мыслью? Например: «он не ответил, но раньше писал сам», "
     "«я ошибся, но не всё испортил».",
     "Now let's gently check the other side. Is there anything that doesn't "
     "fully match this thought? For example: 'he didn't reply, but he used to "
     "text first', 'I made a mistake, but I didn't ruin everything'."),
    ("realistic_thought",
     "Если собрать обе стороны вместе — как бы ты сформулировал(а) мысль чуть "
     "точнее, своими словами? Не обязательно позитивно, просто ближе к фактам.",
     "Putting both sides together — how would you put the thought a bit more "
     "accurately, in your own words? Not necessarily positive, just closer to "
     "the facts."),
    ("change",
     "И последнее: изменилось ли что-то в чувстве сейчас, после того как ты "
     "посмотрел(а) на мысль со стороны?",
     "And last: did anything shift in the feeling now, after you looked at the "
     "thought from the outside?"),
]
CBT_FIELDS = [k for k, _, _ in CBT_STEPS]


def cbt_prompt(step_key: str, lang: str = "ru") -> str:
    for k, ru, en in CBT_STEPS:
        if k == step_key:
            return en if lang == "en" else ru
    return ""


# ── Save confirmations & check-in ack (UX copy — show value, promise nothing) ──
# These were inline in bot.py; centralised here so the journal copy lives in one
# module and stays unit-testable without importing the aiogram bot.
def emotion_saved_text(lang: str = "ru") -> str:
    return (
        "Сохранил. По этой записи уже видна цепочка: что произошло → что ты "
        "почувствовал(а) → как отреагировал(а). Когда таких записей станет "
        "несколько, можно будет заметить, что повторяется." if lang != "en" else
        "Saved. This entry already shows a chain: what happened → what you felt "
        "→ how you reacted. Once you have a few of them, you'll be able to "
        "notice what repeats.")


def cbt_saved_text(lang: str = "ru") -> str:
    return (
        "Записал. Ты уже отделил(а) ситуацию от мысли и проверил(а), насколько "
        "она точна. Позже можно будет увидеть, какие мысли повторяются чаще."
        if lang != "en" else
        "Saved. You've already separated the situation from the thought and "
        "checked how accurate it is. Later you'll be able to see which thoughts "
        "come up more often.")


def checkin_ack_text(lang: str = "ru") -> str:
    # Statement only: the mark is saved (checkin_logs) but there is no
    # user-facing trend/graph, so we promise nothing beyond "noted".
    return ("Понял тебя. Отметил состояние на этот момент." if lang != "en"
            else "Got it. I've noted your state at this moment.")


# ── Check-in button labels (mood marks) ───────────────────────────────────────
MORNING_OPTIONS = [("calm", "😌 спокойно"), ("neutral", "😐 нейтрально"),
                   ("anxious", "😰 тревожно"), ("sad", "😔 грустно"),
                   ("irritated", "😤 раздражён"), ("no_energy", "😴 нет сил")]
EVENING_OPTIONS = [("emotion_journal", "📝 дневник эмоций"),
                   ("cbt_journal", "📘 КПТ"), ("skip", "⏭ пропустить")]


# ── Weekly report (DETERMINISTIC — NO LLM, no causality, no diagnoses) ─────────
def _hour_of(ts: str) -> int:
    try:
        return int(ts[11:13])
    except Exception:
        return 12


def _bucket(hour: int) -> str:
    if hour < 12:
        return "утром"
    if hour < 18:
        return "днём"
    return "вечером"


def build_weekly_report(emotion_entries: list, checkin_logs: list,
                        lang: str = "ru", min_entries: int = 3) -> str:
    """Deterministic 7-day summary built ONLY from what the user literally
    recorded: counts, averages, time-of-day. No interpretation, no causality, no
    diagnoses. Ends with an invitation question, not a conclusion.

    emotion_entries: list of dicts with 'feeling','intensity','created_at'.
    checkin_logs:    list of dicts with 'value','created_at'.
    """
    total = len(emotion_entries)
    if total < min_entries:
        return ("📊 Пока мало записей, чтобы что-то заметить. "
                "Позаполняй дневник на этой неделе — и вернёмся к сводке."
                if lang == "ru" else
                "📊 Not enough entries yet to notice anything. Keep journaling this "
                "week and we'll look again.")

    # Most frequent feelings (user's own words, exact strings).
    feelings = {}
    for e in emotion_entries:
        f = (e.get("feeling") or "").strip().lower()
        if f:
            feelings[f] = feelings.get(f, 0) + 1
    top = sorted(feelings.items(), key=lambda x: -x[1])[:3]
    top_feeling = top[0][0] if top else "—"

    # Average intensity overall and by time-of-day.
    vals = [(e.get("intensity"), _bucket(_hour_of(e.get("created_at") or "")))
            for e in emotion_entries if e.get("intensity")]
    overall = round(sum(v for v, _ in vals) / len(vals), 1) if vals else None
    by_bucket = {}
    for v, b in vals:
        by_bucket.setdefault(b, []).append(v)
    bucket_avg = {b: round(sum(xs) / len(xs), 1) for b, xs in by_bucket.items()}

    lines = [f"📊 Сводка за неделю ({total} записей):", ""]
    lines.append("Чаще всего ты отмечал(а): " + ", ".join(f"{f} ×{n}" for f, n in top))
    if overall is not None:
        lines.append(f"Средняя интенсивность: {overall} из 10")
    if len(bucket_avg) >= 2:
        hi = max(bucket_avg.items(), key=lambda x: x[1])
        lines.append(f"Выше всего в среднем — {hi[0]} ({hi[1]}).")
    if checkin_logs:
        lines.append(f"Отметок настроения за неделю: {len(checkin_logs)}.")
    lines.append("")
    lines.append(f"Хочешь на следующей неделе понаблюдать за тем, что стоит за «{top_feeling}»?")
    return "\n".join(lines)

