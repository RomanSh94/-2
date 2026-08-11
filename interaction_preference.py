"""X20 Interaction Preference Detector — current-turn only.

Detects whether the CURRENT message explicitly asks for a particular
interaction style ("just talk, no advice" vs. "give me advice"). This is
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

# B. Explicit request for guidance/advice.
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

# C. Softer "just want to talk" cues (weaker than A — does not override B).
SOFT_JUST_TALK_SIGNALS: Dict[str, List[str]] = {
    "ru": ["хочу просто поговорить", "просто поговорить", "поговори со мной",
           "хочу поговорить", "давай просто поговорим"],
    "en": ["just want to talk", "just talk to me", "want to just talk", "can we just talk"],
}


def detect_interaction_preference(text: str, lang: str = "ru") -> str:
    """Returns "NONE" | "JUST_TALK" | "ADVICE_REQUEST" for the current turn.

    Precedence: explicit no-advice request > explicit advice request >
    soft just-talk cue > NONE. Both languages are always checked regardless
    of `lang`, matching every other detector in this codebase.
    """
    t = _normalize(text)
    langs = {lang, "ru", "en"}

    for l in langs:
        if any(sig in t for sig in HARD_NO_ADVICE_SIGNALS.get(l, [])):
            return "JUST_TALK"

    for l in langs:
        if any(sig in t for sig in ADVICE_REQUEST_SIGNALS.get(l, [])):
            return "ADVICE_REQUEST"

    for l in langs:
        if any(sig in t for sig in SOFT_JUST_TALK_SIGNALS.get(l, [])):
            return "JUST_TALK"

    return "NONE"
