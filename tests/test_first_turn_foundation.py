"""Phase 1 of the generic first-turn architecture — persistence, concurrency,
privacy, and validation foundation only. Not wired into bot.py:pipeline; no
Telegram keyboard is exposed by any of this. Uses a temporary SQLite database
only — no Telegram, no OpenAI, no network access, no production database.
"""
import asyncio
import secrets

import aiosqlite
import pytest

import database
import privacy_registry as pr
import safety_validator as sv

UID = 555001


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    return database.DB


def _new_token():
    return secrets.token_urlsafe(9)


async def _make_bound_button(uid, action="elaborate", chat_id=100, source_message_id=200,
                             scenario="reflective", lang="ru", turn_id=None):
    """Creates a real assistant `messages` row (unless turn_id is supplied)
    and binds exactly one button to it. Returns (token, turn_id)."""
    if turn_id is None:
        turn_id = await database.save_message(uid, "assistant", "первый ответ", scenario, lang)
    rev = await database.bump_user_revision(uid)
    token = _new_token()
    rows = [{"token": token, "turn_id": turn_id, "chat_id": chat_id,
             "source_message_id": source_message_id, "action": action,
             "expires_at": "2999-01-01"}]
    ok = await database.create_keyboard_batch_if_current(uid, rev, rows)
    assert ok
    return token, turn_id


# ── save_message ─────────────────────────────────────────────────────────────

def test_save_message_returns_row_id(db):
    async def go():
        mid1 = await database.save_message(UID, "user", "hello")
        mid2 = await database.save_message(UID, "assistant", "hi there")
        return mid1, mid2
    mid1, mid2 = asyncio.run(go())
    assert isinstance(mid1, int) and isinstance(mid2, int)
    assert mid2 == mid1 + 1


def test_save_message_bare_call_still_works(db):
    asyncio.run(database.save_message(UID, "user", "hi"))  # must not raise


# ── revision ──────────────────────────────────────────────────────────────────

def test_bump_user_revision_increments(db):
    async def go():
        return (await database.bump_user_revision(UID),
                await database.bump_user_revision(UID),
                await database.bump_user_revision(UID))
    assert asyncio.run(go()) == (1, 2, 3)


def test_get_user_revision_defaults_to_zero(db):
    assert asyncio.run(database.get_user_revision(999999)) == 0


def test_bump_user_revision_concurrent_calls(db):
    async def race():
        return await asyncio.gather(*[database.bump_user_revision(UID) for _ in range(10)])
    results = asyncio.run(race())
    assert sorted(results) == list(range(1, 11))
    assert asyncio.run(database.get_user_revision(UID)) == 10


# ── claim_first_turn ─────────────────────────────────────────────────────────

def test_exactly_one_concurrent_claim_winner(db):
    async def race():
        return await asyncio.gather(*[
            database.claim_first_turn(UID, "v1", _new_token(), "reflective") for _ in range(8)])
    results = asyncio.run(race())
    assert results.count(True) == 1
    assert results.count(False) == 7


def test_claim_version_isolation(db):
    async def go():
        won_v1 = await database.claim_first_turn(UID, "v1", _new_token(), "reflective")
        won_v1_again = await database.claim_first_turn(UID, "v1", _new_token(), "reflective")
        won_v2 = await database.claim_first_turn(UID, "v2", _new_token(), "reflective")
        return won_v1, won_v1_again, won_v2
    assert asyncio.run(go()) == (True, False, True)


def test_v1_legacy_exemption_for_user_with_prior_history(db):
    async def go():
        await database.save_message(UID, "assistant", "an old reply from before this feature")
        won = await database.claim_first_turn(UID, "v1", _new_token(), "reflective")
        async with aiosqlite.connect(database.DB) as conn:
            cur = await conn.execute(
                "SELECT status FROM first_turn_claims WHERE user_id=? AND contract_version='v1'", (UID,))
            row = await cur.fetchone()
        return won, row[0]
    won, status = asyncio.run(go())
    assert won is False
    assert status == "legacy_exempt"


def test_later_version_eligible_despite_old_messages(db):
    async def go():
        await database.save_message(UID, "assistant", "an old reply from before this feature")
        return await database.claim_first_turn(UID, "v2", _new_token(), "reflective")
    assert asyncio.run(go()) is True


def test_new_user_no_history_gets_pending_before_llm(db):
    async def go():
        await database.claim_first_turn(UID, "v1", _new_token(), "reflective")
        async with aiosqlite.connect(database.DB) as conn:
            cur = await conn.execute(
                "SELECT status FROM first_turn_claims WHERE user_id=? AND contract_version='v1'", (UID,))
            row = await cur.fetchone()
        return row[0]
    assert asyncio.run(go()) == "pending_before_llm"


# ── transition_first_turn_claim: graph enforcement ───────────────────────────

def test_transition_success(db):
    async def go():
        token = _new_token()
        await database.claim_first_turn(UID, "v1", token, "reflective")
        return await database.transition_first_turn_claim(
            UID, "v1", token, "pending_before_llm", "generated")
    assert asyncio.run(go()) is True


def test_transition_rejected_wrong_token(db):
    async def go():
        token = _new_token()
        await database.claim_first_turn(UID, "v1", token, "reflective")
        return await database.transition_first_turn_claim(
            UID, "v1", "wrong-token", "pending_before_llm", "generated")
    assert asyncio.run(go()) is False


def test_transition_rejected_wrong_version(db):
    async def go():
        token = _new_token()
        await database.claim_first_turn(UID, "v1", token, "reflective")
        return await database.transition_first_turn_claim(
            UID, "v2", token, "pending_before_llm", "generated")
    assert asyncio.run(go()) is False


def test_transition_rejected_wrong_expected_status(db):
    async def go():
        token = _new_token()
        await database.claim_first_turn(UID, "v1", token, "reflective")
        return await database.transition_first_turn_claim(
            UID, "v1", token, "generated", "send_started")
    assert asyncio.run(go()) is False


def test_transition_rejects_unknown_status_before_sql(db):
    async def go():
        token = _new_token()
        await database.claim_first_turn(UID, "v1", token, "reflective")
        with pytest.raises(ValueError):
            await database.transition_first_turn_claim(
                UID, "v1", token, "pending_before_llm", "not_a_real_status")
    asyncio.run(go())


def test_transition_rejects_illegal_pair_before_sql(db):
    async def go():
        token = _new_token()
        await database.claim_first_turn(UID, "v1", token, "reflective")
        with pytest.raises(ValueError):
            await database.transition_first_turn_claim(
                UID, "v1", token, "pending_before_llm", "delivered")
    asyncio.run(go())


@pytest.mark.parametrize("terminal", sorted(database.FIRST_TURN_CLAIM_TERMINAL_STATUSES))
def test_terminal_statuses_have_no_outgoing_transitions(db, terminal):
    async def go():
        token = _new_token()
        await database.claim_first_turn(UID, "v1", token, "reflective")
        with pytest.raises(ValueError):
            await database.transition_first_turn_claim(UID, "v1", token, terminal, "generated")
    asyncio.run(go())


def test_only_approved_transitions_are_in_the_graph():
    expected = {
        ("pending_before_llm", "generated"),
        ("pending_before_llm", "failed_before_send"),
        ("generated", "send_started"),
        ("generated", "failed_before_send"),
        ("send_started", "reply_delivered"),
        ("send_started", "delivered_without_buttons"),
        ("send_started", "delivered_context_missing"),
        ("send_started", "delivery_uncertain"),
        ("reply_delivered", "delivered"),
        ("reply_delivered", "delivered_without_buttons"),
    }
    assert database.FIRST_TURN_CLAIM_TRANSITIONS == expected


# ── turn_id restriction ───────────────────────────────────────────────────────

def test_turn_id_rejected_on_generated_to_send_started(db):
    async def go():
        token = _new_token()
        await database.claim_first_turn(UID, "v1", token, "reflective")
        await database.transition_first_turn_claim(UID, "v1", token, "pending_before_llm", "generated")
        with pytest.raises(ValueError):
            await database.transition_first_turn_claim(
                UID, "v1", token, "generated", "send_started", turn_id=42)
    asyncio.run(go())


def test_turn_id_required_on_send_started_to_reply_delivered(db):
    async def go():
        token = _new_token()
        await database.claim_first_turn(UID, "v1", token, "reflective")
        await database.transition_first_turn_claim(UID, "v1", token, "pending_before_llm", "generated")
        await database.transition_first_turn_claim(UID, "v1", token, "generated", "send_started")
        with pytest.raises(ValueError):
            await database.transition_first_turn_claim(
                UID, "v1", token, "send_started", "reply_delivered")   # missing turn_id
    asyncio.run(go())


def test_turn_id_assigned_at_send_started_to_reply_delivered(db):
    async def go():
        token = _new_token()
        await database.claim_first_turn(UID, "v1", token, "reflective")
        await database.transition_first_turn_claim(UID, "v1", token, "pending_before_llm", "generated")
        await database.transition_first_turn_claim(UID, "v1", token, "generated", "send_started")
        ok = await database.transition_first_turn_claim(
            UID, "v1", token, "send_started", "reply_delivered", turn_id=42)
        async with aiosqlite.connect(database.DB) as conn:
            cur = await conn.execute(
                "SELECT status, turn_id FROM first_turn_claims WHERE user_id=? AND contract_version='v1'", (UID,))
            row = await cur.fetchone()
        return ok, row
    ok, row = asyncio.run(go())
    assert ok is True
    assert row == ("reply_delivered", 42)


def test_turn_id_rejected_on_send_started_to_delivery_uncertain(db):
    async def go():
        token = _new_token()
        await database.claim_first_turn(UID, "v1", token, "reflective")
        await database.transition_first_turn_claim(UID, "v1", token, "pending_before_llm", "generated")
        await database.transition_first_turn_claim(UID, "v1", token, "generated", "send_started")
        with pytest.raises(ValueError):
            await database.transition_first_turn_claim(
                UID, "v1", token, "send_started", "delivery_uncertain", turn_id=1)
    asyncio.run(go())


# ── create_keyboard_batch_if_current ─────────────────────────────────────────

def test_keyboard_batch_created_when_revision_current(db):
    async def go():
        rev = await database.bump_user_revision(UID)
        rows = [{"token": _new_token(), "turn_id": 1, "chat_id": 100, "source_message_id": 200,
                 "action": a, "expires_at": "2999-01-01"} for a in ("elaborate", "clarify", "hard")]
        ok = await database.create_keyboard_batch_if_current(UID, rev, rows)
        async with aiosqlite.connect(database.DB) as conn:
            cur = await conn.execute(
                "SELECT COUNT(*) FROM interaction_button_bindings WHERE user_id=?", (UID,))
            count = (await cur.fetchone())[0]
        return ok, count
    ok, count = asyncio.run(go())
    assert ok is True and count == 3


def test_keyboard_batch_all_or_nothing_when_revision_stale(db):
    async def go():
        rev = await database.bump_user_revision(UID)
        await database.bump_user_revision(UID)  # revision moves on
        rows = [{"token": _new_token(), "turn_id": 1, "chat_id": 100, "source_message_id": 200,
                 "action": a, "expires_at": "2999-01-01"} for a in ("elaborate", "clarify", "hard")]
        ok = await database.create_keyboard_batch_if_current(UID, rev, rows)
        async with aiosqlite.connect(database.DB) as conn:
            cur = await conn.execute(
                "SELECT COUNT(*) FROM interaction_button_bindings WHERE user_id=?", (UID,))
            count = (await cur.fetchone())[0]
        return ok, count
    ok, count = asyncio.run(go())
    assert ok is False and count == 0


def test_keyboard_batch_rejects_unknown_action_before_insert(db):
    async def go():
        rev = await database.bump_user_revision(UID)
        rows = [{"token": _new_token(), "turn_id": 1, "chat_id": 100, "source_message_id": 200,
                 "action": "not_a_real_action", "expires_at": "2999-01-01"}]
        with pytest.raises(ValueError):
            await database.create_keyboard_batch_if_current(UID, rev, rows)
        async with aiosqlite.connect(database.DB) as conn:
            cur = await conn.execute(
                "SELECT COUNT(*) FROM interaction_button_bindings WHERE user_id=?", (UID,))
            return (await cur.fetchone())[0]
    assert asyncio.run(go()) == 0


def test_keyboard_batch_rejects_whole_batch_if_one_action_invalid(db):
    async def go():
        rev = await database.bump_user_revision(UID)
        rows = [
            {"token": _new_token(), "turn_id": 1, "chat_id": 100, "source_message_id": 200,
             "action": "elaborate", "expires_at": "2999-01-01"},
            {"token": _new_token(), "turn_id": 1, "chat_id": 100, "source_message_id": 200,
             "action": "bogus", "expires_at": "2999-01-01"},
        ]
        with pytest.raises(ValueError):
            await database.create_keyboard_batch_if_current(UID, rev, rows)
        async with aiosqlite.connect(database.DB) as conn:
            cur = await conn.execute(
                "SELECT COUNT(*) FROM interaction_button_bindings WHERE user_id=?", (UID,))
            return (await cur.fetchone())[0]
    assert asyncio.run(go()) == 0


def test_schema_check_rejects_unknown_action_insert(db):
    """Defense-in-depth: even a raw INSERT bypassing the Python-level check
    is rejected by the table's own CHECK constraint."""
    async def go():
        turn_id = await database.save_message(UID, "assistant", "reply", "reflective", "ru")
        async with aiosqlite.connect(database.DB) as conn:
            with pytest.raises(Exception):
                await conn.execute(
                    "INSERT INTO interaction_button_bindings "
                    "(token, turn_id, user_id, chat_id, source_message_id, action, "
                    " binding_revision, expires_at) VALUES (?,?,?,?,?,?,?,?)",
                    (_new_token(), turn_id, UID, 100, 200, "not_an_action", 0, "2999-01-01"))
    asyncio.run(go())


# ── consume_interaction_binding ──────────────────────────────────────────────

def test_callback_single_use(db):
    async def go():
        token, _ = await _make_bound_button(UID)
        return (await database.consume_interaction_binding(token, UID, 100, 200),
                await database.consume_interaction_binding(token, UID, 100, 200))
    first, second = asyncio.run(go())
    assert first is not None and second is None


def test_wrong_user_rejected(db):
    async def go():
        token, _ = await _make_bound_button(UID)
        return await database.consume_interaction_binding(token, UID + 1, 100, 200)
    assert asyncio.run(go()) is None


def test_wrong_message_rejected(db):
    async def go():
        token, _ = await _make_bound_button(UID)
        return await database.consume_interaction_binding(token, UID, 100, 999)
    assert asyncio.run(go()) is None


def test_expired_binding_rejected(db):
    async def go():
        turn_id = await database.save_message(UID, "assistant", "reply", "reflective", "ru")
        rev = await database.bump_user_revision(UID)
        token = _new_token()
        rows = [{"token": token, "turn_id": turn_id, "chat_id": 100, "source_message_id": 200,
                 "action": "elaborate", "expires_at": "2000-01-01"}]
        assert await database.create_keyboard_batch_if_current(UID, rev, rows)
        return await database.consume_interaction_binding(token, UID, 100, 200)
    assert asyncio.run(go()) is None


def test_concurrent_callback_race_exactly_one_winner(db):
    async def go():
        token, _ = await _make_bound_button(UID)
        return await asyncio.gather(*[
            database.consume_interaction_binding(token, UID, 100, 200) for _ in range(6)])
    results = asyncio.run(go())
    assert len([r for r in results if r is not None]) == 1


def test_action_loaded_from_db_not_from_caller(db):
    async def go():
        token, _ = await _make_bound_button(UID, action="clarify")
        return await database.consume_interaction_binding(token, UID, 100, 200)
    result = asyncio.run(go())
    assert result.action == "clarify"


def test_missing_assistant_turn_rejected(db):
    async def go():
        rev = await database.bump_user_revision(UID)
        token = _new_token()
        rows = [{"token": token, "turn_id": 999999, "chat_id": 100, "source_message_id": 200,
                 "action": "elaborate", "expires_at": "2999-01-01"}]
        assert await database.create_keyboard_batch_if_current(UID, rev, rows)
        return await database.consume_interaction_binding(token, UID, 100, 200)
    assert asyncio.run(go()) is None


def test_wrong_user_assistant_turn_rejected(db):
    other_uid = UID + 1
    async def go():
        turn_id = await database.save_message(other_uid, "assistant", "reply for someone else",
                                              "reflective", "ru")
        rev = await database.bump_user_revision(UID)
        token = _new_token()
        rows = [{"token": token, "turn_id": turn_id, "chat_id": 100, "source_message_id": 200,
                 "action": "elaborate", "expires_at": "2999-01-01"}]
        assert await database.create_keyboard_batch_if_current(UID, rev, rows)
        return await database.consume_interaction_binding(token, UID, 100, 200)
    assert asyncio.run(go()) is None


def test_user_role_turn_rejected_not_assistant(db):
    async def go():
        turn_id = await database.save_message(UID, "user", "the user's own message", "reflective", "ru")
        rev = await database.bump_user_revision(UID)
        token = _new_token()
        rows = [{"token": token, "turn_id": turn_id, "chat_id": 100, "source_message_id": 200,
                 "action": "elaborate", "expires_at": "2999-01-01"}]
        assert await database.create_keyboard_batch_if_current(UID, rev, rows)
        return await database.consume_interaction_binding(token, UID, 100, 200)
    assert asyncio.run(go()) is None


def test_normalized_user_action_persisted(db):
    async def go():
        token, _ = await _make_bound_button(UID, action="hard")
        await database.consume_interaction_binding(token, UID, 100, 200)
        async with aiosqlite.connect(database.DB) as conn:
            cur = await conn.execute(
                "SELECT content FROM messages WHERE user_id=? AND role='user' ORDER BY id DESC LIMIT 1", (UID,))
            row = await cur.fetchone()
        return row[0]
    assert asyncio.run(go()) == database.normalized_action_text("hard", "ru")


def test_scenario_and_lang_preserved_from_assistant_turn(db):
    async def go():
        token, _ = await _make_bound_button(UID, action="clarify", scenario="cbt_thought", lang="en")
        await database.consume_interaction_binding(token, UID, 100, 200)
        async with aiosqlite.connect(database.DB) as conn:
            cur = await conn.execute(
                "SELECT scenario, lang, content FROM messages WHERE user_id=? AND role='user' "
                "ORDER BY id DESC LIMIT 1", (UID,))
            row = await cur.fetchone()
        return row
    scenario, lang, content = asyncio.run(go())
    assert scenario == "cbt_thought"
    assert lang == "en"
    assert content == database.normalized_action_text("clarify", "en")


def test_event_created_pending_before_send(db):
    async def go():
        token, turn_id = await _make_bound_button(UID, action="clarify")
        result = await database.consume_interaction_binding(token, UID, 100, 200)
        async with aiosqlite.connect(database.DB) as conn:
            cur = await conn.execute(
                "SELECT reply_status, action, turn_id FROM user_interaction_events WHERE id=?",
                (result.event_id,))
            row = await cur.fetchone()
        return row, turn_id
    row, turn_id = asyncio.run(go())
    assert row == ("pending_before_send", "clarify", turn_id)


def test_consumption_result_fields(db):
    async def go():
        token, turn_id = await _make_bound_button(UID, action="hard")
        result = await database.consume_interaction_binding(token, UID, 100, 200)
        return result, turn_id
    result, turn_id = asyncio.run(go())
    assert result.action == "hard"
    assert result.turn_id == turn_id
    assert result.post_consumption_revision == 2   # 1 at binding creation, 2 at consumption
    assert isinstance(result.event_id, int)


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_consumption_result_lang_matches_source_turn(db, lang):
    async def go():
        token, _ = await _make_bound_button(UID, action="elaborate", lang=lang)
        return await database.consume_interaction_binding(token, UID, 100, 200)
    result = asyncio.run(go())
    assert result.lang == lang


# ── finalize_callback_reply / mark_event_besteffort ──────────────────────────

def test_atomic_finalization_success(db):
    async def go():
        token, _ = await _make_bound_button(UID, action="elaborate")
        result = await database.consume_interaction_binding(token, UID, 100, 200)
        fin = await database.finalize_callback_reply(
            result.event_id, UID, "Хорошо, слушаю.")
        async with aiosqlite.connect(database.DB) as conn:
            cur = await conn.execute(
                "SELECT reply_status, assistant_turn_id FROM user_interaction_events WHERE id=?",
                (result.event_id,))
            ev_row = await cur.fetchone()
            cur2 = await conn.execute("SELECT role, content FROM messages WHERE id=?", (ev_row[1],))
            msg_row = await cur2.fetchone()
        return fin, ev_row, msg_row
    fin, ev_row, msg_row = asyncio.run(go())
    assert fin.status == "delivered"
    assert fin.assistant_turn_id is not None
    assert fin.assistant_turn_id == ev_row[1]
    assert ev_row[0] == "delivered" and ev_row[1] is not None
    assert msg_row == ("assistant", "Хорошо, слушаю.")


async def _make_pending_event(user_id, turn_id, action="elaborate"):
    """Inserts a user_interaction_events row directly, bypassing
    consume_interaction_binding (which already guarantees a matching source
    turn) so finalize_callback_reply's OWN defense-in-depth checks can be
    exercised against an inconsistent event/turn pairing."""
    async with aiosqlite.connect(database.DB) as db:
        cur = await db.execute(
            "INSERT INTO user_interaction_events "
            "(user_id, turn_id, event_type, action, normalized_text, reply_status) "
            "VALUES (?, ?, 'first_turn_button', ?, 'test', 'pending_before_send')",
            (user_id, turn_id, action))
        await db.commit()
        return cur.lastrowid


def test_finalization_inherits_scenario_and_lang_from_source_turn(db):
    async def go():
        token, _ = await _make_bound_button(UID, action="elaborate",
                                            scenario="reflective", lang="ru")
        result = await database.consume_interaction_binding(token, UID, 100, 200)
        fin = await database.finalize_callback_reply(result.event_id, UID, "ок")
        async with aiosqlite.connect(database.DB) as conn:
            cur = await conn.execute(
                "SELECT scenario, lang FROM messages WHERE id=?", (fin.assistant_turn_id,))
            row = await cur.fetchone()
        return row
    assert asyncio.run(go()) == ("reflective", "ru")


def test_finalization_inherits_cbt_thought_and_en(db):
    async def go():
        token, _ = await _make_bound_button(UID, action="clarify",
                                            scenario="cbt_thought", lang="en")
        result = await database.consume_interaction_binding(token, UID, 100, 200)
        fin = await database.finalize_callback_reply(result.event_id, UID, "ok")
        async with aiosqlite.connect(database.DB) as conn:
            cur = await conn.execute(
                "SELECT scenario, lang FROM messages WHERE id=?", (fin.assistant_turn_id,))
            row = await cur.fetchone()
        return row
    assert asyncio.run(go()) == ("cbt_thought", "en")


def test_finalization_does_not_default_to_open_chat_for_other_scenarios(db):
    async def go():
        token, _ = await _make_bound_button(UID, action="hard",
                                            scenario="act_acceptance", lang="ru")
        result = await database.consume_interaction_binding(token, UID, 100, 200)
        fin = await database.finalize_callback_reply(result.event_id, UID, "ок")
        async with aiosqlite.connect(database.DB) as conn:
            cur = await conn.execute(
                "SELECT scenario FROM messages WHERE id=?", (fin.assistant_turn_id,))
            row = await cur.fetchone()
        return row[0]
    assert asyncio.run(go()) == "act_acceptance"


def test_finalize_rejects_wrong_user_source_turn(db):
    OTHER = UID + 1

    async def go():
        turn_id = await database.save_message(OTHER, "assistant", "чужой ответ", "reflective", "ru")
        event_id = await _make_pending_event(UID, turn_id)
        return await database.finalize_callback_reply(event_id, UID, "текст")
    fin = asyncio.run(go())
    assert fin.status == "delivered_context_missing"
    assert fin.assistant_turn_id is None


def test_finalize_rejects_missing_source_turn(db):
    async def go():
        event_id = await _make_pending_event(UID, 999999999)
        return await database.finalize_callback_reply(event_id, UID, "текст")
    fin = asyncio.run(go())
    assert fin.status == "delivered_context_missing"
    assert fin.assistant_turn_id is None


def test_duplicate_finalization_returns_already_resolved(db):
    async def go():
        token, _ = await _make_bound_button(UID, action="elaborate")
        result = await database.consume_interaction_binding(token, UID, 100, 200)
        first = await database.finalize_callback_reply(result.event_id, UID, "текст1")
        second = await database.finalize_callback_reply(result.event_id, UID, "текст2")
        async with aiosqlite.connect(database.DB) as conn:
            cur = await conn.execute(
                "SELECT COUNT(*) FROM messages WHERE role='assistant' AND user_id=?", (UID,))
            assistant_count = (await cur.fetchone())[0]
            cur2 = await conn.execute(
                "SELECT reply_status FROM user_interaction_events WHERE id=?", (result.event_id,))
            final_status = (await cur2.fetchone())[0]
        return first, second, assistant_count, final_status
    first, second, assistant_count, final_status = asyncio.run(go())
    assert first.status == "delivered"
    assert first.assistant_turn_id is not None
    assert second.status == "already_resolved"
    assert second.assistant_turn_id is None
    # exactly 2 assistant rows: the one _make_bound_button created as the
    # originating turn, plus the ONE successful finalize -- the rejected
    # second attempt inserts nothing (its pre-check short-circuits before
    # ever reaching the INSERT).
    assert assistant_count == 2
    assert final_status == "delivered"  # untouched by the second attempt


def test_besteffort_rowcount_zero_reports_already_resolved_not_success(db):
    async def go():
        token, _ = await _make_bound_button(UID, action="clarify")
        result = await database.consume_interaction_binding(token, UID, 100, 200)
        await database.finalize_callback_reply(result.event_id, UID, "ok")
        # event is now 'delivered' -- no longer pending_before_send
        return await database.mark_event_besteffort(
            result.event_id, "delivery_uncertain", database.SEND_EXCEPTION)
    assert asyncio.run(go()) == "already_resolved"


def test_besteffort_success_path(db):
    async def go():
        token, _ = await _make_bound_button(UID, action="clarify")
        result = await database.consume_interaction_binding(token, UID, 100, 200)
        status = await database.mark_event_besteffort(
            result.event_id, "delivery_uncertain", database.SEND_EXCEPTION)
        async with aiosqlite.connect(database.DB) as conn:
            cur = await conn.execute(
                "SELECT reply_status, reply_error_code FROM user_interaction_events WHERE id=?",
                (result.event_id,))
            row = await cur.fetchone()
        return status, row
    status, row = asyncio.run(go())
    assert status == "delivery_uncertain"
    assert row == ("delivery_uncertain", database.SEND_EXCEPTION)


def test_besteffort_rejects_unknown_target_status(db):
    async def go():
        token, _ = await _make_bound_button(UID, action="clarify")
        result = await database.consume_interaction_binding(token, UID, 100, 200)
        with pytest.raises(ValueError):
            await database.mark_event_besteffort(result.event_id, "delivered")
    asyncio.run(go())


def test_besteffort_rejects_unknown_error_code(db):
    async def go():
        token, _ = await _make_bound_button(UID, action="clarify")
        result = await database.consume_interaction_binding(token, UID, 100, 200)
        with pytest.raises(ValueError):
            await database.mark_event_besteffort(
                result.event_id, "delivery_uncertain", "RAW_EXCEPTION_TEXT_LEAK")
    asyncio.run(go())


def test_schema_check_rejects_unknown_error_code_insert(db):
    async def go():
        token, _ = await _make_bound_button(UID, action="clarify")
        result = await database.consume_interaction_binding(token, UID, 100, 200)
        async with aiosqlite.connect(database.DB) as conn:
            with pytest.raises(Exception):
                await conn.execute(
                    "UPDATE user_interaction_events SET reply_error_code=? WHERE id=?",
                    ("some raw traceback text", result.event_id))
    asyncio.run(go())


# ── validate_first_turn_response ─────────────────────────────────────────────

def test_validator_rejects_zero_question_marks():
    ok, reason = sv.validate_first_turn_response(
        "Понимаю тебя. Просто побудь с этим.", "тест", "ru")
    assert ok is False
    assert reason == "first-turn response must contain exactly one question"


def test_validator_accepts_exactly_one_question_mark():
    ok, _ = sv.validate_first_turn_response("Понимаю тебя. Расскажи, что происходит?", "тест", "ru")
    assert ok is True


def test_validator_rejects_two_question_marks():
    ok, reason = sv.validate_first_turn_response(
        "Что случилось? И как ты себя чувствуешь?", "исходное сообщение", "ru")
    assert ok is False
    assert reason == "first-turn response must contain exactly one question"


def test_validator_rejects_forbidden_generic_advice():
    ok, reason = sv.validate_first_turn_response("Мне жаль это слышать. Что происходит?", "тест", "ru")
    assert ok is False and "forbidden generic advice" in reason


def test_validator_rejects_modality_announcement():
    ok, reason = sv.validate_first_turn_response("Мы используем технику КБТ. Что чувствуешь?", "тест", "ru")
    assert ok is False and "modality" in reason


def test_validator_rejects_five_word_literal_overlap():
    user_text = "я вышел из отношений и чувствую себя раздавленным полностью"
    candidate = "Понимаю. Ты вышел из отношений и чувствую себя раздавленным полностью прямо сейчас?"
    ok, reason = sv.validate_first_turn_response(candidate, user_text, "ru")
    assert ok is False and "overlap" in reason


def test_validator_allows_short_non_overlapping_response():
    user_text = "я вышел из отношений и чувствую себя раздавленным полностью"
    candidate = "Сейчас важно понять, что именно тяжелее: сама разлука или то, что было после?"
    ok, _ = sv.validate_first_turn_response(candidate, user_text, "ru")
    assert ok is True


def test_validator_rejects_too_long_response():
    # exactly one question mark, so the length check (not the question-count
    # check) is what fires here
    candidate = " ".join(["слово"] * 121) + "?"
    ok, reason = sv.validate_first_turn_response(candidate, "тест", "ru")
    assert ok is False and "too long" in reason


# ── fallback text ─────────────────────────────────────────────────────────────

def test_fallback_text_exact_ru():
    assert sv.get_first_turn_fallback("ru") == (
        "Не нужно объяснять всё сразу. Что сейчас удерживает сильнее: само событие, "
        "мысли о нём или чувство, которое остаётся после?"
    )


def test_fallback_text_exact_en():
    assert sv.get_first_turn_fallback("en") == (
        "You don't need to explain everything at once. What is holding you most right "
        "now: the event itself, the thoughts about it, or the feeling left afterward?"
    )


def test_fallback_has_exactly_one_question_mark():
    assert sv.get_first_turn_fallback("ru").count("?") == 1
    assert sv.get_first_turn_fallback("en").count("?") == 1


def test_fallback_passes_its_own_validator_ru():
    ok, _ = sv.validate_first_turn_response(sv.get_first_turn_fallback("ru"), "любой текст пользователя", "ru")
    assert ok is True


def test_fallback_passes_its_own_validator_en():
    ok, _ = sv.validate_first_turn_response(sv.get_first_turn_fallback("en"), "any user text", "en")
    assert ok is True


# ── normalized_action_text ────────────────────────────────────────────────────

def test_normalized_action_text_raises_for_unknown_action():
    with pytest.raises(ValueError):
        database.normalized_action_text("not_a_real_action")


def test_normalized_action_text_localized_ru_and_en():
    ru = database.normalized_action_text("hard", "ru")
    en = database.normalized_action_text("hard", "en")
    assert ru != en
    assert "Пользователь" in ru
    assert "User" in en


# ── privacy registry / export / delete ───────────────────────────────────────

NEW_TABLES = ("user_interaction_revision", "first_turn_claims",
             "interaction_button_bindings", "user_interaction_events")


def test_new_tables_registered_in_privacy_registry():
    for table in NEW_TABLES:
        assert table in pr.PRIVACY_REGISTRY, f"{table} not registered"


def test_no_unregistered_sensitive_tables_in_schema():
    assert pr.find_unregistered_sensitive_tables(database.SCHEMA) == []


def test_new_tables_export_and_delete_policy():
    for table in NEW_TABLES:
        entry = pr.PRIVACY_REGISTRY[table]
        assert entry.export_policy == "INCLUDE"
        assert entry.delete_policy == "CASCADE_DELETE"


def test_export_includes_new_rows(db):
    async def go():
        await database.claim_first_turn(UID, "v1", _new_token(), "reflective")
        return await database.export_all_personal_data(UID)
    result = asyncio.run(go())
    assert "first_turn_claims" in result
    assert len(result["first_turn_claims"]) == 1


def test_delete_all_removes_new_rows(db):
    async def go():
        token, _ = await _make_bound_button(UID, action="clarify")
        await database.claim_first_turn(UID, "v1", _new_token(), "reflective")
        await database.consume_interaction_binding(token, UID, 100, 200)
        await database.delete_all_personal_data(UID)
        async with aiosqlite.connect(database.DB) as conn:
            counts = {}
            for t in NEW_TABLES:
                cur = await conn.execute(f"SELECT COUNT(*) FROM {t} WHERE user_id=?", (UID,))
                counts[t] = (await cur.fetchone())[0]
        return counts
    counts = asyncio.run(go())
    assert all(c == 0 for c in counts.values()), counts
