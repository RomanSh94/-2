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
                           repair_constraints={core.RepairConstraint.QUESTION_OVERLOAD})
    assert core.SessionState.from_dict(s.to_dict()).to_dict() == s.to_dict()


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
        await database.add_core_memory_item(30, mem)

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
        await database.add_core_memory_item(50, keep)
        await database.add_core_memory_item(50, drop)
        influencing = await database.list_core_memory_items(50, influencing_only=True)
        contents = {m.content for m in influencing}
        assert keep.content in contents
        assert drop.content not in contents
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
        await database.add_core_memory_item(60, mem)
        other_mem = core.MemoryItem(category=core.MemoryCategory.GOAL,
                                    lifecycle=core.MemoryLifecycle.CONFIRMED, content="not user 60's")
        await database.add_core_memory_item(61, other_mem)

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
