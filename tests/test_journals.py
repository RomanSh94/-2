"""Epic 8 — Journals: risk-gate + emotion journal (item 1).

The safety-critical part is the gate: any free-text journal field is run through
the REAL detector/classifier, so a crisis phrase routes to Crisis Protocol and
an ambiguous phrase aborts the journal.
"""
import asyncio
import pytest

import journals
from journals import gate, should_skip_body, emotion_prompt, EMOTION_FIELDS
from crisis_protocol import RED


# ── Risk gate ─────────────────────────────────────────────────────────────────
def test_gate_suicide_is_red():
    level, _ = gate("хочу умереть")
    assert level == RED


def test_gate_ambiguous_window_is_flagged():
    # "выйти в окно" is not RED but must be caught as ambiguous → journal aborts.
    _, risk = gate("я бы хотела выйти в это окно")
    assert risk.get("ambiguous_phrases")


def test_gate_calm_text_is_green():
    level, risk = gate("сегодня гулял в парке, было неплохо")
    assert level == "GREEN"
    assert not risk.get("ambiguous_phrases")


# ── Self-tracking / body-step guardrail ───────────────────────────────────────
def test_skip_body_at_orange():
    assert should_skip_body("ORANGE", {}) is True


def test_skip_body_when_self_harm_signal():
    assert should_skip_body("GREEN", {"categories": ["self_harm"]}) is True


def test_dont_skip_body_when_green_and_no_sensitive():
    assert should_skip_body("GREEN", {"categories": ["loneliness"]}) is False


# ── Fixed, non-interpretive prompts ───────────────────────────────────────────
def test_prompts_are_fixed_and_present():
    assert emotion_prompt("event") == "Что произошло?"
    assert emotion_prompt("event", "en") == "What happened?"
    assert EMOTION_FIELDS[0] == "event" and EMOTION_FIELDS[-1] == "outcome"


# ── Persistence ───────────────────────────────────────────────────────────────
@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import database
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    return database


def test_save_emotion_entry_roundtrip(tmp_db):
    async def go():
        await tmp_db.upsert_user(1, "u", "U")
        eid = await tmp_db.save_emotion_entry(1, {
            "event": "поспорил с другом", "feeling": "обида", "intensity": 7,
            "body": None, "need": "поддержка", "action": "ушёл", "outcome": "легче"})
        import aiosqlite
        async with aiosqlite.connect(tmp_db.DB) as db:
            cur = await db.execute(
                "SELECT event,feeling,intensity,body,outcome FROM emotion_journal_entries WHERE id=?",
                (eid,))
            return await cur.fetchone()
    row = asyncio.run(go())
    assert row == ("поспорил с другом", "обида", 7, None, "легче")


def test_save_cbt_entry_roundtrip(tmp_db):
    async def go():
        await tmp_db.upsert_user(1, "u", "U")
        await tmp_db.save_cbt_entry(1, {
            "situation": "встреча", "automatic_thought": "я провалюсь",
            "emotion": "тревога", "intensity": 8, "evidence_for": "—",
            "evidence_against": "раньше справлялся", "realistic_thought": "будет непросто, но ок",
            "change": "чуть легче"})
        return await tmp_db.get_emotion_entries_since(1, 7)  # different table → empty
    assert asyncio.run(go()) == []


# ── Weekly report (deterministic, no diagnoses) ───────────────────────────────
_BANNED = ["депресс", "птср", "диагноз", "расстройств", "травма", "потому что", "из-за того"]


def test_report_low_data():
    txt = journals.build_weekly_report([], [], "ru")
    assert "мало" in txt.lower()


def test_report_has_counts_and_no_diagnosis():
    entries = [
        {"feeling": "тревога", "intensity": 8, "created_at": "2026-06-10 21:00:00"},
        {"feeling": "тревога", "intensity": 7, "created_at": "2026-06-11 20:00:00"},
        {"feeling": "грусть", "intensity": 4, "created_at": "2026-06-11 10:00:00"},
    ]
    txt = journals.build_weekly_report(entries, [], "ru").lower()
    assert "тревога" in txt
    assert "?" in txt                       # ends with an invitation question
    assert not any(b in txt for b in _BANNED)


def test_cbt_prompts_present():
    assert journals.cbt_prompt("situation") == "Опиши ситуацию: что случилось?"
    assert journals.CBT_FIELDS[0] == "situation" and journals.CBT_FIELDS[-1] == "change"


# ── GDPR ──────────────────────────────────────────────────────────────────────
def test_forget_all_wipes_journals(tmp_db):
    async def go():
        await tmp_db.upsert_user(5, "u", "U")
        await tmp_db.save_emotion_entry(5, {"event": "x", "feeling": "грусть", "intensity": 3})
        await tmp_db.save_cbt_entry(5, {"situation": "y", "emotion": "тревога"})
        await tmp_db.forget_all(5)
        return await tmp_db.export_journals(5)
    data = asyncio.run(go())
    assert data["emotion_journal_entries"] == []
    assert data["cbt_journal_entries"] == []
