"""Regression tests for the deterministic safety validator (Priority 2/3)."""
from safety_validator import (
    validate_response, get_fallback, get_safe_fallback_high_risk, select_fallback,
    is_elevated_risk, classify_rejection_reason, REJECTION_CATEGORIES,
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


# ── is_elevated_risk: extracted, single-sourced predicate (round 2) ───────────
def test_is_elevated_risk_matches_select_fallback_high_risk_condition():
    for lvl in ("medium", "high", "critical"):
        assert is_elevated_risk({"level": lvl}) is True
    assert is_elevated_risk({"level": "low"}) is False
    assert is_elevated_risk({"level": "low", "ambiguous_phrases": ["x"]}) is True
    assert is_elevated_risk({}) is False
    assert is_elevated_risk(None) is False


# ── classify_rejection_reason: bounded, loggable categories (round 2) ─────────
def test_classify_forbidden_phrase():
    assert classify_rejection_reason("Forbidden phrase: похоже, у тебя депрессия") \
        == "FORBIDDEN_PHRASE"


def test_classify_too_long():
    assert classify_rejection_reason("Response too long (>150 words)") == "TOO_LONG"


def test_classify_certainty_claim():
    assert classify_rejection_reason("Certainty claim detected") == "CERTAINTY_CLAIM"


def test_classify_toxic_validation():
    assert classify_rejection_reason(
        "toxic validation: confirmed distortion 'никто'") == "TOXIC_VALIDATION"


def test_classify_ambiguous_approval():
    assert classify_rejection_reason(
        "approval phrase 'это хорошая идея' after ambiguous user message") \
        == "AMBIGUOUS_APPROVAL"


def test_classify_risky_suggestion():
    assert classify_rejection_reason(
        "risky suggestion 'выйти на улицу' at risk level medium") == "RISKY_SUGGESTION"


def test_classify_unknown_or_missing_reason_is_other():
    assert classify_rejection_reason("some future reason string") == "OTHER_VALIDATOR_REJECTION"
    assert classify_rejection_reason(None) == "OTHER_VALIDATOR_REJECTION"


def test_classify_never_returns_outside_bounded_set():
    samples = [
        "Forbidden phrase: x", "Response too long (>150 words)",
        "Certainty claim detected", "toxic validation: confirmed distortion 'y'",
        "approval phrase 'z' after ambiguous user message",
        "risky suggestion 'w' at risk level high",
        None, "", "unrelated",
    ]
    for reason in samples:
        assert classify_rejection_reason(reason) in REJECTION_CATEGORIES


def test_classify_never_echoes_the_matched_phrase_itself():
    # The category is fixed vocabulary -- the matched substring from the
    # reason string must never leak into the returned category value.
    category = classify_rejection_reason(
        "Forbidden phrase: секретная-фраза-которая-не-должна-логироваться")
    assert "секретная-фраза" not in category
