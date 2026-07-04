"""PR 1B-2 — /privacy_export_all, /privacy_delete_all, /forget_all wiring.

All three are self-service and must work regardless of ordinary product
access (UNKNOWN / unmapped tester / blocked user can still use them) -- none
call ensure_full_access_or_closed_test. Tested against the REAL registry-
driven database.export_all_personal_data / delete_all_personal_data (a real
tmp sqlite DB, not mocks), since the whole point is proving the actual data
movement, not just that some function got called.
"""
import asyncio
import types

import pytest

import bot
import database
import access_control as ac


class FakeUser:
    def __init__(self, uid, username="user", first="U"):
        self.id = uid
        self.username = username
        self.first_name = first


class FakeMessage:
    def __init__(self, user, text=""):
        self.from_user = user
        self.text = text
        self.chat = types.SimpleNamespace(id=user.id)
        self.answers = []       # list of (text, kwargs)
        self.documents = []     # list of (BufferedInputFile, kwargs)

    async def answer(self, text, **kw):
        self.answers.append((text, kw))

    async def answer_document(self, doc, **kw):
        self.documents.append((doc, kw))


class FakeCallback:
    def __init__(self, user, message, data=""):
        self.from_user = user
        self.message = message
        self.data = data

    async def answer(self, *a, **kw):
        pass


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
    # UNKNOWN/blocked-by-default config: nobody is OWNER/TESTER/REVIEWER
    # unless a specific test opts in, proving privacy commands work anyway.
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "controlled_clinical_test")
    monkeypatch.setattr(ac, "OWNER_USER_ID", None)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))


def _decode_doc_json(doc):
    import json
    return json.loads(doc.data.decode("utf-8"))


# ── /privacy_export_all ─────────────────────────────────────────────────────────
def test_export_self_service_works_for_fully_unknown_uid(tmp_db):
    async def seed():
        await tmp_db.upsert_user(424242, "u", "U", "ru")
        await tmp_db.save_message(424242, "user", "hello", "open_chat", "ru", 0, [])
    asyncio.run(seed())

    user = FakeUser(424242)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_privacy_export_all(msg))

    assert msg.documents, "export should have produced a file even for an UNKNOWN uid"
    data = _decode_doc_json(msg.documents[0][0])
    assert any(row.get("content") == "hello" for row in data["messages"])


def test_export_has_no_data_message_when_nothing_exists(tmp_db):
    user = FakeUser(999999)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_privacy_export_all(msg))
    assert msg.documents == []
    assert msg.answers and "нет" in msg.answers[0][0].lower()


def test_export_caption_labels_retained_categories(tmp_db):
    async def seed():
        await tmp_db.upsert_user(1, "u", "U", "ru")
        await tmp_db.log_crisis_event(1, "critical", 100, ["suicide"], "text", "ru")
    asyncio.run(seed())

    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_privacy_export_all(msg))

    assert msg.documents
    caption = msg.documents[0][1]["caption"]
    assert "crisis_events" in caption
    assert "/privacy_delete_all" in caption or "/forget_all" in caption


def test_export_only_ever_reads_the_callers_own_uid(tmp_db):
    # No target-uid argument exists anywhere in the command -- the only uid
    # ever touched is message.from_user.id. Prove it by seeding two users and
    # confirming requester A's export never contains requester B's content.
    async def seed():
        await tmp_db.upsert_user(1, "a", "A", "ru")
        await tmp_db.upsert_user(2, "b", "B", "ru")
        await tmp_db.save_message(1, "user", "from A", "open_chat", "ru", 0, [])
        await tmp_db.save_message(2, "user", "from B", "open_chat", "ru", 0, [])
    asyncio.run(seed())

    msg = FakeMessage(FakeUser(1))
    asyncio.run(bot.cmd_privacy_export_all(msg))
    data = _decode_doc_json(msg.documents[0][0])
    contents = [row.get("content") for row in data["messages"]]
    assert "from A" in contents
    assert "from B" not in contents


# ── /privacy_delete_all ─────────────────────────────────────────────────────────
def test_delete_preview_shown_before_any_deletion(tmp_db):
    async def seed():
        await tmp_db.upsert_user(1, "a", "A", "ru")
        await tmp_db.save_message(1, "user", "hi", "open_chat", "ru", 0, [])
    asyncio.run(seed())

    msg = FakeMessage(FakeUser(1))
    asyncio.run(bot.cmd_privacy_delete_all(msg))

    assert msg.answers
    text, kw = msg.answers[0]
    assert "продолжить" in text.lower() or "continue" in text.lower()
    assert kw["reply_markup"] is not None
    # Nothing deleted yet.
    rows = asyncio.run(tmp_db.export_all_personal_data(1))
    assert rows["messages"]


def test_delete_preview_reflects_real_row_counts_not_static_text(tmp_db):
    # PR 1B-2 round 2, blocker 3: the preview must come from
    # preview_delete_all_personal_data, not a fixed string -- prove it by
    # seeding a specific number of rows and finding that count in the text.
    async def seed():
        await tmp_db.upsert_user(1, "a", "A", "ru")
        for i in range(4):
            await tmp_db.save_message(1, "user", f"msg {i}", "open_chat", "ru", 0, [])
    asyncio.run(seed())

    msg = FakeMessage(FakeUser(1))
    asyncio.run(bot.cmd_privacy_delete_all(msg))
    text = msg.answers[0][0]
    # 4 seeded messages + the 1 ANONYMIZE `users` row upsert_user created = 5.
    assert "5" in text

    # Seed a user with a DIFFERENT row count and confirm the preview differs.
    async def seed_other():
        await tmp_db.upsert_user(2, "b", "B", "ru")
        await tmp_db.save_message(2, "user", "only one", "open_chat", "ru", 0, [])
    asyncio.run(seed_other())
    msg2 = FakeMessage(FakeUser(2))
    asyncio.run(bot.cmd_privacy_delete_all(msg2))
    assert msg2.answers[0][0] != text   # different real counts -> different text


def test_forget_all_preview_also_uses_real_counts(tmp_db):
    async def seed():
        await tmp_db.upsert_user(1, "a", "A", "ru")
        await tmp_db.log_crisis_event(1, "critical", 100, ["suicide"], "text", "ru")
    asyncio.run(seed())

    msg = FakeMessage(FakeUser(1))
    asyncio.run(bot.cmd_forget_all(msg))
    text = msg.answers[0][0]
    assert "crisis_events" in text
    assert "1" in text   # the one retained crisis_events row


def test_delete_confirm_owner_does_not_touch_tester(tmp_db):
    async def seed():
        await tmp_db.upsert_user(1, "owner", "O", "ru")
        await tmp_db.upsert_user(10, "tester", "T", "ru")
        await tmp_db.save_message(1, "user", "owner msg", "open_chat", "ru", 0, [])
        await tmp_db.save_message(10, "user", "tester msg", "open_chat", "ru", 0, [])
    asyncio.run(seed())

    owner = FakeUser(1)
    msg = FakeMessage(owner)
    cb = FakeCallback(owner, msg, data="privacy_delete:yes:1")
    asyncio.run(bot.cb_privacy_delete(cb))

    owner_rows = asyncio.run(tmp_db.export_all_personal_data(1))
    tester_rows = asyncio.run(tmp_db.export_all_personal_data(10))
    assert owner_rows["messages"] == []          # owner's own data erased
    assert len(tester_rows["messages"]) == 1     # tester untouched


def test_delete_confirm_tester_does_not_touch_owner(tmp_db):
    async def seed():
        await tmp_db.upsert_user(1, "owner", "O", "ru")
        await tmp_db.upsert_user(10, "tester", "T", "ru")
        await tmp_db.save_message(1, "user", "owner msg", "open_chat", "ru", 0, [])
        await tmp_db.save_message(10, "user", "tester msg", "open_chat", "ru", 0, [])
    asyncio.run(seed())

    tester = FakeUser(10)
    msg = FakeMessage(tester)
    cb = FakeCallback(tester, msg, data="privacy_delete:yes:10")
    asyncio.run(bot.cb_privacy_delete(cb))

    owner_rows = asyncio.run(tmp_db.export_all_personal_data(1))
    tester_rows = asyncio.run(tmp_db.export_all_personal_data(10))
    assert len(owner_rows["messages"]) == 1
    assert tester_rows["messages"] == []


def test_delete_retains_crisis_events_and_says_so(tmp_db):
    async def seed():
        await tmp_db.upsert_user(1, "u", "U", "ru")
        await tmp_db.log_crisis_event(1, "critical", 100, ["suicide"], "text", "ru")
        await tmp_db.save_message(1, "user", "hi", "open_chat", "ru", 0, [])
    asyncio.run(seed())

    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="privacy_delete:yes:1")
    asyncio.run(bot.cb_privacy_delete(cb))

    rows = asyncio.run(tmp_db.export_all_personal_data(1))
    assert len(rows["crisis_events"]) == 1        # retained
    assert rows["messages"] == []                 # cascade-deleted

    final_text = msg.answers[-1][0]
    assert "все данные удалены" not in final_text.lower()
    assert "crisis_events" in final_text


def test_delete_confirm_callback_uid_mismatch_rejected(tmp_db):
    async def seed():
        await tmp_db.upsert_user(1, "victim", "V", "ru")
        await tmp_db.save_message(1, "user", "hi", "open_chat", "ru", 0, [])
    asyncio.run(seed())

    attacker = FakeUser(999)   # NOT the uid embedded in the callback_data
    msg = FakeMessage(attacker)
    cb = FakeCallback(attacker, msg, data="privacy_delete:yes:1")
    asyncio.run(bot.cb_privacy_delete(cb))

    rows = asyncio.run(tmp_db.export_all_personal_data(1))
    assert rows["messages"], "the mismatched callback must not have deleted victim's data"
    assert msg.answers == []   # pure no-op, nothing sent either


def test_delete_cancel_does_not_delete(tmp_db):
    async def seed():
        await tmp_db.upsert_user(1, "u", "U", "ru")
        await tmp_db.save_message(1, "user", "hi", "open_chat", "ru", 0, [])
    asyncio.run(seed())

    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="privacy_delete:no:1")
    asyncio.run(bot.cb_privacy_delete(cb))

    rows = asyncio.run(tmp_db.export_all_personal_data(1))
    assert rows["messages"]


# ── /forget_all is now the same registry-driven flow ────────────────────────────
def test_forget_all_calls_registry_driven_delete_not_old_partial(tmp_db):
    assert not hasattr(database, "forget_all"), \
        "database.forget_all should have been deleted entirely, not kept around"

    async def seed():
        await tmp_db.upsert_user(1, "u", "U", "ru")
        await tmp_db.log_crisis_event(1, "critical", 100, ["suicide"], "text", "ru")
        await tmp_db.save_cbt_entry(1, {"situation": "s", "emotion": "e"})
    asyncio.run(seed())

    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="forget:yes:1")
    asyncio.run(bot.cb_forget(cb))

    rows = asyncio.run(tmp_db.export_all_personal_data(1))
    assert rows["cbt_journal_entries"] == []      # registry-driven -> journals ALSO wiped
    assert len(rows["crisis_events"]) == 1        # still retained


def test_forget_all_self_service_bypasses_product_gate(tmp_db):
    # DEPLOYMENT_MODE=controlled_clinical_test, uid is fully UNKNOWN -- would
    # be blocked by ensure_full_access_or_closed_test for any product command.
    async def seed():
        await tmp_db.upsert_user(424242, "u", "U", "ru")
        await tmp_db.save_message(424242, "user", "hi", "open_chat", "ru", 0, [])
    asyncio.run(seed())

    user = FakeUser(424242)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_forget_all(msg))
    assert msg.answers   # got the real preview, not a closed-test message
    assert "закрыт" not in msg.answers[0][0].lower()  # not the closed-test wording


# ── PR 1B-2 round 2, blocker 2: destructive delete callbacks fail CLOSED on
# any malformed callback_data -- no legacy 2-part "forget:yes"/
# "privacy_delete:yes" is accepted anymore. These negative tests deliberately
# USE those legacy-shaped strings to prove they do NOT delete -- they are not
# being deleted themselves just to satisfy a grep for the old string.
def test_forget_all_legacy_missing_uid_does_not_delete(tmp_db):
    async def seed():
        await tmp_db.upsert_user(1, "u", "U", "ru")
        await tmp_db.save_message(1, "user", "hi", "open_chat", "ru", 0, [])
    asyncio.run(seed())

    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="forget:yes")   # legacy, no uid segment at all
    asyncio.run(bot.cb_forget(cb))

    rows = asyncio.run(tmp_db.export_all_personal_data(1))
    assert rows["messages"], "missing embedded uid must not delete anything"
    assert msg.answers == []


def test_privacy_delete_legacy_missing_uid_does_not_delete(tmp_db):
    async def seed():
        await tmp_db.upsert_user(1, "u", "U", "ru")
        await tmp_db.save_message(1, "user", "hi", "open_chat", "ru", 0, [])
    asyncio.run(seed())

    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="privacy_delete:yes")   # legacy, no uid segment
    asyncio.run(bot.cb_privacy_delete(cb))

    rows = asyncio.run(tmp_db.export_all_personal_data(1))
    assert rows["messages"], "missing embedded uid must not delete anything"
    assert msg.answers == []


def test_forget_all_malformed_uid_does_not_delete(tmp_db):
    async def seed():
        await tmp_db.upsert_user(1, "u", "U", "ru")
        await tmp_db.save_message(1, "user", "hi", "open_chat", "ru", 0, [])
    asyncio.run(seed())

    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="forget:yes:abc")   # non-numeric uid segment
    asyncio.run(bot.cb_forget(cb))

    rows = asyncio.run(tmp_db.export_all_personal_data(1))
    assert rows["messages"], "malformed embedded uid must not delete anything"
    assert msg.answers == []


def test_forget_all_mismatched_uid_does_not_delete(tmp_db):
    async def seed():
        await tmp_db.upsert_user(1, "victim", "V", "ru")
        await tmp_db.save_message(1, "user", "hi", "open_chat", "ru", 0, [])
    asyncio.run(seed())

    attacker = FakeUser(999)
    msg = FakeMessage(attacker)
    cb = FakeCallback(attacker, msg, data="forget:yes:1")   # embedded uid != presser
    asyncio.run(bot.cb_forget(cb))

    rows = asyncio.run(tmp_db.export_all_personal_data(1))
    assert rows["messages"], "mismatched embedded uid must not delete victim's data"
    assert msg.answers == []


def test_forget_all_matching_uid_deletes(tmp_db):
    async def seed():
        await tmp_db.upsert_user(1, "u", "U", "ru")
        await tmp_db.save_message(1, "user", "hi", "open_chat", "ru", 0, [])
    asyncio.run(seed())

    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="forget:yes:1")   # correctly embedded, matches presser
    asyncio.run(bot.cb_forget(cb))

    rows = asyncio.run(tmp_db.export_all_personal_data(1))
    assert rows["messages"] == []
    assert msg.answers
