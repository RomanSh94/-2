"""PR 1A — Privacy & Data Governance registry.

Symmetric to how `tests/test_clinical_boundary.py` holds A1 (a registry +
default-deny scan, not a markdown list): every table in `database.SCHEMA` that
carries a `user_id` column (or is `users` itself, keyed by `id`) is a candidate
sensitive table and MUST be registered here with an explicit policy. A table that
exists in the schema but is missing from PRIVACY_REGISTRY makes CI red (see
tests/test_privacy_registry.py) — the same discipline as an unregistered latent
source in A1.

`influence_trace` (created in PR 0) is registered from this module's FIRST commit —
it is the bot's own model of the owner, sensitive by definition, and easy to forget
because it was created before this registry existed.
"""
import re
from dataclasses import dataclass, field

# ── Policy vocabularies (closed sets — typos become AssertionErrors at import) ──
EXPORT_POLICIES = {"INCLUDE"}                              # (no EXCLUDE case exists yet)
DELETE_POLICIES = {"CASCADE_DELETE", "ANONYMIZE", "RETAIN"}
CATEGORIES = {
    "ACCOUNT", "CONVERSATION", "STATE", "JOURNAL", "ENGAGEMENT",
    "RESEARCH_LOG", "PSYCH_PROFILE", "INFLUENCE_MODEL", "CRISIS_SAFETY",
    "CONSENT",  # PR 1B-1: added explicitly for tester_acknowledgments — consent/
               # test-state, not a safety-audit record, so it takes CASCADE_DELETE
               # (not RETAIN) unlike CRISIS_SAFETY.
    "QUESTIONNAIRE",  # Questionnaire Core PR #1: self-report sessions/responses.
                     # Storage-only data, not a safety-audit record -> CASCADE_DELETE.
}


@dataclass(frozen=True)
class TableEntry:
    table: str
    user_id_column: str          # "id" for `users`, else "user_id"
    category: str
    export_policy: str           # INCLUDE
    delete_policy: str           # CASCADE_DELETE | ANONYMIZE | RETAIN
    retention_policy: str        # human-readable
    log_policy: str              # human-readable — what may/never appear in logs/alerts
    reason: str = ""             # required, non-empty, for RETAIN (§ crisis data policy: not silent)

    def __post_init__(self):
        assert self.export_policy in EXPORT_POLICIES, f"{self.table}: bad export_policy"
        assert self.delete_policy in DELETE_POLICIES, f"{self.table}: bad delete_policy"
        assert self.category in CATEGORIES, f"{self.table}: bad category"
        if self.delete_policy == "RETAIN":
            assert self.reason.strip(), (
                f"{self.table}: RETAIN requires an explicit `reason` — "
                f"§ crisis data policy: a delete-all request must never silently "
                f"keep data without a documented reason")


def _e(**kw) -> TableEntry:
    return TableEntry(**kw)


# ── The registry ────────────────────────────────────────────────────────────────
PRIVACY_REGISTRY: dict[str, TableEntry] = {
    "users": _e(
        table="users", user_id_column="id", category="ACCOUNT",
        export_policy="INCLUDE", delete_policy="ANONYMIZE",
        retention_policy="Row kept for referential continuity with retained crisis "
                         "records; username/first_name cleared on delete-all request.",
        log_policy="username/first_name are PII — never beyond user_id in logs/alerts."),

    "user_profiles": _e(
        table="user_profiles", user_id_column="user_id", category="STATE",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="No raw field in logs/alerts."),

    "messages": _e(
        table="messages", user_id_column="user_id", category="CONVERSATION",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="Raw content NEVER in logs/alerts/webhooks beyond a masked excerpt "
                   "(see notifications._mask_excerpt)."),

    "summaries": _e(
        table="summaries", user_id_column="user_id", category="CONVERSATION",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="Raw content NEVER in logs/alerts/webhooks."),

    "user_states": _e(
        table="user_states", user_id_column="user_id", category="STATE",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="No raw field in logs/alerts."),

    "intervention_results": _e(
        table="intervention_results", user_id_column="user_id", category="RESEARCH_LOG",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="Scores/metadata only; no free-text payload in this table."),

    "adverse_events": _e(
        table="adverse_events", user_id_column="user_id", category="RESEARCH_LOG",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="`description` is free text — never in logs/alerts beyond internal DB read."),

    "router_decision_logs": _e(
        table="router_decision_logs", user_id_column="user_id", category="RESEARCH_LOG",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="`state_snapshot` is structured state, not raw message text; still never in alerts."),

    "weekly_progress_snapshots": _e(
        table="weekly_progress_snapshots", user_id_column="user_id", category="RESEARCH_LOG",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="Aggregate numbers only."),

    "moderation_logs": _e(
        table="moderation_logs", user_id_column="user_id", category="RESEARCH_LOG",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="`message_text` is raw user content — never in logs/alerts beyond internal DB read."),

    "checkins": _e(
        table="checkins", user_id_column="user_id", category="ENGAGEMENT",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="No raw field in logs/alerts."),

    "validator_blocks": _e(
        table="validator_blocks", user_id_column="user_id", category="RESEARCH_LOG",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="`blocked_text` is raw bot output — never in logs/alerts beyond internal DB read."),

    "disambiguation_events": _e(
        table="disambiguation_events", user_id_column="user_id", category="RESEARCH_LOG",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="`message_text` is raw user content — never in logs/alerts beyond internal DB read."),

    "review_flags": _e(
        table="review_flags", user_id_column="user_id", category="RESEARCH_LOG",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="`context` is free text — never in logs/alerts beyond internal DB read."),

    "toxic_validation_blocks": _e(
        table="toxic_validation_blocks", user_id_column="user_id", category="RESEARCH_LOG",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="`original_text` is raw bot output — never in logs/alerts beyond internal DB read."),

    "emotion_journal_entries": _e(
        table="emotion_journal_entries", user_id_column="user_id", category="JOURNAL",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all (also /journal_delete).",
        log_policy="Raw journal content NEVER in logs/alerts/webhooks."),

    "cbt_journal_entries": _e(
        table="cbt_journal_entries", user_id_column="user_id", category="JOURNAL",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all (also /journal_delete).",
        log_policy="Raw journal content NEVER in logs/alerts/webhooks."),

    "checkin_logs": _e(
        table="checkin_logs", user_id_column="user_id", category="JOURNAL",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all (also /journal_delete).",
        log_policy="No raw field in logs/alerts."),

    "journal_settings": _e(
        table="journal_settings", user_id_column="user_id", category="JOURNAL",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all (also /journal_delete).",
        log_policy="No raw field in logs/alerts."),

    "push_settings": _e(
        table="push_settings", user_id_column="user_id", category="ENGAGEMENT",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="No raw field in logs/alerts."),

    "push_log": _e(
        table="push_log", user_id_column="user_id", category="ENGAGEMENT",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="No raw field in logs/alerts."),

    "user_response_preferences": _e(
        table="user_response_preferences", user_id_column="user_id", category="ENGAGEMENT",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="No raw field in logs/alerts. Only the three closed-set "
                    "values (response_format/response_length/voice_language) -- "
                    "never a raw format-command message or an inferred trait."),

    # ── Crisis safety/audit records — retained, NOT silently deleted ────────────
    "crisis_events": _e(
        table="crisis_events", user_id_column="user_id", category="CRISIS_SAFETY",
        export_policy="INCLUDE", delete_policy="RETAIN",
        retention_policy="Retained indefinitely, not anonymized.",
        log_policy="`message_excerpt` is already a bounded/masked excerpt at write time; "
                  "never expand it in logs/alerts.",
        reason="Safety/duty-of-care audit trail (crisis follow-up scheduling reads this "
              "table; deleting it would also break admin escalation history). A "
              "delete-all request must not silently erase it — retained on purpose, "
              "documented here, not a default-silent-delete."),

    "crisis_message_delivery_log": _e(
        table="crisis_message_delivery_log", user_id_column="user_id", category="CRISIS_SAFETY",
        export_policy="INCLUDE", delete_policy="RETAIN",
        retention_policy="Retained indefinitely, not anonymized. Kept alongside "
                         "crisis_events (both retained together — deleting one but not "
                         "the other would leave a dangling event_id reference).",
        log_policy="`telegram_error` may contain transport error text, not personal "
                  "content — no additional masking needed, but never merged into "
                  "external alerts beyond what it already is.",
        reason="This table is the live, logged proof that a crisis message was "
              "delivered (CLINICAL_BOUNDARY.md §8 — closed live via this exact "
              "table). Deleting it on a generic delete-all would destroy that proof "
              "chain. Retained on purpose, documented here."),

    # ── A1 influence trace — the bot's own model of the owner ───────────────────
    "influence_trace": _e(
        table="influence_trace", user_id_column="user_id", category="INFLUENCE_MODEL",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all. Cascaded together WITH the "
                         "rest of the user's personal data specifically so no orphan "
                         "trace row is left referencing content that no longer exists "
                         "(dangling source_id references are avoided by deleting the "
                         "trace itself, not by trying to null out source_id).",
        log_policy="`human_readable` is a content-ful description of the owner's own "
                  "psychological data — never in logs/alerts/webhooks/CI artifacts/"
                  "debug output; readable only via get_influence_trace() for the "
                  "owner/psychologist review path."),

    "response_quality": _e(
        table="response_quality", user_id_column="user_id", category="RESEARCH_LOG",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="No raw field in logs/alerts."),

    "user_psychology_profile": _e(
        table="user_psychology_profile", user_id_column="user_id", category="PSYCH_PROFILE",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all (also existing delete_profile()).",
        log_policy="No raw field in logs/alerts; surfaced to the user only via /profile."),

    "psychology_profile_history": _e(
        table="psychology_profile_history", user_id_column="user_id", category="PSYCH_PROFILE",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all (also existing delete_profile()).",
        log_policy="`snapshot_json` never in logs/alerts."),

    # PR 1B-1
    "tester_acknowledgments": _e(
        table="tester_acknowledgments", user_id_column="user_id", category="CONSENT",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Owner/tester controlled; no special retention — this is "
                         "consent/test-state, not a safety-audit record, so it does "
                         "NOT get the CRISIS_SAFETY RETAIN treatment.",
        log_policy="Never log payload — acknowledgment timestamp only, but keep out "
                  "of logs/alerts by default like everything else here."),

    # Questionnaire Core PR #1 — storage-only, no scores/interpretation anywhere.
    "questionnaire_sessions": _e(
        table="questionnaire_sessions", user_id_column="user_id", category="QUESTIONNAIRE",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="Session metadata only (status/index/timestamps) — no raw "
                  "item/answer text ever stored here."),

    "questionnaire_responses": _e(
        table="questionnaire_responses", user_id_column="user_id", category="QUESTIONNAIRE",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="answer_id/answer_value are stable definition tokens, never the "
                  "original item/option display text — never in logs/alerts regardless."),

    # Workstream B — DASS-21 discuss-reply delivery claims (dedup bookkeeping
    # only; no response text/subscale values stored here).
    "dass21_discuss_claims": _e(
        table="dass21_discuss_claims", user_id_column="user_id", category="QUESTIONNAIRE",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="status/response_id/topic_id only — no reply text, no subscale "
                  "values; never in logs/alerts beyond internal DB read."),

    # PR A — ordinary-user private invite access. Account-status metadata, not
    # content -- same STATE treatment as user_states/user_profiles.
    "user_access": _e(
        table="user_access", user_id_column="user_id", category="STATE",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="status/source only — no free text; never in logs/alerts beyond "
                  "internal DB read."),

    # First-user illustrated onboarding. This is consent/acknowledgment state
    # (privacy_notice_acknowledged_at) plus progress metadata — semantically the
    # same shape as tester_acknowledgments, hence CONSENT (not STATE): the row
    # records that the user was shown and acknowledged the privacy notice, which
    # is a consent-adjacent fact, not therapeutic content. CASCADE_DELETE (not
    # RETAIN): it is NOT a safety-audit record, so a delete-all request removes
    # it in full — the spec forbids retaining onboarding metadata past delete-all
    # without a documented justified reason, and there is none. No raw content
    # lives here (timestamps, a version tag, an integer step), so it is only ever
    # read back through the owner's own privacy export.
    "user_onboarding_state": _e(
        table="user_onboarding_state", user_id_column="user_id", category="CONSENT",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all — no special retention; "
                         "onboarding/consent-acknowledgment state, not a safety-audit "
                         "record, so it does NOT get CRISIS_SAFETY RETAIN treatment.",
        log_policy="Version tag, status, integer step and timestamps only — no raw "
                  "content of any kind; never in logs/alerts."),

    # Independent, notice-scoped acknowledgement ledger (spec item F
    # correction) — deliberately NOT keyed by onboarding_version/status, so a
    # future privacy-notice-version bump can prompt a settled onboarding user
    # without needing a second onboarding row. Same CONSENT/CASCADE_DELETE
    # treatment as user_onboarding_state: this is consent-adjacent state, not
    # a safety-audit record, so a delete-all request removes it in full. No
    # raw content lives here (a notice id, a version tag, a timestamp).
    "user_notice_acknowledgements": _e(
        table="user_notice_acknowledgements", user_id_column="user_id", category="CONSENT",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all — no special retention; "
                         "consent-acknowledgment state, not a safety-audit record.",
        log_policy="Notice id, version tag and timestamp only — no raw content of "
                  "any kind; never in logs/alerts."),

    # Generic first-turn contract (persistence/concurrency foundation).
    "user_interaction_revision": _e(
        table="user_interaction_revision", user_id_column="user_id", category="STATE",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="A single integer counter — never in logs/alerts."),

    "first_turn_claims": _e(
        table="first_turn_claims", user_id_column="user_id", category="RESEARCH_LOG",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="status/scenario/version/claim_token only — claim_token is an "
                  "opaque bookkeeping value, never in logs/alerts beyond internal DB read."),

    "interaction_button_bindings": _e(
        table="interaction_button_bindings", user_id_column="user_id", category="ENGAGEMENT",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="The opaque callback token is never emitted in logs/alerts; "
                  "no raw content of any kind lives in this table."),

    "user_interaction_events": _e(
        table="user_interaction_events", user_id_column="user_id", category="ENGAGEMENT",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="`normalized_text` is a fixed enum-mapped label, never raw "
                  "content; `reply_error_code` is a bounded internal code, never a "
                  "raw exception message — neither is ever emitted in logs/alerts "
                  "beyond internal DB read."),

    # Therapeutic Core Foundation (Phase 1, master prompt SS15) — same
    # PSYCH_PROFILE treatment as user_psychology_profile/psychology_profile_history:
    # structured therapeutic working state, not a safety-audit record, so it does
    # NOT get CRISIS_SAFETY RETAIN treatment. Ships inert (flag off, nothing in
    # bot.pipeline() reads/writes these tables yet) but is registered from its
    # first commit, same discipline as influence_trace above.
    "core_sessions": _e(
        table="core_sessions", user_id_column="user_id", category="PSYCH_PROFILE",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="`state_json` is structured session state (phase/intent/goal), "
                  "not raw message text — still never in logs/alerts."),

    "core_formulations": _e(
        table="core_formulations", user_id_column="user_id", category="PSYCH_PROFILE",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="`formulation_json` is a user-confirmable working hypothesis — "
                  "never in logs/alerts."),

    "core_interventions": _e(
        table="core_interventions", user_id_column="user_id", category="PSYCH_PROFILE",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="`intervention_json` is method/consent/status metadata only — "
                  "never in logs/alerts."),

    "core_outcomes": _e(
        table="core_outcomes", user_id_column="user_id", category="RESEARCH_LOG",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="Scores/metadata only — same treatment as intervention_results."),

    "core_memory_items": _e(
        table="core_memory_items", user_id_column="user_id", category="PSYCH_PROFILE",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="`memory_json` is confirmable psychological memory content — "
                  "never in logs/alerts. REJECTED/EXPIRED rows are exported like "
                  "any other row (export is not filtered by lifecycle) but must "
                  "never be read back into a live response (see "
                  "MemoryLifecycle.influences_responses in therapeutic_domain.py)."),

    # PRACTICE proposal entity (Phase 3 hardening SS3). Same PSYCH_PROFILE
    # treatment as core_sessions -- a proposal is structured consent-flow
    # state (which practice, purpose, duration, status), not raw message
    # text or a safety-audit record.
    "core_practice_proposals": _e(
        table="core_practice_proposals", user_id_column="user_id",
        category="PSYCH_PROFILE",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="`purpose`/`expected_duration` are short catalog strings "
                  "describing the proposed practice, never raw user text — "
                  "never in logs/alerts."),

    # Depression Disclosure Gate (Phase 2, master prompt SS13). CRISIS_SAFETY
    # category (like crisis_events) because a flow record proves the safety
    # question was asked and answered -- but CASCADE_DELETE, not RETAIN: unlike
    # crisis_events this is a routing/assessment record, not the audit trail of
    # an actual crisis response being delivered, so a delete-all request removes
    # it in full.
    "depression_disclosure_flows": _e(
        table="depression_disclosure_flows", user_id_column="user_id",
        category="CRISIS_SAFETY",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="`answers_json` holds only the closed-set button values from "
                  "DURATION_OPTIONS/FUNCTIONING_OPTIONS/SUPPORT_OPTIONS/"
                  "PURPOSE_OPTIONS (depression_disclosure.py) -- never raw user "
                  "text; never in logs/alerts."),

    # Professional Core V2 -- Entry Triage runtime binding. ENGAGEMENT (not
    # CRISIS_SAFETY): this is a bounded conversation-entry navigation choice
    # (which of six closed categories the user tapped to begin), not a
    # safety-audit record and not raw free text -- same shape/treatment as
    # interaction_button_bindings/user_interaction_events above, so it takes
    # the same CASCADE_DELETE (never RETAIN) a delete-all request removes it
    # in full, with no special retention.
    "professional_entry_triage_bindings": _e(
        table="professional_entry_triage_bindings", user_id_column="user_id",
        category="ENGAGEMENT",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="The opaque callback token must never appear in logs/alerts. "
                  "`category` is user-linked psychological navigation data (which "
                  "of the six closed EntryTriageCategory values the user tapped) "
                  "-- never in logs/alerts. chat_id/source_message_id are Telegram "
                  "transport identifiers, not content, but are still never logged "
                  "beyond what is already necessary for delivery."),

    # Push V1 -- Silence/Re-engagement Continue/New-topic bindings. Same
    # ENGAGEMENT shape/treatment as interaction_button_bindings/
    # professional_entry_triage_bindings above: a bounded navigation choice
    # (which of the two closed push actions the user tapped), never raw
    # conversation content -- anchor_turn_id is an internal row id, not
    # content itself. CASCADE_DELETE, no special retention.
    "push_action_bindings": _e(
        table="push_action_bindings", user_id_column="user_id", category="ENGAGEMENT",
        export_policy="INCLUDE", delete_policy="CASCADE_DELETE",
        retention_policy="Until user-requested delete-all.",
        log_policy="The opaque callback token must never appear in logs/alerts. "
                  "`action` is one of two closed values (push_continue/"
                  "push_new_topic) -- never in logs/alerts. chat_id/"
                  "source_message_id/anchor_turn_id are internal identifiers, not "
                  "content, but are still never logged beyond what is already "
                  "necessary for delivery."),
}


# ── Default-deny scanner (symmetric to A1's find_latent_source_offenders) ──────
_TABLE_RE = re.compile(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\s*\);", re.S)


def find_sensitive_tables_in_schema(schema_sql: str) -> list[str]:
    """Every table with a `user_id` column, or `users` itself (keyed by `id`), is
    a sensitive-table CANDIDATE that must be registered."""
    found = []
    for name, body in _TABLE_RE.findall(schema_sql):
        if name == "users" or re.search(r"\buser_id\b", body):
            found.append(name)
    return found


def find_unregistered_sensitive_tables(schema_sql: str | None = None) -> list[str]:
    """Default-deny: any sensitive-table candidate not in PRIVACY_REGISTRY. Lazily
    imports `database` only if schema_sql isn't passed, to avoid a module-level
    circular import (database.py itself may import this module)."""
    if schema_sql is None:
        import database
        schema_sql = database.SCHEMA
    candidates = find_sensitive_tables_in_schema(schema_sql)
    return [t for t in candidates if t not in PRIVACY_REGISTRY]
