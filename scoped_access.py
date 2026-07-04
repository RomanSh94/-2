"""PR 1B-2 — Scoped Data Access.

The centralized enforcement point for any command that reads, exports, or
deletes a user's sensitive data keyed by uid. PR 1B-2 discovery found that
every existing bot.py handler already derives its uid from the Telegram
sender (self-read by CONVENTION, not by any enforced rule) -- this module
turns that convention into an explicit, testable check for the commands that
newly need it: privacy self-service and the reviewer's review-pack path.

This is a DIFFERENT axis from access_control.has_full_access. has_full_access
answers "may this uid use ordinary product features right now" and DENIES
CLINICIAN_REVIEWER entirely by design. scoped_access answers "may THIS
requester read THAT target's data, for THIS purpose" and is what lets a
CLINICIAN_REVIEWER (zero ordinary product access) legitimately read a mapped
tester's review pack. Privacy commands (export/delete) NEVER allow cross-user
access here, even for the OWNER or a mapped REVIEWER -- self-service is
strictly self-only, full stop.

Fail-closed: any resolver exception -> deny. Never let a broken check
accidentally grant cross-user access.
"""
import access_control

# Closed set -- a typo becomes an AssertionError immediately, not a silent
# always-False purpose nobody notices (same discipline as
# privacy_registry.CATEGORIES / EXPORT_POLICIES).
PURPOSES = {"self_view", "privacy_export", "privacy_delete", "review_pack"}

# These purposes are strictly self-only: requester_uid == target_uid, full
# stop, regardless of role. An OWNER or a mapped CLINICIAN_REVIEWER never
# gets to export/delete/self_view SOMEONE ELSE's data through this module --
# there is no role-based cross-user allowance for them at all.
_SELF_ONLY_PURPOSES = {"self_view", "privacy_export", "privacy_delete"}


class ScopedAccessDenied(PermissionError):
    """Raised by assert_can_read_user_data. The caller MUST NOT proceed --
    no partial read, no raw data, no confirmation of whether the target
    uid/role/mapping exists (that leaks information on its own)."""


def can_read_user_data(requester_uid: int, target_uid: int, purpose: str) -> bool:
    """True iff `requester_uid` may access `target_uid`'s data for `purpose`.

    self_view / privacy_export / privacy_delete: requester_uid == target_uid
    ONLY -- self-uid-equality is the entire rule, nothing else grants access,
    not even for OWNER or a mapped CLINICIAN_REVIEWER.

    review_pack: NOT a self-uid-equality check at all. This purpose delegates
    ENTIRELY to access_control.can_request_review_pack(requester_uid,
    target_uid) -- the single source of truth for that contract (OWNER->self,
    REVIEWER->mapped TESTER; everything else, including a TESTER or UNKNOWN
    requesting their OWN uid, is denied). This module deliberately does not
    re-implement any of those role conditions -- duplicating them here would
    let the two places drift out of sync in a future PR.

    Any exception here (bad purpose aside -- that's a programming error and
    asserts loudly) is treated as denial, never as access."""
    assert purpose in PURPOSES, f"scoped_access: unknown purpose {purpose!r}"
    try:
        if purpose in _SELF_ONLY_PURPOSES:
            return requester_uid == target_uid
        # purpose == "review_pack": pure delegation, no local role logic.
        return access_control.can_request_review_pack(requester_uid, target_uid)
    except Exception:
        return False


def assert_can_read_user_data(requester_uid: int, target_uid: int, purpose: str) -> None:
    if not can_read_user_data(requester_uid, target_uid, purpose):
        # Deliberately generic -- no target-role/mapping detail, so a denial
        # message can never be used to probe who is/isn't a tester, who is
        # mapped to whom, or whether a target uid exists at all.
        raise ScopedAccessDenied("scoped access denied")
