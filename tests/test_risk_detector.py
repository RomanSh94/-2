"""Regression tests for deterministic crisis detection (Priority 2/3).

The pipeline relies on these categories/levels to trigger the crisis
override before any LLM call — so they must stay stable.
"""
from risk_detector import detect_risk


def test_explicit_suicide_is_critical():
    r = detect_risk("я хочу умереть", "ru")
    assert "suicide" in r["categories"]
    assert r["level"] == "critical"


def test_explicit_suicide_english():
    r = detect_risk("i want to kill myself", "en")
    assert "suicide" in r["categories"]


def test_cross_language_detection():
    # Detector scans both ru+en lists regardless of declared lang.
    r = detect_risk("i want to die", "ru")
    assert "suicide" in r["categories"]


def test_benign_text_is_low_and_safe():
    r = detect_risk("сегодня хорошая погода, гулял в парке", "ru")
    assert r["level"] == "low"
    assert "suicide" not in r["categories"]
    assert "self_harm" not in r["categories"]
