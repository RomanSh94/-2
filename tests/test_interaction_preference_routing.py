"""Phase 4A.1b — current-turn interaction preference.

Forensic root cause: a residual same-day EmotionalTrajectory bias (and,
independently, a standalone low-energy fallback) kept overriding the
rewritten open_chat prompt for real owner messages that explicitly said
"just talk" or explicitly asked for advice. interaction_preference.py
detects that CURRENT-TURN signal (never persisted); state_engine.py
suppresses only the confirmed non-acute historical/state-threshold rules
when it's present, while every mandatory acute-safety rule (crisis,
ACUTE_DISTRESS, panic/dissociation, overwhelm) stays completely unconditional.

Three layers are covered here:
  1. the detector in isolation (interaction_preference.py);
  2. the router in isolation (state_engine.choose_scenario), including the
     forensic A1/A3 replays and the mandatory fail-closed contract;
  3. the real end-to-end bot.pipeline() orchestration boundary, proving the
     mood-scale/outcome-tracking message is not emitted once real routing
     reaches open_chat under the exact adverse historical condition that
     used to defeat it.
"""
import asyncio
import itertools
import types

import pytest

import bot
import conversation_controller
import database
from interaction_preference import detect_interaction_preference
from state_engine import EmotionalTrajectory, choose_scenario

A1_TEXT = "Привет. Сегодня немного устал, хочу просто поговорить"
A3_TEXT = "Расскажи, как мне немного разгрузить голову сегодня вечером?"
A4_TEXT = "Я не хочу сейчас советов, просто поговори со мной."

MOOD_SCALE_RU = "1=плохо, 10=хорошо"
MOOD_SCALE_EN = "1=bad, 10=good"


# ══════════════════════════════════════════════════════════════════════════
# 1. Detector — interaction_preference.detect_interaction_preference
# ══════════════════════════════════════════════════════════════════════════

def test_hard_no_advice_wins_over_soft_talk():
    assert detect_interaction_preference(
        "не хочу сейчас советов, просто поговори со мной", "ru") == "JUST_TALK"


def test_soft_talk_does_not_suppress_explicit_advice_request():
    assert detect_interaction_preference(
        "поговори со мной и подскажи, что мне делать", "ru") == "ADVICE_REQUEST"


def test_advice_request_dai_sovet():
    assert detect_interaction_preference("дай совет, как мне разгрузить голову", "ru") == "ADVICE_REQUEST"


def test_exact_a3_is_advice_request():
    assert detect_interaction_preference(A3_TEXT, "ru") == "ADVICE_REQUEST"


def test_soft_just_talk_alone():
    assert detect_interaction_preference("хочу просто поговорить", "ru") == "JUST_TALK"


def test_neutral_ru_text_is_none():
    assert detect_interaction_preference("Сегодня хорошая погода в городе", "ru") == "NONE"


def test_neutral_en_text_is_none():
    assert detect_interaction_preference("The weather is nice today", "en") == "NONE"


def test_hard_refusal_plus_advice_looking_fragment_still_just_talk():
    assert detect_interaction_preference(
        "не хочу советов, но подскажи, что делать", "ru") == "JUST_TALK"


def test_exact_a1_is_just_talk():
    assert detect_interaction_preference(A1_TEXT, "ru") == "JUST_TALK"


def test_exact_a4_is_just_talk():
    assert detect_interaction_preference(A4_TEXT, "ru") == "JUST_TALK"


def test_bare_kak_mne_is_not_a_trigger():
    # The correction explicitly forbids standalone "как мне" from matching.
    assert detect_interaction_preference("как мне сегодня грустно", "ru") == "NONE"


def test_detector_output_always_one_of_three_values():
    samples = [
        "", "   ", "???", "asdkjhasd 1234 !!!", "Привет как дела",
        "Hello, how are you doing today?", "😀😀😀", "не хочу", "подскажи",
        "поговори", "выговориться", "Что мне делать сегодня вечером дома одному",
        "I don't know what to do with my life honestly",
    ]
    for s in samples:
        for lang in ("ru", "en"):
            result = detect_interaction_preference(s, lang)
            assert result in {"NONE", "JUST_TALK", "ADVICE_REQUEST"}


def test_turn_isolation_no_stickiness():
    first = detect_interaction_preference(A1_TEXT, "ru")
    second = detect_interaction_preference("Дай совет, как мне быть", "ru")
    assert first == "JUST_TALK"
    assert second == "ADVICE_REQUEST"


# ══════════════════════════════════════════════════════════════════════════
# 2. Router — state_engine.choose_scenario direct calls
# ══════════════════════════════════════════════════════════════════════════

def _deteriorating_trajectory():
    return EmotionalTrajectory(
        user_id=1, messages_analyzed=5, max_risk_level="YELLOW",
        avg_risk_score=30.0, trend="deteriorating", trend_confidence=0.5,
        risk_categories_frequency={}, has_crisis_in_window=False, last_crisis_at=None,
    )


def test_exact_forensic_a1_just_talk_reaches_open_chat():
    state = {"energy": 0.239, "anxiety": 0.0, "overwhelm": 0.0, "panic": 0.0,
             "dissociation": 0.0, "hopelessness": 0.0, "loneliness": 0.0, "openness": 0.5}
    result = choose_scenario(state, [], "OPEN", "MEDIUM", 0.6,
                              trajectory=_deteriorating_trajectory(),
                              interaction_preference="JUST_TALK")
    assert result == "open_chat"


def test_exact_forensic_a3_advice_request_reaches_open_chat():
    state = {"energy": 0.264, "anxiety": 0.0, "overwhelm": 0.0, "panic": 0.0,
             "dissociation": 0.0, "hopelessness": 0.0, "loneliness": 0.0, "openness": 0.5}
    result = choose_scenario(state, [], "PROBLEM_SOLVING", "MEDIUM", 0.6,
                              trajectory=_deteriorating_trajectory(),
                              interaction_preference="ADVICE_REQUEST")
    assert result == "open_chat"


def test_a4_just_talk_reaches_open_chat_even_under_adverse_trajectory():
    state = {"energy": 0.2, "anxiety": 0.0, "overwhelm": 0.0, "panic": 0.0,
             "dissociation": 0.0, "hopelessness": 0.0, "loneliness": 0.0, "openness": 0.5}
    result = choose_scenario(state, [], "OPEN", "MEDIUM", 0.6,
                              trajectory=_deteriorating_trajectory(),
                              interaction_preference="JUST_TALK")
    assert result == "open_chat"


def test_stale_anxiety_suppressed_by_just_talk():
    state = {"anxiety": 0.7, "energy": 0.5}
    result = choose_scenario(state, [], "OPEN", "MEDIUM", 0.9,
                              interaction_preference="JUST_TALK")
    assert result == "open_chat"


def test_stale_loneliness_suppressed_by_just_talk():
    state = {"loneliness": 0.8, "energy": 0.5}
    result = choose_scenario(state, [], "OPEN", "MEDIUM", 0.9,
                              interaction_preference="JUST_TALK")
    assert result == "open_chat"


def test_stale_hopelessness_suppressed_by_advice_request():
    state = {"hopelessness": 0.7, "openness": 0.6, "energy": 0.5}
    result = choose_scenario(state, [], "OPEN", "MEDIUM", 0.9,
                              interaction_preference="ADVICE_REQUEST")
    assert result == "open_chat"


def test_crisis_precedence_preserved_under_just_talk():
    result = choose_scenario({"energy": 0.5}, ["suicide"], "OPEN", "MEDIUM", 0.9,
                              interaction_preference="JUST_TALK")
    assert result == "crisis"


def test_panic_precedence_preserved_under_just_talk():
    result = choose_scenario({"panic": 0.8, "energy": 0.5}, [], "OPEN", "MEDIUM", 0.9,
                              interaction_preference="JUST_TALK")
    assert result == "grounding"


def test_dissociation_precedence_preserved_under_just_talk():
    result = choose_scenario({"dissociation": 0.8, "energy": 0.5}, [], "OPEN", "MEDIUM", 0.9,
                              interaction_preference="JUST_TALK")
    assert result == "grounding"


def test_overwhelm_precedence_preserved_under_just_talk():
    result = choose_scenario({"overwhelm": 0.8, "energy": 0.5}, [], "OPEN", "MEDIUM", 0.9,
                              interaction_preference="JUST_TALK")
    assert result == "stabilization"


def test_acute_distress_stage_precedence_preserved_under_just_talk():
    result = choose_scenario({"panic": 0.0, "energy": 0.5}, [], "ACUTE_DISTRESS", "MEDIUM", 0.9,
                              interaction_preference="JUST_TALK")
    assert result == "stabilization"


def test_none_default_preserves_existing_somatic_behavior():
    state = {"energy": 0.239, "anxiety": 0.0, "overwhelm": 0.0, "panic": 0.0,
             "dissociation": 0.0, "hopelessness": 0.0, "loneliness": 0.0, "openness": 0.5}
    result = choose_scenario(state, [], "OPEN", "MEDIUM", 0.6,
                              trajectory=_deteriorating_trajectory(),
                              interaction_preference="NONE")
    assert result == "somatic"


def test_fail_closed_invalid_mode_does_not_bypass_non_acute_routing():
    """Mandatory: a malformed/unknown value must fail closed to NONE, not
    disable therapeutic routing."""
    state = {"energy": 0.239, "anxiety": 0.0, "overwhelm": 0.0, "panic": 0.0,
             "dissociation": 0.0, "hopelessness": 0.0, "loneliness": 0.0, "openness": 0.5}
    result = choose_scenario(state, [], "OPEN", "MEDIUM", 0.6,
                              trajectory=_deteriorating_trajectory(),
                              interaction_preference="INVALID_VALUE")
    assert result == "somatic"


def test_fail_closed_empty_string_does_not_bypass_non_acute_routing():
    state = {"energy": 0.239, "anxiety": 0.0, "overwhelm": 0.0, "panic": 0.0,
             "dissociation": 0.0, "hopelessness": 0.0, "loneliness": 0.0, "openness": 0.5}
    result = choose_scenario(state, [], "OPEN", "MEDIUM", 0.6,
                              trajectory=_deteriorating_trajectory(),
                              interaction_preference="")
    assert result == "somatic"


def test_default_parameter_is_none_and_unchanged_behavior_for_existing_callers():
    """Callers that never pass interaction_preference (every pre-4A.1b call
    site) must get byte-identical routing to before this change."""
    state = {"energy": 0.239, "anxiety": 0.0, "overwhelm": 0.0, "panic": 0.0,
             "dissociation": 0.0, "hopelessness": 0.0, "loneliness": 0.0, "openness": 0.5}
    result = choose_scenario(state, [], "OPEN", "MEDIUM", 0.6,
                              trajectory=_deteriorating_trajectory())
    assert result == "somatic"


# ══════════════════════════════════════════════════════════════════════════
# 3. Orchestration boundary — real bot.pipeline() end to end
# ══════════════════════════════════════════════════════════════════════════

_next_id = itertools.count(90000)


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

    async def answer(self, text, **kw):
        sent = FakeSent(self.chat.id, text)
        self.answers.append((text, kw))
        return sent

    async def edit_reply_markup(self, **kw):
        pass


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
    monkeypatch.setattr(bot.dependency_monitor, "assess", _async(None))
    monkeypatch.setattr(bot.bot, "send_chat_action", _async(None))
    monkeypatch.setattr(bot.bot, "edit_message_reply_markup", _async(True))


def _set_llm(monkeypatch, content):
    async def fake_create(*a, **kw):
        msg_obj = types.SimpleNamespace(content=content)
        choice = types.SimpleNamespace(message=msg_obj)
        return types.SimpleNamespace(choices=[choice])
    monkeypatch.setattr(bot.client.chat.completions, "create", fake_create)


def _stub_deteriorating_trajectory(monkeypatch):
    traj = EmotionalTrajectory(
        user_id=1, messages_analyzed=5, max_risk_level="YELLOW",
        avg_risk_score=30.0, trend="deteriorating", trend_confidence=0.5,
        risk_categories_frequency={}, has_crisis_in_window=False, last_crisis_at=None,
    )
    monkeypatch.setattr(bot, "get_emotional_trajectory", _async(traj))


async def _exempt_from_first_turn_and_seed_low_energy(uid, energy):
    # A prior assistant message makes claim_first_turn() auto-classify this
    # user as legacy_exempt (database.py's real, already-shipped rule for
    # contract_version == FIRST_TURN_INITIAL_ROLLOUT_VERSION), so the
    # first-turn contract/button flow — a separate, already-hardened
    # feature — never engages and ordinary routing is exercised directly.
    await database.save_message(uid, "assistant", "предыдущий ответ", "open_chat", "ru", source=database.MessageSource.ASSISTANT_DELIVERED)
    seed_state = dict(bot.DEFAULT_STATE)
    seed_state["energy"] = energy
    await database.save_state(uid, seed_state)


def _run_pipeline(uid, text):
    user = FakeUser(uid)
    msg = FakeMessage(user, text)
    asyncio.run(bot.pipeline(msg, msg.text, None, tg_user=user))
    return msg


def _last_message_row(uid):
    import sqlite3
    con = sqlite3.connect(database.DB)
    row = con.execute(
        "SELECT scenario FROM messages WHERE user_id=? AND role='user' "
        "ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
    con.close()
    return row[0] if row else None


def test_pipeline_a1_no_mood_scale_reaches_open_chat(env, monkeypatch):
    uid = 501
    asyncio.run(_exempt_from_first_turn_and_seed_low_energy(uid, 0.15))
    _stub_deteriorating_trajectory(monkeypatch)
    _set_llm(monkeypatch, "Окей, давай просто поговорим.")

    msg = _run_pipeline(uid, A1_TEXT)

    all_text = " ".join(t for t, _ in msg.answers)
    assert MOOD_SCALE_RU not in all_text
    assert MOOD_SCALE_EN not in all_text
    assert len(msg.answers) >= 1
    assert _last_message_row(uid) == "open_chat"


def test_pipeline_a3_no_mood_scale_reaches_open_chat(env, monkeypatch):
    uid = 502
    asyncio.run(_exempt_from_first_turn_and_seed_low_energy(uid, 0.15))
    _stub_deteriorating_trajectory(monkeypatch)
    _set_llm(monkeypatch, "Смотри, вот пара идей на вечер.")

    msg = _run_pipeline(uid, A3_TEXT)

    all_text = " ".join(t for t, _ in msg.answers)
    assert MOOD_SCALE_RU not in all_text
    assert MOOD_SCALE_EN not in all_text
    assert len(msg.answers) >= 1
    assert _last_message_row(uid) == "open_chat"


def test_pipeline_a4_no_mood_scale_reaches_open_chat(env, monkeypatch):
    uid = 503
    asyncio.run(_exempt_from_first_turn_and_seed_low_energy(uid, 0.15))
    _stub_deteriorating_trajectory(monkeypatch)
    _set_llm(monkeypatch, "Хорошо, я здесь.")

    msg = _run_pipeline(uid, A4_TEXT)

    all_text = " ".join(t for t, _ in msg.answers)
    assert MOOD_SCALE_RU not in all_text
    assert MOOD_SCALE_EN not in all_text
    assert len(msg.answers) >= 1
    assert _last_message_row(uid) == "open_chat"


# ══════════════════════════════════════════════════════════════════════════
# 4. ConversationController ownership regression
# ══════════════════════════════════════════════════════════════════════════

def test_existing_controller_vent_ownership_unaffected():
    """'без советов' is both a HARD_NO_ADVICE detector phrase and an existing
    Controller VENT pattern (_VENT_RE). conversation_controller.py is not
    touched by this change — this pins that its ownership decision for an
    already-recognized phrase is unaffected."""
    assert (conversation_controller.classify_intent("без советов", "ru")
            == conversation_controller.Intent.VENT)
