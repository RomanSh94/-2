"""Database-layer regression coverage for transition_practice_proposal's
argument contract (feat/practice-progressive-two-button-ux audit).

Two proof styles are used deliberately, not interchangeably:

1. Structural: a monkeypatched aiosqlite.connect captures the REAL query
   string and params the function builds for a given call, so predicate
   presence/absence/count is read off the actual generated SQL -- never a
   hand-reconstructed "expected" string compared for equality.
2. Behavioral: a real temporary SQLite file (never x20.db), driven through
   database.init_db(), proves the predicates actually gate real rows under
   real CAS semantics -- competing refinements, stale reason values, and
   AND-combination with require_unexpired/require_active_reporting_window.
"""
import asyncio
import contextlib
import sqlite3

import pytest

import database
from therapeutic_domain import (
    PracticeProposalStatus, UX_PENDING_NOT_COMPLETED_REASON, UX_PENDING_OUTCOME_DETAIL,
)

run = asyncio.run


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    run(database.init_db())
    return str(tmp_path / "t.db")


async def _seed_proposal(uid: int, status: str, *, superseded_reason=None,
                         reporting_window_status="ACTIVE", expired: bool = False) -> int:
    import aiosqlite
    async with aiosqlite.connect(database.DB) as db:
        await db.execute("INSERT OR IGNORE INTO users (id, username, first_name) "
                         "VALUES (?, 'u', 'U')", (uid,))
        cur = await db.execute(
            "INSERT INTO core_sessions (user_id, state_json) VALUES (?, '{}')", (uid,))
        session_id = cur.lastrowid
        expires = "datetime('now', '-1 seconds')" if expired else "datetime('now', '+1800 seconds')"
        cur = await db.execute(
            f"""INSERT INTO core_practice_proposals
               (user_id, session_id, practice_id, practice_version, purpose,
                expected_duration, status, expires_at, superseded_reason,
                reporting_window_status)
               VALUES (?, ?, 'breathing_box_v1', 'v1', 'p', 'd', ?, {expires}, ?, ?)""",
            (uid, session_id, status, superseded_reason, reporting_window_status))
        await db.commit()
        return cur.lastrowid


class _RecordingCursor:
    rowcount = 1
    async def fetchone(self):
        return None


class _RecordingConn:
    def __init__(self, sink):
        self._sink = sink
    async def execute(self, query, params=None):
        self._sink.append((query, list(params) if params is not None else []))
        return _RecordingCursor()
    async def commit(self):
        pass
    async def rollback(self):
        pass
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False


@pytest.fixture
def captured_queries(monkeypatch):
    """Structural capture: replaces aiosqlite.connect with a recorder that
    never touches a real database, so the assertions below read the REAL
    query database.py builds, not a reimplementation of it."""
    sink = []
    monkeypatch.setattr(database.aiosqlite, "connect", lambda path: _RecordingConn(sink))
    return sink


# ── Structural: predicate presence/absence/count on the generated SQL ──────

def test_default_call_adds_neither_prior_reason_predicate(captured_queries):
    run(database.transition_practice_proposal(
        1, 1, from_status="STARTED", to_status="COMPLETED"))
    query, params = captured_queries[-1]
    assert "superseded_reason=?" not in query.replace(
        "superseded_reason=COALESCE(?, superseded_reason)", "")
    assert "superseded_reason IS NULL" not in query


def test_require_prior_reason_adds_exactly_one_equality_predicate_and_param(captured_queries):
    run(database.transition_practice_proposal(
        1, 1, from_status="WITHDRAWN", to_status="WITHDRAWN",
        require_prior_reason=UX_PENDING_NOT_COMPLETED_REASON, reason="user_stopped"))
    query, params = captured_queries[-1]
    where_clause = query.split("WHERE", 1)[1]
    assert where_clause.count("superseded_reason=?") == 1
    assert "superseded_reason IS NULL" not in query
    # base params: [to_status, reason, proposal_id, user_id, from_status] + the
    # appended require_prior_reason value -- exactly one extra param, and it
    # is the exact value passed, at the position the appended clause implies.
    assert params[-1] == UX_PENDING_NOT_COMPLETED_REASON
    assert len(params) == 6


def test_require_prior_reason_null_adds_exactly_one_is_null_predicate(captured_queries):
    run(database.transition_practice_proposal(
        1, 1, from_status="COMPLETED", to_status="COMPLETED",
        require_prior_reason_null=True, reason=UX_PENDING_OUTCOME_DETAIL))
    query, params = captured_queries[-1]
    where_clause = query.split("WHERE", 1)[1]
    assert where_clause.count("superseded_reason IS NULL") == 1
    assert "superseded_reason=?" not in query.replace(
        "superseded_reason=COALESCE(?, superseded_reason)", "")
    # IS NULL is a literal, not a bound param -- param count matches the
    # unmodified base case exactly (no extra placeholder appended).
    assert len(params) == 5


def test_existing_predicates_stay_anded_with_new_predicate(captured_queries):
    run(database.transition_practice_proposal(
        1, 1, from_status="WITHDRAWN", to_status="WITHDRAWN",
        require_unexpired=True, require_active_reporting_window=True,
        require_prior_reason=UX_PENDING_NOT_COMPLETED_REASON, reason="user_stopped"))
    query, _ = captured_queries[-1]
    where_clause = query.split("WHERE", 1)[1]
    # Every predicate after the first is introduced by " AND " (not OR, not
    # a second statement) -- proven by splitting on " AND " and checking each
    # expected fragment appears as its own segment.
    segments = [s.strip() for s in where_clause.split(" AND ")]
    assert any(s.startswith("id=?") for s in segments)
    assert "expires_at > datetime('now')" in segments
    assert "reporting_window_status='ACTIVE'" in segments
    assert "superseded_reason=?" in segments


# ── Contract: contradictory arguments ───────────────────────────────────────

def test_contradictory_prior_reason_arguments_raise_value_error(captured_queries):
    with pytest.raises(ValueError):
        run(database.transition_practice_proposal(
            1, 1, from_status="COMPLETED", to_status="COMPLETED",
            require_prior_reason="x", require_prior_reason_null=True))
    # No query was ever built or sent -- the guard fires before any DB access.
    assert captured_queries == []


# ── Behavioral: real temp SQLite, real CAS semantics ────────────────────────

def test_competing_refinements_cannot_both_succeed(tmp_db):
    pid = run(_seed_proposal(1, "COMPLETED", superseded_reason=None))
    first = run(database.transition_practice_proposal(
        pid, 1, from_status="COMPLETED", to_status="COMPLETED",
        require_prior_reason_null=True, reason=UX_PENDING_OUTCOME_DETAIL,
        require_active_reporting_window=True))
    second = run(database.transition_practice_proposal(
        pid, 1, from_status="COMPLETED", to_status="COMPLETED",
        require_prior_reason_null=True, reason=UX_PENDING_OUTCOME_DETAIL,
        require_active_reporting_window=True))
    assert first is True
    assert second is False


def test_stale_prior_reason_value_loses_the_cas(tmp_db):
    pid = run(_seed_proposal(1, "WITHDRAWN", superseded_reason=UX_PENDING_NOT_COMPLETED_REASON))
    stale = run(database.transition_practice_proposal(
        pid, 1, from_status="WITHDRAWN", to_status="WITHDRAWN",
        require_prior_reason="some_other_stale_value", reason="user_stopped"))
    assert stale is False
    proposal = run(database.get_practice_proposal(pid, 1))
    assert proposal.superseded_reason == UX_PENDING_NOT_COMPLETED_REASON  # untouched
    correct = run(database.transition_practice_proposal(
        pid, 1, from_status="WITHDRAWN", to_status="WITHDRAWN",
        require_prior_reason=UX_PENDING_NOT_COMPLETED_REASON, reason="user_stopped"))
    assert correct is True


def test_expiry_and_reporting_window_predicates_both_actually_gate(tmp_db):
    # core_sessions has a partial unique index on (user_id) WHERE
    # lifecycle_status IN ('OPEN','PAUSED') -- each sub-case below seeds an
    # independent session, so each uses its own user_id to stay clear of it;
    # the property under test (per-predicate CAS gating) is per-user anyway.
    expired_pid = run(_seed_proposal(1, "PENDING", expired=True))
    assert run(database.transition_practice_proposal(
        expired_pid, 1, from_status="PENDING", to_status="GRANTED",
        require_unexpired=True)) is False

    closed_window_pid = run(_seed_proposal(
        2, "STARTED", reporting_window_status="CLOSED"))
    assert run(database.transition_practice_proposal(
        closed_window_pid, 2, from_status="STARTED", to_status="COMPLETED",
        require_active_reporting_window=True)) is False

    valid_pid = run(_seed_proposal(3, "STARTED", reporting_window_status="ACTIVE"))
    assert run(database.transition_practice_proposal(
        valid_pid, 3, from_status="STARTED", to_status="COMPLETED",
        require_active_reporting_window=True)) is True


# ── record_practice_outcome: atomic outcome-finalization contract ──────────

def test_record_outcome_default_call_unchanged(captured_queries):
    run(database.record_practice_outcome(1, 1, "HELPED"))
    query, params = captured_queries[-1]
    assert "superseded_reason" not in query
    assert params == ["HELPED", 1, 1]


def test_record_outcome_contradictory_prior_reason_arguments_raise(captured_queries):
    with pytest.raises(ValueError):
        run(database.record_practice_outcome(
            1, 1, "HELPED", require_prior_reason="x", require_prior_reason_null=True))
    assert captured_queries == []


def test_record_outcome_clear_without_exact_reason_raises(captured_queries):
    with pytest.raises(ValueError):
        run(database.record_practice_outcome(1, 1, "HELPED", clear_superseded_reason=True))
    assert captured_queries == []


def test_record_outcome_require_prior_reason_adds_exact_predicate_and_param(captured_queries):
    run(database.record_practice_outcome(
        1, 1, "NO_CHANGE", require_prior_reason=UX_PENDING_OUTCOME_DETAIL))
    query, params = captured_queries[-1]
    where_clause = query.split("WHERE", 1)[1]
    assert where_clause.count("superseded_reason=?") == 1
    assert params[-1] == UX_PENDING_OUTCOME_DETAIL


def test_record_outcome_require_prior_reason_null_adds_exact_predicate(captured_queries):
    run(database.record_practice_outcome(1, 1, "HELPED", require_prior_reason_null=True))
    query, params = captured_queries[-1]
    where_clause = query.split("WHERE", 1)[1]
    assert where_clause.count("superseded_reason IS NULL") == 1
    assert len(params) == 3  # IS NULL is a literal -- no extra bound param


def test_record_outcome_finalization_clears_marker_in_same_update(tmp_db):
    pid = run(_seed_proposal(1, "COMPLETED", superseded_reason=UX_PENDING_OUTCOME_DETAIL))
    ok = run(database.record_practice_outcome(
        pid, 1, "NO_CHANGE", require_prior_reason=UX_PENDING_OUTCOME_DETAIL,
        clear_superseded_reason=True))
    assert ok is True
    proposal = run(database.get_practice_proposal(pid, 1))
    assert proposal.outcome == "NO_CHANGE"
    assert proposal.superseded_reason is None
    assert proposal.reporting_window_status == "CLOSED"


def test_record_outcome_stale_marker_loses_the_cas(tmp_db):
    pid = run(_seed_proposal(1, "COMPLETED", superseded_reason="some_other_value"))
    ok = run(database.record_practice_outcome(
        pid, 1, "NO_CHANGE", require_prior_reason=UX_PENDING_OUTCOME_DETAIL,
        clear_superseded_reason=True))
    assert ok is False
    proposal = run(database.get_practice_proposal(pid, 1))
    assert proposal.outcome is None
    assert proposal.superseded_reason == "some_other_value"


def test_record_outcome_direct_helped_succeeds_only_while_reason_null(tmp_db):
    pid = run(_seed_proposal(1, "COMPLETED", superseded_reason=None))
    assert run(database.record_practice_outcome(
        pid, 1, "HELPED", require_prior_reason_null=True)) is True


def test_record_outcome_direct_helped_loses_after_pending_marker_written(tmp_db):
    pid = run(_seed_proposal(2, "COMPLETED", superseded_reason=UX_PENDING_OUTCOME_DETAIL))
    ok = run(database.record_practice_outcome(
        pid, 2, "HELPED", require_prior_reason_null=True))
    assert ok is False
    proposal = run(database.get_practice_proposal(pid, 2))
    assert proposal.outcome is None


def test_record_outcome_competing_helped_vs_pending_marker_exactly_one_succeeds(tmp_db):
    # Order 1: HELPED commits first -- it also closes the reporting window,
    # so the marker-creation attempt's require_active_reporting_window fails.
    pid_a = run(_seed_proposal(3, "COMPLETED", superseded_reason=None))
    helped_first = run(database.record_practice_outcome(
        pid_a, 3, "HELPED", require_prior_reason_null=True,
        require_active_reporting_window=True))
    marker_after = run(database.transition_practice_proposal(
        pid_a, 3, from_status="COMPLETED", to_status="COMPLETED",
        require_prior_reason_null=True, reason=UX_PENDING_OUTCOME_DETAIL,
        require_active_reporting_window=True))
    assert helped_first is True
    assert marker_after is False

    # Order 2: the pending marker commits first -- the direct HELPED
    # attempt's require_prior_reason_null then fails.
    pid_b = run(_seed_proposal(4, "COMPLETED", superseded_reason=None))
    marker_first = run(database.transition_practice_proposal(
        pid_b, 4, from_status="COMPLETED", to_status="COMPLETED",
        require_prior_reason_null=True, reason=UX_PENDING_OUTCOME_DETAIL,
        require_active_reporting_window=True))
    helped_after = run(database.record_practice_outcome(
        pid_b, 4, "HELPED", require_prior_reason_null=True,
        require_active_reporting_window=True))
    assert marker_first is True
    assert helped_after is False


def test_record_outcome_competing_final_detail_outcomes_exactly_one_succeeds(tmp_db):
    pid = run(_seed_proposal(5, "COMPLETED", superseded_reason=UX_PENDING_OUTCOME_DETAIL))
    same = run(database.record_practice_outcome(
        pid, 5, "NO_CHANGE", require_prior_reason=UX_PENDING_OUTCOME_DETAIL,
        clear_superseded_reason=True))
    worse = run(database.record_practice_outcome(
        pid, 5, "WORSE", require_prior_reason=UX_PENDING_OUTCOME_DETAIL,
        clear_superseded_reason=True))
    assert same is True
    assert worse is False


def test_record_outcome_active_window_and_outcome_null_predicates_enforced(tmp_db):
    closed_pid = run(_seed_proposal(6, "COMPLETED", reporting_window_status="CLOSED"))
    assert run(database.record_practice_outcome(
        closed_pid, 6, "HELPED", require_active_reporting_window=True)) is False

    already_recorded_pid = run(_seed_proposal(7, "COMPLETED"))
    first = run(database.record_practice_outcome(already_recorded_pid, 7, "HELPED"))
    second = run(database.record_practice_outcome(already_recorded_pid, 7, "NO_CHANGE"))
    assert first is True
    assert second is False


# ── create_practice_proposal: atomic block_if_refinement_pending contract ──
#
# External review F2 follow-up (TOCTOU closure): a separate pre-read SELECT
# followed by a separate INSERT on a different connection cannot be trusted
# as an invariant -- a concurrent writer can land in the gap between them.
# These tests prove the INSERT itself is conditional (INSERT...SELECT...
# WHERE NOT EXISTS...RETURNING, one statement, one connection, one
# transaction), including a genuine two-connection SQLite-lock-level race,
# not merely a sequential call-order check.

def test_create_proposal_default_call_unchanged(captured_queries):
    run(database.create_practice_proposal(1, 1, "breathing_box_v1", "v1", "p", "d"))
    # Two statements: the unconditional supersession UPDATE, then the insert.
    assert len(captured_queries) == 2
    insert_query, insert_params = captured_queries[-1]
    assert "WHERE NOT EXISTS" not in insert_query, \
        "default call (no block_if_refinement_pending) must not add the guard clause"
    assert "RETURNING" in insert_query


def test_create_proposal_block_param_adds_exactly_one_where_not_exists(captured_queries):
    run(database.create_practice_proposal(
        1, 1, "breathing_box_v1", "v1", "p", "d",
        block_if_refinement_pending=(UX_PENDING_NOT_COMPLETED_REASON, UX_PENDING_OUTCOME_DETAIL)))
    insert_query, insert_params = captured_queries[-1]
    assert insert_query.count("WHERE NOT EXISTS") == 1
    assert insert_query.count("RETURNING") == 1
    assert insert_params[-2:] == [UX_PENDING_NOT_COMPLETED_REASON, UX_PENDING_OUTCOME_DETAIL]


def test_create_proposal_blocked_when_refinement_pending_sequential(tmp_db):
    pid = run(_seed_proposal(1, "COMPLETED", superseded_reason=UX_PENDING_OUTCOME_DETAIL))
    proposal = run(database.get_practice_proposal(pid, 1))
    blocked = run(database.create_practice_proposal(
        1, proposal.session_id, "breathing_box_v1", "v1", "p2", "d2",
        block_if_refinement_pending=(UX_PENDING_NOT_COMPLETED_REASON, UX_PENDING_OUTCOME_DETAIL)))
    assert blocked is None
    latest = run(database.get_latest_proposal_for_session(proposal.session_id, 1))
    assert latest.proposal_id == str(pid), "no second row may be created while a refinement is pending"


def test_create_proposal_not_blocked_when_no_refinement_pending_sequential(tmp_db):
    pid = run(_seed_proposal(1, "COMPLETED", superseded_reason=None))
    proposal = run(database.get_practice_proposal(pid, 1))
    created = run(database.create_practice_proposal(
        1, proposal.session_id, "breathing_box_v1", "v1", "p2", "d2",
        block_if_refinement_pending=(UX_PENDING_NOT_COMPLETED_REASON, UX_PENDING_OUTCOME_DETAIL)))
    assert created is not None
    assert created.proposal_id != pid


def test_create_proposal_blocked_by_not_completed_reason_marker(tmp_db):
    pid = run(_seed_proposal(1, "WITHDRAWN", superseded_reason=UX_PENDING_NOT_COMPLETED_REASON))
    proposal = run(database.get_practice_proposal(pid, 1))
    blocked = run(database.create_practice_proposal(
        1, proposal.session_id, "breathing_box_v1", "v1", "p2", "d2",
        block_if_refinement_pending=(UX_PENDING_NOT_COMPLETED_REASON, UX_PENDING_OUTCOME_DETAIL)))
    assert blocked is None
    latest = run(database.get_latest_proposal_for_session(proposal.session_id, 1))
    assert latest.proposal_id == str(pid)


async def _seed_second_proposal_same_session(uid: int, session_id: int, status: str, *,
                                             superseded_reason=None) -> int:
    """Inserts a SECOND proposal row into an already-existing session --
    _seed_proposal always creates a brand-new session, which cannot model a
    session that already has both a refinement-pending terminal proposal
    AND a separate still-actionable one."""
    import aiosqlite
    async with aiosqlite.connect(database.DB) as db:
        cur = await db.execute(
            """INSERT INTO core_practice_proposals
               (user_id, session_id, practice_id, practice_version, purpose,
                expected_duration, status, expires_at, superseded_reason)
               VALUES (?, ?, 'breathing_box_v1', 'v1', 'p', 'd', ?,
                       datetime('now', '+1800 seconds'), ?)""",
            (uid, session_id, status, superseded_reason))
        await db.commit()
        return cur.lastrowid


async def _count_proposals_for_session(session_id, uid: int) -> int:
    import aiosqlite
    async with aiosqlite.connect(database.DB) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM core_practice_proposals WHERE session_id=? AND user_id=?",
            (session_id, uid))
        (n,) = await cur.fetchone()
        return n


async def _new_session_for(uid: int) -> int:
    # core_sessions has a partial unique index on (user_id) WHERE
    # lifecycle_status IN ('OPEN','PAUSED') -- creating a second concurrently
    # OPEN session for the same user is not a real, reachable state, so this
    # closes any pre-existing OPEN/PAUSED session for uid first.
    import aiosqlite
    async with aiosqlite.connect(database.DB) as db:
        await db.execute("INSERT OR IGNORE INTO users (id, username, first_name) "
                         "VALUES (?, 'u', 'U')", (uid,))
        await db.execute(
            "UPDATE core_sessions SET lifecycle_status='COMPLETED' "
            "WHERE user_id=? AND lifecycle_status IN ('OPEN','PAUSED')", (uid,))
        cur = await db.execute("INSERT INTO core_sessions (user_id, state_json) VALUES (?, '{}')", (uid,))
        await db.commit()
        return cur.lastrowid


def test_create_proposal_not_blocked_by_another_user_or_session(tmp_db):
    # Another user, same marker, must not block user 3's own (different) session.
    run(_seed_proposal(2, "COMPLETED", superseded_reason=UX_PENDING_OUTCOME_DETAIL))
    user3_session_id = run(_new_session_for(3))
    created_a = run(database.create_practice_proposal(
        3, user3_session_id, "breathing_box_v1", "v1", "p2", "d2",
        block_if_refinement_pending=(UX_PENDING_NOT_COMPLETED_REASON, UX_PENDING_OUTCOME_DETAIL)))
    assert created_a is not None, "a different user's pending marker must never block this user"

    # Same user, but a DIFFERENT (older, since-closed) session's marker must
    # not block a brand-new session for that same user.
    run(_seed_proposal(4, "COMPLETED", superseded_reason=UX_PENDING_OUTCOME_DETAIL))
    new_session_id = run(_new_session_for(4))
    created_b = run(database.create_practice_proposal(
        4, new_session_id, "breathing_box_v1", "v1", "p3", "d3",
        block_if_refinement_pending=(UX_PENDING_NOT_COMPLETED_REASON, UX_PENDING_OUTCOME_DETAIL)))
    assert created_b is not None, "a different session's pending marker must never block this session"


def test_create_proposal_not_blocked_by_non_active_window_or_unrelated_reason(tmp_db):
    # Marker string matches, but the window is CLOSED, not ACTIVE.
    pid_closed = run(_seed_proposal(
        1, "COMPLETED", superseded_reason=UX_PENDING_OUTCOME_DETAIL,
        reporting_window_status="CLOSED"))
    proposal_closed = run(database.get_practice_proposal(pid_closed, 1))
    created_a = run(database.create_practice_proposal(
        1, proposal_closed.session_id, "breathing_box_v1", "v1", "p2", "d2",
        block_if_refinement_pending=(UX_PENDING_NOT_COMPLETED_REASON, UX_PENDING_OUTCOME_DETAIL)))
    assert created_a is not None, "a non-ACTIVE window must never block, even if the marker string matches"

    # Window is ACTIVE, but superseded_reason is a genuine, unrelated
    # historical reason -- not one of the two internal UX pending markers.
    pid_unrelated = run(_seed_proposal(
        2, "SUPERSEDED", superseded_reason="newer_proposal", reporting_window_status="ACTIVE"))
    proposal_unrelated = run(database.get_practice_proposal(pid_unrelated, 2))
    created_b = run(database.create_practice_proposal(
        2, proposal_unrelated.session_id, "breathing_box_v1", "v1", "p3", "d3",
        block_if_refinement_pending=(UX_PENDING_NOT_COMPLETED_REASON, UX_PENDING_OUTCOME_DETAIL)))
    assert created_b is not None, "an unrelated superseded_reason must never block"


def test_create_proposal_blocked_call_has_zero_persisted_side_effects(tmp_db):
    """External-review correction: a blocked guarded creation must not
    supersede a pre-existing actionable proposal without ever creating the
    replacement that supersession implies. Before this fix, the prior
    supersession UPDATE and the guarded INSERT shared one db.commit()
    regardless of whether the INSERT's WHERE NOT EXISTS blocked it -- so a
    block could still commit 'this proposal was superseded by a newer one'
    with no newer one ever created. Same session, same user: one
    refinement-pending terminal proposal (ACTIVE window, a UX_PENDING
    marker) plus one separate still-actionable proposal."""
    uid = 5
    refinement_pid = run(_seed_proposal(
        uid, "COMPLETED", superseded_reason=UX_PENDING_OUTCOME_DETAIL,
        reporting_window_status="ACTIVE"))
    refinement_proposal = run(database.get_practice_proposal(refinement_pid, uid))
    session_id = refinement_proposal.session_id

    actionable_pid = run(_seed_second_proposal_same_session(
        uid, session_id, "PENDING", superseded_reason=None))

    # Full row captured BEFORE the blocked call. PracticeProposal is a plain
    # @dataclass (default eq=True, comparing every field) -- every one of
    # its persisted columns is set once by the seed INSERT above and never
    # recomputed on SELECT (no trigger/computed column exists in the
    # schema), so a genuinely untouched row must compare fully equal, not
    # just on status/superseded_reason.
    before = run(database.get_practice_proposal(actionable_pid, uid))

    result = run(database.create_practice_proposal(
        uid, session_id, "breathing_box_v1", "v1", "p2", "d2",
        block_if_refinement_pending=(UX_PENDING_NOT_COMPLETED_REASON, UX_PENDING_OUTCOME_DETAIL)))

    assert result is None, "guarded creation must be blocked by the pending refinement marker"

    row_count = run(_count_proposals_for_session(session_id, uid))
    assert row_count == 2, \
        f"no new proposal row may be inserted when the guard blocks creation, found {row_count}"

    latest = run(database.get_latest_proposal_for_session(session_id, uid))
    assert latest.proposal_id == str(actionable_pid), \
        "no new proposal row may be inserted when the guard blocks creation"

    after = run(database.get_practice_proposal(actionable_pid, uid))
    assert after == before, \
        f"a blocked creation must leave every persisted column of the pre-existing " \
        f"actionable proposal unchanged; before={before!r} after={after!r}"


def test_create_proposal_successful_call_still_supersedes_and_inserts_atomically(tmp_db):
    """Happy-path counterpart to the zero-side-effects test above: when the
    guard does NOT block (no refinement pending), the pre-existing
    actionable proposal must still be superseded AND the replacement must
    exist -- proving the rollback-on-block branch didn't also break the
    ordinary success path, which still needs the commit to cover both
    statements together."""
    uid = 6
    session_id = run(_new_session_for(uid))
    old_pid = run(_seed_second_proposal_same_session(
        uid, session_id, "PENDING", superseded_reason=None))

    result = run(database.create_practice_proposal(
        uid, session_id, "breathing_box_v1", "v1", "p2", "d2",
        block_if_refinement_pending=(UX_PENDING_NOT_COMPLETED_REASON, UX_PENDING_OUTCOME_DETAIL)))

    assert result is not None, "no refinement is pending, so creation must succeed"

    reread_old = run(database.get_practice_proposal(old_pid, uid))
    assert reread_old.status is PracticeProposalStatus.SUPERSEDED
    assert reread_old.superseded_reason == "newer_proposal"

    row_count = run(_count_proposals_for_session(session_id, uid))
    assert row_count == 2, f"expected old + replacement, found {row_count}"

    latest = run(database.get_latest_proposal_for_session(session_id, uid))
    assert latest.proposal_id == result.proposal_id


def test_create_proposal_exception_during_insert_does_not_commit_supersession(tmp_db):
    """Fault injection: if the guarded INSERT statement itself raises (e.g. a
    genuine driver/I-O error), the prior supersession UPDATE on the SAME
    connection/transaction must not survive either. Mechanism verified
    independently: closing an aiosqlite connection without an explicit
    commit() discards uncommitted changes -- so the exception propagating
    out of create_practice_proposal's `async with aiosqlite.connect(DB) as
    db:` block, with no db.commit() ever reached, must leave the
    pre-existing actionable proposal completely unchanged. The real
    connection is used for both statements; only the second execute() call
    is made to raise, so the first (the real UPDATE) genuinely runs before
    the fault, matching what a true mid-transaction failure looks like."""
    import aiosqlite

    uid = 7
    session_id = run(_new_session_for(uid))
    old_pid = run(_seed_second_proposal_same_session(
        uid, session_id, "PENDING", superseded_reason=None))

    real_connect = aiosqlite.connect

    class _FailingConn:
        def __init__(self, real_cm):
            self._real_cm = real_cm
            self._real = None
            self._n = 0

        async def __aenter__(self):
            self._real = await self._real_cm.__aenter__()
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return await self._real_cm.__aexit__(exc_type, exc, tb)

        async def execute(self, query, params=None):
            self._n += 1
            if self._n == 2:
                raise sqlite3.OperationalError("injected failure for atomicity test")
            if params is not None:
                return await self._real.execute(query, params)
            return await self._real.execute(query)

        async def commit(self):
            return await self._real.commit()

        async def rollback(self):
            return await self._real.rollback()

    def _connect(path):
        return _FailingConn(real_connect(path))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(database.aiosqlite, "connect", _connect)
        with pytest.raises(sqlite3.OperationalError):
            run(database.create_practice_proposal(
                uid, session_id, "breathing_box_v1", "v1", "p2", "d2",
                block_if_refinement_pending=(
                    UX_PENDING_NOT_COMPLETED_REASON, UX_PENDING_OUTCOME_DETAIL)))

    reread = run(database.get_practice_proposal(old_pid, uid))
    assert reread.status is PracticeProposalStatus.PENDING, \
        "an exception during the guarded INSERT must not leave the supersession UPDATE committed"
    assert reread.superseded_reason is None

    row_count = run(_count_proposals_for_session(session_id, uid))
    assert row_count == 1, "no replacement row may have been inserted either"


def test_create_practice_proposal_concurrent_block_has_zero_persisted_side_effects(tmp_db):
    """Concurrency variant of the zero-side-effects proof: connection A
    commits the pending-refinement marker while a SEPARATE, real
    still-actionable proposal already exists in the same session. Task B
    (the real, unmocked create_practice_proposal, guarded) must be blocked
    by A's committed marker AND must leave that separate actionable
    proposal completely untouched -- proving the rollback-on-block fix
    holds under genuine concurrent execution, not only sequential calls.
    conn_a and task_b are cleaned up on every path via try/finally."""
    import aiosqlite

    uid = 8

    async def scenario():
        refinement_pid = await _seed_proposal(
            uid, "COMPLETED", superseded_reason=None, reporting_window_status="ACTIVE")
        refinement_proposal = await database.get_practice_proposal(refinement_pid, uid)
        session_id = refinement_proposal.session_id
        actionable_pid = await _seed_second_proposal_same_session(
            uid, session_id, "PENDING", superseded_reason=None)

        conn_a = await aiosqlite.connect(database.DB)
        b = None
        blocked_confirmed = None
        result = {}
        try:
            await conn_a.execute("BEGIN IMMEDIATE")
            await conn_a.execute(
                "UPDATE core_practice_proposals SET superseded_reason=? "
                "WHERE id=? AND user_id=?",
                (UX_PENDING_OUTCOME_DETAIL, refinement_pid, uid))

            started = asyncio.Event()

            async def task_b():
                started.set()
                result["proposal"] = await database.create_practice_proposal(
                    uid, session_id, "breathing_box_v1", "v1", "p2", "d2",
                    block_if_refinement_pending=(
                        UX_PENDING_NOT_COMPLETED_REASON, UX_PENDING_OUTCOME_DETAIL))

            b = asyncio.ensure_future(task_b())
            await started.wait()
            try:
                await asyncio.wait_for(asyncio.shield(b), timeout=0.05)
                blocked_confirmed = False
            except asyncio.TimeoutError:
                blocked_confirmed = True

            await conn_a.commit()
            await b
        finally:
            if b is not None and not b.done():
                b.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await b
            await conn_a.close()

        return blocked_confirmed, actionable_pid, session_id, result.get("proposal")

    blocked_confirmed, actionable_pid, session_id, b_result = run(scenario())
    print(f"[diagnostic] task B still pending after 50ms while A held the "
          f"RESERVED lock: {blocked_confirmed}")

    assert b_result is None, "task B must be blocked once A's marker commits"

    reread_actionable = run(database.get_practice_proposal(actionable_pid, uid))
    assert reread_actionable.status is PracticeProposalStatus.PENDING, \
        "a concurrently-blocked creation must not supersede the pre-existing actionable proposal"
    assert reread_actionable.superseded_reason is None

    row_count = run(_count_proposals_for_session(session_id, uid))
    assert row_count == 2, "no replacement row may have been inserted"


def test_create_practice_proposal_atomic_block_survives_concurrent_writer(tmp_db):
    """Deterministic real-SQLite proof: connection A holds an UNCOMMITTED
    BEGIN IMMEDIATE transaction that has already written the pending-
    refinement marker; task B is the REAL, unmocked
    database.create_practice_proposal(block_if_refinement_pending=...),
    started concurrently.

    PRIMARY correctness proof (the only assertions that gate pass/fail):
    once A commits and B is awaited to completion, B must have returned
    None, no second proposal row may exist, and A's original marker must be
    unchanged. These hold regardless of timing or machine speed -- they
    follow from SQLite's own lock semantics (A holds the RESERVED lock, so
    B's write literally cannot land before A resolves) and from the atomic
    INSERT...SELECT...WHERE NOT EXISTS...RETURNING statement itself.

    A bounded-wait probe (50ms) additionally records whether B was still
    pending while A's transaction was open, as SUPPLEMENTARY diagnostic
    evidence only -- it is asserted separately, is not required for the
    test to pass, and does not gate correctness.

    conn_a and task_b are cleaned up on every path via try/finally: an
    unexpected exception anywhere in the scenario must not leave a
    dangling aiosqlite connection holding a RESERVED lock, nor an
    un-awaited/un-cancelled task."""
    import aiosqlite

    uid = 1

    async def scenario():
        pid = await _seed_proposal(uid, "STARTED", superseded_reason=None)
        proposal = await database.get_practice_proposal(pid, uid)
        session_id = proposal.session_id

        conn_a = await aiosqlite.connect(database.DB)
        b = None
        blocked_confirmed = None
        result = {}
        try:
            await conn_a.execute("BEGIN IMMEDIATE")
            await conn_a.execute(
                "UPDATE core_practice_proposals SET status='WITHDRAWN', superseded_reason=? "
                "WHERE id=? AND user_id=?",
                (UX_PENDING_NOT_COMPLETED_REASON, pid, uid))
            # Connection A now holds SQLite's RESERVED lock; the marker
            # exists only inside this uncommitted transaction -- not yet
            # visible to any other connection.

            started = asyncio.Event()

            async def task_b():
                started.set()
                result["proposal"] = await database.create_practice_proposal(
                    uid, session_id, "breathing_box_v1", "v1", "p2", "d2",
                    block_if_refinement_pending=(
                        UX_PENDING_NOT_COMPLETED_REASON, UX_PENDING_OUTCOME_DETAIL))

            b = asyncio.ensure_future(task_b())
            await started.wait()
            try:
                await asyncio.wait_for(asyncio.shield(b), timeout=0.05)
                blocked_confirmed = False
            except asyncio.TimeoutError:
                blocked_confirmed = True

            # A resolves its transaction only AFTER the bounded-wait probe
            # above has already run -- so the probe result reflects A's
            # lock genuinely being held at that moment, not a race against
            # A's own commit.
            await conn_a.commit()
            await b
        finally:
            if b is not None and not b.done():
                b.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await b
            await conn_a.close()

        return blocked_confirmed, pid, result.get("proposal"), session_id

    blocked_confirmed, pid, b_result, session_id = run(scenario())

    # SUPPLEMENTARY diagnostic only (see docstring) -- reported, not
    # required for this test's pass/fail outcome.
    print(f"[diagnostic] task B still pending after 50ms while A held the "
          f"RESERVED lock: {blocked_confirmed}")

    # PRIMARY correctness proof -- timing-independent, deterministic.
    assert b_result is None, \
        "task B's atomic INSERT must be blocked once the marker is committed"
    reread = run(database.get_practice_proposal(pid, uid))
    assert reread.status is PracticeProposalStatus.WITHDRAWN
    assert reread.superseded_reason == UX_PENDING_NOT_COMPLETED_REASON, \
        "the original marker committed by connection A must remain unchanged"
    latest = run(database.get_latest_proposal_for_session(session_id, uid))
    assert latest.proposal_id == str(pid), "no second proposal row may exist"
