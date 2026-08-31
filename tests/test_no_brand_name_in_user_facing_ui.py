"""Round 3 — product rule: the literal brand name "X20"/"x20" must never
appear in normal user-facing UI (the bot may be renamed later). This is a
deliberately curated, bounded guard over KNOWN deterministic user-facing text
builders and the registered BotCommand descriptions -- not a blind source
scan, so it never touches internal docstrings, comments, log lines, service
names, DB paths, or env var names, none of which are in scope for this rule.
"""
import asyncio
import types

import pytest

import bot
import navigation
import questionnaire_ux


def _has_no_brand_name(text: str) -> bool:
    return "X20" not in text and "x20" not in text


# ── navigation.py: every (lang) -> str builder ─────────────────────────────
_NAV_TEXT_BUILDERS = [
    navigation.menu_text,
    navigation.help_text,
    navigation.talk_hub_text,
    navigation.feedback_hub_text,
    navigation.tests_hub_text,
    navigation.journals_hub_text,
    navigation.privacy_hub_text,
    navigation.privacy_stored_data_text,
    navigation.results_hub_text,
    navigation.about_hub_text,
]


@pytest.mark.parametrize("builder", _NAV_TEXT_BUILDERS)
@pytest.mark.parametrize("lang", ["ru", "en"])
def test_navigation_text_builders_have_no_brand_name(builder, lang):
    assert _has_no_brand_name(builder(lang))


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_response_settings_text_has_no_brand_name(lang):
    assert _has_no_brand_name(navigation.response_settings_text(lang, available=True))
    assert _has_no_brand_name(navigation.response_settings_text(lang, available=False))


# ── questionnaire_ux.py: catalog root + a representative result screen ─────
@pytest.mark.parametrize("lang", ["ru", "en"])
def test_questionnaire_list_text_has_no_brand_name(lang):
    assert _has_no_brand_name(questionnaire_ux.list_text(lang))


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_generic_result_text_has_no_brand_name(lang):
    assert _has_no_brand_name(questionnaire_ux.result_text(5, 10, lang))


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_dass21_result_text_has_no_brand_name(lang):
    subscales = {"depression": 4, "anxiety": 3, "stress": 5}
    assert _has_no_brand_name(questionnaire_ux.dass21_result_text(subscales, lang))


# ── MENU_SECTIONS labels (feeds both the persistent lower menu and the new
# /help card) ────────────────────────────────────────────────────────────────
def test_menu_sections_labels_have_no_brand_name():
    for _key, ru, en in navigation.MENU_SECTIONS:
        assert _has_no_brand_name(ru)
        assert _has_no_brand_name(en)


# ── registered BotCommand descriptions (both the default and RU-localized
# lists set in bot.main()) ──────────────────────────────────────────────────
def test_registered_botcommand_descriptions_have_no_brand_name(monkeypatch):
    calls = []

    class FakeBot:
        async def set_my_commands(self, commands, language_code=None):
            calls.append([(c.command, c.description) for c in commands])

    class FakeDispatcher:
        async def start_polling(self, polling_bot):
            pass

    class FakeScheduler:
        def start(self):
            pass

    async def fake_init_db():
        pass

    monkeypatch.setattr(bot, "bot", FakeBot())
    monkeypatch.setattr(bot, "dp", FakeDispatcher())
    monkeypatch.setattr(bot, "init_db", fake_init_db)
    monkeypatch.setattr(bot, "start_dashboard", lambda: None)
    monkeypatch.setattr(bot, "setup_scheduler", lambda scheduler_bot, model_client: FakeScheduler())

    asyncio.run(bot.main())

    assert len(calls) == 2
    for command_list in calls:
        for _cmd, description in command_list:
            assert _has_no_brand_name(description)


# ── the two known user-visible export filenames ─────────────────────────────
def test_export_filenames_have_no_brand_name():
    import inspect
    src = inspect.getsource(bot)
    # A narrow, targeted check of exactly the two known export-filename call
    # sites -- not a blind scan of the whole module source.
    assert 'filename="x20_privacy_export.json"' not in src
    assert 'filename="x20_journals.json"' not in src
    assert 'buf.name = "x20_journals.json"' not in src


# ── Test Group E: questionnaire root wording + non-empty-only rendering ────
def test_questionnaire_root_uses_new_wording():
    assert questionnaire_ux.list_text("ru").endswith("Выбери доступный раздел ниже:")
    assert questionnaire_ux.list_text("en").endswith("Choose an available section below:")
    assert "Тесты помогут лучше оценить" in questionnaire_ux.list_text("ru")
    assert "не ставят диагноз" in questionnaire_ux.list_text("ru")


# ── Test Group F: every currently reachable result screen keeps its
# non-diagnostic disclaimer ──────────────────────────────────────────────────
@pytest.mark.parametrize("lang,phrase", [("ru", "а не диагноз"), ("en", "not a diagnosis")])
def test_dass21_result_screen_keeps_disclaimer(lang, phrase):
    subscales = {"depression": 4, "anxiety": 3, "stress": 5}
    assert phrase in questionnaire_ux.dass21_result_text(subscales, lang)


@pytest.mark.parametrize("lang,phrase", [("ru", "не диагноз"), ("en", "not a diagnosis")])
def test_generic_result_screen_keeps_disclaimer(lang, phrase):
    assert phrase in questionnaire_ux.result_text(5, 10, lang)


# ── Test Group G: diaries/about brand-neutral copy ──────────────────────────
def test_journal_card_is_brand_neutral(monkeypatch):
    class FakeUser:
        def __init__(self, uid):
            self.id = uid

    class FakeMessage:
        def __init__(self, user):
            self.from_user = user
            self.chat = types.SimpleNamespace(id=user.id)
            self.answers = []

        async def answer(self, text, **kw):
            self.answers.append((text, kw))

    async def _true(*a, **kw):
        return True

    async def _lang(*a, **kw):
        return "ru"

    async def _no_crisis(*a, **kw):
        return None

    monkeypatch.setattr(bot, "ensure_full_access_or_closed_test", _true)
    monkeypatch.setattr(bot, "get_user_language", _lang)
    # cmd_journal now goes through _nav_gate (Round 4 final correction),
    # which runs journal_guard's active-crisis check before the access gate.
    monkeypatch.setattr(bot, "get_active_crisis", _no_crisis)

    msg = FakeMessage(FakeUser(1))
    asyncio.run(bot.cmd_journal(msg, None))
    text = msg.answers[0][0]
    assert text == "📝 Дневники\n\nВыбери, что хочешь открыть:"
    assert _has_no_brand_name(text)


def test_about_hub_label_is_brand_neutral():
    labels = {key: (ru, en) for key, ru, en in navigation.MENU_SECTIONS}
    ru, en = labels["about"]
    assert ru == "ℹ️ О боте"
    assert en == "ℹ️ About the bot"
