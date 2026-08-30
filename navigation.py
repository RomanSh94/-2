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
    ("tests", "🧠 Психологические тесты", "🧠 Psychological tests"),
    ("journals", "📝 Дневники", "📝 Diaries"),
    ("results", "📊 Мои результаты", "📊 My results"),
    ("settings", "🎛 Как отвечать", "🎛 How to reply"),
    ("privacy", "🔒 Данные и приватность", "🔒 Data and privacy"),
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


def help_text(lang: str = "ru") -> str:
    """Round 3: /help's own card, and what "⬅️ В меню" (menu:back) returns
    to. NOT a second permanently-visible menu -- the persistent lower
    ReplyKeyboard remains the primary navigation surface.

    UI polish V1: Help no longer advertises section shortcuts the
    persistent lower menu already provides (tests/journals/results/
    settings) or a "Talk" button (the text field is always available) --
    see _help_keyboard in bot.py. Copy updated to match: it now only
    describes the two things Help itself still surfaces (About, Privacy)
    plus the always-available text field."""
    if lang == "ru":
        return (
            "ℹ️ Помощь\n\n"
            "Здесь можно посмотреть информацию о боте и настройках приватности.\n\n"
            "Если хочешь поговорить — просто напиши сообщение."
        )
    return (
        "ℹ️ Help\n\n"
        "Here you can see information about the bot and privacy settings.\n\n"
        "If you'd like to talk, just send a message."
    )


def talk_hub_text(lang: str = "ru") -> str:
    if lang == "ru":
        return "Напиши сообщением, что сейчас происходит или о чём хочется поговорить."
    return "Send a message about what is happening now or what you want to talk about."


def response_settings_text(lang: str = "ru", *, available: bool = True) -> str:
    if lang == "ru":
        return ("Кстати, вот как я звучу 🎧\n\nКак тебе удобнее получать ответы?" if available else
                "Сейчас ответы доступны только текстом. Голосовые ответы временно недоступны.")
    return ("By the way, this is how I sound 🎧\n\nHow would you like to receive replies?" if available else
            "Right now replies are text-only. Voice replies are temporarily unavailable.")


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
    """Round 3: no longer reached by any live handler -- bot.py's
    cb_journals_hub renders the real journal card via the shared
    _journal_hub_text/_journal_hub_keyboard builders instead (one journal
    navigation UX, not a second raw slash-command list here). Kept as a
    neutral, brand-independent heading only for compatibility."""
    return "📝 Дневники" if lang == "ru" else "📝 Diaries"


def privacy_hub_text(lang: str = "ru") -> str:
    if lang == "ru":
        return (
            "🔒 Данные и приватность\n\n"
            "Здесь можно посмотреть, какие данные сохраняются, получить их копию "
            "или удалить данные аккаунта."
        )
    return (
        "🔒 Data and privacy\n\n"
        "Here you can see what data is stored, get a copy, or delete your account data."
    )


def privacy_stored_data_text(lang: str = "ru") -> str:
    if lang == "ru":
        return (
            "ℹ️ Какие данные хранятся\n\n"
            "В зависимости от того, какими функциями ты пользуешься, могут сохраняться:\n\n"
            "• история разговоров и контекст;\n"
            "• настройки и предпочтения;\n"
            "• записи дневников;\n"
            "• ответы и результаты психологических тестов;\n"
            "• технические данные, необходимые для работы сервиса и обеспечения безопасности.\n\n"
            "В этом разделе можно получить копию своих данных или удалить данные аккаунта."
        )
    return (
        "ℹ️ What data is stored\n\n"
        "Depending on the features you use, stored data may include:\n\n"
        "• conversation history and context;\n"
        "• settings and preferences;\n"
        "• diary entries;\n"
        "• questionnaire answers and results;\n"
        "• technical data needed to operate and secure the service.\n\n"
        "You can get a copy of your data or delete account data from this section."
    )


def results_hub_text(lang: str = "ru") -> str:
    if lang == "ru":
        return (
            "📊 Мои результаты\n\n"
            "Здесь можно посмотреть отчёт по дневнику и свои наблюдения за состоянием."
        )
    return (
        "📊 My results\n\n"
        "Here you can see your diary report and your self-observations."
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
