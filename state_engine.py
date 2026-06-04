"""X20 State Engine — tracks emotional trajectory across messages."""
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


def choose_scenario(state: Dict, risk_cats: list, stage: str,
                    readiness: str, capacity: float, variant: str = "control") -> str:
    """
    Routes to psychological scenario.
    Respects Stage restrictions, Readiness, and Cognitive Capacity.
    variant_a: ACT-first for anxiety instead of CBT-first.
    """
    if "suicide" in risk_cats or "self_harm" in risk_cats:
        return "crisis"

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
