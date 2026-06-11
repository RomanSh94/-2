"""
X20 Silence Engine (Epic 3) — deterministic re-engagement push decisions.

Runs on the existing APScheduler (NO Celery/Redis — that full version is
deferred per MIGRATION_PLAN pending a deploy decision). The safety-critical part
is `decide_push()`: a pure function that, given a user's context and the current
time, returns which push tier to send — or None. All antispam rules from
MASTER_SPEC_v2 §8 live here so they can be unit-tested without a DB or Telegram.

Antispam (§8), every one of these can VETO a push:
  1. user muted
  2. quiet hours (default 22:00–08:00)         [NOTE: applied in UTC — see below]
  3. already pushed today
  4. within 24h of a crisis event
  5. 3+ consecutive unanswered pushes (stop until the user writes)
  6. tier frequency limit exceeded

TIMEZONE NOTE: the spec wants quiet hours in the *user's* timezone, but the
schema stores no per-user tz yet. Until that exists we apply quiet hours in UTC
and keep the window wide. This is a deliberate, flagged limitation.
"""
from datetime import datetime, timedelta, timezone

# Inactivity thresholds, longest first so we pick the most overdue tier.
TIERS = [
    ("30d", timedelta(days=30)),
    ("7d",  timedelta(days=7)),
    ("3d",  timedelta(days=3)),
    ("12h", timedelta(hours=12)),
]

# tier → (max sends, rolling window)
TIER_LIMITS = {
    "12h": (3, timedelta(weeks=1)),
    "3d":  (1, timedelta(weeks=1)),
    "7d":  (2, timedelta(days=30)),
    "30d": (1, timedelta(days=90)),
}

MAX_UNANSWERED = 3
QUIET_START = 22   # inclusive
QUIET_END = 8      # exclusive


def is_quiet_hours(now: datetime, start: int = QUIET_START, end: int = QUIET_END) -> bool:
    """True during the no-push window. Window wraps midnight (22:00–08:00)."""
    h = now.hour
    if start <= end:
        return start <= h < end
    return h >= start or h < end


def pick_tier(inactivity: timedelta) -> str | None:
    for tier, threshold in TIERS:
        if inactivity >= threshold:
            return tier
    return None


def _count_in_window(push_times: list, now: datetime, window: timedelta) -> int:
    cutoff = now - window
    return sum(1 for t in push_times if t >= cutoff)


def decide_push(now: datetime, last_activity: datetime, *,
                muted_until: datetime | None = None,
                last_crisis_at: datetime | None = None,
                consecutive_unanswered: int = 0,
                tier_push_times: dict | None = None,
                quiet_start: int = QUIET_START,
                quiet_end: int = QUIET_END) -> str | None:
    """Return the tier to push now, or None if any antispam rule vetoes it.

    `tier_push_times` maps tier -> list[datetime] of past sends (used for both
    the once-per-day rule and per-tier frequency limits).
    """
    tier_push_times = tier_push_times or {}

    # (1) muted
    if muted_until is not None and now < muted_until:
        return None

    # (2) quiet hours
    if is_quiet_hours(now, quiet_start, quiet_end):
        return None

    # (4) within 24h of a crisis event
    if last_crisis_at is not None and (now - last_crisis_at) < timedelta(hours=24):
        return None

    # (5) too many ignored pushes — back off until the user re-engages
    if consecutive_unanswered >= MAX_UNANSWERED:
        return None

    # (3) already pushed today (any tier)
    today = now.date()
    for times in tier_push_times.values():
        if any(t.date() == today for t in times):
            return None

    # pick the overdue tier
    tier = pick_tier(now - last_activity)
    if tier is None:
        return None

    # (6) per-tier frequency limit
    limit, window = TIER_LIMITS[tier]
    if _count_in_window(tier_push_times.get(tier, []), now, window) >= limit:
        return None

    return tier
