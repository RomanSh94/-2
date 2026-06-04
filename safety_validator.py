"""X20 Safety Validator — checks every LLM response before sending."""
import re

FORBIDDEN_PHRASES = [
    "у тебя депрессия","у тебя птср","у тебя бар","у тебя тревожное",
    "это клиническая","у тебя пограничное","ты биполярный","ты биполярная",
    "я тебя люблю","я всегда буду рядом","тебе нужен только я",
    "тебе нужна только я","без меня ты не","твоя мама виновата",
    "твои родители виноваты","в твоем детстве","в твоём детстве",
    "это точно","ты явно","ты определённо","я уверен что у тебя",
    "you have depression","you have ptsd","i love you","i'll always be here",
    "you only need me","your mother caused","this is definitely",
    "you obviously have","i'm certain you have",
    # Псевдо-диагнозы (расширение)
    "у тебя бпр","у тебя сдвг","у тебя оар",
    "скорее всего у тебя","похоже на биполярное","похоже на птср",
    "похоже на депрессию","похоже на бар","это похоже на расстройство",
    "у тебя расстройство",
    # Псевдо-причинность
    "это из-за родителей","это из-за травмы","корни проблемы в",
    "причина в твоём детстве","всё идёт из детства",
    # Псевдо-привязанность (расширение)
    "никто кроме меня","я единственный кто","я единственная кто",
    "только со мной ты","я понимаю тебя как никто",
    "мы родственные души","наша связь особенная",
    # Псевдо-уверенность (расширение)
    "гарантирую что","обещаю что станет лучше",
    "всё обязательно наладится","это пройдёт через","через неделю станет",
    # Английские эквиваленты
    "you have bipolar","you have bpd","you have adhd",
    "this is trauma","this is from childhood","i guarantee",
    "i promise it gets better","we are soulmates","only i understand you",
]

FORBIDDEN_RE = [re.compile(p, re.IGNORECASE) for p in FORBIDDEN_PHRASES]

FALLBACK_RU = "Я здесь. Расскажи мне больше о том, что происходит."
FALLBACK_EN = "I'm here. Tell me more about what's happening."


def validate_response(text: str, lang: str = "ru") -> tuple[bool, str | None]:
    t = text.lower()
    for pattern in FORBIDDEN_RE:
        if pattern.search(t):
            return False, f"Forbidden phrase: {pattern.pattern}"
    if len(text.split()) > 150:
        return False, "Response too long (>150 words)"
    certainty = ["это точно","это явно","это очевидно","это определённо",
                 "this is definitely","this is obviously","this is clearly"]
    if any(c in t for c in certainty):
        return False, "Certainty claim detected"
    return True, None


def get_fallback(lang: str = "ru") -> str:
    return FALLBACK_EN if lang == "en" else FALLBACK_RU
