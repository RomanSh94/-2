"""Real old-schema migration for user_onboarding_state (spec item E).

The table's FIRST shape had PRIMARY KEY(user_id) alone (one row per user, no
per-version history, no card_chat_id/card_message_id/card_rendered_step
columns). The CURRENT shape has PRIMARY KEY(user_id, onboarding_version) plus
those three card_* columns and the one-active-per-user partial unique index.
SQLite cannot ALTER a PK in place, so database._rename_old_onboarding_state_if_needed
(called BEFORE executescript(SCHEMA)) and database._finish_onboarding_state_migration
(called AFTER) implement a real two-step, idempotent, crash-safe migration.

These tests build the OLD schema by hand (a real historical shape, not a
fresh database) and drive it through database.init_db() -- fresh-database
tests alone would never exercise this migration path at all.
"""
import asyncio

import pytest

import database

run = asyncio.run

_OLD_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
    language TEXT DEFAULT 'ru', message_count INTEGER DEFAULT 0,
    last_seen TEXT DEFAULT (datetime('now'))
);
CREATE TABLE user_onboarding_state (
    user_id                        INTEGER PRIMARY KEY REFERENCES users(id),
    onboarding_version             TEXT NOT NULL,
    status                         TEXT NOT NULL DEFAULT 'active',
    current_step                   INTEGER NOT NULL DEFAULT 1,
    started_at                     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at                     TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at                   TEXT,
    skipped_information_at         TEXT,
    privacy_notice_acknowledged_at TEXT,
    CHECK(status IN ('active', 'completed', 'legacy_completed')),
    CHECK(current_step BETWEEN 1 AND 5)
);
"""


@pytest.fixture
def old_db(tmp_path, monkeypatch):
    """A real sqlite file with the OLD user_onboarding_state shape already
    created and populated -- NOT a fresh database.init_db() call."""
    import sqlite3
    path = str(tmp_path / "old.db")
    con = sqlite3.connect(path)
    con.executescript(_OLD_SCHEMA_DDL)
    con.commit()
    con.close()
    monkeypatch.setattr(database, "DB", path)
    return path


def _old_pk_columns(path) -> list:
    import sqlite3
    con = sqlite3.connect(path)
    cols = con.execute("PRAGMA table_info(user_onboarding_state)").fetchall()
    con.close()
    return sorted(c[1] for c in cols if c[5] > 0)


def _insert_old_row(path, uid, version, status, step, **extra):
    import sqlite3
    con = sqlite3.connect(path)
    con.execute("INSERT OR IGNORE INTO users (id, username, first_name) VALUES (?,?,?)",
               (uid, "u", "U"))
    cols = ["user_id", "onboarding_version", "status", "current_step"]
    vals = [uid, version, status, step]
    for k, v in extra.items():
        cols.append(k)
        vals.append(v)
    placeholders = ",".join("?" for _ in cols)
    con.execute(f"INSERT INTO user_onboarding_state ({','.join(cols)}) VALUES ({placeholders})",
               vals)
    con.commit()
    con.close()


def _row_dict(path, uid):
    import sqlite3
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM user_onboarding_state WHERE user_id=?", (uid,)).fetchone()
    con.close()
    return dict(row) if row else None


def _all_rows(path, uid):
    import sqlite3
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM user_onboarding_state WHERE user_id=? ORDER BY onboarding_version",
        (uid,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


# ── old schema with an ACTIVE row ─────────────────────────────────────────────
def test_migration_preserves_active_row(old_db):
    _insert_old_row(old_db, 1001, "v1", "active", 3,
                    started_at="2026-01-01 10:00:00", updated_at="2026-01-02 11:00:00")
    run(database.init_db())
    row = _row_dict(old_db, 1001)
    assert row is not None
    assert row["status"] == "active"
    assert row["current_step"] == 3
    assert row["onboarding_version"] == "v1"
    assert row["started_at"] == "2026-01-01 10:00:00"
    assert row["updated_at"] == "2026-01-02 11:00:00"
    # New columns default to NULL for a migrated pre-versioning row.
    assert row["card_chat_id"] is None
    assert row["card_message_id"] is None
    assert row["card_rendered_step"] is None


# ── old schema with a COMPLETED row ────────────────────────────────────────────
def test_migration_preserves_completed_row(old_db):
    _insert_old_row(old_db, 1002, "v1", "completed", 5,
                    completed_at="2026-01-03 09:00:00",
                    privacy_notice_acknowledged_at="2026-01-03 09:00:00")
    run(database.init_db())
    row = _row_dict(old_db, 1002)
    assert row["status"] == "completed"
    assert row["completed_at"] == "2026-01-03 09:00:00"
    assert row["privacy_notice_acknowledged_at"] == "2026-01-03 09:00:00"


# ── old schema with a LEGACY row (old status name 'legacy_completed') ────────
def test_migration_preserves_legacy_completed_row(old_db):
    """The OLD status value 'legacy_completed' does not exist in the new
    schema's CHECK constraint (spec item F renamed it to the honest
    'legacy_exempt' -- exempted, never actually completed anything). The
    migration must translate the enum value (or the INSERT would violate the
    new CHECK constraint) while still preserving every other historical
    value unchanged, including the old completed_at timestamp as-is."""
    _insert_old_row(old_db, 1003, "v1", "legacy_completed", 5,
                    completed_at="2026-01-04 12:00:00")
    run(database.init_db())
    row = _row_dict(old_db, 1003)
    assert row["status"] == "legacy_exempt"
    assert row["completed_at"] == "2026-01-04 12:00:00"


# ── migration preserves EVERY value (full field-by-field comparison) ─────────
def test_migration_preserves_every_value(old_db):
    _insert_old_row(
        old_db, 1004, "v1", "active", 2,
        started_at="2026-02-01 00:00:00", updated_at="2026-02-02 00:00:00",
        skipped_information_at="2026-02-01 05:00:00")
    before = _row_dict(old_db, 1004)  # read via the OLD schema, before migration
    run(database.init_db())
    after = _row_dict(old_db, 1004)
    for key in ("user_id", "onboarding_version", "status", "current_step",
               "started_at", "updated_at", "skipped_information_at"):
        assert after[key] == before[key], key
    assert after["completed_at"] is None
    assert after["privacy_notice_acknowledged_at"] is None


# ── schema shape after migration ──────────────────────────────────────────────
def test_migrated_table_has_composite_primary_key(old_db):
    _insert_old_row(old_db, 1005, "v1", "active", 1)
    run(database.init_db())
    assert _old_pk_columns(old_db) == ["onboarding_version", "user_id"]


def test_migrated_table_has_partial_unique_active_index(old_db):
    _insert_old_row(old_db, 1006, "v1", "active", 1)
    run(database.init_db())
    import sqlite3
    con = sqlite3.connect(old_db)
    names = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()]
    con.close()
    assert "idx_onboarding_one_active_per_user" in names


# ── second init_db() is a no-op ───────────────────────────────────────────────
def test_second_init_db_is_a_noop(old_db):
    _insert_old_row(old_db, 1007, "v1", "active", 4,
                    started_at="2026-03-01 00:00:00")
    run(database.init_db())
    first = _row_dict(old_db, 1007)
    run(database.init_db())  # rerun -- must not error, duplicate, or change anything
    second = _row_dict(old_db, 1007)
    assert first == second
    assert len(_all_rows(old_db, 1007)) == 1


def test_init_db_on_a_database_with_no_onboarding_table_at_all(tmp_path, monkeypatch):
    """Not every historical DB necessarily had the table yet (feature never
    enabled) -- init_db() must still just create the current schema cleanly."""
    monkeypatch.setattr(database, "DB", str(tmp_path / "brand_new.db"))
    run(database.init_db())  # must not raise
    assert _old_pk_columns(str(tmp_path / "brand_new.db")) == ["onboarding_version", "user_id"]


# ── current version row can coexist with a historical version row ────────────
def test_current_version_coexists_with_historical_version_after_migration(old_db):
    _insert_old_row(old_db, 1008, "v0-old", "legacy_completed", 5,
                    completed_at="2025-06-01 00:00:00")
    run(database.init_db())
    run(database.start_or_get_onboarding(1008, "v1"))
    rows = _all_rows(old_db, 1008)
    versions = {r["onboarding_version"]: r["status"] for r in rows}
    assert versions == {"v0-old": "legacy_exempt", "v1": "active"}


# ── only one ACTIVE version is permitted, even after migration ───────────────
def test_only_one_active_version_permitted_after_migration(old_db):
    import aiosqlite
    _insert_old_row(old_db, 1009, "v1", "active", 1)
    run(database.init_db())

    async def _try_second_active():
        async with aiosqlite.connect(old_db) as db:
            await db.execute(
                "INSERT INTO user_onboarding_state (user_id, onboarding_version, "
                "status, current_step) VALUES (1009, 'v2', 'active', 1)")
            await db.commit()

    with pytest.raises(aiosqlite.IntegrityError):
        run(_try_second_active())


# ── rollback / failed-copy does not destroy the source table ─────────────────
def test_interrupted_migration_does_not_lose_data(old_db):
    """Simulates a crash between step 1 (rename) and step 2 (copy+drop): only
    _rename_old_onboarding_state_if_needed runs, nothing else. The original
    rows must still be fully intact under the renamed table -- nothing lost,
    nothing silently dropped."""
    _insert_old_row(old_db, 1010, "v1", "active", 3,
                    started_at="2026-04-01 00:00:00")
    import aiosqlite

    async def _partial():
        async with aiosqlite.connect(old_db) as db:
            await database._rename_old_onboarding_state_if_needed(db)
            await db.commit()
            # "crash" here -- executescript/_finish_onboarding_state_migration
            # never run in this connection.

    run(_partial())

    import sqlite3
    con = sqlite3.connect(old_db)
    names = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert database._OLD_ONBOARDING_TABLE in names
    row = con.execute(
        f"SELECT user_id, onboarding_version, status, current_step, started_at "
        f"FROM {database._OLD_ONBOARDING_TABLE} WHERE user_id=1010").fetchone()
    con.close()
    assert row == (1010, "v1", "active", 3, "2026-04-01 00:00:00")

    # Resuming with a normal init_db() call finishes the job from here, with
    # nothing lost.
    run(database.init_db())
    finished = _row_dict(old_db, 1010)
    assert finished["status"] == "active" and finished["current_step"] == 3
    assert finished["started_at"] == "2026-04-01 00:00:00"


# ── Independent notice-acknowledgement table + conservative backfill ───────
# (spec item F correction). database._backfill_notice_acknowledgements copies
# an acknowledgement ONLY from a user_onboarding_state row that already has
# BOTH privacy_notice_version AND privacy_notice_acknowledged_at set -- never
# inferred from status (completed/legacy_exempt/superseded) alone.
def test_fresh_db_creates_notice_acknowledgements_table(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "fresh.db"))
    run(database.init_db())
    assert run(database.has_notice_acknowledgement(1, "privacy_notice", "v1")) is False
    inserted = run(database.record_notice_acknowledgement(1, "privacy_notice", "v1"))
    assert inserted is True


def test_old_schema_row_without_notice_version_column_is_not_backfilled(old_db):
    # This OLD schema shape (see _OLD_SCHEMA_DDL above) predates
    # privacy_notice_version entirely -- even a row with
    # privacy_notice_acknowledged_at set carries NO PROVEN exact version, so
    # the conservative backfill must NOT invent an acknowledgement for it.
    _insert_old_row(old_db, 501, "v1", "completed", 5,
                    privacy_notice_acknowledged_at="2024-01-01 00:00:00")
    run(database.init_db())
    assert run(database.has_notice_acknowledgement(501, "privacy_notice", "v1")) is False


def test_current_shape_row_with_proof_is_backfilled(tmp_path, monkeypatch):
    # A "current main-compatible" DB: the CURRENT user_onboarding_state shape
    # (privacy_notice_version column present) already populated by a prior
    # deployment, but user_notice_acknowledgements does not exist yet.
    import sqlite3
    path = str(tmp_path / "current_shape.db")
    con = sqlite3.connect(path)
    con.executescript(database.SCHEMA)  # the CURRENT full schema, incl. the new table
    con.execute("DROP TABLE user_notice_acknowledgements")  # simulate "not yet migrated"
    con.execute("INSERT INTO users (id, username, first_name) VALUES (502, 'u', 'U')")
    con.execute(
        "INSERT INTO user_onboarding_state (user_id, onboarding_version, status, "
        "current_step, privacy_notice_acknowledged_at, privacy_notice_version) "
        "VALUES (502, 'v1', 'completed', 5, '2024-01-01 00:00:00', 'v1')")
    con.commit()
    con.close()
    monkeypatch.setattr(database, "DB", path)
    run(database.init_db())
    assert run(database.has_notice_acknowledgement(502, "privacy_notice", "v1")) is True
    assert run(database.has_notice_acknowledgement(502, "privacy_notice", "v2")) is False


def test_backfill_is_idempotent_across_two_init_db_calls(tmp_path, monkeypatch):
    import sqlite3
    path = str(tmp_path / "twice.db")
    con = sqlite3.connect(path)
    con.executescript(database.SCHEMA)
    con.execute("INSERT INTO users (id, username, first_name) VALUES (503, 'u', 'U')")
    con.execute(
        "INSERT INTO user_onboarding_state (user_id, onboarding_version, status, "
        "current_step, privacy_notice_acknowledged_at, privacy_notice_version) "
        "VALUES (503, 'v1', 'completed', 5, '2024-01-01 00:00:00', 'v1')")
    con.commit()
    con.close()
    monkeypatch.setattr(database, "DB", path)
    run(database.init_db())
    run(database.init_db())  # second boot -- must not raise or duplicate
    con = sqlite3.connect(path)
    count = con.execute(
        "SELECT COUNT(*) FROM user_notice_acknowledgements WHERE user_id=503").fetchone()[0]
    con.close()
    assert count == 1


def test_backfill_does_not_modify_source_onboarding_row(tmp_path, monkeypatch):
    import sqlite3
    path = str(tmp_path / "preserve.db")
    con = sqlite3.connect(path)
    con.executescript(database.SCHEMA)
    con.execute("INSERT INTO users (id, username, first_name) VALUES (504, 'u', 'U')")
    con.execute(
        "INSERT INTO user_onboarding_state (user_id, onboarding_version, status, "
        "current_step, privacy_notice_acknowledged_at, privacy_notice_version) "
        "VALUES (504, 'v1', 'completed', 5, '2024-01-01 00:00:00', 'v1')")
    con.commit()
    con.close()
    monkeypatch.setattr(database, "DB", path)
    before = _row_dict(path, 504)
    run(database.init_db())
    after = _row_dict(path, 504)
    assert before == after


def test_backfill_cross_user_isolation(tmp_path, monkeypatch):
    import sqlite3
    path = str(tmp_path / "cross_user.db")
    con = sqlite3.connect(path)
    con.executescript(database.SCHEMA)
    con.execute("INSERT INTO users (id, username, first_name) VALUES (505, 'u', 'U')")
    con.execute("INSERT INTO users (id, username, first_name) VALUES (506, 'u', 'U')")
    con.execute(
        "INSERT INTO user_onboarding_state (user_id, onboarding_version, status, "
        "current_step, privacy_notice_acknowledged_at, privacy_notice_version) "
        "VALUES (505, 'v1', 'completed', 5, '2024-01-01 00:00:00', 'v1')")
    con.execute(
        "INSERT INTO user_onboarding_state (user_id, onboarding_version, status, "
        "current_step) VALUES (506, 'v1', 'legacy_exempt', 5)")  # never acknowledged
    con.commit()
    con.close()
    monkeypatch.setattr(database, "DB", path)
    run(database.init_db())
    assert run(database.has_notice_acknowledgement(505, "privacy_notice", "v1")) is True
    assert run(database.has_notice_acknowledgement(506, "privacy_notice", "v1")) is False


def test_migration_resumable_after_copy_but_before_drop(old_db):
    """A rarer crash window: the copy (INSERT OR IGNORE) already happened but
    the DROP TABLE of the renamed old table never ran. Re-running the finish
    step must not double-insert or error -- INSERT OR IGNORE on the real
    (user_id, onboarding_version) primary key makes this safe."""
    import aiosqlite
    _insert_old_row(old_db, 1011, "v1", "completed", 5)

    async def _run_up_to_copy_twice():
        async with aiosqlite.connect(old_db) as db:
            await database._rename_old_onboarding_state_if_needed(db)
            await db.executescript(database.SCHEMA)
            await database._finish_onboarding_state_migration(db)
            await db.commit()
        # Simulate a crash that left the renamed table behind by recreating
        # it and re-running finish again -- must be a clean no-op/no-error.

    run(_run_up_to_copy_twice())
    rows = _all_rows(old_db, 1011)
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"
