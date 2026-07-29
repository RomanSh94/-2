"""Conversation Controller — Phase 3 (master prompt §10/§15, roadmap Phase 3).

Deterministic intent recognition + ResponsePlan construction + system-prompt
building + a Controller Fidelity Validator -- pure, no I/O. bot.py issues the
one bounded LLM call for a controller-handled turn (reusing the existing
OpenAI client) using the prompt this module builds, then validates the
result with this module's validator. Session persistence goes through
database.py's existing Phase 1 core_sessions accessors -- this module holds
no state.

Scope boundary (deliberate, not an oversight): the controller activates ONLY
for messages with a deterministically-recognized EXPLICIT intent signal
(VENT/EXPLAIN/ACTION/CHANGE_PATTERN/DECISION_SUPPORT/PRACTICE/REPAIR). A
message with no explicit signal classifies UNKNOWN and is left untouched --
it continues through the existing scenario-based ordinary pipeline,
unchanged. This is "one primary LLM completion per normal controller turn"
and "do not create a second unrestricted LLM classifier call for every
turn" taken together: there is no ambiguous-turn classifier call in Phase 3,
and therefore no second ordinary-message pipeline. Ambiguous-turn structured
classification is explicitly OUT of scope for this phase (§7 of the Phase 3
corrections: "do not prematurely implement all later product paths").
"""
import re

from therapeutic_domain import Intent, RepairConstraint, ResponsePlan

_STEM_STOP = re.compile(r"[–—-]")


def _normalize(text: str) -> str:
    t = text.lower()
    t = _STEM_STOP.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


# ── Explicit intent phrase patterns (RU) — deterministic, high-confidence ──
# Checked in a fixed priority order (REPAIR first: a repair signal always
# takes priority over any other classification for the same turn, per §16
# "repair is mandatory"). Quotation/third-person wrapping excludes a match
# the same way depression_disclosure.py excludes non-personal mentions.

_QUOTED_RE = re.compile(r'[«"\'].{0,80}[»"\']')
_THIRD_PERSON_RE = re.compile(
    r"\b(?:он|она|они|его|её|их|ему|ей|им)\b.{0,20}(?:сказал|сказала|говорит|"
    r"думает|чувствует|хочет)")

REPAIR_SIGNAL_PATTERNS: dict[RepairConstraint, list[re.Pattern]] = {
    RepairConstraint.QUESTION_OVERLOAD: [
        re.compile(r"ты\s+задаёшь\s+одни\s+вопросы"),
        re.compile(r"хватит\s+спрашивать"),
        re.compile(r"опять\s+вопрос"),
        re.compile(r"опять\s+(?:\S+\s+){0,2}вопрос"),
    ],
    RepairConstraint.ADVICE_REJECTED: [
        re.compile(r"не\s+давай\s+совет"),
        re.compile(r"не\s+нужен\s+совет"),
    ],
    RepairConstraint.MISSED_EXPLANATION: [
        re.compile(r"я\s+просил(?:а)?\s+объяснить"),
        re.compile(r"ты\s+не\s+объяснил"),
        re.compile(r"ничего\s+не\s+объясня"),
    ],
    RepairConstraint.EXERCISE_REJECTED: [
        re.compile(r"опять\s+предлагаешь\s+дыхани"),
        re.compile(r"не\s+хочу\s+упражнени"),
        re.compile(r"хватит\s+упражнени"),
    ],
    RepairConstraint.BOT_REPEATS: [
        re.compile(r"ты\s+повторяешься"),
        re.compile(r"я\s+уже\s+это\s+говорил"),
        re.compile(r"ты\s+меня\s+не\s+понимаешь"),
    ],
    RepairConstraint.TOPIC_REJECTED: [
        re.compile(r"не\s+хочу\s+об\s+этом"),
        re.compile(r"давай\s+не\s+про\s+это"),
    ],
}
# "это не помогает" is a generic repair trigger with no specific sub-signal --
# handled separately below, defaulting to BOT_REPEATS (the strategy itself
# needs to change, the most general repair category).
_GENERIC_REPAIR_RE = re.compile(r"это\s+не\s+помогает")

_VENT_RE = [
    re.compile(r"мне\s+нужно\s+выговориться"),
    re.compile(r"просто\s+послушай"),
    re.compile(r"без\s+советов"),
    re.compile(r"хочу\s+рассказать"),
    re.compile(r"хочу\s+выговориться"),
]
_EXPLAIN_RE = [
    re.compile(r"объясни,?\s*почему"),
    re.compile(r"почему\s+я\s+так\s+себя\s+чувствую"),
    re.compile(r"помоги\s+понять"),
    re.compile(r"объясни\s+механизм"),
]
_ACTION_RE = [
    re.compile(r"скажи,?\s*что\s+мне\s+сделать"),
    re.compile(r"выбери\s+сам"),
    re.compile(r"нужен\s+конкретный\s+шаг"),
    re.compile(r"скажи\s+сам"),
]
_CHANGE_PATTERN_RE = [
    re.compile(r"это\s+постоянно\s+повторяется"),
    re.compile(r"хочу\s+изменить\s+этот\s+сценарий"),
    re.compile(r"опять\s+делаю\s+то\s+же\s+самое"),
    re.compile(r"снова\s+и\s+снова"),
]
_DECISION_SUPPORT_RE = [
    re.compile(r"не\s+могу\s+принять\s+решение"),
    re.compile(r"помоги\s+разобраться\s+с\s+выбором"),
    re.compile(r"какой\s+вариант\s+выбрать"),
    re.compile(r"не\s+могу\s+выбрать"),
]
_PRACTICE_RE = [
    re.compile(r"дай\s+упражнение"),
    re.compile(r"хочу\s+попробовать\s+технику"),
    re.compile(r"проведи\s+практику"),
    re.compile(r"дай\s+практику"),
]

_INTENT_PATTERNS: list[tuple[Intent, list[re.Pattern]]] = [
    (Intent.VENT, _VENT_RE),
    (Intent.EXPLAIN, _EXPLAIN_RE),
    (Intent.ACTION, _ACTION_RE),
    (Intent.CHANGE_PATTERN, _CHANGE_PATTERN_RE),
    (Intent.DECISION_SUPPORT, _DECISION_SUPPORT_RE),
    (Intent.PRACTICE, _PRACTICE_RE),
]


def _is_excluded(t: str) -> bool:
    """Quotation or reported third-person speech excludes an explicit-intent
    match the same way depression_disclosure.py excludes non-personal
    mentions -- a quoted example is not the current user's own request."""
    return bool(_QUOTED_RE.search(t) or _THIRD_PERSON_RE.search(t))


def classify_repair_signals(text: str) -> set[RepairConstraint]:
    """Returns every matched repair constraint (a message can trigger more
    than one, e.g. 'хватит спрашивать, ты повторяешься'). Empty set if no
    repair signal is present -- classify_intent() decides REPAIR-vs-not
    separately, this function only maps a REPAIR turn to specific
    constraints for the ResponsePlan."""
    t = _normalize(text)
    if _is_excluded(t):
        return set()
    found = {c for c, patterns in REPAIR_SIGNAL_PATTERNS.items()
            if any(p.search(t) for p in patterns)}
    if not found and _GENERIC_REPAIR_RE.search(t):
        found = {RepairConstraint.BOT_REPEATS}
    return found


def classify_intent(text: str, lang: str = "ru") -> Intent:
    """Deterministic, explicit-phrase-only classification. Returns UNKNOWN
    for anything not matching a required phrase pattern -- there is no
    fuzzy/LLM-based fallback classifier in Phase 3 (see module docstring)."""
    t = _normalize(text)
    if _is_excluded(t):
        return Intent.UNKNOWN
    if classify_repair_signals(text):
        return Intent.REPAIR
    for intent, patterns in _INTENT_PATTERNS:
        if any(p.search(t) for p in patterns):
            return intent
    return Intent.UNKNOWN


# ── ResponsePlan construction (§8) ──────────────────────────────────────────

_INTENT_PLAN_DEFAULTS: dict[Intent, dict] = {
    Intent.VENT: dict(listening_only=True, advice_allowed=False,
                      intervention_allowed=False, max_questions=1),
    Intent.EXPLAIN: dict(explanation_required=True, direct_answer_required=True,
                        max_questions=1),
    Intent.ACTION: dict(question_allowed=False, max_questions=0, max_actions=1),
    Intent.CHANGE_PATTERN: dict(intervention_allowed=False, max_questions=1),
    Intent.DECISION_SUPPORT: dict(intervention_allowed=False, max_questions=1),
    Intent.PRACTICE: dict(consent_required=True, max_questions=1),
    Intent.REPAIR: dict(max_questions=1),
}


def build_response_plan(intent: Intent,
                        repair_constraints: set[RepairConstraint] | None = None) -> ResponsePlan:
    kwargs = dict(_INTENT_PLAN_DEFAULTS.get(intent, {}))
    kwargs["intent"] = intent
    kwargs["repair_constraints"] = repair_constraints or set()
    return ResponsePlan(**kwargs)


# ── Controller Fidelity Validator (§19) ─────────────────────────────────────

_ADVICE_CUES_RU = ("попробуй", "стоит попробовать", "рекомендую", "тебе нужно",
                   "советую", "лучше всего сделать")
_EXERCISE_CUES_RU = ("упражнение", "дыхательн", "техника заземления", "практик")
_CHOICE_DELEGATION_CUES_RU = ("что ты думаешь", "как тебе кажется", "выбери сам",
                             "решай сам", "тебе решать")


def validate_controller_response(text: str, plan: ResponsePlan) -> tuple[bool, str | None]:
    """Deterministic, Phase-3-specific behavioral checks -- runs IN ADDITION
    to (never instead of) the existing safety_validator.validate_response(),
    which callers must run separately (it already covers diagnosis language/
    forbidden phrases/certainty claims universally, for every LLM response,
    not just controller ones -- duplicating that here would be exactly the
    kind of weakening the corrections explicitly forbid).

    Returns (is_valid, violation_reason). On any violation the caller must
    use a deterministic safe fallback, never deliver the flagged text."""
    low = text.lower()
    question_count = text.count("?")

    if plan.listening_only and not plan.advice_allowed:
        if any(cue in low for cue in _ADVICE_CUES_RU):
            return False, "advice_during_vent"
    # Check the more specific repair-driven reason before the generic
    # intervention_allowed check -- ResponsePlan.__post_init__ already turns
    # EXERCISE_REJECTED into intervention_allowed=False, so this order is
    # what makes the reported reason actionable (which repair signal, not
    # just "some exercise-like text appeared").
    if RepairConstraint.EXERCISE_REJECTED in plan.repair_constraints \
            and any(cue in low for cue in _EXERCISE_CUES_RU):
        return False, "repeated_rejected_exercise"
    if not plan.intervention_allowed and any(cue in low for cue in _EXERCISE_CUES_RU):
        return False, "exercise_not_allowed"
    # ACTION-specific checks before the generic question_allowed check: an
    # ACTION response containing a question is ALWAYS wrong (question_allowed
    # is False by construction for ACTION -- SS9), but "choice delegation"
    # is the more actionable, intent-specific reason when that's what the
    # question actually is.
    if plan.intent is Intent.ACTION and any(cue in low for cue in _CHOICE_DELEGATION_CUES_RU):
        return False, "choice_delegation_during_action"
    if not plan.question_allowed and question_count > 0:
        return False, "question_not_allowed"
    if question_count > plan.max_questions:
        return False, "too_many_questions"
    if plan.intent is Intent.EXPLAIN and plan.explanation_required:
        word_count = len(text.split())
        if word_count < 15:
            return False, "explanation_too_short"
    return True, None


# ── Fallback copy (deterministic, never LLM-generated) ──────────────────────
# Phase 3 correction §10: one generic fallback for every violation would
# itself violate the intent's own contract (e.g. a generic "tell me more"
# fallback for a failed EXPLAIN turn still fails to explain anything). Each
# intent gets a fallback that at minimum does not repeat the violation.

_FALLBACK_RU: dict[Intent, str] = {
    Intent.VENT: ("Слышу тебя. Расскажи ещё, что происходит — я здесь, "
                 "без советов и оценок."),
    Intent.EXPLAIN: ("Это может быть связано с несколькими вещами сразу, и "
                     "я не хочу гадать наугад. Расскажи чуть подробнее, что "
                     "именно ты замечаешь — так я смогу объяснить точнее."),
    Intent.ACTION: "Начни с одного: сделай сегодня один маленький конкретный шаг в сторону того, что важно.",
    Intent.CHANGE_PATTERN: ("Похоже на повторяющуюся ситуацию, хотя по одному разу "
                            "сложно сказать наверняка. Расскажи о последнем случае подробнее."),
    Intent.DECISION_SUPPORT: "Давай начнём с одного: что именно нужно решить прямо сейчас?",
    Intent.PRACTICE: "Есть простая практика, которая может помочь. Хочешь, опишу её и её длительность?",
    Intent.REPAIR: "Ты прав — я не учёл это. Расскажи ещё раз, что для тебя сейчас важно, и я буду опираться на это.",
}
_FALLBACK_EN: dict[Intent, str] = {
    Intent.VENT: "I hear you. Tell me more about what's going on — I'm here, no advice, no judgment.",
    Intent.EXPLAIN: ("This could be tied to a few things at once, and I don't "
                     "want to guess. Tell me a bit more about what you're "
                     "noticing so I can explain it more precisely."),
    Intent.ACTION: "Start with one thing: take one small concrete step today toward what matters.",
    Intent.CHANGE_PATTERN: ("This sounds like it could be a recurring pattern, though "
                            "one instance alone isn't enough to be sure. Tell me more about the last time."),
    Intent.DECISION_SUPPORT: "Let's start with one thing: what exactly needs to be decided right now?",
    Intent.PRACTICE: "There's a simple practice that might help. Want me to describe it and how long it takes?",
    Intent.REPAIR: "You're right — I missed that. Tell me again what matters to you right now, and I'll build on it.",
}
_GENERIC_FALLBACK_RU = (
    "Сейчас мне не удалось сформулировать ответ так, как нужно. Расскажи, "
    "пожалуйста, ещё раз своими словами — я внимательно слушаю.")
_GENERIC_FALLBACK_EN = (
    "I wasn't able to phrase this the way I should have just now. Could you "
    "tell me again in your own words — I'm listening.")
# Back-compat aliases (module-level constants some callers/tests reference directly).
FALLBACK_TEXT_RU = _GENERIC_FALLBACK_RU
FALLBACK_TEXT_EN = _GENERIC_FALLBACK_EN


def fallback_text(lang: str, intent: Intent | None = None) -> str:
    table = _FALLBACK_EN if lang == "en" else _FALLBACK_RU
    if intent is not None and intent in table:
        return table[intent]
    return _GENERIC_FALLBACK_EN if lang == "en" else _GENERIC_FALLBACK_RU


# ── Phase 2 handoff -> Intent mapping (§6) ──────────────────────────────────
# Maps the completed Depression Disclosure Gate's PURPOSE answer to the
# controller intent that best matches it -- "not_sure" has no confident
# mapping and stays UNKNOWN (falls through to the ordinary pipeline rather
# than guessing).
HANDOFF_PURPOSE_TO_INTENT: dict[str, Intent] = {
    "vent": Intent.VENT,
    "understand": Intent.EXPLAIN,
    "next_steps": Intent.ACTION,
    "not_sure": Intent.UNKNOWN,
}


# ── System-prompt construction (§8/§18) ─────────────────────────────────────
# Bounded, deterministic, per-intent -- never includes unrestricted historical
# data, only the current ResponsePlan and a short list of already-known facts
# (e.g. from a claimed Phase 2 handoff) the LLM must not re-ask for.

_BASE_INSTRUCTIONS_RU = (
    "Ты — часть системы поддержки. Отвечай по-русски, тепло и конкретно, "
    "не более 150 слов. Не ставь диагноз. Не утверждай точную причину как "
    "факт — формулируй как предположение, если это не подтверждённый факт.")
_BASE_INSTRUCTIONS_EN = (
    "You are part of a support system. Reply in English, warmly and "
    "concretely, at most 150 words. Never state a diagnosis. Never assert an "
    "exact cause as fact — phrase it as a tentative possibility unless it is "
    "a confirmed fact.")

_INTENT_INSTRUCTIONS_RU: dict[Intent, str] = {
    Intent.VENT: ("Пользователь хочет выговориться. Не давай советов. Не "
                 "предлагай упражнение. Отрази то, что он сказал, своими "
                 "словами, точно и без искажений. Вопрос — не более одного, "
                 "и только если он действительно нужен."),
    Intent.EXPLAIN: ("Пользователь просит объяснение. Сначала дай реальное "
                     "содержательное объяснение возможного механизма "
                     "(используй осторожные формулировки вроде «возможно», "
                     "«часто это связано с»). Не заменяй объяснение вопросом. "
                     "После объяснения — не более одного уточняющего вопроса."),
    Intent.ACTION: ("Пользователь просит бота самого выбрать один маленький "
                    "конкретный шаг. Выбери ровно один шаг, объясни коротко, "
                    "почему именно он. Не предлагай список вариантов. Не "
                    "спрашивай, что пользователь сам думает — реши сам."),
    Intent.CHANGE_PATTERN: ("Пользователь описывает повторяющуюся ситуацию. "
                            "Одно событие не доказывает устойчивый паттерн — "
                            "не утверждай его как факт. Обозначь это как "
                            "предположение и задай не более одного уточняющего "
                            "вопроса, чтобы проверить его вместе с пользователем."),
    Intent.DECISION_SUPPORT: ("Пользователь не может принять решение. Помоги "
                              "прояснить сам выбор и отдели факты от "
                              "опасений/прогнозов. Не принимай решение за "
                              "пользователя. Не более одного вопроса."),
    Intent.PRACTICE: ("Пользователь просит практику/технику. Опиши ОДНУ "
                      "простую низкорисковую практику, её цель и примерную "
                      "длительность, и явно спроси согласие, прежде чем "
                      "начинать её проводить."),
    Intent.REPAIR: ("Пользователь указал на ошибку в разговоре. Сначала "
                    "назови конкретную ошибку. Затем кратко перечисли уже "
                    "известные факты. Затем скажи, что изменится. "
                    "Немедленно примени новый подход в этом же ответе."),
}
_INTENT_INSTRUCTIONS_EN: dict[Intent, str] = {
    Intent.VENT: ("The user wants to vent. Do not give advice. Do not offer "
                 "an exercise. Reflect what they said accurately, in your "
                 "own words. At most one question, only if truly needed."),
    Intent.EXPLAIN: ("The user is asking for an explanation. Give a real, "
                     "substantive explanation of a possible mechanism first "
                     "(use tentative language like 'this may be related to'). "
                     "Do not replace the explanation with a question. At most "
                     "one follow-up question after the explanation."),
    Intent.ACTION: ("The user wants the bot to choose one small concrete "
                    "step. Choose exactly one, briefly explain why. Do not "
                    "offer a list. Do not ask the user what they think."),
    Intent.CHANGE_PATTERN: ("The user describes a recurring situation. One "
                            "event does not prove a stable pattern -- frame "
                            "it as a tentative hypothesis and ask at most one "
                            "clarifying question to check it together."),
    Intent.DECISION_SUPPORT: ("The user can't decide. Help clarify the actual "
                              "choice and separate facts from feared "
                              "predictions. Do not decide for them. At most "
                              "one question."),
    Intent.PRACTICE: ("The user is asking for a practice/technique. Describe "
                      "ONE simple low-risk practice, its purpose and rough "
                      "duration, and explicitly ask for consent before "
                      "starting it."),
    Intent.REPAIR: ("The user flagged a mistake. Name the specific mistake "
                    "first. Briefly restate already-known facts. State what "
                    "changes. Apply the new approach immediately in this "
                    "same reply."),
}

_REPAIR_CONSTRAINT_NOTE_RU = {
    RepairConstraint.QUESTION_OVERLOAD: "Не задавай вопрос в этом ответе вообще.",
    RepairConstraint.ADVICE_REJECTED: "Не давай советов, пока пользователь сам не попросит.",
    RepairConstraint.EXERCISE_REJECTED: "Не предлагай упражнение снова.",
    RepairConstraint.MISSED_EXPLANATION: "Сначала дай объяснение, потом (если нужно) вопрос.",
    RepairConstraint.BOT_REPEATS: "Не повторяй то, что уже было сказано — используй известные факты.",
    RepairConstraint.TOPIC_REJECTED: "Не возвращайся к отклонённой теме.",
}
_REPAIR_CONSTRAINT_NOTE_EN = {
    RepairConstraint.QUESTION_OVERLOAD: "Do not ask any question in this reply.",
    RepairConstraint.ADVICE_REJECTED: "Do not give advice unless explicitly asked again.",
    RepairConstraint.EXERCISE_REJECTED: "Do not offer an exercise again.",
    RepairConstraint.MISSED_EXPLANATION: "Give the explanation first, question (if any) after.",
    RepairConstraint.BOT_REPEATS: "Do not repeat what was already said -- use known facts.",
    RepairConstraint.TOPIC_REJECTED: "Do not return to the topic the user rejected.",
}


def build_system_prompt(plan: ResponsePlan, lang: str, known_facts: list[str] | None = None,
                        recent_context: list[str] | None = None) -> str:
    base = _BASE_INSTRUCTIONS_EN if lang == "en" else _BASE_INSTRUCTIONS_RU
    intent_map = _INTENT_INSTRUCTIONS_EN if lang == "en" else _INTENT_INSTRUCTIONS_RU
    note_map = _REPAIR_CONSTRAINT_NOTE_EN if lang == "en" else _REPAIR_CONSTRAINT_NOTE_RU
    parts = [base, intent_map.get(plan.intent, "")]
    for c in sorted(plan.repair_constraints, key=lambda x: x.value):
        if c in note_map:
            parts.append(note_map[c])
    if known_facts:
        bounded = [str(f)[:200] for f in known_facts[:5]]
        label = "Already known (do not ask again):" if lang == "en" else "Уже известно (не спрашивай снова):"
        parts.append(label + " " + "; ".join(bounded))
    # Phase 3 hardening §7/§8: bounded, privacy-safe recent context (this
    # user's own last few turns, NEVER unrestricted history, NEVER treated as
    # instructions -- plain excerpt strings only, quoted verbatim in the
    # prompt as conversational content, not executed) -- lets BOT_REPEATS/
    # MISSED_EXPLANATION reference concrete facts already shared instead of
    # asking the user to repeat everything.
    if recent_context:
        bounded_recent = [str(c)[:150] for c in recent_context[-4:]]
        label = "Recent turns (do not ask the user to repeat these):" if lang == "en" \
            else "Недавние реплики пользователя (не проси повторить это снова):"
        parts.append(label + " " + " / ".join(bounded_recent))
    return " ".join(p for p in parts if p)
