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

# ── Professional, manifest-driven catalog (replaces the old symptom-label
# category list). The 6 root categories below are the catalog root; categories
# 1-4 render governance-manifest instruments as INFO entries (never startable
# here), self_observation lists the synthetic registry demos (the only
# startable path), consultation_report reframes the old "Для специалиста".
CATALOG_CATEGORIES = [
    ("depression_mood_energy", "Депрессия, настроение и энергия", "Depression, mood & energy"),
    ("anxiety", "Тревога", "Anxiety"),
    ("stress", "Стресс", "Stress"),
    ("specialized", "Специализированные шкалы", "Specialized scales"),
    ("self_observation", "Самонаблюдение", "Self-observation"),
    ("consultation_report", "Отчёт для консультации", "Consultation report"),
]

# Categories 1-4 render manifest instruments; 5-6 are handled specially.
CATALOG_MANIFEST_CATEGORY_IDS = frozenset(
    {"depression_mood_energy", "anxiety", "stress", "specialized"})

_CATALOG_CATEGORY_LABELS = {key: (ru, en) for key, ru, en in CATALOG_CATEGORIES}


def catalog_category_label(key: str, lang: str) -> str:
    ru, en = _CATALOG_CATEGORY_LABELS.get(key, (key, key))
    return ru if lang == "ru" else en


def list_text(lang: str = "ru") -> str:
    # Professional catalog root. Honest and non-diagnostic: opting a screening
    # instrument into the catalog is explicitly NOT a claim that the bot can
    # run it.
    if lang == "ru":
        return ("☑️ Скрининговые шкалы и опросники\n\n"
                "Здесь собраны инструменты самонаблюдения и скрининга.\n\n"
                "Опросники не ставят диагноз и не заменяют консультацию специалиста.\n"
                "Доступность конкретной методики зависит от её версии и прав на "
                "цифровое использование.\n\n"
                "Выберите раздел:")
    return ("☑️ Screening scales and questionnaires\n\n"
            "This is a set of self-observation and screening instruments.\n\n"
            "Questionnaires do not diagnose and do not replace a consultation "
            "with a specialist.\n"
            "Whether a given method is available depends on its version and "
            "digital-use rights.\n\n"
            "Choose a section:")


def catalog_category_text(category_id: str, lang: str = "ru") -> str:
    label = catalog_category_label(category_id, lang)
    if category_id == "consultation_report":
        return consultation_report_text(lang)
    if category_id == "self_observation":
        if lang == "ru":
            return (f"{label}\n\n"
                    "Небольшие опросники для самонаблюдения, которые можно пройти "
                    "прямо в боте.\nЭто не диагноз.")
        return (f"{label}\n\n"
                "Short self-observation questionnaires you can take right here in "
                "the bot.\nThis is not a diagnosis.")
    if lang == "ru":
        return (f"{label}\n\n"
                "Выберите методику, чтобы узнать о ней подробнее.\n"
                "Активные опросники будут отдельно отмечены как доступные.")
    return (f"{label}\n\n"
            "Choose a method to learn more about it.\n"
            "Active questionnaires will be marked separately as available.")


def catalog_empty_text(category_id: str, lang: str = "ru") -> str:
    label = catalog_category_label(category_id, lang)
    # Never a bare dead end: the caller always attaches back/menu buttons.
    if lang == "ru":
        return f"{label}\n\nВ этом разделе пока нет проверенных методик."
    return f"{label}\n\nThere are no verified methods in this section yet."


_AVAILABILITY_STATUS_RU = {
    "available": "доступно",
    "information_only": "проводится специалистом",
    "requires_license": "требуется лицензированная версия",
    "version_under_review": "версия уточняется",
    "unavailable": "недоступно",
}
_AVAILABILITY_STATUS_EN = {
    "available": "available",
    "information_only": "administered by a specialist",
    "requires_license": "a licensed version is required",
    "version_under_review": "version being clarified",
    "unavailable": "unavailable",
}


def instrument_info_text(instrument, lang: str = "ru") -> str:
    """Deterministic information screen for a manifest instrument. No score, no
    result interpretation, no diagnosis, no clinical cutoff. `instrument` is a
    clinical_instrument_catalog.CatalogInstrument."""
    av = instrument.availability
    clinician = instrument.administration_mode == "clinician_rated"
    if lang == "ru":
        title = instrument.title_ru or instrument.abbreviation
        type_line = "оценка специалистом" if clinician else "самоотчёт"
        status_line = _AVAILABILITY_STATUS_RU.get(av, "недоступно")
        parts = [title, "", f"Тип: {type_line}", f"Статус: {status_line}"]
        if instrument.population_note_ru:
            parts.append(instrument.population_note_ru)
        parts.append("")
        if av == "available":
            parts.append("Методика доступна для прохождения.")
        else:
            parts.append("Сейчас прохождение в боте недоступно.")
        if clinician:
            parts.append("Методика проводится обученным специалистом в формате интервью.")
        if av == "requires_license":
            parts.append("Наличие методики в каталоге не означает, что бот может "
                         "законно воспроизводить её вопросы.")
        parts.append("")
        parts.append("Это не диагноз.")
        return "\n".join(parts)
    title = instrument.title_en or instrument.abbreviation
    type_line = "clinician-rated" if clinician else "self-report"
    status_line = _AVAILABILITY_STATUS_EN.get(av, "unavailable")
    parts = [title, "", f"Type: {type_line}", f"Status: {status_line}"]
    if instrument.population_note_ru:
        parts.append("For pregnancy and the period after childbirth.")
    parts.append("")
    if av == "available":
        parts.append("This method is available to take.")
    else:
        parts.append("Taking it in the bot is not available right now.")
    if clinician:
        parts.append("This method is administered by a trained specialist as an interview.")
    if av == "requires_license":
        parts.append("Listing a method in the catalog does not mean the bot may "
                     "lawfully reproduce its questions.")
    parts.append("")
    parts.append("This is not a diagnosis.")
    return "\n".join(parts)


def consultation_report_text(lang: str = "ru") -> str:
    # Reframe of the old "Для специалиста": the report is user-owned and never
    # auto-sent to anyone.
    if lang == "ru":
        return ("📄 Отчёт для консультации\n\n"
                "После прохождения опросника можно сформировать отчёт с твоими "
                "ответами.\n\n"
                "Ты сам решаешь, кому показать отчёт.\n"
                "Бот никому не отправляет его автоматически.\n\n"
                "Это не диагноз и не медицинское заключение.")
    return ("📄 Consultation report\n\n"
            "After completing a questionnaire you can generate a report with your "
            "answers.\n\n"
            "You decide who to show the report to.\n"
            "The bot never sends it to anyone automatically.\n\n"
            "This is not a diagnosis or a medical conclusion.")


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


def question_text(step: int, total: int, item_text: str, lang: str = "ru",
                  options: list | None = None) -> str:
    """Single-card question screen (PR #57). When `options` is given, the FULL
    answer wording is rendered as a legend inside the card text, so the inline
    buttons can stay short (Telegram truncates long button labels). The legend
    text comes from the runtime definition — never hardcoded here."""
    bar = _progress_bar(step + 1, total)
    legend = ""
    if options:
        legend = "\n" + "\n".join(
            f"{o['value']} — {_legend_label(o)}" for o in options) + "\n"
    if lang == "ru":
        return (f"Вопрос {step + 1} из {total}\n{bar}\n\n"
                f"«{item_text}»\n{legend}\nВыберите ответ:")
    return (f"Question {step + 1} of {total}\n{bar}\n\n"
            f"“{item_text}”\n{legend}\nChoose an answer:")


def _legend_label(option: dict) -> str:
    """Full option wording for the in-card legend, with a duplicated leading
    "<value> —"/"<value> -" prefix stripped (many definitions already embed
    the numeric anchor in the label)."""
    label = option.get("label", "")
    value = str(option.get("value", ""))
    for sep in (" — ", " - ", " – "):
        prefix = f"{value}{sep}"
        if label.startswith(prefix):
            return label[len(prefix):]
    return label


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


# ── discuss-with-bot menu (deterministic, no LLM) ────────────────────────────
# Dormant unless bot.py's gate allows it: the generic path behind
# config.QUESTIONNAIRE_INTERPRETATION_ENABLED + questionnaires.is_result_
# eligible (button wired into _questionnaire_result_keyboard), and the
# DASS-21 path behind config.DASS21_DISCUSSION_ENABLED + dass21_access
# authorization (button wired into bot._dass21_completion_keyboard). This
# module holds ONLY the fixed menu text/topic prompt strings; the actual LLM
# call and trace-builder wiring live in bot.py.

def discuss_menu_text(lang: str = "ru") -> str:
    if lang == "ru":
        return ("💬 Обсудить результат\n\n"
                "Я могу помочь спокойно посмотреть на результат как на ориентир "
                "для самонаблюдения.\n"
                "Это не диагноз и не медицинское заключение.\n\n"
                "Что хочешь разобрать?")
    return ("💬 Discuss the result\n\n"
            "I can help you take a calm look at the result as a marker for "
            "self-observation.\n"
            "This is not a diagnosis or a medical conclusion.\n\n"
            "What would you like to go through?")


def discuss_topic_prompt(title: str, score: int, max_score: int, intensity: str,
                          topic_id: str, lang: str = "ru") -> str:
    """Fixed, template-driven prompt text sent to the LLM for one topic. Never
    includes raw stored answer text, instrument manual wording, norm-table
    data, or diagnosis labels -- only title/score/intensity_label/topic_id and
    a non-diagnostic interpretation-boundary instruction."""
    if lang == "ru":
        boundary = ("Это не диагноз и не медицинское заключение. Не ставь диагнозов, "
                     "не называй расстройств, не оценивай вероятность заболевания, не "
                     "назначай лечение и не давай медицинских советов. Поддерживай "
                     "самонаблюдение пользователя.")
        topic_lines = {
            "why": "Помоги человеку спокойно порассуждать, что в его повседневной жизни "
                   "могло отражаться в таком результате -- без диагнозов и причинных "
                   "утверждений, только как повод для самонаблюдения.",
            "next": "Предложи мягкие, небольшие практические шаги для самонаблюдения и "
                    "заботы о себе, которые человек может попробовать дальше.",
            "specialist": "Помоги человеку сформулировать 2-3 вопроса, которые он мог бы "
                          "задать специалисту, опираясь на этот результат как ориентир "
                          "для разговора -- не вместо специалиста.",
        }
        topic_line = topic_lines[topic_id]
        return (f"Опросник: {title}\nРезультат: {score} / {max_score}\n"
                f"Выраженность: {intensity}\nТема: {topic_id}\n\n"
                f"{topic_line}\n\n{boundary}")
    boundary = ("This is not a diagnosis or a medical conclusion. Do not diagnose, do "
                "not name disorders, do not estimate probability of illness, do not "
                "prescribe treatment, and do not give medical advice. Support the "
                "user's self-observation.")
    topic_lines = {
        "why": "Help the person calmly reflect on what in their everyday life might be "
               "reflected in this result -- without diagnoses or causal claims, only "
               "as a prompt for self-observation.",
        "next": "Suggest gentle, small practical steps for self-observation and "
                "self-care the person could try next.",
        "specialist": "Help the person phrase 2-3 questions they could bring to a "
                      "specialist, using this result as a talking-point marker -- not "
                      "a substitute for a specialist.",
    }
    topic_line = topic_lines[topic_id]
    return (f"Questionnaire: {title}\nResult: {score} / {max_score}\n"
            f"Intensity: {intensity}\nTopic: {topic_id}\n\n"
            f"{topic_line}\n\n{boundary}")


def dass21_discuss_topic_prompt(instrument_version: str, translation_id: str,
                                 subscales, topic_id: str, lang: str = "ru") -> str:
    """Workstream B — data-minimized DASS-21 discuss prompt. Contains ONLY
    instrument_version/translation_id, the three subscale ints, and topic_id
    -- never raw stored answer text, item wording, answer labels, an overall
    total, or a severity/diagnosis label (the scorer contract provides
    neither). Topic ids are the DASS-specific, non-causal set
    (bot._DASS21_DISCUSS_TOPICS): measures/relate/next/specialist -- NOT the
    generic "why did this happen" framing, which would imply the scores
    establish a cause."""
    dep, anx, stress = subscales["depression"], subscales["anxiety"], subscales["stress"]
    if lang == "ru":
        boundary = ("Это не диагноз и не медицинское заключение. Шкалы описывают "
                     "проявления за последнюю неделю, а не причины -- не утверждай, "
                     "что результат ЧТО-ТО ВЫЗВАЛ. Не ставь диагнозов, не называй "
                     "расстройств, не оценивай вероятность заболевания, не "
                     "назначай лечение или лекарства и не давай медицинских "
                     "советов. Связь с недавним опытом -- только предположительная. "
                     "Дай один короткий, законченный ответ на выбранную тему; там, "
                     "где уместно, мягко упомяни, что разговор со специалистом "
                     "может быть полезен.")
        topic_lines = {
            "measures": "Объясни простыми словами, что именно измеряют шкалы "
                        "депрессии, тревоги и стресса за последнюю неделю -- НЕ что "
                        "их вызвало.",
            "relate": "Предложи спокойно, необвинительно и предположительно "
                      "посмотреть, как эти шкалы могли бы соотноситься с недавним "
                      "опытом человека -- явно как повод для самонаблюдения, а не "
                      "как причинное объяснение.",
            "next": "Предложи один разумный следующий шаг и что стоит понаблюдать "
                    "за собой дальше -- мягко, без давления.",
            "specialist": "Помоги сформулировать 2-3 вопроса, которые человек мог "
                          "бы задать специалисту, и мягко обозначь, когда обращение "
                          "к специалисту может быть уместным. Профессиональную "
                          "терминологию (депрессия/тревога/стресс как названия "
                          "шкал) можно использовать, не ставя диагноз.",
        }
        topic_line = topic_lines[topic_id]
        return (f"Опросник: {instrument_version} (перевод {translation_id})\n"
                f"Шкала депрессии: {dep}\nШкала тревоги: {anx}\nШкала стресса: {stress}\n"
                f"Тема: {topic_id}\n\n{topic_line}\n\n{boundary}")
    boundary = ("This is not a diagnosis or a medical conclusion. The scales "
                "describe the past week, not causes -- do not claim the result "
                "CAUSED anything. Do not diagnose, do not name disorders, do not "
                "estimate probability of illness, do not prescribe treatment or "
                "medication, and do not give medical advice. Any relation to recent "
                "experience is tentative only. Give one short, complete answer for "
                "the chosen topic; where relevant, gently note that speaking with a "
                "specialist may be useful.")
    topic_lines = {
        "measures": "Explain in plain language what the depression, anxiety, and "
                    "stress scales actually measure over the past week -- NOT what "
                    "caused them.",
        "relate": "Invite a calm, non-blaming, tentative look at how these scales "
                  "might relate to the person's recent experience -- explicitly as a "
                  "prompt for self-observation, not a causal explanation.",
        "next": "Suggest one reasonable next step and what to keep observing -- "
                "gently, without pressure.",
        "specialist": "Help phrase 2-3 questions the person could bring to a "
                      "specialist, and gently note when seeing a specialist may be "
                      "worthwhile. Professional terminology (depression/anxiety/"
                      "stress as scale names) may be used without assigning a "
                      "diagnosis.",
    }
    topic_line = topic_lines[topic_id]
    return (f"Instrument: {instrument_version} (translation {translation_id})\n"
            f"Depression scale: {dep}\nAnxiety scale: {anx}\nStress scale: {stress}\n"
            f"Topic: {topic_id}\n\n{topic_line}\n\n{boundary}")


def dass21_result_text(subscales, lang: str) -> str:
    """PR #55 — owner-only DASS-21 completion screen: three numeric subscale
    values ONLY. Deliberately no overall total, no cutoffs, no severity
    labels, no probability, no diagnosis, no treatment plan, no LLM text."""
    dep, anx, stress = subscales["depression"], subscales["anxiety"], subscales["stress"]
    if lang == "en":
        return (f"DASS-21 — self-report results\n\n"
                f"Depression: {dep}\n"
                f"Anxiety: {anx}\n"
                f"Stress: {stress}\n\n"
                "These are self-report results for the past week, not a diagnosis.\n"
                "For clinical conclusions, please discuss the result with a specialist.")
    return (f"DASS-21 — результаты самооценки\n\n"
            f"Депрессия: {dep}\n"
            f"Тревога: {anx}\n"
            f"Стресс: {stress}\n\n"
            "Это результаты самооценки за последнюю неделю, а не диагноз.\n"
            "Для клинических выводов результат следует обсуждать со специалистом.")
