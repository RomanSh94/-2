"""Pure exact-version clinical scoring contract (PR #53).

Covers the scorer key/registry contract, the exact manifest<->definition
scoring-metadata linkage, fail-closed response validation, and the risk /
clinician-rated safety boundaries. Everything is synthetic: the only scorer is a
tiny SyntheticLinearTotalScorer defined here; the production registry is empty by
default, no real instrument resolves a scorer, and no user-facing path changes.
"""
import copy
import json
import pathlib

import pytest

import clinical_scoring as cs
import clinical_definition_validator as cdv
import clinical_instrument_catalog as cat
import questionnaires

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CLINICAL_DIR = pathlib.Path(__file__).parent / "fixtures" / "clinical_definitions"
MANIFEST_PATH = REPO_ROOT / "clinical_instruments_manifest.json"


# ── synthetic scoring fixtures ───────────────────────────────────────────────
def _load_def(name):
    return json.loads((CLINICAL_DIR / name).read_text(encoding="utf-8"))


def _ready_entry(**over):
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
        "scoring_contract_id": "synthetic_linear_total",
        "scoring_version": "1",
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


def _key(**over):
    base = dict(instrument_id="synthetic_scale", instrument_version="v1",
               translation_id="syn_ru_v1",
               scoring_contract_id="synthetic_linear_total", scoring_version="1")
    base.update(over)
    return cs.ClinicalScorerKey(**base)


class SyntheticLinearTotalScorer:
    """Tiny synthetic scorer: raw_total = sum(answer_value). Deliberately NOT
    named after or modelled on any real instrument. No cutoff/severity/
    interpretation."""
    def __init__(self, key=None):
        self.key = key or _key()

    def score(self, definition, responses):
        total = sum(r.answer_value for r in responses)
        return cs.ClinicalScoreResult(
            scorer_key=self.key,
            raw_total=total,
            transformed_total=None,
            subscales={},
            scored_item_ids=tuple(r.item_id for r in responses))


def _responses_for(definition):
    """Complete, valid responses: first option of each item."""
    out = []
    for item in definition["items"]:
        opt = item["options"][0]
        out.append(cs.ClinicalResponse(item["id"], opt["id"], int(opt["value"])))
    return out


def _registry_with_scorer():
    reg = cs.ClinicalScorerRegistry()
    reg.register(SyntheticLinearTotalScorer())
    return reg


def _ready_pair():
    d = _load_def("synthetic_ready_v1.json")
    m = _manifest([_ready_entry()])
    assert cdv.validate_clinical_definition_link(d, m).status \
        is cdv.ClinicalDefinitionStatus.VALID
    return d, m


# ── key / registry ───────────────────────────────────────────────────────────
def test_exact_key_required_missing_scorer_denied():
    reg = cs.ClinicalScorerRegistry()
    with pytest.raises(cs.ClinicalScoringError):
        reg.resolve(_key())


def test_duplicate_registration_denied():
    reg = cs.ClinicalScorerRegistry()
    reg.register(SyntheticLinearTotalScorer())
    with pytest.raises(cs.ClinicalScoringError):
        reg.register(SyntheticLinearTotalScorer())


@pytest.mark.parametrize("field", [
    "instrument_id", "instrument_version", "translation_id",
    "scoring_contract_id", "scoring_version"])
def test_any_key_field_mismatch_denied(field):
    reg = _registry_with_scorer()
    with pytest.raises(cs.ClinicalScoringError):
        reg.resolve(_key(**{field: "different"}))


def test_returned_scorer_key_mismatch_denied():
    d, m = _ready_pair()
    reg = cs.ClinicalScorerRegistry()
    reg.register(SyntheticLinearTotalScorer(key=_key()))
    # A scorer that lies about its result key: registered under the correct key
    # but returns a different one.
    reg._scorers[_key()] = _LyingScorer()
    with pytest.raises(cs.ClinicalScoringError):
        cs.score_validated_clinical_definition(d, m, _responses_for(d), reg)


class _LyingScorer:
    key = _key()

    def score(self, definition, responses):
        return cs.ClinicalScoreResult(
            scorer_key=_key(scoring_version="99"), raw_total=0,
            transformed_total=None, subscales={}, scored_item_ids=())


def test_no_mutable_global_default_registry():
    # Two independent registries share no state; there is no module singleton.
    a, b = cs.ClinicalScorerRegistry(), cs.ClinicalScorerRegistry()
    a.register(SyntheticLinearTotalScorer())
    with pytest.raises(cs.ClinicalScoringError):
        b.resolve(_key())
    assert not hasattr(cs, "DEFAULT_REGISTRY")
    assert not hasattr(cs, "default_registry")


# ── linkage ──────────────────────────────────────────────────────────────────
def test_valid_linkage_required_to_score():
    d, m = _ready_pair()
    reg = _registry_with_scorer()
    result = cs.score_validated_clinical_definition(d, m, _responses_for(d), reg)
    assert isinstance(result, cs.ClinicalScoreResult)
    assert result.scorer_key == _key()


def test_blocked_manifest_denies_scoring():
    d = _load_def("synthetic_ready_v1.json")
    m = _manifest([_ready_entry(activation_status="blocked")])
    with pytest.raises(cs.ClinicalScoringError):
        cs.score_validated_clinical_definition(d, m, _responses_for(d),
                                               _registry_with_scorer())


def test_missing_manifest_denies_scoring():
    d = _load_def("synthetic_ready_v1.json")
    with pytest.raises(cs.ClinicalScoringError):
        cs.score_validated_clinical_definition(d, {}, _responses_for(d),
                                               _registry_with_scorer())


def test_nonclinical_definition_denied_by_clinical_scorer():
    # An ordinary definition (no clinical metadata, not mapped) is NOT_CLINICAL,
    # never VALID -> the clinical scorer refuses it.
    d = _load_def("synthetic_ready_v1.json")
    d = copy.deepcopy(d)
    d.pop("clinical_instrument", None)
    m = _manifest([_ready_entry(questionnaire_definition_id="unmapped_v1")])
    with pytest.raises(cs.ClinicalScoringError):
        cs.score_validated_clinical_definition(d, m, _responses_for(d),
                                               _registry_with_scorer())


def test_scorer_metadata_exact_match_required():
    # Manifest scorer contract differs from definition metadata -> linkage
    # INVALID -> scoring denied.
    d = _load_def("synthetic_ready_v1.json")
    m = _manifest([_ready_entry(scoring_contract_id="other_contract")])
    assert cdv.validate_clinical_definition_link(d, m).status \
        is cdv.ClinicalDefinitionStatus.INVALID
    with pytest.raises(cs.ClinicalScoringError):
        cs.score_validated_clinical_definition(d, m, _responses_for(d),
                                               _registry_with_scorer())


def test_ready_manifest_requires_scoring_contract_and_version():
    for missing in ("scoring_contract_id", "scoring_version"):
        entry = _ready_entry()
        entry[missing] = None
        with pytest.raises(cat.InstrumentManifestError):
            cat.validate_instrument_metadata(entry)


# ── responses ────────────────────────────────────────────────────────────────
def test_complete_synthetic_responses_score():
    d, m = _ready_pair()
    result = cs.score_validated_clinical_definition(
        d, m, _responses_for(d), _registry_with_scorer())
    assert result.raw_total == sum(int(i["options"][0]["value"]) for i in d["items"])


def test_missing_item_denied():
    d, m = _ready_pair()
    with pytest.raises(cs.ClinicalScoringError):
        cs.score_validated_clinical_definition(
            d, m, _responses_for(d)[:-1], _registry_with_scorer())


def test_duplicate_item_denied():
    d, m = _ready_pair()
    r = _responses_for(d)
    dup = [r[0], r[0]] + r[1:]  # duplicate first, drop nothing -> count matches items? no
    with pytest.raises(cs.ClinicalScoringError):
        cs.validate_clinical_responses(d, [r[0]] * len(d["items"]))
    with pytest.raises(cs.ClinicalScoringError):
        cs.validate_clinical_responses(d, dup)


def test_unknown_item_denied():
    d, m = _ready_pair()
    r = _responses_for(d)
    r[0] = cs.ClinicalResponse("no_such_item", r[0].answer_id, r[0].answer_value)
    with pytest.raises(cs.ClinicalScoringError):
        cs.validate_clinical_responses(d, r)


def test_unknown_answer_denied():
    d, m = _ready_pair()
    r = _responses_for(d)
    r[0] = cs.ClinicalResponse(r[0].item_id, "no_such_answer", r[0].answer_value)
    with pytest.raises(cs.ClinicalScoringError):
        cs.validate_clinical_responses(d, r)


def test_answer_value_mismatch_denied():
    d, m = _ready_pair()
    r = _responses_for(d)
    r[0] = cs.ClinicalResponse(r[0].item_id, r[0].answer_id, r[0].answer_value + 999)
    with pytest.raises(cs.ClinicalScoringError):
        cs.validate_clinical_responses(d, r)


def test_non_numeric_answer_value_denied():
    d, m = _ready_pair()
    r = _responses_for(d)
    r[0] = cs.ClinicalResponse(r[0].item_id, r[0].answer_id, "0")  # str, not numeric
    with pytest.raises(cs.ClinicalScoringError):
        cs.validate_clinical_responses(d, r)


def test_extra_response_denied():
    d, m = _ready_pair()
    r = _responses_for(d)
    extra = cs.ClinicalResponse("no_such_item", "a0", 0)
    with pytest.raises(cs.ClinicalScoringError):
        cs.validate_clinical_responses(d, r + [extra])


def test_empty_responses_denied():
    d, m = _ready_pair()
    with pytest.raises(cs.ClinicalScoringError):
        cs.validate_clinical_responses(d, [])


def test_input_order_does_not_change_result():
    d, m = _ready_pair()
    reg = _registry_with_scorer()
    forward = cs.score_validated_clinical_definition(d, m, _responses_for(d), reg)
    reversed_ = cs.score_validated_clinical_definition(
        d, m, list(reversed(_responses_for(d))), reg)
    assert forward.raw_total == reversed_.raw_total
    assert set(forward.scored_item_ids) == set(reversed_.scored_item_ids)


def test_inputs_not_mutated():
    d, m = _ready_pair()
    responses = _responses_for(d)
    snapshot = copy.deepcopy(responses)
    d_snapshot = copy.deepcopy(d)
    cs.score_validated_clinical_definition(d, m, responses, _registry_with_scorer())
    assert responses == snapshot
    assert d == d_snapshot


# ── scope / safety ───────────────────────────────────────────────────────────
def test_risk_bearing_definition_denied():
    d = _load_def("synthetic_risk_bearing.json")
    m = _manifest([_ready_entry(
        questionnaire_definition_id="synthetic_risk_bearing_v1")])
    # BLOCKED at linkage already; scorer refuses regardless.
    with pytest.raises(cs.ClinicalScoringError):
        cs.score_validated_clinical_definition(d, m, _responses_for(d),
                                               _registry_with_scorer())


def test_clinician_rated_definition_denied():
    d = _load_def("synthetic_clinician_rated.json")
    m = _manifest([_ready_entry(
        administration_mode="clinician_rated",
        questionnaire_definition_id="synthetic_clinician_rated_v1")])
    with pytest.raises(cs.ClinicalScoringError):
        cs.score_validated_clinical_definition(d, m, _responses_for(d),
                                               _registry_with_scorer())


def test_only_dass_ready_and_scorer_mapped():
    # PR #55: dass is the single ready + scorer-mapped entry (exact pinned
    # scorer dass21_official_subscales/unsw_template_v1); no other real
    # instrument is ready or scorer-mapped.
    doc = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    ready = [x["instrument_id"] for x in doc["instruments"]
             if x.get("activation_status") == "ready"]
    mapped = [(x["instrument_id"], x.get("scoring_contract_id"),
               x.get("scoring_version")) for x in doc["instruments"]
              if x.get("scoring_contract_id") or x.get("scoring_version")]
    assert ready == ["dass"]
    assert mapped == [("dass", "dass21_official_subscales", "unsw_template_v1")]


def test_no_named_real_scorer_registered_by_default():
    # There is no module-level production registry; a fresh one is empty.
    reg = cs.ClinicalScorerRegistry()
    assert reg._scorers == {}


def test_no_interpretation_or_cutoff_symbols_in_module():
    src = (REPO_ROOT / "clinical_scoring.py").read_text(encoding="utf-8").lower()
    for banned in ("cutoff", "percentile", "severity", "diagnos", "reverse_items"):
        # allowed only inside the guardrail docstring; assert none appear as code
        assert f"{banned} =" not in src and f"def {banned}" not in src


def test_module_has_no_forbidden_imports():
    src = (REPO_ROOT / "clinical_scoring.py").read_text(encoding="utf-8")
    for banned in ("import bot", "import database", "import openai",
                   "from bot", "from database", "from openai"):
        assert banned not in src


def test_existing_compute_sum_score_unchanged():
    # The generic nonclinical scorer still exists and works independently.
    assert hasattr(questionnaires, "compute_sum_score")


# ── §4.1 public risk predicate ────────────────────────────────────────────────
def test_scoring_uses_public_risk_predicate_not_private_helper():
    src = (REPO_ROOT / "clinical_scoring.py").read_text(encoding="utf-8")
    assert "definition_is_risk_bearing(" in src
    assert "_definition_is_risk_bearing" not in src  # no private cross-module use
    # single shared implementation, public name, alias preserved for compat
    assert cdv.definition_is_risk_bearing is cdv._definition_is_risk_bearing


# ── §4.2 scorer result validation ─────────────────────────────────────────────
class _ConfigurableScorer:
    """Scorer whose result is injected by each test."""
    key = _key()

    def __init__(self, result_factory):
        self._factory = result_factory

    def score(self, definition, responses):
        return self._factory(responses)


def _score_with_result(result_factory):
    d, m = _ready_pair()
    reg = cs.ClinicalScorerRegistry()
    reg.register(_ConfigurableScorer(result_factory))
    return cs.score_validated_clinical_definition(
        d, m, _responses_for(d), reg)


def _good_result(responses, **over):
    fields = dict(
        scorer_key=_key(),
        raw_total=float(sum(r.answer_value for r in responses)),
        transformed_total=None,
        subscales={},
        scored_item_ids=tuple(r.item_id for r in responses))
    fields.update(over)
    return cs.ClinicalScoreResult(**fields)


def test_scorer_must_return_clinical_score_result():
    with pytest.raises(cs.ClinicalScoringError):
        _score_with_result(lambda rs: {"raw_total": 1})


def test_scorer_result_key_mismatch_rejected():
    with pytest.raises(cs.ClinicalScoringError):
        _score_with_result(
            lambda rs: _good_result(rs, scorer_key=_key(scoring_version="99")))


def test_scorer_result_missing_item_ids_rejected():
    with pytest.raises(cs.ClinicalScoringError):
        _score_with_result(lambda rs: _good_result(rs, scored_item_ids=tuple(
            r.item_id for r in rs[:-1])))


def test_scorer_result_extra_item_ids_rejected():
    with pytest.raises(cs.ClinicalScoringError):
        _score_with_result(lambda rs: _good_result(rs, scored_item_ids=tuple(
            [r.item_id for r in rs] + ["ghost_item"])))


def test_scorer_result_duplicate_item_ids_rejected():
    with pytest.raises(cs.ClinicalScoringError):
        _score_with_result(lambda rs: _good_result(rs, scored_item_ids=tuple(
            [rs[0].item_id] * len(rs))))


def test_scorer_result_wrong_item_order_rejected():
    with pytest.raises(cs.ClinicalScoringError):
        _score_with_result(lambda rs: _good_result(rs, scored_item_ids=tuple(
            reversed([r.item_id for r in rs]))))


def test_scorer_result_nan_rejected():
    with pytest.raises(cs.ClinicalScoringError):
        _score_with_result(lambda rs: _good_result(rs, raw_total=float("nan")))


def test_scorer_result_infinity_rejected():
    with pytest.raises(cs.ClinicalScoringError):
        _score_with_result(lambda rs: _good_result(rs, raw_total=float("inf")))
    with pytest.raises(cs.ClinicalScoringError):
        _score_with_result(lambda rs: _good_result(
            rs, transformed_total=float("-inf")))


def test_scorer_result_bool_total_rejected():
    with pytest.raises(cs.ClinicalScoringError):
        _score_with_result(lambda rs: _good_result(rs, raw_total=True))


def test_scorer_result_invalid_subscale_value_rejected():
    for bad in ({"sub1": float("nan")}, {"sub1": True}, {"sub1": "5"},
                {"": 1.0}, {"bad key": 1.0}):
        with pytest.raises(cs.ClinicalScoringError):
            _score_with_result(lambda rs, b=bad: _good_result(rs, subscales=b))


def test_scorer_result_mutable_mapping_is_defensively_copied():
    scorer_owned = {"sub1": 1.0}
    result = _score_with_result(
        lambda rs: _good_result(rs, subscales=scorer_owned))
    scorer_owned["sub1"] = 999.0          # later scorer-side mutation
    assert result.subscales["sub1"] == 1.0  # returned result unaffected
    with pytest.raises(TypeError):
        result.subscales["sub1"] = 5.0    # returned mapping is immutable


def test_scorer_result_version_mismatch_rejected():
    # Single authoritative revision: ClinicalScoreResult has NO separate
    # algorithm_version field; scorer_key.scoring_version is the only revision
    # and any divergence is a key mismatch.
    assert "algorithm_version" not in cs.ClinicalScoreResult.__dataclass_fields__
    with pytest.raises(cs.ClinicalScoringError):
        _score_with_result(lambda rs: _good_result(
            rs, scorer_key=_key(scoring_version="2")))


def test_scorer_result_requires_at_least_one_numeric_output():
    with pytest.raises(cs.ClinicalScoringError):
        _score_with_result(lambda rs: _good_result(
            rs, raw_total=None, transformed_total=None, subscales={}))


# ── §4.3 malformed runtime response objects ───────────────────────────────────
def test_non_clinical_response_object_rejected():
    d, _ = _ready_pair()
    r = _responses_for(d)
    r[0] = {"item_id": r[0].item_id, "answer_id": r[0].answer_id,
            "answer_value": r[0].answer_value}  # dict, not ClinicalResponse
    with pytest.raises(cs.ClinicalScoringError):
        cs.validate_clinical_responses(d, r)


def test_response_bool_value_rejected():
    d, _ = _ready_pair()
    r = _responses_for(d)
    r[0] = cs.ClinicalResponse(r[0].item_id, r[0].answer_id, True)
    with pytest.raises(cs.ClinicalScoringError):
        cs.validate_clinical_responses(d, r)


def test_response_nan_rejected():
    d, _ = _ready_pair()
    r = _responses_for(d)
    r[0] = cs.ClinicalResponse(r[0].item_id, r[0].answer_id, float("nan"))
    with pytest.raises(cs.ClinicalScoringError):
        cs.validate_clinical_responses(d, r)


def test_response_infinity_rejected():
    d, _ = _ready_pair()
    r = _responses_for(d)
    r[0] = cs.ClinicalResponse(r[0].item_id, r[0].answer_id, float("inf"))
    with pytest.raises(cs.ClinicalScoringError):
        cs.validate_clinical_responses(d, r)


def test_option_nan_rejected():
    d, _ = _ready_pair()
    d = copy.deepcopy(d)
    d["items"][0]["options"][0]["value"] = "nan"  # float()-parseable NaN
    with pytest.raises(cs.ClinicalScoringError):
        cs.validate_clinical_responses(
            d, _responses_for(_load_def("synthetic_ready_v1.json")))


def test_option_infinity_rejected():
    d, _ = _ready_pair()
    d = copy.deepcopy(d)
    d["items"][0]["options"][0]["value"] = "inf"
    with pytest.raises(cs.ClinicalScoringError):
        cs.validate_clinical_responses(
            d, _responses_for(_load_def("synthetic_ready_v1.json")))


def test_duplicate_definition_item_id_rejected():
    d, _ = _ready_pair()
    d = copy.deepcopy(d)
    d["items"].append(copy.deepcopy(d["items"][0]))  # duplicate item id
    with pytest.raises(cs.ClinicalScoringError):
        cs.validate_clinical_responses(
            d, _responses_for(_load_def("synthetic_ready_v1.json")))


def test_duplicate_definition_option_id_rejected():
    d, _ = _ready_pair()
    d = copy.deepcopy(d)
    item = d["items"][0]
    item["options"].append(copy.deepcopy(item["options"][0]))
    with pytest.raises(cs.ClinicalScoringError):
        cs.validate_clinical_responses(
            d, _responses_for(_load_def("synthetic_ready_v1.json")))


def test_empty_item_id_rejected():
    d, _ = _ready_pair()
    r = _responses_for(d)
    r[0] = cs.ClinicalResponse("", r[0].answer_id, r[0].answer_value)
    with pytest.raises(cs.ClinicalScoringError):
        cs.validate_clinical_responses(d, r)


def test_empty_answer_id_rejected():
    d, _ = _ready_pair()
    r = _responses_for(d)
    r[0] = cs.ClinicalResponse(r[0].item_id, "", r[0].answer_value)
    with pytest.raises(cs.ClinicalScoringError):
        cs.validate_clinical_responses(d, r)


# ── §4.4 atomic scoring token pairs ───────────────────────────────────────────
def test_manifest_scoring_tokens_both_null_allowed():
    entry = _ready_entry(activation_status="blocked",
                         scoring_contract_id=None, scoring_version=None)
    cat.validate_instrument_metadata(entry)  # must not raise


def test_manifest_contract_without_version_rejected():
    entry = _ready_entry(activation_status="blocked", scoring_version=None)
    with pytest.raises(cat.InstrumentManifestError):
        cat.validate_instrument_metadata(entry)


def test_manifest_version_without_contract_rejected():
    entry = _ready_entry(activation_status="blocked", scoring_contract_id=None)
    with pytest.raises(cat.InstrumentManifestError):
        cat.validate_instrument_metadata(entry)


def test_definition_contract_without_version_fails_linkage():
    d, m = _ready_pair()
    d = copy.deepcopy(d)
    d["clinical_instrument"]["scoring_version"] = None
    res = cdv.validate_clinical_definition_link(d, m)
    assert res.status is cdv.ClinicalDefinitionStatus.INVALID
    assert "scoring-pair-not-atomic" in res.reason_codes


def test_definition_version_without_contract_fails_linkage():
    d, m = _ready_pair()
    d = copy.deepcopy(d)
    d["clinical_instrument"]["scoring_contract_id"] = None
    res = cdv.validate_clinical_definition_link(d, m)
    assert res.status is cdv.ClinicalDefinitionStatus.INVALID
    assert "scoring-pair-not-atomic" in res.reason_codes


def test_ready_requires_exact_scoring_pair():
    for missing in ("scoring_contract_id", "scoring_version"):
        entry = _ready_entry(**{missing: None})
        with pytest.raises(cat.InstrumentManifestError):
            cat.validate_instrument_metadata(entry)


# ── §4.5 no unvalidated execution path ────────────────────────────────────────
def test_no_public_unvalidated_scorer_execution_path():
    # The registry exposes register/resolve only; execution goes through
    # score_validated_clinical_definition which validates linkage, responses
    # AND the returned result.
    assert not hasattr(cs.ClinicalScorerRegistry, "score")
    public = [n for n in dir(cs.ClinicalScorerRegistry) if not n.startswith("_")]
    assert sorted(public) == ["register", "resolve"]


# ── §4.6 scorer failure normalization ─────────────────────────────────────────
class _CrashingScorer:
    key = _key()

    def score(self, definition, responses):
        raise KeyError("scorer bug")


class _RaisingClinicalErrorScorer:
    key = _key()

    def score(self, definition, responses):
        raise cs.ClinicalScoringError("explicit refusal")


def test_scorer_exception_wrapped_fail_closed():
    d, m = _ready_pair()
    reg = cs.ClinicalScorerRegistry()
    reg.register(_CrashingScorer())
    with pytest.raises(cs.ClinicalScoringError) as exc_info:
        cs.score_validated_clinical_definition(d, m, _responses_for(d), reg)
    assert isinstance(exc_info.value.__cause__, KeyError)


def test_clinical_scoring_error_passes_through():
    d, m = _ready_pair()
    reg = cs.ClinicalScorerRegistry()
    reg.register(_RaisingClinicalErrorScorer())
    with pytest.raises(cs.ClinicalScoringError, match="explicit refusal"):
        cs.score_validated_clinical_definition(d, m, _responses_for(d), reg)


# ── §4.7 purity / no manifest mutation ────────────────────────────────────────
def test_manifest_not_mutated():
    d, m = _ready_pair()
    snapshot = copy.deepcopy(m)
    cs.score_validated_clinical_definition(d, m, _responses_for(d),
                                           _registry_with_scorer())
    assert m == snapshot
