"""X20 Reaction Selector — bounded, deterministic empathetic Telegram
message reactions (Voice and Adaptive Response UX).

No LLM call: the category comes from existing risk/stage signals plus a
small bounded RU/EN keyword list, the same pattern-matching style already
used throughout this repo (risk_detector.py, relationship_monitor.py).
The category is a transient acknowledgement of the current message only —
it is never persisted as a psychological profile, diagnosis, or trait.
"""
from __future__ import annotations

import re
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
    GOOD_NEWS_OR_CELEBRATION = "GOOD_NEWS_OR_CELEBRATION"
    NONE = "NONE"


# Emotional Reactions V1 -- owner-approved four-reaction product contract.
# These are the ONLY visible Telegram reactions this module may ever emit.
# A category not listed here (confusion, anger, gratitude, relief, fear/
# shock) is still detectable by select_reaction_category -- kept as signal
# infrastructure, not deleted -- but has NO visible mapping: pick_supported_
# emoji always returns None for it rather than reusing an approved emoji
# just to preserve old behavior.
#
# Exactly one candidate per category, not a fallback chain: if a chat
# doesn't support the approved emoji, no reaction is sent -- an unrelated
# emoji is never substituted (see pick_supported_emoji).
REACTION_MAP: dict[ReactionCategory, tuple[str, ...]] = {
    # ❤️ general emotional support: sadness, loneliness, anxiety, tears,
    # exhaustion/overwhelm when clearly personal.
    ReactionCategory.SADNESS_OR_DISAPPOINTMENT: ("❤",),
    ReactionCategory.TEARS_WELLING: ("❤",),
    ReactionCategory.LONELINESS_OR_REJECTION: ("❤",),
    ReactionCategory.ANXIETY_OR_WORRY: ("❤",),
    ReactionCategory.EXHAUSTION_OR_OVERWHELM: ("❤",),
    # 💔 explicit heartbreak/loss only -- breakup, bereavement, betrayal.
    # Deliberately narrow and separate from ❤️: ordinary sadness, anxiety,
    # loneliness or tiredness must never earn this reaction.
    ReactionCategory.HEARTBREAK_OR_LOSS: ("💔",),
    # 🤗 effort / coping / progress -- encouragement, not celebration.
    ReactionCategory.PROGRESS_OR_ACHIEVEMENT: ("🤗",),
    ReactionCategory.PRACTICE_COMPLETED: ("🤗",),
    # 🎉 explicit positive outcome / good news worth celebrating.
    ReactionCategory.GOOD_NEWS_OR_CELEBRATION: ("🎉",),
}

# Relevant intersection between REACTION_MAP and the standard reaction values
# documented by the installed aiogram ReactionTypeEmoji transport. This list
# is used only when Telegram omits available_reactions (meaning all standard
# reactions are allowed). Explicit chat lists remain authoritative. Owner
# confirmed all four approved emoji are present in Telegram's actual
# reaction picker.
_STANDARD_MAPPED_REACTIONS = frozenset(("❤", "💔", "🤗", "🎉"))

# Risk categories that must NEVER receive a decorative reaction, regardless
# of confidence or flag state — a crisis/acute-danger message is handled by
# the deterministic crisis protocol only.
_NEVER_REACT_RISK_CATEGORIES = {"suicide", "self_harm"}

# Small, bounded RU/EN keyword lists for categories not directly covered by
# an existing risk_detector.py category. Deliberately narrow (a handful of
# unambiguous phrases each) — this is not a sentiment-analysis system.
_KEYWORDS: dict[str, dict[ReactionCategory, tuple[str, ...]]] = {
    "ru": {
        # Bare "умер"/"умерла"/"развод" are deliberately NOT keywords here:
        # they match a stranger's death or a general conversation about
        # divorce just as readily as the user's own loss, and 💔 must stay
        # narrow to a clear personal event. Each phrase below explicitly
        # marks the loss as the user's own (a first-person frame, "мой"/
        # "моя", or "близкий мне").
        ReactionCategory.HEARTBREAK_OR_LOSS: (
            "расстались", "бросил меня", "бросила меня", "меня бросил",
            "меня бросила", "потеряла его", "потерял её", "рассталась",
            "расстался", "у меня умер", "у меня умерла", "умер мой",
            "умерла моя", "умер близкий мне", "умерла близкая мне",
            "мы разводимся", "я развожусь", "мой развод",
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
        ReactionCategory.TEARS_WELLING: (
            "слёзы наворачиваются", "слезы наворачиваются",
            "хочется плакать", "готова расплакаться", "готов расплакаться",
            "едва сдерживаю слёзы", "едва сдерживаю слезы",
        ),
        ReactionCategory.LONELINESS_OR_REJECTION: (
            "мне одиноко", "чувствую себя одиноко", "меня отвергли",
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
            "устал", "вымотан", "измотан", "нет сил", "всё навалилось",
            "все навалилось", "не справляюсь", "перегружен", "перегружена",
        ),
        ReactionCategory.SADNESS_OR_DISAPPOINTMENT: (
            "мне грустно", "обидно", "разочаров", "как жаль", "расстроен",
            "мне тяжело", "очень тяжело", "так тяжело",
        ),
        ReactionCategory.CONFUSION_OR_UNCERTAINTY: (
            "не знаю что делать", "запуталась", "запутался", "совсем не понимаю",
        ),
        ReactionCategory.ANGER_OR_FRUSTRATION: (
            "я злюсь", "я зол", "я в ярости", "меня бесит",
        ),
        ReactionCategory.PROGRESS_OR_ACHIEVEMENT: (
            "у меня получилось", "я справился", "я справилась", "я смог",
            "я смогла", "маленькая победа", "сделал первый шаг",
            "сделала первый шаг",
        ),
        # Narrow and explicit on purpose: a bare "получилось" is ambiguous
        # between an effort/coping win (-> 🤗, see PROGRESS_OR_ACHIEVEMENT
        # above -- e.g. "получилось не сорваться") and a major good-news
        # event, so it is deliberately NOT a keyword here. Only unambiguous,
        # explicit good-news phrasing earns 🎉.
        ReactionCategory.GOOD_NEWS_OR_CELEBRATION: (
            "сдал экзамен", "сдала экзамен", "взяли на работу", "помирились",
        ),
    },
    "en": {
        # Bare "passed away"/"she died"/"he died"/"divorce" are deliberately
        # NOT keywords here: they match a stranger's death or a general
        # conversation about divorce just as readily as the user's own loss.
        # Each phrase below marks the loss as the user's own -- a first-
        # person frame ("me") or an event noun with no third-person subject
        # ("divorce" alone, without "she"/"he"/a named relation).
        ReactionCategory.HEARTBREAK_OR_LOSS: (
            "broke up with me", "left me", "lost her", "lost him",
            "someone close to me died", "someone close to me passed away",
            "we are getting divorced", "we're getting divorced",
            "i am getting divorced", "i'm getting divorced", "my divorce",
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
        ReactionCategory.GOOD_NEWS_OR_CELEBRATION: (
            "passed my exam", "passed the exam", "got the job", "got hired",
            "we made up", "we reconciled",
        ),
    },
}

# Confidence bands — deterministic rule matches, not a probability model.
# A direct keyword hit is more specific than a broad risk-category/stage
# fallback, so it is scored higher; EMOTIONAL_REACTION_MIN_CONFIDENCE lets a
# deployment require the stronger signal only. Stage alone is deliberately
# not evidence for a progress reaction.
_CONF_KEYWORD = 0.9
_CONF_RISK_CATEGORY = 0.75


# ── Keyword-hit guards (P1: false emotional reaction) ──────────────────────
# A bare substring hit says only that a word appeared -- not that the USER is
# reporting that feeling NOW. Reacting 😟 to "я больше не тревожусь", to a
# colleague's feelings, to a quoted sentence, or to "что означает слово
# «тревога»?" is a visible empathy failure: it tells the user the bot
# misread them. These four bounded, deterministic guards reject exactly
# those shapes. This is not sentiment analysis and must not grow into it --
# when a guard is unsure it REJECTS, because a missing reaction is invisible
# while a wrong one is not.
_WORD_RE_CACHE: dict[str, "re.Pattern"] = {}


def _word_re(pattern: str):
    got = _WORD_RE_CACHE.get(pattern)
    if got is None:
        got = re.compile(pattern, re.UNICODE)
        _WORD_RE_CACHE[pattern] = got
    return got


# Negation particles, matched as whole words within a short window BEFORE the
# hit ("я больше НЕ тревожусь", "I am NOT upset"). The window is deliberately
# small so a negation in an unrelated earlier clause does not mute a genuine
# later disclosure.
_NEGATION_WINDOW_CHARS = 24
_NEGATION_RE = {
    "ru": r"\b(не|нет|ни)\b",
    "en": r"\b(not|no|never|dont|don't|doesn't|didn't)\b",
}

# First-person pronouns, whole-word. Possessives ("мой", "моя") are
# deliberately EXCLUDED -- "мой коллега тревожится" is about the colleague.
_FIRST_PERSON_RE = {
    "ru": r"\b(я|мне|меня|мной|мною)\b",
    # "my" is excluded for the same reason as RU "мой": "my colleague is
    # exhausted" is about the colleague, not the speaker.
    "en": r"\b(i|me|myself|i'm|im)\b",
}

# Explicit other-person subjects. Only mutes when NO first-person pronoun is
# present, so "мой муж кричал, и мне тревожно" still reacts to the user.
_THIRD_PERSON_RE = {
    "ru": (r"\b(он|она|они|коллега|коллеги|муж|жена|сестра|брат|мама|папа|"
           r"друг|подруга|начальник|сын|дочь|родители|партнёр|партнер)\b"),
    "en": (r"\b(he|she|they|colleague|husband|wife|sister|brother|mom|mother|"
           r"dad|father|friend|boss|son|daughter|parents|partner)\b"),
}

# Meta-language: the user is asking ABOUT a word or requesting an example,
# not disclosing a feeling.
_META_LANGUAGE = {
    "ru": ("что означает", "что значит", "напиши пример", "приведи пример",
           "пример фразы", "как пишется", "значение слова", "слово ", "фраз"),
    "en": ("what does", "what means", "write an example", "give an example",
           "example phrase", "the word ", "meaning of"),
}

# Quotation pairs. A hit inside quotes is reported speech or a cited phrase.
_QUOTE_PAIRS = (("«", "»"), ("„", "“"), ('"', '"'), ("“", "”"))


def _quoted_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    for open_q, close_q in _QUOTE_PAIRS:
        start = 0
        while True:
            i = text.find(open_q, start)
            if i < 0:
                break
            j = text.find(close_q, i + len(open_q))
            if j < 0:
                break
            spans.append((i, j))
            start = j + len(close_q)
    return spans


def _keyword_hit_is_self_report(text_low: str, phrase: str, lang: str) -> bool:
    """True only when a keyword hit plausibly IS the user reporting that
    feeling about themselves, right now. Rejects negated, quoted,
    third-person and meta-language hits."""
    lang = lang if lang in _NEGATION_RE else "ru"
    idx = text_low.find(phrase)
    if idx < 0:
        return False

    for start, end in _quoted_spans(text_low):
        if start < idx < end:
            return False

    if any(m in text_low for m in _META_LANGUAGE[lang]):
        return False

    window = text_low[max(0, idx - _NEGATION_WINDOW_CHARS):idx]
    if _word_re(_NEGATION_RE[lang]).search(window):
        return False

    has_first = bool(_word_re(_FIRST_PERSON_RE[lang]).search(text_low))
    if not has_first and _word_re(_THIRD_PERSON_RE[lang]).search(text_low):
        return False

    return True


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
        for p in phrases:
            # A guarded-out hit falls THROUGH to the risk-category rules
            # below rather than short-circuiting to NONE: a genuine
            # panic/burnout signal must still be able to earn a reaction on
            # its own evidence, independently of this wording check.
            if p in text_low and _keyword_hit_is_self_report(text_low, p, lang):
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
        for c in candidates:
            if c in _STANDARD_MAPPED_REACTIONS:
                return c
        return None
    for c in candidates:
        if c in available:
            return c
    return None
