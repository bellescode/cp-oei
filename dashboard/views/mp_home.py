"""
dashboard/views/mp_home.py
CPOI Platform -- Managing Partner portfolio overview (home).

Branded summary of the whole engagement portfolio. No developer-facing
status text -- if the database is unreachable the user sees a calm,
branded message rather than a stack trace.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.db_helper import run_query
from dashboard.theme import top_bar, page_title, stat_card, NAVY, GOLD


def render() -> None:
    top_bar()
    page_title("Portfolio Overview", "Operational Executive Intelligence across all engagements")

    try:
        active = run_query("SELECT COUNT(*) AS n FROM clients WHERE status = 'active'")[0]["n"]
        unacked = run_query("SELECT COUNT(*) AS n FROM alerts WHERE acknowledged = 0")[0]["n"]
        total_reports = run_query("SELECT COUNT(*) AS n FROM reports")[0]["n"]
        pending = run_query(
            "SELECT COUNT(*) AS n FROM report_drafts WHERE status = 'pending_review'"
        )[0]["n"]
    except Exception:
        st.warning(
            "We could not reach the intelligence database. Please confirm the platform "
            "is configured correctly, then refresh."
        )
        return

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        stat_card("Active Engagements", active, accent=NAVY)
    with c2:
        stat_card("Open Alerts", unacked, accent="#B52A1C" if unacked else GOLD)
    with c3:
        stat_card("Reports Generated", total_reports, accent=GOLD)
    with c4:
        stat_card("Drafts Awaiting Review", pending, accent=GOLD)

    st.write("")

    # ---- Engagements needing attention ----
    st.markdown("#### Attention required")

    try:
        from dashboard.uploads import pending_count
        pend = pending_count()
        if pend:
            st.warning(f"{pend} client submission(s) awaiting your review in Data Intake.")
    except Exception:
        pass

    renewals = run_query(
        """
        SELECT client_name, engagement_end_date, renewal_status
        FROM clients
        WHERE status = 'active' AND engagement_end_date IS NOT NULL
          AND DATE(engagement_end_date) <= DATE('now', '+45 day')
        ORDER BY engagement_end_date ASC
        """
    )
    if renewals:
        df = pd.DataFrame(renewals)
        df.columns = ["Client", "Engagement Ends", "Renewal"]
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption("Engagements ending within 45 days. Confirm renewal intent in Client Management.")
    else:
        st.caption("No engagements ending in the next 45 days.")

    st.write("")
    st.markdown("#### Recent activity")
    recent = run_query(
        """
        SELECT performed_at, event_type, description, performed_by
        FROM audit_log ORDER BY performed_at DESC LIMIT 10
        """
    )
    if recent:
        df = pd.DataFrame(recent)
        df.columns = ["Timestamp", "Event", "Detail", "By"]
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.caption("No activity recorded yet.")
