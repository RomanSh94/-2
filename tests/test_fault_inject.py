"""Crisis fault-injection hook (TEST INSTANCE ONLY) — proves it is INERT in prod
and that, when active, it drives the REAL delivery ladder correctly.

The hook lets a human force the first N crisis send attempts to fail, to verify the
rich→plain→minimal→P0 ladder live. Safety requirement: it can NEVER alter the real
delivery path in production. These tests pin that:
  - flag absent            → inert (n == 0)
  - flag set but prod DB    → inert (physical prod exclusion via database.DB)
  - flag set AND test DB    → active (n)
  - run through the REAL deliver_crisis: N failures drop the ladder exactly one rung
    per N, and the injected error str()-formats cleanly into the delivery log
    (no aiogram method=None hazard — that was the bug this test guards).
"""
import asyncio

import pytest

import bot
import crisis_delivery
import database


def _set(monkeypatch, *, instance, db, flag):
    """Set the three gate inputs (None → unset)."""
    if instance is None:
        monkeypatch.delenv("X20_INSTANCE", raising=False)
    else:
        monkeypatch.setenv("X20_INSTANCE", instance)
    monkeypatch.setattr(database, "DB", db)
    if flag is None:
        monkeypatch.delenv("CRISIS_FAULT_INJECT", raising=False)
    else:
        monkeypatch.setenv("CRISIS_FAULT_INJECT", flag)


def test_active_only_when_all_three_conditions_hold(monkeypatch):
    _set(monkeypatch, instance="test", db="x20_test.db", flag="2")
    assert bot._fault_inject_n() == 2


def test_inert_when_instance_marker_missing_or_wrong(monkeypatch):
    # flag + test DB present, but no X20_INSTANCE marker → inert (this is the hard
    # prod exclusion: a prod process accidentally on a test DB still has no marker).
    _set(monkeypatch, instance=None, db="x20_test.db", flag="2")
    assert bot._fault_inject_n() == 0
    _set(monkeypatch, instance="prod", db="x20_test.db", flag="2")   # wrong value
    assert bot._fault_inject_n() == 0


def test_inert_on_prod_db_even_with_marker_and_flag(monkeypatch):
    _set(monkeypatch, instance="test", db="x20.db", flag="2")
    assert bot._fault_inject_n() == 0


def test_inert_without_flag(monkeypatch):
    _set(monkeypatch, instance="test", db="x20_test.db", flag=None)
    assert bot._fault_inject_n() == 0


def test_inert_on_garbage_flag(monkeypatch):
    _set(monkeypatch, instance="test", db="x20_test.db", flag="nonsense")
    assert bot._fault_inject_n() == 0
    _set(monkeypatch, instance="test", db="x20_test.db", flag="-3")
    assert bot._fault_inject_n() == 0


async def _nosleep(_):
    return None


def _run_ladder(n):
    """Drive the REAL deliver_crisis with a fault-injected send; return
    (delivered_level, last_logged_error)."""
    logged = []

    async def log(eid, uid, kind, level, error):
        logged.append((level, error))

    async def real_send(text, **kw):
        return "ok"

    send = bot._faulty_send(real_send, n)

    async def go():
        return await crisis_delivery.deliver_crisis(
            send, text="T", kb="KB", lang="ru", uid=1, eid=2, kind="screen",
            log=log, on_total_failure=None, retries=2, backoff=0, sleep=_nosleep)

    level = asyncio.run(go())
    return level, (logged[-1][1] if logged else None)


def test_fault_injection_through_real_deliver_crisis():
    # N failures → exactly one rung down per N (rich→plain→minimal→none).
    assert _run_ladder(1)[0] == "plain"
    assert _run_ladder(2)[0] == "minimal"
    assert _run_ladder(3)[0] == "none"


def test_injected_error_str_formats_cleanly_in_log():
    # The bug this guards: the injected exception must str()-format inside
    # deliver_crisis's `f"{type(e).__name__}: {e}"` without raising. A plain
    # _InjectedSendFailure does; TelegramBadRequest(method=None) was the hazard.
    # n=1/2/3 all completing without an exception escaping deliver_crisis already
    # proves the formatting runs cleanly on every failing rung; n=3 also lets us
    # inspect the recorded string (only the final 'none' rung logs its error).
    level, last_err = _run_ladder(3)
    assert level == "none"
    assert last_err is not None
    assert "_InjectedSendFailure" in last_err
    assert "CRISIS_FAULT_INJECT" in last_err
