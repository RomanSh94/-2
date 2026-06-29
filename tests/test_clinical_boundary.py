"""Executable lock for the Clinical Boundary Decision Record (CLINICAL_BOUNDARY.md).

§2/§3.3 invariant: the psychology profile (latent observations) must NEVER be read
as a CONTROL signal by the router / tone / prompt / intervention layers. It may be
WRITTEN (maybe_update_profile) and READ only by the explicit /profile handler.

A one-off grep on a fixed commit ages and is blind to new modules. This test runs
in CI (the smoke gate runs `pytest`), so a future PR that wires the profile into a
prompt builder / scenario router / tone selector turns CI red immediately — the
cheapest guard against a silent "AI-psychologist" creeping in by accident.

It also turns the same `pipeline()` invariant into a test instead of a code-read.
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Reading the profile (its values are latent psychological observations). WRITES
# (maybe_update_profile) are allowed — only READS-as-control are forbidden here.
PROFILE_READ_SYMBOLS = (
    "get_profile",
    "compute_profile",
    "format_profile_for_user",
    "psychology_profile",   # importing the module at all in a control layer
)

# Layers that route / set tone / build prompts / select interventions / build the
# LLM context. §3.3: none of these may touch the profile. (psychology_profile.py
# itself and database.py define the symbols; bot.py is handled separately below.)
CONTROL_MODULES = [
    "state_engine.py",        # choose_scenario (router) + state
    "prompts.py",             # system prompt / scenario copy / tone
    "humanization.py",        # persona voice / tone
    "memory.py",              # build_context → goes into every prompt
    "practice_registry.py",   # intervention selector
    "readiness_engine.py",
    "stage_detector.py",
    "cognitive_capacity.py",
    "relationship_monitor.py",
    "dependency_monitor.py",
    "silence_engine.py",
    "ab_testing.py",
    "crisis_protocol.py",     # crisis must not be profile-driven either
    "crisis_delivery.py",
]


def test_control_layers_never_read_the_profile():
    offenders = []
    for name in CONTROL_MODULES:
        src = (ROOT / name).read_text(encoding="utf-8")
        for sym in PROFILE_READ_SYMBOLS:
            if sym in src:
                offenders.append(f"{name} → {sym}")
    assert not offenders, (
        "Clinical Boundary §3.3 violation — a control/tone/prompt/intervention "
        "layer reads the psychology profile:\n  " + "\n  ".join(offenders))


def _function_source(module_file: str, func_name: str) -> str | None:
    src = (ROOT / module_file).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return ast.get_source_segment(src, node)
    return None


def test_pipeline_does_not_read_profile_as_control():
    # The per-message path may WRITE the profile (maybe_update_profile) but must
    # not READ it — reading would let latent observations steer the live reply.
    src = _function_source("bot.py", "pipeline")
    assert src is not None, "pipeline() not found in bot.py"
    for sym in ("get_profile", "compute_profile", "format_profile_for_user"):
        assert sym not in src, (
            f"pipeline() reads profile symbol {sym!r} — Clinical Boundary §3.3 "
            "violation (the live reply path must not be profile-driven)")


def test_record_file_present():
    # The governing record must stay in the repo (system-map), not drift to a
    # local-only doc.
    assert (ROOT / "CLINICAL_BOUNDARY.md").exists()
