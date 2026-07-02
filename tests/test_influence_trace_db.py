"""A1 / PR 0 — the REAL DB binding for the influence trace (not just injected fakes).

Proves: init_db creates influence_trace; log_influence_trace persists and RAISES on
failure (so fail-closed works); and traced_response_builder wired to the real
persist_influence_trace lands content-ful rows the psychologist can read back.
"""
import asyncio

import pytest

import database
import traced_response
from traced_response import Influence, traced_response_builder, persist_influence_trace


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    return database


def test_init_db_creates_influence_trace(tmp_db):
    import sqlite3
    con = sqlite3.connect(tmp_db.DB)
    got = con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                      "AND name='influence_trace'").fetchone()
    con.close()
    assert got is not None


def test_log_and_get_influence_trace_roundtrip(tmp_db):
    async def go():
        await tmp_db.log_influence_trace("rid-1", 42, [
            ("pattern_hypothesis", "pattern_42", "reply drew on pattern_hypothesis pattern_42"),
            ("questionnaire_score", "stai_2026_07", "reply drew on questionnaire stai_2026_07"),
        ])
        return await tmp_db.get_influence_trace("rid-1")
    rows = asyncio.run(go())
    assert rows == [
        ("pattern_hypothesis", "pattern_42", "reply drew on pattern_hypothesis pattern_42"),
        ("questionnaire_score", "stai_2026_07", "reply drew on questionnaire stai_2026_07"),
    ]


def test_log_influence_trace_raises_on_failure(tmp_db, monkeypatch):
    # Point DB at an unwritable path → the writer must RAISE (fail-closed depends on it).
    monkeypatch.setattr(database, "DB", "/nonexistent-dir/nope.db")
    with pytest.raises(Exception):
        asyncio.run(tmp_db.log_influence_trace("rid-x", 1, [("mode", "m1", "m1 mode")]))


# ── writer-level validation: garbage is rejected even bypassing the builder ────
def _rows_count(tmp_db):
    import sqlite3
    con = sqlite3.connect(tmp_db.DB)
    n = con.execute("SELECT count(*) FROM influence_trace").fetchone()[0]
    con.close()
    return n


@pytest.mark.parametrize("bad_row", [
    ("mode", "", "empty source id names nothing"),                 # empty source_id
    ("mode", "m1", ""),                                             # empty human_readable
    ("mode", "none", "reply drew on mode none"),                    # placeholder source_id
    ("mode", "m1", "influence: none"),                              # placeholder human_readable
    ("mode", "m1", "unknown"),                                      # placeholder human_readable
    ("mode", "m1", "todo"),                                         # placeholder human_readable
    ("mode", "m1", "reply used a mode"),                            # doesn't name source_id
])
def test_log_influence_trace_rejects_garbage_row_writes_nothing(tmp_db, bad_row):
    with pytest.raises(ValueError):
        asyncio.run(tmp_db.log_influence_trace("rid-garbage", 1, [bad_row]))
    assert _rows_count(tmp_db) == 0    # nothing persisted — not even a partial write


def test_log_influence_trace_rejects_null_user_id(tmp_db):
    # user_id must be attributable — a None user_id is rejected before any DB write.
    with pytest.raises(ValueError):
        asyncio.run(tmp_db.log_influence_trace(
            "rid-x", None, [("mode", "m1", "reply drew on mode m1")]))
    assert _rows_count(tmp_db) == 0


def test_log_influence_trace_all_or_nothing_across_rows(tmp_db):
    # One good row + one garbage row in the same call → NEITHER is persisted.
    rows = [
        ("mode", "m1", "reply drew on mode m1"),                     # valid
        ("mode", "", "reply drew on mode m2"),                       # invalid (empty source_id)
    ]
    with pytest.raises(ValueError):
        asyncio.run(tmp_db.log_influence_trace("rid-mixed", 1, rows))
    assert _rows_count(tmp_db) == 0


def test_builder_with_real_binding_persists_rows(tmp_db):
    sent = []

    async def build():
        return "LATENT"

    async def send(t):
        sent.append(t)

    async def fb():
        sent.append("FALLBACK")

    async def go():
        rid = await traced_response_builder(
            user_id=7,
            influences=[Influence("schema_theme", "theme_9", "reply drew on schema_theme theme_9")],
            build_response=build, send=send,
            persist_trace=persist_influence_trace, neutral_fallback=fb,
            response_id="rid-real")
        rows = await tmp_db.get_influence_trace("rid-real")
        return rid, rows

    rid, rows = asyncio.run(go())
    assert rid == "rid-real"
    assert sent == ["LATENT"]                       # latent reply delivered
    assert rows == [("schema_theme", "theme_9", "reply drew on schema_theme theme_9")]
