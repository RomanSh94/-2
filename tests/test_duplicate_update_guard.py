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


def test_update_id_zero_is_tracked_not_treated_as_missing():
    # update_id==0 is a valid id; `is not None` must include it so a repeated
    # 0 is still deduplicated rather than waved through as "no id".
    calls = []
    async def handler(event, data):
        calls.append(event.update_id)
    g = _guard()
    run(g(handler, _update(0), {}))
    run(g(handler, _update(0), {}))   # duplicate 0
    assert calls == [0]                # handled once
    assert 0 in bot._seen_update_ids


def test_concurrent_same_update_id_handled_once():
    # Two tasks feeding the SAME update_id concurrently: the check-and-add is a
    # read-modify-write with no await between, so under asyncio's single thread
    # exactly one passes.
    ran = []
    async def handler(event, data):
        await asyncio.sleep(0)         # yield, to interleave the two tasks
        ran.append(event.update_id)
    g = _guard()
    async def scenario():
        await asyncio.gather(
            g(handler, _update(77), {}),
            g(handler, _update(77), {}),
        )
    run(scenario())
    assert ran == [77]                 # handler body ran exactly once


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


def test_normal_startup_does_not_drop_pending_updates():
    # In aiogram 3.7 drop_pending_updates is NOT a start_polling parameter --
    # passing it would silently fall into **kwargs (a misleading no-op) and the
    # only real drop path, bot.delete_webhook(drop_pending_updates=True),
    # discards messages users sent during a restart. A normal restart must
    # never drop a user's message, so neither appears.
    import inspect
    src = inspect.getsource(bot.main)
    # Assert on the actual call, not comment prose: start_polling is invoked
    # with no extra arguments, so nothing requests a pending-update drop.
    assert "start_polling(bot)" in src
    assert "start_polling(bot," not in src


def test_command_registration_failure_does_not_block_startup(monkeypatch, caplog):
    events = []

    class FakeBot:
        async def set_my_commands(self, commands):
            events.append(("commands", [c.command for c in commands]))
            raise bot.TelegramNetworkError(method=None, message="sensitive detail")

    class FakeDispatcher:
        async def start_polling(self, polling_bot):
            events.append(("polling", polling_bot))

    class FakeScheduler:
        def start(self):
            events.append(("scheduler-start", None))

    async def fake_init_db():
        events.append(("init-db", None))

    fake_bot = FakeBot()
    monkeypatch.setattr(bot, "bot", fake_bot)
    monkeypatch.setattr(bot, "dp", FakeDispatcher())
    monkeypatch.setattr(bot, "init_db", fake_init_db)
    monkeypatch.setattr(bot, "start_dashboard",
                        lambda: events.append(("dashboard", None)))
    monkeypatch.setattr(
        bot, "setup_scheduler",
        lambda scheduler_bot: (
            events.append(("scheduler-setup", scheduler_bot)) or FakeScheduler()))

    with caplog.at_level("WARNING"):
        run(bot.main())

    assert events == [
        ("init-db", None),
        ("commands", ["start", "help"]),
        ("dashboard", None),
        ("scheduler-setup", fake_bot),
        ("scheduler-start", None),
        ("polling", fake_bot),
    ]
    assert "TelegramNetworkError" in caplog.text
    assert "sensitive detail" not in caplog.text


def test_visible_command_list_is_trimmed_to_start_and_help(monkeypatch):
    """Public-beta: the persistent lower ReplyKeyboard is the one primary
    navigation surface, so Telegram's own command list/autocomplete must not
    duplicate it with /menu, /questionnaire, /journal, /format -- those
    handlers stay registered and callable manually (see tests/test_navigation.py),
    this only trims what Telegram's UI shows. A default (language-agnostic)
    list is set, then a RU-localized one via language_code="ru"."""
    calls = []

    class FakeBot:
        async def set_my_commands(self, commands, language_code=None):
            calls.append((language_code, [(c.command, c.description) for c in commands]))

    class FakeDispatcher:
        async def start_polling(self, polling_bot):
            pass

    class FakeScheduler:
        def start(self):
            pass

    async def fake_init_db():
        pass

    monkeypatch.setattr(bot, "bot", FakeBot())
    monkeypatch.setattr(bot, "dp", FakeDispatcher())
    monkeypatch.setattr(bot, "init_db", fake_init_db)
    monkeypatch.setattr(bot, "start_dashboard", lambda: None)
    monkeypatch.setattr(bot, "setup_scheduler", lambda scheduler_bot: FakeScheduler())

    run(bot.main())

    assert len(calls) == 2
    default_lang, default_commands = calls[0]
    ru_lang, ru_commands = calls[1]
    assert default_lang is None
    assert [c for c, _ in default_commands] == ["start", "help"]
    assert ru_lang == "ru"
    assert [c for c, _ in ru_commands] == ["start", "help"]
    # Legacy handlers must still be registered and callable manually.
    for cmd in ("menu", "questionnaire", "journal", "format"):
        assert cmd not in [c for c, _ in default_commands]
        assert cmd not in [c for c, _ in ru_commands]
