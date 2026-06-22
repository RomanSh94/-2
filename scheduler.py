"""X20 Scheduler — ежедневные check-in сообщения + кризисные follow-up'ы"""
import random
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from prompts import get_checkin_msg, get_crisis_followup, get_push_msg
from crisis_protocol import crisis_keyboard
from silence_engine import decide_push
import journals
from database import (
    get_checkin_users, update_last_checkin,
    get_active_crisis_events, mark_crisis_followup_sent,
    get_push_candidates, get_push_context, record_push,
    get_journal_reminder_users, set_journal_settings,
)

# Crisis follow-up cadence after the initial crisis message.
_CRISIS_OFFSETS = [("1h", 3600), ("24h", 86400), ("7d", 604800)]


def _parse_utc(ts: str) -> datetime:
    """Parse a SQLite datetime('now') string ('YYYY-MM-DD HH:MM:SS') as UTC."""
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


async def _send_crisis_followups(bot: Bot) -> None:
    """Gently check in on users after a crisis, at 1h / 24h / 7d, until the
    event is resolved (user pressed 'I'm safe'). Each tag is sent at most once."""
    now = datetime.now(timezone.utc)
    for eid, uid, lang, created_at, sent in await get_active_crisis_events():
        try:
            elapsed = (now - _parse_utc(created_at)).total_seconds()
        except (ValueError, TypeError):
            continue
        for tag, secs in _CRISIS_OFFSETS:
            if elapsed >= secs and tag not in sent:
                try:
                    await bot.send_message(uid, get_crisis_followup(lang, tag),
                                           reply_markup=crisis_keyboard(lang))
                    await mark_crisis_followup_sent(eid, tag)
                except Exception as e:
                    print(f"[scheduler] crisis followup {tag} failed {uid}: {e}")

async def _send_checkins(bot: Bot) -> None:
    hour = datetime.now(timezone.utc).hour
    users = await get_checkin_users()
    sent = 0
    for uid, _, checkin_hour, lang in users:
        if checkin_hour != hour:
            continue
        try:
            msg = get_checkin_msg(lang)
            await bot.send_message(uid, msg)
            await update_last_checkin(uid)
            sent += 1
        except Exception as e:
            print(f"[scheduler] checkin failed {uid}: {e}")
    if sent:
        print(f"[scheduler] Sent {sent} check-in(s) at UTC {hour}:00")

async def _send_silence_pushes(bot: Bot) -> None:
    """Re-engagement pushes (§8). All antispam logic lives in decide_push();
    here we just gather context, ask, and send."""
    now = datetime.now(timezone.utc)
    for uid, last_seen, lang in await get_push_candidates():
        try:
            last_activity = _parse_utc(last_seen)
        except (ValueError, TypeError):
            continue
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
        tz = u["tz_offset"] or 0
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
    s.add_job(_send_silence_pushes, "interval", minutes=30, args=[bot],
              id="silence_pushes", replace_existing=True, misfire_grace_time=600)
    s.add_job(_send_journal_checkins, "cron", minute=0, args=[bot],
              id="journal_checkins", replace_existing=True, misfire_grace_time=300)
    return s
