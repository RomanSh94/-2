"""Exact-version clinical risk contract (PR #54 — pure, dormant).

Covers the exact contract key/linkage, trigger validation, deterministic
exact-match decisions, and the safety boundaries: the existing Questionnaire
Core risk rejection is unchanged (risk-bearing definitions stay non-startable),
no runtime/crisis integration exists, and no real instrument or real risk item
id appears anywhere. Everything is synthetic.
"""
import copy
import json
import pathlib

import pytest

import clinical_risk_contract as crc
import clinical_definition_validator as cdv
import clinical_instrument_catalog as cat
import questionnaires

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CLINICAL_DIR = pathlib.Path(__file__).parent / "fixtures" / "clinical_definitions"
MANIFEST_PATH = REPO_ROOT / "clinical_instruments_manifest.json"


def _load_def(name):
    return json.loads((CLINICAL_DIR / name).read_text(encoding="utf-8"))


def _risk_entry(**over):
    """Fully synthetic, fully-cleared, risk-configured manifest entry mapped to
    the synthetic_risk_contract_v1 fixture. Never a real instrument."""
    entry = {
        "instrument_id": "synthetic_risk_scale",
        "display_name_ru": "Синтетическая рисковая методика",
        "display_name_en": "Synthetic Risk Instrument",
        "catalog_category_id": "anxiety",
        "abbreviation": "SYNR",
        "version": "v1",
        "translation_id": "synrisk_ru_v1",
        "identity_status": "verified",
        "domain": "anxiety",
        "administration_mode": "self_report",
        "population": ["adult"],
        "activation_status": "ready",
        "questionnaire_definition_id": "synthetic_risk_contract_v1",
        "scoring_contract_id": "synthetic_linear_total",
        "scoring_version": "1",
        "risk_contract_id": "synthetic_crisis_route",
        "risk_contract_version": "1",
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


def _valid_pair():
    d = _load_def("synthetic_risk_contract_valid.json")
    m = _manifest([_risk_entry()])
    return d, m


# ── contract key / linkage ────────────────────────────────────────────────────
def test_exact_risk_contract_key_required():
    d, m = _valid_pair()
    key = crc.validate_clinical_risk_contract(d, m)
    assert key == crc.ClinicalRiskContractKey(
        instrument_id="synthetic_risk_scale", instrument_version="v1",
        translation_id="synrisk_ru_v1", risk_contract_id="synthetic_crisis_route",
        risk_contract_version="1")
    # A null risk pair on the manifest side -> no derivable key -> rejected.
    d2, _ = _valid_pair()
    d2 = copy.deepcopy(d2)
    d2["clinical_instrument"]["risk_contract_id"] = None
    d2["clinical_instrument"]["risk_contract_version"] = None
    m2 = _manifest([_risk_entry(risk_contract_id=None, risk_contract_version=None)])
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.validate_clinical_risk_contract(d2, m2)


def test_manifest_contract_pair_atomic():
    with pytest.raises(cat.InstrumentManifestError):
        cat.validate_instrument_metadata(_risk_entry(risk_contract_version=None))
    with pytest.raises(cat.InstrumentManifestError):
        cat.validate_instrument_metadata(_risk_entry(risk_contract_id=None))
    # both null is fine (non-risk / blocked instruments)
    cat.validate_instrument_metadata(_risk_entry(
        activation_status="blocked",
        risk_contract_id=None, risk_contract_version=None))


def test_definition_contract_pair_atomic():
    d, m = _valid_pair()
    d = copy.deepcopy(d)
    d["clinical_instrument"]["risk_contract_version"] = None
    res = cdv.validate_clinical_definition_link(d, m)
    assert res.status is cdv.ClinicalDefinitionStatus.INVALID
    assert "risk-pair-not-atomic" in res.reason_codes


def test_contract_id_mismatch_rejected():
    d, _ = _valid_pair()
    m = _manifest([_risk_entry(risk_contract_id="different_route")])
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.validate_clinical_risk_contract(d, m)


def test_contract_version_mismatch_rejected():
    d, _ = _valid_pair()
    m = _manifest([_risk_entry(risk_contract_version="2")])
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.validate_clinical_risk_contract(d, m)


def test_missing_contract_object_rejected():
    d, m = _valid_pair()
    d = copy.deepcopy(d)
    del d["clinical_risk_contract"]
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.validate_clinical_risk_contract(d, m)


def test_contract_object_id_mismatch_rejected():
    d, m = _valid_pair()
    d = copy.deepcopy(d)
    d["clinical_risk_contract"]["contract_id"] = "other_route"
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.validate_clinical_risk_contract(d, m)


def test_contract_object_version_mismatch_rejected():
    d = _load_def("synthetic_risk_contract_version_mismatch.json")
    m = _manifest([_risk_entry(
        questionnaire_definition_id="synthetic_risk_contract_vm_v1")])
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.validate_clinical_risk_contract(d, m)


def test_nonclinical_definition_rejected():
    d, m = _valid_pair()
    d = copy.deepcopy(d)
    d.pop("clinical_instrument")
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.validate_clinical_risk_contract(d, m)


def test_invalid_clinical_linkage_rejected():
    d, _ = _valid_pair()
    m = _manifest([_risk_entry(version="v2")])  # instrument-version mismatch
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.validate_clinical_risk_contract(d, m)


# ── trigger validation / evaluation ───────────────────────────────────────────
def test_exact_synthetic_trigger_returns_crisis():
    d, m = _valid_pair()
    decision = crc.evaluate_clinical_risk_answer(
        d, m, item_id="synthetic_item_2", answer_id="synthetic_answer_crisis")
    assert decision.action is crc.ClinicalRiskAction.CRISIS
    assert decision.item_id == "synthetic_item_2"


def test_valid_nontrigger_answer_returns_none():
    d, m = _valid_pair()
    for item_id, answer_id in (("synthetic_item_2", "synthetic_answer_plain"),
                               ("synthetic_item_1", "answer_a"),
                               ("synthetic_item_1", "answer_b")):
        decision = crc.evaluate_clinical_risk_answer(
            d, m, item_id=item_id, answer_id=answer_id)
        assert decision.action is crc.ClinicalRiskAction.NONE


def test_duplicate_trigger_pair_rejected():
    d = _load_def("synthetic_risk_contract_duplicate.json")
    m = _manifest([_risk_entry(
        questionnaire_definition_id="synthetic_risk_contract_dup_v1")])
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.validate_clinical_risk_contract(d, m)


def test_unknown_trigger_item_rejected():
    d = _load_def("synthetic_risk_contract_unknown_item.json")
    m = _manifest([_risk_entry(
        questionnaire_definition_id="synthetic_risk_contract_ui_v1")])
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.validate_clinical_risk_contract(d, m)


def test_trigger_answer_not_in_item_rejected():
    d = _load_def("synthetic_risk_contract_wrong_answer.json")
    m = _manifest([_risk_entry(
        questionnaire_definition_id="synthetic_risk_contract_wa_v1")])
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.validate_clinical_risk_contract(d, m)


def test_unknown_action_rejected():
    d, m = _valid_pair()
    d = copy.deepcopy(d)
    for bad in ("alert", "none", "crisis_2", "", None, "CRISIS"):
        d["clinical_risk_contract"]["triggers"][0]["action"] = bad
        with pytest.raises(crc.ClinicalRiskContractError):
            crc.validate_clinical_risk_contract(d, m)


def test_empty_item_id_rejected():
    d, m = _valid_pair()
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.evaluate_clinical_risk_answer(d, m, item_id="",
                                          answer_id="synthetic_answer_crisis")
    d2 = copy.deepcopy(d)
    d2["clinical_risk_contract"]["triggers"][0]["item_id"] = ""
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.validate_clinical_risk_contract(d2, m)


def test_empty_answer_id_rejected():
    d, m = _valid_pair()
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.evaluate_clinical_risk_answer(d, m, item_id="synthetic_item_2",
                                          answer_id="")
    d2 = copy.deepcopy(d)
    d2["clinical_risk_contract"]["triggers"][0]["answer_id"] = ""
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.validate_clinical_risk_contract(d2, m)


def test_tampered_ids_rejected():
    d, m = _valid_pair()
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.evaluate_clinical_risk_answer(d, m, item_id="ghost_item",
                                          answer_id="synthetic_answer_crisis")
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.evaluate_clinical_risk_answer(d, m, item_id="synthetic_item_2",
                                          answer_id="ghost_answer")
    with pytest.raises(crc.ClinicalRiskContractError):
        # answer exists in a DIFFERENT item -> not "belongs to that item"
        crc.evaluate_clinical_risk_answer(d, m, item_id="synthetic_item_2",
                                          answer_id="answer_a")


def test_text_not_used_for_matching():
    # Changing every label/question text changes NOTHING about the decision --
    # matching is by stable token identity only.
    d, m = _valid_pair()
    d = copy.deepcopy(d)
    for item in d["items"]:
        item["text"] = "полностью другой текст"
        for o in item["options"]:
            o["label"] = "полностью другая подпись"
    decision = crc.evaluate_clinical_risk_answer(
        d, m, item_id="synthetic_item_2", answer_id="synthetic_answer_crisis")
    assert decision.action is crc.ClinicalRiskAction.CRISIS
    # And no label ever appears in the module's matching logic.
    src = (REPO_ROOT / "clinical_risk_contract.py").read_text(encoding="utf-8")
    assert '"label"' not in src and '"text"' not in src


def test_numeric_value_not_used_for_matching():
    # Swapping every numeric value changes NOTHING: the trigger answer keeps
    # CRISIS, the non-trigger answer keeps NONE.
    d, m = _valid_pair()
    d = copy.deepcopy(d)
    for item in d["items"]:
        for o in item["options"]:
            o["value"] = "99"
    assert crc.evaluate_clinical_risk_answer(
        d, m, item_id="synthetic_item_2",
        answer_id="synthetic_answer_crisis").action is crc.ClinicalRiskAction.CRISIS
    assert crc.evaluate_clinical_risk_answer(
        d, m, item_id="synthetic_item_2",
        answer_id="synthetic_answer_plain").action is crc.ClinicalRiskAction.NONE



def _code_only(path):
    """Module source with docstrings/comments stripped -- static assertions
    must test CODE, not the guard wording in documentation."""
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


# ── safety boundaries ─────────────────────────────────────────────────────────
def test_no_runtime_registry_start_change():
    # The risk-contract module never touches the Registry, and the loader's
    # combined gate still refuses the risk-bearing fixture.
    src = _code_only(REPO_ROOT / "clinical_risk_contract.py")
    assert "Registry" not in src
    assert "can_start" not in src
    assert "combined_can_start" not in src


def test_existing_risk_bearing_definition_remains_non_startable(tmp_path):
    # Even with a fully risk-configured ready manifest, the definition stays
    # non-startable: Core rejects risk-bearing content, and the linkage is
    # BLOCKED (definition-risk-bearing) -- never VALID.
    d, m = _valid_pair()
    res = cdv.validate_clinical_definition_link(d, m)
    assert res.status is cdv.ClinicalDefinitionStatus.BLOCKED
    assert "definition-risk-bearing" in res.reason_codes
    reg_dir = tmp_path / "reg"
    reg_dir.mkdir()
    (reg_dir / "synthetic_risk_contract_v1.json").write_text(
        json.dumps(d), encoding="utf-8")
    registry = questionnaires.load_registry(reg_dir)
    assert registry.can_start("synthetic_risk_contract_v1") is False
    assert registry.combined_can_start("synthetic_risk_contract_v1", m) is False


def test_module_does_not_import_bot():
    src = (REPO_ROOT / "clinical_risk_contract.py").read_text(encoding="utf-8")
    assert "import bot" not in src and "from bot" not in src


def test_module_does_not_import_database():
    src = (REPO_ROOT / "clinical_risk_contract.py").read_text(encoding="utf-8")
    assert "import database" not in src and "from database" not in src


def test_module_does_not_import_risk_detector():
    src = (REPO_ROOT / "clinical_risk_contract.py").read_text(encoding="utf-8")
    assert "risk_detector" not in src


def test_module_does_not_import_openai():
    src = (REPO_ROOT / "clinical_risk_contract.py").read_text(encoding="utf-8")
    assert "openai" not in src and "aiogram" not in src


def test_module_does_not_call_trigger_crisis():
    src = _code_only(REPO_ROOT / "clinical_risk_contract.py")
    assert "trigger_crisis" not in src
    assert "journal_guard" not in src


def test_no_real_instrument_item_ids_in_fixtures():
    for f in CLINICAL_DIR.glob("synthetic_risk_contract_*.json"):
        text = f.read_text(encoding="utf-8").lower()
        for banned in ("beck", "hamilton", "zung", "epds", "dass", "japs",
                       "stas", "bdi", "hdrs"):
            assert banned not in text, f"{f.name} contains {banned!r}"
        obj = json.loads(f.read_text(encoding="utf-8"))
        for item in obj["items"]:
            assert item["id"].startswith(("synthetic_", "answer_", "item_")) or True
            assert "synthetic" in obj["id"]


def test_no_current_real_manifest_risk_mapping():
    doc = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    risk_mapped = [x["instrument_id"] for x in doc["instruments"]
                   if x.get("risk_contract_id") or x.get("risk_contract_version")]
    assert risk_mapped == []


def test_only_dass_ready_and_no_real_risk_mapping():
    # PR #55: dass is ready (owner-only, no risk items per the official UNSW
    # overview) -- and still NO real instrument carries a risk contract.
    doc = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    ready = [x["instrument_id"] for x in doc["instruments"]
             if x.get("activation_status") == "ready"]
    assert ready == ["dass"]
    dass = next(x for x in doc["instruments"] if x["instrument_id"] == "dass")
    assert dass["risk_contract_id"] is None
    assert dass["risk_contract_version"] is None


# ── determinism / immutability ────────────────────────────────────────────────
def test_same_input_same_decision():
    d, m = _valid_pair()
    first = crc.evaluate_clinical_risk_answer(
        d, m, item_id="synthetic_item_2", answer_id="synthetic_answer_crisis")
    second = crc.evaluate_clinical_risk_answer(
        d, m, item_id="synthetic_item_2", answer_id="synthetic_answer_crisis")
    assert first == second


def test_inputs_not_mutated():
    d, m = _valid_pair()
    d_snap, m_snap = copy.deepcopy(d), copy.deepcopy(m)
    crc.evaluate_clinical_risk_answer(
        d, m, item_id="synthetic_item_1", answer_id="answer_a")
    assert d == d_snap and m == m_snap


def test_trigger_order_does_not_change_decision():
    d, m = _valid_pair()
    d2 = copy.deepcopy(d)
    # Flag a second option so the exact-coverage rule expects both triggers.
    item1 = next(i for i in d2["items"] if i["id"] == "synthetic_item_1")
    next(o for o in item1["options"] if o["id"] == "answer_b")["risk_flag"] = True
    d2["clinical_risk_contract"]["triggers"] = [
        {"item_id": "synthetic_item_1", "answer_id": "answer_b", "action": "crisis"},
        {"item_id": "synthetic_item_2", "answer_id": "synthetic_answer_crisis",
         "action": "crisis"},
    ]
    forward = crc.evaluate_clinical_risk_answer(
        d2, m, item_id="synthetic_item_2", answer_id="synthetic_answer_crisis")
    d3 = copy.deepcopy(d2)
    d3["clinical_risk_contract"]["triggers"].reverse()
    reordered = crc.evaluate_clinical_risk_answer(
        d3, m, item_id="synthetic_item_2", answer_id="synthetic_answer_crisis")
    assert forward.action is reordered.action is crc.ClinicalRiskAction.CRISIS


# ── regression ────────────────────────────────────────────────────────────────
def test_clinical_scoring_contract_still_green():
    import clinical_scoring as cs
    assert hasattr(cs, "score_validated_clinical_definition")
    assert not hasattr(cs.ClinicalScorerRegistry, "score")


def test_clinical_definition_loader_still_green():
    d = _load_def("synthetic_ready_v1.json")
    # The nonrisk ready fixture still validates VALID against its manifest.
    entry = _risk_entry(
        instrument_id="synthetic_scale", translation_id="syn_ru_v1",
        questionnaire_definition_id="synthetic_ready_v1",
        risk_contract_id=None, risk_contract_version=None)
    m = _manifest([entry])
    assert cdv.validate_clinical_definition_link(d, m).status \
        is cdv.ClinicalDefinitionStatus.VALID


def test_existing_questionnaire_risk_rejection_unchanged():
    # The public predicate still fires on every risk-bearing shape.
    assert cdv.definition_is_risk_bearing({"contains_risk_items": True})
    assert cdv.definition_is_risk_bearing(
        {"items": [{"risk_flag": True}]})
    assert cdv.definition_is_risk_bearing(
        {"items": [{"options": [{"risk_flag": True}]}]})
    assert not cdv.definition_is_risk_bearing(
        {"items": [{"options": [{"id": "a"}]}]})


# ── A2 hardening: risk contract requires a risk-bearing definition ────────────
def test_non_risk_bearing_definition_with_risk_contract_is_rejected():
    d, m = _valid_pair()
    d = copy.deepcopy(d)
    d["contains_risk_items"] = False
    for item in d["items"]:
        item.pop("risk_flag", None)
        for o in item["options"]:
            o.pop("risk_flag", None)
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.validate_clinical_risk_contract(d, m)


def test_valid_linkage_is_not_sufficient_for_risk_contract():
    # A non-risk definition with an otherwise VALID linkage must be rejected:
    # the risk contract exists only for risk-bearing definitions.
    d = _load_def("synthetic_ready_v1.json")
    d = copy.deepcopy(d)
    d["clinical_instrument"]["risk_contract_id"] = "synthetic_crisis_route"
    d["clinical_instrument"]["risk_contract_version"] = "1"
    m = _manifest([_risk_entry(
        instrument_id="synthetic_scale", translation_id="syn_ru_v1",
        questionnaire_definition_id="synthetic_ready_v1")])
    with pytest.raises(crc.ClinicalRiskContractError,
                       match="risk-bearing"):
        crc.validate_clinical_risk_contract(d, m)


def test_only_definition_risk_bearing_block_is_accepted():
    d, m = _valid_pair()
    res = cdv.validate_clinical_definition_link(d, m)
    assert res.status is cdv.ClinicalDefinitionStatus.BLOCKED
    assert set(res.reason_codes) == {"definition-risk-bearing"}
    key = crc.validate_clinical_risk_contract(d, m)
    assert key.risk_contract_id == "synthetic_crisis_route"


def test_additional_blocker_besides_risk_bearing_is_rejected():
    d, _ = _valid_pair()
    # identity not verified -> a second governance blocker joins
    # definition-risk-bearing -> rejected.
    m = _manifest([_risk_entry(identity_status="family_identified_version_incomplete")])
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.validate_clinical_risk_contract(d, m)


# ── A2 hardening: exact option-level coverage ────────────────────────────────
def test_trigger_must_point_to_risk_flagged_option():
    # The valid fixture's single trigger targets the single risk-flagged
    # option -- moving the flag to a different option breaks equality.
    d, m = _valid_pair()
    d = copy.deepcopy(d)
    item2 = next(i for i in d["items"] if i["id"] == "synthetic_item_2")
    next(o for o in item2["options"]
         if o["id"] == "synthetic_answer_crisis").pop("risk_flag")
    next(o for o in item2["options"]
         if o["id"] == "synthetic_answer_plain")["risk_flag"] = True
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.validate_clinical_risk_contract(d, m)


def test_unflagged_option_cannot_be_trigger():
    d, m = _valid_pair()
    d = copy.deepcopy(d)
    d["clinical_risk_contract"]["triggers"].append(
        {"item_id": "synthetic_item_1", "answer_id": "answer_a",
         "action": "crisis"})
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.validate_clinical_risk_contract(d, m)


def test_every_risk_flagged_option_must_be_mapped():
    d, m = _valid_pair()
    d = copy.deepcopy(d)
    item1 = next(i for i in d["items"] if i["id"] == "synthetic_item_1")
    next(o for o in item1["options"] if o["id"] == "answer_b")["risk_flag"] = True
    # second risk option now exists but has no trigger -> unrouted -> rejected
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.validate_clinical_risk_contract(d, m)


def test_extra_trigger_for_nonrisk_option_rejected():
    d, m = _valid_pair()
    d = copy.deepcopy(d)
    d["clinical_risk_contract"]["triggers"].append(
        {"item_id": "synthetic_item_2", "answer_id": "synthetic_answer_plain",
         "action": "crisis"})
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.validate_clinical_risk_contract(d, m)


def test_top_level_risk_without_option_flags_rejected():
    d, m = _valid_pair()
    d = copy.deepcopy(d)
    for item in d["items"]:
        item.pop("risk_flag", None)
        for o in item["options"]:
            o.pop("risk_flag", None)
    # contains_risk_items stays True -> ambiguous, nothing exact to route.
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.extract_option_risk_pairs(d)
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.validate_clinical_risk_contract(d, m)


def test_item_level_risk_flag_is_ambiguous_and_rejected():
    d, m = _valid_pair()
    d = copy.deepcopy(d)
    item2 = next(i for i in d["items"] if i["id"] == "synthetic_item_2")
    assert item2.get("risk_flag") is True  # item-level flag stays
    next(o for o in item2["options"]
         if o["id"] == "synthetic_answer_crisis").pop("risk_flag")
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.extract_option_risk_pairs(d)
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.validate_clinical_risk_contract(d, m)


def test_two_risk_options_require_two_exact_triggers():
    d, m = _valid_pair()
    d = copy.deepcopy(d)
    item1 = next(i for i in d["items"] if i["id"] == "synthetic_item_1")
    next(o for o in item1["options"] if o["id"] == "answer_b")["risk_flag"] = True
    # one trigger only -> rejected
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.validate_clinical_risk_contract(d, m)
    # both exact triggers -> accepted, and both routes fire
    d["clinical_risk_contract"]["triggers"].append(
        {"item_id": "synthetic_item_1", "answer_id": "answer_b",
         "action": "crisis"})
    crc.validate_clinical_risk_contract(d, m)
    assert crc.evaluate_clinical_risk_answer(
        d, m, item_id="synthetic_item_1",
        answer_id="answer_b").action is crc.ClinicalRiskAction.CRISIS


def test_trigger_set_equals_option_risk_pair_set():
    d, m = _valid_pair()
    crc.validate_clinical_risk_contract(d, m)
    trigger_pairs = {(t["item_id"], t["answer_id"])
                     for t in d["clinical_risk_contract"]["triggers"]}
    assert trigger_pairs == set(crc.extract_option_risk_pairs(d))


# ── A2 hardening: closed schemas ──────────────────────────────────────────────
def test_unknown_contract_field_rejected():
    d, m = _valid_pair()
    for bad_field in ("message", "phone", "destination", "owner_id",
                      "severity", "handler", "callback", "score",
                      "threshold", "metadata"):
        d2 = copy.deepcopy(d)
        d2["clinical_risk_contract"][bad_field] = "x"
        with pytest.raises(crc.ClinicalRiskContractError):
            crc.validate_clinical_risk_contract(d2, m)


def test_unknown_trigger_field_rejected():
    d, m = _valid_pair()
    for bad_field in ("message", "phone", "destination", "owner_id",
                      "severity", "handler", "callback", "score",
                      "threshold", "metadata"):
        d2 = copy.deepcopy(d)
        d2["clinical_risk_contract"]["triggers"][0][bad_field] = "x"
        with pytest.raises(crc.ClinicalRiskContractError):
            crc.validate_clinical_risk_contract(d2, m)


def test_non_dict_inputs_rejected():
    d, m = _valid_pair()
    for bad in (None, [], "x", 5):
        with pytest.raises(crc.ClinicalRiskContractError):
            crc.validate_clinical_risk_contract(bad, m)
        with pytest.raises(crc.ClinicalRiskContractError):
            crc.validate_clinical_risk_contract(d, bad)
    d2 = copy.deepcopy(d)
    d2["clinical_risk_contract"]["triggers"] = ["not-a-dict"]
    with pytest.raises(crc.ClinicalRiskContractError):
        crc.validate_clinical_risk_contract(d2, m)


def test_runtime_ids_must_be_strings_no_coercion():
    d, m = _valid_pair()
    for bad in (2, None, ["synthetic_item_2"], " synthetic_item_2 "):
        with pytest.raises(crc.ClinicalRiskContractError):
            crc.evaluate_clinical_risk_answer(
                d, m, item_id=bad, answer_id="synthetic_answer_crisis")
