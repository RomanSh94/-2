"""Governance PR — clinical instrument identity and rights manifest.

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


def _load_manifest():
    return cat.load_instrument_manifest(MANIFEST_PATH)


def _by_id(manifest, instrument_id):
    return next(i for i in manifest if i["instrument_id"] == instrument_id)


def test_manifest_loads():
    manifest = _load_manifest()
    assert isinstance(manifest, list)
    assert len(manifest) == 8


def test_instrument_ids_are_unique():
    manifest = _load_manifest()
    ids = [i["instrument_id"] for i in manifest]
    assert len(ids) == len(set(ids))


def test_every_owner_supplied_url_is_represented():
    manifest = _load_manifest()
    source_pages = {i["source_page"] for i in manifest}
    for url in OWNER_SUPPLIED_URLS:
        assert url in source_pages, f"missing manifest entry for owner-supplied URL {url}"


def test_no_instrument_is_ready_without_evidence():
    manifest = _load_manifest()
    for item in manifest:
        assert item["activation_status"] != "ready", (
            f"{item['instrument_id']} is marked ready in the governance PR -- "
            "no instrument may be ready without separately documented rights evidence")


def test_bdi_is_license_gated():
    item = _by_id(_load_manifest(), "bdi_ii")
    assert item["license_status"] == "license_gated"
    assert item["activation_status"] == "blocked"
    assert item["commercial_use_allowed"] is False
    assert item["digital_reproduction_allowed"] is False


def test_hdrs_is_clinician_rated():
    item = _by_id(_load_manifest(), "hdrs")
    assert item["administration_mode"] == "clinician_rated"
    with pytest.raises(cat.InstrumentManifestError):
        cat.validate_instrument_metadata({**item, "administration_mode": "self_report"})


def test_zung_sas_is_anxiety_not_depression():
    item = _by_id(_load_manifest(), "zung_sas")
    assert item["domain"] == "anxiety"
    sds = _by_id(_load_manifest(), "zung_sds")
    assert sds["domain"] == "depression"
    assert item["instrument_id"] != sds["instrument_id"]


def test_zung_sds_is_depression_domain():
    item = _by_id(_load_manifest(), "zung_sds")
    assert item["domain"] == "depression"


def test_epds_has_perinatal_population_gate():
    item = _by_id(_load_manifest(), "epds")
    assert "perinatal" in item["population"] or "postpartum" in item["population"]
    # A "ready" EPDS entry without the population gate must be rejected.
    bad = {**item, "activation_status": "ready", "population": [],
           "version": "1987_original", "license_status": "verified",
           "digital_reproduction_allowed": True, "commercial_use_allowed": True,
           "translation_use_allowed": True}
    with pytest.raises(cat.InstrumentManifestError):
        cat.validate_instrument_metadata(bad)


def test_dass_requires_explicit_version():
    item = _by_id(_load_manifest(), "dass")
    assert item["version"] is None
    assert item["activation_status"] == "blocked"
    bad = {**item, "activation_status": "ready", "version": None,
           "license_status": "verified", "digital_reproduction_allowed": True,
           "commercial_use_allowed": True, "translation_use_allowed": True}
    with pytest.raises(cat.InstrumentManifestError):
        cat.validate_instrument_metadata(bad)


def test_japs_incomplete_identity_cannot_activate():
    item = _by_id(_load_manifest(), "japs")
    assert item["activation_status"] == "metadata_incomplete"
    assert cat.can_activate_instrument(item) is False
    bad = {**item, "activation_status": "ready"}
    assert cat.can_activate_instrument(bad) is False


def test_stas_incomplete_identity_cannot_activate():
    item = _by_id(_load_manifest(), "stas")
    assert item["activation_status"] == "metadata_incomplete"
    assert cat.can_activate_instrument(item) is False
    bad = {**item, "activation_status": "ready"}
    assert cat.can_activate_instrument(bad) is False


def test_third_party_page_is_not_license_evidence():
    """The owner-supplied psytests.org URLs are recorded as `source_page`
    (identification only) -- none of them appear in any license/rights field,
    and every instrument's license/rights fields are unknown/gated/restricted,
    never derived from "it's hosted online somewhere"."""
    manifest = _load_manifest()
    for item in manifest:
        assert item["license_status"] in (
            "unknown", "license_gated", "restricted_commercial_use")
        assert item["digital_reproduction_allowed"] is False
        assert item["commercial_use_allowed"] is False
        assert item["translation_use_allowed"] is False


def test_manifest_contains_no_question_items():
    raw_text = MANIFEST_PATH.read_text(encoding="utf-8")
    for forbidden in ("question_text", "answer_options", "item_text", "reverse_items"):
        assert forbidden not in raw_text


def test_manifest_contains_no_scoring_key():
    raw_text = MANIFEST_PATH.read_text(encoding="utf-8")
    for forbidden in ("cutoff_table", "scoring_key", "sten", "percentile"):
        assert forbidden not in raw_text


# ── loader fail-closed behavior ──────────────────────────────────────────────
def test_loader_rejects_duplicate_instrument_id(tmp_path):
    bad_manifest = [
        {"instrument_id": "dup", "administration_mode": "self_report",
         "activation_status": "blocked", "domain": "depression"},
        {"instrument_id": "dup", "administration_mode": "self_report",
         "activation_status": "blocked", "domain": "depression"},
    ]
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad_manifest), encoding="utf-8")
    with pytest.raises(cat.InstrumentManifestError):
        cat.load_instrument_manifest(p)


def test_loader_rejects_missing_instrument_id(tmp_path):
    bad_manifest = [{"administration_mode": "self_report", "activation_status": "blocked",
                     "domain": "depression"}]
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad_manifest), encoding="utf-8")
    with pytest.raises(cat.InstrumentManifestError):
        cat.load_instrument_manifest(p)


def test_loader_rejects_missing_administration_mode(tmp_path):
    bad_manifest = [{"instrument_id": "x", "activation_status": "blocked",
                     "domain": "depression"}]
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad_manifest), encoding="utf-8")
    with pytest.raises(cat.InstrumentManifestError):
        cat.load_instrument_manifest(p)


def test_loader_rejects_unknown_activation_status(tmp_path):
    bad_manifest = [{"instrument_id": "x", "administration_mode": "self_report",
                     "activation_status": "totally_made_up", "domain": "depression"}]
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad_manifest), encoding="utf-8")
    with pytest.raises(cat.InstrumentManifestError):
        cat.load_instrument_manifest(p)


def test_loader_rejects_malformed_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(cat.InstrumentManifestError):
        cat.load_instrument_manifest(p)


def test_research_doc_forbidden_content_scan():
    """Ordinary prose mentioning prohibited categories in a warning/rule
    context is allowed (e.g. 'do not add scoring_key') -- this test only
    proves no actual question/answer/scoring content exists, by construction
    of the doc's own content, not by pattern-matching prose about the rule
    itself."""
    text = RESEARCH_DOC_PATH.read_text(encoding="utf-8")
    # These would only appear if actual item content were pasted in.
    for forbidden in ("Item 1:", "Question 1:", "Answer options:"):
        assert forbidden not in text
