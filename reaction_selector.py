"""X20 Reaction Selector — bounded, deterministic empathetic Telegram
message reactions (Voice and Adaptive Response UX).

No LLM call: the category comes from existing risk/stage signals plus a
small bounded RU/EN keyword list, the same pattern-matching style already
used throughout this repo (risk_detector.py, relationship_monitor.py).
The category is a transient acknowledgement of the current message only —
it is never persisted as a psychological profile, diagnosis, or trait.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional


class ReactionCategory(str, Enum):
    TEARS_WELLING = "TEARS_WELLING"
    HEARTBREAK_OR_LOSS = "HEARTBREAK_OR_LOSS"
    SADNESS_OR_DISAPPOINTMENT = "SADNESS_OR_DISAPPOINTMENT"
    LONELINESS_OR_REJECTION = "LONELINESS_OR_REJECTION"
    ANXIETY_OR_WORRY = "ANXIETY_OR_WORRY"
    FEAR_OR_SHOCK = "FEAR_OR_SHOCK"
    EXHAUSTION_OR_OVERWHELM = "EXHAUSTION_OR_OVERWHELM"
    CONFUSION_OR_UNCERTAINTY = "CONFUSION_OR_UNCERTAINTY"
    ANGER_OR_FRUSTRATION = "ANGER_OR_FRUSTRATION"
    RELIEF_OR_CALM = "RELIEF_OR_CALM"
    GRATITUDE_OR_WARMTH = "GRATITUDE_OR_WARMTH"
    PROGRESS_OR_ACHIEVEMENT = "PROGRESS_OR_ACHIEVEMENT"
    PRACTICE_COMPLETED = "PRACTICE_COMPLETED"
    NONE = "NONE"


# Ordered preferred -> fallback candidates, tried in order against a chat's
# actual available_reactions. Product correction: 🥹 is the PRIMARY reaction
# for tears-welling / emotional vulnerability / a painful-but-not-acute
# experience — NOT 🫂. 🫂 may appear inside supportive prose elsewhere; it is
# deliberately absent from this mapping.
REACTION_MAP: dict[ReactionCategory, tuple[str, ...]] = {
    ReactionCategory.TEARS_WELLING: ("🥹", "😔"),
    ReactionCategory.HEARTBREAK_OR_LOSS: ("💔", "🥹", "😔"),
    ReactionCategory.SADNESS_OR_DISAPPOINTMENT: ("😔", "🥹"),
    ReactionCategory.LONELINESS_OR_REJECTION: ("🥹", "💔", "😔"),
    ReactionCategory.ANXIETY_OR_WORRY: ("😟", "😔"),
    ReactionCategory.FEAR_OR_SHOCK: ("😨", "😟"),
    ReactionCategory.EXHAUSTION_OR_OVERWHELM: ("😮‍💨", "😔"),
    ReactionCategory.CONFUSION_OR_UNCERTAINTY: ("🤔", "😕"),
    ReactionCategory.ANGER_OR_FRUSTRATION: ("😤", "😔"),
    ReactionCategory.RELIEF_OR_CALM: ("😌", "❤️"),
    ReactionCategory.GRATITUDE_OR_WARMTH: ("❤️", "👍"),
    ReactionCategory.PROGRESS_OR_ACHIEVEMENT: ("🔥", "🎉", "👍"),
    ReactionCategory.PRACTICE_COMPLETED: ("👍", "❤️"),
}

# Risk categories that must NEVER receive a decorative reaction, regardless
# of confidence or flag state — a crisis/acute-danger message is handled by
# the deterministic crisis protocol only.
_NEVER_REACT_RISK_CATEGORIES = {"suicide", "self_harm"}

# Small, bounded RU/EN keyword lists for categories not directly covered by
# an existing risk_detector.py category. Deliberately narrow (a handful of
# unambiguous phrases each) — this is not a sentiment-analysis system.
_KEYWORDS: dict[str, dict[ReactionCategory, tuple[str, ...]]] = {
    "ru": {
        ReactionCategory.HEARTBREAK_OR_LOSS: (
            "расстались", "бросил меня", "бросила меня", "потеряла его",
            "потерял её", "умер", "умерла", "развод", "рассталась", "расстался",
        ),
        ReactionCategory.FEAR_OR_SHOCK: (
            "испугал", "испугалась", "испугался", "очень страшно", "в шоке",
            "напугал",
        ),
        ReactionCategory.RELIEF_OR_CALM: (
            "отпустило", "стало легче", "успокоилась", "успокоился",
            "выдохнула", "выдохнул", "полегчало",
        ),
        ReactionCategory.GRATITUDE_OR_WARMTH: (
            "спасибо", "благодарю", "признательн",
        ),
        # Owner-canary finding: the ordinary phrases actually sent live
        # ("сегодня мне немного тревожно...", "я очень устал...") matched
        # nothing here AND produced no risk category, so the selector
        # correctly returned NONE and no reaction ever appeared. The
        # risk-category fallbacks below only fire on panic/burnout-level
        # signals; everyday, sub-clinical wording never reaches them. These
        # stems close that gap without widening the emotional claim -- a
        # reaction stays a transient acknowledgement, never an assessment.
        #
        # Stems, not whole words, so ordinary inflections cost one entry
        # each: "тревож" covers тревожно/тревожусь/тревога/тревожный,
        # "устал" covers устал/устала, "расстроен" covers расстроен(а).
        # Both stems are needed: the "тревож-" forms (тревожно/тревожусь/
        # тревожный) and the "тревог-" forms (тревога/тревогу/тревоге) do not
        # share a common prefix beyond "трево".
        ReactionCategory.ANXIETY_OR_WORRY: (
            "тревож", "тревог", "переживаю", "беспокоюсь", "волнуюсь",
        ),
        ReactionCategory.EXHAUSTION_OR_OVERWHELM: (
            "устал", "вымотан", "измотан", "нет сил",
        ),
        ReactionCategory.SADNESS_OR_DISAPPOINTMENT: (
            "мне грустно", "обидно", "разочаров", "как жаль", "расстроен",
        ),
        ReactionCategory.CONFUSION_OR_UNCERTAINTY: (
            "не знаю что делать", "запуталась", "запутался", "совсем не понимаю",
        ),
    },
    "en": {
        ReactionCategory.HEARTBREAK_OR_LOSS: (
            "broke up with me", "left me", "passed away", "she died", "he died",
            "divorce", "lost her", "lost him",
        ),
        ReactionCategory.FEAR_OR_SHOCK: (
            "scared me", "so scared", "terrified", "so shocked",
        ),
        ReactionCategory.RELIEF_OR_CALM: (
            "feel so much better", "relieved", "calmed down", "such a relief",
        ),
        ReactionCategory.GRATITUDE_OR_WARMTH: (
            "thank you", "thanks so much", "so grateful", "i appreciate",
        ),
        ReactionCategory.ANXIETY_OR_WORRY: (
            "anxious", "worried", "nervous about", "stressed about",
        ),
        ReactionCategory.EXHAUSTION_OR_OVERWHELM: (
            "exhausted", "so tired", "worn out", "no energy left",
        ),
        ReactionCategory.SADNESS_OR_DISAPPOINTMENT: (
            "so sad", "really disappointed", "bums me out", "upset",
        ),
        ReactionCategory.CONFUSION_OR_UNCERTAINTY: (
            "don't know what to do", "so confused", "i don't understand any of this",
        ),
    },
}

# Confidence bands — deterministic rule matches, not a probability model.
# A direct keyword hit is more specific than a broad risk-category/stage
# fallback, so it is scored higher; EMOTIONAL_REACTION_MIN_CONFIDENCE lets a
# deployment require the stronger signal only.
_CONF_KEYWORD = 0.9
_CONF_RISK_CATEGORY = 0.75
_CONF_STAGE = 0.55


def select_reaction_category(
    user_text: str,
    risk_categories: list[str],
    stage: str,
    lang: str = "ru",
    is_meta_command: bool = False,
    is_dependency_redirect: bool = False,
) -> tuple[ReactionCategory, float]:
    """Deterministic, rule-based category + confidence. Returns
    (NONE, 0.0) for anything crisis-adjacent, a format meta-command, a
    dependency redirect, or when no rule matches."""
    if is_meta_command or is_dependency_redirect:
        return ReactionCategory.NONE, 0.0
    if any(c in risk_categories for c in _NEVER_REACT_RISK_CATEGORIES):
        return ReactionCategory.NONE, 0.0

    text_low = (user_text or "").lower()
    kw = _KEYWORDS.get(lang, _KEYWORDS["ru"])
    for cat, phrases in kw.items():
        if any(p in text_low for p in phrases):
            return cat, _CONF_KEYWORD

    if "hopelessness" in risk_categories:
        return ReactionCategory.TEARS_WELLING, _CONF_RISK_CATEGORY
    if "loneliness" in risk_categories:
        return ReactionCategory.LONELINESS_OR_REJECTION, _CONF_RISK_CATEGORY
    if "panic" in risk_categories:
        return ReactionCategory.ANXIETY_OR_WORRY, _CONF_RISK_CATEGORY
    if "aggression" in risk_categories:
        return ReactionCategory.ANGER_OR_FRUSTRATION, _CONF_RISK_CATEGORY
    if "burnout" in risk_categories:
        return ReactionCategory.EXHAUSTION_OR_OVERWHELM, _CONF_RISK_CATEGORY
    if "dissociation" in risk_categories:
        return ReactionCategory.CONFUSION_OR_UNCERTAINTY, _CONF_RISK_CATEGORY

    if stage == "GROWTH":
        return ReactionCategory.PROGRESS_OR_ACHIEVEMENT, _CONF_STAGE

    return ReactionCategory.NONE, 0.0


def pick_supported_emoji(category: ReactionCategory,
                          available: Optional[list[str]]) -> Optional[str]:
    """`available=None` means the chat allows all standard reactions (Bot
    API semantics: ChatFullInfo.available_reactions is omitted in exactly
    that case — see bot.py's call site). `available=[]` means no reaction
    is ever supported there. Returns the first mapped candidate the chat
    actually supports, or None if none is."""
    candidates = REACTION_MAP.get(category, ())
    if not candidates:
        return None
    if available is None:
        return candidates[0]
    for c in candidates:
        if c in available:
            return c
    return None
