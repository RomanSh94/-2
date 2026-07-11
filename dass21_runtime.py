"""Owner-only DASS-21 runtime gate (PR #55) — fail-closed, no cached auth.

Decides, FRESH on every call, whether the exact owner-only DASS-21 flow is
available for a given user. It may read and hash the private definition file,
but never writes, never touches the DB/Telegram/LLM, and never caches an
authorization globally — every q:d/q:s/q:a/q:b touch and the completion screen
re-run this gate.

Fail-closed rules (any failure -> unavailable, neutral reason code, never a
fallback to another DASS definition, never inference from filename/family):
feature disabled; non-owner while owner-only; missing/empty/malformed SHA-256
pin; missing/unreadable file; hash mismatch; wrong definition id; wrong
version/translation/scorer metadata; wrong item/answer shape; any risk
metadata.
"""
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import config
import access_control

DASS21_DEFINITION_ID = "dass21_ru_fattakhov_2024"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# The exact clinical identity the private file must carry. Never inferred.
_EXPECTED_CLINICAL_METADATA = {
    "instrument_id": "dass",
    "instrument_version": "DASS-21",
    "translation_id": "fattakhov_ru_2024",
    "administration_mode": "self_report",
    "manifest_schema_version": 2,
    "scoring_contract_id": "dass21_official_subscales",
    "scoring_version": "unsw_template_v1",
    "risk_contract_id": None,
    "risk_contract_version": None,
}
_EXPECTED_VERSION = "DASS-21_FATTAKHOV_RU_2024"
_EXPECTED_ITEM_IDS = tuple(f"dass21_{n:02d}" for n in range(1, 22))
_EXPECTED_ANSWER_IDS = ("a0", "a1", "a2", "a3")
_EXPECTED_ANSWER_VALUES = ("0", "1", "2", "3")


@dataclass(frozen=True)
class Dass21RuntimeStatus:
    available: bool
    reason_code: str


def is_dass21_definition_id(definition_id) -> bool:
    return definition_id == DASS21_DEFINITION_ID


def is_dass21_definition(definition) -> bool:
    return (isinstance(definition, dict)
            and definition.get("id") == DASS21_DEFINITION_ID)


def _definition_shape_ok(d: dict) -> bool:
    if d.get("id") != DASS21_DEFINITION_ID:
        return False
    if d.get("version") != _EXPECTED_VERSION:
        return False
    if d.get("contains_risk_items") is not False:
        return False
    meta = d.get("clinical_instrument")
    if not isinstance(meta, dict) or meta != _EXPECTED_CLINICAL_METADATA:
        return False
    items = d.get("items") or []
    if tuple(i.get("id") for i in items) != _EXPECTED_ITEM_IDS:
        return False
    for item in items:
        if item.get("risk_flag"):
            return False
        options = item.get("options") or []
        if tuple(o.get("id") for o in options) != _EXPECTED_ANSWER_IDS:
            return False
        if tuple(o.get("value") for o in options) != _EXPECTED_ANSWER_VALUES:
            return False
        if any(o.get("risk_flag") for o in options):
            return False
    return True


def dass21_runtime_status(user_id) -> Dass21RuntimeStatus:
    """FRESH gate: feature flag -> owner -> hash pin -> file -> hash ->
    definition identity/shape. Reason codes are internal only — callers show
    the user the SAME neutral unavailable text for every code."""
    if not config.DASS21_ENABLED:
        return Dass21RuntimeStatus(False, "disabled")
    if config.DASS21_OWNER_ONLY and (
            access_control.OWNER_USER_ID is None
            or user_id != access_control.OWNER_USER_ID):
        return Dass21RuntimeStatus(False, "not-owner")
    pinned = (config.DASS21_DEFINITION_SHA256 or "").strip().lower()
    if not _SHA256_RE.fullmatch(pinned):
        return Dass21RuntimeStatus(False, "hash-pin-missing-or-malformed")
    path = Path(config.DASS21_DEFINITION_PATH)
    try:
        raw = path.read_bytes()
    except OSError:
        return Dass21RuntimeStatus(False, "definition-file-missing")
    if hashlib.sha256(raw).hexdigest() != pinned:
        return Dass21RuntimeStatus(False, "hash-mismatch")
    try:
        definition = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return Dass21RuntimeStatus(False, "definition-unparseable")
    if not isinstance(definition, dict) or not _definition_shape_ok(definition):
        return Dass21RuntimeStatus(False, "definition-identity-mismatch")
    return Dass21RuntimeStatus(True, "ok")
