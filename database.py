"""
X20 Database — Final Complete Schema

Tables:
  users, user_profiles, messages, summaries, user_states,
  intervention_results (with confidence_score, engagement_metrics JSON),
  adverse_events, router_decision_logs, weekly_progress_snapshots,
  moderation_logs, checkins, validator_blocks, response_quality (👍/👎),
  ab_assignments
"""
import aiosqlite, sqlite3, json
from datetime import datetime, timezone

DB = "x20.db"

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT, first_name TEXT, language TEXT DEFAULT 'ru',
    first_seen    TEXT DEFAULT (datetime('now')),
    last_seen     TEXT DEFAULT (datetime('now')),
    message_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id               INTEGER PRIMARY KEY,
    primary_issue         TEXT DEFAULT 'unknown',
    secondary_issue       TEXT,
    severity_level        TEXT DEFAULT 'medium',
    total_sessions        INTEGER DEFAULT 0,
    effective_scenarios   TEXT DEFAULT '[]',
    ineffective_scenarios TEXT DEFAULT '[]',
    history_deltas        TEXT DEFAULT '[]',
    updated_at            TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER, role TEXT, content TEXT,
    scenario   TEXT DEFAULT 'open_chat',
    lang       TEXT DEFAULT 'ru',
    created_at TEXT DEFAULT (datetime('now')),
    summarized INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER, content TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_states (
    user_id    INTEGER PRIMARY KEY,
    state_json TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS intervention_results (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                 INTEGER NOT NULL,
    session_objective       TEXT NOT NULL,
    scenario                TEXT NOT NULL,
    practice_id             TEXT NOT NULL,
    practice_version        TEXT NOT NULL DEFAULT 'v1',
    router_version          TEXT NOT NULL DEFAULT '2.0',
    ab_variant              TEXT DEFAULT 'control',
    selection_reason_json   TEXT,
    stage                   TEXT DEFAULT 'OPEN',
    readiness               TEXT DEFAULT 'MEDIUM',
    capacity                REAL DEFAULT 1.0,
    before_score            INTEGER CHECK(before_score BETWEEN 1 AND 10),
    after_score             INTEGER,
    follow_up_24h_score     INTEGER,
    confidence_score        REAL DEFAULT 1.0,
    engagement_metrics_json TEXT,
    feedback_rating         INTEGER,
    self_reported_behavior  INTEGER DEFAULT 0,
    created_at              TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS adverse_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER,
    intervention_id  INTEGER,
    practice_id      TEXT, practice_version TEXT,
    event_type       TEXT,
    description      TEXT,
    initial_delta    INTEGER DEFAULT 0,
    risk_level       TEXT DEFAULT 'low',
    created_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS router_decision_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER,
    state_snapshot  TEXT,
    risk_score      INTEGER, risk_categories TEXT,
    stage           TEXT, readiness TEXT, capacity REAL,
    scenario_chosen TEXT, practice_chosen TEXT,
    router_version  TEXT DEFAULT '2.0',
    ab_variant      TEXT DEFAULT 'control',
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS weekly_progress_snapshots (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                    INTEGER,
    year_week                  TEXT,
    avg_before                 REAL, avg_after REAL,
    avg_delta                  REAL, sessions_count INTEGER,
    confidence_threshold_passed INTEGER DEFAULT 0,
    created_at                 TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS moderation_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER, username TEXT, first_name TEXT,
    risk_level   TEXT, risk_score INTEGER, risk_cats TEXT,
    risk_implicit INTEGER DEFAULT 0,
    message_text TEXT, action_taken TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS checkins (
    user_id      INTEGER PRIMARY KEY,
    username TEXT, first_name TEXT,
    enabled      INTEGER DEFAULT 0,
    checkin_hour INTEGER DEFAULT 10,
    language     TEXT DEFAULT 'ru',
    last_checkin TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS validator_blocks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER, reason TEXT, blocked_text TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS disambiguation_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER,
    message_text TEXT,
    phrase       TEXT,
    mode         TEXT,            -- force_disambiguation | force_crisis
    created_at   TEXT DEFAULT (datetime('now'))
);

-- Epic B: quiet review flags for a human (NOT crisis, NOT user-facing).
CREATE TABLE IF NOT EXISTS review_flags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    flag_type   TEXT NOT NULL,        -- e.g. 'sudden_improvement'
    context     TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    reviewed    INTEGER DEFAULT 0
);

-- Epic C: blocked responses that confirmed a cognitive distortion.
CREATE TABLE IF NOT EXISTS toxic_validation_blocks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    matched       TEXT,
    original_text TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);

-- Epic 8: emotion journal entries (user's own words; no interpretation).
CREATE TABLE IF NOT EXISTS emotion_journal_entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    event      TEXT,
    feeling    TEXT,
    intensity  INTEGER,
    body       TEXT,
    need       TEXT,
    action     TEXT,
    outcome    TEXT,
    lang       TEXT DEFAULT 'ru',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cbt_journal_entries (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL,
    situation         TEXT,
    automatic_thought TEXT,
    emotion           TEXT,
    intensity         INTEGER,
    evidence_for      TEXT,
    evidence_against  TEXT,
    realistic_thought TEXT,
    change            TEXT,
    lang              TEXT DEFAULT 'ru',
    created_at        TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS checkin_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    kind       TEXT,                 -- morning | evening
    value      TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS journal_settings (
    user_id        INTEGER PRIMARY KEY,
    morning_enabled INTEGER DEFAULT 0,
    morning_hour    INTEGER DEFAULT 9,
    evening_enabled INTEGER DEFAULT 0,
    evening_hour    INTEGER DEFAULT 21,
    last_morning    TEXT,             -- 'YYYY-MM-DD' local date last sent
    last_evening    TEXT,
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS push_settings (
    user_id                INTEGER PRIMARY KEY,
    mute_mode              TEXT DEFAULT 'none',   -- none | forever | until
    mute_until             TEXT,
    consecutive_unanswered INTEGER DEFAULT 0,
    updated_at             TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS push_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    tier       TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS crisis_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    level           TEXT NOT NULL,
    risk_score      INTEGER,
    categories      TEXT,
    message_excerpt TEXT,
    lang            TEXT DEFAULT 'ru',
    admin_notified  INTEGER DEFAULT 0,
    user_response   TEXT,
    resolved        INTEGER DEFAULT 0,
    followups_json  TEXT DEFAULT '[]',
    created_at      TEXT DEFAULT (datetime('now'))
);

-- v6 §6.2: crisis-message delivery log. One row per crisis send attempt, written
-- by crisis_delivery.deliver_crisis. Makes "was the crisis screen delivered?" a
-- logged fact (level_delivered = rich|plain|minimal|none) instead of a guess.
CREATE TABLE IF NOT EXISTS crisis_message_delivery_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        INTEGER,
    user_id         INTEGER,
    kind            TEXT,                 -- screen | followup | call_text | ...
    level_delivered TEXT,                 -- rich | plain | minimal | none
    telegram_error  TEXT,                 -- last error if a level fell back/failed
    created_at      TEXT DEFAULT (datetime('now'))
);

-- A1 (PR 0): trace of every response influenced by a latent entity (profile,
-- pattern_hypothesis, questionnaire_score, confirmed_episode, schema_theme, mode,
-- formulation). Written by traced_response.traced_response_builder BEFORE the reply
-- is sent (fail-closed). `human_readable` must name the real source, never a
-- placeholder. `response_id` is a stable inspectable id joining all influence rows
-- of one reply. This is itself SENSITIVE (it is the bot-built model of the owner) —
-- registered in the privacy registry (PR 1A).
CREATE TABLE IF NOT EXISTS influence_trace (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    response_id     TEXT NOT NULL,        -- stable trace_group_id for one reply
    user_id         INTEGER NOT NULL,     -- trace must be attributable to a person
    influence_type  TEXT NOT NULL,        -- pattern_hypothesis | questionnaire_score | confirmed_episode | schema_theme | mode | formulation | profile
    source_id       TEXT NOT NULL,        -- id of the source entity (never empty)
    human_readable  TEXT NOT NULL,        -- "reply drew on pattern_hypothesis X"
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS response_quality (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER,
    message_id      INTEGER,
    scenario        TEXT,
    ab_variant      TEXT,
    rating          INTEGER,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Deterministic psychology profile (§5). Every value is an aggregate of
-- already-computed risk signals; NO LLM, NO diagnoses. value+confidence pairs.
CREATE TABLE IF NOT EXISTS user_psychology_profile (
    user_id                      INTEGER PRIMARY KEY,
    loneliness_value             REAL DEFAULT 0.0,
    loneliness_confidence        REAL DEFAULT 0.0,
    hopelessness_value           REAL DEFAULT 0.0,
    hopelessness_confidence      REAL DEFAULT 0.0,
    self_criticism_value         REAL DEFAULT 0.0,
    self_criticism_confidence    REAL DEFAULT 0.0,
    anxiety_value                REAL DEFAULT 0.0,
    anxiety_confidence           REAL DEFAULT 0.0,
    social_support_value         REAL DEFAULT 0.5,
    social_support_confidence    REAL DEFAULT 0.0,
    future_orientation_value     REAL DEFAULT 0.5,
    future_orientation_confidence REAL DEFAULT 0.0,
    energy_value                 REAL DEFAULT 0.5,
    energy_confidence            REAL DEFAULT 0.0,
    sleep_problems_value         REAL DEFAULT 0.0,
    sleep_problems_confidence    REAL DEFAULT 0.0,
    crisis_risk                  REAL DEFAULT 0.0,
    mood_trend                   TEXT DEFAULT 'stable',
    main_themes                  TEXT DEFAULT '[]',
    coping_strategies_used       TEXT DEFAULT '[]',
    messages_analyzed            INTEGER DEFAULT 0,
    last_updated                 TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS psychology_profile_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_profile_history_user
    ON psychology_profile_history(user_id, created_at DESC);
"""

# Additive column migrations (no migration system in this repo; ADD COLUMN is
# non-destructive and safe to run on the production DB every boot).
_MIGRATIONS = [
    # messages: per-message risk snapshot — the deterministic source for
    # trajectory/profile. moderation_logs only stores medium+ risk, so it
    # cannot be the source (it's blind to the calm majority of messages).
    ("messages", "risk_score", "INTEGER DEFAULT 0"),
    ("messages", "risk_categories", "TEXT DEFAULT ''"),
    # Protective factors (Columbia) — context attached to a crisis event.
    ("crisis_events", "protective_factors_json", "TEXT DEFAULT '[]'"),
    # Epic 8: per-user UTC offset (hours) for local-time journal reminders.
    ("users", "tz_offset", "INTEGER DEFAULT 0"),
    # Timezone PR: distinguish "tz explicitly set" from the default 0 (=UTC), so
    # an unset ru user defaults to MSK (+3) while an explicit UTC+0 stays 0.
    ("users", "tz_set", "INTEGER DEFAULT 0"),
    # Crisis-loop fix: monotonic escalation stage on the active event + the time
    # stage 3 was entered (drives the 5-10 min follow-up).
    ("crisis_events", "crisis_stage", "INTEGER DEFAULT 0"),
    ("crisis_events", "stage3_at", "TEXT"),
]


async def _apply_migrations(db) -> None:
    for table, column, decl in _MIGRATIONS:
        cur = await db.execute(f"PRAGMA table_info({table})")
        cols = [r[1] for r in await cur.fetchall()]
        if column not in cols:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.executescript(SCHEMA)
        await _apply_migrations(db)
        await db.commit()

# ── Users ─────────────────────────────────────────────────────────────────────

async def upsert_user(uid: int, username: str, first_name: str, language: str = "ru"):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """INSERT INTO users (id,username,first_name,language) VALUES (?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET username=excluded.username,
               first_name=excluded.first_name, last_seen=datetime('now'),
               message_count=message_count+1, language=excluded.language""",
            (uid, username, first_name, language))
        await db.commit()

async def get_user_language(uid: int) -> str:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT language FROM users WHERE id=?", (uid,))
        row = await cur.fetchone()
        return row[0] if row else "ru"

# ── Messages ──────────────────────────────────────────────────────────────────

async def save_message(uid: int, role: str, content: str,
                        scenario: str = "open_chat", lang: str = "ru",
                        risk_score: int = 0, risk_categories=None):
    cats = ",".join(risk_categories) if risk_categories else ""
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO messages (user_id,role,content,scenario,lang,risk_score,risk_categories)"
            " VALUES (?,?,?,?,?,?,?)",
            (uid, role, content, scenario, lang, int(risk_score), cats))
        await db.commit()

async def get_user_messages_with_risk(uid: int, window_hours: int = 24) -> list:
    """User messages in the last N hours WITH their per-message risk snapshot.

    Returns oldest-first list of dicts. Deterministic source for trajectory and
    profile — NO recomputation, NO LLM. Reads the risk_score/risk_categories
    columns persisted on every inbound message."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT content, risk_score, risk_categories, created_at"
            " FROM messages WHERE user_id=? AND role='user'"
            f"  AND created_at > datetime('now', '-{int(window_hours)} hours')"
            " ORDER BY id", (uid,))
        rows = await cur.fetchall()
    return [{"content": r[0], "risk_score": r[1] or 0,
             "risk_categories": [c for c in (r[2] or "").split(",") if c],
             "created_at": r[3]} for r in rows]

async def get_recent_messages(uid: int, limit: int = 8) -> list:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT role,content FROM messages WHERE user_id=? AND summarized=0"
            " ORDER BY id DESC LIMIT ?", (uid, limit))
        rows = await cur.fetchall(); rows.reverse(); return rows

async def get_unsummarized_messages(uid: int) -> list:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT id,role,content FROM messages WHERE user_id=? AND summarized=0 ORDER BY id",
            (uid,))
        return await cur.fetchall()

async def mark_summarized(ids: list):
    if not ids: return
    async with aiosqlite.connect(DB) as db:
        ph = ",".join("?" * len(ids))
        await db.execute(f"UPDATE messages SET summarized=1 WHERE id IN ({ph})", ids)
        await db.commit()

# ── Summaries ─────────────────────────────────────────────────────────────────

async def save_summary(uid: int, content: str):
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT INTO summaries (user_id,content) VALUES (?,?)", (uid, content))
        await db.commit()

async def get_latest_summary(uid: int):
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT content FROM summaries WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,))
        row = await cur.fetchone(); return row[0] if row else None

# ── State ─────────────────────────────────────────────────────────────────────

async def load_state(uid: int):
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT state_json FROM user_states WHERE user_id=?", (uid,))
        row = await cur.fetchone(); return json.loads(row[0]) if row else None

async def save_state(uid: int, state: dict):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """INSERT INTO user_states (user_id,state_json) VALUES (?,?)
               ON CONFLICT(user_id) DO UPDATE SET state_json=excluded.state_json,
               updated_at=datetime('now')""",
            (uid, json.dumps(state)))
        await db.commit()

# ── User Profile ──────────────────────────────────────────────────────────────

async def get_user_profile(uid: int) -> dict:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT * FROM user_profiles WHERE user_id=?", (uid,))
        row = await cur.fetchone()
        if not row:
            return {"severity_level":"medium","primary_issue":"unknown",
                    "effective_scenarios":[],"ineffective_scenarios":[]}
        cols = [d[0] for d in cur.description]
        d = dict(zip(cols, row))
        d["effective_scenarios"]   = json.loads(d.get("effective_scenarios","[]"))
        d["ineffective_scenarios"] = json.loads(d.get("ineffective_scenarios","[]"))
        d["history_deltas"]        = json.loads(d.get("history_deltas","[]"))
        return d

async def update_user_profile(uid: int, scenario: str, delta: int, was_effective: bool):
    profile = await get_user_profile(uid)
    eff = profile.get("effective_scenarios", [])
    ine = profile.get("ineffective_scenarios", [])
    deltas = profile.get("history_deltas", [])
    if was_effective and scenario not in eff: eff.append(scenario)
    if not was_effective and scenario not in ine: ine.append(scenario)
    deltas.append(delta)
    if len(deltas) > 50: deltas = deltas[-50:]
    avg = sum(deltas[-7:]) / len(deltas[-7:]) if deltas else 0
    severity = "high" if avg < 1 else ("low" if avg > 3 else "medium")
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """INSERT INTO user_profiles
               (user_id,effective_scenarios,ineffective_scenarios,history_deltas,
                severity_level,total_sessions)
               VALUES (?,?,?,?,?,1)
               ON CONFLICT(user_id) DO UPDATE SET
                   effective_scenarios=excluded.effective_scenarios,
                   ineffective_scenarios=excluded.ineffective_scenarios,
                   history_deltas=excluded.history_deltas,
                   severity_level=excluded.severity_level,
                   total_sessions=total_sessions+1,
                   updated_at=datetime('now')""",
            (uid, json.dumps(eff), json.dumps(ine), json.dumps(deltas), severity))
        await db.commit()

# ── Interventions ─────────────────────────────────────────────────────────────

async def start_intervention(uid: int, objective: str, scenario: str,
                              practice_id: str, practice_version: str,
                              selection_reason: dict, before_score: int,
                              stage: str = "OPEN", readiness: str = "MEDIUM",
                              capacity: float = 1.0, ab_variant: str = "control",
                              router_version: str = "2.0") -> int:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            """INSERT INTO intervention_results
               (user_id,session_objective,scenario,practice_id,practice_version,
                router_version,ab_variant,selection_reason_json,stage,readiness,
                capacity,before_score)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uid, objective, scenario, practice_id, practice_version,
             router_version, ab_variant, json.dumps(selection_reason),
             stage, readiness, capacity, before_score))
        await db.commit(); return cur.lastrowid

async def finish_intervention(record_id: int, after_score: int,
                               feedback_rating: int, confidence_score: float,
                               engagement_metrics: dict):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """UPDATE intervention_results SET
               after_score=?, feedback_rating=?,
               confidence_score=?, engagement_metrics_json=?
               WHERE id=?""",
            (after_score, feedback_rating, confidence_score,
             json.dumps(engagement_metrics), record_id))
        await db.commit()

async def log_followup_24h(record_id: int, score: int):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "UPDATE intervention_results SET follow_up_24h_score=? WHERE id=?",
            (score, record_id))
        await db.commit()

# ── Adverse Events ────────────────────────────────────────────────────────────

async def log_adverse_event(uid: int, intervention_id: int, practice_id: str,
                             practice_version: str, event_type: str,
                             description: str, initial_delta: int,
                             risk_level: str = "low"):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """INSERT INTO adverse_events
               (user_id,intervention_id,practice_id,practice_version,
                event_type,description,initial_delta,risk_level)
               VALUES (?,?,?,?,?,?,?,?)""",
            (uid, intervention_id, practice_id, practice_version,
             event_type, description, initial_delta, risk_level))
        await db.commit()

# ── Router Decision Log ───────────────────────────────────────────────────────

async def log_router_decision(uid: int, state: dict, risk_score: int,
                               risk_cats: list, stage: str, readiness: str,
                               capacity: float, scenario: str, practice: str,
                               ab_variant: str, router_version: str = "2.0"):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """INSERT INTO router_decision_logs
               (user_id,state_snapshot,risk_score,risk_categories,stage,readiness,
                capacity,scenario_chosen,practice_chosen,router_version,ab_variant)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (uid, json.dumps(state), risk_score, ",".join(risk_cats),
             stage, readiness, capacity, scenario, practice, router_version, ab_variant))
        await db.commit()

# ── Moderation ────────────────────────────────────────────────────────────────

async def log_moderation(uid: int, username: str, first_name: str,
                          risk_level: str, risk_score: int, risk_cats: list,
                          message_text: str, action_taken: str,
                          risk_implicit: bool = False):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """INSERT INTO moderation_logs
               (user_id,username,first_name,risk_level,risk_score,risk_cats,
                risk_implicit,message_text,action_taken)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (uid, username, first_name, risk_level, risk_score,
             ",".join(risk_cats), int(risk_implicit), message_text, action_taken))
        await db.commit()

async def log_validator_block(uid: int, reason: str, blocked_text: str):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO validator_blocks (user_id,reason,blocked_text) VALUES (?,?,?)",
            (uid, reason, blocked_text))
        await db.commit()

async def log_disambiguation(uid: int, message_text: str, phrase: str, mode: str):
    """Record an ambiguity-triggered clarifying question (v3 hotfix monitoring)."""
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO disambiguation_events (user_id,message_text,phrase,mode)"
            " VALUES (?,?,?,?)",
            (uid, message_text[:500], phrase, mode))
        await db.commit()

# ── Trajectory / profile support (§4/§5) ──────────────────────────────────────

async def get_user_message_count(uid: int) -> int:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT message_count FROM users WHERE id=?", (uid,))
        row = await cur.fetchone()
        return row[0] if row else 0

async def get_last_crisis_at(uid: int, window_hours: int = 24) -> str | None:
    """ISO timestamp of the most recent crisis event within the window, or None."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT created_at FROM crisis_events WHERE user_id=?"
            f"  AND created_at > datetime('now', '-{int(window_hours)} hours')"
            " ORDER BY id DESC LIMIT 1", (uid,))
        row = await cur.fetchone()
        return row[0] if row else None

_PROFILE_COLUMNS = [
    "loneliness_value","loneliness_confidence","hopelessness_value","hopelessness_confidence",
    "self_criticism_value","self_criticism_confidence","anxiety_value","anxiety_confidence",
    "social_support_value","social_support_confidence","future_orientation_value",
    "future_orientation_confidence","energy_value","energy_confidence","sleep_problems_value",
    "sleep_problems_confidence","crisis_risk","mood_trend","main_themes",
    "coping_strategies_used","messages_analyzed","last_updated",
]

async def save_profile(uid: int, fields: dict) -> None:
    """Upsert a profile row + append a history snapshot. `fields` keys == columns."""
    import json
    cols = ["user_id"] + _PROFILE_COLUMNS
    vals = [uid] + [fields.get(c) for c in _PROFILE_COLUMNS]
    placeholders = ",".join("?" for _ in cols)
    updates = ",".join(f"{c}=excluded.{c}" for c in _PROFILE_COLUMNS)
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            f"INSERT INTO user_psychology_profile ({','.join(cols)}) VALUES ({placeholders})"
            f" ON CONFLICT(user_id) DO UPDATE SET {updates}", vals)
        await db.execute(
            "INSERT INTO psychology_profile_history (user_id,snapshot_json) VALUES (?,?)",
            (uid, json.dumps(fields, ensure_ascii=False)))
        await db.commit()

async def get_profile(uid: int) -> dict | None:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            f"SELECT {','.join(_PROFILE_COLUMNS)} FROM user_psychology_profile WHERE user_id=?",
            (uid,))
        row = await cur.fetchone()
    if not row:
        return None
    return dict(zip(_PROFILE_COLUMNS, row))

async def delete_profile(uid: int) -> None:
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM user_psychology_profile WHERE user_id=?", (uid,))
        await db.execute("DELETE FROM psychology_profile_history WHERE user_id=?", (uid,))
        await db.commit()

def sync_get_profile(uid: int) -> dict | None:
    conn = _conn()
    cur = conn.execute(
        f"SELECT {','.join(_PROFILE_COLUMNS)} FROM user_psychology_profile WHERE user_id=?",
        (uid,))
    row = cur.fetchone(); conn.close()
    return dict(zip(_PROFILE_COLUMNS, row)) if row else None

def sync_get_profile_history(uid: int, limit: int = 30) -> list:
    conn = _conn()
    cur = conn.execute(
        "SELECT snapshot_json, created_at FROM psychology_profile_history"
        " WHERE user_id=? ORDER BY id DESC LIMIT ?", (uid, limit))
    rows = cur.fetchall(); conn.close()
    return [(r[0], r[1]) for r in rows]

# ── Silence Engine push state (Epic 3) ────────────────────────────────────────

async def set_mute(uid: int, mode: str, until_iso: str | None = None) -> None:
    """mode: 'none' | 'forever' | 'until' (with until_iso UTC 'YYYY-MM-DD HH:MM:SS')."""
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """INSERT INTO push_settings (user_id,mute_mode,mute_until)
               VALUES (?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                   mute_mode=excluded.mute_mode, mute_until=excluded.mute_until,
                   updated_at=datetime('now')""",
            (uid, mode, until_iso))
        await db.commit()


async def reset_unanswered(uid: int) -> None:
    """Called when the user sends a message — clears the ignored-push counter."""
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "UPDATE push_settings SET consecutive_unanswered=0 WHERE user_id=?", (uid,))
        await db.commit()


async def record_push(uid: int, tier: str) -> None:
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT INTO push_log (user_id,tier) VALUES (?,?)", (uid, tier))
        await db.execute(
            """INSERT INTO push_settings (user_id,consecutive_unanswered)
               VALUES (?,1)
               ON CONFLICT(user_id) DO UPDATE SET
                   consecutive_unanswered=consecutive_unanswered+1,
                   updated_at=datetime('now')""",
            (uid,))
        await db.commit()


async def get_push_candidates() -> list:
    """Users inactive ≥12h who aren't permanently muted:
    (uid, last_seen, lang, tz_offset, tz_set)."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            """SELECT u.id, u.last_seen, u.language, COALESCE(u.tz_offset,0),
                      COALESCE(u.tz_set,0)
               FROM users u
               LEFT JOIN push_settings p ON p.user_id = u.id
               WHERE u.last_seen <= datetime('now','-12 hours')
                 AND COALESCE(p.mute_mode,'none') != 'forever'""")
        return await cur.fetchall()


async def get_push_context(uid: int) -> dict:
    """Everything decide_push() needs for one user (raw strings; caller parses)."""
    async with aiosqlite.connect(DB) as db:
        ps = await (await db.execute(
            "SELECT mute_mode,mute_until,consecutive_unanswered"
            " FROM push_settings WHERE user_id=?", (uid,))).fetchone()
        crisis = await (await db.execute(
            "SELECT MAX(created_at) FROM crisis_events WHERE user_id=?", (uid,))).fetchone()
        logs = await (await db.execute(
            "SELECT tier,created_at FROM push_log WHERE user_id=?"
            " AND created_at >= datetime('now','-90 days')", (uid,))).fetchall()
    return {
        "mute_mode": ps[0] if ps else "none",
        "mute_until": ps[1] if ps else None,
        "consecutive_unanswered": ps[2] if ps else 0,
        "last_crisis_at": crisis[0] if crisis else None,
        "push_log": [(r[0], r[1]) for r in logs],
    }


# ── GDPR memory (Epic 2) ──────────────────────────────────────────────────────

async def get_memory_overview(uid: int) -> dict:
    """What the bot currently remembers about a user (for /memory)."""
    async with aiosqlite.connect(DB) as db:
        msg_cnt = (await (await db.execute(
            "SELECT COUNT(*) FROM messages WHERE user_id=?", (uid,))).fetchone())[0]
        summ = await (await db.execute(
            "SELECT content FROM summaries WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (uid,))).fetchone()
        has_state = (await (await db.execute(
            "SELECT COUNT(*) FROM user_states WHERE user_id=?", (uid,))).fetchone())[0]
        profile = await (await db.execute(
            "SELECT primary_issue,total_sessions FROM user_profiles WHERE user_id=?",
            (uid,))).fetchone()
    return {
        "message_count": msg_cnt,
        "summary": summ[0] if summ else None,
        "has_state": bool(has_state),
        "primary_issue": profile[0] if profile else None,
        "total_sessions": profile[1] if profile else 0,
    }


async def forget_all(uid: int) -> None:
    """GDPR right-to-erasure: wipe conversational memory for a user.

    Deletes messages, summaries, rolling state and the learned profile.
    crisis_events are intentionally kept (safety/duty-of-care record), not
    conversational content."""
    async with aiosqlite.connect(DB) as db:
        for table in ("messages", "summaries", "user_states", "user_profiles",
                      "emotion_journal_entries", "cbt_journal_entries",
                      "checkin_logs", "journal_settings"):
            await db.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
        await db.commit()


# ── Crisis Events (Epic 1) ────────────────────────────────────────────────────

async def log_crisis_event(uid: int, level: str, risk_score: int,
                            categories: list, message_excerpt: str,
                            lang: str = "ru", admin_notified: bool = False) -> int:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            """INSERT INTO crisis_events
               (user_id,level,risk_score,categories,message_excerpt,lang,admin_notified)
               VALUES (?,?,?,?,?,?,?)""",
            (uid, level, risk_score, ",".join(categories), message_excerpt,
             lang, int(admin_notified)))
        await db.commit(); return cur.lastrowid


async def log_crisis_delivery(event_id, user_id: int, kind: str,
                              level_delivered: str, telegram_error=None) -> None:
    """v6 §6.2 — record the outcome of a crisis send (one row per send).
    level_delivered ∈ {rich, plain, minimal, none}. Best-effort: a logging
    failure must never affect the crisis path."""
    try:
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "INSERT INTO crisis_message_delivery_log "
                "(event_id,user_id,kind,level_delivered,telegram_error) VALUES (?,?,?,?,?)",
                (event_id, user_id, kind, level_delivered,
                 (telegram_error[:300] if telegram_error else None)))
            await db.commit()
    except Exception as e:
        print(f"[delivery-log] failed event={event_id} uid={user_id}: {e}")


# Placeholders that mean "no real influence recorded" — a trace row made of these
# is a lie about what actually drove the reply. Kept independent of
# traced_response._PLACEHOLDERS on purpose: the WRITER must reject garbage even if
# called directly (bypassing the builder), not merely trust its caller.
_TRACE_PLACEHOLDERS = {
    "", "none", "n/a", "na", "null", "nil", "placeholder", "influence: none",
    "no influence", "-", "todo", "tbd", "unknown",
}


def _validate_trace_row(response_id: str, user_id, influence_type: str,
                        source_id: str, human_readable: str) -> None:
    if not response_id or not str(response_id).strip():
        raise ValueError("influence_trace: response_id must not be empty")
    if user_id is None:
        raise ValueError("influence_trace: user_id must not be None (trace must be attributable)")
    if not influence_type or not str(influence_type).strip():
        raise ValueError("influence_trace: influence_type must not be empty")
    sid = str(source_id or "").strip()
    hr = str(human_readable or "").strip()
    if not sid or sid.lower() in _TRACE_PLACEHOLDERS:
        raise ValueError(f"influence_trace: source_id is empty/placeholder ({source_id!r})")
    if not hr or hr.lower() in _TRACE_PLACEHOLDERS:
        raise ValueError(f"influence_trace: human_readable is empty/placeholder ({human_readable!r})")
    if sid not in hr:
        raise ValueError(
            f"influence_trace: human_readable ({human_readable!r}) does not name "
            f"source_id ({source_id!r}) — trace would not be reviewable")


async def log_influence_trace(response_id: str, user_id, rows: list) -> None:
    """A1 (PR 0) — persist the influence trace for ONE reply. `rows` is a list of
    (influence_type, source_id, human_readable) tuples.

    UNLIKE the best-effort delivery log, this DELIBERATELY RAISES on failure: the
    traced_response_builder awaits it BEFORE sending, and a persist failure MUST
    block the latent reply (fail-closed). Never swallow the exception here.

    VALIDATES every row before writing ANYTHING (all-or-nothing): empty/placeholder
    source_id or human_readable, or a human_readable that doesn't name its
    source_id, raises ValueError and nothing is persisted — even if called directly,
    bypassing traced_response_builder's own content_ful() check."""
    for (it, sid, hr) in rows:
        _validate_trace_row(response_id, user_id, it, sid, hr)
    async with aiosqlite.connect(DB) as db:
        await db.executemany(
            "INSERT INTO influence_trace "
            "(response_id,user_id,influence_type,source_id,human_readable) "
            "VALUES (?,?,?,?,?)",
            [(response_id, user_id, it, sid, hr) for (it, sid, hr) in rows])
        await db.commit()


async def get_influence_trace(response_id: str) -> list:
    """Return the persisted influence rows for a response_id (psychologist review /
    tests). Ordered by id."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT influence_type,source_id,human_readable FROM influence_trace "
            "WHERE response_id=? ORDER BY id", (response_id,))
        return [tuple(r) for r in await cur.fetchall()]


async def save_cbt_entry(uid: int, data: dict, lang: str = "ru") -> int:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "INSERT INTO cbt_journal_entries (user_id,situation,automatic_thought,"
            "emotion,intensity,evidence_for,evidence_against,realistic_thought,change,lang)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (uid, data.get("situation"), data.get("automatic_thought"),
             data.get("emotion"), data.get("intensity"), data.get("evidence_for"),
             data.get("evidence_against"), data.get("realistic_thought"),
             data.get("change"), lang))
        await db.commit(); return cur.lastrowid


async def get_emotion_entries_since(uid: int, days: int = 7) -> list:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT feeling,intensity,event,need,action,outcome,created_at"
            " FROM emotion_journal_entries WHERE user_id=?"
            f"  AND created_at > datetime('now', '-{int(days)} days') ORDER BY id", (uid,))
        rows = await cur.fetchall()
    keys = ("feeling", "intensity", "event", "need", "action", "outcome", "created_at")
    return [dict(zip(keys, r)) for r in rows]


async def get_checkin_logs_since(uid: int, days: int = 7) -> list:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT kind,value,created_at FROM checkin_logs WHERE user_id=?"
            f"  AND created_at > datetime('now', '-{int(days)} days') ORDER BY id", (uid,))
        rows = await cur.fetchall()
    return [dict(zip(("kind", "value", "created_at"), r)) for r in rows]


async def log_checkin(uid: int, kind: str, value: str) -> None:
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT INTO checkin_logs (user_id,kind,value) VALUES (?,?,?)",
                         (uid, kind, value))
        await db.commit()


async def set_tz_offset(uid: int, offset: int) -> None:
    """Record an explicit timezone choice (tz_set=1 so it won't be overridden by
    the language default)."""
    async with aiosqlite.connect(DB) as db:
        await db.execute("UPDATE users SET tz_offset=?, tz_set=1 WHERE id=?", (offset, uid))
        await db.commit()


async def get_user_tz(uid: int) -> tuple:
    """(tz_offset, tz_set, lang) for one user — fed to tz.effective_tz()."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT COALESCE(tz_offset,0), COALESCE(tz_set,0), COALESCE(language,'ru')"
            " FROM users WHERE id=?", (uid,))
        row = await cur.fetchone()
    return tuple(row) if row else (0, 0, "ru")


async def get_journal_settings(uid: int) -> dict:
    keys = ("morning_enabled", "morning_hour", "evening_enabled", "evening_hour",
            "last_morning", "last_evening")
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT " + ",".join(keys) + " FROM journal_settings WHERE user_id=?", (uid,))
        row = await cur.fetchone()
    if not row:
        return {"morning_enabled": 0, "morning_hour": 9, "evening_enabled": 0,
                "evening_hour": 21, "last_morning": None, "last_evening": None}
    return dict(zip(keys, row))


async def set_journal_settings(uid: int, **fields) -> None:
    cur = await get_journal_settings(uid)
    cur.update(fields)
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO journal_settings (user_id,morning_enabled,morning_hour,"
            "evening_enabled,evening_hour,last_morning,last_evening,updated_at)"
            " VALUES (?,?,?,?,?,?,?,datetime('now'))"
            " ON CONFLICT(user_id) DO UPDATE SET morning_enabled=excluded.morning_enabled,"
            " morning_hour=excluded.morning_hour, evening_enabled=excluded.evening_enabled,"
            " evening_hour=excluded.evening_hour, last_morning=excluded.last_morning,"
            " last_evening=excluded.last_evening, updated_at=datetime('now')",
            (uid, cur["morning_enabled"], cur["morning_hour"], cur["evening_enabled"],
             cur["evening_hour"], cur["last_morning"], cur["last_evening"]))
        await db.commit()


async def get_journal_reminder_users() -> list:
    """Users who opted into at least one journal reminder, with tz + last-sent."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT js.user_id, COALESCE(u.tz_offset,0), js.morning_enabled, js.morning_hour,"
            " js.evening_enabled, js.evening_hour, js.last_morning, js.last_evening,"
            " COALESCE(u.language,'ru'), COALESCE(u.tz_set,0)"
            " FROM journal_settings js JOIN users u ON u.id=js.user_id"
            " WHERE js.morning_enabled=1 OR js.evening_enabled=1")
        rows = await cur.fetchall()
    keys = ("user_id", "tz_offset", "morning_enabled", "morning_hour", "evening_enabled",
            "evening_hour", "last_morning", "last_evening", "lang", "tz_set")
    return [dict(zip(keys, r)) for r in rows]


async def export_journals(uid: int) -> dict:
    """All journal data for GDPR export."""
    out = {}
    async with aiosqlite.connect(DB) as db:
        for table in ("emotion_journal_entries", "cbt_journal_entries", "checkin_logs"):
            cur = await db.execute(f"SELECT * FROM {table} WHERE user_id=? ORDER BY id", (uid,))
            cols = [d[0] for d in cur.description]
            out[table] = [dict(zip(cols, r)) for r in await cur.fetchall()]
    return out


async def delete_journals(uid: int) -> None:
    async with aiosqlite.connect(DB) as db:
        for table in ("emotion_journal_entries", "cbt_journal_entries",
                      "checkin_logs", "journal_settings"):
            await db.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
        await db.commit()


async def save_emotion_entry(uid: int, data: dict, lang: str = "ru") -> int:
    """Persist one emotion-journal entry. `data` keys are EMOTION_FIELDS; missing
    fields (e.g. body skipped at ORANGE) are stored as NULL."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "INSERT INTO emotion_journal_entries"
            " (user_id,event,feeling,intensity,body,need,action,outcome,lang)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (uid, data.get("event"), data.get("feeling"), data.get("intensity"),
             data.get("body"), data.get("need"), data.get("action"),
             data.get("outcome"), lang))
        await db.commit(); return cur.lastrowid


async def log_review_flag(uid: int, flag_type: str, context: str,
                          rate_limit_days: int = 7) -> bool:
    """Insert a review flag unless the same type was raised for this user within
    the rate-limit window. Returns True if inserted, False if rate-limited."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM review_flags WHERE user_id=? AND flag_type=?"
            f"  AND created_at > datetime('now', '-{int(rate_limit_days)} days')",
            (uid, flag_type))
        if (await cur.fetchone())[0] > 0:
            return False
        await db.execute(
            "INSERT INTO review_flags (user_id,flag_type,context) VALUES (?,?,?)",
            (uid, flag_type, context[:500]))
        await db.commit()
        return True

def sync_unreviewed_flags(limit: int = 50) -> list:
    conn = _conn()
    cur = conn.execute(
        "SELECT id,user_id,flag_type,context,created_at FROM review_flags"
        " WHERE reviewed=0 ORDER BY id DESC LIMIT ?", (limit,))
    rows = cur.fetchall(); conn.close()
    return [tuple(r) for r in rows]

def sync_mark_flag_reviewed(flag_id: int) -> None:
    conn = _conn()
    conn.execute("UPDATE review_flags SET reviewed=1 WHERE id=?", (flag_id,))
    conn.commit(); conn.close()

async def log_toxic_validation_block(uid: int, matched: str, original_text: str) -> None:
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO toxic_validation_blocks (user_id,matched,original_text)"
            " VALUES (?,?,?)", (uid, matched, original_text[:500]))
        await db.commit()

def sync_toxic_blocks(limit: int = 50) -> list:
    conn = _conn()
    cur = conn.execute(
        "SELECT user_id,matched,original_text,created_at FROM toxic_validation_blocks"
        " ORDER BY id DESC LIMIT ?", (limit,))
    rows = cur.fetchall(); conn.close()
    return [tuple(r) for r in rows]

def sync_crisis_with_protective(limit: int = 50) -> list:
    conn = _conn()
    cur = conn.execute(
        "SELECT user_id,level,protective_factors_json,message_excerpt,created_at"
        " FROM crisis_events WHERE protective_factors_json IS NOT NULL"
        "   AND protective_factors_json != '[]' ORDER BY id DESC LIMIT ?", (limit,))
    rows = cur.fetchall(); conn.close()
    return [tuple(r) for r in rows]


async def set_crisis_protective_factors(event_id: int, factors: list) -> None:
    """Attach detected protective-factor categories to a crisis event (context
    only — never affects risk). Stored as a JSON array string."""
    import json
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "UPDATE crisis_events SET protective_factors_json=? WHERE id=?",
            (json.dumps(factors, ensure_ascii=False), event_id))
        await db.commit()


async def set_crisis_response(uid: int, response: str) -> None:
    """Record the user's self-report on their most recent unresolved event.

    'safe'  → resolved (stop follow-ups). 'still' → stays open (keep following up).
    """
    resolved = 1 if response == "safe" else 0
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT id FROM crisis_events WHERE user_id=? AND resolved=0"
            " ORDER BY id DESC LIMIT 1", (uid,))
        row = await cur.fetchone()
        if not row:
            return
        await db.execute(
            "UPDATE crisis_events SET user_response=?, resolved=? WHERE id=?",
            (response, resolved, row[0]))
        await db.commit()


async def get_active_crisis_events() -> list:
    """Unresolved events: (id, user_id, lang, created_at, stage, followups[list])."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT id,user_id,lang,created_at,COALESCE(crisis_stage,0),followups_json"
            " FROM crisis_events WHERE resolved=0")
        rows = await cur.fetchall()
    return [(r[0], r[1], r[2], r[3], r[4], json.loads(r[5] or "[]")) for r in rows]


async def get_active_crisis(uid: int, within_hours: int = 24):
    """The user's CURRENT crisis to gate on: most recent unresolved event created
    within `within_hours`. Returns (event_id, stage, lang) or None.

    The recency window is the lifecycle guard — a user who pressed a crisis
    button long ago and never tapped "I'm safe" is NOT blocked forever; after
    `within_hours` the gate releases (and the 7d follow-up auto-resolves it)."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT id,COALESCE(crisis_stage,0),lang FROM crisis_events"
            " WHERE user_id=? AND resolved=0"
            f"  AND created_at > datetime('now', '-{int(within_hours)} hours')"
            " ORDER BY id DESC LIMIT 1", (uid,))
        row = await cur.fetchone()
    return (row[0], row[1], row[2]) if row else None


async def bump_crisis_stage(event_id: int, target: int) -> bool:
    """ATOMIC monotonic stage raise. Sets stage to `target` only if current < target
    (so a stale/double tap is a no-op). Returns True iff it actually changed —
    callers use that as the once-only guard for admin alerts."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "UPDATE crisis_events SET crisis_stage=?"
            "  WHERE id=? AND COALESCE(crisis_stage,0) < ?",
            (target, event_id, target))
        await db.commit()
        return cur.rowcount > 0


async def get_crisis_stage(event_id: int) -> int:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT COALESCE(crisis_stage,0) FROM crisis_events WHERE id=?", (event_id,))
        row = await cur.fetchone()
    return row[0] if row else 0


async def resolve_crisis(event_id: int) -> None:
    async with aiosqlite.connect(DB) as db:
        await db.execute("UPDATE crisis_events SET resolved=1 WHERE id=?", (event_id,))
        await db.commit()


async def set_stage3_at(event_id: int) -> None:
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "UPDATE crisis_events SET stage3_at=datetime('now') WHERE id=? AND stage3_at IS NULL",
            (event_id,))
        await db.commit()


async def get_stage3_pending(min_minutes: int = 5) -> list:
    """Unresolved stage-3 events whose stage3_at is at least min_minutes ago:
    (id, user_id, lang, followups[list]). Drives the 5-10 min follow-up."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT id,user_id,lang,followups_json FROM crisis_events"
            " WHERE resolved=0 AND COALESCE(crisis_stage,0)>=3 AND stage3_at IS NOT NULL"
            f"  AND stage3_at <= datetime('now', '-{int(min_minutes)} minutes')")
        rows = await cur.fetchall()
    return [(r[0], r[1], r[2], json.loads(r[3] or "[]")) for r in rows]


async def auto_resolve_expired_crises(days: int = 7) -> int:
    """Lifecycle cleanup: events still unresolved after `days` are auto-resolved
    so they stop gating and stop follow-ups. Returns how many were closed."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "UPDATE crisis_events SET resolved=1 WHERE resolved=0"
            f"  AND created_at < datetime('now', '-{int(days)} days')")
        await db.commit()
        return cur.rowcount


async def mark_crisis_followup_sent(event_id: int, tag: str) -> None:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT followups_json FROM crisis_events WHERE id=?", (event_id,))
        row = await cur.fetchone()
        sent = json.loads(row[0] or "[]") if row else []
        if tag not in sent:
            sent.append(tag)
        await db.execute(
            "UPDATE crisis_events SET followups_json=? WHERE id=?",
            (json.dumps(sent), event_id))
        await db.commit()

# ── Response Quality (👍/👎) ──────────────────────────────────────────────────

async def save_response_quality(uid: int, message_id: int,
                                 scenario: str, ab_variant: str, rating: int):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """INSERT INTO response_quality
               (user_id,message_id,scenario,ab_variant,rating)
               VALUES (?,?,?,?,?)""",
            (uid, message_id, scenario, ab_variant, rating))
        await db.commit()

# ── Check-ins ─────────────────────────────────────────────────────────────────

async def set_checkin(uid: int, username: str, first_name: str,
                       enabled: bool, hour: int = 10, language: str = "ru"):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """INSERT INTO checkins (user_id,username,first_name,enabled,checkin_hour,language)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                   enabled=excluded.enabled, checkin_hour=excluded.checkin_hour,
                   language=excluded.language""",
            (uid, username, first_name, int(enabled), hour, language))
        await db.commit()

async def get_checkin_users() -> list:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT c.user_id,c.first_name,c.checkin_hour,c.language,"
            " COALESCE(u.tz_offset,0), COALESCE(u.tz_set,0)"
            " FROM checkins c LEFT JOIN users u ON u.id=c.user_id WHERE c.enabled=1")
        return await cur.fetchall()

async def update_last_checkin(uid: int):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "UPDATE checkins SET last_checkin=datetime('now') WHERE user_id=?", (uid,))
        await db.commit()

# ── Weekly Progress ───────────────────────────────────────────────────────────

async def update_weekly_progress(uid: int):
    year_week = datetime.now(timezone.utc).strftime("%Y-W%W")
    async with aiosqlite.connect(DB) as db:
        row = await (await db.execute(
            """SELECT COUNT(*), AVG(before_score), AVG(after_score),
                      AVG(after_score - before_score)
               FROM intervention_results
               WHERE user_id=? AND after_score IS NOT NULL
                 AND date(created_at) >= date('now','-7 days')""", (uid,))).fetchone()
        if not row or not row[0]: return
        cnt, avg_b, avg_a, avg_d = row
        ok = 1 if (avg_d or 0) > 0.5 and cnt >= 2 else 0
        await db.execute(
            """INSERT INTO weekly_progress_snapshots
               (user_id,year_week,avg_before,avg_after,avg_delta,sessions_count,
                confidence_threshold_passed)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT DO NOTHING""",
            (uid, year_week, avg_b, avg_a, avg_d, cnt, ok))
        await db.commit()

# ── Sync helpers (Flask) ──────────────────────────────────────────────────────

def _conn():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c

def sync_stats() -> dict:
    c = _conn().cursor()
    return {
        "total_users":      c.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "total_messages":   c.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
        "messages_today":   c.execute("SELECT COUNT(*) FROM messages WHERE date(created_at)=date('now')").fetchone()[0],
        "mod_total":        c.execute("SELECT COUNT(*) FROM moderation_logs").fetchone()[0],
        "mod_critical":     c.execute("SELECT COUNT(*) FROM moderation_logs WHERE risk_level='critical'").fetchone()[0],
        "mod_today":        c.execute("SELECT COUNT(*) FROM moderation_logs WHERE date(created_at)=date('now')").fetchone()[0],
        "interventions":    c.execute("SELECT COUNT(*) FROM intervention_results").fetchone()[0],
        "avg_improvement":  c.execute("SELECT ROUND(AVG(after_score-before_score),1) FROM intervention_results WHERE after_score IS NOT NULL").fetchone()[0] or 0,
        "adverse_events":   c.execute("SELECT COUNT(*) FROM adverse_events").fetchone()[0],
        "active_checkins":  c.execute("SELECT COUNT(*) FROM checkins WHERE enabled=1").fetchone()[0],
        "validator_blocks": c.execute("SELECT COUNT(*) FROM validator_blocks").fetchone()[0],
        "quality_positive": c.execute("SELECT COUNT(*) FROM intervention_results WHERE feedback_rating=1").fetchone()[0],
    }

def sync_daily(days: int = 7) -> list:
    conn = _conn()
    rows = conn.execute(
        "SELECT date(created_at) as day,COUNT(*) as cnt FROM messages"
        " WHERE created_at>=date('now',? || ' days') AND role='user'"
        " GROUP BY day ORDER BY day", (f"-{days}",)).fetchall()
    conn.close(); return [dict(r) for r in rows]

def sync_users(limit: int = 100) -> list:
    conn = _conn()
    rows = conn.execute(
        "SELECT id,username,first_name,language,first_seen,last_seen,message_count"
        " FROM users ORDER BY last_seen DESC LIMIT ?", (limit,)).fetchall()
    conn.close(); return [dict(r) for r in rows]

def sync_user(uid: int):
    conn = _conn()
    row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    conn.close(); return dict(row) if row else None

def sync_user_messages(uid: int, limit: int = 60) -> list:
    conn = _conn()
    rows = conn.execute(
        "SELECT role,content,scenario,lang,created_at,summarized FROM messages"
        " WHERE user_id=? ORDER BY id DESC LIMIT ?", (uid, limit)).fetchall()
    conn.close(); return [dict(r) for r in rows]

def sync_user_summary(uid: int):
    conn = _conn()
    row = conn.execute(
        "SELECT content FROM summaries WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
    conn.close(); return row[0] if row else None

def sync_moderation(limit: int = 300) -> list:
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM moderation_logs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close(); return [dict(r) for r in rows]

def sync_risk_breakdown() -> list:
    conn = _conn()
    rows = conn.execute(
        "SELECT risk_level,COUNT(*) as cnt FROM moderation_logs GROUP BY risk_level").fetchall()
    conn.close(); return [dict(r) for r in rows]

def sync_outcome_stats() -> list:
    conn = _conn()
    rows = conn.execute(
        """SELECT scenario,COUNT(*) as total,
                  ROUND(AVG(after_score-before_score),2) as avg_delta,
                  ROUND(100.0*SUM(CASE WHEN after_score>before_score THEN 1 ELSE 0 END)/COUNT(*),0) as pct_helped,
                  COUNT(follow_up_24h_score) as followup_count,
                  ROUND(AVG(follow_up_24h_score-before_score),2) as avg_24h_delta,
                  ROUND(AVG(confidence_score),2) as avg_confidence
           FROM intervention_results WHERE after_score IS NOT NULL
           GROUP BY scenario ORDER BY avg_delta DESC""").fetchall()
    conn.close(); return [dict(r) for r in rows]

def sync_ab_stats() -> list:
    conn = _conn()
    rows = conn.execute(
        """SELECT ab_variant,COUNT(*) as sessions,
                  ROUND(AVG(after_score-before_score),2) as avg_delta,
                  ROUND(100.0*SUM(CASE WHEN after_score>before_score THEN 1 ELSE 0 END)/COUNT(*),0) as pct_helped,
                  ROUND(AVG(confidence_score),2) as avg_confidence
           FROM intervention_results WHERE after_score IS NOT NULL
           GROUP BY ab_variant""").fetchall()
    conn.close(); return [dict(r) for r in rows]

def sync_adverse_events(limit: int = 100) -> list:
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM adverse_events ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close(); return [dict(r) for r in rows]

def sync_validator_blocks(limit: int = 50) -> list:
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM validator_blocks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close(); return [dict(r) for r in rows]

def sync_quality_stats() -> list:
    conn = _conn()
    rows = conn.execute(
        """SELECT scenario,
                  SUM(CASE WHEN feedback_rating=1 THEN 1 ELSE 0 END) as positive,
                  SUM(CASE WHEN feedback_rating=0 THEN 1 ELSE 0 END) as neutral,
                  SUM(CASE WHEN feedback_rating=-1 THEN 1 ELSE 0 END) as negative,
                  COUNT(*) as total
           FROM intervention_results
           WHERE feedback_rating IS NOT NULL
           GROUP BY scenario""").fetchall()
    conn.close(); return [dict(r) for r in rows]

_EXPORT_ALLOWED_TABLES = {
    "intervention_results", "router_decision_logs", "adverse_events",
    "moderation_logs", "response_quality", "validator_blocks",
    "weekly_progress_snapshots", "crisis_events", "disambiguation_events",
}

def sync_export_query_safe(table: str) -> tuple:
    """Export rows from a whitelisted table only. Table name cannot be a bind parameter in SQLite."""
    if table not in _EXPORT_ALLOWED_TABLES:
        raise ValueError(f"Table '{table}' is not in the export allowlist")
    conn = _conn()
    cur = conn.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT 1000")
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    conn.close()
    return cols, rows
