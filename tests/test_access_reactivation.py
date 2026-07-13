"""PR #60 — owner-only user access reactivation (blocked -> active).

Completes the invited lifecycle grant -> revoke -> reactivate. The DB function
only ever flips an EXISTING blocked row; it never inserts, so it cannot grant
access to an unknown / never-invited user. The /unblock command is owner-only.
"""
import asyncio
import sqlite3
import types

import pytest

import bot
import database
import questionnaire_ux
import access_control as ac

OWNER, INVITED, INVITED2, UNKNOWN = 1, 200, 201, 999


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    return database.DB


def _status(db_path, uid):
    con = sqlite3.connect(db_path)
    r = con.execute("SELECT status FROM user_access WHERE user_id=?", (uid,)).fetchone()
    con.close()
    return r[0] if r else None


def _row_count(db_path, uid):
    con = sqlite3.connect(db_path)
    n = con.execute("SELECT COUNT(*) FROM user_access WHERE user_id=?", (uid,)).fetchone()[0]
    con.close()
    return n


# ── DB contract (§9) ──────────────────────────────────────────────────────────
def test_blocked_becomes_active(db):
    asyncio.run(database.grant_user_access(INVITED))
    asyncio.run(database.block_user_access(INVITED))
    assert not asyncio.run(database.user_has_active_access(INVITED))
    assert asyncio.run(database.unblock_user_access(INVITED)) == "reactivated"
    assert asyncio.run(database.user_has_active_access(INVITED))
    assert _status(db, INVITED) == "active"
    assert _row_count(db, INVITED) == 1  # no duplicate row


def test_active_stays_active_idempotent(db):
    asyncio.run(database.grant_user_access(INVITED))
    assert asyncio.run(database.unblock_user_access(INVITED)) == "already-active"
    assert _status(db, INVITED) == "active"
    assert _row_count(db, INVITED) == 1


def test_unknown_user_not_granted_no_row_created(db):
    assert asyncio.run(database.unblock_user_access(UNKNOWN)) == "no-existing-access"
    assert _status(db, UNKNOWN) is None
    assert _row_count(db, UNKNOWN) == 0
    assert not asyncio.run(database.user_has_active_access(UNKNOWN))


def test_users_row_without_user_access_not_granted(db):
    asyncio.run(database.upsert_user(555, "u", "U"))  # known user, never invited
    assert asyncio.run(database.unblock_user_access(555)) == "no-existing-access"
    assert _row_count(db, 555) == 0


def test_repeated_reactivation_idempotent(db):
    asyncio.run(database.grant_user_access(INVITED))
    asyncio.run(database.block_user_access(INVITED))
    assert asyncio.run(database.unblock_user_access(INVITED)) == "reactivated"
    assert asyncio.run(database.unblock_user_access(INVITED)) == "already-active"
    assert asyncio.run(database.unblock_user_access(INVITED)) == "already-active"
    assert _status(db, INVITED) == "active" and _row_count(db, INVITED) == 1


def test_concurrent_reactivation_safe(db):
    asyncio.run(database.grant_user_access(INVITED))
    asyncio.run(database.block_user_access(INVITED))

    async def _race():
        return await asyncio.gather(*[database.unblock_user_access(INVITED)
                                      for _ in range(6)])
    results = asyncio.run(_race())
    # §9.7 contract: concurrent unblocks are SAFE -- the final state is exactly
    # one active row with no duplicate, and every call returns a benign code
    # (reactivated/already-active), never an error or a new row.
    assert set(results) <= {"reactivated", "already-active"}
    assert "reactivated" in results
    assert _status(db, INVITED) == "active"
    assert _row_count(db, INVITED) == 1


def test_result_codes_contain_no_user_id(db):
    asyncio.run(database.grant_user_access(INVITED))
    asyncio.run(database.block_user_access(INVITED))
    for uid in (INVITED, UNKNOWN):
        code = asyncio.run(database.unblock_user_access(uid))
        assert str(uid) not in code


# ── owner control path (§10) ──────────────────────────────────────────────────
class FakeUser:
    def __init__(self, uid):
        self.id = uid
        self.username = "u"


class FakeMessage:
    def __init__(self, user, text):
        self.from_user = user
        self.text = text
        self.chat = types.SimpleNamespace(id=user.id)
        self.answers = []

    async def answer(self, text, **kw):
        self.answers.append(text)


def _async(v=None):
    async def _f(*a, **k):
        return v
    return _f


@pytest.fixture
def cmd_env(db, monkeypatch):
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    monkeypatch.setattr(ac, "OWNER_USER_ID", OWNER)
    return db


def _unblock(uid, target_text):
    msg = FakeMessage(FakeUser(uid), f"/unblock {target_text}")
    asyncio.run(bot.cmd_unblock(msg))
    return msg


def test_owner_can_reactivate_blocked_user(cmd_env):
    asyncio.run(database.grant_user_access(INVITED))
    asyncio.run(database.block_user_access(INVITED))
    msg = _unblock(OWNER, str(INVITED))
    assert "восстановлен" in msg.answers[-1]
    assert asyncio.run(database.user_has_active_access(INVITED))


def test_non_owner_cannot_reactivate_and_gets_neutral_denial(cmd_env):
    asyncio.run(database.grant_user_access(INVITED))
    asyncio.run(database.block_user_access(INVITED))
    msg = _unblock(INVITED2, str(INVITED))  # ordinary user tries
    assert msg.answers[-1] == "Команда недоступна."
    assert not asyncio.run(database.user_has_active_access(INVITED))  # unchanged
    # non-owner denial must not disclose the command's real function
    for token in ("unblock", "восстановл", "user_id", "access"):
        assert token not in msg.answers[-1]


def test_invited_user_cannot_reactivate_self(cmd_env):
    asyncio.run(database.grant_user_access(INVITED))
    asyncio.run(database.block_user_access(INVITED))
    msg = _unblock(INVITED, str(INVITED))  # blocked user tries to self-unblock
    assert msg.answers[-1] == "Команда недоступна."
    assert not asyncio.run(database.user_has_active_access(INVITED))


def test_invited_user_cannot_reactivate_another(cmd_env):
    asyncio.run(database.grant_user_access(INVITED))
    asyncio.run(database.block_user_access(INVITED))
    msg = _unblock(INVITED2, str(INVITED))
    assert msg.answers[-1] == "Команда недоступна."
    assert not asyncio.run(database.user_has_active_access(INVITED))


def test_owner_unblock_unknown_user_reports_no_access(cmd_env):
    msg = _unblock(OWNER, str(UNKNOWN))
    assert "нет записи" in msg.answers[-1]
    assert _row_count(cmd_env, UNKNOWN) == 0


def test_owner_unblock_usage_on_bad_arg(cmd_env):
    msg = _unblock(OWNER, "notanumber")
    assert "Использование" in msg.answers[-1]


# ── DASS authorization before/after (§12) ─────────────────────────────────────
def test_dass_auth_denied_before_and_allowed_after_reactivation(db, monkeypatch):
    import config, dass21_access, pathlib, shutil, hashlib
    fixture = pathlib.Path("tests/fixtures/dass21/synthetic_dass21_shape.json")
    priv = pathlib.Path(db).parent / "p.json"
    shutil.copyfile(fixture, priv)
    monkeypatch.setattr(config, "DASS21_ENABLED", True)
    monkeypatch.setattr(config, "DASS21_OWNER_ONLY", True)
    monkeypatch.setattr(config, "DASS21_INVITED_USERS_ENABLED", True)
    monkeypatch.setattr(config, "DASS21_DEFINITION_PATH", str(priv))
    monkeypatch.setattr(config, "DASS21_DEFINITION_SHA256",
                        hashlib.sha256(priv.read_bytes()).hexdigest())
    monkeypatch.setattr(ac, "OWNER_USER_ID", OWNER)
    asyncio.run(database.grant_user_access(INVITED))
    asyncio.run(database.block_user_access(INVITED))
    assert not asyncio.run(dass21_access.authorize_dass21_user(INVITED)).allowed
    asyncio.run(database.unblock_user_access(INVITED))
    assert asyncio.run(dass21_access.authorize_dass21_user(INVITED)).allowed


def test_owner_admin_privileges_unchanged(cmd_env):
    # Reactivation never grants owner/admin: the row stays source-invite, and
    # the reactivated user is not the owner.
    asyncio.run(database.grant_user_access(INVITED))
    asyncio.run(database.block_user_access(INVITED))
    _unblock(OWNER, str(INVITED))
    con = sqlite3.connect(cmd_env)
    src = con.execute("SELECT source FROM user_access WHERE user_id=?", (INVITED,)).fetchone()[0]
    con.close()
    assert src == "invite"
    assert INVITED != ac.OWNER_USER_ID
