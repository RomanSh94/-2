"""Crisis-loop fix — staged escalation. "💔 Мне всё ещё плохо" must ESCALATE,
not repeat. The DB stage is the idempotent loop guard.

Test phrases that mention self-harm stay ONLY in tests — never user-facing.
"""
import asyncio
import pytest

import crisis_protocol as cp
from crisis_protocol import crisis_screen, get_hotline, MAX_STAGE


# ── Stage screens: different texts, number always present ─────────────────────
def test_each_stage_text_is_distinct_and_has_number():
    texts = []
    for stage in range(0, MAX_STAGE + 1):
        text, kb = crisis_screen(stage, "ru", 7)
        assert get_hotline("ru")["primary"] in text or "112" in text
        texts.append(text)
    assert len(set(texts)) == len(texts)        # no two stages repeat


def test_callback_data_carries_event_id():
    _, kb = crisis_screen(0, "ru", 99)
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert all(d.startswith("crisis:") and d.endswith(":99") for d in datas)


def test_no_self_harm_methods_in_texts():
    banned = ["таблетк", "вены", "верёвк", "верёвка", "повес", "прыгн", "spring", "pills", "rope"]
    for stage in range(0, MAX_STAGE + 1):
        text, _ = crisis_screen(stage, "ru", 1)
        low = text.lower()
        assert not any(b in low for b in banned)


def test_stage_clamped():
    t_hi, _ = crisis_screen(99, "ru", 1)
    t3, _ = crisis_screen(3, "ru", 1)
    assert t_hi == t3


# ── DB: atomic monotonic stage + lifecycle ────────────────────────────────────
@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import database
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    return database


def _make_event(db, uid=1):
    async def go():
        await db.upsert_user(uid, "u", "U")
        return await db.log_crisis_event(uid, "RED", 100, ["suicide"], "excerpt", "ru")
    return asyncio.run(go())


def test_bump_is_atomic_and_monotonic(tmp_db):
    eid = _make_event(tmp_db)
    async def go():
        first = await tmp_db.bump_crisis_stage(eid, 1)     # 0→1 changes
        again = await tmp_db.bump_crisis_stage(eid, 1)     # stale → no-op
        down = await tmp_db.bump_crisis_stage(eid, 0)      # never lowers
        up = await tmp_db.bump_crisis_stage(eid, 3)        # 1→3 changes
        return first, again, down, up, await tmp_db.get_crisis_stage(eid)
    first, again, down, up, stage = asyncio.run(go())
    assert first is True and again is False and down is False and up is True
    assert stage == 3


def test_stale_button_is_noop(tmp_db):
    # After advancing to stage 2, a re-tap of an old "still@0→1" lands in an
    # already-passed stage → bump to 1 is a no-op (no loop, no duplicate).
    eid = _make_event(tmp_db)
    async def go():
        await tmp_db.bump_crisis_stage(eid, 2)
        return await tmp_db.bump_crisis_stage(eid, 1)
    assert asyncio.run(go()) is False


def test_safe_resolves_and_active_gate_releases(tmp_db):
    eid = _make_event(tmp_db)
    async def go():
        before = await tmp_db.get_active_crisis(1)
        await tmp_db.resolve_crisis(eid)
        after = await tmp_db.get_active_crisis(1)
        return before, after
    before, after = asyncio.run(go())
    assert before is not None and before[0] == eid
    assert after is None


def test_stage_survives_restart(tmp_db):
    # Stage lives in the DB, so a fresh read (≈ after a bot restart) keeps it.
    eid = _make_event(tmp_db)
    asyncio.run(tmp_db.bump_crisis_stage(eid, 2))
    assert asyncio.run(tmp_db.get_active_crisis(1))[1] == 2


def test_auto_resolve_expired(tmp_db):
    eid = _make_event(tmp_db)
    async def go():
        import aiosqlite
        async with aiosqlite.connect(tmp_db.DB) as db:
            await db.execute(
                "UPDATE crisis_events SET created_at=datetime('now','-8 days') WHERE id=?", (eid,))
            await db.commit()
        n = await tmp_db.auto_resolve_expired_crises(7)
        return n, await tmp_db.get_active_crisis(1)
    n, active = asyncio.run(go())
    assert n == 1 and active is None


def test_push_alert_excerpt_is_masked():
    from notifications import _mask_excerpt
    full = "хочу покончить со всем этим прямо сейчас, не могу больше так"
    assert full not in _mask_excerpt(full)
    assert len(_mask_excerpt(full)) <= 30


def test_hotline_fallback_when_config_missing(monkeypatch):
    import crisis_protocol as c
    monkeypatch.setattr(c, "_load_contacts", lambda: [])   # simulate missing/corrupt file
    assert c.get_hotline("ru")["primary"] == "8-800-2000-122"   # federal line survives
    assert c.get_hotline("en")["primary"] == "988"


def test_old_event_outside_window_does_not_gate(tmp_db):
    eid = _make_event(tmp_db)
    async def go():
        import aiosqlite
        async with aiosqlite.connect(tmp_db.DB) as db:
            await db.execute(
                "UPDATE crisis_events SET created_at=datetime('now','-30 hours') WHERE id=?", (eid,))
            await db.commit()
        return await tmp_db.get_active_crisis(1, within_hours=24)
    assert asyncio.run(go()) is None
