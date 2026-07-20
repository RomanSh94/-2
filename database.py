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
import logging
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
    -- Therapeutic Core Foundation: the Telegram card identity (chat + message
    -- id of the before-score offer), used ONLY as the atomic idempotency key
    -- below -- see idx_intervention_one_baseline_per_card, created after
    -- _apply_migrations (mirrors _migrate_questionnaire_response_uniqueness's
    -- own after-migrations placement, since an upgraded DB only gains these
    -- two columns via _MIGRATIONS, not via this CREATE TABLE).
    source_chat_id          INTEGER,
    source_message_id       INTEGER,
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

-- PR 1B-1: controlled_clinical_test acknowledgment. A CLINICIAN_TESTER must
-- explicitly acknowledge the test-mode notice before getting full product access
-- (access_control.has_full_access also requires a live reviewer mapping — this
-- table alone is not sufficient for access, by design; see access_control.py).
-- Consent/test-state, NOT a safety-audit record -> CASCADE_DELETE, not RETAIN.
CREATE TABLE IF NOT EXISTS tester_acknowledgments (
    user_id         INTEGER PRIMARY KEY,
    acknowledged_at TEXT DEFAULT (datetime('now'))
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

-- Questionnaire Core PR #1 — storage-only. No scores, no interpretation, no
-- diagnosis anywhere in this table pair. current_index drives resume; status
-- is the only state machine (active/completed/cancelled).
CREATE TABLE IF NOT EXISTS questionnaire_sessions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id               INTEGER NOT NULL,
    questionnaire_id      TEXT NOT NULL,
    questionnaire_version TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'active',  -- active | completed | cancelled
    current_index         INTEGER NOT NULL DEFAULT 0,
    started_at            TEXT DEFAULT (datetime('now')),
    completed_at          TEXT
);

-- answer_id/answer_value are stable tokens from the definition (e.g. an
-- option id and its scale value) -- NEVER the item/option display text.
CREATE TABLE IF NOT EXISTS questionnaire_responses (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL,
    session_id       INTEGER NOT NULL,
    questionnaire_id TEXT NOT NULL,
    item_id          TEXT NOT NULL,
    answer_id        TEXT NOT NULL,
    answer_value     TEXT,
    answered_at      TEXT DEFAULT (datetime('now'))
);

-- Workstream B (corrective pass) — atomic delivery claim for one DASS-21
-- discuss topic reply, keyed to the exact MENU CARD (source_chat_id +
-- source_message_id), not just (user_id, session_id, topic_id) -- otherwise
-- each topic would be usable only ONCE for the entire questionnaire session.
-- Reopening the discuss menu sends a NEW Telegram message (a new
-- source_message_id), which is a fresh, legitimate logical attempt.
--
-- 5-state machine (CHECK-constrained -- an unknown status value is rejected
-- at the DB layer regardless of application logic):
--   pending_before_send -> send_started -> delivered
--                       -> failed_before_send (retryable on the SAME card)
--   send_started        -> delivery_uncertain (Telegram raised; unknown
--                          whether it actually sent -- NEVER auto-reclaimed
--                          on this card; a NEW card can still retry)
--
-- No response text/prompt/LLM answer/subscale values are stored here, only
-- delivery bookkeeping (claim_dass21_discuss_reply / transition_dass21_
-- discuss_claim in database.py). No FOREIGN KEY: this codebase never enables
-- `PRAGMA foreign_keys` per connection (see e.g. crisis_events, which is
-- deliberately FK-less for the same reason), so a declared FK here would be
-- silently unenforced -- consistent with every other table in this schema.
CREATE TABLE IF NOT EXISTS dass21_discuss_claims (
    user_id           INTEGER NOT NULL,
    session_id        INTEGER NOT NULL,
    topic_id          TEXT NOT NULL
                       CHECK (topic_id IN ('measures', 'relate', 'next', 'specialist')),
    source_chat_id    INTEGER NOT NULL,
    source_message_id INTEGER NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending_before_send'
                       CHECK (status IN (
                           'pending_before_send', 'send_started', 'delivered',
                           'failed_before_send', 'delivery_uncertain')),
    response_id       TEXT NOT NULL CHECK (length(response_id) > 0),
    created_at        TEXT DEFAULT (datetime('now')),
    updated_at        TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, session_id, topic_id, source_chat_id, source_message_id)
);
CREATE INDEX IF NOT EXISTS idx_dass21_discuss_claims_stale
    ON dass21_discuss_claims(status, updated_at);

-- PR A — ordinary-user private invite access. Lets the owner send a single
-- invite link so a real Telegram user can self-register as an ordinary
-- product user (NOT owner, NOT clinician tester/reviewer). Additive to
-- access_control.has_full_access via user_has_active_access(); independent of
-- the test-only temp-invite mechanism in access_control.py. A blocked user
-- must stay blocked even if they reopen the invite link -- see
-- grant_user_access()'s ON CONFLICT DO NOTHING below.
CREATE TABLE IF NOT EXISTS user_access (
    user_id    INTEGER PRIMARY KEY,
    status     TEXT NOT NULL DEFAULT 'active',
    source     TEXT NOT NULL DEFAULT 'invite',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK(status IN ('active', 'blocked')),
    CHECK(source IN ('owner', 'invite', 'manual', 'migration'))
);
CREATE INDEX IF NOT EXISTS idx_user_access_status ON user_access(status);

-- First-user illustrated onboarding state (5-screen intro shown before the
-- existing mood entry). REAL VERSIONING: PRIMARY KEY is (user_id,
-- onboarding_version), NOT user_id alone -- a user can accumulate a row per
-- onboarding version across deployments (e.g. a legacy_exempt 'v1' row AND
-- a later 'v2' row), which a single-column PK could never represent. The
-- partial UNIQUE index below is the actual database invariant requested by
-- the versioning spec: "at most one ACTIVE onboarding per user, regardless of
-- version" -- SQLite enforces this at the engine level, not just in
-- application code, so even a bug in the Python layer cannot create two
-- concurrently-active rows for the same user.
--
-- card_chat_id/card_message_id/card_rendered_step persist WHICH Telegram
-- message is the user's current visible onboarding card and what step it
-- actually shows. This is what makes /start (and the ordinary-entry gate)
-- restart-safe AND flood-safe: resume always tries to EDIT that exact
-- message first, and only sends a new one if the edit fails -- see
-- onboarding.send_or_edit_onboarding_card. card_rendered_step can trail
-- BEHIND current_step (recoverable-pending state): a transition is committed
-- to current_step FIRST, then delivery is attempted; if delivery raises a
-- genuine network error, current_step is already correct but
-- card_rendered_step/card_message_id still point at the OLD card, so the next
-- /start (or gate hit) naturally retries delivering the untouched target step
-- by editing that same old card -- no separate "pending" flag needed.
--
-- Written ONLY when config.FIRST_USER_ONBOARDING_ENABLED is on:
--   * a genuinely new authorized user starts at ONBOARDING_VERSION, status
--     'active', current_step 1 and advances/skips through the screens;
--   * a legacy user with meaningful prior product use (see
--     database.get_onboarding_eligibility) is recorded as 'legacy_exempt'
--     so they are NEVER retro-forced through onboarding.
-- current_step is DB-range-checked (1..5) and status is a closed CHECK set, so
-- a stale/corrupt transition cannot land out of range. user_id declaratively
-- REFERENCES users(id): this repo does not enable SQLite FK enforcement on its
-- connections (no PRAGMA foreign_keys=ON anywhere), so the reference documents
-- intent only — actual erasure is driven by privacy_registry CASCADE_DELETE
-- (delete_all_personal_data), NOT by an FK cascade, exactly like every other
-- user-scoped table here.
-- Status lifecycle (spec item F -- an HONEST set, not a euphemism):
--   active           -- in progress, current_step is where the user is now.
--   completed        -- the user actually pressed Start on the final privacy
--                       step of THIS version. Only ever set by
--                       complete_onboarding(); never for an interrupted flow.
--   legacy_exempt    -- the user was EXEMPTED from ever seeing this version's
--                       screens (meaningful prior product use predates this
--                       version) -- they never went through it, so this is
--                       NOT "completed" under a different name.
--   superseded       -- this row WAS active, but a newer mandatory
--                       onboarding_version replaced it before the user
--                       finished -- never "completed", the user did not
--                       complete the flow.
--   cancelled        -- reserved for a future explicit user/owner cancel
--                       action; not produced by any code path today.
CREATE TABLE IF NOT EXISTS user_onboarding_state (
    user_id                        INTEGER NOT NULL REFERENCES users(id),
    onboarding_version             TEXT NOT NULL,
    status                         TEXT NOT NULL DEFAULT 'active',
    current_step                   INTEGER NOT NULL DEFAULT 1,
    started_at                     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at                     TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at                   TEXT,
    skipped_information_at         TEXT,
    privacy_notice_acknowledged_at TEXT,
    privacy_notice_version         TEXT,
    card_chat_id                   INTEGER,
    card_message_id                INTEGER,
    card_rendered_step             INTEGER,
    PRIMARY KEY (user_id, onboarding_version),
    CHECK(status IN ('active', 'completed', 'legacy_exempt', 'superseded', 'cancelled')),
    CHECK(current_step BETWEEN 1 AND 5)
);
-- The real "no double onboarding" invariant: at most one ACTIVE row per user,
-- across ALL versions. A partial unique index (WHERE status='active') allows
-- unlimited completed/legacy_exempt/superseded rows (one per version, over time) while
-- making a second concurrently-active row an IntegrityError at the engine
-- level.
CREATE UNIQUE INDEX IF NOT EXISTS idx_onboarding_one_active_per_user
    ON user_onboarding_state(user_id) WHERE status='active';

-- Independent, notice-scoped acknowledgement ledger. Corrects a real gap: the
-- previous design stored privacy_notice_version/privacy_notice_acknowledged_at
-- ONLY on user_onboarding_state rows, so acknowledgement was entangled with
-- onboarding_version/status/completed_at/legacy_exempt/superseded/active-row
-- bookkeeping -- a future PRIVACY_NOTICE_VERSION bump could not safely
-- re-prompt a user already settled on the same onboarding_version (that row's
-- primary key is taken; INSERT OR IGNORE would no-op). This table is keyed
-- ONLY by (user_id, notice_id, notice_version) -- no onboarding_version, no
-- status -- so an acknowledgement is a standalone fact, independent of
-- onboarding-content history entirely. One bounded notice_id ("privacy_notice")
-- is used today; the table is generic enough for a future second notice
-- without a schema change, but no multi-notice framework exists yet.
CREATE TABLE IF NOT EXISTS user_notice_acknowledgements (
    user_id         INTEGER NOT NULL REFERENCES users(id),
    notice_id       TEXT NOT NULL,
    notice_version  TEXT NOT NULL,
    acknowledged_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, notice_id, notice_version)
);

-- Voice and Adaptive Response UX — neutral response-delivery UI preference,
-- nothing more. Stores ONLY the three closed-set choices below, never a raw
-- format-command message, never an inferred psychological trait ("lazy",
-- "dislikes reading", depression status, etc.). One row per user
-- (PRIMARY KEY user_id) since this is a single current preference, not a
-- history. CHECK constraints keep the value space closed at the engine
-- level, matching this repo's convention for every other closed-vocabulary
-- column (e.g. user_onboarding_state.status above).
CREATE TABLE IF NOT EXISTS user_response_preferences (
    user_id         INTEGER PRIMARY KEY REFERENCES users(id),
    response_format TEXT NOT NULL DEFAULT 'text',
    response_length TEXT NOT NULL DEFAULT 'normal',
    voice_language  TEXT NOT NULL DEFAULT 'auto',
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK(response_format IN ('text', 'voice', 'voice_and_concise_text')),
    CHECK(response_length IN ('concise', 'normal')),
    CHECK(voice_language IN ('ru', 'en', 'auto'))
);
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
    # Spec item F: the onboarding-content version and the privacy-notice
    # version are separate axes (a content-only screen fix must not force a
    # privacy re-acknowledgment, and vice versa). Additive/nullable, so the
    # generic ADD-COLUMN pass handles it regardless of which prior shape of
    # user_onboarding_state a given database has (fresh CREATE TABLE already
    # includes it; the pre-versioning-schema migration backfills it as NULL).
    ("user_onboarding_state", "privacy_notice_version", "TEXT"),
    # Therapeutic Core Foundation: atomic baseline-claim key (see
    # _migrate_intervention_baseline_uniqueness below for the actual
    # constraint -- these columns must exist first on an upgraded DB).
    ("intervention_results", "source_chat_id", "INTEGER"),
    ("intervention_results", "source_message_id", "INTEGER"),
]


async def _apply_migrations(db) -> None:
    for table, column, decl in _MIGRATIONS:
        cur = await db.execute(f"PRAGMA table_info({table})")
        cols = [r[1] for r in await cur.fetchall()]
        if column not in cols:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


async def _migrate_questionnaire_response_uniqueness(db) -> None:
    """PR #58 -- enforce the invariant UNIQUE(session_id, item_id): one CURRENT
    answer per item per session. Two idempotent steps, safe to rerun on every
    boot (same convention as _apply_migrations), inside init_db's transaction:

    1. Deterministic dedupe of legacy duplicates (produced by the pre-#58
       Back->revise INSERT bug): per (session_id, item_id) group keep ONLY the
       row with the highest primary-key id -- the most recent answer, since id
       is AUTOINCREMENT and a replacement always happened later. Older
       duplicate rows are removed; distinct items, other sessions, sessions
       themselves and current_index are untouched.
    2. Create the UNIQUE index so a duplicate can never be inserted again
       (record_questionnaire_response upserts through this constraint).

    Logs only aggregate counts -- never response values or user ids."""
    cur = await db.execute(
        "SELECT COUNT(*) FROM questionnaire_responses WHERE id NOT IN ("
        "  SELECT MAX(id) FROM questionnaire_responses"
        "  GROUP BY session_id, item_id)")
    stale = (await cur.fetchone())[0]
    if stale:
        await db.execute(
            "DELETE FROM questionnaire_responses WHERE id NOT IN ("
            "  SELECT MAX(id) FROM questionnaire_responses"
            "  GROUP BY session_id, item_id)")
        logging.info(
            "questionnaire_responses dedupe: removed %d older duplicate row(s)",
            stale)
    await db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_qresponses_session_item "
        "ON questionnaire_responses(session_id, item_id)")


async def _migrate_intervention_baseline_uniqueness(db) -> None:
    """Therapeutic Core Foundation P1 fix -- cb_before's prior guard (an
    in-memory FSMContext check-then-act) is NOT atomic: two genuinely
    concurrent callback_query deliveries for the SAME offer (the Telegram
    card identified by chat_id+message_id) can both read the FSM state
    before either writes it, both pass, and both call start_intervention,
    creating two baseline rows. This partial UNIQUE index is the real,
    engine-enforced invariant -- one baseline per (user, card), same
    "safe on every boot, run after _apply_migrations" convention as
    _migrate_questionnaire_response_uniqueness above (these two columns
    only exist on an upgraded DB once _apply_migrations has run). NULL
    values (rows predating this migration) are excluded from the
    constraint -- SQLite treats NULL as distinct in a UNIQUE index, so
    historical rows never collide with each other or with new rows.
    start_intervention's narrow ON CONFLICT(...) DO NOTHING target relies
    on this exact index; a rowcount of 0 there means "lost the atomic
    claim", not an error -- see start_intervention's docstring.

    Defensive dedupe before creating the index: this exact duplicate shape
    -- two existing rows already sharing a non-NULL (user_id, source_chat_id,
    source_message_id) -- is structurally impossible via any real app code
    path today, since these two columns are introduced by THIS migration and
    no prior release ever wrote a non-NULL value into them. It is kept
    anyway so CREATE UNIQUE INDEX cannot crash init_db() on a hand-edited or
    otherwise corrupted DB.

    Deliberately does NOT reuse _migrate_questionnaire_response_uniqueness's
    "keep MAX(id)" rule: that rule is correct there because a later
    questionnaire_responses row is a legitimate Back->revise correction and
    should win. Here the duplicated column is before_score -- the whole
    point of this migration is that the FIRST accepted baseline must never
    be silently replaced by a second write for the same card (see
    start_intervention's ON CONFLICT ... DO NOTHING below, and
    cb_before's "duplicate tap" contract). So this keeps MIN(id) -- the
    earliest-inserted, first-accepted row -- and discards later duplicates,
    matching that same semantics during a one-time migration cleanup. Only
    non-NULL-identity groups are touched; historical NULL-identity rows are
    never considered for dedupe."""
    cur = await db.execute(
        "SELECT COUNT(*) FROM intervention_results WHERE source_chat_id IS NOT NULL "
        "AND source_message_id IS NOT NULL AND id NOT IN ("
        "  SELECT MIN(id) FROM intervention_results "
        "  WHERE source_chat_id IS NOT NULL AND source_message_id IS NOT NULL "
        "  GROUP BY user_id, source_chat_id, source_message_id)")
    stale = (await cur.fetchone())[0]
    if stale:
        await db.execute(
            "DELETE FROM intervention_results WHERE source_chat_id IS NOT NULL "
            "AND source_message_id IS NOT NULL AND id NOT IN ("
            "  SELECT MIN(id) FROM intervention_results "
            "  WHERE source_chat_id IS NOT NULL AND source_message_id IS NOT NULL "
            "  GROUP BY user_id, source_chat_id, source_message_id)")
        logging.info(
            "intervention_results dedupe: removed %d duplicate-card row(s) "
            "(should be structurally unreachable via app code -- defensive only)",
            stale)
    await db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_intervention_one_baseline_per_card "
        "ON intervention_results(user_id, source_chat_id, source_message_id) "
        "WHERE source_chat_id IS NOT NULL AND source_message_id IS NOT NULL")


# ── Onboarding real-versioning migration (spec item E) ──────────────────────
# The FIRST shape user_onboarding_state ever shipped with had
# PRIMARY KEY(user_id) alone (one row per user, no per-version history, no
# card_chat_id/card_message_id/card_rendered_step columns). The CURRENT shape
# (see SCHEMA above) has PRIMARY KEY(user_id, onboarding_version) plus those
# three card_* columns and the one-active-per-user partial unique index.
# SQLite cannot ALTER a PRIMARY KEY in place, so this is a real two-step
# migration (rename-old -> let SCHEMA's CREATE TABLE IF NOT EXISTS build the
# new one -> copy rows across -> drop the renamed old table), NOT just an
# additive ALTER TABLE ADD COLUMN like _MIGRATIONS above.
#
# Both steps are individually idempotent and crash-safe to rerun (same
# "safe on every boot" convention as _apply_migrations /
# _migrate_questionnaire_response_uniqueness):
#   * step 1 (BEFORE executescript) only renames if the OLD shape is detected
#     (single-column PK, or missing a card_* column) -- a no-op if the table
#     doesn't exist yet, or already has the current shape;
#   * step 2 (AFTER executescript, once the new-shape table definitely
#     exists) only copies if the renamed old table is present, uses
#     INSERT OR IGNORE keyed on the real (user_id, onboarding_version)
#     primary key so a second run after a crash between the copy and the
#     final DROP TABLE never double-inserts, and only drops the renamed
#     table at the very end -- if the process dies between rename and drop,
#     the original data is still sitting intact under the renamed name and
#     the next init_db() call finishes the job from wherever it left off.
_OLD_ONBOARDING_TABLE = "_onboarding_state_pre_versioning"


async def _rename_old_onboarding_state_if_needed(db) -> None:
    cur = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='user_onboarding_state'")
    if not await cur.fetchone():
        return  # no table yet -- executescript's CREATE TABLE IF NOT EXISTS makes a fresh one
    cur = await db.execute("PRAGMA table_info(user_onboarding_state)")
    cols = await cur.fetchall()
    pk_cols = [c[1] for c in cols if c[5] > 0]  # col[5] = pk index, 0 = not part of PK
    col_names = {c[1] for c in cols}
    if pk_cols == ["user_id", "onboarding_version"] and "card_chat_id" in col_names:
        return  # already on the current shape -- nothing to migrate
    await db.execute(
        f"ALTER TABLE user_onboarding_state RENAME TO {_OLD_ONBOARDING_TABLE}")
    logging.info("user_onboarding_state: old schema detected, renamed for migration")


async def _finish_onboarding_state_migration(db) -> None:
    cur = await db.execute(
        f"SELECT name FROM sqlite_master WHERE type='table' AND name='{_OLD_ONBOARDING_TABLE}'")
    if not await cur.fetchone():
        return  # nothing was renamed in step 1 -- no old-schema table existed
    cur = await db.execute(f"PRAGMA table_info({_OLD_ONBOARDING_TABLE})")
    old_cols = {r[1] for r in await cur.fetchall()}

    def col_or_null(name: str) -> str:
        return name if name in old_cols else "NULL"

    cur = await db.execute(f"SELECT COUNT(*) FROM {_OLD_ONBOARDING_TABLE}")
    total = (await cur.fetchone())[0]
    # status enum rename (spec item F, honest lifecycle): the old schema's
    # 'legacy_completed' meant "exempted from onboarding due to prior product
    # use, never actually went through the flow" -- the CURRENT schema calls
    # that 'legacy_exempt' instead (it was never "completed"). Every other old
    # status value ('active', 'completed') is unchanged and still valid.
    await db.execute(
        "INSERT OR IGNORE INTO user_onboarding_state "
        "(user_id, onboarding_version, status, current_step, started_at, "
        " updated_at, completed_at, skipped_information_at, "
        " privacy_notice_acknowledged_at, privacy_notice_version, "
        " card_chat_id, card_message_id, card_rendered_step) "
        "SELECT user_id, onboarding_version, "
        "CASE status WHEN 'legacy_completed' THEN 'legacy_exempt' ELSE status END, "
        "current_step, started_at, "
        "updated_at, completed_at, skipped_information_at, "
        f"privacy_notice_acknowledged_at, {col_or_null('privacy_notice_version')}, "
        f"{col_or_null('card_chat_id')}, "
        f"{col_or_null('card_message_id')}, {col_or_null('card_rendered_step')} "
        f"FROM {_OLD_ONBOARDING_TABLE}")
    cur = await db.execute("SELECT changes()")
    inserted = (await cur.fetchone())[0]
    await db.execute(f"DROP TABLE {_OLD_ONBOARDING_TABLE}")
    logging.info(
        "user_onboarding_state migration: %d/%d row(s) copied to the new schema "
        "(remainder, if any, already present from a prior interrupted run)",
        inserted, total)


async def _backfill_notice_acknowledgements(db) -> None:
    """Conservative, proof-only backfill into user_notice_acknowledgements:
    copies an acknowledgement ONLY from a user_onboarding_state row that
    already recorded a REAL privacy_notice_acknowledged_at timestamp for a
    specific privacy_notice_version -- never inferred from status alone (a
    legacy_exempt row never showed the notice at all; an old completed row
    may have acknowledged a DIFFERENT notice version than the current one).
    Idempotent: INSERT OR IGNORE on the (user_id, notice_id, notice_version)
    primary key is a safe no-op on every subsequent boot."""
    await db.execute(
        "INSERT OR IGNORE INTO user_notice_acknowledgements "
        "(user_id, notice_id, notice_version, acknowledged_at) "
        "SELECT user_id, 'privacy_notice', privacy_notice_version, "
        "privacy_notice_acknowledged_at FROM user_onboarding_state "
        "WHERE privacy_notice_version IS NOT NULL "
        "AND privacy_notice_acknowledged_at IS NOT NULL")


async def init_db():
    async with aiosqlite.connect(DB) as db:
        await _rename_old_onboarding_state_if_needed(db)
        await db.executescript(SCHEMA)
        await _finish_onboarding_state_migration(db)
        await _apply_migrations(db)
        await _migrate_questionnaire_response_uniqueness(db)
        await _migrate_intervention_baseline_uniqueness(db)
        await _backfill_notice_acknowledgements(db)
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

async def get_stored_user_language(uid: int) -> str | None:
    """Raw stored language, or None if no `users` row exists yet -- distinct
    from get_user_language's "ru" default, which cannot tell a genuinely new
    user apart from one explicitly stored as ru. Used by cmd_start to decide
    whether to PRESERVE an existing explicit preference vs. resolve a fresh
    one from Telegram's language_code (a brand-new row, or an invalid/legacy
    stored value)."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT language FROM users WHERE id=?", (uid,))
        row = await cur.fetchone()
    return row[0] if row else None

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

async def get_last_assistant_message(uid: int) -> str | None:
    """The most recent assistant reply, from the EXISTING bounded
    conversation memory (messages table, already written by save_message on
    every ordinary reply) -- Voice and Adaptive Response UX reuses this for
    "voice-ify the last answer" (much-text/lazy-to-read/listen button)
    instead of persisting the answer a second time anywhere."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT content FROM messages WHERE user_id=? AND role='assistant'"
            " ORDER BY id DESC LIMIT 1", (uid,))
        row = await cur.fetchone()
    return row[0] if row else None

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

# ── Response Preferences (Voice and Adaptive Response UX) ────────────────────
_DEFAULT_RESPONSE_PREFERENCES = {
    "response_format": "text", "response_length": "normal", "voice_language": "auto",
}

async def get_response_preferences(uid: int) -> dict:
    """Always returns a full dict with the three closed-set fields, even for
    a user with no row yet (the schema defaults, not a DB round-trip write)."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT response_format, response_length, voice_language"
            " FROM user_response_preferences WHERE user_id=?", (uid,))
        row = await cur.fetchone()
    if not row:
        return dict(_DEFAULT_RESPONSE_PREFERENCES)
    return {"response_format": row[0], "response_length": row[1], "voice_language": row[2]}

async def set_response_preference(uid: int, **fields) -> None:
    """Upserts ONLY the neutral UI fields named in `fields` (a subset of
    response_format/response_length/voice_language) -- never a raw message,
    never an inferred trait. The CHECK constraints in the schema reject an
    out-of-vocabulary value at the engine level regardless of caller intent."""
    allowed = {"response_format", "response_length", "voice_language"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unknown response preference field(s): {unknown}")
    if not fields:
        return
    current = await get_response_preferences(uid)
    current.update(fields)
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """INSERT INTO user_response_preferences
               (user_id, response_format, response_length, voice_language, updated_at)
               VALUES (?,?,?,?,datetime('now'))
               ON CONFLICT(user_id) DO UPDATE SET
                   response_format=excluded.response_format,
                   response_length=excluded.response_length,
                   voice_language=excluded.voice_language,
                   updated_at=excluded.updated_at""",
            (uid, current["response_format"], current["response_length"],
             current["voice_language"]))
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
                              router_version: str = "2.0",
                              source_chat_id: int | None = None,
                              source_message_id: int | None = None) -> int | None:
    """Returns the new row's id, or None if source_chat_id/source_message_id
    were given and idx_intervention_one_baseline_per_card (see
    _migrate_intervention_baseline_uniqueness) rejected this as a duplicate
    claim on the same card -- i.e. this call LOST a genuine concurrent race
    against another callback for the same (user, chat, message). Callers that
    omit both (None, None) never hit the constraint and always get a row, as
    before -- this is additive, not a behavior change for existing callers.

    Uses a NARROW ON CONFLICT(...) target naming the exact partial index,
    not a blanket INSERT OR IGNORE: SQLite's conflict-resolution algorithm
    for OR IGNORE would silently absorb ANY constraint violation on this
    table (e.g. the before_score CHECK(BETWEEN 1 AND 10) -- reachable if a
    forged callback ever supplied an out-of-range score), which would
    misclassify a real data-integrity bug as an ordinary "lost the race"
    outcome. Naming the conflict target means only a violation of THIS
    index is swallowed (rowcount 0 => lost the claim); any other integrity
    violation still raises a genuine IntegrityError, matching the existing
    "fails loud, not fake-success" contract used elsewhere in this module."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            """INSERT INTO intervention_results
               (user_id,session_objective,scenario,practice_id,practice_version,
                router_version,ab_variant,selection_reason_json,stage,readiness,
                capacity,before_score,source_chat_id,source_message_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id,source_chat_id,source_message_id)
               WHERE source_chat_id IS NOT NULL AND source_message_id IS NOT NULL
               DO NOTHING""",
            (uid, objective, scenario, practice_id, practice_version,
             router_version, ab_variant, json.dumps(selection_reason),
             stage, readiness, capacity, before_score,
             source_chat_id, source_message_id))
        await db.commit()
        return cur.lastrowid if cur.rowcount else None

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


async def get_influence_trace_for_user(uid: int) -> list:
    """All influence_trace rows for one user, across every response_id (used by
    review_pack.generate_review_pack — the owner/psychologist view). Ordered by id."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT response_id,influence_type,source_id,human_readable,created_at "
            "FROM influence_trace WHERE user_id=? ORDER BY id", (uid,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in await cur.fetchall()]


# ── PR 1B-1: controlled_clinical_test acknowledgment ───────────────────────────

async def get_tester_acknowledged(uid: int) -> bool:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT 1 FROM tester_acknowledgments WHERE user_id=?", (uid,))
        return (await cur.fetchone()) is not None


async def set_tester_acknowledged(uid: int) -> None:
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO tester_acknowledgments (user_id) VALUES (?) "
            "ON CONFLICT(user_id) DO NOTHING", (uid,))
        await db.commit()


# ── PR A — ordinary-user private invite access ──────────────────────────────
async def grant_user_access(uid: int, source: str = "invite") -> None:
    """Idempotent upsert. A genuinely NEW row is inserted as active. An
    EXISTING row (active or blocked) is left completely untouched -- in
    particular, a previously-blocked user does NOT get silently re-activated
    by a repeat invite attempt; only an explicit future owner-driven unblock
    path (not built in this PR) may do that. ON CONFLICT DO NOTHING is what
    gives us this for free: the insert is a no-op whenever a row already
    exists, regardless of its current status."""
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO user_access (user_id, status, source) VALUES (?, 'active', ?) "
            "ON CONFLICT(user_id) DO NOTHING", (uid, source))
        await db.commit()


async def user_has_active_access(uid: int) -> bool:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT 1 FROM user_access WHERE user_id=? AND status='active'", (uid,))
        return (await cur.fetchone()) is not None


async def block_user_access(uid: int) -> None:
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "UPDATE user_access SET status='blocked', updated_at=datetime('now') "
            "WHERE user_id=?", (uid,))
        await db.commit()


async def unblock_user_access(uid: int) -> str:
    """Owner-driven reactivation of a previously blocked user (the explicit
    unblock path that grant_user_access deliberately does NOT provide). Only
    ever transitions an EXISTING blocked row back to active; it never inserts
    a row, so it cannot grant access to an unknown user or to a users-table
    row that was never invited. Idempotent. Returns a sanitized result code
    (never a user id):

        "reactivated"        blocked -> active (the one real state change)
        "already-active"     row already active (no-op)
        "no-existing-access" no user_access row exists (unknown/never-invited)

    Authorization (owner-only) is enforced by the caller, not here -- this is
    a pure data operation, mirroring grant/block."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT status FROM user_access WHERE user_id=?", (uid,))
        row = await cur.fetchone()
        if row is None:
            return "no-existing-access"
        if row[0] == "active":
            return "already-active"
        # WHERE status='blocked' makes concurrent double-unblock safe: only the
        # first flips the row; the PK guarantees no duplicate row is created.
        await db.execute(
            "UPDATE user_access SET status='active', updated_at=datetime('now') "
            "WHERE user_id=? AND status='blocked'", (uid,))
        await db.commit()
        return "reactivated"


# ── First-user illustrated onboarding state ─────────────────────────────────
# All transitions are single atomic UPDATEs guarded by a WHERE clause on the
# expected prior state (status/current_step/version). This is what makes stale
# callbacks, double taps and two concurrent callbacks safe WITHOUT an app-level
# lock: at most one UPDATE matches the guard, the rest are no-ops (rowcount 0).
# No transition ever moves current_step backward — each guard names the exact
# step it advances FROM. Onboarding metadata carries no personal content, so
# nothing here is logged.
#
# Real versioning (composite PK, see SCHEMA comment above): a user can have AT
# MOST ONE active row (any version, DB-enforced by the partial unique index),
# plus any number of completed/legacy_exempt/superseded rows (one per version
# they ever touched). Read helpers below distinguish "the row for THIS exact
# version" from "whatever row currently matters for this user".

_ONBOARDING_COLUMNS = (
    "user_id, onboarding_version, status, current_step, started_at, updated_at, "
    "completed_at, skipped_information_at, privacy_notice_acknowledged_at, "
    "privacy_notice_version, card_chat_id, card_message_id, card_rendered_step")


def _onboarding_row_to_dict(row) -> dict:
    return {
        "user_id": row[0], "onboarding_version": row[1], "status": row[2],
        "current_step": row[3], "started_at": row[4], "updated_at": row[5],
        "completed_at": row[6], "skipped_information_at": row[7],
        "privacy_notice_acknowledged_at": row[8], "privacy_notice_version": row[9],
        "card_chat_id": row[10], "card_message_id": row[11],
        "card_rendered_step": row[12],
    }


async def get_active_onboarding_state(uid: int) -> dict | None:
    """The single ACTIVE row for this user, any version, or None. At most one
    can ever exist (idx_onboarding_one_active_per_user) -- this is the real
    versioning invariant: "at most one active onboarding per user"."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            f"SELECT {_ONBOARDING_COLUMNS} FROM user_onboarding_state "
            "WHERE user_id=? AND status='active'", (uid,))
        row = await cur.fetchone()
    return _onboarding_row_to_dict(row) if row else None


async def get_any_onboarding_row(uid: int) -> dict | None:
    """The most recently updated row for this user, ANY version/status, or
    None if this user has never touched onboarding at all. Used to decide
    "has this user EVER been through/seen onboarding (any version)" without
    caring which specific version."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            f"SELECT {_ONBOARDING_COLUMNS} FROM user_onboarding_state "
            "WHERE user_id=? ORDER BY updated_at DESC, onboarding_version DESC LIMIT 1",
            (uid,))
        row = await cur.fetchone()
    return _onboarding_row_to_dict(row) if row else None


async def get_onboarding_state(uid: int, version: str | None = None) -> dict | None:
    """version=<str> -> the exact (uid, version) row, or None (does NOT fall
    back to another version). version=None -> "whatever matters right now":
    the active row if one exists (any version), else the most recent row
    (any version), else None. Kept for callers (including tests) that just
    want "the current state" without caring about version bookkeeping."""
    if version is not None:
        async with aiosqlite.connect(DB) as db:
            cur = await db.execute(
                f"SELECT {_ONBOARDING_COLUMNS} FROM user_onboarding_state "
                "WHERE user_id=? AND onboarding_version=?", (uid, version))
            row = await cur.fetchone()
        return _onboarding_row_to_dict(row) if row else None
    active = await get_active_onboarding_state(uid)
    if active is not None:
        return active
    return await get_any_onboarding_row(uid)


async def start_or_get_onboarding(uid: int, version: str) -> dict:
    """Atomically ensure an ACTIVE onboarding row exists for (uid, version),
    then return the row that actually represents this user's state now.
    INSERT OR IGNORE is the atomic primitive -- SQLite silently ignores this
    INSERT on EITHER conflict: the (user_id, onboarding_version) primary key
    (a concurrent /start racing to create the same row, OR a row already
    settled for this exact version), OR the partial unique index (another
    version is already active for this user -- must not happen in the normal
    call path, since callers only invoke this after confirming no active row
    exists, but stays safe under a race). Always starts at step 1 -- the
    privacy-notice-only flow (a settled/legacy user missing only the current
    privacy-notice acknowledgement) does NOT use this function at all; it
    never creates or touches an onboarding row (see
    onboarding_content.determine_onboarding_requirement /
    database.record_notice_acknowledgement). Callers must never call this
    without having already verified eligibility/absence of any row -- this
    function itself does not decide eligibility."""
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR IGNORE INTO user_onboarding_state "
            "(user_id, onboarding_version, status, current_step) "
            "VALUES (?, ?, 'active', 1)", (uid, version))
        await db.commit()
    state = await get_onboarding_state(uid, version)
    if state is not None:
        return state
    # The insert was ignored because ANOTHER version was already active
    # (race) -- surface whatever is actually active now, never raise.
    state = await get_active_onboarding_state(uid)
    assert state is not None
    return state


async def mark_onboarding_legacy_exempt(uid: int, version: str) -> dict:
    """Record a legacy user (meaningful prior product use, no row for this
    version) as legacy_exempt: they are EXEMPTED from ever seeing this
    version's onboarding screens, they did not "complete" anything (spec item
    F: an honest status, not a euphemism -- see the SCHEMA comment on
    user_onboarding_state). Idempotent via INSERT OR IGNORE — never clobbers
    an existing row for this exact version. completed_at is intentionally
    left NULL (nothing was completed); privacy_notice_version is also left
    NULL (the notice was never shown, so nothing was acknowledged)."""
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR IGNORE INTO user_onboarding_state "
            "(user_id, onboarding_version, status, current_step) "
            "VALUES (?, ?, 'legacy_exempt', 5)", (uid, version))
        await db.commit()
    state = await get_onboarding_state(uid, version)
    assert state is not None
    return state


async def supersede_onboarding_version(uid: int, version: str) -> bool:
    """Mark a STALE ACTIVE row for an OLDER onboarding_version as superseded
    (spec item F): a deployment bumped ONBOARDING_VERSION -- a mandatory
    update, e.g. a new privacy notice -- while this user's onboarding was in
    flight. This is deliberately NOT 'completed' and NOT 'legacy_exempt': the
    user did not finish the flow and was not exempt from it, the flow itself
    was superseded out from under them. Guarded to `version` AND
    status='active' so it can never touch the current version's row or an
    already-closed row. Returns True iff it actually superseded a row.
    Callers are responsible for then starting the CURRENT mandatory version
    for this user (see bot.cmd_start) -- this function only closes out the
    old row, it never starts a new one itself."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "UPDATE user_onboarding_state SET status='superseded', "
            "updated_at=datetime('now') "
            "WHERE user_id=? AND onboarding_version=? AND status='active'",
            (uid, version))
        await db.commit()
        return cur.rowcount == 1


async def advance_onboarding_step(uid: int, version: str,
                                  from_step: int, to_step: int) -> bool:
    """Move an ACTIVE onboarding from exactly `from_step` to `to_step`. Returns
    True iff this call performed the move. The WHERE guard on current_step makes
    it idempotent (a replayed/stale callback whose from_step no longer matches is
    a no-op) and forward-only (callers only ever pass to_step > from_step)."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "UPDATE user_onboarding_state SET current_step=?, updated_at=datetime('now') "
            "WHERE user_id=? AND onboarding_version=? AND status='active' "
            "AND current_step=?",
            (to_step, uid, version, from_step))
        await db.commit()
        return cur.rowcount == 1


async def skip_onboarding_to_privacy(uid: int, version: str, last_step: int = 5) -> bool:
    """Jump an ACTIVE onboarding from any informational step (< last_step) straight
    to the final privacy step. Records skipped_information_at once (COALESCE keeps
    the first skip timestamp). Never completes onboarding and never bypasses the
    privacy step. Idempotent: a second skip once already at last_step is a no-op."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "UPDATE user_onboarding_state SET current_step=?, "
            "skipped_information_at=COALESCE(skipped_information_at, datetime('now')), "
            "updated_at=datetime('now') "
            "WHERE user_id=? AND onboarding_version=? AND status='active' "
            "AND current_step<?",
            (last_step, uid, version, last_step))
        await db.commit()
        return cur.rowcount == 1


async def complete_onboarding(uid: int, version: str, last_step: int = 5,
                              privacy_notice_version: str | None = None) -> bool:
    """Finalize onboarding from the ACTIVE final step exactly once. Records
    completed_at AND privacy_notice_acknowledged_at/privacy_notice_version on
    THIS row (audit-trail context: which onboarding flow the user completed
    when they acknowledged it) AND, independently, an entry in
    user_notice_acknowledgements (the actual source of truth read by
    has_privacy_notice_ack -- see that table's SCHEMA comment). Returns True
    only for the single call that actually completes it; a double-tapped
    Start returns False (rowcount 0) and must not re-open mood entry nor
    insert a second acknowledgement."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "UPDATE user_onboarding_state SET status='completed', "
            "completed_at=datetime('now'), "
            "privacy_notice_acknowledged_at=datetime('now'), "
            "privacy_notice_version=?, "
            "updated_at=datetime('now') "
            "WHERE user_id=? AND onboarding_version=? AND status='active' "
            "AND current_step=?",
            (privacy_notice_version, uid, version, last_step))
        completed = cur.rowcount == 1
        if completed and privacy_notice_version:
            await db.execute(
                "INSERT OR IGNORE INTO user_notice_acknowledgements "
                "(user_id, notice_id, notice_version) VALUES (?, ?, ?)",
                (uid, _NOTICE_ID_PRIVACY, privacy_notice_version))
        await db.commit()
        return completed


# ── Independent, notice-scoped acknowledgement (spec item F correction) ────
# Backed by user_notice_acknowledgements, NOT user_onboarding_state -- see
# that table's SCHEMA comment for why this must not depend on
# onboarding_version/status/completed_at/legacy_exempt/superseded/active rows.
_NOTICE_ID_PRIVACY = "privacy_notice"


async def record_notice_acknowledgement(uid: int, notice_id: str, notice_version: str) -> bool:
    """Idempotent: INSERT OR IGNORE on the (user_id, notice_id, notice_version)
    primary key. Returns True only for the call that actually inserted a new
    row; a double tap returns False so the caller can refuse to re-run
    downstream progression (matches complete_onboarding's rowcount contract)."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "INSERT OR IGNORE INTO user_notice_acknowledgements "
            "(user_id, notice_id, notice_version) VALUES (?, ?, ?)",
            (uid, notice_id, notice_version))
        await db.commit()
        return cur.rowcount == 1


async def has_notice_acknowledgement(uid: int, notice_id: str, notice_version: str) -> bool:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT 1 FROM user_notice_acknowledgements "
            "WHERE user_id=? AND notice_id=? AND notice_version=? LIMIT 1",
            (uid, notice_id, notice_version))
        return (await cur.fetchone()) is not None


async def has_privacy_notice_ack(uid: int, notice_version: str) -> bool:
    """True iff this user has EVER acknowledged this EXACT privacy-notice
    version -- backed solely by user_notice_acknowledgements, independent of
    onboarding_version, status, completed_at, legacy_exempt, superseded, or
    any active onboarding row (spec item F correction: the previous
    implementation scanned user_onboarding_state rows directly, which
    structurally could not survive an independent notice-version bump)."""
    return await has_notice_acknowledgement(uid, _NOTICE_ID_PRIVACY, notice_version)


async def set_onboarding_card_ref(uid: int, version: str, step: int,
                                  chat_id: int, message_id: int) -> None:
    """Persist WHICH Telegram message is the user's current visible onboarding
    card, and what step it shows. Called ONLY after a confirmed-successful
    render (see onboarding.send_or_edit_onboarding_card / bot._render_onboarding).
    This is the durable half of the render/state recovery contract (spec item
    H): the transition (advance/skip/start/complete) is committed to
    current_step BEFORE delivery is attempted; this call records what was
    ACTUALLY delivered, so a delivery failure leaves card_rendered_step
    trailing current_step -- a safely resumable state, not corruption.
    Idempotent overwrite; guarded to the specific (uid, version) row so it can
    never attach a card reference to the wrong row."""
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "UPDATE user_onboarding_state SET card_chat_id=?, card_message_id=?, "
            "card_rendered_step=?, updated_at=datetime('now') "
            "WHERE user_id=? AND onboarding_version=?",
            (chat_id, message_id, step, uid, version))
        await db.commit()


# ── Onboarding eligibility (spec item C) ────────────────────────────────────
# Signal tables checked for "meaningful prior product use". Deliberately does
# NOT include `user_access` -- a user who was just invited (has an active
# user_access row) but has never actually used the product must still count
# as genuinely new. MUST be called BEFORE upsert_user() creates/touches the
# `users` row for this call, otherwise the users-row check is meaningless
# (upsert would have just created it, making every user "legacy").
_ONBOARDING_LEGACY_SIGNAL_TABLES = (
    ("messages", "user_id"),
    ("questionnaire_sessions", "user_id"),
    ("emotion_journal_entries", "user_id"),
    ("cbt_journal_entries", "user_id"),
    # `user_profiles` (the OLDER profile table, columns primary_issue/
    # severity_level/effective_scenarios/...) is written ONLY by
    # update_user_profile, which has no caller anywhere in the codebase --
    # checking it here can never actually fire. `user_psychology_profile` is
    # the table the LIVE product writes to (psychology_profile.py's
    # save_profile/maybe_update_profile, called from bot.pipeline() and from
    # bot.py's post-crisis bookkeeping) -- that is the real "does this user
    # have a profile" legacy signal. Both are listed: user_profiles is kept
    # in case anything is ever pointed back at it, but user_psychology_profile
    # is what makes "profile-only history counts as legacy" actually true
    # today, not just documented.
    ("user_profiles", "user_id"),
    ("user_psychology_profile", "user_id"),
    ("user_states", "user_id"),
    ("summaries", "user_id"),
)


async def get_onboarding_eligibility(uid: int) -> str:
    """Returns "new" or "legacy". "legacy" (meaningful prior product use) if
    ANY of: an existing `users` row, or any row in one of
    _ONBOARDING_LEGACY_SIGNAL_TABLES (messages, questionnaire sessions,
    emotion/CBT journal entries, profile, state, summary). "new" only when
    NONE of these exist -- in particular, a user who merely holds an active
    user_access invite grant but has never used the product returns "new"."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT 1 FROM users WHERE id=?", (uid,))
        if await cur.fetchone():
            return "legacy"
        for table, col in _ONBOARDING_LEGACY_SIGNAL_TABLES:
            cur = await db.execute(f"SELECT 1 FROM {table} WHERE {col}=? LIMIT 1", (uid,))
            if await cur.fetchone():
                return "legacy"
    return "new"


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

def sync_review_flag_uid(flag_id: int):
    """PR 1B-1: the dashboard needs to know WHOSE flag this is before marking it
    reviewed, so it can block a direct /safety/review/<id> URL hit for a
    CLINICIAN_TESTER's flag in controlled_clinical_test. Returns None if the
    flag doesn't exist."""
    conn = _conn()
    row = conn.execute("SELECT user_id FROM review_flags WHERE id=?", (flag_id,)).fetchone()
    conn.close()
    return row[0] if row else None

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

# ── PR 1A: Privacy & Data Governance — registry-driven export/delete ──────────
# Generic, driven by privacy_registry.PRIVACY_REGISTRY, so it stays complete as
# tables are added (a table missing from the registry is caught by
# tests/test_privacy_registry.py, not silently skipped here).

async def export_all_personal_data(uid: int) -> dict:
    """Full personal-data export for the owner: every INCLUDE-policy table, keyed
    by table name. Includes crisis_events/crisis_message_delivery_log (retained
    records are still the owner's own data — retained means not silently DELETED,
    not hidden from the owner)."""
    import privacy_registry as pr
    out = {}
    async with aiosqlite.connect(DB) as db:
        for name, entry in pr.PRIVACY_REGISTRY.items():
            if entry.export_policy != "INCLUDE":
                continue
            cur = await db.execute(
                f"SELECT * FROM {name} WHERE {entry.user_id_column}=? ORDER BY rowid",
                (uid,))
            cols = [d[0] for d in cur.description]
            out[name] = [dict(zip(cols, r)) for r in await cur.fetchall()]
    return out


async def delete_all_personal_data(uid: int) -> dict:
    """GDPR right-to-erasure across every registered table, per its delete_policy:
      CASCADE_DELETE -> row(s) removed;
      ANONYMIZE      -> PII columns cleared, row kept (currently only `users`);
      RETAIN         -> NOT touched; the summary records WHY, so a delete-all
                        request is never a silent no-op on safety/audit data.

    influence_trace is CASCADE_DELETE and is deleted alongside the rest in the
    SAME call, which is what avoids dangling references: a trace row that named a
    now-deleted entity does not survive as an orphan, because the trace row itself
    is gone too — not because source_id is nulled out.

    Returns {table: outcome} where outcome is an int row-count (CASCADE_DELETE),
    True (ANONYMIZE), or the retention reason string (RETAIN)."""
    import privacy_registry as pr
    summary: dict = {}
    async with aiosqlite.connect(DB) as db:
        for name, entry in pr.PRIVACY_REGISTRY.items():
            if entry.delete_policy == "RETAIN":
                summary[name] = f"RETAINED: {entry.reason}"
                continue
            if entry.delete_policy == "ANONYMIZE":
                if name == "users":
                    await db.execute(
                        "UPDATE users SET username=NULL, first_name=NULL WHERE id=?",
                        (uid,))
                    summary[name] = True
                else:  # pragma: no cover — no other ANONYMIZE table registered yet
                    raise NotImplementedError(f"ANONYMIZE not implemented for {name}")
                continue
            # CASCADE_DELETE
            cur = await db.execute(
                f"DELETE FROM {name} WHERE {entry.user_id_column}=?", (uid,))
            summary[name] = cur.rowcount
        await db.commit()
    return summary


async def preview_delete_all_personal_data(uid: int) -> dict:
    """PR 1B-2 — real, registry-driven preview of what delete_all_personal_data
    WOULD do, without deleting anything. No hardcoded table list: every table
    in PRIVACY_REGISTRY is covered because this loops the same registry
    delete_all_personal_data does.

    Returns {table: {"policy": ..., "row_count": ..., "retain_reason": ...}}.
    `retain_reason` is only present (non-None) for RETAIN tables. `row_count`
    is a plain COUNT(*) for this uid -- no raw rows, no message content, no
    excerpts of any kind are read or returned."""
    import privacy_registry as pr
    preview: dict = {}
    async with aiosqlite.connect(DB) as db:
        for name, entry in pr.PRIVACY_REGISTRY.items():
            cur = await db.execute(
                f"SELECT COUNT(*) FROM {name} WHERE {entry.user_id_column}=?", (uid,))
            (row_count,) = await cur.fetchone()
            preview[name] = {
                "policy": entry.delete_policy,
                "row_count": row_count,
                "retain_reason": entry.reason if entry.delete_policy == "RETAIN" else None,
            }
    return preview

# ── Questionnaire Core PR #1 — storage-only session/response CRUD ────────────

async def start_questionnaire_session(uid: int, questionnaire_id: str, version: str) -> int:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "INSERT INTO questionnaire_sessions "
            "(user_id, questionnaire_id, questionnaire_version, status, current_index) "
            "VALUES (?,?,?, 'active', 0)", (uid, questionnaire_id, version))
        await db.commit()
        return cur.lastrowid


async def get_active_questionnaire_session(uid: int) -> dict | None:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT id, questionnaire_id, questionnaire_version, current_index "
            "FROM questionnaire_sessions WHERE user_id=? AND status='active' "
            "ORDER BY id DESC LIMIT 1", (uid,))
        row = await cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "questionnaire_id": row[1],
            "questionnaire_version": row[2], "current_index": row[3]}


async def get_questionnaire_session(session_id: int) -> dict | None:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT id, user_id, questionnaire_id, questionnaire_version, status, current_index "
            "FROM questionnaire_sessions WHERE id=?", (session_id,))
        row = await cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "user_id": row[1], "questionnaire_id": row[2],
            "questionnaire_version": row[3], "status": row[4], "current_index": row[5]}


async def record_questionnaire_response(uid: int, session_id: int, questionnaire_id: str,
                                        item_id: str, answer_id: str, answer_value: str) -> None:
    # Idempotent per (session_id, item_id): re-answering an item -- e.g. after
    # pressing Back to REVISE an earlier answer -- must REPLACE the prior row,
    # not append a second one. A duplicate row would (a) inflate the generic
    # sum score and (b) make the exact clinical scorer (DASS) reject the
    # session as having a duplicate item, so it could never complete. A single
    # atomic UPSERT through the UNIQUE(session_id, item_id) index (created in
    # _migrate_questionnaire_response_uniqueness): no delete/insert window, no
    # answer loss on failure, race-safe under interleaved callbacks. Immutable
    # identity (user_id/session_id/questionnaire_id/item_id) is never updated.
    # Single-timestamp rule: answered_at reflects the LATEST answer time.
    # The stale-step guard in bot.py remains the step-level protection.
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO questionnaire_responses "
            "(user_id, session_id, questionnaire_id, item_id, answer_id, answer_value) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(session_id, item_id) DO UPDATE SET "
            "  answer_id = excluded.answer_id, "
            "  answer_value = excluded.answer_value, "
            "  answered_at = datetime('now')",
            (uid, session_id, questionnaire_id, item_id, answer_id, answer_value))
        await db.commit()


async def get_questionnaire_responses(session_id: int) -> list[dict]:
    """Read-only helper (PR B): all recorded responses for one session, oldest
    first. Used ONLY to compute an on-the-fly sum score for eligible synthetic
    questionnaires -- no score is ever cached or written back to a table."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT item_id, answer_id, answer_value FROM questionnaire_responses "
            "WHERE session_id=? ORDER BY id", (session_id,))
        rows = await cur.fetchall()
    return [{"item_id": r[0], "answer_id": r[1], "answer_value": r[2]} for r in rows]


async def advance_questionnaire_session(session_id: int, new_index: int) -> None:
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "UPDATE questionnaire_sessions SET current_index=? WHERE id=?",
            (new_index, session_id))
        await db.commit()


async def complete_questionnaire_session(session_id: int) -> None:
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "UPDATE questionnaire_sessions SET status='completed', completed_at=datetime('now') "
            "WHERE id=?", (session_id,))
        await db.commit()


async def cancel_questionnaire_session(session_id: int) -> None:
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "UPDATE questionnaire_sessions SET status='cancelled', completed_at=datetime('now') "
            "WHERE id=?", (session_id,))
        await db.commit()


# Workstream B (corrective pass) — bounded 5-state claim machine, Python-side
# mirror of the DB CHECK constraint on dass21_discuss_claims.status. Every
# transition this module performs is validated against BOTH this table AND
# the SQL WHERE clause (exact expected current status) -- a stale worker
# holding an old response_id, or an out-of-order transition, changes 0 rows.
DASS21_CLAIM_STATUSES = frozenset({
    "pending_before_send", "send_started", "delivered",
    "failed_before_send", "delivery_uncertain",
})
_DASS21_CLAIM_TRANSITIONS = frozenset({
    ("pending_before_send", "send_started"),
    ("pending_before_send", "failed_before_send"),
    ("send_started", "delivered"),
    ("send_started", "delivery_uncertain"),
})
# A pending_before_send claim whose lease has expired (the process almost
# certainly crashed/died before ever contacting Telegram) is provably safe to
# reclaim -- send_started/delivery_uncertain never are (Telegram may already
# have been contacted; an automatic retry there could duplicate a message we
# cannot prove was never sent).
#
# Margin: bot._dass21_discuss_build_response caps the LLM call at
# timeout=20s; the installed openai SDK (1.35.10) defaults max_retries=2, so
# the worst-case LLM time for one legitimate build is ~20s * 3 = 60s.
# persist_influence_trace and safety validation are near-instant local
# DB/CPU work. 180s leaves a 3x margin over that worst case, so a normal
# in-flight build never loses its lease to a reclaim.
_DASS21_CLAIM_LEASE_SECONDS = 180

# Storage-layer contract: the ONLY topic_ids this table may ever hold (mirrors
# the DB CHECK constraint above). This is deliberately a SEPARATE constant from
# bot._DASS21_DISCUSS_TOPICS (the presentation-layer contract, which buttons to
# show) to avoid a database.py <-> bot.py import cycle -- keep them in sync by
# hand if the DASS discuss topic set ever changes.
_DASS21_VALID_TOPIC_IDS = frozenset({"measures", "relate", "next", "specialist"})


async def claim_dass21_discuss_reply(user_id: int, session_id: int, topic_id: str,
                                     source_chat_id: int, source_message_id: int,
                                     response_id: str) -> bool:
    """ATOMIC claim of the logical action
    (user_id, session_id, topic_id, source_chat_id, source_message_id) --
    the exact menu CARD's topic button, not the topic in general (reopening
    the menu sends a NEW Telegram message with a new message_id, which is a
    fresh, legitimate attempt). One UPSERT: inserts a fresh
    'pending_before_send' row, or -- if a row already exists for this exact
    card -- reclaims it ONLY when it is 'failed_before_send' (a genuine
    retry, Telegram provably never contacted), or a 'pending_before_send'
    whose lease has expired (the process likely crashed before ever reaching
    _send). 'send_started'/'delivered'/'delivery_uncertain' are NEVER
    reclaimed here. Returns True iff THIS call won the claim (caller must
    proceed); False means another delivery already owns this exact card+
    topic -- caller must not call the LLM or send a second reply. Same
    once-only-guard pattern as bump_crisis_stage.

    Raises ValueError for a topic_id outside _DASS21_VALID_TOPIC_IDS -- a
    repository-level check in addition to the DB's own CHECK constraint;
    callers (bot.py) already validate this before calling, so this is
    defense in depth, not the primary gate."""
    if topic_id not in _DASS21_VALID_TOPIC_IDS:
        raise ValueError(f"invalid DASS-21 discuss topic_id: {topic_id!r}")
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "INSERT INTO dass21_discuss_claims "
            "(user_id, session_id, topic_id, source_chat_id, source_message_id, "
            " status, response_id) "
            "VALUES (?, ?, ?, ?, ?, 'pending_before_send', ?) "
            "ON CONFLICT(user_id, session_id, topic_id, source_chat_id, source_message_id) "
            "DO UPDATE SET status='pending_before_send', response_id=excluded.response_id, "
            "  updated_at=datetime('now') "
            "WHERE dass21_discuss_claims.status='failed_before_send' "
            "   OR (dass21_discuss_claims.status='pending_before_send' "
            "       AND dass21_discuss_claims.updated_at <= datetime('now', ?))",
            (user_id, session_id, topic_id, source_chat_id, source_message_id, response_id,
             f"-{_DASS21_CLAIM_LEASE_SECONDS} seconds"))
        await db.commit()
        return cur.rowcount > 0


async def transition_dass21_discuss_claim(user_id: int, session_id: int, topic_id: str,
                                          source_chat_id: int, source_message_id: int,
                                          response_id: str, from_status: str,
                                          to_status: str) -> bool:
    """Bounded, token-checked state transition for one already-claimed card.
    Requires the EXACT claim token (response_id) and EXACT expected current
    status -- a stale worker holding an old response_id, or attempting an
    out-of-order transition, changes 0 rows (verified by rowcount, never
    assumed). Raises ValueError for an unknown status or a transition not in
    _DASS21_CLAIM_TRANSITIONS -- a Python-side check independent of (and in
    addition to) the DB's own CHECK constraint on the status column."""
    if from_status not in DASS21_CLAIM_STATUSES or to_status not in DASS21_CLAIM_STATUSES:
        raise ValueError(f"unknown claim status in transition {from_status!r} -> {to_status!r}")
    if (from_status, to_status) not in _DASS21_CLAIM_TRANSITIONS:
        raise ValueError(f"invalid claim transition {from_status!r} -> {to_status!r}")
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "UPDATE dass21_discuss_claims SET status=?, updated_at=datetime('now') "
            "WHERE user_id=? AND session_id=? AND topic_id=? AND source_chat_id=? "
            "  AND source_message_id=? AND response_id=? AND status=?",
            (to_status, user_id, session_id, topic_id, source_chat_id, source_message_id,
             response_id, from_status))
        await db.commit()
        return cur.rowcount > 0


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
