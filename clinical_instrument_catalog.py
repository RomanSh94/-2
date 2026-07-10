"""Clinical instrument manifest loader/validator — governance PR.

Metadata validation only. Does NOT implement scoring, does NOT interpret
answers, does NOT contain any real questionnaire item text, answer options,
scoring keys, interpretation tables, norms, percentiles, or translations —
only bibliographic/rights identification metadata (see
clinical_instruments_manifest.json / docs/clinical_instruments_research.md).

Not integrated into bot.py in this PR. This module exists so a future PR that
DOES build a real catalog UI or scoring engine has a single, fail-closed
source of truth for "is this instrument allowed to be shown as available" —
that decision must never be made ad hoc at a UI callsite.
"""
import json
from pathlib import Path

_VALID_ADMINISTRATION_MODES = {"self_report", "clinician_rated", "unknown"}
_VALID_ACTIVATION_STATUSES = {"metadata_only", "blocked", "ready", "metadata_incomplete"}
_VALID_DOMAINS = {"depression", "anxiety", "depression_anxiety_stress", "unknown"}

_REQUIRED_FIELDS_FOR_EVIDENCE = (
    "version", "license_status", "digital_reproduction_allowed",
    "commercial_use_allowed", "translation_use_allowed",
)


class InstrumentManifestError(ValueError):
    """Raised by validate_instrument_metadata / load_instrument_manifest. Any
    raise here means 'do not trust this manifest entry' -- callers must fail
    closed, never fall back to treating an invalid entry as usable."""


def load_instrument_manifest(path: Path) -> list[dict]:
    """Loads and validates every entry in the manifest at `path`. Raises
    InstrumentManifestError on ANY problem (malformed JSON, missing fields,
    duplicate ids, invalid enum values) -- never silently skips a bad entry
    and never returns a partial list."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise InstrumentManifestError(f"could not read/parse manifest: {e}") from e

    if not isinstance(raw, list):
        raise InstrumentManifestError("manifest must be a JSON array of instrument objects")

    seen_ids: set[str] = set()
    for item in raw:
        validate_instrument_metadata(item)
        iid = item["instrument_id"]
        if iid in seen_ids:
            raise InstrumentManifestError(f"duplicate instrument_id: {iid!r}")
        seen_ids.add(iid)
    return raw


def validate_instrument_metadata(item: dict) -> None:
    """Fail-closed structural + policy validation for a single manifest
    entry. Raises InstrumentManifestError on any violation. This function is
    intentionally strict and instrument-aware (not just generic schema
    validation) -- see the per-instrument checks below, each of which
    encodes a real classification decision from
    docs/clinical_instruments_research.md, not an arbitrary rule."""
    if not isinstance(item, dict):
        raise InstrumentManifestError(f"instrument entry must be an object, got {type(item)!r}")

    instrument_id = item.get("instrument_id")
    if not instrument_id or not isinstance(instrument_id, str):
        raise InstrumentManifestError("missing or invalid instrument_id")

    admin_mode = item.get("administration_mode")
    if not admin_mode:
        raise InstrumentManifestError(f"{instrument_id}: missing administration_mode")
    if admin_mode not in _VALID_ADMINISTRATION_MODES:
        raise InstrumentManifestError(
            f"{instrument_id}: invalid administration_mode {admin_mode!r}")

    activation_status = item.get("activation_status")
    if activation_status not in _VALID_ACTIVATION_STATUSES:
        raise InstrumentManifestError(
            f"{instrument_id}: unknown activation_status {activation_status!r}")

    domain = item.get("domain")
    if domain not in _VALID_DOMAINS:
        raise InstrumentManifestError(f"{instrument_id}: invalid domain {domain!r}")

    # ── instrument-specific hard rules (from docs/clinical_instruments_research.md) ──
    if instrument_id == "hdrs" and admin_mode == "self_report":
        raise InstrumentManifestError(
            "hdrs: HDRS is clinician-rated; administration_mode must never be self_report")

    if instrument_id == "dass" and activation_status == "ready" and not item.get("version"):
        raise InstrumentManifestError(
            "dass: cannot be ready without an explicit DASS-21/DASS-42 version")

    if instrument_id == "epds" and activation_status == "ready":
        population = item.get("population") or []
        if "perinatal" not in population and "postpartum" not in population:
            raise InstrumentManifestError(
                "epds: cannot be ready without perinatal/postpartum population metadata "
                "(applicability gate requirement)")

    if instrument_id in ("japs", "stas") and activation_status == "ready":
        raise InstrumentManifestError(
            f"{instrument_id}: cannot be ready while identity is incomplete")

    # ── generic "ready requires evidence" rule ──
    if activation_status == "ready":
        for field in _REQUIRED_FIELDS_FOR_EVIDENCE:
            value = item.get(field)
            if value is None or value is False:
                raise InstrumentManifestError(
                    f"{instrument_id}: cannot be ready without evidence for {field!r} "
                    f"(got {value!r})")


def can_activate_instrument(item: dict) -> bool:
    """True only if the entry is both structurally valid AND its
    activation_status is literally 'ready'. Never returns True for
    metadata_only/blocked/metadata_incomplete, and never raises -- a
    validation failure means 'cannot activate', not a crash."""
    try:
        validate_instrument_metadata(item)
    except InstrumentManifestError:
        return False
    return item.get("activation_status") == "ready"
