"""
dashboard/views/client_overview.py
CPOI Platform -- Client Portfolio (Managing Partner).

One card per active client with current OEI composite, classification,
movement vs prior period, open alerts, last submission, a sparkline, and a
"View profile" action that drills through to the full Client Profile page.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard import nav
from dashboard.db_helper import run_query
from dashboard.theme import top_bar, page_title, badge


def render() -> None:
    top_bar()
    page_title("Client Portfolio", "Current-period OEI posture across active engagements")

    clients = run_query(
        """
        SELECT
            c.client_id, c.client_name, c.engagement_type, c.engagement_start_date,
            c.engagement_end_date, c.renewal_status,
            s.oei_composite_score AS current_score,
            s.composite_class     AS classification,
            s.period_date         AS last_period,
            prev.oei_composite_score AS prior_score
        FROM clients c
        LEFT JOIN (
            SELECT client_id, oei_composite_score, composite_class, period_date
            FROM oei_scores
            WHERE (client_id, period_date) IN (
                SELECT client_id, MAX(period_date) FROM oei_scores GROUP BY client_id
            )
        ) s ON s.client_id = c.client_id
        LEFT JOIN (
            SELECT o.client_id, o.oei_composite_score
            FROM oei_scores o
            WHERE o.period_date = (
                SELECT MAX(o2.period_date) FROM oei_scores o2
                WHERE o2.client_id = o.client_id
                  AND o2.period_date < (
                      SELECT MAX(o3.period_date) FROM oei_scores o3 WHERE o3.client_id = o.client_id
                  )
            )
        ) prev ON prev.client_id = c.client_id
        WHERE c.status = 'active'
        ORDER BY c.client_name
        """
    )

    if not clients:
        st.info("No active engagements yet. Add a client in Client Management to begin.")
        return

    alert_counts = run_query(
        "SELECT client_id, COUNT(*) AS n FROM alerts WHERE acknowledged = 0 GROUP BY client_id"
    )
    alert_map = {r["client_id"]: r["n"] for r in alert_counts}

    last_subs = run_query(
        """
        SELECT client_id, MAX(reporting_period_end) AS last_submission
        FROM intake_submissions WHERE ingestion_status = 'processed' GROUP BY client_id
        """
    )
    sub_map = {r["client_id"]: r["last_submission"] for r in last_subs}

    history = run_query(
        "SELECT client_id, oei_composite_score FROM oei_scores ORDER BY client_id, period_date ASC"
    )
    hist_map: dict[str, list] = {}
    for row in history:
        hist_map.setdefault(row["client_id"], []).append(row["oei_composite_score"])

    for client in clients:
        cid = client["client_id"]
        score = client["current_score"]
        prior = client["prior_score"]
        classification = client["classification"] or "N/A"
        alerts_n = alert_map.get(cid, 0)
        last_sub = sub_map.get(cid, "No submissions")

        delta_str = "N/A"
        if score is not None and prior is not None:
            delta = score - prior
            sign = "+" if delta > 0 else ""
            direction = "worsened" if delta > 0 else ("improved" if delta < 0 else "unchanged")
            delta_str = f"{sign}{delta} ({direction})"

        with st.container(border=True):
            c_name, c_score, c_class, c_delta, c_alerts, c_spark, c_action = st.columns(
                [2.4, 0.8, 1.3, 1.3, 0.8, 1.8, 1.2]
            )
            c_name.markdown(f"**{client['client_name']}**")
            eng = (client["engagement_type"] or "").upper()
            c_name.caption(f"{eng} | since {client['engagement_start_date'] or 'N/A'}")

            c_score.metric("OEI", score if score is not None else "N/A")

            c_class.markdown("**Class**")
            if classification != "N/A":
                c_class.html(badge(classification))
            else:
                c_class.caption("No scores yet")

            c_delta.markdown("**vs Prior**")
            c_delta.caption(delta_str)

            c_alerts.metric("Alerts", alerts_n)

            series = hist_map.get(cid, [])
            if len(series) >= 2:
                c_spark.line_chart(pd.DataFrame({"OEI": series[-5:]}), height=70)
            else:
                c_spark.caption("Trend pending")

            if c_action.button("View profile", key=f"view_{cid}", type="primary"):
                st.session_state["profile_client_id"] = cid
                nav.goto("profile")
