"""Executable lock for the Clinical Boundary Decision Record (CLINICAL_BOUNDARY.md).

INVARIANT (A1, v1 personal-use — supersedes the old "profile never influences"):
a latent profile / pattern / questionnaire-result / schema-theme / mode /
formulation MAY influence the reply, but ONLY through `traced_response`, and ONLY
when the influence is content-fully traced and the trace is persisted BEFORE the
reply is sent (fail-closed). Two enforcement layers:

  1. STATIC (no silent path): latent-source read symbols may appear only in the
     narrow allowlist — the definition modules and the sanctioned `traced_response`
     path. Any other module reading them → red CI. Same default-deny mechanism as
     before, now permitting the traced path instead of forbidding all influence.

  2. BEHAVIORAL (the trace is real, not a form): the builder refuses an empty /
     placeholder trace when a real source drove the reply (bidirectional), and it
     fails closed — a persist failure blocks the latent reply rather than sending
     it untraced.
"""
import ast
import asyncio
import pathlib

import pytest

from traced_response import (
    traced_response_builder, Influence, TraceIntegrityError, content_ful,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent


# ── layer 1: static no-silent-path (default-deny over the whole package) ──────
# Registry of latent-source READ symbols, keyed by source. ANY of these appearing
# in a non-allowlisted module = a silent latent path -> red CI. This is default-deny
# for EVERY latent source, not just the profile: when a future source is built
# (questionnaire scoring, pattern detector, schema/mode engine, formulation...),
# register its concrete accessor/table symbol here in a reviewable diff. `mode` is
# NOT scanned as the bare word "mode" (too generic, would false-positive everywhere)
# -- only its concrete accessor names are registered once they exist.
LATENT_SOURCE_SYMBOLS = {
    "profile":             ("get_profile", "compute_profile",
                            "format_profile_for_user", "psychology_profile"),
    "pattern_hypothesis":  ("pattern_hypothes",),          # matches -is and -es
    "questionnaire_score": ("questionnaire_score",),
    "confirmed_episode":   ("confirmed_episode", "pattern_confirmation"),
    "schema_theme":        ("schema_theme",),
    "mode":                ("get_active_mode", "get_mode_profile", "get_schema_modes"),
    "formulation":         ("formulation",),
}
_ALL_LATENT_SYMBOLS = tuple(s for syms in LATENT_SOURCE_SYMBOLS.values() for s in syms)

# Back-compat subset used by the profile-specific AST guards below.
PROFILE_READ_SYMBOLS = LATENT_SOURCE_SYMBOLS["profile"]

# The ONLY files allowed to touch ANY latent source at the file-scan level.
# `traced_response.py` is the sanctioned latent path (A1); the others define/write
# the sources or (bot.py) are further AST-locked per-function below, because A1
# covers more than the profile and bot.py must not get a blanket pass for the rest.
LATENT_ALLOWED_FILES = {
    "psychology_profile.py", "database.py", "bot.py", "dashboard.py",
    "traced_response.py",
    # PR 1A additions — reviewed: neither file READS a latent source as a control
    # signal; both trip the substring scan on inert metadata/placeholders only.
    "privacy_registry.py",  # holds TABLE NAME string constants (e.g.
                            # "user_psychology_profile") for export/delete policy
                            # bookkeeping — never reads profile VALUES.
    "review_pack.py",       # the review-pack shell has an empty `pattern_hypotheses`
                            # placeholder KEY for a future PR; no pattern data source
                            # exists yet and none is read here.
}
_SKIP_DIRS = {"tests", "venv", ".venv", "__pycache__", ".git", ".github"}


def find_latent_source_offenders(root: pathlib.Path = ROOT) -> list[str]:
    """Scan `root` for .py files (outside LATENT_ALLOWED_FILES / skip-dirs /
    underscore-prefixed probes) that contain a latent-source read symbol.
    Parametrized by root so the SAME scan logic can run against the real repo
    (the actual guard) and against a synthetic tmp_path (the positive-control
    test) without ever touching the repository tree."""
    offenders = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        if _SKIP_DIRS & set(rel.parts):
            continue
        if path.name in LATENT_ALLOWED_FILES or path.name.startswith("_"):
            continue
        src = path.read_text(encoding="utf-8")
        offenders += [f"{path.name} -> {s}" for s in _ALL_LATENT_SYMBOLS if s in src]
    return offenders


def test_no_control_layer_reads_a_latent_source():
    offenders = find_latent_source_offenders()
    assert not offenders, (
        "Clinical Boundary A1 -- a non-allowlisted module reads a latent source "
        "outside the traced path. Route it through traced_response, or add the "
        "file to LATENT_ALLOWED_FILES / register the symbol, in a reviewable "
        "diff:\n  " + "\n  ".join(offenders))


def test_scanner_catches_a_rogue_latent_read(tmp_path):
    # Positive control (committed, runs in CI — not a manual bash probe): proves the
    # default-deny guard actually enforces something rather than trivially passing.
    # Uses a synthetic tmp_path directory — never touches the repo tree, so there is
    # no cleanup risk and no chance of leftover files polluting git status/diff-scope.
    rogue = tmp_path / "rogue_latent_probe.py"
    rogue.write_text("from database import pattern_hypothesis_lookup\n", encoding="utf-8")
    offenders = find_latent_source_offenders(root=tmp_path)
    assert any("rogue_latent_probe.py" in o for o in offenders), (
        "positive control failed: scanner did not catch a rogue latent-source read "
        "in a non-allowlisted module — the default-deny guard is not actually "
        "enforcing anything"
    )


def test_scanner_allows_a_file_in_the_allowlist(tmp_path):
    # Complementary negative control: an allowlisted filename with the SAME latent
    # read is NOT flagged — proves the allowlist path of the scanner also works,
    # not just "everything is always an offender".
    ok = tmp_path / "database.py"
    ok.write_text("from database import pattern_hypothesis_lookup\n", encoding="utf-8")
    offenders = find_latent_source_offenders(root=tmp_path)
    assert offenders == []


def _function_source(module_file: str, func_name: str) -> str | None:
    src = (ROOT / module_file).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return ast.get_source_segment(src, node)
    return None


def test_pipeline_does_not_read_any_latent_source_as_control():
    # The per-message hot path may WRITE a latent source but must never READ one
    # directly -- that would let it steer the live reply outside traced_response.
    src = _function_source("bot.py", "pipeline")
    assert src is not None, "pipeline() not found in bot.py"
    for sym in _ALL_LATENT_SYMBOLS:
        assert sym not in src, f"pipeline() reads latent symbol {sym!r} outside the traced path"


# bot.py is wholesale-allowlisted above (it hosts cmd_profile AND, eventually, the
# traced entry points), so it is guarded at FUNCTION granularity for every latent
# source, not just the profile. Only cmd_profile (the explicit /profile display
# handler) may read a latent source directly; any other handler must go through
# traced_response.
_BOTPY_LATENT_READ_ALLOWED_FUNCTIONS = {"cmd_profile"}


def test_botpy_latent_reads_only_in_allowed_handlers():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    offenders = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                node.name not in _BOTPY_LATENT_READ_ALLOWED_FUNCTIONS:
            seg = ast.get_source_segment(src, node) or ""
            offenders += [f"{node.name} -> {s}" for s in _ALL_LATENT_SYMBOLS if s in seg]
    assert not offenders, (
        "Clinical Boundary A1 -- a bot.py handler outside "
        f"{_BOTPY_LATENT_READ_ALLOWED_FUNCTIONS} reads a latent source directly:\n  "
        + "\n  ".join(offenders))


def test_record_file_present():
    assert (ROOT / "CLINICAL_BOUNDARY.md").exists()


# ── layer 2: behavioral — the trace is content-ful and fail-closed ────────────
class _Recorder:
    def __init__(self):
        self.order = []          # sequence of events, to check persist-before-send
        self.persisted = None    # (rid, uid, rows)
        self.sent = []           # latent replies delivered
        self.fallbacks = 0       # non-latent fallbacks delivered

    def make(self, *, persist_raises=False):
        rec = self

        async def persist(rid, uid, rows):
            rec.order.append("persist")
            if persist_raises:
                raise RuntimeError("DB down")
            rec.persisted = (rid, uid, rows)

        async def build_response():
            rec.order.append("build")
            return "LATENT REPLY"

        async def send(text):
            rec.order.append("send")
            rec.sent.append(text)

        async def neutral_fallback():
            rec.order.append("fallback")
            rec.fallbacks += 1

        return persist, build_response, send, neutral_fallback


def _real_influence():
    return [Influence("pattern_hypothesis", "pattern_42",
                      "reply drew on pattern_hypothesis pattern_42")]


def _run(influences, *, persist_raises=False):
    rec = _Recorder()
    persist, build, send, fb = rec.make(persist_raises=persist_raises)
    rid = asyncio.run(traced_response_builder(
        user_id=1, influences=influences, build_response=build, send=send,
        persist_trace=persist, neutral_fallback=fb))
    return rid, rec


def test_trace_persisted_before_send():
    rid, rec = _run(_real_influence())
    assert rid is not None
    assert rec.order == ["persist", "build", "send"]     # trace FIRST
    assert rec.sent == ["LATENT REPLY"]


def test_trace_records_the_real_source():
    # bidirectional: the persisted trace names the actual source, not a placeholder.
    rid, rec = _run(_real_influence())
    _, _, rows = rec.persisted
    assert rows == [("pattern_hypothesis", "pattern_42",
                     "reply drew on pattern_hypothesis pattern_42")]
    assert "pattern_42" in rows[0][2]


def test_empty_influence_is_rejected_nothing_sent():
    with pytest.raises(TraceIntegrityError):
        _run([])
    # and no latent reply was delivered
    rid, rec = None, _Recorder()
    persist, build, send, fb = rec.make()
    with pytest.raises(TraceIntegrityError):
        asyncio.run(traced_response_builder(
            user_id=1, influences=[], build_response=build, send=send,
            persist_trace=persist, neutral_fallback=fb))
    assert rec.sent == [] and rec.order == []            # never even persisted


def test_placeholder_trace_with_real_influence_is_rejected():
    # A builder that has a real source but writes a formal/empty trace → red.
    formal = [Influence("pattern_hypothesis", "pattern_42", "influence: none")]
    assert content_ful(formal) is False
    with pytest.raises(TraceIntegrityError):
        _run(formal)
    # also: human_readable that doesn't name the source is not content-ful
    vague = [Influence("pattern_hypothesis", "pattern_42", "reply used a pattern")]
    assert content_ful(vague) is False


def test_fail_closed_persist_failure_blocks_the_latent_reply():
    rid, rec = _run(_real_influence(), persist_raises=True)
    assert rid is None                                   # failed closed
    assert "send" not in rec.order and rec.sent == []    # latent reply NOT sent
    assert "build" not in rec.order                      # latent context never used
    assert rec.fallbacks == 1                            # non-latent fallback instead
