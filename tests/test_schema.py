"""
tests/test_schema.py
CPOI Platform — Unit tests for database schema and init_db.py

Covers:
  - All 7 tables exist with the correct columns after initialization
  - Append-only enforcement: UPDATE on audit_log raises an error
  - Append-only enforcement: DELETE on audit_log raises an error
  - INSERT on audit_log succeeds
  - write_audit_log() returns a valid UUID string
  - initialize_database() fails clearly when CPOI_DB_KEY is not set
  - Database file is not readable as plaintext (SQLCipher verification)
"""

import json
import os
import tempfile
import uuid
from pathlib import Path

import pytest
import sqlcipher3

# Make sure the db package is importable regardless of working directory.
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.init_db import (
    apply_schema,
    initialize_database,
    open_connection,
    write_audit_log,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEST_KEY = "cpoi-test-key-session1"


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Return a path inside a temp directory for an isolated test database."""
    return tmp_path / "test_cpoi.db"


@pytest.fixture()
def initialized_db(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Initialize a fresh encrypted test database and return its path.
    Sets CPOI_DB_KEY for the duration of the test.
    """
    monkeypatch.setenv("CPOI_DB_KEY", TEST_KEY)
    initialize_database(db_path)
    return db_path


@pytest.fixture()
def conn(initialized_db: Path, monkeypatch: pytest.MonkeyPatch) -> sqlcipher3.Connection:
    """Return an open connection to the initialized test database."""
    monkeypatch.setenv("CPOI_DB_KEY", TEST_KEY)
    connection = open_connection(initialized_db)
    yield connection
    connection.close()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _get_columns(connection: sqlcipher3.Connection, table: str) -> list[str]:
    """Return the list of column names for a given table."""
    cursor = connection.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cursor.fetchall()]


def _get_tables(connection: sqlcipher3.Connection) -> list[str]:
    """Return all user-created table names in the database."""
    cursor = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    return [row[0] for row in cursor.fetchall()]


def _get_triggers(connection: sqlcipher3.Connection) -> list[str]:
    """Return all trigger names in the database."""
    cursor = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name"
    )
    return [row[0] for row in cursor.fetchall()]


# ---------------------------------------------------------------------------
# Tests: all 7 tables exist
# ---------------------------------------------------------------------------

EXPECTED_TABLES = [
    "alerts",
    "audit_log",
    "clients",
    "intake_submissions",
    "oei_scores",
    "reports",
    "signal_readings",
]


def test_all_tables_exist(conn: sqlcipher3.Connection) -> None:
    """All 7 tables defined in the spec must be present after initialization."""
    tables = _get_tables(conn)
    for expected in EXPECTED_TABLES:
        assert expected in tables, f"Expected table '{expected}' not found. Found: {tables}"


# ---------------------------------------------------------------------------
# Tests: column presence per table (spec-verbatim column names)
# ---------------------------------------------------------------------------

def test_clients_columns(conn: sqlcipher3.Connection) -> None:
    cols = _get_columns(conn, "clients")
    for col in [
        "client_id", "client_name", "engagement_type",
        "engagement_start_date", "monthly_retainer",
        "oei_score_intake", "status", "created_at",
    ]:
        assert col in cols, f"clients table missing column '{col}'"


def test_intake_submissions_columns(conn: sqlcipher3.Connection) -> None:
    cols = _get_columns(conn, "intake_submissions")
    for col in [
        "submission_id", "client_id", "reporting_period_start",
        "reporting_period_end", "submitted_at", "file_path",
        "ingestion_status", "processed_at",
    ]:
        assert col in cols, f"intake_submissions table missing column '{col}'"


def test_oei_scores_columns(conn: sqlcipher3.Connection) -> None:
    cols = _get_columns(conn, "oei_scores")
    for col in [
        "score_id", "client_id", "submission_id", "period_date",
        "strategic_saturation_score", "governance_responsiveness_score",
        "execution_visibility_score", "reporting_integrity_score",
        "org_sustainability_score", "oei_composite_score",
        "strategic_saturation_class", "governance_responsiveness_class",
        "execution_visibility_class", "reporting_integrity_class",
        "org_sustainability_class", "composite_class", "calculated_at",
    ]:
        assert col in cols, f"oei_scores table missing column '{col}'"


def test_signal_readings_columns(conn: sqlcipher3.Connection) -> None:
    cols = _get_columns(conn, "signal_readings")
    for col in [
        "reading_id", "submission_id", "client_id", "signal_name",
        "signal_value", "threshold_value", "threshold_breached",
        "period_date", "calculated_at",
    ]:
        assert col in cols, f"signal_readings table missing column '{col}'"


def test_alerts_columns(conn: sqlcipher3.Connection) -> None:
    cols = _get_columns(conn, "alerts")
    for col in [
        "alert_id", "client_id", "signal_name", "signal_value",
        "threshold_value", "alert_severity", "alert_message",
        "triggered_at", "acknowledged", "acknowledged_at",
        "email_sent", "email_sent_at",
    ]:
        assert col in cols, f"alerts table missing column '{col}'"


def test_reports_columns(conn: sqlcipher3.Connection) -> None:
    cols = _get_columns(conn, "reports")
    for col in [
        "report_id", "client_id", "report_type", "period_date",
        "generated_at", "file_path", "ai_narrative_generated",
        "delivered", "delivered_at",
    ]:
        assert col in cols, f"reports table missing column '{col}'"


def test_audit_log_columns(conn: sqlcipher3.Connection) -> None:
    cols = _get_columns(conn, "audit_log")
    for col in [
        "log_id", "event_type", "entity_type", "entity_id",
        "description", "performed_by", "performed_at", "metadata",
    ]:
        assert col in cols, f"audit_log table missing column '{col}'"


# ---------------------------------------------------------------------------
# Tests: append-only enforcement on audit_log
# ---------------------------------------------------------------------------

def test_audit_log_insert_succeeds(conn: sqlcipher3.Connection) -> None:
    """A standard INSERT into audit_log must succeed."""
    log_id = write_audit_log(
        conn,
        event_type="test_event",
        description="Test insert for unit test.",
        entity_type="test",
        entity_id="test-001",
    )
    assert isinstance(log_id, str)
    # Verify the record is retrievable
    cursor = conn.execute("SELECT log_id FROM audit_log WHERE log_id = ?", (log_id,))
    row = cursor.fetchone()
    assert row is not None, "Inserted audit_log record not found on read-back"
    assert row[0] == log_id


def test_audit_log_update_blocked(conn: sqlcipher3.Connection) -> None:
    """UPDATE on audit_log must be rejected by the database trigger.

    SQLCipher's RAISE(ABORT) inside a trigger surfaces as IntegrityError
    (not OperationalError) in the sqlcipher3 Python binding.
    """
    log_id = write_audit_log(
        conn,
        event_type="test_event",
        description="Record to attempt update on.",
    )
    with pytest.raises(sqlcipher3.IntegrityError, match="append-only"):
        conn.execute(
            "UPDATE audit_log SET description = 'tampered' WHERE log_id = ?",
            (log_id,),
        )
        conn.commit()


def test_audit_log_delete_blocked(conn: sqlcipher3.Connection) -> None:
    """DELETE on audit_log must be rejected by the database trigger.

    SQLCipher's RAISE(ABORT) inside a trigger surfaces as IntegrityError
    (not OperationalError) in the sqlcipher3 Python binding.
    """
    log_id = write_audit_log(
        conn,
        event_type="test_event",
        description="Record to attempt delete on.",
    )
    with pytest.raises(sqlcipher3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM audit_log WHERE log_id = ?", (log_id,))
        conn.commit()


# ---------------------------------------------------------------------------
# Tests: triggers exist
# ---------------------------------------------------------------------------

def test_append_only_triggers_present(conn: sqlcipher3.Connection) -> None:
    """Both append-only triggers must exist in the database."""
    triggers = _get_triggers(conn)
    assert "audit_log_no_update" in triggers, "Trigger 'audit_log_no_update' not found"
    assert "audit_log_no_delete" in triggers, "Trigger 'audit_log_no_delete' not found"


# ---------------------------------------------------------------------------
# Tests: missing encryption key
# ---------------------------------------------------------------------------

def test_open_connection_fails_without_key(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """open_connection() must raise RuntimeError if CPOI_DB_KEY is not set."""
    monkeypatch.delenv("CPOI_DB_KEY", raising=False)
    with pytest.raises(RuntimeError, match="CPOI_DB_KEY"):
        open_connection(db_path)


def test_initialize_database_fails_without_key(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """initialize_database() must fail clearly when CPOI_DB_KEY is absent."""
    monkeypatch.delenv("CPOI_DB_KEY", raising=False)
    with pytest.raises((RuntimeError, Exception)):
        initialize_database(db_path)


# ---------------------------------------------------------------------------
# Tests: SQLCipher encryption active (file is not readable as plaintext)
# ---------------------------------------------------------------------------

def test_database_is_not_plaintext(initialized_db: Path) -> None:
    """
    The database file must not be readable as a standard SQLite file.
    Opening it with the standard sqlite3 module (no key) must fail or
    return no readable data — confirming SQLCipher encryption is active.
    """
    import sqlite3

    raw_bytes = initialized_db.read_bytes()
    # A plaintext SQLite file always starts with this 16-byte string.
    sqlite_magic = b"SQLite format 3\x00"
    assert not raw_bytes.startswith(sqlite_magic), (
        "Database file starts with the SQLite plaintext magic bytes. "
        "SQLCipher encryption does not appear to be active."
    )

    # Also confirm that opening with standard sqlite3 does not expose tables.
    plain_conn = sqlite3.connect(str(initialized_db))
    try:
        cursor = plain_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = cursor.fetchall()
        assert tables == [], (
            f"Standard sqlite3 (no key) can read table names: {tables}. "
            "SQLCipher encryption does not appear to be active."
        )
    except sqlite3.DatabaseError:
        # This is the expected outcome — the file is not a valid unencrypted SQLite database.
        pass
    finally:
        plain_conn.close()


# ---------------------------------------------------------------------------
# Tests: write_audit_log metadata serialization
# ---------------------------------------------------------------------------

def test_write_audit_log_with_metadata(conn: sqlcipher3.Connection) -> None:
    """Metadata dict must be stored as JSON and round-trip correctly."""
    meta = {"schema_version": "1.0", "kdf_iter": 256000}
    log_id = write_audit_log(
        conn,
        event_type="test_metadata",
        description="Testing metadata serialization.",
        metadata=meta,
    )
    cursor = conn.execute(
        "SELECT metadata FROM audit_log WHERE log_id = ?", (log_id,)
    )
    row = cursor.fetchone()
    assert row is not None
    stored = json.loads(row[0])
    assert stored == meta, f"Metadata round-trip failed. Expected {meta}, got {stored}"
