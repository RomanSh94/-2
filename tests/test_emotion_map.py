"""Emotion Map helper — deterministic vocabulary aid shown on emotion-naming
prompts (onboarding, emotion-journal "feeling" step, CBT-journal "emotion"
step). Not a test/score/diagnosis; opening it never stores anything.
"""
import asyncio
import types

import pytest

import bot
import database
import journals
import emotion_map
import access_control as ac


class FakeUser:
    def __init__(self, uid, username="user", first_name="U"):
        self.id = uid
        self.username = username
        self.first_name = first_name


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


class FakeFSM:
    def __init__(self, data=None, state=None):
        self._data = dict(data or {})
        self._state = state

    async def get_data(self):
        return dict(self._data)

    async def update_data(self, **kw):
        self._data.update(kw)

    async def set_state(self, s):
        self._state = s

    async def get_state(self):
        return self._state

    async def clear(self):
        self._data = {}
        self._state = None


def _async(value=None):
    async def _f(*a, **kw):
        return value
    return _f


@pytest.fixture(autouse=True)
def _common(monkeypatch):
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    monkeypatch.setattr(bot, "get_active_crisis", _async(None))
    monkeypatch.setattr(bot, "log_crisis_delivery", _async(None))
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})
    monkeypatch.setattr(bot, "CallbackQuery", FakeCallback)


# ── deterministic prompts include the helper ────────────────────────────────
def test_emotion_journal_feeling_step_includes_map_button(monkeypatch):
    monkeypatch.setattr(bot, "save_emotion_entry", _async(None))
    user = FakeUser(1)
    msg = FakeMessage(user, "поругался с коллегой")
    fsm = FakeFSM(data={"jstep": 0, "jdata": {}, "orange": False, "nudged": False})
    asyncio.run(bot.emotion_step(msg, fsm))
    # jstep 0 ("event") -> next is "feeling" (index 1); its prompt must carry
    # the emotion-map button.
    assert journals.EMOTION_FIELDS[1] == "feeling"
    text, kw = msg.answers[-1]
    kb = kw.get("reply_markup")
    assert kb is not None
    assert any(btn.callback_data == "emotion:map" for row in kb.inline_keyboard for btn in row)


def test_cbt_journal_emotion_step_includes_map_button(monkeypatch):
    monkeypatch.setattr(bot, "save_cbt_entry", _async(None))
    user = FakeUser(1)
    msg = FakeMessage(user, "я не справлюсь")
    fsm = FakeFSM(data={"cstep": 1, "cdata": {}})
    asyncio.run(bot.cbt_step(msg, fsm))
    # cstep 1 ("automatic_thought") -> next is "emotion" (index 2).
    assert journals.CBT_FIELDS[2] == "emotion"
    text, kw = msg.answers[-1]
    kb = kw.get("reply_markup")
    assert kb is not None
    assert any(btn.callback_data == "emotion:map" for row in kb.inline_keyboard for btn in row)


def test_onboarding_includes_map_button(monkeypatch, tmp_path):
    # cmd_start's real (unstubbed) DB calls -- get_stored_user_language,
    # get_onboarding_eligibility -- need a real, schema-initialized DB; a
    # bare monkeypatch of upsert_user/get_memory_overview alone left this
    # test dependent on whatever "x20.db" happened to already exist at the
    # default relative path (pre-existing test-isolation gap, unrelated to
    # this pass's other changes -- fixed here to match repo convention).
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    monkeypatch.setattr(bot, "get_memory_overview", _async({"message_count": 0}))
    monkeypatch.setattr(bot, "upsert_user", _async(None))
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_start(msg))
    text, kw = msg.answers[0]
    kb = kw["reply_markup"]
    assert any(btn.callback_data == "emotion:map" for row in kb.inline_keyboard for btn in row)


# ── emotion:map callback ──────────────────────────────────────────────────────
def test_emotion_map_displays_non_diagnostic_map():
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="emotion:map")
    asyncio.run(bot.cb_emotion_map(cb))
    assert msg.answers
    text = msg.answers[0][0]
    assert "🟡 Радость" in text
    for forbidden in ("диагноз", "результат", "оценка", "тест"):
        assert forbidden not in text.lower()


def test_emotion_map_does_not_store_journal_or_questionnaire_answers(monkeypatch):
    calls = {"emotion": 0, "cbt": 0, "questionnaire": 0}

    async def _spy_emotion(*a, **kw):
        calls["emotion"] += 1

    async def _spy_cbt(*a, **kw):
        calls["cbt"] += 1

    async def _spy_qr(*a, **kw):
        calls["questionnaire"] += 1

    monkeypatch.setattr(bot, "save_emotion_entry", _spy_emotion)
    monkeypatch.setattr(bot, "save_cbt_entry", _spy_cbt)
    monkeypatch.setattr(bot, "record_questionnaire_response", _spy_qr)

    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="emotion:map")
    asyncio.run(bot.cb_emotion_map(cb))

    assert calls == {"emotion": 0, "cbt": 0, "questionnaire": 0}


def test_emotion_map_requires_product_gate():
    user = FakeUser(424242)   # UNKNOWN
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="emotion:map")
    asyncio.run(bot.cb_emotion_map(cb))
    assert msg.answers
    assert "🟡 Радость" not in msg.answers[0][0]


def test_emotion_map_respects_active_crisis_gate(monkeypatch):
    monkeypatch.setattr(bot, "get_active_crisis", _async((7, 0, "ru")))
    from crisis_protocol import get_hotline
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="emotion:map")
    asyncio.run(bot.cb_emotion_map(cb))
    assert len(msg.answers) == 1
    assert get_hotline("ru")["primary"] in msg.answers[0][0]
    assert "🟡 Радость" not in msg.answers[0][0]


# ── copy checks ───────────────────────────────────────────────────────────────
def test_map_copy_contains_no_forbidden_wording():
    text = (emotion_map.emotion_map_text("ru") + emotion_map.emotion_map_text("en")
            + emotion_map.emotion_map_return_hint("ru") + emotion_map.emotion_map_return_hint("en")).lower()
    for forbidden in ("диагноз", "результат теста", "уровень тревожности",
                     "я рядом", "я всегда рядом", "я тебя не брошу"):
        assert forbidden not in text
