"""Phase 2B-1 V2 correction -- static regression guard for the Governed
Canonical Memory Persistence Boundary.

database.py's _add_core_memory_item_unchecked and
_update_core_memory_item_lifecycle_unchecked are generic mutation
primitives that bypass ALL Phase 2B-1 governance: unrestricted category,
unrestricted initial lifecycle (including CONFIRMED/CORRECTED directly),
unrestricted lifecycle transitions, no provenance check, no idempotency.
They exist only as private, internal building blocks -- add_governed_core_
memory_candidate and apply_governed_core_memory_lifecycle_transition are
the only SUPPORTED production write paths.

Python's leading-underscore convention alone does not stop a future PR
from importing/calling a private primitive by name. This is the same
default-deny, whole-repository static scan already used by
tests/test_clinical_boundary.py for a different (but structurally
identical) "silent bypass" risk -- a non-allowlisted, non-test module that
references either private name fails CI immediately, instead of relying on
a docstring warning or a code-review habit.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The two private, generic mutation primitives this guard protects. Kept as
# a tuple (not hard-coded twice) so the scanner and any future maintenance
# note stay in sync.
UNCHECKED_MUTATION_PRIMITIVES = (
    "_add_core_memory_item_unchecked",
    "_update_core_memory_item_lifecycle_unchecked",
)

# database.py is where these primitives are DEFINED and legitimately
# referenced (by their own docstrings, by the module-level section comment,
# and — this is the point of the whole correction — NOT by any actual call
# from the governed functions, which reimplement the same atomic SQL
# themselves). No other production module belongs on this list; if a
# genuinely new, reviewed internal caller is ever needed, it must be added
# here in a reviewable diff, not silently.
#
# REPOSITORY-RELATIVE, not basename (Phase 2B-1 V4 correction): this must
# match the exact repo-relative path (as POSIX, e.g. "database.py" for the
# root module), never a bare filename -- a bare-basename comparison would
# wrongly allowlist ANY nested file merely sharing that name (e.g. a
# hypothetical service/database.py), which would silently reopen the exact
# bypass this whole guard exists to close. See
# test_scanner_rejects_a_nested_file_that_merely_shares_the_allowed_name
# below for the regression proof.
ALLOWED_RELATIVE_PATHS = {"database.py"}

# Test files are explicitly permitted to use the unchecked primitives as
# fixture/setup mechanisms (see individual test docstrings for why --
# typically to construct a row state the governed API would itself refuse
# to create or reach, in order to test that the governed API correctly
# refuses to touch it). Skip the whole tests/ tree, plus the usual
# non-source directories.
_SKIP_DIRS = {"tests", "venv", ".venv", "__pycache__", ".git", ".github"}


def find_bypass_offenders(root: pathlib.Path = ROOT) -> list[str]:
    """Scan `root` for .py files (outside ALLOWED_RELATIVE_PATHS / skip-dirs)
    that reference either unchecked mutation primitive by name. Parametrized
    by root so the SAME scan logic can run against the real repo (the actual
    guard) and against a synthetic tmp_path (the positive/negative control
    tests below) without ever touching the repository tree.

    The allowlist check is against the REPO-RELATIVE path (rel.as_posix()),
    never the bare basename (path.name) -- see ALLOWED_RELATIVE_PATHS's own
    comment above for why a basename-only check would be a real bypass.
    Offenders are also reported by relative path (not basename) so a nested
    offender is unambiguous rather than looking identical to a root-level
    one with the same filename."""
    offenders = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        if _SKIP_DIRS & set(rel.parts):
            continue
        if rel.as_posix() in ALLOWED_RELATIVE_PATHS:
            continue
        src = path.read_text(encoding="utf-8")
        offenders += [f"{rel.as_posix()} -> {name}" for name in UNCHECKED_MUTATION_PRIMITIVES
                     if name in src]
    return offenders


def test_no_production_module_references_the_unchecked_mutation_primitives():
    offenders = find_bypass_offenders()
    assert not offenders, (
        "Phase 2B-1 bypass closure -- a non-allowlisted, non-test module "
        "references a private, unchecked canonical-memory mutation "
        "primitive directly, bypassing governed candidate creation / "
        "governed lifecycle transitions. Use add_governed_core_memory_"
        "candidate / apply_governed_core_memory_lifecycle_transition "
        "instead, or add the file's exact relative path to "
        "ALLOWED_RELATIVE_PATHS in a reviewable diff "
        "if a new internal database.py-level caller is genuinely needed:\n  "
        + "\n  ".join(offenders))


def test_scanner_catches_a_rogue_bypass_reference(tmp_path):
    # Positive control (committed, runs in CI -- not a manual bash probe):
    # proves the default-deny guard actually enforces something rather than
    # trivially passing. Synthetic tmp_path directory only -- never touches
    # the repo tree.
    rogue = tmp_path / "rogue_memory_writer.py"
    rogue.write_text(
        "from database import _add_core_memory_item_unchecked\n", encoding="utf-8")
    offenders = find_bypass_offenders(root=tmp_path)
    assert any("rogue_memory_writer.py" in o for o in offenders), (
        "positive control failed: scanner did not catch a rogue reference "
        "to the unchecked mutation primitive in a non-allowlisted module -- "
        "the default-deny guard is not actually enforcing anything")


def test_scanner_catches_the_other_rogue_bypass_reference(tmp_path):
    rogue = tmp_path / "another_rogue_writer.py"
    rogue.write_text(
        "db._update_core_memory_item_lifecycle_unchecked(1, 2, lifecycle)\n",
        encoding="utf-8")
    offenders = find_bypass_offenders(root=tmp_path)
    assert any("another_rogue_writer.py" in o for o in offenders)


def test_scanner_allows_database_py_itself(tmp_path):
    # Complementary negative control: a file literally named database.py
    # (the one allowlisted file) with the SAME reference is NOT flagged --
    # proves the allowlist path of the scanner also works, not just
    # "everything is always an offender".
    ok = tmp_path / "database.py"
    ok.write_text(
        "async def _add_core_memory_item_unchecked(): pass\n", encoding="utf-8")
    offenders = find_bypass_offenders(root=tmp_path)
    assert offenders == []


def test_scanner_rejects_a_nested_file_that_merely_shares_the_allowed_name(tmp_path):
    """Phase 2B-1 V4 correction regression proof: the allowlist is
    repo-RELATIVE-PATH exact, never basename-only. A nested file that
    merely shares the literal filename "database.py" (e.g.
    service/database.py) must NOT be silently allowlisted -- only the
    genuine repository-root database.py is exempt. Before this
    correction, `path.name in ALLOWED_FILES` would have wrongly let a
    nested same-name file reference either unchecked primitive with
    impunity, reopening exactly the bypass this whole guard exists to
    close. This is the required companion to
    test_scanner_allows_database_py_itself above: root database.py ->
    allowed, service/database.py -> rejected."""
    service_dir = tmp_path / "service"
    service_dir.mkdir()
    nested = service_dir / "database.py"
    nested.write_text(
        "from database import _add_core_memory_item_unchecked\n", encoding="utf-8")
    offenders = find_bypass_offenders(root=tmp_path)
    assert any("service/database.py" in o for o in offenders), (
        "scanner failed to catch a nested file that merely shares the "
        "allowed 'database.py' basename -- the allowlist is not "
        "repo-relative-path exact, which is exactly the reported defect "
        f"this test exists to prevent. offenders={offenders!r}")
    assert not any(o.startswith("database.py ->") for o in offenders), (
        "nested offender must be reported by its own relative path "
        "(service/database.py), not misattributed to the real root "
        "database.py")


def test_scanner_skips_the_tests_directory(tmp_path):
    (tmp_path / "tests").mkdir()
    rogue_test = tmp_path / "tests" / "test_uses_the_fixture_primitive.py"
    rogue_test.write_text(
        "from database import _add_core_memory_item_unchecked\n", encoding="utf-8")
    offenders = find_bypass_offenders(root=tmp_path)
    assert offenders == []


def test_both_unchecked_primitives_actually_exist_in_database_module():
    """Sanity check that this guard is protecting real, currently-defined
    names -- not two typo'd strings that would make the whole scanner an
    inert no-op."""
    import database
    for name in UNCHECKED_MUTATION_PRIMITIVES:
        assert hasattr(database, name), (
            f"database.{name} does not exist -- this bypass-closure guard "
            "is protecting a name that isn't even defined")


def test_governed_public_api_still_exists_and_is_not_itself_flagged():
    """The governed replacements must exist and must NOT trip this scanner
    when database.py references its OWN private primitives in prose/
    docstrings (only actual identifier occurrences matter to the plain
    substring scan, and database.py is allowlisted for exactly that
    reason)."""
    import database
    assert hasattr(database, "add_governed_core_memory_candidate")
    assert hasattr(database, "apply_governed_core_memory_lifecycle_transition")
    offenders = find_bypass_offenders()
    assert not any(o.startswith("database.py ->") for o in offenders)
