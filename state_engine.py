"""X20 State Engine — tracks emotional trajectory across messages."""
from dataclasses import dataclass, field
from typing import Dict

DEFAULT_STATE: Dict[str, float] = {
    "anxiety":0.0,"overwhelm":0.0,"panic":0.0,
    "loneliness":0.0,"anger":0.0,"hopelessness":0.0,
    "energy":0.5,"openness":0.5,"dissociation":0.0,
}
DECAY = 0.05

STATE_SIGNALS = {
    "anxiety":     ["тревожно","тревога","беспокоит","переживаю","страшно","боюсь","нервничаю",
                    "anxious","anxiety","worried","scared","nervous","fear"],
    "overwhelm":   ["всё слишком","не справляюсь","перегружен","перегружена","слишком много",
                    "too much","overwhelmed","can't handle","too many"],
    "panic":       ["паника","паническая атака","не могу дышать","задыхаюсь","теряю контроль",
                    "panic","panic attack","can't breathe","losing control"],
    "loneliness":  ["одиноко","я один","я одна","никого","не с кем","никто не понимает",
                    "lonely","alone","no one","nobody","isolated"],
    "anger":       ["злюсь","злость","ненависть","бесит","раздражает","ярость",
                    "angry","rage","hate","annoyed","furious","irritated"],
    "hopelessness":["безнадёжно","бесполезно","нет смысла","бессмысленно","всё равно",
                    "hopeless","pointless","useless","no point","meaningless"],
    "dissociation":["как во сне","не чувствую","пустота","как робот","онемел","онемела",
                    "like a dream","feel nothing","empty","like a robot","numb"],
}
POSITIVE_SIGNALS = {
    "energy":   ["лучше","полегче","немного легче","спасибо","помогло","хорошо",
                 "better","easier","helped","good","thank you"],
    "openness": ["хочу поговорить","расскажу","попробую","давай",
                 "want to talk","i'll tell","let's try","okay"],
}
NEG_ENERGY = ["устал","устала","нет сил","истощён","истощена","не могу вставать",
              "tired","exhausted","no energy","drained","can't get up"]


def update_state(state: Dict[str, float], text: str) -> Dict[str, float]:
    t = text.lower()
    s = {}
    for k, v in state.items():
        s[k] = v + (0.5 - v) * DECAY if k in ("energy", "openness") else max(0.0, v - DECAY)

    for emotion, signals in STATE_SIGNALS.items():
        if any(sig in t for sig in signals):
            s[emotion] = min(1.0, s.get(emotion, 0.0) + 0.2)

    if any(sig in t for sig in NEG_ENERGY):
        s["energy"] = max(0.0, s["energy"] - 0.15)

    for k, sigs in POSITIVE_SIGNALS.items():
        if any(sig in t for sig in sigs):
            s[k] = min(1.0, s.get(k, 0.5) + 0.15)

    return {k: round(min(1.0, max(0.0, v)), 3) for k, v in s.items()}


# ── §4 Conversation trajectory ────────────────────────────────────────────────
# Deterministic aggregate of the per-message risk snapshots already stored on the
# messages table. NO LLM, NO re-scoring. Answers: "what's been happening with
# this user over the last N hours?" — used to amplify ambiguity handling and to
# bias scenario routing.

@dataclass
class EmotionalTrajectory:
    user_id: int
    window_hours: int = 24
    messages_analyzed: int = 0
    max_risk_level: str = "GREEN"            # GREEN | YELLOW | ORANGE | RED
    avg_risk_score: float = 0.0
    trend: str = "stable"                    # deteriorating | stable | improving
    trend_confidence: float = 0.0
    hopelessness_streak: int = 0
    yellow_plus_streak: int = 0
    risk_categories_frequency: dict = field(default_factory=dict)
    has_crisis_in_window: bool = False
    last_crisis_at: str | None = None


def _level_from_score(score: float) -> str:
    if score >= 100: return "critical"
    if score >= 70:  return "high"
    if score >= 40:  return "medium"
    return "low"


def _colour(score: float, cats: list) -> str:
    """Mirror crisis_protocol.classify() on a per-message basis."""
    if "suicide" in cats or "self_harm" in cats:
        return "RED"
    level = _level_from_score(score)
    if level in ("critical", "high"): return "ORANGE"
    if level == "medium":             return "YELLOW"
    return "GREEN"


_COLOUR_ORDER = {"GREEN": 0, "YELLOW": 1, "ORANGE": 2, "RED": 3}


def calculate_trend(messages_with_risk: list) -> tuple[str, float]:
    """Compare avg risk_score of the window's first half vs second half.

    Returns (trend, confidence). Confidence is low when data is sparse."""
    if len(messages_with_risk) < 3:
        return "stable", 0.0
    mid = len(messages_with_risk) // 2
    first = [m["risk_score"] for m in messages_with_risk[:mid]]
    second = [m["risk_score"] for m in messages_with_risk[mid:]]
    diff = (sum(second) / len(second)) - (sum(first) / len(first))
    if diff > 15:
        trend = "deteriorating"
    elif diff < -15:
        trend = "improving"
    else:
        trend = "stable"
    return trend, min(0.9, len(messages_with_risk) / 20)


def _trailing_streak(messages_with_risk: list, predicate) -> int:
    streak = 0
    for m in reversed(messages_with_risk):
        if predicate(m):
            streak += 1
        else:
            break
    return streak


def build_trajectory(user_id: int, window_hours: int, messages_with_risk: list,
                     last_crisis_at: str | None) -> EmotionalTrajectory:
    """Pure builder — all I/O is done by the caller (get_emotional_trajectory).

    Kept separate so it can be unit-tested without a DB."""
    n = len(messages_with_risk)
    if n == 0:
        return EmotionalTrajectory(user_id=user_id, window_hours=window_hours,
                                   has_crisis_in_window=bool(last_crisis_at),
                                   last_crisis_at=last_crisis_at)
    freq: dict = {}
    max_colour = "GREEN"
    total = 0.0
    for m in messages_with_risk:
        total += m["risk_score"]
        for c in m["risk_categories"]:
            freq[c] = freq.get(c, 0) + 1
        col = _colour(m["risk_score"], m["risk_categories"])
        if _COLOUR_ORDER[col] > _COLOUR_ORDER[max_colour]:
            max_colour = col
    trend, conf = calculate_trend(messages_with_risk)
    return EmotionalTrajectory(
        user_id=user_id, window_hours=window_hours, messages_analyzed=n,
        max_risk_level=max_colour, avg_risk_score=round(total / n, 1),
        trend=trend, trend_confidence=round(conf, 2),
        hopelessness_streak=_trailing_streak(
            messages_with_risk, lambda m: "hopelessness" in m["risk_categories"]),
        yellow_plus_streak=_trailing_streak(
            messages_with_risk,
            lambda m: _COLOUR_ORDER[_colour(m["risk_score"], m["risk_categories"])] >= 1),
        risk_categories_frequency=freq,
        has_crisis_in_window=bool(last_crisis_at),
        last_crisis_at=last_crisis_at,
    )


async def get_emotional_trajectory(user_id: int, window_hours: int = 24) -> EmotionalTrajectory:
    """Deterministic trajectory over the last N hours. NO LLM, NO re-scoring —
    reads per-message risk snapshots persisted on the messages table."""
    from database import get_user_messages_with_risk, get_last_crisis_at
    msgs = await get_user_messages_with_risk(user_id, window_hours)
    last_crisis = await get_last_crisis_at(user_id, window_hours)
    return build_trajectory(user_id, window_hours, msgs, last_crisis)


def choose_scenario(state: Dict, risk_cats: list, stage: str,
                    readiness: str, capacity: float, variant: str = "control",
                    trajectory: "EmotionalTrajectory | None" = None) -> str:
    """
    Routes to psychological scenario.
    Respects Stage restrictions, Readiness, and Cognitive Capacity.
    variant_a: ACT-first for anxiety instead of CBT-first.

    `trajectory` (optional, §4): aggregated recent dynamics. It only *biases*
    non-crisis routing — it NEVER overrides the crisis path (that stays purely
    risk-driven).
    """
    if "suicide" in risk_cats or "self_harm" in risk_cats:
        return "crisis"

    # §4 trajectory bias — applied before the state heuristics, but only for
    # non-acute stages (acute distress keeps its grounding/stabilization rules).
    if trajectory and trajectory.messages_analyzed >= 3 and stage != "ACUTE_DISTRESS":
        freq = trajectory.risk_categories_frequency
        if freq:
            top_cat, top_n = max(freq.items(), key=lambda x: x[1])
            if top_cat == "loneliness" and top_n >= 3:
                return "reflective"
        if trajectory.trend == "deteriorating" and state.get("energy", 0.5) < 0.4:
            return "somatic"

    if stage == "ACUTE_DISTRESS":
        if state.get("panic", 0) > 0.4:
            return "grounding"
        return "stabilization"

    if state.get("panic", 0) > 0.6 or state.get("dissociation", 0) > 0.6:
        return "grounding"

    if state.get("overwhelm", 0) > 0.7:
        return "stabilization"

    if state.get("anxiety", 0) > 0.5:
        if capacity < 0.3:
            return "somatic"
        if variant == "variant_a":
            return "act_acceptance"
        return "cbt_thought"

    if state.get("hopelessness", 0) > 0.5 and state.get("openness", 0.5) > 0.35:
        return "act_acceptance"

    if state.get("loneliness", 0) > 0.5:
        return "reflective"

    if state.get("energy", 0.5) < 0.25:
        return "somatic"

    return "open_chat"
