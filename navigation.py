"""Navigation Hub — deterministic menu/section text and catalog metadata.

Aiogram-free by design (same convention as journals.py): this module holds
only text/category constants and pure string builders so they can be
unit-tested without Telegram. Keyboards/handlers live in bot.py, which reuses
the EXISTING product-access gate (access_control.has_full_access via
ensure_full_access_or_closed_test) and the EXISTING active-crisis gate
(journal_guard) for every entrypoint here -- this module never decides
access/safety, it only renders catalog text.

No LLM. No scoring. No interpretation. No diagnosis. Tests/questionnaires
content is placeholder-only until a separate, owner-approved governance PR
adds real (non-copyrighted, licensed) definitions.
"""
from crisis_protocol import get_hotline

# (callback key, RU label, EN label) -- callback_data becomes f"{key}:hub".
MENU_SECTIONS = [
    ("tests", "🧪 Тесты и опросники", "🧪 Tests and questionnaires"),
    ("journals", "📝 Дневники", "📝 Diaries"),
    ("results", "📊 Мои результаты", "📊 My results"),
    ("privacy", "🔒 Приватность", "🔒 Privacy"),
    ("about", "ℹ️ О боте", "ℹ️ About the bot"),
]

# Placeholder catalog only -- no real scale/questionnaire content or links yet.
TEST_CATEGORIES = [
    ("anxiety", "😟 Тревога", "😟 Anxiety"),
    ("mood", "🌧 Настроение", "🌧 Mood"),
    ("stress", "⚡ Стресс", "⚡ Stress"),
    ("sleep", "😴 Сон", "😴 Sleep"),
    ("selfobs", "🧭 Самонаблюдение", "🧭 Self-observation"),
]


def menu_text(lang: str = "ru") -> str:
    if lang == "ru":
        return "Главное меню\n\nВыберите раздел:"
    return "Main menu\n\nChoose a section:"


def tests_hub_text(lang: str = "ru") -> str:
    if lang == "ru":
        categories = "\n".join(ru for _, ru, _ in TEST_CATEGORIES)
        return (
            "Тесты и опросники\n\n"
            "Этот раздел предназначен для самонаблюдения и подготовки к разговору "
            "со специалистом.\nОн не ставит диагнозы и не заменяет врача или "
            "психолога.\n\n"
            f"Категории:\n{categories}\n\n"
            "Скоро здесь будут доступны материалы для самонаблюдения.\n"
            "Мы добавим их только после проверки источников, лицензий и safety-рамки."
        )
    categories = "\n".join(en for _, _, en in TEST_CATEGORIES)
    return (
        "Tests and questionnaires\n\n"
        "This section is for self-observation and preparing for a conversation "
        "with a specialist.\nIt does not diagnose and does not replace a doctor "
        "or psychologist.\n\n"
        f"Categories:\n{categories}\n\n"
        "Self-observation materials will be available here soon.\n"
        "We'll only add them after checking sources, licensing, and the safety framework."
    )


def journals_hub_text(lang: str = "ru") -> str:
    if lang == "ru":
        return (
            "Дневники\n\n"
            "📝 Дневник эмоций → /emotion\n"
            "🧠 КПТ-дневник → /cbt\n"
            "📤 Экспорт дневников → /journal_export\n"
            "🗑 Удаление дневников → /journal_delete"
        )
    return (
        "Diaries\n\n"
        "📝 Emotion journal → /emotion\n"
        "🧠 CBT journal → /cbt\n"
        "📤 Export journals → /journal_export\n"
        "🗑 Delete journals → /journal_delete"
    )


def privacy_hub_text(lang: str = "ru") -> str:
    if lang == "ru":
        return (
            "🔒 Приватность\n\n"
            "📤 Экспорт всех данных → /privacy_export_all\n"
            "🗑 Удаление всех данных → /privacy_delete_all\n"
            "🧹 Забыть всё → /forget_all"
        )
    return (
        "🔒 Privacy\n\n"
        "📤 Export all data → /privacy_export_all\n"
        "🗑 Delete all data → /privacy_delete_all\n"
        "🧹 Forget everything → /forget_all"
    )


def results_hub_text(lang: str = "ru") -> str:
    if lang == "ru":
        return (
            "Раздел результатов пока не активен.\n\n"
            "Мы не показываем оценки, диагнозы или уровни выраженности.\n"
            "Позже здесь могут появиться только безопасные данные "
            "самонаблюдения после отдельного решения."
        )
    return (
        "The results section is not active yet.\n\n"
        "We don't show scores, diagnoses, or severity levels.\n"
        "Later, only safe self-observation data may appear here, after a "
        "separate decision."
    )


def about_hub_text(lang: str = "ru") -> str:
    # Uses the REAL configured hotline (crisis_protocol.get_hotline) rather
    # than inventing a number -- must not contradict the bot's actual
    # deterministic crisis-detection/delivery behavior.
    hotline = get_hotline(lang)["primary"]
    if lang == "ru":
        return (
            "Этот бот помогает вести дневники, структурировать мысли и "
            "готовиться к разговору со специалистом.\n\n"
            "Он не ставит диагнозы и не заменяет психолога или врача. Если в "
            "разговоре появляются явные признаки серьёзного риска, бот может "
            "автоматически показать экстренные контакты — но это не замена "
            f"профессиональной помощи. Если тебе нужна помощь прямо сейчас — "
            f"позвони на горячую линию {hotline}."
        )
    return (
        "This bot helps you keep journals, structure your thoughts, and "
        "prepare for a conversation with a specialist.\n\n"
        "It does not diagnose and does not replace a psychologist or doctor. "
        "If clear signs of serious risk appear in the conversation, the bot "
        "may automatically show emergency contacts — but this is not a "
        "substitute for professional help. If you need help right now, call "
        f"the crisis line {hotline}."
    )
