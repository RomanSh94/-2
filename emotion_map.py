"""Emotion Map — deterministic, non-diagnostic emotion-vocabulary helper.

Shown alongside prompts that ask the user to name/identify a current emotion
(onboarding mood prompt, the emotion-journal "feeling" step, the CBT-journal
"emotion" step). This is a recognition/vocabulary aid, not a test: it never
scores, interprets, or infers anything from which word the user picks -- it
doesn't even record a selection. Opening the map is a pure read; the user's
actual answer is still whatever they type in the ordinary flow afterward.

Aiogram-free (same convention as journals.py/navigation.py) -- keyboards and
gating live in bot.py, reusing the existing product-access and active-crisis
gates exactly as every other entrypoint does.
"""

# (key, RU label, RU words, EN label, EN words)
EMOTION_MAP_CATEGORIES = [
    ("joy", "🟡 Радость", "радость, восторг, безмятежность, оптимизм",
     "🟡 Joy", "joy, delight, serenity, optimism"),
    ("trust", "🟢 Доверие/принятие", "доверие, принятие, любовь, спокойствие",
     "🟢 Trust/acceptance", "trust, acceptance, love, calm"),
    ("sadness", "🔵 Грусть", "грусть, печаль, одиночество, разочарование",
     "🔵 Sadness", "sadness, sorrow, loneliness, disappointment"),
    ("disgust", "🟣 Отвращение", "отвращение, скука, презрение",
     "🟣 Disgust", "disgust, boredom, contempt"),
    ("anger", "🔴 Злость", "злость, раздражение, гнев, агрессия",
     "🔴 Anger", "anger, irritation, rage, aggression"),
    ("fear", "🟠 Тревога/страх", "тревога, страх, напряжение, ужас",
     "🟠 Anxiety/fear", "anxiety, fear, tension, terror"),
    ("surprise", "🔷 Удивление", "удивление, изумление, растерянность",
     "🔷 Surprise", "surprise, amazement, confusion"),
    ("unknown", "⚪ Не знаю", "трудно понять, смешанные чувства, пустота",
     "⚪ Don't know", "hard to tell, mixed feelings, emptiness"),
]


def emotion_map_intro(lang: str = "ru") -> str:
    if lang == "ru":
        return ("Если сложно понять, что именно ты чувствуешь, можно опереться "
                "на карту эмоций.\nВыбери слово, которое ближе всего к твоему "
                "состоянию. Можно выбрать неточно — это нормально.")
    return ("If it's hard to tell what exactly you're feeling, you can lean on "
            "the emotion map.\nPick the word closest to your state. It's fine "
            "if it's not exact.")


def emotion_map_text(lang: str = "ru") -> str:
    lines = [emotion_map_intro(lang), ""]
    for _, ru_label, ru_words, en_label, en_words in EMOTION_MAP_CATEGORIES:
        if lang == "ru":
            lines.append(f"{ru_label}: {ru_words}")
        else:
            lines.append(f"{en_label}: {en_words}")
    return "\n".join(lines)


def emotion_map_return_hint(lang: str = "ru") -> str:
    if lang == "ru":
        return "Вернись к предыдущему вопросу и напиши слово или выбери ближайший вариант."
    return "Go back to the previous question and write a word, or pick the closest option."
