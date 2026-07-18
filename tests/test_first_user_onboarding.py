"""First-user illustrated onboarding — eligibility, real versioning, persistence,
the full callback state machine, copy/safety, language selection, and
regressions against existing behavior.

Handler-level: drives bot.cmd_start and bot.cb_onboarding with fake Message /
CallbackQuery objects and a fake `bot` double (no Telegram, no network). Access
config is pinned in an autouse fixture so nothing leaks from a real .env.
"""
import asyncio
import types

import pytest

import access_control as ac
import config
import database
import onboarding_content as oc

run = asyncio.run
V = oc.ONBOARDING_VERSION


# ── Fakes ─────────────────────────────────────────────────────────────────────
class FakeUser:
    def __init__(self, uid, username="u", first_name="U", language_code=None):
        self.id = uid
        self.username = username
        self.first_name = first_name
        self.language_code = language_code


class FakeMsg:
    """The incoming /start message. Plain replies (mood entry, privacy summary,
    grant messages) still go through .answer() on this object, matching real
    aiogram usage — only the onboarding CARD itself (photo/edit) goes through
    the fake `bot` double now (see FakeBot in test_onboarding_media.py's
    pattern), addressed by chat_id/message_id."""

    def __init__(self, user, text="/start"):
        self.from_user = user
        self.text = text
        self.chat = types.SimpleNamespace(id=user.id)
        self.message_id = 0        # fallback only -- real tests set up an
                                   # active row first, so state["card_message_id"]
                                   # is always what cb_onboarding actually uses
        self.answers = []          # plain text messages sent via .answer()

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))
        return self

    def rendered_texts(self):
        return [t for t, _ in self.answers]

    def keyboards(self):
        return [kb for _, kb in self.answers if kb is not None]


class FakeCb:
    def __init__(self, user, message, data):
        self.from_user = user
        self.message = message
        self.data = data
        self.answered = 0

    async def answer(self, *a, **k):
        self.answered += 1


class FakeBot:
    """Same double as tests/test_onboarding_media.py -- patched onto bot.bot
    for the duration of a test so bot._render_onboarding_card's calls into
    onboarding.send_or_edit_onboarding_card(bot, chat_id, ...) hit this fake,
    never real Telegram."""

    def __init__(self):
        self.sent = []
        self.edits = []
        self.markup_clears = []
        self.edit_exc = None
        self._next_id = 9000

    def _new_id(self):
        self._next_id += 1
        return self._next_id

    async def send_photo(self, chat_id, photo, caption, reply_markup=None):
        mid = self._new_id()
        self.sent.append(("photo", chat_id, caption, reply_markup))
        return types.SimpleNamespace(chat=types.SimpleNamespace(id=chat_id), message_id=mid)

    async def send_message(self, chat_id, text, reply_markup=None):
        mid = self._new_id()
        self.sent.append(("text", chat_id, text, reply_markup))
        return types.SimpleNamespace(chat=types.SimpleNamespace(id=chat_id), message_id=mid)

    async def edit_message_media(self, chat_id, message_id, media, reply_markup=None):
        if self.edit_exc is not None:
            raise self.edit_exc
        self.edits.append(("media", chat_id, message_id, media.caption, reply_markup))

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
        if self.edit_exc is not None:
            raise self.edit_exc
        self.edits.append(("text", chat_id, message_id, text, reply_markup))

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        self.markup_clears.append((chat_id, message_id))

    def rendered_texts(self):
        out = [c for _, _, c, _ in self.sent]
        out += [c for _, _, _, c, _ in self.edits]
        return out

    def keyboards(self):
        out = [kb for _, _, _, kb in self.sent]
        out += [kb for _, _, _, _, kb in self.edits]
        return [kb for kb in out if kb is not None]

    def card_count(self):
        """Total distinct card-affecting Telegram calls (sends + edits) — used
        to prove repeated /start does NOT flood the chat with duplicate cards."""
        return len(self.sent) + len(self.edits)


# ── Fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    run(database.init_db())
    return database


@pytest.fixture
def fake_bot(monkeypatch):
    import bot as bot_module
    fb = FakeBot()
    monkeypatch.setattr(bot_module, "bot", fb)
    return fb


@pytest.fixture(autouse=True)
def _pin_env(monkeypatch, fake_bot):
    """Clean baseline: onboarding ON, invite off, roles pinned away from .env,
    a fake bot double patched in (autouse -- every test in this file gets it,
    whether or not it names `fake_bot` as a parameter)."""
    monkeypatch.setattr(config, "FIRST_USER_ONBOARDING_ENABLED", True)
    monkeypatch.setattr(config, "PRIVACY_POLICY_URL", "")
    monkeypatch.setattr(config, "USER_INVITE_ENABLED", False)
    monkeypatch.setattr(config, "USER_INVITE_CODE", "")
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})
    yield


def _authorized(uid):
    """Give an ordinary (non-owner) uid real product access via user_access."""
    run(database.grant_user_access(uid, source="invite"))


def _make_prior_message(uid):
    """Simulate meaningful prior product use so message_count > 0 (legacy)."""
    run(database.save_message(uid, "user", "hello from before"))


def _start(uid, text="/start", language_code="ru"):
    """Defaults to a Russian Telegram language_code so every test in this file
    that doesn't specifically test language policy keeps exercising the RU
    copy it asserts against -- language POLICY itself (ru*->ru, else->en,
    including missing/malformed) is tested explicitly in the "B: real
    new-user language" section below via language_code=None/other values."""
    import bot
    user = FakeUser(uid, language_code=language_code)
    msg = FakeMsg(user, text=text)
    run(bot.cmd_start(msg))
    return msg


def _tap(uid, data):
    import bot
    # cb_onboarding no longer reads the tapped card off callback.message --
    # it resolves the card via the persisted DB reference (spec item G), so a
    # fresh dummy message object stands in for "the button that was tapped".
    cb = FakeCb(FakeUser(uid), FakeMsg(FakeUser(uid)), data)
    run(bot.cb_onboarding(cb))
    return cb


def _import_bot():
    import bot
    return bot


# ── Eligibility (spec item C) ───────────────────────────────────────────────
def test_new_authorized_user_sees_step1(tmp_db, fake_bot):
    uid = 1001
    _authorized(uid)
    _start(uid)
    st = run(database.get_onboarding_state(uid))
    assert st is not None and st["status"] == "active" and st["current_step"] == 1
    assert st["onboarding_version"] == V
    assert any(oc.caption(1, "ru") == t for t in fake_bot.rendered_texts())


def test_returning_user_without_notice_ack_sees_privacy_only_screen(tmp_db, fake_bot):
    # Fixed gap (spec item F correction): a legacy user who has NEVER
    # acknowledged the CURRENT privacy notice is not silently exempted --
    # they get the PRIVACY-ONLY screen. Corrected architecture: this does
    # NOT create or touch any user_onboarding_state row at all (a fake
    # onboarding row must never be fabricated just to show this screen) --
    # the acknowledgement lives solely in user_notice_acknowledgements.
    uid = 1002
    _authorized(uid)
    _make_prior_message(uid)             # message_count > 0 -> returning/legacy
    _start(uid)
    assert run(database.get_onboarding_state(uid)) is None  # no fake row
    assert any(oc.caption(oc.LAST_STEP, "ru") == t for t in fake_bot.rendered_texts())
    assert not any(oc.caption(1, "ru") == t for t in fake_bot.rendered_texts())


def test_legacy_user_privacy_ack_settles_no_reprompt_on_next_start(tmp_db, fake_bot):
    uid = 1003
    _authorized(uid)
    _make_prior_message(uid)
    _start(uid)  # privacy-only screen shown, NO onboarding row created
    assert run(database.get_onboarding_state(uid)) is None
    assert run(database.has_privacy_notice_ack(uid, oc.PRIVACY_NOTICE_VERSION)) is False
    # Acknowledge via the REAL privacy-only callback -- record_notice_acknowledgement,
    # never complete_onboarding (there is no row to complete).
    _tap(uid, oc.cb_privacy_only_start(oc.PRIVACY_NOTICE_VERSION))
    assert run(database.has_privacy_notice_ack(uid, oc.PRIVACY_NOTICE_VERSION)) is True
    fake_bot.sent.clear(); fake_bot.edits.clear()
    _start(uid)
    # Settled: no re-prompt of any onboarding/privacy screen. A bookkeeping
    # legacy_exempt row is lazily created on THIS /start (nothing to render).
    assert not fake_bot.sent and not fake_bot.edits
    assert run(database.get_onboarding_state(uid, V))["status"] == "legacy_exempt"


def test_blocked_user_cannot_enter_onboarding(tmp_db, fake_bot):
    uid = 1004
    _authorized(uid)
    run(database.block_user_access(uid))
    _start(uid)
    # Gate blocked before onboarding: no state row created.
    assert run(database.get_onboarding_state(uid)) is None
    assert not fake_bot.sent and not fake_bot.edits


def test_unauthorized_user_cannot_enter_onboarding(tmp_db, fake_bot):
    uid = 1005  # never granted any access
    _start(uid)
    assert run(database.get_onboarding_state(uid)) is None
    assert not fake_bot.sent and not fake_bot.edits


def test_owner_access_unchanged_by_onboarding(tmp_db, fake_bot):
    owner = 1  # OWNER_USER_ID per fixture
    assert run(ac.has_full_access(owner)) is True
    _make_prior_message(owner)           # owner has history -> legacy path
    _start(owner)
    assert run(ac.has_full_access(owner)) is True  # access not altered
    # Owner has never acknowledged the current notice -> privacy-only screen,
    # but no onboarding row is fabricated for it.
    assert run(database.get_onboarding_state(owner)) is None
    assert any(oc.caption(oc.LAST_STEP, "ru") == t for t in fake_bot.rendered_texts())


def test_valid_invite_grants_access_before_onboarding(tmp_db, fake_bot, monkeypatch):
    code = "z" * 32
    monkeypatch.setattr(config, "USER_INVITE_ENABLED", True)
    monkeypatch.setattr(config, "USER_INVITE_CODE", code)
    uid = 1006  # brand new, arrives via deep link
    _start(uid, text=f"/start {code}")
    # Access was granted (grant message) AND onboarding then began for the new user.
    assert run(database.user_has_active_access(uid)) is True
    st = run(database.get_onboarding_state(uid))
    assert st is not None and st["status"] == "active" and st["current_step"] == 1


def test_start_during_active_onboarding_resumes_current_step(tmp_db, fake_bot):
    uid = 1007
    _authorized(uid)
    _start(uid)                                   # step 1
    run(database.advance_onboarding_step(uid, V, 1, 2))
    run(database.advance_onboarding_step(uid, V, 2, 3))
    fake_bot.sent.clear(); fake_bot.edits.clear()
    _start(uid)                             # /start again mid-onboarding
    # Resumes at step 3, does NOT restart at 1.
    assert run(database.get_onboarding_state(uid))["current_step"] == 3
    assert any(oc.caption(3, "ru") == t for t in fake_bot.rendered_texts())
    assert not any(oc.caption(1, "ru") == t for t in fake_bot.rendered_texts())


def test_completed_onboarding_does_not_restart(tmp_db, fake_bot):
    uid = 1008
    _authorized(uid)
    _start(uid)
    run(database.skip_onboarding_to_privacy(uid, V))
    # Real completion also records the independent notice acknowledgement
    # (see database.complete_onboarding) -- omitting privacy_notice_version
    # here would leave the CURRENT notice unacknowledged and correctly
    # trigger a privacy-only re-prompt on the next /start, which is not what
    # this test is about (see test_returning_user_without_notice_ack_sees_privacy_only_screen).
    run(database.complete_onboarding(uid, V, privacy_notice_version=oc.PRIVACY_NOTICE_VERSION))
    fake_bot.sent.clear(); fake_bot.edits.clear()
    msg = _start(uid)
    assert run(database.get_onboarding_state(uid))["status"] == "completed"
    # Mood entry, not the welcome card.
    assert any("Я не терапевт" in t for t in msg.rendered_texts())
    assert not any(oc.caption(1, "ru") == t for t in fake_bot.rendered_texts())


# ── C: richer legacy signals (not just messages.count > 0) ────────────────────
def test_invited_but_never_used_user_is_genuinely_new(tmp_db):
    """A user who was JUST invited (has an active user_access row) but has
    never sent a message, completed a questionnaire, journaled, etc. must
    still be eligible='new' -- user_access is deliberately NOT a legacy
    signal (spec item C)."""
    uid = 6001
    _authorized(uid)  # user_access row exists, nothing else does
    assert run(database.get_onboarding_eligibility(uid)) == "new"


def test_prior_existing_users_row_counts_as_legacy(tmp_db):
    uid = 6002
    run(database.upsert_user(uid, "u", "U"))  # a users row exists, no messages
    assert run(database.get_onboarding_eligibility(uid)) == "legacy"


def test_questionnaire_only_history_counts_as_legacy(tmp_db):
    uid = 6003
    run(database.start_questionnaire_session(uid, "demo_q", "1"))
    assert run(database.get_onboarding_eligibility(uid)) == "legacy"


def test_emotion_journal_only_history_counts_as_legacy(tmp_db):
    uid = 6004
    run(database.save_emotion_entry(uid, {"event": "x", "feeling": "y",
                                          "intensity": "3", "coped": "z"}))
    assert run(database.get_onboarding_eligibility(uid)) == "legacy"


def test_cbt_journal_only_history_counts_as_legacy(tmp_db):
    uid = 6005
    run(database.save_cbt_entry(uid, {"situation": "x", "automatic_thought": "y",
                                      "emotion": "z", "evidence_for": "a",
                                      "evidence_against": "b", "balanced_thought": "c"}))
    assert run(database.get_onboarding_eligibility(uid)) == "legacy"


def test_state_history_counts_as_legacy(tmp_db):
    uid = 6006
    from state_engine import DEFAULT_STATE
    run(database.save_state(uid, dict(DEFAULT_STATE)))
    assert run(database.get_onboarding_eligibility(uid)) == "legacy"


def test_profile_only_history_counts_as_legacy(tmp_db):
    uid = 6008
    run(database.save_profile(uid, {}))
    assert run(database.get_onboarding_eligibility(uid)) == "legacy"


def test_summary_only_history_counts_as_legacy(tmp_db):
    uid = 6009
    run(database.save_summary(uid, "a rolling summary with no other history"))
    assert run(database.get_onboarding_eligibility(uid)) == "legacy"


def test_eligibility_checked_before_upsert_not_after(tmp_db, fake_bot):
    """The classic footgun this item guards against: computing eligibility
    AFTER upsert_user would see the just-created users row and call EVERY
    user "legacy". cmd_start must compute it first -- verified end-to-end: a
    genuinely new, never-before-seen uid still gets the welcome screen."""
    uid = 6007
    _authorized(uid)
    assert run(database.get_onboarding_eligibility(uid)) == "new"
    _start(uid)
    # If eligibility had been computed after upsert, this would be
    # legacy_completed instead of active.
    assert run(database.get_onboarding_state(uid))["status"] == "active"


# ── B: real new-user language ──────────────────────────────────────────────────
# Policy (per explicit correction, supersedes an earlier en*->en/else->ru rule
# that would have wrongly defaulted a Dutch/German/etc. user into Russian):
#   "ru*" -> "ru"; EVERYTHING ELSE (missing/malformed/any other language tag,
#   including unrecognized ones) -> "en". A non-Russian, non-English Telegram
#   user must not be silently defaulted into Russian onboarding.
@pytest.mark.parametrize("uid,language_code,expected", [
    (7010, "ru", "ru"),
    (7011, "ru-RU", "ru"),
    (7012, "en", "en"),
    (7013, "en-US", "en"),
    (7014, "nl-NL", "en"),   # NOT ru -- a Dutch user must not get Russian onboarding
    (7015, "de-DE", "en"),   # NOT ru -- a German user must not get Russian onboarding
    (7016, None, "en"),
    (7017, "", "en"),
    (7018, "   ", "en"),
    (7019, "???", "en"),
    (7020, "-", "en"),
    (7021, "es", "en"),      # NOT ru -- a Spanish user must not get Russian onboarding
    (7022, "uk-UA", "en"),   # NOT ru -- Ukrainian is a distinct primary subtag from "ru"
])
def test_start_language_policy_matrix(tmp_db, fake_bot, uid, language_code, expected):
    _authorized(uid)
    _start(uid, language_code=language_code)
    assert run(database.get_user_language(uid)) == expected
    assert any(oc.caption(1, expected) == t for t in fake_bot.rendered_texts())
    other = "en" if expected == "ru" else "ru"
    assert not any(oc.caption(1, other) == t for t in fake_bot.rendered_texts())


def test_new_dutch_user_does_not_receive_russian_onboarding(tmp_db, fake_bot):
    """Explicit regression for the exact scenario named in the correction: a
    genuinely new nl-NL user must not receive Russian onboarding."""
    uid = 7100
    _authorized(uid)
    _start(uid, language_code="nl-NL")
    assert run(database.get_user_language(uid)) == "en"
    assert any(oc.caption(1, "en") == t for t in fake_bot.rendered_texts())
    assert not any(oc.caption(1, "ru") == t for t in fake_bot.rendered_texts())


# ── Stored-language preservation (fixed gap) ─────────────────────────────────
# Real bug found this pass: cmd_start previously re-normalized from Telegram's
# language_code on EVERY /start and unconditionally overwrote the stored
# value (upsert_user's ON CONFLICT always does language=excluded.language) --
# an existing user with an explicit stored preference would have it silently
# clobbered by whatever locale their Telegram client currently reports.
def test_existing_ru_user_keeps_ru_when_telegram_reports_en(tmp_db, fake_bot):
    uid = 7200
    _authorized(uid)
    _start(uid, language_code="ru")           # establishes stored "ru"
    assert run(database.get_user_language(uid)) == "ru"
    _start(uid, language_code="en")           # same user, Telegram now "en"
    assert run(database.get_user_language(uid)) == "ru"  # preserved, not overwritten


def test_existing_en_user_keeps_en_when_telegram_reports_ru(tmp_db, fake_bot):
    uid = 7201
    _authorized(uid)
    _start(uid, language_code="en")
    assert run(database.get_user_language(uid)) == "en"
    _start(uid, language_code="ru")
    assert run(database.get_user_language(uid)) == "en"


def test_repeated_start_same_language_is_stable(tmp_db, fake_bot):
    uid = 7202
    _authorized(uid)
    _start(uid, language_code="ru")
    _start(uid, language_code="ru")
    assert run(database.get_user_language(uid)) == "ru"


def test_invalid_stored_language_is_repaired_from_telegram(tmp_db, fake_bot):
    # A row with an invalid/legacy stored value (not "ru"/"en") is repaired
    # deterministically from the CURRENT Telegram locale, same as a new user.
    uid = 7203
    run(database.upsert_user(uid, "u", "U", "xx-invalid"))
    _authorized(uid)
    _start(uid, language_code="de-DE")
    assert run(database.get_user_language(uid)) == "en"


def test_invited_user_language_resolved_before_upsert(tmp_db, fake_bot, monkeypatch):
    """The invite-grant message fires BEFORE upsert_user ever runs -- it must
    use the FINAL resolved language, not the pre-upsert "ru" default
    get_user_language would have returned for a brand-new row."""
    monkeypatch.setattr(config, "USER_INVITE_ENABLED", True)
    code = "y" * 32
    monkeypatch.setattr(config, "USER_INVITE_CODE", code)
    uid = 7204
    msg = _start(uid, text=f"/start {code}", language_code="en")
    assert any("Access granted" in t for t, _kw in msg.answers)
    assert not any("Доступ открыт" in t for t, _kw in msg.answers)


def test_flag_disabled_language_resolution_unchanged(tmp_db, fake_bot, monkeypatch):
    import config as _config
    monkeypatch.setattr(_config, "FIRST_USER_ONBOARDING_ENABLED", False)
    uid = 7205
    _authorized(uid)
    _start(uid, language_code="en")
    assert run(database.get_user_language(uid)) == "en"
    _start(uid, language_code="ru")
    assert run(database.get_user_language(uid)) == "en"  # still preserved, flag off


def test_english_user_onboarding_callbacks_stay_in_english(tmp_db, fake_bot):
    uid = 7005
    _authorized(uid)
    _start(uid, language_code="en-GB")
    _tap(uid, oc.cb_next(2))
    assert any(oc.caption(2, "en") == t for t in fake_bot.rendered_texts())
    assert not any(oc.caption(2, "ru") == t for t in fake_bot.rendered_texts())


def test_russian_user_onboarding_callbacks_stay_in_russian(tmp_db, fake_bot):
    uid = 7006
    _authorized(uid)
    _start(uid, language_code="ru-RU")
    _tap(uid, oc.cb_next(2))
    assert any(oc.caption(2, "ru") == t for t in fake_bot.rendered_texts())
    assert not any(oc.caption(2, "en") == t for t in fake_bot.rendered_texts())


# ── D: real versioning ──────────────────────────────────────────────────────
def test_schema_primary_key_is_composite_user_and_version(tmp_db):
    import sqlite3
    con = sqlite3.connect(database.DB)
    cols = con.execute("PRAGMA table_info(user_onboarding_state)").fetchall()
    con.close()
    pk_cols = sorted(c[1] for c in cols if c[5] > 0)  # col[5] = pk index (1-based)
    assert pk_cols == ["onboarding_version", "user_id"]


def test_db_rejects_second_concurrently_active_row_for_same_user(tmp_db):
    """The real database invariant (spec item D): at most one ACTIVE row per
    user, enforced by SQLite itself, not just application logic."""
    import aiosqlite
    uid = 8001
    run(database.start_or_get_onboarding(uid, "v1"))

    async def _try_second_active():
        async with aiosqlite.connect(database.DB) as db:
            await db.execute(
                "INSERT INTO user_onboarding_state (user_id, onboarding_version, "
                "status, current_step) VALUES (?, 'v2', 'active', 1)", (uid,))
            await db.commit()

    with pytest.raises(aiosqlite.IntegrityError):
        run(_try_second_active())


def test_completed_v1_with_current_v1_falls_through_no_reonboard(tmp_db, fake_bot):
    uid = 8002
    _authorized(uid)
    _start(uid)
    run(database.skip_onboarding_to_privacy(uid, V))
    run(database.complete_onboarding(uid, V, privacy_notice_version=oc.PRIVACY_NOTICE_VERSION))
    fake_bot.sent.clear(); fake_bot.edits.clear()
    msg = _start(uid)
    assert not fake_bot.sent and not fake_bot.edits
    assert any("Я не терапевт" in t for t in msg.rendered_texts())


def test_active_v1_with_current_v1_resumes(tmp_db, fake_bot):
    uid = 8003
    _authorized(uid)
    _start(uid)
    run(database.advance_onboarding_step(uid, V, 1, 2))
    fake_bot.sent.clear(); fake_bot.edits.clear()
    _start(uid)
    assert run(database.get_active_onboarding_state(uid))["current_step"] == 2
    assert any(oc.caption(2, "ru") == t for t in fake_bot.rendered_texts())


def test_active_older_version_after_deployment_is_superseded_and_restarted(tmp_db, fake_bot, monkeypatch):
    """Simulates a deployment bumping ONBOARDING_VERSION mid-flight: a user has
    an ACTIVE row for the OLD version. Corrected policy (spec item F, chosen
    option): cmd_start marks that row 'superseded' (never old-version
    content, and NEVER 'completed'/'legacy_exempt' -- the user did not
    finish it and was not exempt from it) and immediately starts the
    MANDATORY current version's active flow -- it must NOT silently fall
    through to the ordinary greeting, which would make the mandatory version
    bump toothless for a user already mid-onboarding."""
    uid = 8004
    _authorized(uid)
    run(database.start_or_get_onboarding(uid, "v0-old"))
    run(database.advance_onboarding_step(uid, "v0-old", 1, 2))
    msg = _start(uid)  # current ONBOARDING_VERSION is "v1" (v0-old is stale)
    old_row = run(database.get_onboarding_state(uid, "v0-old"))
    assert old_row["status"] == "superseded"
    assert old_row["completed_at"] is None  # never falsely marked completed
    new_state = run(database.get_active_onboarding_state(uid))
    assert new_state is not None
    assert new_state["onboarding_version"] == V and new_state["current_step"] == 1
    # The mandatory current version's step-1 card was rendered, NOT the
    # ordinary mood entry -- the bump must actually reach this user.
    assert any(oc.caption(1, "ru") == t for t in fake_bot.rendered_texts())
    assert not any("Я не терапевт" in t for t in msg.rendered_texts())


def test_legacy_exempt_state_without_notice_ack_gets_privacy_only_screen(tmp_db, fake_bot):
    # Renamed + corrected (spec item F correction): a legacy_exempt row alone
    # is NOT sufficient to permanently silence onboarding -- this is exactly
    # the gap this pass closes. A legacy_exempt row created without ever
    # going through a real acknowledgement (e.g. a historical/synthetic
    # state) must still be independently prompted for the CURRENT notice.
    uid = 8005
    run(database.mark_onboarding_legacy_exempt(uid, V))
    _authorized(uid)
    msg = _start(uid)
    row = run(database.get_onboarding_state(uid, V))
    assert row["status"] == "legacy_exempt"
    assert row["completed_at"] is None  # exempted, not completed
    assert any(oc.caption(oc.LAST_STEP, "ru") == t for t in fake_bot.rendered_texts())
    assert not any("Я не терапевт" in t for t in msg.rendered_texts())


def test_legacy_exempt_state_with_notice_ack_falls_through(tmp_db, fake_bot):
    # The genuinely settled case: a legacy_exempt row AND a real,
    # independently recorded notice acknowledgement together mean nothing is
    # shown again.
    uid = 8007
    run(database.mark_onboarding_legacy_exempt(uid, V))
    run(database.record_notice_acknowledgement(uid, "privacy_notice", oc.PRIVACY_NOTICE_VERSION))
    _authorized(uid)
    msg = _start(uid)
    assert not fake_bot.sent and not fake_bot.edits
    assert any("Я не терапевт" in t for t in msg.rendered_texts())


def test_completed_row_records_privacy_notice_version(tmp_db, fake_bot):
    uid = 8006
    _authorized(uid)
    _start(uid)
    _tap(uid, oc.CB_SKIP)
    _tap(uid, oc.CB_START)
    row = run(database.get_onboarding_state(uid, V))
    assert row["status"] == "completed"
    assert row["privacy_notice_version"] == oc.PRIVACY_NOTICE_VERSION


def test_legacy_exempt_row_has_no_privacy_notice_version(tmp_db, fake_bot):
    """An exempted user never saw the notice -- nothing was acknowledged."""
    uid = 8007
    run(database.mark_onboarding_legacy_exempt(uid, V))
    row = run(database.get_onboarding_state(uid, V))
    assert row["privacy_notice_version"] is None


def test_superseded_row_is_never_marked_completed_or_exempt(tmp_db, fake_bot):
    uid = 8008
    _authorized(uid)
    run(database.start_or_get_onboarding(uid, "v0-old"))
    run(database.supersede_onboarding_version(uid, "v0-old"))
    row = run(database.get_onboarding_state(uid, "v0-old"))
    assert row["status"] == "superseded"
    assert row["completed_at"] is None
    assert row["privacy_notice_version"] is None


def test_old_version_callback_fails_safely_no_state_change(tmp_db, fake_bot):
    """D: an old-version callback (e.g. from a card rendered before a
    deployment bumped ONBOARDING_VERSION) must be answered (no hung spinner)
    and must NOT be interpreted against the current version's namespace."""
    uid = 8006
    _authorized(uid)
    _start(uid)
    before = run(database.get_onboarding_state(uid))
    cb = _tap(uid, "onb:v0-old:next:2")
    assert cb.answered == 1
    after = run(database.get_onboarding_state(uid))
    assert before == after  # no state change at all


def test_future_version_callback_also_fails_safely(tmp_db, fake_bot):
    uid = 8007
    _authorized(uid)
    _start(uid)
    before = run(database.get_onboarding_state(uid))
    cb = _tap(uid, "onb:v2-future:start")
    assert cb.answered == 1
    assert run(database.get_onboarding_state(uid)) == before


# ── State & persistence ───────────────────────────────────────────────────────
def test_one_row_per_user_and_version_stored(tmp_db, fake_bot):
    uid = 2001
    _authorized(uid)
    _start(uid)
    _start(uid)  # repeat /start must not create a second row
    import sqlite3
    con = sqlite3.connect(database.DB)
    n = con.execute("SELECT COUNT(*) FROM user_onboarding_state WHERE user_id=?", (uid,)).fetchone()[0]
    ver = con.execute("SELECT onboarding_version FROM user_onboarding_state WHERE user_id=?", (uid,)).fetchone()[0]
    con.close()
    assert n == 1 and ver == V


def test_restart_resumes_and_completion_persists(tmp_db, fake_bot):
    uid = 2002
    _authorized(uid)
    _start(uid)
    run(database.advance_onboarding_step(uid, V, 1, 2))
    # "Restart": state lives in the DB, so a fresh read still shows step 2.
    assert run(database.get_onboarding_state(uid))["current_step"] == 2
    run(database.skip_onboarding_to_privacy(uid, V))
    run(database.complete_onboarding(uid, V))
    again = run(database.get_onboarding_state(uid))
    assert again["status"] == "completed" and again["completed_at"]


def test_stale_callback_cannot_move_backward(tmp_db, fake_bot):
    uid = 2003
    _authorized(uid)
    _start(uid)
    _tap(uid, oc.cb_next(2))   # 1 -> 2
    _tap(uid, oc.cb_next(3))   # 2 -> 3
    # A replay of the step-1 button (target 2) must NOT regress to 2.
    _tap(uid, oc.cb_next(2))
    assert run(database.get_onboarding_state(uid))["current_step"] == 3


def test_double_tap_next_is_idempotent(tmp_db, fake_bot):
    uid = 2004
    _authorized(uid)
    _start(uid)
    _tap(uid, oc.cb_next(2))
    count_after_first = fake_bot.card_count()
    _tap(uid, oc.cb_next(2))   # same tap again
    assert run(database.get_onboarding_state(uid))["current_step"] == 2
    # Second (stale) tap does not re-render.
    assert fake_bot.card_count() == count_after_first


def test_concurrent_next_callbacks_do_not_corrupt(tmp_db, fake_bot):
    uid = 2005
    _authorized(uid)
    _start(uid)

    async def race():
        return await asyncio.gather(
            database.advance_onboarding_step(uid, V, 1, 2),
            database.advance_onboarding_step(uid, V, 1, 2),
        )

    results = run(race())
    assert sorted(results) == [False, True]          # exactly one winner
    assert run(database.get_onboarding_state(uid))["current_step"] == 2


def test_skip_moves_to_step5_without_completing(tmp_db, fake_bot):
    uid = 2006
    _authorized(uid)
    _start(uid)
    _tap(uid, oc.CB_SKIP)
    st = run(database.get_onboarding_state(uid))
    assert st["current_step"] == 5 and st["status"] == "active"
    assert st["skipped_information_at"] is not None
    assert st["completed_at"] is None
    # The step-5 (privacy) card was rendered.
    assert any(oc.caption(5, "ru") == t for t in fake_bot.rendered_texts())


def test_skip_from_middle_step_also_reaches_privacy(tmp_db, fake_bot):
    uid = 2007
    _authorized(uid)
    _start(uid)
    _tap(uid, oc.cb_next(2))    # on step 2
    _tap(uid, oc.CB_SKIP)
    assert run(database.get_onboarding_state(uid))["current_step"] == 5


def test_final_start_completes_exactly_once(tmp_db, fake_bot):
    uid = 2008
    _authorized(uid)
    _start(uid)
    _tap(uid, oc.CB_SKIP)                 # jump to privacy step
    _tap(uid, oc.CB_START)               # complete
    st = run(database.get_onboarding_state(uid))
    assert st["status"] == "completed" and st["privacy_notice_acknowledged_at"]
    _tap(uid, oc.CB_START)               # double tap Start
    # Mood entry opened exactly once total across both taps (double-tapped
    # Start does not re-open it) -- the second tap's FakeCb gets its own fresh
    # FakeMsg, so we check the completion row is unchanged rather than count
    # duplicate "not a therapist" lines across two different message objects.
    st_after_double = run(database.get_onboarding_state(uid))
    assert st_after_double == st


# ── G: one-card resume, no flood ───────────────────────────────────────────────
def test_repeated_start_during_active_onboarding_does_not_flood(tmp_db, fake_bot):
    uid = 2009
    _authorized(uid)
    _start(uid)
    count_after_first = fake_bot.card_count()
    assert count_after_first == 1
    _start(uid)  # repeated /start, still on step 1
    _start(uid)
    _start(uid)
    # Every resume EDITS the same card (edit succeeds against the fake bot),
    # so total card-affecting calls grow by one edit per /start, never by a
    # new duplicate SEND each time.
    assert len(fake_bot.sent) == 1                 # only the original send
    assert len(fake_bot.edits) == 3                 # 3 resumed /start calls, each an edit


def test_resume_edits_persisted_card_message_id(tmp_db, fake_bot):
    uid = 2010
    _authorized(uid)
    _start(uid)
    st = run(database.get_active_onboarding_state(uid))
    original_message_id = st["card_message_id"]
    assert original_message_id is not None
    _start(uid)
    assert fake_bot.edits[-1][2] == original_message_id  # same message edited


def test_resume_sends_one_replacement_when_edit_fails(tmp_db, fake_bot):
    from aiogram.exceptions import TelegramBadRequest
    uid = 2011
    _authorized(uid)
    _start(uid)
    fake_bot.edit_exc = TelegramBadRequest(method=None, message="message can't be edited")
    _start(uid)  # edit fails -> exactly one fresh replacement card
    assert len(fake_bot.sent) == 2   # original + one replacement, never more
    st = run(database.get_active_onboarding_state(uid))
    assert st["card_message_id"] == fake_bot._next_id  # points at the NEW card


# ── H: render/state recovery contract ─────────────────────────────────────────
def test_delivery_network_error_propagates_not_swallowed(tmp_db, fake_bot):
    """A genuine delivery failure (not a TelegramBadRequest) must propagate --
    it is a real error, not a recoverable shape mismatch. State (current_step)
    is already durably committed by the time this happens."""
    uid = 2012
    _authorized(uid)
    _start(uid)
    fake_bot.edit_exc = RuntimeError("network exploded")
    with pytest.raises(RuntimeError):
        _tap(uid, oc.cb_next(2))
    # The transition was already committed before delivery was attempted.
    assert run(database.get_onboarding_state(uid))["current_step"] == 2


def test_recoverable_pending_state_resumed_by_next_start(tmp_db, fake_bot):
    """After the propagated failure above, current_step is ahead of what was
    ever actually delivered (card_rendered_step trails). The NEXT /start must
    still work and deliver the real current step, without any special
    "pending" flag -- see database.card_rendered_step / bot._render_onboarding_card."""
    uid = 2013
    _authorized(uid)
    _start(uid)
    fake_bot.edit_exc = RuntimeError("network exploded")
    with pytest.raises(RuntimeError):
        _tap(uid, oc.cb_next(2))
    fake_bot.edit_exc = None  # network recovers
    fake_bot.sent.clear(); fake_bot.edits.clear()
    _start(uid)
    assert any(oc.caption(2, "ru") == t for t in fake_bot.rendered_texts())
    st = run(database.get_active_onboarding_state(uid))
    assert st["card_rendered_step"] == 2


# ── G: state-persistence failure AFTER a successful Telegram send/edit ───────
def test_card_ref_persistence_failure_after_successful_send_propagates(tmp_db, fake_bot, monkeypatch):
    """Telegram delivery SUCCEEDS, but persisting the card reference
    (database.set_onboarding_card_ref) then fails (e.g. DB locked/disk full).
    This must propagate -- never be swallowed -- and must NOT corrupt the
    already-committed current_step/status (set_onboarding_card_ref only ever
    touches card_* columns)."""
    import bot as bot_module
    uid = 2014
    _authorized(uid)
    _start(uid)

    async def boom(*a, **kw):
        raise RuntimeError("disk full")
    monkeypatch.setattr(bot_module, "set_onboarding_card_ref", boom)

    with pytest.raises(RuntimeError):
        _tap(uid, oc.cb_next(2))
    # The step transition itself is unaffected by the ref-persistence failure.
    st = run(database.get_onboarding_state(uid))
    assert st["current_step"] == 2
    # The card really was delivered (Telegram call succeeded) even though the
    # reference was never recorded -- card_rendered_step stays stale, exactly
    # the same recoverable-pending shape as a delivery failure.
    assert fake_bot.edits or fake_bot.sent


# ── G: concurrent /start for a brand-new user does not double-onboard ────────
def test_concurrent_start_for_new_user_creates_exactly_one_active_row(tmp_db, fake_bot):
    uid = 2015
    _authorized(uid)

    async def race():
        return await asyncio.gather(_start_async(uid), _start_async(uid))

    run(race())
    import sqlite3
    con = sqlite3.connect(database.DB)
    n = con.execute(
        "SELECT COUNT(*) FROM user_onboarding_state WHERE user_id=? AND status='active'",
        (uid,)).fetchone()[0]
    con.close()
    assert n == 1


async def _start_async(uid):
    import bot
    user = FakeUser(uid, language_code="ru")
    msg = FakeMsg(user, text="/start")
    await bot.cmd_start(msg)
    return msg


# ── Callback flow specifics ───────────────────────────────────────────────────
def test_next_advances_and_edits_same_card(tmp_db, fake_bot):
    uid = 3001
    _authorized(uid)
    _start(uid)
    _tap(uid, oc.cb_next(2))
    # Edited in place (single-card UX: no new SEND, only an edit) showing
    # step-2 caption. Kind is "text" today because no illustration files are
    # committed yet (default_asset_reader -> None); see test_onboarding_media.py
    # for the media-kind edit path exercised with an injected photo reader.
    assert not fake_bot.sent[1:]  # no card sent beyond the original step-1 card
    assert any(kind in ("text", "media") and cap == oc.caption(2, "ru")
              for kind, _, _, cap, _ in fake_bot.edits)


def test_start_opens_mood_entry_and_clears_keyboard(tmp_db, fake_bot):
    uid = 3002
    _authorized(uid)
    _start(uid)
    _tap(uid, oc.CB_SKIP)
    cb = _tap(uid, oc.CB_START)
    # Keyboard of the final card was removed, mood entry opened.
    assert len(fake_bot.markup_clears) >= 1
    assert any("Я не терапевт" in t for t in cb.message.rendered_texts())


def test_privacy_button_shows_summary_without_completing(tmp_db, fake_bot):
    uid = 3003
    _authorized(uid)
    _start(uid)
    _tap(uid, oc.CB_SKIP)                 # go to privacy screen
    before = run(database.get_onboarding_state(uid))
    cb = _tap(uid, oc.CB_PRIVACY)
    after = run(database.get_onboarding_state(uid))
    # State unchanged, not completed; a summary message was sent.
    assert after["status"] == "active" and after["current_step"] == 5
    assert before == after
    assert any("/privacy_export_all" in t for t in cb.message.rendered_texts())


def test_start_before_privacy_step_does_nothing(tmp_db, fake_bot):
    uid = 3004
    _authorized(uid)
    _start(uid)                          # on step 1
    _tap(uid, oc.CB_START)               # start not valid yet
    st = run(database.get_onboarding_state(uid))
    assert st["status"] == "active" and st["current_step"] == 1


def test_blocked_user_callback_is_noop(tmp_db, fake_bot):
    uid = 3005
    _authorized(uid)
    _start(uid)
    run(database.block_user_access(uid))        # lose access mid-onboarding
    _tap(uid, oc.cb_next(2))
    # Access recheck blocks navigation; state stays at step 1.
    assert run(database.get_onboarding_state(uid))["current_step"] == 1


def test_out_of_range_next_target_ignored(tmp_db, fake_bot):
    uid = 3006
    _authorized(uid)
    _start(uid)
    _tap(uid, f"onb:{V}:next:9")          # invalid target
    _tap(uid, f"onb:{V}:next:1")          # not a valid advance target
    assert run(database.get_onboarding_state(uid))["current_step"] == 1


# ── Copy & safety ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("lang", ["ru", "en"])
def test_welcome_states_no_diagnosis(lang):
    c = oc.caption(1, lang).lower()
    assert ("не ставлю диагнозы" in c) if lang == "ru" else ("do not diagnose" in c)


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_no_screen_claims_replaces_therapist(lang):
    for step in oc.STEPS:
        c = oc.caption(step, lang).lower()
        assert "replace a therapist" not in c
        assert "заменяю психолога" not in c or "не заменяю психолога" in c


def test_ru_crisis_screen_includes_emergency_route():
    c = oc.caption(2, "ru")
    assert "кризисн" in c.lower()


def test_en_crisis_screen_does_not_treat_112_as_universal():
    c = oc.caption(2, "en")
    assert "112" not in c
    assert "local emergency number" in c.lower()


# ── B (this correction round): onboarding NEVER infers a country/number from
# UI language, in EITHER direction, and duplicates no crisis-contact data. ───
@pytest.mark.parametrize("lang", ["ru", "en"])
def test_neither_language_crisis_screen_contains_a_specific_number(lang):
    """No digit sequence of length >= 2 anywhere in the crisis screen -- a
    specific emergency/hotline number would show up as one. Catches "112",
    "988", "911", "8-800-2000-122", or any other number, in EITHER language."""
    import re
    c = oc.caption(2, lang)
    assert re.search(r"(?<![A-Za-z])\d{2,}", c) is None, (lang, c)


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_crisis_screen_wording_is_neutral_local_service(lang):
    c = oc.caption(2, lang).lower()
    if lang == "ru":
        assert "местную экстренную службу" in c
        assert "местную кризисную службу" in c
    else:
        assert "local emergency number" in c
        assert "local crisis service" in c


def test_onboarding_content_never_imports_crisis_protocol():
    """Structural proof there is no second crisis-contact source and no
    language-as-country inference: onboarding_content.py does not import or
    call crisis_protocol.get_hotline at all (RU and EN screen-2 captions are
    both static, hand-written neutral strings). Checks actual import/call
    syntax, not comment text (a comment explaining WHY it must not be called
    is fine and expected)."""
    import ast
    import inspect
    import onboarding_content
    tree = ast.parse(inspect.getsource(onboarding_content))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name == "crisis_protocol" for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.module != "crisis_protocol"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "get_hotline"
    assert not hasattr(onboarding_content, "get_hotline")


def test_ru_and_en_crisis_screens_have_matching_neutral_structure():
    """Same shape in both languages: no digits, both mention a local
    emergency service AND a local crisis service -- proves neither language
    is treated as carrying more/better country information than the other."""
    import re
    ru, en = oc.caption(2, "ru"), oc.caption(2, "en")
    assert re.search(r"(?<![A-Za-z])\d{2,}", ru) is None
    assert re.search(r"(?<![A-Za-z])\d{2,}", en) is None


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_no_unsupported_modalities_advertised(lang):
    joined = " ".join(oc.caption(s, lang) for s in oc.STEPS).lower()
    for term in ("emdr", "ifs", "schema therapy", "схема-терапи", "eft", "десенсибилиз"):
        assert term not in joined, (lang, term)


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_no_screen_claims_end_to_end_encryption(lang):
    joined = " ".join(oc.caption(s, lang) for s in oc.STEPS).lower()
    assert "end-to-end" not in joined and "сквозное шифрование" not in joined


# ── Regressions against existing product behavior ─────────────────────────────
def test_flag_off_preserves_old_start_behavior(tmp_db, fake_bot, monkeypatch):
    monkeypatch.setattr(config, "FIRST_USER_ONBOARDING_ENABLED", False)
    uid = 4001
    _authorized(uid)
    msg = _start(uid)
    # No onboarding row, no photo card; the ordinary mood entry is shown.
    assert run(database.get_onboarding_state(uid)) is None
    assert not fake_bot.sent and not fake_bot.edits
    assert any("Я не терапевт" in t for t in msg.rendered_texts())


def test_mood_buttons_and_emotion_map_present_after_completion(tmp_db, fake_bot):
    uid = 4002
    _authorized(uid)
    _start(uid)
    _tap(uid, oc.CB_SKIP)
    cb = _tap(uid, oc.CB_START)
    # The mood-entry keyboard has mood:N buttons + the emotion-map row.
    datas = []
    for kb in cb.message.keyboards():
        for row in kb.inline_keyboard:
            for b in row:
                if b.callback_data:
                    datas.append(b.callback_data)
    assert any(d.startswith("mood:") for d in datas)
    assert "emotion:map" in datas


def test_questionnaire_discuss_button_still_qm(tmp_db):
    # The onboarding PR must not touch the questionnaire discuss namespace.
    import bot
    kb = bot._questionnaire_result_keyboard(777, "ru")
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "q:m:777" in datas
    assert not any(d.startswith("q:discuss") for d in datas)


def test_onboarding_callbacks_never_grant_access(tmp_db, fake_bot):
    uid = 4003  # no access at all
    # Even if a state row somehow existed, callbacks must not grant access.
    _tap(uid, oc.cb_next(2))
    assert run(database.user_has_active_access(uid)) is False
    assert run(ac.has_full_access(uid)) is False


# ── determine_onboarding_requirement decision matrix (spec item F correction) ─
# Pure function: no Telegram I/O, no DB access -- directly unit-tested against
# every row of the required decision matrix.
FULL = oc.FULL_ONBOARDING
PRIVACY_ONLY = oc.PRIVACY_NOTICE_ONLY
NOT_REQ = oc.NOT_REQUIRED


@pytest.mark.parametrize(
    "eligibility,has_active,has_current_row,notice_acked,expected", [
        ("new", False, False, False, FULL),          # truly new, notice missing
        ("new", False, False, True, FULL),            # truly new, notice already current (degenerate but decided by eligibility)
        ("legacy", False, False, False, PRIVACY_ONLY), # returning legacy, notice missing
        ("legacy", False, False, True, NOT_REQ),       # returning legacy, notice current
        ("legacy", False, True, True, NOT_REQ),        # current onboarding completed, notice current
        ("legacy", False, True, False, PRIVACY_ONLY),  # current onboarding completed, notice missing/old
        ("new", False, True, True, NOT_REQ),           # settled current version, notice current (eligibility irrelevant once has_current_row)
        ("new", False, True, False, PRIVACY_ONLY),     # settled current version, notice missing
        ("legacy", True, False, False, FULL),          # current active onboarding -> resume
        ("new", True, False, False, FULL),             # active state dominates regardless of eligibility
        ("legacy", True, True, True, FULL),            # active state dominates even if a current-version row exists
    ])
def test_determine_onboarding_requirement_decision_matrix(
        eligibility, has_active, has_current_row, notice_acked, expected):
    assert oc.determine_onboarding_requirement(
        eligibility=eligibility, has_active_state=has_active,
        has_current_version_row=has_current_row,
        notice_acknowledged=notice_acked) == expected


# ── Privacy-only screen language resolution (must reuse the same stored-
# language preservation as full onboarding -- never hardcode Russian) ────────
def test_privacy_only_screen_stored_ru_with_telegram_en_stays_ru(tmp_db, fake_bot):
    uid = 7300
    _authorized(uid)
    _make_prior_message(uid)
    _start(uid, language_code="ru")   # establishes stored "ru"
    fake_bot.sent.clear(); fake_bot.edits.clear()
    _start(uid, language_code="en")   # same user, Telegram now "en"
    assert any(oc.caption(oc.LAST_STEP, "ru") == t for t in fake_bot.rendered_texts())
    assert not any(oc.caption(oc.LAST_STEP, "en") == t for t in fake_bot.rendered_texts())


def test_privacy_only_screen_stored_en_with_telegram_ru_stays_en(tmp_db, fake_bot):
    uid = 7301
    _authorized(uid)
    _make_prior_message(uid)
    _start(uid, language_code="en")
    fake_bot.sent.clear(); fake_bot.edits.clear()
    _start(uid, language_code="ru")
    assert any(oc.caption(oc.LAST_STEP, "en") == t for t in fake_bot.rendered_texts())
    assert not any(oc.caption(oc.LAST_STEP, "ru") == t for t in fake_bot.rendered_texts())


# ── Privacy-only acknowledgement: real concurrency, cross-user, idempotency ──
def test_privacy_only_ack_concurrent_double_tap_exactly_one_winner(tmp_db, fake_bot):
    uid = 7302

    async def race():
        return await asyncio.gather(
            database.record_notice_acknowledgement(uid, "privacy_notice", oc.PRIVACY_NOTICE_VERSION),
            database.record_notice_acknowledgement(uid, "privacy_notice", oc.PRIVACY_NOTICE_VERSION),
        )

    results = run(race())
    assert sorted(results) == [False, True]  # exactly one winner
    assert run(database.has_privacy_notice_ack(uid, oc.PRIVACY_NOTICE_VERSION)) is True


def test_privacy_only_ack_callback_cross_user_cannot_leak(tmp_db, fake_bot):
    a, b = 7303, 7304
    _authorized(a)
    _authorized(b)
    _tap(a, oc.cb_privacy_only_start(oc.PRIVACY_NOTICE_VERSION))
    assert run(database.has_privacy_notice_ack(a, oc.PRIVACY_NOTICE_VERSION)) is True
    assert run(database.has_privacy_notice_ack(b, oc.PRIVACY_NOTICE_VERSION)) is False


def test_privacy_only_ack_double_tap_does_not_reopen_mood_entry(tmp_db, fake_bot):
    uid = 7305
    _authorized(a := uid)
    cb1 = _tap(uid, oc.cb_privacy_only_start(oc.PRIVACY_NOTICE_VERSION))
    assert any("Я не терапевт" in t for t in cb1.message.rendered_texts())
    cb1.message.answers.clear()
    cb2 = _tap(uid, oc.cb_privacy_only_start(oc.PRIVACY_NOTICE_VERSION))
    assert cb2.message.answers == []  # double tap: no second mood entry


def test_privacy_only_ack_answers_callback_even_when_flag_off(monkeypatch, tmp_db, fake_bot):
    monkeypatch.setattr(config, "FIRST_USER_ONBOARDING_ENABLED", False)
    uid = 7306
    _authorized(uid)
    cb = _tap(uid, oc.cb_privacy_only_start(oc.PRIVACY_NOTICE_VERSION))
    assert cb.answered >= 1
    assert run(database.has_privacy_notice_ack(uid, oc.PRIVACY_NOTICE_VERSION)) is False


# ── Privacy-only callback version binding (real P1 found during integration:
# the callback must be bound to the EXACT notice_version rendered on the
# card, never to whatever PRIVACY_NOTICE_VERSION happens to be current at tap
# time -- otherwise a stale v1 card left open across a v1->v2 bump could tap
# into a handler that blindly records an acknowledgement for v2, a notice the
# user never actually saw). ─────────────────────────────────────────────────
def test_privacy_only_current_version_callback_succeeds(tmp_db, fake_bot):
    uid = 7307
    _authorized(uid)
    _tap(uid, oc.cb_privacy_only_start(oc.PRIVACY_NOTICE_VERSION))
    assert run(database.has_privacy_notice_ack(uid, oc.PRIVACY_NOTICE_VERSION)) is True


def test_privacy_only_stale_notice_callback_does_not_acknowledge_current_version(
        tmp_db, fake_bot, monkeypatch):
    # Card was rendered under notice v1; the requirement then bumps to v2
    # (PRIVACY_NOTICE_VERSION changes) BEFORE the user taps the old card.
    uid = 7308
    _authorized(uid)
    stale_data = oc.cb_privacy_only_start("v1")
    monkeypatch.setattr(oc, "PRIVACY_NOTICE_VERSION", "v2")
    monkeypatch.setattr("bot.PRIVACY_NOTICE_VERSION", "v2")
    _tap(uid, stale_data)
    # The dangerous outcome this test forbids: a v1 card creating a v2 ack.
    assert run(database.has_privacy_notice_ack(uid, "v2")) is False
    # The safe outcome: rejected entirely, not even recorded as a v1 ack --
    # the user still owes an explicit look at whatever v2 actually says.
    assert run(database.has_privacy_notice_ack(uid, "v1")) is False


def test_privacy_only_forged_future_version_fails_closed(tmp_db, fake_bot):
    uid = 7309
    _authorized(uid)
    _tap(uid, oc.cb_privacy_only_start("v99-never-real"))
    assert run(database.has_privacy_notice_ack(uid, "v99-never-real")) is False
    assert run(database.has_privacy_notice_ack(uid, oc.PRIVACY_NOTICE_VERSION)) is False


def test_privacy_only_forged_notice_id_cannot_be_named_via_callback(tmp_db, fake_bot):
    # notice_id is a fixed literal inside the handler ("privacy_notice"),
    # never parsed from callback data -- there is no way for callback_data to
    # name a different notice id at all. A hand-crafted callback trying to do
    # so simply does not match CB_PRIVACY_ONLY_START_PREFIX and is a no-op.
    uid = 7310
    _authorized(uid)
    forged = f"onb:{oc.ONBOARDING_VERSION}:privacy_only_start_OTHER_NOTICE:{oc.PRIVACY_NOTICE_VERSION}"
    _tap(uid, forged)
    assert run(database.has_notice_acknowledgement(uid, "privacy_notice", oc.PRIVACY_NOTICE_VERSION)) is False
    assert run(database.has_notice_acknowledgement(uid, "some_other_notice", oc.PRIVACY_NOTICE_VERSION)) is False
