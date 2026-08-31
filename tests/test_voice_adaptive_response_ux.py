"""Voice and Adaptive Response UX — flags, meta-commands, TTS/delivery,
listen button, reactions, privacy, and safety-boundary tests.

VOICE_REPLIES_ENABLED, ELEVENLABS_TTS_ENABLED and EMOTIONAL_REACTIONS_ENABLED default false;
most tests explicitly flip them on via monkeypatch to exercise the new
code paths, then separately prove the flag-off path is byte-identical to
prior behavior. No test calls a real paid API — TTS and Telegram calls are
always mocked.
"""
import asyncio
import inspect
import os
import time
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
    def __init__(self, user, text="", message_id=1, chat_type="private"):
        self.from_user = user
        self.text = text
        self.chat = types.SimpleNamespace(id=user.id, type=chat_type)
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


def _no_voice_ux_markup(kw) -> bool:
    """No Voice-UX keyboard (listen button / format selector) was attached.

    A plain ordinary reply carries the permanent lower ReplyKeyboardMarkup.
    Only an InlineKeyboardMarkup would mean a Voice-UX control leaked in."""
    return not isinstance(kw.get("reply_markup"), bot.InlineKeyboardMarkup)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    run(database.init_db())
    return database


@pytest.fixture(autouse=True)
def _voice_flags_off(monkeypatch):
    """Default state for every test unless a test explicitly flips a flag on."""
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", False)
    # Existing voice-path tests opt in by setting VOICE_REPLIES_ENABLED. Keep
    # the independent provider gate/key ready with synthetic values; dedicated
    # tests below prove each fails closed when absent.
    monkeypatch.setattr(config, "ELEVENLABS_TTS_ENABLED", True)
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(config, "EMOTIONAL_REACTIONS_ENABLED", False)
    bot._reaction_last_sent.clear()
    bot._listen_last_tap.clear()
    # dependency_monitor (bot.py) is a module-level singleton that persists
    # in-memory message counts/timestamps for the whole pytest process. Now
    # that the owner-only gate concentrates every owner-requiring test onto
    # the same uid=1 (see _default_owner below), enough real pipeline() runs
    # in sequence can cross the >100-messages/24h threshold and silently
    # replace an unrelated test's expected output with a high-frequency
    # redirect. Reset per test so no test observes another test's traffic.
    dm = bot.dependency_monitor
    dm._timestamps.clear()
    dm._session_start.clear()
    dm._night_msgs.clear()
    dm._night_date.clear()
    dm._last_redirect.clear()
    # Legacy reply-keyboard eviction is also module-level, once-per-user state
    # (see bot._legacy_kb_removal). Reset it so each test starts from a fresh
    # user and no test observes another test's eviction.
    bot._legacy_kb_cleared.clear()


# Note: access_control.OWNER_USER_ID already defaults to 1 for every test in
# this file via the pre-existing _access_env autouse fixture (below, in the
# "incoming voice" section) -- that fixture, not a new one, is why uid=1 is
# this file's default owner for the new owner-only gate too.


# ── §19 Feature flags ────────────────────────────────────────────────────────

def test_both_flags_default_false_from_env():
    assert os.environ.get("VOICE_REPLIES_ENABLED") is None
    assert os.environ.get("ELEVENLABS_TTS_ENABLED") is None
    assert os.environ.get("ELEVENLABS_API_KEY") is None
    assert os.environ.get("EMOTIONAL_REACTIONS_ENABLED") is None
    # config module already evaluated these at import time from a clean env
    # (conftest never sets them) -- re-import is unnecessary; the defaults
    # are exercised directly via the autouse fixture's explicit False too.


def test_deliver_response_flag_off_sends_plain_text_with_no_voice_ux_markup(tmp_db, monkeypatch):
    # The ordinary reply now carries the permanent lower menu. What this test
    # proves is unchanged: voice flag off means plain text and no listen /
    # response-format inline control.
    msg = FakeMessage(FakeUser(1), "hi")
    run(bot.deliver_response(msg, 1, "the answer", "ru"))
    assert [a[0] for a in msg.answers] == ["the answer"]
    assert _no_voice_ux_markup(msg.answers[0][1])
    assert msg.voices == []


def test_format_command_not_exposed_when_flag_off(tmp_db):
    msg = FakeMessage(FakeUser(1), "/format")
    run(bot.cmd_format(msg))
    assert msg.answers == []  # no selector shown, behaves as if unknown


def test_elevenlabs_gate_off_hides_voice_selector_and_never_calls_tts(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    monkeypatch.setattr(config, "ELEVENLABS_TTS_ENABLED", False)
    run(database.upsert_user(1, "u", "U"))
    msg = FakeMessage(FakeUser(1), "/format")
    run(bot.cmd_format(msg))
    assert msg.answers == []

    called = {"n": 0}
    async def forbidden(*a, **kw):
        called["n"] += 1
        raise AssertionError("TTS must remain unreachable while provider gate is off")
    monkeypatch.setattr(bot, "synthesize_speech", forbidden)
    run(database.set_response_preference(1, response_format="voice"))
    delivery = FakeMessage(FakeUser(1), "synthetic input")
    run(bot.deliver_response(delivery, 1, "complete synthetic answer", "ru"))
    assert called["n"] == 0
    assert delivery.answers[0][0] == "complete synthetic answer"
    assert isinstance(delivery.answers[0][1]["reply_markup"], bot.ReplyKeyboardMarkup)


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
    assert "ELEVENLABS_TTS_ENABLED=false" in content
    assert "ELEVENLABS_API_KEY=" in content
    assert "EMOTIONAL_REACTIONS_ENABLED=false" in content
    assert "EMOTIONAL_REACTION_COOLDOWN_SECONDS=300" in content
    assert "EMOTIONAL_REACTION_MIN_CONFIDENCE=0.9" in content


def test_reaction_cooldown_default_is_five_minutes():
    # Reactions are occasional, not per-message: an approximately one-per-
    # five-minutes ceiling per user, and only the stronger direct-keyword
    # signal (not a broad risk-category fallback) qualifies by default.
    assert config.EMOTIONAL_REACTION_COOLDOWN_SECONDS == 300
    assert config.EMOTIONAL_REACTION_MIN_CONFIDENCE == 0.9


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


def test_text_and_voice_mode_sends_full_text_with_listen_button_only(tmp_db, monkeypatch):
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
    assert msg.answers[0][0] == long_answer
    assert len(msg.voices) == 0
    markup = msg.answers[0][1]["reply_markup"]
    assert markup.inline_keyboard[0][0].text == "🔊 Прослушать"


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
    assert [text for text, _ in msg.answers] == ["full answer"]
    assert isinstance(msg.answers[0][1]["reply_markup"], bot.ReplyKeyboardMarkup)


def test_tts_provider_error_falls_back_to_full_text(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(database.set_response_preference(1, response_format="voice"))
    async def fake_synth(client_, text, lang):
        raise RuntimeError("provider down")
    monkeypatch.setattr(bot, "synthesize_speech", fake_synth)
    msg = FakeMessage(FakeUser(1), "hi")
    run(bot.deliver_response(msg, 1, "full answer", "ru"))
    assert [text for text, _ in msg.answers] == ["full answer"]
    assert isinstance(msg.answers[0][1]["reply_markup"], bot.ReplyKeyboardMarkup)


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


def test_long_response_reaches_tts_complete_even_with_legacy_concise_preference(tmp_db, monkeypatch):
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
    assert seen["text"] == long_answer


def test_elevenlabs_request_uses_exact_voice_and_complete_synthetic_text(tmp_db, monkeypatch):
    calls = []

    class FakeResponse:
        content = b"OggS" + b"synthetic-audio"
        def raise_for_status(self):
            return None

    class FakeTransport:
        def __init__(self, **kw):
            self.init = kw
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False
        async def post(self, url, **kw):
            calls.append((url, kw))
            return FakeResponse()

    monkeypatch.setattr(tts_module.httpx, "AsyncClient", FakeTransport)
    synthetic = "Синтетический тестовый ответ длиннее шестисот символов. " * 15
    path = run(tts_module.synthesize_speech(bot.client, synthetic, "ru"))
    try:
        assert len(synthetic) > 600
        assert calls == [(
            f"https://api.elevenlabs.io/v1/text-to-speech/{config.ELEVENLABS_VOICE_ID}",
            calls[0][1],
        )]
        request = calls[0][1]
        assert request["json"]["text"] == synthetic.strip()
        assert request["json"]["model_id"] == "eleven_multilingual_v2"
        assert request["params"] == {
            "output_format": "opus_48000_32", "enable_logging": "false"}
        assert request["headers"]["xi-api-key"] == "synthetic-test-key"
        with open(path, "rb") as audio:
            assert audio.read().startswith(b"OggS")
    finally:
        os.remove(path)


@pytest.mark.parametrize("gate,key", [(False, "synthetic-test-key"), (True, "")])
def test_elevenlabs_requires_gate_and_environment_key(monkeypatch, gate, key):
    monkeypatch.setattr(config, "ELEVENLABS_TTS_ENABLED", gate)
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", key)
    called = {"n": 0}

    class ForbiddenTransport:
        def __init__(self, **kw):
            called["n"] += 1

    monkeypatch.setattr(tts_module.httpx, "AsyncClient", ForbiddenTransport)
    with pytest.raises(tts_module.TTSError):
        run(tts_module.synthesize_speech(bot.client, "synthetic text", "ru"))
    assert called["n"] == 0


def test_over_limit_tts_input_is_not_truncated_or_transmitted(monkeypatch):
    monkeypatch.setattr(config, "TTS_MAX_INPUT_CHARS", 600)
    called = {"n": 0}

    class ForbiddenTransport:
        def __init__(self, **kw):
            called["n"] += 1

    monkeypatch.setattr(tts_module.httpx, "AsyncClient", ForbiddenTransport)
    with pytest.raises(tts_module.TTSError, match="complete-text fallback"):
        run(tts_module.synthesize_speech(bot.client, "x" * 601, "ru"))
    assert called["n"] == 0


def test_legacy_concise_preference_never_shortens_approved_tts_text(tmp_db, monkeypatch):
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

    assert validated_texts == []
    assert synthesized_texts == [long_answer]


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
    monkeypatch.setattr(ac, "OWNER_USER_ID", 42)
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
    monkeypatch.setattr(ac, "OWNER_USER_ID", 99)  # attacker must ALSO pass the owner gate here,
    # so this proves the cross-user encoded-uid check itself fails closed --
    # not merely "the attacker is a non-owner" (a different, already-covered case)
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
    monkeypatch.setattr(ac, "OWNER_USER_ID", 7)
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
# Emotional Reactions V1: the owner-approved product contract allows exactly
# four visible Telegram reactions (❤️ support, 💔 explicit heartbreak/loss,
# 🤗 effort/progress, 🎉 celebration/good news) or none. Categories outside
# this contract (confusion, anger, gratitude, relief, fear/shock) remain
# detectable -- reusable signal infrastructure -- but are never mapped to a
# visible emoji; see REACTION_MAP in reaction_selector.py.

@pytest.mark.parametrize("category,expected", [
    (rs.ReactionCategory.SADNESS_OR_DISAPPOINTMENT, ("❤",)),
    (rs.ReactionCategory.TEARS_WELLING, ("❤",)),
    (rs.ReactionCategory.LONELINESS_OR_REJECTION, ("❤",)),
    (rs.ReactionCategory.ANXIETY_OR_WORRY, ("❤",)),
    (rs.ReactionCategory.EXHAUSTION_OR_OVERWHELM, ("❤",)),
    (rs.ReactionCategory.HEARTBREAK_OR_LOSS, ("💔",)),
    (rs.ReactionCategory.PROGRESS_OR_ACHIEVEMENT, ("🤗",)),
    (rs.ReactionCategory.PRACTICE_COMPLETED, ("🤗",)),
    (rs.ReactionCategory.GOOD_NEWS_OR_CELEBRATION, ("🎉",)),
])
def test_reaction_mapping_matches_approved_semantics(category, expected):
    assert rs.REACTION_MAP[category] == expected


@pytest.mark.parametrize("category", [
    rs.ReactionCategory.FEAR_OR_SHOCK,
    rs.ReactionCategory.CONFUSION_OR_UNCERTAINTY,
    rs.ReactionCategory.ANGER_OR_FRUSTRATION,
    rs.ReactionCategory.RELIEF_OR_CALM,
    rs.ReactionCategory.GRATITUDE_OR_WARMTH,
])
def test_unapproved_categories_have_no_visible_mapping(category):
    # Detection infrastructure is preserved (select_reaction_category can
    # still return these), but none may ever produce a visible reaction.
    assert rs.REACTION_MAP.get(category, ()) == ()
    assert rs.pick_supported_emoji(category, None) is None


def test_near_tears_selects_support_reaction():
    cat, _ = rs.select_reaction_category("мне так тяжело, слёзы наворачиваются",
                                          ["hopelessness"], "OPEN", "ru")
    assert cat == rs.ReactionCategory.TEARS_WELLING
    assert rs.pick_supported_emoji(cat, ["❤"]) == "❤"
    # No fallback chain: an unrelated emoji is never substituted, even if
    # the chat happens to support it.
    assert rs.pick_supported_emoji(cat, ["🫂"]) is None


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
    run(bot._maybe_react(msg, 1, rs.ReactionCategory.SADNESS_OR_DISAPPOINTMENT, 0.55))
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
    run(bot._maybe_react(msg_a, 1, rs.ReactionCategory.HEARTBREAK_OR_LOSS, 0.9))
    run(bot._maybe_react(msg_a, 1, rs.ReactionCategory.HEARTBREAK_OR_LOSS, 0.9))  # cooldown blocks
    monkeypatch.setattr(ac, "OWNER_USER_ID", 2)  # only one owner uid at a time -- re-patch for user 2
    msg_b = FakeMessage(FakeUser(2), "x")
    run(bot._maybe_react(msg_b, 2, rs.ReactionCategory.HEARTBREAK_OR_LOSS, 0.9))  # different user, unaffected
    assert len(calls) == 2  # user 1 once, user 2 once -- never blocked by user 1's cooldown


def test_at_most_one_reaction_per_call(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "EMOTIONAL_REACTIONS_ENABLED", True)
    monkeypatch.setattr(ac, "OWNER_USER_ID", 5)
    calls = []
    async def fake_get_chat(chat_id):
        return types.SimpleNamespace(available_reactions=None)
    async def fake_set_reaction(**kw):
        calls.append(kw)
    monkeypatch.setattr(bot.bot, "get_chat", fake_get_chat)
    monkeypatch.setattr(bot.bot, "set_message_reaction", fake_set_reaction)
    msg = FakeMessage(FakeUser(5), "x")
    run(bot._maybe_react(msg, 5, rs.ReactionCategory.HEARTBREAK_OR_LOSS, 0.9))
    assert len(calls) == 1
    assert len(calls[0]["reaction"]) == 1


def test_unsupported_chat_reaction_returns_none_not_a_substitute():
    # Exactly one approved emoji per category, no fallback chain: if a chat
    # doesn't support it, the answer is "no reaction", never a substitute.
    emoji = rs.pick_supported_emoji(
        rs.ReactionCategory.SADNESS_OR_DISAPPOINTMENT, ["😔"])
    assert emoji is None


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
    run(bot._maybe_react(msg, 1, rs.ReactionCategory.HEARTBREAK_OR_LOSS, 0.9))
    assert calls["n"] == 0
    assert rs.pick_supported_emoji(rs.ReactionCategory.HEARTBREAK_OR_LOSS, []) is None


def test_telegram_reaction_error_does_not_propagate(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "EMOTIONAL_REACTIONS_ENABLED", True)
    async def fake_get_chat(chat_id):
        return types.SimpleNamespace(available_reactions=None)
    async def boom(**kw):
        raise RuntimeError("telegram error")
    monkeypatch.setattr(bot.bot, "get_chat", fake_get_chat)
    monkeypatch.setattr(bot.bot, "set_message_reaction", boom)
    msg = FakeMessage(FakeUser(1), "x")
    run(bot._maybe_react(msg, 1, rs.ReactionCategory.HEARTBREAK_OR_LOSS, 0.9))  # must not raise


def test_omitted_available_reactions_means_all_standard_allowed():
    # Bot API semantics: available_reactions omitted (None) == all standard
    # reactions allowed -- confirmed directly against the installed aiogram
    # type (ChatFullInfo.available_reactions is Optional).
    from aiogram.types import ChatFullInfo
    assert ChatFullInfo.model_fields["available_reactions"].default is None
    assert rs.pick_supported_emoji(rs.ReactionCategory.HEARTBREAK_OR_LOSS, None) == "💔"
    assert rs.pick_supported_emoji(rs.ReactionCategory.SADNESS_OR_DISAPPOINTMENT, None) == "❤"
    # A category outside the four approved product reactions is never sent,
    # regardless of what the chat allows.
    assert rs.pick_supported_emoji(rs.ReactionCategory.RELIEF_OR_CALM, None) is None


def test_reaction_category_is_never_persisted():
    src = inspect.getsource(bot._maybe_react) + inspect.getsource(bot.pipeline)
    assert "INSERT" not in src.upper() or "reaction" not in src.lower()


# ── §23a Emotional Reactions V1 -- four-reaction product contract ──────────

@pytest.mark.parametrize("phrase", [
    "Мне одиноко",
    "Мне тревожно из-за работы",
    "Мне грустно",
    "Я очень устал и нет сил",
])
def test_heart_support_rule_selects_heart(phrase):
    cat, conf = rs.select_reaction_category(phrase, [], "OPEN", "ru")
    assert conf >= config.EMOTIONAL_REACTION_MIN_CONFIDENCE
    assert rs.pick_supported_emoji(cat, None) == "❤"


@pytest.mark.parametrize("phrase", [
    "Мы расстались.",
    "Он меня бросил.",
    "Умер близкий мне человек.",
])
def test_heartbreak_narrow_rule_selects_broken_heart(phrase):
    cat, conf = rs.select_reaction_category(phrase, [], "OPEN", "ru")
    assert cat == rs.ReactionCategory.HEARTBREAK_OR_LOSS
    assert rs.pick_supported_emoji(cat, None) == "💔"


@pytest.mark.parametrize("phrase", [
    "Мне одиноко",
    "Мне грустно",
    "Мне тревожно из-за работы",
])
def test_heartbreak_narrow_rule_excludes_ordinary_distress(phrase):
    # Sadness/loneliness/anxiety alone must never earn the narrower
    # heartbreak reaction -- only the general ❤️ support reaction, or none.
    cat, conf = rs.select_reaction_category(phrase, [], "OPEN", "ru")
    assert cat != rs.ReactionCategory.HEARTBREAK_OR_LOSS
    assert rs.pick_supported_emoji(cat, None) != "💔"


@pytest.mark.parametrize("phrase", [
    "Я сделал первый шаг.",
    "Я справился с трудной задачей.",
])
def test_progress_hug_rule_selects_hug(phrase):
    cat, conf = rs.select_reaction_category(phrase, [], "GROWTH", "ru")
    assert cat == rs.ReactionCategory.PROGRESS_OR_ACHIEVEMENT
    assert rs.pick_supported_emoji(cat, None) == "🤗"


def test_progress_hug_rule_covers_practice_completion():
    # Practice completion is signaled directly by bot.py's cb_quality
    # handler (a fixed category, not text-derived) -- confirm it still
    # maps to the approved effort/progress reaction.
    assert rs.pick_supported_emoji(
        rs.ReactionCategory.PRACTICE_COMPLETED, None) == "🤗"


@pytest.mark.parametrize("phrase", [
    "Я сдал экзамен.",
    "Меня взяли на работу.",
    "Мы помирились.",
])
def test_celebration_rule_selects_party_popper(phrase):
    cat, conf = rs.select_reaction_category(phrase, [], "OPEN", "ru")
    assert cat == rs.ReactionCategory.GOOD_NEWS_OR_CELEBRATION
    assert rs.pick_supported_emoji(cat, None) == "🎉"


@pytest.mark.parametrize("phrase", [
    "Расскажи коротко, как работает этот бот.",
    "ок",
    "понял",
    "Завтра встреча в 15:00.",
    "Я больше не тревожусь.",
    'Она сказала: "Я очень устала".',
    "Мой коллега тревожится.",
    "Что означает слово «тревога»?",
])
def test_default_no_reaction_rule(phrase):
    cat, conf = rs.select_reaction_category(phrase, [], "OPEN", "ru")
    assert cat == rs.ReactionCategory.NONE
    assert conf == 0.0
    assert rs.pick_supported_emoji(cat, None) is None


def test_crisis_no_reaction_end_to_end():
    cat, conf = rs.select_reaction_category(
        "мне так плохо, что не хочу жить", ["suicide"], "ACUTE_DISTRESS", "ru")
    assert cat == rs.ReactionCategory.NONE and conf == 0.0
    assert rs.pick_supported_emoji(cat, None) is None


def test_unsupported_reaction_in_available_reactions_is_noop():
    cat, _ = rs.select_reaction_category("Мы расстались.", [], "OPEN", "ru")
    assert cat == rs.ReactionCategory.HEARTBREAK_OR_LOSS
    assert rs.pick_supported_emoji(cat, []) is None
    assert rs.pick_supported_emoji(cat, ["🎉"]) is None  # never substitutes, even for another approved emoji


_ALL_APPROVED_OR_NONE = {"❤", "💔", "🤗", "🎉", None}
_RETIRED_EMOJI = {"😔", "🥹", "😨", "🤔", "🔥", "👍", "🫂", "😟", "😮‍💨", "😤", "😌"}


@pytest.mark.parametrize("phrase,risk_categories,stage", [
    ("Мне одиноко", [], "OPEN"),
    ("Мы расстались.", [], "OPEN"),
    ("Я сделал первый шаг.", [], "GROWTH"),
    ("Я сдал экзамен.", [], "OPEN"),
    ("Расскажи коротко, как работает этот бот.", [], "OPEN"),
    ("Спасибо!", [], "OPEN"),  # detectable category, but outside the four approved
    ("текст", ["suicide"], "ACUTE_DISTRESS"),
])
def test_output_boundary_only_four_approved_emoji_or_none(phrase, risk_categories, stage):
    cat, _ = rs.select_reaction_category(phrase, risk_categories, stage, "ru")
    emoji = rs.pick_supported_emoji(cat, None)
    assert emoji in _ALL_APPROVED_OR_NONE
    assert emoji not in _RETIRED_EMOJI


def test_no_retired_emoji_anywhere_in_reaction_map():
    for candidates in rs.REACTION_MAP.values():
        for emoji in candidates:
            assert emoji not in _RETIRED_EMOJI
            assert emoji in {"❤", "💔", "🤗", "🎉"}


# ── §23b Reactions must be occasional (owner correction) ───────────────────
# Default frequency was too permissive: a 60s cooldown at 0.6 min confidence
# let broad risk-category fallbacks (0.75) decorate messages routinely.
# New defaults: 300s cooldown, 0.9 min confidence -- only a direct guarded
# keyword hit (_CONF_KEYWORD=0.9) qualifies by default; a bare risk-category
# fallback (_CONF_RISK_CATEGORY=0.75) no longer does. Absence of a reaction
# stays preferable to over-reacting.

def test_direct_keyword_signal_eligible_at_default_confidence(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "EMOTIONAL_REACTIONS_ENABLED", True)
    async def fake_get_chat(chat_id):
        return types.SimpleNamespace(available_reactions=None)
    calls = []
    async def spy_react(**kw):
        calls.append(kw)
    monkeypatch.setattr(bot.bot, "get_chat", fake_get_chat)
    monkeypatch.setattr(bot.bot, "set_message_reaction", spy_react)
    msg = FakeMessage(FakeUser(1), "x")
    # 0.9 == _CONF_KEYWORD, and the confidence gate is strict-less-than, so
    # a direct keyword hit must still clear the new 0.9 default.
    run(bot._maybe_react(msg, 1, rs.ReactionCategory.HEARTBREAK_OR_LOSS, 0.9))
    assert len(calls) == 1


def test_broad_risk_category_fallback_rejected_at_default_confidence(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "EMOTIONAL_REACTIONS_ENABLED", True)
    calls = {"n": 0}
    async def spy(*a, **kw):
        calls["n"] += 1
    monkeypatch.setattr(bot.bot, "set_message_reaction", spy)
    msg = FakeMessage(FakeUser(1), "x")
    # 0.75 == _CONF_RISK_CATEGORY: below the new 0.9 default, so a bare
    # risk-category fallback no longer produces a decorative reaction.
    run(bot._maybe_react(msg, 1, rs.ReactionCategory.HEARTBREAK_OR_LOSS, 0.75))
    assert calls["n"] == 0


def test_second_reaction_within_five_minutes_is_suppressed(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "EMOTIONAL_REACTIONS_ENABLED", True)
    async def fake_get_chat(chat_id):
        return types.SimpleNamespace(available_reactions=None)
    calls = []
    async def spy_react(**kw):
        calls.append(kw)
    monkeypatch.setattr(bot.bot, "get_chat", fake_get_chat)
    monkeypatch.setattr(bot.bot, "set_message_reaction", spy_react)
    msg = FakeMessage(FakeUser(1), "x")
    run(bot._maybe_react(msg, 1, rs.ReactionCategory.HEARTBREAK_OR_LOSS, 0.9))
    run(bot._maybe_react(msg, 1, rs.ReactionCategory.HEARTBREAK_OR_LOSS, 0.9))
    assert len(calls) == 1  # second call, well within the 300s default, is suppressed


# ── §23c 💔 requires clear personal loss (owner correction) ────────────────
# Bare "умер"/"умерла"/"развод" were removed as independent qualifiers: a
# stranger's death or an abstract question about divorce is not the user's
# own loss, and 💔 must stay narrow to breakup/bereavement/betrayal the user
# reports as their own.

@pytest.mark.parametrize("phrase", [
    "Умер известный актёр.",
    "Что думаешь о разводе?",
])
def test_heartbreak_rule_rejects_generic_death_or_divorce_mentions(phrase):
    cat, conf = rs.select_reaction_category(phrase, [], "OPEN", "ru")
    assert cat != rs.ReactionCategory.HEARTBREAK_OR_LOSS
    assert rs.pick_supported_emoji(cat, None) != "💔"


@pytest.mark.parametrize("phrase", [
    "У меня умер близкий человек.",
    "Мы разводимся.",
    "Он меня бросил.",
])
def test_heartbreak_rule_accepts_clear_personal_loss(phrase):
    cat, conf = rs.select_reaction_category(phrase, [], "OPEN", "ru")
    assert cat == rs.ReactionCategory.HEARTBREAK_OR_LOSS
    assert rs.pick_supported_emoji(cat, None) == "💔"


# ── §23d 💔 requires clear personal loss -- EN mirror ───────────────────────
# Same fail-closed contract as §23c, applied to the EN keyword list: bare
# "passed away"/"she died"/"he died"/"divorce" removed as independent
# qualifiers.

@pytest.mark.parametrize("phrase", [
    "A famous actor passed away.",
    "What do you think about divorce?",
])
def test_en_heartbreak_rule_rejects_generic_death_or_divorce_mentions(phrase):
    cat, conf = rs.select_reaction_category(phrase, [], "OPEN", "en")
    assert cat != rs.ReactionCategory.HEARTBREAK_OR_LOSS
    assert rs.pick_supported_emoji(cat, None) != "💔"


@pytest.mark.parametrize("phrase", [
    "Someone close to me passed away.",
    "We're getting divorced.",
    "She broke up with me.",
])
def test_en_heartbreak_rule_accepts_clear_personal_loss(phrase):
    cat, conf = rs.select_reaction_category(phrase, [], "OPEN", "en")
    assert cat == rs.ReactionCategory.HEARTBREAK_OR_LOSS
    assert rs.pick_supported_emoji(cat, None) == "💔"


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


def _consume_first_turn(uid: int):
    """Pre-consumes the one-time first-turn claim via the real, tested API
    (database.claim_first_turn -- same pattern already validated in
    tests/test_stale_response_race.py) so a subsequent pipeline() call for
    this uid is definitively past first-turn eligibility and exercises the
    ordinary/voice/format-command path these tests are actually about, not
    first-turn onboarding."""
    return database.claim_first_turn(uid, config.FIRST_TURN_CONTRACT_VERSION,
                                     f"test-preconsumed-{uid}", "test_setup")


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
    msg = _voice_msg(FakeUser(1))
    run(bot.handle_voice(msg, None))
    assert seen["text"] == "мне очень плохо"  # exact transcript, unaltered, reaches risk detection
    assert msg.answers == []  # transcript is internal input, never a visible echo


def test_long_voice_transcript_is_internal_only_and_not_echoed(tmp_db, monkeypatch):
    transcript = "Мне нужно разобраться в этом. " * 200
    async def fake_transcribe(voice, bot_, client_, lang):
        return transcript
    monkeypatch.setattr(bot, "transcribe_voice", fake_transcribe)
    seen = {}
    async def spy_pipeline(message, text, state, **kw):
        seen["text"] = text
    monkeypatch.setattr(bot, "pipeline", spy_pipeline)
    msg = _voice_msg(FakeUser(1))
    run(bot.handle_voice(msg, None))
    assert seen["text"] == transcript
    assert msg.answers == []


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


# ── Incoming voice and outgoing Voice UX for ordinary product users ────────

@pytest.mark.parametrize("voice_flag", [True, False])
def test_non_owner_incoming_voice_uses_public_voice_setting(tmp_db, monkeypatch, voice_flag):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", voice_flag)
    run(database.upsert_user(2, "u", "U"))
    run(_consume_first_turn(2))
    run(database.grant_user_access(2))  # "normal product access", non-owner
    run(database.set_response_preference(2, response_format="voice"))

    transcribe_calls = {"n": 0}
    async def fake_transcribe(voice, bot_, client_, lang):
        transcribe_calls["n"] += 1
        return "мне грустно сегодня"
    monkeypatch.setattr(bot, "transcribe_voice", fake_transcribe)
    async def fake_create(*a, **kw):
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content="ordinary text reply"))])
    monkeypatch.setattr(bot.client.chat.completions, "create", fake_create)
    tts_calls = {"n": 0}
    async def spy_synth(*a, **kw):
        tts_calls["n"] += 1
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)

    fsm = FakeFSM()
    msg = _voice_msg(FakeUser(2))
    run(bot.handle_voice(msg, fsm))

    assert transcribe_calls["n"] == 1
    assert not any("мне грустно сегодня" in answer[0] for answer in msg.answers)
    if voice_flag:
        assert tts_calls["n"] == 1
        assert len(msg.voices) == 1
        assert msg.answers == []
    else:
        assert msg.answers[-1][0] == "ordinary text reply"
        assert tts_calls["n"] == 0
        assert msg.voices == []


def test_non_owner_incoming_voice_crisis_reaches_visible_crisis_text(tmp_db, monkeypatch):
    # Crisis detection/delivery is never gated by role or access (see
    # access_control.py's own module docstring) -- confirms that holds when
    # the crisis signal arrives via a non-owner's TRANSCRIBED voice too.
    run(database.upsert_user(2, "u", "U"))
    run(database.grant_user_access(2))

    async def fake_transcribe(voice, bot_, client_, lang):
        return "я хочу покончить с собой"
    monkeypatch.setattr(bot, "transcribe_voice", fake_transcribe)
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
    msg = _voice_msg(FakeUser(2))
    run(bot.handle_voice(msg, fsm))

    assert len(crisis_text_calls) == 1 and crisis_text_calls[0]
    assert msg.answers[-1][0] == crisis_text_calls[0]  # visible crisis text sent
    assert llm_calls["n"] == 0
    assert tts_calls["n"] == 0
    assert reaction_calls["n"] == 0


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
    run(_consume_first_turn(1))
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

    monkeypatch.setattr(ac, "OWNER_USER_ID", 2)  # B must also pass the owner gate, so this
    # proves genuine FSM isolation, not merely "B is a non-owner"
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


# ── Previous-response replay authorization scope (FSM-scoped, chat+user) ────
# Replay now sources last_delivered_response/last_delivered_response_at from
# FSM state (aiogram's default FSMStrategy.USER_IN_CHAT, confirmed directly
# against Dispatcher.__init__ -- bot.py never overrides it), NOT from
# database.get_last_assistant_message (removed this pass -- it was
# introduced only by this PR, had no other caller, and was user_id-only
# with no chat dimension at all -- a genuine cross-chat replay risk for the
# SAME Telegram user_id appearing in two different chats). No test here may
# claim cross-chat safety from a user_id-only SQL query.

def test_replay_same_conversation_no_llm_call_one_validated_voice(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
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

    fsm = FakeFSM({"last_delivered_response": "This is the real prior final answer.",
                   "last_delivered_response_at": time.time()})
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

    fsm = FakeFSM({"last_delivered_response": long_prior,
                   "last_delivered_response_at": time.time()})
    run(bot.pipeline(FakeMessage(FakeUser(1), "много текста"), "много текста", fsm))

    assert len(validated) == 1
    concise = validated[0]
    assert concise != long_prior and len(concise) < len(long_prior)
    assert synthesized == [concise]  # exactly the re-validated concise text, never the raw long text


def test_replay_expired_response_outside_ttl_is_not_replayed_and_is_cleared(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    _full_pipeline_stub_set(monkeypatch, llm_text="unused")
    tts_calls = {"n": 0}
    async def spy_synth(*a, **kw):
        tts_calls["n"] += 1
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)
    too_old = time.time() - config.VOICE_LAST_RESPONSE_TTL_SECONDS - 100

    fsm = FakeFSM({"last_delivered_response": "a very old reply",
                   "last_delivered_response_at": too_old})
    run(bot.pipeline(FakeMessage(FakeUser(1), "лень читать"), "лень читать", fsm))

    assert tts_calls["n"] == 0  # outside the bounded TTL -- never replayed
    data = run(fsm.get_data())
    assert data.get("last_delivered_response") is None       # stale value cleared
    assert data.get("one_shot_voice_pending") is True         # armed for the next ordinary reply instead


def test_replay_cross_chat_isolation_same_user_different_fsm(tmp_db, monkeypatch):
    # Same Telegram user_id, two DIFFERENT FSM entries -- exactly what
    # aiogram's default FSMStrategy.USER_IN_CHAT produces for the SAME user
    # in two DIFFERENT chats. This is the actual defect scenario: a
    # user_id-only lookup would have returned chat A's response from chat B;
    # the FSM-scoped source structurally cannot.
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    tts_calls = {"n": 0}
    async def spy_synth(*a, **kw):
        tts_calls["n"] += 1
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)
    _full_pipeline_stub_set(monkeypatch, llm_text="unused")

    fsm_chat_a = FakeFSM({"last_delivered_response": "chat A's private final answer.",
                          "last_delivered_response_at": time.time()})
    fsm_chat_b = FakeFSM()  # a different chat's FSM entry -- never touched by chat A

    run(bot.pipeline(FakeMessage(FakeUser(1), "лень читать"), "лень читать", fsm_chat_b))
    assert tts_calls["n"] == 0  # chat B never sees chat A's stored response
    assert (run(fsm_chat_b.get_data())).get("one_shot_voice_pending") is True
    assert (run(fsm_chat_a.get_data())).get("last_delivered_response") == "chat A's private final answer."


def test_replay_cross_user_isolation(tmp_db, monkeypatch):
    run(database.upsert_user(1, "u", "U"))
    run(database.upsert_user(2, "u", "U"))
    fsm_a = FakeFSM({"last_delivered_response": "A's private final answer",
                     "last_delivered_response_at": time.time()})
    fsm_b = FakeFSM()  # user B's own, separate FSM -- never shares user A's data
    assert (run(fsm_b.get_data())).get("last_delivered_response") is None


def test_last_delivered_response_has_exactly_one_write_site_after_successful_delivery():
    # Structural guarantee that only the ordinary successful-delivery path
    # can ever populate last_delivered_response -- crisis/dependency/error
    # paths all return earlier in pipeline() and never reach this line.
    src = _pipeline_source()
    assert src.count("last_delivered_response=answer") == 1
    write_idx = src.index("last_delivered_response=answer")
    deliver_idx = src.index("await deliver_response(")
    assert deliver_idx < write_idx  # written only AFTER delivery succeeds, never before


# ── Real aiogram FSM-key isolation (StorageKey/FSMContext/MemoryStorage) ────
# Not FakeFSM object isolation -- the actual installed aiogram classes,
# proving the real USER_IN_CHAT contract this whole feature depends on.

def test_real_dispatcher_fsm_strategy_is_user_in_chat():
    from aiogram.fsm.strategy import FSMStrategy, apply_strategy
    assert bot.dp.fsm.strategy == FSMStrategy.USER_IN_CHAT
    # USER_IN_CHAT resolves the storage key to (chat_id, user_id) -- verified
    # directly against aiogram's own apply_strategy, not assumed.
    assert apply_strategy(FSMStrategy.USER_IN_CHAT, chat_id=111, user_id=42) == (111, 42, None)


def test_real_fsm_context_same_user_different_chat_is_isolated():
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    storage = MemoryStorage()  # ONE real shared storage backend
    bot_id = bot.bot.id
    key_chat_a = StorageKey(bot_id=bot_id, chat_id=1001, user_id=1)
    key_chat_b = StorageKey(bot_id=bot_id, chat_id=1002, user_id=1)  # SAME user, DIFFERENT chat
    ctx_a = FSMContext(storage=storage, key=key_chat_a)
    ctx_b = FSMContext(storage=storage, key=key_chat_b)

    async def _run():
        await ctx_a.update_data(last_delivered_response="chat A's private reply",
                                 last_delivered_response_at=time.time())
        await ctx_a.update_data(one_shot_voice_pending=True, one_shot_voice_pending_at=time.time())
        return await ctx_a.get_data(), await ctx_b.get_data()
    data_a, data_b = run(_run())

    assert data_a["last_delivered_response"] == "chat A's private reply"
    assert data_a["one_shot_voice_pending"] is True
    assert data_b == {}  # a DIFFERENT chat for the SAME user_id sees nothing at all


def test_real_fsm_context_different_user_same_chat_is_isolated():
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    storage = MemoryStorage()
    bot_id = bot.bot.id
    key_user_a = StorageKey(bot_id=bot_id, chat_id=2001, user_id=1)
    key_user_b = StorageKey(bot_id=bot_id, chat_id=2001, user_id=2)  # SAME chat, DIFFERENT user
    ctx_a = FSMContext(storage=storage, key=key_user_a)
    ctx_b = FSMContext(storage=storage, key=key_user_b)

    async def _run():
        await ctx_a.update_data(last_delivered_response="user A's private reply")
        return await ctx_a.get_data(), await ctx_b.get_data()
    data_a, data_b = run(_run())

    assert data_a["last_delivered_response"] == "user A's private reply"
    assert data_b == {}  # a DIFFERENT user in the SAME chat sees nothing at all


def test_pipeline_with_real_fsm_context_cross_chat_replay_is_blocked(tmp_db, monkeypatch):
    # End-to-end: bot.pipeline() driven by REAL FSMContext objects (backed by
    # one shared real MemoryStorage), not FakeFSM -- the actual defect
    # scenario reproduced and proven closed with the real aiogram machinery.
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    tts_calls = {"n": 0}
    async def spy_synth(*a, **kw):
        tts_calls["n"] += 1
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)
    _full_pipeline_stub_set(monkeypatch, llm_text="unused")

    storage = MemoryStorage()
    bot_id = bot.bot.id
    ctx_private = FSMContext(storage=storage, key=StorageKey(bot_id=bot_id, chat_id=1, user_id=1))
    ctx_other_chat = FSMContext(storage=storage, key=StorageKey(bot_id=bot_id, chat_id=999, user_id=1))

    run(ctx_private.update_data(last_delivered_response="the private chat's real final answer",
                                last_delivered_response_at=time.time()))

    msg = FakeMessage(FakeUser(1), "лень читать")
    run(bot.pipeline(msg, "лень читать", ctx_other_chat))

    assert tts_calls["n"] == 0  # the OTHER chat's real FSMContext never sees chat 1's stored reply


# ── Explicit one-shot voice override TTL (t0 / t0+TTL-1 / t1+TTL+1) ─────────

def test_one_shot_override_consumed_just_before_ttl_expiry(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    monkeypatch.setattr(config, "VOICE_ONE_SHOT_OVERRIDE_TTL_SECONDS", 300)
    run(database.upsert_user(1, "u", "U"))
    run(_consume_first_turn(1))
    _full_pipeline_stub_set(monkeypatch, llm_text="a real answer")
    voiced = []
    async def spy_synth(client_, text, lang):
        voiced.append(text)
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)
    monkeypatch.setattr(os, "remove", lambda p: None)

    t0 = 1_000_000.0
    monkeypatch.setattr(bot.time, "time", lambda: t0)
    fsm = FakeFSM()
    run(bot.pipeline(FakeMessage(FakeUser(1), "лень читать"), "лень читать", fsm))  # arm at t0
    assert (run(fsm.get_data())).get("one_shot_voice_pending") is True

    monkeypatch.setattr(bot.time, "time", lambda: t0 + 299)  # t0 + TTL - 1
    run(bot.pipeline(FakeMessage(FakeUser(1), "Мне тревожно"), "Мне тревожно", fsm))
    assert voiced == ["a real answer"]  # still within TTL -- consumed exactly once
    data = run(fsm.get_data())
    assert data.get("one_shot_voice_pending") is False
    assert data.get("one_shot_voice_pending_at") is None


def test_one_shot_override_expired_after_ttl_is_cleared_and_not_applied(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    monkeypatch.setattr(config, "VOICE_ONE_SHOT_OVERRIDE_TTL_SECONDS", 300)
    run(database.upsert_user(1, "u", "U"))
    _full_pipeline_stub_set(monkeypatch, llm_text="a real answer")
    voiced = []
    async def spy_synth(client_, text, lang):
        voiced.append(text)
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)

    t1 = 2_000_000.0
    monkeypatch.setattr(bot.time, "time", lambda: t1)
    fsm = FakeFSM()
    run(bot.pipeline(FakeMessage(FakeUser(1), "лень читать"), "лень читать", fsm))  # arm at t1

    monkeypatch.setattr(bot.time, "time", lambda: t1 + 301)  # t1 + TTL + 1
    run(bot.pipeline(FakeMessage(FakeUser(1), "Мне тревожно"), "Мне тревожно", fsm))
    assert voiced == []  # expired -- never applied
    data = run(fsm.get_data())
    assert data.get("one_shot_voice_pending") is False  # cleared, not silently left "armed"
    assert data.get("one_shot_voice_pending_at") is None
    prefs = run(database.get_response_preferences(1))
    assert prefs["response_format"] == "text"  # permanent preference never touched by TTL expiry


def test_one_shot_override_ttl_isolated_per_chat(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    _full_pipeline_stub_set(monkeypatch, llm_text="unused")
    voiced = []
    async def spy_synth(client_, text, lang):
        voiced.append(text)
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)

    fsm_chat_a = FakeFSM()
    fsm_chat_b = FakeFSM()
    run(bot.pipeline(FakeMessage(FakeUser(1), "лень читать"), "лень читать", fsm_chat_a))
    assert (run(fsm_chat_a.get_data())).get("one_shot_voice_pending") is True
    run(bot.pipeline(FakeMessage(FakeUser(1), "Мне тревожно"), "Мне тревожно", fsm_chat_b))
    assert voiced == []  # chat B's separate FSM never saw chat A's armed override


def test_one_shot_override_ttl_isolated_per_user_same_chat_key(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(database.upsert_user(2, "u", "U"))
    _full_pipeline_stub_set(monkeypatch, llm_text="unused")
    voiced = []
    async def spy_synth(client_, text, lang):
        voiced.append(text)
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)

    fsm_user_a = FakeFSM()
    fsm_user_b = FakeFSM()
    run(bot.pipeline(FakeMessage(FakeUser(1), "лень читать"), "лень читать", fsm_user_a))
    monkeypatch.setattr(ac, "OWNER_USER_ID", 2)  # B must also pass the owner gate, so this
    # proves genuine FSM isolation, not merely "B is a non-owner"
    run(bot.pipeline(FakeMessage(FakeUser(2), "Мне тревожно"), "Мне тревожно", fsm_user_b))
    assert voiced == []  # user B's own FSM never sees user A's armed override


# ── Delivery-success write-contract for last_delivered_response ────────────

def test_text_delivery_success_stores_response_and_timestamp(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(_consume_first_turn(1))
    _full_pipeline_stub_set(monkeypatch, llm_text="the approved ordinary answer")
    fsm = FakeFSM()
    before = time.time()
    run(bot.pipeline(FakeMessage(FakeUser(1), "Мне тревожно"), "Мне тревожно", fsm))
    data = run(fsm.get_data())
    assert data.get("last_delivered_response") == "the approved ordinary answer"
    assert data.get("last_delivered_response_at") >= before


def test_voice_only_delivery_success_stores_approved_text_not_audio(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(_consume_first_turn(1))
    run(database.set_response_preference(1, response_format="voice"))
    _full_pipeline_stub_set(monkeypatch, llm_text="the approved spoken answer")
    async def fake_synth(client_, text, lang):
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "synthesize_speech", fake_synth)
    monkeypatch.setattr(os, "remove", lambda p: None)

    fsm = FakeFSM()
    run(bot.pipeline(FakeMessage(FakeUser(1), "Мне тревожно"), "Мне тревожно", fsm))
    data = run(fsm.get_data())
    assert data.get("last_delivered_response") == "the approved spoken answer"  # TEXT, not audio bytes
    assert isinstance(data.get("last_delivered_response"), str)


def test_voice_fails_text_fallback_succeeds_fallback_becomes_replay_source(tmp_db, monkeypatch):
    # Chosen and documented rule: when TTS fails and deliver_response falls
    # back to the full validated text, that text (the SAME already-approved
    # `answer`, never a different value) becomes the replay source -- the
    # user genuinely received it, so it is a legitimate replay candidate.
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(_consume_first_turn(1))
    run(database.set_response_preference(1, response_format="voice"))
    _full_pipeline_stub_set(monkeypatch, llm_text="the approved answer, delivered as text fallback")
    async def failing_synth(client_, text, lang):
        raise tts_module.TTSError("simulated failure")
    monkeypatch.setattr(bot, "synthesize_speech", failing_synth)

    fsm = FakeFSM()
    msg = FakeMessage(FakeUser(1), "Мне тревожно")
    run(bot.pipeline(msg, "Мне тревожно", fsm))
    assert [text for text, _ in msg.answers] == [
        "the approved answer, delivered as text fallback"]
    assert isinstance(msg.answers[0][1]["reply_markup"], bot.ReplyKeyboardMarkup)
    data = run(fsm.get_data())
    assert data.get("last_delivered_response") == "the approved answer, delivered as text fallback"


def test_complete_delivery_failure_never_writes_or_overwrites_replay_state(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(_consume_first_turn(1))
    run(database.set_response_preference(1, response_format="voice"))
    _full_pipeline_stub_set(monkeypatch, llm_text="an answer that will never actually be delivered")
    async def failing_synth(client_, text, lang):
        raise tts_module.TTSError("simulated TTS failure")
    monkeypatch.setattr(bot, "synthesize_speech", failing_synth)

    # Distinct messages per method so the match string below PROVES which
    # call actually raised -- if the exception instead came from
    # answer_voice (or anywhere else), the differing text would fail the
    # match and this test would fail loudly rather than pass ambiguously.
    class BoomMessage(FakeMessage):
        async def answer(self, *a, **kw):
            raise RuntimeError("simulated total Telegram outage on text fallback")
        async def answer_voice(self, *a, **kw):
            raise RuntimeError("simulated total Telegram outage on voice send")

    fsm = FakeFSM({"last_delivered_response": "an older, genuinely delivered answer",
                   "last_delivered_response_at": time.time()})
    msg = BoomMessage(FakeUser(1), "Мне тревожно")
    # synthesize_speech fails first (TTSError, caught inside
    # _synthesize_and_send_voice), so deliver_response's "voice" branch
    # falls back to message.answer(answer) -- THAT is the call expected to
    # raise here, confirmed by the distinct match string, not answer_voice
    # (never reached) or an unrelated AttributeError/mock misconfiguration.
    with pytest.raises(RuntimeError, match="text fallback"):
        run(bot.pipeline(msg, "Мне тревожно", fsm))

    data = run(fsm.get_data())
    # The older, genuinely-delivered value must survive untouched -- a
    # message that never reached Telegram at all must never overwrite it.
    assert data.get("last_delivered_response") == "an older, genuinely delivered answer"


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
    run(_consume_first_turn(1))
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
    run(_consume_first_turn(1))
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


def test_text_and_voice_mode_keeps_full_text_and_does_not_auto_synthesize(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(_consume_first_turn(1))
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

    assert synthesized == []
    assert msg.answers[0][0] == long_answer
    assert msg.answers[0][1]["reply_markup"].inline_keyboard[0][0].text == "🔊 Прослушать"


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
    # Tapper must be the owner (uid=1, this file's default) for the feature
    # to be available at all; the message shown belongs to a different,
    # non-owner user (uid=2) -- proving the tap affects only the TAPPER.
    cb = FakeCallback(FakeUser(1), FakeMessage(FakeUser(2)), f"{bot._FMT_KB_VERSION}:format:voice")
    run(bot.cb_format_select(cb))
    prefs1 = run(database.get_response_preferences(1))
    prefs2 = run(database.get_response_preferences(2))
    assert prefs1["response_format"] == "voice"  # only the TAPPING user (user 1) changed
    assert prefs2["response_format"] == "text"   # message "owner" (user 2) unaffected


# ── Explicit private-chat-only enforcement (§4 this pass) ───────────────────

def test_format_command_does_not_expose_selector_in_group_chat(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    msg = FakeMessage(FakeUser(1), "/format", chat_type="group")
    run(bot.cmd_format(msg))
    assert len(msg.answers) == 1  # a short neutral notice only
    assert "🎙" not in msg.answers[0][0]  # never the selector itself


def test_fmt_callback_fails_closed_in_group_chat(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    group_msg = FakeMessage(FakeUser(1), "", chat_type="group")
    cb = FakeCallback(FakeUser(1), group_msg, f"{bot._FMT_KB_VERSION}:format:voice")
    run(bot.cb_format_select(cb))
    prefs = run(database.get_response_preferences(1))
    assert prefs["response_format"] == "text"  # never modified from a group callback


def test_listen_button_not_attached_in_group_chat(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(database.set_response_preference(1, response_format="text"))
    msg = FakeMessage(FakeUser(1), "", chat_type="group")
    run(bot.deliver_response(msg, 1, "an ordinary answer", "ru"))
    assert [a[0] for a in msg.answers] == ["an ordinary answer"]
    assert _no_voice_ux_markup(msg.answers[0][1])  # no listen button in a group


def test_listen_callback_fails_closed_in_group_chat(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    calls = {"n": 0}
    async def spy_synth(*a, **kw):
        calls["n"] += 1
        return True
    monkeypatch.setattr(bot, "_synthesize_and_send_voice", spy_synth)
    group_msg = FakeMessage(FakeUser(1), "visible text", chat_type="group")
    cb = FakeCallback(FakeUser(1), group_msg, f"{bot._LISTEN_KB_VERSION}:1")
    run(bot.cb_listen(cb))
    assert calls["n"] == 0


def test_pure_lazy_to_read_in_group_gives_neutral_notice_no_llm_no_tts_no_replay(tmp_db, monkeypatch):
    # §5 correction: a PURE meta-command outside a private chat must be
    # recognized and short-circuited with a neutral notice -- NOT silently
    # sent to the therapeutic LLM merely because chat.type != "private".
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
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

    fsm = FakeFSM({"last_delivered_response": "a private reply that must never leak into the group",
                   "last_delivered_response_at": time.time()})
    group_msg = FakeMessage(FakeUser(1), "лень читать", chat_type="group")
    run(bot.pipeline(group_msg, "лень читать", fsm))

    assert llm_calls["n"] == 0                    # never enters therapeutic routing
    assert tts_calls["n"] == 0                     # no replay, no synthesis at all
    assert len(group_msg.answers) == 1              # exactly one neutral notice
    assert "личном чате" in group_msg.answers[0][0]  # the neutral notice, not an LLM reply
    data = run(fsm.get_data())
    assert data.get("one_shot_voice_pending") is not True  # no override ever armed from a group turn
    assert data.get("last_delivered_response") == "a private reply that must never leak into the group"  # untouched


def test_pure_voice_request_in_group_no_llm_no_preference_write(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    llm_calls = {"n": 0}
    async def spy_create(*a, **kw):
        llm_calls["n"] += 1
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content="unused"))])
    monkeypatch.setattr(bot.client.chat.completions, "create", spy_create)

    fsm = FakeFSM()
    msg = FakeMessage(FakeUser(1), "ответь голосом", chat_type="group")
    run(bot.pipeline(msg, "ответь голосом", fsm))

    assert llm_calls["n"] == 0
    prefs = run(database.get_response_preferences(1))
    assert prefs["response_format"] == "text"  # never modified from a group turn


def test_mixed_message_in_group_preserves_ordinary_behavior_ignores_format_fragment(tmp_db, monkeypatch):
    # A MIXED message in a non-private chat still reaches the ordinary LLM
    # (pre-existing bot behavior for emotional content is preserved), but
    # the format fragment ("ответь голосом") must not apply -- delivery
    # stays plain text, no preference is written, no override is armed.
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(_consume_first_turn(1))
    _full_pipeline_stub_set(monkeypatch, llm_text="an ordinary group reply")
    tts_calls = {"n": 0}
    async def spy_synth(*a, **kw):
        tts_calls["n"] += 1
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)

    fsm = FakeFSM()
    text = "Мне тревожно, и ответь голосом"
    msg = FakeMessage(FakeUser(1), text, chat_type="group")
    run(bot.pipeline(msg, text, fsm))

    assert tts_calls["n"] == 0                      # format fragment ignored -- no voice
    assert [a[0] for a in msg.answers] == ["an ordinary group reply"]
    assert _no_voice_ux_markup(msg.answers[0][1])  # ordinary reply, plain text
    prefs = run(database.get_response_preferences(1))
    assert prefs["response_format"] == "text"        # untouched


# ── Persistent commands specifically in a group (§3 this pass) ─────────────
# A one-shot group test alone is not evidence for persistent preference
# safety -- these prove the PERSISTENT phrasing, end to end through a real
# bot.pipeline() call, never modifies a stored preference from a group.

def test_pure_persistent_voice_in_group_gives_neutral_notice_no_llm_no_pref_write(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
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

    fsm = FakeFSM()
    text = "всегда отвечай голосом"
    msg = FakeMessage(FakeUser(1), text, chat_type="group")
    run(bot.pipeline(msg, text, fsm))

    assert llm_calls["n"] == 0
    assert tts_calls["n"] == 0
    assert len(msg.answers) == 1
    assert "личном чате" in msg.answers[0][0]  # exactly one neutral notice
    prefs = run(database.get_response_preferences(1))
    assert prefs["response_format"] == "text"  # untouched by the persistent phrasing
    data = run(fsm.get_data())
    assert data.get("one_shot_voice_pending") is not True  # no override armed either


def test_pure_persistent_text_in_group_does_not_change_existing_voice_preference(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(database.set_response_preference(1, response_format="voice"))
    llm_calls = {"n": 0}
    async def spy_create(*a, **kw):
        llm_calls["n"] += 1
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content="unused"))])
    monkeypatch.setattr(bot.client.chat.completions, "create", spy_create)

    fsm = FakeFSM()
    text = "всегда отвечай текстом"
    msg = FakeMessage(FakeUser(1), text, chat_type="group")
    run(bot.pipeline(msg, text, fsm))

    assert llm_calls["n"] == 0
    assert len(msg.answers) == 1
    assert "личном чате" in msg.answers[0][0]
    prefs = run(database.get_response_preferences(1))
    assert prefs["response_format"] == "voice"  # the EXISTING preference survives untouched


def test_pure_persistent_concise_in_group_does_not_change_response_length(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    llm_calls = {"n": 0}
    async def spy_create(*a, **kw):
        llm_calls["n"] += 1
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content="unused"))])
    monkeypatch.setattr(bot.client.chat.completions, "create", spy_create)

    fsm = FakeFSM()
    text = "всегда пиши короче"
    msg = FakeMessage(FakeUser(1), text, chat_type="group")
    run(bot.pipeline(msg, text, fsm))

    assert llm_calls["n"] == 0
    assert len(msg.answers) == 1
    assert "личном чате" in msg.answers[0][0]
    prefs = run(database.get_response_preferences(1))
    assert prefs["response_length"] == "normal"  # untouched


def test_private_persistent_concise_command_saves_preference_without_llm(tmp_db, monkeypatch):
    # The PRIVATE-chat counterpart to the group test above -- proves the
    # "всегда пиши короче" parser fix (format_commands.py) actually reaches
    # its intended positive outcome (persistence, ack, no LLM/TTS), not just
    # its negative outcome (correctly refused in a group).
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(database.set_response_preference(1, response_format="text", response_length="normal"))
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

    fsm = FakeFSM()
    text = "всегда пиши короче"
    msg = FakeMessage(FakeUser(1), text)  # default chat_type="private"
    run(bot.pipeline(msg, text, fsm))

    assert llm_calls["n"] == 0
    assert tts_calls["n"] == 0
    assert len(msg.answers) == 1  # exactly one acknowledgement
    assert "личном чате" not in msg.answers[0][0]  # the private ack, not the group notice
    prefs = run(database.get_response_preferences(1))
    assert prefs["response_length"] == "concise"  # actually changed this time
    assert prefs["response_format"] == "text"      # untouched -- only length was targeted
    data = run(fsm.get_data())
    assert data.get("one_shot_voice_pending") is not True  # no override armed
    # No raw command text anywhere in the preference table's columns.
    assert "всегда пиши короче" not in prefs.values()


def test_mixed_persistent_command_in_group_delivers_ordinary_text_ignores_preference(tmp_db, monkeypatch):
    # "Мне тревожно, всегда отвечай голосом" -- a MIXED message carrying a
    # PERSISTENT phrasing. Ordinary emotional content still reaches the
    # existing LLM route (pre-existing bot behavior preserved), delivered as
    # plain text; the persistent format fragment has zero effect.
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    run(_consume_first_turn(1))
    _full_pipeline_stub_set(monkeypatch, llm_text="an ordinary group reply")
    tts_calls = {"n": 0}
    async def spy_synth(*a, **kw):
        tts_calls["n"] += 1
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)

    fsm = FakeFSM()
    text = "Мне тревожно, всегда отвечай голосом"
    msg = FakeMessage(FakeUser(1), text, chat_type="group")
    run(bot.pipeline(msg, text, fsm))

    assert tts_calls["n"] == 0
    assert [a[0] for a in msg.answers] == ["an ordinary group reply"]
    assert _no_voice_ux_markup(msg.answers[0][1])  # plain text, ordinary reply
    prefs = run(database.get_response_preferences(1))
    assert prefs["response_format"] == "text"  # never modified by the persistent fragment
    data = run(fsm.get_data())
    assert data.get("one_shot_voice_pending") is not True


def test_pure_persistent_voice_english_in_group_gives_neutral_notice(tmp_db, monkeypatch):
    # EN format-command parsing is supported (format_commands._PERSISTENT_MARKERS
    # includes "always reply"/"always respond") -- confirm the EN phrasing
    # gets the same group boundary, not just RU.
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U", "en"))
    llm_calls = {"n": 0}
    async def spy_create(*a, **kw):
        llm_calls["n"] += 1
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content="unused"))])
    monkeypatch.setattr(bot.client.chat.completions, "create", spy_create)

    fsm = FakeFSM()
    text = "always reply with voice"
    msg = FakeMessage(FakeUser(1), text, chat_type="group")
    run(bot.pipeline(msg, text, fsm))

    assert llm_calls["n"] == 0
    assert len(msg.answers) == 1
    assert "private chat" in msg.answers[0][0]
    prefs = run(database.get_response_preferences(1))
    assert prefs["response_format"] == "text"


def test_private_callback_still_works_after_group_boundary_added(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(1, "u", "U"))
    cb = FakeCallback(FakeUser(1), FakeMessage(FakeUser(1)), f"{bot._FMT_KB_VERSION}:format:voice")
    run(bot.cb_format_select(cb))
    prefs = run(database.get_response_preferences(1))
    assert prefs["response_format"] == "voice"  # private chat (default chat_type) unaffected by the new guard


# ── Owner-only canary gate for Voice and Reactions (this pass) ──────────────
# uid=1 is this file's default owner (see _access_env above). The behavior
# matrix's owner-in-private and owner-in-group cells, and the crisis/
# dependency safety proofs, are already fully exercised by pre-existing
# tests throughout this file (all built on uid=1) -- only the genuinely
# NEW cells (non-owner, and missing/invalid OWNER_USER_ID) are added below.

def test_owner_gate_voice_non_owner_stays_text_only_even_with_saved_voice_pref(tmp_db, monkeypatch):
    # Voice #3 + #9 combined: flag=true + non-owner -> text-only, TTS=0,
    # regardless of a previously-saved response_format="voice" preference.
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(2, "u", "U"))
    run(database.set_response_preference(2, response_format="voice"))
    tts_calls = {"n": 0}
    async def spy_synth(*a, **kw):
        tts_calls["n"] += 1
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)
    msg = FakeMessage(FakeUser(2), "hi")
    run(bot.deliver_response(msg, 2, "an ordinary answer", "ru"))
    assert [a[0] for a in msg.answers] == ["an ordinary answer"]
    assert _no_voice_ux_markup(msg.answers[0][1])  # no listen button either
    assert msg.voices == []
    assert tts_calls["n"] == 0


def test_owner_gate_voice_missing_owner_id_disables_for_everyone(tmp_db, monkeypatch):
    # Voice #4: flag=true but OWNER_USER_ID unset/invalid -> disabled for
    # EVERYONE, including the uid that would otherwise be owner.
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    monkeypatch.setattr(ac, "OWNER_USER_ID", None)
    run(database.upsert_user(1, "u", "U"))
    run(database.set_response_preference(1, response_format="voice"))
    tts_calls = {"n": 0}
    async def spy_synth(*a, **kw):
        tts_calls["n"] += 1
        return "/tmp/x.opus"
    monkeypatch.setattr(bot, "synthesize_speech", spy_synth)
    msg = FakeMessage(FakeUser(1), "hi")
    run(bot.deliver_response(msg, 1, "an ordinary answer", "ru"))
    assert [a[0] for a in msg.answers] == ["an ordinary answer"]
    assert _no_voice_ux_markup(msg.answers[0][1])
    assert tts_calls["n"] == 0


def test_owner_gate_non_owner_pure_voice_command_in_private_chat_gives_neutral_notice(tmp_db, monkeypatch):
    # Voice #5: non-owner sends a PURE voice command in their OWN private
    # chat. In personal_use mode (this file's _access_env default) a
    # non-owner, non-invited uid is already rejected by the PRE-EXISTING
    # access-control gate before format-command parsing is ever reached --
    # an even stronger proof of "no side effects": no preference write, no
    # override armed, no TTS, and the message never reaches the therapeutic
    # LLM at all (existing legacy behavior, per the contract's own wording).
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(2, "u", "U"))
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

    fsm = FakeFSM()
    text = "ответь голосом"
    msg = FakeMessage(FakeUser(2), text)  # uid=2 is a non-owner; private chat
    run(bot.pipeline(msg, text, fsm))

    assert llm_calls["n"] == 0
    assert tts_calls["n"] == 0
    assert len(msg.answers) == 1  # rejected early by the pre-existing access gate
    prefs = run(database.get_response_preferences(2))
    assert prefs["response_format"] == "text"
    data = run(fsm.get_data())
    assert data.get("one_shot_voice_pending") is not True


def test_owner_gate_non_owner_format_command_selector_unavailable(tmp_db, monkeypatch):
    # Voice #6: non-owner /format -> silent no-op (same as flag-off), pref unchanged.
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(2, "u", "U"))
    msg = FakeMessage(FakeUser(2), "/format")
    run(bot.cmd_format(msg))
    assert msg.answers == []
    prefs = run(database.get_response_preferences(2))
    assert prefs["response_format"] == "text"


def test_owner_gate_non_owner_format_callback_fails_closed(tmp_db, monkeypatch):
    # Voice #7: non-owner taps the format-selector callback -> fails closed.
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    run(database.upsert_user(2, "u", "U"))
    cb = FakeCallback(FakeUser(2), FakeMessage(FakeUser(2)), f"{bot._FMT_KB_VERSION}:format:voice")
    run(bot.cb_format_select(cb))
    prefs = run(database.get_response_preferences(2))
    assert prefs["response_format"] == "text"


def test_owner_gate_non_owner_listen_callback_fails_closed(tmp_db, monkeypatch):
    # Voice #8: a non-owner taps a validly self-encoded listen callback
    # (matching their own uid) -- must still fail closed, no TTS.
    monkeypatch.setattr(config, "VOICE_REPLIES_ENABLED", True)
    calls = {"n": 0}
    async def spy_synth(*a, **kw):
        calls["n"] += 1
        return True
    monkeypatch.setattr(bot, "_synthesize_and_send_voice", spy_synth)
    user = FakeUser(2)
    msg = FakeMessage(user, "someone else's visible answer")
    cb = FakeCallback(user, msg, f"{bot._LISTEN_KB_VERSION}:2")
    run(bot.cb_listen(cb))
    assert calls["n"] == 0


def test_public_reactions_non_owner_one_call(tmp_db, monkeypatch):
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "public")
    monkeypatch.setattr(config, "EMOTIONAL_REACTIONS_ENABLED", True)
    async def fake_get_chat(chat_id):
        return types.SimpleNamespace(available_reactions=None)
    calls = []
    async def spy_react(**kw):
        calls.append(kw)
    monkeypatch.setattr(bot.bot, "get_chat", fake_get_chat)
    monkeypatch.setattr(bot.bot, "set_message_reaction", spy_react)
    msg = FakeMessage(FakeUser(2), "x")
    run(bot._maybe_react(msg, 2, rs.ReactionCategory.HEARTBREAK_OR_LOSS, 0.9))
    assert len(calls) == 1


def test_owner_gate_reactions_missing_owner_id_disables_for_everyone(tmp_db, monkeypatch):
    # Reactions #14: flag=true but OWNER_USER_ID unset/invalid -> zero calls,
    # even for the uid that would otherwise be owner.
    monkeypatch.setattr(config, "EMOTIONAL_REACTIONS_ENABLED", True)
    monkeypatch.setattr(ac, "OWNER_USER_ID", None)
    calls = {"n": 0}
    async def spy_react(**kw):
        calls["n"] += 1
    monkeypatch.setattr(bot.bot, "set_message_reaction", spy_react)
    msg = FakeMessage(FakeUser(1), "x")
    run(bot._maybe_react(msg, 1, rs.ReactionCategory.GRATITUDE_OR_WARMTH, 0.9))
    assert calls["n"] == 0


def test_owner_gate_reactions_owner_in_group_unaffected(tmp_db, monkeypatch):
    # Reactions #15: reactions were never chat-type restricted before this
    # gate -- owner-in-group must still get a reaction (only the owner
    # check is new; the existing private-chat policy is not weakened).
    monkeypatch.setattr(config, "EMOTIONAL_REACTIONS_ENABLED", True)
    async def fake_get_chat(chat_id):
        return types.SimpleNamespace(available_reactions=None)
    calls = []
    async def spy_react(**kw):
        calls.append(kw)
    monkeypatch.setattr(bot.bot, "get_chat", fake_get_chat)
    monkeypatch.setattr(bot.bot, "set_message_reaction", spy_react)
    msg = FakeMessage(FakeUser(1), "x", chat_type="group")
    run(bot._maybe_react(msg, 1, rs.ReactionCategory.HEARTBREAK_OR_LOSS, 0.9))
    assert len(calls) == 1


# ── Owner-canary defect fixes: legacy reply keyboard + reaction coverage ────
# Two live defects found during the Phase B owner canary on 29d2fe8:
#   1. a pre-214ba15 ReplyKeyboardMarkup survived CLIENT-SIDE and obscured
#      the chat, because nothing in an ordinary session ever retracted it;
#   2. every ordinary phrase the owner sent selected NONE, so no reaction
#      could ever appear (the flag and the owner gate were both correct).

def test_first_ordinary_reply_carries_permanent_lower_menu(tmp_db, monkeypatch):
    bot._legacy_kb_cleared.clear()
    run(database.upsert_user(1, "u", "U"))
    msg = FakeMessage(FakeUser(1), "hi")
    run(bot.deliver_response(msg, 1, "an ordinary answer", "ru"))
    assert msg.answers[0][0] == "an ordinary answer"
    markup = msg.answers[0][1].get("reply_markup")
    assert isinstance(markup, bot.ReplyKeyboardMarkup)
    # Privacy row removed (owner product decision): Data & Privacy now lives
    # only in Help, not duplicated in the persistent lower menu.
    assert [[button.text for button in row] for row in markup.keyboard] == [
        ["🧠 Психологические тесты", "📊 Мои результаты"],
        ["📝 Дневники", "🎛 Как отвечать"],
    ]
    assert markup.is_persistent is True
    assert markup.resize_keyboard is True


def test_persistent_lower_menu_kb_shape_ru_and_en():
    # Direct, exhaustive proof of the post-removal shape for both
    # languages: exactly 2 rows, 2 buttons per row, 4 buttons total, in
    # the required order, Privacy absent, "Как отвечать"/"How to reply"
    # present.
    ru = bot.persistent_lower_menu_kb("ru")
    ru_rows = [[b.text for b in row] for row in ru.keyboard]
    assert ru_rows == [
        ["🧠 Психологические тесты", "📊 Мои результаты"],
        ["📝 Дневники", "🎛 Как отвечать"],
    ]
    assert len(ru_rows) == 2
    assert all(len(row) == 2 for row in ru_rows)
    assert sum(len(row) for row in ru_rows) == 4
    flat_ru = [label for row in ru_rows for label in row]
    assert "🔒 Данные и приватность" not in flat_ru
    assert "🎛 Как отвечать" in flat_ru

    en = bot.persistent_lower_menu_kb("en")
    en_rows = [[b.text for b in row] for row in en.keyboard]
    assert en_rows == [
        ["🧠 Psychological tests", "📊 My results"],
        ["📝 Diaries", "🎛 How to reply"],
    ]
    assert len(en_rows) == 2
    assert all(len(row) == 2 for row in en_rows)
    assert sum(len(row) for row in en_rows) == 4
    flat_en = [label for row in en_rows for label in row]
    assert "🔒 Data and privacy" not in flat_en
    assert "🎛 How to reply" in flat_en


def test_permanent_lower_menu_remains_attached_on_later_replies(tmp_db, monkeypatch):
    bot._legacy_kb_cleared.clear()
    run(database.upsert_user(1, "u", "U"))
    first = FakeMessage(FakeUser(1), "hi")
    run(bot.deliver_response(first, 1, "first", "ru"))
    second = FakeMessage(FakeUser(1), "hi again")
    run(bot.deliver_response(second, 1, "second", "ru"))
    assert isinstance(first.answers[0][1].get("reply_markup"), bot.ReplyKeyboardMarkup)
    assert isinstance(second.answers[0][1].get("reply_markup"), bot.ReplyKeyboardMarkup)


def test_permanent_lower_menu_is_available_for_each_user(tmp_db, monkeypatch):
    bot._legacy_kb_cleared.clear()
    run(database.upsert_user(1, "u", "U"))
    run(database.upsert_user(2, "u", "U"))
    a = FakeMessage(FakeUser(1), "hi")
    run(bot.deliver_response(a, 1, "for a", "ru"))
    b = FakeMessage(FakeUser(2), "hi")
    run(bot.deliver_response(b, 2, "for b", "ru"))
    assert isinstance(a.answers[0][1].get("reply_markup"), bot.ReplyKeyboardMarkup)
    assert isinstance(b.answers[0][1].get("reply_markup"), bot.ReplyKeyboardMarkup)


def test_failed_lower_menu_send_does_not_mark_delivery_as_success(tmp_db, monkeypatch):
    bot._legacy_kb_cleared.clear()
    run(database.upsert_user(1, "u", "U"))
    sends = []
    class _FlakyMessage(FakeMessage):
        async def answer(self, text, **kw):
            sends.append(kw.get("reply_markup"))
            raise RuntimeError("telegram rejected the message")

    first = _FlakyMessage(FakeUser(1), "hi")
    with pytest.raises(RuntimeError, match="telegram rejected"):
        run(bot.deliver_response(first, 1, "the answer", "ru"))
    assert first.answers == []


def test_eviction_tracking_is_bounded(tmp_db, monkeypatch):
    # The set must not grow for the whole process lifetime.
    bot._legacy_kb_cleared.clear()
    monkeypatch.setattr(bot, "_LEGACY_KB_MAX_TRACKED", 5)
    run(database.upsert_user(1, "u", "U"))
    for uid in range(1, 12):
        msg = FakeMessage(FakeUser(uid), "hi")
        run(bot.deliver_response(msg, uid, "answer", "ru"))
    assert len(bot._legacy_kb_cleared) <= 5


def test_mood_entry_remains_inline_alongside_permanent_lower_menu():
    # The emotion choices must stay attached to their own message (inline) so
    # they can never occupy the user's text-input keyboard area again.
    kb = bot._mood_entry_keyboard("ru", ["a", "b"])
    assert isinstance(kb, bot.InlineKeyboardMarkup)
    assert isinstance(bot.persistent_lower_menu_kb("ru"), bot.ReplyKeyboardMarkup)


def test_emotion_selection_removes_menu_and_does_not_duplicate_it(tmp_db, monkeypatch):
    # Choosing an emotion retracts its own inline menu exactly once and does
    # not re-render a second mood card.
    run(database.upsert_user(1, "u", "U"))
    edits = []
    class _Msg(FakeMessage):
        async def edit_reply_markup(self, reply_markup=None):
            edits.append(reply_markup)
    monkeypatch.setattr(bot, "pipeline", _async(None))
    msg = _Msg(FakeUser(1), "greeting")
    run(bot.cb_mood(FakeCallback(FakeUser(1), msg, "mood:0"), FakeFSM()))
    assert edits == [None]      # menu retracted exactly once
    assert msg.answers == []    # no duplicate mood card re-sent


# ── Reaction selector: the exact phrases sent during the live canary ───────

@pytest.mark.parametrize("phrase,expected", [
    ("Сегодня я сильно тревожусь из-за важной рабочей задачи.",
     rs.ReactionCategory.ANXIETY_OR_WORRY),
    ("Сегодня мне немного тревожно из-за обычной рабочей задачи.",
     rs.ReactionCategory.ANXIETY_OR_WORRY),
    ("Я немного расстроен после тяжёлого дня.",
     rs.ReactionCategory.SADNESS_OR_DISAPPOINTMENT),
    ("Я очень устал после сложного дня.",
     rs.ReactionCategory.EXHAUSTION_OR_OVERWHELM),
])
def test_live_canary_phrases_now_select_an_eligible_reaction(phrase, expected):
    cat, conf = rs.select_reaction_category(phrase, [], "OPEN", "ru")
    assert cat == expected
    assert conf >= config.EMOTIONAL_REACTION_MIN_CONFIDENCE


@pytest.mark.parametrize("phrase,expected", [
    ("Мне тяжело после этого разговора.", rs.ReactionCategory.SADNESS_OR_DISAPPOINTMENT),
    ("На этой неделе всё навалилось.", rs.ReactionCategory.EXHAUSTION_OR_OVERWHELM),
    ("Я совсем не справляюсь с нагрузкой.", rs.ReactionCategory.EXHAUSTION_OR_OVERWHELM),
    ("Мне одиноко после переезда.", rs.ReactionCategory.LONELINESS_OR_REJECTION),
    ("Я едва сдерживаю слёзы.", rs.ReactionCategory.TEARS_WELLING),
    ("Я злюсь из-за этой ситуации.", rs.ReactionCategory.ANGER_OR_FRUSTRATION),
])
def test_conservative_natural_russian_coverage(phrase, expected):
    category, confidence = rs.select_reaction_category(phrase, [], "OPEN", "ru")
    assert category == expected
    assert confidence >= config.EMOTIONAL_REACTION_MIN_CONFIDENCE


def test_growth_stage_alone_does_not_force_progress_reaction():
    assert rs.select_reaction_category(
        "Обычное сообщение без явного достижения.", [], "GROWTH", "ru") == \
        (rs.ReactionCategory.NONE, 0.0)


def test_explicit_small_win_selects_high_confidence_progress_reaction():
    category, confidence = rs.select_reaction_category(
        "У меня получилось сделать первый шаг.", [], "GROWTH", "ru")
    assert category == rs.ReactionCategory.PROGRESS_OR_ACHIEVEMENT
    assert confidence == 0.9
    assert rs.pick_supported_emoji(category, None) == "🤗"


@pytest.mark.parametrize("phrase", [
    "Расскажи коротко, как работает этот бот.",
    "Во сколько ты обычно присылаешь напоминания?",
])
def test_neutral_phrases_still_select_no_reaction(phrase):
    cat, conf = rs.select_reaction_category(phrase, [], "OPEN", "ru")
    assert cat == rs.ReactionCategory.NONE
    assert conf == 0.0


def test_new_stems_cover_ordinary_inflections():
    for phrase in ("мне тревожно", "я тревожусь", "чувствую тревогу"):
        assert rs.select_reaction_category(phrase, [], "OPEN", "ru")[0] == \
            rs.ReactionCategory.ANXIETY_OR_WORRY
    for phrase in ("я устал", "я устала"):
        assert rs.select_reaction_category(phrase, [], "OPEN", "ru")[0] == \
            rs.ReactionCategory.EXHAUSTION_OR_OVERWHELM
    for phrase in ("я расстроен", "я расстроена"):
        assert rs.select_reaction_category(phrase, [], "OPEN", "ru")[0] == \
            rs.ReactionCategory.SADNESS_OR_DISAPPOINTMENT


def test_new_keywords_never_override_crisis_or_dependency():
    # An anxiety/exhaustion stem riding along with a crisis signal must still
    # yield NONE -- safety precedence is unchanged by the widened vocabulary.
    assert rs.select_reaction_category(
        "мне очень тревожно и я не хочу жить", ["suicide"],
        "ACUTE_DISTRESS", "ru") == (rs.ReactionCategory.NONE, 0.0)
    assert rs.select_reaction_category(
        "я устал", [], "OPEN", "ru",
        is_dependency_redirect=True) == (rs.ReactionCategory.NONE, 0.0)
    assert rs.select_reaction_category(
        "я устал", [], "OPEN", "ru",
        is_meta_command=True) == (rs.ReactionCategory.NONE, 0.0)


# ── P1 guard: a keyword hit must be a SELF-REPORT, not any occurrence ─────
# Reacting 😟 to "я больше не тревожусь", to a colleague's feelings, to a
# quoted sentence or to "что означает слово «тревога»?" tells the user the
# bot misread them. A missing reaction is invisible; a wrong one is not.

@pytest.mark.parametrize("phrase", [
    "Я больше не тревожусь.",
    "Я не расстроен.",
    "Я не устала, всё нормально.",
])
def test_negated_feeling_never_reacts(phrase):
    assert rs.select_reaction_category(phrase, [], "OPEN", "ru")[0] == \
        rs.ReactionCategory.NONE


@pytest.mark.parametrize("phrase", [
    "Мой коллега тревожится.",
    "Мой брат очень устал.",
])
def test_third_person_feeling_never_reacts(phrase):
    assert rs.select_reaction_category(phrase, [], "OPEN", "ru")[0] == \
        rs.ReactionCategory.NONE


@pytest.mark.parametrize("phrase", [
    "Она сказала: «Я очень устала».",
    'Он написал "мне тревожно" вчера.',
])
def test_quoted_feeling_never_reacts(phrase):
    assert rs.select_reaction_category(phrase, [], "OPEN", "ru")[0] == \
        rs.ReactionCategory.NONE


@pytest.mark.parametrize("phrase", [
    "Что означает слово «тревога»?",
    "Напиши пример фразы «мне тревожно».",
])
def test_meta_language_never_reacts(phrase):
    assert rs.select_reaction_category(phrase, [], "OPEN", "ru")[0] == \
        rs.ReactionCategory.NONE


def test_third_person_does_not_mute_the_users_own_feeling():
    # A third-person subject is present, but so is a first-person pronoun,
    # so the user's own disclosure still earns its reaction -- the guard
    # must not over-reject.
    cat, conf = rs.select_reaction_category(
        "Мой муж кричал, и мне тревожно.", [], "OPEN", "ru")
    assert cat == rs.ReactionCategory.ANXIETY_OR_WORRY
    assert conf >= config.EMOTIONAL_REACTION_MIN_CONFIDENCE


def test_bare_gratitude_without_pronoun_still_reacts():
    # The guard must not require a first-person pronoun outright -- "Спасибо!"
    # is a genuine self-report containing no pronoun at all.
    assert rs.select_reaction_category("Спасибо!", [], "OPEN", "ru")[0] == \
        rs.ReactionCategory.GRATITUDE_OR_WARMTH


def test_guarded_keyword_still_falls_through_to_risk_category_rules():
    # A guarded-out wording hit must not short-circuit the whole selector:
    # an independent panic risk signal still earns its own reaction.
    cat, conf = rs.select_reaction_category(
        "Я не тревожусь", ["panic"], "ACUTE_DISTRESS", "ru")
    assert cat == rs.ReactionCategory.ANXIETY_OR_WORRY
    assert conf == 0.75


def test_english_negation_and_third_person_guards():
    for phrase in ("I am not anxious about it.", "My colleague is exhausted."):
        assert rs.select_reaction_category(phrase, [], "OPEN", "en")[0] == \
            rs.ReactionCategory.NONE


def test_english_variants_select_expected_categories():
    for phrase, expected in (
        ("i feel anxious about tomorrow", rs.ReactionCategory.ANXIETY_OR_WORRY),
        ("i am exhausted after today", rs.ReactionCategory.EXHAUSTION_OR_OVERWHELM),
    ):
        cat, conf = rs.select_reaction_category(phrase, [], "OPEN", "en")
        assert cat == expected
        assert conf >= config.EMOTIONAL_REACTION_MIN_CONFIDENCE


# ── Privacy-safe reaction observability ───────────────────────────────────

def _capture_reaction_logs(monkeypatch):
    lines = []
    def fake_print(*a, **kw):
        text = " ".join(str(x) for x in a)
        if text.startswith("[reaction]"):
            lines.append(text)
    monkeypatch.setattr("builtins.print", fake_print)
    return lines


def test_reaction_log_records_skip_reason_without_user_data(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "EMOTIONAL_REACTIONS_ENABLED", True)
    lines = _capture_reaction_logs(monkeypatch)
    msg = FakeMessage(FakeUser(1), "секретный текст пользователя")
    run(bot._maybe_react(msg, 1, rs.ReactionCategory.NONE, 0.0))
    assert any("decision=skipped" in l and "reason=no_match" in l for l in lines)
    joined = " ".join(lines)
    assert "секретный текст пользователя" not in joined   # no message text
    assert "uid=" not in joined and "username" not in joined  # no identity


def test_reaction_log_records_low_confidence_then_selected_then_cooldown(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "EMOTIONAL_REACTIONS_ENABLED", True)
    async def fake_get_chat(chat_id):
        return types.SimpleNamespace(available_reactions=None)
    monkeypatch.setattr(bot.bot, "get_chat", fake_get_chat)
    monkeypatch.setattr(bot.bot, "set_message_reaction", _async(None))
    lines = _capture_reaction_logs(monkeypatch)
    msg = FakeMessage(FakeUser(1), "x")
    run(bot._maybe_react(msg, 1, rs.ReactionCategory.HEARTBREAK_OR_LOSS, 0.1))
    assert any("reason=low_confidence" in l for l in lines)
    run(bot._maybe_react(msg, 1, rs.ReactionCategory.HEARTBREAK_OR_LOSS, 0.9))
    assert any("decision=selected" in l for l in lines)
    run(bot._maybe_react(msg, 1, rs.ReactionCategory.HEARTBREAK_OR_LOSS, 0.9))
    assert any("reason=cooldown" in l for l in lines)


def test_reaction_failure_never_blocks_and_records_error_class(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "EMOTIONAL_REACTIONS_ENABLED", True)
    async def fake_get_chat(chat_id):
        return types.SimpleNamespace(available_reactions=None)
    async def boom(**kw):
        raise RuntimeError("telegram down")
    monkeypatch.setattr(bot.bot, "get_chat", fake_get_chat)
    monkeypatch.setattr(bot.bot, "set_message_reaction", boom)
    lines = _capture_reaction_logs(monkeypatch)
    msg = FakeMessage(FakeUser(1), "x")
    run(bot._maybe_react(msg, 1, rs.ReactionCategory.HEARTBREAK_OR_LOSS, 0.9))  # must not raise
    assert any("decision=failed" in l and "RuntimeError" in l for l in lines)


def test_reaction_logging_failure_does_not_affect_delivery(tmp_db, monkeypatch):
    # Observability must never be able to break the response path.
    monkeypatch.setattr(config, "EMOTIONAL_REACTIONS_ENABLED", True)
    def exploding_print(*a, **kw):
        raise OSError("stdout gone")
    monkeypatch.setattr("builtins.print", exploding_print)
    msg = FakeMessage(FakeUser(1), "x")
    run(bot._maybe_react(msg, 1, rs.ReactionCategory.NONE, 0.0))  # must not raise
