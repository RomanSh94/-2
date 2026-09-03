"""Contextual Continue V1 -- dedicated regression tests for the real-dispatch
cb_push_action integration (push_contextual_continue.py + bot._push_continue_
reply_text / bot._try_push_contextual_continue), using the SAME real-dispatch
technique as tests/test_push_v1_callback.py: a real Update fed through
bot.dp.feed_update(bot.bot, update). All OpenAI calls go through a locally
constructed fake client (bot.client is monkeypatched per-test) -- no real
network call ever happens in this file.

Also covers push_contextual_continue.py's own pure, offline unit contract
(build_messages / generate_push_contextual_continue) with a standalone fake
client, independent of bot.py/aiogram entirely.
"""
import asyncio
import itertools
import time
import types

import pytest

import access_control as ac
import bot
import config
import database
import prompts
import push_contextual_continue as pcc
import scheduler
from professional_turn_conversation_context import (
    ConversationTurn, ConversationTurnRole, ProfessionalConversationContext,
)
from aiogram.client.session.base import BaseSession
from aiogram.methods.base import Response
from aiogram.types import Update

USER_ID = 888444

_id_counter = itertools.count(1)


class _StubSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent = []

    async def close(self):
        pass

    async def make_request(self, bot_, method, timeout=None):
        self.sent.append(method)
        returning = method.__returning__
        if returning is bool:
            data = {"ok": True, "result": True}
        else:
            data = {"ok": True, "result": {
                "message_id": next(_id_counter),
                "date": int(time.time()),
                "chat": {"id": USER_ID, "type": "private"},
                "text": getattr(method, "text", "") or "",
            }}
        resp = Response[returning].model_validate(data, context={"bot": bot_})
        return resp.result

    async def stream_content(self, *a, **kw):
        raise NotImplementedError

    def texts(self):
        return [getattr(m, "text", None) for m in self.sent]


class _FakeCompletions:
    def __init__(self, response_content=None, raise_exc=None):
        self.response_content = response_content
        self.raise_exc = raise_exc
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.response_content is None:
            return types.SimpleNamespace(choices=[])
        message = types.SimpleNamespace(content=self.response_content)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


class _FakeOpenAIClient:
    def __init__(self, response_content=None, raise_exc=None):
        self.completions = _FakeCompletions(response_content, raise_exc)
        self.chat = self

    @property
    def calls(self):
        return self.completions.calls


def _async(value=None):
    async def _f(*a, **kw):
        return value
    return _f


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "push_cc.db"))
    asyncio.run(database.init_db())
    return database


@pytest.fixture(autouse=True)
def _common(monkeypatch, tmp_db):
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    monkeypatch.setattr(bot, "get_active_crisis", _async(None))
    monkeypatch.setattr(bot, "log_crisis_delivery", _async(None))
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", USER_ID)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})
    monkeypatch.setattr(config, "FIRST_USER_ONBOARDING_ENABLED", False)
    real_decide_push = scheduler.decide_push

    def _decide_push_at_test_daytime(now, last_activity, **kwargs):
        quiet_now = kwargs.get("quiet_now")
        if quiet_now is not None:
            kwargs["quiet_now"] = quiet_now.replace(
                hour=12, minute=0, second=0, microsecond=0)
        return real_decide_push(now, last_activity, **kwargs)

    monkeypatch.setattr(scheduler, "decide_push", _decide_push_at_test_daytime)
    stub = _StubSession()
    monkeypatch.setattr(bot.bot, "session", stub)
    monkeypatch.setattr(
        scheduler, "_generate_contextual_push_text",
        _async("В прошлый раз ты говорил, что работа выматывает. Стало ли сейчас легче?"),
    )
    return stub


def _make_callback_update(data, message_id, chat_id=USER_ID, uid=USER_ID):
    return Update.model_validate({
        "update_id": next(_id_counter),
        "callback_query": {
            "id": str(next(_id_counter)),
            "from": {"id": uid, "is_bot": False, "first_name": "T"},
            "message": {
                "message_id": message_id,
                "date": int(time.time()),
                "chat": {"id": chat_id, "type": "private"},
                "text": "placeholder",
            },
            "chat_instance": "1",
            "data": data,
        },
    }, context={"bot": bot.bot})


async def _seed_inactive_user(uid=USER_ID, days_inactive=2):
    await database.upsert_user(uid, "u", "U", "ru")
    async with database.aiosqlite.connect(database.DB) as db:
        await db.execute(
            "UPDATE users SET last_seen=datetime('now', ?) WHERE id=?",
            (f"-{days_inactive} days", uid))
        await db.commit()


async def _seed_row(uid, role, content, scenario, source):
    return await database.save_message(uid, role, content, scenario, "ru", source=source)


async def _seed_grounded_conversation(uid=USER_ID):
    """USER_AUTHORED distinctive fact + ASSISTANT_DELIVERED reply -- the
    real conversation the anchor (the assistant row) will be fenced to.
    Returns the anchor row id (the assistant row)."""
    await _seed_row(uid, "user", "Я поссорился с женой и не понимаю, стоит ли писать ей первым.",
                    "open_chat", database.MessageSource.USER_AUTHORED)
    anchor_id = await _seed_row(
        uid, "assistant", "Что бы тебе хотелось получить от следующего разговора с ней?",
        "open_chat", database.MessageSource.ASSISTANT_DELIVERED)
    return anchor_id


async def _deliver_real_push_with_anchor(stub, uid=USER_ID):
    await _seed_inactive_user(uid)
    anchor_id = await _seed_grounded_conversation(uid)
    await scheduler._send_silence_pushes(bot.bot)
    async with database.aiosqlite.connect(database.DB) as db:
        cur = await db.execute(
            "SELECT token, action, chat_id, source_message_id FROM push_action_bindings "
            "WHERE user_id=? AND consumed_at IS NULL AND superseded_at IS NULL", (uid,))
        rows = await cur.fetchall()
    by_action = {r[1]: r for r in rows}
    assert set(by_action) == {"push_continue", "push_new_topic"}
    chat_id = by_action["push_continue"][2]
    message_id = by_action["push_continue"][3]
    return (chat_id, message_id, by_action["push_continue"][0],
            by_action["push_new_topic"][0], anchor_id)


async def _assistant_rows(uid=USER_ID):
    async with database.aiosqlite.connect(database.DB) as db:
        cur = await db.execute(
            "SELECT id, scenario, source, content FROM messages "
            "WHERE user_id=? AND role='assistant' ORDER BY id", (uid,))
        return await cur.fetchall()


async def _user_rows(uid=USER_ID):
    async with database.aiosqlite.connect(database.DB) as db:
        cur = await db.execute(
            "SELECT id, source, content FROM messages "
            "WHERE user_id=? AND role='user' ORDER BY id", (uid,))
        return await cur.fetchall()


# ── 1. Real contextual grounding ────────────────────────────────────────────
def test_generator_receives_real_trusted_prior_turns_ending_at_anchor(monkeypatch):
    fake = _FakeOpenAIClient(response_content="Похоже, тебе сейчас важно решить, стоит ли писать первой.")
    monkeypatch.setattr(bot, "client", fake)

    async def run():
        chat_id, message_id, continue_token, _, anchor_id = await _deliver_real_push_with_anchor(bot_stub)
        await bot.dp.feed_update(
            bot.bot, _make_callback_update(f"pushbtn:{continue_token}", message_id, chat_id))
        return anchor_id
    bot_stub = _StubSession()
    monkeypatch.setattr(bot.bot, "session", bot_stub)
    anchor_id = asyncio.run(run())

    assert len(fake.calls) == 1
    sent_messages = fake.calls[0]["messages"]
    joined = " ".join(m["content"] for m in sent_messages)
    # The real distinctive USER fact and the real prior ASSISTANT question
    # both reached the generator, not just a generic acknowledgement.
    assert "поссорился с женой" in joined
    assert "следующего разговора" in joined
    # Not proven merely by a generic string changing -- the actual
    # generated (fake) candidate was delivered:
    assert "стоит ли писать первой" in " ".join(t for t in bot_stub.texts() if t)


# ── 2. Anchor fence ──────────────────────────────────────────────────────────
def test_context_excludes_rows_after_the_anchor(monkeypatch):
    fake = _FakeOpenAIClient(response_content="ok")
    monkeypatch.setattr(bot, "client", fake)
    stub = _StubSession()
    monkeypatch.setattr(bot.bot, "session", stub)

    async def run():
        chat_id, message_id, continue_token, _, anchor_id = await _deliver_real_push_with_anchor(stub)
        # A genuine NEW conversational turn happens AFTER push-send but
        # BEFORE the Continue tap -- must NOT leak into the context.
        await _seed_row(USER_ID, "user", "УНИКАЛЬНАЯ_ФРАЗА_ПОСЛЕ_АНКОРА",
                        "open_chat", database.MessageSource.USER_AUTHORED)
        await bot.dp.feed_update(
            bot.bot, _make_callback_update(f"pushbtn:{continue_token}", message_id, chat_id))
    asyncio.run(run())

    assert len(fake.calls) == 1
    joined = " ".join(m["content"] for m in fake.calls[0]["messages"])
    assert "УНИКАЛЬНАЯ_ФРАЗА_ПОСЛЕ_АНКОРА" not in joined


# ── 3. Provenance filtering ──────────────────────────────────────────────────
def test_context_excludes_synthetic_ui_push_ui_and_legacy_rows(monkeypatch):
    fake = _FakeOpenAIClient(response_content="ok")
    monkeypatch.setattr(bot, "client", fake)
    stub = _StubSession()
    monkeypatch.setattr(bot.bot, "session", stub)

    async def run():
        await _seed_inactive_user(USER_ID)
        # Untrusted rows seeded BEFORE the real anchor -- all must be
        # excluded from the generator's context regardless of role.
        await _seed_row(USER_ID, "user", "SYNTHETIC_BUTTON_LABEL_TEXT",
                        "open_chat", database.MessageSource.SYNTHETIC_UI)
        await _seed_row(USER_ID, "assistant", "PUSH_UI_REPLY_TEXT",
                        database.PUSH_UI_SCENARIO, database.MessageSource.ASSISTANT_DELIVERED)
        async with database.aiosqlite.connect(database.DB) as db:
            await db.execute(
                "INSERT INTO messages (user_id, role, content, scenario, lang, source) "
                "VALUES (?, 'assistant', 'LEGACY_NULL_PROVENANCE_TEXT', 'open_chat', 'ru', NULL)",
                (USER_ID,))
            await db.commit()
        anchor_id = await _seed_grounded_conversation(USER_ID)
        await scheduler._send_silence_pushes(bot.bot)
        async with database.aiosqlite.connect(database.DB) as db:
            cur = await db.execute(
                "SELECT token, action, chat_id, source_message_id FROM push_action_bindings "
                "WHERE user_id=? AND consumed_at IS NULL AND superseded_at IS NULL", (USER_ID,))
            rows = await cur.fetchall()
        by_action = {r[1]: r for r in rows}
        chat_id = by_action["push_continue"][2]
        message_id = by_action["push_continue"][3]
        continue_token = by_action["push_continue"][0]
        await bot.dp.feed_update(
            bot.bot, _make_callback_update(f"pushbtn:{continue_token}", message_id, chat_id))
    asyncio.run(run())

    assert len(fake.calls) == 1
    joined = " ".join(m["content"] for m in fake.calls[0]["messages"])
    assert "SYNTHETIC_BUTTON_LABEL_TEXT" not in joined
    assert "PUSH_UI_REPLY_TEXT" not in joined
    assert "LEGACY_NULL_PROVENANCE_TEXT" not in joined
    # The genuinely trusted grounded turns are still present:
    assert "поссорился с женой" in joined


# ── 4 & 5. No fabricated user row; exactly one genuine assistant persistence
def test_successful_continue_writes_zero_user_rows_and_one_assistant_row(monkeypatch):
    fake = _FakeOpenAIClient(response_content="Реальный контекстный ответ.")
    monkeypatch.setattr(bot, "client", fake)
    stub = _StubSession()
    monkeypatch.setattr(bot.bot, "session", stub)

    async def run():
        chat_id, message_id, continue_token, _, anchor_id = await _deliver_real_push_with_anchor(stub)
        user_rows_before = await _user_rows()
        assistant_rows_before = await _assistant_rows()
        await bot.dp.feed_update(
            bot.bot, _make_callback_update(f"pushbtn:{continue_token}", message_id, chat_id))
        return user_rows_before, assistant_rows_before
    user_rows_before, assistant_rows_before = asyncio.run(run())

    user_rows_after = asyncio.run(_user_rows())
    assistant_rows_after = asyncio.run(_assistant_rows())
    assert len(user_rows_after) == len(user_rows_before)  # zero new user rows
    assert len(assistant_rows_after) == len(assistant_rows_before) + 1
    new_row = assistant_rows_after[-1]
    _id, scenario, source, content = new_row
    assert content == "Реальный контекстный ответ."
    assert source == database.MessageSource.ASSISTANT_DELIVERED.value
    assert scenario == pcc.SCENARIO
    assert scenario != database.PUSH_UI_SCENARIO
    assert "Реальный контекстный ответ." in stub.texts()


# ── 6. USER vs ASSISTANT trust semantics enforced at the prompt layer ──────
def test_build_messages_keeps_user_and_assistant_roles_distinct_and_states_the_rule():
    ctx = ProfessionalConversationContext(turns=(
        ConversationTurn(message_row_id=1, role=ConversationTurnRole.USER,
                         content="Нет, дело не в страхе. Я просто зол."),
        ConversationTurn(message_row_id=2, role=ConversationTurnRole.ASSISTANT,
                         content="Похоже, ты боишься быть отвергнутым."),
    ))
    messages = pcc.build_messages(ctx, "ru")
    roles_by_content = {m["content"]: m["role"] for m in messages
                        if m["content"] in ("Нет, дело не в страхе. Я просто зол.",
                                            "Похоже, ты боишься быть отвергнутым.")}
    assert roles_by_content["Нет, дело не в страхе. Я просто зол."] == "user"
    assert roles_by_content["Похоже, ты боишься быть отвергнутым."] == "assistant"
    rules_text = " ".join(m["content"] for m in messages if m["role"] == "system")
    assert "the user previously said this" not in rules_text  # RU rules block, not EN
    assert "раньше сказал это" in rules_text
    assert "раньше сказал/спросил это" in rules_text
    assert "Никогда не превращай прошлую догадку" in rules_text


def test_build_messages_en_states_the_same_trust_rule():
    ctx = ProfessionalConversationContext(turns=(
        ConversationTurn(message_row_id=1, role=ConversationTurnRole.USER, content="fact"),
    ))
    messages = pcc.build_messages(ctx, "en")
    rules_text = " ".join(m["content"] for m in messages if m["role"] == "system")
    assert "the user previously said this" in rules_text
    assert "the assistant previously said/asked this" in rules_text
    assert "Never promote a previous assistant guess" in rules_text


# ── 7. Deleted anchor ────────────────────────────────────────────────────────
def test_anchor_deleted_before_tap_never_calls_generator(monkeypatch):
    fake = _FakeOpenAIClient(response_content="should never be used")
    monkeypatch.setattr(bot, "client", fake)
    stub = _StubSession()
    monkeypatch.setattr(bot.bot, "session", stub)

    async def run():
        chat_id, message_id, continue_token, _, anchor_id = await _deliver_real_push_with_anchor(stub)
        async with database.aiosqlite.connect(database.DB) as db:
            await db.execute("DELETE FROM messages WHERE user_id=?", (USER_ID,))
            await db.commit()
        await bot.dp.feed_update(
            bot.bot, _make_callback_update(f"pushbtn:{continue_token}", message_id, chat_id))
    asyncio.run(run())

    assert fake.calls == []
    assert prompts.PUSH_V1_NO_ANCHOR_REPLY_RU in stub.texts()
    assert "should never be used" not in " ".join(t for t in stub.texts() if t)


# ── 8. Model failure ─────────────────────────────────────────────────────────
def test_model_failure_falls_back_and_does_not_duplicate(monkeypatch):
    fake = _FakeOpenAIClient(raise_exc=RuntimeError("simulated provider outage"))
    monkeypatch.setattr(bot, "client", fake)
    stub = _StubSession()
    monkeypatch.setattr(bot.bot, "session", stub)

    async def run():
        chat_id, message_id, continue_token, _, anchor_id = await _deliver_real_push_with_anchor(stub)
        await bot.dp.feed_update(
            bot.bot, _make_callback_update(f"pushbtn:{continue_token}", message_id, chat_id))
    asyncio.run(run())

    assert len(fake.calls) == 1
    texts = [t for t in stub.texts() if t]
    assert texts.count(prompts.PUSH_V1_CONTINUE_REPLY_RU) == 1
    assistant_rows = asyncio.run(_assistant_rows())
    assert assistant_rows[-1][1] == database.PUSH_UI_SCENARIO  # fallback stays push_ui
    user_rows = asyncio.run(_user_rows())
    assert all(r[1] == database.MessageSource.USER_AUTHORED.value for r in user_rows)


# ── 9. Validator rejection ───────────────────────────────────────────────────
def test_validator_rejection_falls_back_never_delivers_rejected_candidate(monkeypatch):
    # A candidate containing a forbidden phrase must be rejected by the
    # REAL, unmodified safety_validator -- no second safety policy.
    rejected_candidate = "Ты определённо прав, продолжай в том же духе."  # certainty phrase
    fake = _FakeOpenAIClient(response_content=rejected_candidate)
    monkeypatch.setattr(bot, "client", fake)
    stub = _StubSession()
    monkeypatch.setattr(bot.bot, "session", stub)

    async def run():
        chat_id, message_id, continue_token, _, anchor_id = await _deliver_real_push_with_anchor(stub)
        await bot.dp.feed_update(
            bot.bot, _make_callback_update(f"pushbtn:{continue_token}", message_id, chat_id))
    asyncio.run(run())

    assert len(fake.calls) == 1
    texts = [t for t in stub.texts() if t]
    assert rejected_candidate not in texts
    assert prompts.PUSH_V1_CONTINUE_REPLY_RU in texts
    assistant_rows = asyncio.run(_assistant_rows())
    assert rejected_candidate not in [r[3] for r in assistant_rows]
    assert assistant_rows[-1][1] == database.PUSH_UI_SCENARIO


async def _seed_older_valid_conversation(uid=USER_ID):
    """A genuine USER_AUTHORED + ASSISTANT_DELIVERED pair, established
    BEFORE whatever pathological anchor row a test constructs next --
    proves a fallback is not accidentally explained by "no real
    conversation exists at all", but specifically by the exact anchor
    itself failing to qualify/survive."""
    await _seed_row(uid, "user", "У меня есть реальная тема для разговора.",
                    "open_chat", database.MessageSource.USER_AUTHORED)
    await _seed_row(uid, "assistant", "Расскажи подробнее.",
                    "open_chat", database.MessageSource.ASSISTANT_DELIVERED)


# ── P1-1 (owner correction): assistant-only trusted context ────────────────
# The exact "wrongly-named" test the owner review flagged is REPLACED here,
# not merely renamed -- it now asserts the CORRECT behavior (zero model
# calls) instead of the bug it used to lock in (one model call).
def test_assistant_only_context_falls_back_without_model_call(monkeypatch):
    fake = _FakeOpenAIClient(response_content="should never be used")
    monkeypatch.setattr(bot, "client", fake)

    async def run():
        await _seed_inactive_user(USER_ID)
        # A genuine, fully-qualifying ASSISTANT_DELIVERED anchor -- but NO
        # USER_AUTHORED row exists anywhere in this user's history at all.
        anchor_id = await database.save_message(
            USER_ID, "assistant", "prior reply", "open_chat", "ru",
            source=database.MessageSource.ASSISTANT_DELIVERED)
        return anchor_id
    anchor_id = asyncio.run(run())

    result = asyncio.run(bot._try_push_contextual_continue(USER_ID, "ru", anchor_id))
    assert result is None
    assert fake.calls == []


# ── P1-1: a role='user' row exists but is provenance-excluded (SYNTHETIC_UI)
def test_synthetic_ui_user_row_does_not_count_as_trusted_user_evidence(monkeypatch):
    fake = _FakeOpenAIClient(response_content="should never be used")
    monkeypatch.setattr(bot, "client", fake)

    async def run():
        await _seed_inactive_user(USER_ID)
        await _seed_row(USER_ID, "user", "SYNTHETIC_BUTTON_LABEL",
                        "open_chat", database.MessageSource.SYNTHETIC_UI)
        anchor_id = await _seed_row(USER_ID, "assistant", "genuine reply",
                                    "open_chat", database.MessageSource.ASSISTANT_DELIVERED)
        return anchor_id
    anchor_id = asyncio.run(run())

    result = asyncio.run(bot._try_push_contextual_continue(USER_ID, "ru", anchor_id))
    assert result is None
    assert fake.calls == []


# ── P1-2: the exact anchor must be the FINAL bounded context turn ──────────
def test_final_context_turn_equals_exact_anchor_for_genuine_case():
    async def run():
        await _seed_inactive_user(USER_ID)
        await _seed_older_valid_conversation(USER_ID)
        anchor_id = await _seed_row(USER_ID, "assistant", "genuine anchor reply",
                                    "open_chat", database.MessageSource.ASSISTANT_DELIVERED)
        rows = await database.get_trusted_conversation_history_through_anchor(USER_ID, anchor_id)
        from professional_turn_conversation_context import build_conversation_context_from_history_rows
        context = build_conversation_context_from_history_rows(rows)
        return context, anchor_id
    context, anchor_id = asyncio.run(run())
    assert not context.is_empty
    assert context.turns[-1].message_row_id == anchor_id
    assert context.turns[-1].role == ConversationTurnRole.ASSISTANT


# ── P1-2, case A: legacy/NULL-source exact anchor, older valid context exists
def test_legacy_null_source_exact_anchor_never_calls_model(monkeypatch):
    fake = _FakeOpenAIClient(response_content="should never be used")
    monkeypatch.setattr(bot, "client", fake)

    async def run():
        await _seed_inactive_user(USER_ID)
        await _seed_older_valid_conversation(USER_ID)
        async with database.aiosqlite.connect(database.DB) as db:
            cur = await db.execute(
                "INSERT INTO messages (user_id, role, content, scenario, lang, source) "
                "VALUES (?, 'assistant', 'legacy reply', 'open_chat', 'ru', NULL)",
                (USER_ID,))
            await db.commit()
            anchor_id = cur.lastrowid
        return anchor_id
    anchor_id = asyncio.run(run())

    result = asyncio.run(bot._try_push_contextual_continue(USER_ID, "ru", anchor_id))
    assert result is None
    assert fake.calls == []


# ── P1-2, case C: push_ui exact anchor, older valid context exists ─────────
def test_push_ui_exact_anchor_never_calls_model(monkeypatch):
    fake = _FakeOpenAIClient(response_content="should never be used")
    monkeypatch.setattr(bot, "client", fake)

    async def run():
        await _seed_inactive_user(USER_ID)
        await _seed_older_valid_conversation(USER_ID)
        anchor_id = await _seed_row(
            USER_ID, "assistant", "push ui reply",
            database.PUSH_UI_SCENARIO, database.MessageSource.ASSISTANT_DELIVERED)
        return anchor_id
    anchor_id = asyncio.run(run())

    result = asyncio.run(bot._try_push_contextual_continue(USER_ID, "ru", anchor_id))
    assert result is None
    assert fake.calls == []


# ── P1-2, case B: source-invalid exact anchor, older valid context exists ──
def test_source_mismatched_exact_anchor_never_calls_model(monkeypatch):
    fake = _FakeOpenAIClient(response_content="should never be used")
    monkeypatch.setattr(bot, "client", fake)

    async def run():
        await _seed_inactive_user(USER_ID)
        await _seed_older_valid_conversation(USER_ID)
        anchor_id = await _seed_row(
            USER_ID, "assistant", "synthetic-sourced anchor",
            "open_chat", database.MessageSource.SYNTHETIC_UI)
        return anchor_id
    anchor_id = asyncio.run(run())

    result = asyncio.run(bot._try_push_contextual_continue(USER_ID, "ru", anchor_id))
    assert result is None
    assert fake.calls == []


# ── P1-2, case D: oversized exact anchor omitted by the context builder ────
def test_oversized_exact_anchor_never_calls_model_and_no_fallback_to_older_topic(monkeypatch):
    fake = _FakeOpenAIClient(response_content="should never be used")
    monkeypatch.setattr(bot, "client", fake)

    async def run():
        await _seed_inactive_user(USER_ID)
        await _seed_older_valid_conversation(USER_ID)
        oversized = "x" * 2001  # > professional_turn_conversation_context.MAX_TURN_CONTENT_CHARS
        anchor_id = await _seed_row(
            USER_ID, "assistant", oversized,
            "open_chat", database.MessageSource.ASSISTANT_DELIVERED)
        return anchor_id
    anchor_id = asyncio.run(run())

    result = asyncio.run(bot._try_push_contextual_continue(USER_ID, "ru", anchor_id))
    assert result is None
    assert fake.calls == []  # never silently falls back to the older topic


# ── P1-3: Contextual Continue must never call validate_response_with_context
def test_contextual_continue_uses_response_only_validator_not_with_context(monkeypatch):
    def _must_not_be_called(*a, **kw):
        raise AssertionError(
            "validate_response_with_context must never be called for Contextual Continue")
    monkeypatch.setattr(bot, "validate_response_with_context", _must_not_be_called)
    fake = _FakeOpenAIClient(response_content="Настоящий контекстный ответ.")
    monkeypatch.setattr(bot, "client", fake)
    stub = _StubSession()
    monkeypatch.setattr(bot.bot, "session", stub)

    async def run():
        chat_id, message_id, continue_token, _, anchor_id = await _deliver_real_push_with_anchor(stub)
        await bot.dp.feed_update(
            bot.bot, _make_callback_update(f"pushbtn:{continue_token}", message_id, chat_id))
    asyncio.run(run())

    assert "Настоящий контекстный ответ." in [t for t in stub.texts() if t]


def test_response_only_validator_rejects_forbidden_phrase_directly():
    import safety_validator
    is_safe, reason = safety_validator.validate_response_without_current_user(
        "это точно так", "ru")
    assert is_safe is False
    assert reason is not None


def test_response_only_validator_accepts_ordinary_reply_directly():
    import safety_validator
    is_safe, reason = safety_validator.validate_response_without_current_user(
        "Что бы тебе хотелось сделать дальше?", "ru")
    assert is_safe is True
    assert reason is None


# ── 11. New Topic regression ─────────────────────────────────────────────────
def test_new_topic_never_calls_generator_and_uses_fixed_reply(monkeypatch):
    fake = _FakeOpenAIClient(response_content="must never appear")
    monkeypatch.setattr(bot, "client", fake)
    stub = _StubSession()
    monkeypatch.setattr(bot.bot, "session", stub)

    async def run():
        chat_id, message_id, _, new_topic_token, anchor_id = await _deliver_real_push_with_anchor(stub)
        await bot.dp.feed_update(
            bot.bot, _make_callback_update(f"pushbtn:{new_topic_token}", message_id, chat_id))
    asyncio.run(run())

    assert fake.calls == []
    texts = [t for t in stub.texts() if t]
    assert prompts.PUSH_V1_NEW_TOPIC_REPLY_RU in texts
    assert "must never appear" not in texts
    assistant_rows = asyncio.run(_assistant_rows())
    assert assistant_rows[-1][1] == database.PUSH_UI_SCENARIO
    user_rows = asyncio.run(_user_rows())
    assert all(r[1] == database.MessageSource.USER_AUTHORED.value for r in user_rows)


# ── push_contextual_continue.py pure unit contract (no bot.py/aiogram) ────
def test_generate_push_contextual_continue_returns_stripped_content():
    fake = _FakeOpenAIClient(response_content="  padded reply  ")
    ctx = ProfessionalConversationContext(turns=(
        ConversationTurn(message_row_id=1, role=ConversationTurnRole.USER, content="hi"),
    ))

    async def run():
        return await pcc.generate_push_contextual_continue(
            client=fake, model="gpt-4o-mini", conversation_context=ctx,
            lang="ru", max_tokens=300)
    result = asyncio.run(run())
    assert result == "padded reply"


def test_generate_push_contextual_continue_raises_on_empty_provider_response():
    fake = _FakeOpenAIClient(response_content=None)
    ctx = ProfessionalConversationContext(turns=(
        ConversationTurn(message_row_id=1, role=ConversationTurnRole.USER, content="hi"),
    ))

    async def run():
        return await pcc.generate_push_contextual_continue(
            client=fake, model="gpt-4o-mini", conversation_context=ctx,
            lang="ru", max_tokens=300)
    with pytest.raises(ValueError):
        asyncio.run(run())


def test_build_messages_rejects_empty_context():
    with pytest.raises(ValueError):
        pcc.build_messages(ProfessionalConversationContext(turns=()), "ru")


# ── 12. Push UI keyboard regression (unchanged -- see test_push_v1_scheduler
# .py for the exhaustive assertion; this is a narrow confirmation this task
# did not touch scheduler.py at all) ────────────────────────────────────────
def test_push_v1_keyboard_construction_untouched():
    kb = scheduler._push_v1_keyboard("ru", {"push_continue": "a", "push_new_topic": "b"})
    assert len(kb.inline_keyboard) == 1
    assert len(kb.inline_keyboard[0]) == 2
    continue_btn, new_topic_btn = kb.inline_keyboard[0]
    assert continue_btn.style == "primary"
    assert "style" not in new_topic_btn.model_dump(exclude_none=True)
