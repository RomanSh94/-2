"""Epic C — anti-toxic-validation. Block confirming a distortion; never block a
negation or genuine feeling-validation. Bias: fewer false positives.
"""
from safety_validator import check_toxic_validation


def test_confirming_distortion_blocked():
    assert check_toxic_validation("да, тебя действительно все ненавидят")[0] is True


def test_confirming_hopelessness_blocked():
    assert check_toxic_validation("ты прав, ничего никогда не изменится")[0] is True


def test_negation_NOT_blocked():
    assert check_toxic_validation("не все тебя ненавидят, это искажение")[0] is False


def test_emotion_validation_NOT_blocked():
    assert check_toxic_validation(
        "то, что тебе сейчас так больно — это правда тяжело")[0] is False


def test_neutral_response_NOT_blocked():
    assert check_toxic_validation("расскажи, что случилось сегодня")[0] is False


def test_plain_validation_without_absolutist_NOT_blocked():
    assert check_toxic_validation("да, это звучит непросто")[0] is False
