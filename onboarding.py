"""First-user illustrated onboarding — Telegram-facing renderer.

One reusable, dependency-injectable renderer: `send_or_edit_onboarding_card`.
It keeps the intended single-card UX (one illustrated card per user, edited in
place step-to-step) while degrading safely:

  * media-edit rejected by Telegram (TelegramBadRequest — e.g. the previous card
    was text-only, the message is too old, or the media can't be edited) -> send
    ONE fresh card, never dead-end, never a blanket `except Exception`;
  * illustration file missing/unreadable -> text-only card with the SAME caption
    and keyboard; the bot never crashes on a bad asset.

Addressed by (chat_id, message_id) rather than a specific Message/CallbackQuery
object, and driven through a `bot`-like object (anything exposing aiogram's
send_photo/send_message/edit_message_media/edit_message_text signatures) rather
than a `target.edit_*()` call. This is what makes ANY entrypoint able to
resume/edit the SAME persisted card: a fresh /start after a restart, the
ordinary-entry gate reacting to a blocked text/voice message, or the onboarding
callback handler itself — see database.card_chat_id/card_message_id and
database.set_onboarding_card_ref. A Message-object-only design could not do
this, since a stored (chat_id, message_id) from a previous process has no live
Message object to call .edit_text()/.edit_media() on.

Pure transition/eligibility logic and all captions/buttons live in
onboarding_content.py; the handler wiring (access recheck, state updates, mood
entry, persisting the card ref) lives in bot.py. This module only turns a
(step, lang) into a rendered/edited card, so it is testable with a fake `bot`
double and an injected asset reader — no real Telegram network access required.
"""
import os

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import (FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup,
                           InputMediaPhoto)

import onboarding_content as oc


def build_keyboard(step: int, lang: str = "ru",
                   privacy_policy_url: str = "") -> InlineKeyboardMarkup:
    """Turn onboarding_content.button_spec (a pure list-of-rows spec) into an
    aiogram markup. A spec button is either a callback button ({"text","cb"}) or
    a URL button ({"text","url"})."""
    rows = []
    for spec_row in oc.button_spec(step, lang, privacy_policy_url):
        row = []
        for b in spec_row:
            if "url" in b:
                row.append(InlineKeyboardButton(text=b["text"], url=b["url"]))
            else:
                row.append(InlineKeyboardButton(text=b["text"], callback_data=b["cb"]))
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_keyboard_privacy_only(lang: str = "ru",
                                privacy_policy_url: str = "") -> InlineKeyboardMarkup:
    """Same conversion as build_keyboard, sourced from
    oc.button_spec_privacy_only instead of oc.button_spec -- see that
    function for why the primary button's callback differs."""
    rows = []
    for spec_row in oc.button_spec_privacy_only(lang, privacy_policy_url):
        row = []
        for b in spec_row:
            if "url" in b:
                row.append(InlineKeyboardButton(text=b["text"], url=b["url"]))
            else:
                row.append(InlineKeyboardButton(text=b["text"], callback_data=b["cb"]))
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def default_asset_reader(step: int):
    """Return an FSInputFile for the step's illustration if the file exists and is
    a readable, non-empty file; otherwise None (-> text-only fallback). Never
    raises — a broken/absent asset must degrade, not crash onboarding. The path
    itself (oc.asset_path) is already resolved relative to onboarding_content.py's
    own location, so this is correct regardless of the process's cwd."""
    path = oc.asset_path(step)
    try:
        if os.path.isfile(path) and os.path.getsize(path) > 0 and os.access(path, os.R_OK):
            return FSInputFile(path)
    except OSError:
        return None
    return None


async def _send_new_card(bot, chat_id, caption, keyboard, photo):
    if photo is not None:
        msg = await bot.send_photo(chat_id=chat_id, photo=photo, caption=caption,
                                   reply_markup=keyboard)
    else:
        msg = await bot.send_message(chat_id=chat_id, text=caption, reply_markup=keyboard)
    if msg is None:
        return None
    return (msg.chat.id, msg.message_id)


async def _send_new_card_or_none_if_forbidden(bot, chat_id, caption, keyboard, photo):
    """A blocked bot (TelegramForbiddenError) cannot reach this chat at all
    right now -- retrying with a fresh send would fail identically, so this
    is not a "try a replacement" situation like TelegramBadRequest. Return
    None (no card delivered) instead of propagating: the caller then simply
    does not persist a card reference, leaving the recoverable-pending-state
    contract intact for whenever the user unblocks the bot and /starts again."""
    try:
        return await _send_new_card(bot, chat_id, caption, keyboard, photo)
    except TelegramForbiddenError:
        return None


async def send_or_edit_onboarding_card(bot, chat_id, step, lang, *, message_id=None,
                                       privacy_policy_url="",
                                       asset_reader=default_asset_reader,
                                       keyboard=None):
    """Render onboarding `step` (localized `lang`) as one illustrated card in
    `chat_id`, addressed by (chat_id, message_id) instead of a live
    Message/CallbackQuery object.

    message_id=None -> nothing to edit yet; always send a NEW card (the first
                       card ever sent to this user, or a forced fresh send).
    message_id=<int> -> try to edit that EXACT message in place; on a
                       TelegramBadRequest (can't be edited into the target
                       shape — text<->media mismatch, too old, not found,
                       etc.) send ONE fresh replacement card instead.

    keyboard=None -> build the keyboard from `step`/`lang` as before (the
                    onboarding_content.button_spec(step, ...) layout).
    keyboard=<InlineKeyboardMarkup> -> use this markup verbatim instead
                    (e.g. onboarding.build_keyboard_privacy_only for the
                    PRIVACY_NOTICE_ONLY screen, which reuses step 5's caption
                    but must not carry step 5's row-based CB_START button).
                    The caption is still `oc.caption(step, lang, ...)` either
                    way — only the keyboard is overridable.

    Returns (chat_id, message_id) for the card that is now actually visible in
    the chat, or None if even sending failed to yield an addressable message
    (defensive only — a real aiogram Bot always returns a Message on success).
    The caller is responsible for persisting this via
    database.set_onboarding_card_ref — this function has no database access.

    Exception contract (spec item G — only intentional Telegram/transport
    exceptions are caught, never a blanket `except Exception`):
      * TelegramBadRequest on the edit attempt (message too old, deleted,
        text<->media shape mismatch, "message to edit not found", etc.) ->
        recoverable: send ONE fresh replacement card instead.
      * TelegramForbiddenError (the user has blocked the bot) on EITHER the
        edit or any send -> NOT recoverable by retrying (a fresh send would
        fail identically) -> return None, no card delivered, no exception
        raised; the caller simply does not persist a card reference.
      * Anything else (TelegramNetworkError, TelegramRetryAfter, a programmer
        error) propagates uncaught. Per the render/state recovery contract
        (spec item G/H), the caller's state transition (current_step/status)
        is already durably committed before this function is ever invoked, so
        a propagated failure here just means the NEXT /start (or gate hit)
        retries delivery of the same, already-decided step; it is not data
        corruption, and hiding it behind a blanket except would only turn a
        visible, retriable failure into a silent, invisible one.
    """
    caption = oc.caption(step, lang, privacy_policy_url)
    keyboard = keyboard if keyboard is not None else build_keyboard(step, lang, privacy_policy_url)
    try:
        photo = asset_reader(step)
    except Exception:
        # An asset reader must never take onboarding down; treat any failure as
        # "no illustration" and fall back to a text-only card.
        photo = None

    if message_id is None:
        return await _send_new_card_or_none_if_forbidden(bot, chat_id, caption, keyboard, photo)

    try:
        if photo is not None:
            await bot.edit_message_media(
                chat_id=chat_id, message_id=message_id,
                media=InputMediaPhoto(media=photo, caption=caption),
                reply_markup=keyboard)
        else:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, text=caption,
                reply_markup=keyboard)
        return (chat_id, message_id)
    except TelegramBadRequest:
        # The one expected recoverable failure: this message can't be edited
        # into the target shape (too old, deleted, shape mismatch, etc.).
        # Send a single replacement card. The caller's transition was already
        # committed exactly once regardless of which branch renders — no
        # duplicate state transition here, only a duplicate delivery attempt.
        return await _send_new_card_or_none_if_forbidden(bot, chat_id, caption, keyboard, photo)
    except TelegramForbiddenError:
        return None
