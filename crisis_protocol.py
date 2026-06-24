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
import functools
import json
import os
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

RED    = "RED"
ORANGE = "ORANGE"
YELLOW = "YELLOW"
GREEN  = "GREEN"

# Crisis hotline numbers live in a CONFIG FILE, not in code — an outdated crisis
# number is itself a safety bug, so they must be easy to review/update and carry
# last_verified_at. We only know the user's language (not country), so we resolve
# by language and default to the project's main region (RU). 112 is the universal
# secondary fallback (works across RU/EU/KZ).
_CONTACTS_PATH = os.path.join(os.path.dirname(__file__), "crisis_contacts.json")


@functools.lru_cache(maxsize=1)
def _load_contacts() -> list:
    try:
        with open(_CONTACTS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


# Hardcoded last-resort so a missing/corrupt crisis_contacts.json never drops the
# real hotline to 112-only (the federal line is itself life-saving).
_FALLBACK_HOTLINES = {
    "ru": {"primary": "8-800-2000-122", "secondary": "112"},
    "en": {"primary": "988",            "secondary": "911"},
}


def get_hotline(lang: str = "ru") -> dict:
    """Resolve {primary, secondary} numbers by language. Unknown → RU. 112 is the
    guaranteed secondary if a row omits one. If the config file can't be read,
    fall back to a hardcoded number (never lose the federal line)."""
    contacts = _load_contacts()
    match = next((c for c in contacts if c.get("language") == lang), None) \
        or next((c for c in contacts if c.get("language") == "ru"), None)
    if not match:
        return _FALLBACK_HOTLINES.get(lang, _FALLBACK_HOTLINES["ru"])
    return {"primary": match.get("primary_emergency_number", "112"),
            "secondary": match.get("secondary_emergency_number") or "112"}


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
    # NOTE: Telegram REJECTS `tel:` URLs in inline keyboard buttons
    # ("Wrong port number") — a tel: button crashes the whole crisis send. So the
    # phone number is shown as plain text in get_crisis_text (mobile clients
    # auto-linkify it → tap to call). Only valid https/tg URLs may sit in a url
    # button; EN keeps the IASP https link. RU shows only the self-report row.
    rows = []
    if lang == "en":
        rows.append([InlineKeyboardButton(
            text="🌍 Find a crisis line",
            url="https://www.iasp.info/resources/Crisis_Centres/")])
        safe_label  = "💬 I'm safe right now"
        still_label = "💔 I'm still struggling"
    else:
        safe_label  = "💬 Я в безопасности"
        still_label = "💔 Мне всё ещё плохо"
    rows.append([
        InlineKeyboardButton(text=safe_label,  callback_data="crisis:safe"),
        InlineKeyboardButton(text=still_label, callback_data="crisis:still"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Staged crisis escalation (loop fix) ───────────────────────────────────────
# "Мне всё ещё плохо" is an ESCALATION, not a repeat. Each stage shows a
# DIFFERENT, warmer text; the emergency number is ALWAYS in the message body
# (mobile Telegram auto-links it — tel: in buttons is rejected). callback_data
# carries the event id: crisis:{action}:{event_id}.
MAX_STAGE = 3

# (label, action) per stage. Actions: call/safe/still/cant_call/contact/
# safe_place/contacted. Only still & cant_call change the stage.
_STAGE_BUTTONS = {
    0: [("📞 ПОЗВОНИТЬ", "call"), ("💬 Я в безопасности", "safe"),
        ("💔 Мне всё ещё плохо", "still"), ("🚫 Не могу позвонить", "cant_call")],
    1: [("📞 ПОЗВОНИТЬ", "call"), ("🚫 Не могу позвонить", "cant_call"),
        ("👤 Написать близкому", "contact"), ("💬 Я в безопасности", "safe")],
    2: [("📞 Всё-таки позвонить", "call"), ("✅ Я в безопасном месте", "safe_place"),
        ("👤 Написал близкому", "contacted"), ("💔 Мне всё ещё плохо", "still")],
    3: [("📞 ПОЗВОНИТЬ", "call"), ("👤 Написать близкому", "contact"),
        ("💬 Я в безопасности", "safe")],
}


def _numbers_block(lang: str, emergency_first: bool = False) -> str:
    h = get_hotline(lang)
    p, s = h["primary"], h["secondary"]
    if emergency_first:
        return f"📞 <b>{s}</b> — экстренная служба\n📞 <b>{p}</b>"
    return (f"📞 <b>{p}</b> — нажми на номер, чтобы позвонить\n"
            f"📞 <b>{s}</b> — экстренная служба")


_STAGE_TEXT = {
    0: ("Мне важно, чтобы ты сейчас не оставался(ась) один(одна).\n\n"
        "Если есть риск, что ты можешь причинить себе вред — пожалуйста, "
        "позвони прямо сейчас:\n\n{nums}\n\n"
        "И если рядом есть близкий человек — напиши ему."),
    1: ("Понял(а). Сейчас главное — не оставаться одному(одной).\n\n"
        "Пожалуйста, позвони:\n\n{nums}\n\n"
        "Если не можешь говорить — выбери «Не могу позвонить»."),
    2: ("Хорошо. Тогда самый простой безопасный шаг: перейди в безопасное место, "
        "подальше от всего, чем можешь себе навредить, и будь рядом с людьми.\n\n"
        "Если рядом есть человек — покажи ему: «Мне сейчас небезопасно одному. "
        "Пожалуйста, побудьте со мной и помогите вызвать помощь».\n\n{nums}"),
    3: ("Сейчас это похоже на ситуацию, где нужна помощь живого человека.\n\n"
        "Пожалуйста, не оставайся один(одна) — позвони или подойди к любому "
        "человеку рядом:\n\n{nums_e}"),
}


def _stage_keyboard(stage: int, event_id) -> InlineKeyboardMarkup:
    rows, row = [], []
    for label, action in _STAGE_BUTTONS[stage]:
        row.append(InlineKeyboardButton(text=label, callback_data=f"crisis:{action}:{event_id}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def crisis_screen(stage: int, lang: str, event_id) -> tuple:
    """Return (text, keyboard) for a crisis stage. The number is always in the
    body. stage is clamped to [0, MAX_STAGE]."""
    stage = max(0, min(MAX_STAGE, int(stage)))
    text = _STAGE_TEXT[stage].format(
        nums=_numbers_block(lang), nums_e=_numbers_block(lang, emergency_first=True))
    return text, _stage_keyboard(stage, event_id)


def safe_only_keyboard(event_id, lang: str = "ru") -> InlineKeyboardMarkup:
    label = "💬 Я в безопасности" if lang != "en" else "💬 I'm safe right now"
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, callback_data=f"crisis:safe:{event_id}")]])


def crisis_call_text(lang: str = "ru") -> str:
    return ("Набери, пожалуйста — номер кликабельный:\n\n" + _numbers_block(lang)
            + "\n\nНажми на номер, чтобы позвонить.")


def crisis_contact_template(lang: str = "ru") -> str:
    return ("Можешь переслать это близкому человеку:\n\n"
            "«Мне сейчас тяжело и небезопасно оставаться одному. "
            "Можешь побыть со мной или позвонить мне прямо сейчас?»")


def crisis_safe_place_ack(lang: str = "ru") -> str:
    return ("Это хорошо. Ты молодец, что сделал(а) этот шаг.\n\n"
            "Я не буду сейчас ничего разбирать. Когда почувствуешь, что "
            "в безопасности — нажми «Я в безопасности».")


# While a crisis is active, the "offer 'I'm safe'" branch must be reserved for
# EXPLICITLY reassuring text. Any distress / unsafe signal (or anything unclear)
# keeps the crisis screen — default to safety, never assume it. Deterministic.
_REASSURING_MARKERS = [
    "я ок", "всё хорошо", "все хорошо", "уже лучше", "мне лучше", "стало легче",
    "спасибо", "успокоил", "я в порядке", "всё нормально", "все нормально",
    "отпустило", "спокойнее", "уже спокойно", "я в безопасности",
    "i'm ok", "im ok", "i'm fine", "im fine", "better now", "calmer", "i'm safe",
]
_DISTRESS_MARKERS = [
    "плохо", "не в безопасности", "небезопасно", "не могу", "страшно", "опасно",
    "тяжело", "хуже", "паник", "не справля", "больно", "помоги", "умру", "конец",
    "not safe", "can't", "scared", "worse", "help", "danger",
]


def is_reassuring(text: str) -> bool:
    """True only for explicitly calm/positive text with NO distress signal.
    Everything else (distress, or simply unclear) → keep the crisis screen."""
    t = (text or "").lower()
    if any(d in t for d in _DISTRESS_MARKERS):
        return False
    return any(r in t for r in _REASSURING_MARKERS)


def crisis_resolved_text(lang: str = "ru") -> str:
    return ("Хорошо. Рад(а), что ты ответил(а). Сейчас ничего не будем разбирать.\n\n"
            "Просто побудь в более безопасном месте и не оставайся один(одна), "
            "если есть возможность. Я рядом.")


def _mask_excerpt(text: str, keep: int = 24) -> str:
    """Short, privacy-preserving excerpt — NEVER the full personal message."""
    t = " ".join((text or "").split())
    return (t[:keep] + "…") if len(t) > keep else t


def admin_alert_text(uid: int, username: str, stage: int,
                     risk: dict, message_text: str, event_id) -> str:
    """Privacy-safe #CRITICAL alert: metadata + event id + a SHORT masked excerpt
    (not the full personal message)."""
    cats = ", ".join(risk.get("categories", []) or []) or "—"
    return (
        f"🚨 #CRITICAL  event_id={event_id}  stage={stage}\n"
        f"user: {uid} (@{username or '—'})\n"
        f"risk: {risk.get('level', '—')} ({risk.get('score', '—')}) | {cats}\n"
        f"выдержка: «{_mask_excerpt(message_text)}» ({len(message_text or '')} симв.)"
    )
