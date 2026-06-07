"""
db/audit.py
CPOI Platform — Audit Log Writer

The single point through which all audit log entries are written.
Every database write, ingestion, signal calculation, scoring, AI generation,
alert, and report event must call write_audit_log() from this module.

No other module inserts directly into the audit_log table.

The audit_log table is append-only: UPDATE and DELETE are blocked at the
database layer by triggers defined in db/schema.sql. This module enforces
the same contract at the application layer by providing only an INSERT path.

Approved event_type values (extend this list as new modules are added):
  schema_init          -- database schema applied at startup
  intake_ingested      -- Excel intake workbook successfully ingested
  intake_rejected      -- Excel intake workbook failed validation
  signal_calculated    -- signal calculation run for a submission
  score_calculated     -- OEI dimension scores and composite calculated
  score_overridden     -- Managing Partner manual score override applied
  alert_created        -- threshold breach alert record created
  alert_sent           -- alert email delivered via SendGrid
  report_generated     -- report file generated (.docx / .pdf)
  report_published     -- report published to client portal
  report_delivered     -- report downloaded or email delivered to client
  client_created       -- new client record created
  session_login        -- Managing Partner login event
  session_logout       -- Managing Partner logout event
  exception_granted    -- missing-data exception formally granted
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

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


log = _build_logger("cpoi.db.audit")

# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def write_audit_log(
    conn: Any,
    event_type: str,
    description: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    performed_by: str = "system",
    metadata: dict | None = None,
) -> str:
    """
    Insert one record into the audit_log table.

    This is the only function in the platform that writes to audit_log.
    Every database write event, ingestion, AI generation, email, and report
    action must call this function. Never INSERT into audit_log directly
    from outside this module.

    The audit_log table is append-only at both the database layer (triggers)
    and the application layer (this function provides no update or delete path).

    Args:
        conn: open, authenticated sqlcipher3 connection. The caller is
              responsible for committing the transaction after this call,
              or for including this call within a larger transaction block.
        event_type: short machine-readable label identifying the event class.
                    See the module docstring for the approved vocabulary.
                    Use a consistent value so events can be filtered and
                    counted reliably across the audit trail.
        description: human-readable description of the specific event.
                     Must be non-empty. Should include enough context that
                     the event is intelligible without additional lookup.
                     There is no enforced length limit, but descriptions
                     should be concise: one to three sentences.
        entity_type: optional — the table or domain object affected
                     (e.g. 'intake_submission', 'client', 'oei_score').
                     Use the singular table name for consistency.
        entity_id: optional — the primary key of the affected record.
                   When provided, allows filtering the audit trail for all
                   events related to a specific record.
        performed_by: the actor that triggered the event.
                      Defaults to 'system' for automated platform actions.
                      Pass 'managing_partner' or a specific user identifier
                      for actions triggered by a human.
        metadata: optional dict of additional structured context.
                  Serialized to JSON before storage. Use this for values
                  that are useful for programmatic audit analysis but would
                  make the description unwieldy (e.g. file sizes, score
                  deltas, signal values). Keys and values must be
                  JSON-serializable.

    Returns:
        str: the log_id (UUID4) of the newly created audit record.

    Raises:
        ValueError: if description is empty or whitespace-only.
        ValueError: if event_type is empty or whitespace-only.
        TypeError: if metadata is provided but is not a dict.
        Exception: any database error is propagated to the caller without
                   wrapping, so the caller's transaction handling is not
                   disrupted.
    """
    if not event_type or not event_type.strip():
        raise ValueError(
            "event_type must be a non-empty string. "
            "See db/audit.py module docstring for approved event_type values."
        )

    if not description or not description.strip():
        raise ValueError(
            "description must be a non-empty string. "
            "Every audit log entry requires a human-readable description."
        )

    if metadata is not None and not isinstance(metadata, dict):
        raise TypeError(
            f"metadata must be a dict or None, got {type(metadata).__name__}."
        )

    log_id = str(uuid.uuid4())
    performed_at = datetime.now(timezone.utc).isoformat()
    metadata_json = json.dumps(metadata) if metadata is not None else None

    conn.execute(
        """
        INSERT INTO audit_log
            (log_id, event_type, entity_type, entity_id,
             description, performed_by, performed_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            log_id,
            event_type.strip(),
            entity_type,
            entity_id,
            description.strip(),
            performed_by,
            performed_at,
            metadata_json,
        ),
    )

    log.info(
        "Audit log entry written: event_type='%s', entity_type='%s', entity_id='%s'",
        event_type,
        entity_type,
        entity_id,
    )

    return log_id
