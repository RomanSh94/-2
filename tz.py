"""
X20 — single source of truth for a user's effective UTC offset.

ALL four time-of-day sites call effective_tz() — the greeting (bot.cmd_start) and
the three scheduler jobs (daily check-ins, silence pushes' quiet hours, journal
reminders). Nothing reads tz_offset directly bypassing this, so changing the
default-by-language is a one-line change here.

Rule: if the user explicitly set a timezone (tz_set=1) → use tz_offset (0 is a
valid value, e.g. London). Otherwise default by language — ru → МСК (+3), else 0.
"""

DEFAULT_TZ_BY_LANG = {"ru": 3}   # МСК; any other language defaults to 0 (UTC)


def effective_tz(tz_offset, tz_set, lang: str = "ru") -> int:
    """Effective UTC offset (hours). tz_set distinguishes an explicit choice
    (including UTC+0) from "never set"."""
    if tz_set:
        return int(tz_offset or 0)
    return DEFAULT_TZ_BY_LANG.get(lang or "ru", 0)
