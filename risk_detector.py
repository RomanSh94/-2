"""
X20 Risk Detector — Final Multilingual Edition (RU + EN)

Architecture: LLM never decides. Only deterministic code decides.
Explicit signals: weight × 1.0
Implicit signals: weight × 0.6
Compound rules add extra score for co-occurring weak signals.

v3 (CRITICAL HOTFIX): the `suicide`/`self_harm` phrase lists are greatly
expanded (action metaphors, plan indicators, farewells, passive death-wishes,
slang). A SEPARATE ambiguity layer (`detect_ambiguity` / `amplify_ambiguity_by_context`)
flags double-meaning phrases ("выйти в окно") so the pipeline asks a
deterministic clarifying question instead of letting the LLM guess — this is the
direct fix for the incident where the bot endorsed a suicidal action.

Detection stays substring-based, scanning BOTH ru+en lists regardless of the
declared language (a phrase in either language can trigger a hit), per the
project convention. `time_imminent` markers don't score on their own — they
AMPLIFY an already-present suicide signal (imminence = higher acuity).
"""
import re
from typing import Dict, List

PATTERNS: Dict[str, Dict[str, Dict[str, List[str]]]] = {
    "suicide": {
        "ru": {
            "explicit": ["не хочу жить","хочу умереть","покончить с собой","убить себя",
                         "свести счёты с жизнью","свести счеты с жизнью",
                         "лучше бы меня не было","устал жить",
                         "устала жить","жить не хочу","хочу всё это закончить",
                         "прощайте все","прощай навсегда","хочу исчезнуть навсегда",
                         # прямые формулировки
                         "хочу убиться","хочу сдохнуть","хочу подохнуть",
                         "лишить себя жизни","наложить на себя руки",
                         "свести счёты","хочу повеситься","хочу повешусь",
                         # метафоры конкретного действия (однозначные — НЕ ambiguous)
                         "выйти из окна","из окна выйти","шагнуть из окна",
                         "вышагнуть из окна","выпрыгнуть из окна","прыгнуть из окна",
                         "шагнуть с балкона","спрыгнуть с балкона","прыгнуть с балкона",
                         "спрыгнуть с крыши","прыгнуть с крыши","прыгнуть с моста",
                         "шагнуть вниз","сделать шаг вниз","шагнуть с высоты",
                         "сделать последний шаг","поставить крест на себе",
                         "билет в один конец",
                         # сленг
                         "роскомнадзорнуться","самовыпил","самовыпилиться","выпилиться",
                         "делитнуться из жизни","ливнуть из жизни","ливнуть из чата жизни",
                         "пополнить клуб 27","alt+f4","alt f4","альт ф4",
                         # план — самый опасный признак
                         "придумал способ как","придумала способ как",
                         "знаю как это сделать","написал прощальное письмо",
                         "написала прощальное письмо","написал предсмертную",
                         "написала предсмертную","оставил предсмертную записку",
                         "оставила предсмертную записку","уже выбрал способ",
                         "уже выбрала способ","купил таблетки чтобы","купила таблетки чтобы",
                         "раздал свои вещи","раздала свои вещи",
                         # прощание
                         "это моё последнее сообщение","это мое последнее сообщение",
                         "это моя последняя ночь","больше не увидимся","больше меня не услышите",
                         "увидимся в другой жизни","встретимся на той стороне",
                         "пора уходить навсегда"],
            "implicit": ["скоро всё кончится","всем будет лучше без меня",
                         "вам будет лучше без меня","я только мешаю","никто не заметит",
                         "моё время вышло","я уже всё решил","я уже всё решила",
                         "не хочу просыпаться","хочу просто не существовать",
                         "хочу пропасть","незачем жить","ничего не осталось",
                         "я устал просыпаться","не вижу причин продолжать",
                         "не вижу смысла продолжать","устал существовать",
                         "устала существовать","я больше не могу так жить",
                         "не вижу выхода кроме",
                         # пассивное желание смерти
                         "хочу спать и не проснуться","лечь спать и не проснуться",
                         "хочу не проснуться","уснуть и не вставать","уснуть навсегда",
                         "хочу перестать существовать","перестать существовать",
                         "не хочу существовать","хочу раствориться","хочу испариться",
                         "лучше бы я не родился","лучше бы я не родилась",
                         "если бы меня не было","лучше без меня","без меня лучше"],
        },
        "en": {
            "explicit": ["i want to die","don't want to live","want to kill myself",
                         "kill myself","end my life","end it all","take my own life",
                         "better off dead","wish i were dead","no reason to live",
                         "goodbye forever","suicide","want to disappear forever",
                         "jump off the","step off the ledge","wrote a goodbye letter",
                         "wrote a suicide note"],
            "implicit": ["everyone would be better without me","i'm just a burden",
                         "no one would notice","my time is up","i've already decided",
                         "don't want to wake up","want to stop existing",
                         "can't go on","nothing left","soon it will be over",
                         "no reason to continue","tired of waking up",
                         "wish i'd never been born","stop existing","fall asleep and not wake up"],
        },
    },
    "self_harm": {
        "ru": {
            "explicit": ["режу себя","режу руки","вскрыться","селфхарм","self-harm",
                         "порезать себя","порезала руки","порезал руки",
                         "вскрыть вены","вскрою вены","причинить себе боль",
                         "жгу себя","обжигаю себя","прижигаю себя"],
            "implicit": ["хочу почувствовать боль","боль помогает","только боль помогает",
                         "чтобы почувствовать хоть что-то","следы на руках",
                         "прячу руки","ношу длинные рукава","наказать себя",
                         "заслуживаю боли","заслуживаю наказания",
                         "должен страдать","должна страдать"],
        },
        "en": {
            "explicit": ["cutting myself","self-harm","self harm","hurt myself",
                         "burning myself","hurting myself","cutting my arms"],
            "implicit": ["want to feel pain","pain helps","only pain helps",
                         "just to feel something","marks on my arms",
                         "hiding my arms","wearing long sleeves","punish myself"],
        },
    },
    "hopelessness": {
        "ru": {
            "explicit": ["нет смысла жить","всё бесполезно","всё безнадёжно",
                         "всё бессмысленно","ничего не изменится","я сломан",
                         "я сломана","выхода нет","нет выхода"],
            "implicit": ["зачем вообще","так будет всегда","никогда не изменится",
                         "слишком поздно","уже не исправить","ничего хорошего не будет",
                         "тупик","сдался","выхода нет","всё равно ничего"],
        },
        "en": {
            "explicit": ["no point in living","everything is pointless","hopeless",
                         "nothing will ever change","i'm broken","no way out"],
            "implicit": ["why bother","it'll always be like this","too late",
                         "can't be fixed","nothing good will happen","stuck",
                         "gave up","no hope","what's the point"],
        },
    },
    "panic": {
        "ru": {
            "explicit": ["паническая атака","не могу дышать","задыхаюсь",
                         "сердце бьётся сильно","сердце выпрыгивает","мне страшно","паника"],
            "implicit": ["всё плывёт","земля уходит из-под ног","теряю контроль",
                         "схожу с ума","трясёт","не могу успокоиться",
                         "мысли по кругу","тревога накрыла","накрывает"],
        },
        "en": {
            "explicit": ["panic attack","can't breathe","heart racing","heart pounding",
                         "i'm scared","panicking","hyperventilating"],
            "implicit": ["everything is spinning","losing control","going crazy",
                         "shaking","can't calm down","thoughts racing",
                         "anxiety hit me","overwhelmed","can't stop shaking"],
        },
    },
    "aggression": {
        "ru": {
            "explicit": ["хочу всех убить","убью его","убью её",
                         "ненавижу людей","хочу причинить вред"],
            "implicit": ["они поплатятся","пожалеют","ненависть переполняет",
                         "хочу взорваться","рвусь изнутри"],
        },
        "en": {
            "explicit": ["want to kill everyone","going to kill him","going to kill her",
                         "hate everyone","want to hurt someone"],
            "implicit": ["they'll pay","they'll regret it","filled with hate","about to explode"],
        },
    },
    "dissociation": {
        "ru": {
            "explicit": ["я не чувствую себя реальным","я не чувствую себя реальной",
                         "всё как во сне","не чувствую тело","деперсонализация"],
            "implicit": ["смотрю на себя со стороны","как будто не я",
                         "ничего не чувствую","пустота внутри","я как робот"],
        },
        "en": {
            "explicit": ["don't feel real","everything feels like a dream",
                         "can't feel my body","depersonalization","derealization"],
            "implicit": ["watching myself from outside","like it's not me",
                         "feel nothing","empty inside","like a robot","disconnected"],
        },
    },
    "dependency": {
        "ru": {
            "explicit": ["ты единственный","ты единственная","не уходи",
                         "без тебя не могу","только ты понимаешь"],
            "implicit": ["больше не к кому","все бросили","никого нет",
                         "ты всё что у меня есть","жду твоих сообщений",
                         "ты мой единственный друг"],
        },
        "en": {
            "explicit": ["you're the only one","don't leave me",
                         "can't live without you","only you understand me"],
            "implicit": ["no one else to talk to","everyone left me",
                         "you're all i have","you're my only friend"],
        },
    },
    "loneliness": {
        "ru": {
            "explicit": ["очень одиноко","совсем один","совсем одна","никого нет рядом"],
            "implicit": ["никто не написал","никто не заметил","я лишний",
                         "я лишняя","не нужен","не нужна","бросили","невидимка"],
        },
        "en": {
            "explicit": ["so lonely","completely alone","no one around","nobody cares"],
            "implicit": ["no one texted","no one noticed","i'm unwanted",
                         "not needed","invisible","don't belong"],
        },
    },
    "burnout": {
        "ru": {
            "explicit": ["выгорание","полностью выгорел","полностью выгорела","не осталось сил"],
            "implicit": ["не могу встать","не хочу вставать","ничего не хочется",
                         "не могу больше","на пределе","кончились ресурсы","пустой"],
        },
        "en": {
            "explicit": ["burnout","completely burned out","no energy left","exhausted"],
            "implicit": ["can't get up","don't want to get up","nothing matters anymore",
                         "can't go on","at my limit","drained","running on empty"],
        },
    },
}

WEIGHTS = {
    "suicide":100,"self_harm":80,"aggression":60,"panic":50,
    "dissociation":45,"hopelessness":40,"dependency":30,
    "loneliness":25,"burnout":20,
}
IMPLICIT_MULT = 0.6

# ── Time-imminence markers ────────────────────────────────────────────────────
# These never score on their own (a benign "сегодня хорошая погода" must stay
# safe). They only MULTIPLY the score when a suicide-family signal is already
# present — imminence ("сегодня", "прямо сейчас") raises acuity.
TIME_IMMINENT = [
    "сегодня","прямо сейчас","сейчас же","этой ночью","ночью сегодня",
    "завтра","завтра утром","завтра ночью","через час","через пару часов",
    "когда стемнеет","как только все уснут","уже скоро","скоро уже",
    "today","right now","tonight","tomorrow","in an hour",
]
TIME_MULTIPLIER = 1.5

# ── Ambiguity layer (the core of the v3 hotfix) ───────────────────────────────
# Phrases that MAY mean suicide but may be benign. On a hit, the pipeline must
# ASK a deterministic clarifying question — never let the LLM interpret them in
# the convenient (safe-sounding) direction. We deliberately keep this list to
# genuinely double-meaning phrases; unambiguous metaphors live in `suicide`
# above (→ full crisis), and clearly-benign mentions ("смотрю в окно") must not
# trigger, so the window case uses a targeted regex rather than a bare "в окно".
AMBIGUOUS_REGEXES = [
    # выйти/выйду/шагнуть/прыгнуть + в/из/на/с + (это/то/своё)? + окно
    re.compile(r"вы(?:йти|йду|хожу|шагн\w+|прыгн\w+|прыгну)\s+(?:в|из|на|с)\s+"
               r"(?:это|то|этого|того|своё|свое|своего)?\s*окн\w*"),
    re.compile(r"шагн\w+\s+в\s+(?:это|то)?\s*окн\w*"),
]
AMBIGUOUS_PHRASES = [
    "выйти на окно",
    "уйти и не вернуться","больше не вернусь","не вернусь больше",
    "пора заканчивать","пора заканчивать всё это","пора заканчивать все это",
    "не хочу продолжать","хочу всё бросить","хочу все бросить","брошу всё","брошу все",
    "пусть всё закончится","пусть все закончится","пусть всё это закончится",
    "не вижу выхода","это последний раз","больше не могу","сил больше нет",
    "хочу уйти насовсем",
    # EN
    "i want it to end","i can't do this anymore","i want to disappear",
]


def detect_ambiguity(text: str) -> List[str]:
    """Return the list of ambiguous phrases/patterns found (empty if none).

    A non-empty result means: do NOT run the normal LLM flow — ask a clarifying
    question first (see bot.pipeline + prompts.get_disambiguation_message)."""
    t = text.lower()
    found = [p for p in AMBIGUOUS_PHRASES if p in t]
    for rx in AMBIGUOUS_REGEXES:
        if rx.search(t):
            # Canonical label used by the disambiguation templates.
            if "выйти в окно" not in found:
                found.insert(0, "выйти в окно")
    return found


def _normalize_recent(recent_messages) -> str:
    """Accept either [{'role','content'}, ...] or [(role, content), ...]."""
    parts = []
    for m in recent_messages or []:
        if isinstance(m, dict):
            if m.get("role") == "user":
                parts.append(str(m.get("content", "")))
        elif isinstance(m, (list, tuple)) and len(m) >= 2:
            if m[0] == "user":
                parts.append(str(m[1]))
    return " ".join(parts)


def amplify_ambiguity_by_context(ambiguous_phrases: List[str],
                                 recent_messages) -> str | None:
    """Decide how to handle an ambiguous message given recent history.

    Returns:
        None                  — no ambiguity, proceed normally
        "force_disambiguation"— ambiguous, calm history → just ask to clarify
        "force_crisis"        — ambiguous + recent risk → clarify + offer hotline
    """
    if not ambiguous_phrases:
        return None
    recent_text = _normalize_recent(recent_messages)
    if recent_text.strip():
        hist = detect_risk(recent_text)
        if "suicide" in hist["categories"] or "self_harm" in hist["categories"] \
                or hist["level"] in ("medium", "high", "critical"):
            return "force_crisis"
    return "force_disambiguation"


# ── Protective factors (Columbia-style) — CONTEXT ONLY ────────────────────────
# Reasons-to-live / connectedness / responsibility / future. These are surfaced
# to the admin alongside a crisis event so a human doing follow-up knows the
# user's anchor points. They are PURELY informational: they NEVER lower the risk
# level, never suppress Crisis Protocol, never change the user-facing message.
# The "burden frame" ("всем без меня лучше") is the OPPOSITE of a protective
# factor (it's a risk signal, caught by self-blame) — it must NOT match here.
PROTECTIVE_FACTOR_PATTERNS = {
    "children": ["сын", "дочь", "дочка", "дети", "детей", "ребёнок", "ребенок",
                 "ради детей", "ради дочери", "ради сына",
                 "my son", "my daughter", "my kids", "my children"],
    "pets": ["моя собака", "мой кот", "моя кошка", "мой пёс", "мой пес", "питомец",
             "некому покормить", "кто покормит кота", "my dog", "my cat", "my pet"],
    "close_people": ["ради мамы", "что будет с мамой", "не могу так с мамой",
                     "ради родителей", "на мне держится", "не могу их оставить",
                     "что будет с ними", "ради близких",
                     "for my mom", "can't do that to my"],
    "future_plans": ["завтра у меня", "через неделю", "хочу успеть", "мечтаю",
                     "моя цель", "планирую", "скоро поездка", "впереди экзамен",
                     "свадьба", "хочу дожить до", "i dream", "my goal", "i plan"],
    "responsibility": ["работа держит", "на работе ждут", "проект надо",
                       "обещал доделать", "обещала доделать"],
    "meaning_faith": ["для меня важна вера", "это грех", "молюсь",
                      "смысл для меня в", "my faith"],
    "reasons_to_live": ["меня держит", "единственное что держит",
                        "ради чего жить", "reason to live"],
}


def detect_protective_factors(text: str) -> List[str]:
    """Return the list of protective-factor categories found (empty if none).

    CONTEXT ONLY — never used to alter risk scoring or the crisis path."""
    t = text.lower()
    found = []
    for category, phrases in PROTECTIVE_FACTOR_PATTERNS.items():
        if any(p in t for p in phrases):
            found.append(category)
    return found


def detect_risk(text: str, lang: str = "ru") -> Dict:
    t = text.lower()
    categories: List[str] = []
    score: float = 0.0
    has_explicit = False
    has_implicit = False

    for cat, lang_groups in PATTERNS.items():
        hit_e = False
        hit_i = False
        for l in {lang, "ru", "en"}:
            g = lang_groups.get(l, {})
            if not hit_e:
                hit_e = any(p in t for p in g.get("explicit", []))
            if not hit_e and not hit_i:
                hit_i = any(p in t for p in g.get("implicit", []))

        if hit_e:
            categories.append(cat); score += WEIGHTS[cat]; has_explicit = True
        elif hit_i:
            categories.append(cat); score += WEIGHTS[cat] * IMPLICIT_MULT; has_implicit = True

    if "hopelessness" in categories and "burnout" in categories: score += 20
    if "loneliness" in categories and "hopelessness" in categories: score += 15

    # Time-imminence amplification — only when a suicide signal is present.
    if "suicide" in categories and any(m in t for m in TIME_IMMINENT):
        score *= TIME_MULTIPLIER

    score = int(score)
    level = "low"
    if score >= 100: level = "critical"
    elif score >= 70: level = "high"
    elif score >= 40: level = "medium"

    return {"score":score,"level":level,"categories":categories,
            "implicit": has_implicit and not has_explicit,
            "ambiguous_phrases": detect_ambiguity(text)}
