"""Phase 2 of the generic first-turn architecture — the approved generic
first-turn flow and continuation buttons wired into the local X20 pipeline
and the single universal-continuation callback handler. Uses a temporary
SQLite database, a mocked OpenAI client, and fake aiogram Message/
CallbackQuery objects only — no Telegram, no network, no production
database, no production credentials.
"""
import asyncio
import itertools
import re
import secrets
import sqlite3
import types

import pytest

import bot
import database
import prompts as pr
import safety_validator as sv

OWNER = 1
_next_id = itertools.count(70000)

ELIGIBLE_TEXT = "Мне в последнее время тревожно из-за работы, не могу расслабиться по вечерам."
# Triggers stage=ACUTE_DISTRESS / scenario=stabilization (both excluded from
# first-turn eligibility) without triggering the RED crisis override.
INELIGIBLE_TEXT = "Я в шоке, меня трясет, это случилось сегодня буквально сейчас, не могу поверить"


class FakeUser:
    def __init__(self, uid, username="user", first="U"):
        self.id = uid
        self.username = username
        self.first_name = first


class FakeSent:
    def __init__(self, chat_id, text):
        self.message_id = next(_next_id)
        self.chat = types.SimpleNamespace(id=chat_id)
        self.text = text


class FakeMessage:
    def __init__(self, user, text=""):
        self.from_user = user
        self.text = text
        self.chat = types.SimpleNamespace(id=user.id)
        self.message_id = next(_next_id)
        self.answers = []
        self.send_attempts = 0
        self.fail_answer = False

    async def answer(self, text, **kw):
        self.send_attempts += 1
        if self.fail_answer:
            raise RuntimeError("send failed")
        sent = FakeSent(self.chat.id, text)
        self.answers.append((text, kw))
        return sent


class FakeCallback:
    def __init__(self, user, message, data=""):
        self.from_user = user
        self.message = message
        self.data = data
        self.answers = []
        self.answer_attempts = 0

    async def answer(self, *a, **kw):
        self.answer_attempts += 1
        self.answers.append((a, kw))


def _async(value=None):
    async def _f(*a, **kw):
        return value
    return _f


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    monkeypatch.setattr(bot, "ensure_full_access_or_closed_test", _async(True))
    monkeypatch.setattr(bot, "_onboarding_blocks_ordinary_entry", _async(False))
    monkeypatch.setattr(bot.dependency_monitor, "record_message", _async(None))
    monkeypatch.setattr(bot.dependency_monitor, "check_dependency", _async(None))
    monkeypatch.setattr(bot.bot, "send_chat_action", _async(None))

    edit_calls = []

    async def fake_edit_markup(**kw):
        edit_calls.append(kw)
        return True
    monkeypatch.setattr(bot.bot, "edit_message_reply_markup", fake_edit_markup)
    return types.SimpleNamespace(edit_calls=edit_calls)


def _set_llm(monkeypatch, content=None, exc=None):
    calls = []

    async def fake_create(*a, **kw):
        calls.append(kw)
        if exc is not None:
            raise exc
        msg_obj = types.SimpleNamespace(content=content)
        choice = types.SimpleNamespace(message=msg_obj)
        return types.SimpleNamespace(choices=[choice])
    monkeypatch.setattr(bot.client.chat.completions, "create", fake_create)
    return calls


def _run(msg, user=None):
    asyncio.run(bot.pipeline(msg, msg.text, None, tg_user=user or msg.from_user))


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


async def _make_bound_button(uid, action, chat_id=100, source_message_id=200,
                             scenario="open_chat", lang="ru", turn_id=None):
    """Directly seeds one real bound button (mirrors the Phase 1 foundation
    test helper) so the six callback branches can each be exercised without
    re-running the whole first-turn generation flow."""
    if turn_id is None:
        turn_id = await database.save_message(uid, "assistant", "первый ответ", scenario, lang)
    rev = await database.bump_user_revision(uid)
    token = secrets.token_urlsafe(9)
    rows = [{"token": token, "turn_id": turn_id, "chat_id": chat_id,
             "source_message_id": source_message_id, "action": action,
             "expires_at": "2999-01-01"}]
    ok = await database.create_keyboard_batch_if_current(uid, rev, rows)
    assert ok
    return token, turn_id


def _press(uid, token, chat_id=100, source_message_id=200, fail=False):
    user = FakeUser(uid)
    src_msg = FakeMessage(user)
    src_msg.chat = types.SimpleNamespace(id=chat_id)
    src_msg.message_id = source_message_id
    src_msg.fail_answer = fail
    cb = FakeCallback(user, src_msg, data=f"ucbtn:{token}")
    asyncio.run(bot.cb_universal_continuation(cb))
    return src_msg, cb


# ── 1. no lexical/topic detector anywhere in the generic contract ─────────────

def test_no_lexical_detector_module_exists():
    import importlib
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("first_response")


def test_first_turn_contract_has_no_per_topic_template_table():
    assert not hasattr(pr, "TOPIC_TEMPLATES")
    assert not hasattr(pr, "TOPIC_EXAMPLES")
    assert isinstance(pr.FIRST_TURN_CONTRACT_TEXT_RU, str)
    assert isinstance(pr.FIRST_TURN_CONTRACT_TEXT_EN, str)


# ── 2/3/6/10/11: eligible turn — augmented prompt, real routing, single send,
#    exactly three opaque-token buttons, no outcome/quality/practice prompt ───

def test_eligible_turn_augments_prompt_runs_pipeline_and_publishes_buttons(env, monkeypatch):
    calls = _set_llm(monkeypatch, content=sv.get_first_turn_fallback("ru"))
    user = FakeUser(OWNER)
    msg = FakeMessage(user, ELIGIBLE_TEXT)
    _run(msg)

    assert len(calls) == 1
    sent_messages = calls[0]["messages"]
    assert sent_messages[0]["role"] == "system"
    assert pr.get_first_turn_contract_text("ru") in sent_messages[0]["content"]

    # state/stage/capacity/routing/router-log/memory really ran against the DB
    assert asyncio.run(database.load_state(OWNER)) is not None
    assert _row("SELECT COUNT(*) FROM router_decision_logs WHERE user_id=?", (OWNER,))[0] == 1

    # exactly one primary reply, no outcome/quality/practice follow-up
    assert len(msg.answers) == 1
    assert msg.answers[0][0] == sv.get_first_turn_fallback("ru")
    assert msg.answers[0][1].get("reply_markup") is None

    # exactly three buttons, opaque-token-only callback_data
    assert len(env.edit_calls) == 1
    kb = env.edit_calls[0]["reply_markup"]
    buttons = [b for row in kb.inline_keyboard for b in row]
    assert len(buttons) == 3
    for b in buttons:
        assert re.fullmatch(r"ucbtn:[A-Za-z0-9_\-]+", b.callback_data)

    assert _row("SELECT status FROM first_turn_claims WHERE user_id=?", (OWNER,))[0] == "delivered"


# ── 7/9: LLM exception -> exactly one fallback reply, no buttons ──────────────

def test_llm_exception_sends_fallback_no_buttons_no_second_llm_call(env, monkeypatch):
    calls = _set_llm(monkeypatch, exc=RuntimeError("boom"))
    msg = FakeMessage(FakeUser(OWNER), ELIGIBLE_TEXT)
    _run(msg)

    assert len(calls) == 1   # LLM never called again after the failure
    assert len(msg.answers) == 1
    assert msg.answers[0][0] == sv.get_first_turn_fallback("ru")
    assert len(env.edit_calls) == 0
    assert _row("SELECT status FROM first_turn_claims WHERE user_id=?",
               (OWNER,))[0] == "delivered_without_buttons"


# ── 8/9: validator rejection -> exactly one fallback reply, no buttons ────────

def test_validator_rejection_sends_fallback_no_buttons_no_second_llm_call(env, monkeypatch):
    calls = _set_llm(monkeypatch, content="Это очень тяжело для тебя.")  # zero '?' -> invalid
    msg = FakeMessage(FakeUser(OWNER), ELIGIBLE_TEXT)
    _run(msg)

    assert len(calls) == 1   # never re-prompted after validation failure
    assert len(msg.answers) == 1
    assert msg.answers[0][0] == sv.get_first_turn_fallback("ru")
    assert len(env.edit_calls) == 0
    assert _row("SELECT status FROM first_turn_claims WHERE user_id=?",
               (OWNER,))[0] == "delivered_without_buttons"


# ── 4: ineligible turns get no claim; ordinary pipeline path runs instead ─────

def test_ineligible_turn_gets_no_claim(env, monkeypatch):
    calls = _set_llm(monkeypatch, content="обычный ответ ассистента")
    msg = FakeMessage(FakeUser(OWNER), INELIGIBLE_TEXT)
    _run(msg)

    assert _row("SELECT COUNT(*) FROM first_turn_claims WHERE user_id=?", (OWNER,))[0] == 0
    assert len(calls) == 1
    assert len(env.edit_calls) == 0
    assert msg.answers[0][0] == "обычный ответ ассистента"
    # "stabilization" is not in (crisis, open_chat), so the pre-existing,
    # unmodified outcome-tracking prompt (step 18) still follows — unrelated
    # to Phase 2, proves the ordinary tail was left untouched.
    assert len(msg.answers) == 2


# ── 5: forced dependency answer — exactly one reply, no claim, no LLM call ────

def test_forced_dependency_answer_single_reply_no_claim_no_llm_call(env, monkeypatch):
    dep_text = "Похоже, ты общаешься очень часто. Помни, что я не замена живому человеку."
    monkeypatch.setattr(bot.dependency_monitor, "check_dependency", _async(dep_text))
    calls = _set_llm(monkeypatch, content="should never be used")
    msg = FakeMessage(FakeUser(OWNER), ELIGIBLE_TEXT)
    _run(msg)

    assert len(calls) == 0
    assert len(msg.answers) == 1
    assert msg.answers[0][0] == dep_text
    assert _row("SELECT COUNT(*) FROM first_turn_claims WHERE user_id=?", (OWNER,))[0] == 0
    assert _row("SELECT COUNT(*) FROM router_decision_logs WHERE user_id=?", (OWNER,))[0] == 1
    assert _row("SELECT COUNT(*) FROM messages WHERE user_id=?", (OWNER,))[0] == 2


# ── 12: new ordinary text bumps revision and invalidates an older binding ─────

def test_new_text_bumps_revision_and_invalidates_older_binding(env, monkeypatch):
    _set_llm(monkeypatch, content=sv.get_first_turn_fallback("ru"))
    user = FakeUser(OWNER)
    msg1 = FakeMessage(user, ELIGIBLE_TEXT)
    _run(msg1)
    kb = env.edit_calls[0]["reply_markup"]
    token = kb.inline_keyboard[0][0].callback_data[len("ucbtn:"):]
    chat_id = env.edit_calls[0]["chat_id"]
    source_message_id = env.edit_calls[0]["message_id"]
    rev_before = asyncio.run(database.get_user_revision(OWNER))

    msg2 = FakeMessage(user, "Ещё одно сообщение просто для проверки состояния.")
    _run(msg2)
    rev_after = asyncio.run(database.get_user_revision(OWNER))
    assert rev_after > rev_before

    consumed = asyncio.run(
        database.consume_interaction_binding(token, OWNER, chat_id, source_message_id))
    assert consumed is None


# ── 13/15: elaborate / clarify — exactly one reply, no keyboard ───────────────

def test_elaborate_sends_exactly_one_reply_no_keyboard(env):
    token, _ = asyncio.run(_make_bound_button(OWNER, "elaborate"))
    src_msg, cb = _press(OWNER, token)
    assert len(src_msg.answers) == 1
    assert src_msg.answers[0][0] == pr.get_continuation_reply("elaborate", "ru")
    assert len(env.edit_calls) == 0


def test_clarify_sends_exactly_one_reply_no_keyboard(env):
    token, turn_id = asyncio.run(_make_bound_button(OWNER, "clarify"))
    src_msg, cb = _press(OWNER, token)
    assert len(src_msg.answers) == 1
    assert src_msg.answers[0][0] == pr.get_continuation_reply("clarify", "ru")
    assert len(env.edit_calls) == 0

    # 19: confirmed delivery persists the reply and finalizes the event
    ev = _row(
        "SELECT reply_status, assistant_turn_id FROM user_interaction_events WHERE user_id=?",
        (OWNER,))
    assert ev[0] == "delivered"
    assert ev[1] is not None
    saved = _row("SELECT role, content FROM messages WHERE id=?", (ev[1],))
    assert saved == ("assistant", pr.get_continuation_reply("clarify", "ru"))


# ── 13/16: hard — low-burden reply, then nested keyboard at the EXACT
#    post_consumption_revision returned by the consume transaction ────────────

def test_hard_action_publishes_nested_keyboard_with_exact_revision(env):
    token, _ = asyncio.run(_make_bound_button(OWNER, "hard"))
    rev_before_consume = asyncio.run(database.get_user_revision(OWNER))
    src_msg, cb = _press(OWNER, token)

    assert len(src_msg.answers) == 1
    assert src_msg.answers[0][0] == pr.get_continuation_reply("hard", "ru")

    rev_after_consume = asyncio.run(database.get_user_revision(OWNER))
    assert rev_after_consume == rev_before_consume + 1

    assert len(env.edit_calls) == 1
    kb = env.edit_calls[0]["reply_markup"]
    buttons = [b for row in kb.inline_keyboard for b in row]
    assert len(buttons) == 3
    tokens = [b.callback_data[len("ucbtn:"):] for b in buttons]
    placeholders = ",".join("?" * len(tokens))
    revs = _rows(
        f"SELECT binding_revision FROM interaction_button_bindings WHERE token IN ({placeholders})",
        tokens)
    assert len(revs) == 3
    assert all(r[0] == rev_after_consume for r in revs)


# ── 17: nested keyboard NOT published once the revision has moved on ──────────

def test_hard_reply_keyboard_not_published_after_revision_moved(env):
    async def go():
        turn_id = await database.save_message(OWNER, "assistant", "низкозатратный ответ",
                                              "open_chat", "ru")
        stale_rev = await database.bump_user_revision(OWNER)
        await database.bump_user_revision(OWNER)   # revision moves on before publish
        msg = FakeMessage(FakeUser(OWNER))
        await bot._publish_hard_reply_buttons(msg, OWNER, turn_id, msg.message_id, stale_rev, "ru")
        return turn_id
    turn_id = asyncio.run(go())
    assert len(env.edit_calls) == 0
    assert _row("SELECT COUNT(*) FROM interaction_button_bindings WHERE turn_id=?",
               (turn_id,))[0] == 0


# ── 13/15: hardreply:easier/same/harder — distinct acks, no further keyboard ──

def test_hardreply_actions_produce_distinct_single_replies_no_keyboard(env):
    seen = set()
    for i, value in enumerate(("easier", "same", "harder")):
        token, _ = asyncio.run(_make_bound_button(OWNER, f"hardreply:{value}",
                                                  source_message_id=300 + i))
        src_msg, cb = _press(OWNER, token, source_message_id=300 + i)
        assert len(src_msg.answers) == 1
        text = src_msg.answers[0][0]
        assert text == pr.get_hard_reply_ack(value, "ru")
        seen.add(text)
    assert len(seen) == 3
    assert len(env.edit_calls) == 0


# ── 14: stale/expired/duplicate/wrong-user/wrong-message -> no reply ──────────

def test_duplicate_callback_sends_no_reply(env):
    token, _ = asyncio.run(_make_bound_button(OWNER, "elaborate"))
    src_msg1, _ = _press(OWNER, token)
    assert len(src_msg1.answers) == 1

    src_msg2, cb2 = _press(OWNER, token)
    assert len(src_msg2.answers) == 0
    assert len(cb2.answers) == 1   # localized "no longer active" popup only


def test_wrong_user_callback_sends_no_reply(env):
    token, _ = asyncio.run(_make_bound_button(OWNER, "elaborate"))
    other = FakeUser(999999)
    src_msg = FakeMessage(other)
    src_msg.chat = types.SimpleNamespace(id=100)
    src_msg.message_id = 200
    cb = FakeCallback(other, src_msg, data=f"ucbtn:{token}")
    asyncio.run(bot.cb_universal_continuation(cb))
    assert len(src_msg.answers) == 0


def test_wrong_message_id_callback_sends_no_reply(env):
    token, _ = asyncio.run(_make_bound_button(OWNER, "elaborate", source_message_id=200))
    src_msg, cb = _press(OWNER, token, source_message_id=999999)
    assert len(src_msg.answers) == 0


def test_expired_binding_sends_no_reply(env):
    async def go():
        turn_id = await database.save_message(OWNER, "assistant", "ответ", "open_chat", "ru")
        rev = await database.bump_user_revision(OWNER)
        token = secrets.token_urlsafe(9)
        rows = [{"token": token, "turn_id": turn_id, "chat_id": 100,
                 "source_message_id": 200, "action": "elaborate",
                 "expires_at": "2000-01-01"}]
        ok = await database.create_keyboard_batch_if_current(OWNER, rev, rows)
        assert ok
        return token
    token = asyncio.run(go())
    src_msg, cb = _press(OWNER, token)
    assert len(src_msg.answers) == 0
    assert len(cb.answers) == 1


# ── 18: callback send failure -> no assistant message; delivery_uncertain ─────

def test_callback_send_failure_creates_no_assistant_message(env):
    token, _ = asyncio.run(_make_bound_button(OWNER, "elaborate"))
    before = _row("SELECT COUNT(*) FROM messages WHERE role='assistant'")[0]
    src_msg, cb = _press(OWNER, token, fail=True)
    after = _row("SELECT COUNT(*) FROM messages WHERE role='assistant'")[0]
    assert after == before
    assert src_msg.send_attempts == 1   # exactly one attempt, no retry/resend

    ev = _row(
        "SELECT reply_status, reply_error_code FROM user_interaction_events WHERE user_id=?",
        (OWNER,))
    assert ev[0] == "delivery_uncertain"
    assert ev[1] == database.SEND_EXCEPTION


# ── A: primary-button revision race — captured user_revision must be used,
#    never a fresh re-read, when another user action moves the revision
#    between Telegram delivery and button-batch creation ───────────────────

def test_race_revision_moves_between_capture_and_publish_no_buttons(env, monkeypatch):
    async def fake_create(*a, **kw):
        # Simulates a second ordinary user action landing between the
        # user_revision captured by pipeline() (N) and button-batch
        # creation -- the live revision is now N+1 by the time
        # _publish_universal_buttons runs.
        await database.bump_user_revision(OWNER)
        msg_obj = types.SimpleNamespace(content=sv.get_first_turn_fallback("ru"))
        choice = types.SimpleNamespace(message=msg_obj)
        return types.SimpleNamespace(choices=[choice])
    monkeypatch.setattr(bot.client.chat.completions, "create", fake_create)

    msg = FakeMessage(FakeUser(OWNER), ELIGIBLE_TEXT)
    _run(msg)

    assert len(msg.answers) == 1   # text still sent
    assert len(env.edit_calls) == 0   # no markup attached
    assert _row("SELECT COUNT(*) FROM interaction_button_bindings WHERE user_id=?",
               (OWNER,))[0] == 0   # no bindings inserted
    assert _row("SELECT status FROM first_turn_claims WHERE user_id=?",
               (OWNER,))[0] == "delivered_without_buttons"


# ── B: forced-dependency send/save ordering and the truthy contract ───────────

def test_forced_dependency_success_one_user_one_assistant_one_send(env, monkeypatch):
    dep_text = "Похоже, ты общаешься очень часто. Помни, что я не замена живому человеку."
    monkeypatch.setattr(bot.dependency_monitor, "check_dependency", _async(dep_text))
    msg = FakeMessage(FakeUser(OWNER), ELIGIBLE_TEXT)
    _run(msg)

    assert msg.send_attempts == 1
    assert len(msg.answers) == 1
    assert msg.answers[0][0] == dep_text
    assert _row("SELECT COUNT(*) FROM messages WHERE user_id=? AND role='user'", (OWNER,))[0] == 1
    assert _row("SELECT COUNT(*) FROM messages WHERE user_id=? AND role='assistant'",
               (OWNER,))[0] == 1


def test_forced_dependency_send_failure_no_assistant_row_one_attempt(env, monkeypatch):
    dep_text = "Похоже, ты общаешься очень часто. Помни, что я не замена живому человеку."
    monkeypatch.setattr(bot.dependency_monitor, "check_dependency", _async(dep_text))
    msg = FakeMessage(FakeUser(OWNER), ELIGIBLE_TEXT)
    msg.fail_answer = True
    _run(msg)

    assert msg.send_attempts == 1   # no retry after the failed send
    assert len(msg.answers) == 0
    assert _row("SELECT COUNT(*) FROM messages WHERE user_id=? AND role='user'", (OWNER,))[0] == 1
    assert _row("SELECT COUNT(*) FROM messages WHERE user_id=? AND role='assistant'",
               (OWNER,))[0] == 0


def test_forced_dependency_send_success_assistant_save_failure_no_raise_no_resend(
        env, monkeypatch, capsys):
    dep_text = "Похоже, ты общаешься очень часто. Помни, что я не замена живому человеку."
    monkeypatch.setattr(bot.dependency_monitor, "check_dependency", _async(dep_text))

    real_save_message = bot.save_message

    async def flaky_save_message(uid, role, content, *a, **kw):
        if role == "assistant":
            raise RuntimeError("disk full: secret-token-xyz")
        return await real_save_message(uid, role, content, *a, **kw)
    monkeypatch.setattr(bot, "save_message", flaky_save_message)

    msg = FakeMessage(FakeUser(OWNER), ELIGIBLE_TEXT)
    _run(msg)   # must not raise -- pipeline() returns cleanly

    assert msg.send_attempts == 1   # delivery already confirmed before the save failed
    assert len(msg.answers) == 1
    assert msg.answers[0][0] == dep_text
    assert _row("SELECT COUNT(*) FROM messages WHERE user_id=? AND role='user'", (OWNER,))[0] == 1
    assert _row("SELECT COUNT(*) FROM messages WHERE user_id=? AND role='assistant'",
               (OWNER,))[0] == 0

    out = capsys.readouterr().out
    assert "event=dependency_assistant_save_failed" in out
    assert f"uid={OWNER}" in out
    assert "exc_type=RuntimeError" in out
    # redacted: no dependency reply text, no user text, no raw exception message
    assert dep_text not in out
    assert ELIGIBLE_TEXT not in out
    assert "disk full" not in out
    assert "secret-token-xyz" not in out


def test_forced_dependency_user_save_failure_no_send_no_raise(env, monkeypatch, capsys):
    dep_text = "Похоже, ты общаешься очень часто. Помни, что я не замена живому человеку."
    monkeypatch.setattr(bot.dependency_monitor, "check_dependency", _async(dep_text))
    calls = _set_llm(monkeypatch, content="should never be used")

    save_attempts = {"user": 0, "assistant": 0}

    async def flaky_save_message(uid, role, content, *a, **kw):
        save_attempts[role] = save_attempts.get(role, 0) + 1
        if role == "user":
            raise RuntimeError("disk full: secret-token-xyz")
        return 1
    monkeypatch.setattr(bot, "save_message", flaky_save_message)

    msg = FakeMessage(FakeUser(OWNER), ELIGIBLE_TEXT)
    _run(msg)   # must not raise -- pipeline() returns cleanly

    assert save_attempts["user"] == 1     # attempted exactly once
    assert save_attempts["assistant"] == 0   # never reached
    assert msg.send_attempts == 0         # no Telegram send attempted
    assert len(calls) == 0                # no LLM call
    assert _row("SELECT COUNT(*) FROM first_turn_claims WHERE user_id=?", (OWNER,))[0] == 0

    out = capsys.readouterr().out
    assert "event=dependency_user_save_failed" in out
    assert f"uid={OWNER}" in out
    assert "exc_type=RuntimeError" in out
    # redacted: no user input, no dependency reply, no raw exception message
    assert ELIGIBLE_TEXT not in out
    assert dep_text not in out
    assert "disk full" not in out
    assert "secret-token-xyz" not in out


def test_empty_string_dependency_result_follows_ordinary_first_turn_path(env, monkeypatch):
    monkeypatch.setattr(bot.dependency_monitor, "check_dependency", _async(""))
    calls = _set_llm(monkeypatch, content=sv.get_first_turn_fallback("ru"))
    msg = FakeMessage(FakeUser(OWNER), ELIGIBLE_TEXT)
    _run(msg)

    # an empty string must NOT be treated as a forced dependency answer --
    # the turn proceeds through the ordinary first-turn claim/LLM path.
    assert len(calls) == 1
    assert msg.answers[0][0] == sv.get_first_turn_fallback("ru")
    assert _row("SELECT status FROM first_turn_claims WHERE user_id=?",
               (OWNER,))[0] == "delivered"


# ── C: callback reply language is source-turn-owned, not profile-owned ────────

def test_callback_reply_uses_source_en_when_profile_is_ru(env):
    async def go():
        await database.upsert_user(OWNER, "user", "U", "ru")
        return await _make_bound_button(OWNER, "elaborate", scenario="open_chat", lang="en")
    token, _ = asyncio.run(go())
    src_msg, cb = _press(OWNER, token)
    assert len(src_msg.answers) == 1
    assert src_msg.answers[0][0] == pr.get_continuation_reply("elaborate", "en")


def test_callback_reply_uses_source_ru_when_profile_is_en(env):
    async def go():
        await database.upsert_user(OWNER, "user", "U", "en")
        return await _make_bound_button(OWNER, "clarify", scenario="open_chat", lang="ru")
    token, _ = asyncio.run(go())
    src_msg, cb = _press(OWNER, token)
    assert len(src_msg.answers) == 1
    assert src_msg.answers[0][0] == pr.get_continuation_reply("clarify", "ru")


def test_hard_nested_keyboard_uses_source_lang_not_profile(env):
    async def go():
        await database.upsert_user(OWNER, "user", "U", "ru")
        return await _make_bound_button(OWNER, "hard", scenario="open_chat", lang="en")
    token, _ = asyncio.run(go())
    src_msg, cb = _press(OWNER, token)
    assert src_msg.answers[0][0] == pr.get_continuation_reply("hard", "en")
    assert len(env.edit_calls) == 1
    kb = env.edit_calls[0]["reply_markup"]
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert labels == [label for label, _ in pr.HARD_REPLY_OPTIONS_EN]


def test_persisted_reply_lang_matches_actual_reply_text_and_scenario(env):
    async def go():
        await database.upsert_user(OWNER, "user", "U", "ru")
        token, _ = await _make_bound_button(OWNER, "clarify", scenario="reflective", lang="en")
        return token
    token = asyncio.run(go())
    src_msg, cb = _press(OWNER, token)
    ev = _row("SELECT assistant_turn_id FROM user_interaction_events WHERE user_id=?", (OWNER,))
    saved = _row("SELECT content, lang, scenario FROM messages WHERE id=?", (ev[0],))
    assert saved[0] == pr.get_continuation_reply("clarify", "en")
    assert saved[1] == "en"
    assert saved[2] == "reflective"   # scenario inheritance unchanged


# ── D: eligibility coverage gaps — isolate each condition individually,
#    proving (not merely assuming) that the OTHER dimensions stayed allowed
#    by wrapping the real production functions and capturing what they
#    actually returned for this turn ────────────────────────────────────────

def _wrap_capture(monkeypatch, name):
    """Wraps the real bot.<name> function so it still runs for real, and
    records its actual return value for this turn."""
    captured = {}
    real_fn = getattr(bot, name)

    def wrapper(*a, **kw):
        result = real_fn(*a, **kw)
        captured["value"] = result
        return result
    monkeypatch.setattr(bot, name, wrapper)
    return captured


def _wrap_detect_risk_override_level(monkeypatch, forced_level):
    """Calls the REAL detect_risk (so score/categories/etc. are genuine),
    then overrides only `level` -- the one axis under test."""
    captured = {}
    real_fn = bot.detect_risk

    def wrapper(text, lang):
        risk = dict(real_fn(text, lang))
        captured["categories"] = list(risk["categories"])
        risk["level"] = forced_level
        captured["level"] = risk["level"]
        return risk
    monkeypatch.setattr(bot, "detect_risk", wrapper)
    return captured


def _wrap_get_capacity_override(monkeypatch, forced_value):
    """Calls the REAL get_capacity for transparency, then overrides the
    returned value to the exact boundary under test."""
    captured = {}
    real_fn = bot.get_capacity

    def wrapper(state):
        captured["natural"] = real_fn(state)
        captured["value"] = forced_value
        return forced_value
    monkeypatch.setattr(bot, "get_capacity", wrapper)
    return captured


def test_risk_level_high_alone_prevents_claim(env, monkeypatch):
    risk_capture = _wrap_detect_risk_override_level(monkeypatch, "high")
    scenario_capture = _wrap_capture(monkeypatch, "choose_scenario")
    stage_capture = _wrap_capture(monkeypatch, "detect_stage")
    capacity_capture = _wrap_capture(monkeypatch, "get_capacity")
    calls = _set_llm(monkeypatch, content="обычный ответ")
    msg = FakeMessage(FakeUser(OWNER), ELIGIBLE_TEXT)
    _run(msg)

    assert risk_capture["level"] == "high"
    assert not ({"suicide", "self_harm"} & set(risk_capture["categories"]))
    assert scenario_capture["value"] in bot.FIRST_TURN_ALLOWED_SCENARIOS
    assert stage_capture["value"] not in bot.FIRST_TURN_EXCLUDED_STAGES
    assert capacity_capture["value"] >= bot.FIRST_TURN_MIN_CAPACITY
    assert _row("SELECT COUNT(*) FROM first_turn_claims WHERE user_id=?", (OWNER,))[0] == 0
    assert len(calls) == 1
    assert msg.answers[0][0] == "обычный ответ"


def test_risk_level_critical_alone_prevents_claim(env, monkeypatch):
    risk_capture = _wrap_detect_risk_override_level(monkeypatch, "critical")
    scenario_capture = _wrap_capture(monkeypatch, "choose_scenario")
    stage_capture = _wrap_capture(monkeypatch, "detect_stage")
    capacity_capture = _wrap_capture(monkeypatch, "get_capacity")
    calls = _set_llm(monkeypatch, content="обычный ответ")
    msg = FakeMessage(FakeUser(OWNER), ELIGIBLE_TEXT)
    _run(msg)

    assert risk_capture["level"] == "critical"
    assert not ({"suicide", "self_harm"} & set(risk_capture["categories"]))
    assert scenario_capture["value"] in bot.FIRST_TURN_ALLOWED_SCENARIOS
    assert stage_capture["value"] not in bot.FIRST_TURN_EXCLUDED_STAGES
    assert capacity_capture["value"] >= bot.FIRST_TURN_MIN_CAPACITY
    assert _row("SELECT COUNT(*) FROM first_turn_claims WHERE user_id=?", (OWNER,))[0] == 0
    assert len(calls) == 1
    assert msg.answers[0][0] == "обычный ответ"


def test_capacity_below_threshold_alone_prevents_claim(env, monkeypatch):
    capacity_capture = _wrap_get_capacity_override(monkeypatch, 0.29)
    scenario_capture = _wrap_capture(monkeypatch, "choose_scenario")
    stage_capture = _wrap_capture(monkeypatch, "detect_stage")
    calls = _set_llm(monkeypatch, content="обычный ответ")
    msg = FakeMessage(FakeUser(OWNER), ELIGIBLE_TEXT)
    _run(msg)

    real_risk = bot.detect_risk(ELIGIBLE_TEXT, "ru")
    assert real_risk["level"] not in bot.FIRST_TURN_EXCLUDED_RISK_LEVELS
    assert scenario_capture["value"] in bot.FIRST_TURN_ALLOWED_SCENARIOS
    assert stage_capture["value"] not in bot.FIRST_TURN_EXCLUDED_STAGES
    assert capacity_capture["value"] == 0.29
    assert _row("SELECT COUNT(*) FROM first_turn_claims WHERE user_id=?", (OWNER,))[0] == 0
    assert len(calls) == 1
    assert msg.answers[0][0] == "обычный ответ"


def test_capacity_exactly_threshold_remains_eligible(env, monkeypatch):
    capacity_capture = _wrap_get_capacity_override(monkeypatch, 0.3)
    scenario_capture = _wrap_capture(monkeypatch, "choose_scenario")
    stage_capture = _wrap_capture(monkeypatch, "detect_stage")
    calls = _set_llm(monkeypatch, content=sv.get_first_turn_fallback("ru"))
    msg = FakeMessage(FakeUser(OWNER), ELIGIBLE_TEXT)
    _run(msg)

    real_risk = bot.detect_risk(ELIGIBLE_TEXT, "ru")
    assert real_risk["level"] not in bot.FIRST_TURN_EXCLUDED_RISK_LEVELS
    assert scenario_capture["value"] in bot.FIRST_TURN_ALLOWED_SCENARIOS
    assert stage_capture["value"] not in bot.FIRST_TURN_EXCLUDED_STAGES
    assert capacity_capture["value"] == 0.3
    assert len(calls) == 1
    assert _row("SELECT status FROM first_turn_claims WHERE user_id=?",
               (OWNER,))[0] == "delivered"


# ── E: exactly one send attempt, including attempts that raise ────────────────

def test_first_turn_send_failure_exactly_one_send_attempt(env, monkeypatch):
    _set_llm(monkeypatch, content=sv.get_first_turn_fallback("ru"))
    msg = FakeMessage(FakeUser(OWNER), ELIGIBLE_TEXT)
    msg.fail_answer = True
    _run(msg)
    assert msg.send_attempts == 1   # no retry/resend after the failed send
    assert len(msg.answers) == 0
    assert _row("SELECT status FROM first_turn_claims WHERE user_id=?",
               (OWNER,))[0] == "delivery_uncertain"
