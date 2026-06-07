"""
dashboard/access.py
CPOI Platform -- client portal access window.

Snapshot engagements grant portal access for 14 calendar days from the date
the report is delivered (published). After that the engagement is considered
concluded and the client can no longer access the portal.

Resolution order:
  1. clients.portal_access_expires (manual override set by the Managing
     Partner) -- authoritative if present.
  2. Snapshot auto-rule: first delivered report's date + 14 days.
  3. Otherwise: active (no auto-expiry, e.g. an OEIL engagement in progress).

A closed engagement is always concluded.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from dashboard.db_helper import run_query

SNAPSHOT_ACCESS_DAYS = 14


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def access_state(client: dict) -> dict:
    """
    Return {"state": "active"|"concluded", "expires": date|None, "reason": str}.
    """
    cid = client["client_id"]

    if client.get("status") == "closed":
        return {"state": "concluded", "expires": None, "reason": "Engagement closed."}

    today = date.today()

    override = _parse_date(client.get("portal_access_expires"))
    if override is not None:
        if today > override:
            return {"state": "concluded", "expires": override, "reason": "Access window ended."}
        return {"state": "active", "expires": override, "reason": "Manual access window."}

    if (client.get("engagement_type") or "snapshot") == "snapshot":
        rows = run_query(
            """
            SELECT MIN(delivered_at) AS first_delivered
            FROM reports WHERE client_id = ? AND delivered = 1 AND delivered_at IS NOT NULL
            """,
            (cid,),
        )
        first_delivered = rows[0]["first_delivered"] if rows else None
        d = _parse_date(first_delivered)
        if d is not None:
            expires = d + timedelta(days=SNAPSHOT_ACCESS_DAYS)
            if today > expires:
                return {"state": "concluded", "expires": expires,
                        "reason": "Snapshot access window ended."}
            return {"state": "active", "expires": expires, "reason": "Snapshot access window."}

    return {"state": "active", "expires": None, "reason": "Active engagement."}
