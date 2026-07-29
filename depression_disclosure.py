"""Depression Disclosure Gate — deterministic detection (master prompt §13).

LLM never decides here; this module is pure pattern matching, same
architecture as risk_detector.py. Russian is the enforced, fully-tested
contract (§5 of the Phase 2 corrections). A small EN mirror is included
because detect_risk()/pipeline() already loop over {lang,"ru","en"}
regardless of detected language (CLAUDE.md convention) -- adding EN here
follows that SAME existing bilingual architecture rather than inventing a
parallel one, and is deliberately smaller than the RU list.

classify_disclosure() returns exactly one of:
  POSITIVE               -- eligible first-person disclosure, trigger the gate
  NEGATED                -- "у меня нет депрессии" -- explicitly excluded
  THIRD_PERSON            -- "у друга депрессия" -- explicitly excluded
  META_QUESTION           -- "что такое депрессия?" -- explicitly excluded
  QUOTED_OR_HYPOTHETICAL  -- quoted/example/hypothetical mention -- excluded
  NONE                    -- no depression mention at all

Exclusions are checked BEFORE the positive check and any exclusion match
short-circuits to that exclusion, never POSITIVE -- a message can only be
POSITIVE if none of the exclusion patterns matched anywhere in it.
"""
import re

_STEM_RU = "депресс"
_STEM_EN = "depress"

_NEGATION_RU = [
    re.compile(r"\bнет\s+(?:никакой\s+)?депресс"),
    re.compile(r"\bне\s+депресс"),
    re.compile(r"\bнету\s+депресс"),
    re.compile(r"\bбез\s+депресс"),
    re.compile(r"депресс\w*\s+(?:у меня\s+)?нет\b"),
    # Epistemic hedges ("I don't think I have depression") -- the negation
    # word is not adjacent to the stem, it negates the whole belief.
    re.compile(r"не\s+(?:думаю|уверен\w*|считаю|знаю)\W*что\b.{0,20}у меня.{0,20}депресс"),
]
_NEGATION_EN = [
    re.compile(r"\bno\s+depress"),
    re.compile(r"\bnot\s+depress"),
    re.compile(r"\bdon'?t\s+(?:have|think i have)\s+depress"),
]

_THIRD_PERSON_RU = [
    re.compile(r"у\s+(?:друга|подруги|брата|сестры|мамы|папы|мужа|жены|коллеги|"
              r"знакомого|знакомой|него|неё|нее)\b.{0,20}депресс"),
    re.compile(r"(?:мой|моя)\s+(?:друг|подруга|брат|сестра|муж|жена|коллега)\b"
              r".{0,20}депресс"),
    re.compile(r"у\s+моего\s+\w+.{0,20}депресс"),
    re.compile(r"у\s+моей\s+\w+.{0,20}депресс"),
]
_THIRD_PERSON_EN = [
    re.compile(r"\b(?:he|she|they)\s+(?:has|have|is|are)\b.{0,20}depress"),
    re.compile(r"\bmy\s+(?:friend|brother|sister|husband|wife|colleague)\b"
              r".{0,20}depress"),
]

_META_QUESTION_RU = [
    re.compile(r"что\s+такое\s+депресс"),
    re.compile(r"как\s+(?:может\s+)?(?:проявляется|выглядит|протекать|протекает)\s+депресс"),
    re.compile(r"что\s+значит\s+депресс"),
]
_META_QUESTION_EN = [
    re.compile(r"what\s+is\s+depress"),
    re.compile(r"what\s+does\s+depress\w*\s+mean"),
]

_QUOTED_OR_HYPOTHETICAL_RU = [
    re.compile(r'[«"\'].{0,80}депресс.{0,80}[»"\']'),
    re.compile(r"(?:если\s+бы|что\s+если|как\s+будто|предположим|допустим|"
              r"например,?\s*(?:у меня|я))\b.{0,40}депресс"),
    # "если у меня будет депрессия" -- hypothetical without "если бы".
    re.compile(r"если\b.{0,20}у меня.{0,25}депресс"),
]
_QUOTED_OR_HYPOTHETICAL_EN = [
    re.compile(r'["\'].{0,80}depress.{0,80}["\']'),
    re.compile(r"(?:what\s+if|as\s+if|suppose|for\s+example)\b.{0,40}depress"),
]

_POSITIVE_RU = [
    re.compile(r"(?:кажется,?\s*|похоже,?\s*)?у меня\s+(?:клиническая\s+)?депресс"),
    re.compile(r"мне\s+(?:поставили|диагностировали)\s+депресс"),
    re.compile(r"я\s+леч(?:усь|у)\s+от\s+депресс"),
    re.compile(r"(?:похоже,?\s*|кажется,?\s*)?я\s+в\s+депресс"),
    re.compile(r"(?:врач|психиатр|психотерапевт)\s+поставил\s+мне\s+депресс"),
]
_POSITIVE_EN = [
    re.compile(r"\bi\s+have\s+depress"),
    re.compile(r"\bi\s+was\s+diagnosed\s+with\s+depress"),
    re.compile(r"\bi\s+(?:am|'m)\s+depress"),
    re.compile(r"\bi\s+think\s+i\s+have\s+depress"),
]


def _normalize(text: str) -> str:
    t = text.lower()
    t = re.sub(r"[–—-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def classify_disclosure(text: str, lang: str = "ru") -> str:
    t = _normalize(text)
    stems = (_STEM_RU, _STEM_EN)
    if not any(s in t for s in stems):
        return "NONE"

    for group in (_NEGATION_RU, _NEGATION_EN):
        if any(p.search(t) for p in group):
            return "NEGATED"
    for group in (_THIRD_PERSON_RU, _THIRD_PERSON_EN):
        if any(p.search(t) for p in group):
            return "THIRD_PERSON"
    for group in (_META_QUESTION_RU, _META_QUESTION_EN):
        if any(p.search(t) for p in group):
            return "META_QUESTION"
    for group in (_QUOTED_OR_HYPOTHETICAL_RU, _QUOTED_OR_HYPOTHETICAL_EN):
        if any(p.search(t) for p in group):
            return "QUOTED_OR_HYPOTHETICAL"
    for group in (_POSITIVE_RU, _POSITIVE_EN):
        if any(p.search(t) for p in group):
            return "POSITIVE"
    return "NONE"


# ── Deterministic, user-visible copy (never LLM-generated) ─────────────────

SAFETY_CHECK_TEXT_RU = (
    "Я отнесусь к этому серьёзно. По одному сообщению я не могу подтвердить "
    "диагноз. Сначала важный вопрос: есть ли сейчас мысли, что не хочется жить "
    "или причинить себе вред?")
SAFETY_CHECK_TEXT_EN = (
    "I'll take this seriously. I can't confirm a diagnosis from one message. "
    "First, an important question: right now, do you have any thoughts of not "
    "wanting to live or of harming yourself?")

DIAGNOSIS_SOURCE_TEXT_RU = "Эта депрессия — это то, что тебе поставил специалист, или ты сам(а) так это описываешь?"
DIAGNOSIS_SOURCE_TEXT_EN = "Was this diagnosed by a specialist, or is that how you'd describe it yourself?"

DURATION_TEXT_RU = "Как давно ты это замечаешь?"
DURATION_TEXT_EN = "How long have you been noticing this?"

FUNCTIONING_TEXT_RU = "Насколько сейчас получается справляться с обычными делами?"
FUNCTIONING_TEXT_EN = "How well are you managing everyday things right now?"

BASIC_ACTIVITIES_TEXT_RU = "Как сейчас получается с базовыми вещами — вставать, есть, следить за собой?"
BASIC_ACTIVITIES_TEXT_EN = "How are basic things going right now — getting up, eating, taking care of yourself?"

SUPPORT_TEXT_RU = "Есть ли сейчас рядом кто-то, к кому можно обратиться?"
SUPPORT_TEXT_EN = "Is there anyone around you right now that you could reach out to?"

PURPOSE_TEXT_RU = "Что для тебя сейчас важнее всего в этом разговоре?"
PURPOSE_TEXT_EN = "What matters most to you in this conversation right now?"

CLOSING_TEXT_RU = "Спасибо, что рассказал(а) — я буду учитывать это дальше в разговоре."
CLOSING_TEXT_EN = "Thank you for sharing this — I'll keep it in mind as we continue."


def safety_check_text(lang: str) -> str:
    return SAFETY_CHECK_TEXT_EN if lang == "en" else SAFETY_CHECK_TEXT_RU


def diagnosis_source_text(lang: str) -> str:
    return DIAGNOSIS_SOURCE_TEXT_EN if lang == "en" else DIAGNOSIS_SOURCE_TEXT_RU


def duration_text(lang: str) -> str:
    return DURATION_TEXT_EN if lang == "en" else DURATION_TEXT_RU


def functioning_text(lang: str) -> str:
    return FUNCTIONING_TEXT_EN if lang == "en" else FUNCTIONING_TEXT_RU


def basic_activities_text(lang: str) -> str:
    return BASIC_ACTIVITIES_TEXT_EN if lang == "en" else BASIC_ACTIVITIES_TEXT_RU


def support_text(lang: str) -> str:
    return SUPPORT_TEXT_EN if lang == "en" else SUPPORT_TEXT_RU


def purpose_text(lang: str) -> str:
    return PURPOSE_TEXT_EN if lang == "en" else PURPOSE_TEXT_RU


def closing_text(lang: str) -> str:
    return CLOSING_TEXT_EN if lang == "en" else CLOSING_TEXT_RU


# Each assessment step's button options: (callback_value, ru_label, en_label).
# Deterministic multiple-choice -- no free-text parsing, no diagnostic framing.
DURATION_OPTIONS = [
    ("lt_week", "Меньше недели", "Less than a week"),
    ("weeks", "Несколько недель", "A few weeks"),
    ("months", "Несколько месяцев", "A few months"),
    ("half_year_plus", "Больше полугода", "More than half a year"),
]
FUNCTIONING_OPTIONS = [
    ("as_usual", "Справляюсь как обычно", "Managing as usual"),
    ("harder", "Тяжелее, чем обычно, но справляюсь", "Harder than usual, but managing"),
    ("basic_hard", "Сложно справляться с базовыми делами", "Hard to manage basic things"),
    ("barely", "Почти не могу делать обычные дела", "Barely able to do everyday things"),
]
BASIC_ACTIVITIES_OPTIONS = [
    ("managing", "Встаю, ем, слежу за собой — в целом справляюсь",
     "Getting up, eating, taking care of myself — managing overall"),
    ("some_days_hard", "В какие-то дни сложно вставать или следить за собой",
     "Some days it's hard to get up or take care of myself"),
    ("often_hard", "Часто сложно даже с едой, гигиеной, обычными делами",
     "Often hard even with eating, hygiene, everyday tasks"),
    ("rarely_manage", "Почти не получается вставать, есть, следить за собой",
     "Barely managing to get up, eat, take care of myself"),
]
SUPPORT_OPTIONS = [
    ("close_ones", "Есть близкие, к кому можно обратиться", "I have close people I can turn to"),
    ("dont_want_to_burden", "Есть, но не хочется никого нагружать", "I have people, but don't want to burden them"),
    ("specialist", "Специалист (врач/психолог)", "A specialist (doctor/therapist)"),
    ("no_one", "Сейчас никого нет", "No one right now"),
]
PURPOSE_OPTIONS = [
    ("vent", "Хочу выговориться", "I want to talk it out"),
    ("understand", "Хочу разобраться, что происходит", "I want to understand what's happening"),
    ("next_steps", "Хочу понять, что делать дальше", "I want to know what to do next"),
    ("not_sure", "Пока не знаю", "Not sure yet"),
]
DIAGNOSIS_SOURCE_OPTIONS = [
    ("specialist", "Поставил специалист", "A specialist diagnosed it"),
    ("self", "Я так чувствую", "That's how I feel"),
    ("unknown", "Не знаю", "I don't know"),
]
SAFETY_CHECK_OPTIONS = [
    ("yes", "Да", "Yes"),
    ("no", "Нет", "No"),
    ("unsure", "Не уверен", "Not sure"),
]


# ── Strict per-step closed allowlists (Phase 2 correction §1) ──────────────
# A callback value not in the allowlist for its step is REJECTED -- never
# coerced into any other value (in particular, never treated as "no").
STEP_ALLOWED_VALUES: dict[str, set] = {
    "SAFETY_CHECK": {v for v, _, _ in SAFETY_CHECK_OPTIONS},
    "DIAGNOSIS_SOURCE": {v for v, _, _ in DIAGNOSIS_SOURCE_OPTIONS},
    "DURATION": {v for v, _, _ in DURATION_OPTIONS},
    "FUNCTIONING": {v for v, _, _ in FUNCTIONING_OPTIONS},
    "BASIC_ACTIVITIES": {v for v, _, _ in BASIC_ACTIVITIES_OPTIONS},
    "SUPPORT": {v for v, _, _ in SUPPORT_OPTIONS},
    "PURPOSE": {v for v, _, _ in PURPOSE_OPTIONS},
}

# Approved deterministic sequence (Phase 2 correction §6).
STEP_SEQUENCE = ["SAFETY_CHECK", "DIAGNOSIS_SOURCE", "DURATION", "FUNCTIONING",
                 "BASIC_ACTIVITIES", "SUPPORT", "PURPOSE", "HANDOFF_READY"]

# callback_data step-tag (short, used in "dd:<tag>:<flow_id>:<value>") <-> DB step.
STEP_TAG_TO_DB_STEP = {
    "safety": "SAFETY_CHECK", "src": "DIAGNOSIS_SOURCE", "dur": "DURATION",
    "func": "FUNCTIONING", "basic": "BASIC_ACTIVITIES", "supp": "SUPPORT",
    "purp": "PURPOSE",
}

# The key each step's answer is recorded under in the flow's bounded answers_json.
STEP_ANSWER_KEY = {
    "DURATION": "duration", "FUNCTIONING": "functioning",
    "BASIC_ACTIVITIES": "basic_activities", "SUPPORT": "support", "PURPOSE": "purpose",
}

ANSWERS_JSON_MAX_LEN = 2000
