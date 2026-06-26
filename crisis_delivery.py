"""X20 — crisis message DELIVERY layer (v6 §6.1–6.3).

safety = detection + decision + DELIVERY. Detection and decision are deterministic
and already hardened; this module hardens the last mile — actually getting the
crisis screen onto the user's phone even when the Telegram send is flaky.

A real prod incident (TelegramNetworkError on a crisis-related send, cb_crisis
call-text) proved a bare `await message.answer(...)` can drop a crisis message
silently. deliver_crisis wraps any crisis send in a ladder, stopping at the FIRST
success (the user never gets duplicates):

    rich (text + buttons)            -- retried on transient network errors
      → plain (text, no buttons)     -- markup can't break it; number is in body
        → minimal (plain hotline)    -- no HTML/markup at all, maximally sendable
          → total failure            -- P0 admin alert: nothing reached the user

Every send's outcome is written to crisis_message_delivery_log so "was it
delivered?" becomes a logged fact instead of a reconstruction.

The module's logic is aiogram-free: the actual send, the delivery-log write, and
the failure alert are INJECTED, so it unit-tests with plain fakes.
"""
import asyncio

from aiogram.exceptions import (
    TelegramNetworkError, TelegramRetryAfter, TelegramBadRequest,
    TelegramServerError,
)
from crisis_protocol import get_hotline

# Transient errors worth a short retry at the SAME ladder level before falling
# back. TelegramBadRequest is NOT here: a markup/format error won't fix on retry,
# so we drop straight to the next (simpler) level.
_TRANSIENT = (TelegramNetworkError, TelegramRetryAfter, TelegramServerError)


def _minimal_text(lang: str) -> str:
    """Last-resort message: plain text, NO HTML/markup, always with a hotline
    number. Nothing about it can trigger a markup error — only the network could
    stop it, and that level is retried."""
    h = get_hotline(lang)
    if (lang or "").lower().startswith("en"):
        return (f"If you might be in danger, please call now: {h['primary']} "
                f"(or emergency services {h['secondary']}). You are not alone.")
    return (f"Если есть риск, что ты можешь причинить себе вред — позвони сейчас: "
            f"{h['primary']} (или экстренная служба {h['secondary']}). "
            f"Ты не один(одна).")


async def _try_level(send, text, *, retries, backoff, sleep, **kwargs):
    """One ladder level. Retries ONLY transient network errors; a bad-request /
    other error drops to the next level immediately. Returns (ok, last_error)."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            await send(text, **kwargs)
            return True, None
        except _TRANSIENT as e:
            last_err = f"{type(e).__name__}: {e}"
            if attempt < retries:
                await sleep(backoff * (attempt + 1))
                continue
            return False, last_err
        except TelegramBadRequest as e:
            return False, f"{type(e).__name__}: {e}"
        except Exception as e:           # noqa: BLE001 — any send failure → fall back
            return False, f"{type(e).__name__}: {e}"
    return False, last_err


async def _safe_log(log, eid, uid, kind, level, error):
    if log is not None:
        try:
            await log(eid, uid, kind, level, error)
        except Exception:
            pass


async def deliver_crisis(send, *, text, kb, lang, uid, eid, kind,
                         log=None, on_total_failure=None,
                         retries=2, backoff=0.5, sleep=asyncio.sleep) -> str:
    """Deliver a crisis message through the rich → plain → minimal ladder,
    stopping at the FIRST success. Returns the delivered level:
    'rich' | 'plain' | 'minimal' | 'none'.

    send: callable like message.answer or partial(bot.send_message, uid).
    kb:   inline keyboard for the rich level, or None (then rich is skipped).
    log:  async (eid, uid, kind, level, error) — delivery-log writer (optional).
    on_total_failure: async (uid, eid, kind, error) — P0 alert when NOTHING got
          through (optional)."""
    err = None

    # 1. rich — full screen WITH buttons (skip if there are no buttons).
    if kb is not None:
        ok, err = await _try_level(send, text, retries=retries, backoff=backoff,
                                   sleep=sleep, parse_mode="HTML", reply_markup=kb)
        if ok:
            await _safe_log(log, eid, uid, kind, "rich", None)
            return "rich"

    # 2. plain — same text, no buttons. Markup can't break it; number is in body.
    ok, err = await _try_level(send, text, retries=retries, backoff=backoff,
                               sleep=sleep, parse_mode="HTML")
    if ok:
        await _safe_log(log, eid, uid, kind, "plain", err)
        return "plain"

    # 3. minimal — plain hotline text, no HTML/markup at all.
    ok, err = await _try_level(send, _minimal_text(lang), retries=retries,
                               backoff=backoff, sleep=sleep)
    if ok:
        await _safe_log(log, eid, uid, kind, "minimal", err)
        return "minimal"

    # 4. total failure — nothing reached the user. Log it and raise the alarm.
    await _safe_log(log, eid, uid, kind, "none", err)
    if on_total_failure is not None:
        try:
            await on_total_failure(uid, eid, kind, err)
        except Exception:
            pass
    return "none"
