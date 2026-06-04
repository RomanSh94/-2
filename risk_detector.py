"""
X20 Risk Detector — Final Multilingual Edition (RU + EN)

Architecture: LLM never decides. Only deterministic code decides.
Explicit signals: weight × 1.0
Implicit signals: weight × 0.6
Compound rules add extra score for co-occurring weak signals.
"""
from typing import Dict, List

PATTERNS: Dict[str, Dict[str, Dict[str, List[str]]]] = {
    "suicide": {
        "ru": {
            "explicit": ["не хочу жить","хочу умереть","покончить с собой","убить себя",
                         "свести счёты с жизнью","лучше бы меня не было","устал жить",
                         "устала жить","жить не хочу","хочу всё это закончить",
                         "прощайте все","прощай навсегда","хочу исчезнуть навсегда"],
            "implicit": ["скоро всё кончится","всем будет лучше без меня",
                         "вам будет лучше без меня","я только мешаю","никто не заметит",
                         "моё время вышло","я уже всё решил","я уже всё решила",
                         "не хочу просыпаться","хочу просто не существовать",
                         "хочу пропасть","незачем жить","ничего не осталось",
                         "я устал просыпаться","не вижу причин продолжать",
                         "не вижу смысла продолжать","устал существовать",
                         "устала существовать","я больше не могу так жить",
                         "не вижу выхода кроме"],
        },
        "en": {
            "explicit": ["i want to die","don't want to live","want to kill myself",
                         "end my life","end it all","take my own life",
                         "better off dead","wish i were dead","no reason to live",
                         "goodbye forever","suicide","want to disappear forever"],
            "implicit": ["everyone would be better without me","i'm just a burden",
                         "no one would notice","my time is up","i've already decided",
                         "don't want to wake up","want to stop existing",
                         "can't go on","nothing left","soon it will be over",
                         "no reason to continue","tired of waking up"],
        },
    },
    "self_harm": {
        "ru": {
            "explicit": ["режу себя","режу руки","вскрыться","селфхарм","self-harm",
                         "порезать себя","причинить себе боль","жгу себя"],
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

    score = int(score)
    level = "low"
    if score >= 100: level = "critical"
    elif score >= 70: level = "high"
    elif score >= 40: level = "medium"

    return {"score":score,"level":level,"categories":categories,
            "implicit": has_implicit and not has_explicit}
