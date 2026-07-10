"""PR 1B-1 — Access Roles + A1 Gate + Alert Routing.

The FIRST real access-control layer in this project. Before this module, no
Telegram user_id check existed anywhere in bot.py — anyone who messaged the bot
got full product access. This module introduces roles and a deployment mode, but
does NOT and MUST NOT gate crisis detection/delivery for any role — that
invariant is enforced structurally by WHERE this module's checks are inserted in
bot.py (strictly after the existing RED-crisis-return), not by anything in here.

Roles: OWNER, CLINICIAN_TESTER, CLINICIAN_REVIEWER, UNKNOWN.
Modes: personal_use (default) | controlled_clinical_test | public (unsupported —
A1 always off, no separate governing PR/approval done yet in this codebase).

Fail-closed discipline: every function here defaults to the STRICTEST outcome
(no access, no A1, no alert) if role resolution or config lookup fails for any
reason. A broken resolver must never accidentally grant access or leak a
sensitive alert to the wrong channel — see CLINICAL_BOUNDARY.md §0.3.
"""
import os
from datetime import datetime, timedelta, timezone

from config import ADMIN_USER_IDS

OWNER = "OWNER"
CLINICIAN_TESTER = "CLINICIAN_TESTER"
CLINICIAN_REVIEWER = "CLINICIAN_REVIEWER"
UNKNOWN = "UNKNOWN"

DEPLOYMENT_MODE = os.getenv("DEPLOYMENT_MODE", "personal_use")

# Checkpoint-2 item 4: an unrecognized mode (e.g. a typo like "personl_use") must
# NOT silently fall through to full OWNER access. `_mode_is_valid()` is checked
# fresh in has_full_access/a1_allowed (not cached), same continuous-check
# discipline as resolved_reviewers_for.
VALID_DEPLOYMENT_MODES = {"personal_use", "controlled_clinical_test", "public"}


def _mode_is_valid() -> bool:
    return DEPLOYMENT_MODE in VALID_DEPLOYMENT_MODES

_OWNER_ENV = os.getenv("OWNER_USER_ID", "").strip()
OWNER_USER_ID = int(_OWNER_ENV) if _OWNER_ENV.isdigit() else None

CLINICIAN_TESTER_IDS = {int(x.strip()) for x in os.getenv("CLINICIAN_TESTER_IDS", "").split(",")
                        if x.strip().isdigit()}
CLINICIAN_REVIEWER_IDS = {int(x.strip()) for x in os.getenv("CLINICIAN_REVIEWER_IDS", "").split(",")
                          if x.strip().isdigit()}

# Many-to-many, built explicitly (no "one reviewer per tester" limit):
#   TESTER_REVIEWER_MAP=111:222,111:333,444:555
#   -> tester 111 has reviewers [222, 333]; tester 444 has reviewer [555].
# A repeated tester key APPENDS a reviewer, it does not overwrite.
def _parse_tester_reviewer_map(raw: str) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if ":" not in pair:
            continue
        t_str, r_str = pair.split(":", 1)
        t_str, r_str = t_str.strip(), r_str.strip()
        if t_str.isdigit() and r_str.isdigit():
            out.setdefault(int(t_str), []).append(int(r_str))
    return out


TESTER_REVIEWER_MAP: dict[int, list[int]] = _parse_tester_reviewer_map(
    os.getenv("TESTER_REVIEWER_MAP", ""))


def resolve_role(uid: int) -> str:
    """Pure, fast, no I/O. Callers that need fail-closed behavior on top of this
    (in case a future refactor makes this raise, or it's monkeypatched to raise
    in tests) wrap their OWN call to this in try/except — see below."""
    if OWNER_USER_ID is not None and uid == OWNER_USER_ID:
        return OWNER
    if uid in CLINICIAN_TESTER_IDS:
        return CLINICIAN_TESTER
    if uid in CLINICIAN_REVIEWER_IDS:
        return CLINICIAN_REVIEWER
    return UNKNOWN


def resolve_role_safe(uid: int) -> str:
    """resolve_role, but any exception (including a monkeypatched raise in tests
    simulating a broken resolver) is treated as UNKNOWN — the strictest role."""
    try:
        return resolve_role(uid)
    except Exception:
        return UNKNOWN


def resolved_reviewers_for(tester_uid: int) -> list[int]:
    """Reviewers explicitly mapped to this tester.

    Two safety filters beyond a raw dict lookup (checkpoint items 6-7):
      - only ids ALSO present in CLINICIAN_REVIEWER_IDS count. A mapping to an
        id that isn't a configured reviewer (typo, removed reviewer, etc.) is
        silently NOT a reviewer for isolation purposes — it must not receive
        alerts, and it must not count toward "this tester has a reviewer" for
        has_full_access.
      - deduplicated, order-preserving (a repeated identical pair in
        TESTER_REVIEWER_MAP must not cause a duplicate alert send).

    Empty list (not an exception) if unmapped, unknown, or on any lookup
    error — fail-closed."""
    try:
        raw = TESTER_REVIEWER_MAP.get(tester_uid, [])
        return list(dict.fromkeys(r for r in raw if r in CLINICIAN_REVIEWER_IDS))
    except Exception:
        return []


async def has_full_access(uid: int) -> bool:
    """Whether this uid may use ordinary product features right now.

    `public` mode: False for EVERYONE, including OWNER — public is unsupported
    and must not silently become "owner still gets full access, everyone else
    blocked" without a separate governing PR/approval (checkpoint item 5). RED
    still gets the full crisis screen regardless — that check happens
    structurally earlier in bot.py's pipeline, before this function is ever
    consulted, so this restriction cannot affect crisis delivery.

    OWNER (personal_use / controlled_clinical_test): always True.
    CLINICIAN_TESTER: True only if ALL of: mode is controlled_clinical_test, the
      tester has acknowledged the test-mode notice, AND at least one reviewer is
      currently mapped to them AND that reviewer id is a genuine, currently-
      configured CLINICIAN_REVIEWER (checkpoint item 6 — a mapping to a
      non-reviewer id does not count). The reviewer-mapping check is
      re-evaluated on EVERY call (not cached at acknowledgment time) so that if
      a mapping is later removed, a previously-active tester is disabled
      immediately, not left as a silently-orphaned active user with no reviewer
      (§ correction 6).
    CLINICIAN_REVIEWER / UNKNOWN: always False (reviewers get the review-pack
      path only, never ordinary bot product access; see can_request_review_pack).
    """
    if not _mode_is_valid() or DEPLOYMENT_MODE == "public":
        return False
    role = resolve_role_safe(uid)
    if role == OWNER:
        return True
    if role == CLINICIAN_TESTER:
        if DEPLOYMENT_MODE != "controlled_clinical_test":
            return False
        try:
            import database
            acknowledged = await database.get_tester_acknowledged(uid)
        except Exception:
            return False
        if not acknowledged:
            return False
        return bool(resolved_reviewers_for(uid))
    # PR C3a.1 — temporary invite-based test access. Only ever matters for a
    # uid that didn't already resolve via an existing role path above (OWNER /
    # CLINICIAN_TESTER are handled and returned above already). Structurally
    # inert unless every fail-closed condition in has_temp_test_access /
    # is_temp_test_invite_active holds (test instance + test DB + mode +
    # enabled flag + valid window) — see access_control.py's temp-invite block.
    if has_temp_test_access(uid):
        return True
    # PR A — ordinary-user private invite access. A permanent, production
    # mechanism (unlike the temp-invite block above): once a uid has an
    # active grant recorded in the `user_access` table (see
    # database.grant_user_access / cmd_start's deep-link handling), it has
    # ordinary product access forever, independent of role and of the
    # temp-invite mechanism. Fail-closed on any lookup error -- a broken DB
    # call must never accidentally grant access.
    try:
        import database
        if await database.user_has_active_access(uid):
            return True
    except Exception:
        pass
    return False


async def a1_allowed(requester_uid: int) -> bool:
    """Whether traced latent influence (A1) may be built for this requester right
    now. `public` mode is always denied outright. OWNER is allowed in
    personal_use and controlled_clinical_test. CLINICIAN_TESTER is allowed only
    when they currently have full access (acknowledged + reviewer-mapped) in
    controlled_clinical_test — i.e. A1 for a tester is a strict subset of
    "tester is even allowed to use the product right now". An active
    ordinary invite-registered user (PR A, `user_access` table) is allowed
    too — same "A1 is a subset of ordinary product access" principle, not a
    role: `resolve_role_safe` returns UNKNOWN for these uids (user_access
    registration is a separate mechanism from the OWNER/CLINICIAN_* role
    model), so this branch is checked explicitly rather than folded into the
    role dispatch above it. Never grants OWNER/reviewer/cross-user/dashboard
    access — this is exactly the same has_full_access() an ordinary user
    already has for the rest of the product, nothing more. Fail-closed: any
    DB lookup error -> False."""
    if not _mode_is_valid() or DEPLOYMENT_MODE == "public":
        return False
    role = resolve_role_safe(requester_uid)
    if role == OWNER:
        return True
    if role == CLINICIAN_TESTER:
        if DEPLOYMENT_MODE != "controlled_clinical_test":
            return False
        try:
            return await has_full_access(requester_uid)
        except Exception:
            return False
    try:
        import database
        return await database.user_has_active_access(requester_uid)
    except Exception:
        return False


class A1NotAllowed(PermissionError):
    """Raised by assert_a1_allowed. The caller (traced_response_builder) MUST NOT
    build or send the latent-influenced reply when this is raised — this is the
    role/mode gate, structurally earlier than and independent of PR 0's
    fail-closed-on-persist-failure guard. Both apply simultaneously."""


async def assert_a1_allowed(requester_uid: int) -> None:
    # PR C3a.1 — temp-invite users may exercise A1 only while their grant is
    # active; has_temp_test_access re-checks the full fail-closed condition set
    # (including window expiry) on every call, so this can never outlive the
    # invite window.
    if has_temp_test_access(requester_uid):
        return
    if not await a1_allowed(requester_uid):
        # Checkpoint item 8: no raw uid in the exception text — this is not yet
        # wired into a live print/log path (traced_response_builder has no bot.py
        # caller in PR 1B-1), but the message is sanitized now so a future PR that
        # DOES wire it in can't accidentally leak a uid through an error log by
        # forgetting to sanitize retroactively. Role/mode are not identifying.
        raise A1NotAllowed(
            f"A1 traced latent influence not allowed for this requester "
            f"(role={resolve_role_safe(requester_uid)}, mode={DEPLOYMENT_MODE})")


def should_alert_owner(uid: int) -> bool:
    """Whether the OWNER admin channel (ADMIN_USER_IDS) should receive an alert
    triggered by this uid's event. True for OWNER and CLINICIAN_REVIEWER (their
    own events reach the owner like any accountable known user always has).
    False for CLINICIAN_TESTER (isolation: owner must not see tester
    psychological content) and UNKNOWN (an uninvited person's crisis must not
    even generate a per-event alert naming their uid — see crisis_alert_targets).
    Fail-closed: any resolution error -> False (never alert on a broken check)."""
    return resolve_role_safe(uid) in (OWNER, CLINICIAN_REVIEWER)


def crisis_alert_targets(uid: int) -> tuple[str, list[int]]:
    """Single decision point for EVERY crisis-related admin/reviewer alert site
    (replaces what would otherwise be 6 separate per-callsite checks).

    Returns (kind, target_ids):
      ("owner", ADMIN_USER_IDS)      — OWNER/CLINICIAN_REVIEWER's own event
      ("reviewer", [reviewer_ids])   — CLINICIAN_TESTER event, reviewer(s) mapped
      ("none", [])                   — UNKNOWN, or CLINICIAN_TESTER with no
                                        mapped reviewer, or ANY resolution error

    "none" is the fail-closed default: an UNKNOWN person's crisis, or a broken
    role resolver mid-routing, produces NO per-event alert to anyone (uid + the
    fact of a RED event are themselves sensitive) — an optional fully-anonymized
    aggregate counter ("N unauthorized RED events / 24h") is left as a TODO for a
    future PR, deliberately not built here to keep this PR's scope tight."""
    try:
        if should_alert_owner(uid):
            return "owner", list(ADMIN_USER_IDS)
        reviewers = resolved_reviewers_for(uid)
        if reviewers:
            return "reviewer", reviewers
        return "none", []
    except Exception:
        return "none", []


def can_request_review_pack(requester_uid: int, target_uid: int) -> bool:
    """Permission CONTRACT only (PR 1B-1) — generate_review_pack() itself is not
    yet wired to this (PR 1B-2). Fixed now so "owner-only by default" never
    becomes an accidental precedent.

      OWNER    requesting OWNER's own pack               -> allowed
      REVIEWER requesting a TESTER's pack, IF mapped to
               that specific tester (TESTER_REVIEWER_MAP) -> allowed
      OWNER    requesting a TESTER's pack                 -> denied by default
               (direct consequence of the isolation model in §2/crisis_alert_targets)
      REVIEWER requesting OWNER's pack                    -> denied by default
               (would need a separate explicit owner-initiated path, not built here)
      everything else                                     -> denied

    Fail-closed: any resolution error -> False."""
    try:
        requester_role = resolve_role_safe(requester_uid)
        if requester_role == OWNER:
            return target_uid == OWNER_USER_ID
        if requester_role == CLINICIAN_REVIEWER:
            target_role = resolve_role_safe(target_uid)
            if target_role != CLINICIAN_TESTER:
                return False
            return requester_uid in resolved_reviewers_for(target_uid)
        return False
    except Exception:
        return False


# ── PR C3a.1 — temporary invite-based test access ──────────────────────────────
# A deliberately narrow, safety-bounded mechanism so the owner can grant
# short-lived (<=72h) test access to a Telegram id they don't know in advance,
# on the TEST INSTANCE ONLY. See CLAUDE.md / the PR description for full
# rationale. Every condition below is fail-closed and re-checked fresh (never
# cached) each time it matters. This block never stores, logs, or prints the
# actual invite code value anywhere — only plain string equality against it.

# In-memory only, by design: this is a short-lived, test-only mechanism. If the
# test process restarts, the grant is gone and the account holder can simply
# re-open the same invite deep link to grant again. No DB table.
_TEMP_TEST_GRANTED_UNTIL: dict[int, datetime] = {}

_TEMP_TEST_INVITE_MAX_WINDOW = timedelta(hours=72)


def _parse_utc_iso(raw: str):
    """Parse a UTC ISO-8601 timestamp string. Returns an aware UTC datetime, or
    None if raw is empty/unparseable. Never raises."""
    if not raw:
        return None
    try:
        # Accept a trailing "Z" (not accepted by fromisoformat on some Python
        # versions) as well as an explicit +00:00 offset or a naive timestamp
        # (treated as UTC).
        value = raw.strip()
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def temp_test_invite_config() -> dict:
    """Reads and validates every env-based condition for temporary invite
    access, fresh (no caching) every call. Returns a dict:
      {"valid": bool, "reason": str, "code": str|None,
       "start": datetime|None, "end": datetime|None}
    "code"/"start"/"end" are only populated when "valid" is True. The invite
    code value itself is never logged anywhere by this function or any caller
    — it is returned here purely so is_temp_test_invite_active/other functions
    in this module can compare it, never for display."""
    if DEPLOYMENT_MODE != "controlled_clinical_test":
        return {"valid": False, "reason": "deployment_mode", "code": None,
                 "start": None, "end": None}
    if os.getenv("X20_TEST_INSTANCE") != "1":
        return {"valid": False, "reason": "not_test_instance", "code": None,
                 "start": None, "end": None}
    try:
        import database
        if getattr(database, "DB", None) != "x20_test.db":
            return {"valid": False, "reason": "not_test_db", "code": None,
                     "start": None, "end": None}
    except Exception:
        return {"valid": False, "reason": "not_test_db", "code": None,
                 "start": None, "end": None}
    enabled = os.getenv("TEMP_TEST_INVITE_ENABLED", "false").strip().lower() in (
        "1", "true", "yes", "on")
    if not enabled:
        return {"valid": False, "reason": "disabled", "code": None,
                 "start": None, "end": None}
    code = os.getenv("TEMP_TEST_INVITE_CODE", "")
    if len(code) < 24:
        return {"valid": False, "reason": "code_too_short", "code": None,
                 "start": None, "end": None}
    start = _parse_utc_iso(os.getenv("TEMP_TEST_INVITE_START_UTC", ""))
    if start is None:
        return {"valid": False, "reason": "invalid_start", "code": None,
                 "start": None, "end": None}
    end = _parse_utc_iso(os.getenv("TEMP_TEST_INVITE_END_UTC", ""))
    if end is None:
        return {"valid": False, "reason": "invalid_end", "code": None,
                 "start": None, "end": None}
    if end <= start:
        return {"valid": False, "reason": "end_before_start", "code": None,
                 "start": None, "end": None}
    if (end - start) > _TEMP_TEST_INVITE_MAX_WINDOW:
        return {"valid": False, "reason": "window_too_long", "code": None,
                 "start": None, "end": None}
    return {"valid": True, "reason": "ok", "code": code, "start": start, "end": end}


def is_temp_test_invite_active(now: datetime | None = None) -> bool:
    """ALL 9 conditions from the PR description. Convention: the active window
    is [start, end) — inclusive of start, exclusive of end."""
    try:
        cfg = temp_test_invite_config()
        if not cfg["valid"]:
            return False
        now = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
        return cfg["start"] <= now < cfg["end"]
    except Exception:
        return False


def grant_temp_test_access(uid: int, now: datetime | None = None) -> bool:
    """Records a grant in memory for uid, valid until the invite window's end.
    Only actually grants if is_temp_test_invite_active() right now. Returns
    whether it granted."""
    try:
        if not is_temp_test_invite_active(now):
            return False
        cfg = temp_test_invite_config()
        _TEMP_TEST_GRANTED_UNTIL[uid] = cfg["end"]
        return True
    except Exception:
        return False


def has_temp_test_access(uid: int, now: datetime | None = None) -> bool:
    """True iff uid was previously granted AND the invite mechanism is still
    active AND uid's own grant hasn't expired. Re-checks is_temp_test_invite_active
    fresh so a grant never outlives the mechanism's own fail-closed conditions
    (e.g. mode flipped away from controlled_clinical_test, or the window ended)."""
    try:
        if not is_temp_test_invite_active(now):
            return False
        until = _TEMP_TEST_GRANTED_UNTIL.get(uid)
        if until is None:
            return False
        now = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
        return now < until
    except Exception:
        return False


def clear_expired_temp_test_access(now: datetime | None = None) -> None:
    """Housekeeping — prune expired entries from the in-memory grant dict."""
    try:
        now = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
        expired = [uid for uid, until in _TEMP_TEST_GRANTED_UNTIL.items() if now >= until]
        for uid in expired:
            del _TEMP_TEST_GRANTED_UNTIL[uid]
    except Exception:
        pass


# ── PR A — private invite-based access for ordinary product users ──────────────
# Permanent, production feature (contrast with the temp-invite block above,
# which is test-instance-only and <=72h-capped). Grants ordinary product
# access only -- never OWNER, never CLINICIAN_TESTER/REVIEWER, never A1. See
# database.grant_user_access / user_has_active_access / block_user_access for
# the persistence side, and bot.py's cmd_start for the deep-link call site
# (which uses hmac.compare_digest against config.USER_INVITE_CODE, never a
# plain == comparison, since this is reachable by any stranger with the link).

def user_invite_active() -> bool:
    """Whether the ordinary-user invite mechanism is currently usable: the
    feature flag is on AND a real code of sufficient length is configured.
    Re-checked fresh (no caching) every call, same discipline as
    temp_test_invite_config -- a config change takes effect immediately.
    Fail-closed: any lookup error -> False."""
    try:
        import config
        if not config.USER_INVITE_ENABLED:
            return False
        return len(config.USER_INVITE_CODE) >= 24
    except Exception:
        return False
