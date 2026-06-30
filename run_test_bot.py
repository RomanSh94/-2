"""
X20 TEST instance launcher — beta/staging, fully isolated from prod.

- Reads secrets from `.env.test` (separate test BOT_TOKEN), NOT `.env`.
- Uses a separate, throwaway database `x20_test.db` (prod x20.db untouched).
- Dashboard on a separate port (default 8081).

Run locally:  python run_test_bot.py
A test bot uses a DIFFERENT token than prod, so its getUpdates does NOT conflict
with the live bot.
"""
import os
from dotenv import dotenv_values

# Self-locating: resolve .env.test next to THIS file and run from here, so the
# launcher works regardless of the caller's CWD (and x20_test.db lands here too).
_HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(_HERE)
_vals = dotenv_values(os.path.join(_HERE, ".env.test"))
for k, v in _vals.items():
    if v is not None:
        os.environ[k] = v
if not os.environ.get("BOT_TOKEN"):
    raise SystemExit("run_test_bot: BOT_TOKEN missing — fill .env.test")
os.environ.setdefault("ADMIN_PORT", "8081")
# Instance marker — this launcher IS the test instance. Gates the crisis
# fault-injection hook (bot._fault_inject_n); prod (python bot.py) never sets it.
os.environ["X20_INSTANCE"] = "test"

import asyncio
import database
database.DB = "x20_test.db"          # isolate the DB before anything touches it

from bot import main                 # imports config (now reading .env.test)

if __name__ == "__main__":
    print("🧪 X20 TEST bot — DB=x20_test.db, port=" + os.environ["ADMIN_PORT"])
    asyncio.run(main())
