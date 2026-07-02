"""PR 1A — privacy registry: default-deny for sensitive tables (symmetric to A1's
default-deny for latent sources in tests/test_clinical_boundary.py).

A sensitive-table candidate (any table with a `user_id` column, or `users` itself)
that exists in the live schema but isn't registered in privacy_registry.PRIVACY_REGISTRY
must fail CI — exactly the same discipline as an unregistered latent-source read.
"""
import database
import privacy_registry as pr


def test_no_sensitive_table_is_unregistered():
    offenders = pr.find_unregistered_sensitive_tables(database.SCHEMA)
    assert not offenders, (
        "Privacy registry — a sensitive table exists in the schema but is not "
        "registered in privacy_registry.PRIVACY_REGISTRY. Register it with an "
        "explicit export/delete/retention/log policy in a reviewable diff:\n  "
        + "\n  ".join(offenders))


def test_scanner_catches_an_unregistered_sensitive_table():
    # Positive control: a synthetic schema with a user_id-bearing table NOT in the
    # registry must be flagged — proves the default-deny scan actually enforces
    # something, not just trivially passes on the real (already-complete) schema.
    fake_schema = """
    CREATE TABLE IF NOT EXISTS rogue_sensitive_table (
        id INTEGER PRIMARY KEY,
        user_id INTEGER,
        secret TEXT
    );
    """
    offenders = pr.find_unregistered_sensitive_tables(fake_schema)
    assert offenders == ["rogue_sensitive_table"]


def test_scanner_ignores_a_table_without_user_id():
    # Negative control: a table with no user_id column and not named `users` is not
    # a sensitive-table candidate — proves the scan isn't just "everything fails".
    fake_schema = """
    CREATE TABLE IF NOT EXISTS global_config (
        id INTEGER PRIMARY KEY,
        key TEXT,
        value TEXT
    );
    """
    offenders = pr.find_unregistered_sensitive_tables(fake_schema)
    assert offenders == []


def test_influence_trace_registered_from_day_one():
    # influence_trace (PR 0) is the bot's own model of the owner — sensitive by
    # definition, created before this registry existed, and easy to forget.
    assert "influence_trace" in pr.PRIVACY_REGISTRY
    entry = pr.PRIVACY_REGISTRY["influence_trace"]
    assert entry.category == "INFLUENCE_MODEL"
    assert entry.export_policy == "INCLUDE"


def test_crisis_tables_are_retained_not_cascade_deleted():
    # § crisis data policy: a generic "delete all my data" request must NOT
    # silently wipe safety/audit records. Both crisis tables must be RETAIN with a
    # non-empty documented reason (enforced by TableEntry.__post_init__ too).
    for table in ("crisis_events", "crisis_message_delivery_log"):
        entry = pr.PRIVACY_REGISTRY[table]
        assert entry.delete_policy == "RETAIN", (
            f"{table} must be RETAIN, not silently deleted on a generic delete-all")
        assert entry.reason.strip(), f"{table}: RETAIN without a documented reason"


def test_retain_without_reason_is_rejected():
    # The dataclass itself enforces this — a RETAIN entry with an empty reason
    # cannot even be constructed.
    import pytest
    with pytest.raises(AssertionError):
        pr.TableEntry(
            table="x", user_id_column="user_id", category="RESEARCH_LOG",
            export_policy="INCLUDE", delete_policy="RETAIN",
            retention_policy="forever", log_policy="n/a", reason="")


def test_every_registry_entry_has_a_real_policy():
    for name, entry in pr.PRIVACY_REGISTRY.items():
        assert entry.table == name
        assert entry.user_id_column in ("id", "user_id")
        assert entry.retention_policy.strip()
        assert entry.log_policy.strip()
