"""Per-user timezone: greeting + reminders in local time, with the
default-by-language rule and the not-set/UTC+0 distinction.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from tz import effective_tz
from humanization import _time_bucket, pick_greeting, _GREETINGS
from silence_engine import decide_push


# ── effective_tz: the single source of truth ──────────────────────────────────
def test_unset_ru_defaults_to_msk():
    assert effective_tz(0, 0, "ru") == 3          # not set, ru → МСК
    assert effective_tz(5, 0, "ru") == 3          # offset ignored while unset


def test_unset_other_lang_defaults_utc():
    assert effective_tz(0, 0, "en") == 0


def test_explicit_utc0_is_not_overridden():
    # The key trap: an explicit UTC+0 (London) must survive the ru default.
    assert effective_tz(0, 1, "ru") == 0


def test_explicit_offset_used():
    assert effective_tz(5, 1, "ru") == 5
    assert effective_tz(-1, 1, "en") == -1


# ── greeting uses LOCAL hour ───────────────────────────────────────────────────
def test_local_hour_gives_day_not_night():
    # 08:00 UTC for an unset ru user → 11:00 local → "morning"/"day", not "night".
    utc_hour = 8
    local_hour = (utc_hour + effective_tz(0, 0, "ru")) % 24   # 11
    assert _time_bucket(local_hour) in ("morning", "day")
    assert pick_greeting(False, local_hour, "ru") in _GREETINGS["ru"][_time_bucket(local_hour)]


def test_night_greeting_softened():
    # No presumptuous "не спится" assertion anymore.
    assert all("не спится" not in g.lower() for g in _GREETINGS["ru"]["night"])


# ── quiet hours move WITH the tz shift (no 3am-local pings) ────────────────────
def test_default_shift_respects_quiet_hours():
    now = datetime(2026, 6, 8, 20, 0, tzinfo=timezone.utc)   # 20:00 UTC
    long_ago = now - timedelta(days=2)
    # Without tz: 20:00 is allowed.
    assert decide_push(now, long_ago) == "12h"
    # ru-default user: local = 23:00 → quiet → vetoed (the +3 shift cannot push
    # a notification into someone's night).
    local_now = now + timedelta(hours=effective_tz(0, 0, "ru"))
    assert decide_push(now, long_ago, quiet_now=local_now) is None


# ── check-in fires at the user's LOCAL hour ───────────────────────────────────
def test_checkin_local_hour_formula():
    # ru user (unset → +3) with checkin_hour 10 fires when (utc+3)%24 == 10 → utc 7.
    tz = effective_tz(0, 0, "ru")
    assert (7 + tz) % 24 == 10


# ── DB: set marks tz_set, getter roundtrips ───────────────────────────────────
@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import database
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    return database


def test_set_tz_marks_tz_set(tmp_db):
    async def go():
        await tmp_db.upsert_user(1, "u", "U", "ru")
        before = await tmp_db.get_user_tz(1)          # (0, 0, 'ru') — unset
        await tmp_db.set_tz_offset(1, 0)              # explicit UTC+0
        after = await tmp_db.get_user_tz(1)
        return before, after
    before, after = asyncio.run(go())
    assert before[1] == 0                             # tz_set False before
    assert after == (0, 1, "ru")                      # explicit 0, tz_set True
    assert effective_tz(*after) == 0                  # not overridden to +3
