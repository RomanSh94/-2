"""Questionnaire UX (PR A) — deterministic, non-diagnostic in-chat screens.

Aiogram-free by design (same convention as navigation.py/emotion_map.py/
journals.py): this module holds only text/category constants and pure string
builders so they can be unit-tested without Telegram. Keyboards/handlers live
in bot.py, which reuses the EXISTING gates (journal_guard, then
ensure_full_access_or_closed_test) for every entrypoint.

PR A scope: registry + in-chat navigation skeleton ONLY. No scoring, no color
bars, no result interpretation, no "what scales mean" screens, no
discuss-with-bot. See CLAUDE.md / CLINICAL_BOUNDARY.md for why this is
deliberately narrow.
"""

# (category key, RU label, EN label) -- must match questionnaires.Registry
# definitions' `category` field.
CATEGORIES = [
    ("anxiety", "😟 Тревога", "😟 Anxiety"),
    ("mood", "🌧 Настроение", "🌧 Mood"),
    ("sleep_stress", "💤 Сон / стресс", "💤 Sleep / stress"),
    ("selfobs", "🧠 Самонаблюдение", "🧠 Self-observation"),
    ("specialist", "📄 Для специалиста", "📄 For a specialist"),
]

_CATEGORY_LABELS = {key: (ru, en) for key, ru, en in CATEGORIES}


def category_label(key: str, lang: str) -> str:
    ru, en = _CATEGORY_LABELS.get(key, (key, key))
    return ru if lang == "ru" else en


def list_text(lang: str = "ru") -> str:
    if lang == "ru":
        return "🧭 Опросники\n\nВыберите раздел:"
    return "🧭 Questionnaires\n\nChoose a section:"


def category_text(category: str, definitions: list[dict], lang: str = "ru") -> str:
    label = category_label(category, lang)
    if not definitions:
        if lang == "ru":
            return f"{label}\n\nВ этом разделе пока нет доступных опросников."
        return f"{label}\n\nNo questionnaires are available in this section yet."
    lines = []
    for d in definitions:
        minutes = d.get("estimated_minutes")
        n_items = len(d.get("items", []))
        if lang == "ru":
            minutes_txt = f"{minutes} мин" if minutes else ""
            lines.append(f"{d['title']} — {n_items} вопросов" + (f", {minutes_txt}" if minutes_txt else ""))
        else:
            minutes_txt = f"{minutes} min" if minutes else ""
            lines.append(f"{d['title']} — {n_items} questions" + (f", {minutes_txt}" if minutes_txt else ""))
    body = "\n".join(lines)
    if lang == "ru":
        return f"{label}\n\nДоступные опросники:\n\n{body}"
    return f"{label}\n\nAvailable questionnaires:\n\n{body}"


def detail_text(definition: dict, lang: str = "ru") -> str:
    n_items = len(definition.get("items", []))
    minutes = definition.get("estimated_minutes")
    desc = definition.get("description", "")
    if lang == "ru":
        minutes_line = f"{minutes} минут" if minutes else ""
        parts = [definition["title"], "", f"{n_items} вопросов"]
        if minutes_line:
            parts.append(minutes_line)
        parts += ["", desc, "", "Это не диагноз."]
        return "\n".join(p for p in parts if p != "" or True).replace("\n\n\n", "\n\n")
    minutes_line = f"{minutes} minutes" if minutes else ""
    parts = [definition["title"], "", f"{n_items} questions"]
    if minutes_line:
        parts.append(minutes_line)
    parts += ["", desc, "", "This is not a diagnosis."]
    return "\n".join(p for p in parts if p != "" or True).replace("\n\n\n", "\n\n")


def _progress_bar(step: int, total: int, width: int = 10) -> str:
    if total <= 0:
        return ""
    filled = round((step / total) * width)
    filled = max(0, min(width, filled))
    pct = round((step / total) * 100)
    return "█" * filled + "░" * (width - filled) + f" {pct}%"


def question_text(step: int, total: int, item_text: str, lang: str = "ru") -> str:
    bar = _progress_bar(step + 1, total)
    if lang == "ru":
        return (f"Вопрос {step + 1} из {total}\n{bar}\n\n"
                f"«{item_text}»\n\nВыберите ответ:")
    return (f"Question {step + 1} of {total}\n{bar}\n\n"
            f"“{item_text}”\n\nChoose an answer:")


def completion_text(lang: str = "ru") -> str:
    # Fixed generic text -- deliberately NOT the definition's own
    # completion_message, which has not been validated against the forbidden
    # diagnosis/dependency wording list. No score, no color bar, no
    # calculations, no "what scales mean" text, no discuss-with-bot button --
    # deliberately, per PR A scope (see CLAUDE.md / CLINICAL_BOUNDARY.md §8).
    if lang == "ru":
        return ("✅ Опросник завершён\n\n"
                 "Ответы сохранены.\n"
                 "Результаты и визуальные шкалы будут добавлены отдельным PR.\n\n"
                 "Это не диагноз.")
    return ("✅ Questionnaire complete\n\n"
            "Your answers are saved.\n"
            "Results and visual scales will be added in a separate PR.\n\n"
            "This is not a diagnosis.")


def stale_answer_text(lang: str = "ru") -> str:
    if lang == "ru":
        return "Этот вопрос уже неактуален. Продолжим с текущего места."
    return "This question is no longer current. Let's continue from where you are."


def not_available_text(lang: str = "ru") -> str:
    # Neutral, non-specific -- used both when a definition can't be found and
    # when it becomes invalid/archived/draft/restricted mid-session. Never
    # reveals internal reasons (malformed JSON, status change, etc.) to the
    # Telegram user.
    if lang == "ru":
        return "Этот опросник сейчас недоступен."
    return "This questionnaire is not available right now."


def cancelled_text(lang: str = "ru") -> str:
    if lang == "ru":
        return "Опрос прерван."
    return "Questionnaire stopped."
