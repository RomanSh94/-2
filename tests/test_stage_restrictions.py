"""STAGE_RESTRICTIONS as a HARD filter in choose_scenario.

Stage policy (stage_detector.STAGE_RESTRICTIONS) must actually gate the routed
scenario — not just be advisory.
"""
from state_engine import choose_scenario, apply_stage_restrictions, _select_scenario

_BASE = dict(anxiety=0.0, panic=0.0, overwhelm=0.0, hopelessness=0.0,
             loneliness=0.0, energy=0.5, openness=0.5, dissociation=0.0)


def _state(**kw):
    s = dict(_BASE); s.update(kw); return s


# ── apply_stage_restrictions unit ─────────────────────────────────────────────
def test_growth_blocks_grounding():
    # GROWTH blocks grounding → swapped for a safe allowed fallback (not grounding).
    out = apply_stage_restrictions("grounding", "GROWTH")
    assert out != "grounding"
    assert out in ("stabilization", "somatic", "reflective", "open_chat")


def test_crisis_never_downgraded():
    assert apply_stage_restrictions("crisis", "GROWTH") == "crisis"


def test_allowed_scenario_passes_through():
    assert apply_stage_restrictions("reflective", "GROWTH") == "reflective"
    assert apply_stage_restrictions("open_chat", "OPEN") == "open_chat"


def test_acute_blocks_cbt():
    assert apply_stage_restrictions("cbt_thought", "ACUTE_DISTRESS") != "cbt_thought"


# ── end-to-end through choose_scenario ────────────────────────────────────────
def test_growth_panic_no_longer_returns_grounding():
    # The exact bug from the backlog: GROWTH stage + panic spike used to grounding.
    raw = _select_scenario(_state(panic=0.9), [], "GROWTH", "MEDIUM", 0.5)
    gated = choose_scenario(_state(panic=0.9), [], "GROWTH", "MEDIUM", 0.5)
    assert raw == "grounding"          # selection still picks grounding…
    assert gated != "grounding"        # …but the hard filter swaps it out


def test_acute_distress_still_grounding_allowed():
    # ACUTE_DISTRESS allows grounding — must not be filtered away.
    assert choose_scenario(_state(panic=0.9), [], "ACUTE_DISTRESS", "LOW", 0.5) == "grounding"


def test_suicide_routes_crisis_regardless_of_stage():
    assert choose_scenario(_state(), ["suicide"], "GROWTH", "HIGH", 0.5) == "crisis"
