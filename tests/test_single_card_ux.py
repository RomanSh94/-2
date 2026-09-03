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
        self.answers = []          # NEW messages sent
        self.edits = []            # in-place edits of THIS message
        self.edit_exc = None       # exception edit_text should raise
        self.markup_cleared = 0    # edit_reply_markup(None) calls

    async def answer(self, text, **kw):
        self.answers.append((text, kw))

    async def edit_text(self, text, **kw):
        if self.edit_exc is not None:
            raise self.edit_exc
        self.edits.append((text, kw))

    async def edit_reply_markup(self, **kw):
        self.markup_cleared += 1


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
    assert "Депрессия — 0" in msg.edits[-1][0]
    assert msg.answers == []


def _session_id():
    import sqlite3
    con = sqlite3.connect(database.DB)
    sid = con.execute("SELECT id FROM questionnaire_sessions").fetchone()[0]
    con.close()
    return sid


def _bad_request(text):
    from aiogram.exceptions import TelegramBadRequest
    return TelegramBadRequest(method=None, message=text)


def test_known_edit_failure_falls_back_to_new_message(flow):
    user = FakeUser(1)
    msg = FakeCardMessage(user)
    _start(user, msg)
    session_id = _session_id()
    msg.edit_exc = _bad_request("Bad Request: message can't be edited")
    asyncio.run(bot.cb_questionnaire_answer(
        FakeCallback(user, msg, data=f"q:a:{session_id}:0:a0")))
    assert msg.answers, "must fall back to a fresh message"
    assert "Вопрос 2 из 21" in msg.answers[-1][0]


def test_message_not_modified_does_not_send_duplicate(flow):
    user = FakeUser(1)
    msg = FakeCardMessage(user)
    _start(user, msg)
    session_id = _session_id()
    msg.edit_exc = _bad_request("Bad Request: message is not modified")
    asyncio.run(bot.cb_questionnaire_answer(
        FakeCallback(user, msg, data=f"q:a:{session_id}:0:a0")))
    assert msg.answers == []           # treated as success: no duplicate card
    import sqlite3
    con = sqlite3.connect(database.DB)
    idx = con.execute("SELECT current_index FROM questionnaire_sessions").fetchone()[0]
    con.close()
    assert idx == 1                    # DB still advanced (source of truth)


def test_unexpected_edit_exception_is_not_swallowed(flow):
    user = FakeUser(1)
    msg = FakeCardMessage(user)
    _start(user, msg)
    session_id = _session_id()
    msg.edit_exc = RuntimeError("programming error")
    with pytest.raises(RuntimeError):
        asyncio.run(bot.cb_questionnaire_answer(
            FakeCallback(user, msg, data=f"q:a:{session_id}:0:a0")))
    assert msg.answers == []  # never half-handled into a fresh message


def test_fallback_send_exception_is_not_swallowed(flow):
    user = FakeUser(1)
    msg = FakeCardMessage(user)
    _start(user, msg)
    session_id = _session_id()
    msg.edit_exc = _bad_request("Bad Request: message to edit not found")
    async def broken_answer(text, **kw):
        raise RuntimeError("send failed")
    msg.answer = broken_answer
    with pytest.raises(RuntimeError):
        asyncio.run(bot.cb_questionnaire_answer(
            FakeCallback(user, msg, data=f"q:a:{session_id}:0:a0")))


def test_fallback_attempts_to_disable_old_keyboard(flow):
    user = FakeUser(1)
    msg = FakeCardMessage(user)
    _start(user, msg)
    session_id = _session_id()
    msg.edit_exc = _bad_request("Bad Request: message can't be edited")
    asyncio.run(bot.cb_questionnaire_answer(
        FakeCallback(user, msg, data=f"q:a:{session_id}:0:a0")))
    assert msg.markup_cleared >= 1  # stale card keyboard disabled best-effort


def test_stale_answer_from_old_card_rejected(flow):
    # After a fallback the OLD card may keep buttons for step 0; the session is
    # already on step 1 -> the stale q:a is refused by the step guard and the
    # answer is NOT overwritten/duplicated.
    user = FakeUser(1)
    msg = FakeCardMessage(user)
    _start(user, msg)
    session_id = _session_id()
    asyncio.run(bot.cb_questionnaire_answer(
        FakeCallback(user, msg, data=f"q:a:{session_id}:0:a0")))
    asyncio.run(bot.cb_questionnaire_answer(
        FakeCallback(user, msg, data=f"q:a:{session_id}:0:a3")))  # stale step 0
    import sqlite3
    con = sqlite3.connect(database.DB)
    n, idx = con.execute(
        "SELECT (SELECT COUNT(*) FROM questionnaire_responses), "
        "(SELECT current_index FROM questionnaire_sessions)").fetchone()
    vals = con.execute("SELECT answer_value FROM questionnaire_responses").fetchall()
    con.close()
    assert (n, idx) == (1, 1)
    assert vals == [("0",)]  # first answer kept; stale a3 rejected


def test_double_tap_same_answer_advances_once(flow):
    user = FakeUser(1)
    msg = FakeCardMessage(user)
    _start(user, msg)
    session_id = _session_id()
    for _ in range(2):  # double tap on the same step-0 button
        asyncio.run(bot.cb_questionnaire_answer(
            FakeCallback(user, msg, data=f"q:a:{session_id}:0:a0")))
    import sqlite3
    con = sqlite3.connect(database.DB)
    n, idx = con.execute(
        "SELECT (SELECT COUNT(*) FROM questionnaire_responses), "
        "(SELECT current_index FROM questionnaire_sessions)").fetchone()
    con.close()
    assert (n, idx) == (1, 1)  # advanced exactly once


def test_back_then_old_answer_cannot_corrupt_state(flow):
    user = FakeUser(1)
    msg = FakeCardMessage(user)
    _start(user, msg)
    session_id = _session_id()
    asyncio.run(bot.cb_questionnaire_answer(
        FakeCallback(user, msg, data=f"q:a:{session_id}:0:a0")))
    asyncio.run(bot.cb_questionnaire_back(
        FakeCallback(user, msg, data=f"q:b:{session_id}")))  # back to step 0
    # a stale step-1 button (from the pre-Back card) must be rejected
    asyncio.run(bot.cb_questionnaire_answer(
        FakeCallback(user, msg, data=f"q:a:{session_id}:1:a2")))
    import sqlite3
    con = sqlite3.connect(database.DB)
    idx = con.execute("SELECT current_index FROM questionnaire_sessions").fetchone()[0]
    con.close()
    assert idx == 0  # still on the replayed step; stale forward answer refused


def test_overlong_generic_card_uses_safe_fallback(flow):
    # A synthetic definition whose legend would exceed the Telegram limit must
    # fall back deterministically: no legend, full-label buttons, no silent
    # truncation of any label.
    user = FakeUser(1)
    msg = FakeCardMessage(user)
    _start(user, msg)
    session_id = _session_id()
    big_item = {"id": "big", "text": "t",
                "options": [{"id": f"a{i}", "label": "Д" * 1300, "value": str(i)}
                            for i in range(4)]}
    d = json.loads(FIXTURE.read_text(encoding="utf-8"))
    asyncio.run(bot._send_questionnaire_step(
        bot._edit_or_answer(msg), {**d, "items": [big_item]}, session_id, 0, "ru"))
    text, kw = msg.edits[-1]
    assert len(text) <= bot._QUESTIONNAIRE_CARD_MAXLEN
    labels = [b.text for row in kw["reply_markup"].inline_keyboard
              for b in row if b.callback_data.startswith("q:a:")]
    assert labels == ["Д" * 1300] * 4  # full labels on buttons, not truncated


def test_dass_card_below_telegram_limit(flow):
    user = FakeUser(1)
    msg = FakeCardMessage(user)
    _start(user, msg)
    text, _ = msg.edits[-1]
    assert len(text) <= bot._QUESTIONNAIRE_CARD_MAXLEN


def test_compact_token_validation():
    f = bot._compact_button_token
    assert f("0") == "0" and f("42") == "42"
    assert f(None) is None
    assert f(True) is None and f(False) is None
    assert f(0) is None and f(3) is None      # non-string numerics
    assert f("") is None and f(" 1") is None and f("1 ") is None
    assert f("x" * 17) is None
    assert f("x" * 16) == "x" * 16


def test_callback_format_unchanged(flow):
    user = FakeUser(1)
    msg = FakeCardMessage(user)
    _start(user, msg)
    _, kw = msg.edits[-1]
    datas = [cd for _, cd in _card_buttons(kw)]
    assert any(cd.startswith("q:a:") and cd.endswith(":a0") for cd in datas)
    assert any(cd.startswith("q:b:") for cd in datas)
    # Owner-review UX correction: the live card's exit action is now the
    # state-preserving pause (q:p), not the destructive cancel (q:x) --
    # q:x stays registered for old cached keyboards, but is no longer rendered.
    assert any(cd.startswith("q:p:") for cd in datas)
    assert not any(cd.startswith("q:x:") for cd in datas)
    assert all(len(cd.encode("utf-8")) <= 64 for cd in datas)


# ── Journal reminder settings — single-card navigation (owner-review
# correction). Diary hub -> Reminder settings -> toggle morning/evening ->
# timezone picker -> back must all edit the SAME Telegram card instead of
# appending a fresh message at every step. A plain /journal_settings command
# still creates the initial card, since there is no existing callback card to
# edit yet.
@pytest.fixture
def journal_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    asyncio.run(database.upsert_user(1, "u", "U"))
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})
    monkeypatch.setattr(bot, "CallbackQuery", FakeCallback)
    return database


def test_jhub_settings_edits_existing_card_no_new_message(journal_flow):
    user = FakeUser(1)
    msg = FakeCardMessage(user)
    asyncio.run(bot.cb_jhub(FakeCallback(user, msg, data="jhub:settings"), state=None))
    assert msg.answers == []
    assert len(msg.edits) == 1
    assert "Напоминания" in msg.edits[-1][0]


def test_toggle_morning_edits_existing_card_and_flips_real_users_setting(journal_flow):
    # Also proves the tg_user fix: cb_jhub's callback.message.from_user is the
    # BOT, so the redrawn card must be built for the real tapping user (1),
    # not fall through to a wrong-uid access-denial screen.
    user = FakeUser(1)
    msg = FakeCardMessage(user)
    asyncio.run(bot.cb_jhub(FakeCallback(user, msg, data="jhub:settings"), state=None))
    asyncio.run(bot.cb_jset(FakeCallback(user, msg, data="jset:morning"), state=None))
    assert msg.answers == []
    assert len(msg.edits) == 2
    _, kw = msg.edits[-1]
    assert any(t.startswith("✅ Утро") for t, _ in _card_buttons(kw))
    settings = asyncio.run(database.get_journal_settings(1))
    assert settings["morning_enabled"] == 1


def test_tz_picker_and_selection_edit_the_same_card(journal_flow):
    user = FakeUser(1)
    msg = FakeCardMessage(user)
    asyncio.run(bot.cb_jhub(FakeCallback(user, msg, data="jhub:settings"), state=None))
    asyncio.run(bot.cb_jset(FakeCallback(user, msg, data="jset:tz"), state=None))
    asyncio.run(bot.cb_jtz(FakeCallback(user, msg, data="jtz:3")))
    assert msg.answers == []
    assert len(msg.edits) == 3
    assert "UTC+3" in msg.edits[-1][0]
    offset, tz_set, _lang = asyncio.run(database.get_user_tz(1))
    assert (offset, tz_set) == (3, 1)   # timezone persistence unaffected


def test_direct_journal_settings_command_creates_fresh_card(journal_flow):
    user = FakeUser(1)
    msg = FakeCardMessage(user)
    asyncio.run(bot.cmd_journal_settings(msg, None))
    assert msg.edits == []
    assert len(msg.answers) == 1
    assert "Напоминания" in msg.answers[-1][0]


def test_journal_settings_edit_failure_falls_back_to_new_message(journal_flow):
    user = FakeUser(1)
    msg = FakeCardMessage(user)
    asyncio.run(bot.cb_jhub(FakeCallback(user, msg, data="jhub:settings"), state=None))
    msg.edit_exc = _bad_request("Bad Request: message can't be edited")
    asyncio.run(bot.cb_jset(FakeCallback(user, msg, data="jset:morning"), state=None))
    assert len(msg.answers) == 1  # same _edit_or_answer fallback contract as questionnaires
    _, kw = msg.answers[-1]
    assert any(t.startswith("✅ Утро") for t, _ in _card_buttons(kw))


# ── jhub:report must resolve the REAL clicking user, not the bot-authored
# card (owner-review correction). REAL_USER_ID and BOT_LIKE_ID are genuinely
# distinct ids/objects -- callback.message.from_user is deliberately the
# bot-like id, exactly like real Telegram, so this cannot pass by accident.
REAL_USER_ID = 555
BOT_LIKE_ID = 999


@pytest.fixture
def journal_report_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    asyncio.run(database.upsert_user(REAL_USER_ID, "u", "U"))
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "public")  # ordinary access without invite
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})
    monkeypatch.setattr(bot, "CallbackQuery", FakeCallback)
    for i in range(3):
        asyncio.run(database.save_emotion_entry(REAL_USER_ID, {
            "event": f"событие {i}", "feeling": "радость", "intensity": 7,
            "body": None, "need": "поддержка", "action": "погулял", "outcome": "легче"}))
    return database


def test_jhub_report_uses_real_clicking_user_not_bot_authored_message(journal_report_flow, monkeypatch):
    calls = []
    real_get = database.get_emotion_entries_since
    async def spy_get_emotion(uid, days=7):
        calls.append(uid)
        return await real_get(uid, days)
    monkeypatch.setattr(bot, "get_emotion_entries_since", spy_get_emotion)

    bot_message_user = FakeUser(BOT_LIKE_ID)      # callback.message's author
    real_clicking_user = FakeUser(REAL_USER_ID)   # callback.from_user
    msg = FakeCardMessage(bot_message_user)
    cb = FakeCallback(real_clicking_user, msg, data="jhub:report")
    assert msg.from_user.id != cb.from_user.id  # genuinely distinct, not the same object

    asyncio.run(bot.cb_jhub(cb, state=None))

    assert calls == [REAL_USER_ID]  # data lookup used the real clicking user
    assert BOT_LIKE_ID not in calls
    text = msg.answers[-1][0]
    # BOT_LIKE_ID has zero journal entries, so using it would always render the
    # "not enough entries" placeholder; REAL_USER_ID has 3 seeded entries, so a
    # genuine populated summary proves the report was built from real data.
    assert "Пока мало записей" not in text
    assert "радость" in text
