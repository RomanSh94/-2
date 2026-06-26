"""X20 Scheduler — ежедневные check-in сообщения + кризисные follow-up'ы"""
import random
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from functools import partial
from prompts import get_checkin_msg, get_crisis_followup, get_push_msg
from crisis_protocol import crisis_screen
from crisis_delivery import deliver_crisis
from silence_engine import decide_push
from tz import effective_tz
from config import ADMIN_USER_IDS
import journals
from database import (
    get_checkin_users, update_last_checkin,
    get_active_crisis_events, mark_crisis_followup_sent,
    get_stage3_pending, auto_resolve_expired_crises,
    get_push_candidates, get_push_context, record_push,
    get_journal_reminder_users, set_journal_settings,
    log_crisis_delivery,
)


async def _send_crisis(bot: Bot, uid: int, text, kb, lang, eid, kind) -> str:
    """Scheduler-side binding of the crisis delivery ladder (same log + P0 alert
    as bot.py, but built from the Bot passed in — no import of bot.py)."""
    async def _alert(u, e, k, err):
        m = (f"🚨🚨 P0 CRISIS UNDELIVERED (followup) — uid={u} event={e} kind={k}\n"
             f"err={err}")
        for admin_id in ADMIN_USER_IDS:
            try:
                await bot.send_message(admin_id, m)
            except Exception:
                pass
    return await deliver_crisis(partial(bot.send_message, uid), text=text, kb=kb,
                                lang=lang, uid=uid, eid=eid, kind=kind,
                                log=log_crisis_delivery, on_total_failure=_alert)

# Crisis follow-up cadence after the initial crisis message.
_CRISIS_OFFSETS = [("1h", 3600), ("24h", 86400), ("7d", 604800)]

# Bounded retries when a follow-up SCREEN fails to deliver at any ladder level
# (mirrors the stage-3 redo cap). After this, mark done and rely on backstops.
_FOLLOWUP_MAX_RETRIES = 3


def _parse_utc(ts: str) -> datetime:
    """Parse a SQLite datetime('now') string ('YYYY-MM-DD HH:MM:SS') as UTC."""
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


async def _send_crisis_followups(bot: Bot) -> None:
    """Gently check in on users after a crisis, at 1h / 24h / 7d, until the
    event is resolved (user pressed 'I'm safe'). Each tag is sent at most once.
    After 7d the event is auto-resolved (lifecycle cleanup)."""
    await auto_resolve_expired_crises(7)
    now = datetime.now(timezone.utc)
    for eid, uid, lang, created_at, stage, sent in await get_active_crisis_events():
        try:
            elapsed = (now - _parse_utc(created_at)).total_seconds()
        except (ValueError, TypeError):
            continue
        for tag, secs in _CRISIS_OFFSETS:
            if elapsed >= secs and tag not in sent:
                try:
                    text, kb = crisis_screen(stage, lang, eid)
                    # DELIVERY ORDER IS INTENTIONAL — DO NOT reorder for "readability".
                    # The number-carrying SCREEN goes FIRST, the gentle intro second.
                    # On a half-failing network only one of two sends may get through
                    # (a real prod TelegramNetworkError proved the network flaps); the
                    # message that MUST arrive is the screen with the hotline, not a
                    # context-less "как ты?". intro-first would re-open the silent-
                    # delivery hole.
                    level = await _send_crisis(bot, uid, text, kb, lang, eid, "followup")
                    if level == "none":
                        # The screen reached the user at NO level (P0-alert already
                        # fired inside deliver_crisis). Do NOT mark the tag sent so the
                        # next tick retries the SCREEN — bounded by a stage-3-style cap
                        # so a long outage can't loop forever. The intro is NOT sent
                        # here, so there is nothing to spam on retry.
                        retries = [t for t in sent if t.startswith(f"{tag}_retry")]
                        if len(retries) < _FOLLOWUP_MAX_RETRIES:
                            await mark_crisis_followup_sent(eid, f"{tag}_retry{len(retries)+1}")
                        else:
                            # Retry budget spent — mark done and lean on the backstops:
                            # P0-alert, the next offset tag, and the pipeline active-gate
                            # (any user reply instantly re-shows the screen).
                            await mark_crisis_followup_sent(eid, tag)
                        continue
                    # Screen delivered → the gentle check-in may follow (best-effort,
                    # not gated; its failure doesn't undo the delivered screen).
                    await _send_crisis(bot, uid, get_crisis_followup(lang, tag), None,
                                       lang, eid, "followup_intro")
                    await mark_crisis_followup_sent(eid, tag)
                except Exception as e:
                    print(f"[scheduler] crisis followup {tag} failed {uid}: {e}")


_STAGE3_MAX_REDOS = 3


async def _send_stage3_followups(bot: Bot) -> None:
    """Stage-3 fast follow-up (5-10 min): if still unresolved, re-show the crisis
    screen + a repeat critical alert, with an antispam cap on the number of redos.
    Runs on a dedicated 3-min job so the 5-10 min window is actually honoured."""
    for eid, uid, lang, sent in await get_stage3_pending(min_minutes=5):
        redos = [t for t in sent if t.startswith("redo_")]
        if len(redos) >= _STAGE3_MAX_REDOS:
            continue
        tag = f"redo_{len(redos) + 1}"
        try:
            text, kb = crisis_screen(3, lang, eid)
            await _send_crisis(bot, uid, text, kb, lang, eid, "followup")
            for admin_id in ADMIN_USER_IDS:
                try:
                    await bot.send_message(
                        admin_id, f"🚨 #CRITICAL stage=3 (повтор {len(redos)+1}) "
                                  f"event_id={eid} user={uid} — событие не сведено.")
                except Exception:
                    pass
            # NOTE: do NOT gate this mark on delivery (unlike _send_crisis_followups).
            # Here `redo_N` is the cap COUNTER, not a delivered-flag: the screen is
            # re-sent as redo_{N+1} on the next 3-min tick regardless of outcome, up
            # to _STAGE3_MAX_REDOS. Gating by delivery would break that retry loop.
            await mark_crisis_followup_sent(eid, tag)
        except Exception as e:
            print(f"[scheduler] stage3 followup failed {uid}: {e}")

async def _send_checkins(bot: Bot) -> None:
    utc_hour = datetime.now(timezone.utc).hour
    users = await get_checkin_users()
    sent = 0
    for uid, _, checkin_hour, lang, tz, tz_set in users:
        # checkin_hour is the user's LOCAL hour; compare in local time.
        if checkin_hour != (utc_hour + effective_tz(tz, tz_set, lang)) % 24:
            continue
        try:
            msg = get_checkin_msg(lang)
            await bot.send_message(uid, msg)
            await update_last_checkin(uid)
            sent += 1
        except Exception as e:
            print(f"[scheduler] checkin failed {uid}: {e}")
    if sent:
        print(f"[scheduler] Sent {sent} check-in(s) at UTC {utc_hour}:00")

async def _send_silence_pushes(bot: Bot) -> None:
    """Re-engagement pushes (§8). All antispam logic lives in decide_push();
    here we just gather context, ask, and send."""
    now = datetime.now(timezone.utc)
    for uid, last_seen, lang, tz, tz_set in await get_push_candidates():
        try:
            last_activity = _parse_utc(last_seen)
        except (ValueError, TypeError):
            continue
        # Quiet hours evaluated in the user's effective LOCAL time, so the +3
        # default shift never pushes a notification into someone's night.
        local_now = now + timedelta(hours=effective_tz(tz, tz_set, lang))
        ctx = await get_push_context(uid)
        muted_until = None
        if ctx["mute_mode"] == "until" and ctx["mute_until"]:
            try:
                muted_until = _parse_utc(ctx["mute_until"])
            except (ValueError, TypeError):
                muted_until = None
        last_crisis_at = None
        if ctx["last_crisis_at"]:
            try:
                last_crisis_at = _parse_utc(ctx["last_crisis_at"])
            except (ValueError, TypeError):
                last_crisis_at = None
        tier_push_times: dict = {}
        for tier, ts in ctx["push_log"]:
            try:
                tier_push_times.setdefault(tier, []).append(_parse_utc(ts))
            except (ValueError, TypeError):
                continue

        tier = decide_push(
            now, last_activity,
            muted_until=muted_until,
            last_crisis_at=last_crisis_at,
            consecutive_unanswered=ctx["consecutive_unanswered"],
            tier_push_times=tier_push_times,
            quiet_now=local_now,
        )
        if not tier:
            continue
        try:
            await bot.send_message(uid, get_push_msg(lang or "ru", tier))
            await record_push(uid, tier)
        except Exception as e:
            print(f"[scheduler] push {tier} failed {uid}: {e}")


def _checkin_kb(kind: str, options) -> InlineKeyboardMarkup:
    rows, row = [], []
    for value, label in options:
        row.append(InlineKeyboardButton(text=label, callback_data=f"checkin:{kind}:{value}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _muted_or_post_crisis(uid: int, now: datetime) -> bool:
    """Reuse Silence-Engine antispam: respect mute and the 24h post-crisis
    cooldown so journal reminders never pile onto a fragile moment."""
    ctx = await get_push_context(uid)
    if ctx["mute_mode"] == "forever":
        return True
    if ctx["mute_mode"] == "until" and ctx["mute_until"]:
        try:
            if now < _parse_utc(ctx["mute_until"]):
                return True
        except (ValueError, TypeError):
            pass
    if ctx["last_crisis_at"]:
        try:
            if (now - _parse_utc(ctx["last_crisis_at"])) < timedelta(hours=24):
                return True
        except (ValueError, TypeError):
            pass
    return False


async def _send_journal_checkins(bot: Bot) -> None:
    """Morning/evening journal check-ins in the user's LOCAL time (tz_offset).
    Opt-in only, once per slot per local day, never during mute / post-crisis."""
    now = datetime.now(timezone.utc)
    for u in await get_journal_reminder_users():
        tz = effective_tz(u["tz_offset"], u["tz_set"], u["lang"])
        local = now + timedelta(hours=tz)
        local_hour = local.hour
        local_date = local.strftime("%Y-%m-%d")
        try:
            if await _muted_or_post_crisis(u["user_id"], now):
                continue
            if u["morning_enabled"] and local_hour == u["morning_hour"] \
                    and u["last_morning"] != local_date:
                await bot.send_message(u["user_id"], "Доброе утро. Как ты сейчас?",
                                       reply_markup=_checkin_kb("morning", journals.MORNING_OPTIONS))
                await set_journal_settings(u["user_id"], last_morning=local_date)
            elif u["evening_enabled"] and local_hour == u["evening_hour"] \
                    and u["last_evening"] != local_date:
                await bot.send_message(u["user_id"], "Вечер. Хочешь что-то записать?",
                                       reply_markup=_checkin_kb("evening", journals.EVENING_OPTIONS))
                await set_journal_settings(u["user_id"], last_evening=local_date)
        except Exception as e:
            print(f"[journal-checkin] {u['user_id']}: {type(e).__name__}: {e}")


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    s = AsyncIOScheduler()
    s.add_job(_send_checkins, "cron", minute=0, args=[bot],
              id="checkins", replace_existing=True, misfire_grace_time=300)
    s.add_job(_send_crisis_followups, "interval", minutes=15, args=[bot],
              id="crisis_followups", replace_existing=True, misfire_grace_time=600)
    s.add_job(_send_stage3_followups, "interval", minutes=3, args=[bot],
              id="stage3_followups", replace_existing=True, misfire_grace_time=120)
    s.add_job(_send_silence_pushes, "interval", minutes=30, args=[bot],
              id="silence_pushes", replace_existing=True, misfire_grace_time=600)
    s.add_job(_send_journal_checkins, "cron", minute=0, args=[bot],
              id="journal_checkins", replace_existing=True, misfire_grace_time=300)
    return s
