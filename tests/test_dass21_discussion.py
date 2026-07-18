"""Workstream B — DASS-21 "discuss result" via the existing q:m:<session_id>
namespace. Handler-level tests against the REAL bot.py handlers + a REAL tmp
sqlite DB, following the exact fixture conventions of test_dass21_flow.py /
test_dass21_invited_access.py. Uses ONLY the synthetic shape fixture as the
private file -- no real item wording appears anywhere in this tracked file.

Note on scope: an earlier planning document referenced a test named
`test_dass21_completion_keyboard_missing_qm_button_tracked_gap` that was
supposed to be replaced here. A repo-wide grep (current tree + full git
history) found no such test ever existed under that name in this codebase --
there was, however, a REAL gap: the DASS-21 completion screen had no discuss
entry point at all (_send_dass21_result rendered via
_questionnaire_completion_keyboard, which never includes a q:m row). The
tests below close that real gap; none of them assert that missing behavior
is correct.
"""
import asyncio
import hashlib
import itertools
import pathlib
import shutil
import types

import pytest

import aiosqlite
import bot
import clinical_scoring
import config
import database
import questionnaires
import questionnaire_ux
import access_control as ac
import dass21_access
import discussion_adapters
import traced_response
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "dass21" / "synthetic_dass21_shape.json"
QID = "dass21_ru_fattakhov_2024"
OWNER, INVITED, UNKNOWN = 1, 200, 999
REAL_ITEM_TEXT = "Синтетическое placeholder-утверждение номер 1."

# Deterministic, distinct message_id per FakeMessage -- mirrors real Telegram,
# where every sent message gets its own id. A fresh FakeMessage() (a newly
# OPENED menu card) therefore always differs from a reused one (the SAME
# card, passed explicitly via _press(..., msg=existing_msg)).
_next_message_id = itertools.count(9000)


class FakeUser:
    def __init__(self, uid):
        self.id = uid
        self.username = "user"


class FakeMessage:
    def __init__(self, user, text=""):
        self.from_user = user
        self.text = text
        self.chat = types.SimpleNamespace(id=user.id)
        self.message_id = next(_next_message_id)
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

    async def answer(self, *a, **kw):
        pass


def _async(value=None):
    async def _f(*a, **kw):
        return value
    return _f


def _dass_entry():
    return {
        "instrument_id": "dass", "display_name_ru": "Шкала", "display_name_en": "Scale",
        "catalog_category_id": "stress", "abbreviation": "DASS", "version": "DASS-21",
        "translation_id": "fattakhov_ru_2024", "identity_status": "verified",
        "domain": "depression_anxiety_stress", "administration_mode": "self_report",
        "population": ["adult"], "activation_status": "ready",
        "questionnaire_definition_id": QID,
        "scoring_contract_id": "dass21_official_subscales",
        "scoring_version": "unsw_template_v1",
        "risk_contract_id": None, "risk_contract_version": None,
        "public_catalog_visible": False, "risk_item_metadata_status": "verified",
        "evidence": [{"kind": "official_publisher", "title": "x",
                      "url": "https://www2.psy.unsw.edu.au/dass/",
                      "accessed_at": "2026-07-11", "supports": ["identity"]}],
        "rights": {k: {"status": "allowed", "evidence": [
            {"kind": "license_terms", "title": "x",
             "url": "https://www2.psy.unsw.edu.au/dass/down.htm",
             "accessed_at": "2026-07-11", "supports": [k]}]}
            for k in ("digital_reproduction", "commercial_use", "translation_use")},
        "blockers": [],
    }


@pytest.fixture
def flow(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    asyncio.run(database.grant_user_access(INVITED))
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    monkeypatch.setattr(bot, "get_active_crisis", _async(None))
    monkeypatch.setattr(bot, "log_crisis_delivery", _async(None))
    monkeypatch.setattr(bot, "ensure_full_access_or_closed_test", _async(True))
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", OWNER)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})
    monkeypatch.setattr(bot, "CallbackQuery", FakeCallback)

    priv = tmp_path / "dass21_private.json"
    shutil.copyfile(FIXTURE, priv)
    reg_dir = tmp_path / "registry"
    reg_dir.mkdir()
    shutil.copyfile(FIXTURE, reg_dir / f"{QID}.json")
    monkeypatch.setattr(config, "DASS21_ENABLED", True)
    monkeypatch.setattr(config, "DASS21_OWNER_ONLY", True)
    monkeypatch.setattr(config, "DASS21_INVITED_USERS_ENABLED", True)
    monkeypatch.setattr(config, "DASS21_DEFINITION_PATH", str(priv))
    monkeypatch.setattr(config, "DASS21_DEFINITION_SHA256",
                        hashlib.sha256(priv.read_bytes()).hexdigest())
    monkeypatch.setattr(config, "DASS21_DISCUSSION_ENABLED", True)

    holder = {"manifest": {"schema_version": 2, "instruments": [_dass_entry()]}}
    monkeypatch.setattr(bot, "_load_registry_fresh",
                        lambda: questionnaires.load_registry(reg_dir))
    monkeypatch.setattr(bot, "_load_catalog_document", lambda: holder["manifest"])
    return holder


def _sessions_for(uid):
    import sqlite3
    con = sqlite3.connect(database.DB)
    rows = con.execute(
        "SELECT id, status FROM questionnaire_sessions WHERE user_id=?", (uid,)).fetchall()
    con.close()
    return rows


def _press(handler, uid, data, msg=None):
    user = FakeUser(uid)
    msg = msg or FakeMessage(user)
    asyncio.run(handler(FakeCallback(user, msg, data=data)))
    return msg


def _buttons(kw):
    kb = kw.get("reply_markup")
    if kb is None:
        return []
    return [(b.text, b.callback_data) for row in kb.inline_keyboard for b in row]


def _complete_dass(uid, answer="a1", n=21):
    """Starts a DASS-21 session for uid and answers `n` items (default all 21,
    each with the given answer value -> completed session). Returns
    (session_id, final FakeMessage)."""
    _press(bot.cb_questionnaire_start, uid, f"q:s:{QID}")
    session_id = _sessions_for(uid)[-1][0]
    user = FakeUser(uid)
    msg = FakeMessage(user)
    for step in range(n):
        asyncio.run(bot.cb_questionnaire_answer(
            FakeCallback(user, msg, data=f"q:a:{session_id}:{step}:{answer}")))
    return session_id, msg


# ── B3: visible discuss button, flag-gated ───────────────────────────────────
def test_discuss_button_absent_when_flag_off(flow, monkeypatch):
    monkeypatch.setattr(config, "DASS21_DISCUSSION_ENABLED", False)
    session_id, msg = _complete_dass(OWNER)
    datas = [cd for _, cd in _buttons(msg.answers[-1][1])]
    assert f"q:m:{session_id}" not in datas


def test_discuss_button_present_when_flag_on(flow):
    session_id, msg = _complete_dass(OWNER)
    datas = [cd for _, cd in _buttons(msg.answers[-1][1])]
    assert f"q:m:{session_id}" in datas
    labels_by_data = {cd: text for text, cd in _buttons(msg.answers[-1][1])}
    assert labels_by_data[f"q:m:{session_id}"] == "💬 Обсудить результат"


def test_discuss_button_en_label(flow, monkeypatch):
    monkeypatch.setattr(bot, "get_user_language", _async("en"))
    session_id, msg = _complete_dass(OWNER)
    labels_by_data = {cd: text for text, cd in _buttons(msg.answers[-1][1])}
    assert labels_by_data[f"q:m:{session_id}"] == "💬 Discuss the result"


# ── menu open ─────────────────────────────────────────────────────────────────
def test_discuss_menu_opens_for_completed_dass_session(flow):
    session_id, _ = _complete_dass(OWNER)
    msg = _press(bot.cb_questionnaire_discuss_menu, OWNER, f"q:m:{session_id}")
    assert msg.answers[-1][0] == questionnaire_ux.discuss_menu_text("ru")
    datas = [cd for _, cd in _buttons(msg.answers[-1][1])]
    assert datas == [f"q:m:{session_id}:measures", f"q:m:{session_id}:relate",
                      f"q:m:{session_id}:next", f"q:m:{session_id}:specialist",
                      f"q:r:{session_id}", "menu:back"]


def test_discuss_menu_uses_dass_safe_labels_not_causal_generic_ones(flow):
    session_id, _ = _complete_dass(OWNER)
    msg = _press(bot.cb_questionnaire_discuss_menu, OWNER, f"q:m:{session_id}")
    labels = [text for text, _ in _buttons(msg.answers[-1][1])]
    assert "Почему так вышло?" not in labels
    assert any("измеряют" in t for t in labels)
    assert any("связано с последней неделей" in t for t in labels)


def test_discuss_menu_double_tap_stable(flow):
    session_id, _ = _complete_dass(OWNER)
    msg1 = _press(bot.cb_questionnaire_discuss_menu, OWNER, f"q:m:{session_id}")
    msg2 = _press(bot.cb_questionnaire_discuss_menu, OWNER, f"q:m:{session_id}")
    assert msg1.answers[-1][0] == msg2.answers[-1][0] == questionnaire_ux.discuss_menu_text("ru")


def test_discuss_menu_blocked_when_flag_off(flow, monkeypatch):
    session_id, _ = _complete_dass(OWNER)
    monkeypatch.setattr(config, "DASS21_DISCUSSION_ENABLED", False)
    msg = _press(bot.cb_questionnaire_discuss_menu, OWNER, f"q:m:{session_id}")
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")


def test_discuss_menu_blocked_for_incomplete_session(flow):
    session_id, _ = _complete_dass(OWNER, n=5)  # active, not completed
    assert _sessions_for(OWNER)[-1][1] == "active"
    msg = _press(bot.cb_questionnaire_discuss_menu, OWNER, f"q:m:{session_id}")
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")


def test_discuss_menu_blocked_cross_user(flow):
    session_id, _ = _complete_dass(OWNER)
    msg = _press(bot.cb_questionnaire_discuss_menu, INVITED, f"q:m:{session_id}")
    assert msg.answers == []  # silent no-op, same non-disclosure convention


def test_discuss_menu_blocked_stale_callback_bad_session_id(flow):
    msg = _press(bot.cb_questionnaire_discuss_menu, OWNER, "q:m:999999")
    assert msg.answers == []


def test_discuss_menu_blocked_when_invited_access_revoked(flow):
    session_id, _ = _complete_dass(INVITED)
    asyncio.run(database.block_user_access(INVITED))
    msg = _press(bot.cb_questionnaire_discuss_menu, INVITED, f"q:m:{session_id}")
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")


def test_discuss_menu_blocked_when_integrity_fails(flow, monkeypatch):
    session_id, _ = _complete_dass(OWNER)
    monkeypatch.setattr(config, "DASS21_DEFINITION_SHA256", "0" * 64)
    msg = _press(bot.cb_questionnaire_discuss_menu, OWNER, f"q:m:{session_id}")
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")


def test_discuss_menu_blocked_when_scorer_fails(flow, monkeypatch):
    session_id, _ = _complete_dass(OWNER)

    def boom(*a, **kw):
        raise clinical_scoring.ClinicalScoringError("synthetic failure")
    monkeypatch.setattr(clinical_scoring, "score_validated_clinical_definition", boom)
    msg = _press(bot.cb_questionnaire_discuss_menu, OWNER, f"q:m:{session_id}")
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")


# ── topic reply: traced, minimized payload ───────────────────────────────────
def test_discuss_topic_uses_traced_response_builder(monkeypatch, flow):
    calls = []

    async def _fake_builder(**kwargs):
        calls.append(kwargs)
        await kwargs["send"]("TRACED-DASS-REPLY")
        return "rid-fake"
    monkeypatch.setattr(bot, "traced_response_builder", _fake_builder)

    session_id, _ = _complete_dass(OWNER, answer="a1")
    msg = _press(bot.cb_questionnaire_discuss_topic, OWNER, f"q:m:{session_id}:measures")

    assert len(calls) == 1
    assert calls[0]["user_id"] == OWNER
    assert calls[0]["requester_uid"] == OWNER
    assert msg.answers[-1][0] == "TRACED-DASS-REPLY"


def test_discuss_topic_influence_is_content_ful_and_names_session(monkeypatch, flow):
    captured = {}

    async def _capture_builder(**kwargs):
        captured["influences"] = kwargs["influences"]
        await kwargs["send"]("ok")
        return "rid"
    monkeypatch.setattr(bot, "traced_response_builder", _capture_builder)

    session_id, _ = _complete_dass(OWNER, answer="a1")  # each subscale 7*1*2=14
    _press(bot.cb_questionnaire_discuss_topic, OWNER, f"q:m:{session_id}:specialist")

    influences = captured["influences"]
    assert traced_response.content_ful(influences)
    inf = influences[0]
    assert str(session_id) in inf.human_readable
    assert "depression=14" in inf.human_readable
    assert "anxiety=14" in inf.human_readable
    assert "stress=14" in inf.human_readable
    assert "specialist" in inf.human_readable


def test_discuss_topic_llm_prompt_is_data_minimized(monkeypatch, flow):
    captured = {}

    class _Resp:
        class _Choice:
            class _Msg:
                content = "Это не диагноз. Давай посмотрим на результат спокойно."
            message = _Msg()
        choices = [_Choice()]

    async def _capture_llm(*a, **kw):
        captured["messages"] = kw["messages"]
        return _Resp()
    monkeypatch.setattr(bot.client.chat.completions, "create", _capture_llm)

    session_id, _ = _complete_dass(OWNER, answer="a1")
    msg = _press(bot.cb_questionnaire_discuss_topic, OWNER, f"q:m:{session_id}:measures")

    prompt = captured["messages"][-1]["content"]
    # allowed: subscale ints + instrument identity + topic
    assert "14" in prompt
    assert "DASS-21" in prompt
    assert "fattakhov_ru_2024" in prompt
    # forbidden: raw item text/wording, private item ids, an overall total,
    # a diagnosis claim
    assert REAL_ITEM_TEXT not in prompt
    assert "dass21_01" not in prompt
    assert "42" not in prompt  # would-be (14+14+14) total never appears
    assert "у тебя депрессия" not in prompt.lower()
    # the reply actually sent is the (safe) LLM output, not a fallback
    assert msg.answers[-1][0] == _Resp._Choice._Msg.content


def test_discuss_topic_output_rejected_falls_back_no_leak(monkeypatch, flow):
    class _Resp:
        class _Choice:
            class _Msg:
                content = "у тебя депрессия и я тебя люблю"
            message = _Msg()
        choices = [_Choice()]

    async def _bad_llm(*a, **kw):
        return _Resp()
    monkeypatch.setattr(bot.client.chat.completions, "create", _bad_llm)

    session_id, _ = _complete_dass(OWNER, answer="a1")
    msg = _press(bot.cb_questionnaire_discuss_topic, OWNER, f"q:m:{session_id}:measures")

    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")
    for text, _ in msg.answers:
        assert "депрессия и я тебя люблю" not in text


def test_discuss_topic_llm_failure_falls_back(monkeypatch, flow):
    async def _boom(*a, **kw):
        raise RuntimeError("network down")
    monkeypatch.setattr(bot.client.chat.completions, "create", _boom)

    session_id, _ = _complete_dass(OWNER, answer="a1")
    msg = _press(bot.cb_questionnaire_discuss_topic, OWNER, f"q:m:{session_id}:measures")

    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")
    for text, _ in msg.answers:
        assert "network down" not in text


def test_discuss_topic_llm_hard_timeout_one_fallback_no_late_send(monkeypatch, flow):
    # Deterministic timeout proof -- NO real 20s sleep. Shrinks the real
    # named bound (bot._DASS21_LLM_TIMEOUT_SECONDS) to a tiny value and lets
    # the fake LLM sleep a bit longer than THAT (still far under a second),
    # so the REAL asyncio.wait_for mechanism does the cancelling, not a
    # mocked timeout.
    monkeypatch.setattr(bot, "_DASS21_LLM_TIMEOUT_SECONDS", 0.05)

    async def _hangs(*a, **kw):
        await asyncio.sleep(0.3)
        raise AssertionError("must have been cancelled by wait_for before returning")
    monkeypatch.setattr(bot.client.chat.completions, "create", _hangs)

    session_id, _ = _complete_dass(OWNER, answer="a1")
    msg = _press(bot.cb_questionnaire_discuss_topic, OWNER, f"q:m:{session_id}:measures")

    assert msg.answers == [(questionnaire_ux.not_available_text("ru"), {})]  # exactly one fallback
    import sqlite3
    con = sqlite3.connect(database.DB)
    status = con.execute(
        "SELECT status FROM dass21_discuss_claims WHERE session_id=? AND topic_id='measures'",
        (session_id,)).fetchone()[0]
    con.close()
    # The neutral fallback IS a real claim-checked delivery (same design as
    # every other build failure -- see test_discuss_topic_trace_persist_
    # failure_falls_back_delivered_via_claim_path) -- ends 'delivered', not
    # stuck/retryable on this exact card; a NEW card can retry the topic.
    assert status == "delivered"


@pytest.mark.parametrize("bad_response", [
    None,
    types.SimpleNamespace(choices=None),
    types.SimpleNamespace(choices="not-a-list"),
    types.SimpleNamespace(choices=[]),
    types.SimpleNamespace(choices=[types.SimpleNamespace()]),  # choice with no .message
    types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace())]),  # no .content
    types.SimpleNamespace(choices=[types.SimpleNamespace(
        message=types.SimpleNamespace(content=None))]),
    types.SimpleNamespace(choices=[types.SimpleNamespace(
        message=types.SimpleNamespace(content=123))]),  # not a string
    types.SimpleNamespace(choices=[types.SimpleNamespace(
        message=types.SimpleNamespace(content="   "))]),  # whitespace only
])
def test_dass21_extract_llm_text_rejects_malformed_shapes(bad_response):
    with pytest.raises(bot.DiscussBuildFailed):
        bot._dass21_extract_llm_text(bad_response)


def test_dass21_extract_llm_text_accepts_valid_shape():
    resp = types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="ok text"))])
    assert bot._dass21_extract_llm_text(resp) == "ok text"


@pytest.mark.parametrize("exc_factory", [
    lambda: __import__("openai").APIConnectionError(request=__import__("httpx").Request("POST", "https://x")),
    lambda: __import__("openai").RateLimitError(
        message="rate limited", response=__import__("httpx").Response(
            429, request=__import__("httpx").Request("POST", "https://x")), body=None),
])
def test_discuss_topic_openai_operational_errors_fall_back(monkeypatch, flow, exc_factory):
    async def _boom(*a, **kw):
        raise exc_factory()
    monkeypatch.setattr(bot.client.chat.completions, "create", _boom)

    session_id, _ = _complete_dass(OWNER, answer="a1")
    msg = _press(bot.cb_questionnaire_discuss_topic, OWNER, f"q:m:{session_id}:measures")
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")


def test_discuss_topic_blocked_cross_user(flow, monkeypatch):
    async def _boom_llm(*a, **kw):
        raise AssertionError("LLM must not be reached for a cross-user callback")
    monkeypatch.setattr(bot.client.chat.completions, "create", _boom_llm)

    session_id, _ = _complete_dass(OWNER, answer="a1")
    msg = _press(bot.cb_questionnaire_discuss_topic, INVITED, f"q:m:{session_id}:measures")
    assert msg.answers == []


def test_discuss_topic_blocked_for_incomplete_session(flow, monkeypatch):
    async def _boom_llm(*a, **kw):
        raise AssertionError("LLM must not be reached before completion")
    monkeypatch.setattr(bot.client.chat.completions, "create", _boom_llm)

    session_id, _ = _complete_dass(OWNER, n=5)
    msg = _press(bot.cb_questionnaire_discuss_topic, OWNER, f"q:m:{session_id}:measures")
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")


def test_discuss_topic_blocked_when_access_revoked(flow, monkeypatch):
    async def _boom_llm(*a, **kw):
        raise AssertionError("LLM must not be reached once access is revoked")
    monkeypatch.setattr(bot.client.chat.completions, "create", _boom_llm)

    session_id, _ = _complete_dass(INVITED, answer="a1")
    asyncio.run(database.block_user_access(INVITED))
    msg = _press(bot.cb_questionnaire_discuss_topic, INVITED, f"q:m:{session_id}:measures")
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")


# ── never a second callback namespace ────────────────────────────────────────
def test_no_q_discuss_namespace_introduced():
    src = pathlib.Path(bot.__file__).read_text(encoding="utf-8")
    assert "q:discuss" not in src


def test_dass21_scorer_registry_never_exposes_total_or_severity(flow):
    # Structural guarantee: the recompute path used by discuss returns ONLY
    # the three subscales -- same object shape the completion screen uses.
    session_id, _ = _complete_dass(OWNER, answer="a3")  # each subscale 7*3*2=42
    adapter = discussion_adapters.Dass21DiscussionAdapter()
    registry_def = bot._load_registry_fresh().get(QID)
    session = asyncio.run(database.get_questionnaire_session(session_id))
    responses = asyncio.run(database.get_questionnaire_responses(session_id))
    result = adapter.recompute_result(registry_def, bot._load_catalog_document(), responses, session)
    assert result.subscales == {"depression": 42, "anxiety": 42, "stress": 42}
    assert not hasattr(result, "score") and not hasattr(result, "raw_total")  # no total field exists at all


# ── crisis preemption ─────────────────────────────────────────────────────────
def test_crisis_preempts_discuss_menu_no_dass_gate_reached(flow, monkeypatch):
    session_id, _ = _complete_dass(OWNER, answer="a1")
    monkeypatch.setattr(bot, "get_active_crisis", _async((1, 1, "ru")))
    crisis_calls = {"n": 0}

    async def _fake_send_crisis(*a, **kw):
        crisis_calls["n"] += 1
    monkeypatch.setattr(bot, "send_crisis", _fake_send_crisis)

    async def _boom_gate(*a, **kw):
        raise AssertionError("must not reach the DASS gate during active crisis")
    monkeypatch.setattr(bot, "_dass21_discuss_gate_and_load", _boom_gate)

    msg = _press(bot.cb_questionnaire_discuss_menu, OWNER, f"q:m:{session_id}")
    assert crisis_calls["n"] == 1
    assert msg.answers == []  # send_crisis is faked out; no real reply from this path


def test_crisis_preempts_discuss_topic_no_llm_no_trace(flow, monkeypatch):
    session_id, _ = _complete_dass(OWNER, answer="a1")
    monkeypatch.setattr(bot, "get_active_crisis", _async((1, 1, "ru")))
    monkeypatch.setattr(bot, "send_crisis", _async(None))

    async def _boom_llm(*a, **kw):
        raise AssertionError("LLM must not be called during active crisis")
    monkeypatch.setattr(bot.client.chat.completions, "create", _boom_llm)

    async def _boom_trace(*a, **kw):
        raise AssertionError("trace must not be persisted during active crisis")
    monkeypatch.setattr(bot, "persist_influence_trace", _boom_trace)

    msg = _press(bot.cb_questionnaire_discuss_topic, OWNER, f"q:m:{session_id}:measures")
    assert msg.answers == []


# ── trace persistence failure ─────────────────────────────────────────────────
def test_discuss_topic_trace_persist_failure_falls_back_delivered_via_claim_path(monkeypatch, flow):
    # The neutral fallback IS a real, successfully-delivered Telegram message
    # -- it goes through the SAME claim-checked delivery path as the real
    # answer (this pass's fix: no separate unchecked fallback send), so the
    # claim correctly ends 'delivered', not stuck/retryable on this exact
    # card. A retry now happens via a NEW menu card (see
    # test_old_card_stays_deduplicated_after_restart_new_card_allowed), which
    # is the same rule that already applies to a successful real answer.
    llm_calls = {"n": 0}

    class _Resp:
        class _Choice:
            class _Msg:
                content = "safe text"
            message = _Msg()
        choices = [_Choice()]

    async def _llm(*a, **kw):
        llm_calls["n"] += 1
        return _Resp()
    monkeypatch.setattr(bot.client.chat.completions, "create", _llm)

    async def _boom_persist(*a, **kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(bot, "persist_influence_trace", _boom_persist)

    session_id, _ = _complete_dass(OWNER, answer="a1")
    msg = _press(bot.cb_questionnaire_discuss_topic, OWNER, f"q:m:{session_id}:measures")

    assert llm_calls["n"] == 0  # persist_trace runs BEFORE build_response
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")
    import sqlite3
    con = sqlite3.connect(database.DB)
    status = con.execute(
        "SELECT status FROM dass21_discuss_claims WHERE session_id=? AND topic_id='measures'",
        (session_id,)).fetchone()[0]
    con.close()
    assert status == "delivered"


# ── A1 denial ─────────────────────────────────────────────────────────────────
def test_discuss_topic_a1_denied_falls_back_no_llm_no_uid_leak(monkeypatch, flow, capsys):
    async def _boom_llm(*a, **kw):
        raise AssertionError("LLM must not be called")
    monkeypatch.setattr(bot.client.chat.completions, "create", _boom_llm)

    async def _deny(requester_uid):
        raise ac.A1NotAllowed(f"A1 denied for role/mode of {requester_uid}")
    monkeypatch.setattr(ac, "assert_a1_allowed", _deny)

    session_id, _ = _complete_dass(OWNER, answer="a1")
    msg = _press(bot.cb_questionnaire_discuss_topic, OWNER, f"q:m:{session_id}:measures")

    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")
    captured = capsys.readouterr()
    assert str(OWNER) not in captured.out
    assert str(OWNER) not in captured.err


# ── idempotency: direct claim-table races + real double tap + retry ──────────
CHAT, MSGID = 555, 9001  # one fixed "card" identity for the direct-DB tests below


def test_claim_direct_second_call_denied_while_pending(flow):
    async def go():
        first = await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-1")
        second = await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-2")
        return first, second
    assert asyncio.run(go()) == (True, False)


def test_claim_new_card_same_topic_is_a_new_attempt(flow):
    """Reopening the menu -> a NEW source_message_id -> a fresh, independent
    claim, even though (user, session, topic) are identical -- this is the
    exact behavior that makes a topic reusable across menu re-opens instead
    of a permanent one-topic-per-session lock."""
    async def go():
        first = await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-1")
        second = await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID + 1, "rid-2")
        return first, second
    assert asyncio.run(go()) == (True, True)


def test_claim_denied_after_delivered(flow):
    async def go():
        await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-1")
        await database.transition_dass21_discuss_claim(
            OWNER, 1, "measures", CHAT, MSGID, "rid-1", "pending_before_send", "send_started")
        await database.transition_dass21_discuss_claim(
            OWNER, 1, "measures", CHAT, MSGID, "rid-1", "send_started", "delivered")
        return await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-2")
    assert asyncio.run(go()) is False


def test_claim_retry_allowed_after_failed_before_send(flow):
    async def go():
        await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-1")
        await database.transition_dass21_discuss_claim(
            OWNER, 1, "measures", CHAT, MSGID, "rid-1", "pending_before_send", "failed_before_send")
        return await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-2")
    assert asyncio.run(go()) is True


def test_claim_expired_pending_before_send_is_reclaimable(flow):
    async def go():
        await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-1")
        async with aiosqlite.connect(database.DB) as db:
            await db.execute(
                "UPDATE dass21_discuss_claims SET updated_at=datetime('now', '-300 seconds') "
                "WHERE user_id=? AND session_id=? AND topic_id=?", (OWNER, 1, "measures"))
            await db.commit()
        return await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-2")
    assert asyncio.run(go()) is True


def test_claim_fresh_pending_before_send_not_yet_expired_blocks(flow):
    async def go():
        first = await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-1")
        second = await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-2")
        return first, second
    assert asyncio.run(go()) == (True, False)


def test_claim_two_concurrent_reclaim_attempts_produce_one_winner(flow):
    async def go():
        await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-0")
        await database.transition_dass21_discuss_claim(
            OWNER, 1, "measures", CHAT, MSGID, "rid-0", "pending_before_send", "failed_before_send")
        first = await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-1")
        second = await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-2")
        return first, second
    assert asyncio.run(go()) == (True, False)


def test_send_started_is_not_auto_reclaimed(flow):
    async def go():
        await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-1")
        await database.transition_dass21_discuss_claim(
            OWNER, 1, "measures", CHAT, MSGID, "rid-1", "pending_before_send", "send_started")
        async with aiosqlite.connect(database.DB) as db:
            await db.execute(
                "UPDATE dass21_discuss_claims SET updated_at=datetime('now', '-3600 seconds') "
                "WHERE user_id=? AND session_id=? AND topic_id=?", (OWNER, 1, "measures"))
            await db.commit()
        return await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-2")
    assert asyncio.run(go()) is False


def test_delivery_uncertain_is_not_auto_reclaimed(flow):
    async def go():
        await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-1")
        await database.transition_dass21_discuss_claim(
            OWNER, 1, "measures", CHAT, MSGID, "rid-1", "pending_before_send", "send_started")
        await database.transition_dass21_discuss_claim(
            OWNER, 1, "measures", CHAT, MSGID, "rid-1", "send_started", "delivery_uncertain")
        async with aiosqlite.connect(database.DB) as db:
            await db.execute(
                "UPDATE dass21_discuss_claims SET updated_at=datetime('now', '-3600 seconds') "
                "WHERE user_id=? AND session_id=? AND topic_id=?", (OWNER, 1, "measures"))
            await db.commit()
        return await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-2")
    assert asyncio.run(go()) is False


def test_old_card_stays_deduplicated_after_restart_new_card_allowed(flow):
    """Simulates a process restart: no in-memory state, only the DB row
    survives. The OLD card (delivered) stays deduplicated; a NEW card
    (different source_message_id) is a legitimate new attempt."""
    async def go():
        await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-1")
        await database.transition_dass21_discuss_claim(
            OWNER, 1, "measures", CHAT, MSGID, "rid-1", "pending_before_send", "send_started")
        await database.transition_dass21_discuss_claim(
            OWNER, 1, "measures", CHAT, MSGID, "rid-1", "send_started", "delivered")
        old_card_retry = await database.claim_dass21_discuss_reply(
            OWNER, 1, "measures", CHAT, MSGID, "rid-2")
        new_card = await database.claim_dass21_discuss_reply(
            OWNER, 1, "measures", CHAT, MSGID + 7, "rid-3")
        return old_card_retry, new_card
    assert asyncio.run(go()) == (False, True)


# ── finalization bound to the exact winning claim token ──────────────────────
def test_transition_wrong_response_id_cannot_finalize(flow):
    async def go():
        await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-real")
        return await database.transition_dass21_discuss_claim(
            OWNER, 1, "measures", CHAT, MSGID, "rid-WRONG",
            "pending_before_send", "send_started")
    assert asyncio.run(go()) is False


def test_transition_stale_worker_cannot_finalize_a_reclaimed_claim(flow):
    """A worker holding rid-1 loses its claim (failed_before_send + a NEW
    claim rid-2 reclaims it) -- the stale rid-1 worker must not be able to
    transition the row rid-2 now owns."""
    async def go():
        await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-1")
        await database.transition_dass21_discuss_claim(
            OWNER, 1, "measures", CHAT, MSGID, "rid-1", "pending_before_send", "failed_before_send")
        await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-2")
        # stale worker, still holding rid-1, tries to finalize
        return await database.transition_dass21_discuss_claim(
            OWNER, 1, "measures", CHAT, MSGID, "rid-1", "pending_before_send", "send_started")
    assert asyncio.run(go()) is False


def test_transition_invalid_status_rejected(flow):
    async def go():
        await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-1")
        with pytest.raises(ValueError):
            await database.transition_dass21_discuss_claim(
                OWNER, 1, "measures", CHAT, MSGID, "rid-1",
                "pending_before_send", "bogus_status")
    asyncio.run(go())


def test_transition_invalid_transition_rejected(flow):
    async def go():
        await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-1")
        with pytest.raises(ValueError):
            await database.transition_dass21_discuss_claim(
                OWNER, 1, "measures", CHAT, MSGID, "rid-1",
                "pending_before_send", "delivered")  # must go through send_started
    asyncio.run(go())


def test_transition_delivered_cannot_become_failed(flow):
    async def go():
        await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-1")
        await database.transition_dass21_discuss_claim(
            OWNER, 1, "measures", CHAT, MSGID, "rid-1", "pending_before_send", "send_started")
        await database.transition_dass21_discuss_claim(
            OWNER, 1, "measures", CHAT, MSGID, "rid-1", "send_started", "delivered")
        with pytest.raises(ValueError):
            await database.transition_dass21_discuss_claim(
                OWNER, 1, "measures", CHAT, MSGID, "rid-1", "delivered", "failed_before_send")
    asyncio.run(go())


def test_transition_failed_before_send_cannot_become_delivered_without_new_claim(flow):
    async def go():
        await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-1")
        await database.transition_dass21_discuss_claim(
            OWNER, 1, "measures", CHAT, MSGID, "rid-1", "pending_before_send", "failed_before_send")
        with pytest.raises(ValueError):
            await database.transition_dass21_discuss_claim(
                OWNER, 1, "measures", CHAT, MSGID, "rid-1", "failed_before_send", "delivered")
    asyncio.run(go())


def test_transition_rowcount_verified_not_assumed(flow):
    async def go():
        await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-1")
        ok = await database.transition_dass21_discuss_claim(
            OWNER, 1, "measures", CHAT, MSGID, "rid-1", "pending_before_send", "send_started")
        assert ok is True
        # repeating the SAME transition a second time must report False (row
        # is no longer in the FROM state) -- callers must check the return
        # value, never assume success.
        repeated = await database.transition_dass21_discuss_claim(
            OWNER, 1, "measures", CHAT, MSGID, "rid-1", "pending_before_send", "send_started")
        assert repeated is False
    asyncio.run(go())


def test_claim_table_status_check_constraint_rejects_invalid_value(flow):
    async def go():
        async with aiosqlite.connect(database.DB) as db:
            with pytest.raises(aiosqlite.IntegrityError):
                await db.execute(
                    "INSERT INTO dass21_discuss_claims "
                    "(user_id, session_id, topic_id, source_chat_id, source_message_id, "
                    " status, response_id) VALUES (?, ?, ?, ?, ?, 'not_a_real_status', ?)",
                    (OWNER, 1, "measures", CHAT, MSGID, "rid-x"))
    asyncio.run(go())


def test_discuss_topic_double_tap_one_llm_call_one_reply(monkeypatch, flow):
    llm_calls = {"n": 0}

    class _Resp:
        class _Choice:
            class _Msg:
                content = "safe reply"
            message = _Msg()
        choices = [_Choice()]

    async def _llm(*a, **kw):
        llm_calls["n"] += 1
        return _Resp()
    monkeypatch.setattr(bot.client.chat.completions, "create", _llm)

    session_id, _ = _complete_dass(OWNER, answer="a1")
    user = FakeUser(OWNER)
    msg = FakeMessage(user)
    data = f"q:m:{session_id}:measures"
    asyncio.run(bot.cb_questionnaire_discuss_topic(FakeCallback(user, msg, data=data)))
    asyncio.run(bot.cb_questionnaire_discuss_topic(FakeCallback(user, msg, data=data)))

    assert llm_calls["n"] == 1
    assert sum(1 for text, _ in msg.answers if text == "safe reply") == 1


def test_discuss_topic_same_card_retry_after_build_failure_is_blocked(monkeypatch, flow):
    # Design change this pass: the neutral fallback after a build failure is
    # a real, claim-checked delivery (status ends 'delivered') -- so a
    # second tap on the SAME card is now correctly blocked, same as after a
    # successful real answer. There is no more same-card "retry after
    # transient failure"; see the next test for the actual retry path (a NEW
    # card).
    attempts = {"n": 0}

    class _Resp:
        class _Choice:
            class _Msg:
                content = "safe reply"
            message = _Msg()
        choices = [_Choice()]

    async def _llm(*a, **kw):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("transient failure")
        return _Resp()
    monkeypatch.setattr(bot.client.chat.completions, "create", _llm)

    session_id, _ = _complete_dass(OWNER, answer="a1")
    user = FakeUser(OWNER)
    msg = FakeMessage(user)
    data = f"q:m:{session_id}:measures"
    asyncio.run(bot.cb_questionnaire_discuss_topic(FakeCallback(user, msg, data=data)))
    asyncio.run(bot.cb_questionnaire_discuss_topic(FakeCallback(user, msg, data=data)))

    assert attempts["n"] == 1  # second tap on the SAME card never reaches the LLM
    assert msg.answers == [(questionnaire_ux.not_available_text("ru"), {})]


def test_discuss_topic_new_card_retry_after_build_failure_succeeds(monkeypatch, flow):
    attempts = {"n": 0}

    class _Resp:
        class _Choice:
            class _Msg:
                content = "safe reply"
            message = _Msg()
        choices = [_Choice()]

    async def _llm(*a, **kw):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("transient failure")
        return _Resp()
    monkeypatch.setattr(bot.client.chat.completions, "create", _llm)

    session_id, _ = _complete_dass(OWNER, answer="a1")
    user = FakeUser(OWNER)
    data = f"q:m:{session_id}:measures"
    msg1 = FakeMessage(user)  # first card
    asyncio.run(bot.cb_questionnaire_discuss_topic(FakeCallback(user, msg1, data=data)))
    msg2 = FakeMessage(user)  # a NEW card -- reopened menu, new message_id
    asyncio.run(bot.cb_questionnaire_discuss_topic(FakeCallback(user, msg2, data=data)))

    assert attempts["n"] == 2
    assert msg1.answers == [(questionnaire_ux.not_available_text("ru"), {})]
    assert msg2.answers == [("safe reply", {})]


# ── flag off completeness ─────────────────────────────────────────────────────
def test_direct_topic_blocked_when_flag_off(flow, monkeypatch):
    monkeypatch.setattr(config, "DASS21_DISCUSSION_ENABLED", False)
    session_id, _ = _complete_dass(OWNER, answer="a1")
    msg = _press(bot.cb_questionnaire_discuss_topic, OWNER, f"q:m:{session_id}:measures")
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")


def test_generic_discuss_gate_independent_of_dass_flag():
    import inspect
    src = inspect.getsource(bot._discuss_gate_and_load)
    assert "DASS21_DISCUSSION_ENABLED" not in src


# ── back to result (q:r) — DASS-aware, read-only ─────────────────────────────
def test_back_to_result_owner_succeeds_no_mutation(flow):
    session_id, _ = _complete_dass(OWNER, answer="a1")
    before = asyncio.run(database.get_questionnaire_session(session_id))
    msg = _press(bot.cb_questionnaire_result, OWNER, f"q:r:{session_id}")
    assert "Депрессия: 14" in msg.answers[-1][0]
    datas = [cd for _, cd in _buttons(msg.answers[-1][1])]
    assert f"q:m:{session_id}" in datas
    after = asyncio.run(database.get_questionnaire_session(session_id))
    assert after == before


def test_back_to_result_invited_succeeds(flow):
    session_id, _ = _complete_dass(INVITED, answer="a1")
    msg = _press(bot.cb_questionnaire_result, INVITED, f"q:r:{session_id}")
    assert "Депрессия: 14" in msg.answers[-1][0]


def test_back_to_result_blocked_when_access_revoked(flow):
    session_id, _ = _complete_dass(INVITED, answer="a1")
    asyncio.run(database.block_user_access(INVITED))
    msg = _press(bot.cb_questionnaire_result, INVITED, f"q:r:{session_id}")
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")


def test_back_to_result_blocked_cross_user(flow):
    session_id, _ = _complete_dass(OWNER, answer="a1")
    msg = _press(bot.cb_questionnaire_result, INVITED, f"q:r:{session_id}")
    assert msg.answers == []


def test_back_to_result_blocked_incomplete_session(flow):
    session_id, _ = _complete_dass(OWNER, n=5)
    msg = _press(bot.cb_questionnaire_result, OWNER, f"q:r:{session_id}")
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")


def test_back_to_result_blocked_integrity_failure(flow, monkeypatch):
    session_id, _ = _complete_dass(OWNER, answer="a1")
    monkeypatch.setattr(config, "DASS21_DEFINITION_SHA256", "0" * 64)
    msg = _press(bot.cb_questionnaire_result, OWNER, f"q:r:{session_id}")
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")


def test_back_to_result_blocked_scorer_failure(flow, monkeypatch):
    session_id, _ = _complete_dass(OWNER, answer="a1")

    def boom(*a, **kw):
        raise clinical_scoring.ClinicalScoringError("synthetic failure")
    monkeypatch.setattr(clinical_scoring, "score_validated_clinical_definition", boom)
    msg = _press(bot.cb_questionnaire_result, OWNER, f"q:r:{session_id}")
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")


def test_back_to_result_never_calls_llm(flow, monkeypatch):
    async def _boom_llm(*a, **kw):
        raise AssertionError("q:r must never call the LLM")
    monkeypatch.setattr(bot.client.chat.completions, "create", _boom_llm)
    session_id, _ = _complete_dass(OWNER, answer="a1")
    _press(bot.cb_questionnaire_result, OWNER, f"q:r:{session_id}")  # must not raise


def test_back_to_result_flag_off_uses_plain_keyboard(flow, monkeypatch):
    monkeypatch.setattr(config, "DASS21_DISCUSSION_ENABLED", False)
    session_id, _ = _complete_dass(OWNER, answer="a1")
    msg = _press(bot.cb_questionnaire_result, OWNER, f"q:r:{session_id}")
    datas = [cd for _, cd in _buttons(msg.answers[-1][1])]
    assert f"q:m:{session_id}" not in datas
    assert "Депрессия: 14" in msg.answers[-1][0]


# ── adapter-level malformed-row / malformed-manifest fail-closed handling ────
def _dass_session(status="completed"):
    return {"user_id": OWNER, "status": status}


def _valid_responses():
    return [{"item_id": f"dass21_{n:02d}", "answer_id": "a1", "answer_value": "1"}
            for n in range(1, 22)]


def test_adapter_rejects_missing_item_id_key(flow):
    responses = _valid_responses()
    del responses[0]["item_id"]
    adapter = discussion_adapters.Dass21DiscussionAdapter()
    definition = bot._load_registry_fresh().get(QID)
    assert adapter.recompute_result(
        definition, bot._load_catalog_document(), responses, _dass_session()) is None


def test_adapter_rejects_missing_answer_id_key(flow):
    responses = _valid_responses()
    del responses[0]["answer_id"]
    adapter = discussion_adapters.Dass21DiscussionAdapter()
    definition = bot._load_registry_fresh().get(QID)
    assert adapter.recompute_result(
        definition, bot._load_catalog_document(), responses, _dass_session()) is None


def test_adapter_rejects_missing_answer_value_key(flow):
    responses = _valid_responses()
    del responses[0]["answer_value"]
    adapter = discussion_adapters.Dass21DiscussionAdapter()
    definition = bot._load_registry_fresh().get(QID)
    assert adapter.recompute_result(
        definition, bot._load_catalog_document(), responses, _dass_session()) is None


def test_adapter_rejects_non_integer_answer_value(flow):
    responses = _valid_responses()
    responses[0]["answer_value"] = "not-a-number"
    adapter = discussion_adapters.Dass21DiscussionAdapter()
    definition = bot._load_registry_fresh().get(QID)
    assert adapter.recompute_result(
        definition, bot._load_catalog_document(), responses, _dass_session()) is None


def test_adapter_rejects_null_answer_value(flow):
    responses = _valid_responses()
    responses[0]["answer_value"] = None
    adapter = discussion_adapters.Dass21DiscussionAdapter()
    definition = bot._load_registry_fresh().get(QID)
    assert adapter.recompute_result(
        definition, bot._load_catalog_document(), responses, _dass_session()) is None


def test_adapter_rejects_duplicate_item(flow):
    responses = _valid_responses()
    responses[1] = dict(responses[0])  # duplicate item id, one item now missing
    adapter = discussion_adapters.Dass21DiscussionAdapter()
    definition = bot._load_registry_fresh().get(QID)
    assert adapter.recompute_result(
        definition, bot._load_catalog_document(), responses, _dass_session()) is None


def test_adapter_rejects_unknown_extra_item(flow):
    responses = _valid_responses()
    responses.append({"item_id": "dass21_99", "answer_id": "a1", "answer_value": "1"})
    adapter = discussion_adapters.Dass21DiscussionAdapter()
    definition = bot._load_registry_fresh().get(QID)
    assert adapter.recompute_result(
        definition, bot._load_catalog_document(), responses, _dass_session()) is None


def test_adapter_rejects_scorer_result_missing_subscale_key(flow, monkeypatch):
    class _FakeResult:
        subscales = {"depression": 1, "anxiety": 2}  # missing "stress"
    monkeypatch.setattr(clinical_scoring, "score_validated_clinical_definition",
                        lambda *a, **kw: _FakeResult())
    adapter = discussion_adapters.Dass21DiscussionAdapter()
    definition = bot._load_registry_fresh().get(QID)
    assert adapter.recompute_result(
        definition, bot._load_catalog_document(), _valid_responses(), _dass_session()) is None


def test_adapter_rejects_malformed_manifest_missing_instrument_entry(flow):
    adapter = discussion_adapters.Dass21DiscussionAdapter()
    definition = bot._load_registry_fresh().get(QID)
    broken_manifest = {"schema_version": 2, "instruments": []}
    assert adapter.recompute_result(
        definition, broken_manifest, _valid_responses(), _dass_session()) is None


def test_adapter_rejects_incomplete_session_status(flow):
    adapter = discussion_adapters.Dass21DiscussionAdapter()
    definition = bot._load_registry_fresh().get(QID)
    assert adapter.recompute_result(
        definition, bot._load_catalog_document(), _valid_responses(),
        _dass_session(status="active")) is None


# ── topic isolation: DASS and generic contracts are NOT interchangeable ──────
def test_dass_session_rejects_generic_only_topic_why(flow, monkeypatch):
    async def _boom_llm(*a, **kw):
        raise AssertionError("LLM must not be called for a cross-adapter topic")
    monkeypatch.setattr(bot.client.chat.completions, "create", _boom_llm)

    session_id, _ = _complete_dass(OWNER, answer="a1")
    msg = _press(bot.cb_questionnaire_discuss_topic, OWNER, f"q:m:{session_id}:why")
    assert msg.answers == []  # silent no-op, before gate/claim/trace/LLM
    import sqlite3
    con = sqlite3.connect(database.DB)
    n = con.execute("SELECT COUNT(*) FROM dass21_discuss_claims WHERE session_id=?",
                    (session_id,)).fetchone()[0]
    con.close()
    assert n == 0  # no claim row for a rejected topic


def test_dass_session_rejects_unknown_topic(flow):
    session_id, _ = _complete_dass(OWNER, answer="a1")
    # "unknown" is not in the syntax-level union at all, so the handler is
    # never even matched by aiogram's filter -- proven directly against the
    # filter function, the real routing boundary.
    assert not bot._is_discuss_topic_data(f"q:m:{session_id}:unknown")


def test_generic_session_rejects_dass_only_topic(tmp_path, monkeypatch):
    # A REAL generic (synthetic, non-DASS) completed session, tapped with a
    # DASS-only topic ("measures") -- must be rejected before any LLM call,
    # same as the reverse (DASS session + "why") above. Self-contained fixture
    # (own tmp DB), independent of the `flow` fixture used elsewhere in this
    # file, using the same synthetic registry fixture as
    # tests/test_questionnaire_discuss.py.
    monkeypatch.setattr(database, "DB", str(tmp_path / "generic.db"))
    asyncio.run(database.init_db())
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    monkeypatch.setattr(bot, "get_active_crisis", _async(None))
    monkeypatch.setattr(bot, "log_crisis_delivery", _async(None))
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", OWNER)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})
    monkeypatch.setattr(bot, "CallbackQuery", FakeCallback)
    generic_reg_dir = pathlib.Path(__file__).parent / "fixtures" / "registry"
    monkeypatch.setattr(bot, "_load_registry_fresh",
                        lambda: questionnaires.load_registry(generic_reg_dir))
    monkeypatch.setattr(config, "QUESTIONNAIRE_INTERPRETATION_ENABLED", True)

    async def _boom_llm(*a, **kw):
        raise AssertionError("LLM must not be called for a cross-adapter topic")
    monkeypatch.setattr(bot.client.chat.completions, "create", _boom_llm)

    user = FakeUser(OWNER)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_start(
        FakeCallback(user, msg, data="q:s:demo_result_eligible_v1")))
    import sqlite3
    con = sqlite3.connect(database.DB)
    session_id = con.execute(
        "SELECT id FROM questionnaire_sessions WHERE user_id=?", (OWNER,)).fetchone()[0]
    con.close()
    for step in range(3):
        asyncio.run(bot.cb_questionnaire_answer(
            FakeCallback(user, msg, data=f"q:a:{session_id}:{step}:a1")))

    msg2 = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_discuss_topic(
        FakeCallback(user, msg2, data=f"q:m:{session_id}:measures")))
    assert msg2.answers == []


def test_valid_dass_topics_all_accepted(flow, monkeypatch):
    class _Resp:
        class _Choice:
            class _Msg:
                content = "safe"
            message = _Msg()
        choices = [_Choice()]

    async def _llm(*a, **kw):
        return _Resp()
    monkeypatch.setattr(bot.client.chat.completions, "create", _llm)

    for topic in sorted(bot._DASS21_DISCUSS_TOPICS):
        session_id, _ = _complete_dass(OWNER, answer="a1")
        msg = _press(bot.cb_questionnaire_discuss_topic, OWNER, f"q:m:{session_id}:{topic}")
        assert msg.answers[-1][0] == "safe", topic


# ── Telegram send-failure handling ────────────────────────────────────────────
def test_send_failure_no_second_send_state_recorded_callback_answered(monkeypatch, flow, caplog):
    class _Resp:
        class _Choice:
            class _Msg:
                content = "safe reply"
            message = _Msg()
        choices = [_Choice()]

    async def _llm(*a, **kw):
        return _Resp()
    monkeypatch.setattr(bot.client.chat.completions, "create", _llm)

    session_id, _ = _complete_dass(OWNER, answer="a1")
    user = FakeUser(OWNER)
    msg = FakeMessage(user)
    send_calls = {"n": 0}

    async def _boom_answer(text, **kw):
        send_calls["n"] += 1
        raise TelegramNetworkError(method=None, message="network down")
    msg.answer = _boom_answer

    answered = {"n": 0}
    cb = FakeCallback(user, msg, data=f"q:m:{session_id}:measures")
    real_cb_answer = cb.answer

    async def _cb_answer(*a, **kw):
        answered["n"] += 1
        return await real_cb_answer(*a, **kw)
    cb.answer = _cb_answer

    with caplog.at_level("WARNING"):
        asyncio.run(bot.cb_questionnaire_discuss_topic(cb))

    assert send_calls["n"] == 1  # exactly one send attempt, no automatic retry
    assert answered["n"] == 1    # callback still answered where possible
    import sqlite3
    con = sqlite3.connect(database.DB)
    status = con.execute(
        "SELECT status FROM dass21_discuss_claims WHERE session_id=? AND topic_id='measures'",
        (session_id,)).fetchone()[0]
    con.close()
    assert status == "delivery_uncertain"

    # The failure IS logged (sanitized), but never with raw content.
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "dass21 discuss answer send failed" in joined
    assert "TelegramNetworkError" in joined
    for banned in ("safe reply", "depression=", "anxiety=", "stress=", "network down"):
        assert banned not in joined


def test_send_success_but_db_finalization_failure_is_uncertain_not_resent(monkeypatch, flow):
    """Telegram send succeeds, but marking the claim 'delivered' afterward
    fails (DB error). The message was NOT resent, and the row is left in
    'send_started' -- honestly uncertain (not silently upgraded to
    'delivered', not auto-retried) -- documented, best-effort reconciliation
    only, never claimed exact."""
    class _Resp:
        class _Choice:
            class _Msg:
                content = "safe reply"
            message = _Msg()
        choices = [_Choice()]

    async def _llm(*a, **kw):
        return _Resp()
    monkeypatch.setattr(bot.client.chat.completions, "create", _llm)

    real_transition = database.transition_dass21_discuss_claim
    calls = {"n": 0}

    async def _flaky_transition(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:  # the send_started -> delivered call specifically
            raise aiosqlite.Error("db down at finalize")
        return await real_transition(*a, **kw)
    monkeypatch.setattr(bot, "transition_dass21_discuss_claim", _flaky_transition)

    session_id, _ = _complete_dass(OWNER, answer="a1")
    msg = _press(bot.cb_questionnaire_discuss_topic, OWNER, f"q:m:{session_id}:measures")

    assert msg.answers[-1][0] == "safe reply"  # message WAS sent exactly once
    assert sum(1 for t, _ in msg.answers if t == "safe reply") == 1
    import sqlite3
    con = sqlite3.connect(database.DB)
    status = con.execute(
        "SELECT status FROM dass21_discuss_claims WHERE session_id=? AND topic_id='measures'",
        (session_id,)).fetchone()[0]
    con.close()
    assert status == "send_started"  # honestly uncertain, not falsely "delivered"


# ── fail-closed infrastructure loaders ────────────────────────────────────────
def test_registry_malformed_definition_file_fails_closed_not_crash(flow, monkeypatch, tmp_path):
    # REAL fail-closed proof, not a mocked crash: questionnaires.Registry._load
    # is documented to catch (json.JSONDecodeError, OSError) PER FILE and
    # DefinitionError for validation -- a bad file is silently excluded, the
    # registry never raises. This points _load_registry_fresh at an actual
    # directory containing only a malformed JSON file for the DASS
    # definition id, so registry.get(QID) is None and the handler fails
    # closed the same way it does for any other missing/invalid definition.
    session_id, _ = _complete_dass(OWNER, answer="a1")
    bad_reg_dir = tmp_path / "bad_registry"
    bad_reg_dir.mkdir()
    (bad_reg_dir / f"{QID}.json").write_text("{ not valid json !!!", encoding="utf-8")
    monkeypatch.setattr(bot, "_load_registry_fresh",
                        lambda: questionnaires.load_registry(bad_reg_dir))

    async def _boom_llm(*a, **kw):
        raise AssertionError("LLM must not be called against a malformed registry")
    monkeypatch.setattr(bot.client.chat.completions, "create", _boom_llm)

    msg = _press(bot.cb_questionnaire_discuss_topic, OWNER, f"q:m:{session_id}:measures")
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")


def test_registry_missing_directory_fails_closed_not_crash(flow, monkeypatch, tmp_path):
    # Registry._load: `if not self.directory.exists(): return` -- a missing
    # directory yields an EMPTY registry, never an exception.
    session_id, _ = _complete_dass(OWNER, answer="a1")
    missing_dir = tmp_path / "does_not_exist"
    monkeypatch.setattr(bot, "_load_registry_fresh",
                        lambda: questionnaires.load_registry(missing_dir))
    msg = _press(bot.cb_questionnaire_discuss_topic, OWNER, f"q:m:{session_id}:measures")
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")


def test_registry_directory_enumeration_oserror_fails_closed_topic(flow, monkeypatch):
    # Registry._load's directory-level enumeration (Path.exists/Path.glob) is
    # NOT covered by its own per-file try/except -- a real OSError there
    # (permission denied, a vanishing network mount) is caught at the DASS-
    # specific boundary (_dass21_recompute_result_or_none's `except
    # (aiosqlite.Error, OSError)`), not inside questionnaires.py.
    session_id, _ = _complete_dass(OWNER, answer="a1")

    def _boom():
        raise OSError("permission denied enumerating registry directory")
    monkeypatch.setattr(bot, "_load_registry_fresh", _boom)

    async def _boom_llm(*a, **kw):
        raise AssertionError("LLM must not be called on a registry enumeration failure")
    monkeypatch.setattr(bot.client.chat.completions, "create", _boom_llm)

    async def _boom_trace(*a, **kw):
        raise AssertionError("trace must not be persisted on a registry enumeration failure")
    monkeypatch.setattr(bot, "persist_influence_trace", _boom_trace)

    msg = _press(bot.cb_questionnaire_discuss_topic, OWNER, f"q:m:{session_id}:measures")
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")
    import sqlite3
    con = sqlite3.connect(database.DB)
    n = con.execute("SELECT COUNT(*) FROM dass21_discuss_claims WHERE session_id=?",
                    (session_id,)).fetchone()[0]
    con.close()
    assert n == 0  # no claim created before source validation completed


def test_registry_directory_enumeration_oserror_fails_closed_menu(flow, monkeypatch):
    session_id, _ = _complete_dass(OWNER, answer="a1")

    def _boom():
        raise OSError("permission denied")
    monkeypatch.setattr(bot, "_load_registry_fresh", _boom)
    msg = _press(bot.cb_questionnaire_discuss_menu, OWNER, f"q:m:{session_id}")
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")


def test_registry_directory_enumeration_oserror_fails_closed_back_to_result(flow, monkeypatch):
    session_id, _ = _complete_dass(OWNER, answer="a1")

    def _boom():
        raise OSError("permission denied")
    monkeypatch.setattr(bot, "_load_registry_fresh", _boom)
    msg = _press(bot.cb_questionnaire_result, OWNER, f"q:r:{session_id}")
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")


def test_loader_failure_catalog_document_already_fails_closed(monkeypatch):
    # _load_catalog_document already catches its OWN expected exception
    # (clinical_instrument_catalog.InstrumentManifestError) and returns None --
    # proving the EXISTING contract, not adding a new one. Deliberately does
    # NOT use the `flow` fixture, which replaces _load_catalog_document with
    # a lambda -- this test targets the REAL function.
    import clinical_instrument_catalog as cat

    def _boom(path):
        raise cat.InstrumentManifestError("bad manifest")
    monkeypatch.setattr(cat, "load_instrument_manifest", _boom)
    assert bot._load_catalog_document() is None


def test_session_db_error_fails_closed_topic_not_crash(flow, monkeypatch):
    # _load_owned_session (shared by q:r/q:k/q:e/q:o and both q:m entry
    # points, generic and DASS) now catches aiosqlite.Error and treats it
    # identically to "not found" -- the handler must NOT crash, must not
    # call the LLM, and must degrade to the SAME silent no-op every other
    # ownership failure already uses.
    session_id, _ = _complete_dass(OWNER, answer="a1")

    async def _boom(session_id):
        raise aiosqlite.Error("db locked")
    monkeypatch.setattr(bot, "get_questionnaire_session", _boom)

    async def _boom_llm(*a, **kw):
        raise AssertionError("LLM must not be called on a session DB failure")
    monkeypatch.setattr(bot.client.chat.completions, "create", _boom_llm)

    msg = _press(bot.cb_questionnaire_discuss_topic, OWNER, f"q:m:{session_id}:measures")
    assert msg.answers == []  # silent no-op, same convention as an unowned session


def test_session_db_error_fails_closed_menu_not_crash(flow, monkeypatch):
    session_id, _ = _complete_dass(OWNER, answer="a1")

    async def _boom(session_id):
        raise aiosqlite.Error("db locked")
    monkeypatch.setattr(bot, "get_questionnaire_session", _boom)

    msg = _press(bot.cb_questionnaire_discuss_menu, OWNER, f"q:m:{session_id}")
    assert msg.answers == []


def test_session_db_error_fails_closed_back_to_result_not_crash(flow, monkeypatch):
    session_id, _ = _complete_dass(OWNER, answer="a1")

    async def _boom(session_id):
        raise aiosqlite.Error("db locked")
    monkeypatch.setattr(bot, "get_questionnaire_session", _boom)

    msg = _press(bot.cb_questionnaire_result, OWNER, f"q:r:{session_id}")
    assert msg.answers == []


def test_loader_failure_get_questionnaire_responses_fails_closed(flow, monkeypatch):
    session_id, _ = _complete_dass(OWNER, answer="a1")

    async def _boom(session_id):
        raise aiosqlite.Error("db locked")
    monkeypatch.setattr(bot, "get_questionnaire_responses", _boom)
    msg = _press(bot.cb_questionnaire_discuss_topic, OWNER, f"q:m:{session_id}:measures")
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")


def test_loader_failure_dass_authorization_db_read_fails_closed(flow, monkeypatch):
    # route through an invited (non-owner) user so authorize_dass21_user
    # actually reaches the DB read this test breaks
    session_id, _ = _complete_dass(INVITED, answer="a1")

    async def _boom(uid):
        raise aiosqlite.Error("db locked")
    monkeypatch.setattr(database, "user_has_active_access", _boom)
    msg = _press(bot.cb_questionnaire_discuss_topic, INVITED, f"q:m:{session_id}:measures")
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")


def test_loader_failure_claim_insert_fails_closed_no_llm(flow, monkeypatch):
    # Workstream B (this pass): a claim-insert DB failure now sends NO new
    # chat message at all (a repeated tap during a DB outage must not flood
    # the chat) -- only a bounded callback alert, using the existing neutral
    # copy, no internal detail.
    async def _boom_llm(*a, **kw):
        raise AssertionError("LLM must not be called if the claim insert itself fails")
    monkeypatch.setattr(bot.client.chat.completions, "create", _boom_llm)

    async def _boom_claim(*a, **kw):
        raise aiosqlite.Error("db locked")
    monkeypatch.setattr(bot, "claim_dass21_discuss_reply", _boom_claim)

    async def _boom_trace(*a, **kw):
        raise AssertionError("trace must not be persisted if the claim insert itself fails")
    monkeypatch.setattr(bot, "persist_influence_trace", _boom_trace)

    session_id, _ = _complete_dass(OWNER, answer="a1")
    user = FakeUser(OWNER)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data=f"q:m:{session_id}:measures")
    alerts = []
    real_answer = cb.answer

    async def _capture_answer(*a, **kw):
        alerts.append((a, kw))
        return await real_answer(*a, **kw)
    cb.answer = _capture_answer

    asyncio.run(bot.cb_questionnaire_discuss_topic(cb))

    assert msg.answers == []  # zero new chat messages
    assert len(alerts) == 1
    args, kwargs = alerts[0]
    assert args[0] == questionnaire_ux.not_available_text("ru")
    assert kwargs.get("show_alert") is True
    for banned in ("database", "sqlite", "SQLite", str(session_id)):
        assert banned not in args[0]

    # Repeated callback still does not flood the chat.
    cb2 = FakeCallback(user, msg, data=f"q:m:{session_id}:measures")
    asyncio.run(bot.cb_questionnaire_discuss_topic(cb2))
    assert msg.answers == []


def test_loader_failure_claim_finalization_is_best_effort_reply_still_sent(flow, monkeypatch):
    # A finalization (send_started -> delivered) failure must NOT stop the
    # already-earned reply from being sent -- it only affects future reclaim
    # bookkeeping. The FIRST transition (pending_before_send -> send_started,
    # the pre-send ownership check) must still succeed via the real
    # function, or nothing would be sent at all (that IS the section-3 fix:
    # a pre-send transition failure blocks the send). Only the second call
    # (finalization) is broken here.
    class _Resp:
        class _Choice:
            class _Msg:
                content = "safe reply"
            message = _Msg()
        choices = [_Choice()]

    async def _llm(*a, **kw):
        return _Resp()
    monkeypatch.setattr(bot.client.chat.completions, "create", _llm)

    real_transition = database.transition_dass21_discuss_claim
    calls = {"n": 0}

    async def _flaky_transition(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:  # the send_started -> delivered finalization call
            raise aiosqlite.Error("db locked")
        return await real_transition(*a, **kw)
    monkeypatch.setattr(bot, "transition_dass21_discuss_claim", _flaky_transition)

    session_id, _ = _complete_dass(OWNER, answer="a1")
    msg = _press(bot.cb_questionnaire_discuss_topic, OWNER, f"q:m:{session_id}:measures")
    assert msg.answers[-1][0] == "safe reply"
    assert calls["n"] == 2


# ── privacy: export / preview / delete-all / forget-all / isolation ─────────
def test_privacy_export_includes_current_user_claim(flow):
    async def go():
        await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-1")
        return await database.export_all_personal_data(OWNER)
    data = asyncio.run(go())
    rows = data["dass21_discuss_claims"]
    assert len(rows) == 1 and rows[0]["topic_id"] == "measures"


def test_privacy_export_excludes_other_user_claim(flow):
    async def go():
        await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-1")
        return await database.export_all_personal_data(INVITED)
    data = asyncio.run(go())
    assert data["dass21_discuss_claims"] == []


def test_privacy_delete_preview_reports_correct_count(flow):
    async def go():
        await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-1")
        await database.claim_dass21_discuss_reply(OWNER, 1, "next", CHAT, MSGID + 1, "rid-2")
        return await database.preview_delete_all_personal_data(OWNER)
    preview = asyncio.run(go())
    assert preview["dass21_discuss_claims"]["row_count"] == 2
    assert preview["dass21_discuss_claims"]["policy"] == "CASCADE_DELETE"


def test_privacy_delete_all_removes_current_user_claims(flow):
    async def go():
        await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-1")
        await database.delete_all_personal_data(OWNER)
        return await database.export_all_personal_data(OWNER)
    data = asyncio.run(go())
    assert data["dass21_discuss_claims"] == []


def test_privacy_delete_all_for_user_b_does_not_remove_user_a_claims(flow):
    async def go():
        await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-1")
        await database.delete_all_personal_data(INVITED)
        return await database.export_all_personal_data(OWNER)
    data = asyncio.run(go())
    assert len(data["dass21_discuss_claims"]) == 1


def test_privacy_forget_all_routes_to_the_same_registry_driven_delete(flow):
    # /forget_all's callback (cb_forget, callback_data "forget:*") delegates
    # to _handle_privacy_delete_callback, the SAME shared function
    # /privacy_delete_all's callback uses, which calls
    # delete_all_personal_data(uid) directly on confirm -- so proving
    # delete_all_personal_data cascades the claim table (done above) IS the
    # forget-all proof, not a separate mechanism. Verified at the source
    # level (real call sites, no vacuous fallback).
    import inspect
    assert "_handle_privacy_delete_callback" in inspect.getsource(bot.cb_forget)
    assert "delete_all_personal_data" in inspect.getsource(bot._handle_privacy_delete_callback)


def test_privacy_default_deny_scanner_reports_claim_table_registered(flow):
    import privacy_registry as pr
    offenders = pr.find_unregistered_sensitive_tables(database.SCHEMA)
    assert "dass21_discuss_claims" not in offenders


def test_privacy_claim_row_never_stores_sensitive_content(flow):
    async def go():
        await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-1")
        return await database.export_all_personal_data(OWNER)
    data = asyncio.run(go())
    row = data["dass21_discuss_claims"][0]
    assert set(row) == {"user_id", "session_id", "topic_id", "source_chat_id",
                        "source_message_id", "status", "response_id",
                        "created_at", "updated_at"}
    # no raw answer/item/prompt/LLM-response/subscale field of any kind
    for banned in ("depression", "anxiety", "stress", "answer", "prompt", "item_id"):
        assert banned not in str(row.values()).lower() or banned == "topic_id"


# ── schema / migration ────────────────────────────────────────────────────────
def test_schema_brand_new_db_has_claims_table(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "fresh.db"))
    asyncio.run(database.init_db())
    import sqlite3
    con = sqlite3.connect(database.DB)
    cols = [c[1] for c in con.execute("PRAGMA table_info(dass21_discuss_claims)").fetchall()]
    con.close()
    assert cols == ["user_id", "session_id", "topic_id", "source_chat_id",
                    "source_message_id", "status", "response_id",
                    "created_at", "updated_at"]


def test_schema_init_db_twice_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "twice.db"))
    asyncio.run(database.init_db())
    asyncio.run(database.init_db())  # must not raise, must not duplicate the table
    import sqlite3
    con = sqlite3.connect(database.DB)
    n = con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='dass21_discuss_claims'"
    ).fetchone()[0]
    con.close()
    assert n == 1


def test_schema_existing_rows_preserved_across_init_db(flow):
    asyncio.run(database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-1"))
    asyncio.run(database.init_db())  # re-run against the SAME (already-populated) DB
    import sqlite3
    con = sqlite3.connect(database.DB)
    n = con.execute("SELECT COUNT(*) FROM dass21_discuss_claims").fetchone()[0]
    con.close()
    assert n == 1


def test_schema_privacy_registry_remains_complete_after_new_table(flow):
    import privacy_registry as pr
    assert pr.find_unregistered_sensitive_tables(database.SCHEMA) == []


def test_schema_rejects_invalid_topic_id(flow):
    async def go():
        async with aiosqlite.connect(database.DB) as db:
            with pytest.raises(aiosqlite.IntegrityError):
                await db.execute(
                    "INSERT INTO dass21_discuss_claims "
                    "(user_id, session_id, topic_id, source_chat_id, source_message_id, "
                    " status, response_id) VALUES (?, ?, ?, ?, ?, 'pending_before_send', ?)",
                    (OWNER, 1, "not_a_real_topic", CHAT, MSGID, "rid-x"))
    asyncio.run(go())


def test_claim_function_rejects_invalid_topic_id(flow):
    async def go():
        with pytest.raises(ValueError):
            await database.claim_dass21_discuss_reply(
                OWNER, 1, "not_a_real_topic", CHAT, MSGID, "rid-x")
    asyncio.run(go())


# ── REAL concurrency: asyncio.gather + an asyncio.Event barrier ─────────────
# Sequential awaits are not concurrency. Every test below releases ALL
# competing coroutines from a single asyncio.Event at once, so which one's
# DB call the event loop actually runs first is not determined by source
# order -- proving the ATOMIC SQL guard (not test sequencing) is what
# produces exactly one winner.
async def _run_concurrently(*coro_factories):
    start = asyncio.Event()

    async def _wrapped(factory):
        await start.wait()
        return await factory()

    tasks = [asyncio.ensure_future(_wrapped(f)) for f in coro_factories]
    await asyncio.sleep(0)  # let every task reach `await start.wait()` first
    start.set()
    return await asyncio.gather(*tasks)


def test_real_concurrent_initial_claim_race_exactly_one_winner(flow):
    async def claim_a():
        return await database.claim_dass21_discuss_reply(
            OWNER, 1, "measures", CHAT, MSGID, "rid-A")

    async def claim_b():
        return await database.claim_dass21_discuss_reply(
            OWNER, 1, "measures", CHAT, MSGID, "rid-B")

    results = asyncio.run(_run_concurrently(claim_a, claim_b))
    assert sorted(results) == [False, True]
    import sqlite3
    con = sqlite3.connect(database.DB)
    row = con.execute(
        "SELECT response_id FROM dass21_discuss_claims WHERE user_id=? AND session_id=? "
        "AND topic_id=? AND source_chat_id=? AND source_message_id=?",
        (OWNER, 1, "measures", CHAT, MSGID)).fetchall()
    con.close()
    assert len(row) == 1  # exactly one row, exactly one owning response_id
    assert row[0][0] in ("rid-A", "rid-B")


def test_real_concurrent_same_card_handler_race_one_effective_delivery(flow, monkeypatch):
    llm_calls = {"n": 0}

    class _Resp:
        class _Choice:
            class _Msg:
                content = "safe reply"
            message = _Msg()
        choices = [_Choice()]

    async def _llm(*a, **kw):
        llm_calls["n"] += 1
        return _Resp()
    monkeypatch.setattr(bot.client.chat.completions, "create", _llm)

    session_id, _ = _complete_dass(OWNER, answer="a1")
    user = FakeUser(OWNER)
    msg = FakeMessage(user)  # the SAME card for both concurrent presses
    data = f"q:m:{session_id}:measures"

    async def press_a():
        await bot.cb_questionnaire_discuss_topic(FakeCallback(user, msg, data=data))

    async def press_b():
        await bot.cb_questionnaire_discuss_topic(FakeCallback(user, msg, data=data))

    asyncio.run(_run_concurrently(press_a, press_b))

    assert llm_calls["n"] == 1  # one effective claim -> one LLM build
    assert sum(1 for t, _ in msg.answers if t == "safe reply") == 1  # one Telegram send
    import sqlite3
    con = sqlite3.connect(database.DB)
    status = con.execute(
        "SELECT status FROM dass21_discuss_claims WHERE session_id=? AND topic_id='measures'",
        (session_id,)).fetchone()[0]
    con.close()
    assert status == "delivered"  # one final delivered state


def test_real_concurrent_stale_reclaim_race_exactly_one_winner(flow):
    async def setup():
        await database.claim_dass21_discuss_reply(
            OWNER, 1, "measures", CHAT, MSGID, "rid-0")
        async with aiosqlite.connect(database.DB) as db:
            await db.execute(
                "UPDATE dass21_discuss_claims SET updated_at=datetime('now', '-300 seconds') "
                "WHERE user_id=? AND session_id=? AND topic_id=?", (OWNER, 1, "measures"))
            await db.commit()
    asyncio.run(setup())

    async def reclaim_a():
        return await database.claim_dass21_discuss_reply(
            OWNER, 1, "measures", CHAT, MSGID, "rid-A")

    async def reclaim_b():
        return await database.claim_dass21_discuss_reply(
            OWNER, 1, "measures", CHAT, MSGID, "rid-B")

    results = asyncio.run(_run_concurrently(reclaim_a, reclaim_b))
    assert sorted(results) == [False, True]
    import sqlite3
    con = sqlite3.connect(database.DB)
    response_id = con.execute(
        "SELECT response_id FROM dass21_discuss_claims WHERE user_id=? AND session_id=? "
        "AND topic_id=?", (OWNER, 1, "measures")).fetchone()[0]
    con.close()
    assert response_id in ("rid-A", "rid-B")  # exactly one new response_id stored


def test_old_worker_cannot_send_after_being_reclaimed(flow):
    # NOT a "concurrent" test by name -- this scenario is inherently
    # sequential (A's lease expires, THEN B reclaims, THEN A resumes) --
    # proves the exact-token check in transition_dass21_discuss_claim, the
    # invariant real concurrency also depends on.
    async def go():
        await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-A")
        async with aiosqlite.connect(database.DB) as db:
            await db.execute(
                "UPDATE dass21_discuss_claims SET updated_at=datetime('now', '-300 seconds') "
                "WHERE user_id=? AND session_id=? AND topic_id=?", (OWNER, 1, "measures"))
            await db.commit()
        b_won = await database.claim_dass21_discuss_reply(OWNER, 1, "measures", CHAT, MSGID, "rid-B")
        # worker A, unaware it was reclaimed, now tries its pre-send transition
        a_can_send = await database.transition_dass21_discuss_claim(
            OWNER, 1, "measures", CHAT, MSGID, "rid-A", "pending_before_send", "send_started")
        b_can_send = await database.transition_dass21_discuss_claim(
            OWNER, 1, "measures", CHAT, MSGID, "rid-B", "pending_before_send", "send_started")
        return b_won, a_can_send, b_can_send
    b_won, a_can_send, b_can_send = asyncio.run(go())
    assert b_won is True
    assert a_can_send is False  # stale worker A can never send
    assert b_can_send is True   # only the reclaiming worker B may send


def test_real_concurrent_transition_vs_stale_token_race(flow):
    # Two concurrent transition attempts on the SAME row: one with the
    # correct (current) response_id, one with a stale/wrong one. SQLite
    # transaction ordering must not let the stale token win regardless of
    # which coroutine the event loop happens to run first.
    async def setup():
        return await database.claim_dass21_discuss_reply(
            OWNER, 1, "measures", CHAT, MSGID, "rid-correct")
    asyncio.run(setup())

    async def correct_transition():
        return await database.transition_dass21_discuss_claim(
            OWNER, 1, "measures", CHAT, MSGID, "rid-correct",
            "pending_before_send", "send_started")

    async def stale_transition():
        return await database.transition_dass21_discuss_claim(
            OWNER, 1, "measures", CHAT, MSGID, "rid-stale",
            "pending_before_send", "send_started")

    results = asyncio.run(_run_concurrently(correct_transition, stale_transition))
    # exactly one send owner: the correct token, regardless of scheduling
    assert sorted(results) == [False, True]
    import sqlite3
    con = sqlite3.connect(database.DB)
    owner_row = con.execute(
        "SELECT response_id, status FROM dass21_discuss_claims WHERE user_id=? AND session_id=? "
        "AND topic_id=?", (OWNER, 1, "measures")).fetchone()
    con.close()
    assert owner_row == ("rid-correct", "send_started")
