"""PR 1B-1 checkpoint item 4 — traced_response_builder own-context enforcement.

requester_uid must equal user_id for an ordinary traced reply; a mismatch is
blocked BEFORE persist/build/send, independent of whether the requester's role
would otherwise be allowed A1 at all.
"""
import asyncio

import pytest

import access_control as ac
from traced_response import Influence, traced_response_builder


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "controlled_clinical_test")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", {10, 11})
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", {20})
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {10: [20], 11: [20]})


def _recorder():
    calls = {"persist": 0, "build": 0, "send": 0, "fallback": 0}

    async def persist(rid, uid, rows):
        calls["persist"] += 1

    async def build():
        calls["build"] += 1
        return "LATENT"

    async def send(text):
        calls["send"] += 1

    async def fallback():
        calls["fallback"] += 1

    return calls, persist, build, send, fallback


def _influence():
    return [Influence("mode", "m1", "reply drew on mode m1")]


def _run(*, user_id, requester_uid, ack_tester_ids=()):
    async def _ack(uid):
        return uid in ack_tester_ids
    import database
    calls, persist, build, send, fallback = _recorder()

    async def go():
        import unittest.mock
        with unittest.mock.patch.object(database, "get_tester_acknowledged", _ack):
            return await traced_response_builder(
                user_id=user_id, requester_uid=requester_uid, influences=_influence(),
                build_response=build, send=send, persist_trace=persist,
                neutral_fallback=fallback)
    rid = asyncio.run(go())
    return rid, calls


def test_owner_requester_user_mismatch_blocked():
    # requester_uid=OWNER, user_id=TESTER -> blocked (checkpoint item 4 example 1).
    with pytest.raises(ac.A1NotAllowed):
        _run(user_id=10, requester_uid=1)


def test_tester_a_cannot_build_for_tester_b():
    # requester_uid=TESTER_A, user_id=TESTER_B -> blocked.
    with pytest.raises(ac.A1NotAllowed):
        _run(user_id=11, requester_uid=10, ack_tester_ids=(10, 11))


def test_tester_own_context_with_mapping_and_ack_allowed():
    # requester_uid=TESTER_A, user_id=TESTER_A, mapped+acknowledged+controlled mode
    # -> allowed (checkpoint item 4 example 3).
    rid, calls = _run(user_id=10, requester_uid=10, ack_tester_ids=(10,))
    assert rid is not None
    assert calls == {"persist": 1, "build": 1, "send": 1, "fallback": 0}


def test_mismatch_blocks_before_any_side_effect():
    # Nothing is persisted/built/sent when the context check fails -- it runs
    # before content_ful/persist, same discipline as the role/mode gate.
    calls, persist, build, send, fallback = _recorder()

    async def go():
        return await traced_response_builder(
            user_id=10, requester_uid=1, influences=_influence(),
            build_response=build, send=send, persist_trace=persist,
            neutral_fallback=fallback)
    with pytest.raises(ac.A1NotAllowed):
        asyncio.run(go())
    assert calls == {"persist": 0, "build": 0, "send": 0, "fallback": 0}


def test_owner_own_context_allowed():
    rid, calls = _run(user_id=1, requester_uid=1)
    assert rid is not None
    assert calls["send"] == 1


def test_unacknowledged_tester_own_context_still_blocked_by_role_gate():
    # Own-context matches, but the tester hasn't acknowledged -> role/mode gate
    # (assert_a1_allowed, checked BEFORE own-context) still blocks it. Proves the
    # two gates are independent, not one substituting for the other.
    with pytest.raises(ac.A1NotAllowed):
        _run(user_id=10, requester_uid=10, ack_tester_ids=())  # not acknowledged
