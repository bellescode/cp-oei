"""
intake/ingestor.py
CPOI Platform — Intake Submission Ingestor

Accepts a validated ValidationResult and writes the submission to the database
and file system. This is the only path through which intake data enters the
platform. It never runs on unvalidated input.

Design contract:
  - Raises ValueError if the ValidationResult is not valid.
  - Raises ValueError if the client_id does not exist in the clients table.
  - File copy and database write succeed together or both roll back.
    No orphaned files. No partial database records.
  - Returns an IngestionResult that gives Module 2 (Signal Calculation)
    everything it needs: submission_id, dataframes, and metadata.
  - Every ingestion event is written to audit_log before this function returns.

Usage:
    from intake.ingestor import ingest_submission

    result = ingest_submission(
        source_file_path=Path("CP_OEI_DataIntake_Acme_2026_05.xlsx"),
        validation_result=validation_result,
        client_id="acme-corp-001",
    )
"""

import json
import logging
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from db.audit import write_audit_log
from db.init_db import open_connection
from intake.validator import ValidationResult

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def _build_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


log = _build_logger("cpoi.intake.ingestor")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Root directory for all stored intake files.
# Lives at the project root alongside cpoi.db.
INTAKE_FILES_DIR = Path(__file__).parent.parent / "intake_files"


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class IngestionResult:
    """
    Outcome of a successful ingestion.

    Passed directly to the Signal Calculation Engine (Module 2).

    Attributes:
        submission_id: UUID string of the created intake_submissions record.
        client_id: the client this submission belongs to.
        stored_file_path: absolute path where the .xlsx file was stored.
        reporting_period_start: YYYY-MM-DD string from workbook metadata.
        reporting_period_end: YYYY-MM-DD string from workbook metadata.
        dataframes: normalized pandas DataFrames keyed by tab name,
                    ready for signal calculation.
        metadata: full METADATA tab dict from the workbook.
    """
    submission_id: str
    client_id: str
    stored_file_path: Path
    reporting_period_start: str
    reporting_period_end: str
    dataframes: dict[str, pd.DataFrame]
    metadata: dict[str, str]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _verify_client_exists(conn, client_id: str) -> None:
    """
    Confirm that the client_id exists in the clients table.

    Args:
        conn: open, authenticated database connection.
        client_id: the client identifier to look up.

    Raises:
        ValueError: if no matching client record is found.
    """
    cursor = conn.execute(
        "SELECT client_id FROM clients WHERE client_id = ?",
        (client_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError(
            f"Client '{client_id}' not found in the clients table. "
            "Create the client record before ingesting a submission."
        )


def _build_intake_file_path(client_id: str, submission_id: str) -> Path:
    """
    Return the destination path for a stored intake file.

    Layout: intake_files/{client_id}/{submission_id}.xlsx

    Args:
        client_id: client identifier (used as directory name).
        submission_id: UUID of the submission (used as filename).

    Returns:
        Path: absolute destination path.
    """
    return INTAKE_FILES_DIR / client_id / f"{submission_id}.xlsx"


def _copy_intake_file(source: Path, destination: Path) -> None:
    """
    Copy the source .xlsx file to the intake_files directory.

    Creates the destination directory tree if it does not exist.

    Args:
        source: path to the original uploaded file.
        destination: path where the file should be stored.

    Raises:
        FileNotFoundError: if source does not exist.
        OSError: if the copy or directory creation fails.
    """
    if not source.exists():
        raise FileNotFoundError(
            f"Source intake file not found: '{source}'."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(source), str(destination))
    log.info("Intake file copied to '%s'", destination)


def _insert_intake_submission(
    conn,
    submission_id: str,
    client_id: str,
    reporting_period_start: str,
    reporting_period_end: str,
    submitted_at: str,
    file_path: Path,
    processed_at: str,
) -> None:
    """
    Insert a single record into the intake_submissions table.

    Args:
        conn: open, authenticated database connection (within a transaction).
        submission_id: UUID string.
        client_id: FK to clients table.
        reporting_period_start: YYYY-MM-DD.
        reporting_period_end: YYYY-MM-DD.
        submitted_at: ISO timestamp from workbook Submission_Date field.
        file_path: absolute path to the stored .xlsx file.
        processed_at: ISO timestamp of when ingestion completed.

    Raises:
        Exception: any database error is propagated to the caller.
    """
    conn.execute(
        """
        INSERT INTO intake_submissions (
            submission_id,
            client_id,
            reporting_period_start,
            reporting_period_end,
            submitted_at,
            file_path,
            ingestion_status,
            processed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            submission_id,
            client_id,
            reporting_period_start,
            reporting_period_end,
            submitted_at,
            str(file_path),
            "processed",
            processed_at,
        ),
    )


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def ingest_submission(
    source_file_path: Path,
    validation_result: ValidationResult,
    client_id: str,
    db_path: Path | None = None,
) -> IngestionResult:
    """
    Ingest a validated intake submission into the platform.

    Stores the Excel file on disk and creates an intake_submissions record.
    Writes an audit log entry. Returns an IngestionResult ready for Module 2.

    This function enforces the no-partial-ingestion contract: the file copy
    and database write succeed together, or both are rolled back. If the
    database write fails after the file has been copied, the copied file is
    deleted before the exception is re-raised.

    Args:
        source_file_path: path to the uploaded .xlsx file to be ingested.
        validation_result: must have valid=True. Raises ValueError otherwise.
        client_id: the client this submission belongs to. Must exist in the
                   clients table. Raises ValueError if not found.
        db_path: optional override for the database file path.

    Returns:
        IngestionResult: submission_id, stored file path, dataframes,
                         and metadata ready for Module 2.

    Raises:
        ValueError: if validation_result.valid is False, or if client_id
                    is not found in the clients table.
        FileNotFoundError: if source_file_path does not exist.
        RuntimeError: if CPOI_DB_KEY is not set, or if the database
                      write fails after the file was already copied
                      (file is cleaned up before raising).
        OSError: if the file copy fails.
    """
    # Step 1: Enforce validated-only ingestion
    if not validation_result.valid:
        raise ValueError(
            "Cannot ingest an invalid submission. "
            "ValidationResult.valid is False. "
            f"Validation errors: {validation_result.errors}"
        )

    assert validation_result.dataframes is not None, (
        "ValidationResult.valid is True but dataframes is None. "
        "This indicates a bug in the validator."
    )
    assert validation_result.metadata is not None, (
        "ValidationResult.valid is True but metadata is None. "
        "This indicates a bug in the validator."
    )

    log.info(
        "Ingestion started for client '%s', file '%s'",
        client_id,
        source_file_path.name,
    )

    # Step 2: Open database connection and verify client exists
    conn = open_connection(db_path)
    try:
        _verify_client_exists(conn, client_id)
    except ValueError:
        conn.close()
        raise

    # Step 3: Build identifiers and timestamps
    submission_id = str(uuid.uuid4())
    processed_at = datetime.now(timezone.utc).isoformat()

    # submitted_at comes from the workbook metadata; fall back to now if absent
    submission_date_raw = validation_result.metadata.get("Submission_Date", "")
    submitted_at = (
        submission_date_raw
        if submission_date_raw
        else processed_at
    )

    reporting_period_start = validation_result.metadata["Reporting_Period_Start"]
    reporting_period_end = validation_result.metadata["Reporting_Period_End"]

    # Step 4: Copy the file to the intake directory
    destination = _build_intake_file_path(client_id, submission_id)
    try:
        _copy_intake_file(source_file_path, destination)
    except (FileNotFoundError, OSError):
        conn.close()
        raise

    # Step 5: Write to the database (intake_submissions + audit_log)
    # If this fails, delete the copied file to preserve the no-partial-ingestion contract.
    try:
        _insert_intake_submission(
            conn=conn,
            submission_id=submission_id,
            client_id=client_id,
            reporting_period_start=reporting_period_start,
            reporting_period_end=reporting_period_end,
            submitted_at=submitted_at,
            file_path=destination,
            processed_at=processed_at,
        )

        write_audit_log(
            conn=conn,
            event_type="intake_ingested",
            entity_type="intake_submission",
            entity_id=submission_id,
            description=(
                f"Intake submission ingested for client '{client_id}'. "
                f"Period: {reporting_period_start} to {reporting_period_end}. "
                f"Source file: '{source_file_path.name}'."
            ),
            metadata={
                "client_id": client_id,
                "submission_id": submission_id,
                "source_file": source_file_path.name,
                "stored_file": str(destination),
                "reporting_period_start": reporting_period_start,
                "reporting_period_end": reporting_period_end,
                "engagement_type": validation_result.metadata.get("Engagement_Type", ""),
                "data_version": validation_result.metadata.get("Data_Version", ""),
            },
        )

        conn.commit()

    except Exception as exc:
        # Roll back the transaction and delete the copied file
        conn.rollback()
        conn.close()
        if destination.exists():
            try:
                destination.unlink()
                log.info(
                    "Rolled back: deleted copied file at '%s' after DB write failure",
                    destination,
                )
            except OSError as cleanup_exc:
                log.info(
                    "Rollback warning: could not delete '%s' after DB failure: %s",
                    destination,
                    cleanup_exc,
                )
        raise RuntimeError(
            f"Database write failed during ingestion for client '{client_id}'. "
            f"File copy has been rolled back. Original error: {exc}"
        ) from exc

    conn.close()

    log.info(
        "Ingestion complete. submission_id='%s', client='%s', period='%s to %s'",
        submission_id,
        client_id,
        reporting_period_start,
        reporting_period_end,
    )

    return IngestionResult(
        submission_id=submission_id,
        client_id=client_id,
        stored_file_path=destination,
        reporting_period_start=reporting_period_start,
        reporting_period_end=reporting_period_end,
        dataframes=validation_result.dataframes,
        metadata=validation_result.metadata,
    )
