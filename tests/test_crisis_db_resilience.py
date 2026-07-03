"""PR 1B-1 checkpoint-2 Priority 0 — crisis delivery must not depend on
pre-delivery DB success.

Two proofs, in order:
  1. Against the REAL sqlite schema (not monkeypatched), log_crisis_event
     succeeds for a uid that never went through upsert_user — there is no
     FOREIGN KEY on crisis_events.user_id, so this is not an FK failure mode.
  2. Even so, trigger_crisis degrades safely (screen still delivered, no
     event-id-carrying buttons) if log_crisis_event raises for ANY reason —
     the general invariant is broader than the one FK-shaped cause.
"""
import asyncio
import types

import pytest

import bot
import database
import access_control as ac
from crisis_protocol import get_hotline


class FakeUser:
    def __init__(self, uid, username="user"):
        self.id = uid
        self.username = username


class FakeMessage:
    def __init__(self, user, text=""):
        self.from_user = user
        self.text = text
        self.chat = types.SimpleNamespace(id=user.id)
        self.answers = []

    async def answer(self, text, **kw):
        self.answers.append((text, kw))

    async def edit_reply_markup(self, **kw):
        pass


class FakeCallback:
    def __init__(self, user, message, data=""):
        self.from_user = user
        self.message = message
        self.data = data

    async def answer(self, *a, **kw):
        pass


def _async(value=None):
    async def _f(*a, **kw):
        return value
    return _f


def _risk():
    return {"score": 100, "level": "critical", "categories": ["suicide"]}


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    return database


# ── Option A: prove the real write succeeds for an unknown/never-upserted uid ──
def test_log_crisis_event_succeeds_for_uid_never_upserted(tmp_db):
    unknown_new_id = 987654321   # never touched upsert_user / users table at all
    async def go():
        return await tmp_db.log_crisis_event(
            unknown_new_id, "critical", 100, ["suicide"], "test text", "ru",
            admin_notified=False)
    eid = asyncio.run(go())
    assert eid is not None and eid > 0

    import sqlite3
    con = sqlite3.connect(tmp_db.DB)
    row = con.execute("SELECT user_id FROM crisis_events WHERE id=?", (eid,)).fetchone()
    con.close()
    assert row == (unknown_new_id,)


def test_no_foreign_key_from_crisis_events_to_users(tmp_db):
    import sqlite3
    con = sqlite3.connect(tmp_db.DB)
    fks = con.execute("PRAGMA foreign_key_list(crisis_events)").fetchall()
    pragma_on = con.execute("PRAGMA foreign_keys").fetchone()[0]
    con.close()
    assert fks == []            # no declared FK on crisis_events at all
    assert pragma_on == 0       # and enforcement isn't even turned on


# ── higher-level: UNKNOWN RED through the REAL trigger_crisis, REAL log_crisis_event ──
@pytest.fixture(autouse=True)
def _role_config(monkeypatch):
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "controlled_clinical_test")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})
    monkeypatch.setattr(ac, "ADMIN_USER_IDS", [999])
    monkeypatch.setattr(bot, "ADMIN_USER_IDS", [999])


def test_unknown_red_real_log_crisis_event_screen_delivered(tmp_db, monkeypatch):
    # Bind bot.py's DB-backed collaborator to the REAL database.log_crisis_event
    # (not a fake) so this test exercises the real write path end to end.
    monkeypatch.setattr(bot, "log_crisis_event", tmp_db.log_crisis_event)
    monkeypatch.setattr(bot, "log_crisis_delivery", tmp_db.log_crisis_delivery)

    user = FakeUser(555555)   # UNKNOWN, never upserted
    msg = FakeMessage(user)
    asyncio.run(bot.trigger_crisis(msg, user.id, user.username, "RED text", _risk(), "ru"))

    assert len(msg.answers) == 1
    assert get_hotline("ru")["primary"] in msg.answers[0][0]

    import sqlite3
    con = sqlite3.connect(tmp_db.DB)
    row = con.execute("SELECT user_id FROM crisis_events ORDER BY id DESC LIMIT 1").fetchone()
    con.close()
    assert row == (555555,)


# ── Option B: fallback when log_crisis_event raises for ANY reason ─────────────
def test_log_crisis_event_raises_still_delivers_degraded_screen(monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("disk full / lock timeout / whatever")
    monkeypatch.setattr(bot, "log_crisis_event", boom)

    calls = {"save_message": 0}
    async def spy_save(*a, **kw):
        calls["save_message"] += 1
    monkeypatch.setattr(bot, "save_message", spy_save)

    user = FakeUser(42)
    msg = FakeMessage(user)
    asyncio.run(bot.trigger_crisis(msg, user.id, user.username, "RED text", _risk(), "ru"))

    # The screen was still delivered, with the real hotline number.
    assert len(msg.answers) == 1
    assert get_hotline("ru")["primary"] in msg.answers[0][0]
    # Nothing keyed off eid ran (no event row exists to attach it to).
    assert calls["save_message"] == 0


def test_degraded_screen_has_no_buttons_at_all(monkeypatch):
    # checkpoint-2 round 3 item 1A: NOT crisis_keyboard's eid-less pair -- NO
    # buttons whatsoever. A stateful "crisis:*" callback is never safe to send
    # when we already know the DB just failed once for this event.
    async def boom(*a, **kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(bot, "log_crisis_event", boom)

    user = FakeUser(42)
    msg = FakeMessage(user)
    asyncio.run(bot.trigger_crisis(msg, user.id, user.username, "RED text", _risk(), "ru"))

    assert len(msg.answers) == 1
    _, kwargs = msg.answers[0]
    assert kwargs.get("reply_markup") is None


def test_normal_path_with_real_eid_still_uses_staged_buttons(monkeypatch):
    async def fake_log_crisis_event(*a, **kw):
        return 42
    monkeypatch.setattr(bot, "log_crisis_event", fake_log_crisis_event)

    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.trigger_crisis(msg, user.id, user.username, "RED text", _risk(), "ru"))

    _, kwargs = msg.answers[0]
    kb = kwargs.get("reply_markup")
    assert kb is not None
    found = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert any(cb.endswith(":42") for cb in found)   # staged buttons DO carry the real eid


# ── item 1B: cb_crisis defense-in-depth around the legacy 2-part resolve ───────
def test_legacy_two_part_callback_get_active_crisis_raises_is_safe(monkeypatch):
    async def boom(uid):
        raise RuntimeError("db locked")
    monkeypatch.setattr(bot, "get_active_crisis", boom)
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))

    calls = {"resolve_crisis": 0, "bump_crisis_stage": 0, "send_crisis": 0}

    async def spy_resolve(*a, **kw):
        calls["resolve_crisis"] += 1
    async def spy_bump(*a, **kw):
        calls["bump_crisis_stage"] += 1
    async def spy_send(*a, **kw):
        calls["send_crisis"] += 1
    monkeypatch.setattr(bot, "resolve_crisis", spy_resolve)
    monkeypatch.setattr(bot, "bump_crisis_stage", spy_bump)
    monkeypatch.setattr(bot, "send_crisis", spy_send)

    user = FakeUser(42)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="crisis:safe")   # legacy 2-part, no eid

    # Must not raise past cb_crisis.
    asyncio.run(bot.cb_crisis(cb))

    assert calls["resolve_crisis"] == 0
    assert calls["bump_crisis_stage"] == 0
    assert calls["send_crisis"] == 0


def test_legacy_two_part_callback_no_active_crisis_is_safe_noop(monkeypatch):
    resolve_calls = {"n": 0}

    async def fake_get_active_crisis(uid):
        resolve_calls["n"] += 1
        return None
    monkeypatch.setattr(bot, "get_active_crisis", fake_get_active_crisis)
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))

    user = FakeUser(42)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="crisis:safe")

    asyncio.run(bot.cb_crisis(cb))
    assert resolve_calls["n"] == 1   # cb_crisis tried to resolve, found nothing, no-op
    assert msg.answers == []          # nothing sent -- pure no-op


def test_normal_three_part_callback_still_works(monkeypatch):
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    monkeypatch.setattr(bot, "log_crisis_delivery", _async(None))
    resolved = {"n": 0}

    async def fake_resolve_crisis(eid):
        resolved["eid"] = eid
        resolved["n"] += 1

    async def fake_set_crisis_response(uid, resp):
        pass

    monkeypatch.setattr(bot, "resolve_crisis", fake_resolve_crisis)
    monkeypatch.setattr(bot, "set_crisis_response", fake_set_crisis_response)

    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="crisis:safe:42")   # new 3-part, carries eid

    asyncio.run(bot.cb_crisis(cb))
    assert resolved["n"] == 1
    assert resolved["eid"] == 42
    assert len(msg.answers) == 1
