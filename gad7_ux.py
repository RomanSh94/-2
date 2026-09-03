"""Deterministic presentation for the approved GAD-7 Russian adaptation.

Instrument-specific visible copy stays out of the generic questionnaire UX
module. This module contains no scoring, persistence, Telegram, or LLM logic.
"""


def detail_text(lang: str = "ru") -> str:
    if lang != "ru":
        return ("Generalized Anxiety Disorder-7 (GAD-7)\n\n"
                "7 questions · about 2 minutes\n\n"
                "This questionnaire assesses how often anxiety symptoms have "
                "bothered you over the past 2 weeks.\n\n"
                "Choose how often each experience occurred.\n\n"
                "The result describes anxiety symptom level; it is not a diagnosis.\n\n"
                "Authors: R. L. Spitzer, K. Kroenke, J. B. W. Williams, B. Löwe, 2006.\n"
                "Russian adaptation: A. A. Zolotareva, 2023.")
    return ("Опросник генерализованного тревожного расстройства — ГТР-7 (GAD-7)\n\n"
            "7 вопросов · около 2 минут\n\n"
            "Опросник поможет оценить, как часто за последние 2 недели тебя беспокоили\n"
            "тревожные симптомы.\n\n"
            "Для каждого пункта выбери, насколько часто это происходило.\n\n"
            "Результат помогает оценить выраженность тревожных симптомов,\n"
            "но не устанавливает диагноз.\n\n"
            "Авторы: R. L. Spitzer, K. Kroenke, J. B. W. Williams, B. Löwe, 2006.\n"
            "Русская адаптация: А. А. Золотарева, 2023.")


def question_text(step: int, total: int, item_text: str,
                  options: list, lang: str = "ru") -> str:
    labels = "\n".join(option["label"] for option in options)
    if lang != "ru":
        body = f"GAD-7 · {step + 1} of {total}\n\n{item_text}\n\nOver the past two weeks:"
    else:
        body = f"ГТР-7 · {step + 1} из {total}\n\n{item_text}\n\nЗа последние две недели:"
    return f"{body}\n\n{labels}" if labels else body


def result_text(score: int, band_ru: str, lang: str = "ru") -> str:
    if lang != "ru":
        extra = ("\n\nThis result may be a reason for a more detailed assessment "
                 "of anxiety symptoms." if score >= 10 else "")
        return ("GAD-7 — result\n\n"
                f"Total score — {score} of 21\n"
                f"Anxiety symptom level — {band_ru}\n\n"
                "The questionnaire shows how often seven anxiety-related experiences "
                "bothered you over the past 2 weeks."
                f"{extra}\n\nThe result is not a diagnosis.")
    extra = ("\n\nТакой результат может быть основанием для более подробной оценки\n"
             "тревожных симптомов." if score >= 10 else "")
    return ("ГТР-7 (GAD-7) — результат\n\n"
            f"Общий балл — {score} из 21\n"
            f"Выраженность тревожных симптомов — {band_ru}\n\n"
            "Опросник показывает, как часто семь тревожных проявлений беспокоили тебя\n"
            "за последние 2 недели."
            f"{extra}\n\nРезультат не является диагнозом.")
