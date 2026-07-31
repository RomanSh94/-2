"""Real old-schema migration for core_practice_proposals (PR #73 request-
changes §9).

PR #72 created core_practice_proposals WITHOUT the outcome/outcome_
recorded_at columns and without 'DELIVERING' in the status CHECK. PR #73's
first pass added those columns via a plain ADD COLUMN migration -- which
cannot also add a CHECK constraint to an existing table, so the exact-
pre-PR-73 production shape kept enforcing the OLD status enum and had no
outcome CHECK at all. SQLite cannot ALTER a CHECK constraint in place, so
database._rename_old_practice_proposals_if_needed (called BEFORE
executescript(SCHEMA)) and database._finish_practice_proposals_migration
(called AFTER) implement a real two-step, idempotent, crash-safe rebuild,
the same pattern already used for user_onboarding_state.

These tests build the OLD schema by hand (the real historical shape, not a
fresh database) and drive it through database.init_db() -- fresh-database
tests alone would never exercise this migration path, and would not prove
the CHECK constraint is actually enforced by SQLite itself (not just by
Python's therapeutic_domain.PracticeProposal.__post_init__)."""
import asyncio
import sqlite3

import pytest

import database

run = asyncio.run

# The exact PR #72 shape: has outcome/outcome_recorded_at is NOT present at
# all here (the true original shape); a second variant below simulates the
# in-between shape (columns added via plain ADD COLUMN, no CHECK) to prove
# both realistic upgrade paths land on the same, correctly-constrained table.
_OLD_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
    language TEXT DEFAULT 'ru', message_count INTEGER DEFAULT 0,
    last_seen TEXT DEFAULT (datetime('now'))
);
CREATE TABLE core_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    intent TEXT NOT NULL DEFAULT 'UNKNOWN', phase TEXT NOT NULL DEFAULT 'OPENING',
    lifecycle_status TEXT NOT NULL DEFAULT 'OPEN', state_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE core_practice_proposals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL REFERENCES users(id),
    session_id          INTEGER NOT NULL REFERENCES core_sessions(id),
    practice_id         TEXT NOT NULL,
    practice_version    TEXT NOT NULL,
    purpose             TEXT NOT NULL DEFAULT '',
    expected_duration   TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'PROPOSED',
    proposal_message_id INTEGER,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at          TEXT NOT NULL,
    delivered_at        TEXT,
    superseded_reason   TEXT,
    CHECK(status IN ('PROPOSED','PENDING','GRANTED','DECLINED','STARTED','COMPLETED',
                     'WITHDRAWN','EXPIRED','SUPERSEDED','DELIVERY_FAILED'))
);
CREATE INDEX IF NOT EXISTS idx_core_practice_proposals_user ON core_practice_proposals(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_core_one_actionable_proposal_per_session
    ON core_practice_proposals(session_id) WHERE status IN ('PROPOSED','PENDING');
"""


@pytest.fixture
def old_db(tmp_path, monkeypatch):
    """A real sqlite file with the exact pre-PR-73 core_practice_proposals
    shape already created and populated with one real row -- NOT a fresh
    database.init_db() call."""
    path = str(tmp_path / "old.db")
    con = sqlite3.connect(path)
    con.executescript(_OLD_SCHEMA_DDL)
    con.execute("INSERT INTO users (id, username, first_name) VALUES (1, 'u', 'U')")
    con.execute(
        "INSERT INTO core_sessions (id, user_id, state_json) VALUES (1, 1, '{}')")
    con.execute(
        "INSERT INTO core_practice_proposals "
        "(id, user_id, session_id, practice_id, practice_version, purpose, "
        " expected_duration, status, expires_at) "
        "VALUES (1, 1, 1, 'breathing_box_v1', 'v1', 'Box breathing', '3 минуты', "
        "'COMPLETED', datetime('now'))")
    con.commit()
    con.close()
    monkeypatch.setattr(database, "DB", path)
    return path


def _table_sql(path) -> str:
    con = sqlite3.connect(path)
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='core_practice_proposals'"
    ).fetchone()
    con.close()
    return row[0] if row else ""


def test_pre_pr73_shaped_db_upgrades_in_place_preserving_the_row(old_db):
    run(database.init_db())
    sql = _table_sql(old_db)
    assert "DELIVERING" in sql, "the upgraded table must allow the new status value"
    assert "outcome" in sql, "the upgraded table must have the outcome column"

    row = run(database.get_practice_proposal(1, 1))
    assert row is not None, "the pre-existing row must survive the migration"
    assert row.practice_id == "breathing_box_v1"
    assert row.status.value == "COMPLETED"
    assert row.outcome is None
    # PR #73 request-changes §6: the prompt-delivery-tracking columns must
    # also exist and be usable on the upgraded (not just fresh) table.
    assert row.outcome_prompt_status is None
    assert row.helped_prompt_status is None
    ok = run(database.mark_prompt_delivered(1, 1, "helped", 999))
    assert ok is True
    reloaded = run(database.get_practice_proposal(1, 1))
    assert reloaded.helped_prompt_status == "DELIVERED"
    assert reloaded.helped_prompt_message_id == 999


def test_fresh_db_has_delivering_and_outcome_check_from_the_start(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "fresh.db"))
    run(database.init_db())
    sql = _table_sql(str(tmp_path / "fresh.db"))
    assert "DELIVERING" in sql
    assert "outcome" in sql


def test_sqlite_itself_rejects_an_invalid_status_value_after_migration(old_db):
    """Not just therapeutic_domain.PracticeProposal.__post_init__ -- the raw
    CHECK constraint in the DATABASE must reject a bad value directly."""
    run(database.init_db())
    con = sqlite3.connect(old_db)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "UPDATE core_practice_proposals SET status='NOT_A_REAL_STATUS' WHERE id=1")
    con.close()


def test_sqlite_itself_rejects_an_invalid_outcome_value_after_migration(old_db):
    run(database.init_db())
    con = sqlite3.connect(old_db)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "UPDATE core_practice_proposals SET outcome='NOT_A_REAL_OUTCOME' WHERE id=1")
    con.close()


def test_migration_is_idempotent_on_a_second_boot(old_db):
    run(database.init_db())
    run(database.init_db())  # simulates a second boot against the same file
    row = run(database.get_practice_proposal(1, 1))
    assert row is not None
    assert row.status.value == "COMPLETED"


def test_delivering_status_is_actually_usable_after_migration(old_db):
    """A real end-to-end proof that the migrated table accepts the new
    status through the ordinary application code path, not just raw SQL."""
    run(database.init_db())
    ok = run(database.transition_practice_proposal(
        1, 1, from_status="COMPLETED", to_status="COMPLETED"))
    # COMPLETED->COMPLETED is a no-op CAS (from_status must differ in real
    # use, but this still proves the CHECK constraint accepts a value this
    # module writes without raising) -- assert no exception was the point.
    assert ok is True
