"""
tests/test_ingestor.py
CPOI Platform — Unit tests for intake/ingestor.py

Covers:
  Happy path:
    - Valid submission creates an intake_submissions record
    - ingestion_status is 'processed'
    - stored_file_path exists on disk after ingestion
    - IngestionResult carries correct submission_id, client_id, period dates
    - IngestionResult dataframes match the validated input
    - IngestionResult metadata matches the validated input
    - audit_log contains one entry for the ingestion event
    - audit_log entry names the submission_id and client_id

  No-partial-ingestion contract:
    - If validation_result.valid is False, raises ValueError before touching disk or DB
    - If client_id does not exist, raises ValueError before touching disk or DB
    - If the DB write is simulated to fail, the copied file is deleted

  File storage:
    - File is stored under intake_files/{client_id}/{submission_id}.xlsx
    - Original source file is unchanged after ingestion

  Idempotency / isolation:
    - Two submissions for the same client produce distinct submission_ids
    - Two submissions produce two intake_submissions records
    - Two submissions produce two audit_log entries (plus the init entry)
"""

import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.init_db import initialize_database, open_connection, write_audit_log
from intake.ingestor import INTAKE_FILES_DIR, IngestionResult, ingest_submission
from intake.validator import ValidationResult

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEST_KEY = "cpoi-test-key-ingestor"
TEST_CLIENT_ID = "test-client-001"
TEST_CLIENT_NAME = "Acme Test Corporation"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Initialize a fresh encrypted test database and return its path."""
    monkeypatch.setenv("CPOI_DB_KEY", TEST_KEY)
    path = tmp_path / "test_cpoi.db"
    initialize_database(path)
    return path


@pytest.fixture()
def db_with_client(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Add a test client record to the database."""
    monkeypatch.setenv("CPOI_DB_KEY", TEST_KEY)
    conn = open_connection(db_path)
    conn.execute(
        """
        INSERT INTO clients (client_id, client_name, engagement_type, status)
        VALUES (?, ?, ?, ?)
        """,
        (TEST_CLIENT_ID, TEST_CLIENT_NAME, "oeil", "active"),
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def valid_result(tmp_path: Path) -> tuple[Path, ValidationResult]:
    """
    Build a minimal valid ValidationResult and write the source .xlsx file.
    Returns (source_file_path, validation_result).
    """
    # Build minimal dataframes matching the schema
    initiatives_df = pd.DataFrame([{
        "Initiative_ID": "INI-001",
        "Initiative_Name": "ERP Modernization",
        "Priority_Classification": "Critical",
        "Status_Reported": "Green",
        "Program_Owner": "Alice Smith",
        "Start_Date": "2026-01-01",
        "Target_Completion_Date": "2026-12-31",
        "Current_Phase": "Design",
        "Open_Blockers": "2",
        "Critical_Blockers": "0",
        "Last_Status_Update_Date": "2026-05-01",
        "Notes": "",
    }])

    empty_cols = {
        "ESCALATIONS": [
            "Escalation_ID", "Initiative_ID", "Date_Raised", "Raised_By_Level",
            "Escalation_Category", "Description", "Date_Resolved",
            "Resolved_At_Level", "Resolution_Days", "Outcome",
        ],
        "DEPENDENCIES": [
            "Dependency_ID", "Upstream_Initiative_ID", "Downstream_Initiative_ID",
            "Dependency_Type", "Status", "Days_Open", "Owner", "Notes",
        ],
        "RESOURCE_UTILIZATION": [
            "Team_Name", "Team_Size", "Allocated_Programs",
            "Estimated_Utilization_Pct", "Open_Requisitions",
            "Avg_Days_Open_Reqs", "Notes",
        ],
        "GOVERNANCE_EVENTS": [
            "Event_ID", "Event_Type", "Date_Scheduled", "Decision_Made",
            "Date_Occurred", "Delay_Days", "Initiative_ID", "Notes",
        ],
        "REPORTING_VARIANCE": [
            "Initiative_ID", "Reported_Status", "Actual_Blocker_Count",
            "Milestone_At_Risk", "Leadership_Aware", "Variance_Detected", "Notes",
        ],
        "HEADCOUNT_SIGNALS": [
            "Role_Title", "Team", "Tenure_Months", "Program_Assignment",
            "Escalation_Count_Last_90_Days", "Retention_Risk_Flag", "Notes",
        ],
    }

    dataframes = {"INITIATIVES": initiatives_df}
    for tab, cols in empty_cols.items():
        dataframes[tab] = pd.DataFrame(columns=cols)

    metadata = {
        "Client_Name": TEST_CLIENT_NAME,
        "Reporting_Period_Start": "2026-05-01",
        "Reporting_Period_End": "2026-05-31",
        "Submitted_By": "Jane Doe",
        "Submission_Date": "2026-06-01",
        "Engagement_Type": "OEIL_Month_1",
        "Data_Version": "1.0",
    }

    # Write a minimal .xlsx to disk so file copy can succeed
    source_path = tmp_path / "CP_OEI_DataIntake_Acme_2026_05.xlsx"
    with pd.ExcelWriter(str(source_path), engine="openpyxl") as writer:
        initiatives_df.to_excel(writer, sheet_name="INITIATIVES", index=False)

    result = ValidationResult(
        valid=True,
        errors=[],
        dataframes=dataframes,
        metadata=metadata,
    )
    return source_path, result


# ---------------------------------------------------------------------------
# Tests: happy path
# ---------------------------------------------------------------------------

def test_ingestion_creates_submission_record(
    db_with_client: Path,
    valid_result: tuple[Path, ValidationResult],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid submission must create one record in intake_submissions."""
    monkeypatch.setenv("CPOI_DB_KEY", TEST_KEY)
    monkeypatch.setenv("CPOI_INTAKE_DIR", str(tmp_path / "intake_files"))
    source_path, vr = valid_result

    with patch("intake.ingestor.INTAKE_FILES_DIR", tmp_path / "intake_files"):
        result = ingest_submission(source_path, vr, TEST_CLIENT_ID, db_with_client)

    conn = open_connection(db_with_client)
    cursor = conn.execute(
        "SELECT submission_id, ingestion_status FROM intake_submissions WHERE submission_id = ?",
        (result.submission_id,),
    )
    row = cursor.fetchone()
    conn.close()

    assert row is not None, "No intake_submissions record found after ingestion"
    assert row[0] == result.submission_id
    assert row[1] == "processed"


def test_ingestion_status_is_processed(
    db_with_client: Path,
    valid_result: tuple[Path, ValidationResult],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ingestion_status field must be 'processed' after successful ingestion."""
    monkeypatch.setenv("CPOI_DB_KEY", TEST_KEY)
    source_path, vr = valid_result

    with patch("intake.ingestor.INTAKE_FILES_DIR", tmp_path / "intake_files"):
        result = ingest_submission(source_path, vr, TEST_CLIENT_ID, db_with_client)

    conn = open_connection(db_with_client)
    cursor = conn.execute(
        "SELECT ingestion_status FROM intake_submissions WHERE submission_id = ?",
        (result.submission_id,),
    )
    row = cursor.fetchone()
    conn.close()
    assert row[0] == "processed"


def test_ingestion_stores_file_on_disk(
    db_with_client: Path,
    valid_result: tuple[Path, ValidationResult],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The intake file must exist on disk at stored_file_path after ingestion."""
    monkeypatch.setenv("CPOI_DB_KEY", TEST_KEY)
    source_path, vr = valid_result

    with patch("intake.ingestor.INTAKE_FILES_DIR", tmp_path / "intake_files"):
        result = ingest_submission(source_path, vr, TEST_CLIENT_ID, db_with_client)

    assert result.stored_file_path.exists(), (
        f"Expected stored file at '{result.stored_file_path}' but it does not exist."
    )


def test_ingestion_file_stored_under_client_directory(
    db_with_client: Path,
    valid_result: tuple[Path, ValidationResult],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stored file path must be under intake_files/{client_id}/."""
    monkeypatch.setenv("CPOI_DB_KEY", TEST_KEY)
    source_path, vr = valid_result
    intake_root = tmp_path / "intake_files"

    with patch("intake.ingestor.INTAKE_FILES_DIR", intake_root):
        result = ingest_submission(source_path, vr, TEST_CLIENT_ID, db_with_client)

    assert TEST_CLIENT_ID in str(result.stored_file_path), (
        f"Expected client_id in path but got: '{result.stored_file_path}'"
    )
    assert result.stored_file_path.suffix == ".xlsx"


def test_ingestion_source_file_unchanged(
    db_with_client: Path,
    valid_result: tuple[Path, ValidationResult],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The original source file must be unchanged after ingestion."""
    monkeypatch.setenv("CPOI_DB_KEY", TEST_KEY)
    source_path, vr = valid_result
    original_size = source_path.stat().st_size

    with patch("intake.ingestor.INTAKE_FILES_DIR", tmp_path / "intake_files"):
        ingest_submission(source_path, vr, TEST_CLIENT_ID, db_with_client)

    assert source_path.exists(), "Source file was deleted or moved."
    assert source_path.stat().st_size == original_size, "Source file was modified."


def test_ingestion_result_carries_correct_ids(
    db_with_client: Path,
    valid_result: tuple[Path, ValidationResult],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IngestionResult must carry the correct submission_id and client_id."""
    monkeypatch.setenv("CPOI_DB_KEY", TEST_KEY)
    source_path, vr = valid_result

    with patch("intake.ingestor.INTAKE_FILES_DIR", tmp_path / "intake_files"):
        result = ingest_submission(source_path, vr, TEST_CLIENT_ID, db_with_client)

    assert result.client_id == TEST_CLIENT_ID
    # submission_id must be a valid UUID
    parsed = uuid.UUID(result.submission_id)
    assert str(parsed) == result.submission_id


def test_ingestion_result_carries_period_dates(
    db_with_client: Path,
    valid_result: tuple[Path, ValidationResult],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IngestionResult must carry the reporting period dates from workbook metadata."""
    monkeypatch.setenv("CPOI_DB_KEY", TEST_KEY)
    source_path, vr = valid_result

    with patch("intake.ingestor.INTAKE_FILES_DIR", tmp_path / "intake_files"):
        result = ingest_submission(source_path, vr, TEST_CLIENT_ID, db_with_client)

    assert result.reporting_period_start == "2026-05-01"
    assert result.reporting_period_end == "2026-05-31"


def test_ingestion_result_carries_dataframes(
    db_with_client: Path,
    valid_result: tuple[Path, ValidationResult],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IngestionResult dataframes must match the validated input."""
    monkeypatch.setenv("CPOI_DB_KEY", TEST_KEY)
    source_path, vr = valid_result

    with patch("intake.ingestor.INTAKE_FILES_DIR", tmp_path / "intake_files"):
        result = ingest_submission(source_path, vr, TEST_CLIENT_ID, db_with_client)

    assert result.dataframes is not None
    assert "INITIATIVES" in result.dataframes
    assert len(result.dataframes["INITIATIVES"]) == 1
    assert result.dataframes["INITIATIVES"]["Initiative_ID"].iloc[0] == "INI-001"


def test_ingestion_result_carries_metadata(
    db_with_client: Path,
    valid_result: tuple[Path, ValidationResult],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IngestionResult metadata must match the validated input."""
    monkeypatch.setenv("CPOI_DB_KEY", TEST_KEY)
    source_path, vr = valid_result

    with patch("intake.ingestor.INTAKE_FILES_DIR", tmp_path / "intake_files"):
        result = ingest_submission(source_path, vr, TEST_CLIENT_ID, db_with_client)

    assert result.metadata["Client_Name"] == TEST_CLIENT_NAME
    assert result.metadata["Engagement_Type"] == "OEIL_Month_1"


def test_ingestion_writes_audit_log_entry(
    db_with_client: Path,
    valid_result: tuple[Path, ValidationResult],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ingestion must write exactly one 'intake_ingested' audit_log entry."""
    monkeypatch.setenv("CPOI_DB_KEY", TEST_KEY)
    source_path, vr = valid_result

    with patch("intake.ingestor.INTAKE_FILES_DIR", tmp_path / "intake_files"):
        result = ingest_submission(source_path, vr, TEST_CLIENT_ID, db_with_client)

    conn = open_connection(db_with_client)
    cursor = conn.execute(
        "SELECT log_id, description FROM audit_log WHERE event_type = 'intake_ingested'"
    )
    rows = cursor.fetchall()
    conn.close()

    assert len(rows) == 1, f"Expected 1 intake_ingested audit entry, found {len(rows)}"


def test_audit_log_entry_names_submission_and_client(
    db_with_client: Path,
    valid_result: tuple[Path, ValidationResult],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The audit_log entry must reference both the submission_id and client_id."""
    monkeypatch.setenv("CPOI_DB_KEY", TEST_KEY)
    source_path, vr = valid_result

    with patch("intake.ingestor.INTAKE_FILES_DIR", tmp_path / "intake_files"):
        result = ingest_submission(source_path, vr, TEST_CLIENT_ID, db_with_client)

    conn = open_connection(db_with_client)
    cursor = conn.execute(
        "SELECT entity_id, description FROM audit_log WHERE event_type = 'intake_ingested'"
    )
    row = cursor.fetchone()
    conn.close()

    assert row[0] == result.submission_id, (
        f"audit_log entity_id '{row[0]}' does not match submission_id '{result.submission_id}'"
    )
    assert TEST_CLIENT_ID in row[1], (
        f"audit_log description does not mention client_id '{TEST_CLIENT_ID}'"
    )


# ---------------------------------------------------------------------------
# Tests: no-partial-ingestion contract
# ---------------------------------------------------------------------------

def test_invalid_validation_result_raises_before_touching_disk(
    db_with_client: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passing a ValidationResult with valid=False must raise ValueError immediately."""
    monkeypatch.setenv("CPOI_DB_KEY", TEST_KEY)
    invalid_result = ValidationResult(
        valid=False,
        errors=["Tab 'INITIATIVES' not found."],
    )
    source_path = tmp_path / "fake.xlsx"
    source_path.write_bytes(b"not a real file")

    with pytest.raises(ValueError, match="valid is False"):
        ingest_submission(source_path, invalid_result, TEST_CLIENT_ID, db_with_client)

    # Confirm no intake_submissions record was created
    conn = open_connection(db_with_client)
    cursor = conn.execute("SELECT COUNT(*) FROM intake_submissions")
    count = cursor.fetchone()[0]
    conn.close()
    assert count == 0, "intake_submissions should be empty after rejected ingestion"


def test_missing_client_raises_before_touching_disk(
    db_with_client: Path,
    valid_result: tuple[Path, ValidationResult],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrecognized client_id must raise ValueError before any file is copied."""
    monkeypatch.setenv("CPOI_DB_KEY", TEST_KEY)
    source_path, vr = valid_result
    intake_root = tmp_path / "intake_files"

    with patch("intake.ingestor.INTAKE_FILES_DIR", intake_root):
        with pytest.raises(ValueError, match="not found in the clients table"):
            ingest_submission(source_path, vr, "no-such-client", db_with_client)

    # No file should have been written
    assert not intake_root.exists() or not any(intake_root.rglob("*.xlsx")), (
        "A file was copied despite the client not existing."
    )


def test_db_failure_rolls_back_copied_file(
    db_with_client: Path,
    valid_result: tuple[Path, ValidationResult],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    If the database INSERT fails after the file has been copied,
    the copied file must be deleted (rollback).
    """
    monkeypatch.setenv("CPOI_DB_KEY", TEST_KEY)
    source_path, vr = valid_result
    intake_root = tmp_path / "intake_files"

    with patch("intake.ingestor.INTAKE_FILES_DIR", intake_root):
        with patch(
            "intake.ingestor._insert_intake_submission",
            side_effect=RuntimeError("Simulated DB failure"),
        ):
            with pytest.raises(RuntimeError, match="Database write failed"):
                ingest_submission(source_path, vr, TEST_CLIENT_ID, db_with_client)

    # No .xlsx files should remain in the intake directory
    leftover = list(intake_root.rglob("*.xlsx")) if intake_root.exists() else []
    assert leftover == [], (
        f"Copied file was not cleaned up after DB failure: {leftover}"
    )


# ---------------------------------------------------------------------------
# Tests: isolation between submissions
# ---------------------------------------------------------------------------

def test_two_submissions_produce_distinct_ids(
    db_with_client: Path,
    valid_result: tuple[Path, ValidationResult],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two ingestions must produce two distinct submission_ids."""
    monkeypatch.setenv("CPOI_DB_KEY", TEST_KEY)
    source_path, vr = valid_result
    intake_root = tmp_path / "intake_files"

    with patch("intake.ingestor.INTAKE_FILES_DIR", intake_root):
        r1 = ingest_submission(source_path, vr, TEST_CLIENT_ID, db_with_client)
        r2 = ingest_submission(source_path, vr, TEST_CLIENT_ID, db_with_client)

    assert r1.submission_id != r2.submission_id


def test_two_submissions_produce_two_records(
    db_with_client: Path,
    valid_result: tuple[Path, ValidationResult],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two ingestions must produce two distinct intake_submissions records."""
    monkeypatch.setenv("CPOI_DB_KEY", TEST_KEY)
    source_path, vr = valid_result
    intake_root = tmp_path / "intake_files"

    with patch("intake.ingestor.INTAKE_FILES_DIR", intake_root):
        ingest_submission(source_path, vr, TEST_CLIENT_ID, db_with_client)
        ingest_submission(source_path, vr, TEST_CLIENT_ID, db_with_client)

    conn = open_connection(db_with_client)
    cursor = conn.execute("SELECT COUNT(*) FROM intake_submissions")
    count = cursor.fetchone()[0]
    conn.close()
    assert count == 2


def test_two_submissions_produce_two_audit_entries(
    db_with_client: Path,
    valid_result: tuple[Path, ValidationResult],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two ingestions must each produce their own audit_log entry."""
    monkeypatch.setenv("CPOI_DB_KEY", TEST_KEY)
    source_path, vr = valid_result
    intake_root = tmp_path / "intake_files"

    with patch("intake.ingestor.INTAKE_FILES_DIR", intake_root):
        ingest_submission(source_path, vr, TEST_CLIENT_ID, db_with_client)
        ingest_submission(source_path, vr, TEST_CLIENT_ID, db_with_client)

    conn = open_connection(db_with_client)
    cursor = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE event_type = 'intake_ingested'"
    )
    count = cursor.fetchone()[0]
    conn.close()
    assert count == 2
