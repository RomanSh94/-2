"""Pure clinical definition <-> manifest linkage validator + registry
composition (Layer 1 + Layer 2).

No Telegram, no DB, no LLM. Exercises clinical_definition_validator against
synthetic definitions (tests/fixtures/clinical_definitions/) and in-memory
synthetic manifest documents, plus the questionnaires.Registry composition
helpers. No real instrument content, no scoring, no cutoffs anywhere.
"""
import copy
import json
import pathlib

import pytest

import clinical_instrument_catalog as cat
import clinical_definition_validator as cdv
import questionnaires

Status = cdv.ClinicalDefinitionStatus

REPO_ROOT = pathlib.Path(__file__).parent.parent
CLINICAL_DIR = pathlib.Path(__file__).parent / "fixtures" / "clinical_definitions"
REGISTRY_DIR = pathlib.Path(__file__).parent / "fixtures" / "registry"
MANIFEST_PATH = REPO_ROOT / "clinical_instruments_manifest.json"


def _load_def(name):
    return json.loads((CLINICAL_DIR / name).read_text(encoding="utf-8"))


def _ready_entry(**over):
    """Fully synthetic, fully-cleared manifest entry (never a real
    instrument). Maps to the synthetic_ready_v1 fixture by default."""
    entry = {
        "instrument_id": "synthetic_scale",
        "display_name_ru": "Синтетическая методика",
        "display_name_en": "Synthetic Instrument",
        "catalog_category_id": "anxiety",
        "abbreviation": "SYN",
        "version": "v1",
        "translation_id": "syn_ru_v1",
        "identity_status": "verified",
        "domain": "anxiety",
        "administration_mode": "self_report",
        "population": ["adult"],
        "activation_status": "ready",
        "questionnaire_definition_id": "synthetic_ready_v1",
        "public_catalog_visible": True,
        "risk_item_metadata_status": "verified",
        "evidence": [{"kind": "primary_source", "title": "x", "url": None,
                      "accessed_at": "2026-07-10", "supports": ["identity"]}],
        "rights": {k: {"status": "allowed", "evidence": [
            {"kind": "license_terms", "title": "x", "url": None,
             "accessed_at": "2026-07-10", "supports": [k]}]}
            for k in ("digital_reproduction", "commercial_use", "translation_use")},
        "blockers": [],
    }
    entry.update(over)
    return entry


def _manifest(entries):
    return {"schema_version": 2, "instruments": list(entries)}


def _validate(definition, manifest):
    return cdv.validate_clinical_definition_link(definition, manifest)


# ── NOT_CLINICAL / mapping contract ──────────────────────────────────────────
def test_nonclinical_synthetic_definition_remains_supported():
    d = json.loads((REGISTRY_DIR / "demo_anxiety_v1.json").read_text(encoding="utf-8"))
    result = _validate(d, _manifest([_ready_entry()]))
    assert result.status is Status.NOT_CLINICAL
    assert result.reason_codes == ()


def test_clinical_definition_requires_explicit_manifest_mapping():
    d = _load_def("synthetic_ready_v1.json")
    # Manifest entry maps to a DIFFERENT definition id -> no mapping for d.
    m = _manifest([_ready_entry(questionnaire_definition_id="some_other_def_v1")])
    result = _validate(d, m)
    assert result.status is Status.INVALID
    assert "no-manifest-mapping" in result.reason_codes


def test_mapping_is_not_inferred_from_instrument_id():
    d = _load_def("synthetic_ready_v1.json")
    # questionnaire_definition_id set to the instrument family id, NOT the
    # concrete definition id -> the definition is not mapped.
    m = _manifest([_ready_entry(questionnaire_definition_id="synthetic_scale")])
    assert _validate(d, m).status is Status.INVALID


def test_ready_manifest_and_matching_definition_is_valid():
    d = _load_def("synthetic_ready_v1.json")
    result = _validate(d, _manifest([_ready_entry()]))
    assert result.status is Status.VALID
    assert result.instrument_id == "synthetic_scale"
    assert result.reason_codes == ()
    assert cdv.clinical_definition_can_start(d, _manifest([_ready_entry()])) is True


def test_blocked_manifest_rejects_valid_definition():
    d = _load_def("synthetic_ready_v1.json")
    m = _manifest([_ready_entry(activation_status="blocked")])
    result = _validate(d, m)
    assert result.status is Status.BLOCKED
    assert "activation-not-ready" in result.reason_codes


def test_missing_definition_mapping_rejects_start():
    d = _load_def("synthetic_ready_v1.json")
    m = _manifest([_ready_entry(questionnaire_definition_id="not_this_one_v1")])
    assert cdv.clinical_definition_can_start(d, m) is False


# ── field-by-field mismatch -> INVALID ───────────────────────────────────────
def test_instrument_id_mismatch_rejected():
    d = _load_def("synthetic_ready_v1.json")
    d = copy.deepcopy(d)
    d["clinical_instrument"]["instrument_id"] = "wrong_scale"
    result = _validate(d, _manifest([_ready_entry()]))
    assert result.status is Status.INVALID
    assert "instrument-id-mismatch" in result.reason_codes


def test_instrument_version_mismatch_rejected():
    d = _load_def("synthetic_version_mismatch.json")
    m = _manifest([_ready_entry(
        questionnaire_definition_id="synthetic_version_mismatch_v1")])
    result = _validate(d, m)
    assert result.status is Status.INVALID
    assert "instrument-version-mismatch" in result.reason_codes


def test_translation_id_mismatch_rejected():
    d = _load_def("synthetic_translation_mismatch.json")
    m = _manifest([_ready_entry(
        questionnaire_definition_id="synthetic_translation_mismatch_v1")])
    result = _validate(d, m)
    assert result.status is Status.INVALID
    assert "translation-id-mismatch" in result.reason_codes


def test_administration_mode_mismatch_rejected():
    d = _load_def("synthetic_ready_v1.json")  # self_report metadata
    m = _manifest([_ready_entry(administration_mode="clinician_rated")])
    result = _validate(d, m)
    assert result.status is Status.INVALID
    assert "administration-mode-mismatch" in result.reason_codes


def test_manifest_schema_version_mismatch_rejected():
    d = copy.deepcopy(_load_def("synthetic_ready_v1.json"))
    d["clinical_instrument"]["manifest_schema_version"] = 3
    result = _validate(d, _manifest([_ready_entry()]))
    assert result.status is Status.INVALID
    assert "manifest-schema-version-mismatch" in result.reason_codes


# ── governance denials -> BLOCKED ────────────────────────────────────────────
def test_clinician_rated_definition_not_user_startable():
    d = _load_def("synthetic_clinician_rated.json")
    m = _manifest([_ready_entry(
        instrument_id="synthetic_clin_scale",
        administration_mode="clinician_rated",
        questionnaire_definition_id="synthetic_clinician_rated_v1")])
    result = _validate(d, m)
    assert result.status is Status.BLOCKED
    assert "administration-clinician-rated" in result.reason_codes
    assert cdv.clinical_definition_can_start(d, m) is False


def test_risk_bearing_definition_remains_rejected():
    d = _load_def("synthetic_risk_bearing.json")
    m = _manifest([_ready_entry(
        questionnaire_definition_id="synthetic_risk_bearing_v1")])
    result = _validate(d, m)
    assert result.status is Status.BLOCKED
    assert "definition-risk-bearing" in result.reason_codes


# ── manifest-document level / fail-closed ────────────────────────────────────
def test_duplicate_ready_definition_mapping_rejected():
    d = _load_def("synthetic_ready_v1.json")
    # Two ready entries mapping to the same definition id -> the manifest
    # document itself is rejected -> validator fails closed to INVALID.
    m = _manifest([_ready_entry(), _ready_entry(instrument_id="synthetic_scale_2")])
    result = _validate(d, m)
    assert result.status is Status.INVALID
    assert "manifest-invalid" in result.reason_codes


def test_invalid_manifest_fails_closed():
    d = _load_def("synthetic_ready_v1.json")
    bad = {"schema_version": 2}  # missing 'instruments'
    result = _validate(d, bad)
    assert result.status is Status.INVALID
    assert "manifest-invalid" in result.reason_codes


def test_missing_manifest_fails_closed():
    d = _load_def("synthetic_ready_v1.json")
    assert _validate(d, None).status is Status.INVALID
    assert _validate(d, {}).status is Status.INVALID


def test_manifest_ready_without_definition_fails_closed():
    # Manifest ready + explicit mapping, but the definition itself is missing /
    # Core-invalid (represented by a bare {id} stub with no clinical metadata).
    stub = {"id": "synthetic_ready_v1"}
    result = _validate(stub, _manifest([_ready_entry()]))
    assert result.status is Status.INVALID
    assert "definition-missing-clinical-metadata" in result.reason_codes


def test_definition_without_ready_manifest_fails_closed():
    d = _load_def("synthetic_ready_v1.json")
    # Definition maps to a blocked entry -> not startable.
    m = _manifest([_ready_entry(activation_status="blocked")])
    assert _validate(d, m).status is Status.BLOCKED
    assert cdv.clinical_definition_can_start(d, m) is False


# ── scope guards ─────────────────────────────────────────────────────────────
def test_no_scoring_fields_required_or_processed():
    d = _load_def("synthetic_ready_v1.json")
    result = _validate(d, _manifest([_ready_entry()]))
    assert result.status is Status.VALID
    # The validation result exposes NO score/cutoff/severity surface.
    for banned in ("score", "cutoff", "severity", "percentile", "diagnosis"):
        assert not hasattr(result, banned)


def test_no_real_instrument_content_in_fixtures():
    banned = ("beck", "hamilton", "zung", "edinburgh", "epds", "dass",
              "cutoff", "percentile", "reverse_items", "score_key", "diagnosis")
    for path in CLINICAL_DIR.glob("*.json"):
        text = path.read_text(encoding="utf-8").lower()
        for token in banned:
            assert token not in text, f"{path.name} contains banned token {token!r}"


def test_no_current_real_manifest_entry_is_ready():
    document = cat.load_instrument_manifest(MANIFEST_PATH)
    for item in document["instruments"]:
        assert item["activation_status"] != "ready"


# ── registry composition (VALID alone never authorizes) ──────────────────────
def _clinical_registry():
    return questionnaires.load_registry(CLINICAL_DIR)


def test_valid_link_alone_does_not_authorize_session_creation():
    reg = _clinical_registry()
    m = _manifest([_ready_entry()])
    # Linkage is VALID...
    assert reg.clinical_can_start("synthetic_ready_v1", m) is True
    # ...but demote the definition (Core can_start False) -> combined False.
    reg.by_id["synthetic_ready_v1"]["status"] = "draft"
    assert reg.can_start("synthetic_ready_v1") is False
    assert reg.combined_can_start("synthetic_ready_v1", m) is False
    # The pure validator still reports VALID -- it never authorized anything.
    assert reg.clinical_can_start("synthetic_ready_v1", m) is True


def test_valid_link_still_requires_existing_registry_can_start():
    reg = _clinical_registry()
    m = _manifest([_ready_entry()])
    assert reg.combined_can_start("synthetic_ready_v1", m) is True
    reg.by_id["synthetic_ready_v1"]["legal_status"] = "restricted"
    assert reg.combined_can_start("synthetic_ready_v1", m) is False


def test_ready_manifest_bypass_blocked_by_invalid_definition():
    reg = _clinical_registry()
    # Manifest is ready and maps to the risk-bearing definition id, but Core
    # excluded that definition (risk-bearing) -> registry.get is None ->
    # combined False. A ready manifest never bypasses a missing definition.
    assert reg.get("synthetic_risk_bearing_v1") is None
    m = _manifest([_ready_entry(
        questionnaire_definition_id="synthetic_risk_bearing_v1")])
    assert reg.combined_can_start("synthetic_risk_bearing_v1", m) is False


def test_missing_manifest_does_not_break_nonclinical_definition():
    reg = questionnaires.load_registry(REGISTRY_DIR)
    # demo_anxiety_v1 is nonclinical; a None/missing manifest must not change
    # its startability.
    assert reg.combined_can_start("demo_anxiety_v1", None) is True
    assert reg.combined_can_start("demo_anxiety_v1", None) == reg.can_start("demo_anxiety_v1")
    assert reg.combined_can_answer("demo_anxiety_v1", None) is True


# ── §4.5 clinical metadata shape / type validation (fail-closed per field) ────
def _ready_pair():
    """A matching (definition, manifest) that validates to VALID, as the
    baseline each field-corruption test perturbs."""
    d = _load_def("synthetic_ready_v1.json")
    m = _manifest([_ready_entry()])
    assert _validate(d, m).status is cdv.ClinicalDefinitionStatus.VALID
    return d, m


def test_metadata_missing_each_field_fails_closed():
    for field in cdv.CLINICAL_METADATA_FIELDS:
        d, m = _ready_pair()
        del d["clinical_instrument"][field]
        assert _validate(d, m).status is not cdv.ClinicalDefinitionStatus.VALID, field


def test_metadata_wrong_type_each_field_fails_closed():
    # A wrong-typed value can never equal its manifest counterpart, so the
    # linkage fails closed rather than silently authorizing.
    for field in cdv.CLINICAL_METADATA_FIELDS:
        d, m = _ready_pair()
        d["clinical_instrument"][field] = ["unexpected", "list"]
        assert _validate(d, m).status is not cdv.ClinicalDefinitionStatus.VALID, field


def test_metadata_not_a_dict_with_mapping_is_invalid():
    # A mapping exists but clinical_instrument is not a metadata object ->
    # mandatory metadata missing -> INVALID (never NOT_CLINICAL passthrough).
    d, m = _ready_pair()
    for bad in ([], "clinical", 5, True):
        d2 = copy.deepcopy(d)
        d2["clinical_instrument"] = bad
        res = _validate(d2, m)
        assert res.status is cdv.ClinicalDefinitionStatus.INVALID
        assert "definition-missing-clinical-metadata" in res.reason_codes


def test_metadata_empty_dict_with_mapping_is_invalid():
    d, m = _ready_pair()
    d["clinical_instrument"] = {}
    res = _validate(d, m)
    assert res.status is cdv.ClinicalDefinitionStatus.INVALID
