"""
X20 Dependency Monitor

Tracks unhealthy usage patterns (high frequency, marathon sessions, late-night messages).
Distinct from relationship_monitor.py, which reacts to explicit dependency *phrases*.
This module reacts to behavioural *patterns* across time.

Triggers:
  - > 100 messages in any 24-hour window  → high-frequency redirect
  - unbroken session > 3 hours            → marathon-session redirect
  - > 5 messages between 22:00–06:00 UTC  → late-night redirect

Storage: in-memory (sufficient for single-instance bot; no DB required).
"""
from __future__ import annotations

import datetime
import time
from collections import defaultdict, deque
from typing import Optional

# ── Thresholds ────────────────────────────────────────────────────────────────
_DAY_SEC   = 86_400
_3_HOURS   = 3 * 3600
_NIGHT_START = 22   # UTC hour (inclusive)
_NIGHT_END   = 6    # UTC hour (exclusive)
_MAX_DAY_MSGS  = 100
_MAX_NIGHT_MSGS = 5

# ── Redirect texts ────────────────────────────────────────────────────────────
_HIGH_FREQ_RU  = ("Похоже, мы много общаемся сегодня. "
                  "Подумай — есть ли рядом человек, с которым можно поговорить вживую?")
_MARATHON_RU   = ("Мы уже долго в диалоге. "
                  "Часто полезно прерваться: вода, окно, движение. Я никуда не денусь.")
_NIGHT_RU      = ("Сейчас ночь. "
                  "Сон сейчас полезнее любого разговора. Если что — я буду здесь утром.")

_HIGH_FREQ_EN  = ("Looks like we've been talking a lot today. "
                  "Is there someone nearby you could speak with in person?")
_MARATHON_EN   = ("We've been at this for a while. "
                  "A short break — water, window, movement — often helps. I'll be here.")
_NIGHT_EN      = ("It's night time. "
                  "Sleep will do more good than any conversation right now. I'll be here in the morning.")


class DependencyMonitor:
    def __init__(self) -> None:
        # timestamps of recent messages per user (last 24 h window)
        self._timestamps: dict[int, deque] = defaultdict(deque)
        # start timestamp of the current unbroken session per user
        self._session_start: dict[int, float] = {}
        # count of night messages in the current night window (reset at 06:00)
        self._night_msgs: dict[int, int] = defaultdict(int)
        # last night-window date string (YYYY-MM-DD) to detect roll-over
        self._night_date: dict[int, str] = {}
        # set of users who already got a specific redirect this check cycle
        # (avoid spamming the same message every single message)
        self._last_redirect: dict[int, str] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    async def record_message(self, user_id: int) -> None:
        """Register an inbound message from user."""
        now = time.time()
        ts  = self._timestamps[user_id]

        # trim messages older than 24 h
        cutoff = now - _DAY_SEC
        while ts and ts[0] < cutoff:
            ts.popleft()

        # reset session start if gap since LAST recorded message > 30 min
        # (check before appending so ts[-1] is the previous message)
        if not ts or (now - ts[-1]) > 1800:
            self._session_start[user_id] = now

        ts.append(now)

        # track night messages
        now_utc  = datetime.datetime.now(datetime.timezone.utc)
        utc_hour = now_utc.hour
        today    = now_utc.strftime("%Y-%m-%d")
        is_night = utc_hour >= _NIGHT_START or utc_hour < _NIGHT_END
        if is_night:
            if self._night_date.get(user_id) != today:
                self._night_msgs[user_id] = 0
                self._night_date[user_id] = today
            self._night_msgs[user_id] += 1
        else:
            self._night_msgs[user_id] = 0
            self._night_date[user_id] = today

    async def check_dependency(self, user_id: int, lang: str = "ru") -> Optional[str]:
        """
        Returns a redirect message string if a dependency pattern is detected, else None.
        Each distinct trigger fires exactly once; the gate resets only when the
        condition drops below threshold (not just because we already notified).
        """
        ts  = self._timestamps[user_id]
        now = time.time()

        # Determine which conditions are currently active (in priority order)
        night_active    = self._night_msgs.get(user_id, 0) > _MAX_NIGHT_MSGS
        freq_active     = len(ts) > _MAX_DAY_MSGS
        session_start   = self._session_start.get(user_id)
        marathon_active = bool(session_start and (now - session_start) > _3_HOURS)

        last = self._last_redirect.get(user_id)

        if night_active:
            if last != "night":
                self._last_redirect[user_id] = "night"
                return _NIGHT_EN if lang == "en" else _NIGHT_RU
            return None  # already notified, condition still active — stay silent

        if freq_active:
            if last != "freq":
                self._last_redirect[user_id] = "freq"
                return _HIGH_FREQ_EN if lang == "en" else _HIGH_FREQ_RU
            return None

        if marathon_active:
            if last != "marathon":
                self._last_redirect[user_id] = "marathon"
                return _MARATHON_EN if lang == "en" else _MARATHON_RU
            return None

        # No conditions active — reset gate so next threshold crossing fires again
        self._last_redirect.pop(user_id, None)
        return None
