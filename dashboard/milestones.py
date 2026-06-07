"""
dashboard/milestones.py
CPOI Platform -- engagement milestone tracking.

Each engagement type has an ordered list of milestones. A milestone's status
is resolved as: the Managing Partner's stored override if present, otherwise an
automatic status derived from platform activity (uploads, scores, delivered
reports, engagement status). The first non-complete milestone is shown as the
current/active step.
"""

from __future__ import annotations

from dashboard.db_helper import get_connection, run_query
from db.audit import write_audit_log

# Ordered milestone definitions per engagement type: (key, label)
SNAPSHOT_MILESTONES: list[tuple[str, str]] = [
    ("intake_submitted",      "Intake Assessment Submitted"),
    ("engagement_confirmed",  "Engagement Confirmed"),
    ("discovery_completed",   "Discovery Session Completed"),
    ("data_received",         "Data Submission Received"),
    ("analysis_in_progress",  "Intelligence Analysis In Progress"),
    ("report_delivered",      "Report Delivered"),
    ("review_scheduled",      "Executive Intelligence Review Scheduled"),
    ("engagement_complete",   "Engagement Complete"),
]

OEIL_MILESTONES: list[tuple[str, str]] = [
    ("engagement_confirmed",  "Engagement Confirmed"),
    ("onboarding_completed",  "Onboarding Completed"),
    ("data_received",         "Latest Data Received"),
    ("brief_published",       "Monthly Brief Published"),
    ("review_scheduled",      "Review Scheduled"),
]

STATUS_OPTIONS = ["pending", "active", "complete"]


def definitions_for(engagement_type: str) -> list[tuple[str, str]]:
    return OEIL_MILESTONES if engagement_type == "oeil" else SNAPSHOT_MILESTONES


def _overrides(client_id: str) -> dict[str, str]:
    rows = run_query(
        "SELECT milestone_key, status FROM engagement_milestones WHERE client_id = ?",
        (client_id,),
    )
    return {r["milestone_key"]: r["status"] for r in rows}


def _auto_status(client: dict) -> dict[str, str]:
    """Derive milestone completion from platform activity."""
    cid = client["client_id"]

    def _exists(sql: str) -> bool:
        return bool(run_query(sql, (cid,)))

    has_journal_discovery = _exists(
        "SELECT 1 FROM engagement_journal WHERE client_id = ? AND entry_type = 'discovery_call' LIMIT 1"
    )
    has_upload = _exists("SELECT 1 FROM client_uploads WHERE client_id = ? LIMIT 1") or \
        _exists("SELECT 1 FROM intake_submissions WHERE client_id = ? LIMIT 1")
    has_scores = _exists("SELECT 1 FROM oei_scores WHERE client_id = ? LIMIT 1")
    has_delivered = _exists("SELECT 1 FROM reports WHERE client_id = ? AND delivered = 1 LIMIT 1")
    confirmed = bool(client.get("engagement_start_date"))
    closed = (client.get("status") == "closed")

    auto = {
        "intake_submitted":     "complete",
        "engagement_confirmed": "complete" if confirmed else "pending",
        "discovery_completed":  "complete" if has_journal_discovery else "pending",
        "onboarding_completed": "complete" if has_journal_discovery else "pending",
        "data_received":        "complete" if has_upload else "pending",
        "analysis_in_progress": "complete" if has_scores else ("active" if has_upload else "pending"),
        "brief_published":      "complete" if has_delivered else "pending",
        "report_delivered":     "complete" if has_delivered else "pending",
        "review_scheduled":     "pending",
        "engagement_complete":  "complete" if closed else "pending",
    }
    return auto


def get_tracker(client: dict) -> list[dict]:
    """
    Return the ordered tracker for a client:
      [{key, label, status} ...] with status in pending/active/complete.
    Override beats auto. The first non-complete step is surfaced as 'active'.
    """
    engagement_type = client.get("engagement_type") or "snapshot"
    defs = definitions_for(engagement_type)
    overrides = _overrides(client["client_id"])
    auto = _auto_status(client)

    resolved = []
    for key, label in defs:
        status = overrides.get(key) or auto.get(key, "pending")
        resolved.append({"key": key, "label": label, "status": status})

    # Mark the first non-complete step as the current/active one for display.
    for item in resolved:
        if item["status"] != "complete":
            item["status"] = "active"
            break
    return resolved


def set_milestone(client_id: str, key: str, status: str) -> None:
    """Managing Partner override of a milestone status."""
    if status not in STATUS_OPTIONS:
        raise ValueError("status must be pending, active, or complete.")
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO engagement_milestones (client_id, milestone_key, status, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(client_id, milestone_key)
            DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at
            """,
            (client_id, key, status),
        )
        write_audit_log(
            conn, event_type="milestone_updated", entity_type="client", entity_id=client_id,
            description=f"Milestone '{key}' set to '{status}'.",
            performed_by="managing_partner", metadata={"milestone": key, "status": status},
        )
        conn.commit()
    finally:
        conn.close()
