import os
import urllib.parse
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

# PR #55 — owner-only Russian DASS-21 (Fattakhov translation, official UNSW
# source). Disabled by default; the real definition file lives OUTSIDE Git
# (private_questionnaires/ is gitignored) and is integrity-pinned by SHA-256.
# An empty/malformed hash, a missing file, a hash mismatch, or wrong metadata
# inside the file all fail closed (see dass21_runtime.py) — there is never a
# fallback to another DASS definition.
DASS21_ENABLED = (
    os.getenv("DASS21_ENABLED", "false").strip().lower()
    in ("1", "true", "yes", "on")
)
DASS21_OWNER_ONLY = (
    os.getenv("DASS21_OWNER_ONLY", "true").strip().lower()
    in ("1", "true", "yes", "on")
)
DASS21_DEFINITION_PATH = os.getenv(
    "DASS21_DEFINITION_PATH",
    "private_questionnaires/dass21_ru_fattakhov_2024.json").strip()
DASS21_DEFINITION_SHA256 = os.getenv("DASS21_DEFINITION_SHA256", "").strip().lower()

# PR #59 — controlled invited-user DASS access. Default OFF. This is the ONLY
# switch that can admit non-owner users to DASS (an active user_access row is
# additionally required per user); DASS21_OWNER_ONLY=false never opens access
# (it fails closed for everyone -- see dass21_runtime/dass21_access).
DASS21_INVITED_USERS_ENABLED = (
    os.getenv("DASS21_INVITED_USERS_ENABLED", "false").strip().lower()
    in ("1", "true", "yes", "on")
)

# Workstream B — DASS-21 "discuss result" via the existing q:m:<session_id>
# namespace (no new callback namespace). Default OFF. Gates ONLY the visible
# discuss button on the DASS-21 completion screen and the q:m gate for DASS-21
# sessions; product access itself is still governed by dass21_access. The
# generic (synthetic, non-DASS) q:m flow is unaffected by this flag -- it
# keeps using QUESTIONNAIRE_INTERPRETATION_ENABLED as before.
DASS21_DISCUSSION_ENABLED = (
    os.getenv("DASS21_DISCUSSION_ENABLED", "false").strip().lower()
    in ("1", "true", "yes", "on")
)

# First-user illustrated onboarding (5 screens) — default OFF so /start behaves
# byte-for-byte as before. When true it affects ONLY genuinely new authorized
# users (see bot.cmd_start eligibility); returning and legacy users are never
# forced through it. Rollback = set false; no onboarding metadata is deleted and
# questionnaires are unaffected. Same safe boolean parser as every flag above.
FIRST_USER_ONBOARDING_ENABLED = (
    os.getenv("FIRST_USER_ONBOARDING_ENABLED", "false").strip().lower()
    in ("1", "true", "yes", "on")
)
# Optional real Privacy Policy URL for the onboarding privacy screen's secondary
# button. Empty by default — the screen then shows a deterministic in-bot privacy
# summary ("About data and privacy" / "О данных и приватности" — NOT labeled as
# the Privacy Policy, since none is configured) and the existing
# /privacy_export_all / /privacy_delete_all commands instead of a dead or
# invented link. Never hardcode a fake URL here.
#
# Validated at load time: only an absolute http(s) URL with a non-empty host is
# accepted as "a real policy URL"; anything else (empty, malformed, javascript:,
# a bare path, a URL with no host) is normalized to "" so the rest of the code
# can trust PRIVACY_POLICY_URL is either "" or safe-to-render. The raw env value
# is deliberately never logged (a malformed value could contain anything).
def _validate_privacy_policy_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        parsed = urllib.parse.urlparse(raw)
    except ValueError:
        return ""
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return ""
    return raw


PRIVACY_POLICY_URL = _validate_privacy_policy_url(os.getenv("PRIVACY_POLICY_URL", ""))

# Explicit, centralized, truthful onboarding rollout policy (spec item F
# correction). The name and behavior must actually match at runtime: full
# onboarding is shown ONLY to genuinely new users (bot.cmd_start's eligibility
# check via database.get_onboarding_eligibility) -- every returning/legacy
# user is independently, mandatorily re-checked for the CURRENT privacy
# notice (database.has_privacy_notice_ack), never silently exempted by an old
# onboarding-version completion/exemption row. The PREVIOUS name
# "MANDATORY_ALL" was misleading: it never actually forced returning users
# through the full 5-screen flow, only through an independent privacy check
# -- "NEW_USERS_ONLY" is what the runtime has always actually done.
# Only ONE policy has ever been implemented and tested; an unrecognized
# configured value is rejected deterministically at import time rather than
# silently falling back to a default the deployer never asked for.
_SUPPORTED_ONBOARDING_ROLLOUT_POLICIES = ("NEW_USERS_ONLY",)


def _validate_rollout_policy(raw: str) -> str:
    value = (raw or "").strip()
    if value not in _SUPPORTED_ONBOARDING_ROLLOUT_POLICIES:
        raise ValueError(
            f"Unsupported ONBOARDING_ROLLOUT_POLICY={value!r}; "
            f"supported values: {_SUPPORTED_ONBOARDING_ROLLOUT_POLICIES}")
    return value


ONBOARDING_ROLLOUT_POLICY = _validate_rollout_policy(
    os.getenv("ONBOARDING_ROLLOUT_POLICY", "NEW_USERS_ONLY"))
