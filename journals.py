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
    ("event",     "Что произошло?",                          "What happened?"),
    ("feeling",   "Что ты почувствовал(а)?",                 "What did you feel?"),
    ("intensity", "Насколько сильно, от 1 до 10?",           "How strong, 1 to 10?"),
    ("body",      "Где это ощущается в теле?",               "Where do you feel it in your body?"),
    ("need",      "Что тебе было нужно в этот момент?",       "What did you need in that moment?"),
    ("action",    "Что ты сделал(а)?",                       "What did you do?"),
    ("outcome",   "Стало легче, тяжелее или без изменений?", "Did it get easier, harder, or no change?"),
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
    ("situation",         "Опиши ситуацию: что случилось?",            "Describe the situation: what happened?"),
    ("automatic_thought", "Какая мысль автоматически пришла в голову?", "What thought came automatically?"),
    ("emotion",           "Какую эмоцию это вызвало?",                  "What emotion did it cause?"),
    ("intensity",         "Насколько сильно, от 1 до 10?",             "How strong, 1 to 10?"),
    ("evidence_for",      "Какие факты ЗА эту мысль?",                  "What facts support this thought?"),
    ("evidence_against",  "Какие факты ПРОТИВ этой мысли?",            "What facts go against it?"),
    ("realistic_thought", "Как можно сформулировать мысль более реалистично — твоими словами?",
                          "How could you put the thought more realistically — in your own words?"),
    ("change",            "Что изменилось в эмоции сейчас?",           "What changed in the emotion now?"),
]
CBT_FIELDS = [k for k, _, _ in CBT_STEPS]


def cbt_prompt(step_key: str, lang: str = "ru") -> str:
    for k, ru, en in CBT_STEPS:
        if k == step_key:
            return en if lang == "en" else ru
    return ""


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

