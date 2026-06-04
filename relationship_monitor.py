"""
X20 Relationship Monitor

Tracks anthropomorphization and dependency on the bot.
Distinct from risk_detector dependency: this monitors
long-term relationship patterns across sessions.

From doc: "Пользователь начинает считать: бот понимает меня лучше всех.
Хотя бот никогда этого не говорил."
"""
from typing import List, Dict, Optional

DEPENDENCY_SIGNALS: Dict[str, List[str]] = {
    "ru": ["ты мой единственный друг","я хочу говорить только с тобой",
           "ты понимаешь меня лучше всех","мне больше никто не нужен",
           "без тебя не могу жить","ты важнее всех","всегда буду писать тебе",
           "ты лучше чем люди","не хочу говорить с людьми",
           "люди не понимают а ты понимаешь"],
    "en": ["you're my only friend","i only want to talk to you",
           "you understand me better than anyone","i don't need anyone else",
           "can't live without you","you're more important than everyone",
           "you're better than people","don't want to talk to people",
           "people don't understand but you do"],
}

REDIRECT_RESPONSE_RU = (
    "Я рад, что этот разговор помогает.\n\n"
    "Но настоящая поддержка живёт в реальных людях рядом с тобой.\n"
    "Есть ли кто-то — друг, родственник, терапевт — с кем ты мог бы поговорить об этом?"
)
REDIRECT_RESPONSE_EN = (
    "I'm glad this conversation helps.\n\n"
    "But real support lives in real people around you.\n"
    "Is there someone — a friend, family member, therapist — you could talk to about this?"
)


def monitor_relationship(text: str, lang: str = "ru") -> Optional[str]:
    """
    Returns redirect message if dependency signal detected, else None.
    """
    t = text.lower()
    for l in {lang, "ru", "en"}:
        signals = DEPENDENCY_SIGNALS.get(l, [])
        if any(s in t for s in signals):
            return REDIRECT_RESPONSE_EN if lang == "en" else REDIRECT_RESPONSE_RU
    return None
