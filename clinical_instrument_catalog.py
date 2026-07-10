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
from dataclasses import dataclass
from pathlib import Path

_VALID_ADMINISTRATION_MODES = {"self_report", "clinician_rated", "unknown"}
# UI catalog category placement (distinct from `domain`: e.g. DASS is domain
# depression_anxiety_stress but lives under "Стресс"; EPDS is domain depression
# but lives under "Специализированные шкалы"). null is allowed only for
# instruments that are NOT public_catalog_visible.
_VALID_CATALOG_CATEGORY_IDS = {
    "depression_mood_energy", "anxiety", "stress", "specialized",
}
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

    # catalog_category_id: one of the 4 valid UI category ids or null. A
    # public_catalog_visible instrument MUST carry a non-null placement, else
    # it would render into no section (fail closed).
    catalog_category_id = item.get("catalog_category_id")
    if catalog_category_id is not None and catalog_category_id not in _VALID_CATALOG_CATEGORY_IDS:
        raise InstrumentManifestError(
            f"{instrument_id}: invalid catalog_category_id {catalog_category_id!r}")
    if item.get("public_catalog_visible") and catalog_category_id is None:
        raise InstrumentManifestError(
            f"{instrument_id}: public_catalog_visible requires a non-null "
            "catalog_category_id")

    # questionnaire_definition_id: the EXPLICIT id of the concrete private
    # questionnaire definition this instrument would start from. It is NEVER
    # inferred from instrument_id (a family id like `zung_sds` is not the same
    # as a concrete definition id like `zung_sds_ru_adaptation_x_v1`). Nullable
    # for blocked/metadata-only entries; a `ready` entry MUST carry a
    # non-empty one (see the ready-rules block below).
    qdid = item.get("questionnaire_definition_id")
    if qdid is not None and (not isinstance(qdid, str) or not qdid.strip()):
        raise InstrumentManifestError(
            f"{instrument_id}: questionnaire_definition_id must be a non-empty "
            f"string or null, got {qdid!r}")

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
        qdid = item.get("questionnaire_definition_id")
        if not qdid or not str(qdid).strip():
            raise InstrumentManifestError(
                f"{instrument_id}: ready requires an explicit non-empty "
                "questionnaire_definition_id (never inferred from instrument_id)")


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


# ── catalog service layer (professional catalog UX) ──────────────────────────
# Read-only presentation façade over the governance manifest. Renders honest
# availability states from the governance fields — it does NOT activate any
# instrument, does NOT load question/answer/scoring content, does NOT touch the
# DB or the LLM. In THIS PR no instrument is ever 'available' (none is ready);
# the `available` path exists but never fires.

# Exact availability strings — do not add ad-hoc values.
AVAILABILITY_AVAILABLE = "available"
AVAILABILITY_INFORMATION_ONLY = "information_only"
AVAILABILITY_REQUIRES_LICENSE = "requires_license"
AVAILABILITY_VERSION_UNDER_REVIEW = "version_under_review"
AVAILABILITY_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CatalogInstrument:
    instrument_id: str
    title_ru: str
    title_en: str
    abbreviation: str
    category_id: str
    availability: str          # one of the AVAILABILITY_* strings above
    administration_mode: str
    population_note_ru: str | None
    blocker_note_ru: str | None


def _derive_availability(item: dict) -> str:
    """Deterministic mapping from governance fields to a user-facing
    availability state. Order matters and encodes the policy:

    1. ready + fully activatable -> available (never true in this PR).
    2. clinician_rated -> information_only. This PRECEDES the license check:
       a clinician-administered instrument (HDRS) can never be a normal
       self-test regardless of its licensing status, so it must render as
       information-only, not as "a self-test that's merely license-blocked".
    3. digital_reproduction permission_required (BDI family) -> requires_license.
    4. otherwise public/visible -> version_under_review (identity/version
       incomplete but shown honestly).
    5. not public-visible -> unavailable (never rendered)."""
    if item.get("activation_status") == "ready" and can_activate_instrument(item):
        return AVAILABILITY_AVAILABLE
    if item.get("administration_mode") == "clinician_rated":
        return AVAILABILITY_INFORMATION_ONLY
    rights = item.get("rights", {}) or {}
    if rights.get("digital_reproduction", {}).get("status") == "permission_required":
        return AVAILABILITY_REQUIRES_LICENSE
    if is_public_catalog_visible(item):
        return AVAILABILITY_VERSION_UNDER_REVIEW
    return AVAILABILITY_UNAVAILABLE


def _population_note_ru(item: dict) -> str | None:
    population = item.get("population") or []
    if "perinatal" in population or "postpartum" in population:
        return "Для периода беременности и после рождения ребёнка."
    return None


def _to_catalog_instrument(item: dict) -> CatalogInstrument:
    title_ru = item.get("display_name_ru") or item.get("abbreviation") or item.get("instrument_id")
    title_en = item.get("display_name_en") or item.get("abbreviation") or item.get("instrument_id")
    blockers = item.get("blockers") or []
    blocker_note = blockers[0] if blockers else None
    return CatalogInstrument(
        instrument_id=item["instrument_id"],
        title_ru=title_ru,
        title_en=title_en,
        abbreviation=item.get("abbreviation") or "",
        category_id=item.get("catalog_category_id"),
        availability=_derive_availability(item),
        administration_mode=item.get("administration_mode"),
        population_note_ru=_population_note_ru(item),
        blocker_note_ru=blocker_note,
    )


def public_catalog_instruments(document: dict) -> tuple[CatalogInstrument, ...]:
    """Every is_public_catalog_visible-true instrument as a CatalogInstrument.
    Identity-incomplete / identity-conflict / non-visible entries (JAPS, STAS)
    are excluded — they are never rendered."""
    instruments = (document or {}).get("instruments", [])
    return tuple(
        _to_catalog_instrument(item)
        for item in instruments
        if is_public_catalog_visible(item)
    )


def catalog_instruments_by_category(document: dict, category_id: str) -> tuple[CatalogInstrument, ...]:
    return tuple(
        ci for ci in public_catalog_instruments(document)
        if ci.category_id == category_id
    )


def get_catalog_instrument(document: dict, instrument_id: str) -> CatalogInstrument | None:
    for ci in public_catalog_instruments(document):
        if ci.instrument_id == instrument_id:
            return ci
    return None


def catalog_start_definition_id(item: dict, registry) -> str | None:
    """Availability double-gate for FUTURE activation, expressed as BUTTON
    VISIBILITY rather than start execution. Returns the EXPLICIT
    questionnaire_definition_id to route a "Start" button at — but ONLY when
    ALL of:

      (a) can_activate_instrument(item) is True   (manifest fully cleared), AND
      (b) item carries an explicit non-empty questionnaire_definition_id
          (never inferred from instrument_id), AND
      (c) that definition exists in the registry and registry.can_start(it)
          returns True.

    Otherwise returns None -> no start button, information screen only, no
    session, no answers. This function NEVER creates a session or starts a
    questionnaire itself: it only tells the UI which existing q:d/q:s
    definition id (if any) a Start button may point at. Those existing
    handlers remain the ONLY code that creates sessions.

    Never returns non-None in this PR (no manifest entry is 'ready'). `registry`
    is duck-typed (anything exposing can_start) to avoid coupling this
    governance module to questionnaires.py."""
    if not can_activate_instrument(item):
        return None
    definition_id = item.get("questionnaire_definition_id")
    if not definition_id or not str(definition_id).strip():
        return None
    if registry is None:
        return None
    try:
        if registry.can_start(definition_id):
            return definition_id
    except Exception:
        return None
    return None
