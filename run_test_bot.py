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

# PR C3a.1 — marks this process as the test instance. Must be set AFTER the
# .env.test values above are copied into os.environ, but BEFORE `from bot
# import main` (which triggers config.py's first import) so any code reading
# X20_TEST_INSTANCE at import time sees it. This is one of the required,
# fail-closed conditions for the temporary invite-access mechanism in
# access_control.py — it is never sufficient on its own (database.DB ==
# "x20_test.db", set below, is a separate, additional required condition).
os.environ.setdefault("X20_TEST_INSTANCE", "1")

import asyncio
import database
database.DB = "x20_test.db"          # isolate the DB before anything touches it

from bot import main                 # imports config (now reading .env.test)
import config
import access_control

if __name__ == "__main__":
    # Self-check banner (PR C3a) -- config.QUESTIONNAIRE_INTERPRETATION_ENABLED
    # is read from os.environ at config.py's IMPORT time (see config.py), which
    # already happened above via `from bot import main`. Changing .env.test on
    # disk after this process has started has no effect -- restart the test
    # process to pick up a new value. This banner exists so the owner can
    # visually confirm which process (and which flag value) they launched,
    # never so they can toggle the flag without a restart.
    print("🧪 X20 TEST bot")
    print(f"DB={database.DB}")
    print(f"QUESTIONNAIRE_INTERPRETATION_ENABLED={config.QUESTIONNAIRE_INTERPRETATION_ENABLED}")
    print("port=" + os.environ["ADMIN_PORT"])
    # PR C3a.1 -- never print the invite code itself, only whether the
    # mechanism is enabled/active and when it ends.
    _temp_invite_enabled = os.environ.get("TEMP_TEST_INVITE_ENABLED", "false").strip().lower() in (
        "1", "true", "yes", "on")
    print(f"TEMP_TEST_INVITE_ENABLED={_temp_invite_enabled}")
    print(f"TEMP_TEST_INVITE_ACTIVE={access_control.is_temp_test_invite_active()}")
    print(f"TEMP_TEST_INVITE_END_UTC={os.environ.get('TEMP_TEST_INVITE_END_UTC') or 'unset'}")
    asyncio.run(main())
