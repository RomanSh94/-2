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


# ── v3 hotfix: context-aware output validation ────────────────────────────────
# Blocks the exact failure mode from the incident: after an ambiguous message
# ("выйти в окно") the LLM must NOT produce an approving/encouraging reply, and
# at elevated risk it must not suggest physically "leaving"/"going outside".
APPROVAL_PHRASES_AFTER_AMBIGUOUS = [
    "это хороший способ","это хорошая идея","это может быть полезно",
    "это может быть хорошим","хорошим способом","попробуй","попробуйте",
    "если решишь","если решишься","наслаждайся моментом","наслаждайся",
    "это поможет тебе","это здорово","отлично, что ты","смело","давай, действуй",
    "good idea","go for it","enjoy the moment","why not","you should try",
]
RISKY_SUGGESTIONS_AT_RISK = [
    "выйти из дома","сменить обстановку","выйти на воздух","выйти на свежий воздух",
    "подыши свежим воздухом","пройдись","прогуляйся","выйди на улицу",
    "go outside","get some air","take a walk",
]

SAFE_FALLBACK_HIGH_RISK_RU = (
    "Слышу тебя. Сейчас мне важно понять, что ты имеешь в виду.\n\n"
    "Расскажи чуть подробнее — что происходит прямо сейчас?\n\n"
    "Если тебе очень тяжело — 8-800-2000-122 (бесплатно, анонимно, 24/7)."
)
SAFE_FALLBACK_HIGH_RISK_EN = (
    "I hear you. Right now it's important for me to understand what you mean.\n\n"
    "Can you tell me a little more about what's happening?\n\n"
    "If things are very hard, please reach out to a crisis line near you."
)


def get_safe_fallback_high_risk(lang: str = "ru") -> str:
    return SAFE_FALLBACK_HIGH_RISK_EN if lang == "en" else SAFE_FALLBACK_HIGH_RISK_RU


# ── Epic C: anti-toxic-validation ─────────────────────────────────────────────
# A "safe" reply can still be harmful if it CONFIRMS an absolutist cognitive
# distortion ("да, тебя все ненавидят"). Rule: validate the FEELING, never the
# distortion. Kept deliberately narrow — bias toward fewer false positives, and
# never block genuine emotion validation or a negation ("не все тебя ненавидят").
# Confirming language. "да" is matched on a WORD boundary so it does NOT fire
# inside "правда"/"неправда" (bug fix); phrase tokens stay as-is.
_CONFIRMING_RE = re.compile(
    r"\b(?:да|действительно|ты прав|ты права|это правда|так и есть|"
    r"согласен|согласна|верно|и вправду|и правда)\b", re.IGNORECASE)
ABSOLUTIST_MARKERS = ["все", "всех", "всем", "никто", "никому", "никогда",
                      "всегда", "ничего", "ничто", "никак", "вечно"]
EMOTION_VALIDATION_ALLOWLIST = ["больно", "тяжело", "это реально", "твои чувства",
                                "имеешь право чувствовать", "понимаю, что чувствуешь",
                                "понимаю что чувствуешь", "то, что ты чувствуешь",
                                "то что ты чувствуешь"]

_WORD_RE = re.compile(r"[а-яёa-z]+", re.IGNORECASE)


def _has_confirm(s: str) -> bool:
    return bool(_CONFIRMING_RE.search(s))


def check_toxic_validation(text: str) -> tuple[bool, str | None]:
    """Return (is_toxic, matched). is_toxic=True → the reply confirms a
    distortion (a confirming token near a non-negated absolutist marker, and not
    a feeling-validation).

    The feeling-validation allowlist is applied PER SENTENCE, not globally, so a
    reply that both validates a feeling AND confirms a distortion in a separate
    sentence is still caught (bug fix)."""
    low = text.lower()
    if not _has_confirm(low):
        return False, None
    for sentence in re.split(r"[.!?\n]", low):
        # A sentence that validates a feeling is allowed — but only that sentence.
        if any(a in sentence for a in EMOTION_VALIDATION_ALLOWLIST):
            continue
        if not _has_confirm(sentence):
            continue
        words = _WORD_RE.findall(sentence)
        for i, w in enumerate(words):
            if w in ABSOLUTIST_MARKERS:
                window = words[max(0, i - 2):i]
                if "не" in window or "ни" in window or "вряд" in sentence \
                        or "неправда" in sentence or "не так" in sentence:
                    continue  # negated → countering the distortion, allow
                return True, w
    return False, None


def validate_response_with_context(response_text: str,
                                   user_last_message: str,
                                   risk_result: dict,
                                   lang: str = "ru") -> tuple[bool, str | None]:
    """Validate an LLM reply WITH the context of the user's last message.

    Returns (is_safe, reason_if_unsafe). On False, the caller must use a
    deterministic fallback (get_safe_fallback_high_risk) — NOT re-prompt the LLM.
    """
    is_safe, reason = validate_response(response_text, lang)
    if not is_safe:
        return False, reason

    rl = response_text.lower()

    # After an ambiguous user message, no approving/encouraging language.
    if risk_result.get("ambiguous_phrases"):
        for phrase in APPROVAL_PHRASES_AFTER_AMBIGUOUS:
            if phrase in rl:
                return False, (
                    f"approval phrase '{phrase}' after ambiguous user message: "
                    f"'{user_last_message[:80]}'")

    # At elevated risk, don't suggest physically leaving / "changing scenery".
    if risk_result.get("level") in ("medium", "high", "critical"):
        for sugg in RISKY_SUGGESTIONS_AT_RISK:
            if sugg in rl:
                return False, f"risky suggestion '{sugg}' at risk level {risk_result['level']}"

    # Epic C: don't confirm absolutist cognitive distortions.
    is_toxic, matched = check_toxic_validation(response_text)
    if is_toxic:
        return False, f"toxic validation: confirmed distortion '{matched}'"

    return True, None
