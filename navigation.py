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

# (callback key, RU label, EN label) -- bot.py maps the questionnaire entry to
# q:l and all other entries to their existing hub callbacks.
MENU_SECTIONS = [
    ("talk", "💬 Поговорить", "💬 Talk"),
    ("tests", "🧪 Тесты и опросники", "🧪 Tests and questionnaires"),
    ("journals", "📝 Дневники", "📝 Diaries"),
    ("results", "📊 Мои результаты", "📊 My results"),
    ("settings", "⚙️ Настройки ответа", "⚙️ Response settings"),
    ("privacy", "🔒 Данные и приватность", "🔒 Data and privacy"),
    ("about", "ℹ️ О X20", "ℹ️ About X20"),
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


def talk_hub_text(lang: str = "ru") -> str:
    if lang == "ru":
        return "Напиши сообщением, что сейчас происходит или о чём хочется поговорить."
    return "Send a message about what is happening now or what you want to talk about."


def response_settings_text(lang: str = "ru", *, available: bool = True) -> str:
    if lang == "ru":
        return ("Как тебе удобнее получать ответы?" if available else
                "Настройки озвучивания сейчас выключены. Ответы будут приходить текстом.")
    return ("How would you like to receive replies?" if available else
            "Voice settings are currently off. Replies will arrive as text.")


def feedback_hub_text(lang: str = "ru") -> str:
    if lang == "ru":
        return (
            "Обратная связь откроется во внешнем публичном или общественном пространстве. "
            "Не публикуй там фрагменты личного чата и чувствительные персональные данные, "
            "если только ты осознанно не хочешь ими поделиться."
        )
    return (
        "Feedback opens an external public or community space. Do not post private chat "
        "excerpts or sensitive personal information unless you consciously want to share it."
    )


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
            "📊 Недельный отчёт → /report\n"
            "⏰ Настройки напоминаний → /journal_settings\n"
            "📤 Экспорт дневников → /journal_export\n"
            "🗑 Удаление дневников → /journal_delete"
        )
    return (
        "Diaries\n\n"
        "📝 Emotion journal → /emotion\n"
        "🧠 CBT journal → /cbt\n"
        "📊 Weekly report → /report\n"
        "⏰ Reminder settings → /journal_settings\n"
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
            "Мои результаты\n\n"
            "📊 Недельный отчёт дневника → /report\n"
            "🧭 Мой профиль самонаблюдения → /profile\n\n"
            "Опросники показывают только те результаты, которые разрешены их "
            "проверенным контрактом. X20 не ставит диагнозы."
        )
    return (
        "My results\n\n"
        "📊 Weekly journal report → /report\n"
        "🧭 My self-observation profile → /profile\n\n"
        "Questionnaires show only results allowed by their validated contract. "
        "X20 does not diagnose."
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
