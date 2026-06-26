"""§6.1 — crisis FOLLOW-UP delivery semantics (scheduler._send_crisis_followups).

Guards the seam: a partially-delivered follow-up (gentle intro arrives, screen
does not) must NOT be marked done. Screen goes FIRST; the intro only follows a
delivered screen; an undelivered screen retries within a bounded cap.
"""
import asyncio
from datetime import datetime, timezone, timedelta

import pytest

import scheduler


def _event(sent, stage=1, hours_ago=2):
    created = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime(
        "%Y-%m-%d %H:%M:%S")
    # (eid, uid, lang, created_at, stage, sent) — elapsed ~2h → only the "1h" tag.
    return (7, 42, "ru", created, stage, sent)


@pytest.fixture
def harness(monkeypatch):
    marked = []
    sends = []                       # ordered list of kinds actually sent

    async def fake_resolve(*a, **k):
        return None

    async def fake_mark(eid, tag):
        marked.append(tag)

    monkeypatch.setattr(scheduler, "auto_resolve_expired_crises", fake_resolve)
    monkeypatch.setattr(scheduler, "mark_crisis_followup_sent", fake_mark)

    def install(events, screen_level="rich"):
        async def fake_events():
            return events

        async def fake_send(bot, uid, text, kb, lang, eid, kind):
            sends.append(kind)
            # The SCREEN's level is scripted; intro always "delivers".
            return screen_level if kind == "followup" else "rich"

        monkeypatch.setattr(scheduler, "get_active_crisis_events", fake_events)
        monkeypatch.setattr(scheduler, "_send_crisis", fake_send)

    return install, marked, sends


# ── screen none → NOT marked, intro NOT sent, retry tag recorded ──────────────
def test_screen_none_does_not_mark_and_skips_intro(harness):
    install, marked, sends = harness
    install([_event(sent=[])], screen_level="none")
    asyncio.run(scheduler._send_crisis_followups(None))
    assert sends == ["followup"]              # ONLY the screen was attempted
    assert "followup_intro" not in sends      # intro never sent on a failed screen
    assert marked == ["1h_retry1"]            # retry recorded, real tag NOT marked
    assert "1h" not in marked


# ── screen delivered → screen first, then intro, then mark real tag ───────────
def test_screen_ok_sends_intro_after_and_marks(harness):
    install, marked, sends = harness
    install([_event(sent=[])], screen_level="rich")
    asyncio.run(scheduler._send_crisis_followups(None))
    assert sends == ["followup", "followup_intro"]   # screen FIRST, intro second
    assert marked == ["1h"]


# ── screen delivered only at minimal still counts as delivered ────────────────
def test_screen_minimal_counts_as_delivered(harness):
    install, marked, sends = harness
    install([_event(sent=[])], screen_level="minimal")
    asyncio.run(scheduler._send_crisis_followups(None))
    assert sends == ["followup", "followup_intro"]
    assert marked == ["1h"]


# ── retry cap exhausted → mark the real tag, stop retrying ────────────────────
def test_retry_cap_exhausted_marks_real_tag(harness):
    install, marked, sends = harness
    install([_event(sent=["1h_retry1", "1h_retry2", "1h_retry3"])],
            screen_level="none")
    asyncio.run(scheduler._send_crisis_followups(None))
    assert sends == ["followup"]              # screen attempted once more
    assert "followup_intro" not in sends
    assert marked == ["1h"]                   # cap spent → real tag marked, no retry4
