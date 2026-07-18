"""First-user onboarding — privacy governance + privacy-copy truthfulness.

Proves the onboarding-state table is under the same registry-driven export /
delete governance as every other user-scoped table, and that the screen-5 copy
makes only claims the system actually supports (spec item F: never claims the
user read/opened a Privacy Policy that isn't actually linked anywhere, and the
PRIVACY_POLICY_URL env value is validated to a safe http(s) URL or dropped).
"""
import asyncio

import pytest

import config
import database
import onboarding_content as oc
import privacy_registry as pr


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    return database


# ── Registry governance ───────────────────────────────────────────────────────
def test_onboarding_state_registered_in_privacy_registry():
    assert "user_onboarding_state" in pr.PRIVACY_REGISTRY
    e = pr.PRIVACY_REGISTRY["user_onboarding_state"]
    assert e.user_id_column == "user_id"
    assert e.category == "CONSENT"
    assert e.export_policy == "INCLUDE"
    # Onboarding metadata is NOT a safety-audit record -> removed on delete-all.
    assert e.delete_policy == "CASCADE_DELETE"
    # Default-deny scanner must see it as already registered (no gap).
    assert "user_onboarding_state" not in pr.find_unregistered_sensitive_tables(database.SCHEMA)


def test_onboarding_metadata_included_in_export(tmp_db):
    uid = 4242
    asyncio.run(tmp_db.upsert_user(uid, "u", "U"))
    asyncio.run(tmp_db.start_or_get_onboarding(uid, oc.ONBOARDING_VERSION))
    exp = asyncio.run(tmp_db.export_all_personal_data(uid))
    assert "user_onboarding_state" in exp
    assert len(exp["user_onboarding_state"]) == 1
    row = exp["user_onboarding_state"][0]
    assert row["user_id"] == uid
    assert row["onboarding_version"] == oc.ONBOARDING_VERSION


def test_onboarding_metadata_removed_on_delete_all(tmp_db):
    uid = 4243
    asyncio.run(tmp_db.upsert_user(uid, "u", "U"))
    asyncio.run(tmp_db.start_or_get_onboarding(uid, oc.ONBOARDING_VERSION))
    assert asyncio.run(tmp_db.get_onboarding_state(uid)) is not None

    summary = asyncio.run(tmp_db.delete_all_personal_data(uid))
    assert summary["user_onboarding_state"] == 1            # a row was deleted
    assert asyncio.run(tmp_db.get_onboarding_state(uid)) is None
    # No RETAIN of onboarding metadata past delete-all.
    assert "RETAINED" not in str(summary["user_onboarding_state"])


def test_delete_preview_covers_onboarding_state(tmp_db):
    uid = 4244
    asyncio.run(tmp_db.upsert_user(uid, "u", "U"))
    asyncio.run(tmp_db.start_or_get_onboarding(uid, oc.ONBOARDING_VERSION))
    preview = asyncio.run(tmp_db.preview_delete_all_personal_data(uid))
    assert "user_onboarding_state" in preview
    assert preview["user_onboarding_state"]["policy"] == "CASCADE_DELETE"
    assert preview["user_onboarding_state"]["row_count"] == 1


def test_delete_all_removes_only_current_user_onboarding(tmp_db):
    a, b = 5001, 5002
    for uid in (a, b):
        asyncio.run(tmp_db.upsert_user(uid, "u", "U"))
        asyncio.run(tmp_db.start_or_get_onboarding(uid, oc.ONBOARDING_VERSION))
    asyncio.run(tmp_db.delete_all_personal_data(a))
    assert asyncio.run(tmp_db.get_onboarding_state(a)) is None
    assert asyncio.run(tmp_db.get_onboarding_state(b)) is not None


# ── Privacy-copy truthfulness (screen 5) ──────────────────────────────────────
@pytest.mark.parametrize("lang", ["ru", "en"])
def test_privacy_copy_states_history_is_stored(lang):
    c = oc.caption(5, lang).lower()
    if lang == "ru":
        assert "сохраняет историю" in c
    else:
        assert "stores the history" in c


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_privacy_copy_admits_ai_provider_processing(lang):
    c = oc.caption(5, lang).lower()
    if lang == "ru":
        assert "ai-провайдер" in c
    else:
        assert "ai provider" in c


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_privacy_copy_makes_no_forbidden_claims(lang):
    c = oc.caption(5, lang).lower()
    # No end-to-end encryption claim.
    assert "end-to-end" not in c and "сквозное шифрование" not in c
    # No "only 100 messages stored" claim.
    assert "100" not in c
    # No "no third party ever processes data" claim — we DO disclose a processor.
    assert "no third party" not in c
    # No GDPR-compliance or medical-confidentiality claim in the notice copy.
    assert "gdpr" not in c
    assert "medical confidentiality" not in c and "врачебная тайна" not in c


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_privacy_copy_mentions_view_export_delete(lang):
    c = oc.caption(5, lang).lower()
    if lang == "ru":
        assert "экспортировать" in c and "удалять" in c
    else:
        assert "export" in c and "delete" in c


# ── In-bot privacy summary (fallback when no PRIVACY_POLICY_URL) ──────────────
@pytest.mark.parametrize("lang", ["ru", "en"])
def test_privacy_summary_points_at_real_commands_no_invented_url(lang):
    s = oc.privacy_summary(lang)
    assert "/privacy_export_all" in s
    assert "/privacy_delete_all" in s
    # Never an invented public URL.
    assert "http://" not in s and "https://" not in s


# ── F: acknowledgment wording must not overclaim a policy the user never saw ──
@pytest.mark.parametrize("lang", ["ru", "en"])
def test_no_url_final_line_acknowledges_notice_only_not_policy(lang):
    c = oc.caption(5, lang, "").lower()  # no PRIVACY_POLICY_URL configured
    if lang == "ru":
        assert "уведомлением." in c or "уведомлением" in c.split("нажимая")[-1]
        # Must NOT claim the Policy was read when nothing links to it.
        assert "и политикой конфиденциальности" not in c
    else:
        assert "read this notice." in c
        assert "and the privacy policy" not in c


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_real_url_final_line_acknowledges_notice_and_policy(lang):
    c = oc.caption(5, lang, "https://example.org/privacy").lower()
    if lang == "ru":
        assert "и политикой конфиденциальности" in c
    else:
        assert "and the privacy policy" in c


# ── F: two distinct labels — "Privacy Policy" only for a REAL url= button ─────
def test_privacy_button_label_differs_by_url_presence():
    no_url = oc.button_spec(5, "en")
    labeled_cb = [b["text"] for row in no_url for b in row if b.get("cb") == oc.CB_PRIVACY]
    assert labeled_cb == ["About data and privacy"]
    assert "Privacy Policy" not in [b["text"] for row in no_url for b in row]

    with_url = oc.button_spec(5, "ru", privacy_policy_url="https://example.org/privacy")
    labeled_url = [b["text"] for row in with_url for b in row if b.get("url")]
    assert labeled_url == ["Политика конфиденциальности"]


# ── J: unapproved organizational claims removed entirely (not reframed) ──────
# Until the owner explicitly approves it, onboarding copy must not assert
# "we do not sell data" / "we do not use data for advertising" in ANY framing
# (a bare claim OR a "per our policy" / "по нашей политике" wrapper) -- only
# technically verified statements remain: history is stored, the AI provider
# may process text, export/delete tools exist, safety-audit data may have
# disclosed retention exceptions.
@pytest.mark.parametrize("lang", ["ru", "en"])
def test_no_data_sale_or_advertising_claim_anywhere_in_privacy_copy(lang):
    for text in (oc.caption(5, lang, "").lower(),
                oc.caption(5, lang, "https://example.org/privacy").lower(),
                oc.privacy_summary(lang).lower()):
        if lang == "ru":
            assert "не продаём" not in text
            assert "рекламы" not in text
            assert "по нашей политике" not in text
        else:
            assert "do not sell" not in text
            assert "advertising" not in text
            assert "per our policy" not in text


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_privacy_copy_keeps_only_technically_verified_statements(lang):
    c = oc.caption(5, lang).lower()
    if lang == "ru":
        assert "сохраняет историю" in c            # conversation history is stored
        assert "ai-провайдер" in c                  # AI provider may process text
        assert "экспортировать" in c and "удалять" in c  # export/delete tools exist
    else:
        assert "stores the history" in c
        assert "ai provider" in c
        assert "export" in c and "delete" in c
    s = oc.privacy_summary(lang).lower()
    if lang == "ru":
        assert "безопасности" in s  # safety-audit retention exception disclosed
    else:
        assert "safety policy" in s


# ── F: PRIVACY_POLICY_URL validation (only http/https + non-empty host) ──────
@pytest.mark.parametrize("bad", [
    "",
    "not a url",
    "javascript:alert(1)",
    "ftp://example.org/privacy",
    "http://",              # scheme with no host
    "https:///no-host",
    "example.org/privacy",  # no scheme at all
])
def test_malformed_privacy_policy_url_falls_back_to_empty(bad):
    assert config._validate_privacy_policy_url(bad) == ""


@pytest.mark.parametrize("good", [
    "https://example.org/privacy",
    "http://example.org/privacy",
    "https://sub.example.org:8443/legal/privacy?x=1",
])
def test_valid_privacy_policy_url_is_kept(good):
    assert config._validate_privacy_policy_url(good) == good


# ── Truthful, centralized onboarding rollout policy (spec item F correction) ─
# "NEW_USERS_ONLY" is the only value ever implemented: full onboarding goes
# ONLY to genuinely new users (bot.cmd_start via database.get_onboarding_eligibility);
# every other user is independently, mandatorily re-checked for the current
# privacy notice (database.has_privacy_notice_ack). The previous name
# "MANDATORY_ALL" was rejected because it never actually forced returning
# users through full onboarding.
def test_default_rollout_policy_is_new_users_only():
    assert config.ONBOARDING_ROLLOUT_POLICY == "NEW_USERS_ONLY"


def test_rollout_policy_validator_accepts_the_one_supported_value():
    assert config._validate_rollout_policy("NEW_USERS_ONLY") == "NEW_USERS_ONLY"


@pytest.mark.parametrize("bad", ["MANDATORY_ALL", "", "new_users_only", "ALL", "garbage"])
def test_rollout_policy_validator_rejects_unknown_values(bad):
    with pytest.raises(ValueError):
        config._validate_rollout_policy(bad)


# ── Independent privacy-notice acknowledgement (spec item F correction) ──────
# has_privacy_notice_ack answers "has this user EVER acknowledged this exact
# notice version, in ANY onboarding_version row" -- independent of which row
# (if any) currently represents onboarding-content completion/exemption.
# Expressed directly against the DB layer (fast; no Telegram fakes needed for
# a pure data-layer decision).
def test_new_user_has_no_ack(tmp_db):
    assert asyncio.run(database.has_privacy_notice_ack(9001, oc.PRIVACY_NOTICE_VERSION)) is False


def _start_active_at_last_step(uid: int, version: str) -> None:
    """Test helper: reach an ACTIVE row at LAST_STEP using only production
    functions (start_or_get_onboarding no longer accepts a start_step kwarg --
    the real PRIVACY_NOTICE_ONLY flow never creates an onboarding row at all,
    see bot.cmd_start / onboarding_content.determine_onboarding_requirement)."""
    asyncio.run(database.start_or_get_onboarding(uid, version))
    asyncio.run(database.skip_onboarding_to_privacy(uid, version, oc.LAST_STEP))


def test_current_version_completed_with_current_notice_is_acked(tmp_db):
    uid = 9002
    _start_active_at_last_step(uid, oc.ONBOARDING_VERSION)
    assert asyncio.run(database.complete_onboarding(
        uid, oc.ONBOARDING_VERSION, oc.LAST_STEP, oc.PRIVACY_NOTICE_VERSION)) is True
    assert asyncio.run(database.has_privacy_notice_ack(uid, oc.PRIVACY_NOTICE_VERSION)) is True


def test_old_version_completed_with_current_notice_still_counts(tmp_db):
    # The notice check is INDEPENDENT of which onboarding_version row carries
    # it -- a user who acknowledged the CURRENT notice version under an OLDER
    # onboarding_version (a content-only bump, same legal notice) must not be
    # re-prompted.
    uid = 9003
    old_version = "v0"
    _start_active_at_last_step(uid, old_version)
    assert asyncio.run(database.complete_onboarding(
        uid, old_version, oc.LAST_STEP, oc.PRIVACY_NOTICE_VERSION)) is True
    assert asyncio.run(database.has_privacy_notice_ack(uid, oc.PRIVACY_NOTICE_VERSION)) is True


def test_old_notice_version_acked_does_not_satisfy_current(tmp_db):
    uid = 9004
    _start_active_at_last_step(uid, oc.ONBOARDING_VERSION)
    assert asyncio.run(database.complete_onboarding(
        uid, oc.ONBOARDING_VERSION, oc.LAST_STEP, "an-older-notice-version")) is True
    assert asyncio.run(database.has_privacy_notice_ack(uid, oc.PRIVACY_NOTICE_VERSION)) is False


def test_legacy_exempt_row_has_null_privacy_fields_not_acked(tmp_db):
    uid = 9005
    asyncio.run(database.mark_onboarding_legacy_exempt(uid, oc.ONBOARDING_VERSION))
    st = asyncio.run(database.get_onboarding_state(uid, oc.ONBOARDING_VERSION))
    assert st["status"] == "legacy_exempt"
    assert st["privacy_notice_acknowledged_at"] is None
    assert asyncio.run(database.has_privacy_notice_ack(uid, oc.PRIVACY_NOTICE_VERSION)) is False


def test_active_incomplete_row_is_not_acked(tmp_db):
    uid = 9006
    asyncio.run(database.start_or_get_onboarding(uid, oc.ONBOARDING_VERSION))
    assert asyncio.run(database.has_privacy_notice_ack(uid, oc.PRIVACY_NOTICE_VERSION)) is False


def test_superseded_row_carries_no_ack_either(tmp_db):
    uid = 9007
    asyncio.run(database.start_or_get_onboarding(uid, "v_old"))
    assert asyncio.run(database.supersede_onboarding_version(uid, "v_old")) is True
    assert asyncio.run(database.has_privacy_notice_ack(uid, oc.PRIVACY_NOTICE_VERSION)) is False


def test_cross_user_ack_does_not_leak(tmp_db):
    uid_a, uid_b = 9009, 9010
    _start_active_at_last_step(uid_a, oc.ONBOARDING_VERSION)
    asyncio.run(database.complete_onboarding(
        uid_a, oc.ONBOARDING_VERSION, oc.LAST_STEP, oc.PRIVACY_NOTICE_VERSION))
    assert asyncio.run(database.has_privacy_notice_ack(uid_a, oc.PRIVACY_NOTICE_VERSION)) is True
    assert asyncio.run(database.has_privacy_notice_ack(uid_b, oc.PRIVACY_NOTICE_VERSION)) is False


# ── Independent notice-acknowledgement table (spec item F correction) ───────
# Directly against the new user_notice_acknowledgements table -- these prove
# the acknowledgement mechanism works standalone, with NO onboarding row
# involved at all (the real PRIVACY_NOTICE_ONLY flow's actual mechanism).
def test_record_and_check_notice_acknowledgement_standalone(tmp_db):
    uid = 9011
    assert asyncio.run(database.has_notice_acknowledgement(uid, "privacy_notice", "v1")) is False
    inserted = asyncio.run(database.record_notice_acknowledgement(uid, "privacy_notice", "v1"))
    assert inserted is True
    assert asyncio.run(database.has_notice_acknowledgement(uid, "privacy_notice", "v1")) is True
    assert asyncio.run(database.get_onboarding_state(uid)) is None  # no row created


def test_record_notice_acknowledgement_double_tap_returns_false(tmp_db):
    uid = 9012
    assert asyncio.run(database.record_notice_acknowledgement(uid, "privacy_notice", "v1")) is True
    assert asyncio.run(database.record_notice_acknowledgement(uid, "privacy_notice", "v1")) is False


def test_notice_acknowledgement_scoped_by_version(tmp_db):
    uid = 9013
    asyncio.run(database.record_notice_acknowledgement(uid, "privacy_notice", "v1"))
    assert asyncio.run(database.has_notice_acknowledgement(uid, "privacy_notice", "v2")) is False


def test_notice_acknowledgement_scoped_by_notice_id(tmp_db):
    uid = 9014
    asyncio.run(database.record_notice_acknowledgement(uid, "privacy_notice", "v1"))
    assert asyncio.run(database.has_notice_acknowledgement(uid, "some_other_notice", "v1")) is False


def test_notice_acknowledgement_registered_in_privacy_registry():
    assert "user_notice_acknowledgements" in pr.PRIVACY_REGISTRY
    e = pr.PRIVACY_REGISTRY["user_notice_acknowledgements"]
    assert e.user_id_column == "user_id"
    assert e.category == "CONSENT"
    assert e.export_policy == "INCLUDE"
    assert e.delete_policy == "CASCADE_DELETE"
    assert "user_notice_acknowledgements" not in pr.find_unregistered_sensitive_tables(database.SCHEMA)


def test_notice_acknowledgement_included_in_export(tmp_db):
    uid = 9015
    asyncio.run(tmp_db.upsert_user(uid, "u", "U"))
    asyncio.run(database.record_notice_acknowledgement(uid, "privacy_notice", "v1"))
    exp = asyncio.run(tmp_db.export_all_personal_data(uid))
    assert "user_notice_acknowledgements" in exp
    assert len(exp["user_notice_acknowledgements"]) == 1
    assert exp["user_notice_acknowledgements"][0]["notice_version"] == "v1"


def test_notice_acknowledgement_removed_on_delete_all(tmp_db):
    uid = 9016
    asyncio.run(tmp_db.upsert_user(uid, "u", "U"))
    asyncio.run(database.record_notice_acknowledgement(uid, "privacy_notice", "v1"))
    summary = asyncio.run(tmp_db.delete_all_personal_data(uid))
    assert summary["user_notice_acknowledgements"] == 1
    assert asyncio.run(database.has_notice_acknowledgement(uid, "privacy_notice", "v1")) is False


def test_notice_acknowledgement_delete_all_does_not_affect_other_user(tmp_db):
    a, b = 9017, 9018
    for uid in (a, b):
        asyncio.run(tmp_db.upsert_user(uid, "u", "U"))
        asyncio.run(database.record_notice_acknowledgement(uid, "privacy_notice", "v1"))
    asyncio.run(tmp_db.delete_all_personal_data(a))
    assert asyncio.run(database.has_notice_acknowledgement(a, "privacy_notice", "v1")) is False
    assert asyncio.run(database.has_notice_acknowledgement(b, "privacy_notice", "v1")) is True


def test_notice_acknowledgement_delete_preview_covers_table(tmp_db):
    uid = 9019
    asyncio.run(tmp_db.upsert_user(uid, "u", "U"))
    asyncio.run(database.record_notice_acknowledgement(uid, "privacy_notice", "v1"))
    preview = asyncio.run(tmp_db.preview_delete_all_personal_data(uid))
    assert "user_notice_acknowledgements" in preview
    assert preview["user_notice_acknowledgements"]["policy"] == "CASCADE_DELETE"
    assert preview["user_notice_acknowledgements"]["row_count"] == 1


# ── The real independence proof: a notice-version bump reaches a settled
# onboarding user WITHOUT touching ONBOARDING_VERSION (the exact gap this
# pass closes -- previously structurally impossible: the old design stored
# the ack on the onboarding row itself, whose primary key was already taken
# by the settled row for a bumped-notice-but-same-onboarding-version user).
def test_notice_version_bump_reaches_settled_onboarding_user_independent_of_onboarding_version(tmp_db):
    uid = 9020
    _start_active_at_last_step(uid, oc.ONBOARDING_VERSION)
    assert asyncio.run(database.complete_onboarding(
        uid, oc.ONBOARDING_VERSION, oc.LAST_STEP, "v1")) is True
    assert asyncio.run(database.has_privacy_notice_ack(uid, "v1")) is True
    # ONBOARDING_VERSION never changes; only the notice version bumps.
    assert asyncio.run(database.has_privacy_notice_ack(uid, "v2")) is False
    # The user's onboarding row is unaffected -- still settled/completed for
    # the same (unchanged) onboarding_version; only the independent notice
    # table distinguishes v1 from v2.
    row = asyncio.run(database.get_onboarding_state(uid, oc.ONBOARDING_VERSION))
    assert row["status"] == "completed"
    # Acknowledging the new notice version does not require (and does not
    # touch) the onboarding row at all.
    asyncio.run(database.record_notice_acknowledgement(uid, "privacy_notice", "v2"))
    assert asyncio.run(database.has_privacy_notice_ack(uid, "v2")) is True
    row_after = asyncio.run(database.get_onboarding_state(uid, oc.ONBOARDING_VERSION))
    assert row_after["updated_at"] == row["updated_at"]  # onboarding row untouched
