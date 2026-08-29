"""Push V1 (Round 5) §5 — real aiogram dispatcher-level tests for
bot.ActivityTouchMiddleware, the single centralized hook that refreshes
users.last_seen for ANY real inbound Message/CallbackQuery so the Silence
Engine's inactivity signal reflects real product use (lower-menu taps,
inline navigation, journal/questionnaire UI), not only ordinary free-text
turns through pipeline()/upsert_user.

Real dispatch (bot.dp.feed_update) is used throughout because this is
LITERALLY a dispatcher middleware -- a direct function call could never
prove it is actually wired into the real update flow, only that its body
is individually correct.
"""
import asyncio
import itertools
import time

import pytest

import access_control as ac
import bot
import database
from aiogram.client.session.base import BaseSession
from aiogram.methods.base import Response
from aiogram.types import Update

USER_ID = 909111
UNKNOWN_USER_ID = 909222

_id_counter = itertools.count(1)


class _StubSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent = []

    async def close(self):
        pass

    async def make_request(self, bot_, method, timeout=None):
        self.sent.append(method)
        returning = method.__returning__
        if returning is bool:
            data = {"ok": True, "result": True}
        else:
            data = {"ok": True, "result": {
                "message_id": next(_id_counter),
                "date": int(time.time()),
                "chat": {"id": USER_ID, "type": "private"},
                "text": getattr(method, "text", "") or "",
            }}
        resp = Response[returning].model_validate(data, context={"bot": bot_})
        return resp.result

    async def stream_content(self, *a, **kw):
        raise NotImplementedError


def _async(value=None):
    async def _f(*a, **kw):
        return value
    return _f


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "activity_touch.db"))
    asyncio.run(database.init_db())
    return database


@pytest.fixture(autouse=True)
def _common(monkeypatch, tmp_db):
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    monkeypatch.setattr(bot, "get_active_crisis", _async(None))
    monkeypatch.setattr(bot, "log_crisis_delivery", _async(None))
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", USER_ID)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})
    stub = _StubSession()
    monkeypatch.setattr(bot.bot, "session", stub)
    return stub


def _make_text_update(text, uid=USER_ID):
    return Update.model_validate({
        "update_id": next(_id_counter),
        "message": {
            "message_id": next(_id_counter),
            "date": int(time.time()),
            "chat": {"id": uid, "type": "private"},
            "from": {"id": uid, "is_bot": False, "first_name": "T"},
            "text": text,
        },
    }, context={"bot": bot.bot})


def _make_callback_update(data, uid=USER_ID):
    return Update.model_validate({
        "update_id": next(_id_counter),
        "callback_query": {
            "id": str(next(_id_counter)),
            "from": {"id": uid, "is_bot": False, "first_name": "T"},
            "message": {
                "message_id": next(_id_counter),
                "date": int(time.time()),
                "chat": {"id": uid, "type": "private"},
                "text": "placeholder",
            },
            "chat_instance": "1",
            "data": data,
        },
    }, context={"bot": bot.bot})


async def _seed_stale_user(uid=USER_ID, days_ago=5):
    await database.upsert_user(uid, "u", "U", "ru")
    async with database.aiosqlite.connect(database.DB) as db:
        await db.execute(
            "UPDATE users SET last_seen=datetime('now', ?) WHERE id=?",
            (f"-{days_ago} days", uid))
        await db.commit()


async def _row(uid=USER_ID):
    async with database.aiosqlite.connect(database.DB) as db:
        cur = await db.execute(
            "SELECT last_seen, message_count FROM users WHERE id=?", (uid,))
        return await cur.fetchone()


async def _seed_unanswered(uid=USER_ID, count=3):
    async with database.aiosqlite.connect(database.DB) as db:
        await db.execute(
            "INSERT INTO push_settings (user_id, consecutive_unanswered) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET consecutive_unanswered=excluded.consecutive_unanswered",
            (uid, count))
        await db.commit()


async def _consecutive_unanswered(uid=USER_ID):
    async with database.aiosqlite.connect(database.DB) as db:
        cur = await db.execute(
            "SELECT consecutive_unanswered FROM push_settings WHERE user_id=?", (uid,))
        row = await cur.fetchone()
    return row[0] if row else None


async def _push_settings_row_exists(uid=USER_ID):
    async with database.aiosqlite.connect(database.DB) as db:
        cur = await db.execute("SELECT 1 FROM push_settings WHERE user_id=?", (uid,))
        row = await cur.fetchone()
    return row is not None


def test_ordinary_free_text_refreshes_last_seen(monkeypatch):
    async def run():
        await _seed_stale_user()
        before = await _row()
        # Route to the generic catch-all -- stub out pipeline() so this is a
        # pure routing/middleware proof, not an LLM-path test.
        calls = []
        async def _fake_pipeline(message, text, state):
            calls.append(text)
        monkeypatch.setattr(bot, "pipeline", _fake_pipeline)
        await bot.dp.feed_update(bot.bot, _make_text_update("привет, как дела"))
        after = await _row()
        return before, after
    before, after = asyncio.run(run())
    assert after[0] != before[0]  # last_seen advanced


def test_lower_menu_message_refreshes_last_seen():
    async def run():
        await _seed_stale_user()
        before = await _row()
        await bot.dp.feed_update(bot.bot, _make_text_update("📝 Дневники"))
        after = await _row()
        return before, after
    before, after = asyncio.run(run())
    assert after[0] != before[0]


def test_inline_callback_refreshes_last_seen():
    async def run():
        await _seed_stale_user()
        before = await _row()
        await bot.dp.feed_update(bot.bot, _make_callback_update("menu:back"))
        after = await _row()
        return before, after
    before, after = asyncio.run(run())
    assert after[0] != before[0]


def test_journal_hub_callback_refreshes_last_seen():
    async def run():
        await _seed_stale_user()
        before = await _row()
        await bot.dp.feed_update(bot.bot, _make_callback_update("journals:hub"))
        after = await _row()
        return before, after
    before, after = asyncio.run(run())
    assert after[0] != before[0]


def test_questionnaire_callback_refreshes_last_seen():
    async def run():
        await _seed_stale_user()
        before = await _row()
        await bot.dp.feed_update(bot.bot, _make_callback_update("q:l"))
        after = await _row()
        return before, after
    before, after = asyncio.run(run())
    assert after[0] != before[0]


def test_unknown_user_gets_no_row_created_by_activity_touch():
    async def run():
        # No upsert_user call at all -- purely a callback tap from a user
        # the bot has never seen (e.g. a stray/forwarded callback).
        await bot.dp.feed_update(bot.bot, _make_callback_update("menu:back", uid=UNKNOWN_USER_ID))
        async with database.aiosqlite.connect(database.DB) as db:
            cur = await db.execute("SELECT COUNT(*) FROM users WHERE id=?", (UNKNOWN_USER_ID,))
            row = await cur.fetchone()
        return row[0]
    count = asyncio.run(run())
    assert count == 0  # touch_last_seen is UPDATE-only -- no row fabricated


def test_message_count_unchanged_by_activity_touch_alone():
    async def run():
        await _seed_stale_user()
        before = await _row()
        await bot.dp.feed_update(bot.bot, _make_callback_update("menu:back"))
        after = await _row()
        return before, after
    before, after = asyncio.run(run())
    assert after[1] == before[1]  # message_count column untouched


def test_no_new_content_stored_by_activity_touch():
    # touch_last_seen only ever executes a single-column UPDATE on `users`
    # -- verify no row was written anywhere that could hold callback/message
    # content (messages table stays empty for a pure navigation tap).
    async def run():
        await _seed_stale_user()
        await bot.dp.feed_update(bot.bot, _make_callback_update("menu:back"))
        async with database.aiosqlite.connect(database.DB) as db:
            cur = await db.execute("SELECT COUNT(*) FROM messages WHERE user_id=?", (USER_ID,))
            row = await cur.fetchone()
        return row[0]
    count = asyncio.run(run())
    assert count == 0


def test_touch_last_seen_never_inserts_directly():
    # Unit-level pin on the primitive itself: calling it for a user with no
    # row must be a pure no-op, never an INSERT.
    async def run():
        await database.touch_last_seen(UNKNOWN_USER_ID)
        async with database.aiosqlite.connect(database.DB) as db:
            cur = await db.execute("SELECT COUNT(*) FROM users WHERE id=?", (UNKNOWN_USER_ID,))
            row = await cur.fetchone()
        return row[0]
    assert asyncio.run(run()) == 0


# ── Correction #1, Blocker 4B: real activity resets consecutive_unanswered ──
def test_lower_menu_message_resets_consecutive_unanswered():
    async def run():
        await _seed_stale_user()
        await _seed_unanswered(count=3)
        await bot.dp.feed_update(bot.bot, _make_text_update("📝 Дневники"))
        return await _consecutive_unanswered()
    assert asyncio.run(run()) == 0


def test_inline_callback_resets_consecutive_unanswered():
    async def run():
        await _seed_stale_user()
        await _seed_unanswered(count=3)
        await bot.dp.feed_update(bot.bot, _make_callback_update("menu:back"))
        return await _consecutive_unanswered()
    assert asyncio.run(run()) == 0


def test_valid_push_v1_button_tap_resets_consecutive_unanswered(monkeypatch):
    # cb_push_action's callback_query update ALSO flows through
    # ActivityTouchMiddleware like any other callback -- a real button tap
    # is genuine re-engagement and must clear the ignored-push backoff,
    # exactly like any other real product activity. Drives the REAL send
    # path (scheduler._send_silence_pushes) through the SAME dispatcher
    # bot so the resulting token/chat_id/message_id are real, not
    # hand-constructed.
    import config
    import scheduler
    monkeypatch.setattr(config, "FIRST_USER_ONBOARDING_ENABLED", False)

    async def run():
        await _seed_stale_user()
        await database.save_message(
            USER_ID, "assistant", "prior reply", "open_chat", "ru",
            source=database.MessageSource.ASSISTANT_DELIVERED)
        await scheduler._send_silence_pushes(bot.bot)
        async with database.aiosqlite.connect(database.DB) as db:
            cur = await db.execute(
                "SELECT token, chat_id, source_message_id FROM push_action_bindings "
                "WHERE user_id=? AND action='push_continue' AND consumed_at IS NULL",
                (USER_ID,))
            token, chat_id, message_id = await cur.fetchone()
        await _seed_unanswered(count=3)
        # NOT _make_callback_update: that mints a FRESH message_id, but
        # consume_push_action_binding requires an EXACT chat_id/
        # source_message_id match -- using the real ones here is what
        # proves this is a genuinely VALID, consumable tap (not merely any
        # callback_query, which the middleware would touch regardless of
        # whether the inner handler's own logic ever succeeds).
        upd = Update.model_validate({
            "update_id": next(_id_counter),
            "callback_query": {
                "id": str(next(_id_counter)),
                "from": {"id": USER_ID, "is_bot": False, "first_name": "T"},
                "message": {
                    "message_id": message_id,
                    "date": int(time.time()),
                    "chat": {"id": chat_id, "type": "private"},
                    "text": "placeholder",
                },
                "chat_instance": "1",
                "data": f"pushbtn:{token}",
            },
        }, context={"bot": bot.bot})
        await bot.dp.feed_update(bot.bot, upd)
        consumed = await database.consume_push_action_binding(
            token, USER_ID, chat_id, message_id)
        return await _consecutive_unanswered(), consumed
    unanswered, consumed = asyncio.run(run())
    assert unanswered == 0
    # sanity: the tap really WAS a valid, successfully-consumed action --
    # otherwise consume_push_action_binding above would still succeed
    # (proving the FIRST tap never consumed it), which would mean this
    # test wasn't actually exercising cb_push_action's real success path.
    assert consumed is None  # already consumed by the real tap above


def test_unknown_user_activity_creates_no_users_row_blocker4b():
    async def run():
        await bot.dp.feed_update(bot.bot, _make_callback_update("menu:back", uid=UNKNOWN_USER_ID))
        async with database.aiosqlite.connect(database.DB) as db:
            cur = await db.execute("SELECT COUNT(*) FROM users WHERE id=?", (UNKNOWN_USER_ID,))
            row = await cur.fetchone()
        return row[0]
    assert asyncio.run(run()) == 0


def test_activity_does_not_create_push_settings_row_if_none_existed():
    async def run():
        await _seed_stale_user()
        assert not await _push_settings_row_exists()  # sanity: none yet
        await bot.dp.feed_update(bot.bot, _make_callback_update("menu:back"))
        return await _push_settings_row_exists()
    assert asyncio.run(run()) is False


def test_message_count_unchanged_by_backoff_reset_too():
    async def run():
        await _seed_stale_user()
        await _seed_unanswered(count=3)
        before = await _row()
        await bot.dp.feed_update(bot.bot, _make_callback_update("menu:back"))
        after = await _row()
        return before, after
    before, after = asyncio.run(run())
    assert after[1] == before[1]
