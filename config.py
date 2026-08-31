import os
import urllib.parse
from dotenv import load_dotenv

load_dotenv()


def _bounded_positive_int_env(name: str, default: int, upper_bound: int) -> int:
    """Read a required-positive, explicitly bounded integer setting."""
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(
            f"{name} must be an integer from 1 to {upper_bound}") from None
    if not 1 <= value <= upper_bound:
        raise ValueError(f"{name} must be an integer from 1 to {upper_bound}")
    return value

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

# Generic first-turn contract (persistence/concurrency foundation only in
# this phase -- not yet wired into the pipeline). FIRST_TURN_INITIAL_ROLLOUT_
# VERSION is frozen forever: it is the only version eligible for the lazy
# legacy-exemption bootstrap. A later FIRST_TURN_CONTRACT_VERSION bump makes
# every user newly eligible, independent of historical message presence.
FIRST_TURN_CONTRACT_VERSION        = "v1"
FIRST_TURN_INITIAL_ROLLOUT_VERSION = "v1"

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

# PR #59 — controlled invited-user DASS access. Default OFF. In non-public
# modes this is the only switch that can admit non-owner users (an active
# user_access row is additionally required per user). Public ordinary access
# is separately composed in dass21_access after the same integrity gate.
# DASS21_OWNER_ONLY=false never opens access in any mode; it fails closed.
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

# Therapeutic Core Foundation — default OFF. Gates ONLY the new explicit
# baseline-skip control on the existing before-score prompt (cb_before_skip /
# before_score_kb); flag false reproduces the prior score_kb keyboard
# byte-for-byte. Does NOT gate the dependency-monitor consolidation (an
# always-on safety correction, never a product feature) or the canonical
# production-practice allowlist (a safety/reachability enforcement, not new
# user-visible behavior — the 7 production ids were already the only ones
# ever actually selected).
THERAPEUTIC_CORE_FOUNDATION_ENABLED = (
    os.getenv("THERAPEUTIC_CORE_FOUNDATION_ENABLED", "false").strip().lower()
    in ("1", "true", "yes", "on")
)

# Master-prompt §24's off/owner/invited/all rollout contract for the NEW
# governed Therapeutic Core (session/hypothesis/intervention/outcome/memory —
# therapeutic_domain.py + database.py's core_* tables, Phase 1 onward). This is
# the ONE canonical rollout switch for that surface, going forward.
#
# Legacy-flag relationship (no two competing sources of truth): the boolean
# THERAPEUTIC_CORE_FOUNDATION_ENABLED above is scoped, permanently, to the
# narrow pre-existing fixes documented in its own comment (baseline-skip
# button, dependency-monitor consolidation, practice-registry reachability) —
# it predates this rollout model and MUST NOT be read by any Phase-1-forward
# Core code. Nothing in the new core_* storage or therapeutic_domain.py checks
# it, and this flag will never be repurposed to mean something new; it is
# simply a different, already-shipped feature that happens to share the
# "Therapeutic Core" name from the original handoff document.
# THERAPEUTIC_CORE_ROLLOUT_MODE is independent and starts at "off", so
# introducing it changes no runtime behavior (flag-off compatibility is
# preserved for both flags simultaneously). "invited" and "all" have no
# effect yet because no Phase 3+ user-facing Core behavior exists to gate —
# access_control.core_rollout_allowed() is the single check future phases
# must call before running any Core turn.
_THERAPEUTIC_CORE_ROLLOUT_MODES = ("off", "owner", "invited", "all")


def _validate_core_rollout_mode(raw: str) -> str:
    value = (raw or "off").strip().lower()
    if value not in _THERAPEUTIC_CORE_ROLLOUT_MODES:
        raise ValueError(
            f"Unsupported THERAPEUTIC_CORE_ROLLOUT_MODE={value!r}; "
            f"supported values: {_THERAPEUTIC_CORE_ROLLOUT_MODES}")
    return value


THERAPEUTIC_CORE_ROLLOUT_MODE = _validate_core_rollout_mode(
    os.getenv("THERAPEUTIC_CORE_ROLLOUT_MODE", "off"))

# Depression Disclosure Gate (Phase 2, master prompt §13) — default OFF, own
# flag rather than THERAPEUTIC_CORE_ROLLOUT_MODE: this gate is a standalone
# deterministic safety feature that runs independently of the Core session/
# hypothesis/intervention surface, not part of that Core rollout contract.
# Flag false => pipeline() behaves byte-for-byte as before this phase; no
# first-person depression disclosure is intercepted, no new DB row is ever
# created. Deploys dormant; do not flip true for owner/all except through an
# explicit later canary phase.
DEPRESSION_DISCLOSURE_GATE_ENABLED = (
    os.getenv("DEPRESSION_DISCLOSURE_GATE_ENABLED", "false").strip().lower()
    in ("1", "true", "yes", "on")
)

# Professional Free-Text Runtime V1 — default OFF, own feature-specific kill
# switch composed with (never replacing) THERAPEUTIC_CORE_ROLLOUT_MODE, same
# pattern as DEPRESSION_DISCLOSURE_GATE_ENABLED above: this flag alone never
# grants eligibility, and rollout population (owner/invited/all) is still
# decided entirely by the existing core_rollout_allowed contract. Flag false
# => pipeline() behaves byte-for-byte as before this slice; no ordinary
# free-text/voice turn is ever claimed by Professional Core, no
# scenario="professional" row is ever created. Deploys dormant; do not flip
# true for owner/invited/all except through a separately authorized canary
# phase (this PR is capability only).
PROFESSIONAL_FREE_TEXT_RUNTIME_ENABLED = (
    os.getenv("PROFESSIONAL_FREE_TEXT_RUNTIME_ENABLED", "false").strip().lower()
    in ("1", "true", "yes", "on")
)

# Public-beta Therapist Core V1.  Both values are required before the Core can
# claim a turn; an empty model deliberately disables it and is never replaced
# with a legacy/default model in code.
THERAPIST_CORE_V1_ENABLED = (
    os.getenv("THERAPIST_CORE_V1_ENABLED", "false").strip().lower()
    in ("1", "true", "yes", "on")
)
THERAPIST_CORE_V1_MODEL = os.getenv("THERAPIST_CORE_V1_MODEL", "").strip()
THERAPIST_CORE_V1_MAX_COMPLETION_TOKENS = _bounded_positive_int_env(
    "THERAPIST_CORE_V1_MAX_COMPLETION_TOKENS", 1200, 8192)


def _validate_feedback_chat_url(raw: str) -> str:
    """Return a renderable HTTPS/TG feedback URL, or hide it as empty."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        parsed = urllib.parse.urlparse(raw)
    except ValueError:
        return ""
    if parsed.scheme == "https" and parsed.netloc:
        return raw
    if parsed.scheme == "tg" and parsed.netloc:
        return raw
    return ""


FEEDBACK_CHAT_URL = _validate_feedback_chat_url(
    os.getenv("FEEDBACK_CHAT_URL", ""))


def feedback_chat_url() -> str:
    """Return the currently configured validated feedback destination."""
    return _validate_feedback_chat_url(FEEDBACK_CHAT_URL)

# ── Voice and Adaptive Response UX — both default OFF ───────────────────────
# VOICE_REPLIES_ENABLED gates: the /format selector, the "🔊 Прослушать"
# listen button, natural-language format/voice meta-commands, and the
# response-preferences-driven delivery (deliver_response in bot.py). Flag
# false => deliver_response always sends plain text, byte-for-byte the prior
# `await message.answer(answer)` behavior, and /format replies as if it were
# an unknown command (no selector shown, nothing saved).
VOICE_REPLIES_ENABLED = (
    os.getenv("VOICE_REPLIES_ENABLED", "false").strip().lower()
    in ("1", "true", "yes", "on")
)
# EMOTIONAL_REACTIONS_ENABLED gates ONLY the best-effort Telegram message
# reaction (reaction_selector.py + bot.py's _maybe_react). Independent of
# VOICE_REPLIES_ENABLED -- a deployment can enable one without the other.
EMOTIONAL_REACTIONS_ENABLED = (
    os.getenv("EMOTIONAL_REACTIONS_ENABLED", "false").strip().lower()
    in ("1", "true", "yes", "on")
)

# ElevenLabs TTS transport. Both gates must be true before any user text can
# leave X20 for speech synthesis. The key is environment-only; the selected
# voice is an opaque owner-approved ID with no invented public metadata.
ELEVENLABS_TTS_ENABLED = (
    os.getenv("ELEVENLABS_TTS_ENABLED", "false").strip().lower()
    in ("1", "true", "yes", "on")
)
ELEVENLABS_API_KEY      = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID     = "N8lIVPsFkvOoqev5Csxo"
ELEVENLABS_MODEL_ID     = "eleven_multilingual_v2"
ELEVENLABS_OUTPUT_FORMAT = "opus_48000_32"
TTS_TIMEOUT_SECONDS    = int(os.getenv("TTS_TIMEOUT_SECONDS", "20"))
TTS_MAX_INPUT_CHARS    = int(os.getenv("TTS_MAX_INPUT_CHARS", "10000"))
TTS_MAX_AUDIO_SECONDS  = int(os.getenv("TTS_MAX_AUDIO_SECONDS", "90"))

# Reaction configuration (not rollout flags -- inert while
# EMOTIONAL_REACTIONS_ENABLED is false).
EMOTIONAL_REACTION_COOLDOWN_SECONDS = int(
    os.getenv("EMOTIONAL_REACTION_COOLDOWN_SECONDS", "300"))
EMOTIONAL_REACTION_MIN_CONFIDENCE = float(
    os.getenv("EMOTIONAL_REACTION_MIN_CONFIDENCE", "0.9"))

# Bounded TTLs for the two pieces of ephemeral FSM-scoped state used by
# format-command replay (not rollout flags -- inert while
# VOICE_REPLIES_ENABLED is false). Both are plain configuration values, not
# feature flags: no default or migration path ever changes their meaning.
# ONE_SHOT_OVERRIDE: how long a "voice the next reply" armed-but-unconsumed
# override (from "лень читать" with nothing yet to voice-ify) remains valid.
# LAST_RESPONSE: how long a successfully delivered final ordinary answer
# stays eligible to be replayed by a later "лень читать"/"много текста".
VOICE_ONE_SHOT_OVERRIDE_TTL_SECONDS = int(
    os.getenv("VOICE_ONE_SHOT_OVERRIDE_TTL_SECONDS", "300"))
VOICE_LAST_RESPONSE_TTL_SECONDS = int(
    os.getenv("VOICE_LAST_RESPONSE_TTL_SECONDS", "21600"))
