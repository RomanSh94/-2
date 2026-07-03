"""PR 1B-1 checkpoint — access_control.py unit tests (roles, modes, alert routing,
reviewer-mapping validation, public-mode fail-closed, A1 own-context check).

Bot-integration tests (trigger_crisis role behavior, cmd_start gate, dashboard
filtering) live in their own dedicated test files once those pieces land; this
file covers the pure access_control functions in isolation.
"""
import asyncio

import pytest

import access_control as ac


@pytest.fixture(autouse=True)
def _reset_config(monkeypatch):
    """Every test gets a clean, explicit config — no leakage from a real .env."""
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "controlled_clinical_test")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", {10, 11})
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", {20, 21})
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {10: [20]})
    monkeypatch.setattr(ac, "ADMIN_USER_IDS", [999])


# ── role resolution ────────────────────────────────────────────────────────────
def test_resolve_role_owner_tester_reviewer_unknown():
    assert ac.resolve_role(1) == ac.OWNER
    assert ac.resolve_role(10) == ac.CLINICIAN_TESTER
    assert ac.resolve_role(20) == ac.CLINICIAN_REVIEWER
    assert ac.resolve_role(999999) == ac.UNKNOWN


def test_resolve_role_safe_treats_exception_as_unknown(monkeypatch):
    def _boom(uid):
        raise RuntimeError("broken resolver")
    monkeypatch.setattr(ac, "resolve_role", _boom)
    assert ac.resolve_role_safe(1) == ac.UNKNOWN


# ── item 6+7: resolved_reviewers_for validates + dedupes ───────────────────────
def test_resolved_reviewers_for_valid_mapping():
    assert ac.resolved_reviewers_for(10) == [20]


def test_resolved_reviewers_for_unmapped_tester_is_empty():
    assert ac.resolved_reviewers_for(11) == []


def test_resolved_reviewers_for_mapping_to_non_reviewer_id_is_ignored(monkeypatch):
    # TESTER_REVIEWER_MAP=111:999 where 999 is NOT in CLINICIAN_REVIEWER_IDS.
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {10: [999]})
    assert ac.resolved_reviewers_for(10) == []


def test_resolved_reviewers_for_dedupes_repeated_mapping(monkeypatch):
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {10: [20, 20]})
    assert ac.resolved_reviewers_for(10) == [20]


def test_parse_tester_reviewer_map_many_to_many():
    parsed = ac._parse_tester_reviewer_map("111:222,111:333,444:555")
    assert parsed == {111: [222, 333], 444: [555]}


# ── item 6: invalid mapping -> no full access, no A1 ───────────────────────────
def test_has_full_access_acknowledged_tester_with_invalid_mapping_denied(monkeypatch):
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {10: [999]})  # 999 not a reviewer

    async def _ack_true(uid):
        return True
    import database
    monkeypatch.setattr(database, "get_tester_acknowledged", _ack_true)

    assert asyncio.run(ac.has_full_access(10)) is False
    assert asyncio.run(ac.a1_allowed(10)) is False


def test_has_full_access_acknowledged_tester_with_valid_mapping_allowed(monkeypatch):
    async def _ack_true(uid):
        return True
    import database
    monkeypatch.setattr(database, "get_tester_acknowledged", _ack_true)

    assert asyncio.run(ac.has_full_access(10)) is True


# ── item 5: public mode fails closed for full access, including OWNER ─────────
def test_public_mode_denies_full_access_for_owner(monkeypatch):
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "public")
    assert asyncio.run(ac.has_full_access(1)) is False       # OWNER, still denied


def test_public_mode_denies_a1_for_owner(monkeypatch):
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "public")
    assert asyncio.run(ac.a1_allowed(1)) is False


def test_personal_use_owner_has_full_access_and_a1(monkeypatch):
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    assert asyncio.run(ac.has_full_access(1)) is True
    assert asyncio.run(ac.a1_allowed(1)) is True


# ── should_alert_owner / crisis_alert_targets ──────────────────────────────────
def test_should_alert_owner_true_for_owner_and_reviewer():
    assert ac.should_alert_owner(1) is True
    assert ac.should_alert_owner(20) is True


def test_should_alert_owner_false_for_tester_and_unknown():
    assert ac.should_alert_owner(10) is False
    assert ac.should_alert_owner(999999) is False


def test_should_alert_owner_fail_closed_on_resolver_exception(monkeypatch):
    def _boom(uid):
        raise RuntimeError("broken")
    monkeypatch.setattr(ac, "resolve_role", _boom)
    assert ac.should_alert_owner(1) is False   # even for what would be OWNER


def test_crisis_alert_targets_owner():
    kind, targets = ac.crisis_alert_targets(1)
    assert kind == "owner" and targets == [999]


def test_crisis_alert_targets_reviewer_own_event():
    kind, targets = ac.crisis_alert_targets(20)
    assert kind == "owner" and targets == [999]   # reviewer's OWN event alerts owner


def test_crisis_alert_targets_mapped_tester():
    kind, targets = ac.crisis_alert_targets(10)
    assert kind == "reviewer" and targets == [20]


def test_crisis_alert_targets_unmapped_tester_is_none():
    kind, targets = ac.crisis_alert_targets(11)
    assert kind == "none" and targets == []


def test_crisis_alert_targets_unknown_is_none():
    kind, targets = ac.crisis_alert_targets(999999)
    assert kind == "none" and targets == []


def test_crisis_alert_targets_fail_closed_on_resolver_exception(monkeypatch):
    def _boom(uid):
        raise RuntimeError("broken during alert routing")
    monkeypatch.setattr(ac, "resolve_role", _boom)
    kind, targets = ac.crisis_alert_targets(1)   # would-be OWNER
    assert kind == "none" and targets == []


# ── can_request_review_pack permission contract ────────────────────────────────
def test_can_request_review_pack_owner_owner_allowed():
    assert ac.can_request_review_pack(1, 1) is True


def test_can_request_review_pack_reviewer_own_tester_allowed():
    assert ac.can_request_review_pack(20, 10) is True


def test_can_request_review_pack_reviewer_unmapped_tester_denied():
    assert ac.can_request_review_pack(20, 11) is False


def test_can_request_review_pack_owner_tester_denied_by_default():
    assert ac.can_request_review_pack(1, 10) is False


def test_can_request_review_pack_reviewer_owner_denied_by_default():
    assert ac.can_request_review_pack(20, 1) is False


def test_can_request_review_pack_fail_closed_on_resolver_exception(monkeypatch):
    def _boom(uid):
        raise RuntimeError("broken")
    monkeypatch.setattr(ac, "resolve_role", _boom)
    assert ac.can_request_review_pack(1, 1) is False
