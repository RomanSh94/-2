"""Questionnaire Core PR #1 — DB schema + privacy registry coverage.

Proves: tables exist, are registered in PRIVACY_REGISTRY, are covered by the
existing registry-generic export/delete/preview functions with no code
changes needed there, are NOT retained (CASCADE_DELETE, unlike crisis
tables), and store stable tokens rather than raw display text.
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


def test_questionnaire_tables_created_by_init_db(tmp_db):
    import sqlite3
    con = sqlite3.connect(tmp_db.DB)
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    con.close()
    assert "questionnaire_sessions" in names
    assert "questionnaire_responses" in names


def test_questionnaire_tables_registered_in_privacy_registry():
    assert "questionnaire_sessions" in pr.PRIVACY_REGISTRY
    assert "questionnaire_responses" in pr.PRIVACY_REGISTRY


def test_questionnaire_tables_are_not_retained():
    assert pr.PRIVACY_REGISTRY["questionnaire_sessions"].delete_policy == "CASCADE_DELETE"
    assert pr.PRIVACY_REGISTRY["questionnaire_responses"].delete_policy == "CASCADE_DELETE"


async def _seed(db, uid=42):
    await db.upsert_user(uid, "alice", "Alice", "ru")
    session_id = await db.start_questionnaire_session(uid, "synthetic_demo_v1", "1")
    await db.record_questionnaire_response(uid, session_id, "synthetic_demo_v1", "energy", "mid", "2")
    return session_id


def test_privacy_export_includes_questionnaire_sessions_and_responses(tmp_db):
    async def go():
        await _seed(tmp_db)
        return await tmp_db.export_all_personal_data(42)
    out = asyncio.run(go())
    assert len(out["questionnaire_sessions"]) == 1
    assert out["questionnaire_sessions"][0]["questionnaire_id"] == "synthetic_demo_v1"
    assert len(out["questionnaire_responses"]) == 1
    assert out["questionnaire_responses"][0]["answer_id"] == "mid"


def test_privacy_delete_removes_questionnaire_sessions_and_responses(tmp_db):
    async def go():
        await _seed(tmp_db)
        summary = await tmp_db.delete_all_personal_data(42)
        remaining = await tmp_db.export_all_personal_data(42)
        return summary, remaining
    summary, remaining = asyncio.run(go())
    assert summary["questionnaire_sessions"] == 1
    assert summary["questionnaire_responses"] == 1
    assert remaining["questionnaire_sessions"] == []
    assert remaining["questionnaire_responses"] == []


def test_privacy_delete_preview_counts_questionnaire_rows_without_raw_content(tmp_db):
    async def go():
        await _seed(tmp_db)
        return await tmp_db.preview_delete_all_personal_data(42)
    preview = asyncio.run(go())
    assert preview["questionnaire_sessions"]["row_count"] == 1
    assert preview["questionnaire_sessions"]["policy"] == "CASCADE_DELETE"
    assert preview["questionnaire_sessions"]["retain_reason"] is None
    assert preview["questionnaire_responses"]["row_count"] == 1
    # exactly {policy, row_count, retain_reason} -- no raw content keys.
    assert set(preview["questionnaire_responses"].keys()) == {"policy", "row_count", "retain_reason"}


def test_existing_crisis_retain_behavior_unchanged_after_addition(tmp_db):
    async def go():
        await tmp_db.upsert_user(1, "u", "U", "ru")
        await tmp_db.log_crisis_event(1, "critical", 100, ["suicide"], "text", "ru")
        return await tmp_db.delete_all_personal_data(1)
    summary = asyncio.run(go())
    assert summary["crisis_events"].startswith("RETAINED:")


def test_questionnaire_response_stores_token_not_label(tmp_db):
    # answer_value must be the stable token ("2"), never the display label
    # ("Средняя") -- proves the storage layer, independent of bot.py wiring.
    async def go():
        await _seed(tmp_db)
        return await tmp_db.export_all_personal_data(42)
    out = asyncio.run(go())
    row = out["questionnaire_responses"][0]
    assert row["answer_value"] == "2"
    assert row["answer_value"] != "Средняя"
    assert "Средняя" not in repr(row)


def test_active_and_completed_session_lifecycle(tmp_db):
    async def go():
        await tmp_db.upsert_user(1, "u", "U", "ru")
        sid = await tmp_db.start_questionnaire_session(1, "synthetic_demo_v1", "1")
        active = await tmp_db.get_active_questionnaire_session(1)
        await tmp_db.advance_questionnaire_session(sid, 1)
        mid = await tmp_db.get_questionnaire_session(sid)
        await tmp_db.complete_questionnaire_session(sid)
        after = await tmp_db.get_active_questionnaire_session(1)
        final = await tmp_db.get_questionnaire_session(sid)
        return active, mid, after, final
    active, mid, after, final = asyncio.run(go())
    assert active["id"] == mid["id"]
    assert mid["current_index"] == 1
    assert after is None                      # no longer active
    assert final["status"] == "completed"


def test_cancel_marks_session_cancelled(tmp_db):
    async def go():
        await tmp_db.upsert_user(1, "u", "U", "ru")
        sid = await tmp_db.start_questionnaire_session(1, "synthetic_demo_v1", "1")
        await tmp_db.cancel_questionnaire_session(sid)
        return await tmp_db.get_questionnaire_session(sid)
    session = asyncio.run(go())
    assert session["status"] == "cancelled"
