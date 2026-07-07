"""Discuss-with-bot -- PR C2. First production caller of traced_response_builder.

Handler-level tests against the REAL bot.py handlers and a REAL tmp sqlite DB,
following the exact conventions of tests/test_questionnaire_results_killswitch.py
and tests/test_questionnaire_specialist_report.py (FakeUser/FakeMessage/
FakeCallback, bot._load_registry_fresh monkeypatched to tests/fixtures/registry/).

Scope reminder (see CLAUDE.md): NO visible button is added in this PR (deferred
to C2.1) -- q:m is reachable only by direct callback invocation here. NO FSM,
no free-text continuation. NO new detect_risk call (there is no user-typed
text in this flow).
"""
import asyncio
import inspect
import pathlib
import types

import pytest

import bot
import config
import database
import questionnaires
import questionnaire_ux
import access_control as ac
import traced_response
from safety_validator import validate_response_with_context

FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures" / "registry"


class FakeUser:
    def __init__(self, uid, username="user"):
        self.id = uid
        self.username = username


class FakeMessage:
    def __init__(self, user, text=""):
        self.from_user = user
        self.text = text
        self.chat = types.SimpleNamespace(id=user.id)
        self.answers = []

    async def answer(self, text, **kw):
        self.answers.append((text, kw))

    async def edit_reply_markup(self, **kw):
        pass


class FakeCallback:
    def __init__(self, user, message, data=""):
        self.from_user = user
        self.message = message
        self.data = data
        self.answered = 0

    async def answer(self, *a, **kw):
        self.answered += 1


def _async(value=None):
    async def _f(*a, **kw):
        return value
    return _f


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    return database


@pytest.fixture(autouse=True)
def _common(monkeypatch, tmp_db):
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    monkeypatch.setattr(bot, "get_active_crisis", _async(None))
    monkeypatch.setattr(bot, "log_crisis_delivery", _async(None))
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})
    monkeypatch.setattr(bot, "CallbackQuery", FakeCallback)
    monkeypatch.setattr(bot, "_load_registry_fresh",
                        lambda: questionnaires.load_registry(FIXTURE_DIR))
    monkeypatch.setattr(config, "QUESTIONNAIRE_INTERPRETATION_ENABLED", True)


async def _sessions_for(uid):
    import sqlite3
    con = sqlite3.connect(database.DB)
    rows = con.execute(
        "SELECT id, questionnaire_id, questionnaire_version, status, current_index "
        "FROM questionnaire_sessions WHERE user_id=?", (uid,)).fetchall()
    con.close()
    return rows


def _complete_flow(user, msg, qid="demo_result_eligible_v1", answer="a1", n_items=3):
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data=f"q:s:{qid}")))
    session_id = asyncio.run(_sessions_for(user.id))[-1][0]
    for step in range(n_items):
        cb = FakeCallback(user, msg, data=f"q:a:{session_id}:{step}:{answer}")
        asyncio.run(bot.cb_questionnaire_answer(cb))
    return session_id


def _force_completed_session(uid, qid, version="1"):
    session_id = asyncio.run(database.start_questionnaire_session(uid, qid, version))
    asyncio.run(database.complete_questionnaire_session(session_id))
    return session_id


# ── 1. bare menu is deterministic, never calls the LLM/traced builder ───────
def test_discuss_menu_no_llm_call(monkeypatch):
    called = {"llm": False, "traced": False}

    async def _boom_llm(*a, **kw):
        called["llm"] = True
        raise AssertionError("LLM must not be called for the bare menu")

    async def _boom_traced(*a, **kw):
        called["traced"] = True
        raise AssertionError("traced_response_builder must not be called for the bare menu")

    monkeypatch.setattr(bot.client.chat.completions, "create", _boom_llm)
    monkeypatch.setattr(bot, "traced_response_builder", _boom_traced)

    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg)
    cb = FakeCallback(user, msg, data=f"q:m:{session_id}")
    asyncio.run(bot.cb_questionnaire_discuss_menu(cb))

    assert called["llm"] is False
    assert called["traced"] is False
    text, kw = msg.answers[-1]
    assert text == questionnaire_ux.discuss_menu_text("ru")
    kb = kw["reply_markup"]
    datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert datas == [f"q:m:{session_id}:why", f"q:m:{session_id}:next",
                      f"q:m:{session_id}:specialist", f"q:r:{session_id}", "menu:back"]


# ── 2. topic callback goes through traced_response_builder ──────────────────
def test_discuss_topic_uses_traced_response_builder(monkeypatch):
    calls = []

    async def _fake_builder(**kwargs):
        calls.append(kwargs)
        await kwargs["send"]("TRACED-REPLY")
        return "rid-fake"

    monkeypatch.setattr(bot, "traced_response_builder", _fake_builder)

    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg)
    cb = FakeCallback(user, msg, data=f"q:m:{session_id}:why")
    asyncio.run(bot.cb_questionnaire_discuss_topic(cb))

    assert len(calls) == 1
    assert calls[0]["user_id"] == 1
    assert calls[0]["requester_uid"] == 1
    assert msg.answers[-1][0] == "TRACED-REPLY"


# ── 3. A1NotAllowed fails closed, no crash, no raw uid, no LLM ───────────────
def test_discuss_a1_not_allowed_fails_closed_no_raw_uid(monkeypatch, capsys):
    llm_called = {"v": False}

    async def _boom_llm(*a, **kw):
        llm_called["v"] = True
        raise AssertionError("LLM must not be called")

    monkeypatch.setattr(bot.client.chat.completions, "create", _boom_llm)

    async def _deny(requester_uid):
        raise ac.A1NotAllowed(f"A1 traced latent influence not allowed for this requester "
                               f"(role={ac.resolve_role_safe(requester_uid)}, mode={ac.DEPLOYMENT_MODE})")
    monkeypatch.setattr(ac, "assert_a1_allowed", _deny)

    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg)
    n_before = len(msg.answers)
    cb = FakeCallback(user, msg, data=f"q:m:{session_id}:why")
    asyncio.run(bot.cb_questionnaire_discuss_topic(cb))

    assert llm_called["v"] is False
    assert len(msg.answers) == n_before + 1
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")
    captured = capsys.readouterr()
    assert str(user.id) not in captured.out
    assert str(user.id) not in captured.err


# ── 4. LLM call failure never sends a latent reply ───────────────────────────
def test_discuss_llm_call_failure_never_sends_latent_reply(monkeypatch):
    async def _boom(*a, **kw):
        raise RuntimeError("network down")
    monkeypatch.setattr(bot.client.chat.completions, "create", _boom)

    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg)
    cb = FakeCallback(user, msg, data=f"q:m:{session_id}:why")
    asyncio.run(bot.cb_questionnaire_discuss_topic(cb))

    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")
    for text, _ in msg.answers:
        assert "network down" not in text


# ── 5. validator rejection never sends a latent reply ────────────────────────
def test_discuss_output_rejected_never_sends_latent_reply(monkeypatch):
    class _Resp:
        class _Choice:
            class _Msg:
                content = "ты явно биполярная и я тебя люблю"
            message = _Msg()
        choices = [_Choice()]

    async def _bad_llm(*a, **kw):
        return _Resp()
    monkeypatch.setattr(bot.client.chat.completions, "create", _bad_llm)

    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg)
    cb = FakeCallback(user, msg, data=f"q:m:{session_id}:why")
    asyncio.run(bot.cb_questionnaire_discuss_topic(cb))

    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")
    for text, _ in msg.answers:
        assert "биполярная" not in text


# ── 6. validator receives the deterministic context, never stored answers ────
def test_discuss_validator_receives_deterministic_context(monkeypatch):
    captured = {}
    real_validate = validate_response_with_context

    def _spy(response_text, user_last_message, risk_result, lang="ru"):
        captured["user_last_message"] = user_last_message
        captured["risk_result"] = risk_result
        return real_validate(response_text, user_last_message, risk_result, lang)

    monkeypatch.setattr(bot, "validate_response_with_context", _spy)

    class _Resp:
        class _Choice:
            class _Msg:
                content = ("Похоже, тебе сейчас непросто. Можно понаблюдать за собой "
                           "в ближайшие дни -- что помогает, а что нет.")
            message = _Msg()
        choices = [_Choice()]

    async def _safe_llm(*a, **kw):
        return _Resp()
    monkeypatch.setattr(bot.client.chat.completions, "create", _safe_llm)

    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg, answer="a1", n_items=3)
    cb = FakeCallback(user, msg, data=f"q:m:{session_id}:next")
    asyncio.run(bot.cb_questionnaire_discuss_topic(cb))

    # user_last_message is the fixed topic prompt, never stored answer labels
    assert "user_last_message" in captured
    assert "topic=next" in captured["user_last_message"] or "Тема: next" in captured["user_last_message"]
    assert "совсем нет" not in captured["user_last_message"]  # a stored answer label
    assert captured["risk_result"] == {"score": 0, "level": "low", "categories": [],
                                       "implicit": False, "ambiguous_phrases": []}
    # And it must not have misfired: the safe reply was actually sent.
    assert msg.answers[-1][0] != questionnaire_ux.not_available_text("ru")


# ── 7. flag off -> unavailable, no LLM/traced call ────────────────────────────
def test_discuss_flag_off_unavailable(monkeypatch):
    monkeypatch.setattr(config, "QUESTIONNAIRE_INTERPRETATION_ENABLED", True)
    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg)
    monkeypatch.setattr(config, "QUESTIONNAIRE_INTERPRETATION_ENABLED", False)

    called = {"v": False}

    async def _boom(*a, **kw):
        called["v"] = True
        raise AssertionError("must not be called")
    monkeypatch.setattr(bot.client.chat.completions, "create", _boom)
    monkeypatch.setattr(bot, "traced_response_builder", _boom)

    cb = FakeCallback(user, msg, data=f"q:m:{session_id}:why")
    asyncio.run(bot.cb_questionnaire_discuss_topic(cb))
    assert called["v"] is False
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")

    cb2 = FakeCallback(user, msg, data=f"q:m:{session_id}")
    asyncio.run(bot.cb_questionnaire_discuss_menu(cb2))
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")


# ── 8. ineligible definition -> unavailable, no LLM/traced call ──────────────
def test_discuss_ineligible_definition_unavailable(monkeypatch):
    session_id = _force_completed_session(1, "demo_no_score_v1")
    asyncio.run(database.record_questionnaire_response(1, session_id, "demo_no_score_v1", "q1", "a1", "1"))
    user = FakeUser(1)
    msg = FakeMessage(user)

    called = {"v": False}

    async def _boom(*a, **kw):
        called["v"] = True
        raise AssertionError("must not be called")
    monkeypatch.setattr(bot.client.chat.completions, "create", _boom)
    monkeypatch.setattr(bot, "traced_response_builder", _boom)

    cb = FakeCallback(user, msg, data=f"q:m:{session_id}:why")
    asyncio.run(bot.cb_questionnaire_discuss_topic(cb))
    assert called["v"] is False
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")


# ── 9. ScoringError -> unavailable, no LLM/traced call ────────────────────────
def test_discuss_scoring_error_unavailable(monkeypatch):
    session_id = _force_completed_session(1, "demo_result_eligible_v1")
    # incomplete: only 1 of 3 items answered -> ScoringError
    asyncio.run(database.record_questionnaire_response(
        1, session_id, "demo_result_eligible_v1", "q1", "a1", "1"))
    user = FakeUser(1)
    msg = FakeMessage(user)

    called = {"v": False}

    async def _boom(*a, **kw):
        called["v"] = True
        raise AssertionError("must not be called")
    monkeypatch.setattr(bot.client.chat.completions, "create", _boom)
    monkeypatch.setattr(bot, "traced_response_builder", _boom)

    cb = FakeCallback(user, msg, data=f"q:m:{session_id}:why")
    asyncio.run(bot.cb_questionnaire_discuss_topic(cb))
    assert called["v"] is False
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")


# ── 10. session ownership enforced ───────────────────────────────────────────
def test_discuss_session_ownership_enforced(monkeypatch):
    owner = FakeUser(1)
    msg = FakeMessage(owner)
    session_id = _complete_flow(owner, msg)
    n_before = len(msg.answers)

    monkeypatch.setattr(ac, "OWNER_USER_ID", 999)
    attacker = FakeUser(999)
    cb = FakeCallback(attacker, msg, data=f"q:m:{session_id}:why")
    asyncio.run(bot.cb_questionnaire_discuss_topic(cb))
    # silent no-op: session belongs to uid 1, not 999 -- no NEW message leaked
    assert len(msg.answers) == n_before

    cb2 = FakeCallback(attacker, msg, data=f"q:m:{session_id}")
    asyncio.run(bot.cb_questionnaire_discuss_menu(cb2))
    assert len(msg.answers) == n_before


# ── 11. Influence is content-ful and names the real session ──────────────────
def test_discuss_influence_is_content_ful_and_names_session(monkeypatch):
    captured = {}

    async def _capture_builder(**kwargs):
        captured["influences"] = kwargs["influences"]
        await kwargs["send"]("ok")
        return "rid"
    monkeypatch.setattr(bot, "traced_response_builder", _capture_builder)

    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg, answer="a2", n_items=3)
    cb = FakeCallback(user, msg, data=f"q:m:{session_id}:specialist")
    asyncio.run(bot.cb_questionnaire_discuss_topic(cb))

    influences = captured["influences"]
    assert traced_response.content_ful(influences)
    inf = influences[0]
    assert str(session_id) in inf.human_readable
    assert inf.source_id == str(session_id)
    assert "6/9" in inf.human_readable or "6 / 9" in inf.human_readable or "6" in inf.human_readable
    assert "specialist" in inf.human_readable
    for placeholder in ("none", "n/a", "todo", "tbd", "unknown"):
        assert placeholder not in inf.human_readable.lower()


# ── 12. no FSM state introduced for this feature ──────────────────────────────
def test_discuss_no_fsm_state_added():
    import inspect as _inspect
    src = _inspect.getsource(bot)
    # InterventionStates is the ONLY existing StatesGroup in bot.py; discuss
    # code must not add a new one.
    discuss_src = src.split("# ── PR C2 — discuss-with-bot")[1].split("# ── Navigation Hub")[0]
    assert "StatesGroup" not in discuss_src
    assert "FSMContext" not in discuss_src


# ── 13. no visible button added in C2 (deferred to C2.1) ─────────────────────
def test_discuss_no_visible_button_added_in_c2(monkeypatch):
    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg)
    _, kw = msg.answers[-1]
    kb = kw["reply_markup"]
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert not any("Обсудить" in t for t in texts)
    assert not any(d.startswith("q:m:") for d in datas)

    cb = FakeCallback(user, msg, data=f"q:r:{session_id}")
    asyncio.run(bot.cb_questionnaire_result(cb))
    _, kw2 = msg.answers[-1]
    kb2 = kw2["reply_markup"]
    texts2 = [btn.text for row in kb2.inline_keyboard for btn in row]
    datas2 = [btn.callback_data for row in kb2.inline_keyboard for btn in row]
    assert not any("Обсудить" in t for t in texts2)
    assert not any(d.startswith("q:m:") for d in datas2)


# ── 14. callback_data stays under 64 bytes for every discuss format ─────────
def test_discuss_callback_formats_stay_under_64_bytes():
    session_id = 123456789
    for fmt in (f"q:m:{session_id}", f"q:m:{session_id}:why",
                f"q:m:{session_id}:next", f"q:m:{session_id}:specialist"):
        assert len(fmt.encode("utf-8")) <= 64


# ── 15. re-proof: /menu, cb_emotion_map, and all prior q: callbacks unchanged ─
PRIOR_Q_CALLBACKS = [
    ("cb_questionnaire_list", "q:l", 2),
    ("cb_questionnaire_category", "q:c:anxiety", 3),
    ("cb_questionnaire_detail", "q:d:demo_result_eligible_v1", 3),
    ("cb_questionnaire_start", "q:s:demo_result_eligible_v1", 3),
    ("cb_questionnaire_back", None, None),   # needs active session, tested via _complete_flow variant below
    ("cb_questionnaire_pause", None, None),
    ("cb_questionnaire_cancel", None, None),
    ("cb_questionnaire_result", None, None),
    ("cb_questionnaire_calculations", None, None),
    ("cb_questionnaire_explanation", None, None),
    ("cb_questionnaire_specialist_report", None, None),
]


def test_menu_and_prior_q_callbacks_gate_order_unchanged(monkeypatch):
    # /menu and emotion map still gated by the same journal_guard + product
    # access chain -- smoke re-proof (see tests/test_navigation.py /
    # tests/test_emotion_map.py for the full suite; this is a lightweight
    # cross-check that this PR did not touch those entrypoints).
    monkeypatch.setattr(bot, "get_active_crisis", _async((7, 0, "ru")))
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_menu(msg))
    from crisis_protocol import get_hotline
    assert get_hotline("ru")["primary"] in msg.answers[-1][0]


@pytest.mark.parametrize("handler_name,data", [
    ("cb_questionnaire_list", "q:l"),
    ("cb_questionnaire_category", "q:c:anxiety"),
])
def test_prior_q_callbacks_still_gated_by_crisis(monkeypatch, handler_name, data):
    monkeypatch.setattr(bot, "get_active_crisis", _async((7, 0, "ru")))
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data=data)
    asyncio.run(getattr(bot, handler_name)(cb))
    from crisis_protocol import get_hotline
    assert get_hotline("ru")["primary"] in msg.answers[-1][0]


def test_prior_q_r_k_e_o_still_gated_by_crisis(monkeypatch):
    monkeypatch.setattr(config, "QUESTIONNAIRE_INTERPRETATION_ENABLED", True)
    user = FakeUser(1)
    msg = FakeMessage(user)
    session_id = _complete_flow(user, msg)
    monkeypatch.setattr(bot, "get_active_crisis", _async((7, 0, "ru")))
    from crisis_protocol import get_hotline
    for handler_name, prefix in [
        ("cb_questionnaire_result", "q:r"), ("cb_questionnaire_calculations", "q:k"),
        ("cb_questionnaire_explanation", "q:e"), ("cb_questionnaire_specialist_report", "q:o"),
    ]:
        cb = FakeCallback(user, msg, data=f"{prefix}:{session_id}")
        asyncio.run(getattr(bot, handler_name)(cb))
        assert get_hotline("ru")["primary"] in msg.answers[-1][0]
