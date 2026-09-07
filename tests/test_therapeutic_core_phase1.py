"""Therapeutic Core Foundation — Phase 1 (master prompt §15, Phase 1 of the
autonomous roadmap in §25). Covers the NEW domain foundation added this phase:
therapeutic_domain.py's validated models, the additive core_* storage in
database.py, their privacy_registry.py registration, and
access_control.core_rollout_allowed's off/owner/invited/all contract.

Deliberately separate from tests/test_therapeutic_core_foundation.py, which
covers an earlier, narrower, already-shipped slice behind the SAME
THERAPEUTIC_CORE_FOUNDATION_ENABLED flag name (baseline-skip button,
dependency-monitor consolidation, practice reachability) — see config.py's
comment reconciling the two. Nothing here reads that flag; this phase is
gated by config.THERAPEUTIC_CORE_ROLLOUT_MODE instead, currently "off" with
no user-facing effect (Phase 1 ships storage only, nothing in bot.pipeline()
calls into it yet).
"""
import asyncio
import json
import sqlite3

import pytest

import access_control as ac
import config
import database
import privacy_registry as pr
import therapeutic_domain as core

run = asyncio.run


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    run(database.init_db())
    return database


@pytest.fixture(autouse=True)
def _access_env(monkeypatch):
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})


# ── Pure model validation (no DB) ───────────────────────────────────────────

def test_unknown_enum_value_raises():
    with pytest.raises(ValueError):
        core.as_enum(core.Intent, "NOT_A_REAL_INTENT")


def test_session_phase_and_lifecycle_are_independent_axes():
    s = core.SessionState(session_id="1", user_id=1,
                           phase=core.SessionPhase.INTERVENE,
                           lifecycle_status=core.LifecycleStatus.PAUSED)
    assert s.phase is core.SessionPhase.INTERVENE
    assert s.lifecycle_status is core.LifecycleStatus.PAUSED
    assert s.is_active is True


def test_session_state_round_trips_through_dict():
    s = core.SessionState(session_id="7", user_id=42, intent=core.Intent.VENT,
                           repair_records=[core.RepairRecord(
                               constraint=core.RepairConstraint.QUESTION_OVERLOAD,
                               source_turn_id=None, created_at="", remaining_turns=3)])
    assert core.SessionState.from_dict(s.to_dict()).to_dict() == s.to_dict()
    assert s.active_repair_constraints == {core.RepairConstraint.QUESTION_OVERLOAD}


def test_formulation_confidence_out_of_range_rejected():
    with pytest.raises(ValueError):
        core.Formulation(confidence=1.5)


def test_memory_item_empty_content_rejected():
    with pytest.raises(ValueError):
        core.MemoryItem(category=core.MemoryCategory.GOAL,
                         lifecycle=core.MemoryLifecycle.CANDIDATE, content="   ")


@pytest.mark.parametrize("lifecycle,expected", [
    (core.MemoryLifecycle.CANDIDATE, False),
    (core.MemoryLifecycle.PROPOSED, False),
    (core.MemoryLifecycle.CONFIRMED, True),
    (core.MemoryLifecycle.CORRECTED, True),
    (core.MemoryLifecycle.REJECTED, False),
    (core.MemoryLifecycle.EXPIRED, False),
])
def test_memory_lifecycle_influence_gate(lifecycle, expected):
    item = core.MemoryItem(category=core.MemoryCategory.GOAL, lifecycle=lifecycle,
                            content="learn to pause before agreeing")
    assert item.influences_responses is expected


@pytest.mark.parametrize("direction,before,after,expected", [
    (core.MetricDirection.LOWER_IS_BETTER, 8, 4, core.OutcomeClass.IMPROVED),
    (core.MetricDirection.LOWER_IS_BETTER, 4, 8, core.OutcomeClass.WORSENED),
    (core.MetricDirection.HIGHER_IS_BETTER, 4, 8, core.OutcomeClass.IMPROVED),
    (core.MetricDirection.HIGHER_IS_BETTER, 8, 4, core.OutcomeClass.WORSENED),
    (core.MetricDirection.HIGHER_IS_BETTER, 5, 5, core.OutcomeClass.UNCHANGED),
])
def test_outcome_classify_never_conflates_direction(direction, before, after, expected):
    o = core.OutcomeMeasurement(metric_kind=core.MetricKind.DISTRESS, direction=direction,
                                 scale_min=1, scale_max=10, prompt_version="v1",
                                 before=before, after=after, completed=True)
    assert o.classify() is expected


def test_outcome_not_completed_is_incomplete_not_worsened():
    o = core.OutcomeMeasurement(metric_kind=core.MetricKind.ACTION_COMPLETION,
                                 direction=core.MetricDirection.HIGHER_IS_BETTER,
                                 scale_min=0, scale_max=1, prompt_version="v1",
                                 before=1, completed=False)
    assert o.classify() is core.OutcomeClass.INCOMPLETE


# ── DB storage: ownership + cross-user isolation + restart-safety ──────────

async def _seed_user(uid: int):
    await database.upsert_user(uid, f"u{uid}", f"U{uid}")


def test_session_create_get_roundtrip(tmp_db):
    async def go():
        await _seed_user(10)
        s = await database.create_core_session(10, intent=core.Intent.EXPLAIN)
        assert s.session_id.isdigit()
        fetched = await database.get_core_session(s.session_id, 10)
        assert fetched.to_dict() == s.to_dict()
    run(go())


def test_session_id_is_the_db_row_id_not_duplicated_in_json(tmp_db):
    """One source of truth (Phase 1 correction #4): the row id is canonical;
    state_json must never carry its own copy of session_id, so there is no
    second field that could ever diverge from the row it lives in."""
    async def go():
        await _seed_user(11)
        s = await database.create_core_session(11)
        async with database.aiosqlite.connect(database.DB) as db:
            cur = await db.execute("SELECT id, state_json FROM core_sessions WHERE id=?",
                                   (s.session_id,))
            row_id, raw_json = await cur.fetchone()
        import json as _json
        stored = _json.loads(raw_json)
        assert "session_id" not in stored, (
            "state_json must not embed session_id -- it is hydrated from the "
            "row id on every read, see database._load_session")
        assert str(row_id) == s.session_id
    run(go())


def test_session_ownership_blocks_cross_user_read(tmp_db):
    async def go():
        await _seed_user(10); await _seed_user(20)
        s = await database.create_core_session(10)
        assert await database.get_core_session(s.session_id, 20) is None
        assert await database.get_core_session(s.session_id, 10) is not None
    run(go())


def test_session_ownership_blocks_cross_user_update(tmp_db):
    async def go():
        await _seed_user(10); await _seed_user(20)
        s = await database.create_core_session(10)
        s.phase = core.SessionPhase.FORMULATE
        assert await database.update_core_session(
            core.SessionState(session_id=s.session_id, user_id=20,
                               phase=core.SessionPhase.CLOSE)) is False
        untouched = await database.get_core_session(s.session_id, 10)
        assert untouched.phase is core.SessionPhase.OPENING
    run(go())


def test_one_open_session_per_user_enforced_by_db(tmp_db):
    """Concurrent/duplicate create_core_session() calls for the same user fail
    deterministically (IntegrityError) instead of silently forking two active
    sessions -- idx_core_one_open_session_per_user."""
    async def go():
        await _seed_user(12)
        await database.create_core_session(12)
        with pytest.raises(sqlite3.IntegrityError):
            await database.create_core_session(12)
    run(go())


def test_second_session_allowed_once_first_is_closed(tmp_db):
    """The uniqueness constraint is scoped to OPEN/PAUSED, not to the user
    forever -- a CLOSED session frees the slot for a new one."""
    async def go():
        await _seed_user(13)
        first = await database.create_core_session(13)
        first.lifecycle_status = core.LifecycleStatus.COMPLETED
        assert await database.update_core_session(first) is True
        second = await database.create_core_session(13)
        assert second.session_id != first.session_id
    run(go())


def test_init_db_is_idempotent_across_repeated_calls(tmp_db):
    async def go():
        await _seed_user(14)
        s = await database.create_core_session(14)
        await database.init_db()  # simulates a second boot against the same file
        await database.init_db()
        still_there = await database.get_core_session(s.session_id, 14)
        assert still_there is not None
    run(go())


def test_corrupted_persisted_phase_fails_closed_on_read(tmp_db):
    """Invalid persisted state must not be silently coerced into a valid
    enum -- reading a hand-corrupted row raises ValueError (SS15.2's
    "unknown enum values FAIL validation"), it never falls back to a
    default phase/status that could misrepresent where the session is."""
    async def go():
        await _seed_user(15)
        s = await database.create_core_session(15)
        async with database.aiosqlite.connect(database.DB) as db:
            await db.execute(
                "UPDATE core_sessions SET state_json=? WHERE id=?",
                ('{"user_id":15,"intent":"UNKNOWN","phase":"NOT_A_REAL_PHASE",'
                 '"lifecycle_status":"OPEN","consent":"ABSENT","active_goal":null,'
                 '"active_intervention_id":null,"pending_outcome":false,'
                 '"repair_constraints":[]}', s.session_id))
            await db.commit()
        with pytest.raises(ValueError):
            await database.get_core_session(s.session_id, 15)
    run(go())


def test_session_restart_safety_full_chain_survives_fresh_read(tmp_db):
    """No process-local dict involved: every accessor opens its own connection,
    so writing then reading via brand-new calls IS the restart-safety proof."""
    async def go():
        await _seed_user(30)
        s = await database.create_core_session(30, intent=core.Intent.CHANGE_PATTERN)
        f = core.Formulation(trigger="he didn't reply for 3 hours",
                              thought="I'm not important to him", emotion="anxiety",
                              confidence=0.4)
        fid = await database.add_core_formulation(s.session_id, 30, f)
        iv = core.Intervention(method_id="cbt_thought_record", version="v1",
                                capability_level=core.CapabilityLevel.AUTONOMOUS,
                                purpose="separate fact from interpretation",
                                status=core.InterventionStatus.ACCEPTED,
                                consent=core.ConsentState.GRANTED)
        ivid = await database.add_core_intervention(s.session_id, 30, iv)
        outcome = core.OutcomeMeasurement(metric_kind=core.MetricKind.DISTRESS,
                                          direction=core.MetricDirection.LOWER_IS_BETTER,
                                          scale_min=1, scale_max=10, prompt_version="v1",
                                          before=7, after=5, completed=True)
        await database.add_core_outcome(ivid, 30, outcome)
        mem = core.MemoryItem(category=core.MemoryCategory.HYPOTHESIS,
                              lifecycle=core.MemoryLifecycle.CONFIRMED,
                              content="uncertainty reads as rejection")
        await database._add_core_memory_item_unchecked(30, mem)

        # Fresh reads, no shared state with the writers above.
        assert (await database.list_core_formulations(s.session_id, 30))[0].trigger == f.trigger
        active = await database.get_active_core_intervention(s.session_id, 30)
        assert active.method_id == "cbt_thought_record"
        assert (await database.list_core_outcomes(ivid, 30))[0].classify() is core.OutcomeClass.IMPROVED
        influencing = await database.list_core_memory_items(30, influencing_only=True)
        assert any(m.content == mem.content for m in influencing)
        assert fid > 0
    run(go())


def test_one_active_intervention_per_session_enforced_by_db(tmp_db):
    async def go():
        await _seed_user(40)
        s = await database.create_core_session(40)
        first = core.Intervention(method_id="grounding_5senses", version="v1",
                                  capability_level=core.CapabilityLevel.AUTONOMOUS,
                                  purpose="stabilize", status=core.InterventionStatus.STARTED)
        await database.add_core_intervention(s.session_id, 40, first)
        second = core.Intervention(method_id="act_defusion", version="v1",
                                   capability_level=core.CapabilityLevel.AUTONOMOUS,
                                   purpose="defuse", status=core.InterventionStatus.ACCEPTED)
        with pytest.raises(sqlite3.IntegrityError):
            await database.add_core_intervention(s.session_id, 40, second)
    run(go())


def test_rejected_memory_excluded_from_influencing_query(tmp_db):
    async def go():
        await _seed_user(50)
        keep = core.MemoryItem(category=core.MemoryCategory.PREFERENCE,
                               lifecycle=core.MemoryLifecycle.CONFIRMED, content="prefers directness")
        drop = core.MemoryItem(category=core.MemoryCategory.PREFERENCE,
                               lifecycle=core.MemoryLifecycle.REJECTED, content="wrong guess")
        await database._add_core_memory_item_unchecked(50, keep)
        await database._add_core_memory_item_unchecked(50, drop)
        influencing = await database.list_core_memory_items(50, influencing_only=True)
        contents = {m.content for m in influencing}
        assert keep.content in contents
        assert drop.content not in contents
    run(go())


# Phase 2A -- list_core_memory_item_records preserves real DB id provenance
# (list_core_memory_items itself discards it) and applies the same
# influencing_only filter.
def test_list_core_memory_item_records_preserves_db_id_and_filters(tmp_db):
    async def go():
        await _seed_user(70)
        keep = core.MemoryItem(category=core.MemoryCategory.EXPLICIT_FACT,
                               lifecycle=core.MemoryLifecycle.CONFIRMED, content="confirmed fact")
        drop = core.MemoryItem(category=core.MemoryCategory.PREFERENCE,
                               lifecycle=core.MemoryLifecycle.CANDIDATE, content="unconfirmed guess")
        keep_id = await database._add_core_memory_item_unchecked(70, keep)
        drop_id = await database._add_core_memory_item_unchecked(70, drop)
        assert keep_id > 0 and drop_id > 0 and drop_id != keep_id

        all_records = await database.list_core_memory_item_records(70)
        assert [rid for rid, _item in all_records] == [keep_id, drop_id]

        influencing = await database.list_core_memory_item_records(70, influencing_only=True)
        assert len(influencing) == 1
        record_id, record_item = influencing[0]
        assert record_id == keep_id
        assert record_item.content == keep.content
        assert record_item.lifecycle is core.MemoryLifecycle.CONFIRMED
    run(go())


# P2-1 correction: a real, directly-persisted (not MemoryItem-constructed)
# core_memory_items row whose raw content would be silently reshaped by
# MemoryItem.__post_init__'s own _clip(...,1000) bound must make
# list_core_memory_item_records raise, never silently return the reshaped
# (1000-char) value.
def test_list_core_memory_item_records_raises_on_persisted_content_over_1000_chars(tmp_db):
    async def go():
        import json as _json
        await _seed_user(80)
        raw_content = "x" * 1001
        payload = _json.dumps({
            "category": core.MemoryCategory.EXPLICIT_FACT.value,
            "lifecycle": core.MemoryLifecycle.CONFIRMED.value,
            "content": raw_content,
            "confidence": 0.0,
            "source_event_ids": [],
        })
        async with database.aiosqlite.connect(database.DB) as db:
            await db.execute(
                "INSERT INTO core_memory_items (user_id, category, lifecycle, memory_json) "
                "VALUES (?,?,?,?)",
                (80, core.MemoryCategory.EXPLICIT_FACT.value,
                 core.MemoryLifecycle.CONFIRMED.value, payload))
            await db.commit()

        with pytest.raises(ValueError):
            await database.list_core_memory_item_records(80)
    run(go())


# Same raw-integrity guard, different transformation: MemoryItem._clip also
# strips surrounding whitespace -- a raw persisted value that differs from
# its own stripped form must raise, never silently return the stripped one.
def test_list_core_memory_item_records_raises_on_persisted_whitespace_stripped(tmp_db):
    async def go():
        import json as _json
        await _seed_user(81)
        raw_content = "  confirmed content with padding  "
        payload = _json.dumps({
            "category": core.MemoryCategory.EXPLICIT_FACT.value,
            "lifecycle": core.MemoryLifecycle.CONFIRMED.value,
            "content": raw_content,
            "confidence": 0.0,
            "source_event_ids": [],
        })
        async with database.aiosqlite.connect(database.DB) as db:
            await db.execute(
                "INSERT INTO core_memory_items (user_id, category, lifecycle, memory_json) "
                "VALUES (?,?,?,?)",
                (81, core.MemoryCategory.EXPLICIT_FACT.value,
                 core.MemoryLifecycle.CONFIRMED.value, payload))
            await db.commit()

        with pytest.raises(ValueError):
            await database.list_core_memory_item_records(81)
    run(go())


# ══════════════════════════════════════════════════════════════════════════
# Phase 2A V3 correction -- P1: memory lifecycle/category coherence across
# the SQL columns, the raw memory_json, and the deserialized MemoryItem.
# _update_core_memory_item_lifecycle_unchecked must transition BOTH representations
# atomically in one UPDATE; both memory readers must verify all three
# representations agree before influencing_only filtering ever runs.
# ══════════════════════════════════════════════════════════════════════════

# Item 1: CANDIDATE -> CONFIRMED via the supported API -- all three
# representations end up CONFIRMED, both influencing readers include it.
def test_update_lifecycle_candidate_to_confirmed_stays_coherent(tmp_db):
    async def go():
        await _seed_user(91)
        item = core.MemoryItem(category=core.MemoryCategory.EXPLICIT_FACT,
                               lifecycle=core.MemoryLifecycle.CANDIDATE, content="works nights")
        item_id = await database._add_core_memory_item_unchecked(91, item)

        ok = await database._update_core_memory_item_lifecycle_unchecked(
            item_id, 91, core.MemoryLifecycle.CONFIRMED)
        assert ok is True

        async with database.aiosqlite.connect(database.DB) as db:
            cur = await db.execute(
                "SELECT lifecycle, memory_json FROM core_memory_items WHERE id=?", (item_id,))
            sql_lifecycle, memory_json = await cur.fetchone()
        json_lifecycle = json.loads(memory_json)["lifecycle"]
        assert sql_lifecycle == "CONFIRMED"
        assert json_lifecycle == "CONFIRMED"

        influencing_items = await database.list_core_memory_items(91, influencing_only=True)
        assert any(i.content == "works nights" and i.lifecycle is core.MemoryLifecycle.CONFIRMED
                   for i in influencing_items)
        influencing_records = await database.list_core_memory_item_records(
            91, influencing_only=True)
        assert any(rid == item_id and i.lifecycle is core.MemoryLifecycle.CONFIRMED
                   for rid, i in influencing_records)
    run(go())


# Item 2: CONFIRMED -> REJECTED via the supported API -- all three
# representations end up REJECTED, both influencing readers exclude it.
def test_update_lifecycle_confirmed_to_rejected_stays_coherent(tmp_db):
    async def go():
        await _seed_user(92)
        item = core.MemoryItem(category=core.MemoryCategory.EXPLICIT_FACT,
                               lifecycle=core.MemoryLifecycle.CONFIRMED, content="wrong guess")
        item_id = await database._add_core_memory_item_unchecked(92, item)

        ok = await database._update_core_memory_item_lifecycle_unchecked(
            item_id, 92, core.MemoryLifecycle.REJECTED)
        assert ok is True

        async with database.aiosqlite.connect(database.DB) as db:
            cur = await db.execute(
                "SELECT lifecycle, memory_json FROM core_memory_items WHERE id=?", (item_id,))
            sql_lifecycle, memory_json = await cur.fetchone()
        json_lifecycle = json.loads(memory_json)["lifecycle"]
        assert sql_lifecycle == "REJECTED"
        assert json_lifecycle == "REJECTED"

        influencing_items = await database.list_core_memory_items(92, influencing_only=True)
        assert not any(i.content == "wrong guess" for i in influencing_items)
        influencing_records = await database.list_core_memory_item_records(
            92, influencing_only=True)
        assert not any(rid == item_id for rid, _i in influencing_records)
    run(go())


async def _insert_raw_core_memory_row(user_id, sql_category, sql_lifecycle, json_category,
                                      json_lifecycle, content="divergent content"):
    """Async helper -- callers already run inside their own `go()` coroutine
    and must `await` this directly, never wrap it in a second run(...)."""
    payload = json.dumps({
        "category": json_category, "lifecycle": json_lifecycle, "content": content,
        "confidence": 0.0, "source_event_ids": [],
    })
    async with database.aiosqlite.connect(database.DB) as db:
        cur = await db.execute(
            "INSERT INTO core_memory_items (user_id, category, lifecycle, memory_json) "
            "VALUES (?,?,?,?)",
            (user_id, sql_category, sql_lifecycle, payload))
        await db.commit()
        return cur.lastrowid


# Item 3: a pre-existing divergent lifecycle (SQL disagrees with JSON) must
# make BOTH readers raise -- never silently return empty, never silently
# pick one representation over the other. Both directions covered.
def test_readers_raise_on_preexisting_divergent_lifecycle_sql_rejected_json_confirmed(tmp_db):
    async def go():
        await _seed_user(93)
        await _insert_raw_core_memory_row(93, "EXPLICIT_FACT", "REJECTED", "EXPLICIT_FACT", "CONFIRMED")
        with pytest.raises(ValueError):
            await database.list_core_memory_items(93)
        with pytest.raises(ValueError):
            await database.list_core_memory_item_records(93)
    run(go())


def test_readers_raise_on_preexisting_divergent_lifecycle_sql_confirmed_json_candidate(tmp_db):
    async def go():
        await _seed_user(94)
        await _insert_raw_core_memory_row(94, "EXPLICIT_FACT", "CONFIRMED", "EXPLICIT_FACT", "CANDIDATE")
        with pytest.raises(ValueError):
            await database.list_core_memory_items(94)
        with pytest.raises(ValueError):
            await database.list_core_memory_item_records(94)
    run(go())


# Item 5: calling _update_core_memory_item_lifecycle_unchecked against an ALREADY
# divergent row must itself fail closed -- raise, with ZERO mutation of
# either representation (proves pre-transition validation, not just
# post-transition atomicity).
def test_update_lifecycle_against_already_divergent_row_raises_with_zero_mutation(tmp_db):
    async def go():
        await _seed_user(95)
        div_id = await _insert_raw_core_memory_row(
            95, "EXPLICIT_FACT", "REJECTED", "EXPLICIT_FACT", "CONFIRMED")

        with pytest.raises(ValueError):
            await database._update_core_memory_item_lifecycle_unchecked(
                div_id, 95, core.MemoryLifecycle.CONFIRMED)

        async with database.aiosqlite.connect(database.DB) as db:
            cur = await db.execute(
                "SELECT lifecycle, memory_json FROM core_memory_items WHERE id=?", (div_id,))
            sql_lifecycle, memory_json = await cur.fetchone()
        # Neither representation was mutated by the failed attempt.
        assert sql_lifecycle == "REJECTED"
        assert json.loads(memory_json)["lifecycle"] == "CONFIRMED"
    run(go())


# Item 6: a category mismatch (SQL disagrees with JSON) protects the frozen
# HYPOTHESIS semantic boundary the same way a lifecycle mismatch does --
# both readers must fail closed, both directions.
def test_readers_raise_on_category_mismatch_sql_hypothesis_json_explicit_fact(tmp_db):
    async def go():
        await _seed_user(96)
        await _insert_raw_core_memory_row(96, "HYPOTHESIS", "CONFIRMED", "EXPLICIT_FACT", "CONFIRMED")
        with pytest.raises(ValueError):
            await database.list_core_memory_items(96)
        with pytest.raises(ValueError):
            await database.list_core_memory_item_records(96)
    run(go())


def test_readers_raise_on_category_mismatch_sql_explicit_fact_json_hypothesis(tmp_db):
    async def go():
        await _seed_user(97)
        await _insert_raw_core_memory_row(97, "EXPLICIT_FACT", "CONFIRMED", "HYPOTHESIS", "CONFIRMED")
        with pytest.raises(ValueError):
            await database.list_core_memory_items(97)
        with pytest.raises(ValueError):
            await database.list_core_memory_item_records(97)
    run(go())


# ── Privacy: registration, export, delete-preview, delete-all/forget_all ───

def test_all_core_tables_registered_default_deny():
    assert pr.find_unregistered_sensitive_tables(database.SCHEMA) == []


def test_export_delete_preview_and_delete_all_cover_core_tables(tmp_db):
    async def go():
        await _seed_user(60); await _seed_user(61)
        s = await database.create_core_session(60)
        mem = core.MemoryItem(category=core.MemoryCategory.GOAL,
                              lifecycle=core.MemoryLifecycle.CONFIRMED, content="reduce rituals")
        await database._add_core_memory_item_unchecked(60, mem)
        other_mem = core.MemoryItem(category=core.MemoryCategory.GOAL,
                                    lifecycle=core.MemoryLifecycle.CONFIRMED, content="not user 60's")
        await database._add_core_memory_item_unchecked(61, other_mem)

        exported = await database.export_all_personal_data(60)
        assert len(exported["core_sessions"]) == 1
        assert len(exported["core_memory_items"]) == 1

        preview = await database.preview_delete_all_personal_data(60)
        assert preview["core_sessions"]["row_count"] == 1
        assert preview["core_memory_items"]["row_count"] == 1

        summary = await database.delete_all_personal_data(60)
        assert summary["core_sessions"] == 1
        assert summary["core_memory_items"] == 1
        assert await database.get_core_session(s.session_id, 60) is None
        # forget_all for user 60 must never touch user 61's rows.
        remaining = await database.list_core_memory_items(61)
        assert any(m.content == "not user 60's" for m in remaining)
    run(go())


def test_export_delete_preview_cover_formulations_interventions_outcomes(tmp_db):
    """The 2-table check above already proves the registry-driven loop works
    generically; this test proves it specifically for the three tables not
    covered there, and proves cross-user isolation holds for every one of
    them individually (not just in aggregate)."""
    async def go():
        await _seed_user(80); await _seed_user(81)
        s = await database.create_core_session(80)
        other_s = await database.create_core_session(81)

        f = core.Formulation(trigger="t", confirmation=core.ConfirmationStatus.CONFIRMED)
        await database.add_core_formulation(s.session_id, 80, f)
        await database.add_core_formulation(other_s.session_id, 81, f)

        iv = core.Intervention(method_id="m", version="v1",
                               capability_level=core.CapabilityLevel.AUTONOMOUS,
                               purpose="p", status=core.InterventionStatus.PROPOSED)
        ivid = await database.add_core_intervention(s.session_id, 80, iv)
        await database.add_core_intervention(other_s.session_id, 81, iv)

        outcome = core.OutcomeMeasurement(metric_kind=core.MetricKind.MOOD,
                                          direction=core.MetricDirection.HIGHER_IS_BETTER,
                                          scale_min=1, scale_max=10, prompt_version="v1")
        await database.add_core_outcome(ivid, 80, outcome)

        for table in ("core_formulations", "core_interventions", "core_outcomes"):
            exported = await database.export_all_personal_data(80)
            assert len(exported[table]) == 1, f"{table}: expected exactly user 80's own row"
            preview = await database.preview_delete_all_personal_data(80)
            assert preview[table]["row_count"] == 1

        summary = await database.delete_all_personal_data(80)
        assert summary["core_formulations"] == 1
        assert summary["core_interventions"] == 1
        assert summary["core_outcomes"] == 1

        # forget_all for user 80 must never touch user 81's rows.
        remaining = await database.export_all_personal_data(81)
        assert len(remaining["core_formulations"]) == 1
        assert len(remaining["core_interventions"]) == 1
    run(go())


# ── access_control.core_rollout_allowed: off/owner/invited/all contract ────

def test_rollout_off_denies_everyone_including_owner(monkeypatch):
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "off")
    assert run(ac.core_rollout_allowed(1)) is False  # uid 1 == fixture OWNER_USER_ID


def test_rollout_owner_mode_allows_only_owner(monkeypatch):
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "owner")
    assert run(ac.core_rollout_allowed(1)) is True
    assert run(ac.core_rollout_allowed(999)) is False


def test_rollout_all_mode_allows_anyone(monkeypatch):
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "all")
    assert run(ac.core_rollout_allowed(999)) is True


def test_rollout_invited_mode_owner_always_allowed(monkeypatch):
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "invited")
    assert run(ac.core_rollout_allowed(1)) is True


def test_rollout_invited_mode_checks_user_access_table(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "invited")
    async def go():
        await _seed_user(70)
        assert await ac.core_rollout_allowed(70) is False
        await database.grant_user_access(70)
        assert await ac.core_rollout_allowed(70) is True
    run(go())


def test_rollout_unexpected_mode_fails_closed(monkeypatch):
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "totally_bogus")
    assert run(ac.core_rollout_allowed(1)) is False


def test_rollout_owner_mode_denies_everyone_when_owner_identity_missing(monkeypatch):
    """Missing/invalid owner identity (OWNER_USER_ID unset) must fail closed,
    not silently grant access to whoever asks."""
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "owner")
    monkeypatch.setattr(ac, "OWNER_USER_ID", None)
    assert run(ac.core_rollout_allowed(1)) is False
    assert run(ac.core_rollout_allowed(999)) is False


def test_rollout_unauthorized_user_denied_in_every_restrictive_mode(monkeypatch):
    uid = 12345
    for mode in ("off", "owner", "invited"):
        monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", mode)
        assert run(ac.core_rollout_allowed(uid)) is False, f"mode={mode}"


@pytest.mark.parametrize("legacy_flag", [True, False])
def test_rollout_contract_is_independent_of_legacy_foundation_flag(monkeypatch, legacy_flag):
    """The two flags must never interact -- config.py's own reconciliation
    comment promises this; prove it in both legacy-flag states."""
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_FOUNDATION_ENABLED", legacy_flag)
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "owner")
    assert run(ac.core_rollout_allowed(1)) is True
    assert run(ac.core_rollout_allowed(999)) is False


# ── Flag-off / config-contract compatibility ────────────────────────────────

def test_default_rollout_mode_is_off():
    assert config.THERAPEUTIC_CORE_ROLLOUT_MODE == "off"


def test_legacy_foundation_flag_untouched_by_new_contract():
    assert config.THERAPEUTIC_CORE_FOUNDATION_ENABLED is False


def test_rollout_off_denies_invited_user_with_active_access_too(tmp_db, monkeypatch):
    """Feature-off compatibility must be absolute: even a user who WOULD
    qualify under "invited" gets nothing while the global switch is off."""
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "off")
    async def go():
        await _seed_user(90)
        await database.grant_user_access(90)
        assert await ac.core_rollout_allowed(90) is False
    run(go())


# ── Phase 2B-1 -- Governed Canonical Memory Persistence Boundary ───────────
# canonical_memory_governance.py owns the pure policy (see
# tests/test_canonical_memory_governance.py); these tests exercise the
# actual DB-backed governed functions end-to-end.

async def _user_authored_message_id(uid: int, content: str = "i feel anxious today") -> int:
    return await database.save_message(
        uid, "user", content, source=database.MessageSource.USER_AUTHORED)


async def _assistant_message_id(uid: int, content: str = "that sounds hard") -> int:
    return await database.save_message(
        uid, "assistant", content, source=database.MessageSource.ASSISTANT_DELIVERED)


def test_governed_candidate_valid_persists_as_candidate(tmp_db):
    async def go():
        await _seed_user(200)
        mid = await _user_authored_message_id(200)
        item_id = await database.add_governed_core_memory_candidate(
            200, category=core.MemoryCategory.EXPLICIT_FACT,
            content="works night shifts", source_event_ids=[mid])
        assert item_id > 0
        records = await database.list_core_memory_item_records(200)
        assert len(records) == 1
        record_id, item = records[0]
        assert record_id == item_id
        assert item.category is core.MemoryCategory.EXPLICIT_FACT
        assert item.lifecycle is core.MemoryLifecycle.CANDIDATE
        assert item.content == "works night shifts"
        assert item.source_event_ids == [mid]
    run(go())


def test_governed_candidate_caller_cannot_choose_initial_lifecycle(tmp_db):
    """The function signature itself has no lifecycle parameter -- calling
    with one is a TypeError, not a silently-accepted override."""
    async def go():
        await _seed_user(201)
        mid = await _user_authored_message_id(201)
        with pytest.raises(TypeError):
            await database.add_governed_core_memory_candidate(
                201, category=core.MemoryCategory.EXPLICIT_FACT,
                content="x", source_event_ids=[mid],
                lifecycle=core.MemoryLifecycle.CONFIRMED)
    run(go())


def test_governed_candidate_specialized_category_rejected(tmp_db):
    async def go():
        await _seed_user(202)
        mid = await _user_authored_message_id(202)
        with pytest.raises(ValueError):
            await database.add_governed_core_memory_candidate(
                202, category=core.MemoryCategory.CONFIRMED_PATTERN,
                content="x", source_event_ids=[mid])
        assert await database.list_core_memory_item_records(202) == []
    run(go())


def test_governed_candidate_empty_provenance_rejected(tmp_db):
    async def go():
        await _seed_user(203)
        with pytest.raises(ValueError):
            await database.add_governed_core_memory_candidate(
                203, category=core.MemoryCategory.GOAL,
                content="wants to exercise more", source_event_ids=[])
        assert await database.list_core_memory_item_records(203) == []
    run(go())


def test_governed_candidate_non_int_provenance_rejected(tmp_db):
    async def go():
        await _seed_user(204)
        mid = await _user_authored_message_id(204)
        with pytest.raises(ValueError):
            await database.add_governed_core_memory_candidate(
                204, category=core.MemoryCategory.GOAL,
                content="x", source_event_ids=[str(mid)])
        assert await database.list_core_memory_item_records(204) == []
    run(go())


def test_governed_candidate_bool_provenance_rejected(tmp_db):
    async def go():
        await _seed_user(205)
        with pytest.raises(ValueError):
            await database.add_governed_core_memory_candidate(
                205, category=core.MemoryCategory.GOAL,
                content="x", source_event_ids=[True])
        assert await database.list_core_memory_item_records(205) == []
    run(go())


def test_governed_candidate_zero_negative_provenance_rejected(tmp_db):
    async def go():
        await _seed_user(206)
        with pytest.raises(ValueError):
            await database.add_governed_core_memory_candidate(
                206, category=core.MemoryCategory.GOAL,
                content="x", source_event_ids=[0])
        with pytest.raises(ValueError):
            await database.add_governed_core_memory_candidate(
                206, category=core.MemoryCategory.GOAL,
                content="x", source_event_ids=[-5])
        assert await database.list_core_memory_item_records(206) == []
    run(go())


def test_governed_candidate_duplicate_provenance_rejected(tmp_db):
    async def go():
        await _seed_user(207)
        mid = await _user_authored_message_id(207)
        with pytest.raises(ValueError):
            await database.add_governed_core_memory_candidate(
                207, category=core.MemoryCategory.GOAL,
                content="x", source_event_ids=[mid, mid])
        assert await database.list_core_memory_item_records(207) == []
    run(go())


def test_governed_candidate_missing_message_id_rejected(tmp_db):
    async def go():
        await _seed_user(208)
        with pytest.raises(ValueError):
            await database.add_governed_core_memory_candidate(
                208, category=core.MemoryCategory.GOAL,
                content="x", source_event_ids=[999999])
        assert await database.list_core_memory_item_records(208) == []
    run(go())


def test_governed_candidate_another_users_message_rejected(tmp_db):
    async def go():
        await _seed_user(209)
        await _seed_user(210)
        other_users_mid = await _user_authored_message_id(210)
        with pytest.raises(ValueError):
            await database.add_governed_core_memory_candidate(
                209, category=core.MemoryCategory.GOAL,
                content="x", source_event_ids=[other_users_mid])
        assert await database.list_core_memory_item_records(209) == []
        assert await database.list_core_memory_item_records(210) == []
    run(go())


def test_governed_candidate_assistant_authored_message_rejected(tmp_db):
    async def go():
        await _seed_user(211)
        assistant_mid = await _assistant_message_id(211)
        with pytest.raises(ValueError):
            await database.add_governed_core_memory_candidate(
                211, category=core.MemoryCategory.GOAL,
                content="x", source_event_ids=[assistant_mid])
        assert await database.list_core_memory_item_records(211) == []
    run(go())


def test_governed_candidate_synthetic_ui_message_rejected(tmp_db):
    """role='user' alone is NOT proof of authorship -- a SYNTHETIC_UI row
    (normalized Telegram-button label) also persists with role='user' and
    must be refused just like an assistant row."""
    async def go():
        await _seed_user(212)
        synthetic_mid = await database.save_message(
            212, "user", "[button: yes]", source=database.MessageSource.SYNTHETIC_UI)
        with pytest.raises(ValueError):
            await database.add_governed_core_memory_candidate(
                212, category=core.MemoryCategory.GOAL,
                content="x", source_event_ids=[synthetic_mid])
        assert await database.list_core_memory_item_records(212) == []
    run(go())


def test_governed_candidate_role_assistant_source_user_authored_mismatch_rejected(tmp_db):
    """Phase 2B-1 V5 correction: a corrupted/inconsistent row -- role=
    'assistant' but source='USER_AUTHORED' -- must never pass as
    user-authored provenance merely because `source` alone says so. The
    boundary requires the COHERENT pair (role='user' AND
    source='USER_AUTHORED' together), never either field independently.
    This exact pairing would have wrongly PASSED under the pre-V5 check
    (which only inspected `source`), so it is the direct regression proof
    for this correction."""
    async def go():
        await _seed_user(220)
        mismatched_mid = await database.save_message(
            220, "assistant", "a role/source-inconsistent row",
            source=database.MessageSource.USER_AUTHORED)
        with pytest.raises(ValueError):
            await database.add_governed_core_memory_candidate(
                220, category=core.MemoryCategory.GOAL,
                content="x", source_event_ids=[mismatched_mid])
        assert await database.list_core_memory_item_records(220) == []
    run(go())


def test_governed_candidate_role_user_source_assistant_delivered_mismatch_rejected(tmp_db):
    """Companion mismatch in the other direction: role='user' but
    source='ASSISTANT_DELIVERED'. Also must never pass -- `role` alone is
    not proof of authorship either, exactly as MessageSource's own
    docstring already warns for the SYNTHETIC_UI case; this proves the
    same discipline for a different inconsistent source value."""
    async def go():
        await _seed_user(221)
        mismatched_mid = await database.save_message(
            221, "user", "a role/source-inconsistent row",
            source=database.MessageSource.ASSISTANT_DELIVERED)
        with pytest.raises(ValueError):
            await database.add_governed_core_memory_candidate(
                221, category=core.MemoryCategory.GOAL,
                content="x", source_event_ids=[mismatched_mid])
        assert await database.list_core_memory_item_records(221) == []
    run(go())


def test_governed_candidate_legacy_null_source_message_rejected(tmp_db):
    """A pre-MESSAGES-PROVENANCE-V1 row (source IS NULL) is UNKNOWN
    provenance, never backfilled -- it must be refused, not trusted."""
    async def go():
        await _seed_user(213)
        async with database.aiosqlite.connect(database.DB) as db:
            cur = await db.execute(
                "INSERT INTO messages (user_id, role, content) VALUES (?,?,?)",
                (213, "user", "a legacy row with no source column"))
            await db.commit()
            legacy_mid = cur.lastrowid
        with pytest.raises(ValueError):
            await database.add_governed_core_memory_candidate(
                213, category=core.MemoryCategory.GOAL,
                content="x", source_event_ids=[legacy_mid])
        assert await database.list_core_memory_item_records(213) == []
    run(go())


def test_governed_candidate_valid_same_user_user_row_accepted(tmp_db):
    async def go():
        await _seed_user(214)
        mid = await _user_authored_message_id(214)
        item_id = await database.add_governed_core_memory_candidate(
            214, category=core.MemoryCategory.HYPOTHESIS,
            content="may avoid conflict to keep the peace", source_event_ids=[mid])
        assert item_id > 0
    run(go())


def test_governed_candidate_content_needing_strip_rejected(tmp_db):
    async def go():
        await _seed_user(215)
        mid = await _user_authored_message_id(215)
        with pytest.raises(ValueError):
            await database.add_governed_core_memory_candidate(
                215, category=core.MemoryCategory.GOAL,
                content="  padded  ", source_event_ids=[mid])
        assert await database.list_core_memory_item_records(215) == []
    run(go())


def test_governed_candidate_1000_chars_rejected_not_truncated(tmp_db):
    async def go():
        await _seed_user(216)
        mid = await _user_authored_message_id(216)
        with pytest.raises(ValueError):
            await database.add_governed_core_memory_candidate(
                216, category=core.MemoryCategory.GOAL,
                content="x" * 1001, source_event_ids=[mid])
        assert await database.list_core_memory_item_records(216) == []
    run(go())


def test_governed_candidate_invalid_candidate_produces_zero_db_writes(tmp_db):
    async def go():
        await _seed_user(217)
        with pytest.raises(ValueError):
            await database.add_governed_core_memory_candidate(
                217, category=core.MemoryCategory.EXPLICIT_FACT,
                content="a perfectly valid fact", source_event_ids=[-1])
        assert await database.list_core_memory_item_records(217) == []
    run(go())


def test_governed_candidate_exact_retry_is_idempotent(tmp_db):
    async def go():
        await _seed_user(218)
        mid = await _user_authored_message_id(218)
        first_id = await database.add_governed_core_memory_candidate(
            218, category=core.MemoryCategory.PREFERENCE,
            content="prefers short, direct answers", source_event_ids=[mid])
        second_id = await database.add_governed_core_memory_candidate(
            218, category=core.MemoryCategory.PREFERENCE,
            content="prefers short, direct answers", source_event_ids=[mid])
        assert second_id == first_id
        records = await database.list_core_memory_item_records(218)
        assert len(records) == 1
    run(go())


def test_governed_candidate_different_content_same_provenance_is_not_a_duplicate(tmp_db):
    """Sanity check on the idempotency comparison -- it must not be so
    loose that any resubmission for the same message collapses into the
    first row regardless of content."""
    async def go():
        await _seed_user(219)
        mid = await _user_authored_message_id(219)
        first_id = await database.add_governed_core_memory_candidate(
            219, category=core.MemoryCategory.PREFERENCE,
            content="prefers short answers", source_event_ids=[mid])
        second_id = await database.add_governed_core_memory_candidate(
            219, category=core.MemoryCategory.PREFERENCE,
            content="prefers long answers", source_event_ids=[mid])
        assert second_id != first_id
        records = await database.list_core_memory_item_records(219)
        assert len(records) == 2
    run(go())


# ── Phase 2B-1 V2 correction -- idempotency must survive lifecycle change ──
# BLOCKER 3: the V1 idempotency scan only matched an existing row still at
# lifecycle=CANDIDATE. That was too weak -- an exact retry after the
# original row has advanced (or terminated) must still reuse the existing
# row, never insert a second one. One test per named lifecycle state below,
# plus HISTORICAL (reachable via CONFIRMED -> HISTORICAL, not explicitly
# named in the minimum list but worth covering since it IS reachable).
# CORRECTED (Phase 2B-1 V3 correction) is covered separately, further
# below (test_governed_candidate_retry_against_existing_corrected_row_is_
# idempotent) -- no CANDIDATE-rooted row can ever reach CORRECTED through
# the governed transition graph in this slice (canonical_memory_
# governance.ALLOWED_LIFECYCLE_TRANSITIONS has no edge targeting CORRECTED
# at all; it is only ever created by a later Phase 2B append-only
# correction transaction, out of scope here), so unlike every other
# lifecycle in this block, that one test builds its CORRECTED fixture with
# the private unchecked primitive rather than a governed transition
# sequence -- there is no governed path that reaches CORRECTED to exercise
# instead.

def test_governed_candidate_retry_while_candidate_is_idempotent(tmp_db):
    async def go():
        await _seed_user(240)
        mid = await _user_authored_message_id(240)
        first_id = await database.add_governed_core_memory_candidate(
            240, category=core.MemoryCategory.PREFERENCE,
            content="prefers short, direct answers", source_event_ids=[mid])
        second_id = await database.add_governed_core_memory_candidate(
            240, category=core.MemoryCategory.PREFERENCE,
            content="prefers short, direct answers", source_event_ids=[mid])
        assert second_id == first_id
        records = await database.list_core_memory_item_records(240)
        assert len(records) == 1
        assert records[0][1].lifecycle is core.MemoryLifecycle.CANDIDATE
    run(go())


def test_governed_candidate_retry_after_proposed_is_idempotent(tmp_db):
    async def go():
        await _seed_user(241)
        mid = await _user_authored_message_id(241)
        first_id = await database.add_governed_core_memory_candidate(
            241, category=core.MemoryCategory.EXPLICIT_FACT,
            content="works night shifts", source_event_ids=[mid])
        await database.apply_governed_core_memory_lifecycle_transition(
            241, first_id, expected_current_lifecycle=core.MemoryLifecycle.CANDIDATE,
            target_lifecycle=core.MemoryLifecycle.PROPOSED)

        retry_id = await database.add_governed_core_memory_candidate(
            241, category=core.MemoryCategory.EXPLICIT_FACT,
            content="works night shifts", source_event_ids=[mid])
        assert retry_id == first_id
        records = await database.list_core_memory_item_records(241)
        assert len(records) == 1
        assert records[0][1].lifecycle is core.MemoryLifecycle.PROPOSED
    run(go())


def test_governed_candidate_retry_after_confirmed_is_idempotent(tmp_db):
    async def go():
        await _seed_user(242)
        mid = await _user_authored_message_id(242)
        first_id = await database.add_governed_core_memory_candidate(
            242, category=core.MemoryCategory.EXPLICIT_FACT,
            content="lives alone", source_event_ids=[mid])
        await database.apply_governed_core_memory_lifecycle_transition(
            242, first_id, expected_current_lifecycle=core.MemoryLifecycle.CANDIDATE,
            target_lifecycle=core.MemoryLifecycle.PROPOSED)
        await database.apply_governed_core_memory_lifecycle_transition(
            242, first_id, expected_current_lifecycle=core.MemoryLifecycle.PROPOSED,
            target_lifecycle=core.MemoryLifecycle.CONFIRMED)

        retry_id = await database.add_governed_core_memory_candidate(
            242, category=core.MemoryCategory.EXPLICIT_FACT,
            content="lives alone", source_event_ids=[mid])
        assert retry_id == first_id
        records = await database.list_core_memory_item_records(242)
        assert len(records) == 1
        assert records[0][1].lifecycle is core.MemoryLifecycle.CONFIRMED
    run(go())


def test_governed_candidate_retry_after_rejected_is_idempotent(tmp_db):
    async def go():
        await _seed_user(243)
        mid = await _user_authored_message_id(243)
        first_id = await database.add_governed_core_memory_candidate(
            243, category=core.MemoryCategory.EXPLICIT_FACT,
            content="has a younger sister", source_event_ids=[mid])
        await database.apply_governed_core_memory_lifecycle_transition(
            243, first_id, expected_current_lifecycle=core.MemoryLifecycle.CANDIDATE,
            target_lifecycle=core.MemoryLifecycle.PROPOSED)
        await database.apply_governed_core_memory_lifecycle_transition(
            243, first_id, expected_current_lifecycle=core.MemoryLifecycle.PROPOSED,
            target_lifecycle=core.MemoryLifecycle.REJECTED)

        retry_id = await database.add_governed_core_memory_candidate(
            243, category=core.MemoryCategory.EXPLICIT_FACT,
            content="has a younger sister", source_event_ids=[mid])
        assert retry_id == first_id
        records = await database.list_core_memory_item_records(243)
        assert len(records) == 1
        assert records[0][1].lifecycle is core.MemoryLifecycle.REJECTED
    run(go())


def test_governed_candidate_retry_after_expired_is_idempotent(tmp_db):
    async def go():
        await _seed_user(244)
        mid = await _user_authored_message_id(244)
        first_id = await database.add_governed_core_memory_candidate(
            244, category=core.MemoryCategory.EXPLICIT_FACT,
            content="recently changed jobs", source_event_ids=[mid])
        await database.apply_governed_core_memory_lifecycle_transition(
            244, first_id, expected_current_lifecycle=core.MemoryLifecycle.CANDIDATE,
            target_lifecycle=core.MemoryLifecycle.EXPIRED)

        retry_id = await database.add_governed_core_memory_candidate(
            244, category=core.MemoryCategory.EXPLICIT_FACT,
            content="recently changed jobs", source_event_ids=[mid])
        assert retry_id == first_id
        records = await database.list_core_memory_item_records(244)
        assert len(records) == 1
        assert records[0][1].lifecycle is core.MemoryLifecycle.EXPIRED
    run(go())


def test_governed_candidate_retry_after_historical_is_idempotent(tmp_db):
    """Not in the task's named minimum list, but HISTORICAL is reachable
    (CONFIRMED -> HISTORICAL) and explicitly named among the lifecycle
    states a retry must survive, so it is covered here too."""
    async def go():
        await _seed_user(245)
        mid = await _user_authored_message_id(245)
        first_id = await database.add_governed_core_memory_candidate(
            245, category=core.MemoryCategory.EXPLICIT_FACT,
            content="owns a small dog", source_event_ids=[mid])
        await database.apply_governed_core_memory_lifecycle_transition(
            245, first_id, expected_current_lifecycle=core.MemoryLifecycle.CANDIDATE,
            target_lifecycle=core.MemoryLifecycle.PROPOSED)
        await database.apply_governed_core_memory_lifecycle_transition(
            245, first_id, expected_current_lifecycle=core.MemoryLifecycle.PROPOSED,
            target_lifecycle=core.MemoryLifecycle.CONFIRMED)
        await database.apply_governed_core_memory_lifecycle_transition(
            245, first_id, expected_current_lifecycle=core.MemoryLifecycle.CONFIRMED,
            target_lifecycle=core.MemoryLifecycle.HISTORICAL)

        retry_id = await database.add_governed_core_memory_candidate(
            245, category=core.MemoryCategory.EXPLICIT_FACT,
            content="owns a small dog", source_event_ids=[mid])
        assert retry_id == first_id
        records = await database.list_core_memory_item_records(245)
        assert len(records) == 1
        assert records[0][1].lifecycle is core.MemoryLifecycle.HISTORICAL
    run(go())


def test_governed_candidate_retry_against_existing_corrected_row_is_idempotent(tmp_db):
    """Phase 2B-1 V3 correction: CORRECTED is never reachable through the
    governed transition graph in this slice (no edge targets it -- a
    corrected item will only ever be created by a later, separate
    append-only correction transaction). A row can still legitimately be
    CORRECTED today via that future mechanism or via historical/legacy
    data, so the fixture here is built with the private unchecked
    primitive (exactly as tests/test_canonical_memory_bypass_closure.py's
    own docstring anticipates for test-only setup), NOT through any
    governed API -- there is no governed path that reaches CORRECTED to
    exercise instead. The governed candidate creator must still treat an
    existing exact-identity CORRECTED row as an idempotency match, exactly
    like every other lifecycle: no new row, existing id returned."""
    async def go():
        await _seed_user(248)
        mid = await _user_authored_message_id(248)
        corrected = core.MemoryItem(
            category=core.MemoryCategory.EXPLICIT_FACT,
            lifecycle=core.MemoryLifecycle.CORRECTED,
            content="corrected: actually works day shifts", source_event_ids=[mid])
        corrected_id = await database._add_core_memory_item_unchecked(248, corrected)

        before_count = len(await database.list_core_memory_item_records(248))

        retry_id = await database.add_governed_core_memory_candidate(
            248, category=core.MemoryCategory.EXPLICIT_FACT,
            content="corrected: actually works day shifts", source_event_ids=[mid])

        assert retry_id == corrected_id
        after_records = await database.list_core_memory_item_records(248)
        assert len(after_records) == before_count
        assert len(after_records) == 1
        assert after_records[0][1].lifecycle is core.MemoryLifecycle.CORRECTED
    run(go())


def test_governed_candidate_idempotency_deterministic_on_multiple_exact_duplicates(tmp_db):
    """Documents and proves the deterministic tie-break rule: if more than
    one existing row already matches this exact identity (only reachable
    via legacy/historical data or the raw internal primitive -- the
    governed API itself never creates a second exact duplicate), the
    LOWEST core_memory_items.id (the oldest, first-created match) is the
    one returned. Never a function of dict/set iteration order."""
    async def go():
        await _seed_user(246)
        mid = await _user_authored_message_id(246)
        dup = core.MemoryItem(category=core.MemoryCategory.GOAL,
                              lifecycle=core.MemoryLifecycle.CANDIDATE,
                              content="wants to run a 10k", source_event_ids=[mid])
        older_id = await database._add_core_memory_item_unchecked(246, dup)
        newer_id = await database._add_core_memory_item_unchecked(246, dup)
        assert older_id < newer_id

        retry_id = await database.add_governed_core_memory_candidate(
            246, category=core.MemoryCategory.GOAL,
            content="wants to run a 10k", source_event_ids=[mid])
        assert retry_id == older_id
        records = await database.list_core_memory_item_records(246)
        assert len(records) == 2  # no third row inserted
    run(go())


# ── Phase 2B-1 V6 correction -- type-exact provenance identity (Codex P2) ──
# Ordinary Python list equality is not type-exact ([True] == [1] and
# [1.0] == [1] are both True), so a malformed EXISTING row (only
# reachable via the private unchecked primitive or genuinely historical
# data -- the governed write path itself never persists a non-int member)
# must never be wrongly treated as the exact retry of a new, validly-typed
# candidate. See database._source_event_ids_exactly_equal.

@pytest.mark.parametrize("malformed_value,type_label", [
    (True, "bool"),
    (1.0, "float"),
])
def test_governed_candidate_malformed_provenance_type_is_not_a_false_exact_match(
        tmp_db, malformed_value, type_label):
    """Codex-reproduced P2: an existing malformed row with
    source_event_ids=[True] (or [1.0]) must NOT be returned as the exact
    match for a new valid candidate submitting source_event_ids=[1] -- a
    new, correctly-typed row must be created instead, and the malformed
    row must be left completely unchanged (never repaired, never
    deleted).

    Deterministic message id: tmp_db gives each parametrized run of this
    test its own fresh, empty per-test database (see the tmp_db fixture),
    so the FIRST message ever inserted in THIS run gets AUTOINCREMENT
    id=1 -- asserted explicitly below, not an accidental coincidence."""
    async def go():
        await _seed_user(249)
        mid = await _user_authored_message_id(249)
        assert mid == 1, (
            f"[{type_label}] test assumes a fresh per-test DB where the "
            "first inserted message row gets autoincrement id=1 -- see "
            "the tmp_db fixture")

        malformed = core.MemoryItem(
            category=core.MemoryCategory.GOAL,
            lifecycle=core.MemoryLifecycle.CANDIDATE,
            content="wants to run a 10k", source_event_ids=[malformed_value])
        malformed_id = await database._add_core_memory_item_unchecked(249, malformed)

        new_id = await database.add_governed_core_memory_candidate(
            249, category=core.MemoryCategory.GOAL,
            content="wants to run a 10k", source_event_ids=[1])

        assert new_id != malformed_id, (
            f"[{type_label}] malformed existing row was wrongly returned "
            "as the exact-retry match for a validly-typed new candidate")
        records = await database.list_core_memory_item_records(249)
        assert len(records) == 2
        by_id = dict(records)
        # Malformed row left completely unchanged -- same value, same type.
        assert by_id[malformed_id].source_event_ids == [malformed_value]
        assert type(by_id[malformed_id].source_event_ids[0]) is type(malformed_value)
        # New row has genuine, exactly-typed [1] provenance.
        assert by_id[new_id].source_event_ids == [1]
        assert type(by_id[new_id].source_event_ids[0]) is int
    run(go())


def test_governed_candidate_exact_retry_provenance_order_is_significant(tmp_db):
    """[1, 2] must not exact-match [2, 1] -- provenance identity is an
    ORDERED sequence, never treated as a set, and never sorted."""
    async def go():
        await _seed_user(252)
        mid1 = await _user_authored_message_id(252, "first related message")
        mid2 = await _user_authored_message_id(252, "second related message")

        first_id = await database.add_governed_core_memory_candidate(
            252, category=core.MemoryCategory.EPISODE_MAP,
            content="two related events", source_event_ids=[mid1, mid2])
        second_id = await database.add_governed_core_memory_candidate(
            252, category=core.MemoryCategory.EPISODE_MAP,
            content="two related events", source_event_ids=[mid2, mid1])

        assert second_id != first_id
        records = await database.list_core_memory_item_records(252)
        assert len(records) == 2
        by_id = dict(records)
        assert by_id[first_id].source_event_ids == [mid1, mid2]
        assert by_id[second_id].source_event_ids == [mid2, mid1]
    run(go())


def test_governed_candidate_concurrent_identical_submissions_create_only_one_row(tmp_db):
    """Genuine concurrency proof (not merely sequential retries): two
    identical submissions fired via asyncio.gather -- both truly in flight
    at once -- must still result in exactly one row. BEGIN IMMEDIATE
    serializes the second writer behind the first's RESERVED lock; by the
    time the second transaction's idempotency scan runs, it observes the
    first transaction's already-committed row."""
    async def go():
        await _seed_user(247)
        mid = await _user_authored_message_id(247)

        results = await asyncio.gather(
            database.add_governed_core_memory_candidate(
                247, category=core.MemoryCategory.PREFERENCE,
                content="prefers written summaries", source_event_ids=[mid]),
            database.add_governed_core_memory_candidate(
                247, category=core.MemoryCategory.PREFERENCE,
                content="prefers written summaries", source_event_ids=[mid]),
        )
        assert results[0] == results[1]
        records = await database.list_core_memory_item_records(247)
        assert len(records) == 1
    run(go())


# ── Phase 2B-1 -- governed lifecycle transitions ────────────────────────────

def test_governed_transition_candidate_to_proposed_updates_sql_and_json(tmp_db):
    async def go():
        await _seed_user(230)
        mid = await _user_authored_message_id(230)
        item_id = await database.add_governed_core_memory_candidate(
            230, category=core.MemoryCategory.EXPLICIT_FACT,
            content="works night shifts", source_event_ids=[mid])
        ok = await database.apply_governed_core_memory_lifecycle_transition(
            230, item_id, expected_current_lifecycle=core.MemoryLifecycle.CANDIDATE,
            target_lifecycle=core.MemoryLifecycle.PROPOSED)
        assert ok is True

        async with database.aiosqlite.connect(database.DB) as db:
            cur = await db.execute(
                "SELECT lifecycle, memory_json FROM core_memory_items WHERE id=?",
                (item_id,))
            sql_lifecycle, memory_json = await cur.fetchone()
        assert sql_lifecycle == "PROPOSED"
        assert json.loads(memory_json)["lifecycle"] == "PROPOSED"
    run(go())


def test_governed_transition_proposed_to_confirmed_works(tmp_db):
    async def go():
        await _seed_user(231)
        mid = await _user_authored_message_id(231)
        item_id = await database.add_governed_core_memory_candidate(
            231, category=core.MemoryCategory.EXPLICIT_FACT,
            content="lives alone", source_event_ids=[mid])
        assert await database.apply_governed_core_memory_lifecycle_transition(
            231, item_id, expected_current_lifecycle=core.MemoryLifecycle.CANDIDATE,
            target_lifecycle=core.MemoryLifecycle.PROPOSED) is True
        assert await database.apply_governed_core_memory_lifecycle_transition(
            231, item_id, expected_current_lifecycle=core.MemoryLifecycle.PROPOSED,
            target_lifecycle=core.MemoryLifecycle.CONFIRMED) is True

        records = await database.list_core_memory_item_records(231, influencing_only=True)
        assert len(records) == 1
        assert records[0][1].lifecycle is core.MemoryLifecycle.CONFIRMED
    run(go())


def test_governed_transition_direct_candidate_to_confirmed_rejected_no_write(tmp_db):
    async def go():
        await _seed_user(232)
        mid = await _user_authored_message_id(232)
        item_id = await database.add_governed_core_memory_candidate(
            232, category=core.MemoryCategory.EXPLICIT_FACT,
            content="has a younger sister", source_event_ids=[mid])
        with pytest.raises(ValueError):
            await database.apply_governed_core_memory_lifecycle_transition(
                232, item_id, expected_current_lifecycle=core.MemoryLifecycle.CANDIDATE,
                target_lifecycle=core.MemoryLifecycle.CONFIRMED)

        async with database.aiosqlite.connect(database.DB) as db:
            cur = await db.execute(
                "SELECT lifecycle FROM core_memory_items WHERE id=?", (item_id,))
            (sql_lifecycle,) = await cur.fetchone()
        assert sql_lifecycle == "CANDIDATE"
    run(go())


def test_governed_transition_confirmed_hypothesis_remains_category_hypothesis(tmp_db):
    async def go():
        await _seed_user(233)
        mid = await _user_authored_message_id(233)
        item_id = await database.add_governed_core_memory_candidate(
            233, category=core.MemoryCategory.HYPOTHESIS,
            content="may fear abandonment", source_event_ids=[mid])
        await database.apply_governed_core_memory_lifecycle_transition(
            233, item_id, expected_current_lifecycle=core.MemoryLifecycle.CANDIDATE,
            target_lifecycle=core.MemoryLifecycle.PROPOSED)
        await database.apply_governed_core_memory_lifecycle_transition(
            233, item_id, expected_current_lifecycle=core.MemoryLifecycle.PROPOSED,
            target_lifecycle=core.MemoryLifecycle.CONFIRMED)

        records = await database.list_core_memory_item_records(233)
        assert len(records) == 1
        item = records[0][1]
        assert item.lifecycle is core.MemoryLifecycle.CONFIRMED
        assert item.category is core.MemoryCategory.HYPOTHESIS
    run(go())


def test_governed_transition_does_not_alter_content_confidence_provenance_category(tmp_db):
    async def go():
        await _seed_user(234)
        mid = await _user_authored_message_id(234)
        item_id = await database.add_governed_core_memory_candidate(
            234, category=core.MemoryCategory.GOAL,
            content="wants to rebuild trust with his brother",
            source_event_ids=[mid], confidence=0.42)
        await database.apply_governed_core_memory_lifecycle_transition(
            234, item_id, expected_current_lifecycle=core.MemoryLifecycle.CANDIDATE,
            target_lifecycle=core.MemoryLifecycle.PROPOSED)

        records = await database.list_core_memory_item_records(234)
        item = records[0][1]
        assert item.content == "wants to rebuild trust with his brother"
        assert item.confidence == 0.42
        assert item.source_event_ids == [mid]
        assert item.category is core.MemoryCategory.GOAL
    run(go())


def test_governed_transition_stale_expected_lifecycle_performs_no_write(tmp_db):
    async def go():
        await _seed_user(235)
        mid = await _user_authored_message_id(235)
        item_id = await database.add_governed_core_memory_candidate(
            235, category=core.MemoryCategory.EXPLICIT_FACT,
            content="works remotely", source_event_ids=[mid])
        # Real current lifecycle is CANDIDATE -- caller wrongly believes PROPOSED.
        result = await database.apply_governed_core_memory_lifecycle_transition(
            235, item_id, expected_current_lifecycle=core.MemoryLifecycle.PROPOSED,
            target_lifecycle=core.MemoryLifecycle.CONFIRMED)
        assert result is False

        async with database.aiosqlite.connect(database.DB) as db:
            cur = await db.execute(
                "SELECT lifecycle FROM core_memory_items WHERE id=?", (item_id,))
            (sql_lifecycle,) = await cur.fetchone()
        assert sql_lifecycle == "CANDIDATE"
    run(go())


def test_governed_transition_wrong_user_performs_no_write(tmp_db):
    async def go():
        await _seed_user(236)
        await _seed_user(237)
        mid = await _user_authored_message_id(236)
        item_id = await database.add_governed_core_memory_candidate(
            236, category=core.MemoryCategory.EXPLICIT_FACT,
            content="owns a dog", source_event_ids=[mid])
        result = await database.apply_governed_core_memory_lifecycle_transition(
            237, item_id, expected_current_lifecycle=core.MemoryLifecycle.CANDIDATE,
            target_lifecycle=core.MemoryLifecycle.PROPOSED)
        assert result is False

        async with database.aiosqlite.connect(database.DB) as db:
            cur = await db.execute(
                "SELECT lifecycle, user_id FROM core_memory_items WHERE id=?", (item_id,))
            sql_lifecycle, owner = await cur.fetchone()
        assert sql_lifecycle == "CANDIDATE"
        assert owner == 236
    run(go())


def test_governed_transition_terminal_lifecycle_resurrection_rejected(tmp_db):
    async def go():
        await _seed_user(238)
        mid = await _user_authored_message_id(238)
        item_id = await database.add_governed_core_memory_candidate(
            238, category=core.MemoryCategory.EXPLICIT_FACT,
            content="recently changed jobs", source_event_ids=[mid])
        await database.apply_governed_core_memory_lifecycle_transition(
            238, item_id, expected_current_lifecycle=core.MemoryLifecycle.CANDIDATE,
            target_lifecycle=core.MemoryLifecycle.EXPIRED)

        with pytest.raises(ValueError):
            await database.apply_governed_core_memory_lifecycle_transition(
                238, item_id, expected_current_lifecycle=core.MemoryLifecycle.EXPIRED,
                target_lifecycle=core.MemoryLifecycle.CANDIDATE)
        with pytest.raises(ValueError):
            await database.apply_governed_core_memory_lifecycle_transition(
                238, item_id, expected_current_lifecycle=core.MemoryLifecycle.EXPIRED,
                target_lifecycle=core.MemoryLifecycle.PROPOSED)

        async with database.aiosqlite.connect(database.DB) as db:
            cur = await db.execute(
                "SELECT lifecycle FROM core_memory_items WHERE id=?", (item_id,))
            (sql_lifecycle,) = await cur.fetchone()
        assert sql_lifecycle == "EXPIRED"
    run(go())


def test_governed_transition_malformed_stored_row_is_not_repaired(tmp_db):
    """A row whose SQL lifecycle column and memory_json lifecycle key have
    already diverged (e.g. a hand-edited/legacy row) must never be silently
    'fixed' by the governed updater -- it fails closed, exactly like
    _update_core_memory_item_lifecycle_unchecked's own pre-transition check."""
    async def go():
        await _seed_user(239)
        mem = core.MemoryItem(category=core.MemoryCategory.EXPLICIT_FACT,
                              lifecycle=core.MemoryLifecycle.CANDIDATE,
                              content="a fact pending review")
        item_id = await database._add_core_memory_item_unchecked(239, mem)

        async with database.aiosqlite.connect(database.DB) as db:
            await db.execute(
                "UPDATE core_memory_items SET lifecycle='PROPOSED' WHERE id=?",
                (item_id,))
            await db.commit()

        with pytest.raises(ValueError):
            await database.apply_governed_core_memory_lifecycle_transition(
                239, item_id, expected_current_lifecycle=core.MemoryLifecycle.PROPOSED,
                target_lifecycle=core.MemoryLifecycle.CONFIRMED)

        async with database.aiosqlite.connect(database.DB) as db:
            cur = await db.execute(
                "SELECT lifecycle, memory_json FROM core_memory_items WHERE id=?",
                (item_id,))
            sql_lifecycle, memory_json = await cur.fetchone()
        assert sql_lifecycle == "PROPOSED"
        assert json.loads(memory_json)["lifecycle"] == "CANDIDATE"
    run(go())
