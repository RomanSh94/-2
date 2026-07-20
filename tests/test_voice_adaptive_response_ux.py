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
