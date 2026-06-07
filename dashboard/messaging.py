"""
dashboard/messaging.py
CPOI Platform -- secure message thread data layer.

One thread per client between the Managing Partner and the client's executive
sponsor. Bodies are encrypted with dashboard/crypto.py before storage and
decrypted on read. Every send is recorded in the audit log. All reads are
scoped to a single client_id.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from dashboard import crypto
from dashboard.db_helper import get_connection
from db.audit import write_audit_log


def send_message(client_id: str, sender_role: str, sender_label: str, body: str) -> str:
    """Encrypt and store one message. Returns the new message_id."""
    if sender_role not in ("managing_partner", "client"):
        raise ValueError("sender_role must be 'managing_partner' or 'client'.")
    if not body.strip():
        raise ValueError("Message body cannot be empty.")

    stored, is_encrypted = crypto.encrypt(body.strip())
    message_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO messages
                (message_id, client_id, sender_role, sender_label, body_ciphertext,
                 is_encrypted, created_at, read_by_client, read_by_mp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id, client_id, sender_role, sender_label, stored,
                1 if is_encrypted else 0, now,
                1 if sender_role == "client" else 0,
                1 if sender_role == "managing_partner" else 0,
            ),
        )
        write_audit_log(
            conn, event_type="message_sent", entity_type="message", entity_id=message_id,
            description=f"Secure portal message sent by {sender_role}.",
            performed_by=sender_role, metadata={"client_id": client_id, "encrypted": is_encrypted},
        )
        conn.commit()
    finally:
        conn.close()
    return message_id


def get_thread(client_id: str) -> list[dict]:
    """Return the full decrypted thread for a client, oldest first."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT message_id, sender_role, sender_label, body_ciphertext, is_encrypted, created_at
            FROM messages WHERE client_id = ? ORDER BY created_at ASC
            """,
            (client_id,),
        ).fetchall()
    finally:
        conn.close()

    thread = []
    for r in rows:
        thread.append({
            "message_id": r[0],
            "sender_role": r[1],
            "sender_label": r[2],
            "body": crypto.decrypt(r[3], bool(r[4])),
            "created_at": r[5],
        })
    return thread


def mark_thread_read(client_id: str, reader_role: str) -> None:
    """Mark all inbound messages in a thread as read by the given role."""
    column = "read_by_mp" if reader_role == "managing_partner" else "read_by_client"
    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE messages SET {column} = 1 WHERE client_id = ? AND {column} = 0",
            (client_id,),
        )
        conn.commit()
    finally:
        conn.close()


def unread_count(client_id: str, reader_role: str) -> int:
    """Count messages in a client's thread not yet read by the given role."""
    column = "read_by_mp" if reader_role == "managing_partner" else "read_by_client"
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT COUNT(*) FROM messages WHERE client_id = ? AND {column} = 0",
            (client_id,),
        ).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def total_unread_mp() -> int:
    """Total messages across all clients not yet read by the Managing Partner."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) FROM messages WHERE read_by_mp = 0").fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def unread_by_client_for_mp() -> dict:
    """Map of client_id -> unread count for the Managing Partner."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT client_id, COUNT(*) FROM messages WHERE read_by_mp = 0 GROUP BY client_id"
        ).fetchall()
        return {r[0]: r[1] for r in rows}
    finally:
        conn.close()
