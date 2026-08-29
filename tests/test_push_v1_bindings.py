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
        anchor_id = await database.save_message(
            UID, "assistant", "prior reply", "open_chat", "ru",
            source=database.MessageSource.ASSISTANT_DELIVERED)
        tokens = _tokens()
        ok = await database.create_push_action_bindings(
            UID, CHAT_ID, 1, revision, anchor_id, _rows(tokens, _future_expiry()))
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
            UID, CHAT_ID, 1, captured_revision, anchor_id, _rows(tokens, _future_expiry()))
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
