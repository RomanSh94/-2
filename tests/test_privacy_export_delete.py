"""PR 1A — export_all_personal_data / delete_all_personal_data on a test DB.

Covers: export includes registered data; delete removes CASCADE_DELETE tables,
anonymizes `users`, and explicitly RETAINS crisis tables (not a silent no-op);
influence_trace (the dependent-reference case in the task) is deleted alongside
its owner's data so no dangling trace row survives.
"""
import asyncio

import pytest

import database
import privacy_registry as pr


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    return database


async def _seed(db, uid=42):
    await db.upsert_user(uid, "alice", "Alice", "ru")
    await db.save_message(uid, "user", "hello", "open_chat", "ru", 0, "")
    await db.save_emotion_entry(uid, {"event": "x", "feeling": "sad", "intensity": 5})
    await db.log_crisis_event(uid, "critical", 100, ["suicide"], "excerpt", "ru", True)
    active = await db.get_active_crisis(uid)
    eid = active[0]
    await db.log_crisis_delivery(eid, uid, "screen", "rich", None)
    await db.log_influence_trace("rid-1", uid, [
        ("pattern_hypothesis", "pattern_1", "reply drew on pattern_hypothesis pattern_1"),
    ])
    return eid


def test_export_includes_registered_tables_with_real_data(tmp_db):
    async def go():
        await _seed(tmp_db)
        return await tmp_db.export_all_personal_data(42)
    out = asyncio.run(go())
    assert len(out["messages"]) == 1 and out["messages"][0]["content"] == "hello"
    assert len(out["emotion_journal_entries"]) == 1
    assert len(out["crisis_events"]) == 1                  # retained data still exported to owner
    assert len(out["influence_trace"]) == 1
    assert out["influence_trace"][0]["source_id"] == "pattern_1"
    # every registry table key is present in the export dict (even if empty)
    assert set(out.keys()) == set(pr.PRIVACY_REGISTRY.keys())


def test_delete_all_cascades_conversational_and_journal_data(tmp_db):
    async def go():
        await _seed(tmp_db)
        summary = await tmp_db.delete_all_personal_data(42)
        remaining_messages = await tmp_db.export_all_personal_data(42)
        return summary, remaining_messages
    summary, remaining = asyncio.run(go())
    assert summary["messages"] == 1                        # 1 row deleted
    assert summary["emotion_journal_entries"] == 1
    assert remaining["messages"] == []
    assert remaining["emotion_journal_entries"] == []


def test_delete_all_anonymizes_users_row_not_deletes_it(tmp_db):
    async def go():
        await _seed(tmp_db)
        await tmp_db.delete_all_personal_data(42)
        out = await tmp_db.export_all_personal_data(42)
        return out["users"]
    users = asyncio.run(go())
    assert len(users) == 1                                  # row still exists
    assert users[0]["username"] is None and users[0]["first_name"] is None


def test_delete_all_retains_crisis_tables_explicitly_not_silently(tmp_db):
    async def go():
        eid = await _seed(tmp_db)
        summary = await tmp_db.delete_all_personal_data(42)
        remaining = await tmp_db.export_all_personal_data(42)
        return eid, summary, remaining
    eid, summary, remaining = asyncio.run(go())
    # The summary must SAY it retained them, with a reason — not silently skip.
    assert summary["crisis_events"].startswith("RETAINED:")
    assert summary["crisis_message_delivery_log"].startswith("RETAINED:")
    assert "audit" in summary["crisis_events"].lower() or "safety" in summary["crisis_events"].lower()
    # And the data is ACTUALLY still there (retention is a real behavior, not just a label).
    assert len(remaining["crisis_events"]) == 1
    assert len(remaining["crisis_message_delivery_log"]) == 1
    assert remaining["crisis_message_delivery_log"][0]["event_id"] == eid


def test_delete_all_removes_influence_trace_avoiding_dangling_reference(tmp_db):
    # The dependent-reference case called out in the task: influence_trace.source_id
    # points at pattern_1 (a future-table concept that doesn't exist as a row here).
    # Deleting the trace ALONGSIDE the rest means no orphan row survives claiming an
    # influence from data that's gone.
    async def go():
        await _seed(tmp_db)
        summary = await tmp_db.delete_all_personal_data(42)
        remaining = await tmp_db.export_all_personal_data(42)
        return summary, remaining
    summary, remaining = asyncio.run(go())
    assert summary["influence_trace"] == 1
    assert remaining["influence_trace"] == []               # no dangling trace row left


def test_delete_all_is_scoped_to_the_requesting_user_only(tmp_db):
    async def go():
        await _seed(tmp_db, uid=42)
        await _seed(tmp_db, uid=43)
        await tmp_db.delete_all_personal_data(42)
        return await tmp_db.export_all_personal_data(43)
    other = asyncio.run(go())
    assert len(other["messages"]) == 1                      # user 43 untouched
    assert len(other["crisis_events"]) == 1
