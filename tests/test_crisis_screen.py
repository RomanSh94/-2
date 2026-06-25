"""Crisis screen (RED) — direct phone + warm text, and the detection that must
reach RED so the Crisis Protocol (not the LLM) answers.

The product requirement: each listed phrase → RED, and the crisis screen shows a
number + a CALL button, with NO "can you call someone?" question before it.
"""
import pytest

from risk_detector import detect_risk
from crisis_protocol import (
    classify, crisis_keyboard, RED, get_hotline,
    crisis_screen, crisis_call_text, crisis_contact_template,
    crisis_safe_place_ack, crisis_resolved_text, is_reassuring, MAX_STAGE,
)
from prompts import get_crisis_text


# ── Detection must reach RED (gaps found in Step 0) ───────────────────────────
@pytest.mark.parametrize("text", [
    "хочу покончить с собой", "не хочу жить", "лучше бы меня не было",
    "хочу умереть", "не вижу смысла жить", "нет смысла жить", "смысла жить нет",
    "жить незачем", "не хочу больше жить", "зачем мне жить",
    "прыгну с балкона", "хочу спрыгнуть", "выйти в окно",
    "я бы хотела сейчас выйти в это окно", "шагнуть из окна",
])
def test_crisis_phrases_are_red(text):
    assert classify(detect_risk(text)) == RED


@pytest.mark.parametrize("text", [
    "смотрю в окно на дождь", "сегодня гулял в парке", "открой окно, душно",
])
def test_benign_not_red(text):
    assert classify(detect_risk(text)) != RED


# ── Crisis screen content ─────────────────────────────────────────────────────
def test_crisis_text_shows_a_number_and_is_warm():
    txt = get_crisis_text("ru")
    assert "8-800-2000-122" in txt and "112" in txt
    # Warm/direct, not the old clinical hedge.
    assert "если есть риск" not in txt.lower()
    assert "не оставался" in txt or "не оставалась" in txt or "не оставался(ась)" in txt


def test_crisis_keyboard_no_tel_url():
    # Telegram rejects tel: in inline-button URLs (it crashes the send). The
    # number lives in the message text instead; guard against re-introducing it.
    for lang in ("ru", "en"):
        for row in crisis_keyboard(lang).inline_keyboard:
            for b in row:
                assert not (b.url and b.url.startswith("tel:"))


def test_crisis_keyboard_has_safety_buttons():
    kb = crisis_keyboard("ru")
    flat = [b for row in kb.inline_keyboard for b in row]
    assert any(b.callback_data == "crisis:safe" for b in flat)
    assert any(b.callback_data == "crisis:still" for b in flat)
    # Only valid (non-tel) URL buttons allowed; EN's IASP https link is fine.
    en = [b for row in crisis_keyboard("en").inline_keyboard for b in row]
    assert any(b.url and b.url.startswith("https://") for b in en)


def test_hotline_directory_defaults_to_ru():
    assert get_hotline("ru")["primary"] == "8-800-2000-122"
    assert get_hotline("xx")["primary"] == "8-800-2000-122"   # unknown → main region
    assert get_hotline("ru")["secondary"] == "112"            # universal fallback


# ── EN crisis screens: English text + 988/911 + English labels on every stage ─
def test_en_crisis_screens_are_english_with_us_numbers():
    for stage in range(MAX_STAGE + 1):
        text, kb = crisis_screen(stage, "en", event_id=7)
        assert "988" in text and "911" in text            # US hotline + emergency
        # Body is English, not the Russian template.
        assert "пожалуйста" not in text.lower()
        labels = [b.text for row in kb.inline_keyboard for b in row]
        low = " ".join(labels).lower()
        assert "call" in low and "safe" in low            # English action labels
        # No Cyrillic leaked into EN button labels.
        assert not any(any("а" <= ch.lower() <= "я" for ch in l) for l in labels)


def test_en_helper_texts_are_english():
    assert "tappable" in crisis_call_text("en").lower()
    assert "forward this" in crisis_contact_template("en").lower()
    assert "you feel safe" in crisis_safe_place_ack("en").lower()
    assert "im glad" in crisis_resolved_text("en").lower().replace("'", "")


def test_ru_crisis_screens_unchanged():
    for stage in range(MAX_STAGE + 1):
        text, _ = crisis_screen(stage, "ru", event_id=1)
        assert "8-800-2000-122" in text
        assert "988" not in text


def test_unknown_lang_falls_back_to_ru():
    # Requirement 3: unknown language → RU screen (never an empty/untranslated one).
    for lang in ("xx", "", None, "de"):
        text, kb = crisis_screen(0, lang, event_id=1)
        assert "8-800-2000-122" in text
        labels = [b.text for row in kb.inline_keyboard for b in row]
        assert any("ПОЗВОНИТЬ" in l for l in labels)


def test_en_crisis_texts_name_no_self_harm_methods():
    blob = " ".join(
        crisis_screen(s, "en", 1)[0] for s in range(MAX_STAGE + 1)
    ) + crisis_call_text("en") + crisis_contact_template("en")
    for bad in ("pills", "rope", "razor", "overdose", "jump", "cut", "hang"):
        assert bad not in blob.lower()


# ── Requirement 1: apostrophe variants of EN suicidal phrases are caught ───────
@pytest.mark.parametrize("text", [
    "i cant go on", "i can't go on", "cant go on",
    "i dont want to live", "i don't want to live",
    "im just a burden", "i'm just a burden",
    "ive already decided", "i've already decided",
    "i dont want to wake up", "don't want to wake up",
    "i wish id never been born", "i wish i'd never been born",
])
def test_apostrophe_variants_reach_red(text):
    assert classify(detect_risk(text, "en")) == RED


# ── Requirement 2: EN active-crisis gate is symmetric to RU ────────────────────
# Distress (or anything unclear) must NOT be treated as reassuring → crisis screen
# stays; only explicitly calm text flips to the "I'm safe" offer.
@pytest.mark.parametrize("text", [
    "I'm not safe", "im not safe", "I feel awful", "i feel terrible",
    "i cant cope", "i still feel awful", "i want to hurt myself",
])
def test_en_distress_not_reassuring(text):
    assert is_reassuring(text) is False


@pytest.mark.parametrize("text", [
    "I'm ok now", "im okay", "i'm safe now", "feeling better", "all good thanks",
])
def test_en_explicit_calm_is_reassuring(text):
    assert is_reassuring(text) is True
