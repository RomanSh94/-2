"""DASS-21 product authorization — owner, invited, or public ordinary access.

Separated from dass21_runtime by design: the runtime module answers the pure,
user-independent question "is the DASS feature/definition intact?" (flags,
path, SHA pin, exact identity/shape); THIS module answers the async product
question "may THIS user use it right now?".

Authorization model (exact, never inferred):

    OWNER:    DASS integrity valid AND user_id == access_control.OWNER_USER_ID
    INVITED:  DASS integrity valid
              AND config.DASS21_INVITED_USERS_ENABLED (rollout flag, default
                  false)
              AND database.user_has_active_access(user_id)  (existing
                  user_access table -- active row, not blocked)
    PUBLIC:   DASS integrity valid
              AND access_control.DEPLOYMENT_MODE == "public"
              AND access_control.has_full_access(user_id) (fresh ordinary
                  product authorization; reviewers remain excluded)
    ANYONE ELSE: denied.

Notes:
- Calling /dass21 NEVER creates access; there is no auto-grant path here.
- DASS21_OWNER_ONLY=false never broadens access (integrity fails closed).
- Public access is admitted only through the existing ordinary-product gate;
  non-public invited behavior remains behind its explicit rollout flag.
- Decisions are FRESH on every call (no cache): revoking a user_access row
  mid-session blocks the very next answer/back/result.
- reason_code is internal-only; callers show the same neutral unavailable
  text for every denial.
"""
from dataclasses import dataclass

import access_control
import config
import database
import dass21_runtime


@dataclass(frozen=True)
class Dass21AccessDecision:
    allowed: bool
    reason_code: str


async def authorize_dass21_user(user_id) -> Dass21AccessDecision:
    integrity = dass21_runtime.dass21_integrity_status()
    if not integrity.available:
        return Dass21AccessDecision(False, integrity.reason_code)
    if (access_control.OWNER_USER_ID is not None
            and user_id == access_control.OWNER_USER_ID):
        return Dass21AccessDecision(True, "owner")
    if access_control.DEPLOYMENT_MODE == "public":
        try:
            ordinary_access = await access_control.has_full_access(user_id)
        except Exception:
            ordinary_access = False
        if ordinary_access:
            return Dass21AccessDecision(True, "public-ordinary-access")
        return Dass21AccessDecision(False, "public-product-access-denied")
    if config.DASS21_INVITED_USERS_ENABLED:
        if await database.user_has_active_access(user_id):
            return Dass21AccessDecision(True, "invited")
        return Dass21AccessDecision(False, "no-active-access")
    return Dass21AccessDecision(False, "invited-rollout-disabled")
