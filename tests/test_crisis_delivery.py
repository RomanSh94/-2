"""§6.1 Crisis Delivery Hardening — the rich→plain→minimal→alert ladder.

These test crisis_delivery.deliver_crisis in isolation: the send, the delivery-log
and the failure alert are injected fakes, so no aiogram Bot / network is involved.
A real prod TelegramNetworkError on a crisis send is what this ladder defends.
"""
import asyncio

import pytest

from aiogram.exceptions import TelegramNetworkError, TelegramBadRequest
from crisis_delivery import deliver_crisis, _minimal_text


async def _nosleep(_):
    return None


class Sender:
    """Records every send; raises per-call according to a script of exceptions
    (None = success). Captures text + kwargs so we can assert which level ran."""
    def __init__(self, script):
        self.script = list(script)
        self.calls = []          # list of (text, kwargs)

    async def __call__(self, text, **kwargs):
        self.calls.append((text, kwargs))
        exc = self.script.pop(0) if self.script else None
        if exc is not None:
            raise exc
        return "ok"


class Recorder:
    def __init__(self):
        self.logs = []           # (eid, uid, kind, level, error)
        self.alerts = []         # (uid, eid, kind, error)

    async def log(self, eid, uid, kind, level, error):
        self.logs.append((eid, uid, kind, level, error))

    async def alert(self, uid, eid, kind, error):
        self.alerts.append((uid, eid, kind, error))


def _run(send, *, kb="KB", rec=None, retries=2):
    rec = rec or Recorder()
    level = asyncio.run(deliver_crisis(
        send, text="CRISIS TEXT", kb=kb, lang="ru", uid=42, eid=7, kind="screen",
        log=rec.log, on_total_failure=rec.alert, retries=retries,
        backoff=0, sleep=_nosleep))
    return level, rec


# ── happy path + stop-ladder (no duplicates) ──────────────────────────────────
def test_rich_success_stops_ladder_no_duplicates():
    send = Sender([None])                       # rich succeeds first try
    level, rec = _run(send)
    assert level == "rich"
    assert len(send.calls) == 1                 # plain/minimal NEVER sent
    # rich carried the buttons.
    assert send.calls[0][1].get("reply_markup") == "KB"
    assert rec.logs == [(7, 42, "screen", "rich", None)]
    assert rec.alerts == []


def test_plain_success_stops_before_minimal():
    err = TelegramNetworkError(method=None, message="timeout")
    send = Sender([err, err, err, None])        # rich: 1+2 retries fail; plain ok
    level, rec = _run(send)
    assert level == "plain"
    # 3 rich attempts (1 + 2 retries) + 1 plain = 4; minimal NOT reached.
    assert len(send.calls) == 4
    assert send.calls[-1][1].get("reply_markup") is None    # plain has no buttons
    assert rec.logs[-1][3] == "plain"
    assert rec.alerts == []


# ── fallback to plain ─────────────────────────────────────────────────────────
def test_rich_network_fail_falls_back_to_plain():
    err = TelegramNetworkError(method=None, message="timeout")
    send = Sender([err, err, err, None])
    level, _ = _run(send)
    assert level == "plain"


# ── fallback all the way to minimal ───────────────────────────────────────────
def test_rich_and_plain_fail_delivers_minimal():
    err = TelegramNetworkError(method=None, message="timeout")
    # rich: 3 fail, plain: 3 fail, minimal: ok  → 7th call succeeds
    send = Sender([err] * 6 + [None])
    level, rec = _run(send)
    assert level == "minimal"
    assert send.calls[-1][0] == _minimal_text("ru")          # the plain hotline text
    assert "reply_markup" not in send.calls[-1][1]           # truly minimal
    assert rec.logs[-1][3] == "minimal"


# ── total failure → log 'none' + P0 alert ─────────────────────────────────────
def test_all_levels_fail_logs_none_and_alerts():
    err = TelegramNetworkError(method=None, message="timeout")
    send = Sender([err] * 9)                     # every level + retry fails
    level, rec = _run(send)
    assert level == "none"
    assert rec.logs[-1][3] == "none"
    assert rec.logs[-1][4] is not None                       # error captured
    assert rec.alerts == [(42, 7, "screen", rec.logs[-1][4])]


# ── transient retry: one blip then success at rich ────────────────────────────
def test_transient_error_is_retried_then_succeeds():
    err = TelegramNetworkError(method=None, message="timeout")
    send = Sender([err, None])                   # 1st rich fails, retry succeeds
    level, rec = _run(send)
    assert level == "rich"
    assert len(send.calls) == 2                  # retried once, no fallback
    assert rec.logs == [(7, 42, "screen", "rich", None)]


# ── bad-request is NOT retried — drops straight to the next level ─────────────
def test_bad_request_skips_retries_drops_to_plain():
    bad = TelegramBadRequest(method=None, message="can't parse entities")
    send = Sender([bad, None])                   # rich bad-request → plain ok
    level, _ = _run(send)
    assert level == "plain"
    assert len(send.calls) == 2                  # NO retry on rich (would be 3 calls)


# ── no keyboard → rich level is skipped, starts at plain ──────────────────────
def test_no_keyboard_starts_at_plain():
    send = Sender([None])
    rec = Recorder()
    level = asyncio.run(deliver_crisis(
        send, text="CALL TEXT", kb=None, lang="ru", uid=42, eid=7, kind="call_text",
        log=rec.log, on_total_failure=rec.alert, retries=2, backoff=0, sleep=_nosleep))
    assert level == "plain"
    assert len(send.calls) == 1
    assert "reply_markup" not in send.calls[0][1]


# ── DB: the delivery-log table is created by init_db and the writer inserts ────
@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import database
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    return database


def test_delivery_log_roundtrip(tmp_db):
    import sqlite3
    asyncio.run(tmp_db.log_crisis_delivery(7, 42, "screen", "plain", "Net: timeout"))
    asyncio.run(tmp_db.log_crisis_delivery(7, 42, "screen", "rich", None))
    con = sqlite3.connect(tmp_db.DB)
    rows = con.execute("SELECT event_id,user_id,kind,level_delivered,telegram_error "
                       "FROM crisis_message_delivery_log ORDER BY id").fetchall()
    con.close()
    assert rows[0] == (7, 42, "screen", "plain", "Net: timeout")
    assert rows[1] == (7, 42, "screen", "rich", None)
