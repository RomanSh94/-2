"""Stale-response race — a superseded ordinary answer must not be delivered.

aiogram runs updates as concurrent tasks (handle_as_tasks=True), so two turns
from the SAME user can run pipeline() at once. If an earlier turn's LLM call is
slow and a later turn finishes first, the earlier turn would otherwise deliver
its now-stale answer AFTER the newer one -- "two answers, the second referencing
older context", the actual mechanism behind the owner report. The per-user
generation guard suppresses the older ordinary answer. Deterministic safety
replies (crisis / dependency) are never suppressed.

No real Telegram or OpenAI call: the LLM completion is a mock whose timing we
control with an asyncio.Event.
"""
import asyncio
import types

import pytest

import access_control as ac
import bot
import config
import database

run = asyncio.run


class FakeUser:
    def __init__(self, uid):
        self.id = uid
        self.username = "u"
        self.first_name = "U"


class FakeMessage:
    def __init__(self, user, text):
        self.from_user = user
        self.text = text
        self.chat = types.SimpleNamespace(id=user.id, type="private")
        self.message_id = 1
        self.answers = []

    async def answer(self, text, **kw):
        self.answers.append(text)

    async def answer_voice(self, *a, **kw):
        pass


class FakeFSM:
    def __init__(self):
        self._d = {}

    async def get_data(self):
        return dict(self._d)

    async def update_data(self, **kw):
        self._d.update(kw)

    async def set_state(self, state):
        pass


def _async(value=None):
    async def _f(*a, **kw):
        return value
    return _f


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "race.db"))
    run(database.init_db())
    return database


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", False)
    monkeypatch.setattr(config, "EMOTIONAL_REACTIONS_ENABLED", False)
    monkeypatch.setattr(config, "FIRST_USER_ONBOARDING_ENABLED", False)
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})
    bot._user_generation.clear()
    # Neutralize network + slow bits, keep pipeline logic real.
    monkeypatch.setattr(bot.bot, "send_chat_action", _async(None))
    monkeypatch.setattr(bot, "typing_delay", lambda a: 0)
    monkeypatch.setattr(bot, "get_emotional_trajectory", _async(types.SimpleNamespace(
        trend="stable", hopelessness_streak=0, yellow_plus_streak=0, messages_analyzed=0)))
    monkeypatch.setattr(bot, "maybe_summarize", _async(None))
    monkeypatch.setattr(bot, "build_context", _async(("", [])))
    monkeypatch.setattr(bot, "maybe_update_profile", _async(None))
    monkeypatch.setattr(bot, "get_user_message_count", _async(1))
    monkeypatch.setattr(bot, "check_sudden_improvement", _async(False))
    monkeypatch.setattr(bot.dependency_monitor, "record_message", _async(None))
    monkeypatch.setattr(bot.dependency_monitor, "assess", _async(None))


def _gated_llm(monkeypatch):
    """First completion blocks on an Event (the 'slow' turn); the rest return
    immediately. Returns the gate so the test releases the slow turn."""
    gate = asyncio.Event()
    calls = {"n": 0}

    async def create(*a, **kw):
        idx = calls["n"]
        calls["n"] += 1
        if idx == 0:
            await gate.wait()
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=f"answer{idx}"))])
    monkeypatch.setattr(bot.client.chat.completions, "create", create)
    return gate


def test_slow_A_then_fast_B_only_B_is_delivered(tmp_db, monkeypatch):
    run(database.upsert_user(1, "u", "U"))
    gate = _gated_llm(monkeypatch)
    mA = FakeMessage(FakeUser(1), "первое сообщение")
    mB = FakeMessage(FakeUser(1), "второе сообщение")

    async def scenario():
        tA = asyncio.create_task(bot.pipeline(mA, "первое сообщение", FakeFSM()))
        await asyncio.sleep(0.02)              # A reaches the blocked LLM
        tB = asyncio.create_task(bot.pipeline(mB, "второе сообщение", FakeFSM()))
        await tB                                # B finishes fast and delivers
        gate.set()                              # release the slow A
        await tA
    run(scenario())

    assert mB.answers == ["answer1"]            # newer turn delivered
    assert mA.answers == []                     # stale older turn suppressed


async def _message_roles(uid):
    """(id, role) rows for uid in insertion order -- METADATA ONLY, never the
    content column, so no message text is read. Read via aiosqlite inside the
    test's own event loop to avoid sync/async DB-connection contention."""
    import aiosqlite
    async with aiosqlite.connect(database.DB) as db:
        cur = await db.execute(
            "SELECT id, role FROM messages WHERE user_id=? ORDER BY id", (uid,))
        rows = await cur.fetchall()
    return [role for _id, role in rows]


def test_persistence_order_slow_A_then_fast_B_keeps_arrival_order(tmp_db, monkeypatch):
    # P1 regression: memory loads recent messages by autoincrement id
    # (get_recent_messages ORDER BY id DESC). A slow older turn A must not have
    # its user row land AFTER a newer fast turn B, or A would look like the
    # newest active context. Required order (by id): user_A, user_B, assistant_B
    # -- and NO assistant_A (the stale answer is never persisted).
    run(database.upsert_user(1, "u", "U"))
    gate = _gated_llm(monkeypatch)
    mA = FakeMessage(FakeUser(1), "first arrives, slow")
    mB = FakeMessage(FakeUser(1), "second arrives, fast")

    async def scenario():
        tA = asyncio.create_task(bot.pipeline(mA, "first arrives, slow", FakeFSM()))
        await asyncio.sleep(0.02)               # A saves its user row, then blocks in LLM
        tB = asyncio.create_task(bot.pipeline(mB, "second arrives, fast", FakeFSM()))
        await tB
        gate.set()
        await tA
        return await _message_roles(1)
    roles = run(scenario())

    # user_A (arrival order) then user_B then assistant_B; assistant_A absent.
    assert roles == ["user", "user", "assistant"]
    # The LAST user row is B's, not stale A's -- next memory load sees B as
    # the newest user turn.
    assert mB.answers == ["answer1"] and mA.answers == []


def test_stale_turn_persists_no_assistant_row(tmp_db, monkeypatch):
    run(database.upsert_user(1, "u", "U"))
    gate = _gated_llm(monkeypatch)
    mA = FakeMessage(FakeUser(1), "slow")
    mB = FakeMessage(FakeUser(1), "fast")

    async def scenario():
        tA = asyncio.create_task(bot.pipeline(mA, "slow", FakeFSM()))
        await asyncio.sleep(0.02)
        await bot.pipeline(mB, "fast", FakeFSM())
        gate.set()
        await tA
        return await _message_roles(1)
    roles = run(scenario())

    assert roles.count("assistant") == 1        # only B's assistant, never A's


def test_non_overlapping_two_messages_both_get_answered(tmp_db, monkeypatch):
    # The guard must ONLY suppress an answer superseded WHILE in flight. Two
    # sequential, non-overlapping messages must each still get their answer.
    run(database.upsert_user(1, "u", "U"))
    async def create(*a, **kw):
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content="ok"))])
    monkeypatch.setattr(bot.client.chat.completions, "create", create)
    m1 = FakeMessage(FakeUser(1), "one")
    m2 = FakeMessage(FakeUser(1), "two")
    run(bot.pipeline(m1, "one", FakeFSM()))
    run(bot.pipeline(m2, "two", FakeFSM()))
    assert m1.answers == ["ok"]
    assert m2.answers == ["ok"]


def test_single_ordinary_message_gets_exactly_one_answer(tmp_db, monkeypatch):
    run(database.upsert_user(1, "u", "U"))
    async def create(*a, **kw):
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content="ok"))])
    monkeypatch.setattr(bot.client.chat.completions, "create", create)
    m = FakeMessage(FakeUser(1), "hi")
    run(bot.pipeline(m, "hi", FakeFSM()))
    assert m.answers == ["ok"]


def test_slow_A_then_start_suppresses_A(tmp_db, monkeypatch):
    # Race B: an in-flight ordinary answer must not appear after /start.
    run(database.upsert_user(1, "u", "U"))
    gate = _gated_llm(monkeypatch)
    # cmd_start does a lot of I/O; stub the parts that would hit network/LLM,
    # but let it run its real generation bump.
    monkeypatch.setattr(bot, "_send_mood_entry", _async(None))
    monkeypatch.setattr(bot, "ensure_full_access_or_closed_test", _async(True))
    mA = FakeMessage(FakeUser(1), "медленное сообщение")
    start_msg = FakeMessage(FakeUser(1), "/start")

    async def scenario():
        tA = asyncio.create_task(bot.pipeline(mA, "медленное сообщение", FakeFSM()))
        await asyncio.sleep(0.02)
        await bot.cmd_start(start_msg)          # bumps the generation
        gate.set()
        await tA
    run(scenario())
    assert mA.answers == []                     # in-flight ordinary answer suppressed


def test_slow_A_then_crisis_delivers_crisis_and_suppresses_A(tmp_db, monkeypatch):
    # Race C: crisis is delivered; the older ordinary answer is suppressed;
    # crisis is NEVER suppressed by the stale guard.
    run(database.upsert_user(1, "u", "U"))
    gate = _gated_llm(monkeypatch)
    crisis_sent = []
    async def spy_send_crisis(answer_fn, text, kb, lang, uid, eid, kind):
        crisis_sent.append(text)
        await answer_fn(text)
    monkeypatch.setattr(bot, "send_crisis", spy_send_crisis)
    monkeypatch.setattr(bot, "trigger_crisis",
                        lambda message, uid, username, text, risk, lang:
                        spy_send_crisis(message.answer, "CRISIS TEXT", None, lang, uid, 1, "screen"))

    mA = FakeMessage(FakeUser(1), "обычное сообщение")
    mCrisis = FakeMessage(FakeUser(1), "я хочу покончить с собой")

    async def scenario():
        tA = asyncio.create_task(bot.pipeline(mA, "обычное сообщение", FakeFSM()))
        await asyncio.sleep(0.02)
        await bot.pipeline(mCrisis, "я хочу покончить с собой", FakeFSM())  # crisis bumps gen
        gate.set()
        await tA
    run(scenario())

    assert crisis_sent and crisis_sent[0]        # crisis delivered
    assert mCrisis.answers == ["CRISIS TEXT"]
    assert mA.answers == []                       # stale ordinary suppressed


def test_slow_user1_does_not_suppress_user2(tmp_db, monkeypatch):
    # Race D: a slow request from user 1 must not affect user 2.
    run(database.upsert_user(1, "u", "U"))
    run(database.upsert_user(2, "u", "U"))
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    gate = _gated_llm(monkeypatch)

    m1 = FakeMessage(FakeUser(1), "user one slow")
    # user 2 must also be authorized; personal_use grants only the owner, so
    # give user 2 its own owner id via a second-owner re-patch mid-flight is
    # not possible with a single OWNER_USER_ID -- instead grant explicit access.
    run(database.grant_user_access(2))
    m2 = FakeMessage(FakeUser(2), "user two")

    async def scenario():
        t1 = asyncio.create_task(bot.pipeline(m1, "user one slow", FakeFSM()))
        await asyncio.sleep(0.02)
        await bot.pipeline(m2, "user two", FakeFSM())   # different user
        gate.set()
        await t1
    run(scenario())

    assert m2.answers == ["answer1"]              # user 2 answered normally
    assert m1.answers == ["answer0"]              # user 1 NOT suppressed by user 2


def test_generation_store_is_bounded(monkeypatch):
    monkeypatch.setattr(bot, "_USER_GEN_MAX", 4)
    for uid in range(20):
        bot._bump_user_generation(uid)
    assert len(bot._user_generation) <= 4


def test_dispatch_logs_are_privacy_safe_by_construction():
    # The runtime suppression itself is proven behaviorally elsewhere (the
    # older turn's .answers stays empty). Here we prove the [dispatch] log
    # lines can carry no identity/content by construction: every one is a
    # fixed f-string of cid / stage / source / decision / update_id only.
    import inspect
    src = (inspect.getsource(bot.pipeline)
           + inspect.getsource(bot.DuplicateUpdateGuard)
           + inspect.getsource(bot._dispatch_log))
    for line in src.splitlines():
        if "_dispatch_log(" in line or "print(" in line:
            for banned in ("user_text", "username", "message.text", "u.id",
                           "first_name", "content"):
                assert banned not in line, line
    # The correlation id is random hex, never derived from a user identifier.
    a, b = bot._new_correlation_id(), bot._new_correlation_id()
    assert a != b
    assert len(a) == 8 and all(c in "0123456789abcdef" for c in a)
