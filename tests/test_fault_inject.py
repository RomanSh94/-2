"""Crisis fault-injection hook (TEST INSTANCE ONLY) — proves it is INERT in prod.

The hook lets a human force the first N crisis send attempts to fail, to verify the
rich→plain→minimal→P0 ladder live. The safety requirement is that it can NEVER alter
the real delivery path in production. These tests pin that:
  - flag absent            → inert (n == 0)
  - flag set but prod DB    → inert (physical prod exclusion via database.DB)
  - flag set AND test DB    → active (n)
  - the wrapper fails exactly N times, then passes through to the real send.
"""
import asyncio

import pytest

import bot
import database
from aiogram.exceptions import TelegramBadRequest


def test_inert_when_flag_absent(monkeypatch):
    monkeypatch.setattr(database, "DB", "x20.db")          # prod DB
    monkeypatch.delenv("CRISIS_FAULT_INJECT", raising=False)
    assert bot._fault_inject_n() == 0


def test_inert_in_prod_even_if_flag_set(monkeypatch):
    # The key safety property: setting the flag in PROD must do nothing, because the
    # prod process runs on x20.db, not the test DB.
    monkeypatch.setattr(database, "DB", "x20.db")
    monkeypatch.setenv("CRISIS_FAULT_INJECT", "2")
    assert bot._fault_inject_n() == 0


def test_active_only_on_test_db_with_flag(monkeypatch):
    monkeypatch.setattr(database, "DB", "x20_test.db")
    monkeypatch.setenv("CRISIS_FAULT_INJECT", "2")
    assert bot._fault_inject_n() == 2
    # garbage flag value → safe 0, never crashes
    monkeypatch.setenv("CRISIS_FAULT_INJECT", "nonsense")
    assert bot._fault_inject_n() == 0
    monkeypatch.setenv("CRISIS_FAULT_INJECT", "-3")
    assert bot._fault_inject_n() == 0


def test_faulty_send_fails_n_then_passes_through():
    calls = []

    async def real_send(text, **kw):
        calls.append(text)
        return "delivered"

    wrapped = bot._faulty_send(real_send, 2)

    async def go():
        results = []
        for i in range(4):
            try:
                results.append(await wrapped(f"msg{i}"))
            except TelegramBadRequest as e:
                results.append(f"FAIL:{type(e).__name__}")
        return results

    results = asyncio.run(go())
    # first 2 raise, real_send never called for them; 3rd and 4th pass through
    assert results[0].startswith("FAIL") and results[1].startswith("FAIL")
    assert results[2] == "delivered" and results[3] == "delivered"
    assert calls == ["msg2", "msg3"]      # real send only reached after N failures
