"""Questionnaire Registry (PR A) — clinical boundary wording checks.

Scans the ACTUAL user-facing text builders in questionnaire_ux.py against the
forbidden diagnosis/threshold/dependency wording list from CLINICAL_BOUNDARY.md
§7. PR A is deliberately narrow: no scoring, no color bar, no interpretation,
no discuss-with-bot anywhere in these screens (see completion_text).
"""
import json
import pathlib

import questionnaire_ux as ux

FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "synthetic_questionnaire.json"

FORBIDDEN_PHRASES = (
    "у тебя депрессия", "лёгкая депрессия", "умеренная депрессия",
    "тяжёлая депрессия", "выраженная тревожность", "результат выше нормы",
    "я рядом", "я всегда рядом",
)


def _all_wording_ru() -> list[str]:
    return [
        ux.list_text("ru"), ux.not_available_text("ru"),
        ux.completion_text("ru"), ux.cancelled_text("ru"),
        ux.stale_answer_text("ru"),
    ]


def _all_wording_en() -> list[str]:
    return [
        ux.list_text("en"), ux.not_available_text("en"),
        ux.completion_text("en"), ux.cancelled_text("en"),
        ux.stale_answer_text("en"),
    ]


def test_questionnaire_completion_text_is_non_diagnostic():
    text = ux.completion_text("ru")
    assert "не диагноз" in text
    assert "not a diagnosis" in ux.completion_text("en")


def test_questionnaire_copy_contains_no_diagnosis_labels():
    for text in _all_wording_ru() + _all_wording_en():
        low = text.lower()
        for phrase in FORBIDDEN_PHRASES:
            assert phrase not in low, f"forbidden phrase {phrase!r} found in: {text!r}"


def test_questionnaire_copy_contains_no_threshold_or_severity_labels():
    for text in _all_wording_ru() + _all_wording_en():
        low = text.lower()
        assert "результат выше нормы" not in low
        assert "severity" not in low
        assert "threshold" not in low


def test_questionnaire_does_not_emit_depression_verdict():
    for text in _all_wording_ru() + _all_wording_en():
        low = text.lower()
        assert "депресси" not in low   # RU stem covers "депрессия"/"депрессии"/etc.
        assert "depression" not in low


def test_questionnaire_copy_contains_no_dependency_language():
    for text in _all_wording_ru() + _all_wording_en():
        low = text.lower()
        assert "я рядом" not in low
        assert "я всегда рядом" not in low
        assert "i'm always here" not in low


def test_private_completion_message_not_used_without_validation():
    # The fixture's own completion_message must NOT be what gets sent --
    # ux.completion_text is a fixed generic string, never derived from a
    # loaded definition dict.
    definition = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    private_completion_ru = definition["completion_message"]["ru"]
    assert ux.completion_text("ru") != private_completion_ru


# ── PR A scope guard: completion screen has NO score/color/interpretation ──────
def test_completion_screen_has_no_score_color_or_discuss_button():
    text_ru = ux.completion_text("ru")
    text_en = ux.completion_text("en")
    forbidden_terms = (
        "балл", "score", "обсудить с ботом", "discuss with",
        "интерпретац", "interpretation",
        "██", "░░",  # progress/color-bar-style glyphs must not appear here
    )
    for term in forbidden_terms:
        assert term not in text_ru.lower(), f"{term!r} found in RU completion text"
        assert term not in text_en.lower(), f"{term!r} found in EN completion text"
