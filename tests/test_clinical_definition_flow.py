"""Handler-level integration/regression for the clinical definition <-> manifest
linkage bridge, against the REAL bot.py handlers + a REAL tmp sqlite DB.

Follows tests/test_clinical_instrument_catalog_ux.py conventions:
monkeypatched bot._load_registry_fresh (synthetic clinical fixtures) and
bot._load_catalog_document (in-memory synthetic manifest). No real instrument
content, no scoring, no new DB tables. Fresh revalidation at catalog / q:d /
q:s / q:a is exercised by mutating the in-memory manifest mid-session.
"""
import asyncio
import json
import pathlib
import types

import pytest

import bot
import database
import questionnaires
import questionnaire_ux
import access_control as ac

CLINICAL_DIR = pathlib.Path(__file__).parent / "fixtures" / "clinical_definitions"
REGISTRY_DIR = pathlib.Path(__file__).parent / "fixtures" / "registry"


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


def _ready_entry(**over):
    entry = {
        "instrument_id": "synthetic_scale",
        "display_name_ru": "Синтетическая методика",
        "display_name_en": "Synthetic Instrument",
        "catalog_category_id": "anxiety",
        "abbreviation": "SYN",
        "version": "v1",
        "translation_id": "syn_ru_v1",
        "identity_status": "verified",
        "domain": "anxiety",
        "administration_mode": "self_report",
        "population": ["adult"],
        "activation_status": "ready",
        "questionnaire_definition_id": "synthetic_ready_v1",
        "scoring_contract_id": "synthetic_linear_total",
        "scoring_version": "1",
        "public_catalog_visible": True,
        "risk_item_metadata_status": "verified",
        "evidence": [{"kind": "primary_source", "title": "x", "url": None,
                      "accessed_at": "2026-07-10", "supports": ["identity"]}],
        "rights": {k: {"status": "allowed", "evidence": [
            {"kind": "license_terms", "title": "x", "url": None,
             "accessed_at": "2026-07-10", "supports": [k]}]}
            for k in ("digital_reproduction", "commercial_use", "translation_use")},
        "blockers": [],
    }
    entry.update(over)
    return entry


def _ready_manifest(**over):
    return {"schema_version": 2, "instruments": [_ready_entry(**over)]}


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    return database


@pytest.fixture
def flow(monkeypatch, tmp_db):
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    monkeypatch.setattr(bot, "get_active_crisis", _async(None))
    monkeypatch.setattr(bot, "log_crisis_delivery", _async(None))
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})
    monkeypatch.setattr(bot, "CallbackQuery", FakeCallback)

    holder = {"manifest": _ready_manifest(), "registry_dir": CLINICAL_DIR,
              "registry": None}

    def _reg():
        if holder["registry"] is not None:
            return holder["registry"]
        return questionnaires.load_registry(holder["registry_dir"])

    monkeypatch.setattr(bot, "_load_registry_fresh", _reg)
    monkeypatch.setattr(bot, "_load_catalog_document", lambda: holder["manifest"])
    return holder


def _sessions_for(uid):
    import sqlite3
    con = sqlite3.connect(database.DB)
    rows = con.execute(
        "SELECT id, questionnaire_id, questionnaire_version, status, current_index "
        "FROM questionnaire_sessions WHERE user_id=?", (uid,)).fetchall()
    con.close()
    return rows


def _responses(uid):
    return asyncio.run(database.export_all_personal_data(uid))["questionnaire_responses"]


def _buttons(kw):
    kb = kw["reply_markup"]
    return [(btn.text, btn.callback_data) for row in kb.inline_keyboard for btn in row]


def _press(handler, uid, data):
    user = FakeUser(uid)
    msg = FakeMessage(user)
    asyncio.run(handler(FakeCallback(user, msg, data=data)))
    return msg


def _start_session(uid="1_start"):
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_start(
        FakeCallback(user, msg, data="q:s:synthetic_ready_v1")))
    return msg


# ── catalog start-button visibility ──────────────────────────────────────────
def test_catalog_start_button_requires_combined_clinical_validation(flow):
    # ready manifest + valid registry -> the q:d start button appears.
    msg = _press(bot.cb_questionnaire_info, 1, "q:i:synthetic_scale")
    datas = [cd for _, cd in _buttons(msg.answers[-1][1])]
    assert "q:d:synthetic_ready_v1" in datas

    # Demote the DEFINITION (registry can_start False) while the manifest stays
    # ready -> combined gate closes the button, though the manifest is clear.
    reg = questionnaires.load_registry(CLINICAL_DIR)
    reg.by_id["synthetic_ready_v1"]["status"] = "draft"
    flow["registry"] = reg
    msg2 = _press(bot.cb_questionnaire_info, 1, "q:i:synthetic_scale")
    datas2 = [cd for _, cd in _buttons(msg2.answers[-1][1])]
    assert "q:d:synthetic_ready_v1" not in datas2
    assert _sessions_for(1) == []  # q:i never creates a session


def test_q_i_remains_read_only_with_synthetic_ready_item(flow):
    def _boom(*a, **kw):
        raise AssertionError("q:i must never start a session")
    import unittest.mock as m
    with m.patch.object(bot, "start_questionnaire_session", _boom), \
            m.patch.object(bot, "_send_questionnaire_step", _boom):
        msg = _press(bot.cb_questionnaire_info, 1, "q:i:synthetic_scale")
    datas = [cd for _, cd in _buttons(msg.answers[-1][1])]
    assert "q:d:synthetic_ready_v1" in datas
    assert "q:d:synthetic_scale" not in datas
    assert _sessions_for(1) == []


# ── q:d fresh recheck ────────────────────────────────────────────────────────
def test_q_d_rechecks_combined_validation(flow):
    msg = _press(bot.cb_questionnaire_detail, 1, "q:d:synthetic_ready_v1")
    datas = [cd for _, cd in _buttons(msg.answers[-1][1])]
    assert "q:s:synthetic_ready_v1" in datas

    flow["manifest"] = _ready_manifest(activation_status="blocked")
    msg2 = _press(bot.cb_questionnaire_detail, 1, "q:d:synthetic_ready_v1")
    assert msg2.answers[-1][0] == questionnaire_ux.not_available_text("ru")


# ── q:s fresh recheck ────────────────────────────────────────────────────────
def test_q_s_rechecks_combined_validation(flow):
    _start_session()
    assert len(_sessions_for(1)) == 1  # ready manifest -> session created


def test_definition_demoted_in_manifest_stops_fresh_start(flow):
    flow["manifest"] = _ready_manifest(activation_status="blocked")
    msg = _start_session()
    assert _sessions_for(1) == []
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")


def test_definition_version_changed_stops_fresh_start(flow):
    flow["manifest"] = _ready_manifest(version="v2")  # mismatch vs def meta v1
    _start_session()
    assert _sessions_for(1) == []


# ── q:a fresh recheck / mid-session invalidation ─────────────────────────────
def _start_and_answer_once(flow):
    _start_session()
    session_id = _sessions_for(1)[0][0]
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_answer(
        FakeCallback(user, msg, data=f"q:a:{session_id}:0:a1")))
    return session_id, msg


def test_q_a_rechecks_fresh_clinical_linkage_before_save(flow):
    session_id, _ = _start_and_answer_once(flow)
    assert len(_responses(1)) == 1  # first answer saved normally
    # Invalidate the linkage (manifest demoted) before the second answer.
    flow["manifest"] = _ready_manifest(activation_status="blocked")
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_answer(
        FakeCallback(user, msg, data=f"q:a:{session_id}:1:a1")))
    assert len(_responses(1)) == 1  # no new answer saved


def test_existing_active_session_fails_closed_if_definition_link_becomes_invalid(flow):
    session_id, _ = _start_and_answer_once(flow)
    flow["manifest"] = _ready_manifest(version="v2")  # link now INVALID
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_answer(
        FakeCallback(user, msg, data=f"q:a:{session_id}:1:a1")))
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")
    assert len(_responses(1)) == 1


def test_manifest_demoted_after_start_blocks_next_answer(flow):
    session_id, _ = _start_and_answer_once(flow)
    flow["manifest"] = _ready_manifest(activation_status="blocked")
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_answer(
        FakeCallback(user, msg, data=f"q:a:{session_id}:1:a1")))
    assert len(_responses(1)) == 1
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")


def test_mapping_changed_after_start_blocks_next_answer(flow):
    session_id, _ = _start_and_answer_once(flow)
    # Manifest now maps its ready entry to a DIFFERENT definition id -> the
    # clinical-metadata definition is no longer mapped -> INVALID.
    flow["manifest"] = _ready_manifest(questionnaire_definition_id="something_else_v1")
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_answer(
        FakeCallback(user, msg, data=f"q:a:{session_id}:1:a1")))
    assert len(_responses(1)) == 1


def test_translation_changed_after_start_blocks_next_answer(flow):
    session_id, _ = _start_and_answer_once(flow)
    flow["manifest"] = _ready_manifest(translation_id="syn_ru_v2")
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_answer(
        FakeCallback(user, msg, data=f"q:a:{session_id}:1:a1")))
    assert len(_responses(1)) == 1


def test_invalidated_session_saves_no_new_answer(flow):
    session_id, _ = _start_and_answer_once(flow)
    flow["manifest"] = _ready_manifest(activation_status="blocked")
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_answer(
        FakeCallback(user, msg, data=f"q:a:{session_id}:1:a1")))
    assert len(_responses(1)) == 1


def test_invalidated_session_does_not_advance(flow):
    session_id, _ = _start_and_answer_once(flow)
    idx_before = asyncio.run(database.get_questionnaire_session(session_id))["current_index"]
    flow["manifest"] = _ready_manifest(activation_status="blocked")
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_answer(
        FakeCallback(user, msg, data=f"q:a:{session_id}:1:a1")))
    session = asyncio.run(database.get_questionnaire_session(session_id))
    assert session["current_index"] == idx_before
    assert session["status"] == "active"


def test_neutral_unavailable_message_hides_internal_reason(flow):
    session_id, _ = _start_and_answer_once(flow)
    flow["manifest"] = _ready_manifest(version="v2")
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_answer(
        FakeCallback(user, msg, data=f"q:a:{session_id}:1:a1")))
    text = msg.answers[-1][0]
    assert text == questionnaire_ux.not_available_text("ru")
    for token in ("mismatch", "invalid", "blocked", "activation", "linkage",
                  "instrument-version"):
        assert token not in text.lower()


# ── ordinary nonclinical regressions ─────────────────────────────────────────
def test_self_observation_synthetic_flow_unchanged(flow, monkeypatch):
    # Point registry at the ordinary self-observation demos and use a manifest
    # that maps to none of them -> NOT_CLINICAL -> unchanged start behaviour.
    flow["registry_dir"] = REGISTRY_DIR
    flow["registry"] = None
    flow["manifest"] = _ready_manifest()  # maps only synthetic_ready_v1
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_start(
        FakeCallback(user, msg, data="q:s:demo_anxiety_v1")))
    assert _sessions_for(1)  # started via the unchanged nonclinical path


def test_questionnaire_result_and_discuss_flows_unchanged(flow, monkeypatch):
    # A full nonclinical flow still reaches the completion screen (result path
    # unaffected by the clinical bridge). Uses the ordinary demo registry.
    flow["registry_dir"] = REGISTRY_DIR
    flow["registry"] = None
    flow["manifest"] = _ready_manifest()
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cb_questionnaire_start(
        FakeCallback(user, msg, data="q:s:demo_anxiety_v1")))
    session_id = _sessions_for(1)[0][0]
    for step in range(5):
        asyncio.run(bot.cb_questionnaire_answer(
            FakeCallback(user, msg, data=f"q:a:{session_id}:{step}:a1")))
    assert msg.answers[-1][0] == questionnaire_ux.completion_text("ru")
    assert asyncio.run(database.get_questionnaire_session(session_id))["status"] == "completed"


# ── §4.3 q:b (Back) revalidates the combined linkage before moving index ──────
def test_q_b_rechecks_fresh_clinical_linkage(flow):
    # A live clinical session with a demoted manifest must not step Back.
    session_id, _ = _start_and_answer_once(flow)  # current_index -> 1
    flow["manifest"] = _ready_manifest(activation_status="blocked")
    msg = _press(bot.cb_questionnaire_back, 1, f"q:b:{session_id}")
    assert msg.answers[-1][0] == questionnaire_ux.not_available_text("ru")
    session = asyncio.run(database.get_questionnaire_session(session_id))
    assert session["status"] == "active"


def test_invalidated_clinical_session_back_does_not_change_index(flow):
    session_id, _ = _start_and_answer_once(flow)
    idx_before = asyncio.run(
        database.get_questionnaire_session(session_id))["current_index"]
    assert idx_before == 1
    flow["manifest"] = _ready_manifest(version="v2")  # link now INVALID
    _press(bot.cb_questionnaire_back, 1, f"q:b:{session_id}")
    idx_after = asyncio.run(
        database.get_questionnaire_session(session_id))["current_index"]
    assert idx_after == idx_before  # Back was refused, index unchanged


def test_cancel_remains_available_after_clinical_invalidation(flow):
    # Fail-closed must never trap the user: Cancel is always honoured even when
    # the clinical linkage has gone invalid mid-session.
    session_id, _ = _start_and_answer_once(flow)
    flow["manifest"] = _ready_manifest(activation_status="blocked")
    msg = _press(bot.cb_questionnaire_cancel, 1, f"q:x:{session_id}")
    assert msg.answers[-1][0] == questionnaire_ux.cancelled_text("ru")
    session = asyncio.run(database.get_questionnaire_session(session_id))
    assert session["status"] == "cancelled"


# ── §4.4 clinical-metadata definitions never surface in self_observation ──────
def _selfobs_registry_dir(tmp_path):
    """A tmp private-questionnaire registry with two ACTIVE selfobs-tagged
    definitions: one carrying clinical_instrument metadata (must be hidden from
    the self-observation surface) and one ordinary (must remain visible)."""
    d = tmp_path / "selfobs_registry"
    d.mkdir()
    # Clinical-metadata definition, deliberately (mis)tagged category=selfobs.
    clinical = json.loads(
        (CLINICAL_DIR / "synthetic_ready_v1.json").read_text(encoding="utf-8"))
    assert clinical["category"] == "selfobs"
    assert isinstance(clinical.get("clinical_instrument"), dict)
    (d / "synthetic_ready_v1.json").write_text(
        json.dumps(clinical), encoding="utf-8")
    # Ordinary nonclinical selfobs definition (no clinical_instrument).
    ordinary = json.loads(
        (REGISTRY_DIR / "demo_no_score_v1.json").read_text(encoding="utf-8"))
    ordinary["id"] = "ordinary_selfobs_v1"
    ordinary["category"] = "selfobs"
    ordinary.pop("clinical_instrument", None)
    (d / "ordinary_selfobs_v1.json").write_text(
        json.dumps(ordinary), encoding="utf-8")
    return d


def test_clinical_metadata_definition_hidden_from_self_observation_even_if_selfobs(
        flow, tmp_path):
    flow["registry_dir"] = _selfobs_registry_dir(tmp_path)
    flow["registry"] = None
    msg = _press(bot.cb_questionnaire_category, 1, "q:c:self_observation")
    datas = [cd for _, cd in _buttons(msg.answers[-1][1])]
    assert not any("synthetic_ready_v1" in cd for cd in datas)


def test_ordinary_synthetic_selfobs_definition_still_visible(flow, tmp_path):
    flow["registry_dir"] = _selfobs_registry_dir(tmp_path)
    flow["registry"] = None
    msg = _press(bot.cb_questionnaire_category, 1, "q:c:self_observation")
    datas = [cd for _, cd in _buttons(msg.answers[-1][1])]
    assert any("ordinary_selfobs_v1" in cd for cd in datas)
