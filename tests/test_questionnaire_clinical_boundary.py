"""Questionnaire Core PR #1 — clinical boundary wording checks.

Scans the ACTUAL user-facing text builders in bot.py against the forbidden
diagnosis/threshold/dependency wording list from CLINICAL_BOUNDARY.md §7 and
the owner's explicit corrections in this PR's implementation prompt.
"""
import json
import pathlib

import bot

FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "synthetic_questionnaire.json"

FORBIDDEN_PHRASES = (
    "у тебя депрессия", "лёгкая депрессия", "умеренная депрессия",
    "тяжёлая депрессия", "выраженная тревожность", "результат выше нормы",
    "я рядом", "я всегда рядом",
)


def _all_wording_ru() -> list[str]:
    return [
        bot._questionnaire_consent_text("ru"),
        bot._questionnaire_not_configured_text("ru"),
        bot._questionnaire_completion_text("ru"),
        bot._questionnaire_cancelled_text("ru"),
    ]


def _all_wording_en() -> list[str]:
    return [
        bot._questionnaire_consent_text("en"),
        bot._questionnaire_not_configured_text("en"),
        bot._questionnaire_completion_text("en"),
        bot._questionnaire_cancelled_text("en"),
    ]


def test_questionnaire_consent_text_is_non_diagnostic():
    text = bot._questionnaire_consent_text("ru")
    assert "не диагноз" in text
    assert "замена специалиста" in text or "substitute" in bot._questionnaire_consent_text("en")


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
    # bot._questionnaire_completion_text is a fixed generic string, never
    # derived from the private definition dict.
    definition = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    private_completion_ru = definition["completion_message"]["ru"]
    assert bot._questionnaire_completion_text("ru") != private_completion_ru
