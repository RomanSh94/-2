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

import pytest

import database
from therapeutic_domain import UX_PENDING_NOT_COMPLETED_REASON, UX_PENDING_OUTCOME_DETAIL

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
