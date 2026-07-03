"""PR 1B-1 checkpoint-2 item 7 — dashboard route isolation for
controlled_clinical_test: /export, /safety (+ /safety/review), /moderation,
/research must not leak CLINICIAN_TESTER data to the OWNER-only dashboard.
"""
import asyncio
import sqlite3

import pytest

import database
import access_control as ac
import dashboard


@pytest.fixture
def client(tmp_path, monkeypatch):
    dbfile = str(tmp_path / "t.db")
    monkeypatch.setattr(database, "DB", dbfile)
    asyncio.run(database.init_db())

    dashboard.app.config["TESTING"] = True
    c = dashboard.app.test_client()
    with c.session_transaction() as sess:
        sess["ok"] = True
    return c


@pytest.fixture
def role_config(monkeypatch):
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "controlled_clinical_test")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", {10})
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", {20})
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {10: [20]})


def _seed():
    con = sqlite3.connect(database.DB)
    con.execute("INSERT INTO users (id, username, first_name) VALUES (1,'owner','O')")
    con.execute("INSERT INTO users (id, username, first_name) VALUES (10,'tester','T')")
    con.execute(
        "INSERT INTO crisis_events (user_id, level, risk_score, categories, "
        "message_excerpt, protective_factors_json) VALUES "
        "(1,'critical',100,'suicide','owner secret text','[\"children\"]'),"
        "(10,'critical',100,'suicide','tester secret text','[\"pets\"]')")
    con.execute(
        "INSERT INTO review_flags (user_id, flag_type, context) VALUES "
        "(1,'sudden_improvement','owner flag ctx'),"
        "(10,'sudden_improvement','tester flag ctx')")
    con.execute(
        "INSERT INTO toxic_validation_blocks (user_id, matched, original_text) VALUES "
        "(1,'m1','owner toxic text'),(10,'m1','tester toxic text')")
    con.execute(
        "INSERT INTO moderation_logs (user_id, username, first_name, risk_level, "
        "risk_score, risk_cats, message_text) VALUES "
        "(1,'owner','O','high',70,'aggression','owner raw message'),"
        "(10,'tester','T','high',70,'aggression','tester raw message')")
    con.commit()
    tester_flag_id = con.execute(
        "SELECT id FROM review_flags WHERE user_id=10").fetchone()[0]
    con.close()
    return tester_flag_id


# ── /export ─────────────────────────────────────────────────────────────────
def test_export_disabled_in_controlled_clinical_test(client, role_config):
    _seed()
    resp = client.get("/export?table=crisis_events")
    assert resp.status_code == 403


def test_export_still_works_in_personal_use(client, monkeypatch):
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    _seed()
    resp = client.get("/export?table=crisis_events")
    assert resp.status_code == 200


# ── /safety ──────────────────────────────────────────────────────────────────
def test_safety_review_excludes_tester_rows(client, role_config):
    _seed()
    resp = client.get("/safety")
    body = resp.get_data(as_text=True)
    assert "owner secret text" in body
    assert "tester secret text" not in body
    assert "owner flag ctx" in body
    assert "tester flag ctx" not in body
    assert "owner toxic text" in body
    assert "tester toxic text" not in body


def test_safety_review_direct_url_cannot_mark_tester_flag(client, role_config):
    tester_flag_id = _seed()
    resp = client.get(f"/safety/review/{tester_flag_id}")
    assert resp.status_code == 403
    con = sqlite3.connect(database.DB)
    reviewed = con.execute(
        "SELECT reviewed FROM review_flags WHERE id=?", (tester_flag_id,)).fetchone()[0]
    con.close()
    assert reviewed == 0   # still unreviewed -- the direct hit did not mark it


def test_safety_review_owner_flag_can_still_be_marked(client, role_config):
    _seed()
    con = sqlite3.connect(database.DB)
    owner_flag_id = con.execute(
        "SELECT id FROM review_flags WHERE user_id=1").fetchone()[0]
    con.close()
    resp = client.get(f"/safety/review/{owner_flag_id}")
    assert resp.status_code == 302
    con = sqlite3.connect(database.DB)
    reviewed = con.execute(
        "SELECT reviewed FROM review_flags WHERE id=?", (owner_flag_id,)).fetchone()[0]
    con.close()
    assert reviewed == 1


# ── /moderation ──────────────────────────────────────────────────────────────
def test_moderation_excludes_tester_rows(client, role_config):
    _seed()
    resp = client.get("/moderation")
    body = resp.get_data(as_text=True)
    assert "owner raw message" in body
    assert "tester raw message" not in body


# ── /research ────────────────────────────────────────────────────────────────
def test_research_disabled_in_controlled_clinical_test(client, role_config):
    resp = client.get("/research")
    assert resp.status_code == 403


def test_research_still_works_in_personal_use(client, monkeypatch):
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    resp = client.get("/research")
    assert resp.status_code == 200


# ── /users, /user/<uid>, /profile/<uid> (already-applied fixes, re-verified) ──
def test_users_list_excludes_tester_rows(client, role_config):
    _seed()
    resp = client.get("/users")
    body = resp.get_data(as_text=True)
    assert ">1<" in body or "owner" in body
    assert "@tester" not in body


def test_user_detail_403_for_tester_uid(client, role_config):
    _seed()
    resp = client.get("/user/10")
    assert resp.status_code == 403


def test_profile_detail_403_for_tester_uid(client, role_config):
    resp = client.get("/profile/10")
    assert resp.status_code == 403


def test_owner_data_still_fully_visible(client, role_config):
    _seed()
    resp = client.get("/user/1")
    assert resp.status_code == 200
