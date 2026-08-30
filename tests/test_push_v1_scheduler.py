"""Push V1 (Round 5, + Owner Correction #1) — scheduler-side regression
tests for scheduler._send_silence_pushes against a REAL temp SQLite DB
(tmp_db, same pattern as tests/test_questionnaire_command_flow.py),
covering:

- P0: unresolved-crisis veto (entire lifecycle, not just 24h), both the
  EARLY (pre-decide_push) check and the FINAL pre-send recheck;
- P1 §5/§6: fresh access/onboarding rechecks right before send;
- P1 §7: the T0-candidate/T2-reengagement stale-push race;
- deterministic RU/EN Push V1 copy, exactly 2 buttons;
- publication order (bindings exist before the keyboard is attached;
  binding failure leaves plain text, no dead buttons);
- the send/record-failure boundary, including the Correction-#1 bounded
  process-local duplicate-resend suppression;
- Correction-#1 Blocker 5: the push is a conversation RE-ENGAGEMENT
  feature and must never be sent without a real prior conversation anchor,
  and a previous Push V1 UI reply must never itself count as one.

decide_push() itself (the pure antispam function) is already exhaustively
covered in tests/test_silence_engine.py -- this file exercises the
INTEGRATION around it.

Every test that expects a push to be sent (or to be blocked by a SPECIFIC
gate other than the anchor requirement) seeds a real conversation anchor
via _seed_inactive_user_with_anchor -- otherwise the Blocker-5 anchor veto
would trivially explain a blocked send in EVERY test, hiding a real
regression in whichever OTHER gate is actually under test.
"""
import asyncio

import pytest

import access_control as ac
import config
import database
import onboarding_content
import prompts
import scheduler

run = asyncio.run


class FakeUser:
    def __init__(self, uid):
        self.id = uid


class FakeChat:
    def __init__(self, uid):
        self.id = uid


class FakeSentMessage:
    def __init__(self, chat_id, message_id):
        self.chat = FakeChat(chat_id)
        self.message_id = message_id


class FakeBot:
    """Records every send/edit call instead of hitting Telegram."""

    def __init__(self, send_message_id_start=1000):
        self.sent = []            # list of (uid, text)
        self.edited_markup = []   # list of (chat_id, message_id, reply_markup)
        self._next_message_id = send_message_id_start
        self.fail_send_for = set()

    async def send_message(self, uid, text, **kw):
        if uid in self.fail_send_for:
            raise RuntimeError("simulated Telegram send failure")
        self.sent.append((uid, text))
        mid = self._next_message_id
        self._next_message_id += 1
        return FakeSentMessage(uid, mid)

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        self.edited_markup.append((chat_id, message_id, reply_markup))


def _async(value=None):
    async def _f(*a, **kw):
        return value
    return _f


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "push_v1.db"))
    run(database.init_db())
    return database


@pytest.fixture(autouse=True)
def _common(monkeypatch, tmp_db):
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})
    monkeypatch.setattr(config, "FIRST_USER_ONBOARDING_ENABLED", False)
    # A fresh module-level set per test -- the record_push-failure
    # suppression guard is process-local and must not leak state across
    # tests.
    monkeypatch.setattr(scheduler, "_unrecorded_send_uids", set())
    return tmp_db


async def _seed_inactive_user(uid, days_inactive=2, lang="ru"):
    await database.upsert_user(uid, "u", "U", lang)
    async with database.aiosqlite.connect(database.DB) as db:
        await db.execute(
            "UPDATE users SET last_seen=datetime('now', ?) WHERE id=?",
            (f"-{days_inactive} days", uid))
        await db.commit()


async def _seed_conversation_anchor(uid, scenario="open_chat"):
    """A genuine prior conversational assistant turn -- the Push V1 "real
    conversation anchor" requirement (Correction #1, Blocker 5)."""
    return await database.save_message(
        uid, "assistant", "prior reply", scenario, "ru",
        source=database.MessageSource.ASSISTANT_DELIVERED)


async def _seed_inactive_user_with_anchor(uid, days_inactive=2, lang="ru"):
    await _seed_inactive_user(uid, days_inactive, lang)
    await _seed_conversation_anchor(uid)


def test_ru_push_copy_is_exact_and_deterministic():
    assert prompts.get_push_v1_text("ru") == (
        "Как ты после нашего разговора?\n"
        "Если захочешь, можем продолжить с того места или переключиться на что-то другое."
    )


def test_en_push_copy_is_natural_equivalent():
    text = prompts.get_push_v1_text("en")
    assert "since we talked" in text
    assert "pick up where we left off" in text
    assert "switch to something else" in text


def test_push_v1_text_has_no_randomness():
    # Deterministic per product contract -- unlike the old tier-random copy.
    assert prompts.get_push_v1_text("ru") == prompts.get_push_v1_text("ru")


def test_owner_1_is_a_valid_push_candidate_and_gets_the_push():
    async def scenario():
        await _seed_inactive_user_with_anchor(1, days_inactive=2)
        bot = FakeBot()
        await scheduler._send_silence_pushes(bot)
        return bot
    bot = run(scenario())
    assert len(bot.sent) == 1
    uid, text = bot.sent[0]
    assert uid == 1
    assert text == prompts.PUSH_V1_TEXT_RU


def test_exactly_two_buttons_continue_and_new_topic():
    # UI polish V1: one row, two buttons -- Continue first, New topic
    # second. Callback data/token semantics are unchanged from the prior
    # two-row layout. Direct unit test of _push_v1_keyboard itself (not
    # the full _send_silence_pushes integration path) -- this is a pure
    # presentation-layer property and should not depend on decide_push's
    # real-wall-clock quiet-hours/cadence decision, which the full
    # integration path is otherwise subject to.
    kb = scheduler._push_v1_keyboard("ru", {"push_continue": "tok-a", "push_new_topic": "tok-b"})
    rows = kb.inline_keyboard
    assert len(rows) == 1
    assert len(rows[0]) == 2
    continue_btn, new_topic_btn = rows[0]
    assert continue_btn.text == prompts.PUSH_V1_CONTINUE_LABEL_RU
    assert new_topic_btn.text == prompts.PUSH_V1_NEW_TOPIC_LABEL_RU
    assert continue_btn.callback_data == "pushbtn:tok-a"
    assert new_topic_btn.callback_data == "pushbtn:tok-b"
    assert continue_btn.callback_data != new_topic_btn.callback_data


def test_continue_button_has_primary_style_new_topic_does_not():
    # aiogram 3.7.0 passes an untyped "style" field through construction
    # and serialization (Pydantic extra="allow") -- see the compatibility
    # probe for the UI-polish task. Continue gets style="primary"; New
    # topic intentionally carries no style field (default Telegram look).
    # Direct unit test of _push_v1_keyboard -- see note above.
    kb = scheduler._push_v1_keyboard("ru", {"push_continue": "tok-a", "push_new_topic": "tok-b"})
    continue_btn, new_topic_btn = kb.inline_keyboard[0]
    assert continue_btn.style == "primary"
    assert "style" not in new_topic_btn.model_dump(exclude_none=True)


def test_bindings_exist_before_the_keyboard_is_attached():
    async def scenario():
        await _seed_inactive_user_with_anchor(1, days_inactive=2)
        bot = FakeBot()
        await scheduler._send_silence_pushes(bot)
        return bot
    bot = run(scenario())
    chat_id, message_id, kb = bot.edited_markup[0]
    token = kb.inline_keyboard[0][0].callback_data[len("pushbtn:"):]

    async def check():
        result = await database.consume_push_action_binding(token, 1, chat_id, message_id)
        return result
    result = run(check())
    # The binding was already durably written BEFORE edit_message_reply_markup
    # was called above -- it consumes successfully here.
    assert result is not None
    assert result.action == "push_continue"


def test_binding_failure_leaves_plain_text_no_dead_buttons(monkeypatch):
    async def _fail_create(*a, **kw):
        return False
    monkeypatch.setattr(scheduler, "create_push_action_bindings", _fail_create)

    async def scenario():
        await _seed_inactive_user_with_anchor(1, days_inactive=2)
        bot = FakeBot()
        await scheduler._send_silence_pushes(bot)
        return bot
    bot = run(scenario())
    assert len(bot.sent) == 1          # plain text still delivered
    assert bot.edited_markup == []     # no keyboard ever attached


# ── POST-CODEX REREVIEW CORRECTION, P1-2: delete-all committing in the gap
# between durable binding creation and keyboard publication must block the
# publish -- final_push_keyboard_publish_guard is the last awaited DB read
# before bot.edit_message_reply_markup, mirroring final_push_send_guard's
# discipline before bot.send_message. ──────────────────────────────────────
def test_delete_all_between_binding_creation_and_publication_blocks_keyboard(monkeypatch):
    real_create = database.create_push_action_bindings

    async def _create_then_delete(user_id, chat_id, source_message_id,
                                   response_revision, anchor_turn_id, bindings):
        ok = await real_create(user_id, chat_id, source_message_id,
                                response_revision, anchor_turn_id, bindings)
        if ok:
            # Real delete-all commits in the gap AFTER bindings are durably
            # written but BEFORE the scheduler reaches the publication
            # guard -- the exact race this correction closes.
            await database.delete_all_personal_data(user_id)
        return ok
    monkeypatch.setattr(scheduler, "create_push_action_bindings", _create_then_delete)

    async def scenario():
        await _seed_inactive_user_with_anchor(1, days_inactive=2)
        bot = FakeBot()
        await scheduler._send_silence_pushes(bot)
        return bot, await _count("push_action_bindings", 1), await _count("messages", 1)
    bot, bindings_n, messages_n = run(scenario())
    assert len(bot.sent) == 1          # plain text was already delivered before the race
    assert bot.edited_markup == []     # keyboard never published -- guard blocked it
    assert bindings_n == 0             # bindings remain deleted, not resurrected
    assert messages_n == 0             # messages remain deleted


# ── NARROW ONE-BUG CORRECTION: final_push_keyboard_publish_guard must check
# the LIVE user_interaction_revision, not merely re-read the binding rows'
# own already-committed binding_revision column. A genuine ordinary user
# turn (or any other revision-bumping lifecycle event) can commit in the
# same gap as the delete-all case above, WITHOUT deleting or closing the
# binding rows at all -- they stay open, unconsumed, unsuperseded, and
# still carry their original binding_revision, yet consume_push_action_
# binding is already guaranteed to reject them at tap time. Publishing a
# keyboard in that state would be visibly actionable but dead on arrival.
def test_live_revision_bump_between_binding_creation_and_publication_blocks_keyboard(monkeypatch):
    real_create = database.create_push_action_bindings

    async def _create_then_bump(user_id, chat_id, source_message_id,
                                 response_revision, anchor_turn_id, bindings):
        ok = await real_create(user_id, chat_id, source_message_id,
                                response_revision, anchor_turn_id, bindings)
        if ok:
            # A genuine ordinary user turn commits in the gap AFTER
            # bindings are durably written but BEFORE the publication
            # guard runs -- the bindings are untouched by this (still open,
            # still carrying their original binding_revision), only the
            # LIVE user_interaction_revision moves past them.
            await database.bump_user_revision(user_id)
        return ok
    monkeypatch.setattr(scheduler, "create_push_action_bindings", _create_then_bump)

    async def scenario():
        await _seed_inactive_user_with_anchor(1, days_inactive=2)
        bot = FakeBot()
        await scheduler._send_silence_pushes(bot)
        return bot, await _count("push_action_bindings", 1)
    bot, bindings_n = run(scenario())
    assert len(bot.sent) == 1          # plain text was already delivered before the race
    assert bot.edited_markup == []     # keyboard never published -- guard blocked it
    assert bindings_n == 2             # bindings still physically exist, still open --
                                        # proves the guard checks LIVE revision, not just
                                        # the rows' own stored binding_revision


# ── P0: unresolved crisis vetoes the ENTIRE lifecycle (EARLY check) ────────
def test_unresolved_crisis_older_than_24h_still_blocks_push():
    async def scenario():
        await _seed_inactive_user_with_anchor(1, days_inactive=2)
        await database.log_crisis_event(1, "critical", 10, ["suicide"], "x", "ru")
        # Push the crisis event's created_at back >24h so the OLD 24h-only
        # rule would have allowed it -- resolved is still 0.
        async with database.aiosqlite.connect(database.DB) as db:
            await db.execute(
                "UPDATE crisis_events SET created_at=datetime('now','-30 hours') "
                "WHERE user_id=?", (1,))
            await db.commit()
        bot = FakeBot()
        await scheduler._send_silence_pushes(bot)
        return bot
    bot = run(scenario())
    assert bot.sent == []


def test_resolved_crisis_under_24h_still_gets_existing_cooldown():
    async def scenario():
        await _seed_inactive_user_with_anchor(1, days_inactive=2)
        eid = await database.log_crisis_event(1, "critical", 10, ["suicide"], "x", "ru")
        await database.resolve_crisis(eid)
        bot = FakeBot()
        await scheduler._send_silence_pushes(bot)
        return bot
    bot = run(scenario())
    assert bot.sent == []  # resolved, but <24h ago -- existing cooldown applies


def test_resolved_crisis_over_24h_ago_sends_normally():
    async def scenario():
        await _seed_inactive_user_with_anchor(1, days_inactive=2)
        eid = await database.log_crisis_event(1, "critical", 10, ["suicide"], "x", "ru")
        await database.resolve_crisis(eid)
        async with database.aiosqlite.connect(database.DB) as db:
            await db.execute(
                "UPDATE crisis_events SET created_at=datetime('now','-30 hours') "
                "WHERE user_id=?", (1,))
            await db.commit()
        bot = FakeBot()
        await scheduler._send_silence_pushes(bot)
        return bot
    bot = run(scenario())
    assert len(bot.sent) == 1


# ── Correction #2, Blocker A: the final guard must be the LAST awaited
# call before send -- races during the LATER prerequisite awaits (anchor,
# revision), not just an early freshness snapshot, must still be caught. ──
def test_A_activity_during_anchor_lookup_blocks_final_send(monkeypatch):
    # T0: candidate gathered (last_seen snapshot captured).
    # T1: access/onboarding pass.
    # T2: DURING the anchor prerequisite lookup, the user sends real
    #     traffic and last_seen advances (side effect of the SAME real
    #     get_last_assistant_message_id() the anchor step itself calls).
    # T3: the FINAL guard (the very last awaited call before send) must
    #     see the new last_seen and refuse.
    # T4: the Telegram send must NOT happen.
    real_get_anchor = database.get_last_assistant_message_id

    async def _side_effecting_anchor_lookup(uid):
        result = await real_get_anchor(uid)
        async with database.aiosqlite.connect(database.DB) as db:
            await db.execute(
                "UPDATE users SET last_seen=datetime('now') WHERE id=?", (uid,))
            await db.commit()
        return result
    monkeypatch.setattr(scheduler, "get_last_assistant_message_id",
                        _side_effecting_anchor_lookup)

    async def scenario():
        await _seed_inactive_user_with_anchor(1, days_inactive=2)
        bot = FakeBot()
        await scheduler._send_silence_pushes(bot)
        return bot
    bot = run(scenario())
    assert bot.sent == []


def test_A_activity_during_revision_lookup_blocks_final_send(monkeypatch):
    # Same race, one prerequisite step later: the side effect happens
    # during the revision fetch instead of the anchor fetch.
    real_get_revision = database.get_user_revision

    async def _side_effecting_revision_lookup(uid):
        result = await real_get_revision(uid)
        async with database.aiosqlite.connect(database.DB) as db:
            await db.execute(
                "UPDATE users SET last_seen=datetime('now') WHERE id=?", (uid,))
            await db.commit()
        return result
    monkeypatch.setattr(scheduler, "get_user_revision", _side_effecting_revision_lookup)

    async def scenario():
        await _seed_inactive_user_with_anchor(1, days_inactive=2)
        bot = FakeBot()
        await scheduler._send_silence_pushes(bot)
        return bot
    bot = run(scenario())
    assert bot.sent == []


def test_B_crisis_during_anchor_lookup_blocks_final_send(monkeypatch):
    # Same shape as test A, but the side effect starts an UNRESOLVED
    # crisis instead of touching last_seen -- the final guard's crisis
    # check must catch a crisis that begins during a LATER prerequisite
    # await, not just before the early efficiency check.
    real_get_anchor = database.get_last_assistant_message_id

    async def _side_effecting_anchor_lookup(uid):
        result = await real_get_anchor(uid)
        await database.log_crisis_event(uid, "critical", 10, ["suicide"], "x", "ru")
        return result
    monkeypatch.setattr(scheduler, "get_last_assistant_message_id",
                        _side_effecting_anchor_lookup)

    async def scenario():
        await _seed_inactive_user_with_anchor(1, days_inactive=2)
        bot = FakeBot()
        await scheduler._send_silence_pushes(bot)
        return bot
    bot = run(scenario())
    assert bot.sent == []


def test_B_crisis_during_revision_lookup_blocks_final_send(monkeypatch):
    real_get_revision = database.get_user_revision

    async def _side_effecting_revision_lookup(uid):
        result = await real_get_revision(uid)
        await database.log_crisis_event(uid, "critical", 10, ["suicide"], "x", "ru")
        return result
    monkeypatch.setattr(scheduler, "get_user_revision", _side_effecting_revision_lookup)

    async def scenario():
        await _seed_inactive_user_with_anchor(1, days_inactive=2)
        bot = FakeBot()
        await scheduler._send_silence_pushes(bot)
        return bot
    bot = run(scenario())
    assert bot.sent == []


def test_C_final_guard_db_failure_blocks_send(monkeypatch):
    async def _raise(uid, expected_last_seen):
        raise RuntimeError("simulated final-guard DB failure")
    monkeypatch.setattr(scheduler, "final_push_send_guard", _raise)

    async def scenario():
        await _seed_inactive_user_with_anchor(1, days_inactive=2)
        bot = FakeBot()
        await scheduler._send_silence_pushes(bot)  # must not raise
        return bot
    bot = run(scenario())
    assert bot.sent == []  # fail CLOSED


def test_D_normal_unchanged_candidate_still_sends():
    # Control case: without any race, both facts hold at guard time and
    # the push still sends -- proves the final guard isn't over-blocking.
    async def scenario():
        await _seed_inactive_user_with_anchor(1, days_inactive=2)
        bot = FakeBot()
        await scheduler._send_silence_pushes(bot)
        return bot
    bot = run(scenario())
    assert len(bot.sent) == 1


# ── Final Atomic-Guard Correction: final_push_send_guard's INTERNAL
# contract -- one atomic eligibility SELECT, not two separately awaited
# queries (even on the same connection, two statements are not one atomic
# observation: a write from another connection can land between them).
# This is distinct from the Correction-#2 A/B/C/D tests above, which only
# prove the guard is called LAST and reacts to state that changed BEFORE
# it runs -- those pass equally against the old two-SELECT design, since
# both of its SELECTs still run before send. Only inspecting what SQL the
# guard itself issues can distinguish the two designs. ─────────────────────
def test_final_guard_issues_exactly_one_atomic_eligibility_select(monkeypatch):
    import aiosqlite
    real_execute = aiosqlite.Connection.execute
    calls = []

    async def _spy_execute(self, sql, parameters=None):
        calls.append(sql)
        if parameters is None:
            return await real_execute(self, sql)
        return await real_execute(self, sql, parameters)
    monkeypatch.setattr(aiosqlite.Connection, "execute", _spy_execute)

    async def scenario():
        await database.upsert_user(1, "u", "U", "ru")
        anchor_id = await _seed_conversation_anchor(1)
        last_seen = await database.get_last_seen(1)
        revision = await database.get_user_revision(1)  # 0 -- no ordinary turn yet
        calls.clear()  # only count what final_push_send_guard itself issues
        return await database.final_push_send_guard(1, last_seen, revision, anchor_id)
    result = run(scenario())

    assert result is True
    # Exactly one statement that carries ALL FOUR eligibility facts together
    # -- never several separately awaited SELECTs (the old, non-atomic
    # multi-statement design): a write from another connection could land
    # between them.
    assert len(calls) == 1
    sql = calls[0].lower()
    assert "users" in sql
    assert "last_seen" in sql
    assert "not exists" in sql
    assert "crisis_events" in sql
    assert "resolved" in sql
    assert "user_interaction_revision" in sql
    assert "messages" in sql


# ── P1 §6: fresh access/onboarding rechecks right before send ──────────────
def test_access_revoked_after_candidate_gathered_blocks_send(monkeypatch):
    async def _no_access(uid):
        return False
    monkeypatch.setattr(scheduler.access_control, "proactive_push_eligible", _no_access)

    async def scenario():
        await _seed_inactive_user_with_anchor(1, days_inactive=2)
        bot = FakeBot()
        await scheduler._send_silence_pushes(bot)
        return bot
    bot = run(scenario())
    assert bot.sent == []


def test_active_mandatory_onboarding_blocks_send(monkeypatch):
    async def _blocks(uid):
        return True
    monkeypatch.setattr(scheduler, "_onboarding_blocks_push", _blocks)

    async def scenario():
        await _seed_inactive_user_with_anchor(1, days_inactive=2)
        bot = FakeBot()
        await scheduler._send_silence_pushes(bot)
        return bot
    bot = run(scenario())
    assert bot.sent == []


def test_onboarding_disabled_globally_never_blocks():
    # config.FIRST_USER_ONBOARDING_ENABLED=False (fixture default) -- the
    # real _onboarding_blocks_push must short-circuit to False, not raise.
    async def scenario():
        await _seed_inactive_user_with_anchor(1, days_inactive=2)
        bot = FakeBot()
        await scheduler._send_silence_pushes(bot)
        return bot
    bot = run(scenario())
    assert len(bot.sent) == 1


# ── §10 / Correction #2 Blocker B: send/record-finalization failure ────────
def test_telegram_send_failure_never_calls_record_push(monkeypatch):
    calls = []

    async def _spy_record_push(uid, tier, anchor_turn_id, expected_last_seen):
        calls.append((uid, tier, anchor_turn_id, expected_last_seen))
    monkeypatch.setattr(scheduler, "record_push_v1_delivery", _spy_record_push)

    async def scenario():
        await _seed_inactive_user_with_anchor(1, days_inactive=2)
        bot = FakeBot()
        bot.fail_send_for = {1}
        await scheduler._send_silence_pushes(bot)
        return bot
    run(scenario())
    assert calls == []  # never recorded a push that Telegram never accepted


def test_record_push_failure_is_bounded_and_does_not_crash(monkeypatch):
    attempts = []

    async def _always_fail(uid, tier, anchor_turn_id, expected_last_seen):
        attempts.append((uid, tier, anchor_turn_id, expected_last_seen))
        raise RuntimeError("simulated DB failure")
    monkeypatch.setattr(scheduler, "record_push_v1_delivery", _always_fail)

    async def scenario():
        await _seed_inactive_user_with_anchor(1, days_inactive=2)
        bot = FakeBot()
        await scheduler._send_silence_pushes(bot)  # must not raise
        return bot
    bot = run(scenario())
    assert len(bot.sent) == 1  # the send itself still succeeded
    assert len(attempts) == scheduler._RECORD_PUSH_MAX_ATTEMPTS  # bounded, not infinite
    # Binding creation/keyboard attach still proceed even though the
    # bookkeeping write failed -- a lost push_log row must not also cost
    # the user their Continue/New-topic buttons.
    assert len(bot.edited_markup) == 1


def test_persistent_record_push_failure_does_not_resend_on_next_tick(monkeypatch):
    # Telegram send succeeds, record_push_v1_delivery ALWAYS fails (so
    # push_log never reflects the send), and _send_silence_pushes runs
    # TWICE -- total Telegram sends must be 1, not 2.
    async def _always_fail(uid, tier, anchor_turn_id, expected_last_seen):
        raise RuntimeError("simulated persistent DB failure")
    monkeypatch.setattr(scheduler, "record_push_v1_delivery", _always_fail)

    async def scenario():
        await _seed_inactive_user_with_anchor(1, days_inactive=2)
        bot = FakeBot()
        await scheduler._send_silence_pushes(bot)   # tick 1: sends, record fails
        await scheduler._send_silence_pushes(bot)   # tick 2: must be suppressed
        return bot
    bot = run(scenario())
    assert len(bot.sent) == 1


def test_B_persistent_record_push_failure_suppresses_for_remainder_of_process(monkeypatch):
    # Correction #2, Blocker B: a bounded-TTL guard was rejected because,
    # once the TTL expired, the SAME unrecorded delivered push would make
    # the user eligible for ANOTHER send -- repeating indefinitely for as
    # long as record_push keeps failing. The fix must be PERMANENT (for
    # this process's lifetime), not time-bounded.
    #
    # 10 back-to-back ticks alone would not distinguish this from the OLD
    # 45-minute-TTL design (not enough real wall-clock time elapses in a
    # fast test loop for that TTL to expire either way) -- so between each
    # tick we actively age any TTL-keyed timestamp state that might still
    # be present, well past the old 45-minute window. This is a complete
    # no-op against the correct, PERMANENT set-based implementation (a set
    # has no timestamps to age); against a regressed TTL-based
    # implementation it forces the old expiry-then-resend behavior to fire
    # on every tick, which is exactly the bug this test must catch.
    import datetime as dt
    async def _always_fail(uid, tier, anchor_turn_id, expected_last_seen):
        raise RuntimeError("simulated persistent DB failure")
    monkeypatch.setattr(scheduler, "record_push_v1_delivery", _always_fail)

    def _age_any_ttl_state_past_the_old_window():
        state = scheduler._unrecorded_send_uids
        if isinstance(state, dict):
            for uid in list(state.keys()):
                state[uid] -= dt.timedelta(hours=1)

    async def scenario():
        await _seed_inactive_user_with_anchor(1, days_inactive=2)
        bot = FakeBot()
        for _ in range(10):
            _age_any_ttl_state_past_the_old_window()
            await scheduler._send_silence_pushes(bot)
        return bot
    bot = run(scenario())
    assert len(bot.sent) == 1  # exactly one delivery across all 10 ticks


def test_TTL_can_reenable_send_after_unrecorded_delivery_is_NO():
    # Explicit negative pin on the rejected design: there is no TTL-based
    # re-enable mechanism left in the codebase at all -- the constant and
    # the timestamp-keyed dict it used to gate no longer exist.
    assert not hasattr(scheduler, "_RECENT_UNRECORDED_SEND_TTL")
    assert isinstance(scheduler._unrecorded_send_uids, set)


def test_successful_record_push_clears_any_stale_suppression_entry():
    # Direct call to _finalize_push_record (not the full scheduler flow --
    # going through _send_silence_pushes would have the guard entry itself
    # SUPPRESS the send before record_push_v1_delivery is ever reached,
    # which would prove nothing about its own success path). A real anchor
    # and last_seen must be seeded, since the persistence path is now
    # anchor-fenced.
    async def scenario():
        await _seed_inactive_user_with_anchor(1, days_inactive=2)
        anchor_id = await database.get_last_assistant_message_id(1)
        last_seen = await database.get_last_seen(1)
        scheduler._unrecorded_send_uids.add(1)
        outcome = await scheduler._finalize_push_record(1, "12h", anchor_id, last_seen)
        return outcome, 1 in scheduler._unrecorded_send_uids
    outcome, still_present = run(scenario())
    assert outcome == "recorded"
    assert still_present is False


def test_failed_record_push_adds_uid_to_permanent_suppression_set(monkeypatch):
    async def _always_fail(uid, tier, anchor_turn_id, expected_last_seen):
        raise RuntimeError("simulated DB failure")
    monkeypatch.setattr(scheduler, "record_push_v1_delivery", _always_fail)

    async def scenario():
        outcome = await scheduler._finalize_push_record(1, "12h", 42, "irrelevant")
        return outcome, 1 in scheduler._unrecorded_send_uids
    outcome, present = run(scenario())
    assert outcome == "failed"
    assert present is True


# ── Correction #1, Blocker 5: real-conversation-anchor requirement ─────────
def test_no_anchor_at_all_means_no_push_sent():
    async def scenario():
        await _seed_inactive_user(1, days_inactive=2)  # NO anchor seeded
        bot = FakeBot()
        await scheduler._send_silence_pushes(bot)
        return bot
    bot = run(scenario())
    assert bot.sent == []


def test_only_a_previous_push_ui_reply_does_not_count_as_an_anchor():
    async def scenario():
        await _seed_inactive_user(1, days_inactive=2)
        # The user's ONLY assistant history is a prior Push V1 UI reply.
        await _seed_conversation_anchor(1, scenario=database.PUSH_UI_SCENARIO)
        bot = FakeBot()
        await scheduler._send_silence_pushes(bot)
        return bot
    bot = run(scenario())
    assert bot.sent == []


def test_genuine_ordinary_conversation_anchor_sends_normally_with_two_buttons():
    async def scenario():
        await _seed_inactive_user_with_anchor(1, days_inactive=2)  # scenario="open_chat"
        bot = FakeBot()
        await scheduler._send_silence_pushes(bot)
        return bot
    bot = run(scenario())
    assert len(bot.sent) == 1
    assert len(bot.edited_markup) == 1
    _, _, kb = bot.edited_markup[0]
    assert len(kb.inline_keyboard) == 1
    assert len(kb.inline_keyboard[0]) == 2


# ── POST-CODEX CORRECTION §1 (P1): access revocation must be bound to the
# final send guard -- a block_user_access() during ANY later prerequisite
# await (anchor, revision, or even the fresh access recheck itself) must
# still block the send, via the revision match in final_push_send_guard.
# uid=1 is OWNER_USER_ID in this fixture (has_full_access always True for
# the owner, independent of user_access) -- these tests use uid=2, a plain
# invited user, so a REAL user_access transition actually matters. ────────
def test_access_revoked_via_real_block_user_access_during_anchor_lookup_blocks_send(monkeypatch):
    real_get_anchor = database.get_last_assistant_message_id

    async def _side_effecting_anchor_lookup(uid):
        result = await real_get_anchor(uid)
        await database.block_user_access(uid)  # REAL revocation, not a mock
        return result
    monkeypatch.setattr(scheduler, "get_last_assistant_message_id",
                        _side_effecting_anchor_lookup)

    async def scenario():
        await database.grant_user_access(2)
        await _seed_inactive_user_with_anchor(2, days_inactive=2)
        bot = FakeBot()
        await scheduler._send_silence_pushes(bot)
        return bot
    bot = run(scenario())
    assert bot.sent == []


def test_access_revoked_between_fresh_access_check_and_final_guard_still_blocks(monkeypatch):
    # The fresh access_control.proactive_push_eligible() recheck (the
    # SECOND call inside one scheduler tick -- the first is the earlier,
    # pre-prerequisite check) itself observes True -- access was still
    # active at the instant it read -- but is revoked for real in the
    # narrow window immediately after that read, before the coroutine
    # returns to the scheduler. The stale True is returned anyway. The
    # final guard must still reject, because it independently re-checks the
    # revision block_user_access just bumped -- it does not rely on the
    # access check's own return value at all.
    real_proactive_push_eligible = ac.proactive_push_eligible
    calls = {"n": 0}

    async def _wrapper(uid):
        calls["n"] += 1
        if calls["n"] == 1:
            return await real_proactive_push_eligible(uid)  # the EARLIER check -- unaffected
        result = await real_proactive_push_eligible(uid)    # the FRESH, late recheck: True
        await database.block_user_access(uid)               # revoked immediately after
        return result                                        # stale True, returned anyway
    monkeypatch.setattr(scheduler.access_control, "proactive_push_eligible", _wrapper)

    async def scenario():
        await database.grant_user_access(2)
        await _seed_inactive_user_with_anchor(2, days_inactive=2)
        bot = FakeBot()
        await scheduler._send_silence_pushes(bot)
        return bot
    bot = run(scenario())
    assert bot.sent == []


def test_block_user_access_bumps_revision_on_real_active_to_blocked_transition():
    async def scenario():
        await database.upsert_user(9, "u", "U", "ru")
        await database.grant_user_access(9)
        before = await database.get_user_revision(9)
        await database.block_user_access(9)
        after = await database.get_user_revision(9)
        return before, after
    before, after = run(scenario())
    assert before == 0
    assert after == before + 1


def test_block_user_access_does_not_bump_again_on_idempotent_repeat_call():
    async def scenario():
        await database.upsert_user(9, "u", "U", "ru")
        await database.grant_user_access(9)
        await database.block_user_access(9)
        after_first = await database.get_user_revision(9)
        await database.block_user_access(9)  # already blocked -- safe no-op
        after_second = await database.get_user_revision(9)
        return after_first, after_second
    after_first, after_second = run(scenario())
    assert after_second == after_first  # no double bump


def test_block_user_access_never_touches_last_seen_or_message_count():
    async def scenario():
        await database.upsert_user(9, "u", "U", "ru")
        await database.grant_user_access(9)
        async with database.aiosqlite.connect(database.DB) as db:
            await db.execute(
                "UPDATE users SET last_seen=datetime('now','-5 days') WHERE id=?", (9,))
            await db.commit()
        before_last_seen = await database.get_last_seen(9)
        async with database.aiosqlite.connect(database.DB) as db:
            cur = await db.execute("SELECT message_count FROM users WHERE id=?", (9,))
            (before_count,) = await cur.fetchone()
        await database.block_user_access(9)
        after_last_seen = await database.get_last_seen(9)
        async with database.aiosqlite.connect(database.DB) as db:
            cur = await db.execute("SELECT message_count FROM users WHERE id=?", (9,))
            (after_count,) = await cur.fetchone()
        return before_last_seen, after_last_seen, before_count, after_count
    before_ls, after_ls, before_c, after_c = run(scenario())
    assert after_ls == before_ls
    assert after_c == before_c


# ── POST-CODEX CORRECTION §2 (P1): a completed GDPR delete-all must never
# be undone by a Push V1 post-send write racing against it. ────────────────
class _DeleteAllDuringSendBot(FakeBot):
    """Simulates a REAL delete-all committing in the window after Telegram
    confirms the send but before control returns to the scheduler."""
    def __init__(self, uid_to_delete):
        super().__init__()
        self._uid_to_delete = uid_to_delete

    async def send_message(self, uid, text, **kw):
        sent = await super().send_message(uid, text, **kw)
        if uid == self._uid_to_delete:
            await database.delete_all_personal_data(uid)
        return sent


async def _count(table, uid):
    async with database.aiosqlite.connect(database.DB) as db:
        cur = await db.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id=?", (uid,))
        (n,) = await cur.fetchone()
        return n


def test_B_delete_all_during_telegram_send_writes_nothing_and_no_keyboard():
    async def scenario():
        await _seed_inactive_user_with_anchor(1, days_inactive=2)
        bot = _DeleteAllDuringSendBot(uid_to_delete=1)
        await scheduler._send_silence_pushes(bot)
        return (bot, await _count("push_log", 1), await _count("push_settings", 1),
                await _count("push_action_bindings", 1), await _count("messages", 1),
                await _count("user_interaction_revision", 1))
    (bot, push_log_n, push_settings_n, bindings_n, messages_n, revision_n) = run(scenario())
    assert len(bot.sent) == 1        # Telegram send happened exactly once
    assert push_log_n == 0           # nothing recreated after erasure
    assert push_settings_n == 0
    assert bindings_n == 0
    assert messages_n == 0           # messages remain deleted
    assert revision_n == 0           # revision remains deleted
    assert bot.edited_markup == []   # no keyboard ever attached


# ── POST-CODEX CORRECTION §2 (P1), direct-DB serialization proof ──────────
def test_E_delete_before_write_writer_creates_nothing():
    async def scenario():
        await database.upsert_user(1, "u", "U", "ru")
        anchor_id = await _seed_conversation_anchor(1)
        last_seen = await database.get_last_seen(1)
        await database.delete_all_personal_data(1)
        recorded = await database.record_push_v1_delivery(1, "12h", anchor_id, last_seen)
        return recorded, await _count("push_log", 1)
    recorded, count = run(scenario())
    assert recorded is False
    assert count == 0


def test_E_write_before_delete_is_removed_afterward():
    async def scenario():
        await database.upsert_user(1, "u", "U", "ru")
        anchor_id = await _seed_conversation_anchor(1)
        last_seen = await database.get_last_seen(1)
        recorded = await database.record_push_v1_delivery(1, "12h", anchor_id, last_seen)
        count_before_delete = await _count("push_log", 1)
        await database.delete_all_personal_data(1)
        count_after_delete = await _count("push_log", 1)
        return recorded, count_before_delete, count_after_delete
    recorded, before, after = run(scenario())
    assert recorded is True
    assert before == 1
    assert after == 0


# ── POST-CODEX CORRECTION §3 (P2): re-engagement between send and
# persistence must not re-arm consecutive_unanswered. ──────────────────────
async def _consecutive_unanswered(uid):
    async with database.aiosqlite.connect(database.DB) as db:
        cur = await db.execute(
            "SELECT consecutive_unanswered FROM push_settings WHERE user_id=?", (uid,))
        row = await cur.fetchone()
        return row[0] if row else None


def test_D_user_reengages_between_send_and_persistence_keeps_unanswered_zero(monkeypatch):
    real_record = database.record_push_v1_delivery

    async def _reengage_then_record(uid, tier, anchor_turn_id, expected_last_seen):
        await database.touch_last_seen(uid)  # genuine re-engagement, right before persistence
        return await real_record(uid, tier, anchor_turn_id, expected_last_seen)
    monkeypatch.setattr(scheduler, "record_push_v1_delivery", _reengage_then_record)

    async def scenario():
        await _seed_inactive_user_with_anchor(1, days_inactive=2)
        bot = FakeBot()
        await scheduler._send_silence_pushes(bot)
        return bot, await _count("push_log", 1), await _consecutive_unanswered(1)
    bot, log_count, unanswered = run(scenario())
    assert len(bot.sent) == 1   # exactly one delivery
    assert log_count == 1       # delivery is still logged
    assert unanswered == 0      # the genuine reset survives


def test_D_first_attempt_fails_then_real_activity_then_second_attempt_succeeds_zero_unanswered(
        monkeypatch):
    real_record = database.record_push_v1_delivery
    state = {"n": 0}

    async def _fail_once_then_reengage_then_record(uid, tier, anchor_turn_id, expected_last_seen):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("simulated DB failure")
        await database.touch_last_seen(uid)
        return await real_record(uid, tier, anchor_turn_id, expected_last_seen)
    monkeypatch.setattr(scheduler, "record_push_v1_delivery",
                        _fail_once_then_reengage_then_record)

    async def scenario():
        await _seed_inactive_user_with_anchor(1, days_inactive=2)
        bot = FakeBot()
        await scheduler._send_silence_pushes(bot)
        return await _consecutive_unanswered(1)
    unanswered = run(scenario())
    assert unanswered == 0
