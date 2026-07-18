"""First-user onboarding — single-card media UX, caption/callback limits, the
text-only / edit-failure fallbacks, and module-relative asset resolution.

Exercises the Telegram-facing renderer (onboarding.send_or_edit_onboarding_card)
and the pure content specs (onboarding_content) WITHOUT any Telegram network
access, using a fake `bot` double (addressed by chat_id/message_id, matching
the real aiogram Bot API surface) and an injected asset reader.
"""
import asyncio
import os
import types

import pytest

from aiogram.exceptions import TelegramBadRequest

import onboarding
import onboarding_content as oc


# ── Fakes ─────────────────────────────────────────────────────────────────────
class FakeBot:
    """A `bot`-like double addressed by (chat_id, message_id) — NOT a specific
    Message/CallbackQuery object — matching send_or_edit_onboarding_card's real
    contract (spec items G/H: any entrypoint must be able to resume/edit the
    SAME persisted card via stored chat_id/message_id)."""

    def __init__(self):
        self.sent = []          # (kind, chat_id, caption/text, reply_markup)
        self.edits = []         # (kind, chat_id, message_id, caption/text, reply_markup)
        self.markup_clears = []
        self.edit_exc = None    # exception edit_message_media/text should raise
        self.send_exc = None    # exception send_photo/send_message should raise
        self._next_id = 9000

    def _new_id(self):
        self._next_id += 1
        return self._next_id

    async def send_photo(self, chat_id, photo, caption, reply_markup=None):
        if self.send_exc is not None:
            raise self.send_exc
        mid = self._new_id()
        self.sent.append(("photo", chat_id, caption, reply_markup))
        return types.SimpleNamespace(chat=types.SimpleNamespace(id=chat_id), message_id=mid)

    async def send_message(self, chat_id, text, reply_markup=None):
        if self.send_exc is not None:
            raise self.send_exc
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


_HAVE_PHOTO = lambda step: f"PHOTO:{step}"   # pretend every asset is present
_NO_PHOTO = lambda step: None                # pretend every asset is missing


# ── Image mapping ─────────────────────────────────────────────────────────────
def test_every_step_maps_to_its_required_illustration():
    expected = {
        1: "01_welcome.png", 2: "02_safety.png", 3: "03_topics.png",
        4: "04_features.png", 5: "05_privacy.png",
    }
    for step, fname in expected.items():
        p = oc.asset_path(step)
        assert p.endswith(os.path.join("assets", "onboarding", "v1", fname)), (step, p)
        # Illustration-only naming: no screenshot/mockup-style filenames.
        assert "mockup" not in p and "screenshot" not in p and "screen_" not in p


def test_exactly_five_steps():
    assert oc.STEPS == (1, 2, 3, 4, 5)
    assert oc.FIRST_STEP == 1 and oc.LAST_STEP == 5


# ── Caption / callback limits ─────────────────────────────────────────────────
@pytest.mark.parametrize("lang", ["ru", "en"])
def test_every_caption_within_telegram_photo_caption_limit(lang):
    for step in oc.STEPS:
        for url in ("", "https://example.org/privacy"):
            c = oc.caption(step, lang, url)
            assert 0 < len(c) <= oc.TELEGRAM_CAPTION_LIMIT == 1024, (lang, step, url, len(c))


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_every_callback_within_telegram_callback_limit(lang):
    seen = set()
    for step in oc.STEPS:
        for row in oc.button_spec(step, lang):
            for b in row:
                if "cb" in b:
                    seen.add(b["cb"])
                    assert len(b["cb"].encode("utf-8")) <= oc.TELEGRAM_CALLBACK_LIMIT == 64
    # The parametric next callbacks are also bounded.
    for t in (2, 3, 4, 5):
        assert len(oc.cb_next(t).encode("utf-8")) <= 64


# ── Button composition per step ───────────────────────────────────────────────
@pytest.mark.parametrize("lang", ["ru", "en"])
def test_step1_has_continue_and_skip(lang):
    spec = oc.button_spec(1, lang)
    cbs = [b.get("cb") for row in spec for b in row]
    assert oc.cb_next(2) in cbs
    assert oc.CB_SKIP in cbs


@pytest.mark.parametrize("lang", ["ru", "en"])
@pytest.mark.parametrize("step", [2, 3, 4])
def test_steps_2_3_4_have_primary_and_skip(lang, step):
    spec = oc.button_spec(step, lang)
    cbs = [b.get("cb") for row in spec for b in row]
    assert oc.cb_next(step + 1) in cbs, (lang, step, cbs)
    assert oc.CB_SKIP in cbs


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_step5_has_start_and_privacy_and_no_skip(lang):
    spec = oc.button_spec(5, lang)
    cbs = [b.get("cb") for row in spec for b in row]
    labels = [b.get("text") for row in spec for b in row]
    assert oc.CB_START in cbs
    # No skip button, and no skip callback, on the privacy screen.
    assert oc.CB_SKIP not in cbs
    assert not any("Skip" in (t or "") or "Пропустить" in (t or "") for t in labels)


def test_step5_privacy_button_is_url_when_configured_else_callback():
    # No URL configured -> callback button, labeled "About data and privacy"
    # (spec item F: NOT labeled as "the Privacy Policy" when none is linked).
    spec = oc.button_spec(5, "en")
    privacy_row = [row for row in spec if any(b.get("cb") == oc.CB_PRIVACY for b in row)]
    assert privacy_row
    assert privacy_row[0][0]["text"] == "About data and privacy"
    # Real URL configured -> URL button, labeled "Privacy Policy" (truthful:
    # it links straight to the real document).
    spec_url = oc.button_spec(5, "en", privacy_policy_url="https://example.org/privacy")
    url_row = [row for row in spec_url for b in row if b.get("url")]
    urls = [b.get("url") for row in spec_url for b in row]
    labels = [b.get("text") for row in spec_url for b in row if b.get("url")]
    assert "https://example.org/privacy" in urls
    assert labels == ["Privacy Policy"]


# ── Renderer: single-card reuse, edit-failure fallback, missing-image fallback ─
def test_step1_sends_one_photo_card():
    fb = FakeBot()
    ref = asyncio.run(onboarding.send_or_edit_onboarding_card(
        fb, 555, 1, "ru", message_id=None, asset_reader=_HAVE_PHOTO))
    assert len(fb.sent) == 1 and fb.sent[0][0] == "photo"
    assert fb.sent[0][1] == 555
    assert fb.sent[0][2] == oc.caption(1, "ru")
    assert not fb.edits
    assert ref == (555, 9001)  # first minted id


def test_media_edit_reuses_one_message_on_success():
    fb = FakeBot()
    ref = asyncio.run(onboarding.send_or_edit_onboarding_card(
        fb, 555, 2, "en", message_id=4242, asset_reader=_HAVE_PHOTO))
    # Edited in place, no new message -- SAME chat_id/message_id come back.
    assert len(fb.edits) == 1 and fb.edits[0][0] == "media"
    assert fb.edits[0][1:3] == (555, 4242)
    assert fb.edits[0][3] == oc.caption(2, "en")
    assert not fb.sent
    assert ref == (555, 4242)


def test_media_edit_failure_sends_one_replacement_card():
    fb = FakeBot()
    fb.edit_exc = TelegramBadRequest(method=None, message="message can't be edited")
    ref = asyncio.run(onboarding.send_or_edit_onboarding_card(
        fb, 555, 3, "ru", message_id=4242, asset_reader=_HAVE_PHOTO))
    # Exactly one fresh card, no partial/duplicate, and the NEW id is returned
    # (never the stale 4242).
    assert not fb.edits
    assert len(fb.sent) == 1 and fb.sent[0][0] == "photo"
    assert ref[1] != 4242


def test_missing_image_uses_text_only_fallback_on_send():
    fb = FakeBot()
    asyncio.run(onboarding.send_or_edit_onboarding_card(
        fb, 555, 1, "ru", message_id=None, asset_reader=_NO_PHOTO))
    assert len(fb.sent) == 1 and fb.sent[0][0] == "text"
    assert fb.sent[0][2] == oc.caption(1, "ru")


def test_missing_image_uses_text_only_fallback_on_edit():
    fb = FakeBot()
    asyncio.run(onboarding.send_or_edit_onboarding_card(
        fb, 555, 4, "en", message_id=4242, asset_reader=_NO_PHOTO))
    assert len(fb.edits) == 1 and fb.edits[0][0] == "text"
    assert fb.edits[0][3] == oc.caption(4, "en")


def test_asset_reader_exception_degrades_to_text_not_crash():
    fb = FakeBot()

    def boom(step):
        raise OSError("unreadable")

    asyncio.run(onboarding.send_or_edit_onboarding_card(
        fb, 555, 2, "ru", message_id=None, asset_reader=boom))
    assert len(fb.sent) == 1 and fb.sent[0][0] == "text"


def test_only_telegram_bad_request_is_caught_on_edit():
    """H: a genuine (non-TelegramBadRequest) failure during edit must PROPAGATE,
    never be swallowed -- it is not a recoverable "can't edit this shape"
    situation, it's a real error the caller/dispatcher must see."""
    fb = FakeBot()
    fb.edit_exc = RuntimeError("boom - not a TelegramBadRequest")
    with pytest.raises(RuntimeError):
        asyncio.run(onboarding.send_or_edit_onboarding_card(
            fb, 555, 2, "ru", message_id=4242, asset_reader=_HAVE_PHOTO))


# ── G: stale/deleted card -- both are just TelegramBadRequest on the edit ────
def test_stale_card_id_falls_back_to_replacement_card():
    fb = FakeBot()
    fb.edit_exc = TelegramBadRequest(method=None, message="message to edit not found")
    ref = asyncio.run(onboarding.send_or_edit_onboarding_card(
        fb, 555, 2, "ru", message_id=999999, asset_reader=_HAVE_PHOTO))
    assert len(fb.sent) == 1
    assert ref is not None and ref[1] != 999999


def test_deleted_card_falls_back_to_replacement_card():
    fb = FakeBot()
    fb.edit_exc = TelegramBadRequest(
        method=None, message="message can't be edited")  # Telegram's real wording when deleted
    ref = asyncio.run(onboarding.send_or_edit_onboarding_card(
        fb, 555, 3, "ru", message_id=4242, asset_reader=_HAVE_PHOTO))
    assert len(fb.sent) == 1
    assert not fb.edits
    assert ref is not None


# ── G: forbidden bot access (user blocked the bot) ───────────────────────────
def test_forbidden_on_edit_returns_none_without_attempting_replacement():
    """A blocked bot cannot reach this chat AT ALL right now -- a replacement
    send would fail identically, so (unlike TelegramBadRequest) this must NOT
    trigger a fallback send attempt, and must NOT raise."""
    from aiogram.exceptions import TelegramForbiddenError
    fb = FakeBot()
    fb.edit_exc = TelegramForbiddenError(method=None, message="bot was blocked by the user")
    ref = asyncio.run(onboarding.send_or_edit_onboarding_card(
        fb, 555, 2, "ru", message_id=4242, asset_reader=_HAVE_PHOTO))
    assert ref is None
    assert not fb.sent and not fb.edits


def test_forbidden_on_initial_send_returns_none_not_raise():
    from aiogram.exceptions import TelegramForbiddenError
    fb = FakeBot()
    fb.send_exc = TelegramForbiddenError(method=None, message="bot was blocked by the user")
    ref = asyncio.run(onboarding.send_or_edit_onboarding_card(
        fb, 555, 1, "ru", message_id=None, asset_reader=_HAVE_PHOTO))
    assert ref is None


def test_forbidden_on_fallback_send_after_bad_request_returns_none():
    """Edit fails with a recoverable TelegramBadRequest, so a fallback send is
    attempted -- but if THAT fails with TelegramForbiddenError (blocked
    between the edit attempt and the fallback, or just consistently
    unreachable), the whole call must still resolve to None, not raise."""
    from aiogram.exceptions import TelegramForbiddenError
    fb = FakeBot()
    fb.edit_exc = TelegramBadRequest(method=None, message="message can't be edited")
    fb.send_exc = TelegramForbiddenError(method=None, message="bot was blocked by the user")
    ref = asyncio.run(onboarding.send_or_edit_onboarding_card(
        fb, 555, 2, "ru", message_id=4242, asset_reader=_HAVE_PHOTO))
    assert ref is None


# ── G: replacement-send failure (a REAL error, not forbidden) must propagate ─
def test_replacement_send_failure_propagates_when_not_forbidden():
    """Edit fails recoverably (TelegramBadRequest), the fallback send is
    attempted, and THAT fails with a genuine, non-forbidden error -- this must
    propagate (it is not a recoverable "can't edit this shape" situation, and
    it is not the "user blocked the bot, nothing else to try" situation
    either -- it's a real failure the caller/dispatcher must see)."""
    fb = FakeBot()
    fb.edit_exc = TelegramBadRequest(method=None, message="message can't be edited")
    fb.send_exc = RuntimeError("network exploded on the fallback send too")
    with pytest.raises(RuntimeError):
        asyncio.run(onboarding.send_or_edit_onboarding_card(
            fb, 555, 2, "ru", message_id=4242, asset_reader=_HAVE_PHOTO))


# ── default_asset_reader against the real filesystem ──────────────────────────
def test_default_asset_reader_returns_none_when_file_absent():
    # Current production state: illustration files are not committed yet, so the
    # reader returns None for every step -> text-only onboarding works today.
    for step in oc.STEPS:
        if not os.path.isfile(oc.asset_path(step)):
            assert onboarding.default_asset_reader(step) is None


def test_default_asset_reader_reads_present_nonempty_file(tmp_path, monkeypatch):
    from aiogram.types import FSInputFile
    p = tmp_path / "01_welcome.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)  # non-empty, PNG-ish
    monkeypatch.setitem(oc.ASSETS, 1, str(p))
    got = onboarding.default_asset_reader(1)
    assert isinstance(got, FSInputFile)


def test_default_asset_reader_treats_empty_file_as_missing(tmp_path, monkeypatch):
    p = tmp_path / "02_safety.png"
    p.write_bytes(b"")  # zero-byte -> treated as missing (text-only)
    monkeypatch.setitem(oc.ASSETS, 2, str(p))
    assert onboarding.default_asset_reader(2) is None


# ── Asset paths resolved relative to the MODULE, not the process cwd (spec I) ──
def test_asset_path_is_absolute_and_module_anchored_regardless_of_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # simulate the bot launched from a different cwd
    p = oc.asset_path(1)
    assert os.path.isabs(p)
    expected_root = os.path.dirname(os.path.abspath(oc.__file__))
    assert p.startswith(expected_root)


def test_default_asset_reader_finds_real_file_after_cwd_change(monkeypatch, tmp_path):
    """A present asset at the TRUE resolved (module-anchored) path must still be
    found by default_asset_reader even when the process cwd is somewhere else
    entirely -- proves the fix is real, not just "asset_path looks absolute"."""
    monkeypatch.chdir(tmp_path)
    real_path = oc.asset_path(1)  # absolute, module-anchored -- independent of cwd
    created_dirs = []
    parent = os.path.dirname(real_path)
    # Create only the directories that don't already exist, so cleanup removes
    # exactly what this test added and nothing pre-existing.
    d = parent
    while not os.path.isdir(d):
        created_dirs.append(d)
        d = os.path.dirname(d)
    os.makedirs(parent, exist_ok=True)
    pre_existing = os.path.isfile(real_path)
    with open(real_path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    try:
        from aiogram.types import FSInputFile
        got = onboarding.default_asset_reader(1)
        assert isinstance(got, FSInputFile)
    finally:
        if not pre_existing:
            os.remove(real_path)
        for d in sorted(created_dirs, key=len, reverse=True):
            try:
                os.rmdir(d)
            except OSError:
                pass  # not empty / already gone -- best-effort cleanup only


# ── keyboard markup construction (aiogram) ────────────────────────────────────
def test_build_keyboard_url_vs_callback():
    kb = onboarding.build_keyboard(5, "ru", privacy_policy_url="https://ex.org/p")
    assert kb.inline_keyboard[0][0].callback_data == oc.CB_START
    assert kb.inline_keyboard[1][0].url == "https://ex.org/p"
    kb2 = onboarding.build_keyboard(5, "ru")
    assert kb2.inline_keyboard[1][0].callback_data == oc.CB_PRIVACY
