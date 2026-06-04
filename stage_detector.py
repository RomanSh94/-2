"""
X20 Stage Detector

Determines WHERE the user is in their emotional process.
This is CRITICAL: the same emotion requires different interventions
depending on the stage.

Stage: ACUTE_DISTRESS → only grounding/containment, NO CBT
Stage: REFLECTION     → CBT/ACT appropriate
Stage: PROBLEM_SOLVING→ structured approaches
Stage: GROWTH         → maintenance, skill-building

From research doc: "Пользователь пишет 'Мне изменил муж 2 часа назад.'
→ Нельзя давать CBT. Нужна стабилизация и контейнирование."
"""
from typing import Dict, List

STAGE_SIGNALS: Dict[str, Dict[str, List[str]]] = {
    "ACUTE_DISTRESS": {
        "ru": ["только что произошло","я не могу поверить","меня трясет",
               "все рухнуло","только что узнал","только что узнала",
               "это случилось сегодня","буквально сейчас","я в шоке",
               "не могу прийти в себя","всё рухнуло","произошло только что",
               "несколько минут назад","несколько часов назад",
               "не могу остановить слезы","не могу перестать плакать"],
        "en": ["just happened","i can't believe it","i'm shaking",
               "everything collapsed","just found out","in shock",
               "can't calm down","happened today","happened just now",
               "a few hours ago","can't stop crying","can't process this"],
    },
    "REFLECTION": {
        "ru": ["я думаю о том","я понял что","я поняла что","мне кажется",
               "я заметил","я заметила","я начинаю понимать",
               "если подумать","я размышляю","хочу разобраться",
               "пытаюсь понять","это связано с","я осознал"],
        "en": ["i've been thinking about","i realized","it seems to me",
               "i noticed","i'm starting to understand","if i think about it",
               "i'm reflecting","want to understand","trying to figure out",
               "this is connected to","i've come to understand"],
    },
    "PROBLEM_SOLVING": {
        "ru": ["что делать","как справиться","какой план","нужен совет",
               "помоги решить","как поступить","что лучше","подскажи как",
               "хочу изменить","хочу исправить","как мне"],
        "en": ["what should i do","how to cope","what's the plan","need advice",
               "help me solve","what's better","how do i","want to change",
               "want to fix","how can i"],
    },
    "GROWTH": {
        "ru": ["стало лучше","я справляюсь","прогресс","развиваюсь",
               "учусь","хочу стать","работаю над","улучшаю","расту"],
        "en": ["feeling better","i'm coping","progress","developing",
               "learning","want to become","working on","improving","growing"],
    },
}

# What is BLOCKED per stage
STAGE_RESTRICTIONS = {
    "ACUTE_DISTRESS": {"blocked": ["cbt_thought","act_acceptance","problem_solving"],
                       "allowed": ["grounding","stabilization","somatic","reflective"]},
    "REFLECTION":     {"blocked": [], "allowed": "all"},
    "PROBLEM_SOLVING":{"blocked": [], "allowed": "all"},
    "GROWTH":         {"blocked": ["crisis","grounding"], "allowed": "all"},
    "OPEN":           {"blocked": [], "allowed": "all"},
}


def detect_stage(text: str, lang: str = "ru") -> str:
    t = text.lower()
    for stage, langs in STAGE_SIGNALS.items():
        for l in {lang, "ru", "en"}:
            signals = langs.get(l, [])
            if any(s in t for s in signals):
                return stage
    return "OPEN"


def get_stage_restrictions(stage: str) -> Dict:
    return STAGE_RESTRICTIONS.get(stage, STAGE_RESTRICTIONS["OPEN"])
