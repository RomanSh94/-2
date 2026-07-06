"""Questionnaire Registry (PR A) — gate-order proof, callback-format proof,
old-loader-replacement proof, and journal_guard-before-access-gate parametrized
across every new questionnaire callback (mirrors tests/test_navigation.py's
NAV_CALLBACKS pattern).
"""
import asyncio
import pathlib
import re
import types

import pytest

import bot
import database
import questionnaires
import access_control as ac

FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures" / "registry"
ROOT = pathlib.Path(__file__).resolve().parent.parent


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


async def _sessions_for(uid):
    import sqlite3
    con = sqlite3.connect(database.DB)
    rows = con.execute(
        "SELECT id FROM questionnaire_sessions WHERE user_id=?", (uid,)).fetchall()
    con.close()
    return rows


def _make_active_session(uid=1):
    return asyncio.run(database.start_questionnaire_session(uid, "demo_anxiety_v1", "1"))


# ── registry loads multiple definitions (item 1) ─────────────────────────────
def test_registry_loads_all_four_fixture_definitions():
    registry = questionnaires.load_registry(FIXTURE_DIR)
    assert set(registry.by_id.keys()) == {
        "demo_anxiety_v1", "demo_archived_v1", "demo_draft_v1", "demo_restricted_v1"}


def test_active_questionnaire_in_list_active():
    registry = questionnaires.load_registry(FIXTURE_DIR)
    active_ids = {d["id"] for d in registry.list_active()}
    assert "demo_anxiety_v1" in active_ids


def test_archived_questionnaire_hidden_from_list_active():
    registry = questionnaires.load_registry(FIXTURE_DIR)
    active_ids = {d["id"] for d in registry.list_active()}
    assert "demo_archived_v1" not in active_ids


def test_draft_and_restricted_cannot_start_via_registry():
    registry = questionnaires.load_registry(FIXTURE_DIR)
    assert registry.can_start("demo_draft_v1") is False
    assert registry.can_start("demo_restricted_v1") is False
    assert registry.can_start("demo_archived_v1") is False
    assert registry.can_start("demo_anxiety_v1") is True


def test_draft_and_restricted_cannot_answer_via_registry():
    registry = questionnaires.load_registry(FIXTURE_DIR)
    assert registry.can_answer("demo_draft_v1") is False
    assert registry.can_answer("demo_restricted_v1") is False


# ── callback_data <=64 bytes for every documented format ─────────────────────
def test_every_callback_format_is_at_most_64_bytes():
    sid = 1234567
    step = 4
    formats = [
        "q:l",
        "q:c:anxiety",
        "q:d:demo_anxiety_v1",
        "q:s:demo_anxiety_v1",
        f"q:a:{sid}:{step}:a3",
        f"q:b:{sid}",
        f"q:p:{sid}",
        f"q:x:{sid}",
    ]
    for fmt in formats:
        assert len(fmt.encode("utf-8")) <= 64, f"{fmt!r} exceeds 64 bytes"


def test_item_id_never_embedded_in_answer_callback_data():
    # q:a:<sid>:<step>:<aid> -- the format has exactly 5 colon-separated
    # parts and no item-id-shaped segment; the current item is derived from
    # session.current_index (== step), never encoded directly.
    fmt = "q:a:42:3:a1"
    parts = fmt.split(":")
    assert len(parts) == 5
    assert parts[0] == "q" and parts[1] == "a"
    # step and sid are both purely numeric -- neither is an item id string.
    assert parts[2].isdigit() and parts[3].isdigit()


# ── journal_guard runs before access gate on every new entrypoint ────────────
NEW_QUESTIONNAIRE_CALLBACKS = {
    "q:l": bot.cb_questionnaire_list,
    "q:c:anxiety": bot.cb_questionnaire_category,
    "q:d:demo_anxiety_v1": bot.cb_questionnaire_detail,
    "q:s:demo_anxiety_v1": bot.cb_questionnaire_start,
}


@pytest.mark.parametrize("data,handler", list(NEW_QUESTIONNAIRE_CALLBACKS.items()))
def test_new_callback_requires_product_gate(data, handler):
    user = FakeUser(424242)   # UNKNOWN under personal_use
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data=data)
    asyncio.run(handler(cb))
    assert msg.answers
    assert "Demo Anxiety Check" not in msg.answers[0][0]
    assert "Опросники" not in msg.answers[0][0] or data != "q:l"


@pytest.mark.parametrize("data,handler", list(NEW_QUESTIONNAIRE_CALLBACKS.items()))
def test_new_callback_respects_active_crisis_gate(monkeypatch, data, handler):
    monkeypatch.setattr(bot, "get_active_crisis", _async((7, 0, "ru")))
    user = FakeUser(1)   # OWNER, full access -- crisis must still intercept
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data=data)
    asyncio.run(handler(cb))
    assert len(msg.answers) == 1
    from crisis_protocol import get_hotline
    assert get_hotline("ru")["primary"] in msg.answers[0][0]


# session-scoped callbacks (q:a/q:b/q:p/q:x) need a real session id, tested
# separately since NEW_QUESTIONNAIRE_CALLBACKS above covers the id-less ones.
SESSION_CALLBACKS = {
    "q:b": bot.cb_questionnaire_back,
    "q:p": bot.cb_questionnaire_pause,
    "q:x": bot.cb_questionnaire_cancel,
}


@pytest.mark.parametrize("prefix,handler", list(SESSION_CALLBACKS.items()))
def test_session_callback_requires_product_gate(prefix, handler):
    user = FakeUser(424242)
    session_id = _make_active_session(uid=424242)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data=f"{prefix}:{session_id}")
    asyncio.run(handler(cb))
    assert msg.answers
    assert "Вопрос" not in msg.answers[0][0]


@pytest.mark.parametrize("prefix,handler", list(SESSION_CALLBACKS.items()))
def test_session_callback_respects_active_crisis_gate(monkeypatch, prefix, handler):
    monkeypatch.setattr(bot, "get_active_crisis", _async((7, 0, "ru")))
    user = FakeUser(1)
    session_id = _make_active_session(uid=1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data=f"{prefix}:{session_id}")
    asyncio.run(handler(cb))
    assert len(msg.answers) == 1
    from crisis_protocol import get_hotline
    assert get_hotline("ru")["primary"] in msg.answers[0][0]


def test_answer_callback_requires_active_crisis_gate_before_session_checks(monkeypatch):
    # q:a runs journal_guard directly (not via _questionnaire_gate, since it
    # must NOT run ensure_full_access_or_closed_test a second time inside an
    # in-progress flow step -- same convention as emotion_step/cbt_step) --
    # prove the crisis screen still intercepts before any session lookup.
    monkeypatch.setattr(bot, "get_active_crisis", _async((7, 0, "ru")))
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="q:a:999999:0:a1")   # session doesn't even exist
    asyncio.run(bot.cb_questionnaire_answer(cb))
    from crisis_protocol import get_hotline
    assert get_hotline("ru")["primary"] in msg.answers[-1][0]


# ── session ownership silent no-op (non-owner) ───────────────────────────────
@pytest.mark.parametrize("prefix,handler", list(SESSION_CALLBACKS.items()))
def test_session_callback_silent_noop_for_non_owner(prefix, handler):
    owner_session = _make_active_session(uid=1)
    attacker = FakeUser(999)
    msg = FakeMessage(attacker)
    cb = FakeCallback(attacker, msg, data=f"{prefix}:{owner_session}")
    asyncio.run(handler(cb))
    # No content-revealing message sent for a session that isn't theirs.
    for text, _ in msg.answers:
        assert "Вопрос" not in text
        assert text != bot.questionnaire_ux.cancelled_text("ru")


# ── old single-definition loader fully replaced, not left running in parallel ─
def test_old_loader_function_no_longer_exists():
    assert not hasattr(questionnaires, "get_validated_definition"), (
        "the old single-definition loader must be fully replaced by the "
        "Registry class, not left running as a second parallel path")


def test_bot_py_does_not_reference_old_loader_function():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert "get_validated_definition" not in src


def test_questionnaires_module_defines_registry_not_old_single_loader():
    src = (ROOT / "questionnaires.py").read_text(encoding="utf-8")
    assert "class Registry" in src
    assert "def get_validated_definition" not in src


# ── /menu and cb_emotion_map gate order unchanged (literal grep evidence) ────
def test_nav_gate_and_emotion_map_still_use_journal_guard_then_access_gate():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    # _nav_gate body: journal_guard call textually precedes the
    # ensure_full_access_or_closed_test call, and cmd_menu/cb_emotion_map both
    # still call _nav_gate as their ONLY gate (unchanged from before this PR).
    nav_gate_src = re.search(
        r"async def _nav_gate\(.*?\n(?:.*\n)*?(?=\ndef _menu_keyboard)", src).group(0)
    jg_pos = nav_gate_src.index("journal_guard(")
    ag_pos = nav_gate_src.index("ensure_full_access_or_closed_test(")
    assert jg_pos < ag_pos, "_nav_gate must call journal_guard before ensure_full_access_or_closed_test"

    cmd_menu_src = re.search(r"async def cmd_menu\(.*?\n(?:.*\n)*?(?=\n@dp\.callback_query)", src).group(0)
    assert "_nav_gate(message, uid, lang)" in cmd_menu_src

    cb_emotion_map_src = re.search(
        r"async def cb_emotion_map\(.*?\n(?:.*\n)*?(?=\n@dp\.message\(F\.voice\))", src).group(0)
    assert "_nav_gate(callback, uid, lang)" in cb_emotion_map_src


def test_questionnaire_gate_mirrors_nav_gate_order():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    qg_src = re.search(
        r"async def _questionnaire_gate\(.*?\n(?:.*\n)*?(?=\nasync def _send_questionnaire_step)", src).group(0)
    jg_pos = qg_src.index("journal_guard(")
    ag_pos = qg_src.index("ensure_full_access_or_closed_test(")
    assert jg_pos < ag_pos, "_questionnaire_gate must call journal_guard before ensure_full_access_or_closed_test"


# ── no questionnaire_scores table ─────────────────────────────────────────────
def test_no_questionnaire_scores_table_in_schema():
    import database
    assert "questionnaire_scores" not in database.SCHEMA


# ── no copyrighted content in fixtures/source ─────────────────────────────────
_FORBIDDEN_INSTRUMENT_FRAGMENTS = (
    "phq-9", "phq9", "gad-7", "gad7", "stai", "ysq", "smi", "epi", "lsi",
    "svf-120", "bpnss", "csis", "little interest or pleasure",
    "beck depression", "bdi-ii", "feeling nervous, anxious",
)


def test_no_copyrighted_instrument_content_in_new_fixtures():
    for path in FIXTURE_DIR.glob("*.json"):
        src = path.read_text(encoding="utf-8").lower()
        for frag in _FORBIDDEN_INSTRUMENT_FRAGMENTS:
            assert frag not in src, f"{frag!r} found in {path.name}"


def test_no_copyrighted_instrument_content_in_new_source_modules():
    for name in ("questionnaires.py", "questionnaire_ux.py"):
        src = (ROOT / name).read_text(encoding="utf-8").lower()
        for frag in _FORBIDDEN_INSTRUMENT_FRAGMENTS:
            assert frag not in src, f"{frag!r} found in {name}"
