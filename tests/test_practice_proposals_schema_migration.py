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
        'INSERT INTO core_sessions (id, user_id, state_json) VALUES (1, 1, \'{"user_id": 1}\')')
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
    assert row.reporting_window_status is None
    assert row.outcome_prompt_claim_id is None
    assert row.is_worse_override is False
    # The claim-first contract itself (claim_prompt_send -> mark_prompt_
    # delivered) needs a STARTED proposal with an ACTIVE window to claim
    # against -- this pre-seeded COMPLETED/no-window row can't legitimately
    # claim one; that full end-to-end proof lives in
    # test_delivering_status_is_actually_usable_after_migration below,
    # which builds a fresh proposal through the real pipeline instead.


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
    """PR #73 FINAL REQUEST CHANGES §7: the previous version of this test
    only proved a COMPLETED->COMPLETED no-op CAS didn't raise -- that never
    actually wrote 'DELIVERING' through the CHECK constraint at all. This
    drives a FRESH proposal (the pre-seeded row's expires_at is already
    stale, so it can't be used for a require_unexpired=True transition)
    through the real GRANTED->DELIVERING->STARTED pipeline on the migrated
    table, exactly the sequence bot.cb_cc_consent performs in production."""
    run(database.init_db())
    # Reuse the pre-seeded session (id=1) -- the fixture's row is already
    # OPEN, and idx_core_one_open_session_per_user allows at most one
    # OPEN/PAUSED session per user, so create_core_session(1) here would
    # collide with it.
    session = run(database.get_core_session(1, 1))
    proposal = run(database.create_practice_proposal(
        1, session.session_id, "breathing_box_v1", "v1", "p", "5 минут"))
    assert run(database.transition_practice_proposal(
        proposal.proposal_id, 1, from_status="PROPOSED", to_status="PENDING")) is True
    assert run(database.transition_practice_proposal(
        proposal.proposal_id, 1, from_status="PENDING", to_status="GRANTED",
        require_unexpired=True)) is True
    assert run(database.transition_practice_proposal(
        proposal.proposal_id, 1, from_status="GRANTED", to_status="DELIVERING",
        require_unexpired=True)) is True, \
        "the migrated status CHECK constraint must accept 'DELIVERING'"
    assert run(database.transition_practice_proposal(
        proposal.proposal_id, 1, from_status="DELIVERING", to_status="STARTED",
        open_reporting_window=True)) is True
    final = run(database.get_practice_proposal(proposal.proposal_id, 1))
    assert final.status.value == "STARTED"
    assert final.reporting_window_status == "ACTIVE"


# ── In-between schema: the PR#73-FIRST-PASS shape (PR #73 FINAL REQUEST
# CHANGES §7). A real system could have already run a plain ADD COLUMN
# migration once (outcome/outcome_prompt_*/helped_prompt_* columns exist)
# before this rebuild-based migration existed -- that pass could not also
# add a CHECK constraint or widen a partial index's WHERE clause, so
# DELIVERING/RETRYING/reporting_window_status are all still missing from
# the CHECKs even though the earlier columns are present. This is a SECOND,
# independently realistic upgrade path, distinct from the original
# pre-PR-73 shape in old_db above.
_IN_BETWEEN_SCHEMA_DDL = """
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
    outcome             TEXT,
    outcome_recorded_at TEXT,
    outcome_prompt_message_id   INTEGER,
    outcome_prompt_delivered_at TEXT,
    outcome_prompt_status       TEXT,
    helped_prompt_message_id    INTEGER,
    helped_prompt_delivered_at  TEXT,
    helped_prompt_status        TEXT,
    CHECK(status IN ('PROPOSED','PENDING','GRANTED','DECLINED','STARTED','COMPLETED',
                     'WITHDRAWN','EXPIRED','SUPERSEDED','DELIVERY_FAILED'))
);
CREATE INDEX IF NOT EXISTS idx_core_practice_proposals_user ON core_practice_proposals(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_core_one_actionable_proposal_per_session
    ON core_practice_proposals(session_id) WHERE status IN ('PROPOSED','PENDING','GRANTED');
"""


@pytest.fixture
def in_between_db(tmp_path, monkeypatch):
    path = str(tmp_path / "inbetween.db")
    con = sqlite3.connect(path)
    con.executescript(_IN_BETWEEN_SCHEMA_DDL)
    con.execute("INSERT INTO users (id, username, first_name) VALUES (1, 'u', 'U')")
    con.execute(
        'INSERT INTO core_sessions (id, user_id, state_json) VALUES (1, 1, \'{"user_id": 1}\')')
    con.execute(
        "INSERT INTO core_practice_proposals "
        "(id, user_id, session_id, practice_id, practice_version, purpose, "
        " expected_duration, status, expires_at, outcome) "
        "VALUES (1, 1, 1, 'breathing_box_v1', 'v1', 'Box breathing', '3 минуты', "
        "'COMPLETED', datetime('now'), 'HELPED')")
    con.commit()
    con.close()
    monkeypatch.setattr(database, "DB", path)
    return path


def test_in_between_shaped_db_upgrades_in_place_preserving_the_row(in_between_db):
    """Proves the SECOND realistic upgrade path also lands on the fully-
    constrained current schema, preserving both status AND the already-
    present outcome value."""
    run(database.init_db())
    sql = _table_sql(in_between_db)
    assert "DELIVERING" in sql
    assert "reporting_window_status" in sql

    row = run(database.get_practice_proposal(1, 1))
    assert row is not None
    assert row.status.value == "COMPLETED"
    assert row.outcome.value == "HELPED", "a pre-existing outcome value must survive this upgrade path too"
    assert row.reporting_window_status is None


def test_in_between_shaped_db_second_boot_is_idempotent(in_between_db):
    run(database.init_db())
    run(database.init_db())
    row = run(database.get_practice_proposal(1, 1))
    assert row is not None
    assert row.status.value == "COMPLETED"
    assert row.outcome.value == "HELPED"


def test_in_between_shaped_db_delivering_and_retrying_are_usable_end_to_end(in_between_db):
    """The same full-pipeline proof as test_delivering_status_is_actually_
    usable_after_migration, run against the OTHER realistic upgrade path --
    also exercises the RETRYING claim state and reporting_window_status,
    neither of which existed at all in the in-between shape's CHECKs."""
    run(database.init_db())
    session = run(database.get_core_session(1, 1))  # reuse the pre-seeded OPEN session
    proposal = run(database.create_practice_proposal(
        1, session.session_id, "breathing_box_v1", "v1", "p", "5 минут"))
    assert run(database.transition_practice_proposal(
        proposal.proposal_id, 1, from_status="PROPOSED", to_status="PENDING")) is True
    assert run(database.transition_practice_proposal(
        proposal.proposal_id, 1, from_status="PENDING", to_status="GRANTED",
        require_unexpired=True)) is True
    assert run(database.transition_practice_proposal(
        proposal.proposal_id, 1, from_status="GRANTED", to_status="DELIVERING",
        require_unexpired=True)) is True
    assert run(database.transition_practice_proposal(
        proposal.proposal_id, 1, from_status="DELIVERING", to_status="STARTED",
        open_reporting_window=True)) is True
    claim_id = run(database.claim_prompt_send(proposal.proposal_id, 1, "outcome", "STARTED"))
    assert claim_id is not None
    assert run(database.mark_prompt_delivered(proposal.proposal_id, 1, "outcome", 42, claim_id)) is True
    final = run(database.get_practice_proposal(proposal.proposal_id, 1))
    assert final.status.value == "STARTED"
    assert final.reporting_window_status == "ACTIVE"
    assert final.outcome_prompt_status == "DELIVERED"
    assert final.outcome_prompt_claim_id == claim_id


def test_in_between_shaped_db_rejects_invalid_status(in_between_db):
    run(database.init_db())
    con = sqlite3.connect(in_between_db)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "UPDATE core_practice_proposals SET status='NOT_A_REAL_STATUS' WHERE id=1")
    con.close()


def test_interrupted_migration_recovers_on_next_boot(old_db):
    """§7: interrupted rename/rebuild recovery. Simulates a crash strictly
    AFTER the rename (old table renamed aside) but BEFORE the rebuild
    finished (executescript + copy-and-drop never ran) -- a subsequent
    clean init_db() call must still complete the migration correctly, as if
    the process had died mid-upgrade and simply been restarted."""
    import aiosqlite

    async def simulate_crash_after_rename():
        async with aiosqlite.connect(old_db) as db:
            await database._rename_old_practice_proposals_if_needed(db)
            await db.commit()
        # Crash here: the old table is renamed aside; executescript (fresh
        # CREATE TABLE) and _finish_practice_proposals_migration never ran.
    run(simulate_crash_after_rename())

    con = sqlite3.connect(old_db)
    live = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='core_practice_proposals'"
    ).fetchone()
    renamed = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (database._OLD_PRACTICE_PROPOSALS_TABLE,)).fetchone()
    con.close()
    assert live is None, "mid-crash: the old table was renamed aside, no live table exists yet"
    assert renamed is not None

    run(database.init_db())  # the "next boot"
    row = run(database.get_practice_proposal(1, 1))
    assert row is not None, "the row must survive an interrupted-then-resumed migration"
    assert row.practice_id == "breathing_box_v1"
    assert row.status.value == "COMPLETED"
    con = sqlite3.connect(old_db)
    renamed_after = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (database._OLD_PRACTICE_PROPOSALS_TABLE,)).fetchone()
    con.close()
    assert renamed_after is None, "the temporary renamed-aside table must be cleaned up"


# ── Duplicate-actionable-row migration compatibility (PR #73 FINAL
# MIGRATION COMPATIBILITY GATE). The OLD partial unique index (see
# _OLD_SCHEMA_DDL above: `WHERE status IN ('PROPOSED','PENDING')`) and the
# OLD create_practice_proposal (which only ever superseded PROPOSED/
# PENDING before inserting) never covered GRANTED -- real old-schema data
# can therefore legally contain MORE than one actionable row for the same
# (user_id, session_id). Copying such rows verbatim into the NEW schema's
# wider partial unique index (PROPOSED/PENDING/GRANTED/DELIVERING) would
# raise IntegrityError and crash init_db(). database._finish_practice_
# proposals_migration now normalizes: the newest actionable row (by
# created_at, ties broken by id) keeps its real status; every older
# conflicting one becomes SUPERSEDED/'migration_duplicate_actionable'.
# No row is ever deleted, and no non-actionable historical status is ever
# rewritten. ───────────────────────────────────────────────────────────────

@pytest.fixture
def old_db_with_duplicates(tmp_path, monkeypatch):
    """A realistic OLD-schema database with exactly the three duplicate
    shapes named in the spec (two old GRANTED; old GRANTED + newer
    PENDING; old PROPOSED + newer GRANTED), plus same-user/different-
    session and different-user control rows, plus a historical COMPLETED
    row sharing a session with duplicates -- to prove normalization never
    leaks across session or user boundaries and never touches non-
    actionable history."""
    path = str(tmp_path / "dup.db")
    con = sqlite3.connect(path)
    con.executescript(_OLD_SCHEMA_DDL)
    con.execute("INSERT INTO users (id, username, first_name) VALUES (1, 'u1', 'U1')")
    con.execute("INSERT INTO users (id, username, first_name) VALUES (2, 'u2', 'U2')")
    # lifecycle_status='COMPLETED' on every seeded session -- the REAL
    # schema's idx_core_one_open_session_per_user (at most one OPEN/PAUSED
    # session per user) applies to core_sessions unconditionally on
    # executescript(SCHEMA), and user 1 here legitimately has several
    # historical sessions, none of which need to be OPEN for this test.
    for sid, uid in ((10, 1), (11, 1), (12, 1), (13, 1), (20, 2)):
        con.execute(
            "INSERT INTO core_sessions (id, user_id, lifecycle_status, state_json) VALUES "
            f'({sid}, {uid}, \'COMPLETED\', \'{{"user_id": {uid}}}\')')

    def seed(pid, uid, sid, status, created_at):
        con.execute(
            "INSERT INTO core_practice_proposals "
            "(id, user_id, session_id, practice_id, practice_version, purpose, "
            " expected_duration, status, expires_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,datetime('now'),?)",
            (pid, uid, sid, "breathing_box_v1", "v1", "Box breathing", "5 минут",
             status, created_at))

    # Session 10: two old GRANTED rows -- the OLD create_practice_proposal
    # never superseded either (it only ever superseded PROPOSED/PENDING).
    seed(101, 1, 10, "GRANTED", "2026-01-01 00:00:01")
    seed(102, 1, 10, "GRANTED", "2026-01-01 00:00:02")
    # A historical row in the SAME session -- must stay untouched even
    # though its session also has duplicate actionable rows.
    seed(103, 1, 10, "COMPLETED", "2026-01-01 00:00:03")
    # Session 11: an old GRANTED plus a newer PENDING.
    seed(111, 1, 11, "GRANTED", "2026-01-01 00:00:01")
    seed(112, 1, 11, "PENDING", "2026-01-01 00:00:02")
    # Session 12: an old PROPOSED plus a newer GRANTED.
    seed(121, 1, 12, "PROPOSED", "2026-01-01 00:00:01")
    seed(122, 1, 12, "GRANTED", "2026-01-01 00:00:02")
    # Session 13: same user, single actionable row -- control.
    seed(131, 1, 13, "PENDING", "2026-01-01 00:00:01")
    # Session 20: different user, single actionable row -- control.
    seed(201, 2, 20, "PENDING", "2026-01-01 00:00:01")

    con.commit()
    con.close()
    monkeypatch.setattr(database, "DB", path)
    return path


def test_migration_normalizes_two_old_granted_rows_in_one_session(old_db_with_duplicates):
    run(database.init_db())
    p101 = run(database.get_practice_proposal(101, 1))
    p102 = run(database.get_practice_proposal(102, 1))
    assert p102.status.value == "GRANTED", "the newest row wins and keeps its real status"
    assert p101.status.value == "SUPERSEDED"
    assert p101.superseded_reason == "migration_duplicate_actionable"


def test_migration_normalizes_old_granted_plus_newer_pending(old_db_with_duplicates):
    run(database.init_db())
    p111 = run(database.get_practice_proposal(111, 1))
    p112 = run(database.get_practice_proposal(112, 1))
    assert p112.status.value == "PENDING"
    assert p111.status.value == "SUPERSEDED"
    assert p111.superseded_reason == "migration_duplicate_actionable"


def test_migration_normalizes_old_proposed_plus_newer_granted(old_db_with_duplicates):
    run(database.init_db())
    p121 = run(database.get_practice_proposal(121, 1))
    p122 = run(database.get_practice_proposal(122, 1))
    assert p122.status.value == "GRANTED"
    assert p121.status.value == "SUPERSEDED"
    assert p121.superseded_reason == "migration_duplicate_actionable"


def test_migration_duplicate_normalization_does_not_cross_sessions(old_db_with_duplicates):
    run(database.init_db())
    p131 = run(database.get_practice_proposal(131, 1))
    assert p131.status.value == "PENDING", \
        "session 13's single actionable row must be untouched by duplicates in sessions 10-12"


def test_migration_duplicate_normalization_does_not_cross_users(old_db_with_duplicates):
    run(database.init_db())
    p201 = run(database.get_practice_proposal(201, 2))
    assert p201.status.value == "PENDING", "user 2's row must be untouched by user 1's duplicates"


def test_migration_duplicate_normalization_preserves_unrelated_historical_row(old_db_with_duplicates):
    run(database.init_db())
    p103 = run(database.get_practice_proposal(103, 1))
    assert p103.status.value == "COMPLETED", \
        "a COMPLETED row must never be rewritten, even in a session that also has duplicates"


def test_migration_preserves_total_row_count_with_duplicates(old_db_with_duplicates):
    run(database.init_db())
    con = sqlite3.connect(old_db_with_duplicates)
    count = con.execute("SELECT COUNT(*) FROM core_practice_proposals").fetchone()[0]
    con.close()
    assert count == 9, "every row must survive the migration -- normalization never deletes"


def test_migration_installs_a_valid_new_partial_unique_index_with_duplicates(old_db_with_duplicates):
    """The whole point: init_db() must not raise, AND the resulting index
    must actually be enforceable afterward -- a second actionable row for
    an already-normalized session must now be rejected. Also proves, via
    sqlite_master directly, that the rebuilt index (a) belongs to the LIVE
    core_practice_proposals table (not an orphan still attached to some
    other table) and (b) reflects the NEW, wider WHERE clause -- the exact
    proof the index-name-collision bug (see database._rename_old_practice_
    proposals_if_needed) would have failed before it was fixed."""
    run(database.init_db())  # must not raise IntegrityError
    con = sqlite3.connect(old_db_with_duplicates)
    idx_row = con.execute(
        "SELECT tbl_name, sql FROM sqlite_master WHERE type='index' "
        "AND name='idx_core_one_actionable_proposal_per_session'").fetchone()
    assert idx_row is not None, "the index must exist at all -- this is exactly what the collision bug broke"
    tbl_name, idx_sql = idx_row
    assert tbl_name == "core_practice_proposals", \
        "the index must belong to the LIVE table, not an orphan renamed-aside one"
    assert "DELIVERING" in idx_sql, "must be the NEW (wider) index definition, not the old PROPOSED/PENDING-only one"

    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO core_practice_proposals "
            "(user_id, session_id, practice_id, practice_version, purpose, "
            " expected_duration, status, expires_at) "
            "VALUES (1, 10, 'breathing_box_v1', 'v1', 'p', '5 минут', 'GRANTED', datetime('now'))")

    # No orphan artifacts left behind: the renamed-aside temp table is gone,
    # and there is exactly one index by this name in the whole database
    # (not a stale one plus a fresh one under a mangled/duplicate name).
    orphan_table = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (database._OLD_PRACTICE_PROPOSALS_TABLE,)).fetchone()
    assert orphan_table is None, "the renamed-aside old table must not survive migration"
    index_count = con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='index' "
        "AND name='idx_core_one_actionable_proposal_per_session'").fetchone()[0]
    assert index_count == 1, "exactly one index by this name, never a leftover duplicate"
    con.close()


def test_migration_deterministic_winner_uses_id_as_tiebreak(tmp_path, monkeypatch):
    """Two actionable rows with an IDENTICAL created_at -- the tiebreak
    (highest id) must still produce exactly one deterministic winner."""
    path = str(tmp_path / "tie.db")
    con = sqlite3.connect(path)
    con.executescript(_OLD_SCHEMA_DDL)
    con.execute("INSERT INTO users (id, username, first_name) VALUES (1, 'u', 'U')")
    con.execute('INSERT INTO core_sessions (id, user_id, state_json) VALUES (30, 1, \'{"user_id": 1}\')')
    for pid in (301, 302):
        con.execute(
            "INSERT INTO core_practice_proposals "
            "(id, user_id, session_id, practice_id, practice_version, purpose, "
            " expected_duration, status, expires_at, created_at) "
            "VALUES (?, 1, 30, 'breathing_box_v1', 'v1', 'p', '5 минут', 'GRANTED', "
            "datetime('now'), '2026-01-01 00:00:00')",
            (pid,))
    con.commit()
    con.close()
    monkeypatch.setattr(database, "DB", path)
    run(database.init_db())
    p301 = run(database.get_practice_proposal(301, 1))
    p302 = run(database.get_practice_proposal(302, 1))
    assert p302.status.value == "GRANTED", "identical created_at -- the higher id must win"
    assert p301.status.value == "SUPERSEDED"
    assert p301.superseded_reason == "migration_duplicate_actionable"


def test_migration_with_duplicates_is_idempotent_on_second_boot(old_db_with_duplicates):
    run(database.init_db())
    run(database.init_db())
    p101 = run(database.get_practice_proposal(101, 1))
    p102 = run(database.get_practice_proposal(102, 1))
    assert p102.status.value == "GRANTED"
    assert p101.status.value == "SUPERSEDED"


def test_interrupted_migration_with_duplicates_recovers_on_next_boot(old_db_with_duplicates):
    import aiosqlite

    async def simulate_crash_after_rename():
        async with aiosqlite.connect(old_db_with_duplicates) as db:
            await database._rename_old_practice_proposals_if_needed(db)
            await db.commit()
    run(simulate_crash_after_rename())

    run(database.init_db())  # the "next boot"
    p101 = run(database.get_practice_proposal(101, 1))
    p102 = run(database.get_practice_proposal(102, 1))
    assert p102.status.value == "GRANTED"
    assert p101.status.value == "SUPERSEDED"
    con = sqlite3.connect(old_db_with_duplicates)
    count = con.execute("SELECT COUNT(*) FROM core_practice_proposals").fetchone()[0]
    con.close()
    assert count == 9


@pytest.fixture
def in_between_db_with_duplicates(tmp_path, monkeypatch):
    """The in-between shape's OWN partial index (PROPOSED/PENDING/GRANTED)
    already prevents duplicate actionable rows in real use, unlike the
    original pre-PR-73 shape above -- its index already covers GRANTED, and
    DELIVERING cannot exist as data at all under its own CHECK constraint
    (the CHECK and the wider index were introduced together in the same
    rebuild). This fixture drops the index before seeding, not to claim
    real in-between-shaped production data could contain duplicates today,
    but to prove the migration's normalization logic is unconditional --
    it does not silently rely on the old table's own index having caught
    everything."""
    path = str(tmp_path / "inbetween_dup.db")
    con = sqlite3.connect(path)
    con.executescript(_IN_BETWEEN_SCHEMA_DDL)
    con.execute("DROP INDEX idx_core_one_actionable_proposal_per_session")
    con.execute("INSERT INTO users (id, username, first_name) VALUES (1, 'u', 'U')")
    con.execute('INSERT INTO core_sessions (id, user_id, state_json) VALUES (40, 1, \'{"user_id": 1}\')')
    for pid, status, created_at in ((401, "GRANTED", "2026-01-01 00:00:01"),
                                    (402, "GRANTED", "2026-01-01 00:00:02")):
        con.execute(
            "INSERT INTO core_practice_proposals "
            "(id, user_id, session_id, practice_id, practice_version, purpose, "
            " expected_duration, status, expires_at, created_at) "
            "VALUES (?, 1, 40, 'breathing_box_v1', 'v1', 'p', '5 минут', ?, datetime('now'), ?)",
            (pid, status, created_at))
    con.commit()
    con.close()
    monkeypatch.setattr(database, "DB", path)
    return path


def test_in_between_schema_migration_normalizes_duplicate_actionable_rows(in_between_db_with_duplicates):
    run(database.init_db())
    p401 = run(database.get_practice_proposal(401, 1))
    p402 = run(database.get_practice_proposal(402, 1))
    assert p402.status.value == "GRANTED", "newest wins"
    assert p401.status.value == "SUPERSEDED"
    assert p401.superseded_reason == "migration_duplicate_actionable"

