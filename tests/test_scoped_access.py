"""PR 1B-2 — scoped_access.py: unit tests for the permission contract, plus a
default-deny static scan (same style as A1's find_latent_source_offenders and
privacy_registry's find_unregistered_sensitive_tables) for the two
cross-user-CAPABLE sensitive accessors that take an arbitrary uid argument:
export_all_personal_data / delete_all_personal_data.

Honesty note (explicit, not implied): this scan is a GUARDRAIL against a
*new* unguarded call appearing, not a proof that every existing read path in
the codebase is correctly scoped. PR 1B-2 discovery established that today's
self-read convention in bot.py (every handler derives uid from the Telegram
sender) is correct by inspection, not by anything this scanner checks.

generate_review_pack is deliberately NOT covered by the same "must mention
scoped_access" rule -- its cross-user permission check is enforced INSIDE
review_pack.generate_review_pack itself via access_control.
can_request_review_pack (see tests/test_review_pack_permission.py), not via
this module. Requiring a "scoped_access" mention at every call site would be
a vacuous/misleading check for that symbol specifically.
"""
import asyncio
import pathlib

import pytest

import access_control as ac
import scoped_access
from scoped_access import (
    PURPOSES, can_read_user_data, assert_can_read_user_data, ScopedAccessDenied,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent


# ── permission contract ────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _role_config(monkeypatch):
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", {10})
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", {20, 21})
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {10: [20]})


def test_self_read_allowed_for_self_only_purposes():
    # self_view / privacy_export / privacy_delete: pure uid-equality, for ANY
    # uid regardless of role (including one that resolves to UNKNOWN).
    for purpose in ("self_view", "privacy_export", "privacy_delete"):
        assert can_read_user_data(42, 42, purpose) is True         # UNKNOWN self
        assert can_read_user_data(1, 1, purpose) is True           # OWNER self
        assert can_read_user_data(10, 10, purpose) is True         # TESTER self
        assert can_read_user_data(20, 20, purpose) is True         # REVIEWER self


def test_review_pack_is_not_self_service_uid_equality():
    # review_pack must NEVER be granted merely because requester_uid ==
    # target_uid -- it delegates entirely to can_request_review_pack, which
    # has no general "any uid may request its own pack" rule at all.
    assert can_read_user_data(10, 10, "review_pack") is False   # TESTER -> self: denied
    assert can_read_user_data(999999, 999999, "review_pack") is False  # UNKNOWN -> self: denied
    assert can_read_user_data(21, 21, "review_pack") is False   # unmapped REVIEWER -> self: denied


def test_review_pack_owner_self_allowed_through_can_request_review_pack():
    # This passes NOT because requester==target, but because
    # access_control.can_request_review_pack(1, 1) is True for OWNER.
    assert can_read_user_data(1, 1, "review_pack") is True


def test_review_pack_mapped_reviewer_to_tester_allowed():
    assert can_read_user_data(20, 10, "review_pack") is True
    assert can_read_user_data(20, 10, "privacy_export") is False
    assert can_read_user_data(20, 10, "privacy_delete") is False
    assert can_read_user_data(20, 10, "self_view") is False


def test_review_pack_reviewer_unmapped_tester_denied():
    assert can_read_user_data(21, 10, "review_pack") is False


def test_review_pack_owner_to_tester_denied():
    assert can_read_user_data(1, 10, "review_pack") is False
    assert can_read_user_data(1, 10, "privacy_export") is False
    assert can_read_user_data(1, 10, "privacy_delete") is False


def test_review_pack_reviewer_to_owner_denied():
    assert can_read_user_data(20, 1, "review_pack") is False


def test_resolver_exception_denies(monkeypatch):
    def _boom(uid):
        raise RuntimeError("broken")
    monkeypatch.setattr(ac, "resolve_role", _boom)
    assert can_read_user_data(20, 10, "review_pack") is False


def test_unknown_purpose_asserts_loudly():
    with pytest.raises(AssertionError):
        can_read_user_data(1, 1, "some_typo_purpose")


def test_assert_raises_scoped_access_denied_generic_message():
    with pytest.raises(ScopedAccessDenied) as exc_info:
        assert_can_read_user_data(1, 10, "privacy_export")
    msg = str(exc_info.value)
    # Generic -- no role/mapping/target detail leaked in the denial itself.
    assert "10" not in msg
    assert "CLINICIAN" not in msg


def test_assert_allows_self_silently():
    assert_can_read_user_data(42, 42, "privacy_delete")   # must not raise


# ── static scan: cross-user-capable sensitive accessors ─────────────────────────
CROSS_USER_CAPABLE_SYMBOLS = ("export_all_personal_data(", "delete_all_personal_data(")

_SKIP_DIRS = {"tests", "venv", ".venv", "__pycache__", ".git", ".github"}
# Files that DEFINE these symbols, or the module that itself IS the guard --
# both are expected to reference them without a "scoped_access" mention.
ALLOWED_FILES = {"database.py", "scoped_access.py"}


def find_unguarded_cross_user_reads(root: pathlib.Path = ROOT) -> list[str]:
    """Whole-file scan (same granularity as most of A1's checks): a file
    outside ALLOWED_FILES that calls export_all_personal_data(/
    delete_all_personal_data( without ALSO mentioning "scoped_access"
    anywhere in the file is flagged. Parametrized by root so the same logic
    runs against the real repo and a synthetic tmp_path (positive/negative
    controls) without ever touching the repo tree."""
    offenders = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        if _SKIP_DIRS & set(rel.parts):
            continue
        if path.name in ALLOWED_FILES:
            continue
        src = path.read_text(encoding="utf-8")
        hits = [s for s in CROSS_USER_CAPABLE_SYMBOLS if s in src]
        if hits and "scoped_access" not in src:
            offenders += [f"{path.name} -> {s}" for s in hits]
    return offenders


def test_no_unguarded_cross_user_read_in_repo():
    offenders = find_unguarded_cross_user_reads()
    assert not offenders, (
        "A file calls export_all_personal_data/delete_all_personal_data "
        "without referencing scoped_access anywhere in the file -- route it "
        "through scoped_access.assert_can_read_user_data, or add the file to "
        "ALLOWED_FILES in a reviewable diff:\n  " + "\n  ".join(offenders))


def test_scanner_catches_a_rogue_unguarded_call(tmp_path):
    rogue = tmp_path / "rogue_export.py"
    rogue.write_text(
        "async def leak(uid):\n"
        "    return await database.export_all_personal_data(uid)\n",
        encoding="utf-8")
    offenders = find_unguarded_cross_user_reads(root=tmp_path)
    assert any("rogue_export.py" in o for o in offenders), (
        "positive control failed: scanner did not catch an unguarded "
        "cross-user-capable call in a non-allowlisted file")


def test_scanner_allows_a_call_that_mentions_scoped_access(tmp_path):
    ok = tmp_path / "guarded_export.py"
    ok.write_text(
        "import scoped_access\n"
        "async def export(requester_uid, target_uid):\n"
        "    scoped_access.assert_can_read_user_data(requester_uid, target_uid, 'privacy_export')\n"
        "    return await database.export_all_personal_data(target_uid)\n",
        encoding="utf-8")
    offenders = find_unguarded_cross_user_reads(root=tmp_path)
    assert offenders == []
