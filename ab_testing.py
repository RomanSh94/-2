"""
X20 A/B Testing

Users are deterministically assigned to variants by user_id % len(variants).
Deterministic: same user always gets same variant.
No DB needed for assignment — pure function.

Variants affect scenario_router behavior (in state_engine.choose_scenario):
  control   — CBT-first for anxiety
  variant_a — ACT-first for anxiety
"""
from config import AB_VARIANTS


def get_variant(user_id: int) -> str:
    if not AB_VARIANTS:
        return "control"
    return AB_VARIANTS[user_id % len(AB_VARIANTS)]
