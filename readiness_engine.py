"""
X20 Readiness Engine

Determines user's readiness to engage with structured interventions.
LOW readiness → only short questions, grounding, observation
HIGH readiness → full CBT, ACT, structured work

From doc: "Бот: Какая мысль вызывает тревогу?
User: Не знаю. → Система ломается."
"""
from typing import Dict, List

LOW_SIGNALS: Dict = {
    "ru": ["не знаю","не понимаю","не могу объяснить","сложно сказать",
           "не могу говорить","просто плохо","не знаю что","ничего не понимаю",
           "не могу думать","голова не варит","слишком тяжело объяснять",
           "просто больно","не знаю как"],
    "en": ["i don't know","don't understand","can't explain","hard to say",
           "can't talk","just bad","don't know what","can't think",
           "brain not working","too hard to explain","just hurts","not sure how"],
}

HIGH_SIGNALS: Dict = {
    "ru": ["я хочу разобраться","хочу поработать над","давай попробуем",
           "я готов","я готова","да давай","попробуем","хочу понять",
           "объясни мне","расскажи как"],
    "en": ["i want to work on","let's try","i'm ready","yes let's",
           "want to understand","explain to me","tell me how","willing to try"],
}

def assess_readiness(text: str, lang: str = "ru") -> str:
    """Returns 'LOW', 'MEDIUM', or 'HIGH'"""
    t = text.lower()

    for l in {lang, "ru", "en"}:
        if any(s in t for s in HIGH_SIGNALS.get(l, [])):
            return "HIGH"

    for l in {lang, "ru", "en"}:
        if any(s in t for s in LOW_SIGNALS.get(l, [])):
            return "LOW"

    return "MEDIUM"
