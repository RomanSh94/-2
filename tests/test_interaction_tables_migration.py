"""Real old-schema migration for interaction_button_bindings and
user_interaction_events (Phase 3 technical-blocker fix, item A).

Phase 2 (approved checkpoint 6309850) shipped these two tables with
CHECK(action IN ('elaborate','clarify','hard','hardreply:easier',
'hardreply:same','hardreply:harder')). Phase 3 widened the action set to 16
values (hardreg:*/hardstate:* replacing hardreply:easier/same/harder).
SQLite cannot ALTER a CHECK constraint in place, so
database._migrate_interaction_tables_atomically -- run as the FIRST thing
init_db() does, inside one explicit BEGIN IMMEDIATE/COMMIT (or ROLLBACK on
any exception) -- rebuilds both tables together.

This is a genuinely atomic replacement for an earlier (buggy) version that
relied on init_db()'s single trailing commit for atomicity while calling
db.executescript() in between -- executescript() implicitly COMMITs any
pending transaction first, which silently broke that assumption. These tests
prove the CURRENT version really is atomic: a forced failure at any of the
five required points rolls back to the complete, unchanged, still-usable
original Phase 2 database -- not a half-migrated intermediate state.

These tests build the OLD schema by hand (the real historical shape) and
drive it through database.init_db() -- a fresh database never exercises this
migration path at all.
"""
import asyncio
import sqlite3

import aiosqlite
import pytest

import database

run = asyncio.run

_OLD_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS interaction_button_bindings (
    token              TEXT PRIMARY KEY,
    turn_id            INTEGER NOT NULL,
    user_id            INTEGER NOT NULL,
    chat_id            INTEGER NOT NULL,
    source_message_id  INTEGER NOT NULL,
    action             TEXT NOT NULL
                       CHECK (action IN ('elaborate','clarify','hard',
                                          'hardreply:easier','hardreply:same','hardreply:harder')),
    binding_revision   INTEGER NOT NULL,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at         TEXT NOT NULL,
    consumed_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_interaction_bindings_open
    ON interaction_button_bindings(user_id, consumed_at, expires_at);

CREATE TABLE IF NOT EXISTS user_interaction_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL,
    turn_id           INTEGER NOT NULL,
    event_type        TEXT NOT NULL,
    action            TEXT NOT NULL
                      CHECK (action IN ('elaborate','clarify','hard',
                                         'hardreply:easier','hardreply:same','hardreply:harder')),
    normalized_text   TEXT NOT NULL,
    reply_status      TEXT NOT NULL DEFAULT 'pending_before_send'
                      CHECK (reply_status IN ('pending_before_send','delivery_uncertain',
                                                'delivered','delivered_context_missing',
                                                'no_reply_required')),
    assistant_turn_id INTEGER,
    reply_updated_at  TEXT,
    reply_error_code  TEXT
                      CHECK (reply_error_code IS NULL OR reply_error_code IN
                             ('SEND_EXCEPTION','SAVE_EXCEPTION','FINALIZE_EXCEPTION')),
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@pytest.fixture
def old_db(tmp_path, monkeypatch):
    """A real sqlite file with the OLD (Phase 2) interaction-table shapes
    already created and populated -- NOT a fresh database.init_db() call."""
    path = str(tmp_path / "old_interaction.db")
    con = sqlite3.connect(path)
    con.executescript(_OLD_SCHEMA_DDL)
    con.commit()
    con.close()
    monkeypatch.setattr(database, "DB", path)
    return path


def _insert_old_binding(path, token, turn_id, user_id, chat_id, source_message_id, action,
                        binding_revision, expires_at, consumed_at=None):
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO interaction_button_bindings "
        "(token, turn_id, user_id, chat_id, source_message_id, action, "
        " binding_revision, expires_at, consumed_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (token, turn_id, user_id, chat_id, source_message_id, action,
         binding_revision, expires_at, consumed_at))
    con.commit()
    con.close()


def _insert_old_event(path, user_id, turn_id, event_type, action, normalized_text,
                      reply_status="pending_before_send", assistant_turn_id=None):
    con = sqlite3.connect(path)
    cur = con.execute(
        "INSERT INTO user_interaction_events "
        "(user_id, turn_id, event_type, action, normalized_text, reply_status, assistant_turn_id) "
        "VALUES (?,?,?,?,?,?,?)",
        (user_id, turn_id, event_type, action, normalized_text, reply_status, assistant_turn_id))
    con.commit()
    rowid = cur.lastrowid
    con.close()
    return rowid


def _table_names(path):
    con = sqlite3.connect(path)
    names = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    con.close()
    return names


def _index_names(path):
    con = sqlite3.connect(path)
    names = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()]
    con.close()
    return names


def _binding_row(path, token):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM interaction_button_bindings WHERE token=?", (token,)).fetchone()
    con.close()
    return dict(row) if row else None


def _event_row(path, event_id):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM user_interaction_events WHERE id=?", (event_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def _install_checkpoint_failure(monkeypatch, checkpoint_name, exc_message):
    async def flaky_checkpoint(name):
        if name == checkpoint_name:
            raise RuntimeError(exc_message)
    monkeypatch.setattr(database, "_migration_checkpoint", flaky_checkpoint)


def _assert_original_phase2_db_completely_intact(path, token, expected_action):
    """The single, central atomicity proof: after a forced failure at any
    point, the database must be indistinguishable from before init_db() was
    ever called -- no staging table left behind, the original table/index
    still in place under their real names, the original row unchanged, and
    the OLD (6-value) CHECK constraint still actually enforced (not
    replaced) -- i.e. the database is still fully USABLE in its old shape,
    not stuck half-migrated."""
    con = sqlite3.connect(path)
    names = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert database._OLD_INTERACTION_BINDINGS_TABLE not in names
    assert database._OLD_INTERACTION_EVENTS_TABLE not in names
    assert "interaction_button_bindings" in names
    assert "user_interaction_events" in names

    row = con.execute(
        "SELECT action FROM interaction_button_bindings WHERE token=?", (token,)).fetchone()
    assert row == (expected_action,)

    # The OLD CHECK constraint must still be the one actually enforced --
    # proves the table itself was never swapped, only that a rollback
    # occurred (a partially-succeeded rebuild could otherwise leave the NEW
    # empty/mid-copy table sitting under the real name with an ABORTed
    # transaction papering over it in some other engine; SQLite's real
    # transactional DDL must not permit that, and this is the direct check).
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO interaction_button_bindings "
            "(token, turn_id, user_id, chat_id, source_message_id, action, "
            " binding_revision, expires_at) VALUES (?,?,?,?,?,?,?,?)",
            ("proof_tok_never_committed", 1, 999999, 1, 1, "hardreg:unsafe", 1, "2999-01-01"))
    con.rollback()

    idx = con.execute(
        "SELECT tbl_name FROM sqlite_master WHERE type='index' "
        "AND name='idx_interaction_bindings_open'").fetchone()
    assert idx == ("interaction_button_bindings",)

    # Still usable in its old shape: a legacy insert succeeds normally.
    con.execute(
        "INSERT INTO interaction_button_bindings "
        "(token, turn_id, user_id, chat_id, source_message_id, action, "
        " binding_revision, expires_at) VALUES (?,?,?,?,?,?,?,?)",
        ("still_usable_tok", 1, 999998, 1, 1, "hard", 1, "2999-01-01"))
    con.commit()
    con.close()


# ── legacy rows survive the migration ─────────────────────────────────────────

def test_migration_preserves_unconsumed_legacy_binding(old_db):
    _insert_old_binding(old_db, "tok1", 1, 2001, 100, 200, "hardreply:easier",
                        1, "2999-01-01 00:00:00")
    run(database.init_db())
    row = _binding_row(old_db, "tok1")
    assert row is not None
    assert row["action"] == "hardreply:easier"
    assert row["consumed_at"] is None


def test_migration_preserves_consumed_legacy_binding(old_db):
    _insert_old_binding(old_db, "tok2", 1, 2002, 100, 201, "hardreply:same",
                        1, "2999-01-01 00:00:00", consumed_at="2026-01-01 00:00:00")
    run(database.init_db())
    row = _binding_row(old_db, "tok2")
    assert row["consumed_at"] == "2026-01-01 00:00:00"


def test_migration_preserves_all_three_legacy_actions(old_db):
    for i, action in enumerate(("hardreply:easier", "hardreply:same", "hardreply:harder")):
        _insert_old_binding(old_db, f"tok_a{i}", 1, 2003, 100, 300 + i, action,
                            1, "2999-01-01 00:00:00")
    run(database.init_db())
    actions = {_binding_row(old_db, f"tok_a{i}")["action"] for i in range(3)}
    assert actions == {"hardreply:easier", "hardreply:same", "hardreply:harder"}


def test_migration_preserves_historical_event_with_delivered_status(old_db):
    eid = _insert_old_event(old_db, 2004, 1, "first_turn_button", "hardreply:harder",
                            "[legacy text]", reply_status="delivered", assistant_turn_id=5)
    run(database.init_db())
    row = _event_row(old_db, eid)
    assert row["action"] == "hardreply:harder"
    assert row["reply_status"] == "delivered"
    assert row["assistant_turn_id"] == 5


def test_migration_preserves_current_shape_actions_untouched(old_db):
    _insert_old_binding(old_db, "tok_mix", 1, 2005, 100, 400, "elaborate",
                        1, "2999-01-01 00:00:00")
    run(database.init_db())
    row = _binding_row(old_db, "tok_mix")
    assert row["action"] == "elaborate"


# ── new Phase 3 actions become insertable after migration ────────────────────

def test_new_phase3_action_insertable_after_migration(old_db):
    _insert_old_binding(old_db, "tok3", 1, 2006, 100, 202, "hard", 1, "2999-01-01 00:00:00")
    run(database.init_db())

    async def _insert_new():
        async with aiosqlite.connect(old_db) as db:
            await db.execute(
                "INSERT INTO interaction_button_bindings "
                "(token, turn_id, user_id, chat_id, source_message_id, action, "
                " binding_revision, expires_at) VALUES (?,?,?,?,?,?,?,?)",
                ("tok4", 1, 2006, 100, 203, "hardreg:unsafe", 1, "2999-01-01 00:00:00"))
            await db.commit()
    run(_insert_new())   # must not raise -- the new CHECK accepts the new action
    row = _binding_row(old_db, "tok4")
    assert row["action"] == "hardreg:unsafe"


def test_new_hardstate_action_insertable_in_events_after_migration(old_db):
    run(database.init_db())

    async def _insert_new():
        async with aiosqlite.connect(old_db) as db:
            cur = await db.execute(
                "INSERT INTO user_interaction_events "
                "(user_id, turn_id, event_type, action, normalized_text) "
                "VALUES (?,?,?,?,?)",
                (2007, 1, "first_turn_button", "hardstate:anxiety", "[x]"))
            await db.commit()
            return cur.lastrowid
    eid = run(_insert_new())   # must not raise
    row = _event_row(old_db, eid)
    assert row["action"] == "hardstate:anxiety"


# ── indexes survive ────────────────────────────────────────────────────────────

def test_index_exists_after_migration(old_db):
    _insert_old_binding(old_db, "tok5", 1, 2008, 100, 204, "hard", 1, "2999-01-01 00:00:00")
    run(database.init_db())
    assert "idx_interaction_bindings_open" in _index_names(old_db)


# ── idempotency: second init_db() is a no-op ──────────────────────────────────

def test_second_init_db_is_a_noop_for_interaction_tables(old_db):
    _insert_old_binding(old_db, "tok6", 1, 2009, 100, 205, "hardreply:easier",
                        1, "2999-01-01 00:00:00")
    run(database.init_db())
    first = _binding_row(old_db, "tok6")
    run(database.init_db())   # rerun -- must not error, duplicate, or change anything
    second = _binding_row(old_db, "tok6")
    assert first == second


# ── AUTOINCREMENT continuity ──────────────────────────────────────────────────

def test_autoincrement_continues_after_highest_migrated_id(old_db):
    id1 = _insert_old_event(old_db, 3010, 1, "first_turn_button", "hardreply:harder", "[x]")
    id2 = _insert_old_event(old_db, 3010, 1, "first_turn_button", "hardreply:easier", "[y]")
    highest_old_id = max(id1, id2)
    run(database.init_db())

    async def _insert_new_event():
        async with aiosqlite.connect(old_db) as db:
            cur = await db.execute(
                "INSERT INTO user_interaction_events "
                "(user_id, turn_id, event_type, action, normalized_text) "
                "VALUES (?,?,?,?,?)",
                (3010, 1, "first_turn_button", "hardreg:unsafe", "[z]"))
            await db.commit()
            return cur.lastrowid
    new_id = run(_insert_new_event())
    assert new_id > highest_old_id


# ── PK collision during copy: never silently replaced/ignored/lost ───────────

def test_pk_collision_during_copy_raises_and_never_replaces_destination_row(tmp_path, monkeypatch):
    """Direct proof on the copy primitive itself (a true end-to-end PK
    collision cannot arise through the normal init_db() entry path, since
    the destination table is always freshly created empty inside the same
    transaction as the source's own unique-PK guarantee) -- INSERT (never
    INSERT OR IGNORE) means a real collision raises immediately rather than
    silently dropping or overwriting the pre-existing destination row."""
    path = str(tmp_path / "collision.db")
    monkeypatch.setattr(database, "DB", path)

    async def go():
        async with aiosqlite.connect(path) as db:
            await db.execute(
                "CREATE TABLE src (token TEXT PRIMARY KEY, turn_id INTEGER, user_id INTEGER, "
                "chat_id INTEGER, source_message_id INTEGER, action TEXT, "
                "binding_revision INTEGER, created_at TEXT DEFAULT (datetime('now')), "
                "expires_at TEXT, consumed_at TEXT)")
            await db.execute(
                "INSERT INTO src (token, turn_id, user_id, chat_id, source_message_id, action, "
                "binding_revision, expires_at) VALUES ('dup', 1, 111, 1, 1, 'hard', 1, '2999-01-01')")
            await db.execute(database._INTERACTION_BINDINGS_TABLE_DDL)
            await db.execute(
                "INSERT INTO interaction_button_bindings "
                "(token, turn_id, user_id, chat_id, source_message_id, action, "
                " binding_revision, expires_at) VALUES ('dup', 2, 222, 2, 2, 'hard', 1, '2999-01-01')")
            await db.commit()

            raised = False
            try:
                await database._copy_and_verify_interaction_table(
                    db, old_name="src", new_name="interaction_button_bindings",
                    pk_column="token",
                    columns="token, turn_id, user_id, chat_id, source_message_id, action, "
                           "binding_revision, created_at, expires_at, consumed_at")
            except Exception:
                raised = True
            cur = await db.execute(
                "SELECT turn_id, user_id FROM interaction_button_bindings WHERE token='dup'")
            row = await cur.fetchone()
            return raised, row
    raised, row = run(go())
    assert raised is True            # the collision was never silently swallowed
    assert row == (2, 222)           # the pre-existing destination row was never replaced


# ── atomicity: forced failure at each of the 5 required points rolls back
#    to the COMPLETE, unchanged, still-usable original Phase 2 database ─────

def test_forced_failure_before_index_recreation_rolls_back_completely(old_db, monkeypatch):
    _insert_old_binding(old_db, "tokA", 1, 4001, 100, 700, "hard", 1, "2999-01-01 00:00:00")
    _install_checkpoint_failure(monkeypatch, "before_index_recreation", "forced-A")
    with pytest.raises(RuntimeError, match="forced-A"):
        run(database.init_db())
    _assert_original_phase2_db_completely_intact(old_db, "tokA", "hard")


def test_forced_failure_after_first_table_copied_rolls_back_completely(old_db, monkeypatch):
    _insert_old_binding(old_db, "tokB", 1, 4002, 100, 701, "hardreply:easier",
                        1, "2999-01-01 00:00:00")
    _install_checkpoint_failure(monkeypatch, "after_first_table_copied", "forced-B")
    with pytest.raises(RuntimeError, match="forced-B"):
        run(database.init_db())
    _assert_original_phase2_db_completely_intact(old_db, "tokB", "hardreply:easier")


def test_forced_failure_while_second_table_copied_rolls_back_completely(old_db, monkeypatch):
    _insert_old_binding(old_db, "tokC", 1, 4003, 100, 702, "hardreply:same",
                        1, "2999-01-01 00:00:00")
    _install_checkpoint_failure(
        monkeypatch, "mid_copy:user_interaction_events", "forced-C")
    with pytest.raises(RuntimeError, match="forced-C"):
        run(database.init_db())
    _assert_original_phase2_db_completely_intact(old_db, "tokC", "hardreply:same")


def test_forced_failure_before_old_tables_dropped_rolls_back_completely(old_db, monkeypatch):
    _insert_old_binding(old_db, "tokD", 1, 4004, 100, 703, "hardreply:harder",
                        1, "2999-01-01 00:00:00")
    _install_checkpoint_failure(monkeypatch, "before_old_tables_dropped", "forced-D")
    with pytest.raises(RuntimeError, match="forced-D"):
        run(database.init_db())
    _assert_original_phase2_db_completely_intact(old_db, "tokD", "hardreply:harder")


def test_forced_failure_before_commit_rolls_back_completely(old_db, monkeypatch):
    _insert_old_binding(old_db, "tokE", 1, 4005, 100, 704, "elaborate",
                        1, "2999-01-01 00:00:00")
    _install_checkpoint_failure(monkeypatch, "before_commit", "forced-E")
    with pytest.raises(RuntimeError, match="forced-E"):
        run(database.init_db())
    _assert_original_phase2_db_completely_intact(old_db, "tokE", "elaborate")


def test_forced_failure_does_not_leave_a_staging_table_behind(old_db, monkeypatch):
    """Same proof as above, isolated to the specific requirement that NEITHER
    original name nor either staging name is left in an inconsistent state
    -- the rename itself must roll back, not just the copy."""
    _insert_old_binding(old_db, "tokF", 1, 4006, 100, 705, "hard", 1, "2999-01-01 00:00:00")
    _install_checkpoint_failure(monkeypatch, "mid_copy:interaction_button_bindings", "forced-F")
    with pytest.raises(RuntimeError, match="forced-F"):
        run(database.init_db())
    names = _table_names(old_db)
    assert database._OLD_INTERACTION_BINDINGS_TABLE not in names
    assert database._OLD_INTERACTION_EVENTS_TABLE not in names
    assert names.count("interaction_button_bindings") == 1
    assert names.count("user_interaction_events") == 1


def test_second_init_db_after_a_forced_failure_succeeds_normally(old_db, monkeypatch):
    """A rolled-back attempt must not leave the database in a state where a
    SUBSEQUENT, un-sabotaged init_db() call can no longer complete the
    migration -- proves the rollback is truly a clean revert, not a partial
    one that blocks future retries."""
    _insert_old_binding(old_db, "tokG", 1, 4007, 100, 706, "hard", 1, "2999-01-01 00:00:00")
    _install_checkpoint_failure(monkeypatch, "before_commit", "forced-G")
    with pytest.raises(RuntimeError, match="forced-G"):
        run(database.init_db())

    # Restore the real no-op checkpoint and retry.
    async def real_checkpoint(name):
        return None
    monkeypatch.setattr(database, "_migration_checkpoint", real_checkpoint)

    run(database.init_db())   # must now succeed cleanly
    row = _binding_row(old_db, "tokG")
    assert row["action"] == "hard"

    async def _insert_new():
        async with aiosqlite.connect(old_db) as db:
            await db.execute(
                "INSERT INTO interaction_button_bindings "
                "(token, turn_id, user_id, chat_id, source_message_id, action, "
                " binding_revision, expires_at) VALUES (?,?,?,?,?,?,?,?)",
                ("tokG2", 1, 4007, 100, 707, "hardreg:unsafe", 1, "2999-01-01 00:00:00"))
            await db.commit()
    run(_insert_new())   # proves the migration truly completed this time
    assert _binding_row(old_db, "tokG2")["action"] == "hardreg:unsafe"


# ── fresh database with no interaction tables at all ──────────────────────────

def test_init_db_on_database_with_no_interaction_tables(tmp_path, monkeypatch):
    path = str(tmp_path / "brand_new_interaction.db")
    monkeypatch.setattr(database, "DB", path)
    run(database.init_db())   # must not raise
    assert "interaction_button_bindings" in _table_names(path)


# ── index exists at the exact moment the ATOMIC migration itself commits ─────
# (independent correctness fix, round 3): a real regression was found where
# the round-1 index-collision guard (dropping the carried-over index before
# the rename) was lost while rewriting the migration to be atomic -- the
# atomic transaction's own commit left NO index at all; it only reappeared
# later because the separate, non-atomic executescript(SCHEMA) call happened
# to run its own CREATE INDEX IF NOT EXISTS afterward. test_index_exists_
# after_migration above only checks POST-init_db() state and could not have
# caught this. This test calls the atomic migration function directly and
# NEVER runs executescript at all, isolating its own guarantee precisely.

def test_index_exists_immediately_after_atomic_migration_alone(old_db):
    _insert_old_binding(old_db, "tok_idx", 1, 5001, 100, 900, "hard", 1, "2999-01-01 00:00:00")

    async def go():
        async with aiosqlite.connect(old_db) as db:
            await database._migrate_interaction_tables_atomically(db)
    run(go())   # ONLY the atomic migration -- executescript(SCHEMA) never runs

    assert "idx_interaction_bindings_open" in _index_names(old_db)
    con = sqlite3.connect(old_db)
    idx = con.execute(
        "SELECT tbl_name FROM sqlite_master WHERE type='index' "
        "AND name='idx_interaction_bindings_open'").fetchone()
    con.close()
    assert idx == ("interaction_button_bindings",)   # bound to the real table, not orphaned


# ── test isolation: never the repository-root database ────────────────────────

def test_migration_never_touches_repository_root_db(old_db):
    assert database.DB != "x20.db"
