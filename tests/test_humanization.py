"""Tests for the Humanization layer (Epic 2, MASTER_SPEC_v2 §7).

Deterministic, no LLM/Telegram. Pins: greeting variety axes, typing-pause
bounds, the anti-robot cliché filter (both languages), and persona voice
injection into system prompts.
"""
import random

from humanization import (
    pick_greeting, typing_delay, has_robotic_phrase, persona_voice,
    rephrase_instruction,
)


def test_first_greeting_differs_from_return():
    rng = random.Random(0)
    first = pick_greeting(True, 9, "ru", rng)
    # first-time pool is distinct from the time-of-day return pools
    from humanization import _GREETINGS
    assert first in _GREETINGS["ru"]["first"]


def test_greeting_varies_by_time_of_day():
    from humanization import _GREETINGS
    morning = pick_greeting(False, 8, "ru", random.Random(1))
    night = pick_greeting(False, 2, "ru", random.Random(1))
    assert morning in _GREETINGS["ru"]["morning"]
    assert night in _GREETINGS["ru"]["night"]


def test_greeting_english():
    from humanization import _GREETINGS
    g = pick_greeting(True, 14, "en", random.Random(2))
    assert g in _GREETINGS["en"]["first"]


def test_typing_delay_within_bounds():
    # min ≈ 1.5 (empty), max ≈ 1.5 + 2.0 + 0.5 = 4.0
    for text in ["", "x" * 50, "y" * 1000]:
        d = typing_delay(text)
        assert 1.5 <= d <= 4.0


def test_robotic_phrase_detected_ru():
    assert has_robotic_phrase("Я слышу тебя, это пройдёт", "ru")


def test_robotic_phrase_detected_en():
    assert has_robotic_phrase("I hear you, tell me more", "en")


def test_robotic_phrase_cross_language():
    # detector scans both lists regardless of declared lang
    assert has_robotic_phrase("you're not alone", "ru")


def test_natural_phrase_passes():
    assert not has_robotic_phrase("Звучит так, будто понедельник всегда давит.", "ru")


def test_persona_voice_present_in_prompt():
    from prompts import get_system_prompt
    p_ru = get_system_prompt("open_chat", "ru")
    p_en = get_system_prompt("open_chat", "en")
    assert "ГОЛОС" in p_ru
    assert "VOICE" in p_en


def test_rephrase_instruction_bilingual():
    assert rephrase_instruction("ru") != rephrase_instruction("en")
