"""
X20 Crisis Protocol (Epic 1) — deterministic crisis handling.

CORE INVARIANT: the LLM is NEVER called on the crisis path. Levels here are
*mapped* from the existing risk_detector output — we do not re-score. All
user-facing crisis text is the pre-written deterministic text from prompts.py.

Levels (severity colour):
  RED    — explicit suicide / self_harm category present → full crisis resources
  ORANGE — critical/high risk score, no suicide/self_harm  → heightened concern
  YELLOW — medium risk score
  GREEN  — low risk score (no crisis action)

Only RED stops the pipeline and shows crisis resources; ORANGE/YELLOW/GREEN are
informational for logging/alerts and let the normal pipeline continue.
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

RED    = "RED"
ORANGE = "ORANGE"
YELLOW = "YELLOW"
GREEN  = "GREEN"


def classify(risk: dict) -> str:
    """Map a risk_detector result dict to a crisis colour level."""
    cats = risk.get("categories", []) or []
    if "suicide" in cats or "self_harm" in cats:
        return RED
    level = risk.get("level", "low")
    if level in ("critical", "high"):
        return ORANGE
    if level == "medium":
        return YELLOW
    return GREEN


def crisis_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    """Inline keyboard for the crisis message.

    Row 1 — direct help. RU gets a `tel:` button to the federal hotline; EN gets
    a URL button to IASP (no single international number exists). NOTE: `tel:`
    inline buttons are not honoured by every Telegram client, so the phone
    number always ALSO appears as plain text in the crisis message itself
    (prompts.get_crisis_text) as a guaranteed fallback.

    Row 2 — self-report buttons so the user can tell us how they are; this drives
    follow-up scheduling and resolution.
    """
    if lang == "en":
        help_btn = InlineKeyboardButton(
            text="🌍 Find a crisis center",
            url="https://www.iasp.info/resources/Crisis_Centres/")
        safe_label  = "🟢 I'm safe right now"
        still_label = "🆘 I'm still struggling"
    else:
        help_btn = InlineKeyboardButton(
            text="📞 8-800-2000-122",
            url="tel:+78002000122")
        safe_label  = "🟢 Я в безопасности"
        still_label = "🆘 Мне всё ещё тяжело"

    return InlineKeyboardMarkup(inline_keyboard=[
        [help_btn],
        [
            InlineKeyboardButton(text=safe_label,  callback_data="crisis:safe"),
            InlineKeyboardButton(text=still_label, callback_data="crisis:still"),
        ],
    ])


def admin_alert_text(uid: int, username: str, level_color: str,
                     risk: dict, message_text: str) -> str:
    """Structured #CRITICAL alert sent to admin Telegram accounts."""
    cats = ", ".join(risk.get("categories", []) or []) or "—"
    return (
        f"🚨 #CRITICAL [{level_color}]\n"
        f"User: {uid} (@{username or '—'})\n"
        f"Risk: {risk.get('level', '—')}  |  score: {risk.get('score', '—')}\n"
        f"Categories: {cats}\n"
        f"Message: {message_text[:200]}"
    )
