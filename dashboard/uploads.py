"""
dashboard/uploads.py
CPOI Platform -- client self-service upload queue (data layer).

A client-submitted workbook is stored on disk and recorded in client_uploads
with status 'pending_review'. It is NOT validated-into or scored until the
Managing Partner approves it on the Data Intake page. This module only stores,
lists, and transitions uploads; the actual ingest/score/alert pipeline runs in
the MP view on approval.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from dashboard.db_helper import get_connection, run_query
from db.audit import write_audit_log

UPLOADS_DIR = Path(__file__).parent.parent / "intake_uploads"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_client_upload(
    client_id: str, original_filename: str, data: bytes,
    uploaded_by: str, validation_ok: bool | None,
) -> str:
    """Persist the uploaded bytes and queue them for Managing Partner review."""
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    upload_id = str(uuid.uuid4())
    stored_path = UPLOADS_DIR / f"{upload_id}.xlsx"
    stored_path.write_bytes(data)

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO client_uploads
                (upload_id, client_id, original_filename, stored_path, uploaded_by,
                 uploaded_at, validation_ok, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_review')
            """,
            (
                upload_id, client_id, original_filename, str(stored_path), uploaded_by,
                _now(), None if validation_ok is None else (1 if validation_ok else 0),
            ),
        )
        write_audit_log(
            conn, event_type="client_upload_received", entity_type="client_upload",
            entity_id=upload_id,
            description=f"Client submitted workbook '{original_filename}' for review.",
            performed_by=f"client:{uploaded_by}",
            metadata={"client_id": client_id, "validation_ok": validation_ok},
        )
        client_name_row = conn.execute(
            "SELECT client_name FROM clients WHERE client_id = ?", (client_id,)
        ).fetchone()
        client_name = client_name_row[0] if client_name_row else client_id
        conn.commit()
    finally:
        conn.close()

    # Best-effort notification to the Managing Partner. Never fail the upload
    # because email delivery (or configuration) failed.
    try:
        from dashboard.email_helper import send_submission_notification
        send_submission_notification(client_name, uploaded_by, original_filename)
    except Exception:
        pass

    return upload_id


def list_pending() -> list[dict]:
    """All pending uploads across clients, oldest first (the MP review queue)."""
    return run_query(
        """
        SELECT u.upload_id, u.client_id, c.client_name, u.original_filename,
               u.stored_path, u.uploaded_by, u.uploaded_at, u.validation_ok
        FROM client_uploads u JOIN clients c ON c.client_id = u.client_id
        WHERE u.status = 'pending_review'
        ORDER BY u.uploaded_at ASC
        """
    )


def list_for_client(client_id: str) -> list[dict]:
    """A client's own upload history, newest first."""
    return run_query(
        """
        SELECT original_filename, uploaded_at, status, review_note, validation_ok
        FROM client_uploads WHERE client_id = ? ORDER BY uploaded_at DESC
        """,
        (client_id,),
    )


def get_upload(upload_id: str) -> dict | None:
    rows = run_query("SELECT * FROM client_uploads WHERE upload_id = ?", (upload_id,))
    return rows[0] if rows else None


def mark_approved(upload_id: str, submission_id: str, reviewed_by: str = "managing_partner") -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE client_uploads
            SET status = 'approved', submission_id = ?, reviewed_by = ?, reviewed_at = ?
            WHERE upload_id = ?
            """,
            (submission_id, reviewed_by, _now(), upload_id),
        )
        write_audit_log(
            conn, event_type="client_upload_approved", entity_type="client_upload",
            entity_id=upload_id,
            description="Client upload approved and processed by Managing Partner.",
            performed_by=reviewed_by, metadata={"submission_id": submission_id},
        )
        conn.commit()
    finally:
        conn.close()


def mark_rejected(upload_id: str, note: str, reviewed_by: str = "managing_partner") -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE client_uploads
            SET status = 'rejected', review_note = ?, reviewed_by = ?, reviewed_at = ?
            WHERE upload_id = ?
            """,
            (note.strip() or None, reviewed_by, _now(), upload_id),
        )
        write_audit_log(
            conn, event_type="client_upload_rejected", entity_type="client_upload",
            entity_id=upload_id,
            description=f"Client upload rejected by Managing Partner. Note: {note.strip()}",
            performed_by=reviewed_by,
        )
        conn.commit()
    finally:
        conn.close()


def pending_count() -> int:
    rows = run_query("SELECT COUNT(*) AS n FROM client_uploads WHERE status = 'pending_review'")
    return rows[0]["n"] if rows else 0
