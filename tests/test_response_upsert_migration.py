"""PR #58 — atomic unique response upsert + legacy-duplicate migration.

Invariant: UNIQUE(session_id, item_id) — one CURRENT answer per item per
session. The migration dedupes legacy duplicates (keep highest id = most
recent), creates the unique index, and is idempotent; the write path is a
single atomic UPSERT. Synthetic data only.
"""
import asyncio
import sqlite3

import pytest

import database


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    return database.DB


def _raw_insert(db_path, session_id, item_id, answer_id, value, uid=1):
    """Simulate a PRE-migration legacy row (bypasses the upsert)."""
    con = sqlite3.connect(db_path)
    con.execute("DROP INDEX IF EXISTS idx_qresponses_session_item")
    con.execute(
        "INSERT INTO questionnaire_responses "
        "(user_id, session_id, questionnaire_id, item_id, answer_id, answer_value) "
        "VALUES (?,?,?,?,?,?)", (uid, session_id, "demo_v1", item_id, answer_id, value))
    con.commit()
    con.close()


def _rows(db_path, session_id=None):
    con = sqlite3.connect(db_path)
    q = ("SELECT id, session_id, item_id, answer_id, answer_value "
         "FROM questionnaire_responses")
    if session_id is not None:
        q += f" WHERE session_id={int(session_id)}"
    rows = con.execute(q + " ORDER BY id").fetchall()
    con.close()
    return rows


def _dup_groups(db_path):
    con = sqlite3.connect(db_path)
    n = con.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM questionnaire_responses "
        "GROUP BY session_id, item_id HAVING COUNT(*) > 1)").fetchone()[0]
    con.close()
    return n


def _index_present(db_path):
    con = sqlite3.connect(db_path)
    names = [r[1] for r in con.execute(
        "PRAGMA index_list(questionnaire_responses)").fetchall()]
    con.close()
    return "idx_qresponses_session_item" in names


# ── migration ─────────────────────────────────────────────────────────────────
def test_migration_dedupes_pair_keeps_latest(db):
    _raw_insert(db, 1, "item_1", "a0", "0")      # older
    _raw_insert(db, 1, "item_1", "a1", "1")      # newer duplicate (higher id)
    _raw_insert(db, 1, "item_2", "a2", "2")      # distinct item — untouched
    _raw_insert(db, 2, "item_1", "a3", "3")      # other session — untouched
    assert _dup_groups(db) == 1
    asyncio.run(database.init_db())               # rerun boot migration
    assert _dup_groups(db) == 0
    s1 = _rows(db, 1)
    assert [(r[2], r[3], r[4]) for r in s1] == [
        ("item_1", "a1", "1"),                    # latest kept
        ("item_2", "a2", "2")]                    # distinct untouched
    assert [(r[2], r[3]) for r in _rows(db, 2)] == [("item_1", "a3")]


def test_migration_is_idempotent(db):
    _raw_insert(db, 1, "item_1", "a0", "0")
    _raw_insert(db, 1, "item_1", "a1", "1")
    asyncio.run(database.init_db())
    first = _rows(db)
    asyncio.run(database.init_db())               # rerun: no further change
    assert _rows(db) == first
    assert _index_present(db)


def test_migration_creates_unique_index_and_it_rejects_duplicates(db):
    assert _index_present(db)
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO questionnaire_responses "
        "(user_id, session_id, questionnaire_id, item_id, answer_id, answer_value) "
        "VALUES (1, 9, 'demo_v1', 'x', 'a0', '0')")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO questionnaire_responses "
            "(user_id, session_id, questionnaire_id, item_id, answer_id, answer_value) "
            "VALUES (1, 9, 'demo_v1', 'x', 'a1', '1')")
    con.close()


def test_migration_does_not_touch_sessions(db):
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO questionnaire_sessions "
        "(user_id, questionnaire_id, questionnaire_version, status, current_index) "
        "VALUES (1, 'demo_v1', 'v1', 'active', 4)")
    con.commit()
    con.close()
    _raw_insert(db, 1, "item_1", "a0", "0")
    _raw_insert(db, 1, "item_1", "a1", "1")
    asyncio.run(database.init_db())
    con = sqlite3.connect(db)
    status, idx = con.execute(
        "SELECT status, current_index FROM questionnaire_sessions").fetchone()
    con.close()
    assert (status, idx) == ("active", 4)         # session untouched


# ── upsert write path ─────────────────────────────────────────────────────────
def _record(uid, sid, item, aid, val):
    asyncio.run(database.record_questionnaire_response(
        uid, sid, "demo_v1", item, aid, val))


def test_first_answer_inserts_one_row(db):
    _record(1, 1, "item_1", "a0", "0")
    assert len(_rows(db, 1)) == 1


def test_same_answer_retry_remains_one_row(db):
    _record(1, 1, "item_1", "a0", "0")
    _record(1, 1, "item_1", "a0", "0")
    rows = _rows(db, 1)
    assert len(rows) == 1 and rows[0][3:] == ("a0", "0")


def test_replacement_updates_id_and_value_one_row(db):
    _record(1, 1, "item_1", "a0", "0")
    row_id = _rows(db, 1)[0][0]
    _record(1, 1, "item_1", "a3", "3")
    rows = _rows(db, 1)
    assert len(rows) == 1
    assert rows[0][0] == row_id                   # same row updated in place
    assert rows[0][3:] == ("a3", "3")             # replacement wins


def test_other_item_and_session_untouched(db):
    _record(1, 1, "item_1", "a0", "0")
    _record(1, 1, "item_2", "a1", "1")
    _record(2, 2, "item_1", "a2", "2")
    _record(1, 1, "item_1", "a3", "3")            # replace only s1/item_1
    assert [(r[2], r[3]) for r in _rows(db, 1)] == [
        ("item_1", "a3"), ("item_2", "a1")]
    assert [(r[2], r[3]) for r in _rows(db, 2)] == [("item_1", "a2")]


def test_ordering_stable_after_update(db):
    _record(1, 1, "item_1", "a0", "0")
    _record(1, 1, "item_2", "a1", "1")
    _record(1, 1, "item_1", "a3", "3")            # update keeps original id
    assert [r[2] for r in _rows(db, 1)] == ["item_1", "item_2"]


def test_rapid_interleaved_replacements_keep_single_row(db):
    for aid, val in (("a0", "0"), ("a1", "1"), ("a2", "2"), ("a3", "3")):
        _record(1, 1, "item_1", aid, val)
    rows = _rows(db, 1)
    assert len(rows) == 1 and rows[0][3:] == ("a3", "3")
    assert _dup_groups(db) == 0


def test_concurrent_sessions_stay_isolated(db):
    _record(1, 1, "item_1", "a0", "0")
    _record(2, 2, "item_1", "a3", "3")
    _record(1, 1, "item_1", "a1", "1")
    assert [r[3] for r in _rows(db, 1)] == ["a1"]
    assert [r[3] for r in _rows(db, 2)] == ["a3"]


# ── export/delete compatibility ───────────────────────────────────────────────
def test_export_contains_replacement_not_old_answer(db):
    _record(1, 1, "item_1", "a0", "0")
    _record(1, 1, "item_1", "a2", "2")
    data = asyncio.run(database.export_all_personal_data(1))
    answers = [(r.get("item_id"), r.get("answer_id"))
               for r in data["questionnaire_responses"]]
    assert ("item_1", "a2") in answers
    assert ("item_1", "a0") not in answers


def test_delete_removes_replacement_row(db):
    _record(1, 1, "item_1", "a0", "0")
    _record(1, 1, "item_1", "a2", "2")
    asyncio.run(database.delete_all_personal_data(1))
    assert _rows(db, 1) == []


# ── strict scorer preserved ───────────────────────────────────────────────────
def test_retrieval_returns_stored_truth_no_silent_dedupe():
    import inspect
    src = inspect.getsource(database.get_questionnaire_responses)
    assert "DISTINCT" not in src.upper().replace("SELECT ITEM_ID", "")
    assert "GROUP BY" not in src.upper()
