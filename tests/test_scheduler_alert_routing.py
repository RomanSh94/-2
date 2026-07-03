"""PR 1B-1 checkpoint-2 item 3 — scheduler._send_stage3_followups behavior
tests. Import-safety (access_control imported explicitly, no bot.py circular
import, local _minimal_reviewer_payload) was already verified manually via
`python -c "import scheduler"`; these tests cover the actual alert-routing
BEHAVIOR the checkpoint asked for."""
import asyncio

import pytest

import scheduler
import access_control as ac


@pytest.fixture(autouse=True)
def _role_config(monkeypatch):
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "controlled_clinical_test")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", {10})
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", {20})
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {10: [20]})
    monkeypatch.setattr(ac, "ADMIN_USER_IDS", [999])


class FakeBot:
    def __init__(self, raise_for=()):
        self.sent = []
        self._raise_for = set(raise_for)

    async def send_message(self, target_id, text):
        self.sent.append((target_id, text))
        if target_id in self._raise_for:
            raise RuntimeError("telegram send failed")


def _pending_for(uid, lang="ru"):
    async def fake(min_minutes=5):
        return [(7, uid, lang, [])]   # eid=7, no redos sent yet
    return fake


@pytest.fixture
def stubs(monkeypatch):
    marks = []

    async def fake_mark(event_id, tag):
        marks.append((event_id, tag))

    async def fake_send_crisis(bot, uid, text, kb, lang, eid, kind):
        return "plain"   # delivered, doesn't matter which level for these tests

    monkeypatch.setattr(scheduler, "mark_crisis_followup_sent", fake_mark)
    monkeypatch.setattr(scheduler, "_send_crisis", fake_send_crisis)
    return marks


def test_owner_stage3_followup_routes_owner_alert(monkeypatch, stubs):
    monkeypatch.setattr(scheduler, "get_stage3_pending", _pending_for(1))  # OWNER
    bot = FakeBot()
    asyncio.run(scheduler._send_stage3_followups(bot))

    assert len(bot.sent) == 1
    assert bot.sent[0][0] == 999   # ADMIN_USER_IDS
    assert stubs == [(7, "redo_1")]   # mark logic still ran


def test_mapped_tester_stage3_followup_minimal_reviewer_alert_only(monkeypatch, stubs):
    monkeypatch.setattr(scheduler, "get_stage3_pending", _pending_for(10))  # TESTER
    bot = FakeBot()
    asyncio.run(scheduler._send_stage3_followups(bot))

    assert len(bot.sent) == 1
    target_id, text = bot.sent[0]
    assert target_id == 20   # the mapped reviewer, not the owner admin id
    assert "tester_id: 10" in text
    assert "event_id: 7" in text
    # minimal payload only — no raw message text/username in it.
    assert "убить" not in text and "покончить" not in text
    assert stubs == [(7, "redo_1")]


def test_unknown_stage3_followup_sends_no_alert(monkeypatch, stubs):
    monkeypatch.setattr(scheduler, "get_stage3_pending", _pending_for(424242))  # UNKNOWN
    bot = FakeBot()
    asyncio.run(scheduler._send_stage3_followups(bot))

    assert bot.sent == []             # no owner alert, no reviewer alert
    assert stubs == [(7, "redo_1")]   # the redo-counter mark still ran (screen resend logic)


def test_reviewer_send_raising_does_not_break_mark_logic(monkeypatch, stubs):
    monkeypatch.setattr(scheduler, "get_stage3_pending", _pending_for(10))  # TESTER
    bot = FakeBot(raise_for={20})   # the reviewer send raises
    # Must not propagate -- the whole followup loop is already wrapped in a
    # try/except per-event in _send_stage3_followups.
    asyncio.run(scheduler._send_stage3_followups(bot))

    assert stubs == [(7, "redo_1")]   # mark_crisis_followup_sent still ran despite the raise
