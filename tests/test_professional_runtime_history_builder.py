"""PROFESSIONAL RUNTIME HISTORY BUILDER V1 -- database.py read primitive.

Tests database.get_professional_conversation_history_rows against a real
temp SQLite DB (not mocks) -- the point is proving the actual SQL filter
(provenance + current-row boundary + ownership), not that some function got
called. The pure builder half of this slice (professional_turn_
conversation_context.build_conversation_context_from_history_rows) is
tested in tests/test_professional_turn_conversation_context.py, alongside
the rest of that module's existing test suite.

This slice does NOT wire anything into bot.py and does NOT activate
Professional free-text runtime -- see
tests/test_professional_turn_conversation_context.py::
test_bot_py_does_not_runtime_wire_professional_free_text_pipeline and
::test_bot_py_remains_unmodified_by_this_slice.
"""
import asyncio
import inspect

import aiosqlite
import pytest

import database
from professional_turn_conversation_context import (
    MAX_TURN_CONTENT_CHARS,
    build_conversation_context_from_history_rows,
)

UID = 810000
OTHER_UID = 810001


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    return database.DB


async def _save(uid, role, content, source):
    return await database.save_message(uid, role, content, source=source)


def test_empty_history_returns_empty_list(db):
    async def go():
        current_id = await _save(UID, "user", "current turn", database.MessageSource.USER_AUTHORED)
        return await database.get_professional_conversation_history_rows(UID, current_id)
    assert asyncio.run(go()) == []


def test_current_source_message_row_id_must_be_positive(db):
    async def go(bad_id):
        await database.get_professional_conversation_history_rows(UID, bad_id)
    with pytest.raises(ValueError):
        asyncio.run(go(0))
    with pytest.raises(ValueError):
        asyncio.run(go(-1))


def test_only_rows_with_id_less_than_current_are_returned(db):
    async def go():
        m1 = await _save(UID, "user", "first", database.MessageSource.USER_AUTHORED)
        m2 = await _save(UID, "assistant", "second", database.MessageSource.ASSISTANT_DELIVERED)
        current = await _save(UID, "user", "current", database.MessageSource.USER_AUTHORED)
        rows = await database.get_professional_conversation_history_rows(UID, current)
        return [r[0] for r in rows], m1, m2
    ids, m1, m2 = asyncio.run(go())
    assert ids == [m1, m2]


def test_current_row_itself_excluded(db):
    async def go():
        current = await _save(UID, "user", "current turn text", database.MessageSource.USER_AUTHORED)
        rows = await database.get_professional_conversation_history_rows(UID, current)
        return current, [r[0] for r in rows]
    current, ids = asyncio.run(go())
    assert current not in ids


def test_future_rows_excluded(db):
    async def go():
        current = await _save(UID, "user", "current", database.MessageSource.USER_AUTHORED)
        future = await _save(UID, "assistant", "reply to current", database.MessageSource.ASSISTANT_DELIVERED)
        rows = await database.get_professional_conversation_history_rows(UID, current)
        return future, [r[0] for r in rows]
    future, ids = asyncio.run(go())
    assert future not in ids


def test_user_authored_included(db):
    async def go():
        m1 = await _save(UID, "user", "hi", database.MessageSource.USER_AUTHORED)
        current = await _save(UID, "user", "current", database.MessageSource.USER_AUTHORED)
        rows = await database.get_professional_conversation_history_rows(UID, current)
        return m1, rows
    m1, rows = asyncio.run(go())
    assert any(r[0] == m1 and r[3] == "USER_AUTHORED" for r in rows)


def test_assistant_delivered_included(db):
    async def go():
        m1 = await _save(UID, "assistant", "hello", database.MessageSource.ASSISTANT_DELIVERED)
        current = await _save(UID, "user", "current", database.MessageSource.USER_AUTHORED)
        rows = await database.get_professional_conversation_history_rows(UID, current)
        return m1, rows
    m1, rows = asyncio.run(go())
    assert any(r[0] == m1 and r[3] == "ASSISTANT_DELIVERED" for r in rows)


def test_synthetic_ui_excluded(db):
    async def go():
        synth = await _save(UID, "user", "elaborate", database.MessageSource.SYNTHETIC_UI)
        current = await _save(UID, "user", "current", database.MessageSource.USER_AUTHORED)
        rows = await database.get_professional_conversation_history_rows(UID, current)
        return synth, [r[0] for r in rows]
    synth, ids = asyncio.run(go())
    assert synth not in ids


def test_null_provenance_excluded(db):
    async def go():
        async with aiosqlite.connect(database.DB) as conn:
            cur = await conn.execute(
                "INSERT INTO messages (user_id, role, content) VALUES (?,?,?)",
                (UID, "user", "legacy pre-provenance row"))
            await conn.commit()
            legacy_id = cur.lastrowid
        current = await _save(UID, "user", "current", database.MessageSource.USER_AUTHORED)
        rows = await database.get_professional_conversation_history_rows(UID, current)
        return legacy_id, [r[0] for r in rows]
    legacy_id, ids = asyncio.run(go())
    assert legacy_id not in ids


def test_another_users_rows_excluded(db):
    async def go():
        other = await _save(OTHER_UID, "user", "someone else's message", database.MessageSource.USER_AUTHORED)
        current = await _save(UID, "user", "current", database.MessageSource.USER_AUTHORED)
        rows = await database.get_professional_conversation_history_rows(UID, current)
        return other, [r[0] for r in rows]
    other, ids = asyncio.run(go())
    assert other not in ids


def test_chronological_ascending_id_order(db):
    async def go():
        ids = []
        for i in range(5):
            ids.append(await _save(UID, "user" if i % 2 == 0 else "assistant", f"turn {i}",
                                   database.MessageSource.USER_AUTHORED if i % 2 == 0
                                   else database.MessageSource.ASSISTANT_DELIVERED))
        current = await _save(UID, "user", "current", database.MessageSource.USER_AUTHORED)
        rows = await database.get_professional_conversation_history_rows(UID, current)
        return ids, [r[0] for r in rows]
    ids, returned = asyncio.run(go())
    assert returned == ids
    assert returned == sorted(returned)


def test_identical_content_in_two_genuine_rows_preserved_twice(db):
    async def go():
        m1 = await _save(UID, "user", "мне грустно", database.MessageSource.USER_AUTHORED)
        m2 = await _save(UID, "user", "мне грустно", database.MessageSource.USER_AUTHORED)
        current = await _save(UID, "user", "current", database.MessageSource.USER_AUTHORED)
        rows = await database.get_professional_conversation_history_rows(UID, current)
        return m1, m2, rows
    m1, m2, rows = asyncio.run(go())
    matching = [r for r in rows if r[0] in (m1, m2)]
    assert len(matching) == 2
    assert all(r[2] == "мне грустно" for r in matching)


def test_no_summaries_table_read():
    """Structural: the function's own SQL never references the separate
    `summaries` table (that is memory.py's own rolling-summary mechanism,
    not a source of trusted Professional history)."""
    src = inspect.getsource(database.get_professional_conversation_history_rows)
    assert "summaries" not in src
    assert "get_latest_summary" not in src


def test_summarized_flag_does_not_gate_professional_history(db):
    """A row already folded into the legacy rolling summary (summarized=1)
    is still trusted, provenance-eligible history for Professional -- the
    two mechanisms are independent (see module docstring)."""
    async def go():
        m1 = await _save(UID, "user", "already summarized elsewhere", database.MessageSource.USER_AUTHORED)
        async with aiosqlite.connect(database.DB) as conn:
            await conn.execute("UPDATE messages SET summarized=1 WHERE id=?", (m1,))
            await conn.commit()
        current = await _save(UID, "user", "current", database.MessageSource.USER_AUTHORED)
        rows = await database.get_professional_conversation_history_rows(UID, current)
        return m1, [r[0] for r in rows]
    m1, ids = asyncio.run(go())
    assert m1 in ids


def test_no_write_or_mutation_occurs_during_history_read(db):
    async def go():
        m1 = await _save(UID, "user", "hi", database.MessageSource.USER_AUTHORED)
        current = await _save(UID, "user", "current", database.MessageSource.USER_AUTHORED)

        async def snapshot():
            async with aiosqlite.connect(database.DB) as conn:
                cur = await conn.execute(
                    "SELECT id, role, content, scenario, lang, source, summarized"
                    " FROM messages WHERE user_id=? ORDER BY id", (UID,))
                return await cur.fetchall()

        before = await snapshot()
        await database.get_professional_conversation_history_rows(UID, current)
        after = await snapshot()
        return before, after
    before, after = asyncio.run(go())
    assert before == after


def test_read_primitive_returns_id_role_content_source_fields(db):
    async def go():
        m1 = await _save(UID, "assistant", "hello there", database.MessageSource.ASSISTANT_DELIVERED)
        current = await _save(UID, "user", "current", database.MessageSource.USER_AUTHORED)
        rows = await database.get_professional_conversation_history_rows(UID, current)
        return m1, rows
    m1, rows = asyncio.run(go())
    assert rows == [(m1, "assistant", "hello there", "ASSISTANT_DELIVERED")]


def test_read_primitive_has_no_sql_limit_and_returns_every_eligible_row(db):
    """There is deliberately no SQL LIMIT at all (see the function's own
    docstring for why an earlier V1 draft's `LIMIT 200` was a correctness
    bug, fixed below): seed well past any plausible old default and confirm
    every eligible row comes back, unbounded."""
    async def go():
        ids = []
        for i in range(20):
            ids.append(await _save(UID, "user", f"turn {i}", database.MessageSource.USER_AUTHORED))
        current = await _save(UID, "user", "current", database.MessageSource.USER_AUTHORED)
        rows = await database.get_professional_conversation_history_rows(UID, current)
        return ids, rows
    ids, rows = asyncio.run(go())
    assert len(rows) == 20
    assert [r[0] for r in rows] == ids


def test_no_sql_limit_clause_in_read_primitive_source():
    src = inspect.getsource(database.get_professional_conversation_history_rows)
    query_text = src[src.index('"SELECT id'):src.index('return await cur.fetchall()')]
    assert "LIMIT" not in query_text.upper()


def test_older_valid_row_survives_past_the_removed_two_hundred_row_window(db):
    """Regression proof for the removed arbitrary SQL LIMIT (correctness
    fix): seeds one genuine old valid row, then 250 newer oversized
    (individually builder-ineligible) rows, then the current turn. Under
    the old `ORDER BY id DESC LIMIT 200` implementation, the raw SQL fetch
    would have returned only the newest 200 oversized rows -- the old
    valid row would never even have reached the pure builder, and the
    final context would have been wrongly empty. Exercises the real DB
    read primitive end-to-end (not the builder called directly with
    hand-picked rows), so this test would fail under the old
    implementation and passes after the fix."""
    async def go():
        old_valid_id = await _save(UID, "user", "genuine old valid message",
                                   database.MessageSource.USER_AUTHORED)
        oversized = "x" * (MAX_TURN_CONTENT_CHARS + 1)
        for _ in range(250):
            await _save(UID, "assistant", oversized, database.MessageSource.ASSISTANT_DELIVERED)
        current = await _save(UID, "user", "current", database.MessageSource.USER_AUTHORED)
        rows = await database.get_professional_conversation_history_rows(UID, current)
        context = build_conversation_context_from_history_rows(rows)
        return old_valid_id, rows, context
    old_valid_id, rows, context = asyncio.run(go())
    assert len(rows) == 251  # every prior row returned, unbounded
    assert len(context.turns) == 1
    assert context.turns[0].message_row_id == old_valid_id
    assert context.turns[0].content == "genuine old valid message"
