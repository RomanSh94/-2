"""Clinical instrument manifest loader/validator — governance PR (schema v2).

Metadata validation only. Does NOT implement scoring, does NOT interpret
answers, does NOT contain any real questionnaire item text, answer options,
scoring keys, interpretation tables, norms, percentiles, or translations —
only bibliographic/rights identification metadata (see
clinical_instruments_manifest.json / docs/clinical_instruments_research.md).

Schema v2 design decisions (v6 governance corrections):
- Rights are STATUS ENUMS, never booleans. A boolean `false` would collapse
  "not investigated", "not proven", "permission required", and "explicitly
  prohibited" into one indistinguishable value — governance decisions need to
  know WHICH of those it is. Fail-closed rule: anything except
  allowed/allowed_with_conditions WITH structured evidence -> cannot activate.
- Identity is a status enum too: family identification (e.g. "this is some
  Beck Depression Inventory") is explicitly distinct from exact-version
  verification (BDI vs BDI-II) — an instrument is never implementation-ready
  on family identification alone.
- Evidence is structured records (kind/title/url/accessed_at/supports), not
  prose. A psytests.org source_page supports identification/discovery ONLY —
  it can never support license/rights/scoring claims (enforced below).
- No executable risk-item ids are encoded while exact version/translation is
  unverified; risk_item_metadata_status stays "unverified" until an exact
  approved definition exists.

Not integrated into bot.py in this PR.
"""
import json
from pathlib import Path

_VALID_ADMINISTRATION_MODES = {"self_report", "clinician_rated", "unknown"}
_VALID_ACTIVATION_STATUSES = {"metadata_only", "blocked", "ready"}
_VALID_IDENTITY_STATUSES = {
    "verified", "family_identified_version_incomplete",
    "metadata_incomplete", "identity_conflict",
}
_VALID_DOMAINS = {
    "depression", "anxiety", "depression_anxiety_stress", "occupational", "unknown",
}
_VALID_RIGHTS_STATUSES = {
    "unknown", "permission_required", "allowed", "allowed_with_conditions",
    "prohibited", "not_applicable",
}
_REQUIRED_RIGHTS_KEYS = ("digital_reproduction", "commercial_use", "translation_use")
_VALID_EVIDENCE_KINDS = {
    "primary_source", "official_publisher", "license_terms", "adaptation_publication",
}
# Rights/scoring claims a third-party test-hosting page can never support.
_LICENSE_SUPPORT_KINDS = {"license", "digital_reproduction", "commercial_use",
                          "translation_use", "official_scoring", "official_cutoffs"}


class InstrumentManifestError(ValueError):
    """Raised by the validators below. Any raise here means 'do not trust
    this manifest entry/document' — callers must fail closed, never fall back
    to treating an invalid entry as usable."""


def load_instrument_manifest(path: Path) -> dict:
    """Loads and validates the whole manifest document at `path`. Raises
    InstrumentManifestError on ANY problem — never silently skips a bad entry
    and never returns a partial document."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise InstrumentManifestError(f"could not read/parse manifest: {e}") from e
    validate_manifest_document(raw)
    return raw


def validate_manifest_document(document: dict) -> None:
    if not isinstance(document, dict):
        raise InstrumentManifestError("manifest must be a JSON object (schema v2)")
    if document.get("schema_version") != 2:
        raise InstrumentManifestError(
            f"unsupported schema_version: {document.get('schema_version')!r}")
    instruments = document.get("instruments")
    if not isinstance(instruments, list) or not instruments:
        raise InstrumentManifestError("manifest must contain a non-empty 'instruments' array")

    seen_ids: set[str] = set()
    for item in instruments:
        validate_instrument_metadata(item)
        iid = item["instrument_id"]
        if iid in seen_ids:
            raise InstrumentManifestError(f"duplicate instrument_id: {iid!r}")
        seen_ids.add(iid)


def _validate_rights(instrument_id: str, item: dict) -> None:
    rights = item.get("rights")
    if not isinstance(rights, dict):
        raise InstrumentManifestError(f"{instrument_id}: missing rights object")
    for key in _REQUIRED_RIGHTS_KEYS:
        entry = rights.get(key)
        if not isinstance(entry, dict):
            raise InstrumentManifestError(f"{instrument_id}: missing rights.{key}")
        status = entry.get("status")
        if status not in _VALID_RIGHTS_STATUSES:
            raise InstrumentManifestError(
                f"{instrument_id}: unknown rights.{key}.status {status!r}")
        if not isinstance(entry.get("evidence"), list):
            raise InstrumentManifestError(
                f"{instrument_id}: rights.{key}.evidence must be a list")
        # prohibited requires evidence just like allowed does — never assert a
        # legal prohibition without documentation either.
        if status in ("allowed", "allowed_with_conditions", "prohibited") \
                and not entry["evidence"]:
            raise InstrumentManifestError(
                f"{instrument_id}: rights.{key}.status={status!r} requires evidence")


def _validate_evidence(instrument_id: str, item: dict) -> None:
    evidence = item.get("evidence")
    if not isinstance(evidence, list):
        raise InstrumentManifestError(f"{instrument_id}: evidence must be a list")
    for record in evidence:
        if not isinstance(record, dict):
            raise InstrumentManifestError(f"{instrument_id}: evidence record must be an object")
        if record.get("kind") not in _VALID_EVIDENCE_KINDS:
            raise InstrumentManifestError(
                f"{instrument_id}: unknown evidence kind {record.get('kind')!r}")
        supports = record.get("supports", [])
        # A psytests.org page (or any third-party test-hosting page) can only
        # ever support identification/discovery — never rights/scoring claims.
        url = (record.get("url") or "")
        if "psytests.org" in url:
            bad = set(supports) & _LICENSE_SUPPORT_KINDS
            if bad:
                raise InstrumentManifestError(
                    f"{instrument_id}: psytests.org evidence cannot support {sorted(bad)!r}")


def _rights_sufficient_for_ready(item: dict) -> bool:
    rights = item.get("rights", {})
    for key in _REQUIRED_RIGHTS_KEYS:
        entry = rights.get(key, {})
        if entry.get("status") not in ("allowed", "allowed_with_conditions"):
            return False
        if not entry.get("evidence"):
            return False
    return True


def validate_instrument_metadata(item: dict) -> None:
    """Fail-closed structural + policy validation for a single manifest
    entry. Raises InstrumentManifestError on any violation. Deliberately
    instrument-aware — the per-instrument checks below each encode a real
    classification decision from docs/clinical_instruments_research.md."""
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

    identity_status = item.get("identity_status")
    if identity_status not in _VALID_IDENTITY_STATUSES:
        raise InstrumentManifestError(
            f"{instrument_id}: unknown identity_status {identity_status!r}")

    domain = item.get("domain")
    if domain not in _VALID_DOMAINS:
        raise InstrumentManifestError(f"{instrument_id}: invalid domain {domain!r}")

    if not isinstance(item.get("public_catalog_visible"), bool):
        raise InstrumentManifestError(
            f"{instrument_id}: public_catalog_visible must be a bool")

    _validate_rights(instrument_id, item)
    _validate_evidence(instrument_id, item)

    # ── instrument-specific hard rules ──
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

    # ── identity gating (generic, applies to every instrument) ──
    if identity_status in ("metadata_incomplete", "identity_conflict"):
        if activation_status == "ready":
            raise InstrumentManifestError(
                f"{instrument_id}: cannot be ready while identity is "
                f"{identity_status}")
        if item.get("public_catalog_visible"):
            raise InstrumentManifestError(
                f"{instrument_id}: cannot be public_catalog_visible while identity is "
                f"{identity_status}")

    # ── generic "ready requires full evidence" rule ──
    if activation_status == "ready":
        if identity_status != "verified":
            raise InstrumentManifestError(
                f"{instrument_id}: ready requires identity_status=verified, "
                f"got {identity_status!r}")
        if not item.get("version"):
            raise InstrumentManifestError(
                f"{instrument_id}: ready requires an exact version")
        if not item.get("evidence"):
            raise InstrumentManifestError(
                f"{instrument_id}: ready requires structured evidence records")
        if not _rights_sufficient_for_ready(item):
            raise InstrumentManifestError(
                f"{instrument_id}: ready requires every rights status to be "
                "allowed/allowed_with_conditions WITH evidence")
        if item.get("risk_item_metadata_status") == "unverified":
            raise InstrumentManifestError(
                f"{instrument_id}: ready requires verified risk-item metadata")


def can_activate_instrument(item: dict) -> bool:
    """True only if the entry is structurally valid AND activation_status is
    literally 'ready' (which validation already requires to carry verified
    identity, exact version, sufficient rights evidence, and verified risk
    metadata). Never raises — a validation failure means 'cannot activate'."""
    try:
        validate_instrument_metadata(item)
    except InstrumentManifestError:
        return False
    return item.get("activation_status") == "ready"


def is_public_catalog_visible(item: dict) -> bool:
    """True only if the entry is structurally valid AND explicitly marked
    public_catalog_visible (which validation already forbids for
    metadata_incomplete/identity_conflict identities). Never raises."""
    try:
        validate_instrument_metadata(item)
    except InstrumentManifestError:
        return False
    return bool(item.get("public_catalog_visible"))
