"""X20 Scheduler — ежедневные check-in сообщения"""
import random
from datetime import datetime, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot
from prompts import get_checkin_msg
from database import get_checkin_users, update_last_checkin

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

def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    s = AsyncIOScheduler()
    s.add_job(_send_checkins, "cron", minute=0, args=[bot],
              id="checkins", replace_existing=True, misfire_grace_time=300)
    return s
