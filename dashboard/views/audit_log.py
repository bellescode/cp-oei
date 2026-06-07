"""
dashboard/views/audit_log.py
CPOI Platform -- Audit Log viewer (Managing Partner). Read-only with filters
and CSV export. All writes go through db/audit.py.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from dashboard.db_helper import run_query
from dashboard.theme import top_bar, page_title


def render() -> None:
    top_bar()
    page_title("Audit Log", "Complete, append-only platform audit trail")

    c_from, c_to, c_event, c_entity, c_by = st.columns([1.5, 1.5, 2, 1.5, 1.5])
    date_from = c_from.date_input("From", value=date.today() - timedelta(days=30), key="audit_from")
    date_to = c_to.date_input("To", value=date.today(), key="audit_to")

    event_types = run_query("SELECT DISTINCT event_type FROM audit_log ORDER BY event_type")
    event_filter = c_event.selectbox("Event type", ["All"] + [r["event_type"] for r in event_types])

    entity_types = run_query(
        "SELECT DISTINCT entity_type FROM audit_log WHERE entity_type IS NOT NULL ORDER BY entity_type"
    )
    entity_filter = c_entity.selectbox("Entity type", ["All"] + [r["entity_type"] for r in entity_types])

    performers = run_query(
        "SELECT DISTINCT performed_by FROM audit_log WHERE performed_by IS NOT NULL ORDER BY performed_by"
    )
    by_filter = c_by.selectbox("Performed by", ["All"] + [r["performed_by"] for r in performers])

    sql = """
        SELECT performed_at, event_type, entity_type, entity_id, description, performed_by
        FROM audit_log WHERE DATE(performed_at) >= ? AND DATE(performed_at) <= ?
    """
    params: list = [date_from.isoformat(), date_to.isoformat()]
    if event_filter != "All":
        sql += " AND event_type = ?"
        params.append(event_filter)
    if entity_filter != "All":
        sql += " AND entity_type = ?"
        params.append(entity_filter)
    if by_filter != "All":
        sql += " AND performed_by = ?"
        params.append(by_filter)
    sql += " ORDER BY performed_at DESC LIMIT 500"

    rows = run_query(sql, tuple(params))
    st.divider()
    st.markdown(f"**{len(rows)}** event(s) found (capped at 500)")

    if rows:
        df = pd.DataFrame(rows)
        df.columns = ["Timestamp", "Event Type", "Entity Type", "Entity ID", "Description", "By"]
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.download_button(
            "Export as CSV", data=df.to_csv(index=False).encode("utf-8"),
            file_name=f"audit_log_{date_from}_{date_to}.csv", mime="text/csv", key="audit_export",
        )
    else:
        st.info("No audit events match the current filters.")
