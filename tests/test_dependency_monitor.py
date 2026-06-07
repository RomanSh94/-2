"""Regression test for the dependency-monitor frequency threshold.

Bug fixed in bot.pipeline: check_dependency() ran BEFORE record_message(),
so the current message wasn't counted and the >100/day threshold never
fired. This test pins the monitor contract the corrected order relies on:
once 101 messages are recorded, the high-frequency redirect triggers.
"""
import asyncio

from dependency_monitor import DependencyMonitor


def test_high_frequency_triggers_after_threshold():
    m = DependencyMonitor()
    uid = 42

    async def run():
        for _ in range(101):              # > _MAX_DAY_MSGS (100)
            await m.record_message(uid)
        m._night_msgs[uid] = 0            # isolate freq from the night path
        return await m.check_dependency(uid, "en")

    msg = asyncio.run(run())
    assert msg is not None                # frequency redirect fired


def test_below_threshold_is_silent():
    m = DependencyMonitor()
    uid = 7

    async def run():
        for _ in range(5):
            await m.record_message(uid)
        m._night_msgs[uid] = 0
        return await m.check_dependency(uid, "en")

    assert asyncio.run(run()) is None
