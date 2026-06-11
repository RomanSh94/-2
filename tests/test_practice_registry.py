"""Tests for the expanded practice library and need-aware selector (Epic 4).

Pin: library size & integrity (40+, bilingual, valid fields, unique IDs, only
allowed schools), the NEED_HEARD=no-practice rule, need/severity/contraindication
matching, and that the legacy scenario selector still returns valid practices.
"""
import practice_registry as pr
from practice_registry import (
    REGISTRY, select_practice, select_practice_by_need, get_practice_by_id,
    USER_NEEDS, NEED_HEARD, NEED_CALM, NEED_SOLVE, NEED_BE_WITH, NEED_UNDERSTAND,
)

_ALLOWED_APPROACHES = {
    "CBT", "ACT", "DBT", "Mindfulness", "Self-Compassion", "MI",
    "Rogerian", "Positive Psychology", "Somatic",
}


def test_library_has_at_least_40():
    assert len(REGISTRY) >= 40


def test_ids_unique():
    ids = [p["id"] for p in REGISTRY]
    assert len(ids) == len(set(ids))


def test_every_practice_is_well_formed():
    for p in REGISTRY:
        assert p["steps_ru"] and p["steps_en"], p["id"]
        assert p["name_ru"] and p["name_en"], p["id"]
        assert p["user_need"] in USER_NEEDS, p["id"]
        assert p["approach"] in _ALLOWED_APPROACHES, p["id"]
        assert p["severity_min"] in ("low", "medium", "high"), p["id"]
        assert p["severity_max"] in ("low", "medium", "high"), p["id"]


def test_need_heard_returns_no_practice():
    assert select_practice_by_need(NEED_HEARD, "OPEN", "medium", "ru") is None


def test_need_calm_returns_calming_practice():
    p = select_practice_by_need(NEED_CALM, "OPEN", "high", "ru")
    assert p is not None
    assert p["user_need"] == NEED_CALM
    assert p["steps"]  # localized


def test_need_solve_in_acute_distress_is_safe():
    # CBT/MI solve-practices are contraindicated in ACUTE_DISTRESS; selector must
    # never return a practice contraindicated for the current stage.
    p = select_practice_by_need(NEED_SOLVE, "ACUTE_DISTRESS", "high", "ru")
    if p is not None:
        assert "ACUTE_DISTRESS" not in p.get("contraindications", [])


def test_localization_en():
    p = select_practice_by_need(NEED_BE_WITH, "OPEN", "low", "en")
    assert p is not None
    assert p["steps"] == p["steps_en"]
    assert p["name"] == p["name_en"]


def test_legacy_selector_all_scenarios():
    for scenario in ("crisis", "grounding", "stabilization", "cbt_thought",
                     "act_acceptance", "reflective", "somatic", "open_chat"):
        p = select_practice(scenario, "OPEN", "medium", "ru")
        assert p is not None and p["steps"], scenario


def test_legacy_selector_respects_acute_contraindication():
    # cbt_thought maps to cbt; in ACUTE_DISTRESS the thought-record is blocked,
    # so the fallback must not be a contraindicated practice.
    p = select_practice("cbt_thought", "ACUTE_DISTRESS", "medium", "ru")
    assert "ACUTE_DISTRESS" not in p.get("contraindications", [])


def test_get_practice_by_id_new_entry():
    p = get_practice_by_id("sc_break_v1", "en")
    assert p is not None and p["approach"] == "Self-Compassion"
