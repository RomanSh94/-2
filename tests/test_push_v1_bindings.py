"""Push V1 (Round 5) — database-level regression tests for
push_action_bindings / create_push_action_bindings / consume_push_action_binding
/ supersede_push_action_bindings, against a REAL temp SQLite DB.

Covers the exact staleness/lifecycle properties required by the task:
stale-revision rejection, double-consumption rejection, sibling-action
invalidation, old-push-superseded-by-newer-push, expiry, wrong-identity
rejection, and the account-deletion lifecycle (via the real, registry-driven
delete_all_personal_data).
"""
import asyncio
import secrets
from datetime import datetime, timedelta, timezone

import pytest

import database

run = asyncio.run

UID, CHAT_ID = 555, 555


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "bindings.db"))
    run(database.init_db())
    return database


def _future_expiry(days=14):
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _past_expiry():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")


async def _seed_user_and_revision(uid=UID):
    await database.upsert_user(uid, "u", "U", "ru")
    revision = await database.bump_user_revision(uid)
    return revision


def _rows(tokens, expires_at):
    return [{"token": tokens["push_continue"], "action": "push_continue", "expires_at": expires_at},
            {"token": tokens["push_new_topic"], "action": "push_new_topic", "expires_at": expires_at}]


def _tokens():
    return {"push_continue": secrets.token_urlsafe(9), "push_new_topic": secrets.token_urlsafe(9)}


def test_create_and_consume_round_trip(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        # OWNER CORRECTION V3, P1: an anchor-bearing batch now REQUIRES a
        # resume target -- see test_create_rejects_anchor_bearing_batch_
        # without_resume_target below for the negative case this positive
        # round-trip test is deliberately not about.
        resume_id = await database.save_message(
            UID, "user", "prior user turn", "open_chat", "ru",
            source=database.MessageSource.USER_AUTHORED)
        anchor_id = await database.save_message(
            UID, "assistant", "prior reply", "open_chat", "ru",
            source=database.MessageSource.ASSISTANT_DELIVERED)
        tokens = _tokens()
        ok = await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, anchor_id, _rows(tokens, _future_expiry()),
            resume_source_event_id=resume_id)
        assert ok is True
        result = await database.consume_push_action_binding(
            tokens["push_continue"], UID, CHAT_ID, 1)
        return result, anchor_id
    result, anchor_id = run(scenario())
    assert result is not None
    assert result.action == "push_continue"
    assert result.anchor_turn_id == anchor_id


def test_create_rejects_incomplete_action_set(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        tokens = _tokens()
        rows = [{"token": tokens["push_continue"], "action": "push_continue",
                 "expires_at": _future_expiry()}]
        with pytest.raises(ValueError):
            await database.create_push_action_bindings(UID, CHAT_ID, 1, revision, None, rows)
    run(scenario())


def test_create_rejects_unknown_action(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        rows = [{"token": "x", "action": "delete_everything", "expires_at": _future_expiry()},
                {"token": "y", "action": "push_new_topic", "expires_at": _future_expiry()}]
        with pytest.raises(ValueError):
            await database.create_push_action_bindings(UID, CHAT_ID, 1, revision, None, rows)
    run(scenario())


def test_create_fails_silently_if_revision_already_moved(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        await database.bump_user_revision(UID)  # a newer ordinary turn happened
        tokens = _tokens()
        ok = await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, None, _rows(tokens, _future_expiry()))
        return ok
    assert run(scenario()) is False


# ── A: stale revision (user sent a newer ordinary message) ─────────────────
def test_stale_revision_rejected_at_consumption(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, None, _rows(tokens, _future_expiry()))
        # user sends a newer ordinary message -> revision moves
        await database.bump_user_revision(UID)
        return await database.consume_push_action_binding(
            tokens["push_continue"], UID, CHAT_ID, 1)
    assert run(scenario()) is None


# ── B: double tap ────────────────────────────────────────────────────────
def test_double_consumption_only_succeeds_once(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, None, _rows(tokens, _future_expiry()))
        first = await database.consume_push_action_binding(
            tokens["push_continue"], UID, CHAT_ID, 1)
        second = await database.consume_push_action_binding(
            tokens["push_continue"], UID, CHAT_ID, 1)
        return first, second
    first, second = run(scenario())
    assert first is not None
    assert second is None


# ── C: sibling invalidation ─────────────────────────────────────────────
def test_consuming_one_action_invalidates_the_sibling(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, None, _rows(tokens, _future_expiry()))
        consumed = await database.consume_push_action_binding(
            tokens["push_continue"], UID, CHAT_ID, 1)
        sibling = await database.consume_push_action_binding(
            tokens["push_new_topic"], UID, CHAT_ID, 1)
        return consumed, sibling
    consumed, sibling = run(scenario())
    assert consumed is not None
    assert sibling is None


# ── D: two old pushes -> only the newest is actionable ─────────────────
def test_newer_push_supersedes_older_pushs_controls(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        old_tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, None, _rows(old_tokens, _future_expiry()))
        # A second push is sent later (SAME revision -- no ordinary user
        # turn happened in between, e.g. two scheduler ticks with no reply).
        new_tokens = _tokens()
        ok = await database.create_push_action_bindings(
            UID, CHAT_ID, 2, revision, None, _rows(new_tokens, _future_expiry()))
        old_result = await database.consume_push_action_binding(
            old_tokens["push_continue"], UID, CHAT_ID, 1)
        new_result = await database.consume_push_action_binding(
            new_tokens["push_continue"], UID, CHAT_ID, 2)
        return ok, old_result, new_result
    ok, old_result, new_result = run(scenario())
    assert ok is True
    assert old_result is None       # old push's controls are inert
    assert new_result is not None   # newest push's controls still work


# ── expiry ───────────────────────────────────────────────────────────────
def test_expired_binding_rejected(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, None, _rows(tokens, _past_expiry()))
        return await database.consume_push_action_binding(
            tokens["push_continue"], UID, CHAT_ID, 1)
    assert run(scenario()) is None


# ── wrong identity (forwarded/guessed token) ────────────────────────────
def test_wrong_user_rejected(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, None, _rows(tokens, _future_expiry()))
        return await database.consume_push_action_binding(
            tokens["push_continue"], UID + 1, CHAT_ID, 1)
    assert run(scenario()) is None


def test_wrong_source_message_id_rejected(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, None, _rows(tokens, _future_expiry()))
        return await database.consume_push_action_binding(
            tokens["push_continue"], UID, CHAT_ID, 999)
    assert run(scenario()) is None


def test_unknown_token_rejected(tmp_db):
    async def scenario():
        return await database.consume_push_action_binding("no-such-token", UID, CHAT_ID, 1)
    assert run(scenario()) is None


# ── supersede (crisis-start cleanup) ────────────────────────────────────
def test_supersede_makes_open_bindings_inert(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, None, _rows(tokens, _future_expiry()))
        superseded_count = await database.supersede_push_action_bindings(UID)
        result = await database.consume_push_action_binding(
            tokens["push_continue"], UID, CHAT_ID, 1)
        return superseded_count, result
    superseded_count, result = run(scenario())
    assert superseded_count == 2
    assert result is None


def test_supersede_is_idempotent_and_scoped_to_one_user(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        other_uid = UID + 1
        await database.upsert_user(other_uid, "u2", "U2", "ru")
        other_revision = await database.bump_user_revision(other_uid)
        tokens = _tokens()
        other_tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, None, _rows(tokens, _future_expiry()))
        await database.create_push_action_bindings(
            other_uid, other_uid, 1, other_revision, None,
            _rows(other_tokens, _future_expiry()))
        first_call = await database.supersede_push_action_bindings(UID)
        second_call = await database.supersede_push_action_bindings(UID)
        other_result = await database.consume_push_action_binding(
            other_tokens["push_continue"], other_uid, other_uid, 1)
        return first_call, second_call, other_result
    first_call, second_call, other_result = run(scenario())
    assert first_call == 2
    assert second_call == 0          # idempotent -- nothing left to supersede
    assert other_result is not None  # a DIFFERENT user's bindings are untouched


# ── POST-CODEX CORRECTION §2 (P1): revision=0 alone must never be enough
# to recreate bindings after a real delete-all -- the anchor-existence
# check inside create_push_action_bindings closes this independently of
# the revision comparison. ──────────────────────────────────────────────
def test_create_rejects_when_anchor_was_deleted_before_creation(tmp_db):
    async def scenario():
        await database.upsert_user(UID, "u", "U", "ru")
        # OWNER CORRECTION V3, P1: an anchor-bearing batch now requires a
        # resume target too -- seed one so the pure contract check passes
        # and this test still reaches (and proves) the LIVE anchor-
        # existence check specifically, not merely the unrelated
        # missing-target check.
        resume_id = await database.save_message(
            UID, "user", "prior user turn", "open_chat", "ru",
            source=database.MessageSource.USER_AUTHORED)
        anchor_id = await database.save_message(
            UID, "assistant", "prior reply", "open_chat", "ru",
            source=database.MessageSource.ASSISTANT_DELIVERED)
        # No ordinary user turn has happened yet -- captured revision is 0,
        # exactly the same fallback value current_revision collapses to
        # AFTER delete-all removes the user_interaction_revision row too.
        captured_revision = await database.get_user_revision(UID)
        assert captured_revision == 0
        await database.delete_all_personal_data(UID)
        tokens = _tokens()
        ok = await database.create_push_action_bindings(
            UID, CHAT_ID, 1, captured_revision, anchor_id, _rows(tokens, _future_expiry()),
            resume_source_event_id=resume_id)
        async with database.aiosqlite.connect(database.DB) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM push_action_bindings WHERE user_id=?", (UID,))
            (count,) = await cur.fetchone()
        return ok, count
    ok, count = run(scenario())
    assert ok is False
    assert count == 0


def test_create_still_succeeds_with_null_anchor_when_revision_matches(tmp_db):
    # Anchor-existence check must be skipped (not treated as a failure)
    # when anchor_turn_id is None -- preserves the pre-correction behavior
    # for a null anchor, which the current Push V1 scheduler never actually
    # passes but the function's signature still allows.
    async def scenario():
        revision = await _seed_user_and_revision()
        tokens = _tokens()
        return await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, None, _rows(tokens, _future_expiry()))
    assert run(scenario()) is True


# ── H: account deletion removes bindings ────────────────────────────────
def test_account_deletion_removes_push_bindings(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, None, _rows(tokens, _future_expiry()))
        summary = await database.delete_all_personal_data(UID)
        result = await database.consume_push_action_binding(
            tokens["push_continue"], UID, CHAT_ID, 1)
        return summary, result
    summary, result = run(scenario())
    assert summary["push_action_bindings"] == 2
    assert result is None  # the deleted account cannot restore state via an old push


# ── Owner Correction #1, Blocker 1: get_unresolved_crisis (no recency bound) ─
def test_get_unresolved_crisis_finds_a_row_older_than_24h(tmp_db):
    async def scenario():
        await database.upsert_user(UID, "u", "U", "ru")
        eid = await database.log_crisis_event(UID, "critical", 10, ["suicide"], "x", "ru")
        async with database.aiosqlite.connect(database.DB) as db:
            await db.execute(
                "UPDATE crisis_events SET created_at=datetime('now','-72 hours') "
                "WHERE id=?", (eid,))
            await db.commit()
        return eid, await database.get_unresolved_crisis(UID)
    eid, result = run(scenario())
    assert result is not None
    got_eid, stage, lang = result
    assert got_eid == eid
    assert lang == "ru"


def test_get_unresolved_crisis_returns_none_when_resolved(tmp_db):
    async def scenario():
        await database.upsert_user(UID, "u", "U", "ru")
        eid = await database.log_crisis_event(UID, "critical", 10, ["suicide"], "x", "ru")
        await database.resolve_crisis(eid)
        return await database.get_unresolved_crisis(UID)
    assert run(scenario()) is None


def test_get_unresolved_crisis_returns_none_with_no_crisis_at_all(tmp_db):
    async def scenario():
        await database.upsert_user(UID, "u", "U", "ru")
        return await database.get_unresolved_crisis(UID)
    assert run(scenario()) is None


def test_get_unresolved_crisis_picks_latest_when_several_exist(tmp_db):
    async def scenario():
        await database.upsert_user(UID, "u", "U", "ru")
        await database.log_crisis_event(UID, "critical", 10, ["suicide"], "x", "ru")
        await database.resolve_crisis((await database.get_unresolved_crisis(UID))[0])
        second = await database.log_crisis_event(UID, "high", 8, ["self_harm"], "y", "ru")
        return second, await database.get_unresolved_crisis(UID)
    second, result = run(scenario())
    assert result is not None
    assert result[0] == second  # the still-unresolved one, not the resolved one


# ── Owner Correction #1, Blocker 4A: successful consumption bumps revision ──
def test_successful_consumption_bumps_revision_exactly_once(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, None, _rows(tokens, _future_expiry()))
        before = await database.get_user_revision(UID)
        result = await database.consume_push_action_binding(
            tokens["push_continue"], UID, CHAT_ID, 1)
        after = await database.get_user_revision(UID)
        return before, after, result
    before, after, result = run(scenario())
    assert result is not None
    assert after == before + 1


def test_failed_consumption_does_not_bump_revision_wrong_user(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, None, _rows(tokens, _future_expiry()))
        before = await database.get_user_revision(UID)
        result = await database.consume_push_action_binding(
            tokens["push_continue"], UID + 999, CHAT_ID, 1)
        after = await database.get_user_revision(UID)
        return before, after, result
    before, after, result = run(scenario())
    assert result is None
    assert after == before


def test_stale_revision_consumption_does_not_bump_revision(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, None, _rows(tokens, _future_expiry()))
        await database.bump_user_revision(UID)  # simulate a newer ordinary turn
        before = await database.get_user_revision(UID)
        result = await database.consume_push_action_binding(
            tokens["push_continue"], UID, CHAT_ID, 1)
        after = await database.get_user_revision(UID)
        return before, after, result
    before, after, result = run(scenario())
    assert result is None
    assert after == before


def test_expired_consumption_does_not_bump_revision(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, None, _rows(tokens, _past_expiry()))
        before = await database.get_user_revision(UID)
        result = await database.consume_push_action_binding(
            tokens["push_continue"], UID, CHAT_ID, 1)
        after = await database.get_user_revision(UID)
        return before, after, result
    before, after, result = run(scenario())
    assert result is None
    assert after == before


def test_double_consumption_second_attempt_does_not_bump_revision_again(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, None, _rows(tokens, _future_expiry()))
        await database.consume_push_action_binding(tokens["push_continue"], UID, CHAT_ID, 1)
        after_first = await database.get_user_revision(UID)
        second = await database.consume_push_action_binding(
            tokens["push_continue"], UID, CHAT_ID, 1)
        after_second = await database.get_user_revision(UID)
        return after_first, after_second, second
    after_first, after_second, second = run(scenario())
    assert second is None
    assert after_second == after_first  # no double bump


def test_revision_bump_makes_a_newer_binding_batch_captured_before_it_fail(tmp_db):
    # "old push binding current -> scheduler sends newer plain push,
    # captures same revision -> user consumes old push action -> revision
    # remains unchanged -> newer push bindings can still be created on the
    # same revision" -- the exact race Correction #1 Blocker 4A closes.
    async def scenario():
        revision = await _seed_user_and_revision()
        old_tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, None, _rows(old_tokens, _future_expiry()))
        # A newer push is being prepared and has ALREADY captured `revision`
        # (the value that was live at the moment it read get_user_revision),
        # but has not yet called create_push_action_bindings.
        captured_revision_for_newer_push = revision

        # The user taps the OLD push's Continue button first.
        consumed = await database.consume_push_action_binding(
            old_tokens["push_continue"], UID, CHAT_ID, 1)

        # The newer push now tries to create its bindings against the
        # revision it captured BEFORE the consumption above.
        new_tokens = _tokens()
        create_ok = await database.create_push_action_bindings(
            UID, CHAT_ID, 2, captured_revision_for_newer_push, None,
            _rows(new_tokens, _future_expiry()))
        return consumed, create_ok
    consumed, create_ok = run(scenario())
    assert consumed is not None
    assert create_ok is False  # revision had already moved -- creation correctly refused


def test_revision_bump_makes_an_unrelated_older_control_stale(tmp_db):
    # An existing revision-bound control (a professional-entry-triage
    # binding, standing in for "any other revision-gated interaction
    # surface") issued at the SAME revision as an open push binding must
    # become stale once the push action is consumed.
    from professional_reply_affordances import EntryTriageCategory
    async def scenario():
        revision = await _seed_user_and_revision()
        push_tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, None, _rows(push_tokens, _future_expiry()))

        triage_token = secrets.token_urlsafe(9)
        one_category = next(iter(EntryTriageCategory))
        # create_professional_entry_triage_bindings requires exactly one
        # row per EntryTriageCategory member -- build the full set so this
        # stays a realistic, valid binding batch, not a hand-trimmed one.
        rows = [{"token": (triage_token if c is one_category else secrets.token_urlsafe(9)),
                 "category": c, "expires_at": _future_expiry()}
                for c in EntryTriageCategory]
        await database.create_professional_entry_triage_bindings(
            UID, CHAT_ID, 1, revision, rows)

        await database.consume_push_action_binding(push_tokens["push_continue"], UID, CHAT_ID, 1)

        return await database.consume_professional_entry_triage_binding(
            triage_token, UID, CHAT_ID, 1)
    result = run(scenario())
    assert result is None  # stale -- the push consumption's revision bump invalidated it


# ═══════════════════════════════════════════════════════════════════════════
# PUSH RESUME TARGET V1 -- resume_source_event_id round-trip, validation, and
# guard coverage. anchor_turn_id/resume_source_event_id-agnostic tests above
# are all backward compatible (resume_source_event_id defaults to None) and
# were deliberately left untouched.
# ═══════════════════════════════════════════════════════════════════════════

async def _seed_grounded_pair(uid=UID):
    """A genuine USER_AUTHORED row followed by a genuine ASSISTANT_DELIVERED
    row -- the smallest realistic shape for a resume target + anchor pair.
    Returns (resume_source_event_id, anchor_turn_id)."""
    resume_id = await database.save_message(
        uid, "user", "prior user turn", "open_chat", "ru",
        source=database.MessageSource.USER_AUTHORED)
    anchor_id = await database.save_message(
        uid, "assistant", "prior reply", "open_chat", "ru",
        source=database.MessageSource.ASSISTANT_DELIVERED)
    return resume_id, anchor_id


def test_resume_source_event_id_round_trips_through_create_and_consume(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        resume_id, anchor_id = await _seed_grounded_pair()
        tokens = _tokens()
        ok = await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, anchor_id, _rows(tokens, _future_expiry()),
            resume_source_event_id=resume_id)
        assert ok is True
        result = await database.consume_push_action_binding(
            tokens["push_continue"], UID, CHAT_ID, 1)
        return result, resume_id
    result, resume_id = run(scenario())
    assert result is not None
    assert result.resume_source_event_id == resume_id


# ── OWNER CORRECTION V3, P1: NULL is a legacy-only storage state. A row
# with resume_source_event_id IS NULL can only legitimately exist as a
# pre-PUSH-RESUME-TARGET-V1 row the modern create API never wrote -- so
# this test seeds it via a DIRECT INSERT (simulating exactly that), never
# through create_push_action_bindings, which now REFUSES to manufacture
# one whenever a real anchor_turn_id is also present (see
# test_create_rejects_anchor_bearing_batch_without_resume_target below).
def test_legacy_null_resume_target_row_is_still_readable_and_consumable(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        anchor_id = await database.save_message(
            UID, "assistant", "prior reply", "open_chat", "ru",
            source=database.MessageSource.ASSISTANT_DELIVERED)
        tokens = _tokens()
        expires_at = _future_expiry()
        async with database.aiosqlite.connect(database.DB) as db:
            for row in _rows(tokens, expires_at):
                await db.execute(
                    "INSERT INTO push_action_bindings "
                    "(token, user_id, chat_id, source_message_id, action, "
                    " anchor_turn_id, resume_source_event_id, binding_revision, expires_at) "
                    "VALUES (?,?,?,?,?,?,NULL,?,?)",
                    (row["token"], UID, CHAT_ID, 1, row["action"],
                     anchor_id, revision, row["expires_at"]))
            await db.commit()
        return await database.consume_push_action_binding(
            tokens["push_continue"], UID, CHAT_ID, 1)
    result = run(scenario())
    assert result is not None
    assert result.action == "push_continue"
    assert result.resume_source_event_id is None


def test_resume_source_event_id_is_shared_across_both_binding_rows(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        resume_id, anchor_id = await _seed_grounded_pair()
        tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, anchor_id, _rows(tokens, _future_expiry()),
            resume_source_event_id=resume_id)
        async with database.aiosqlite.connect(database.DB) as db:
            cur = await db.execute(
                "SELECT action, resume_source_event_id FROM push_action_bindings "
                "WHERE user_id=? ORDER BY action", (UID,))
            rows = await cur.fetchall()
        return rows, resume_id
    rows, resume_id = run(scenario())
    assert rows == [("push_continue", resume_id), ("push_new_topic", resume_id)]


def test_create_rejects_resume_source_event_id_without_an_anchor(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        resume_id, _anchor_id = await _seed_grounded_pair()
        tokens = _tokens()
        with pytest.raises(ValueError):
            await database.create_push_action_bindings(
                UID, CHAT_ID, 1, revision, None, _rows(tokens, _future_expiry()),
                resume_source_event_id=resume_id)
    run(scenario())


def test_create_rejects_resume_source_event_id_not_strictly_before_anchor(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        _resume_id, anchor_id = await _seed_grounded_pair()
        tokens = _tokens()
        with pytest.raises(ValueError):
            # resume_source_event_id == anchor_turn_id (not strictly less).
            await database.create_push_action_bindings(
                UID, CHAT_ID, 1, revision, anchor_id, _rows(tokens, _future_expiry()),
                resume_source_event_id=anchor_id)
    run(scenario())


def test_create_rejects_non_positive_resume_source_event_id(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        _resume_id, anchor_id = await _seed_grounded_pair()
        tokens = _tokens()
        with pytest.raises(ValueError):
            await database.create_push_action_bindings(
                UID, CHAT_ID, 1, revision, anchor_id, _rows(tokens, _future_expiry()),
                resume_source_event_id=0)
    run(scenario())


# ── OWNER CORRECTION V3, P1: FROZEN CREATION CONTRACT ───────────────────────
def test_create_rejects_anchor_bearing_batch_without_resume_target(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        anchor_id = await database.save_message(
            UID, "assistant", "prior reply", "open_chat", "ru",
            source=database.MessageSource.ASSISTANT_DELIVERED)
        tokens = _tokens()
        with pytest.raises(ValueError):
            await database.create_push_action_bindings(
                UID, CHAT_ID, 1, revision, anchor_id, _rows(tokens, _future_expiry()))
        # No SQL ran at all -- the pure contract check fails before the
        # transaction even opens.
        return await _count_push_bindings()
    count = run(scenario())
    assert count == 0


def test_invalid_targetless_new_batch_does_not_supersede_older_open_batch(tmp_db):
    # The ValueError from a targetless anchor-bearing batch is raised
    # BEFORE any DB connection is even opened -- so an older, genuinely
    # open batch must survive completely untouched, exactly like the
    # already-covered live-race atomicity case above.
    async def scenario():
        revision = await _seed_user_and_revision()
        resume_id, anchor_id = await _seed_grounded_pair()
        old_tokens = _tokens()
        older_ok = await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, anchor_id, _rows(old_tokens, _future_expiry()),
            resume_source_event_id=resume_id)
        assert older_ok is True

        new_tokens = _tokens()
        with pytest.raises(ValueError):
            await database.create_push_action_bindings(
                UID, CHAT_ID, 2, revision, anchor_id, _rows(new_tokens, _future_expiry()))

        old_consumed = await database.consume_push_action_binding(
            old_tokens["push_continue"], UID, CHAT_ID, 1)
        new_consumed = await database.consume_push_action_binding(
            new_tokens["push_continue"], UID, CHAT_ID, 2)
        return old_consumed, new_consumed
    old_consumed, new_consumed = run(scenario())
    assert old_consumed is not None          # the older batch is untouched -- still open
    assert old_consumed.action == "push_continue"
    assert new_consumed is None              # nothing was ever written for the newer batch


async def _count_push_bindings(uid=UID):
    async with database.aiosqlite.connect(database.DB) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM push_action_bindings WHERE user_id=?", (uid,))
        (n,) = await cur.fetchone()
        return n


# ── OWNER CORRECTION P1-A: fail-closed, whole-batch rejection ──────────────
# A supplied-but-unresolvable resume_source_event_id must never be silently
# downgraded to NULL -- it must fail the ENTIRE new binding batch (zero rows
# written), exactly like a missing anchor already does. NULL remains a
# legitimate STORAGE state only for a legacy row the modern create API never
# wrote in the first place (see test_legacy_null_resume_target_row_is_still_
# readable_and_consumable above, and test_create_rejects_anchor_bearing_
# batch_without_resume_target below).
def test_create_rejects_nonexistent_resume_source_event_id_whole_batch(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        resume_id, anchor_id = await _seed_grounded_pair()
        # The resume target row is deleted (e.g. the exact race the
        # scheduler closes between context-fetch and binding creation) --
        # still structurally < anchor_id, but no longer resolves live.
        async with database.aiosqlite.connect(database.DB) as db:
            await db.execute("DELETE FROM messages WHERE id=?", (resume_id,))
            await db.commit()
        tokens = _tokens()
        ok = await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, anchor_id, _rows(tokens, _future_expiry()),
            resume_source_event_id=resume_id)
        return ok, await _count_push_bindings()
    ok, count = run(scenario())
    assert ok is False
    assert count == 0  # neither push_continue nor push_new_topic was written


def test_create_rejects_assistant_role_resume_source_event_id_whole_batch(tmp_db):
    # A role='assistant' row (wrong role) at that id must be rejected live
    # exactly like a role='user' AND source != 'USER_AUTHORED' row would --
    # role='user' alone is never proof of authorship (mirrors the
    # canonical-memory governance boundary's own coherent-pair check).
    async def scenario():
        revision = await _seed_user_and_revision()
        wrong_role_id = await database.save_message(
            UID, "assistant", "not a user turn", "open_chat", "ru",
            source=database.MessageSource.ASSISTANT_DELIVERED)
        anchor_id = await database.save_message(
            UID, "assistant", "prior reply", "open_chat", "ru",
            source=database.MessageSource.ASSISTANT_DELIVERED)
        tokens = _tokens()
        ok = await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, anchor_id, _rows(tokens, _future_expiry()),
            resume_source_event_id=wrong_role_id)
        return ok, await _count_push_bindings()
    ok, count = run(scenario())
    assert ok is False
    assert count == 0


def test_create_rejects_foreign_user_resume_source_event_id_whole_batch(tmp_db):
    # The target row is a genuine role='user'/source='USER_AUTHORED' turn --
    # but it belongs to a DIFFERENT user than the one creating this batch.
    async def scenario():
        revision = await _seed_user_and_revision()
        other_uid = UID + 1
        await database.upsert_user(other_uid, "other", "Other", "ru")
        foreign_resume_id = await database.save_message(
            other_uid, "user", "someone else's turn", "open_chat", "ru",
            source=database.MessageSource.USER_AUTHORED)
        anchor_id = await database.save_message(
            UID, "assistant", "prior reply", "open_chat", "ru",
            source=database.MessageSource.ASSISTANT_DELIVERED)
        tokens = _tokens()
        ok = await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, anchor_id, _rows(tokens, _future_expiry()),
            resume_source_event_id=foreign_resume_id)
        return ok, await _count_push_bindings()
    ok, count = run(scenario())
    assert ok is False
    assert count == 0


def test_create_rejects_role_user_but_source_not_user_authored_whole_batch(tmp_db):
    # role='user' alone is never proof of authorship -- a SYNTHETIC_UI (or
    # any non-USER_AUTHORED) row with role='user' must be rejected exactly
    # like a wrong role would.
    async def scenario():
        revision = await _seed_user_and_revision()
        wrong_source_id = await database.save_message(
            UID, "user", "synthetic button label", "open_chat", "ru",
            source=database.MessageSource.SYNTHETIC_UI)
        anchor_id = await database.save_message(
            UID, "assistant", "prior reply", "open_chat", "ru",
            source=database.MessageSource.ASSISTANT_DELIVERED)
        tokens = _tokens()
        ok = await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, anchor_id, _rows(tokens, _future_expiry()),
            resume_source_event_id=wrong_source_id)
        return ok, await _count_push_bindings()
    ok, count = run(scenario())
    assert ok is False
    assert count == 0


def test_create_rejects_source_user_authored_but_role_not_user_whole_batch(tmp_db):
    # An incoherent/corrupted row: source='USER_AUTHORED' but role=
    # 'assistant' -- the coherent role+source PAIR is what is trusted, not
    # either column alone (same reasoning as the previous test, mirrored).
    async def scenario():
        revision = await _seed_user_and_revision()
        await database.upsert_user(UID, "u", "U", "ru")
        async with database.aiosqlite.connect(database.DB) as db:
            cur = await db.execute(
                "INSERT INTO messages (user_id, role, content, scenario, lang, source) "
                "VALUES (?, 'assistant', 'incoherent row', 'open_chat', 'ru', 'USER_AUTHORED')",
                (UID,))
            await db.commit()
            incoherent_id = cur.lastrowid
        anchor_id = await database.save_message(
            UID, "assistant", "prior reply", "open_chat", "ru",
            source=database.MessageSource.ASSISTANT_DELIVERED)
        tokens = _tokens()
        ok = await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, anchor_id, _rows(tokens, _future_expiry()),
            resume_source_event_id=incoherent_id)
        return ok, await _count_push_bindings()
    ok, count = run(scenario())
    assert ok is False
    assert count == 0


def test_create_rejects_bool_resume_source_event_id(tmp_db):
    # type(True) is bool, not int -- must never be silently accepted as 1.
    async def scenario():
        revision = await _seed_user_and_revision()
        _resume_id, anchor_id = await _seed_grounded_pair()
        tokens = _tokens()
        with pytest.raises(ValueError):
            await database.create_push_action_bindings(
                UID, CHAT_ID, 1, revision, anchor_id, _rows(tokens, _future_expiry()),
                resume_source_event_id=True)
    run(scenario())


def test_create_rejects_negative_resume_source_event_id(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        _resume_id, anchor_id = await _seed_grounded_pair()
        tokens = _tokens()
        with pytest.raises(ValueError):
            await database.create_push_action_bindings(
                UID, CHAT_ID, 1, revision, anchor_id, _rows(tokens, _future_expiry()),
                resume_source_event_id=-1)
    run(scenario())


# ── P1-A atomicity: an invalid NEW batch must never disturb an existing
# OPEN batch (validation happens strictly before the supersede step). ──────
def test_invalid_new_contextual_batch_leaves_older_open_batch_untouched(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        resume_id, anchor_id = await _seed_grounded_pair()
        old_tokens = _tokens()
        older_ok = await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, anchor_id, _rows(old_tokens, _future_expiry()),
            resume_source_event_id=resume_id)
        assert older_ok is True

        # A second, newer push is prepared at the SAME revision (no ordinary
        # user turn happened in between -- e.g. two scheduler ticks with no
        # reply) but its OWN resume target no longer resolves live.
        async with database.aiosqlite.connect(database.DB) as db:
            await db.execute("DELETE FROM messages WHERE id=?", (resume_id,))
            await db.commit()
        new_tokens = _tokens()
        newer_ok = await database.create_push_action_bindings(
            UID, CHAT_ID, 2, revision, anchor_id, _rows(new_tokens, _future_expiry()),
            resume_source_event_id=resume_id)

        # The OLDER batch must remain exactly as it was: still open, still
        # consumable, never superseded by the failed newer attempt.
        old_consumed = await database.consume_push_action_binding(
            old_tokens["push_continue"], UID, CHAT_ID, 1)
        new_consumed = await database.consume_push_action_binding(
            new_tokens["push_continue"], UID, CHAT_ID, 2)
        return newer_ok, old_consumed, new_consumed
    newer_ok, old_consumed, new_consumed = run(scenario())
    assert newer_ok is False                # the invalid newer batch was rejected
    assert old_consumed is not None          # the older batch is untouched -- still open
    assert old_consumed.action == "push_continue"
    assert new_consumed is None              # nothing was ever written for the newer batch


def test_final_push_keyboard_publish_guard_passes_with_valid_resume_target(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        resume_id, anchor_id = await _seed_grounded_pair()
        tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, anchor_id, _rows(tokens, _future_expiry()),
            resume_source_event_id=resume_id)
        return await database.final_push_keyboard_publish_guard(
            UID, CHAT_ID, 1, revision, anchor_id, tokens,
            resume_source_event_id=resume_id)
    assert run(scenario()) is True


def test_final_push_keyboard_publish_guard_blocks_when_resume_target_deleted_after_creation(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        resume_id, anchor_id = await _seed_grounded_pair()
        tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, anchor_id, _rows(tokens, _future_expiry()),
            resume_source_event_id=resume_id)
        # The resume target row is deleted in the gap AFTER binding creation
        # but BEFORE keyboard publication -- the exact same class of race
        # final_push_keyboard_publish_guard already closes for anchor_turn_id.
        async with database.aiosqlite.connect(database.DB) as db:
            await db.execute("DELETE FROM messages WHERE id=?", (resume_id,))
            await db.commit()
        return await database.final_push_keyboard_publish_guard(
            UID, CHAT_ID, 1, revision, anchor_id, tokens,
            resume_source_event_id=resume_id)
    assert run(scenario()) is False


# ── OWNER CORRECTION P1-B: exact stored-value match, not permissive EXISTS ─
def test_final_push_keyboard_publish_guard_rejects_expected_target_mismatch(tmp_db):
    # The bindings genuinely carry resume_id -- but the CALLER (a wiring
    # bug, or a stale/wrong in-memory value) asks the guard to verify a
    # DIFFERENT id. A permissive "IS NULL OR EXISTS(some target)" check
    # would wrongly pass here (a valid OTHER target exists); the guard must
    # instead prove the STORED value on both rows equals what was asked.
    async def scenario():
        revision = await _seed_user_and_revision()
        resume_id, anchor_id = await _seed_grounded_pair()
        other_resume_id = await database.save_message(
            UID, "user", "a different, unrelated real user turn", "open_chat", "ru",
            source=database.MessageSource.USER_AUTHORED)
        tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, anchor_id, _rows(tokens, _future_expiry()),
            resume_source_event_id=resume_id)
        # other_resume_id is a genuine, live, resolvable target -- just NOT
        # the one actually stored on these bindings.
        return await database.final_push_keyboard_publish_guard(
            UID, CHAT_ID, 1, revision, anchor_id, tokens,
            resume_source_event_id=other_resume_id)
    assert run(scenario()) is False


# ── OWNER CORRECTION V3, P2: the publish guard must bind the STORED anchor
# too, not merely prove "some valid assistant anchor exists somewhere". ────
def test_final_push_keyboard_publish_guard_rejects_stored_anchor_mismatch(tmp_db):
    # TWO genuinely valid assistant rows exist -- a weak implementation
    # that only checks "does a role='assistant' row exist at the id the
    # caller names" would wrongly PASS here, since other_anchor_id is a
    # perfectly real, live assistant row too. The correct implementation
    # proves the bindings THEMSELVES actually carry the id being asked
    # about, not merely that SOME valid anchor exists in the database.
    async def scenario():
        revision = await _seed_user_and_revision()
        resume_id, anchor_id = await _seed_grounded_pair()
        other_anchor_id = await database.save_message(
            UID, "assistant", "a different, unrelated genuine reply", "open_chat", "ru",
            source=database.MessageSource.ASSISTANT_DELIVERED)
        tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, anchor_id, _rows(tokens, _future_expiry()),
            resume_source_event_id=resume_id)
        # other_anchor_id is a genuine, live, resolvable ASSISTANT anchor --
        # just NOT the one actually stored on these bindings.
        return await database.final_push_keyboard_publish_guard(
            UID, CHAT_ID, 1, revision, other_anchor_id, tokens,
            resume_source_event_id=resume_id)
    assert run(scenario()) is False


# ── OWNER CORRECTION P2-A: final callback reply-delivery guard rejects an
# expected-target mismatch against the exact consumed token row. ──────────
def test_final_push_action_reply_delivery_guard_rejects_expected_target_mismatch(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        resume_id, anchor_id = await _seed_grounded_pair()
        other_resume_id = await database.save_message(
            UID, "user", "a different, unrelated real user turn", "open_chat", "ru",
            source=database.MessageSource.USER_AUTHORED)
        tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, anchor_id, _rows(tokens, _future_expiry()),
            resume_source_event_id=resume_id)
        result = await database.consume_push_action_binding(
            tokens["push_continue"], UID, CHAT_ID, 1)
        assert result.resume_source_event_id == resume_id
        # The caller claims a DIFFERENT (but otherwise valid) target than
        # what this exact consumed row actually carries.
        return await database.final_push_action_reply_delivery_guard(
            UID, CHAT_ID, 1, tokens["push_continue"], "push_continue",
            result.post_consume_revision, anchor_id,
            resume_source_event_id=other_resume_id, require_live_resume_target=True)
    assert run(scenario()) is False


def test_final_push_action_reply_delivery_guard_passes_with_exact_consumed_target(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        resume_id, anchor_id = await _seed_grounded_pair()
        tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, anchor_id, _rows(tokens, _future_expiry()),
            resume_source_event_id=resume_id)
        result = await database.consume_push_action_binding(
            tokens["push_continue"], UID, CHAT_ID, 1)
        return await database.final_push_action_reply_delivery_guard(
            UID, CHAT_ID, 1, tokens["push_continue"], "push_continue",
            result.post_consume_revision, anchor_id,
            resume_source_event_id=result.resume_source_event_id,
            require_live_anchor=True,
            require_live_resume_target=True)
    assert run(scenario()) is True


# ── OWNER CORRECTION V4, P2-2: the final callback fence must bind the
# STORED anchor too, not merely prove "some valid assistant anchor exists
# somewhere" -- the same class of fix already applied to the resume target
# above and to the publish guard's own anchor check. ───────────────────────
def test_final_push_action_reply_delivery_guard_rejects_stored_anchor_mismatch(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        resume_id, anchor_id = await _seed_grounded_pair()
        other_anchor_id = await database.save_message(
            UID, "assistant", "a different, unrelated genuine reply", "open_chat", "ru",
            source=database.MessageSource.ASSISTANT_DELIVERED)
        tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, anchor_id, _rows(tokens, _future_expiry()),
            resume_source_event_id=resume_id)
        result = await database.consume_push_action_binding(
            tokens["push_continue"], UID, CHAT_ID, 1)
        # other_anchor_id is a genuine, live, resolvable ASSISTANT anchor --
        # just NOT the one actually stored on this consumed row.
        return await database.final_push_action_reply_delivery_guard(
            UID, CHAT_ID, 1, tokens["push_continue"], "push_continue",
            result.post_consume_revision, other_anchor_id,
            resume_source_event_id=result.resume_source_event_id,
            require_live_anchor=True,
            require_live_resume_target=True)
    assert run(scenario()) is False


def test_record_push_action_reply_delivery_rejects_stored_anchor_mismatch(tmp_db):
    async def scenario():
        revision = await _seed_user_and_revision()
        resume_id, anchor_id = await _seed_grounded_pair()
        other_anchor_id = await database.save_message(
            UID, "assistant", "a different, unrelated genuine reply", "open_chat", "ru",
            source=database.MessageSource.ASSISTANT_DELIVERED)
        tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, anchor_id, _rows(tokens, _future_expiry()),
            resume_source_event_id=resume_id)
        result = await database.consume_push_action_binding(
            tokens["push_continue"], UID, CHAT_ID, 1)
        before_count = await _count_messages_for(UID)
        persisted = await database.record_push_action_reply_delivery(
            UID, CHAT_ID, 1, tokens["push_continue"], "push_continue",
            "some grounded reply", "push_v1_contextual_continue", "ru",
            result.post_consume_revision, other_anchor_id,
            resume_source_event_id=result.resume_source_event_id,
            require_live_anchor=True,
            require_live_resume_target=True)
        after_count = await _count_messages_for(UID)
        return persisted, before_count, after_count
    persisted, before_count, after_count = run(scenario())
    assert persisted is False
    # Nothing was written -- a rejected fence must never insert the reply.
    assert after_count == before_count


async def _count_messages_for(uid):
    async with database.aiosqlite.connect(database.DB) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM messages WHERE user_id=? AND role='assistant' "
            "AND source=?", (uid, database.MessageSource.ASSISTANT_DELIVERED.value))
        (n,) = await cur.fetchone()
        return n


def test_final_push_action_reply_delivery_guard_new_topic_ignores_live_target_requirement(tmp_db):
    # New Topic's reply never depends on the resume target's (or the
    # anchor's) CONTENT -- require_live_resume_target=False and
    # require_live_anchor=False must let this pass even though the target
    # row has since been deleted, as long as the IDENTITY re-check (the
    # exact stored values on the consumed row) still holds for BOTH
    # anchor_turn_id and resume_source_event_id.
    async def scenario():
        revision = await _seed_user_and_revision()
        resume_id, anchor_id = await _seed_grounded_pair()
        tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, anchor_id, _rows(tokens, _future_expiry()),
            resume_source_event_id=resume_id)
        result = await database.consume_push_action_binding(
            tokens["push_new_topic"], UID, CHAT_ID, 1)
        async with database.aiosqlite.connect(database.DB) as db:
            await db.execute("DELETE FROM messages WHERE id=?", (resume_id,))
            await db.commit()
        # OWNER CORRECTION V4, P2-2: the caller must still pass the row's
        # own true anchor_turn_id (never None) as the identity value --
        # only require_live_anchor controls whether it must ALSO resolve
        # live, and New Topic does not need that.
        return await database.final_push_action_reply_delivery_guard(
            UID, CHAT_ID, 1, tokens["push_new_topic"], "push_new_topic",
            result.post_consume_revision, result.anchor_turn_id,
            resume_source_event_id=result.resume_source_event_id,
            require_live_anchor=False,
            require_live_resume_target=False)
    assert run(scenario()) is True


def test_final_push_keyboard_publish_guard_rejects_none_resume_target(tmp_db):
    # OWNER CORRECTION V3, P1: final_push_keyboard_publish_guard is a
    # MODERN publication function -- its own anchor_turn_id parameter is
    # never optional, so resume_source_event_id=None (the default) is
    # rejected immediately, before the authoritative SELECT even runs,
    # regardless of what the bindings actually have stored.
    async def scenario():
        revision = await _seed_user_and_revision()
        resume_id = await database.save_message(
            UID, "user", "prior user turn", "open_chat", "ru",
            source=database.MessageSource.USER_AUTHORED)
        anchor_id = await database.save_message(
            UID, "assistant", "prior reply", "open_chat", "ru",
            source=database.MessageSource.ASSISTANT_DELIVERED)
        tokens = _tokens()
        await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, anchor_id, _rows(tokens, _future_expiry()),
            resume_source_event_id=resume_id)
        # The caller asks the guard to publish as though there were NO
        # resume target at all -- but the bindings genuinely carry one.
        return await database.final_push_keyboard_publish_guard(
            UID, CHAT_ID, 1, revision, anchor_id, tokens)
    assert run(scenario()) is False


# ── Additive migration coverage ─────────────────────────────────────────────
def test_resume_source_event_id_column_exists_on_a_fresh_db(tmp_db):
    async def scenario():
        async with database.aiosqlite.connect(database.DB) as db:
            cur = await db.execute("PRAGMA table_info(push_action_bindings)")
            return [r[1] for r in await cur.fetchall()]
    columns = run(scenario())
    assert "resume_source_event_id" in columns


def test_apply_migrations_adding_resume_source_event_id_is_idempotent(tmp_db):
    # OWNER CORRECTION V3, P2 -- strengthened beyond mere column presence:
    # simulates an UPGRADED pre-PUSH-RESUME-TARGET-V1 database carrying a
    # REAL, realistic, nontrivial pre-existing row (an already-CONSUMED
    # binding, exactly the shape a genuine legacy row would have -- see
    # column comment above _PUSH_ACTION_BINDINGS_TABLE_DDL), and proves
    # _apply_migrations preserves every one of its fields exactly, adds
    # exactly one new nullable column, backfills/guesses nothing, and is a
    # safe no-op on a second run -- the same idempotency guarantee every
    # other _MIGRATIONS entry already relies on across every server
    # restart. TEMP DB only (the tmp_db fixture).
    async def scenario():
        revision = await _seed_user_and_revision()
        anchor_id = await database.save_message(
            UID, "assistant", "prior reply", "open_chat", "ru",
            source=database.MessageSource.ASSISTANT_DELIVERED)
        legacy_row = {
            "token": "legacy-real-token-abc123",
            "user_id": UID,
            "chat_id": CHAT_ID,
            "source_message_id": 777,
            "action": "push_continue",
            "anchor_turn_id": anchor_id,
            "binding_revision": revision,
            "created_at": "2024-01-15 09:30:00",
            "expires_at": "2024-01-29 09:30:00",
            "consumed_at": "2024-01-16 08:00:00",
            "superseded_at": None,
        }
        async with database.aiosqlite.connect(database.DB) as db:
            # Rebuild the table in the EXACT pre-PUSH-RESUME-TARGET-V1
            # shape (no resume_source_event_id column at all), preserving
            # the one realistic row across the rebuild.
            await db.execute("ALTER TABLE push_action_bindings RENAME TO _old_pab")
            await db.execute(
                "CREATE TABLE push_action_bindings (\n"
                "    token TEXT PRIMARY KEY, user_id INTEGER NOT NULL,\n"
                "    chat_id INTEGER NOT NULL, source_message_id INTEGER NOT NULL,\n"
                "    action TEXT NOT NULL, anchor_turn_id INTEGER,\n"
                "    binding_revision INTEGER NOT NULL,\n"
                "    created_at TEXT NOT NULL DEFAULT (datetime('now')),\n"
                "    expires_at TEXT NOT NULL, consumed_at TEXT, superseded_at TEXT\n"
                ")")
            await db.execute("DROP TABLE _old_pab")
            await db.execute(
                "INSERT INTO push_action_bindings "
                "(token, user_id, chat_id, source_message_id, action, anchor_turn_id, "
                " binding_revision, created_at, expires_at, consumed_at, superseded_at) "
                "VALUES (:token,:user_id,:chat_id,:source_message_id,:action,:anchor_turn_id,"
                " :binding_revision,:created_at,:expires_at,:consumed_at,:superseded_at)",
                legacy_row)
            await db.commit()

            async def _snapshot(select_new_column):
                cur = await db.execute("PRAGMA table_info(push_action_bindings)")
                columns = [r[1] for r in await cur.fetchall()]
                select_cols = (
                    "token, user_id, chat_id, source_message_id, action, "
                    "anchor_turn_id, binding_revision, created_at, expires_at, "
                    "consumed_at, superseded_at"
                    + (", resume_source_event_id" if select_new_column else ""))
                cur = await db.execute(
                    f"SELECT {select_cols} FROM push_action_bindings WHERE token=?",
                    (legacy_row["token"],))
                row = await cur.fetchone()
                cur = await db.execute("SELECT COUNT(*) FROM push_action_bindings")
                (count,) = await cur.fetchone()
                return columns, row, count

            columns_before, _row_before, count_before = await _snapshot(False)
            await database._apply_migrations(db)
            await db.commit()
            columns_after_first, row_after_first, count_after_first = await _snapshot(True)
            await database._apply_migrations(db)  # must be a safe no-op
            await db.commit()
            columns_after_second, row_after_second, count_after_second = await _snapshot(True)
        return (legacy_row, columns_before, columns_after_first, columns_after_second,
                row_after_first, row_after_second, count_before, count_after_first,
                count_after_second)
    (legacy_row, columns_before, columns_after_first, columns_after_second,
     row_after_first, row_after_second, count_before, count_after_first,
     count_after_second) = run(scenario())

    assert "resume_source_event_id" not in columns_before
    assert columns_after_first.count("resume_source_event_id") == 1  # exactly one column
    assert columns_after_first == columns_after_second                # no duplicate schema effect

    assert count_before == count_after_first == count_after_second == 1  # row count unchanged

    # Every pre-existing field is preserved EXACTLY -- no truncation, no
    # normalization, no reformatting -- and the new column is NULL, never
    # backfilled or guessed. Checked after BOTH migration runs.
    for row in (row_after_first, row_after_second):
        (token, user_id, chat_id, source_message_id, action, anchor_turn_id,
         binding_revision, created_at, expires_at, consumed_at, superseded_at,
         resume_source_event_id) = row
        assert token == legacy_row["token"]
        assert user_id == legacy_row["user_id"]
        assert chat_id == legacy_row["chat_id"]
        assert source_message_id == legacy_row["source_message_id"]
        assert action == legacy_row["action"]
        assert anchor_turn_id == legacy_row["anchor_turn_id"]
        assert binding_revision == legacy_row["binding_revision"]
        assert created_at == legacy_row["created_at"]
        assert expires_at == legacy_row["expires_at"]
        assert consumed_at == legacy_row["consumed_at"]
        assert superseded_at == legacy_row["superseded_at"]
        assert resume_source_event_id is None
