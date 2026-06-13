"""§5 Psychology profile — DETERMINISTIC, no LLM, no diagnoses.

Pure keyword/aggregation helpers are tested directly; compute_profile and the
GDPR delete path run against a temporary SQLite DB. The no-LLM guarantee is
pinned both structurally (no openai import) and behaviourally.
"""
import asyncio
import pytest

import psychology_profile as pp
from psychology_profile import (
    compute_future_orientation, compute_sleep_problems,
    extract_themes, extract_coping, _bar, PsychologyProfile,
)


def _m(content):
    return {"content": content, "risk_score": 0, "risk_categories": []}


# ── pure helpers ───────────────────────────────────────────────────────────────
def test_future_orientation_positive():
    v, c = compute_future_orientation([_m("я планирую и мечтаю научиться")])
    assert v > 0.5 and c > 0.0


def test_future_orientation_neutral_zero_confidence():
    assert compute_future_orientation([_m("обычный день")]) == (0.5, 0.0)


def test_sleep_problems_detected():
    v, _ = compute_sleep_problems([_m("опять бессонница, не могу уснуть")])
    assert v > 0.0


def test_themes_need_two_mentions():
    msgs = [_m("на работе аврал"), _m("начальник давит, проект горит")]
    assert "работа" in extract_themes(msgs)


def test_themes_single_mention_ignored():
    assert extract_themes([_m("сегодня про работу один раз")]) == []


def test_coping_captured():
    out = extract_coping([_m("дыхание реально помогло сегодня")])
    assert out and "помогло" in out[0]


def test_bar_low_confidence_label():
    assert "мало данных" in _bar(0.8, 0.1)
    assert "мало данных" not in _bar(0.8, 0.9)


def test_no_openai_import_in_module():
    # Structural guarantee: the profile module must not pull in the LLM client.
    import sys
    src = open(pp.__file__, encoding="utf-8").read()
    assert "openai" not in src.lower()


# ── integration against a temp DB ──────────────────────────────────────────────
@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import database
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    return database


def test_empty_user_returns_empty_profile(tmp_db):
    prof = asyncio.run(pp.compute_profile(999))
    assert prof.loneliness == (0.0, 0.0)
    assert prof.messages_analyzed == 0


def test_loneliness_high_after_loneliness_messages(tmp_db):
    async def go():
        await tmp_db.upsert_user(1, "u", "U")
        for _ in range(3):
            await tmp_db.save_message(1, "user", "мне одиноко", "open_chat", "ru", 25, ["loneliness"])
        return await pp.compute_profile(1)
    prof = asyncio.run(go())
    assert prof.loneliness[0] > 0.5
    assert prof.loneliness[1] > 0.0


def test_no_llm_called_during_compute(tmp_db, monkeypatch):
    import openai
    called = {"n": 0}
    class Boom:
        def __init__(self, *a, **k): called["n"] += 1
    monkeypatch.setattr(openai, "AsyncOpenAI", Boom)
    async def go():
        await tmp_db.upsert_user(2, "u", "U")
        await tmp_db.save_message(2, "user", "привет", "open_chat", "ru", 0, [])
        return await pp.compute_profile(2)
    asyncio.run(go())
    assert called["n"] == 0


def test_profile_save_get_delete_roundtrip(tmp_db):
    async def go():
        await tmp_db.upsert_user(3, "u", "U")
        await tmp_db.save_message(3, "user", "мне одиноко", "open_chat", "ru", 25, ["loneliness"])
        prof = await pp.compute_profile(3)
        await tmp_db.save_profile(3, prof.to_db_fields())
        got = await tmp_db.get_profile(3)
        await tmp_db.delete_profile(3)
        after = await tmp_db.get_profile(3)
        return got, after
    got, after = asyncio.run(go())
    assert got is not None
    assert after is None
