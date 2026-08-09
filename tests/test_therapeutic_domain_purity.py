"""Regression guard for therapeutic_domain.py's Clinical Boundary A1 allowlist
entry (tests/test_clinical_boundary.py's LATENT_ALLOWED_FILES). That entry is
safe only as long as the file stays PURE domain vocabulary: dataclasses and
enums, nothing else. This test parses the file's AST (not just its imports —
also rejects code that could give it I/O by other means) so a future PR that
tries to add database access, an LLM call, Telegram delivery, or any other
runtime behavior to this specific file fails CI immediately, instead of
silently inheriting an allowlist exemption that was only ever reviewed for a
pure-vocabulary module.
"""
import ast
import pathlib

MODULE_PATH = pathlib.Path(__file__).resolve().parent.parent / "therapeutic_domain.py"

# The complete, closed set of modules therapeutic_domain.py may import. Any
# addition to this set is itself a reviewable diff — that review is the whole
# point (§3 of the Phase 1 corrections: "prefer ... a narrow AST rule proving
# that only declarations are exempt").
ALLOWED_IMPORTS = {"__future__", "dataclasses", "enum", "typing"}

# Names that would immediately signal runtime/I/O behavior if ever bound or
# called in this file, even without a matching import (e.g. via a dynamically
# constructed getattr chain). Belt-and-suspenders on top of the import check.
FORBIDDEN_NAME_SUBSTRINGS = (
    "aiosqlite", "sqlite3", "openai", "aiogram", "telegram", "requests",
    "httpx", "aiohttp", "socket", "subprocess", "os.system",
)


def _tree() -> ast.Module:
    return ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))


def test_domain_module_exists_and_parses():
    assert MODULE_PATH.is_file(), f"expected {MODULE_PATH} to exist"
    _tree()  # raises SyntaxError if malformed


def test_domain_module_imports_only_the_pure_allowlist():
    tree = _tree()
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders += [a.name.split(".")[0] for a in node.names
                         if a.name.split(".")[0] not in ALLOWED_IMPORTS]
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in ALLOWED_IMPORTS:
                offenders.append(root)
    assert not offenders, (
        f"therapeutic_domain.py imported {offenders}, outside its pure-domain "
        f"allowlist {sorted(ALLOWED_IMPORTS)} — this file sits on Clinical "
        f"Boundary A1's LATENT_ALLOWED_FILES on the promise it stays pure "
        f"vocabulary; move runtime/I/O code to a differently-named module.")


def test_domain_module_defines_no_functions_only_classes_and_constants():
    """No free function is defined at module scope. A pure-vocabulary module
    needs dataclasses, enums, and small pure helpers bound as module-level
    `def` are still allowed (e.g. as_enum/_clip) as long as they don't touch
    any forbidden name -- checked separately below -- but this test at least
    proves no class smuggles in I/O-shaped dunder methods like __enter__/
    __aenter__ that would suggest a context-managed resource."""
    tree = _tree()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            forbidden_dunders = {"__enter__", "__exit__", "__aenter__", "__aexit__"}
            defined = {n.name for n in node.body if isinstance(n, ast.FunctionDef)}
            assert not (defined & forbidden_dunders), (
                f"class {node.name} defines {defined & forbidden_dunders} — "
                f"context-manager methods suggest a real I/O resource, not "
                f"pure domain vocabulary")


def test_domain_module_source_has_no_forbidden_runtime_names():
    src = MODULE_PATH.read_text(encoding="utf-8")
    hits = [name for name in FORBIDDEN_NAME_SUBSTRINGS if name in src]
    assert not hits, f"therapeutic_domain.py contains forbidden runtime tokens: {hits}"
