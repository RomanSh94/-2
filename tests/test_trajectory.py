"""§4 Conversation trajectory — deterministic, no LLM, no re-scoring.

Most logic is covered through the PURE builders (build_trajectory /
calculate_trend) so the DB isn't needed; one integration test exercises
get_emotional_trajectory against a temporary SQLite file.
"""
import asyncio
import pytest

from state_engine import (
    calculate_trend, build_trajectory, _colour, choose_scenario,
    EmotionalTrajectory,
)


def _m(score, cats):
    return {"content": "x", "risk_score": score, "risk_categories": cats, "created_at": ""}


# ── calculate_trend ────────────────────────────────────────────────────────────
def test_trend_needs_min_data():
    assert calculate_trend([_m(10, [])]) == ("stable", 0.0)


def test_trend_deteriorating():
    msgs = [_m(0, []), _m(5, []), _m(40, []), _m(60, [])]
    trend, conf = calculate_trend(msgs)
    assert trend == "deteriorating"
    assert conf > 0.0


def test_trend_improving():
    msgs = [_m(60, []), _m(50, []), _m(10, []), _m(0, [])]
    assert calculate_trend(msgs)[0] == "improving"


def test_trend_stable_small_changes():
    msgs = [_m(20, []), _m(22, []), _m(18, []), _m(25, [])]
    assert calculate_trend(msgs)[0] == "stable"


# ── colour mapping ─────────────────────────────────────────────────────────────
def test_colour_red_on_suicide_regardless_of_score():
    assert _colour(0, ["suicide"]) == "RED"


def test_colour_thresholds():
    assert _colour(80, []) == "ORANGE"
    assert _colour(50, []) == "YELLOW"
    assert _colour(10, []) == "GREEN"


# ── build_trajectory ───────────────────────────────────────────────────────────
def test_empty_window_is_stable_zero_confidence():
    t = build_trajectory(1, 24, [], None)
    assert t.trend == "stable"
    assert t.trend_confidence < 0.3
    assert t.messages_analyzed == 0


def test_hopelessness_streak_counted():
    msgs = [_m(10, []), _m(40, ["hopelessness"]), _m(40, ["hopelessness"]),
            _m(40, ["hopelessness"]), _m(40, ["hopelessness"])]
    t = build_trajectory(1, 24, msgs, None)
    assert t.hopelessness_streak == 4


def test_streak_resets_on_calm_tail():
    msgs = [_m(40, ["hopelessness"]), _m(40, ["hopelessness"]), _m(5, [])]
    assert build_trajectory(1, 24, msgs, None).hopelessness_streak == 0


def test_category_frequency_and_max_level():
    msgs = [_m(25, ["loneliness"]), _m(25, ["loneliness"]), _m(40, ["hopelessness"])]
    t = build_trajectory(1, 24, msgs, None)
    assert t.risk_categories_frequency["loneliness"] == 2
    assert t.max_risk_level in ("YELLOW", "ORANGE")


# ── choose_scenario trajectory bias ────────────────────────────────────────────
def test_loneliness_trajectory_routes_reflective():
    traj = EmotionalTrajectory(user_id=1, messages_analyzed=5,
                               risk_categories_frequency={"loneliness": 4})
    state = dict(anxiety=0.0, panic=0.0, overwhelm=0.0, hopelessness=0.0,
                 loneliness=0.0, energy=0.5, openness=0.5, dissociation=0.0)
    assert choose_scenario(state, [], "OPEN", "MEDIUM", 0.5, trajectory=traj) == "reflective"


def test_trajectory_never_overrides_crisis():
    traj = EmotionalTrajectory(user_id=1, messages_analyzed=5,
                               risk_categories_frequency={"loneliness": 9})
    state = dict(anxiety=0.0, panic=0.0, overwhelm=0.0, hopelessness=0.0,
                 loneliness=0.0, energy=0.5, openness=0.5, dissociation=0.0)
    assert choose_scenario(state, ["suicide"], "OPEN", "MEDIUM", 0.5, trajectory=traj) == "crisis"


# ── integration against a temp DB ──────────────────────────────────────────────
@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import database
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    return database


def test_get_emotional_trajectory_reads_persisted_risk(tmp_db):
    from state_engine import get_emotional_trajectory
    async def go():
        await tmp_db.upsert_user(1, "u", "U")
        for sc in (0, 10, 40, 60):
            await tmp_db.save_message(1, "user", "msg", "open_chat", "ru", sc, ["hopelessness"] if sc >= 40 else [])
        return await get_emotional_trajectory(1, 24)
    t = asyncio.run(go())
    assert t.messages_analyzed == 4
    assert t.trend == "deteriorating"
    assert t.hopelessness_streak == 2
