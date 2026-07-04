"""PR 1B-2 — /review_pack <target_uid> command wiring in bot.py.

Permission-matrix correctness itself lives in tests/test_review_pack_permission.py
(against review_pack.generate_review_pack directly). These tests are about the
TELEGRAM COMMAND layer: usage parsing, generic denial text, successful
delivery as a file, and that it is NOT gated by ensure_full_access_or_closed_test
(a CLINICIAN_REVIEWER has zero ordinary product access but must still be able
to use this for a mapped tester).
"""
import asyncio
import json
import types

import pytest

import bot
import database
import access_control as ac


class FakeUser:
    def __init__(self, uid, username="user"):
        self.id = uid
        self.username = username


class FakeMessage:
    def __init__(self, user, text=""):
        self.from_user = user
        self.text = text
        self.chat = types.SimpleNamespace(id=user.id)
        self.answers = []
        self.documents = []

    async def answer(self, text, **kw):
        self.answers.append((text, kw))

    async def answer_document(self, doc, **kw):
        self.documents.append((doc, kw))


def _async(value=None):
    async def _f(*a, **kw):
        return value
    return _f


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
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))


def test_usage_message_when_no_argument(tmp_db):
    msg = FakeMessage(FakeUser(20), text="/review_pack")
    asyncio.run(bot.cmd_review_pack(msg))
    assert msg.documents == []
    assert "Использование" in msg.answers[0][0] or "Usage" in msg.answers[0][0]


def test_usage_message_when_argument_not_numeric(tmp_db):
    msg = FakeMessage(FakeUser(20), text="/review_pack abc")
    asyncio.run(bot.cmd_review_pack(msg))
    assert msg.documents == []


def test_mapped_reviewer_gets_pack_file(tmp_db):
    asyncio.run(tmp_db.upsert_user(10, "tester", "T", "ru"))
    msg = FakeMessage(FakeUser(20), text="/review_pack 10")
    asyncio.run(bot.cmd_review_pack(msg))
    assert msg.documents
    doc, kw = msg.documents[0]
    pack = json.loads(doc.data.decode("utf-8"))
    assert pack["user_id"] == 10


def test_unmapped_reviewer_gets_generic_denial_no_detail(tmp_db):
    asyncio.run(tmp_db.upsert_user(10, "tester", "T", "ru"))
    msg = FakeMessage(FakeUser(21), text="/review_pack 10")   # 21 not mapped to 10
    asyncio.run(bot.cmd_review_pack(msg))
    assert msg.documents == []
    text = msg.answers[0][0]
    assert "10" not in text
    assert "TESTER" not in text.upper() or "CLINICIAN" not in text


def test_owner_requesting_tester_pack_denied_generic(tmp_db):
    asyncio.run(tmp_db.upsert_user(10, "tester", "T", "ru"))
    msg = FakeMessage(FakeUser(1), text="/review_pack 10")
    asyncio.run(bot.cmd_review_pack(msg))
    assert msg.documents == []


def test_unknown_requester_denied(tmp_db):
    msg = FakeMessage(FakeUser(555555), text="/review_pack 10")
    asyncio.run(bot.cmd_review_pack(msg))
    assert msg.documents == []


def test_reviewer_with_zero_product_access_can_still_use_review_pack(tmp_db, monkeypatch):
    # Prove this command is NOT gated by ensure_full_access_or_closed_test:
    # has_full_access(20) is False for a CLINICIAN_REVIEWER (by design, per
    # PR 1B-1), yet the command must still work via can_request_review_pack.
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "controlled_clinical_test")
    has_access = asyncio.run(ac.has_full_access(20))
    assert has_access is False   # sanity: reviewer indeed has no ordinary product access

    asyncio.run(tmp_db.upsert_user(10, "tester", "T", "ru"))
    msg = FakeMessage(FakeUser(20), text="/review_pack 10")
    asyncio.run(bot.cmd_review_pack(msg))
    assert msg.documents   # worked anyway
