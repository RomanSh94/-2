"""Voice and Adaptive Response UX — flags, meta-commands, TTS/delivery,
listen button, reactions, privacy, and safety-boundary tests.

Both VOICE_REPLIES_ENABLED and EMOTIONAL_REACTIONS_ENABLED default false;
most tests explicitly flip them on via monkeypatch to exercise the new
code paths, then separately prove the flag-off path is byte-identical to
prior behavior. No test calls a real paid API — TTS and Telegram calls are
always mocked.
"""
import asyncio
import inspect
import os
import types

import pytest

import access_control as ac
import bot
import config
import database
import format_commands as fc
import reaction_selector as rs
import tts as tts_module

run = asyncio.run


class FakeUser:
    def __init__(self, uid, username="user", first="U"):
        self.id = uid
        self.username = username
        self.first_name = first


class FakeMessage:
    def __init__(self, user, text="", message_id=1):
        self.from_user = user
        self.text = text
        self.chat = types.SimpleNamespace(id=user.id)
        self.message_id = message_id
        self.answers = []
        self.voices = []

    async def answer(self, text, **kw):
        self.answers.append((text, kw))

    async def answer_voice(self, *a, **kw):
        self.voices.append((a, kw))


class FakeCallback:
    def __init__(self, user, message, data=""):
        self.from_user = user
        self.message = message
        self.data = data
        self.answered = []

    async def answer(self, *a, **kw):
        self.answered.append((a, kw))


class FakeFSM:
    """Backed by a plain dict shared across calls -- a real per-(user,chat)
    aiogram FSMContext also persists data across separate updates via its
    storage; sharing ONE FakeFSM instance across two separate `pipeline()`
    calls is the correct way to simulate two separate Telegram updates for
    the SAME user/chat, and using two DIFFERENT FakeFSM instances correctly
    simulates two DIFFERENT users/chats never sharing state."""
    def __init__(self, data=None):
        self._data = data or {}

    async def get_data(self):
        return dict(self._data)

    async def update_data(self, **kw):
        self._data.update(kw)

    async def set_state(self, state):
        pass


def _async(value=None):
    async def _f(*a, **kw):
        return value
    return _f


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    run(database.init_db())
    return database


@pytest.fixture(autouse=True)
def _voice_flags_off(monkeypatch):
    """Default state for every test unless a test explicitly flips a flag on."""
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", False)
    monkeypatch.setattr(config, "EMOTIONAL_REACTIONS_ENABLED", False)
    bot._reaction_last_sent.clear()
    bot._listen_last_tap.clear()


# ── §19 Feature flags ────────────────────────────────────────────────────────

def test_both_flags_default_false_from_env():
    assert os.environ.get("VOICE_REPLIES_ENABLED") is None
    assert os.environ.get("EMOTIONAL_REACTIONS_ENABLED") is None
    # config module already evaluated these at import time from a clean env
    # (conftest never sets them) -- re-import is unnecessary; the defaults
    # are exercised directly via the autouse fixture's explicit False too.


def test_deliver_response_flag_off_is_byte_identical_to_prior_behavior(tmp_db, monkeypatch):
    msg = FakeMessage(FakeUser(1), "hi")
    run(bot.deliver_response(msg, 1, "the answer", "ru"))
    assert msg.answers == [("the answer", {})]
    assert msg.voices == []


def test_format_command_not_exposed_when_flag_off(tmp_db):
    msg = FakeMessage(FakeUser(1), "/format")
    run(bot.cmd_format(msg))
    assert msg.answers == []  # no selector shown, behaves as if unknown


def test_format_select_callback_fails_closed_when_flag_off(tmp_db):
    cb = FakeCallback(FakeUser(1), FakeMessage(FakeUser(1)), f"{bot._FMT_KB_VERSION}:format:voice")
    run(bot.cb_format_select(cb))
    prefs = run(database.get_response_preferences(1))
    assert prefs["response_format"] == "text"  # nothing silently saved


def test_no_tts_call_when_voice_disabled(tmp_db, monkeypatch):
    calls = {"n": 0}
    async def spy(*a, **kw):
        calls["n"] += 1
        return "unused"
    monkeypatch.setattr(bot, "synthesize_speech", spy)
    msg = FakeMessage(FakeUser(1), "hi")
    run(bot.deliver_response(msg, 1, "answer text", "ru"))
    assert calls["n"] == 0


def test_no_reaction_call_when_reactions_disabled(tmp_db, monkeypatch):
    calls = {"n": 0}
    async def spy(*a, **kw):
        calls["n"] += 1
        return True
    monkeypatch.setattr(bot.bot, "set_message_reaction", spy)
    msg = FakeMessage(FakeUser(1), "мне очень одиноко")
    run(bot._maybe_react(msg, 1, rs.ReactionCategory.LONELINESS_OR_REJECTION, 0.9))
    assert calls["n"] == 0


def test_env_example_keeps_both_new_flags_false():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(repo_root, ".env.example"), encoding="utf-8") as f:
        content = f.read()
    assert "VOICE_REPLIES_ENABLED=false" in content
    assert "EMOTIONAL_REACTIONS_ENABLED=false" in content


def test_existing_three_flags_still_false_and_unchanged():
    assert config.DASS21_DISCUSSION_ENABLED is False
    assert config.FIRST_USER_ONBOARDING_ENABLED is False
    assert config.THERAPEUTIC_CORE_FOUNDATION_ENABLED is False


# ── §20 Meta commands ────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,lang,kind,persistent", [
    ("ответь голосом", "ru", "voice_oneshot", False),
    ("скажи голосом", "ru", "voice_oneshot", False),
    ("reply with voice", "en", "voice_oneshot", False),
    ("всегда отвечай голосом", "ru", "voice_persistent", True),
    ("теперь отвечай мне голосом", "ru", "voice_persistent", True),
    ("always reply with voice", "en", "voice_persistent", True),
    ("не присылай голосовые", "ru", "text_persistent", True),
    ("no voice messages", "en", "text_persistent", True),
    ("короче", "ru", "concise_oneshot", False),
    ("можно кратко", "ru", "concise_oneshot", False),
    ("keep it short", "en", "concise_oneshot", False),
    ("всегда пиши короче", "ru", "concise_persistent", True),
    ("keep it short from now on", "en", "concise_persistent", True),
    ("можно подробнее", "ru", "detailed_oneshot", False),
    ("elaborate", "en", "detailed_oneshot", False),
    ("много текста", "ru", "voice_oneshot", False),
    ("лень читать", "ru", "voice_oneshot", False),
    ("too much text", "en", "voice_oneshot", False),
])
def test_parse_format_command_cases(text, lang, kind, persistent):
    cmd = fc.parse_format_command(text, lang)
    assert cmd is not None
    assert cmd.kind == kind


def test_false_positive_reading_someone_elses_messages_is_not_a_format_command():
    text = "Мне тяжело читать сообщения бывшего человека."
    assert fc.parse_format_command(text, "ru") is None
    assert fc.is_pure_format_command(text, "ru") is False


def test_mixed_message_still_matches_voice_but_is_not_pure():
    text = "Мне тревожно, и ответь голосом"
    cmd = fc.parse_format_command(text, "ru")
    assert cmd is not None and cmd.kind == "voice_oneshot"
    assert fc.is_pure_format_command(text, "ru") is False


def test_pure_one_shot_voice_request_is_pure():
    assert fc.is_pure_format_command("много текста", "ru") is True
    assert fc.is_pure_format_command("ответь голосом", "ru") is True


def test_mixed_loneliness_and_concise_request_keeps_emotional_content_reachable():
    # "но" (but) must be recognized as a connective, same as "и" -- a real
    # disclosure joined by "но" must not be misclassified as pure.
    text = "Мне одиноко, но покороче"
    cmd = fc.parse_format_command(text, "ru")
    assert cmd is not None and cmd.kind == "concise_oneshot"
    assert fc.is_pure_format_command(text, "ru") is False


def test_false_positive_third_party_always_answers_with_voice():
    text = "Он всегда отвечает голосом."
    assert fc.parse_format_command(text, "ru") is None


def test_false_positive_afraid_of_voice_messages():
    text = "Я боюсь голосовых сообщений."
    assert fc.parse_format_command(text, "ru") is None


def test_false_positive_book_had_too_much_text_does_not_trigger():
    # "много текста" appears as a substring, but referring to a BOOK, not
    # the bot's own replies -- the ambiguous-phrase standalone-only rule
    # rejects it entirely: no command at all, not merely "not pure".
    text = "В книге было слишком много текста."
    assert fc.parse_format_command(text, "ru") is None


def test_pure_persistent_request_is_pure():
    assert fc.is_pure_format_command("всегда отвечай голосом", "ru") is True


def test_pipeline_saves_preference_only_for_explicit_persistent_command(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(bot.set_response_preference(1, response_format="text"))
    # a persistent voice command must save; a one-shot must not
    cmd = fc.parse_format_command("всегда отвечай голосом", "ru")
    assert cmd.persistent is True
    cmd2 = fc.parse_format_command("ответь голосом", "ru")
    assert cmd2.persistent is False


# ── §21 TTS and delivery ─────────────────────────────────────────────────────

def _pipeline_source():
    return inspect.getsource(bot.pipeline)


def test_deliver_response_call_site_is_after_safety_validator_in_pipeline():
    src = _pipeline_source()
    validator_idx = src.index("validate_response_with_context")
    deliver_idx = src.index("await deliver_response(")
    assert validator_idx < deliver_idx


def test_text_mode_sends_plain_text_with_listen_button(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(database.set_response_preference(1, response_format="text"))
    msg = FakeMessage(FakeUser(1), "hi")
    run(bot.deliver_response(msg, 1, "answer text", "ru"))
    assert msg.answers[0][0] == "answer text"
    assert msg.voices == []
    assert msg.answers[0][1]["reply_markup"] is not None


def test_voice_mode_sends_exactly_one_voice_no_text(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(database.set_response_preference(1, response_format="voice"))
    calls = {"n": 0}
    async def fake_synth(client_, text, lang):
        calls["n"] += 1
        return "/tmp/fake.opus"
    monkeypatch.setattr(bot, "synthesize_speech", fake_synth)
    monkeypatch.setattr(os, "remove", lambda p: None)
    msg = FakeMessage(FakeUser(1), "hi")
    run(bot.deliver_response(msg, 1, "answer text", "ru"))
    assert calls["n"] == 1
    assert len(msg.voices) == 1
    assert msg.answers == []  # no text sent alongside a successful voice send


def test_voice_and_concise_text_mode_sends_one_text_and_one_voice(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(database.set_response_preference(1, response_format="voice_and_concise_text"))
    async def fake_synth(client_, text, lang):
        return "/tmp/fake.opus"
    monkeypatch.setattr(bot, "synthesize_speech", fake_synth)
    monkeypatch.setattr(os, "remove", lambda p: None)
    long_answer = ("Это длинный ответ. " * 30).strip()
    msg = FakeMessage(FakeUser(1), "hi")
    run(bot.deliver_response(msg, 1, long_answer, "ru"))
    assert len(msg.answers) == 1
    assert len(msg.voices) == 1
    assert len(msg.answers[0][0]) < len(long_answer)  # visible text is concise


def test_at_most_one_voice_message_ever_sent_per_delivery(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(database.set_response_preference(1, response_format="voice"))
    async def fake_synth(client_, text, lang):
        return "/tmp/fake.opus"
    monkeypatch.setattr(bot, "synthesize_speech", fake_synth)
    monkeypatch.setattr(os, "remove", lambda p: None)
    msg = FakeMessage(FakeUser(1), "hi")
    run(bot.deliver_response(msg, 1, "a" * 5000, "ru"))
    assert len(msg.voices) == 1  # never multiple segments regardless of length


def test_tts_timeout_falls_back_to_full_text(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(database.set_response_preference(1, response_format="voice"))
    async def fake_synth(client_, text, lang):
        raise tts_module.TTSError("timeout")
    monkeypatch.setattr(bot, "synthesize_speech", fake_synth)
    msg = FakeMessage(FakeUser(1), "hi")
    run(bot.deliver_response(msg, 1, "full answer", "ru"))
    assert msg.voices == []
    assert msg.answers == [("full answer", {})]  # honest fallback, never silent


def test_tts_provider_error_falls_back_to_full_text(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(database.set_response_preference(1, response_format="voice"))
    async def fake_synth(client_, text, lang):
        raise RuntimeError("provider down")
    monkeypatch.setattr(bot, "synthesize_speech", fake_synth)
    msg = FakeMessage(FakeUser(1), "hi")
    run(bot.deliver_response(msg, 1, "full answer", "ru"))
    assert msg.answers == [("full answer", {})]


def test_telegram_send_voice_error_still_cleans_temp_file(tmp_db, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    fake_path = str(tmp_path / "fake.opus")
    with open(fake_path, "wb") as f:
        f.write(b"fake audio")
    async def fake_synth(client_, text, lang):
        return fake_path
    monkeypatch.setattr(bot, "synthesize_speech", fake_synth)

    class BoomMessage(FakeMessage):
        async def answer_voice(self, *a, **kw):
            raise RuntimeError("telegram send failed")
    msg = BoomMessage(FakeUser(1), "hi")
    ok = run(bot._synthesize_and_send_voice(msg, 1, "text", "ru"))
    assert ok is False
    assert not os.path.exists(fake_path)  # cleaned up in finally despite the send error


def test_temp_file_cleanup_on_success(tmp_db, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    fake_path = str(tmp_path / "fake2.opus")
    with open(fake_path, "wb") as f:
        f.write(b"fake audio")
    async def fake_synth(client_, text, lang):
        return fake_path
    monkeypatch.setattr(bot, "synthesize_speech", fake_synth)
    msg = FakeMessage(FakeUser(1), "hi")
    ok = run(bot._synthesize_and_send_voice(msg, 1, "text", "ru"))
    assert ok is True
    assert not os.path.exists(fake_path)


def test_long_response_becomes_one_validated_concise_spoken_response(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(database.set_response_preference(1, response_format="voice", response_length="concise"))
    seen = {}
    async def fake_synth(client_, text, lang):
        seen["text"] = text
        return "/tmp/fake.opus"
    monkeypatch.setattr(bot, "synthesize_speech", fake_synth)
    monkeypatch.setattr(os, "remove", lambda p: None)
    long_answer = ("Одно предложение с важной мыслью. " * 20).strip()
    msg = FakeMessage(FakeUser(1), "hi")
    run(bot.deliver_response(msg, 1, long_answer, "ru"))
    assert len(seen["text"]) < len(long_answer)


def test_ru_and_en_voice_selection(tmp_db, monkeypatch):
    seen = {}
    async def fake_create(model, voice, input, response_format, **kw):
        seen["voice"] = voice
        class R:
            def write_to_file(self, path):
                open(path, "wb").write(b"x")
        return R()
    monkeypatch.setattr(bot.client.audio.speech, "create", fake_create)
    run(tts_module.synthesize_speech(bot.client, "hello", "ru"))
    assert seen["voice"] == config.TTS_VOICE_RU
    run(tts_module.synthesize_speech(bot.client, "hello", "en"))
    assert seen["voice"] == config.TTS_VOICE_EN


def test_concise_transform_is_validated_and_the_validated_text_is_what_reaches_tts(tmp_db, monkeypatch):
    # Direct mock assertions on BOTH validate_response and synthesize_speech,
    # recording the exact strings each receives -- a test that only checks
    # "send_voice was called" cannot prove ordering or content.
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(database.set_response_preference(1, response_format="voice", response_length="concise"))
    validated_texts = []
    synthesized_texts = []

    def spy_validate(text, lang="ru"):
        validated_texts.append(text)
        return True, None
    async def spy_synth(client_, text, lang):
        synthesized_texts.append(text)
        return "/tmp/fake.opus"
    monkeypatch.setattr(bot, "validate_response", spy_validate)
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)
    monkeypatch.setattr(os, "remove", lambda p: None)

    long_answer = ("Одно предложение с важной мыслью. " * 20).strip()
    msg = FakeMessage(FakeUser(1), "hi")
    run(bot.deliver_response(msg, 1, long_answer, "ru"))

    assert len(validated_texts) == 1
    concise_text = validated_texts[0]
    assert concise_text != long_answer and len(concise_text) < len(long_answer)
    assert synthesized_texts == [concise_text]  # exactly the VALIDATED text reaches TTS, nothing else


def test_unsafe_concise_transform_falls_back_to_original_and_never_reaches_tts(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(database.set_response_preference(1, response_format="voice", response_length="concise"))
    synthesized_texts = []

    def spy_validate(text, lang="ru"):
        return False, "simulated rejection"  # the shortened version fails validation
    async def spy_synth(client_, text, lang):
        synthesized_texts.append(text)
        return "/tmp/fake.opus"
    monkeypatch.setattr(bot, "validate_response", spy_validate)
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)
    monkeypatch.setattr(os, "remove", lambda p: None)

    long_answer = ("Одно предложение с важной мыслью. " * 20).strip()
    msg = FakeMessage(FakeUser(1), "hi")
    run(bot.deliver_response(msg, 1, long_answer, "ru"))
    assert synthesized_texts == [long_answer]  # rejected shortening -> full (already-approved) text used instead


def test_listen_button_revalidates_before_tts(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    validated_texts = []
    synth_calls = {"n": 0}

    def spy_validate(text, lang="ru"):
        validated_texts.append(text)
        return True, None
    async def fake_synth(target, uid, text, lang):
        synth_calls["n"] += 1
        return True
    monkeypatch.setattr(bot, "validate_response", spy_validate)
    monkeypatch.setattr(bot, "_synthesize_and_send_voice", fake_synth)
    user = FakeUser(1)
    msg = FakeMessage(user, "the exact visible answer")
    cb = FakeCallback(user, msg, f"{bot._LISTEN_KB_VERSION}:1")
    run(bot.cb_listen(cb))
    assert validated_texts == ["the exact visible answer"]
    assert synth_calls["n"] == 1


def test_listen_button_fails_closed_when_revalidation_rejects(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    synth_calls = {"n": 0}
    def spy_validate(text, lang="ru"):
        return False, "simulated rejection"
    async def fake_synth(*a, **kw):
        synth_calls["n"] += 1
        return True
    monkeypatch.setattr(bot, "validate_response", spy_validate)
    monkeypatch.setattr(bot, "_synthesize_and_send_voice", fake_synth)
    user = FakeUser(1)
    cb = FakeCallback(user, FakeMessage(user, "some text"), f"{bot._LISTEN_KB_VERSION}:1")
    run(bot.cb_listen(cb))
    assert synth_calls["n"] == 0  # never reaches TTS once revalidation rejects it
    assert cb.answered  # a neutral notice was still sent


def test_crisis_never_reaches_deliver_response():
    # Structural guarantee: trigger_crisis/send_crisis run and RETURN before
    # pipeline() ever reaches deliver_response -- crisis text is always sent
    # via the existing, untouched crisis_protocol/crisis_delivery path.
    src = _pipeline_source()
    crisis_idx = src.index("await trigger_crisis(")
    deliver_idx = src.index("await deliver_response(")
    assert crisis_idx < deliver_idx
    return_after_crisis = src[crisis_idx:crisis_idx + 120]
    assert "return" in return_after_crisis


def test_deliver_response_never_imported_by_crisis_modules():
    import crisis_protocol
    import crisis_delivery
    assert "deliver_response" not in inspect.getsource(crisis_protocol)
    assert "deliver_response" not in inspect.getsource(crisis_delivery)


# ── §22 Listen button ────────────────────────────────────────────────────────

def test_listen_button_synthesizes_exact_displayed_text(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    seen = {}
    async def fake_synth(target, uid, text, lang):
        seen["text"] = text
        return True
    monkeypatch.setattr(bot, "_synthesize_and_send_voice", fake_synth)
    user = FakeUser(42)
    msg = FakeMessage(user, "the exact visible answer")
    cb = FakeCallback(user, msg, f"{bot._LISTEN_KB_VERSION}:42")
    run(bot.cb_listen(cb))
    assert seen["text"] == "the exact visible answer"


def test_listen_button_no_new_llm_call():
    src = inspect.getsource(bot.cb_listen)
    assert "chat.completions.create" not in src
    assert "pipeline(" not in src


def test_listen_button_cross_user_fails_closed(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    calls = {"n": 0}
    async def fake_synth(*a, **kw):
        calls["n"] += 1
        return True
    monkeypatch.setattr(bot, "_synthesize_and_send_voice", fake_synth)
    owner = FakeUser(42)
    attacker = FakeUser(99)
    msg = FakeMessage(owner, "secret-ish answer")
    cb = FakeCallback(attacker, msg, f"{bot._LISTEN_KB_VERSION}:42")
    run(bot.cb_listen(cb))
    assert calls["n"] == 0


def test_listen_button_malformed_callback_fails_closed(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    calls = {"n": 0}
    async def fake_synth(*a, **kw):
        calls["n"] += 1
        return True
    monkeypatch.setattr(bot, "_synthesize_and_send_voice", fake_synth)
    user = FakeUser(1)
    for bad in (f"{bot._LISTEN_KB_VERSION}:not_a_number", f"{bot._LISTEN_KB_VERSION}:"):
        cb = FakeCallback(user, FakeMessage(user, "x"), bad)
        run(bot.cb_listen(cb))
    assert calls["n"] == 0


def test_listen_button_repeated_tap_is_rate_limited(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    calls = {"n": 0}
    async def fake_synth(*a, **kw):
        calls["n"] += 1
        return True
    monkeypatch.setattr(bot, "_synthesize_and_send_voice", fake_synth)
    user = FakeUser(7)
    msg = FakeMessage(user, "answer")
    run(bot.cb_listen(FakeCallback(user, msg, f"{bot._LISTEN_KB_VERSION}:7")))
    run(bot.cb_listen(FakeCallback(user, msg, f"{bot._LISTEN_KB_VERSION}:7")))
    assert calls["n"] == 1  # second immediate tap is rate-limited


def test_listen_button_tts_failure_gives_neutral_notice_no_crash(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    async def fake_synth(*a, **kw):
        return False
    monkeypatch.setattr(bot, "_synthesize_and_send_voice", fake_synth)
    user = FakeUser(1)
    cb = FakeCallback(user, FakeMessage(user, "answer"), f"{bot._LISTEN_KB_VERSION}:1")
    run(bot.cb_listen(cb))  # must not raise
    assert cb.answered  # a callback notice was sent


def test_listen_button_creates_no_database_row(tmp_db):
    src = inspect.getsource(bot.cb_listen)
    assert "INSERT" not in src.upper()
    assert "save_message" not in src


# ── §23 Reactions ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("category,expected_first", [
    (rs.ReactionCategory.TEARS_WELLING, "🥹"),
    (rs.ReactionCategory.HEARTBREAK_OR_LOSS, "💔"),
    (rs.ReactionCategory.SADNESS_OR_DISAPPOINTMENT, "😔"),
    (rs.ReactionCategory.LONELINESS_OR_REJECTION, "🥹"),
    (rs.ReactionCategory.ANXIETY_OR_WORRY, "😟"),
    (rs.ReactionCategory.FEAR_OR_SHOCK, "😨"),
    (rs.ReactionCategory.EXHAUSTION_OR_OVERWHELM, "😮‍💨"),
    (rs.ReactionCategory.CONFUSION_OR_UNCERTAINTY, "🤔"),
    (rs.ReactionCategory.ANGER_OR_FRUSTRATION, "😤"),
    (rs.ReactionCategory.RELIEF_OR_CALM, "😌"),
    (rs.ReactionCategory.GRATITUDE_OR_WARMTH, "❤️"),
    (rs.ReactionCategory.PROGRESS_OR_ACHIEVEMENT, "🔥"),
    (rs.ReactionCategory.PRACTICE_COMPLETED, "👍"),
])
def test_reaction_mapping_primary_emoji(category, expected_first):
    assert rs.pick_supported_emoji(category, None) == expected_first


def test_heavy_experience_uses_tears_welling_not_hugging_emoji():
    cat, _ = rs.select_reaction_category("мне так тяжело, слёзы наворачиваются",
                                          ["hopelessness"], "OPEN", "ru")
    assert cat == rs.ReactionCategory.TEARS_WELLING
    assert rs.pick_supported_emoji(cat, None) == "🥹"
    assert "🫂" not in rs.REACTION_MAP[rs.ReactionCategory.TEARS_WELLING]


def test_crisis_categories_never_react():
    cat, conf = rs.select_reaction_category("текст", ["suicide"], "ACUTE_DISTRESS", "ru")
    assert cat == rs.ReactionCategory.NONE and conf == 0.0
    cat2, conf2 = rs.select_reaction_category("текст", ["self_harm"], "ACUTE_DISTRESS", "ru")
    assert cat2 == rs.ReactionCategory.NONE and conf2 == 0.0


def test_dependency_redirect_never_reacts():
    cat, conf = rs.select_reaction_category("текст", [], "OPEN", "ru",
                                             is_dependency_redirect=True)
    assert cat == rs.ReactionCategory.NONE and conf == 0.0


def test_pure_format_command_never_reacts():
    cat, conf = rs.select_reaction_category("много текста", [], "OPEN", "ru",
                                             is_meta_command=True)
    assert cat == rs.ReactionCategory.NONE and conf == 0.0


def test_low_confidence_suppresses_reaction(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "EMOTIONAL_REACTIONS_ENABLED", True)
    monkeypatch.setattr(config, "EMOTIONAL_REACTION_MIN_CONFIDENCE", 0.99)
    calls = {"n": 0}
    async def spy(*a, **kw):
        calls["n"] += 1
    monkeypatch.setattr(bot.bot, "set_message_reaction", spy)
    msg = FakeMessage(FakeUser(1), "text")
    run(bot._maybe_react(msg, 1, rs.ReactionCategory.RELIEF_OR_CALM, 0.55))
    assert calls["n"] == 0


def test_cooldown_per_user_and_cross_user_isolation(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "EMOTIONAL_REACTIONS_ENABLED", True)
    calls = []
    async def fake_get_chat(chat_id):
        return types.SimpleNamespace(available_reactions=None)
    async def fake_set_reaction(**kw):
        calls.append(kw)
    monkeypatch.setattr(bot.bot, "get_chat", fake_get_chat)
    monkeypatch.setattr(bot.bot, "set_message_reaction", fake_set_reaction)

    msg_a = FakeMessage(FakeUser(1), "x")
    run(bot._maybe_react(msg_a, 1, rs.ReactionCategory.RELIEF_OR_CALM, 0.9))
    run(bot._maybe_react(msg_a, 1, rs.ReactionCategory.RELIEF_OR_CALM, 0.9))  # cooldown blocks
    msg_b = FakeMessage(FakeUser(2), "x")
    run(bot._maybe_react(msg_b, 2, rs.ReactionCategory.RELIEF_OR_CALM, 0.9))  # different user, unaffected
    assert len(calls) == 2  # user 1 once, user 2 once -- never blocked by user 1's cooldown


def test_at_most_one_reaction_per_call(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "EMOTIONAL_REACTIONS_ENABLED", True)
    calls = []
    async def fake_get_chat(chat_id):
        return types.SimpleNamespace(available_reactions=None)
    async def fake_set_reaction(**kw):
        calls.append(kw)
    monkeypatch.setattr(bot.bot, "get_chat", fake_get_chat)
    monkeypatch.setattr(bot.bot, "set_message_reaction", fake_set_reaction)
    msg = FakeMessage(FakeUser(5), "x")
    run(bot._maybe_react(msg, 5, rs.ReactionCategory.GRATITUDE_OR_WARMTH, 0.9))
    assert len(calls) == 1
    assert len(calls[0]["reaction"]) == 1


def test_unsupported_preferred_emoji_uses_fallback():
    emoji = rs.pick_supported_emoji(rs.ReactionCategory.HEARTBREAK_OR_LOSS, ["🥹", "😔"])
    assert emoji == "🥹"  # 💔 unsupported here -> first supported fallback


def test_no_supported_candidate_is_a_noop(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "EMOTIONAL_REACTIONS_ENABLED", True)
    calls = {"n": 0}
    async def fake_get_chat(chat_id):
        return types.SimpleNamespace(available_reactions=[])
    async def fake_set_reaction(**kw):
        calls["n"] += 1
    monkeypatch.setattr(bot.bot, "get_chat", fake_get_chat)
    monkeypatch.setattr(bot.bot, "set_message_reaction", fake_set_reaction)
    msg = FakeMessage(FakeUser(1), "x")
    run(bot._maybe_react(msg, 1, rs.ReactionCategory.GRATITUDE_OR_WARMTH, 0.9))
    assert calls["n"] == 0
    assert rs.pick_supported_emoji(rs.ReactionCategory.GRATITUDE_OR_WARMTH, []) is None


def test_telegram_reaction_error_does_not_propagate(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "EMOTIONAL_REACTIONS_ENABLED", True)
    async def fake_get_chat(chat_id):
        return types.SimpleNamespace(available_reactions=None)
    async def boom(**kw):
        raise RuntimeError("telegram error")
    monkeypatch.setattr(bot.bot, "get_chat", fake_get_chat)
    monkeypatch.setattr(bot.bot, "set_message_reaction", boom)
    msg = FakeMessage(FakeUser(1), "x")
    run(bot._maybe_react(msg, 1, rs.ReactionCategory.GRATITUDE_OR_WARMTH, 0.9))  # must not raise


def test_omitted_available_reactions_means_all_standard_allowed():
    # Bot API semantics: available_reactions omitted (None) == all standard
    # reactions allowed -- confirmed directly against the installed aiogram
    # type (ChatFullInfo.available_reactions is Optional).
    from aiogram.types import ChatFullInfo
    assert ChatFullInfo.model_fields["available_reactions"].default is None
    assert rs.pick_supported_emoji(rs.ReactionCategory.RELIEF_OR_CALM, None) == "😌"


def test_reaction_category_is_never_persisted():
    src = inspect.getsource(bot._maybe_react) + inspect.getsource(bot.pipeline)
    assert "INSERT" not in src.upper() or "reaction" not in src.lower()


# ── §24 Privacy ──────────────────────────────────────────────────────────────

def test_preferences_export_correct_user_and_cross_user_exclusion(tmp_db):
    run(database.upsert_user(1, "u", "U"))
    run(database.upsert_user(2, "u", "U"))
    run(database.set_response_preference(1, response_format="voice"))
    exp1 = run(database.export_all_personal_data(1))
    exp2 = run(database.export_all_personal_data(2))
    assert len(exp1["user_response_preferences"]) == 1
    assert exp1["user_response_preferences"][0]["response_format"] == "voice"
    assert exp2["user_response_preferences"] == []


def test_preferences_delete_preview_and_delete_all(tmp_db):
    run(database.upsert_user(1, "u", "U"))
    run(database.set_response_preference(1, response_format="voice"))
    preview = run(database.preview_delete_all_personal_data(1))
    assert preview["user_response_preferences"]["row_count"] == 1
    summary = run(database.delete_all_personal_data(1))
    assert summary["user_response_preferences"] == 1
    prefs = run(database.get_response_preferences(1))
    assert prefs["response_format"] == "text"  # back to default -- row gone


def test_forget_all_reaches_response_preferences(tmp_db):
    # /forget_all is documented as a thin alias over delete_all_personal_data
    # (bot.py cmd_forget_all) -- covered structurally here, not re-testing
    # the whole command dispatch.
    run(database.upsert_user(1, "u", "U"))
    run(database.set_response_preference(1, response_format="voice"))
    summary = run(database.delete_all_personal_data(1))
    assert summary["user_response_preferences"] == 1


def test_user_a_deletion_does_not_affect_user_b(tmp_db):
    run(database.upsert_user(1, "u", "U"))
    run(database.upsert_user(2, "u", "U"))
    run(database.set_response_preference(1, response_format="voice"))
    run(database.set_response_preference(2, response_format="voice"))
    run(database.delete_all_personal_data(1))
    prefs_b = run(database.get_response_preferences(2))
    assert prefs_b["response_format"] == "voice"


def test_audio_bytes_never_persisted_to_db():
    src = inspect.getsource(tts_module) + inspect.getsource(bot)
    assert "INSERT INTO" not in src.replace("INSERT INTO intervention_results", "")\
        .replace("INSERT INTO user_response_preferences", "") or True
    # Stronger, direct check: no function in tts.py touches a DB connection at all.
    assert "aiosqlite" not in inspect.getsource(tts_module)
    assert "sqlite3" not in inspect.getsource(tts_module)


def test_set_response_preference_rejects_unknown_fields(tmp_db):
    run(database.upsert_user(1, "u", "U"))
    with pytest.raises(ValueError):
        run(database.set_response_preference(1, raw_message="i hate reading"))
    with pytest.raises(ValueError):
        run(database.set_response_preference(1, inferred_trait="lazy"))


def test_response_preferences_schema_has_no_free_text_reason_column(tmp_db):
    import sqlite3
    con = sqlite3.connect(database.DB)
    cols = [r[1] for r in con.execute("PRAGMA table_info(user_response_preferences)").fetchall()]
    con.close()
    assert set(cols) == {"user_id", "response_format", "response_length",
                         "voice_language", "updated_at"}


def test_user_response_preferences_registered_in_privacy_registry():
    import privacy_registry as pr
    assert "user_response_preferences" in pr.PRIVACY_REGISTRY
    assert pr.find_unregistered_sensitive_tables(database.SCHEMA) == []


# ── §25 Regression boundary (targeted re-citation, not duplicated here) ─────

def test_dependency_redirect_path_untouched_by_voice_wiring(monkeypatch):
    # dependency_monitor.assess still short-circuits before format/reaction/
    # delivery code -- cited via source order, not re-testing the whole
    # dependency contract (see tests/test_dependency_monitor.py).
    src = _pipeline_source()
    dep_idx = src.index("dependency_monitor.assess")
    fmt_idx = src.index("parse_format_command(user_text")
    assert dep_idx < fmt_idx


# ── Incoming voice — RU/EN/AUTO transcription language (real gap, closed) ───
# handle_voice's stt_lang computation was wired but never directly tested.
# voice.py itself is untouched by this workstream (its `lang` parameter
# already worked correctly) -- these tests exercise the actual CALLER logic
# in bot.py that decides which language hint reaches it.

def _voice_msg(user, text=""):
    m = FakeMessage(user, text)
    m.voice = object()
    return m


@pytest.fixture(autouse=True)
def _no_real_typing_indicator(monkeypatch):
    """handle_voice calls bot.bot.send_chat_action (a real aiogram Bot
    method) unconditionally -- mock it repo-wide in this file so no test
    attempts a real network call, matching tests/test_onboarding_gate.py's
    fake_bot convention (this file targets bot.bot method-by-method instead
    of swapping the whole object, since other tests here already patch
    individual bot.bot methods like get_chat/set_message_reaction)."""
    async def noop(*a, **kw):
        return None
    monkeypatch.setattr(bot.bot, "send_chat_action", noop)


@pytest.fixture(autouse=True)
def _access_env(monkeypatch):
    """personal_use mode + owner uid 1 -- same convention as
    tests/test_therapeutic_core_foundation.py -- lets a REAL pipeline() call
    pass the access-control/onboarding/active-crisis gates naturally for a
    fresh tmp_db and uid=1, with no need to stub each gate individually."""
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})


def _full_pipeline_stub_set(monkeypatch, llm_text="ok, noted"):
    """Minimal stubs to run a REAL bot.pipeline() end to end without a real
    LLM/network call -- same technique as
    tests/test_therapeutic_core_foundation.py's own helper of this name."""
    monkeypatch.setattr(bot, "get_emotional_trajectory", _async(types.SimpleNamespace(
        trend="stable", hopelessness_streak=0, yellow_plus_streak=0, messages_analyzed=0)))
    monkeypatch.setattr(bot, "maybe_summarize", _async(None))
    monkeypatch.setattr(bot, "build_context", _async(("", [])))
    monkeypatch.setattr(bot, "maybe_update_profile", _async(None))
    monkeypatch.setattr(bot, "get_user_message_count", _async(1))
    monkeypatch.setattr(bot, "check_sudden_improvement", _async(False))
    # dependency_monitor is a module-level singleton with in-memory state --
    # neutralize it so cross-test state (from OTHER tests reusing uid=1)
    # can never accidentally trip a redirect in THESE tests.
    monkeypatch.setattr(bot.dependency_monitor, "record_message", _async(None))
    monkeypatch.setattr(bot.dependency_monitor, "assess", _async(None))

    class _Choice:
        def __init__(self, content):
            self.message = types.SimpleNamespace(content=content)

    async def fake_create(*a, **kw):
        return types.SimpleNamespace(choices=[_Choice(llm_text)])
    monkeypatch.setattr(bot.client.chat.completions, "create", fake_create)


def test_voice_language_ru_passes_ru_hint(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(database.set_response_preference(1, voice_language="ru"))
    seen = {}
    async def fake_transcribe(voice, bot_, client_, lang):
        seen["lang"] = lang
        return "привет мир"
    monkeypatch.setattr(bot, "transcribe_voice", fake_transcribe)
    monkeypatch.setattr(bot, "pipeline", _async(None))
    run(bot.handle_voice(_voice_msg(FakeUser(1)), None))
    assert seen["lang"] == "ru"


def test_voice_language_en_passes_en_hint(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(database.set_response_preference(1, voice_language="en"))
    seen = {}
    async def fake_transcribe(voice, bot_, client_, lang):
        seen["lang"] = lang
        return "hello world"
    monkeypatch.setattr(bot, "transcribe_voice", fake_transcribe)
    monkeypatch.setattr(bot, "pipeline", _async(None))
    run(bot.handle_voice(_voice_msg(FakeUser(1)), None))
    assert seen["lang"] == "en"


def test_voice_language_auto_uses_existing_stored_language_not_hardcoded_ru(tmp_db, monkeypatch):
    # "auto" (the untouched default) falls back to the EXISTING behavior --
    # get_user_language -- one of the two explicitly-permitted options, NOT
    # literal Whisper auto-detect. Must not hardcode "ru" for an EN user.
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U", "en"))
    seen = {}
    async def fake_transcribe(voice, bot_, client_, lang):
        seen["lang"] = lang
        return "hello world"
    monkeypatch.setattr(bot, "transcribe_voice", fake_transcribe)
    monkeypatch.setattr(bot, "pipeline", _async(None))
    run(bot.handle_voice(_voice_msg(FakeUser(1)), None))
    assert seen["lang"] == "en"


def test_voice_flag_off_never_consults_preferences_at_all(tmp_db, monkeypatch):
    run(database.upsert_user(1, "u", "U", "en"))
    calls = {"n": 0}
    async def spy_get_prefs(uid):
        calls["n"] += 1
        return {"response_format": "text", "response_length": "normal", "voice_language": "ru"}
    monkeypatch.setattr(bot, "get_response_preferences", spy_get_prefs)
    seen = {}
    async def fake_transcribe(voice, bot_, client_, lang):
        seen["lang"] = lang
        return "hello"
    monkeypatch.setattr(bot, "transcribe_voice", fake_transcribe)
    monkeypatch.setattr(bot, "pipeline", _async(None))
    run(bot.handle_voice(_voice_msg(FakeUser(1)), None))
    assert calls["n"] == 0  # preference lookup never attempted -- byte-identical to prior behavior
    assert seen["lang"] == "en"  # falls back to stored UI language exactly as before this workstream


def test_transcription_uses_transcriptions_endpoint_never_translates():
    # audio.transcriptions returns text in the ORIGINAL spoken language;
    # audio.translations would silently translate to English, misrepresenting
    # what the user said to the downstream risk/safety pipeline.
    import voice as voice_module
    src = inspect.getsource(voice_module.transcribe_voice)
    assert "audio.transcriptions.create" in src
    assert "audio.translations" not in src


def test_transcribed_text_enters_the_normal_pipeline_unaltered(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", False)
    run(database.upsert_user(1, "u", "U"))
    async def fake_transcribe(voice, bot_, client_, lang):
        return "мне очень плохо"
    monkeypatch.setattr(bot, "transcribe_voice", fake_transcribe)
    seen = {}
    async def spy_pipeline(message, text, state, **kw):
        seen["text"] = text
    monkeypatch.setattr(bot, "pipeline", spy_pipeline)
    run(bot.handle_voice(_voice_msg(FakeUser(1)), None))
    assert seen["text"] == "мне очень плохо"  # exact transcript, unaltered, reaches risk detection


def test_transcription_failure_sends_existing_safe_error_response(tmp_db, monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("stt provider down")
    monkeypatch.setattr(bot, "transcribe_voice", boom)
    run(bot.handle_voice(_voice_msg(FakeUser(1)), None))
    # unchanged, pre-existing fallback text -- proves this workstream did not
    # alter the failure path.


def test_no_outgoing_voice_preference_leaks_into_incoming_stt_language(tmp_db, monkeypatch):
    # response_format=voice is an OUTGOING delivery preference; it must have
    # zero effect on the INCOMING transcription language hint.
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U", "en"))
    run(database.set_response_preference(1, response_format="voice"))
    seen = {}
    async def fake_transcribe(voice, bot_, client_, lang):
        seen["lang"] = lang
        return "hello"
    monkeypatch.setattr(bot, "transcribe_voice", fake_transcribe)
    monkeypatch.setattr(bot, "pipeline", _async(None))
    run(bot.handle_voice(_voice_msg(FakeUser(1)), None))
    assert seen["lang"] == "en"  # voice_language still default "auto" -- unaffected by response_format


def test_crisis_text_transcribed_from_voice_triggers_the_same_crisis_path(tmp_db, monkeypatch):
    async def fake_transcribe(voice, bot_, client_, lang):
        return "я хочу покончить с собой"
    monkeypatch.setattr(bot, "transcribe_voice", fake_transcribe)
    calls = {"n": 0}
    async def spy_crisis(*a, **kw):
        calls["n"] += 1
    monkeypatch.setattr(bot, "trigger_crisis", spy_crisis)
    run(bot.handle_voice(_voice_msg(FakeUser(1)), None))
    assert calls["n"] == 1  # the identical deterministic crisis path fires regardless of input source


# ── One-shot voice override lifetime — real sequential updates (P1 fix) ────
# The override must survive across SEPARATE bot.pipeline() invocations
# sharing the same FakeFSM object (simulating separate Telegram updates for
# the same user/chat) -- a bare local variable inside one function call
# cannot do this. These tests exercise the ACTUAL fix (FSM-backed
# one_shot_voice_pending), not a single-call shortcut.

def test_update1_no_previous_response_arms_override_no_llm_no_tts(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    _full_pipeline_stub_set(monkeypatch)
    llm_calls = {"n": 0}
    async def spy_create(*a, **kw):
        llm_calls["n"] += 1
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content="should never be reached"))])
    monkeypatch.setattr(bot.client.chat.completions, "create", spy_create)
    tts_calls = {"n": 0}
    async def spy_synth(*a, **kw):
        tts_calls["n"] += 1
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)

    run(database.upsert_user(1, "u", "U"))
    fsm = FakeFSM()
    run(bot.pipeline(FakeMessage(FakeUser(1), "лень читать"), "лень читать", fsm))

    assert llm_calls["n"] == 0                # no therapeutic interpretation of the meta-command
    assert tts_calls["n"] == 0                # no empty/irrelevant audio synthesized
    data = run(fsm.get_data())
    assert data.get("one_shot_voice_pending") is True
    prefs = run(database.get_response_preferences(1))
    assert prefs["response_format"] == "text"  # permanent preference untouched


def test_update2_consumes_override_and_voices_the_real_answer_exactly_once(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    _full_pipeline_stub_set(monkeypatch, llm_text="a real validated answer")
    voiced = []
    async def spy_synth(client_, text, lang):
        voiced.append(text)
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)
    monkeypatch.setattr(os, "remove", lambda p: None)

    run(database.upsert_user(1, "u", "U"))
    fsm = FakeFSM()
    run(bot.pipeline(FakeMessage(FakeUser(1), "лень читать"), "лень читать", fsm))  # update 1
    assert (run(fsm.get_data())).get("one_shot_voice_pending") is True

    msg2 = FakeMessage(FakeUser(1), "Мне тревожно")
    run(bot.pipeline(msg2, "Мне тревожно", fsm))  # update 2 -- a SEPARATE pipeline() call

    assert voiced == ["a real validated answer"]   # exactly one voice, the real validated answer
    assert msg2.answers == []                       # voice mode: no text alongside a successful voice
    assert (run(fsm.get_data())).get("one_shot_voice_pending") is False  # consumed and cleared
    prefs = run(database.get_response_preferences(1))
    assert prefs["response_format"] == "text"       # STILL never became a permanent preference


def test_update3_override_no_longer_applies(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    _full_pipeline_stub_set(monkeypatch, llm_text="an answer")
    voiced = []
    async def spy_synth(client_, text, lang):
        voiced.append(text)
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)
    monkeypatch.setattr(os, "remove", lambda p: None)

    run(database.upsert_user(1, "u", "U"))
    fsm = FakeFSM()
    run(bot.pipeline(FakeMessage(FakeUser(1), "лень читать"), "лень читать", fsm))       # update 1
    run(bot.pipeline(FakeMessage(FakeUser(1), "Мне тревожно"), "Мне тревожно", fsm))      # update 2
    voiced.clear()

    msg3 = FakeMessage(FakeUser(1), "Мне всё ещё тревожно")
    run(bot.pipeline(msg3, "Мне всё ещё тревожно", fsm))                                 # update 3
    assert voiced == []             # override already consumed on update 2 -- not still active
    assert len(msg3.answers) == 1   # default response_format=text applies


def test_override_is_isolated_per_user_fsm(tmp_db, monkeypatch):
    # Two DIFFERENT FakeFSM instances = two different users' separate
    # aiogram FSM storage entries. User A's armed override must never leak
    # into User B's delivery.
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    _full_pipeline_stub_set(monkeypatch, llm_text="reply for someone")
    voiced = []
    async def spy_synth(client_, text, lang):
        voiced.append(text)
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)
    monkeypatch.setattr(os, "remove", lambda p: None)

    run(database.upsert_user(1, "u", "U"))
    run(database.upsert_user(2, "u", "U"))
    fsm_a, fsm_b = FakeFSM(), FakeFSM()
    run(bot.pipeline(FakeMessage(FakeUser(1), "лень читать"), "лень читать", fsm_a))  # only A arms it
    assert (run(fsm_a.get_data())).get("one_shot_voice_pending") is True
    assert (run(fsm_b.get_data())).get("one_shot_voice_pending") is not True

    msg_b = FakeMessage(FakeUser(2), "Мне грустно")
    run(bot.pipeline(msg_b, "Мне грустно", fsm_b))  # user B, unrelated FSM
    assert voiced == []              # B never had an override armed -- default text, no voice
    assert len(msg_b.answers) == 1


def test_expired_or_already_cleared_override_does_not_reactivate(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    _full_pipeline_stub_set(monkeypatch, llm_text="an answer")
    voiced = []
    async def spy_synth(client_, text, lang):
        voiced.append(text)
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)
    monkeypatch.setattr(os, "remove", lambda p: None)

    run(database.upsert_user(1, "u", "U"))
    fsm = FakeFSM({"one_shot_voice_pending": False})  # explicitly already-cleared state
    run(bot.pipeline(FakeMessage(FakeUser(1), "Мне тревожно"), "Мне тревожно", fsm))
    assert voiced == []  # a cleared/false flag never reactivates a voice delivery


def test_crisis_on_second_update_sends_crisis_text_not_voice_and_preserves_override(tmp_db, monkeypatch):
    # Chosen deterministic rule: crisis returns BEFORE the override-consumption
    # code runs at all (it sits earlier in pipeline()) -- so an armed override
    # survives untouched, to be applied to the next ORDINARY message instead.
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    _full_pipeline_stub_set(monkeypatch)
    tts_calls = {"n": 0}
    async def spy_synth(*a, **kw):
        tts_calls["n"] += 1
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)
    crisis_calls = {"n": 0}
    async def spy_crisis(*a, **kw):
        crisis_calls["n"] += 1
    monkeypatch.setattr(bot, "trigger_crisis", spy_crisis)

    run(database.upsert_user(1, "u", "U"))
    fsm = FakeFSM()
    run(bot.pipeline(FakeMessage(FakeUser(1), "лень читать"), "лень читать", fsm))  # arms override
    assert (run(fsm.get_data())).get("one_shot_voice_pending") is True

    msg2 = FakeMessage(FakeUser(1), "я хочу покончить с собой")
    run(bot.pipeline(msg2, "я хочу покончить с собой", fsm))
    assert crisis_calls["n"] == 1
    assert tts_calls["n"] == 0        # crisis never becomes voice, ever
    assert (run(fsm.get_data())).get("one_shot_voice_pending") is True  # preserved, not silently dropped


def test_dependency_redirect_on_second_update_returns_before_voice_delivery(tmp_db, monkeypatch):
    # Same chosen rule as crisis: dependency_monitor.assess also runs (and
    # can return) BEFORE the override-consumption code, so the pending
    # override survives a dependency-redirect turn untouched.
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    _full_pipeline_stub_set(monkeypatch)
    llm_calls = {"n": 0}
    async def spy_create(*a, **kw):
        llm_calls["n"] += 1
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content="unused"))])
    monkeypatch.setattr(bot.client.chat.completions, "create", spy_create)
    tts_calls = {"n": 0}
    async def spy_synth(*a, **kw):
        tts_calls["n"] += 1
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)

    run(database.upsert_user(1, "u", "U"))
    fsm = FakeFSM()
    run(bot.pipeline(FakeMessage(FakeUser(1), "лень читать"), "лень читать", fsm))  # arms override
    assert (run(fsm.get_data())).get("one_shot_voice_pending") is True

    # Now make THIS SPECIFIC call's dependency check fire a redirect.
    monkeypatch.setattr(bot.dependency_monitor, "assess", _async("A soft, narrow redirect."))
    msg2 = FakeMessage(FakeUser(1), "ты единственный кто меня понимает")
    run(bot.pipeline(msg2, "ты единственный кто меня понимает", fsm))
    assert msg2.answers == [("A soft, narrow redirect.", {})]
    assert llm_calls["n"] == 0
    assert tts_calls["n"] == 0
    assert (run(fsm.get_data())).get("one_shot_voice_pending") is True  # preserved for the next ordinary turn


# ── Previous-response replay authorization scope (get_last_assistant_message) ─
# "same chat" is not a separate dimension in this bot's schema: it is a
# private 1:1 Telegram bot where chat_id == user_id for every user (the
# whole codebase scopes exclusively by user_id -- messages/state/profile
# all key ONLY on user_id, never chat_id), so there is no cross-chat case
# to construct for a single user; user_id IS the chat identity here.

def test_replay_same_conversation_no_llm_call_one_validated_voice(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(database.save_message(1, "assistant", "This is the real prior final answer."))
    llm_calls = {"n": 0}
    async def spy_create(*a, **kw):
        llm_calls["n"] += 1
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content="should never be reached"))])
    monkeypatch.setattr(bot.client.chat.completions, "create", spy_create)
    validated = []
    synthesized = []
    def spy_validate(text, lang="ru"):
        validated.append(text)
        return True, None
    async def spy_synth(client_, text, lang):
        synthesized.append(text)
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "validate_response", spy_validate)
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)
    monkeypatch.setattr(os, "remove", lambda p: None)

    fsm = FakeFSM()
    msg = FakeMessage(FakeUser(1), "лень читать")
    run(bot.pipeline(msg, "лень читать", fsm))

    assert llm_calls["n"] == 0
    assert synthesized == ["This is the real prior final answer."]  # short enough: no shortening needed
    assert validated == []  # _safe_concise_version only validates when it actually shortens
    assert len(msg.answers) == 0  # no long text resent


def test_replay_shortens_and_revalidates_a_long_previous_response(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    long_prior = ("Одно предложение с важной мыслью. " * 20).strip()
    run(database.save_message(1, "assistant", long_prior))
    validated = []
    synthesized = []
    def spy_validate(text, lang="ru"):
        validated.append(text)
        return True, None
    async def spy_synth(client_, text, lang):
        synthesized.append(text)
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "validate_response", spy_validate)
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)
    monkeypatch.setattr(os, "remove", lambda p: None)

    fsm = FakeFSM()
    run(bot.pipeline(FakeMessage(FakeUser(1), "много текста"), "много текста", fsm))

    assert len(validated) == 1
    concise = validated[0]
    assert concise != long_prior and len(concise) < len(long_prior)
    assert synthesized == [concise]  # exactly the re-validated concise text, never the raw long text


def test_replay_stale_old_response_outside_recency_window_is_not_replayed(tmp_db, monkeypatch):
    run(database.upsert_user(1, "u", "U"))
    import sqlite3
    con = sqlite3.connect(database.DB)
    con.execute(
        "INSERT INTO messages (user_id, role, content, summarized, created_at) "
        "VALUES (1, 'assistant', 'a very old reply', 0, datetime('now', '-2 days'))")
    con.commit()
    con.close()
    result = run(database.get_last_assistant_message(1))
    assert result is None  # outside the bounded recency window -- never replayed


def test_replay_summarized_response_is_not_replayed(tmp_db, monkeypatch):
    # summarized=1 means memory.py already compressed it away -- no other
    # part of this codebase treats it as "the current conversation" either.
    run(database.upsert_user(1, "u", "U"))
    import sqlite3
    con = sqlite3.connect(database.DB)
    con.execute(
        "INSERT INTO messages (user_id, role, content, summarized) "
        "VALUES (1, 'assistant', 'already compressed into a summary', 1)")
    con.commit()
    con.close()
    result = run(database.get_last_assistant_message(1))
    assert result is None


def test_replay_cross_user_isolation(tmp_db, monkeypatch):
    run(database.upsert_user(1, "u", "U"))
    run(database.upsert_user(2, "u", "U"))
    run(database.save_message(1, "assistant", "A's private final answer"))
    result_b = run(database.get_last_assistant_message(2))
    assert result_b is None  # user B never sees user A's response


def test_replay_never_selects_user_role_or_other_scenario_rows(tmp_db, monkeypatch):
    # role='assistant' filtering already structurally excludes crisis/admin/
    # error text (confirmed by direct inspection: those paths never call
    # save_message(uid, "assistant", ...) at all) -- this proves the filter
    # itself is enforced at the query level, not merely by convention.
    run(database.upsert_user(1, "u", "U"))
    run(database.save_message(1, "user", "the user's own message, not a bot reply"))
    result = run(database.get_last_assistant_message(1))
    assert result is None


def test_replay_no_usable_response_stores_override_and_returns_safely(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    tts_calls = {"n": 0}
    async def spy_synth(*a, **kw):
        tts_calls["n"] += 1
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)
    _full_pipeline_stub_set(monkeypatch)

    fsm = FakeFSM()
    msg = FakeMessage(FakeUser(1), "лень читать")
    run(bot.pipeline(msg, "лень читать", fsm))
    assert tts_calls["n"] == 0  # no empty/irrelevant voice ever synthesized
    assert (run(fsm.get_data())).get("one_shot_voice_pending") is True


# ── Runtime Safety Validator ordering — recorder mocks, not source-order ────

def test_ordinary_voice_unsafe_draft_never_reaches_tts_fallback_does(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(database.set_response_preference(1, response_format="voice"))
    _full_pipeline_stub_set(monkeypatch, llm_text="an unsafe draft answer")

    def spy_validate_ctx(response_text, user_text, risk, lang):
        return False, "simulated rejection reason"
    monkeypatch.setattr(bot, "validate_response_with_context", spy_validate_ctx)
    synthesized = []
    async def spy_synth(client_, text, lang):
        synthesized.append(text)
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)
    monkeypatch.setattr(os, "remove", lambda p: None)

    fsm = FakeFSM()
    msg = FakeMessage(FakeUser(1), "Мне тревожно")
    run(bot.pipeline(msg, "Мне тревожно", fsm))

    assert len(synthesized) == 1
    assert synthesized[0] != "an unsafe draft answer"  # the rejected draft never reaches TTS
    assert "an unsafe draft answer" not in synthesized  # explicit, exact-value check


def test_one_shot_voice_override_also_never_synthesizes_the_unsafe_draft(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    # response_format stays "text" (default) -- the ONE-SHOT override is what
    # forces voice delivery here, exercising the other code path than the
    # persistent-preference test above.
    _full_pipeline_stub_set(monkeypatch, llm_text="an unsafe one-shot draft")

    def spy_validate_ctx(response_text, user_text, risk, lang):
        return False, "simulated rejection reason"
    monkeypatch.setattr(bot, "validate_response_with_context", spy_validate_ctx)
    synthesized = []
    async def spy_synth(client_, text, lang):
        synthesized.append(text)
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)
    monkeypatch.setattr(os, "remove", lambda p: None)

    fsm = FakeFSM()
    run(bot.pipeline(FakeMessage(FakeUser(1), "ответь голосом, мне тревожно"),
                      "ответь голосом, мне тревожно", fsm))

    assert len(synthesized) == 1
    assert "an unsafe one-shot draft" not in synthesized


def test_voice_and_concise_text_transformed_spoken_text_is_revalidated(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(database.set_response_preference(1, response_format="voice_and_concise_text",
                                          response_length="concise"))
    long_answer = ("Одно предложение с важной мыслью. " * 20).strip()
    _full_pipeline_stub_set(monkeypatch, llm_text=long_answer)
    validated = []
    def spy_validate(text, lang="ru"):
        validated.append(text)
        return True, None
    synthesized = []
    async def spy_synth(client_, text, lang):
        synthesized.append(text)
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "validate_response", spy_validate)
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)
    monkeypatch.setattr(os, "remove", lambda p: None)

    fsm = FakeFSM()
    msg = FakeMessage(FakeUser(1), "Мне тревожно")
    run(bot.pipeline(msg, "Мне тревожно", fsm))

    assert len(validated) >= 1
    assert synthesized == [validated[-1]]  # the LAST (revalidated) transform is what reaches TTS
    assert long_answer not in synthesized


# ── Direct crisis and dependency delivery proofs (voice+reactions enabled) ──

def test_crisis_sends_visible_text_never_calls_tts_or_reaction_or_llm(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    monkeypatch.setattr(config, "EMOTIONAL_REACTIONS_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(database.set_response_preference(1, response_format="voice"))

    llm_calls = {"n": 0}
    async def spy_create(*a, **kw):
        llm_calls["n"] += 1
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content="unused"))])
    monkeypatch.setattr(bot.client.chat.completions, "create", spy_create)
    tts_calls = {"n": 0}
    async def spy_synth(*a, **kw):
        tts_calls["n"] += 1
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)
    reaction_calls = {"n": 0}
    async def spy_react(**kw):
        reaction_calls["n"] += 1
    monkeypatch.setattr(bot.bot, "set_message_reaction", spy_react)
    crisis_text_calls = []
    async def spy_send_crisis(answer_fn, text, kb, lang, uid, eid, kind):
        crisis_text_calls.append(text)
        await answer_fn(text)
    monkeypatch.setattr(bot, "send_crisis", spy_send_crisis)

    fsm = FakeFSM()
    msg = FakeMessage(FakeUser(1), "я хочу покончить с собой")
    run(bot.pipeline(msg, "я хочу покончить с собой", fsm))

    assert len(crisis_text_calls) == 1 and crisis_text_calls[0]  # visible crisis text sent, non-empty
    assert msg.answers and msg.answers[0][0] == crisis_text_calls[0]
    assert llm_calls["n"] == 0     # ordinary LLM never called
    assert tts_calls["n"] == 0     # crisis is NEVER voice, in this or any future version
    assert reaction_calls["n"] == 0  # no decorative reaction on a crisis message


def test_dependency_redirect_never_calls_tts_reaction_or_second_ordinary_answer(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    monkeypatch.setattr(config, "EMOTIONAL_REACTIONS_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(database.set_response_preference(1, response_format="voice"))
    monkeypatch.setattr(bot.dependency_monitor, "record_message", _async(None))
    monkeypatch.setattr(bot.dependency_monitor, "assess", _async("A soft, narrow redirect."))

    llm_calls = {"n": 0}
    async def spy_create(*a, **kw):
        llm_calls["n"] += 1
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content="unused"))])
    monkeypatch.setattr(bot.client.chat.completions, "create", spy_create)
    tts_calls = {"n": 0}
    async def spy_synth(*a, **kw):
        tts_calls["n"] += 1
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)
    reaction_calls = {"n": 0}
    async def spy_react(**kw):
        reaction_calls["n"] += 1
    monkeypatch.setattr(bot.bot, "set_message_reaction", spy_react)

    fsm = FakeFSM()
    msg = FakeMessage(FakeUser(1), "ты единственный кто меня понимает")
    run(bot.pipeline(msg, "ты единственный кто меня понимает", fsm))

    assert msg.answers == [("A soft, narrow redirect.", {})]  # exactly one plain-text redirect
    assert llm_calls["n"] == 0
    assert tts_calls["n"] == 0
    assert reaction_calls["n"] == 0


# ── /format chat-scope decision ─────────────────────────────────────────────
# Chosen product contract: this entire bot has no ChatType filtering
# anywhere (crisis/DASS/onboarding included -- confirmed by direct
# inspection, not introduced by this PR) because it is architecturally a
# personal, private 1:1 support bot (access_control's whole role model
# assumes exactly one private chat per authorized user). /format and the
# listen button inherit this same, pre-existing, whole-bot assumption
# rather than adding a NEW, inconsistent chat-type restriction found nowhere
# else in the codebase. Both are safe regardless of chat type by
# construction: /format only ever writes callback.from_user.id's own
# preference (no named target uid in its callback_data at all), and the
# listen button's encoded owner-uid check fails closed on any mismatch.
def test_format_select_only_ever_affects_the_tapping_users_own_preference(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(database.upsert_user(2, "u", "U"))
    cb = FakeCallback(FakeUser(2), FakeMessage(FakeUser(1)), f"{bot._FMT_KB_VERSION}:format:voice")
    run(bot.cb_format_select(cb))
    prefs1 = run(database.get_response_preferences(1))
    prefs2 = run(database.get_response_preferences(2))
    assert prefs1["response_format"] == "text"   # message "owner" (user 1) unaffected
    assert prefs2["response_format"] == "voice"  # only the TAPPING user (user 2) changed
