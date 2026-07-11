"""Exact DASS-21 scorer (PR #55). All content here is the SYNTHETIC shape
fixture — no real item wording appears in tracked files."""
import copy
import json
import pathlib

import pytest

import clinical_scoring as cs
import dass21_scorer as ds

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "dass21" / "synthetic_dass21_shape.json"


def _definition():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _dass_entry(**over):
    entry = {
        "instrument_id": "dass",
        "display_name_ru": "Шкала депрессии, тревоги и стресса",
        "display_name_en": "Depression Anxiety Stress Scales",
        "catalog_category_id": "stress",
        "abbreviation": "DASS",
        "version": "DASS-21",
        "translation_id": "fattakhov_ru_2024",
        "identity_status": "verified",
        "domain": "depression_anxiety_stress",
        "administration_mode": "self_report",
        "population": ["adult"],
        "activation_status": "ready",
        "questionnaire_definition_id": "dass21_ru_fattakhov_2024",
        "scoring_contract_id": "dass21_official_subscales",
        "scoring_version": "unsw_template_v1",
        "risk_contract_id": None,
        "risk_contract_version": None,
        "public_catalog_visible": False,
        "risk_item_metadata_status": "verified",
        "evidence": [{"kind": "official_publisher", "title": "UNSW DASS site",
                      "url": "https://www2.psy.unsw.edu.au/dass/",
                      "accessed_at": "2026-07-11", "supports": ["identity"]}],
        "rights": {k: {"status": "allowed", "evidence": [
            {"kind": "license_terms", "title": "UNSW down.htm",
             "url": "https://www2.psy.unsw.edu.au/dass/down.htm",
             "accessed_at": "2026-07-11", "supports": [k]}]}
            for k in ("digital_reproduction", "commercial_use", "translation_use")},
        "blockers": [],
    }
    entry.update(over)
    return entry


def _manifest(**over):
    return {"schema_version": 2, "instruments": [_dass_entry(**over)]}


def _responses(value_by_item=None, default=0):
    d = _definition()
    out = []
    for item in d["items"]:
        v = (value_by_item or {}).get(item["id"], default)
        out.append(cs.ClinicalResponse(item["id"], f"a{v}", v))
    return out


def _registry():
    reg = cs.ClinicalScorerRegistry()
    reg.register(ds.Dass21Scorer())
    return reg


def _score(responses, manifest=None, definition=None):
    return cs.score_validated_clinical_definition(
        definition or _definition(), manifest or _manifest(),
        responses, _registry())


# ── exact key ─────────────────────────────────────────────────────────────────
def test_exact_scorer_key():
    assert ds.DASS21_SCORER_KEY == cs.ClinicalScorerKey(
        instrument_id="dass", instrument_version="DASS-21",
        translation_id="fattakhov_ru_2024",
        scoring_contract_id="dass21_official_subscales",
        scoring_version="unsw_template_v1")
    assert ds.Dass21Scorer().key == ds.DASS21_SCORER_KEY


# ── official mapping ──────────────────────────────────────────────────────────
def test_seven_items_per_subscale_and_full_coverage():
    groups = (ds.STRESS_ITEMS, ds.ANXIETY_ITEMS, ds.DEPRESSION_ITEMS)
    assert all(len(g) == 7 for g in groups)
    all_ids = set().union(*map(set, groups))
    assert all_ids == {f"dass21_{n:02d}" for n in range(1, 22)}
    assert sum(map(len, groups)) == 21  # disjoint


def test_official_template_assignment():
    # S A D A D S A S A D S S D S A D D S A A D (items 1..21)
    assert ds.STRESS_ITEMS == ("dass21_01", "dass21_06", "dass21_08",
                               "dass21_11", "dass21_12", "dass21_14", "dass21_18")
    assert ds.ANXIETY_ITEMS == ("dass21_02", "dass21_04", "dass21_07",
                                "dass21_09", "dass21_15", "dass21_19", "dass21_20")
    assert ds.DEPRESSION_ITEMS == ("dass21_03", "dass21_05", "dass21_10",
                                   "dass21_13", "dass21_16", "dass21_17", "dass21_21")


def test_all_zero_scores_zero():
    result = _score(_responses(default=0))
    assert result.subscales == {"depression": 0, "anxiety": 0, "stress": 0}


def test_all_three_scores_42_each():
    result = _score(_responses(default=3))
    # 7 items * 3 * multiplier 2 = 42 per subscale (official x2 rule)
    assert result.subscales == {"depression": 42, "anxiety": 42, "stress": 42}


def test_single_item_lands_on_correct_subscale_with_multiplier_two():
    for item_id, scale in (("dass21_01", "stress"), ("dass21_02", "anxiety"),
                           ("dass21_03", "depression"), ("dass21_21", "depression"),
                           ("dass21_20", "anxiety"), ("dass21_18", "stress")):
        result = _score(_responses({item_id: 1}))
        expected = {"depression": 0, "anxiety": 0, "stress": 0, scale: 2}
        assert dict(result.subscales) == expected, item_id


def test_no_overall_total():
    result = _score(_responses(default=2))
    assert result.raw_total is None
    assert result.transformed_total is None
    assert set(result.subscales) == {"depression", "anxiety", "stress"}


def test_input_order_invariant():
    a = _score(_responses({"dass21_05": 3, "dass21_09": 2}))
    r = _responses({"dass21_05": 3, "dass21_09": 2})
    r.reverse()
    b = _score(r)
    assert a.subscales == b.subscales


def test_missing_response_rejected():
    with pytest.raises(cs.ClinicalScoringError):
        _score(_responses()[:-1])


def test_unknown_item_rejected():
    r = _responses()
    r[0] = cs.ClinicalResponse("dass21_99", "a0", 0)
    with pytest.raises(cs.ClinicalScoringError):
        _score(r)


def test_version_mismatch_rejected():
    with pytest.raises(cs.ClinicalScoringError):
        _score(_responses(), manifest=_manifest(version="DASS-42"))


def test_translation_mismatch_rejected():
    with pytest.raises(cs.ClinicalScoringError):
        _score(_responses(), manifest=_manifest(translation_id="other_ru_v1"))


def test_scoring_contract_mismatch_rejected():
    with pytest.raises(cs.ClinicalScoringError):
        _score(_responses(), manifest=_manifest(
            scoring_contract_id="different_contract"))


def test_risk_bearing_definition_rejected():
    d = _definition()
    d = copy.deepcopy(d)
    d["contains_risk_items"] = True
    with pytest.raises(cs.ClinicalScoringError):
        _score(_responses(), definition=d)


def test_no_cutoff_or_severity_logic_in_module():
    # Assert against CODE, not the negative guards in the module docstring.
    import ast
    tree = ast.parse((REPO_ROOT / "dass21_scorer.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)):
                node.body = node.body[1:] or [ast.Pass()]
    src = ast.unparse(tree).lower()
    for banned in ("mild", "moderate", "extremely", "норма", "лёгк", "умерен",
                   "тяжёл", "percentile", "threshold", "диагноз"):
        assert banned not in src, banned


def test_module_is_pure_no_forbidden_imports():
    src = (REPO_ROOT / "dass21_scorer.py").read_text(encoding="utf-8")
    for banned in ("import bot", "import database", "import openai",
                   "import aiogram", "from bot", "from database",
                   "from openai", "from aiogram"):
        assert banned not in src
