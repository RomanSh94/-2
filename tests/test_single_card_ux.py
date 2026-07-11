"""PR #57 — single-card questionnaire UX.

One editable card per run (questions edit the same message instead of piling
up), SHORT numeric answer buttons (0/1/2/3), and the FULL answer wording as a
legend inside the card text. Synthetic fixtures only.
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

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "dass21" / "synthetic_dass21_shape.json"
QID = "dass21_ru_fattakhov_2024"


class FakeUser:
    def __init__(self, uid):
        self.id = uid
        self.username = "user"


class FakeCardMessage:
    """Editable fake: tracks edits separately from new messages."""

    def __init__(self, user):
        self.from_user = user
        self.chat = types.SimpleNamespace(id=user.id)
        self.answers = []      # NEW messages sent
        self.edits = []        # in-place edits of THIS message
        self.fail_edit = False

    async def answer(self, text, **kw):
        self.answers.append((text, kw))

    async def edit_text(self, text, **kw):
        if self.fail_edit:
            raise RuntimeError("message can't be edited")
        self.edits.append((text, kw))

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
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    monkeypatch.setattr(bot, "get_active_crisis", _async(None))
    monkeypatch.setattr(bot, "log_crisis_delivery", _async(None))
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})
    monkeypatch.setattr(bot, "CallbackQuery", FakeCallback)
    priv = tmp_path / "p.json"
    shutil.copyfile(FIXTURE, priv)
    reg_dir = tmp_path / "reg"
    reg_dir.mkdir()
    shutil.copyfile(FIXTURE, reg_dir / f"{QID}.json")
    monkeypatch.setattr(config, "DASS21_ENABLED", True)
    monkeypatch.setattr(config, "DASS21_OWNER_ONLY", True)
    monkeypatch.setattr(config, "DASS21_DEFINITION_PATH", str(priv))
    monkeypatch.setattr(config, "DASS21_DEFINITION_SHA256",
                        hashlib.sha256(priv.read_bytes()).hexdigest())
    monkeypatch.setattr(bot, "_load_registry_fresh",
                        lambda: questionnaires.load_registry(reg_dir))
    monkeypatch.setattr(bot, "_load_catalog_document",
                        lambda: {"schema_version": 2, "instruments": [_dass_entry()]})
    return reg_dir


def _card_buttons(kw):
    kb = kw.get("reply_markup")
    return [(b.text, b.callback_data) for row in kb.inline_keyboard for b in row]


def _start(user, msg):
    asyncio.run(bot.cb_questionnaire_start(FakeCallback(user, msg, data=f"q:s:{QID}")))


# ── short buttons + legend ────────────────────────────────────────────────────
def test_answer_buttons_are_short_numeric_values(flow):
    user = FakeUser(1)
    msg = FakeCardMessage(user)
    _start(user, msg)
    text, kw = msg.edits[-1]
    answer_buttons = [(t, cd) for t, cd in _card_buttons(kw) if cd.startswith("q:a:")]
    assert [t for t, _ in answer_buttons] == ["0", "1", "2", "3"]


def test_full_answer_wording_lives_in_card_text(flow):
    user = FakeUser(1)
    msg = FakeCardMessage(user)
    _start(user, msg)
    text, _ = msg.edits[-1]
    d = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for opt in d["items"][0]["options"]:
        # legend line "<value> — <full wording without duplicated prefix>"
        assert questionnaire_ux._legend_label(opt) in text
    assert "Выберите ответ" in text


def test_legend_label_strips_duplicated_value_prefix():
    assert questionnaire_ux._legend_label(
        {"value": "2", "label": "2 — часто"}) == "часто"
    assert questionnaire_ux._legend_label(
        {"value": "1", "label": "иногда"}) == "иногда"


def test_nonunique_values_fall_back_to_full_label_buttons(flow, tmp_path):
    # A definition whose option values collide must never render two identical
    # short buttons -- it falls back to one full-label button per row.
    d = json.loads(FIXTURE.read_text(encoding="utf-8"))
    kb = bot._questionnaire_item_keyboard(
        d, 1, 0,
        {"id": "x", "text": "t", "options": [
            {"id": "a0", "label": "первый", "value": "1"},
            {"id": "a1", "label": "второй", "value": "1"}]},
        "ru")
    texts = [b.text for row in kb.inline_keyboard for b in row if b.callback_data.startswith("q:a:")]
    assert texts == ["первый", "второй"]


# ── single editable card ──────────────────────────────────────────────────────
def test_questions_edit_the_same_card_no_accumulation(flow):
    user = FakeUser(1)
    msg = FakeCardMessage(user)
    _start(user, msg)
    session_id = None
    import sqlite3
    con = sqlite3.connect(database.DB)
    session_id = con.execute("SELECT id FROM questionnaire_sessions").fetchone()[0]
    con.close()
    for step in range(5):
        asyncio.run(bot.cb_questionnaire_answer(
            FakeCallback(user, msg, data=f"q:a:{session_id}:{step}:a0")))
    # question 1 + five advances all landed as EDITS of the same card;
    # not a single new message accumulated in the chat.
    assert len(msg.edits) == 6
    assert msg.answers == []
    assert "Вопрос 6 из 21" in msg.edits[-1][0]


def test_back_also_edits_in_place(flow):
    user = FakeUser(1)
    msg = FakeCardMessage(user)
    _start(user, msg)
    import sqlite3
    con = sqlite3.connect(database.DB)
    session_id = con.execute("SELECT id FROM questionnaire_sessions").fetchone()[0]
    con.close()
    asyncio.run(bot.cb_questionnaire_answer(
        FakeCallback(user, msg, data=f"q:a:{session_id}:0:a0")))
    before = len(msg.answers)
    asyncio.run(bot.cb_questionnaire_back(
        FakeCallback(user, msg, data=f"q:b:{session_id}")))
    assert len(msg.answers) == before          # no new message
    assert "Вопрос 1 из 21" in msg.edits[-1][0]  # back to item 1 in-place


def test_completion_result_edits_the_card(flow):
    user = FakeUser(1)
    msg = FakeCardMessage(user)
    _start(user, msg)
    import sqlite3
    con = sqlite3.connect(database.DB)
    session_id = con.execute("SELECT id FROM questionnaire_sessions").fetchone()[0]
    con.close()
    for step in range(21):
        asyncio.run(bot.cb_questionnaire_answer(
            FakeCallback(user, msg, data=f"q:a:{session_id}:{step}:a0")))
    assert "Депрессия: 0" in msg.edits[-1][0]
    assert msg.answers == []


def test_edit_failure_falls_back_to_new_message(flow):
    user = FakeUser(1)
    msg = FakeCardMessage(user)
    _start(user, msg)
    import sqlite3
    con = sqlite3.connect(database.DB)
    session_id = con.execute("SELECT id FROM questionnaire_sessions").fetchone()[0]
    con.close()
    msg.fail_edit = True  # e.g. message too old for edit_text
    asyncio.run(bot.cb_questionnaire_answer(
        FakeCallback(user, msg, data=f"q:a:{session_id}:0:a0")))
    assert msg.answers, "must fall back to a fresh message"
    assert "Вопрос 2 из 21" in msg.answers[-1][0]


def test_callback_format_unchanged(flow):
    user = FakeUser(1)
    msg = FakeCardMessage(user)
    _start(user, msg)
    _, kw = msg.edits[-1]
    datas = [cd for _, cd in _card_buttons(kw)]
    assert any(cd.startswith("q:a:") and cd.endswith(":a0") for cd in datas)
    assert any(cd.startswith("q:b:") for cd in datas)
    assert any(cd.startswith("q:x:") for cd in datas)
    assert all(len(cd.encode("utf-8")) <= 64 for cd in datas)
