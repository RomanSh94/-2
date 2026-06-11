"""Regression tests for the deterministic safety validator (Priority 2/3)."""
from safety_validator import validate_response


def test_forbidden_diagnosis_blocked():
    ok, reason = validate_response("Похоже, у тебя депрессия.", "ru")
    assert ok is False
    assert reason


def test_forbidden_love_declaration_blocked_en():
    ok, _ = validate_response("Honestly, i love you and only you.", "en")
    assert ok is False


def test_clean_response_passes():
    ok, reason = validate_response("Я здесь. Расскажи, что происходит.", "ru")
    assert ok is True
    assert reason is None


def test_overlong_response_blocked():
    long_text = " ".join(["слово"] * 151)
    ok, reason = validate_response(long_text, "ru")
    assert ok is False
    assert "long" in reason.lower()
