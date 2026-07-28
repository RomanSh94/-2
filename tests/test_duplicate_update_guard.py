"""DuplicateUpdateGuard — drop exact-duplicate Telegram update_ids.

Investigation of a "one message -> a second, stale-context answer ~1 min
later" report found the bot ran polling with no update deduplication and no
drop_pending_updates, and logged nothing per update (so the two sends could
not be correlated after the fact). This guard drops only an EXACT duplicate
update_id -- a legitimate, monotonic update is never dropped -- and emits a
privacy-safe dispatch trace (update_id + decision only, never content/uid).
"""
import asyncio
import types

import pytest

import bot

run = asyncio.run


def _update(update_id):
    # Duck-typed stand-in: the guard only reads .update_id.
    return types.SimpleNamespace(update_id=update_id)


@pytest.fixture(autouse=True)
def _clean_seen():
    bot._seen_update_ids.clear()
    yield
    bot._seen_update_ids.clear()


def _guard():
    return bot.DuplicateUpdateGuard()


def test_first_time_update_is_handled():
    calls = []
    async def handler(event, data):
        calls.append(event.update_id)
        return "handled"
    result = run(_guard()(handler, _update(100), {}))
    assert result == "handled"
    assert calls == [100]


def test_exact_duplicate_update_is_dropped_and_handler_not_called():
    calls = []
    async def handler(event, data):
        calls.append(event.update_id)
        return "handled"
    g = _guard()
    run(g(handler, _update(100), {}))
    result = run(g(handler, _update(100), {}))   # same id again
    assert result is None                         # dropped
    assert calls == [100]                          # handler ran exactly once


def test_distinct_updates_all_pass():
    calls = []
    async def handler(event, data):
        calls.append(event.update_id)
    g = _guard()
    for uid in (1, 2, 3, 4):
        run(g(handler, _update(uid), {}))
    assert calls == [1, 2, 3, 4]


def test_seen_store_is_bounded_fifo(monkeypatch):
    monkeypatch.setattr(bot, "_SEEN_UPDATE_IDS_MAX", 3)
    async def handler(event, data):
        return None
    g = _guard()
    for uid in range(10):
        run(g(handler, _update(uid), {}))
    assert len(bot._seen_update_ids) <= 3
    # The oldest ids were evicted; the newest remain.
    assert 9 in bot._seen_update_ids
    assert 0 not in bot._seen_update_ids


def test_update_without_update_id_is_passed_through():
    # A malformed/duck event lacking update_id must not be dropped or crash.
    calls = []
    async def handler(event, data):
        calls.append(True)
        return "ok"
    result = run(_guard()(handler, types.SimpleNamespace(), {}))
    assert result == "ok"
    assert calls == [True]


def test_dispatch_trace_logs_metadata_only(monkeypatch):
    lines = []
    def fake_print(*a, **kw):
        text = " ".join(str(x) for x in a)
        if text.startswith("[dispatch]"):
            lines.append(text)
    monkeypatch.setattr("builtins.print", fake_print)
    async def handler(event, data):
        return None
    g = _guard()
    run(g(handler, _update(555), {}))
    run(g(handler, _update(555), {}))
    joined = " ".join(lines)
    assert "update_id=555 decision=accepted" in joined
    assert "decision=duplicate_dropped" in joined
    # Privacy: the trace carries no content, user id or username.
    for banned in ("content", "uid=", "username", "text="):
        assert banned not in joined


def test_guard_registered_as_update_outer_middleware():
    # It must sit at the UPDATE level (before message/callback middleware) so a
    # duplicate is dropped once, regardless of the inner event type.
    import inspect
    src = inspect.getsource(bot)
    assert "dp.update.outer_middleware(DuplicateUpdateGuard())" in src


def test_polling_drops_pending_updates_on_startup():
    import inspect
    src = inspect.getsource(bot.main)
    assert "drop_pending_updates=True" in src
