"""Tests for the Silence Engine push decision (Epic 3, MASTER_SPEC_v2 §8).

decide_push() is the safety-critical antispam gate. These pin every veto rule
and the tier selection. Pure function — no DB, no Telegram.
"""
from datetime import datetime, timedelta, timezone

from silence_engine import decide_push, pick_tier, is_quiet_hours, MAX_UNANSWERED

# A fixed "now" at a non-quiet hour (12:00 UTC) so quiet-hours never interferes
# unless a test sets it up explicitly.
NOON = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)


def _ago(**kw):
    return NOON - timedelta(**kw)


def test_pick_tier_thresholds():
    assert pick_tier(timedelta(hours=11)) is None
    assert pick_tier(timedelta(hours=13)) == "12h"
    assert pick_tier(timedelta(days=4)) == "3d"
    assert pick_tier(timedelta(days=8)) == "7d"
    assert pick_tier(timedelta(days=40)) == "30d"


def test_quiet_hours_wraps_midnight():
    assert is_quiet_hours(NOON.replace(hour=23))
    assert is_quiet_hours(NOON.replace(hour=2))
    assert not is_quiet_hours(NOON.replace(hour=12))


def test_basic_push_fires_after_12h():
    assert decide_push(NOON, _ago(hours=13)) == "12h"


def test_no_push_when_recently_active():
    assert decide_push(NOON, _ago(hours=5)) is None


def test_mute_vetoes():
    assert decide_push(NOON, _ago(days=2), muted_until=NOON + timedelta(days=1)) is None


def test_expired_mute_does_not_veto():
    assert decide_push(NOON, _ago(days=2), muted_until=NOON - timedelta(hours=1)) == "12h"


def test_quiet_hours_vetoes():
    night = NOON.replace(hour=23)
    assert decide_push(night, night - timedelta(days=2)) is None


def test_crisis_within_24h_vetoes():
    assert decide_push(NOON, _ago(days=2), last_crisis_at=_ago(hours=5)) is None


def test_crisis_older_than_24h_allows():
    assert decide_push(NOON, _ago(days=2), last_crisis_at=_ago(hours=30)) == "12h"


def test_too_many_unanswered_vetoes():
    assert decide_push(NOON, _ago(days=2),
                       consecutive_unanswered=MAX_UNANSWERED) is None


def test_already_pushed_today_vetoes():
    times = {"12h": [NOON - timedelta(hours=3)]}  # same calendar day
    assert decide_push(NOON, _ago(days=2), tier_push_times=times) is None


def test_tier_frequency_limit_vetoes():
    # 12h tier limit is 3 per week; 3 prior sends this week (but not today) → veto
    times = {"12h": [NOON - timedelta(days=1), NOON - timedelta(days=2),
                     NOON - timedelta(days=3)]}
    assert decide_push(NOON, _ago(days=1, hours=1), tier_push_times=times) is None


def test_under_frequency_limit_allows():
    times = {"7d": [NOON - timedelta(days=20)]}  # 7d limit is 2 / 30 days
    assert decide_push(NOON, _ago(days=8), tier_push_times=times) == "7d"
