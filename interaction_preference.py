"""X20 Interaction Preference Detector — current-turn only.

Detects whether the CURRENT message explicitly asks for a particular
interaction style (talk, understand, or act). This is
NOT persisted anywhere and NOT re-derived from history — it is computed
fresh per inbound message and used only to bias state_engine.choose_scenario
away from stale non-acute therapeutic routing when the user has just said,
in this turn, what they actually want.

Deterministic substring matching only. No regex, no fuzzy matching, no LLM.
"""
import re
from typing import Dict, List

_PUNCT_RE = re.compile(r"[,.!?;:\"'()«»\-–—]")


def _normalize(text: str) -> str:
    t = _PUNCT_RE.sub(" ", text.lower())
    return " ".join(t.split())


# A. Explicit refusal of advice / request to just be heard.
HARD_NO_ADVICE_SIGNALS: Dict[str, List[str]] = {
    "ru": ["без советов", "не хочу советов", "не хочу сейчас советов",
           "не надо советов", "не давай советов", "не советуй",
           "просто послушай", "просто выслушай", "хочу выговориться"],
    "en": ["no advice", "don't want advice", "don't need advice",
           "just listen", "just want to vent", "i just need to vent"],
}

# B. Explicit request to understand the mechanism/reason before advice.
# "мне интересно понять/разобраться" and "мне хочется понять/разобраться"
# (production-incident hotfix) are deliberately narrow, first-person,
# explicit-desire formulations -- NOT a broad standalone "что происходит"/
# "почему" trigger, which would false-positive on ordinary curiosity or
# meta-questions with no genuine understand-before-advice request behind them.
UNDERSTAND_SIGNALS: Dict[str, List[str]] = {
    "ru": ["хочу понять", "хочу разобраться", "мне важно понять",
           "мне важно разобраться", "мне интересно понять",
           "мне интересно разобраться", "мне хочется понять",
           "мне хочется разобраться", "почему я так делаю", "почему я это делаю",
           "почему это происходит", "почему со мной так", "понять причину",
           "разобраться в причине", "не просто получить совет",
           "не хочу просто совет", "не нужен просто совет"],
    "en": ["i want to understand", "i want to figure out",
           "it's important for me to understand", "it is important for me to understand",
           "i'm curious to understand", "i'm curious to figure out",
           "i'd like to understand", "i'd like to figure out",
           "why do i do this", "why am i doing this", "why does this happen",
           "understand the reason", "figure out the reason", "not just get advice",
           "don't just want advice", "do not just want advice", "not looking for just advice"],
}

# C. Explicit request for guidance/action.
ADVICE_REQUEST_SIGNALS: Dict[str, List[str]] = {
    "ru": ["дай совет", "дайте совет", "дай мне совет", "посоветуй", "посоветуйте",
           "подскажи", "подскажите", "что мне делать", "что мне сделать",
           "как поступить", "как справиться", "нужен совет", "нужен твой совет",
           "что посоветуешь", "расскажи как мне", "подскажи как мне",
           "посоветуй как мне", "скажи как мне"],
    "en": ["what should i do", "any advice", "what would you suggest",
           "how should i cope", "how do i handle this", "how can i deal with this",
           "what do you suggest", "tell me how i can", "tell me what to do"],
}

# D. Softer "just want to talk" cues (weaker than explicit action).
SOFT_JUST_TALK_SIGNALS: Dict[str, List[str]] = {
    "ru": ["хочу просто поговорить", "просто поговорить", "поговори со мной",
           "хочу поговорить", "давай просто поговорим"],
    "en": ["just want to talk", "just talk to me", "want to just talk", "can we just talk"],
}

# Negation words that, immediately preceding an UNDERSTAND signal, negate
# THAT specific occurrence ("я не хочу понять" must not match "хочу понять").
# A negation elsewhere in the same message ("не хочу советов, хочу понять
# почему") never suppresses a genuinely unnegated later occurrence -- see
# _has_unnegated_match. Authored naturally (contractions included); run
# through _normalize() below like every signal, so no form needs to be
# hand-pre-normalized here.
_UNDERSTAND_NEGATIONS: Dict[str, tuple] = {
    "ru": ("не",),
    "en": ("not", "never", "don't", "do not", "doesn't", "does not"),
}


def _normalized_signals(signals: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """Derive a normalized copy of an authored signal dict. Authored strings
    stay human-readable (contractions, punctuation) -- normalization happens
    here, once, from that single source, so there is never a hand-maintained
    second copy to keep in sync."""
    return {lang: [_normalize(sig) for sig in sigs] for lang, sigs in signals.items()}


_NORM_HARD_NO_ADVICE_SIGNALS = _normalized_signals(HARD_NO_ADVICE_SIGNALS)
_NORM_UNDERSTAND_SIGNALS = _normalized_signals(UNDERSTAND_SIGNALS)
_NORM_ADVICE_REQUEST_SIGNALS = _normalized_signals(ADVICE_REQUEST_SIGNALS)
_NORM_SOFT_JUST_TALK_SIGNALS = _normalized_signals(SOFT_JUST_TALK_SIGNALS)
_NORM_UNDERSTAND_NEGATIONS = {
    lang: tuple(_normalize(neg) for neg in negs) for lang, negs in _UNDERSTAND_NEGATIONS.items()
}


def _has_unnegated_match(t: str, signals: List[str], negations: tuple) -> bool:
    """True if at least one (already-normalized) signal has an occurrence in
    `t` that is NOT immediately preceded by a (already-normalized) negation
    word as its own token."""
    for sig in signals:
        start = 0
        while True:
            pos = t.find(sig, start)
            if pos == -1:
                break
            prefix = t[:pos].rstrip()
            if not any(prefix == neg or prefix.endswith(" " + neg) for neg in negations):
                return True
            start = pos + 1
    return False


def detect_interaction_preference(text: str, lang: str = "ru") -> str:
    """Return NONE | JUST_TALK | UNDERSTAND | ACTION for the current turn.

    UNDERSTAND is checked first because requests such as "не хочу просто
    совет, хочу понять причину" are a positive request to investigate, not
    merely a request to be heard. Both languages are always checked regardless
    of `lang`, matching every other detector in this codebase.

    UNDERSTAND matching is negation-aware: an UNDERSTAND signal immediately
    preceded by "не"/"not"/etc. ("не хочу понять") does not count as that
    occurrence being a genuine UNDERSTAND request. A negation attached to a
    DIFFERENT part of the same message ("не хочу советов, хочу понять
    почему") never suppresses the real, unnegated UNDERSTAND request later in
    the same text -- only the specific negated occurrence is discounted.

    Every signal is matched in normalized form (see _normalized_signals):
    `_normalize` strips apostrophes, so an authored contraction like "don't
    want advice" must be compared against normalized user text as "don t
    want advice", not against its own unnormalized punctuation.
    """
    t = _normalize(text)
    langs = {lang, "ru", "en"}

    for l in langs:
        if _has_unnegated_match(t, _NORM_UNDERSTAND_SIGNALS.get(l, []),
                                _NORM_UNDERSTAND_NEGATIONS.get(l, ())):
            return "UNDERSTAND"

    for l in langs:
        if any(sig in t for sig in _NORM_HARD_NO_ADVICE_SIGNALS.get(l, [])):
            return "JUST_TALK"

    for l in langs:
        if any(sig in t for sig in _NORM_ADVICE_REQUEST_SIGNALS.get(l, [])):
            return "ACTION"

    for l in langs:
        if any(sig in t for sig in _NORM_SOFT_JUST_TALK_SIGNALS.get(l, [])):
            return "JUST_TALK"

    return "NONE"
