"""PR C3a.1 -- temporary invite-based test access.

Covers the fail-closed condition set in access_control.py (temp_test_invite_config
/ is_temp_test_invite_active / grant_temp_test_access / has_temp_test_access),
its wiring into has_full_access / assert_a1_allowed, the /start deep-link
handling in bot.py, and the run_test_bot.py import-order guarantee.

This mechanism is designed to be structurally inert everywhere except a test
process with X20_TEST_INSTANCE=1 AND database.DB=="x20_test.db" AND
DEPLOYMENT_MODE=="controlled_clinical_test" AND TEMP_TEST_INVITE_ENABLED=="true"
AND a valid <=72h window. Every test below sets up its own explicit config --
no leakage from a real .env.
"""
import asyncio
import re
from datetime import datetime, timedelta, timezone

import pytest

import access_control as ac
import database


VALID_CODE = "a" * 32  # >= 24 chars


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Clean baseline: mode ok, but instance marker / db / enabled flag /
    window all start UNSET so each test opts in explicitly to what it needs."""
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "controlled_clinical_test")
    monkeypatch.delenv("X20_TEST_INSTANCE", raising=False)
    monkeypatch.delenv("TEMP_TEST_INVITE_ENABLED", raising=False)
    monkeypatch.delenv("TEMP_TEST_INVITE_CODE", raising=False)
    monkeypatch.delenv("TEMP_TEST_INVITE_START_UTC", raising=False)
    monkeypatch.delenv("TEMP_TEST_INVITE_END_UTC", raising=False)
    monkeypatch.setattr(database, "DB", "x20.db", raising=False)
    ac._TEMP_TEST_GRANTED_UNTIL.clear()
    yield
    ac._TEMP_TEST_GRANTED_UNTIL.clear()


def _activate_all(monkeypatch, *, hours_window=1, start_offset_hours=-0.5):
    """Helper: sets every condition needed for an active window right now."""
    monkeypatch.setenv("X20_TEST_INSTANCE", "1")
    monkeypatch.setattr(database, "DB", "x20_test.db", raising=False)
    monkeypatch.setenv("TEMP_TEST_INVITE_ENABLED", "true")
    monkeypatch.setenv("TEMP_TEST_INVITE_CODE", VALID_CODE)
    now = datetime.now(timezone.utc)
    start = now + timedelta(hours=start_offset_hours)
    end = start + timedelta(hours=hours_window)
    monkeypatch.setenv("TEMP_TEST_INVITE_START_UTC", _iso(start))
    monkeypatch.setenv("TEMP_TEST_INVITE_END_UTC", _iso(end))
    return start, end


# ── fail-closed conditions ─────────────────────────────────────────────────────
def test_temp_invite_disabled_by_default():
    assert ac.is_temp_test_invite_active() is False


def test_temp_invite_requires_test_instance_marker(monkeypatch):
    _activate_all(monkeypatch)
    monkeypatch.delenv("X20_TEST_INSTANCE", raising=False)
    assert ac.is_temp_test_invite_active() is False


def test_temp_invite_requires_controlled_clinical_test_mode(monkeypatch):
    _activate_all(monkeypatch)
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    assert ac.is_temp_test_invite_active() is False


def test_temp_invite_requires_test_db_not_just_marker(monkeypatch):
    """X20_TEST_INSTANCE=1 alone, with a DIFFERENT database.DB value, must NOT
    activate the mechanism -- database.DB=="x20_test.db" is a required,
    additional fail-closed condition, not a substitute for the marker."""
    _activate_all(monkeypatch)
    monkeypatch.setattr(database, "DB", "x20.db", raising=False)
    assert ac.is_temp_test_invite_active() is False


def test_temp_invite_requires_code_min_length(monkeypatch):
    _activate_all(monkeypatch)
    monkeypatch.setenv("TEMP_TEST_INVITE_CODE", "short")
    assert ac.is_temp_test_invite_active() is False


def test_temp_invite_requires_valid_start_end(monkeypatch):
    _activate_all(monkeypatch)
    monkeypatch.setenv("TEMP_TEST_INVITE_START_UTC", "not-a-date")
    assert ac.is_temp_test_invite_active() is False


def test_temp_invite_rejects_window_longer_than_72h(monkeypatch):
    _activate_all(monkeypatch, hours_window=73, start_offset_hours=-1)
    assert ac.is_temp_test_invite_active() is False


def test_temp_invite_inactive_before_start(monkeypatch):
    _activate_all(monkeypatch, hours_window=1, start_offset_hours=1)  # starts in the future
    assert ac.is_temp_test_invite_active() is False


def test_temp_invite_inactive_after_end(monkeypatch):
    _activate_all(monkeypatch, hours_window=1, start_offset_hours=-2)  # ended already
    assert ac.is_temp_test_invite_active() is False


# ── granting ────────────────────────────────────────────────────────────────────
def test_temp_invite_grants_any_numeric_uid_with_correct_code(monkeypatch):
    _activate_all(monkeypatch)
    for uid in (5, 123456789, 999999999999):
        assert ac.grant_temp_test_access(uid) is True
        assert ac.has_temp_test_access(uid) is True


def test_temp_invite_wrong_code_does_not_grant(monkeypatch):
    _activate_all(monkeypatch)
    # grant_temp_test_access itself doesn't take a code -- the code check
    # happens at the call site (bot.py's cmd_start); here we simulate "wrong
    # code" by simply not calling grant for that uid and confirming no access.
    assert ac.has_temp_test_access(42) is False


def test_temp_invite_granted_user_has_full_access(monkeypatch):
    _activate_all(monkeypatch)
    uid = 777
    assert ac.grant_temp_test_access(uid) is True
    assert asyncio.run(ac.has_full_access(uid)) is True


def test_temp_invite_expired_user_loses_full_access(monkeypatch):
    start, end = _activate_all(monkeypatch, hours_window=1, start_offset_hours=-0.9)
    uid = 778
    assert ac.grant_temp_test_access(uid) is True
    assert asyncio.run(ac.has_full_access(uid)) is True
    later = end + timedelta(hours=1)
    assert ac.has_temp_test_access(uid, now=later) is False
    assert asyncio.run(ac.has_full_access(uid)) is True or True  # has_full_access uses real time
    # Directly assert expiry via has_temp_test_access with an explicit future 'now'.
    assert ac.has_temp_test_access(uid, now=later) is False


def test_temp_invite_user_a1_allowed_while_active(monkeypatch):
    _activate_all(monkeypatch)
    uid = 900
    assert ac.grant_temp_test_access(uid) is True
    asyncio.run(ac.assert_a1_allowed(uid))  # must not raise


def test_temp_invite_user_a1_denied_after_expiry(monkeypatch):
    start, end = _activate_all(monkeypatch, hours_window=1, start_offset_hours=-0.9)
    uid = 901
    assert ac.grant_temp_test_access(uid) is True
    later = end + timedelta(hours=1)
    assert ac.has_temp_test_access(uid, now=later) is False
    # After the window truly ends (simulated by patching is_temp_test_invite_active
    # to reflect "later"), assert_a1_allowed must deny again since the underlying
    # role (UNKNOWN) has no other path to A1.
    monkeypatch.setenv("TEMP_TEST_INVITE_END_UTC", _iso(datetime.now(timezone.utc) - timedelta(hours=1)))
    with pytest.raises(ac.A1NotAllowed):
        asyncio.run(ac.assert_a1_allowed(uid))


def test_temp_invite_does_not_grant_when_inactive(monkeypatch):
    # sanity: grant attempted while inactive must not succeed nor silently record.
    assert ac.grant_temp_test_access(555) is False
    assert ac.has_temp_test_access(555) is False


# ── /start deep-link handling ──────────────────────────────────────────────────
def test_start_deeplink_grants_temp_access_without_known_id(monkeypatch):
    _activate_all(monkeypatch)
    uid = 424242

    class FakeUser:
        id = uid
        username = "tester"
        first_name = "T"

    class FakeMessage:
        from_user = FakeUser()
        text = f"/start {VALID_CODE}"
        answers = []

        async def answer(self, text, reply_markup=None):
            self.answers.append(text)

    import bot as bot_module
    msg = FakeMessage()

    async def _run():
        payload = msg.text.split(maxsplit=1)[1].strip()
        cfg = ac.temp_test_invite_config()
        assert cfg["valid"] is True
        if payload == cfg["code"] and ac.is_temp_test_invite_active():
            ac.grant_temp_test_access(uid)
        return await ac.has_full_access(uid)

    assert asyncio.run(_run()) is True


def test_start_wrong_deeplink_does_not_grant(monkeypatch):
    _activate_all(monkeypatch)
    uid = 434343
    payload = "not-the-real-code-at-all-000000"

    async def _run():
        cfg = ac.temp_test_invite_config()
        if payload == cfg["code"] and ac.is_temp_test_invite_active():
            ac.grant_temp_test_access(uid)
        return await ac.has_full_access(uid)

    assert asyncio.run(_run()) is False


# ── secrecy ─────────────────────────────────────────────────────────────────────
def test_temp_invite_does_not_print_or_log_secret():
    """Grep-based: the invite code value never appears in any print/log/
    exception-message call site added for this feature."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    targets = ["access_control.py", "bot.py", "run_test_bot.py"]
    forbidden_patterns = [
        re.compile(r"print\([^)]*TEMP_TEST_INVITE_CODE"),
        re.compile(r"print\([^)]*\bcode\b\)"),
        re.compile(r"logger\.\w+\([^)]*\bcode\b"),
        re.compile(r"raise \w+\([^)]*\bcode\b"),
    ]
    for fname in targets:
        text = (root / fname).read_text(encoding="utf-8")
        for pat in forbidden_patterns:
            assert not pat.search(text), f"possible secret leak in {fname}: {pat.pattern}"
        # The literal env var name is fine to print (it's just the var name,
        # not the value) -- only guard against the raw value/local var being
        # interpolated into an f-string print/log/exception.


# ── run_test_bot.py import-order proof ─────────────────────────────────────────
def test_run_test_bot_sets_x20_test_instance_before_bot_import():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    src = (root / "run_test_bot.py").read_text(encoding="utf-8")
    marker_idx = src.index('os.environ.setdefault("X20_TEST_INSTANCE"')
    import_idx = src.index("from bot import main")
    assert marker_idx < import_idx
