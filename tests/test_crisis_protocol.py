"""Tests for the Crisis Protocol level mapping and UI (Epic 1).

These pin the deterministic crisis path: RED must fire for any suicide/self_harm
signal (the only level that stops the pipeline and shows resources), the crisis
keyboard must always expose the safe/still self-report buttons plus a help link,
and the admin alert must be tagged #CRITICAL.
"""
from crisis_protocol import (
    classify, crisis_keyboard, admin_alert_text, RED, ORANGE, YELLOW, GREEN,
)


def test_suicide_is_red():
    assert classify({"categories": ["suicide"], "level": "critical"}) == RED


def test_self_harm_is_red():
    assert classify({"categories": ["self_harm"], "level": "high"}) == RED


def test_high_score_without_suicide_is_orange():
    assert classify({"categories": ["panic"], "level": "high"}) == ORANGE
    assert classify({"categories": ["hopelessness"], "level": "critical"}) == ORANGE


def test_medium_is_yellow():
    assert classify({"categories": ["loneliness"], "level": "medium"}) == YELLOW


def test_low_is_green():
    assert classify({"categories": [], "level": "low"}) == GREEN


def test_keyboard_has_self_report_callbacks():
    for lang in ("ru", "en"):
        kb = crisis_keyboard(lang)
        callbacks = [b.callback_data for row in kb.inline_keyboard for b in row
                     if b.callback_data]
        assert "crisis:safe" in callbacks
        assert "crisis:still" in callbacks


def test_ru_keyboard_has_no_tel_button():
    # Telegram rejects tel: in inline buttons (it crashed the crisis send). The
    # number is shown as tappable plain text in get_crisis_text instead.
    kb = crisis_keyboard("ru")
    urls = [b.url for row in kb.inline_keyboard for b in row if b.url]
    assert not any(u.startswith("tel:") for u in urls)


def test_en_keyboard_has_help_link():
    kb = crisis_keyboard("en")
    urls = [b.url for row in kb.inline_keyboard for b in row if b.url]
    assert any("iasp" in u for u in urls)


def test_admin_alert_is_tagged_critical():
    txt = admin_alert_text(123, "alice", RED,
                           {"level": "critical", "score": 100,
                            "categories": ["suicide"]},
                           "i want to die")
    assert "#CRITICAL" in txt
    assert "suicide" in txt
