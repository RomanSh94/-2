"""Clinical definition ↔ manifest linkage validator (Layer 1 — pure).

A fail-closed bridge between the governance manifest
(`clinical_instruments_manifest.json`, validated by clinical_instrument_catalog)
and the concrete private questionnaire definitions loaded by questionnaires.py.

This module is PURE: no Telegram, no DB, no LLM, no filesystem reads, no global
mutable cache. It never calls `registry.can_start()` and never creates a
session. It only decides whether a manifest-to-definition linkage is
governance-compatible. The registry composition layer (questionnaires.Registry)
combines this VALID/NOT_CLINICAL verdict with the existing Questionnaire Core
`can_start()`/`can_answer()` gate; a VALID verdict here ALONE never authorizes a
session.

Scope guardrails (see CLAUDE.md / task): no scoring, no reverse-scoring, no
cutoffs, no severity labels, no diagnosis, no real instrument text. Only
bibliographic/link metadata is compared here.
"""
from dataclasses import dataclass
from enum import Enum

import clinical_instrument_catalog as _cat


class ClinicalDefinitionStatus(str, Enum):
    NOT_CLINICAL = "not_clinical"
    VALID = "valid"
    BLOCKED = "blocked"
    INVALID = "invalid"


@dataclass(frozen=True)
class ClinicalDefinitionValidation:
    definition_id: str
    status: ClinicalDefinitionStatus
    instrument_id: str | None
    reason_codes: tuple[str, ...]


# Fields of the OPTIONAL top-level `clinical_instrument` metadata object on a
# private definition. Absent for ordinary synthetic self-observation
# definitions; mandatory for any definition referenced by a manifest
# `questionnaire_definition_id`.
CLINICAL_METADATA_KEY = "clinical_instrument"
CLINICAL_METADATA_FIELDS = (
    "instrument_id", "instrument_version", "translation_id",
    "administration_mode", "manifest_schema_version",
    "scoring_contract_id", "scoring_version",
)


def _has_clinical_metadata(definition: dict) -> bool:
    return isinstance(definition.get(CLINICAL_METADATA_KEY), dict)


def _definition_is_risk_bearing(definition: dict) -> bool:
    """Mirror of the existing Questionnaire Core risk rejection (never
    weakened): a definition carrying top-level contains_risk_items, or any item
    / option risk_flag, is risk-bearing."""
    if definition.get("contains_risk_items"):
        return True
    for item in definition.get("items", []) or []:
        if item.get("risk_flag"):
            return True
        for option in item.get("options", []) or []:
            if option.get("risk_flag"):
                return True
    return False


def _mapping_entries(definition_id, manifest_document: dict) -> list[dict]:
    """Manifest entries whose questionnaire_definition_id EXACTLY equals
    definition_id. Never inferred from instrument_id. definition_id None never
    matches (a null questionnaire_definition_id is not a mapping)."""
    if definition_id is None:
        return []
    instruments = (manifest_document or {}).get("instruments", []) or []
    return [e for e in instruments
            if isinstance(e, dict)
            and e.get("questionnaire_definition_id") == definition_id]


def validate_clinical_definition_link(definition: dict,
                                      manifest_document: dict) -> ClinicalDefinitionValidation:
    definition_id = definition.get("id")
    has_meta = _has_clinical_metadata(definition)

    # Rule 1: the manifest document itself must validate. Wrap in try/except.
    manifest_valid = True
    try:
        _cat.validate_manifest_document(manifest_document)
    except Exception:  # noqa: BLE001 -- any manifest problem fails closed
        manifest_valid = False

    if not manifest_valid:
        # A broken/missing/stale manifest must NOT break ordinary nonclinical
        # definitions (they have no metadata and cannot be mapped) -- they stay
        # NOT_CLINICAL so the registry falls back to plain can_start. But a
        # definition that DOES carry clinical metadata fails closed to INVALID.
        if has_meta:
            return ClinicalDefinitionValidation(
                definition_id, ClinicalDefinitionStatus.INVALID, None,
                ("manifest-invalid",))
        return ClinicalDefinitionValidation(
            definition_id, ClinicalDefinitionStatus.NOT_CLINICAL, None, ())

    mapped = _mapping_entries(definition_id, manifest_document)

    # Rule 2: NOT_CLINICAL only when there is neither metadata nor a mapping.
    if not has_meta and not mapped:
        return ClinicalDefinitionValidation(
            definition_id, ClinicalDefinitionStatus.NOT_CLINICAL, None, ())

    # Metadata present but no manifest entry maps to it -> contradictory link.
    if has_meta and not mapped:
        return ClinicalDefinitionValidation(
            definition_id, ClinicalDefinitionStatus.INVALID, None,
            ("no-manifest-mapping",))

    # A manifest entry maps here but the definition carries no clinical
    # metadata -> mandatory metadata missing.
    if mapped and not has_meta:
        return ClinicalDefinitionValidation(
            definition_id, ClinicalDefinitionStatus.INVALID, None,
            ("definition-missing-clinical-metadata",))

    # Ambiguous: more than one manifest entry maps to the same definition id.
    if len(mapped) > 1:
        return ClinicalDefinitionValidation(
            definition_id, ClinicalDefinitionStatus.INVALID, None,
            ("ambiguous-mapping",))

    entry = mapped[0]
    meta = definition[CLINICAL_METADATA_KEY]
    instrument_id = entry.get("instrument_id")

    invalid_reasons: list[str] = []
    blocked_reasons: list[str] = []

    # ── contradiction / mismatch checks -> INVALID ──
    if meta.get("manifest_schema_version") != manifest_document.get("schema_version"):
        invalid_reasons.append("manifest-schema-version-mismatch")
    if meta.get("instrument_id") != instrument_id:
        invalid_reasons.append("instrument-id-mismatch")
    if meta.get("instrument_version") != entry.get("version"):
        invalid_reasons.append("instrument-version-mismatch")
    if meta.get("translation_id") != entry.get("translation_id"):
        invalid_reasons.append("translation-id-mismatch")
    if meta.get("administration_mode") != entry.get("administration_mode"):
        invalid_reasons.append("administration-mode-mismatch")
    # Exact-version scoring-contract identity (PR #53). The definition's pinned
    # scorer + revision must match the manifest entry EXACTLY. null==null is a
    # valid match (a not-yet-scoreable but otherwise consistent linkage); any
    # divergence is a contradiction -> INVALID (never silently VALID).
    if meta.get("scoring_contract_id") != entry.get("scoring_contract_id"):
        invalid_reasons.append("scoring-contract-id-mismatch")
    if meta.get("scoring_version") != entry.get("scoring_version"):
        invalid_reasons.append("scoring-version-mismatch")

    # ── governance checks -> BLOCKED ──
    if entry.get("activation_status") != "ready":
        blocked_reasons.append("activation-not-ready")
    if not _cat.can_activate_instrument(entry):
        blocked_reasons.append("manifest-not-activatable")
    if entry.get("identity_status") != "verified":
        blocked_reasons.append("identity-not-verified")
    if not entry.get("version"):
        blocked_reasons.append("manifest-version-missing")
    if not entry.get("translation_id"):
        blocked_reasons.append("manifest-translation-missing")
    # Ordinary-user start requires self_report. clinician_rated is BLOCKED
    # (never invalid, never ordinary-user-startable).
    if entry.get("administration_mode") == "clinician_rated":
        blocked_reasons.append("administration-clinician-rated")
    # Preserve the existing Core risk rejection exactly: risk-bearing -> BLOCKED.
    if _definition_is_risk_bearing(definition):
        blocked_reasons.append("definition-risk-bearing")

    if invalid_reasons:
        return ClinicalDefinitionValidation(
            definition_id, ClinicalDefinitionStatus.INVALID, instrument_id,
            tuple(invalid_reasons))
    if blocked_reasons:
        return ClinicalDefinitionValidation(
            definition_id, ClinicalDefinitionStatus.BLOCKED, instrument_id,
            tuple(blocked_reasons))
    return ClinicalDefinitionValidation(
        definition_id, ClinicalDefinitionStatus.VALID, instrument_id, ())


def validate_registry_clinical_links(
        definitions: list[dict],
        manifest_document: dict) -> tuple[ClinicalDefinitionValidation, ...]:
    return tuple(
        validate_clinical_definition_link(d, manifest_document)
        for d in definitions)


def clinical_definition_can_start(definition: dict, manifest_document: dict) -> bool:
    """True only iff the linkage is VALID and the (matched) administration mode
    is self_report. NEVER authorizes a session by itself -- the registry
    composition layer still requires the existing can_start() gate."""
    result = validate_clinical_definition_link(definition, manifest_document)
    if result.status != ClinicalDefinitionStatus.VALID:
        return False
    meta = definition.get(CLINICAL_METADATA_KEY) or {}
    return meta.get("administration_mode") == "self_report"
