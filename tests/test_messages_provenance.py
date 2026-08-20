"""MESSAGES PROVENANCE V1 — `messages.source` closed vocabulary.

Proves `role` alone was never sufficient to know whether a `messages` row is
genuine user-authored speech: consume_interaction_binding (a live, ongoing
mechanism, not just first-turn) persists a normalized Telegram-button label
as role='user'. This slice adds an explicit, required, closed provenance
column instead. It does NOT build a Professional conversation-history
reader, does NOT touch bot.py's Professional-runtime wiring, and does NOT
backfill any historical row.

Structural (AST-based) checks are used instead of text/regex search wherever
a real runtime/database assertion is not feasible (mirrors the existing
convention in tests/test_professional_entry_triage_runtime.py). A real
runtime/database assertion is used everywhere it IS feasible (save_message
call boundary, DB CHECK constraint, real consume_interaction_binding /
finalize_callback_reply execution, real privacy export/delete).
"""
import ast
import asyncio
import inspect
import pathlib
import secrets
import textwrap

import aiosqlite
import pytest

import bot
import database

UID = 700000


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    return database.DB


def _new_token():
    return secrets.token_urlsafe(9)


async def _row_source(mid):
    async with aiosqlite.connect(database.DB) as conn:
        cur = await conn.execute("SELECT source FROM messages WHERE id=?", (mid,))
        row = await cur.fetchone()
    return row[0] if row else None


async def _bind_and_consume(uid, action="elaborate", chat_id=100, source_message_id=200,
                             scenario="reflective", lang="ru"):
    """Real end-to-end exercise of the LIVE `ucbtn:` synthetic-interaction
    path: a genuine assistant turn, a genuine button binding, then a real
    consume_interaction_binding() call -- not a mock."""
    turn_id = await database.save_message(
        uid, "assistant", "первый ответ", scenario, lang,
        source=database.MessageSource.ASSISTANT_DELIVERED)
    rev = await database.bump_user_revision(uid)
    token = _new_token()
    rows = [{"token": token, "turn_id": turn_id, "chat_id": chat_id,
             "source_message_id": source_message_id, "action": action,
             "expires_at": "2999-01-01"}]
    ok = await database.create_keyboard_batch_if_current(uid, rev, rows)
    assert ok
    result = await database.consume_interaction_binding(token, uid, chat_id, source_message_id)
    assert result is not None
    return turn_id, result


def _save_message_calls(func):
    """AST-parse `func`'s own source (structural, not a text/regex search)
    and return [(role_literal, source_enum_member_name), ...] for every
    save_message(...) call found directly inside it."""
    src = textwrap.dedent(inspect.getsource(func))
    tree = ast.parse(src)
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "save_message":
            role = None
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                role = node.args[1].value
            src_val = None
            for kw in node.keywords:
                if kw.arg == "source" and isinstance(kw.value, ast.Attribute):
                    src_val = kw.value.attr
            calls.append((role, src_val))
    return calls


# ── schema / migration convergence ──────────────────────────────────────────

def test_upgraded_db_gains_nullable_messages_source(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "old.db"))

    async def go():
        async with aiosqlite.connect(database.DB) as conn:
            # Pre-migration state: CREATE TABLE only, no _apply_migrations yet
            # -- messages.source does not exist.
            await conn.executescript(database.SCHEMA)
            await conn.commit()
            cur = await conn.execute("PRAGMA table_info(messages)")
            cols_before = [r[1] for r in await cur.fetchall()]
            await conn.execute(
                "INSERT INTO messages (user_id, role, content) VALUES (?,?,?)",
                (UID, "user", "legacy row from before this feature"))
            await conn.commit()
            cur = await conn.execute("SELECT id FROM messages WHERE user_id=?", (UID,))
            old_id = (await cur.fetchone())[0]
            await database._apply_migrations(conn)
            await conn.commit()
            cur = await conn.execute("PRAGMA table_info(messages)")
            cols_after = [r[1] for r in await cur.fetchall()]
        return cols_before, cols_after, old_id

    cols_before, cols_after, old_id = asyncio.run(go())
    assert "source" not in cols_before
    assert "source" in cols_after


def test_pre_migration_row_remains_source_null_after_migration(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "old2.db"))

    async def go():
        async with aiosqlite.connect(database.DB) as conn:
            await conn.executescript(database.SCHEMA)
            await conn.commit()
            await conn.execute(
                "INSERT INTO messages (user_id, role, content) VALUES (?,?,?)",
                (UID, "assistant", "legacy assistant row"))
            await conn.commit()
            cur = await conn.execute("SELECT id FROM messages WHERE user_id=?", (UID,))
            old_id = (await cur.fetchone())[0]
            await database._apply_migrations(conn)
            await conn.commit()
            cur = await conn.execute("SELECT source FROM messages WHERE id=?", (old_id,))
            source = (await cur.fetchone())[0]
        return source

    assert asyncio.run(go()) is None


def test_fresh_db_schema_also_has_source(db):
    async def go():
        async with aiosqlite.connect(database.DB) as conn:
            cur = await conn.execute("PRAGMA table_info(messages)")
            return [r[1] for r in await cur.fetchall()]
    assert "source" in asyncio.run(go())


# ── save_message contract ───────────────────────────────────────────────────

def test_valid_user_authored_insert_persists_exactly(db):
    async def go():
        mid = await database.save_message(
            UID, "user", "hi", source=database.MessageSource.USER_AUTHORED)
        return mid, await _row_source(mid)
    mid, source = asyncio.run(go())
    assert source == "USER_AUTHORED"


def test_valid_assistant_delivered_insert_persists_exactly(db):
    async def go():
        mid = await database.save_message(
            UID, "assistant", "hi", source=database.MessageSource.ASSISTANT_DELIVERED)
        return mid, await _row_source(mid)
    mid, source = asyncio.run(go())
    assert source == "ASSISTANT_DELIVERED"


def test_valid_synthetic_ui_insert_persists_exactly(db):
    async def go():
        mid = await database.save_message(
            UID, "user", "elaborate please", source=database.MessageSource.SYNTHETIC_UI)
        return mid, await _row_source(mid)
    mid, source = asyncio.run(go())
    assert source == "SYNTHETIC_UI"


def test_invalid_source_type_fails_closed(db):
    async def go():
        await database.save_message(UID, "user", "hi", source="USER_AUTHORED")
    with pytest.raises(ValueError):
        asyncio.run(go())


def test_db_check_constraint_rejects_invalid_source_value(db):
    """Defense-in-depth: even a raw INSERT bypassing save_message's Python-
    level check is rejected by the table's own CHECK constraint -- the same
    pattern already used for interaction_button_bindings.action."""
    async def go():
        async with aiosqlite.connect(database.DB) as conn:
            await conn.execute(
                "INSERT INTO messages (user_id, role, content, source) VALUES (?,?,?,?)",
                (UID, "user", "bad", "NOT_A_REAL_VALUE"))
    with pytest.raises(Exception):
        asyncio.run(go())


def test_save_message_cannot_silently_omit_provenance(db):
    """The repository-level guard for save_message itself: source is
    keyword-only with no default, so a caller that omits it fails at the
    Python call boundary before any DB access -- fail closed by
    construction, not merely by a runtime check."""
    async def go():
        await database.save_message(UID, "user", "hi")
    with pytest.raises(TypeError):
        asyncio.run(go())


# ── every bot.py writer is correctly classified (structural, AST-based) ────

def test_no_save_message_call_in_bot_py_omits_or_mismatches_source():
    """Whole-file structural guard (ast, not a text/regex search): every
    save_message(...) call in bot.py must carry a source= keyword whose
    MessageSource member matches its own role argument. A future call site
    added without provenance, or with a role/source mismatch, fails this
    test. Covers every genuine writer: ordinary typed-user, crisis,
    controller, depression-disclosure, disambiguation, professional, and
    every assistant writer (ordinary/deterministic/controller/Entry-Triage/
    first-turn/professional). The exact count (15, up from 13 as of
    PROFESSIONAL FREE-TEXT RUNTIME V1, which legitimately added the
    Professional user-claim persist inside pipeline() and the Professional
    assistant persist inside _run_professional_free_text_and_deliver) is
    intentionally pinned here so a FUTURE writer added without provenance
    is caught even if it happens to reuse an already-correct source= value
    -- see test_pipeline_user_and_assistant_writers_classified_correctly
    and test_run_professional_free_text_and_deliver_assistant_writer_uses_
    assistant_delivered below for the two new call sites individually."""
    src = pathlib.Path(bot.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    expected_by_role = {"user": "USER_AUTHORED", "assistant": "ASSISTANT_DELIVERED"}
    checked = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "save_message":
            checked += 1
            role = None
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                role = node.args[1].value
            source_attr = None
            for kw in node.keywords:
                if kw.arg == "source" and isinstance(kw.value, ast.Attribute):
                    source_attr = kw.value.attr
            assert source_attr is not None, f"save_message call at bot.py:{node.lineno} has no source="
            assert source_attr == expected_by_role.get(role), (
                f"save_message call at bot.py:{node.lineno}: role={role!r} source={source_attr!r}")
    assert checked == 15, f"expected exactly 15 known save_message call sites in bot.py, found {checked}"


def test_trigger_crisis_user_writer_uses_user_authored():
    assert ("user", "USER_AUTHORED") in _save_message_calls(bot.trigger_crisis)


def test_controller_claim_turn_user_writer_uses_user_authored():
    assert _save_message_calls(bot._controller_claim_turn) == [("user", "USER_AUTHORED")]


def test_controller_generate_and_deliver_assistant_writers_use_assistant_delivered():
    calls = _save_message_calls(bot._controller_generate_and_deliver)
    assert calls and all(role == "assistant" and src == "ASSISTANT_DELIVERED" for role, src in calls)


def test_run_professional_free_text_and_deliver_assistant_writer_uses_assistant_delivered():
    assert _save_message_calls(bot._run_professional_free_text_and_deliver) == [
        ("assistant", "ASSISTANT_DELIVERED")]


def test_pipeline_user_and_assistant_writers_classified_correctly():
    """Covers the ordinary-typed-user writer, the pipeline-inline crisis
    writer, the depression-disclosure writer (both roles), the
    disambiguation writer (both roles), and the Professional Free-Text
    Runtime V1 claim's own user-persist writer -- all within the single
    `pipeline` function. The Professional assistant persist lives in the
    separate _run_professional_free_text_and_deliver function (see the
    dedicated test above), so it is NOT counted here."""
    calls = _save_message_calls(bot.pipeline)
    assert calls.count(("user", "USER_AUTHORED")) == 5  # crisis, disclosure, disambiguation, professional, ordinary
    assert calls.count(("assistant", "ASSISTANT_DELIVERED")) == 3  # disclosure, disambiguation, ordinary
    assert all(src is not None for _role, src in calls)


def test_entry_triage_assistant_writer_uses_assistant_delivered():
    assert _save_message_calls(bot.cb_professional_entry_triage) == [
        ("assistant", "ASSISTANT_DELIVERED")]


def test_first_turn_deliver_response_assistant_writer_uses_assistant_delivered():
    assert _save_message_calls(bot._deliver_first_turn_response) == [
        ("assistant", "ASSISTANT_DELIVERED")]


def test_voice_transcript_enters_the_user_authored_pipeline_writer():
    """handle_voice transcribes then calls pipeline(message, text, state) --
    the SAME function proven above to persist typed user text as
    USER_AUTHORED. There is no separate voice-specific writer or source
    value, so a genuine voice transcript automatically inherits
    USER_AUTHORED with no special-casing needed."""
    src = inspect.getsource(bot.handle_voice)
    assert "transcribe_voice(" in src
    assert "pipeline(message, text, state)" in src
    assert ("user", "USER_AUTHORED") in _save_message_calls(bot.pipeline)


# ── raw-SQL writers: real end-to-end execution against a temp DB ───────────

def test_consume_interaction_binding_synthetic_row_uses_synthetic_ui(db):
    async def go():
        turn_id, _result = await _bind_and_consume(UID)
        async with aiosqlite.connect(database.DB) as conn:
            cur = await conn.execute(
                "SELECT source FROM messages WHERE user_id=? AND id>? ORDER BY id", (UID, turn_id))
            rows = await cur.fetchall()
        return rows
    rows = asyncio.run(go())
    assert len(rows) == 1
    assert rows[0][0] == database.MessageSource.SYNTHETIC_UI.value


def test_finalize_callback_reply_assistant_row_uses_assistant_delivered(db):
    async def go():
        _turn_id, result = await _bind_and_consume(UID)
        fin = await database.finalize_callback_reply(result.event_id, UID, "ответ")
        return fin.assistant_turn_id
    assistant_turn_id = asyncio.run(go())
    assert asyncio.run(_row_source(assistant_turn_id)) == database.MessageSource.ASSISTANT_DELIVERED.value


# ── no historical backfill ───────────────────────────────────────────────────

def test_no_historical_backfill_occurs(db):
    """Inserting new, correctly-provenanced rows must never touch an
    existing NULL (legacy-shaped) row for the same user."""
    async def go():
        old_id = await database.save_message(
            UID, "user", "old row", source=database.MessageSource.USER_AUTHORED)
        async with aiosqlite.connect(database.DB) as conn:
            await conn.execute("UPDATE messages SET source=NULL WHERE id=?", (old_id,))
            await conn.commit()
        await database.save_message(
            UID, "user", "new row after this feature", source=database.MessageSource.USER_AUTHORED)
        return await _row_source(old_id)
    assert asyncio.run(go()) is None


# ── privacy ──────────────────────────────────────────────────────────────────

def test_messages_still_registered_include_cascade_delete():
    import privacy_registry as pr
    entry = pr.PRIVACY_REGISTRY["messages"]
    assert entry.export_policy == "INCLUDE"
    assert entry.delete_policy == "CASCADE_DELETE"


def test_privacy_export_still_covers_messages_including_source(db):
    async def go():
        await database.upsert_user(UID, "u", "U")
        await database.save_message(
            UID, "user", "exportable", source=database.MessageSource.USER_AUTHORED)
        return await database.export_all_personal_data(UID)
    data = asyncio.run(go())
    assert "messages" in data
    assert any(r["content"] == "exportable" and r["source"] == "USER_AUTHORED"
               for r in data["messages"])


def test_privacy_delete_still_covers_messages(db):
    async def go():
        await database.upsert_user(UID, "u", "U")
        await database.save_message(
            UID, "user", "deletable", source=database.MessageSource.USER_AUTHORED)
        summary = await database.delete_all_personal_data(UID)
        async with aiosqlite.connect(database.DB) as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM messages WHERE user_id=?", (UID,))
            count = (await cur.fetchone())[0]
        return summary, count
    summary, count = asyncio.run(go())
    assert summary["messages"] >= 1
    assert count == 0
