"""Shared test setup.

bot.py instantiates aiogram Bot(token=...) and AsyncOpenAI(api_key=...) at import
time, so the handler-level tests need dummy credentials present BEFORE bot is
imported. These are throwaway values assembled at runtime (not a real token); no
network call happens at construction.
"""
import os

import pytest

# Built from parts so the file contains no literal token-shaped string.
_DUMMY_TOKEN = "123456789" + ":" + ("A" * 35)

os.environ.setdefault("BOT_TOKEN", _DUMMY_TOKEN)
os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")
os.environ.setdefault("ADMIN_PASSWORD", "test")
os.environ.setdefault("ADMIN_USER_IDS", "")


@pytest.fixture(autouse=True)
def _reset_dependency_monitor():
    """Root cause of a real, deterministic (not merely 'flaky') full-suite
    failure found 2026-07-30: bot.dependency_monitor is a single MODULE-
    LEVEL instance with real in-memory, wall-clock-timestamped state
    (message counts per user_id), created ONCE at bot.py import time and
    never reset between tests. Almost every test in this suite drives
    bot.pipeline() for the SAME uid (1) -- so across a large enough test
    run, this ONE shared instance's internal 24h message counter for uid=1
    silently crosses dependency_monitor._MAX_DAY_MSGS (100), and the very
    NEXT pipeline() call for that uid gets a dependency redirect (a fixed
    message, zero LLM calls, no exception) instead of its expected ordinary
    reply -- entirely unrelated to whatever that specific test is checking.
    This is exactly what caused
    test_new_topic_message_cancels_pending_flow_silently to intermittently
    fail in full-suite runs (confirmed by direct reproduction, not
    speculation): a test-isolation gap in a production singleton that must
    persist across real user turns but must never leak across separate
    tests. A fresh instance per test closes this class of failure
    deterministically, without touching production code or weakening any
    assertion."""
    import sys
    if "bot" in sys.modules:
        import dependency_monitor as _dm
        sys.modules["bot"].dependency_monitor = _dm.DependencyMonitor()
    yield
    if "bot" in sys.modules:
        import dependency_monitor as _dm
        sys.modules["bot"].dependency_monitor = _dm.DependencyMonitor()
