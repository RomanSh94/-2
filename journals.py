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
