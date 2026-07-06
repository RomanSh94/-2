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


# ── PR B — gated result/calculations/explanation screens ────────────────────
# Everything below is dormant unless config.QUESTIONNAIRE_INTERPRETATION_ENABLED
# is true AND the definition passes the eligibility check in bot.py (synthetic
# legal_status + user_visible_full/user_visible_score result_policy). Pure,
# deterministic string builders only -- no scoring/eligibility logic lives
# here (that's questionnaires.py / bot.py); this module only renders text from
# numbers/labels it is given. Never use: норма, патология, опасность,
# расстройство, вероятность заболевания, диагноз (except in the fixed
# "not a diagnosis" disclaimers) -- anywhere in this module.

_INTENSITY_LABELS = ("низкая", "умеренная", "заметная", "высокая")
_INTENSITY_LABELS_EN = ("low", "moderate", "noticeable", "high")
_SEGMENT_COLORS = ("🟩", "🟨", "🟧", "🟥")


def render_intensity_bar(score: int, max_score: int, segments: int = 7) -> str:
    """Pure/deterministic. Renders `segments` colored blocks (low->high:
    green/yellow/orange/red) filled proportionally to score/max_score, with
    the remainder as empty ⬜ blocks. No labels, no numbers -- just the bar."""
    if segments <= 0:
        return ""
    if max_score <= 0:
        fraction = 0.0
    else:
        fraction = max(0.0, min(1.0, score / max_score))
    filled = round(fraction * segments)
    filled = max(0, min(segments, filled))
    bar_chars = []
    for i in range(filled):
        # Color ramps low->high across the FILLED portion's own position in
        # the overall bar (not just the fill count), so a bar filled to 2/7
        # stays green while one filled to 6/7 progresses to red.
        pos_fraction = (i + 1) / segments
        if pos_fraction <= 0.25:
            color_idx = 0
        elif pos_fraction <= 0.5:
            color_idx = 1
        elif pos_fraction <= 0.75:
            color_idx = 2
        else:
            color_idx = 3
        bar_chars.append(_SEGMENT_COLORS[color_idx])
    bar_chars += ["⬜"] * (segments - filled)
    return "".join(bar_chars)


def intensity_label(score: int, max_score: int, lang: str = "ru") -> str:
    """Maps score/max_score fraction to one of exactly four labels (never
    anything else): низкая/умеренная/заметная/высокая (ru) or their EN
    equivalents low/moderate/noticeable/high."""
    if max_score <= 0:
        fraction = 0.0
    else:
        fraction = max(0.0, min(1.0, score / max_score))
    labels = _INTENSITY_LABELS if lang == "ru" else _INTENSITY_LABELS_EN
    if fraction <= 0.25:
        return labels[0]
    if fraction <= 0.5:
        return labels[1]
    if fraction <= 0.75:
        return labels[2]
    return labels[3]


def result_text(score: int, max_score: int, lang: str = "ru", segments: int = 7) -> str:
    bar = render_intensity_bar(score, max_score, segments)
    label = intensity_label(score, max_score, lang)
    if lang == "ru":
        return (f"✅ Результат готов\n\n"
                f"Ваш результат: {score} / {max_score}\n\n"
                f"{bar}\n"
                f"0        ●        {max_score}\n\n"
                f"Выраженность по вашим ответам: {label}\n\n"
                f"Это не диагноз. Результат можно использовать для самонаблюдения.")
    return (f"✅ Result ready\n\n"
            f"Your result: {score} / {max_score}\n\n"
            f"{bar}\n"
            f"0        ●        {max_score}\n\n"
            f"Level based on your answers: {label}\n\n"
            f"This is not a diagnosis. You can use the result for self-observation.")


def calculations_text(values: list[int], score: int, max_score: int, lang: str = "ru") -> str:
    sum_expr = " + ".join(str(v) for v in values)
    if lang == "ru":
        return (f"📊 Расчёты\n\n"
                f"Ответы:\n{sum_expr} = {score}\n\n"
                f"Итог:\n{score} / {max_score}\n\n"
                f"Цветовая шкала показывает выраженность суммы ответов.\n"
                f"Она не является диагнозом.")
    return (f"📊 Calculations\n\n"
            f"Answers:\n{sum_expr} = {score}\n\n"
            f"Total:\n{score} / {max_score}\n\n"
            f"The color scale reflects the intensity of the answer sum.\n"
            f"It is not a diagnosis.")


def explanation_text(scale_explanation_main: str, lang: str = "ru") -> str:
    if lang == "ru":
        return (f"🧠 Что значат шкалы\n\n"
                f"{scale_explanation_main}\n\n"
                f"Это не диагноз и не медицинское заключение.")
    return (f"🧠 What the scales mean\n\n"
            f"{scale_explanation_main}\n\n"
            f"This is not a diagnosis or a medical conclusion.")


# ── PR C1 — specialist report (self-only, no LLM, dormant unless the flag/
# eligibility conditions in bot.py's report handler allow a score line).
# Pure string builder: bot.py assembles the (item_text, answer_label) pairs
# in definition-item order and passes them in already resolved; this module
# only formats them. Reuses the same "не диагноз" disclaimer framing as the
# rest of this file rather than inventing new wording.

def specialist_report_text(title: str, completed_at: str | None,
                            answer_lines: list[str],
                            score_line: str | None, lang: str = "ru") -> str:
    if lang == "ru":
        parts = [f"📄 Отчёт для специалиста\n\n{title}"]
        if completed_at:
            parts.append(f"Дата завершения: {completed_at}")
        parts.append("")
        parts.append("Ответы:")
        parts.extend(answer_lines)
        if score_line:
            parts.append("")
            parts.append(score_line)
        parts.append("")
        parts.append("Это не диагноз и не медицинское заключение. Отчёт предназначен "
                      "только для вашего личного использования, например при разговоре "
                      "со специалистом.")
        return "\n".join(parts)
    parts = [f"📄 Specialist report\n\n{title}"]
    if completed_at:
        parts.append(f"Completed: {completed_at}")
    parts.append("")
    parts.append("Answers:")
    parts.extend(answer_lines)
    if score_line:
        parts.append("")
        parts.append(score_line)
    parts.append("")
    parts.append("This is not a diagnosis or a medical conclusion. This report is for "
                  "your own personal use, for example when talking with a specialist.")
    return "\n".join(parts)
