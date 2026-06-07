"""
dashboard/billing.py
CPOI Platform -- invoice / billing status (data layer).

Simple invoice line items per client. The Managing Partner records each
billing milestone (e.g. Snapshot 30/40/30) and its status; the client sees a
read-only Paid/Due/Overdue view on their Engagement page.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from dashboard.db_helper import get_connection, run_query
from db.audit import write_audit_log

STATUS_OPTIONS = ["due", "paid", "overdue"]

# Snapshot standard schedule (label, fraction of total).
SNAPSHOT_SCHEDULE = [
    ("Deposit (30%) - engagement start", 0.30),
    ("Interpretation (40%) - before scoring", 0.40),
    ("Deliverable (30%) - before report", 0.30),
]


def list_invoices(client_id: str) -> list[dict]:
    return run_query(
        """
        SELECT invoice_id, label, amount, status, due_date
        FROM invoices WHERE client_id = ? ORDER BY created_at ASC
        """,
        (client_id,),
    )


def add_invoice(client_id: str, label: str, amount: float | None,
                status: str = "due", due_date: str | None = None) -> str:
    if status not in STATUS_OPTIONS:
        raise ValueError("status must be due, paid, or overdue.")
    invoice_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO invoices (invoice_id, client_id, label, amount, status, due_date)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (invoice_id, client_id, label.strip(), amount, status, due_date),
        )
        write_audit_log(
            conn, event_type="invoice_created", entity_type="invoice", entity_id=invoice_id,
            description=f"Invoice line '{label.strip()}' created ({status}).",
            performed_by="managing_partner", metadata={"client_id": client_id, "amount": amount},
        )
        conn.commit()
    finally:
        conn.close()
    return invoice_id


def set_status(invoice_id: str, status: str) -> None:
    if status not in STATUS_OPTIONS:
        raise ValueError("status must be due, paid, or overdue.")
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE invoices SET status = ?, updated_at = ? WHERE invoice_id = ?",
            (status, datetime.now(timezone.utc).isoformat(), invoice_id),
        )
        write_audit_log(
            conn, event_type="invoice_updated", entity_type="invoice", entity_id=invoice_id,
            description=f"Invoice status set to '{status}'.", performed_by="managing_partner",
        )
        conn.commit()
    finally:
        conn.close()


def delete_invoice(invoice_id: str) -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM invoices WHERE invoice_id = ?", (invoice_id,))
        conn.commit()
    finally:
        conn.close()


def create_snapshot_schedule(client_id: str, total: float) -> None:
    """Create the standard 30/40/30 Snapshot invoice lines for a client."""
    for label, frac in SNAPSHOT_SCHEDULE:
        add_invoice(client_id, label, round(total * frac, 2), status="due")
