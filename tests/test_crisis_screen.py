"""Crisis screen (RED) — direct phone + warm text, and the detection that must
reach RED so the Crisis Protocol (not the LLM) answers.

The product requirement: each listed phrase → RED, and the crisis screen shows a
number + a CALL button, with NO "can you call someone?" question before it.
"""
import pytest

from risk_detector import detect_risk
from crisis_protocol import classify, crisis_keyboard, RED, get_hotline
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


def test_crisis_keyboard_has_call_button_first():
    kb = crisis_keyboard("ru")
    first = kb.inline_keyboard[0][0]
    assert first.text == "📞 ПОЗВОНИТЬ"
    assert first.url.startswith("tel:")
    # Safety self-report buttons come AFTER, on row 2.
    row2 = kb.inline_keyboard[1]
    assert any(b.callback_data == "crisis:safe" for b in row2)
    assert any(b.callback_data == "crisis:still" for b in row2)


def test_hotline_directory_defaults_to_ru():
    assert get_hotline("ru")["tel"] == "+78002000122"
    assert get_hotline("xx")["tel"] == "+78002000122"   # unknown → main region
