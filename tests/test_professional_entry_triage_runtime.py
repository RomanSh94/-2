"""Professional Core V2 -- Professional Entry Triage Runtime V1.

The first real Telegram vertical slice for Entry Triage: a dedicated,
isolated `professional_entry_triage_bindings` table and two dedicated
database functions (database.create_professional_entry_triage_bindings /
database.consume_professional_entry_triage_binding), plus a dedicated
`pucbtn:` callback namespace in bot.py that replaces (for RU users under
access_control.core_rollout_allowed) the legacy mood-entry surface.

Uses a temporary SQLite database and fake aiogram Message/CallbackQuery
objects only -- no Telegram, no network, no production database, no model
call. Every test proves one of: additive schema isolation, fail-closed DB
semantics for the two new functions, the send-then-bind-then-attach-keyboard
ordering, the trusted canonicalization chain from tap to delivered text,
crisis-priority override, the legacy mood-tap migration bridge, or the
static absence of any Analyzer/Planner/Renderer/Acceptance/model/legacy-
consumption call from the new code paths.
"""
import asyncio
import inspect
import sqlite3
import types

import pytest

import access_control as ac
import bot
import config
import database
from professional_reply_affordances import (
    ENTRY_TRIAGE_CONTRACT_V1,
    EntryTriageCategory,
    followup_focus_for_category,
)
from professional_turn_ui_context import TrustedEntryTriageDirective
from professional_turn_ui_immediate_response import build_trusted_ui_immediate_response

run = asyncio.run


# ── Fakes (mirrors tests/test_first_turn_pipeline.py's harness) ─────────────

class FakeUser:
    def __init__(self, uid, username="user", first_name="U"):
        self.id = uid
        self.username = username
        self.first_name = first_name


class FakeSent:
    def __init__(self, chat_id, text):
        self.message_id = FakeSent._next_id()
        self.chat = types.SimpleNamespace(id=chat_id)
        self.text = text

    _counter = 90000

    @classmethod
    def _next_id(cls):
        cls._counter += 1
        return cls._counter


class FakeMessage:
    def __init__(self, user, text=""):
        self.from_user = user
        self.text = text
        self.chat = types.SimpleNamespace(id=user.id)
        self.message_id = FakeSent._next_id()
        self.answers = []
        self.edit_reply_markup_calls = []

    async def answer(self, text, **kw):
        sent = FakeSent(self.chat.id, text)
        self.answers.append((text, kw))
        return sent

    async def edit_reply_markup(self, **kw):
        self.edit_reply_markup_calls.append(kw)


class FakeCallback:
    def __init__(self, user, message, data=""):
        self.from_user = user
        self.message = message
        self.data = data
        self.answers = []

    async def answer(self, *a, **kw):
        self.answers.append((a, kw))


class FakeFSM:
    async def get_data(self):
        return {}

    async def update_data(self, **kw):
        pass


def _async(value=None):
    async def _f(*a, **kw):
        return value
    return _f


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    run(database.init_db())

    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "off")
    monkeypatch.setattr(config, "FIRST_USER_ONBOARDING_ENABLED", False)

    monkeypatch.setattr(bot, "ensure_full_access_or_closed_test", _async(True))
    monkeypatch.setattr(bot, "get_memory_overview", _async({"message_count": 0}))
    monkeypatch.setattr(bot, "upsert_user", _async(None))
    monkeypatch.setattr(bot, "get_active_crisis", _async(None))
    # Default assumption for every test that isn't specifically about access
    # revocation (see the dedicated ACCESS-REVOCATION section near the end
    # of this file, which overrides this per-test): ordinary product access
    # is currently valid. cb_professional_entry_triage's V3 fresh
    # access_control.has_full_access(uid) recheck would otherwise reject
    # every test uid here, since none of them have a real user_access grant
    # or OWNER-role uid in the temp DB.
    monkeypatch.setattr(ac, "has_full_access", _async(True))

    edit_calls = []

    async def fake_edit_markup(**kw):
        edit_calls.append(kw)
        return True
    monkeypatch.setattr(bot.bot, "edit_message_reply_markup", fake_edit_markup)
    return types.SimpleNamespace(edit_calls=edit_calls)


def _allow_ru_rollout(monkeypatch):
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "all")


def _row(sql, params=()):
    con = sqlite3.connect(database.DB)
    r = con.execute(sql, params).fetchone()
    con.close()
    return r


def _rows(sql, params=()):
    con = sqlite3.connect(database.DB)
    rs = con.execute(sql, params).fetchall()
    con.close()
    return rs


def _binding_row(token):
    con = sqlite3.connect(database.DB)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM professional_entry_triage_bindings WHERE token=?", (token,)).fetchone()
    con.close()
    return dict(row) if row else None


def _messages_count(uid):
    return _row("SELECT COUNT(*) FROM messages WHERE user_id=?", (uid,))[0]


def _user_messages_count(uid):
    return _row(
        "SELECT COUNT(*) FROM messages WHERE user_id=? AND role='user'", (uid,))[0]


def _assistant_messages_count(uid):
    return _row(
        "SELECT COUNT(*) FROM messages WHERE user_id=? AND role='assistant'", (uid,))[0]


def _events_count(uid):
    return _row("SELECT COUNT(*) FROM user_interaction_events WHERE user_id=?", (uid,))[0]


async def _make_bindings(uid, chat_id=100, source_message_id=200, revision=None):
    if revision is None:
        revision = await database.get_user_revision(uid)
    tokens = {c: f"tok_{c.value}_{source_message_id}" for c in EntryTriageCategory}
    bindings = [{"token": tokens[c], "category": c, "expires_at": "2999-01-01 00:00:00"}
                for c in EntryTriageCategory]
    ok = await database.create_professional_entry_triage_bindings(
        uid, chat_id, source_message_id, revision, bindings)
    return ok, tokens


# ── Schema: additive, isolated ────────────────────────────────────────────

def test_table_created_by_init_db():
    names = [r[0] for r in _rows("SELECT name FROM sqlite_master WHERE type='table'")]
    assert "professional_entry_triage_bindings" in names


def test_legacy_interaction_tables_untouched_by_new_table():
    names = [r[0] for r in _rows("SELECT name FROM sqlite_master WHERE type='table'")]
    assert "interaction_button_bindings" in names
    assert "user_interaction_events" in names


def test_second_init_db_is_a_noop_for_the_new_table():
    run(database.init_db())   # rerun -- must not error or duplicate
    names = [r[0] for r in _rows("SELECT name FROM sqlite_master WHERE type='table'")]
    assert names.count("professional_entry_triage_bindings") == 1


# ── create_professional_entry_triage_bindings ────────────────────────────

def test_create_bindings_rejects_non_enum_category():
    with pytest.raises(ValueError):
        run(database.create_professional_entry_triage_bindings(
            1, 100, 200, 0, [{"token": "t1", "category": "ANXIETY_STRESS",
                              "expires_at": "2999-01-01"}]))


def test_create_bindings_invalid_category_in_batch_writes_nothing():
    bindings = [
        {"token": "good1", "category": EntryTriageCategory.ANXIETY_STRESS,
         "expires_at": "2999-01-01"},
        {"token": "bad1", "category": "NOT_REAL", "expires_at": "2999-01-01"},
    ]
    with pytest.raises(ValueError):
        run(database.create_professional_entry_triage_bindings(1, 100, 200, 0, bindings))
    assert _row("SELECT COUNT(*) FROM professional_entry_triage_bindings")[0] == 0


def test_create_bindings_succeeds_when_revision_matches():
    ok, tokens = run(_make_bindings(1))
    assert ok is True
    for c, tok in tokens.items():
        row = _binding_row(tok)
        assert row is not None
        assert row["category"] == c.value
        assert row["consumed_at"] is None
        assert row["superseded_at"] is None


def test_create_bindings_fails_when_revision_stale():
    run(database.bump_user_revision(2))   # live revision becomes 1
    ok, tokens = run(_make_bindings(2, revision=999))
    assert ok is False
    assert _row("SELECT COUNT(*) FROM professional_entry_triage_bindings WHERE user_id=?",
                (2,))[0] == 0


def test_create_bindings_supersedes_prior_open_bindings_for_same_user():
    ok1, tokens1 = run(_make_bindings(3, source_message_id=201))
    ok2, tokens2 = run(_make_bindings(3, source_message_id=202))
    assert ok1 and ok2
    for tok in tokens1.values():
        assert _binding_row(tok)["superseded_at"] is not None
    for tok in tokens2.values():
        assert _binding_row(tok)["superseded_at"] is None


def test_create_bindings_never_writes_to_messages_table():
    run(_make_bindings(4))
    assert _messages_count(4) == 0


def test_create_bindings_never_writes_to_user_interaction_events_table():
    run(_make_bindings(4))
    assert _events_count(4) == 0


def test_create_bindings_source_never_inserts_into_messages_or_events_tables():
    src = inspect.getsource(database.create_professional_entry_triage_bindings)
    assert "INSERT INTO messages" not in src
    assert "INSERT INTO user_interaction_events" not in src


# ── consume_professional_entry_triage_binding ────────────────────────────

def test_consume_binding_happy_path_returns_category():
    ok, tokens = run(_make_bindings(5, source_message_id=210))
    assert ok
    tok = tokens[EntryTriageCategory.ANXIETY_STRESS]
    result = run(database.consume_professional_entry_triage_binding(tok, 5, 100, 210))
    assert result is not None
    assert result.category is EntryTriageCategory.ANXIETY_STRESS


def test_consume_binding_wrong_user_fails_closed():
    ok, tokens = run(_make_bindings(6, source_message_id=211))
    tok = tokens[EntryTriageCategory.ANXIETY_STRESS]
    result = run(database.consume_professional_entry_triage_binding(tok, 999, 100, 211))
    assert result is None
    assert _binding_row(tok)["consumed_at"] is None


def test_consume_binding_wrong_chat_fails_closed():
    ok, tokens = run(_make_bindings(7, source_message_id=212))
    tok = tokens[EntryTriageCategory.ANXIETY_STRESS]
    result = run(database.consume_professional_entry_triage_binding(tok, 7, 999, 212))
    assert result is None


def test_consume_binding_wrong_message_fails_closed():
    ok, tokens = run(_make_bindings(8, source_message_id=213))
    tok = tokens[EntryTriageCategory.ANXIETY_STRESS]
    result = run(database.consume_professional_entry_triage_binding(tok, 8, 100, 999))
    assert result is None


def test_consume_binding_unknown_token_fails_closed():
    result = run(database.consume_professional_entry_triage_binding("no_such_token", 1, 100, 200))
    assert result is None


def test_consume_binding_already_consumed_fails_closed_on_second_tap():
    ok, tokens = run(_make_bindings(9, source_message_id=214))
    tok = tokens[EntryTriageCategory.ANXIETY_STRESS]
    first = run(database.consume_professional_entry_triage_binding(tok, 9, 100, 214))
    second = run(database.consume_professional_entry_triage_binding(tok, 9, 100, 214))
    assert first is not None
    assert second is None


def test_consume_binding_superseded_fails_closed():
    ok1, tokens1 = run(_make_bindings(10, source_message_id=215))
    run(_make_bindings(10, source_message_id=216))   # supersedes the first offer
    tok = tokens1[EntryTriageCategory.ANXIETY_STRESS]
    result = run(database.consume_professional_entry_triage_binding(tok, 10, 100, 215))
    assert result is None


def test_consume_binding_expired_fails_closed():
    con = sqlite3.connect(database.DB)
    con.execute(
        "INSERT INTO professional_entry_triage_bindings "
        "(token, user_id, chat_id, source_message_id, category, binding_revision, expires_at) "
        "VALUES ('expired_tok', 11, 100, 217, 'ANXIETY_STRESS', 0, '2000-01-01 00:00:00')")
    con.commit()
    con.close()
    result = run(database.consume_professional_entry_triage_binding("expired_tok", 11, 100, 217))
    assert result is None


def test_consume_binding_revision_mismatch_fails_closed():
    ok, tokens = run(_make_bindings(12, source_message_id=218, revision=0))
    run(database.bump_user_revision(12))   # live revision moves to 1, binding_revision stays 0
    tok = tokens[EntryTriageCategory.ANXIETY_STRESS]
    result = run(database.consume_professional_entry_triage_binding(tok, 12, 100, 218))
    assert result is None


def test_consume_binding_invalid_stored_category_fails_closed():
    con = sqlite3.connect(database.DB)
    con.execute(
        "INSERT INTO professional_entry_triage_bindings "
        "(token, user_id, chat_id, source_message_id, category, binding_revision, expires_at) "
        "VALUES ('garbage_cat_tok', 13, 100, 219, 'NOT_A_REAL_CATEGORY', 0, '2999-01-01 00:00:00')")
    con.commit()
    con.close()
    result = run(database.consume_professional_entry_triage_binding(
        "garbage_cat_tok", 13, 100, 219))
    assert result is None


def test_consume_binding_supersedes_sibling_options_from_same_offer():
    ok, tokens = run(_make_bindings(14, source_message_id=220))
    consumed_tok = tokens[EntryTriageCategory.ANXIETY_STRESS]
    result = run(database.consume_professional_entry_triage_binding(consumed_tok, 14, 100, 220))
    assert result is not None
    for category, tok in tokens.items():
        if category is EntryTriageCategory.ANXIETY_STRESS:
            continue
        row = _binding_row(tok)
        assert row["superseded_at"] is not None
        assert row["consumed_at"] is None


def test_consume_binding_never_writes_to_messages_table():
    ok, tokens = run(_make_bindings(15, source_message_id=221))
    run(database.consume_professional_entry_triage_binding(
        tokens[EntryTriageCategory.ANXIETY_STRESS], 15, 100, 221))
    assert _messages_count(15) == 0


def test_consume_binding_never_writes_to_user_interaction_events_table():
    ok, tokens = run(_make_bindings(16, source_message_id=222))
    run(database.consume_professional_entry_triage_binding(
        tokens[EntryTriageCategory.ANXIETY_STRESS], 16, 100, 222))
    assert _events_count(16) == 0


def test_consume_binding_source_never_inserts_into_messages_or_events_tables():
    src = inspect.getsource(database.consume_professional_entry_triage_binding)
    assert "INSERT INTO messages" not in src
    assert "INSERT INTO user_interaction_events" not in src


def test_consume_binding_source_never_calls_legacy_consumption_primitives():
    src = inspect.getsource(database.consume_professional_entry_triage_binding)
    assert "normalized_action_text" not in src
    assert "consume_interaction_binding" not in src


def test_binding_survives_a_fresh_connection_restart_proxy():
    """Durability proxy: the row is readable from a brand-new sqlite3
    connection (never the same aiosqlite connection object that wrote it,
    which is closed immediately after every call in this codebase's
    `async with aiosqlite.connect(DB) as db:` pattern) -- proves the write
    is really committed to the on-disk file, not held in an open
    transaction."""
    ok, tokens = run(_make_bindings(17, source_message_id=223))
    assert ok
    con = sqlite3.connect(database.DB)
    row = con.execute(
        "SELECT category FROM professional_entry_triage_bindings WHERE token=?",
        (tokens[EntryTriageCategory.ANXIETY_STRESS],)).fetchone()
    con.close()
    assert row == ("ANXIETY_STRESS",)


# ── bot.py: render-time eligibility and surface selection ───────────────────

def test_send_mood_entry_renders_professional_entry_triage_for_eligible_ru_user(monkeypatch):
    _allow_ru_rollout(monkeypatch)
    monkeypatch.setattr(bot, "get_stored_user_language", _async("ru"))
    user = FakeUser(1)
    msg = FakeMessage(user)
    run(bot.cmd_start(msg))
    text, kw = msg.answers[0]
    # V3: EXACT equality, not merely "contains" -- no legacy greeting
    # prefix, no legacy disclaimer suffix, no extra newline either side.
    assert text == ENTRY_TRIAGE_CONTRACT_V1.prompt_ru
    assert "Я не терапевт" not in text
    assert "Как ты сейчас себя чувствуешь?" not in text   # the old legacy mood question
    assert "Привет. Я здесь, чтобы выслушать." not in text   # the old legacy greeting
    assert kw.get("reply_markup") is None   # sent WITHOUT a keyboard first
    assert _row("SELECT COUNT(*) FROM professional_entry_triage_bindings WHERE user_id=?",
                (1,))[0] == len(EntryTriageCategory)


def test_professional_entry_surface_is_exact_even_via_first_user_onboarding_path(monkeypatch):
    # The first-user onboarding Start button's own call site passes through
    # get_onboarding(lang), which is exactly where the legacy greeting/mood
    # question text comes from -- proves the Professional branch inside
    # _send_mood_entry never lets that legacy text leak in via this second
    # call site either.
    _allow_ru_rollout(monkeypatch)
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    text, _ = bot.get_onboarding("ru")
    msg = FakeMessage(FakeUser(1))
    run(bot._send_mood_entry(msg, 1, "ru", text))
    sent_text, kw = msg.answers[0]
    assert sent_text == ENTRY_TRIAGE_CONTRACT_V1.prompt_ru
    assert "Как ты сейчас себя чувствуешь?" not in sent_text
    assert "Привет. Я здесь, чтобы выслушать." not in sent_text


def test_professional_entry_surface_is_exact_for_a_returning_ru_user(monkeypatch):
    # A returning user's cmd_start path computes a time-varied pick_greeting
    # string (is_first=False) and would prepend it in the legacy branch --
    # prove it is NOT prepended to the Professional contract either.
    _allow_ru_rollout(monkeypatch)
    monkeypatch.setattr(bot, "get_stored_user_language", _async("ru"))
    monkeypatch.setattr(bot, "get_memory_overview", _async({"message_count": 5}))
    monkeypatch.setattr(bot, "get_user_tz", _async((0, True, "ru")))
    user = FakeUser(1)
    msg = FakeMessage(user)
    run(bot.cmd_start(msg))
    text, kw = msg.answers[0]
    assert text == ENTRY_TRIAGE_CONTRACT_V1.prompt_ru


def test_send_mood_entry_uses_legacy_surface_for_non_ru_user(monkeypatch):
    _allow_ru_rollout(monkeypatch)
    monkeypatch.setattr(bot, "get_stored_user_language", _async("en"))
    user = FakeUser(1)
    msg = FakeMessage(user, "/start")
    run(bot.cmd_start(msg))
    text, kw = msg.answers[0]
    # Existing legacy EN text/disclaimer/mood-keyboard behavior unchanged.
    assert "Hi. I'm here to listen." in text
    assert "How are you feeling right now?" in text
    assert "I'm not a therapist." in text
    assert kw.get("reply_markup") is not None
    kb = kw["reply_markup"]
    assert any(btn.callback_data.startswith("mood:")
              for row in kb.inline_keyboard for btn in row)
    assert _row("SELECT COUNT(*) FROM professional_entry_triage_bindings")[0] == 0


def test_send_mood_entry_uses_legacy_surface_when_rollout_not_allowed(monkeypatch):
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "off")
    monkeypatch.setattr(bot, "get_stored_user_language", _async("ru"))
    user = FakeUser(1)
    msg = FakeMessage(user, "/start")
    run(bot.cmd_start(msg))
    text, kw = msg.answers[0]
    # Existing legacy RU text/disclaimer/mood-keyboard behavior unchanged.
    assert "Привет. Я здесь, чтобы выслушать." in text
    assert "Как ты сейчас себя чувствуешь?" in text
    assert "Я не терапевт." in text
    assert kw.get("reply_markup") is not None
    kb = kw["reply_markup"]
    assert any(btn.callback_data.startswith("mood:")
              for row in kb.inline_keyboard for btn in row)
    assert _row("SELECT COUNT(*) FROM professional_entry_triage_bindings")[0] == 0


def test_entry_triage_prompt_options_are_exactly_the_sealed_six_in_order(monkeypatch, env):
    _allow_ru_rollout(monkeypatch)
    monkeypatch.setattr(bot, "get_stored_user_language", _async("ru"))
    user = FakeUser(1)
    msg = FakeMessage(user)
    run(bot.cmd_start(msg))
    assert len(env.edit_calls) == 1
    kb = env.edit_calls[0]["reply_markup"]
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert labels == [opt.label_ru for opt in ENTRY_TRIAGE_CONTRACT_V1.options]
    for row in kb.inline_keyboard:
        for btn in row:
            assert btn.callback_data.startswith("pucbtn:")


def test_send_professional_entry_triage_does_not_bump_revision(monkeypatch):
    _allow_ru_rollout(monkeypatch)
    before = run(database.get_user_revision(1))
    msg = FakeMessage(FakeUser(1))
    run(bot._send_professional_entry_triage(msg, 1))
    after = run(database.get_user_revision(1))
    assert before == after


def test_send_professional_entry_triage_binds_before_attaching_keyboard(monkeypatch, env):
    _allow_ru_rollout(monkeypatch)
    msg = FakeMessage(FakeUser(1))
    run(bot._send_professional_entry_triage(msg, 1))
    assert len(msg.answers) == 1
    assert msg.answers[0][1].get("reply_markup") is None
    assert len(env.edit_calls) == 1
    assert _row("SELECT COUNT(*) FROM professional_entry_triage_bindings WHERE user_id=?",
                (1,))[0] == len(EntryTriageCategory)


def _source_without_docstring(func) -> str:
    """Scans only the CODE body, never the docstring -- a docstring
    explaining what this function deliberately does NOT call (as this
    module's own docstrings do) would otherwise trip a naive substring
    scan on the very prose that documents the guarantee."""
    src = inspect.getsource(func)
    doc = inspect.getdoc(func)
    if doc:
        src = src.replace(func.__doc__, "")
    return src


def test_send_professional_entry_triage_source_has_no_forbidden_calls():
    src = _source_without_docstring(bot._send_professional_entry_triage)
    for forbidden in ("consume_interaction_binding", "normalized_action_text",
                      "govern_turn_plan", "render_turn_response",
                      "accept_professional_response", "professional_turn_analyzer",
                      "professional_turn_producer", "chat.completions.create", "pipeline("):
        assert forbidden not in src


# ── bot.py: the trusted tap -> delivered-text chain ──────────────────────────

def _extract_token(kb, category):
    for row in kb.inline_keyboard:
        for btn in row:
            if btn.text == next(o.label_ru for o in ENTRY_TRIAGE_CONTRACT_V1.options
                                if o.category is category):
                return btn.callback_data[len("pucbtn:"):]
    raise AssertionError(f"no button found for {category}")


@pytest.mark.parametrize("category", list(EntryTriageCategory))
def test_tap_delivers_the_sealed_response_for_its_own_category(monkeypatch, env, category):
    _allow_ru_rollout(monkeypatch)
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    user = FakeUser(1)
    msg = FakeMessage(user)
    run(bot._send_professional_entry_triage(msg, 1))
    kb = env.edit_calls[0]["reply_markup"]
    token = _extract_token(kb, category)

    # The binding is keyed to the actual sent message's chat/message id.
    sent_row = _row(
        "SELECT chat_id, source_message_id FROM professional_entry_triage_bindings "
        "WHERE token=?", (token,))
    real_src = FakeMessage(user)
    real_src.chat = types.SimpleNamespace(id=sent_row[0])
    real_src.message_id = sent_row[1]
    cb = FakeCallback(user, real_src, data=f"pucbtn:{token}")

    run(bot.cb_professional_entry_triage(cb))

    expected = build_trusted_ui_immediate_response(
        TrustedEntryTriageDirective(category=category,
                                    followup_focus=followup_focus_for_category(category))
    ).text_ru
    assert real_src.answers
    assert real_src.answers[-1][0] == expected


def test_tap_never_writes_a_role_user_message(monkeypatch):
    _allow_ru_rollout(monkeypatch)
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    ok, tokens = run(_make_bindings(20, source_message_id=230))
    user = FakeUser(20)
    msg = FakeMessage(user)
    msg.chat = types.SimpleNamespace(id=100)
    msg.message_id = 230
    cb = FakeCallback(user, msg, data=f"pucbtn:{tokens[EntryTriageCategory.ANXIETY_STRESS]}")
    run(bot.cb_professional_entry_triage(cb))
    assert msg.answers   # the real consumption+delivery path actually ran
    # No fabricated role='user' row -- but the exact sealed role='assistant'
    # response IS intentionally persisted after successful delivery (V3
    # Defect 3 fix, see cb_professional_entry_triage).
    assert _user_messages_count(20) == 0
    assert _assistant_messages_count(20) == 1


def test_tap_never_writes_user_interaction_events(monkeypatch):
    _allow_ru_rollout(monkeypatch)
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    ok, tokens = run(_make_bindings(21, source_message_id=231))
    user = FakeUser(21)
    msg = FakeMessage(user)
    msg.chat = types.SimpleNamespace(id=100)
    msg.message_id = 231
    cb = FakeCallback(user, msg, data=f"pucbtn:{tokens[EntryTriageCategory.ANXIETY_STRESS]}")
    run(bot.cb_professional_entry_triage(cb))
    assert msg.answers   # the real consumption+delivery path actually ran
    assert _events_count(21) == 0


def test_tap_rejects_wrong_user(monkeypatch):
    _allow_ru_rollout(monkeypatch)
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    ok, tokens = run(_make_bindings(22, source_message_id=232))
    intruder = FakeUser(999)
    msg = FakeMessage(intruder)
    msg.chat = types.SimpleNamespace(id=100)
    msg.message_id = 232
    cb = FakeCallback(intruder, msg, data=f"pucbtn:{tokens[EntryTriageCategory.ANXIETY_STRESS]}")
    run(bot.cb_professional_entry_triage(cb))
    assert not msg.answers
    # the token is untouched -- the real owner can still use it.
    row = _binding_row(tokens[EntryTriageCategory.ANXIETY_STRESS])
    assert row["consumed_at"] is None


def test_tap_is_a_safe_noop_when_binding_already_consumed(monkeypatch):
    _allow_ru_rollout(monkeypatch)
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    ok, tokens = run(_make_bindings(23, source_message_id=233))
    tok = tokens[EntryTriageCategory.ANXIETY_STRESS]
    user = FakeUser(23)

    def _cb():
        msg = FakeMessage(user)
        msg.chat = types.SimpleNamespace(id=100)
        msg.message_id = 233
        return FakeCallback(user, msg, data=f"pucbtn:{tok}")

    first_cb = _cb()
    run(bot.cb_professional_entry_triage(first_cb))
    assert first_cb.message.answers   # first tap delivered a response

    second_cb = _cb()
    run(bot.cb_professional_entry_triage(second_cb))
    assert not second_cb.message.answers   # second tap on the same token is inert


def test_tap_crisis_priority_override_suppresses_response_and_supersedes_offer(monkeypatch):
    _allow_ru_rollout(monkeypatch)
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    monkeypatch.setattr(bot, "get_active_crisis", _async((1, 1, "ru")))
    ok, tokens = run(_make_bindings(24, source_message_id=234))
    tok = tokens[EntryTriageCategory.ANXIETY_STRESS]
    user = FakeUser(24)
    msg = FakeMessage(user)
    msg.chat = types.SimpleNamespace(id=100)
    msg.message_id = 234
    cb = FakeCallback(user, msg, data=f"pucbtn:{tok}")
    run(bot.cb_professional_entry_triage(cb))
    assert not msg.answers
    # Correction pass: the active-crisis branch best-effort SUPERSEDES (never
    # consumes) this user's open offers -- never a category consumption, and
    # the token can never be tapped into a delivered response again, even
    # after the crisis later resolves.
    row = _binding_row(tok)
    assert row["consumed_at"] is None
    assert row["superseded_at"] is not None
    assert _messages_count(24) == 0
    assert _events_count(24) == 0


def test_tap_defense_in_depth_rejects_when_rollout_no_longer_allowed(monkeypatch):
    ok, tokens = run(_make_bindings(25, source_message_id=235))
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "off")
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    user = FakeUser(25)
    msg = FakeMessage(user)
    msg.chat = types.SimpleNamespace(id=100)
    msg.message_id = 235
    cb = FakeCallback(user, msg, data=f"pucbtn:{tokens[EntryTriageCategory.ANXIETY_STRESS]}")
    run(bot.cb_professional_entry_triage(cb))
    assert not msg.answers
    assert _binding_row(tokens[EntryTriageCategory.ANXIETY_STRESS])["consumed_at"] is None


def test_entry_triage_callback_source_has_no_forbidden_calls():
    src = _source_without_docstring(bot.cb_professional_entry_triage)
    for forbidden in ("consume_interaction_binding", "normalized_action_text",
                      "govern_turn_plan", "render_turn_response",
                      "accept_professional_response", "professional_turn_analyzer",
                      "professional_turn_producer", "chat.completions.create", "pipeline("):
        assert forbidden not in src


# ── repeated /start supersession ──────────────────────────────────────────

def test_repeated_start_supersedes_prior_offer_and_old_token_stops_working(monkeypatch, env):
    _allow_ru_rollout(monkeypatch)
    monkeypatch.setattr(bot, "get_stored_user_language", _async("ru"))
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    user = FakeUser(1)

    msg1 = FakeMessage(user)
    run(bot.cmd_start(msg1))
    old_kb = env.edit_calls[0]["reply_markup"]
    old_token = old_kb.inline_keyboard[0][0].callback_data[len("pucbtn:"):]
    old_row = _binding_row(old_token)

    msg2 = FakeMessage(user)
    run(bot.cmd_start(msg2))
    new_kb = env.edit_calls[1]["reply_markup"]
    new_token = new_kb.inline_keyboard[0][0].callback_data[len("pucbtn:"):]

    assert _binding_row(old_token)["superseded_at"] is not None
    assert _binding_row(new_token)["superseded_at"] is None

    old_src = FakeMessage(user)
    old_src.chat = types.SimpleNamespace(id=old_row["chat_id"])
    old_src.message_id = old_row["source_message_id"]
    old_cb = FakeCallback(user, old_src, data=f"pucbtn:{old_token}")
    run(bot.cb_professional_entry_triage(old_cb))
    assert not old_src.answers   # the superseded token no longer works


# ── legacy mood:* migration bridge ────────────────────────────────────────

def test_legacy_mood_tap_redirects_newly_eligible_ru_user_to_entry_triage(monkeypatch, env):
    _allow_ru_rollout(monkeypatch)
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    pipeline_calls = []

    async def fake_pipeline(*a, **kw):
        pipeline_calls.append((a, kw))
    monkeypatch.setattr(bot, "pipeline", fake_pipeline)

    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="mood:0")
    run(bot.cb_mood(cb, FakeFSM()))

    assert pipeline_calls == []
    assert any(ENTRY_TRIAGE_CONTRACT_V1.prompt_ru in text for text, _ in msg.answers)
    assert _row("SELECT COUNT(*) FROM professional_entry_triage_bindings WHERE user_id=?",
                (1,))[0] == len(EntryTriageCategory)


def test_legacy_mood_tap_unchanged_for_non_eligible_user(monkeypatch):
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_ROLLOUT_MODE", "off")
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    pipeline_calls = []

    async def fake_pipeline(message, choice, state, tg_user=None):
        pipeline_calls.append(choice)
    monkeypatch.setattr(bot, "pipeline", fake_pipeline)

    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="mood:0")
    run(bot.cb_mood(cb, FakeFSM()))

    _, buttons = bot.get_onboarding("ru")
    assert pipeline_calls == [buttons[0]]
    assert _row("SELECT COUNT(*) FROM professional_entry_triage_bindings")[0] == 0


def test_legacy_mood_tap_unchanged_for_non_ru_user(monkeypatch):
    _allow_ru_rollout(monkeypatch)
    monkeypatch.setattr(bot, "get_user_language", _async("en"))
    pipeline_calls = []

    async def fake_pipeline(message, choice, state, tg_user=None):
        pipeline_calls.append(choice)
    monkeypatch.setattr(bot, "pipeline", fake_pipeline)

    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="mood:0")
    run(bot.cb_mood(cb, FakeFSM()))

    _, buttons = bot.get_onboarding("en")
    assert pipeline_calls == [buttons[0]]


# ── bot.py import-surface isolation ───────────────────────────────────────

def test_bot_py_imports_only_the_three_authorized_professional_modules():
    src = inspect.getsource(bot)
    for forbidden_module in ("professional_turn_analyzer", "professional_turn_producer",
                             "professional_turn_analysis"):
        assert f"import {forbidden_module}" not in src
        assert f"from {forbidden_module} " not in src
    for required in ("from professional_reply_affordances import",
                     "from professional_turn_ui_context import",
                     "from professional_turn_ui_immediate_response import"):
        assert required in src


# ══════════════════════════════════════════════════════════════════════════
# BOUNDED CORRECTION PASS -- privacy governance + crisis lifecycle
# ══════════════════════════════════════════════════════════════════════════
#
# Blocker 1 (privacy governance) is proven by tests/test_privacy_registry.py
# itself once professional_entry_triage_bindings is registered -- no new
# tests are added here for that; the existing generic default-deny/export/
# delete-all machinery already covers any INCLUDE/CASCADE_DELETE-registered
# table with no bespoke code. Blocker 2 (pre-crisis offer resurrection) is
# new product behavior specific to this table and is proven below.

def _risk():
    return {"score": 100, "level": "critical", "categories": ["suicide"]}


# ── supersede_professional_entry_triage_bindings: DB-level invariants ───────

def test_supersede_marks_open_bindings_superseded_not_consumed():
    ok, tokens = run(_make_bindings(30, source_message_id=300))
    affected = run(database.supersede_professional_entry_triage_bindings(30))
    assert affected == len(EntryTriageCategory)
    for tok in tokens.values():
        row = _binding_row(tok)
        assert row["superseded_at"] is not None
        assert row["consumed_at"] is None


def test_superseded_binding_then_fails_closed_on_consume():
    ok, tokens = run(_make_bindings(31, source_message_id=301))
    run(database.supersede_professional_entry_triage_bindings(31))
    tok = tokens[EntryTriageCategory.ANXIETY_STRESS]
    result = run(database.consume_professional_entry_triage_binding(tok, 31, 100, 301))
    assert result is None


def test_supersede_is_idempotent():
    ok, tokens = run(_make_bindings(32, source_message_id=302))
    first = run(database.supersede_professional_entry_triage_bindings(32))
    second = run(database.supersede_professional_entry_triage_bindings(32))
    assert first == len(EntryTriageCategory)
    assert second == 0


def test_supersede_with_nothing_open_affects_zero_rows():
    affected = run(database.supersede_professional_entry_triage_bindings(999999))
    assert affected == 0


def test_supersede_is_isolated_to_the_exact_user():
    ok_a, tokens_a = run(_make_bindings(33, source_message_id=303))
    ok_b, tokens_b = run(_make_bindings(34, source_message_id=304))
    run(database.supersede_professional_entry_triage_bindings(33))
    for tok in tokens_a.values():
        assert _binding_row(tok)["superseded_at"] is not None
    for tok in tokens_b.values():
        assert _binding_row(tok)["superseded_at"] is None


def test_supersede_never_writes_to_messages_or_events_tables():
    run(_make_bindings(35, source_message_id=305))
    run(database.supersede_professional_entry_triage_bindings(35))
    assert _messages_count(35) == 0
    assert _events_count(35) == 0


def test_supersede_never_touches_legacy_interaction_button_bindings():
    # Legacy interaction_button_bindings has no superseded_at column at all
    # (see database._INTERACTION_BINDINGS_TABLE_DDL) -- the only observable
    # proof available is that the legacy row is entirely untouched: still
    # present, still unconsumed.
    legacy_token = "legacy_tok_isolation"
    rows = [{"token": legacy_token, "turn_id": 1, "chat_id": 100,
             "source_message_id": 400, "action": "elaborate", "expires_at": "2999-01-01"}]
    ok = run(database.create_keyboard_batch_if_current(36, 0, rows))
    assert ok
    run(_make_bindings(36, source_message_id=401))
    run(database.supersede_professional_entry_triage_bindings(36))
    legacy_row = _row(
        "SELECT consumed_at FROM interaction_button_bindings WHERE token=?", (legacy_token,))
    assert legacy_row == (None,)


def test_supersede_source_never_inspects_category_or_forbidden_tables():
    src = _source_without_docstring(database.supersede_professional_entry_triage_bindings)
    assert "category" not in src
    assert "INSERT INTO messages" not in src
    assert "INSERT INTO user_interaction_events" not in src
    assert "interaction_button_bindings" not in src
    assert "user_interaction_revision" not in src


# ── crisis-start integration: no pre-crisis offer resurrection ─────────────

def test_crisis_start_supersedes_a_preexisting_entry_triage_offer(monkeypatch):
    monkeypatch.setattr(bot, "ADMIN_USER_IDS", [])
    ok, tokens = run(_make_bindings(40, source_message_id=500))
    tok = tokens[EntryTriageCategory.ANXIETY_STRESS]

    user = FakeUser(40)
    msg = FakeMessage(user)
    run(bot.trigger_crisis(msg, user.id, user.username, "RED text", _risk(), "ru"))

    # Crisis delivery happened.
    assert msg.answers
    # The pre-crisis offer was superseded, not consumed -- category was
    # never inspected/consumed as a selection.
    row = _binding_row(tok)
    assert row["superseded_at"] is not None
    assert row["consumed_at"] is None
    # And it can never later be tapped into a delivered response, even
    # after the crisis eventually resolves (simulated here simply by the
    # fact that get_active_crisis is not consulted by consume_* at all --
    # the token is already dead regardless of crisis state).
    result = run(database.consume_professional_entry_triage_binding(tok, 40, 100, 500))
    assert result is None


def test_crisis_start_supersession_creates_no_role_user_message():
    run(_make_bindings(41, source_message_id=501))
    run(database.supersede_professional_entry_triage_bindings(41))
    assert _messages_count(41) == 0


def test_crisis_start_supersession_creates_no_interaction_event():
    run(_make_bindings(42, source_message_id=502))
    run(database.supersede_professional_entry_triage_bindings(42))
    assert _events_count(42) == 0


def test_crisis_cleanup_failure_does_not_block_crisis_delivery(monkeypatch):
    monkeypatch.setattr(bot, "ADMIN_USER_IDS", [])

    async def boom(uid):
        raise RuntimeError("db locked")
    monkeypatch.setattr(bot, "supersede_professional_entry_triage_bindings", boom)

    user = FakeUser(43)
    msg = FakeMessage(user)
    # Must not raise past trigger_crisis, and the screen must still be sent.
    run(bot.trigger_crisis(msg, user.id, user.username, "RED text", _risk(), "ru"))
    assert len(msg.answers) == 1


def test_crisis_start_cleanup_placement_is_unconditional_and_swallows_exceptions():
    src = inspect.getsource(bot.trigger_crisis)
    idx_cleanup = src.index("supersede_professional_entry_triage_bindings(uid)")
    idx_log_event = src.index("log_crisis_event(")
    assert idx_cleanup < idx_log_event, (
        "crisis-start Entry Triage cleanup must run BEFORE log_crisis_event, "
        "same unconditional placement as the disclosure-flow/core-session "
        "supersession calls it sits beside")


# ── active-crisis callback: defense-in-depth supersession ──────────────────

def test_active_crisis_callback_supersedes_via_the_shared_primitive(monkeypatch):
    _allow_ru_rollout(monkeypatch)
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    monkeypatch.setattr(bot, "get_active_crisis", _async((1, 1, "ru")))
    ok, tokens = run(_make_bindings(44, source_message_id=503))
    tok = tokens[EntryTriageCategory.ANXIETY_STRESS]
    user = FakeUser(44)
    msg = FakeMessage(user)
    msg.chat = types.SimpleNamespace(id=100)
    msg.message_id = 503
    cb = FakeCallback(user, msg, data=f"pucbtn:{tok}")
    run(bot.cb_professional_entry_triage(cb))
    assert not msg.answers
    row = _binding_row(tok)
    assert row["superseded_at"] is not None
    assert row["consumed_at"] is None


def test_active_crisis_callback_supersession_failure_is_swallowed(monkeypatch):
    _allow_ru_rollout(monkeypatch)
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    monkeypatch.setattr(bot, "get_active_crisis", _async((1, 1, "ru")))

    async def boom(uid):
        raise RuntimeError("db locked")
    monkeypatch.setattr(bot, "supersede_professional_entry_triage_bindings", boom)

    ok, tokens = run(_make_bindings(45, source_message_id=504))
    user = FakeUser(45)
    msg = FakeMessage(user)
    msg.chat = types.SimpleNamespace(id=100)
    msg.message_id = 504
    cb = FakeCallback(user, msg, data=f"pucbtn:{tokens[EntryTriageCategory.ANXIETY_STRESS]}")
    # Must not raise past cb_professional_entry_triage.
    run(bot.cb_professional_entry_triage(cb))
    assert not msg.answers


# ── privacy governance: registry-driven export / delete, dedicated proof ────
# tests/test_privacy_registry.py::test_no_sensitive_table_is_unregistered and
# tests/test_privacy_export_delete.py's generic
# `set(out.keys()) == set(pr.PRIVACY_REGISTRY.keys())` check already prove
# the table is registered and appears in the generic export/preview dict.
# These tests go one step further: they seed a REAL row and prove the
# generic, registry-driven export_all_personal_data/delete_all_personal_data
# actually include/remove ITS data specifically, with real cross-user
# isolation -- no bespoke per-table code exists or is added for this.

def test_export_all_personal_data_includes_entry_triage_binding_row():
    run(_make_bindings(50, source_message_id=600))
    out = run(database.export_all_personal_data(50))
    rows = out["professional_entry_triage_bindings"]
    assert len(rows) == len(EntryTriageCategory)
    assert {r["category"] for r in rows} == {c.value for c in EntryTriageCategory}
    assert all(r["user_id"] == 50 for r in rows)


def test_export_all_personal_data_excludes_another_users_binding():
    run(_make_bindings(51, source_message_id=601))
    run(_make_bindings(52, source_message_id=602))
    out = run(database.export_all_personal_data(51))
    rows = out["professional_entry_triage_bindings"]
    assert len(rows) == len(EntryTriageCategory)
    assert all(r["user_id"] == 51 for r in rows)


def test_delete_all_personal_data_removes_entry_triage_bindings():
    run(_make_bindings(53, source_message_id=603))
    summary = run(database.delete_all_personal_data(53))
    assert summary["professional_entry_triage_bindings"] == len(EntryTriageCategory)
    remaining = run(database.export_all_personal_data(53))
    assert remaining["professional_entry_triage_bindings"] == []


def test_delete_all_personal_data_is_scoped_to_the_requesting_user_only():
    run(_make_bindings(54, source_message_id=604))
    run(_make_bindings(55, source_message_id=605))
    run(database.delete_all_personal_data(54))
    other = run(database.export_all_personal_data(55))
    assert len(other["professional_entry_triage_bindings"]) == len(EntryTriageCategory)


def test_entry_triage_bindings_registry_entry_has_expected_policies():
    import privacy_registry as pr
    entry = pr.PRIVACY_REGISTRY["professional_entry_triage_bindings"]
    assert entry.export_policy == "INCLUDE"
    assert entry.delete_policy == "CASCADE_DELETE"
    assert entry.category == "ENGAGEMENT"
    assert entry.user_id_column == "user_id"


# ══════════════════════════════════════════════════════════════════════════
# V3 CONTRACT-COMPLETION PASS -- exact surface, revision race, assistant
# persistence, live access recheck, exact-six batch closedness
# ══════════════════════════════════════════════════════════════════════════

# ── Defect 2: revision must be captured BEFORE the prompt is sent ──────────

def test_free_text_race_during_render_stales_the_offer_before_binding():
    """Deterministically exercises the ACTUAL runtime ordering inside
    _send_professional_entry_triage (not just create_professional_entry_
    triage_bindings's own already-existing stale-revision DB test): a
    genuine newer user turn is simulated landing in the exact gap between
    delivering the prompt and this function's own binding-creation call,
    by bumping the live revision as a side effect of the fake Telegram
    send itself. This test FAILS against the V2 ordering (which read the
    revision AFTER target.answer, so it would have observed the bumped
    value and matched itself) and PASSES only once revision capture moved
    before send."""
    uid = 60
    msg = FakeMessage(FakeUser(uid))
    original_answer = msg.answer

    async def racy_answer(text, **kw):
        sent = await original_answer(text, **kw)
        await database.bump_user_revision(uid)
        return sent
    msg.answer = racy_answer

    edit_calls = []

    async def fake_edit_markup(**kw):
        edit_calls.append(kw)
        return True
    import bot as _bot
    _orig_edit = _bot.bot.edit_message_reply_markup
    _bot.bot.edit_message_reply_markup = fake_edit_markup
    try:
        run(bot._send_professional_entry_triage(msg, uid))
    finally:
        _bot.bot.edit_message_reply_markup = _orig_edit

    assert len(msg.answers) == 1   # the exact plain prompt was still delivered
    assert msg.answers[0][0] == ENTRY_TRIAGE_CONTRACT_V1.prompt_ru
    # No bindings were created for the now-stale revision.
    assert _row("SELECT COUNT(*) FROM professional_entry_triage_bindings WHERE user_id=?",
                (uid,))[0] == 0
    # No actionable keyboard was ever attached -- the prompt may remain
    # visible as plain, freely-typeable text.
    assert edit_calls == []
    # No synthetic user message was created by this render path either.
    assert _user_messages_count(uid) == 0


# ── Defect 3: assistant persistence only after successful delivery ─────────

def test_successful_tap_persists_exactly_one_assistant_row_with_exact_fields(monkeypatch, env):
    _allow_ru_rollout(monkeypatch)
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    ok, tokens = run(_make_bindings(61, source_message_id=610))
    user = FakeUser(61)
    msg = FakeMessage(user)
    msg.chat = types.SimpleNamespace(id=100)
    msg.message_id = 610
    cb = FakeCallback(user, msg, data=f"pucbtn:{tokens[EntryTriageCategory.ANXIETY_STRESS]}")
    run(bot.cb_professional_entry_triage(cb))

    row = _row(
        "SELECT content, scenario, lang FROM messages WHERE user_id=? AND role='assistant'",
        (61,))
    assert row is not None
    content, scenario, lang = row
    assert content == msg.answers[-1][0]
    assert scenario == "open_chat"
    assert lang == "ru"
    assert _assistant_messages_count(61) == 1
    assert _user_messages_count(61) == 0
    assert _events_count(61) == 0


def test_telegram_send_failure_persists_no_assistant_row(monkeypatch):
    _allow_ru_rollout(monkeypatch)
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    ok, tokens = run(_make_bindings(62, source_message_id=611))
    user = FakeUser(62)
    msg = FakeMessage(user)
    msg.chat = types.SimpleNamespace(id=100)
    msg.message_id = 611

    async def failing_answer(text, **kw):
        raise RuntimeError("telegram send failed")
    msg.answer = failing_answer

    cb = FakeCallback(user, msg, data=f"pucbtn:{tokens[EntryTriageCategory.ANXIETY_STRESS]}")
    # Must not raise past cb_professional_entry_triage.
    run(bot.cb_professional_entry_triage(cb))

    assert _assistant_messages_count(62) == 0
    assert _user_messages_count(62) == 0
    assert _events_count(62) == 0
    # The binding was still consumed (the failure is a delivery failure,
    # not a rejection of the tap itself) -- a second tap on the same token
    # remains a safe no-op, never a retry that could double-consume.
    row = _binding_row(tokens[EntryTriageCategory.ANXIETY_STRESS])
    assert row["consumed_at"] is not None


def test_assistant_save_failure_after_successful_delivery_sends_no_duplicate(monkeypatch):
    _allow_ru_rollout(monkeypatch)
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))

    async def boom(*a, **kw):
        raise RuntimeError("db locked")
    monkeypatch.setattr(bot, "save_message", boom)

    ok, tokens = run(_make_bindings(63, source_message_id=612))
    user = FakeUser(63)
    msg = FakeMessage(user)
    msg.chat = types.SimpleNamespace(id=100)
    msg.message_id = 612
    cb = FakeCallback(user, msg, data=f"pucbtn:{tokens[EntryTriageCategory.ANXIETY_STRESS]}")
    # Must not raise past cb_professional_entry_triage.
    run(bot.cb_professional_entry_triage(cb))

    # Telegram delivery happened exactly once -- no duplicate/retry send.
    assert len(msg.answers) == 1
    # No assistant row exists (the save itself failed) and no user row was
    # fabricated either.
    assert _assistant_messages_count(63) == 0
    assert _user_messages_count(63) == 0
    # The token is not resurrected into an actionable state -- it stays
    # consumed, exactly as the real delivery outcome was.
    row = _binding_row(tokens[EntryTriageCategory.ANXIETY_STRESS])
    assert row["consumed_at"] is not None


def test_send_professional_entry_triage_source_never_saves_assistant_messages():
    # Assistant persistence belongs ONLY in the bot callback after
    # successful delivery, never in the render helper itself.
    src = _source_without_docstring(bot._send_professional_entry_triage)
    assert "save_message" not in src


def test_create_and_consume_and_supersede_never_insert_into_messages():
    for fn in (database.create_professional_entry_triage_bindings,
              database.consume_professional_entry_triage_binding,
              database.supersede_professional_entry_triage_bindings):
        src = inspect.getsource(fn)
        assert "INSERT INTO messages" not in src


# ── Defect 4: live access recheck before consumption ────────────────────────

def test_revoked_access_tap_creates_no_response_and_no_messages(monkeypatch):
    _allow_ru_rollout(monkeypatch)
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    # Token was validly created (env fixture's default has_full_access=True
    # was in effect at creation time)...
    ok, tokens = run(_make_bindings(64, source_message_id=613))
    # ...then ordinary access is revoked before the tap.
    monkeypatch.setattr(ac, "has_full_access", _async(False))

    user = FakeUser(64)
    msg = FakeMessage(user)
    msg.chat = types.SimpleNamespace(id=100)
    msg.message_id = 613
    cb = FakeCallback(user, msg, data=f"pucbtn:{tokens[EntryTriageCategory.ANXIETY_STRESS]}")
    run(bot.cb_professional_entry_triage(cb))

    assert not msg.answers
    assert _user_messages_count(64) == 0
    assert _assistant_messages_count(64) == 0
    assert _events_count(64) == 0
    # The token is not treated as successfully consumed -- it remains
    # exactly as it was (not consumed, not superseded), so a legitimate
    # later tap (once access is restored) is not silently burned by the
    # revoked-access attempt.
    row = _binding_row(tokens[EntryTriageCategory.ANXIETY_STRESS])
    assert row["consumed_at"] is None
    assert row["superseded_at"] is None


def test_has_full_access_lookup_exception_fails_closed(monkeypatch):
    _allow_ru_rollout(monkeypatch)
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))

    async def boom(uid):
        raise RuntimeError("db locked")
    monkeypatch.setattr(ac, "has_full_access", boom)

    ok, tokens = run(_make_bindings(65, source_message_id=614))
    user = FakeUser(65)
    msg = FakeMessage(user)
    msg.chat = types.SimpleNamespace(id=100)
    msg.message_id = 614
    cb = FakeCallback(user, msg, data=f"pucbtn:{tokens[EntryTriageCategory.ANXIETY_STRESS]}")
    # Must not raise past cb_professional_entry_triage.
    run(bot.cb_professional_entry_triage(cb))
    assert not msg.answers
    row = _binding_row(tokens[EntryTriageCategory.ANXIETY_STRESS])
    assert row["consumed_at"] is None


def test_historical_mood_tap_access_false_is_a_fail_closed_noop_not_legacy_pipeline(monkeypatch):
    # V4 correction: has_full_access=False for a RU + core_rollout_allowed-
    # eligible user must NEVER be reinterpreted as permission to route the
    # stale mood label through the legacy synthetic-text pipeline() call --
    # it is a neutral no-op, not a fallback to legacy behavior.
    _allow_ru_rollout(monkeypatch)
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    monkeypatch.setattr(ac, "has_full_access", _async(False))
    pipeline_calls = []

    async def fake_pipeline(message, choice, state, tg_user=None):
        pipeline_calls.append(choice)
    monkeypatch.setattr(bot, "pipeline", fake_pipeline)

    uid = 80
    user = FakeUser(uid)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="mood:0")
    run(bot.cb_mood(cb, FakeFSM()))

    assert pipeline_calls == []
    assert not msg.answers
    assert cb.answers   # the callback was still answered (no hung spinner)
    assert _row("SELECT COUNT(*) FROM professional_entry_triage_bindings")[0] == 0
    assert _user_messages_count(uid) == 0
    assert _assistant_messages_count(uid) == 0
    assert _events_count(uid) == 0


def test_historical_mood_tap_access_exception_is_a_fail_closed_noop_not_legacy_pipeline(monkeypatch):
    # V4 correction: an access_control.has_full_access lookup EXCEPTION for
    # a RU + core_rollout_allowed-eligible user must also never reach the
    # legacy synthetic-text pipeline() call -- fails closed exactly like a
    # False result, via cb_mood itself (not a DB-only proxy test).
    _allow_ru_rollout(monkeypatch)
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))

    async def boom(uid):
        raise RuntimeError("db locked")
    monkeypatch.setattr(ac, "has_full_access", boom)
    pipeline_calls = []

    async def fake_pipeline(message, choice, state, tg_user=None):
        pipeline_calls.append(choice)
    monkeypatch.setattr(bot, "pipeline", fake_pipeline)

    uid = 81
    user = FakeUser(uid)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="mood:0")
    # Must not raise past cb_mood.
    run(bot.cb_mood(cb, FakeFSM()))

    assert pipeline_calls == []
    assert not msg.answers
    assert cb.answers   # the callback was still answered (no hung spinner)
    assert _row("SELECT COUNT(*) FROM professional_entry_triage_bindings")[0] == 0
    assert _user_messages_count(uid) == 0
    assert _assistant_messages_count(uid) == 0
    assert _events_count(uid) == 0


def test_historical_mood_tap_full_access_true_still_redirects_to_professional(monkeypatch):
    # V3 behavior preserved: the only outcome that renders the Professional
    # surface is RU + core_rollout_allowed + has_full_access all True.
    _allow_ru_rollout(monkeypatch)
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    monkeypatch.setattr(ac, "has_full_access", _async(True))
    pipeline_calls = []

    async def fake_pipeline(message, choice, state, tg_user=None):
        pipeline_calls.append(choice)
    monkeypatch.setattr(bot, "pipeline", fake_pipeline)

    uid = 82
    user = FakeUser(uid)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="mood:0")
    run(bot.cb_mood(cb, FakeFSM()))

    assert pipeline_calls == []
    assert msg.answers
    text, kw = msg.answers[0]
    assert text == ENTRY_TRIAGE_CONTRACT_V1.prompt_ru
    assert _row("SELECT COUNT(*) FROM professional_entry_triage_bindings WHERE user_id=?",
                (uid,))[0] == len(EntryTriageCategory)


def test_cb_mood_source_never_reaches_legacy_pipeline_from_professional_branch():
    """Static proof mirroring section 17: within the RU + core_rollout_
    allowed branch, has_full_access's exception path and its False path
    each their own `return` strictly before the branch's own `pipeline(`-
    reaching legacy code (which lives entirely outside/after this whole
    if-block) -- there is no code path from either outcome down to the
    module-level `await pipeline(callback.message, choice, state,`
    call."""
    src = _source_without_docstring(bot.cb_mood)
    branch_start = src.index('if lang == "ru" and await access_control.core_rollout_allowed(uid):')
    branch_end = src.index("_, buttons = get_onboarding(lang)")
    branch_body = src[branch_start:branch_end]
    assert "pipeline(" not in branch_body
    # Every path inside the branch is terminated by its own return --
    # exactly 3 explicit returns (exception path, False path, success
    # path) inside this branch body, and none other.
    assert branch_body.count("return") == 3


def test_cb_professional_entry_triage_checks_access_after_crisis_before_consume():
    src = _source_without_docstring(bot.cb_professional_entry_triage)
    idx_crisis = src.index("get_active_crisis(uid)")
    idx_access = src.index("has_full_access(uid)")
    idx_consume = src.index("consume_professional_entry_triage_binding(")
    assert idx_crisis < idx_access < idx_consume


# ── Small closedness gap: exactly the six sealed categories, no more/fewer ──

def test_create_bindings_rejects_five_row_batch_missing_a_category():
    bindings = [{"token": f"tok{i}", "category": c, "expires_at": "2999-01-01"}
                for i, c in enumerate(list(EntryTriageCategory)[:-1])]
    with pytest.raises(ValueError):
        run(database.create_professional_entry_triage_bindings(70, 100, 700, 0, bindings))
    assert _row("SELECT COUNT(*) FROM professional_entry_triage_bindings WHERE user_id=?",
                (70,))[0] == 0


def test_create_bindings_rejects_duplicate_category_replacing_another():
    cats = list(EntryTriageCategory)
    bindings = [{"token": f"tok{i}", "category": cats[0], "expires_at": "2999-01-01"}
                for i in range(len(cats))]   # same category six times
    with pytest.raises(ValueError):
        run(database.create_professional_entry_triage_bindings(71, 100, 701, 0, bindings))
    assert _row("SELECT COUNT(*) FROM professional_entry_triage_bindings WHERE user_id=?",
                (71,))[0] == 0


def test_create_bindings_rejects_seven_row_batch_with_a_duplicate():
    cats = list(EntryTriageCategory) + [list(EntryTriageCategory)[0]]
    bindings = [{"token": f"tok{i}", "category": c, "expires_at": "2999-01-01"}
                for i, c in enumerate(cats)]
    with pytest.raises(ValueError):
        run(database.create_professional_entry_triage_bindings(72, 100, 702, 0, bindings))
    assert _row("SELECT COUNT(*) FROM professional_entry_triage_bindings WHERE user_id=?",
                (72,))[0] == 0


def test_create_bindings_accepts_the_exact_six_batch():
    ok, tokens = run(_make_bindings(73, source_message_id=703))
    assert ok is True
    assert _row("SELECT COUNT(*) FROM professional_entry_triage_bindings WHERE user_id=?",
                (73,))[0] == len(EntryTriageCategory)


# ── V2 correction areas remain frozen ────────────────────────────────────────

def test_privacy_registry_entry_unchanged_from_v2():
    import privacy_registry as pr
    entry = pr.PRIVACY_REGISTRY["professional_entry_triage_bindings"]
    assert entry.category == "ENGAGEMENT"
    assert entry.export_policy == "INCLUDE"
    assert entry.delete_policy == "CASCADE_DELETE"


def test_supersede_primitive_still_exists_and_is_unchanged_in_shape():
    assert hasattr(database, "supersede_professional_entry_triage_bindings")
    src = inspect.getsource(database.supersede_professional_entry_triage_bindings)
    assert "professional_entry_triage_bindings" in src
    assert "category" not in _source_without_docstring(
        database.supersede_professional_entry_triage_bindings)
