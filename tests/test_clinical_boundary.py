"""Executable lock for the Clinical Boundary Decision Record (CLINICAL_BOUNDARY.md).

§2/§3.3 invariant: the psychology profile (latent observations) must NEVER be read
as a CONTROL signal by the router / tone / prompt / intervention layers. It may be
WRITTEN (maybe_update_profile) and READ only where it is surfaced to a human
(the /profile handler, the admin dashboard).

DEFAULT-DENY by design: scanning a hardcoded list of "control modules" ages exactly
like a grep — add `response_builder.py` / `tone_v2.py` in six months, wire the
profile in, and a fixed list never notices. So this scans the WHOLE package and
allows the profile only in a narrow allowlist. Making a new module read the profile
then requires an explicit, reviewable edit to PROFILE_ALLOWED — that's both the
audit trail and continuous coverage. CI (the smoke gate runs pytest) keeps it live.
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Reading the profile (its values are latent psychological observations). WRITES
# (maybe_update_profile) are allowed everywhere — only READS-as-control are barred.
# Substring match, fails-closed: also catches sync_get_profile etc.
PROFILE_READ_SYMBOLS = (
    "get_profile",
    "compute_profile",
    "format_profile_for_user",
    "psychology_profile",
)

# The ONLY files allowed to touch the profile. Each entry is a deliberate decision:
#   psychology_profile.py — defines compute_profile / format_profile_for_user (owner)
#   database.py           — defines get_profile / sync_get_profile / save_profile
#   bot.py                — the /profile handler (cmd_profile); its per-message hot
#                           path is separately AST-locked below (read barred there)
#   dashboard.py          — admin-facing display (separate thread); shows profile to
#                           the operator, never steers the user's reply
# A future user-facing reader (e.g. specialist_summary.py) must be ADDED here in a
# reviewable diff — that is the point.
PROFILE_ALLOWED = {"psychology_profile.py", "database.py", "bot.py", "dashboard.py"}

_SKIP_DIRS = {"tests", "venv", ".venv", "__pycache__", ".git", ".github"}


def test_no_control_layer_reads_the_profile():
    offenders = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if _SKIP_DIRS & set(rel.parts):
            continue
        if path.name in PROFILE_ALLOWED:
            continue
        if path.name.startswith("_"):
            continue  # gitignored local probes (_*.py) — not in the CI checkout
        src = path.read_text(encoding="utf-8")
        offenders += [f"{path.name} → {s}" for s in PROFILE_READ_SYMBOLS if s in src]
    assert not offenders, (
        "Clinical Boundary §3.3 — a non-allowlisted module reads the psychology "
        "profile. If it genuinely should, add it to PROFILE_ALLOWED in a reviewable "
        "diff (e.g. a future specialist_summary):\n  " + "\n  ".join(offenders))


def _function_source(module_file: str, func_name: str) -> str | None:
    src = (ROOT / module_file).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return ast.get_source_segment(src, node)
    return None


def test_pipeline_does_not_read_profile_as_control():
    # The per-message path may WRITE the profile (maybe_update_profile) but must not
    # READ it — reading would let latent observations steer the live reply.
    src = _function_source("bot.py", "pipeline")
    assert src is not None, "pipeline() not found in bot.py"
    for sym in ("get_profile", "compute_profile", "format_profile_for_user"):
        assert sym not in src, (
            f"pipeline() reads profile symbol {sym!r} — Clinical Boundary §3.3 "
            "violation (the live reply path must not be profile-driven)")


def test_botpy_profile_reads_only_in_profile_handler():
    # bot.py is wholesale-allowlisted above, so guard it at function granularity:
    # profile READS may appear ONLY in cmd_profile (the /profile display handler),
    # never in any other handler (a new one could otherwise steer tone silently).
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    reads = ("get_profile", "compute_profile", "format_profile_for_user")
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name != "cmd_profile":
            seg = ast.get_source_segment(src, node) or ""
            offenders += [f"{node.name} → {s}" for s in reads if s in seg]
    assert not offenders, (
        "Clinical Boundary §3.3 — a bot.py handler other than cmd_profile reads the "
        "profile:\n  " + "\n  ".join(offenders))


def test_record_file_present():
    # The governing record must stay in the repo (system-map), not drift local-only.
    assert (ROOT / "CLINICAL_BOUNDARY.md").exists()
