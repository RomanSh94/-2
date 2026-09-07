"""Canonical Memory Governance V1 (Phase 2B-1) — pure policy tests.

canonical_memory_governance.py performs no I/O of any kind; every test in
this file is synchronous and needs no database, no event loop, and no
fixture beyond the module itself.
"""
import ast
import itertools
import pathlib

import pytest

import canonical_memory_governance as gov
import therapeutic_domain as core

MODULE_PATH = pathlib.Path(__file__).resolve().parent.parent / "canonical_memory_governance.py"

ALL_LIFECYCLES = list(core.MemoryLifecycle)
ALL_CATEGORIES = list(core.MemoryCategory)


# ── Purity / import-discipline (mirrors test_therapeutic_domain_purity.py) ──

ALLOWED_IMPORTS = {"__future__", "therapeutic_domain"}


def _tree() -> ast.Module:
    return ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))


def test_module_exists_and_parses():
    assert MODULE_PATH.is_file(), f"expected {MODULE_PATH} to exist"
    _tree()


def test_module_imports_only_the_pure_allowlist():
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
        f"canonical_memory_governance.py imported {offenders}, outside its "
        f"pure-policy allowlist {sorted(ALLOWED_IMPORTS)}")


def test_module_source_has_no_forbidden_runtime_names():
    src = MODULE_PATH.read_text(encoding="utf-8")
    forbidden = ("aiosqlite", "sqlite3", "openai", "aiogram", "telegram",
                 "requests", "httpx", "aiohttp", "socket", "subprocess",
                 "os.system", "bot.py")
    hits = [name for name in forbidden if name in src]
    assert not hits, f"canonical_memory_governance.py contains forbidden runtime tokens: {hits}"


# ── Candidate-writable category allowlist ───────────────────────────────────

def test_candidate_writable_category_allowlist_is_exact():
    assert gov.GENERIC_CANDIDATE_WRITABLE_CATEGORIES == frozenset({
        core.MemoryCategory.EXPLICIT_FACT,
        core.MemoryCategory.PREFERENCE,
        core.MemoryCategory.GOAL,
        core.MemoryCategory.EPISODE_MAP,
        core.MemoryCategory.HYPOTHESIS,
    })


@pytest.mark.parametrize("category", [
    core.MemoryCategory.EXPLICIT_FACT, core.MemoryCategory.PREFERENCE,
    core.MemoryCategory.GOAL, core.MemoryCategory.EPISODE_MAP,
    core.MemoryCategory.HYPOTHESIS,
])
def test_allowlisted_category_is_writable(category):
    assert gov.is_generic_candidate_writable_category(category) is True


@pytest.mark.parametrize("category", [
    core.MemoryCategory.CONFIRMED_PATTERN, core.MemoryCategory.INTERVENTION,
    core.MemoryCategory.OUTCOME, core.MemoryCategory.PLAN,
])
def test_specialized_category_rejected_by_generic_candidate_policy(category):
    assert gov.is_generic_candidate_writable_category(category) is False


def test_category_writability_check_covers_every_member_exactly():
    """Truth-table equivalence: writable iff in the frozen allowlist, for
    every single MemoryCategory member -- not just the ones named above."""
    for category in ALL_CATEGORIES:
        expected = category in gov.GENERIC_CANDIDATE_WRITABLE_CATEGORIES
        assert gov.is_generic_candidate_writable_category(category) is expected


def test_category_writability_check_fails_closed_on_non_enum():
    with pytest.raises(ValueError):
        gov.is_generic_candidate_writable_category("EXPLICIT_FACT")
    with pytest.raises(ValueError):
        gov.is_generic_candidate_writable_category(None)


# ── Lifecycle transition graph ──────────────────────────────────────────────

def test_all_nine_allowed_edges_pass():
    expected_edges = {
        (core.MemoryLifecycle.CANDIDATE, core.MemoryLifecycle.PROPOSED),
        (core.MemoryLifecycle.CANDIDATE, core.MemoryLifecycle.EXPIRED),
        (core.MemoryLifecycle.PROPOSED, core.MemoryLifecycle.CONFIRMED),
        (core.MemoryLifecycle.PROPOSED, core.MemoryLifecycle.REJECTED),
        (core.MemoryLifecycle.PROPOSED, core.MemoryLifecycle.EXPIRED),
        (core.MemoryLifecycle.CONFIRMED, core.MemoryLifecycle.HISTORICAL),
        (core.MemoryLifecycle.CONFIRMED, core.MemoryLifecycle.REJECTED),
        (core.MemoryLifecycle.CORRECTED, core.MemoryLifecycle.HISTORICAL),
        (core.MemoryLifecycle.CORRECTED, core.MemoryLifecycle.REJECTED),
    }
    assert gov.ALLOWED_LIFECYCLE_TRANSITIONS == expected_edges
    assert len(expected_edges) == 9
    for current, target in expected_edges:
        assert gov.is_allowed_lifecycle_transition(current, target) is True


@pytest.mark.parametrize("current,target", [
    (core.MemoryLifecycle.CANDIDATE, core.MemoryLifecycle.CONFIRMED),
    (core.MemoryLifecycle.CANDIDATE, core.MemoryLifecycle.CORRECTED),
    (core.MemoryLifecycle.PROPOSED, core.MemoryLifecycle.CORRECTED),
])
def test_specifically_forbidden_edges_named_in_the_spec(current, target):
    assert gov.is_allowed_lifecycle_transition(current, target) is False


def test_candidate_to_confirmed_fails():
    assert gov.is_allowed_lifecycle_transition(
        core.MemoryLifecycle.CANDIDATE, core.MemoryLifecycle.CONFIRMED) is False


@pytest.mark.parametrize("terminal", [
    core.MemoryLifecycle.HISTORICAL, core.MemoryLifecycle.REJECTED,
    core.MemoryLifecycle.EXPIRED,
])
def test_terminal_lifecycles_cannot_resurrect_to_any_target(terminal):
    assert terminal in gov.TERMINAL_LIFECYCLES
    for target in ALL_LIFECYCLES:
        assert gov.is_allowed_lifecycle_transition(terminal, target) is False


def test_terminal_lifecycles_set_is_exact():
    assert gov.TERMINAL_LIFECYCLES == frozenset({
        core.MemoryLifecycle.HISTORICAL, core.MemoryLifecycle.REJECTED,
        core.MemoryLifecycle.EXPIRED,
    })


def test_no_arbitrary_same_state_transition():
    for lifecycle in ALL_LIFECYCLES:
        assert gov.is_allowed_lifecycle_transition(lifecycle, lifecycle) is False


def test_transition_graph_is_exact_truth_table_over_every_pair():
    """Strongest possible proof: for the full cross-product of MemoryLifecycle
    x MemoryLifecycle, is_allowed_lifecycle_transition agrees exactly with
    membership in ALLOWED_LIFECYCLE_TRANSITIONS -- not just for the pairs
    named explicitly elsewhere in this file."""
    for current, target in itertools.product(ALL_LIFECYCLES, ALL_LIFECYCLES):
        expected = (current, target) in gov.ALLOWED_LIFECYCLE_TRANSITIONS
        assert gov.is_allowed_lifecycle_transition(current, target) is expected


def test_corrected_is_never_a_transition_target():
    for current, target in gov.ALLOWED_LIFECYCLE_TRANSITIONS:
        assert target is not core.MemoryLifecycle.CORRECTED


def test_transition_check_fails_closed_on_non_enum():
    with pytest.raises(ValueError):
        gov.is_allowed_lifecycle_transition("CANDIDATE", core.MemoryLifecycle.PROPOSED)
    with pytest.raises(ValueError):
        gov.is_allowed_lifecycle_transition(core.MemoryLifecycle.CANDIDATE, "PROPOSED")


# ── Raw candidate content boundary ──────────────────────────────────────────

def test_valid_content_passes():
    assert gov.validate_new_candidate_content("a normal fact") is None


def test_content_non_str_rejected():
    with pytest.raises(ValueError):
        gov.validate_new_candidate_content(12345)


def test_content_empty_rejected():
    with pytest.raises(ValueError):
        gov.validate_new_candidate_content("")


def test_content_whitespace_only_rejected():
    with pytest.raises(ValueError):
        gov.validate_new_candidate_content("   ")


def test_content_needing_strip_rejected():
    with pytest.raises(ValueError):
        gov.validate_new_candidate_content("  leading and trailing  ")
    with pytest.raises(ValueError):
        gov.validate_new_candidate_content("trailing newline\n")


def test_content_exactly_1000_chars_accepted():
    content = "x" * 1000
    assert gov.validate_new_candidate_content(content) is None


def test_content_1001_chars_rejected_not_truncated():
    with pytest.raises(ValueError):
        gov.validate_new_candidate_content("x" * 1001)


# ── Raw candidate provenance shape ──────────────────────────────────────────

def test_valid_provenance_shape_passes():
    assert gov.validate_new_candidate_provenance_shape([1, 2, 3]) is None


def test_provenance_non_list_rejected():
    with pytest.raises(ValueError):
        gov.validate_new_candidate_provenance_shape((1, 2))
    with pytest.raises(ValueError):
        gov.validate_new_candidate_provenance_shape({1, 2})
    with pytest.raises(ValueError):
        gov.validate_new_candidate_provenance_shape(None)


def test_provenance_empty_rejected():
    with pytest.raises(ValueError):
        gov.validate_new_candidate_provenance_shape([])


def test_provenance_non_int_member_rejected():
    with pytest.raises(ValueError):
        gov.validate_new_candidate_provenance_shape([1, "2"])
    with pytest.raises(ValueError):
        gov.validate_new_candidate_provenance_shape([1.5])


def test_provenance_bool_member_rejected():
    with pytest.raises(ValueError):
        gov.validate_new_candidate_provenance_shape([1, True])


def test_provenance_zero_or_negative_member_rejected():
    with pytest.raises(ValueError):
        gov.validate_new_candidate_provenance_shape([0])
    with pytest.raises(ValueError):
        gov.validate_new_candidate_provenance_shape([-1])


def test_provenance_duplicate_member_rejected():
    with pytest.raises(ValueError):
        gov.validate_new_candidate_provenance_shape([5, 5])
