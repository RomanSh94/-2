"""
X20 Practice Registry — Structured, versioned practice library.

Each practice is a structured object (not just text).
Router selects an object, not a text string.
Includes: contraindications, severity limits, evidence level, adverse_risk.

From doc: "GPT не должен придумывать практики каждый раз.
Нужна библиотека. Router выбирает объект, а не текст."
"""
from typing import List, Dict, Optional

VERSION = "v1"

REGISTRY: List[Dict] = [
    {
        "id": "grounding_5senses_v1", "version": "v1",
        "category": "grounding", "approach": "DBT",
        "name_ru": "Заземление 5-4-3-2-1", "name_en": "5-4-3-2-1 Grounding",
        "duration_min": 3, "evidence_level": "strong",
        "severity_min": "low", "severity_max": "high",
        "contraindications": [],
        "adverse_risk": "low",
        "steps_ru": [
            "Найди 5 вещей, которые ты ВИДИШЬ прямо сейчас. Назови каждую мысленно.",
            "Найди 4 вещи, которые ты можешь ПОТРОГАТЬ. Почувствуй текстуру.",
            "Найди 3 звука, которые ты СЛЫШИШЬ прямо сейчас.",
            "Найди 2 запаха или представь, что мог бы почувствовать.",
            "Найди 1 вкус во рту прямо сейчас.",
            "Почувствуй ноги на полу. Сделай медленный выдох.",
        ],
        "steps_en": [
            "Find 5 things you can SEE right now. Name each one mentally.",
            "Find 4 things you can TOUCH. Feel the texture.",
            "Find 3 sounds you can HEAR right now.",
            "Find 2 smells, or imagine what you could smell.",
            "Find 1 taste in your mouth right now.",
            "Feel your feet on the floor. Take a slow breath out.",
        ],
    },
    {
        "id": "breathing_box_v1", "version": "v1",
        "category": "grounding", "approach": "Somatic",
        "name_ru": "Дыхание по квадрату", "name_en": "Box Breathing",
        "duration_min": 3, "evidence_level": "strong",
        "severity_min": "low", "severity_max": "high",
        "contraindications": [],
        "adverse_risk": "low",
        "steps_ru": [
            "Вдох — медленно считай до 4.",
            "Задержка дыхания — держи 4 счёта.",
            "Выдох — медленно на 4 счёта.",
            "Пауза без воздуха — 4 счёта.",
            "Повтори 4 раза. Позволь телу замедлиться.",
        ],
        "steps_en": [
            "Inhale slowly, counting to 4.",
            "Hold your breath for 4 counts.",
            "Exhale slowly for 4 counts.",
            "Pause without breathing for 4 counts.",
            "Repeat 4 times. Let your body slow down.",
        ],
    },
    {
        "id": "dbt_stop_v1", "version": "v1",
        "category": "stabilization", "approach": "DBT",
        "name_ru": "Техника СТОП", "name_en": "STOP Skill",
        "duration_min": 2, "evidence_level": "strong",
        "severity_min": "low", "severity_max": "high",
        "contraindications": [],
        "adverse_risk": "low",
        "steps_ru": [
            "С — СТОП. Физически остановись. Замри на секунду.",
            "Т — ТОРМОЗИ. Не действуй под влиянием эмоции прямо сейчас.",
            "О — ОТСТУПИ. Сделай шаг назад мысленно. Сделай глубокий вдох.",
            "П — ПРИМИ решение осознанно. Что сейчас лучше всего сделать?",
        ],
        "steps_en": [
            "S — STOP. Physically freeze. Pause for a moment.",
            "T — TAKE a step back. Don't act on the emotion right now.",
            "O — OBSERVE. Take a step back mentally. Take a deep breath.",
            "P — PROCEED mindfully. What is the best thing to do right now?",
        ],
    },
    {
        "id": "cbt_thought_record_v1", "version": "v1",
        "category": "cbt", "approach": "CBT",
        "name_ru": "Дневник мысли", "name_en": "Thought Record",
        "duration_min": 5, "evidence_level": "strong",
        "severity_min": "low", "severity_max": "medium",
        "contraindications": ["ACUTE_DISTRESS"],
        "adverse_risk": "low",
        "steps_ru": [
            "Какая мысль беспокоит тебя больше всего прямо сейчас? Запиши её.",
            "Какие факты ПОДДЕРЖИВАЮТ эту мысль?",
            "Какие факты ПРОТИВОРЕЧАТ ей?",
            "Если бы так думал твой близкий друг — что бы ты ему сказал?",
            "Как можно переформулировать эту мысль более реалистично?",
        ],
        "steps_en": [
            "What thought is bothering you most right now? Write it down.",
            "What facts SUPPORT this thought?",
            "What facts CONTRADICT it?",
            "If a close friend thought this — what would you tell them?",
            "How can you reframe this thought more realistically?",
        ],
    },
    {
        "id": "act_defusion_v1", "version": "v1",
        "category": "act", "approach": "ACT",
        "name_ru": "Разделение с мыслью", "name_en": "Cognitive Defusion",
        "duration_min": 4, "evidence_level": "moderate",
        "severity_min": "low", "severity_max": "medium",
        "contraindications": ["ACUTE_DISTRESS"],
        "adverse_risk": "low",
        "steps_ru": [
            "Заметь мысль, которая тебя тревожит. Не борись с ней.",
            "Скажи себе: 'Я замечаю, что у меня есть мысль о том, что...'",
            "Представь эту мысль как облако, которое медленно проплывает мимо.",
            "Мысль может существовать рядом — и ты всё равно можешь делать то, что важно тебе.",
        ],
        "steps_en": [
            "Notice the thought that's bothering you. Don't fight it.",
            "Say to yourself: 'I notice I'm having the thought that...'",
            "Imagine this thought as a cloud slowly drifting by.",
            "The thought can exist alongside you — and you can still do what matters to you.",
        ],
    },
    {
        "id": "somatic_cold_v1", "version": "v1",
        "category": "somatic", "approach": "Somatic",
        "name_ru": "Быстрая регуляция (холодная вода)", "name_en": "Cold Water Regulation",
        "duration_min": 2, "evidence_level": "moderate",
        "severity_min": "low", "severity_max": "high",
        "contraindications": [],
        "adverse_risk": "low",
        "steps_ru": [
            "Если есть возможность — умой лицо холодной водой.",
            "Или положи запястья под холодную воду на 30 секунд.",
            "Почувствуй ощущение. Это сигнал телу: ты в безопасности.",
            "Сделай медленный выдох через рот. Повтори 3 раза.",
        ],
        "steps_en": [
            "If possible, splash cold water on your face.",
            "Or hold your wrists under cold water for 30 seconds.",
            "Notice the sensation. This signals to your body: you are safe.",
            "Take a slow breath out through your mouth. Repeat 3 times.",
        ],
    },
    {
        "id": "reflective_listen_v1", "version": "v1",
        "category": "reflective", "approach": "Rogerian",
        "name_ru": "Эмпатическое принятие", "name_en": "Empathic Reflection",
        "duration_min": 5, "evidence_level": "strong",
        "severity_min": "low", "severity_max": "high",
        "contraindications": [],
        "adverse_risk": "low",
        "steps_ru": [
            "Опиши, что происходит — без выводов, только факты.",
            "Что ты чувствуешь при этом? Попробуй назвать эмоцию.",
            "Это нормально — чувствовать именно это в такой ситуации.",
            "Чего тебе сейчас больше всего не хватает?",
        ],
        "steps_en": [
            "Describe what's happening — no judgments, just facts.",
            "What do you feel about it? Try to name the emotion.",
            "It's normal to feel exactly that in this situation.",
            "What do you need most right now?",
        ],
    },
    {
        "id": "breathing_478_v1", "version": "v1",
        "category": "somatic", "approach": "Somatic",
        "name_ru": "Дыхание 4-7-8", "name_en": "4-7-8 Breathing",
        "duration_min": 3, "evidence_level": "moderate",
        "severity_min": "low", "severity_max": "high",
        "contraindications": [],
        "adverse_risk": "low",
        "steps_ru": [
            "Вдох носом на 4 счёта.",
            "Задержи дыхание на 7 счётов.",
            "Выдох ртом со звуком на 8 счётов.",
            "Повтори цикл 3–4 раза.",
        ],
        "steps_en": [
            "Inhale through your nose for 4 counts.",
            "Hold your breath for 7 counts.",
            "Exhale through your mouth with a sound for 8 counts.",
            "Repeat the cycle 3–4 times.",
        ],
    },
]

CATEGORY_MAP = {
    "crisis":"grounding","grounding":"grounding",
    "stabilization":"stabilization","cbt_thought":"cbt",
    "act_acceptance":"act","reflective":"reflective",
    "somatic":"somatic","open_chat":"reflective",
}


def select_practice(scenario: str, stage: str = "OPEN",
                    severity: str = "medium", lang: str = "ru") -> Optional[Dict]:
    """
    Select best practice for scenario/stage/severity.
    Respects contraindications and severity limits.
    Returns practice dict with 'steps' key populated from correct language.
    """
    category = CATEGORY_MAP.get(scenario, "grounding")

    severity_order = {"low":0,"medium":1,"high":2}
    user_sev = severity_order.get(severity, 1)

    candidates = [
        p for p in REGISTRY
        if p["category"] == category
        and stage not in p.get("contraindications", [])
        and severity_order.get(p["severity_min"],0) <= user_sev
        and user_sev <= severity_order.get(p["severity_max"],2)
    ]

    if not candidates:
        candidates = [p for p in REGISTRY if p["category"] == "grounding"]

    practice = candidates[0]
    result = dict(practice)
    result["steps"] = practice.get(f"steps_{lang}", practice.get("steps_ru", []))
    result["name"]  = practice.get(f"name_{lang}", practice.get("name_ru", practice["id"]))
    return result


def get_practice_by_id(practice_id: str, lang: str = "ru") -> Optional[Dict]:
    for p in REGISTRY:
        if p["id"] == practice_id:
            result = dict(p)
            result["steps"] = p.get(f"steps_{lang}", p.get("steps_ru", []))
            result["name"]  = p.get(f"name_{lang}", p.get("name_ru", p["id"]))
            return result
    return None
