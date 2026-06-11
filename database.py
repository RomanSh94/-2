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

CREATE TABLE IF NOT EXISTS response_quality (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER,
    message_id      INTEGER,
    scenario        TEXT,
    ab_variant      TEXT,
    rating          INTEGER,
    created_at      TEXT DEFAULT (datetime('now'))
);
"""

async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.executescript(SCHEMA)
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
                        scenario: str = "open_chat", lang: str = "ru"):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO messages (user_id,role,content,scenario,lang) VALUES (?,?,?,?,?)",
            (uid, role, content, scenario, lang))
        await db.commit()

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
    """Users inactive ≥12h who aren't permanently muted: (uid, last_seen, lang)."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            """SELECT u.id, u.last_seen, u.language
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
        for table in ("messages", "summaries", "user_states", "user_profiles"):
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
    """Unresolved events: (id, user_id, lang, created_at, followups[list])."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT id,user_id,lang,created_at,followups_json"
            " FROM crisis_events WHERE resolved=0")
        rows = await cur.fetchall()
    return [(r[0], r[1], r[2], r[3], json.loads(r[4] or "[]")) for r in rows]


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
            "SELECT user_id,first_name,checkin_hour,language FROM checkins WHERE enabled=1")
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
    "weekly_progress_snapshots", "crisis_events",
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
