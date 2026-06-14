"""Epic B — sudden-improvement detector (quiet review flag, not a crisis).

Pure detector covers the logic; a temp-DB test covers the weekly rate-limit.
"""
import asyncio
import pytest

from state_engine import is_sudden_improvement


def _m(score, content="..."):
    return {"content": content, "risk_score": score, "risk_categories": []}


def test_abrupt_jump_flags():
    msgs = [_m(90, "не хочу жить"), _m(95), _m(90), _m(100), _m(85),
            _m(0, "всё хорошо теперь, я решил")]
    assert is_sudden_improvement(msgs) is True


def test_gradual_improvement_does_not_flag():
    # Smooth descent ending in relief — recent pre-relief risk is low → not abrupt.
    msgs = [_m(80), _m(65), _m(50), _m(35), _m(20), _m(8), _m(0, "мне легче")]
    assert is_sudden_improvement(msgs) is False


def test_no_prior_distress_does_not_flag():
    msgs = [_m(0), _m(5), _m(0), _m(0), _m(0, "всё хорошо")]
    assert is_sudden_improvement(msgs) is False


def test_relief_without_zero_risk_does_not_flag():
    msgs = [_m(90), _m(95), _m(90), _m(100), _m(70, "всё хорошо")]
    assert is_sudden_improvement(msgs) is False


def test_detector_does_not_mutate_input():
    msgs = [_m(90), _m(95), _m(90), _m(100), _m(0, "всё хорошо")]
    snapshot = [dict(m) for m in msgs]
    is_sudden_improvement(msgs)
    assert msgs == snapshot


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import database
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    return database


def test_rate_limited_once_per_week(tmp_db):
    async def go():
        first = await tmp_db.log_review_flag(1, "sudden_improvement", "ctx")
        second = await tmp_db.log_review_flag(1, "sudden_improvement", "ctx")
        return first, second
    first, second = asyncio.run(go())
    assert first is True
    assert second is False
