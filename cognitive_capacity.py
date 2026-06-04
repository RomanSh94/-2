"""
X20 Cognitive Capacity Engine

Derived from state. High panic/overwhelm = low capacity.
Used to prevent giving complex CBT tasks when brain is overloaded.

if capacity < 0.3: disable_cbt() — from research doc.
"""

def get_capacity(state: dict) -> float:
    """Returns float 0.0 (overloaded) to 1.0 (full capacity)."""
    panic    = state.get("panic", 0.0)
    overwhelm= state.get("overwhelm", 0.0)
    energy   = state.get("energy", 0.5)
    capacity = 1.0 - (panic * 0.5 + overwhelm * 0.35 + (1.0 - energy) * 0.15)
    return round(max(0.0, min(1.0, capacity)), 3)


def capacity_label(capacity: float) -> str:
    if capacity < 0.3: return "overload"
    if capacity < 0.6: return "limited"
    return "full"


def should_allow_cbt(capacity: float) -> bool:
    return capacity >= 0.3


def should_allow_act(capacity: float) -> bool:
    return capacity >= 0.35
