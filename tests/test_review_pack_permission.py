"""PR 1B-2 — generate_review_pack's permission gate.

generate_review_pack(target_uid, requester_uid) now requires BOTH arguments
and enforces access_control.can_request_review_pack before touching any data.
These tests exercise the full role matrix from the PR 1B-1 contract, including
the combinations PR 1B-1's own test_access_control.py didn't cover explicitly
(tester-as-requester, unknown-as-requester, reviewer->reviewer).
"""
import asyncio

import pytest

import access_control as ac
import database
import review_pack


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    return database


@pytest.fixture(autouse=True)
def _role_config(monkeypatch):
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", {10})
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", {20, 21})
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {10: [20]})


def _seed(tmp_db, uid):
    asyncio.run(tmp_db.upsert_user(uid, "u", "U", "ru"))


def test_owner_requesting_own_pack_allowed(tmp_db):
    _seed(tmp_db, 1)
    pack = asyncio.run(review_pack.generate_review_pack(1, requester_uid=1))
    assert pack["user_id"] == 1


def test_reviewer_requesting_mapped_tester_pack_allowed(tmp_db):
    _seed(tmp_db, 10)
    pack = asyncio.run(review_pack.generate_review_pack(10, requester_uid=20))
    assert pack["user_id"] == 10


def test_reviewer_requesting_unmapped_tester_denied(tmp_db):
    _seed(tmp_db, 10)
    with pytest.raises(review_pack.ReviewPackNotAllowed):
        asyncio.run(review_pack.generate_review_pack(10, requester_uid=21))


def test_owner_requesting_tester_pack_denied(tmp_db):
    _seed(tmp_db, 10)
    with pytest.raises(review_pack.ReviewPackNotAllowed):
        asyncio.run(review_pack.generate_review_pack(10, requester_uid=1))


def test_reviewer_requesting_owner_pack_denied(tmp_db):
    _seed(tmp_db, 1)
    with pytest.raises(review_pack.ReviewPackNotAllowed):
        asyncio.run(review_pack.generate_review_pack(1, requester_uid=20))


def test_tester_as_requester_denied(tmp_db):
    # Not covered explicitly by PR 1B-1's own test_access_control.py.
    _seed(tmp_db, 1)
    with pytest.raises(review_pack.ReviewPackNotAllowed):
        asyncio.run(review_pack.generate_review_pack(1, requester_uid=10))


def test_unknown_as_requester_denied(tmp_db):
    _seed(tmp_db, 1)
    with pytest.raises(review_pack.ReviewPackNotAllowed):
        asyncio.run(review_pack.generate_review_pack(1, requester_uid=999999))


def test_reviewer_requesting_another_reviewer_pack_denied(tmp_db):
    _seed(tmp_db, 21)
    with pytest.raises(review_pack.ReviewPackNotAllowed):
        asyncio.run(review_pack.generate_review_pack(21, requester_uid=20))


def test_fail_closed_on_resolver_exception(tmp_db, monkeypatch):
    def _boom(uid):
        raise RuntimeError("resolver broke")
    monkeypatch.setattr(ac, "resolve_role", _boom)
    _seed(tmp_db, 1)
    with pytest.raises(review_pack.ReviewPackNotAllowed):
        asyncio.run(review_pack.generate_review_pack(1, requester_uid=1))


def test_denied_request_produces_no_pack_content(tmp_db):
    # The exception itself must carry no target-role/mapping detail.
    _seed(tmp_db, 10)
    with pytest.raises(review_pack.ReviewPackNotAllowed) as exc_info:
        asyncio.run(review_pack.generate_review_pack(10, requester_uid=1))
    msg = str(exc_info.value)
    assert "CLINICIAN_TESTER" not in msg
    assert "10" not in msg
    assert "mapped" not in msg.lower()


def test_requester_uid_is_required_kwarg():
    with pytest.raises(TypeError):
        asyncio.run(review_pack.generate_review_pack(1))
