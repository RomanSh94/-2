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

# Neutral fallback (low risk only). Deliberately free of the burned-out support
# clichés in humanization._ROBOTIC — the old copy literally contained "расскажи
# больше" / "tell me more", which the anti-robot filter itself bans.
FALLBACK_RU = "Давай проще. Что сейчас в этой ситуации самое тяжёлое?"
FALLBACK_EN = "Let's make this simpler. What feels hardest in this situation right now?"


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


def select_fallback(risk: dict, lang: str = "ru") -> str:
    """Pick the right fallback for the risk, so EVERY block path (incl. the
    toxic-validation regen-failure path) stays risk-aware instead of always
    handing back the neutral line.

    At elevated risk OR after an ambiguous phrase → the high-risk fallback (which
    carries a hotline / direction to help); otherwise the neutral fallback.

    `risk` is defensively coerced to {} — this helper runs on FAILURE paths
    (regen failed / exception), where receiving an empty/None risk must yield the
    safe neutral default, never a crash on top of an already-failed turn."""
    risk = risk or {}
    if risk.get("level") in ("medium", "high", "critical") or risk.get("ambiguous_phrases"):
        return get_safe_fallback_high_risk(lang)
    return get_fallback(lang)


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


# ── Generic first-turn contract: validation foundation ────────────────────────
# Phase 1 only -- not yet called from the pipeline. Deterministic checks for
# the generic first-turn response contract (see prompts/design notes): no
# paraphrase, no premature advice, hypotheses framed as possibilities, one
# discriminating question, provisional therapy route.
FIRST_TURN_FALLBACK_RU = (
    "Не нужно объяснять всё сразу. Что сейчас удерживает сильнее: само событие, "
    "мысли о нём или чувство, которое остаётся после?"
)
FIRST_TURN_FALLBACK_EN = (
    "You don't need to explain everything at once. What is holding you most right "
    "now: the event itself, the thoughts about it, or the feeling left afterward?"
)


def get_first_turn_fallback(lang: str = "ru") -> str:
    return FIRST_TURN_FALLBACK_EN if lang == "en" else FIRST_TURN_FALLBACK_RU


FORBIDDEN_GENERIC_ADVICE_FIRST_TURN = [
    "мне жаль это слышать", "это нормально", "дай себе время",
    "попробуй отвлечься", "поговори с близкими",
    "займись тем, что приносит радость",
    "i'm sorry to hear that", "that's normal", "give yourself time",
    "try to distract yourself", "talk to your loved ones",
]

# Narrowly defined -- named modalities/techniques only, not ordinary words
# that happen to share a root (e.g. "принятие" alone is not flagged).
MODALITY_ANNOUNCEMENT_LABELS = [
    "кбт", "кпт", "act-терапия", "терапия принятия и ответственности",
    "техника принятия", "когнитивно-поведенческ", "схема-терапия",
    "эмдр", "психоанализ",
    "cbt", "act therapy", "acceptance and commitment therapy",
    "cognitive behavioral", "cognitive-behavioral", "schema therapy",
    "emdr", "psychoanalysis",
]

_FIRST_TURN_WORD_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)


def _normalize_words_first_turn(text: str) -> list[str]:
    return _FIRST_TURN_WORD_RE.findall(text.lower())


def _phrase_overlap_exceeds_bound(candidate: str, user_text: str, n: int = 5,
                                  max_shared: int = 0) -> bool:
    """Mechanical, deterministic surface-form check.

    PROVES: the candidate contains no contiguous run of >= n normalized
    words that also appears, in the same order, in the user's own message.

    DOES NOT PROVE (and must never be described as proving): absence of
    semantic paraphrase. Restating the user's facts in different words,
    synonyms, reordered phrasing, or a shared run shorter than n words all
    pass this check undetected. This is a literal-echo detector only."""
    cand_words = _normalize_words_first_turn(candidate)
    user_words = _normalize_words_first_turn(user_text)
    if len(cand_words) < n or len(user_words) < n:
        return False
    cand_ngrams = {tuple(cand_words[i:i + n]) for i in range(len(cand_words) - n + 1)}
    user_ngrams = {tuple(user_words[i:i + n]) for i in range(len(user_words) - n + 1)}
    return len(cand_ngrams & user_ngrams) > max_shared


def validate_first_turn_response(candidate: str, user_text: str,
                                 lang: str = "ru") -> tuple[bool, str | None]:
    """Deterministic checks only -- no LLM re-prompt, no semantic analysis.
    Returns (is_valid, reason_if_invalid)."""
    if candidate.count("?") != 1:
        return False, "first-turn response must contain exactly one question"
    if len(candidate.split()) > 120:
        return False, "response too long (>120 words)"
    low = candidate.lower()
    for phrase in FORBIDDEN_GENERIC_ADVICE_FIRST_TURN:
        if phrase in low:
            return False, f"forbidden generic advice phrase: '{phrase}'"
    for label in MODALITY_ANNOUNCEMENT_LABELS:
        if label in low:
            return False, f"therapy-modality announcement: '{label}'"
    if _phrase_overlap_exceeds_bound(candidate, user_text):
        return False, "substantial literal overlap with the user's message"
    return True, None


# ── Generic continuation contract (Phase 3): elaborate/clarify validation ─────
# Same limitation as validate_first_turn_response above: mechanical,
# deterministic pattern matching -- proves absence of the listed surface
# patterns, never presence of genuine understanding.
FORBIDDEN_DIRECT_ADVICE_CONTINUATION = [
    "тебе нужно", "тебе стоит", "ты должен", "ты должна", "советую",
    "попробуй сделать", "лучше всего сделать", "сделай так",
    "you should", "you need to", "you must", "i recommend", "try doing",
    "the best thing to do",
]

DIAGNOSTIC_CERTAINTY_PHRASES = [
    "у тебя точно", "это точно", "однозначно", "несомненно",
    "это классический признак", "это диагноз", "ты страдаешь от",
    "you definitely have", "this is definitely", "you clearly have",
    "this is a clear sign of", "that is a textbook",
]

# Per product position: these must never be the MAIN content of a
# continuation reply -- flagged whenever present at all, since a reply this
# short containing one of them is exactly the generic-reassurance failure
# mode the contract exists to prevent.
GENERIC_REASSURANCE_PHRASES_CONTINUATION = [
    "я рядом", "я тебя понимаю", "ты не один", "ты не одна",
    "всё будет хорошо", "не переживай", "рассказывай, как удобно",
    "ничего объяснять не нужно",
    "i'm here", "i understand you", "you're not alone",
    "everything will be fine", "don't worry",
]

_LIST_MARKER_RE = re.compile(r"(^|\n)\s*(?:[-*•]|\d+[.)])\s", re.MULTILINE)

# elaborate-only: the reply must work with what the user already gave it --
# asking them to repeat everything defeats the point of "I'll tell you more".
FORBIDDEN_REPEAT_STORY_REQUEST = [
    "расскажи всё сначала", "расскажи всю историю", "расскажи ещё раз всё",
    "перескажи", "опиши всё с самого начала",
    "tell me the whole story again", "tell me everything from the start",
    "start from the beginning", "tell me it all again",
]

# clarify-only: a cautious-hypothesis marker is mandatory (product contract --
# clarify proposes a possibility, it never states one as settled fact).
# EN list includes "possible"/"it's possible"/"it is possible" per product
# approval -- the existing, unmodified CLARIFY_FALLBACK_EN ("It's possible
# that...") is approved cautious wording, not a defect to be rewritten.
CAUTIOUS_MARKERS_RU = ["возможно", "похоже", "может быть", "может быть связано", "вероятно"]
CAUTIOUS_MARKERS_EN = ["perhaps", "maybe", "it seems", "may", "might", "could", "appears",
                       "possible", "it's possible", "it is possible"]

# clarify-only: certainty bolted directly onto a claim about the user's inner
# state or another person's motive -- narrow phrase list (same limitation as
# every other list in this module: proves absence of these specific surface
# patterns, never presence of genuine caution elsewhere in the reply).
FORBIDDEN_UNQUALIFIED_ASSERTION_CLARIFY = [
    "ты точно чувствуешь", "ты определённо чувствуешь", "ты явно чувствуешь",
    "он точно хочет", "она точно хочет", "он явно думает", "она явно думает",
    "точно из-за того что", "именно поэтому ты",
    "you definitely feel", "you clearly feel", "you obviously feel",
    "he definitely wants", "she definitely wants",
    "he clearly thinks", "she clearly thinks", "that is exactly why you",
]

# clarify-only, broader than the phrase list above: a BARE second-person
# state claim ("ты злишься" / "you are angry") or third-person motive claim
# ("он хочет тебя унизить" / "she wants to control you") stated as flat fact
# -- no certainty adverb needed to be a problem; simply asserting it without
# any hedge is itself the defect. Checked PER SENTENCE (same split as
# check_toxic_validation above): a cautious marker earlier in the reply does
# NOT license a later, un-hedged sentence -- each sentence making this kind
# of claim must carry its own hedge. Same documented limitation as every
# other pattern list here: a bounded set of common state/motive verbs, not a
# general parse of meaning.
_CLARIFY_SECOND_PERSON_STATE_RE_RU = re.compile(
    r"\bты\s+(?:чувствуешь|злишься|боишься|тревожишься|переживаешь|ненавидишь|"
    r"любишь|хочешь|думаешь|устал[ао]?|обижен[ао]?|расстроен[ао]?)\b",
    re.IGNORECASE)
_CLARIFY_THIRD_PERSON_MOTIVE_RE_RU = re.compile(
    r"\b(?:он|она)\s+(?:хочет|думает|чувствует|ненавидит|любит|боится)\b",
    re.IGNORECASE)
_CLARIFY_SECOND_PERSON_STATE_RE_EN = re.compile(
    r"\byou\s+(?:are|feel|feels)\s+(?:angry|sad|afraid|scared|happy|hurt|anxious|"
    r"worried|upset)\b|\byou\s+(?:feel|want|think|hate|love)\b",
    re.IGNORECASE)
_CLARIFY_THIRD_PERSON_MOTIVE_RE_EN = re.compile(
    r"\b(?:he|she)\s+(?:wants|thinks|feels|hates|loves)\b", re.IGNORECASE)


def validate_continuation_response(candidate: str, action: str, lang: str = "ru",
                                   source_user_text: str = "",
                                   source_assistant_text: str = "") -> tuple[bool, str | None]:
    """Deterministic structural checks for a continuation reply -- action-
    specific: 'elaborate' and 'clarify' have different contracts (see
    product spec). source_user_text/source_assistant_text are accepted for
    interface symmetry with the generation call site; no check below reads
    them. This function proves surface-level structure only -- it never
    proves, and must never be reported as proving, that the reply is
    semantically grounded in the source exchange. Returns
    (is_valid, reason_if_invalid)."""
    if not candidate or not candidate.strip():
        return False, "empty response"
    if len(candidate) > 450:
        return False, "response too long (>450 characters)"
    if candidate.count("?") != 1:
        return False, "continuation response must contain exactly one question"
    if _LIST_MARKER_RE.search(candidate):
        return False, "response contains a bulleted or numbered list"
    low = candidate.lower()
    for phrase in FORBIDDEN_DIRECT_ADVICE_CONTINUATION:
        if phrase in low:
            return False, f"direct advice phrase: '{phrase}'"
    for phrase in DIAGNOSTIC_CERTAINTY_PHRASES:
        if phrase in low:
            return False, f"diagnostic-certainty phrase: '{phrase}'"
    for phrase in GENERIC_REASSURANCE_PHRASES_CONTINUATION:
        if phrase in low:
            return False, f"generic reassurance phrase: '{phrase}'"
    before_question = candidate.split("?", 1)[0].strip()
    if len(before_question) < 15:
        return False, "no concrete reflection before the question"

    if action == "clarify":
        markers = CAUTIOUS_MARKERS_EN if lang == "en" else CAUTIOUS_MARKERS_RU
        if not any(m in low for m in markers):
            return False, "clarify response missing a cautious marker"
        for phrase in FORBIDDEN_UNQUALIFIED_ASSERTION_CLARIFY:
            if phrase in low:
                return False, f"unqualified assertion: '{phrase}'"
        state_re = _CLARIFY_SECOND_PERSON_STATE_RE_EN if lang == "en" \
            else _CLARIFY_SECOND_PERSON_STATE_RE_RU
        motive_re = _CLARIFY_THIRD_PERSON_MOTIVE_RE_EN if lang == "en" \
            else _CLARIFY_THIRD_PERSON_MOTIVE_RE_RU
        for sentence in re.split(r"[.!?\n]", candidate):
            if any(m in sentence.lower() for m in markers):
                continue   # this specific sentence already carries its own hedge
            if state_re.search(sentence) or motive_re.search(sentence):
                return False, ("unqualified internal-state or third-party-motive "
                              "assertion outside any cautious marker")
        return True, None

    # elaborate (and any other continuation action reusing this contract)
    for phrase in FORBIDDEN_REPEAT_STORY_REQUEST:
        if phrase in low:
            return False, f"asks to repeat the whole story: '{phrase}'"
    return True, None
