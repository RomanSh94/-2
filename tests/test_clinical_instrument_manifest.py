"""Governance PR — clinical instrument identity and rights manifest (schema v2).

Metadata validation only. No scoring, no real question/answer content, no
user-visible result interpretation, no bot.py integration. See
docs/clinical_instruments_research.md and clinical_instruments_manifest.json.
"""
import json
import pathlib

import pytest

import clinical_instrument_catalog as cat

REPO_ROOT = pathlib.Path(__file__).parent.parent
MANIFEST_PATH = REPO_ROOT / "clinical_instruments_manifest.json"
RESEARCH_DOC_PATH = REPO_ROOT / "docs" / "clinical_instruments_research.md"

OWNER_SUPPLIED_URLS = [
    "https://psytests.org/depr/bdi-run.html",
    "https://psytests.org/diag/hdrs-run.html",
    "https://psytests.org/anxiety/zars.html",
    "https://psytests.org/depr/zung.html",
    "https://psytests.org/depr/epds.html",
    "https://psytests.org/depr/dass.html",
    "https://psytests.org/work/japs.html",
    "https://psytests.org/depr/stas.html",
]

RIGHTS_KEYS = ("digital_reproduction", "commercial_use", "translation_use")


def _load_document():
    return cat.load_instrument_manifest(MANIFEST_PATH)


def _instruments():
    return _load_document()["instruments"]


def _by_id(instrument_id):
    return next(i for i in _instruments() if i["instrument_id"] == instrument_id)


def _minimal_valid(**overrides):
    """A structurally valid schema-v2 entry for negative-testing the
    validator. Synthetic id — not one of the real instruments."""
    base = {
        "instrument_id": "synthetic_test_entry",
        "instrument_family": "Synthetic",
        "version": None,
        "identity_status": "family_identified_version_incomplete",
        "domain": "depression",
        "administration_mode": "self_report",
        "population": ["adult"],
        "activation_status": "blocked",
        "public_catalog_visible": True,
        "risk_item_metadata_status": "unverified",
        "evidence": [],
        "rights": {
            "digital_reproduction": {"status": "unknown", "evidence": []},
            "commercial_use": {"status": "unknown", "evidence": []},
            "translation_use": {"status": "unknown", "evidence": []},
        },
        "blockers": [],
    }
    base.update(overrides)
    return base


# ── document-level ───────────────────────────────────────────────────────────
def test_manifest_loads():
    document = _load_document()
    assert document["schema_version"] == 2
    assert len(document["instruments"]) == 8


def test_instrument_ids_are_unique():
    ids = [i["instrument_id"] for i in _instruments()]
    assert len(ids) == len(set(ids))


def test_every_owner_supplied_url_is_represented():
    source_pages = {i["source_page"] for i in _instruments()}
    for url in OWNER_SUPPLIED_URLS:
        assert url in source_pages, f"missing manifest entry for owner-supplied URL {url}"


def test_no_instrument_is_ready_without_evidence():
    for item in _instruments():
        assert item["activation_status"] != "ready", (
            f"{item['instrument_id']} is marked ready in the governance PR -- "
            "no instrument may be ready without separately documented rights evidence")


# ── v6 corrections: rights are tri-state enums, never booleans ───────────────
def test_rights_status_is_not_boolean():
    for item in _instruments():
        for key in RIGHTS_KEYS:
            entry = item["rights"][key]
            assert isinstance(entry, dict)
            assert isinstance(entry["status"], str)
            assert not isinstance(entry["status"], bool)
            assert entry["status"] in (
                "unknown", "permission_required", "allowed",
                "allowed_with_conditions", "prohibited", "not_applicable")


def test_unknown_rights_are_not_reported_as_prohibited():
    """'not investigated' must be distinguishable from 'explicitly
    prohibited'. Nothing in this governance pass produced evidence of a
    verified legal prohibition, so no entry may claim one."""
    for item in _instruments():
        for key in RIGHTS_KEYS:
            assert item["rights"][key]["status"] != "prohibited", (
                f"{item['instrument_id']}.rights.{key} claims 'prohibited' -- a legal "
                "prohibition must never be asserted without evidence, and this pass has none")


def test_prohibited_requires_evidence_in_validator():
    bad = _minimal_valid()
    bad["rights"]["commercial_use"] = {"status": "prohibited", "evidence": []}
    with pytest.raises(cat.InstrumentManifestError):
        cat.validate_instrument_metadata(bad)


def test_ready_requires_structured_rights_evidence():
    bad = _minimal_valid(
        activation_status="ready", identity_status="verified", version="v1",
        risk_item_metadata_status="verified",
        evidence=[{"kind": "primary_source", "title": "x", "url": None,
                   "accessed_at": "2026-07-10", "supports": ["identity"]}])
    # rights all 'unknown' with no evidence -> must be rejected
    with pytest.raises(cat.InstrumentManifestError):
        cat.validate_instrument_metadata(bad)


def test_third_party_source_cannot_support_license():
    """Structural rule: a psytests.org evidence record whose `supports` list
    claims license/rights/scoring backing must be rejected outright."""
    bad = _minimal_valid(evidence=[{
        "kind": "license_terms", "title": "x",
        "url": "https://psytests.org/depr/whatever.html",
        "accessed_at": "2026-07-10", "supports": ["license"]}])
    with pytest.raises(cat.InstrumentManifestError):
        cat.validate_instrument_metadata(bad)
    # And the real manifest contains no such record anywhere.
    for item in _instruments():
        for record in item["evidence"]:
            if "psytests.org" in (record.get("url") or ""):
                assert not (set(record.get("supports", [])) & {
                    "license", "digital_reproduction", "commercial_use",
                    "translation_use", "official_scoring", "official_cutoffs"})


def test_exact_version_required_for_ready():
    bad = _minimal_valid(
        activation_status="ready", identity_status="verified", version=None,
        risk_item_metadata_status="verified",
        evidence=[{"kind": "primary_source", "title": "x", "url": None,
                   "accessed_at": "2026-07-10", "supports": ["identity"]}])
    for key in RIGHTS_KEYS:
        bad["rights"][key] = {"status": "allowed", "evidence": [
            {"kind": "license_terms", "title": "x", "url": None,
             "accessed_at": "2026-07-10", "supports": [key]}]}
    with pytest.raises(cat.InstrumentManifestError):
        cat.validate_instrument_metadata(bad)


# ── v6 corrections: family identified != exact version verified ─────────────
def test_bdi_family_identified_but_version_incomplete():
    item = _by_id("bdi_ii")
    assert item["identity_status"] == "family_identified_version_incomplete"
    assert item["version"] is None
    assert item["activation_status"] == "blocked"


def test_hdrs_family_identified_but_version_incomplete():
    item = _by_id("hdrs")
    assert item["identity_status"] == "family_identified_version_incomplete"
    assert item["version"] is None


def test_dass_family_identified_but_version_incomplete():
    item = _by_id("dass")
    assert item["identity_status"] == "family_identified_version_incomplete"
    assert item["version"] is None
    assert "exact_version_unconfirmed_dass21_vs_dass42" in item["blockers"]


# ── v6 corrections: no executable risk metadata before exact version ─────────
def test_risk_item_runtime_metadata_unverified_without_definition():
    for item in _instruments():
        assert item["risk_item_metadata_status"] == "unverified", (
            f"{item['instrument_id']}: risk_item_metadata_status must stay "
            "'unverified' until an exact approved definition exists")
        # No executable risk-item id fields anywhere in the entry.
        assert "risk_item_ids" not in item
        assert "risk_items" not in item


def test_ready_requires_verified_risk_metadata():
    bad = _minimal_valid(
        activation_status="ready", identity_status="verified", version="v1",
        risk_item_metadata_status="unverified",
        evidence=[{"kind": "primary_source", "title": "x", "url": None,
                   "accessed_at": "2026-07-10", "supports": ["identity"]}])
    for key in RIGHTS_KEYS:
        bad["rights"][key] = {"status": "allowed", "evidence": [
            {"kind": "license_terms", "title": "x", "url": None,
             "accessed_at": "2026-07-10", "supports": [key]}]}
    with pytest.raises(cat.InstrumentManifestError):
        cat.validate_instrument_metadata(bad)


# ── per-instrument classification (carried over from v1, updated) ────────────
def test_bdi_is_license_gated():
    item = _by_id("bdi_ii")
    assert item["rights"]["digital_reproduction"]["status"] == "permission_required"
    assert item["rights"]["commercial_use"]["status"] == "permission_required"
    assert item["activation_status"] == "blocked"


def test_hdrs_is_clinician_rated():
    item = _by_id("hdrs")
    assert item["administration_mode"] == "clinician_rated"
    with pytest.raises(cat.InstrumentManifestError):
        cat.validate_instrument_metadata({**item, "administration_mode": "self_report"})


def test_zung_sas_is_anxiety_not_depression():
    item = _by_id("zung_sas")
    assert item["domain"] == "anxiety"
    sds = _by_id("zung_sds")
    assert sds["domain"] == "depression"


def test_zung_sds_is_depression_domain():
    assert _by_id("zung_sds")["domain"] == "depression"


def test_epds_has_perinatal_population_gate():
    item = _by_id("epds")
    assert "perinatal" in item["population"] or "postpartum" in item["population"]
    bad = {**item, "activation_status": "ready", "population": []}
    with pytest.raises(cat.InstrumentManifestError):
        cat.validate_instrument_metadata(bad)


def test_dass_requires_explicit_version():
    item = _by_id("dass")
    bad = {**item, "activation_status": "ready", "version": None}
    with pytest.raises(cat.InstrumentManifestError):
        cat.validate_instrument_metadata(bad)


# ── JAPS / STAS: identity handling after direct page inspection ──────────────
def test_japs_hidden_while_identity_incomplete():
    item = _by_id("japs")
    # Page inspection identified the FAMILY (Job Apathy Scale, occupational --
    # not a depression instrument), but exact version/item count remain
    # unverified and it stays hidden from any public catalog.
    assert item["identity_status"] == "family_identified_version_incomplete"
    assert item["domain"] == "occupational"
    assert item["public_catalog_visible"] is False
    assert cat.is_public_catalog_visible(item) is False
    assert cat.can_activate_instrument(item) is False


def test_stas_hidden_while_identity_conflicted():
    item = _by_id("stas")
    assert item["identity_status"] == "identity_conflict"
    assert item["public_catalog_visible"] is False
    assert cat.is_public_catalog_visible(item) is False
    assert cat.can_activate_instrument(item) is False
    # Validator structurally forbids making a conflicted identity visible.
    with pytest.raises(cat.InstrumentManifestError):
        cat.validate_instrument_metadata({**item, "public_catalog_visible": True})


def test_identity_incomplete_cannot_be_ready():
    for instrument_id in ("japs", "stas"):
        item = _by_id(instrument_id)
        bad = {**item, "activation_status": "ready"}
        assert cat.can_activate_instrument(bad) is False


# ── no real instrument content anywhere ──────────────────────────────────────
def test_manifest_contains_no_question_items():
    raw_text = MANIFEST_PATH.read_text(encoding="utf-8")
    for forbidden in ("question_text", "answer_options", "item_text", "reverse_items"):
        assert forbidden not in raw_text


def test_manifest_contains_no_scoring_key():
    raw_text = MANIFEST_PATH.read_text(encoding="utf-8")
    for forbidden in ("cutoff_table", "scoring_key", "sten", "percentile"):
        assert forbidden not in raw_text


def test_research_doc_forbidden_content_scan():
    text = RESEARCH_DOC_PATH.read_text(encoding="utf-8")
    for forbidden in ("Item 1:", "Question 1:", "Answer options:"):
        assert forbidden not in text


# ── loader fail-closed behavior ──────────────────────────────────────────────
def _write_doc(tmp_path, instruments):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"schema_version": 2, "instruments": instruments}),
                 encoding="utf-8")
    return p


def test_loader_rejects_duplicate_instrument_id(tmp_path):
    a = _minimal_valid()
    b = _minimal_valid()
    with pytest.raises(cat.InstrumentManifestError):
        cat.load_instrument_manifest(_write_doc(tmp_path, [a, b]))


def test_loader_rejects_missing_instrument_id(tmp_path):
    bad = _minimal_valid()
    del bad["instrument_id"]
    with pytest.raises(cat.InstrumentManifestError):
        cat.load_instrument_manifest(_write_doc(tmp_path, [bad]))


def test_loader_rejects_missing_administration_mode(tmp_path):
    bad = _minimal_valid()
    del bad["administration_mode"]
    with pytest.raises(cat.InstrumentManifestError):
        cat.load_instrument_manifest(_write_doc(tmp_path, [bad]))


def test_loader_rejects_unknown_activation_status(tmp_path):
    with pytest.raises(cat.InstrumentManifestError):
        cat.load_instrument_manifest(
            _write_doc(tmp_path, [_minimal_valid(activation_status="totally_made_up")]))


def test_loader_rejects_unknown_identity_status(tmp_path):
    with pytest.raises(cat.InstrumentManifestError):
        cat.load_instrument_manifest(
            _write_doc(tmp_path, [_minimal_valid(identity_status="probably_fine")]))


def test_loader_rejects_missing_rights_object(tmp_path):
    bad = _minimal_valid()
    del bad["rights"]
    with pytest.raises(cat.InstrumentManifestError):
        cat.load_instrument_manifest(_write_doc(tmp_path, [bad]))


def test_loader_rejects_unknown_rights_status(tmp_path):
    bad = _minimal_valid()
    bad["rights"]["commercial_use"] = {"status": "yes_sure", "evidence": []}
    with pytest.raises(cat.InstrumentManifestError):
        cat.load_instrument_manifest(_write_doc(tmp_path, [bad]))


def test_loader_rejects_old_v1_schema(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps([_minimal_valid()]), encoding="utf-8")  # v1 was a bare array
    with pytest.raises(cat.InstrumentManifestError):
        cat.load_instrument_manifest(p)


def test_loader_rejects_malformed_json(tmp_path):
    p = tmp_path / "m.json"
    p.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(cat.InstrumentManifestError):
        cat.load_instrument_manifest(p)
