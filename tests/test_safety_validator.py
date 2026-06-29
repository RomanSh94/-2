"""Regression tests for the deterministic safety validator (Priority 2/3)."""
from safety_validator import (
    validate_response, get_fallback, get_safe_fallback_high_risk, select_fallback,
)
from humanization import has_robotic_phrase


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


# ── PR1b: neutral fallback is no longer a banned cliché ───────────────────────
def test_neutral_fallback_has_no_robotic_cliche():
    assert "расскажи больше" not in get_fallback("ru").lower()
    assert "tell me more" not in get_fallback("en").lower()
    assert has_robotic_phrase(get_fallback("ru"), "ru") is False
    assert has_robotic_phrase(get_fallback("en"), "en") is False


def test_neutral_fallback_validates():
    for lng in ("ru", "en"):
        ok, reason = validate_response(get_fallback(lng), lng)
        assert ok is True, f"{lng}: {reason}"


# ── PR1b: select_fallback is risk-aware (the routing fix) ─────────────────────
def test_select_fallback_low_is_neutral():
    assert select_fallback({"level": "low"}, "ru") == get_fallback("ru")
    assert select_fallback({"level": "low"}, "en") == get_fallback("en")


def test_select_fallback_elevated_is_high_risk():
    for lvl in ("medium", "high", "critical"):
        assert select_fallback({"level": lvl}, "ru") == get_safe_fallback_high_risk("ru")
    assert select_fallback({"level": "high"}, "en") == get_safe_fallback_high_risk("en")


def test_select_fallback_ambiguous_overrides_low():
    # The key case: an ambiguous phrase must route to high-risk even at low level.
    r = {"level": "low", "ambiguous_phrases": ["выйти в окно"]}
    assert select_fallback(r, "ru") == get_safe_fallback_high_risk("ru")
    assert select_fallback(r, "en") == get_safe_fallback_high_risk("en")


def test_select_fallback_empty_risk_defaults_to_neutral():
    # Runs on failure paths — empty/None risk must NOT crash; safe neutral default.
    assert select_fallback({}, "ru") == get_fallback("ru")
    assert select_fallback(None, "ru") == get_fallback("ru")
