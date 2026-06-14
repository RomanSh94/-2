"""Epic A — protective factors (Columbia-style), CONTEXT ONLY.

These pin two things: (1) we surface reasons-to-live, (2) doing so NEVER changes
the risk decision — a suicidal message with an anchor is still RED.
"""
import pytest

from risk_detector import detect_protective_factors, detect_risk
from crisis_protocol import classify, RED


def test_daughter_is_protective():
    assert "children" in detect_protective_factors("я не хочу жить, но у меня дочь")


def test_pet_is_protective():
    assert "pets" in detect_protective_factors("кто покормит кота, если меня не станет")


def test_future_plans_protective():
    assert "future_plans" in detect_protective_factors("хочу дожить до свадьбы сестры")


def test_burden_frame_is_NOT_protective():
    # Burden frame is the OPPOSITE of a protective factor — must not match.
    assert detect_protective_factors("всем без меня будет лучше") == []
    assert detect_protective_factors("я обуза для семьи") == []


def test_empty_when_no_factors():
    assert detect_protective_factors("сегодня шёл дождь") == []


def test_protective_factors_do_not_change_risk():
    # The invariant: anchor + suicide is still RED.
    plain = detect_risk("хочу умереть")
    with_anchor = detect_risk("хочу умереть, но у меня сын")
    assert classify(plain) == RED
    assert classify(with_anchor) == RED
    # The anchor must not appear as / reduce risk categories either.
    assert "suicide" in with_anchor["categories"]
