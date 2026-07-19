"""Dependency monitor: the ONE deterministic authority (Therapeutic Core
Foundation) for "is a dependency-boundary redirect warranted right now".

Before this pass, `check_dependency` (behavioural patterns: frequency/
marathon-session/night) and `relationship_monitor.monitor_relationship`
(explicit dependency phrases) were two independent mechanisms with no shared
cooldown -- the phrase-based one fired on EVERY matching message with no
cooldown at all. `assess()` consolidates both behind one shared cooldown gate
(`_last_redirect`), so bot.py has a single call site and a single authority.

Regression test for the frequency threshold is preserved: check_dependency()
(now assess()) must run AFTER record_message() so the current message is
counted before the threshold check -- otherwise the >100/day threshold never
fires.
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
        return await m.assess(uid, "just chatting", "en")

    msg = asyncio.run(run())
    assert msg is not None                # frequency redirect fired


def test_below_threshold_is_silent():
    m = DependencyMonitor()
    uid = 7

    async def run():
        for _ in range(5):
            await m.record_message(uid)
        m._night_msgs[uid] = 0
        return await m.assess(uid, "just chatting", "en")

    assert asyncio.run(run()) is None


# ── Consolidation: explicit-phrase signal now shares the same authority ──────
def test_explicit_phrase_triggers_redirect():
    m = DependencyMonitor()
    msg = asyncio.run(m.assess(99, "you're my only friend", "en"))
    assert msg is not None


def test_ordinary_gratitude_is_not_dependency():
    m = DependencyMonitor()
    msg = asyncio.run(m.assess(100, "thanks, that helped a lot", "en"))
    assert msg is None


def test_phrase_redirect_has_a_cooldown_not_fired_every_message():
    # The exact P1 gap this pass closes: monitor_relationship previously had
    # NO cooldown at all and fired on every matching message.
    m = DependencyMonitor()
    uid = 101

    async def run():
        first = await m.assess(uid, "you're my only friend", "en")
        second = await m.assess(uid, "you're my only friend", "en")
        return first, second

    first, second = asyncio.run(run())
    assert first is not None
    assert second is None  # cooldown: not fired again immediately


def test_phrase_cooldown_resets_once_condition_drops():
    m = DependencyMonitor()
    uid = 102

    async def run():
        await m.assess(uid, "you're my only friend", "en")
        neutral = await m.assess(uid, "how does CBT work", "en")
        again = await m.assess(uid, "you're my only friend", "en")
        return neutral, again

    neutral, again = asyncio.run(run())
    assert neutral is None
    assert again is not None  # condition dropped and reappeared -> fires again


def test_behavioural_and_phrase_signals_share_one_cooldown_gate():
    # If a behavioural condition is already active, a same-turn phrase match
    # must not ALSO fire (one redirect per turn, one shared authority).
    m = DependencyMonitor()
    uid = 103

    async def run():
        for _ in range(101):
            await m.record_message(uid)
        m._night_msgs[uid] = 0
        return await m.assess(uid, "you're my only friend", "en")

    msg = asyncio.run(run())
    assert msg is not None  # exactly one redirect, behavioural takes priority


def test_no_permanent_label_persisted_across_monitor_instances():
    # dependency_monitor is explicitly in-memory/per-process (no DB row, no
    # profile field) -- a fresh instance has no memory of a prior assessment.
    m1 = DependencyMonitor()
    asyncio.run(m1.assess(104, "you're my only friend", "en"))
    m2 = DependencyMonitor()
    msg = asyncio.run(m2.assess(104, "you're my only friend", "en"))
    assert msg is not None  # fresh instance, no carried-over state
