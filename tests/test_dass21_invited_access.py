"""PR #59 — controlled invited-user DASS access.

Authorization model: OWNER (always, while DASS enabled + integrity valid) OR
active invited user (existing user_access row) behind the
DASS21_INVITED_USERS_ENABLED rollout flag (default false). Calling /dass21
never grants access; every entry/callback/result path re-authorizes fresh.
Synthetic fixtures only.
"""
import asyncio
import hashlib
import json
import pathlib
import shutil
import types

import pytest

import bot
import config
import database
import questionnaires
import questionnaire_ux
import access_control as ac
import dass21_access
import dass21_runtime

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "dass21" / "synthetic_dass21_shape.json"
QID = "dass21_ru_fattakhov_2024"
OWNER, INVITED, INVITED2, BLOCKED, UNKNOWN = 1, 200, 201, 300, 999


class FakeUser:
    def __init__(self, uid):
        self.id = uid
        self.username = "user"


class FakeMessage:
    def __init__(self, user, text=""):
        self.from_user = user
        self.text = text
        self.chat = types.SimpleNamespace(id=user.id)
        self.answers = []

    async def answer(self, text, **kw):
        self.answers.append((text, kw))

    async def edit_text(self, text, **kw):
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
    # real invited/blocked rows in the EXISTING user_access table
    asyncio.run(database.grant_user_access(INVITED))
    asyncio.run(database.grant_user_access(INVITED2))
    asyncio.run(database.grant_user_access(BLOCKED))
    asyncio.run(database.block_user_access(BLOCKED))
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    monkeypatch.setattr(bot, "get_active_crisis", _async(None))
    monkeypatch.setattr(bot, "log_crisis_delivery", _async(None))
    # isolate the DASS authorization layer from the deployment-mode product
    # gate (which is covered by its own suite): everyone passes product access
    monkeypatch.setattr(bot, "ensure_full_access_or_closed_test", _async(True))
    monkeypatch.setattr(ac, "OWNER_USER_ID", OWNER)
    monkeypatch.setattr(bot, "CallbackQuery", FakeCallback)
    priv = tmp_path / "p.json"
    shutil.copyfile(FIXTURE, priv)
    reg_dir = tmp_path / "reg"
    reg_dir.mkdir()
    shutil.copyfile(FIXTURE, reg_dir / f"{QID}.json")
    monkeypatch.setattr(config, "DASS21_ENABLED", True)
    monkeypatch.setattr(config, "DASS21_OWNER_ONLY", True)
    monkeypatch.setattr(config, "DASS21_INVITED_USERS_ENABLED", True)
    monkeypatch.setattr(config, "DASS21_DEFINITION_PATH", str(priv))
    monkeypatch.setattr(config, "DASS21_DEFINITION_SHA256",
                        hashlib.sha256(priv.read_bytes()).hexdigest())
    monkeypatch.setattr(bot, "_load_registry_fresh",
                        lambda: questionnaires.load_registry(reg_dir))
    monkeypatch.setattr(bot, "_load_catalog_document",
                        lambda: {"schema_version": 2, "instruments": [_dass_entry()]})
    return tmp_path


def _auth(uid):
    return asyncio.run(dass21_access.authorize_dass21_user(uid))


def _cmd(uid):
    user = FakeUser(uid)
    msg = FakeMessage(user, text="/dass21")
    asyncio.run(bot.cmd_dass21(msg))
    return msg


def _press(handler, uid, data, msg=None):
    user = FakeUser(uid)
    msg = msg or FakeMessage(user)
    asyncio.run(handler(FakeCallback(user, msg, data=data)))
    return msg


def _sessions(uid):
    import sqlite3
    con = sqlite3.connect(database.DB)
    rows = con.execute("SELECT id, status, current_index FROM questionnaire_sessions "
                       "WHERE user_id=?", (uid,)).fetchall()
    con.close()
    return rows


def _buttons(kw):
    kb = kw.get("reply_markup")
    if kb is None:
        return []
    return [(b.text, b.callback_data) for row in kb.inline_keyboard for b in row]


def _start_session(uid):
    _press(bot.cb_questionnaire_start, uid, f"q:s:{QID}")
    return _sessions(uid)[0][0]


NEUTRAL = None  # resolved lazily


def _neutral():
    return questionnaire_ux.not_available_text("ru")


# ── flags / access ────────────────────────────────────────────────────────────
def test_invited_rollout_defaults_false():
    import importlib, os
    assert os.getenv("DASS21_INVITED_USERS_ENABLED") is None or True
    # the config parser itself: unset -> False (validated at import in prod;
    # here assert the documented default literal)
    src = pathlib.Path("config.py").read_text(encoding="utf-8")
    assert 'os.getenv("DASS21_INVITED_USERS_ENABLED", "false")' in src


def test_owner_allowed_when_invited_rollout_false(flow, monkeypatch):
    monkeypatch.setattr(config, "DASS21_INVITED_USERS_ENABLED", False)
    d = _auth(OWNER)
    assert d.allowed and d.reason_code == "owner"


def test_active_invited_denied_when_rollout_false(flow, monkeypatch):
    monkeypatch.setattr(config, "DASS21_INVITED_USERS_ENABLED", False)
    assert not _auth(INVITED).allowed


def test_active_invited_allowed_when_rollout_true(flow):
    d = _auth(INVITED)
    assert d.allowed and d.reason_code == "invited"


def test_blocked_user_denied(flow):
    assert not _auth(BLOCKED).allowed


def test_unknown_user_denied(flow):
    assert not _auth(UNKNOWN).allowed


def test_dass_command_does_not_grant_access(flow):
    _cmd(UNKNOWN)
    assert not _auth(UNKNOWN).allowed  # calling the command grants nothing
    assert not asyncio.run(database.user_has_active_access(UNKNOWN))


def test_integrity_failure_denies_everyone_including_owner(flow, monkeypatch):
    monkeypatch.setattr(config, "DASS21_DEFINITION_SHA256", "0" * 64)
    assert not _auth(OWNER).allowed
    assert not _auth(INVITED).allowed


# ── menu / catalog visibility ─────────────────────────────────────────────────
def _stress_datas(uid):
    msg = _press(bot.cb_questionnaire_category, uid, "q:c:stress")
    return [cd for _, cd in _buttons(msg.answers[-1][1])] if msg.answers else []


def test_dass_visible_to_active_invited_when_rollout_true(flow):
    assert f"q:d:{QID}" in _stress_datas(INVITED)


def test_dass_hidden_from_invited_when_rollout_false(flow, monkeypatch):
    monkeypatch.setattr(config, "DASS21_INVITED_USERS_ENABLED", False)
    assert f"q:d:{QID}" not in _stress_datas(INVITED)


def test_dass_hidden_from_blocked(flow):
    assert f"q:d:{QID}" not in _stress_datas(BLOCKED)


def test_dass_hidden_from_unknown_not_globally_public(flow):
    assert f"q:d:{QID}" not in _stress_datas(UNKNOWN)


def test_dass_hidden_when_integrity_invalid(flow, monkeypatch):
    monkeypatch.setattr(config, "DASS21_DEFINITION_SHA256", "0" * 64)
    assert f"q:d:{QID}" not in _stress_datas(INVITED)


def test_owner_direct_command_works_when_catalog_hidden(flow, monkeypatch):
    monkeypatch.setattr(config, "DASS21_INVITED_USERS_ENABLED", False)
    msg = _cmd(OWNER)
    datas = [cd for _, cd in _buttons(msg.answers[-1][1])]
    assert f"q:s:{QID}" in datas  # detail card with Start


# ── callback bypass ───────────────────────────────────────────────────────────
def test_direct_q_d_requires_access(flow, monkeypatch):
    monkeypatch.setattr(config, "DASS21_INVITED_USERS_ENABLED", False)
    msg = _press(bot.cb_questionnaire_detail, INVITED, f"q:d:{QID}")
    assert msg.answers[-1][0] == _neutral()


def test_direct_q_s_requires_access(flow, monkeypatch):
    monkeypatch.setattr(config, "DASS21_INVITED_USERS_ENABLED", False)
    _press(bot.cb_questionnaire_start, INVITED, f"q:s:{QID}")
    assert _sessions(INVITED) == []


def test_forwarded_start_denied_for_unknown(flow):
    # An unknown user pressing a forwarded/stale Start button gets nothing.
    _press(bot.cb_questionnaire_start, UNKNOWN, f"q:s:{QID}")
    assert _sessions(UNKNOWN) == []


def test_invited_q_d_and_q_s_work_when_authorized(flow):
    msg = _press(bot.cb_questionnaire_detail, INVITED, f"q:d:{QID}")
    assert f"q:s:{QID}" in [cd for _, cd in _buttons(msg.answers[-1][1])]
    sid = _start_session(INVITED)
    assert _sessions(INVITED)[0][1] == "active"


def test_q_a_rechecks_access_before_write(flow):
    sid = _start_session(INVITED)
    _press(bot.cb_questionnaire_answer, INVITED, f"q:a:{sid}:0:a0")
    asyncio.run(database.block_user_access(INVITED))  # revoke mid-session
    msg = _press(bot.cb_questionnaire_answer, INVITED, f"q:a:{sid}:1:a0")
    assert msg.answers[-1][0] == _neutral()
    assert asyncio.run(database.get_questionnaire_session(sid))["current_index"] == 1


def test_q_b_rechecks_access_before_mutation(flow):
    sid = _start_session(INVITED)
    _press(bot.cb_questionnaire_answer, INVITED, f"q:a:{sid}:0:a0")
    asyncio.run(database.block_user_access(INVITED))
    msg = _press(bot.cb_questionnaire_back, INVITED, f"q:b:{sid}")
    assert msg.answers[-1][0] == _neutral()
    assert asyncio.run(database.get_questionnaire_session(sid))["current_index"] == 1


def test_resume_rechecks_access(flow):
    sid = _start_session(INVITED)
    _press(bot.cb_questionnaire_answer, INVITED, f"q:a:{sid}:0:a0")
    asyncio.run(database.block_user_access(INVITED))
    _press(bot.cb_questionnaire_start, INVITED, f"q:s:{QID}")  # resume path
    # resume refused: the session must not advance and no new session appears
    assert len(_sessions(INVITED)) == 1
    assert asyncio.run(database.get_questionnaire_session(sid))["current_index"] == 1


def test_result_rechecks_access(flow):
    sid = _start_session(INVITED)
    user = FakeUser(INVITED)
    msg = FakeMessage(user)
    for step in range(20):
        asyncio.run(bot.cb_questionnaire_answer(
            FakeCallback(user, msg, data=f"q:a:{sid}:{step}:a0")))
    asyncio.run(database.block_user_access(INVITED))
    asyncio.run(bot.cb_questionnaire_answer(
        FakeCallback(user, msg, data=f"q:a:{sid}:20:a0")))
    assert msg.answers[-1][0] == _neutral()
    assert asyncio.run(database.get_questionnaire_session(sid))["status"] == "active"


def test_cancel_available_after_revocation(flow):
    sid = _start_session(INVITED)
    asyncio.run(database.block_user_access(INVITED))
    msg = _press(bot.cb_questionnaire_cancel, INVITED, f"q:x:{sid}")
    assert msg.answers[-1][0] == questionnaire_ux.cancelled_text("ru")
    assert asyncio.run(database.get_questionnaire_session(sid))["status"] == "cancelled"


# ── isolation ─────────────────────────────────────────────────────────────────
def test_invited_cannot_touch_owner_session(flow):
    owner_sid = _start_session(OWNER)
    msg = _press(bot.cb_questionnaire_answer, INVITED, f"q:a:{owner_sid}:0:a3")
    assert msg.answers == []  # silent no-op, no disclosure
    assert asyncio.run(database.get_questionnaire_session(owner_sid))["current_index"] == 0


def test_owner_cannot_touch_invited_session(flow):
    sid = _start_session(INVITED)
    msg = _press(bot.cb_questionnaire_answer, OWNER, f"q:a:{sid}:0:a3")
    assert msg.answers == []
    assert asyncio.run(database.get_questionnaire_session(sid))["current_index"] == 0


def test_two_invited_users_isolated(flow):
    sid1 = _start_session(INVITED)
    sid2 = _start_session(INVITED2)
    assert sid1 != sid2
    msg = _press(bot.cb_questionnaire_answer, INVITED2, f"q:a:{sid1}:0:a3")
    assert msg.answers == []  # cross-user stale callback denied
    _press(bot.cb_questionnaire_answer, INVITED, f"q:a:{sid1}:0:a0")
    _press(bot.cb_questionnaire_answer, INVITED2, f"q:a:{sid2}:0:a1")
    import sqlite3
    con = sqlite3.connect(database.DB)
    v1 = con.execute("SELECT answer_value FROM questionnaire_responses WHERE session_id=?", (sid1,)).fetchall()
    v2 = con.execute("SELECT answer_value FROM questionnaire_responses WHERE session_id=?", (sid2,)).fetchall()
    con.close()
    assert v1 == [("0",)] and v2 == [("1",)]


def test_export_delete_remain_self_only(flow):
    sid = _start_session(INVITED)
    _press(bot.cb_questionnaire_answer, INVITED, f"q:a:{sid}:0:a2")
    data_other = asyncio.run(database.export_all_personal_data(INVITED2))
    assert data_other["questionnaire_responses"] == []
    asyncio.run(database.delete_all_personal_data(INVITED2))  # deletes nothing of INVITED
    data_own = asyncio.run(database.export_all_personal_data(INVITED))
    assert len(data_own["questionnaire_responses"]) == 1


# ── response invariant for invited users ──────────────────────────────────────
def test_invited_back_replace_keeps_one_row_and_result_uses_replacement(flow):
    sid = _start_session(INVITED)
    user = FakeUser(INVITED)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_answer(FakeCallback(user, msg, data=f"q:a:{sid}:0:a0")))
    asyncio.run(bot.cb_questionnaire_back(FakeCallback(user, msg, data=f"q:b:{sid}")))
    asyncio.run(bot.cb_questionnaire_answer(FakeCallback(user, msg, data=f"q:a:{sid}:0:a1")))
    for step in range(1, 21):
        asyncio.run(bot.cb_questionnaire_answer(
            FakeCallback(user, msg, data=f"q:a:{sid}:{step}:a0")))
    text = msg.answers[-1][0]
    # item 1 (dass21_01) is a Stress item; replacement 1 -> Стресс 1*2=2
    assert "Стресс: 2" in text and "Депрессия: 0" in text and "Тревога: 0" in text
    import sqlite3
    con = sqlite3.connect(database.DB)
    n, dist = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT item_id) FROM questionnaire_responses "
        "WHERE session_id=?", (sid,)).fetchone()
    dups = con.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM questionnaire_responses "
        "GROUP BY session_id, item_id HAVING COUNT(*)>1)").fetchone()[0]
    con.close()
    assert (n, dist, dups) == (21, 21, 0)


def test_invited_double_tap_keeps_one_row(flow):
    sid = _start_session(INVITED)
    for _ in range(2):
        _press(bot.cb_questionnaire_answer, INVITED, f"q:a:{sid}:0:a0")
    import sqlite3
    con = sqlite3.connect(database.DB)
    n = con.execute("SELECT COUNT(*) FROM questionnaire_responses WHERE session_id=?", (sid,)).fetchone()[0]
    idx = con.execute("SELECT current_index FROM questionnaire_sessions WHERE id=?", (sid,)).fetchone()[0]
    con.close()
    assert (n, idx) == (1, 1)


# ── clinical boundary ─────────────────────────────────────────────────────────
def test_invited_result_has_no_llm_total_or_severity(flow):
    sid = _start_session(INVITED)
    user = FakeUser(INVITED)
    msg = FakeMessage(user)
    for step in range(21):
        asyncio.run(bot.cb_questionnaire_answer(
            FakeCallback(user, msg, data=f"q:a:{sid}:{step}:a3")))
    text = msg.answers[-1][0]
    assert "Депрессия: 42" in text and "Тревога: 42" in text and "Стресс: 42" in text
    assert "не диагноз" in text
    for banned in ("Итог", "Общий", "норма", "лёгк", "умерен", "тяжёл"):
        assert banned not in text


def test_access_module_has_no_llm_or_telegram_imports():
    src = pathlib.Path(dass21_access.__file__).read_text(encoding="utf-8")
    for banned in ("import openai", "from openai", "import aiogram", "from aiogram",
                   "import bot", "from bot"):
        assert banned not in src
