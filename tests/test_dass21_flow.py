"""Owner-only DASS-21 flow (PR #55) against the REAL bot.py handlers + a REAL
tmp sqlite DB. Uses ONLY the synthetic shape fixture as the private file — no
real item wording appears anywhere in tracked files."""
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
import dass21_runtime

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "dass21" / "synthetic_dass21_shape.json"
QID = "dass21_ru_fattakhov_2024"


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

    async def answer(self, *a, **kw):
        pass


def _async(value=None):
    async def _f(*a, **kw):
        return value
    return _f


def _dass_entry(**over):
    entry = {
        "instrument_id": "dass",
        "display_name_ru": "Шкала депрессии, тревоги и стресса",
        "display_name_en": "Depression Anxiety Stress Scales",
        "catalog_category_id": "stress",
        "abbreviation": "DASS",
        "version": "DASS-21",
        "translation_id": "fattakhov_ru_2024",
        "identity_status": "verified",
        "domain": "depression_anxiety_stress",
        "administration_mode": "self_report",
        "population": ["adult"],
        "activation_status": "ready",
        "questionnaire_definition_id": QID,
        "scoring_contract_id": "dass21_official_subscales",
        "scoring_version": "unsw_template_v1",
        "risk_contract_id": None,
        "risk_contract_version": None,
        "public_catalog_visible": False,
        "risk_item_metadata_status": "verified",
        "evidence": [{"kind": "official_publisher", "title": "UNSW DASS site",
                      "url": "https://www2.psy.unsw.edu.au/dass/",
                      "accessed_at": "2026-07-11", "supports": ["identity"]}],
        "rights": {k: {"status": "allowed", "evidence": [
            {"kind": "license_terms", "title": "UNSW down.htm",
             "url": "https://www2.psy.unsw.edu.au/dass/down.htm",
             "accessed_at": "2026-07-11", "supports": [k]}]}
            for k in ("digital_reproduction", "commercial_use", "translation_use")},
        "blockers": [],
    }
    entry.update(over)
    return entry


@pytest.fixture
def flow(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    monkeypatch.setattr(bot, "get_active_crisis", _async(None))
    monkeypatch.setattr(bot, "log_crisis_delivery", _async(None))
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})
    monkeypatch.setattr(bot, "CallbackQuery", FakeCallback)

    # private file: the synthetic fixture, hash-pinned, enabled, owner=1
    priv = tmp_path / "dass21_private.json"
    shutil.copyfile(FIXTURE, priv)
    reg_dir = tmp_path / "registry"
    reg_dir.mkdir()
    shutil.copyfile(FIXTURE, reg_dir / f"{QID}.json")
    monkeypatch.setattr(config, "DASS21_ENABLED", True)
    monkeypatch.setattr(config, "DASS21_OWNER_ONLY", True)
    monkeypatch.setattr(config, "DASS21_DEFINITION_PATH", str(priv))
    monkeypatch.setattr(config, "DASS21_DEFINITION_SHA256",
                        hashlib.sha256(priv.read_bytes()).hexdigest())

    holder = {"manifest": {"schema_version": 2, "instruments": [_dass_entry()]}}
    monkeypatch.setattr(bot, "_load_registry_fresh",
                        lambda: questionnaires.load_registry(reg_dir))
    monkeypatch.setattr(bot, "_load_catalog_document", lambda: holder["manifest"])
    return holder


def _sessions_for(uid):
    import sqlite3
    con = sqlite3.connect(database.DB)
    rows = con.execute(
        "SELECT id, status, current_index FROM questionnaire_sessions "
        "WHERE user_id=?", (uid,)).fetchall()
    con.close()
    return rows


def _cmd(uid):
    user = FakeUser(uid)
    msg = FakeMessage(user, text="/dass21")
    asyncio.run(bot.cmd_dass21(msg))
    return msg


def _press(handler, uid, data):
    user = FakeUser(uid)
    msg = FakeMessage(user)
    asyncio.run(handler(FakeCallback(user, msg, data=data)))
    return msg


def _buttons(kw):
    kb = kw.get("reply_markup")
    if kb is None:
        return []
    return [(b.text, b.callback_data) for row in kb.inline_keyboard for b in row]


# ── /dass21 command ───────────────────────────────────────────────────────────
def test_owner_command_routes_to_detail_not_direct_start(flow):
    msg = _cmd(1)
    datas = [cd for _, cd in _buttons(msg.answers[-1][1])]
    assert f"q:s:{QID}" in datas          # detail screen offers Start...
    assert _sessions_for(1) == []          # ...but no session was created


def test_disabled_command_neutral(flow, monkeypatch):
    monkeypatch.setattr(config, "DASS21_ENABLED", False)
    msg = _cmd(1)
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")


def test_non_owner_command_neutral_no_disclosure(flow, monkeypatch):
    # Bypass the deployment-mode access gate so the DASS owner-only gate
    # itself is what refuses -- with the SAME neutral text.
    monkeypatch.setattr(bot, "ensure_full_access_or_closed_test", _async(True))
    msg = _cmd(2)
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")
    for token in ("owner", "hash", "dass", "DASS"):
        assert token not in msg.answers[-1][0]


# ── q:d / q:s / q:a / q:b fresh gate ──────────────────────────────────────────
def test_owner_full_flow_completes_with_three_subscale_values(flow):
    _press(bot.cb_questionnaire_start, 1, f"q:s:{QID}")
    session_id = _sessions_for(1)[0][0]
    user = FakeUser(1)
    msg = FakeMessage(user)
    for step in range(21):
        asyncio.run(bot.cb_questionnaire_answer(
            FakeCallback(user, msg, data=f"q:a:{session_id}:{step}:a1")))
    text = msg.answers[-1][0]
    # 21 answers of value 1 -> each subscale 7 * 1 * 2 = 14
    assert "DASS-21" in text
    assert "Депрессия: 14" in text
    assert "Тревога: 14" in text
    assert "Стресс: 14" in text
    assert "не диагноз" in text
    # no overall total / severity wording
    for banned in ("Итог", "Общий", "норма", "лёгк", "умерен", "тяжёл"):
        assert banned not in text


def test_non_owner_q_d_and_q_s_blocked(flow, monkeypatch):
    monkeypatch.setattr(bot, "ensure_full_access_or_closed_test", _async(True))
    msg = _press(bot.cb_questionnaire_detail, 2, f"q:d:{QID}")
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")
    _press(bot.cb_questionnaire_start, 2, f"q:s:{QID}")
    assert _sessions_for(2) == []


def test_disabled_mid_session_blocks_q_a(flow, monkeypatch):
    _press(bot.cb_questionnaire_start, 1, f"q:s:{QID}")
    session_id = _sessions_for(1)[0][0]
    _press(bot.cb_questionnaire_answer, 1, f"q:a:{session_id}:0:a0")
    monkeypatch.setattr(config, "DASS21_ENABLED", False)
    msg = _press(bot.cb_questionnaire_answer, 1, f"q:a:{session_id}:1:a0")
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")
    assert asyncio.run(
        database.get_questionnaire_session(session_id))["current_index"] == 1


def test_hash_mismatch_mid_session_blocks_q_b(flow, monkeypatch):
    _press(bot.cb_questionnaire_start, 1, f"q:s:{QID}")
    session_id = _sessions_for(1)[0][0]
    _press(bot.cb_questionnaire_answer, 1, f"q:a:{session_id}:0:a0")
    monkeypatch.setattr(config, "DASS21_DEFINITION_SHA256", "0" * 64)
    msg = _press(bot.cb_questionnaire_back, 1, f"q:b:{session_id}")
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")
    assert asyncio.run(
        database.get_questionnaire_session(session_id))["current_index"] == 1


def test_cancel_remains_available_when_gate_fails(flow, monkeypatch):
    _press(bot.cb_questionnaire_start, 1, f"q:s:{QID}")
    session_id = _sessions_for(1)[0][0]
    monkeypatch.setattr(config, "DASS21_ENABLED", False)
    msg = _press(bot.cb_questionnaire_cancel, 1, f"q:x:{session_id}")
    assert msg.answers[-1][0] == questionnaire_ux.cancelled_text("ru")


def test_gate_failure_at_completion_no_partial_result(flow, monkeypatch):
    _press(bot.cb_questionnaire_start, 1, f"q:s:{QID}")
    session_id = _sessions_for(1)[0][0]
    user = FakeUser(1)
    msg = FakeMessage(user)
    for step in range(20):
        asyncio.run(bot.cb_questionnaire_answer(
            FakeCallback(user, msg, data=f"q:a:{session_id}:{step}:a1")))
    # Break the manifest linkage before the LAST answer completes the session:
    # the q:a combined gate itself must fail closed -- no score, no partial.
    flow["manifest"] = {"schema_version": 2,
                        "instruments": [_dass_entry(activation_status="blocked")]}
    asyncio.run(bot.cb_questionnaire_answer(
        FakeCallback(user, msg, data=f"q:a:{session_id}:20:a1")))
    text = msg.answers[-1][0]
    assert text == questionnaire_ux.not_available_text("ru")
    assert "Депрессия" not in text


# ── visibility / privacy ──────────────────────────────────────────────────────
def test_not_visible_in_self_observation(flow):
    msg = _press(bot.cb_questionnaire_category, 1, "q:c:self_observation")
    datas = [cd for _, cd in _buttons(msg.answers[-1][1])]
    assert not any(QID in cd for cd in datas)


def test_not_visible_in_public_catalog(flow):
    import clinical_instrument_catalog as cat
    ids = {ci.instrument_id
           for ci in cat.public_catalog_instruments(flow["manifest"])}
    assert "dass" not in ids  # public_catalog_visible=false


def test_cross_user_answer_blocked(flow):
    _press(bot.cb_questionnaire_start, 1, f"q:s:{QID}")
    session_id = _sessions_for(1)[0][0]
    # Even the owner id can't answer another user's session -- but here user 2
    # (non-owner) tries the owner's session: silent no-op, nothing stored.
    msg = _press(bot.cb_questionnaire_answer, 2, f"q:a:{session_id}:0:a0")
    assert msg.answers == []
    assert asyncio.run(
        database.get_questionnaire_session(session_id))["current_index"] == 0


def test_no_score_persisted(flow):
    _press(bot.cb_questionnaire_start, 1, f"q:s:{QID}")
    session_id = _sessions_for(1)[0][0]
    user = FakeUser(1)
    msg = FakeMessage(user)
    for step in range(21):
        asyncio.run(bot.cb_questionnaire_answer(
            FakeCallback(user, msg, data=f"q:a:{session_id}:{step}:a3")))
    assert "Депрессия: 42" in msg.answers[-1][0]
    # Only the 21 raw responses exist -- no computed score anywhere in the DB.
    import sqlite3
    con = sqlite3.connect(database.DB)
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    for t in tables:
        cols = [c[1] for c in con.execute(f"PRAGMA table_info({t})").fetchall()]
        for row in con.execute(f"SELECT * FROM {t}").fetchall():
            for col, val in zip(cols, row):
                assert val != 42 or t == "questionnaire_responses" and False, \
                    f"computed score persisted in {t}.{col}"
    con.close()


def test_real_dass_text_absent_from_tracked_files():
    # The needles are read from the LOCAL private file (never hardcoded here --
    # this test file is tracked, so a literal would itself leak real wording).
    # Skipped where the private file is not installed (e.g. CI).
    import subprocess
    repo = pathlib.Path(__file__).resolve().parents[1]
    private = repo / "private_questionnaires" / "dass21_ru_fattakhov_2024.json"
    if not private.exists():
        pytest.skip("private DASS-21 file not installed on this machine")
    real = json.loads(private.read_text(encoding="utf-8"))
    needles = [item["text"] for item in real["items"]]
    args = ["git", "grep", "-l", "-F"]
    for n in needles:
        args += ["-e", n]
    out = subprocess.run(args, capture_output=True, text=True, cwd=repo)
    assert out.stdout.strip() == ""  # no tracked file carries real item text


# ── A8 completion transaction ordering ────────────────────────────────────────
def test_dass21_runtime_invalidation_at_completion_does_not_false_complete_session(
        flow, monkeypatch):
    _press(bot.cb_questionnaire_start, 1, f"q:s:{QID}")
    session_id = _sessions_for(1)[0][0]
    user = FakeUser(1)
    msg = FakeMessage(user)
    for step in range(20):
        asyncio.run(bot.cb_questionnaire_answer(
            FakeCallback(user, msg, data=f"q:a:{session_id}:{step}:a1")))
    # Invalidate the runtime gate between the last q:a gate check and the
    # completion screen: hash pin flips right when scoring would run.
    real_gate = dass21_runtime.dass21_runtime_status
    calls = {"n": 0}
    def flaky_gate(uid):
        calls["n"] += 1
        if calls["n"] > 1:   # q:a gate passes, completion-gate call fails
            return dass21_runtime.Dass21RuntimeStatus(False, "hash-mismatch")
        return real_gate(uid)
    monkeypatch.setattr(bot.dass21_runtime, "dass21_runtime_status", flaky_gate)
    asyncio.run(bot.cb_questionnaire_answer(
        FakeCallback(user, msg, data=f"q:a:{session_id}:20:a1")))
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")
    session = asyncio.run(database.get_questionnaire_session(session_id))
    assert session["status"] == "active"   # NOT falsely completed
    assert session["current_index"] == 21  # answers preserved, recoverable


def test_dass21_scoring_failure_does_not_false_complete_session(flow, monkeypatch):
    _press(bot.cb_questionnaire_start, 1, f"q:s:{QID}")
    session_id = _sessions_for(1)[0][0]
    user = FakeUser(1)
    msg = FakeMessage(user)
    def boom(*a, **kw):
        raise bot.clinical_scoring.ClinicalScoringError("synthetic failure")
    monkeypatch.setattr(bot.clinical_scoring,
                        "score_validated_clinical_definition", boom)
    for step in range(21):
        asyncio.run(bot.cb_questionnaire_answer(
            FakeCallback(user, msg, data=f"q:a:{session_id}:{step}:a0")))
    text = msg.answers[-1][0]
    assert text == questionnaire_ux.not_available_text("ru")
    assert "Депрессия" not in text  # no partial result
    session = asyncio.run(database.get_questionnaire_session(session_id))
    assert session["status"] == "active"  # recoverable, not falsely completed
    # Cancel still works on the stuck-at-completion session.
    msg2 = _press(bot.cb_questionnaire_cancel, 1, f"q:x:{session_id}")
    assert msg2.answers[-1][0] == questionnaire_ux.cancelled_text("ru")


def test_successful_result_marks_session_completed_once(flow):
    _press(bot.cb_questionnaire_start, 1, f"q:s:{QID}")
    session_id = _sessions_for(1)[0][0]
    user = FakeUser(1)
    msg = FakeMessage(user)
    for step in range(21):
        asyncio.run(bot.cb_questionnaire_answer(
            FakeCallback(user, msg, data=f"q:a:{session_id}:{step}:a0")))
    assert "Депрессия: 0" in msg.answers[-1][0]
    session = asyncio.run(database.get_questionnaire_session(session_id))
    assert session["status"] == "completed"


def test_owner_recovers_result_after_transient_failure(flow, monkeypatch):
    # Failure at completion leaves the session active; resuming via q:s
    # retries the completion branch and now succeeds.
    _press(bot.cb_questionnaire_start, 1, f"q:s:{QID}")
    session_id = _sessions_for(1)[0][0]
    user = FakeUser(1)
    msg = FakeMessage(user)
    for step in range(20):
        asyncio.run(bot.cb_questionnaire_answer(
            FakeCallback(user, msg, data=f"q:a:{session_id}:{step}:a0")))
    monkeypatch.setattr(config, "DASS21_DEFINITION_SHA256", "0" * 64)
    asyncio.run(bot.cb_questionnaire_answer(
        FakeCallback(user, msg, data=f"q:a:{session_id}:20:a0")))
    assert asyncio.run(
        database.get_questionnaire_session(session_id))["status"] == "active"
    # restore the correct pin -> resume - the same owned session completes
    import hashlib as _h
    monkeypatch.setattr(config, "DASS21_DEFINITION_SHA256",
                        _h.sha256(pathlib.Path(
                            config.DASS21_DEFINITION_PATH).read_bytes()).hexdigest())
    # The q:a gate refused BEFORE saving answer 21, so resume re-shows item
    # 21; answering it now completes the same owned session with the result.
    msg2 = _press(bot.cb_questionnaire_start, 1, f"q:s:{QID}")
    assert "21" in msg2.answers[-1][0]  # back on the last question
    msg3 = _press(bot.cb_questionnaire_answer, 1, f"q:a:{session_id}:20:a0")
    assert "Депрессия: 0" in msg3.answers[-1][0]
    assert asyncio.run(
        database.get_questionnaire_session(session_id))["status"] == "completed"


# ── regression: Back → revise answer must not duplicate the response row ───────
def test_back_then_revise_answer_does_not_duplicate_and_completes(flow):
    # Reproduces the production finding (session s4: 4 responses / 3 distinct):
    # answering an item, going Back, and re-answering it used to INSERT a second
    # row for the same item. For DASS the exact scorer rejects duplicate items,
    # so such a session could never complete. After the fix the revised answer
    # REPLACES the old one: exactly one row per item, and the session completes
    # with the revised value reflected in the score.
    _press(bot.cb_questionnaire_start, 1, f"q:s:{QID}")
    session_id = _sessions_for(1)[0][0]
    user = FakeUser(1)
    msg = FakeMessage(user)
    for step in (0, 1, 2):
        asyncio.run(bot.cb_questionnaire_answer(
            FakeCallback(user, msg, data=f"q:a:{session_id}:{step}:a0")))
    asyncio.run(bot.cb_questionnaire_back(
        FakeCallback(user, msg, data=f"q:b:{session_id}")))          # idx 3 -> 2
    asyncio.run(bot.cb_questionnaire_answer(
        FakeCallback(user, msg, data=f"q:a:{session_id}:2:a1")))     # revise item 2
    for step in range(3, 21):
        asyncio.run(bot.cb_questionnaire_answer(
            FakeCallback(user, msg, data=f"q:a:{session_id}:{step}:a0")))
    session = asyncio.run(database.get_questionnaire_session(session_id))
    assert session["status"] == "completed"
    rows = asyncio.run(database.get_questionnaire_responses(session_id))
    assert len(rows) == 21                                # no duplicate row
    assert len({r["item_id"] for r in rows}) == 21        # one per item
    # step 2 == dass21_03 (a depression item); revised a1 (=1) -> depression 1*2=2
    text = msg.answers[-1][0]
    assert "Депрессия: 2" in text
    assert "Тревога: 0" in text and "Стресс: 0" in text


def test_answer_same_item_twice_keeps_latest_value(flow):
    _press(bot.cb_questionnaire_start, 1, f"q:s:{QID}")
    session_id = _sessions_for(1)[0][0]
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_answer(
        FakeCallback(user, msg, data=f"q:a:{session_id}:0:a2")))
    asyncio.run(bot.cb_questionnaire_back(
        FakeCallback(user, msg, data=f"q:b:{session_id}")))
    asyncio.run(bot.cb_questionnaire_answer(
        FakeCallback(user, msg, data=f"q:a:{session_id}:0:a0")))
    rows = asyncio.run(database.get_questionnaire_responses(session_id))
    item0 = [r for r in rows if r["item_id"] == "dass21_01"]
    assert len(item0) == 1 and item0[0]["answer_value"] == "0"  # latest wins
