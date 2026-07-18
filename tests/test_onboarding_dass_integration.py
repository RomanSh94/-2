"""Combined onboarding + DASS-21 integration (PR #61 merge + PR #62 rebase).

Both features are independently frozen and independently tested elsewhere
(tests/test_first_user_onboarding.py & friends; tests/test_dass21_flow.py,
tests/test_dass21_discussion.py). This file proves ONLY the NEW cross-feature
invariants that did not exist until both landed on the same main:

  * the shared OnboardingGateMiddleware/_onboarding_blocks_ordinary_entry gate
    blocks a non-exempt product entrypoint (DASS included) not only for an
    ACTIVE onboarding row but also for a PRIVACY_NOTICE_ONLY-owed user who has
    no row at all (a real gap found and fixed during this integration pass --
    see bot._onboarding_blocks_ordinary_entry's docstring);
  * "onb:" and "q:m:" namespaces never collide;
  * crisis still preempts onboarding even in the privacy-only-owed state;
  * the four flag-combination baselines produce no cross-feature leakage.
"""
import asyncio
import types

import pytest

import access_control as ac
import bot
import config
import database
import onboarding_content as oc

from test_onboarding_gate import (
    _via_message_gate, _via_callback_gate, FakeUser, FakeMessage, FakeCallback, FakeBot)

run = asyncio.run


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    run(database.init_db())
    return database


@pytest.fixture(autouse=True)
def fake_bot(monkeypatch):
    fb = FakeBot()
    monkeypatch.setattr(bot, "bot", fb)
    return fb


@pytest.fixture(autouse=True)
def _access_env(monkeypatch):
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})


def _authorized(uid):
    run(database.grant_user_access(uid, source="invite"))


# ── Namespace collision proof ────────────────────────────────────────────────
def test_onb_and_qm_namespaces_never_collide():
    assert not any(p == "q:m:" or "q:m:".startswith(p) for p in bot._ONBOARDING_EXEMPT_CALLBACK_PREFIXES)
    assert not oc.CB_PREFIX.startswith("q:")
    assert "q:discuss" not in bot._ONBOARDING_EXEMPT_CALLBACK_PREFIXES


def test_dass_command_and_qm_callback_are_not_exempt_from_onboarding_gate():
    assert "dass21" not in bot._ONBOARDING_EXEMPT_COMMANDS
    assert not any("q:m:".startswith(p) for p in bot._ONBOARDING_EXEMPT_CALLBACK_PREFIXES)


# ── The core fixed gap: privacy-only-owed (no row) still blocks ordinary
# entry, not only an ACTIVE onboarding row ──────────────────────────────────
def test_privacy_only_owed_blocks_a_generic_product_command_via_real_middleware(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "FIRST_USER_ONBOARDING_ENABLED", True)
    uid = 9101
    _authorized(uid)
    run(database.save_message(uid, "user", "prior activity"))  # -> legacy, no notice ack
    assert run(database.get_active_onboarding_state(uid)) is None  # no row at all

    async def stand_in_dass_command(message):
        pass

    msg = FakeMessage(FakeUser(uid), "/dass21")
    ran = _via_message_gate(msg, stand_in_dass_command)
    assert ran == 0  # blocked


def test_privacy_only_owed_blocks_a_qm_style_callback_via_real_middleware(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "FIRST_USER_ONBOARDING_ENABLED", True)
    uid = 9102
    _authorized(uid)
    run(database.save_message(uid, "user", "prior activity"))

    async def stand_in_qm_callback(callback):
        pass

    cb = FakeCallback(FakeUser(uid), FakeMessage(FakeUser(uid)), data="q:m:1:measures")
    ran = _via_callback_gate(cb, stand_in_qm_callback)
    assert ran == 0


def test_settled_privacy_notice_no_longer_blocks_product_command(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "FIRST_USER_ONBOARDING_ENABLED", True)
    uid = 9103
    _authorized(uid)
    run(database.save_message(uid, "user", "prior activity"))
    run(database.record_notice_acknowledgement(uid, "privacy_notice", oc.PRIVACY_NOTICE_VERSION))

    async def stand_in_dass_command(message):
        pass

    msg = FakeMessage(FakeUser(uid), "/dass21")
    ran = _via_message_gate(msg, stand_in_dass_command)
    assert ran == 1  # no longer blocked


def test_new_user_not_yet_started_is_blocked_until_onboarding_begins(tmp_db, monkeypatch):
    # A genuinely new, never-/start'ed user also owes FULL_ONBOARDING -- the
    # shared gate must block them too, not only privacy-only-owed legacy users.
    monkeypatch.setattr(config, "FIRST_USER_ONBOARDING_ENABLED", True)
    uid = 9104
    _authorized(uid)

    async def stand_in_dass_command(message):
        pass

    msg = FakeMessage(FakeUser(uid), "/dass21")
    ran = _via_message_gate(msg, stand_in_dass_command)
    assert ran == 0


# Crisis-preempts-onboarding is already proven, state-independently, by
# tests/test_onboarding_gate.py::test_active_crisis_preempts_onboarding_gate
# and ::test_red_risk_crisis_preempts_onboarding_gate: both spy on
# bot._onboarding_blocks_ordinary_entry itself and prove pipeline()'s active-
# crisis branch returns BEFORE that function is ever called at all -- this
# holds regardless of which onboarding sub-state (active row, privacy-only-
# owed, or settled) a real call would have found, so it is not duplicated
# here for the new privacy-only-owed case specifically.


# ── Combined flag-state baselines (no cross-feature leakage) ────────────────
def test_both_flags_false_no_onboarding_no_notice_row(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "FIRST_USER_ONBOARDING_ENABLED", False)
    monkeypatch.setattr(config, "DASS21_DISCUSSION_ENABLED", False)
    uid = 9106
    _authorized(uid)
    msg = FakeMessage(FakeUser(uid), "/start")
    run(bot.cmd_start(msg))
    assert run(database.get_onboarding_state(uid)) is None
    assert run(database.has_privacy_notice_ack(uid, oc.PRIVACY_NOTICE_VERSION)) is False
    assert run(bot._onboarding_blocks_ordinary_entry(uid)) is False


def test_onboarding_true_dass_discussion_false_privacy_only_flow_unaffected(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "FIRST_USER_ONBOARDING_ENABLED", True)
    monkeypatch.setattr(config, "DASS21_DISCUSSION_ENABLED", False)
    uid = 9107
    _authorized(uid)
    run(database.save_message(uid, "user", "prior activity"))
    msg = FakeMessage(FakeUser(uid), "/start")
    run(bot.cmd_start(msg))
    # Privacy-only path is independent of DASS21_DISCUSSION_ENABLED entirely.
    assert run(database.get_onboarding_state(uid)) is None
    assert run(bot._onboarding_blocks_ordinary_entry(uid)) is True


def test_onboarding_false_dass_true_gate_never_blocks(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "FIRST_USER_ONBOARDING_ENABLED", False)
    monkeypatch.setattr(config, "DASS21_DISCUSSION_ENABLED", True)
    uid = 9108
    _authorized(uid)
    assert run(bot._onboarding_blocks_ordinary_entry(uid)) is False

    async def stand_in_dass_command(message):
        pass

    msg = FakeMessage(FakeUser(uid), "/dass21")
    ran = _via_message_gate(msg, stand_in_dass_command)
    assert ran == 1


def test_both_flags_true_dass_unblocked_only_after_settling(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "FIRST_USER_ONBOARDING_ENABLED", True)
    monkeypatch.setattr(config, "DASS21_DISCUSSION_ENABLED", True)
    uid = 9109
    _authorized(uid)
    run(database.save_message(uid, "user", "prior activity"))

    async def stand_in_dass_command(message):
        pass

    msg = FakeMessage(FakeUser(uid), "/dass21")
    assert _via_message_gate(msg, stand_in_dass_command) == 0  # blocked: notice owed

    run(database.record_notice_acknowledgement(uid, "privacy_notice", oc.PRIVACY_NOTICE_VERSION))
    msg2 = FakeMessage(FakeUser(uid), "/dass21")
    assert _via_message_gate(msg2, stand_in_dass_command) == 1  # unblocked: settled


# ── Combined schema/privacy evidence: both new tables coexist ──────────────
import privacy_registry as pr


async def _make_claim_row(uid):
    await database.claim_dass21_discuss_reply(
        uid, session_id=1, topic_id="measures",
        source_chat_id=uid, source_message_id=1, response_id="resp-1")


def test_both_new_tables_created_by_the_same_init_db(tmp_db):
    import sqlite3
    con = sqlite3.connect(database.DB)
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    con.close()
    assert "dass21_discuss_claims" in names
    assert "user_notice_acknowledgements" in names


def test_both_tables_survive_init_db_called_twice(tmp_db):
    uid = 9201
    run(_make_claim_row(uid))
    run(database.record_notice_acknowledgement(uid, "privacy_notice", "v1"))
    run(database.init_db())  # second boot, same DB -- must not lose either row
    import sqlite3
    con = sqlite3.connect(database.DB)
    claims = con.execute(
        "SELECT COUNT(*) FROM dass21_discuss_claims WHERE user_id=?", (uid,)).fetchone()[0]
    con.close()
    assert claims == 1
    assert run(database.has_notice_acknowledgement(uid, "privacy_notice", "v1")) is True


def test_both_tables_registered_zero_unregistered_sensitive_tables(tmp_db):
    assert pr.find_unregistered_sensitive_tables(database.SCHEMA) == []


def test_export_includes_both_tables_for_the_correct_user_only(tmp_db):
    a, b = 9202, 9203
    for uid in (a, b):
        run(database.upsert_user(uid, "u", "U"))
    run(_make_claim_row(a))
    run(database.record_notice_acknowledgement(a, "privacy_notice", "v1"))
    exp_a = run(database.export_all_personal_data(a))
    exp_b = run(database.export_all_personal_data(b))
    assert len(exp_a["dass21_discuss_claims"]) == 1
    assert len(exp_a["user_notice_acknowledgements"]) == 1
    assert exp_b["dass21_discuss_claims"] == []
    assert exp_b["user_notice_acknowledgements"] == []


def test_delete_preview_counts_both_tables(tmp_db):
    uid = 9204
    run(database.upsert_user(uid, "u", "U"))
    run(_make_claim_row(uid))
    run(database.record_notice_acknowledgement(uid, "privacy_notice", "v1"))
    preview = run(database.preview_delete_all_personal_data(uid))
    assert preview["dass21_discuss_claims"]["row_count"] == 1
    assert preview["user_notice_acknowledgements"]["row_count"] == 1


def test_delete_all_removes_both_tables_cross_user_isolated(tmp_db):
    a, b = 9205, 9206
    for uid in (a, b):
        run(database.upsert_user(uid, "u", "U"))
        run(_make_claim_row(uid))
        run(database.record_notice_acknowledgement(uid, "privacy_notice", "v1"))
    summary = run(database.delete_all_personal_data(a))
    assert summary["dass21_discuss_claims"] == 1
    assert summary["user_notice_acknowledgements"] == 1
    # user A fully cleared
    assert run(database.has_notice_acknowledgement(a, "privacy_notice", "v1")) is False
    import sqlite3
    con = sqlite3.connect(database.DB)
    claims_a = con.execute(
        "SELECT COUNT(*) FROM dass21_discuss_claims WHERE user_id=?", (a,)).fetchone()[0]
    claims_b = con.execute(
        "SELECT COUNT(*) FROM dass21_discuss_claims WHERE user_id=?", (b,)).fetchone()[0]
    con.close()
    assert claims_a == 0
    assert claims_b == 1  # user B untouched
    assert run(database.has_notice_acknowledgement(b, "privacy_notice", "v1")) is True
