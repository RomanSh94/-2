import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN         = os.getenv("BOT_TOKEN")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY")
ADMIN_PASSWORD    = os.getenv("ADMIN_PASSWORD", "change_me")
ADMIN_PORT        = int(os.getenv("ADMIN_PORT", "8080"))
DASHBOARD_HOST    = os.getenv("DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_SECRET  = os.getenv("DASHBOARD_SECRET", "")
ADMIN_USER_IDS    = [int(x.strip()) for x in os.getenv("ADMIN_USER_IDS","").split(",") if x.strip().isdigit()]

SMTP_HOST         = os.getenv("SMTP_HOST", "")
SMTP_PORT         = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER         = os.getenv("SMTP_USER", "")
SMTP_PASSWORD     = os.getenv("SMTP_PASSWORD", "")
ALERT_EMAIL_TO    = os.getenv("ALERT_EMAIL_TO", "")
ALERT_WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL", "")

AB_VARIANTS = [v.strip() for v in os.getenv("AB_VARIANTS","control,variant_a").split(",") if v.strip()]

ROUTER_VERSION    = "2.0"
PRACTICE_VERSION  = "v1"

# PR B (questionnaire result screens) — hard kill switch, default OFF. When
# false (the default; no .env entry sets this true), every questionnaire
# result/calculations/explanation entrypoint must behave byte-for-byte like
# PR A's dormant completion screen. See CLAUDE.md / bot.py's questionnaire
# section for the full gate order this flag sits in.
QUESTIONNAIRE_INTERPRETATION_ENABLED = (
    os.getenv("QUESTIONNAIRE_INTERPRETATION_ENABLED", "false").strip().lower()
    in ("1", "true", "yes", "on")
)

# PR A — private invite-based access for ordinary (non-owner, non-clinician)
# product users. A real production feature (unlike TEMP_TEST_INVITE_*, which
# is test-instance-only and time-boxed) — default OFF, and usable only once
# access_control.user_invite_active() also confirms the code meets the
# minimum-length bar. Never == compared directly at the call site in bot.py —
# hmac.compare_digest() is used there, since this is reachable by strangers.
USER_INVITE_ENABLED = (
    os.getenv("USER_INVITE_ENABLED", "false").strip().lower()
    in ("1", "true", "yes", "on")
)
USER_INVITE_CODE = os.getenv("USER_INVITE_CODE", "").strip()
