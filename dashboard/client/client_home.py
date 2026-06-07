"""
dashboard/client/client_home.py
CPOI Platform -- Client portal dashboard.

The executive sponsor's own results only. Per the client portal spec, the
sponsor sees their OEI composite, the five dimension scores, the trajectory,
and active alerts as severity + description (NEVER raw signal values, the
methodology, the audit log, the journal, or any other client's data).

All queries are scoped to the client_id in the authenticated session.
"""

from __future__ import annotations

import base64
from datetime import date

import pandas as pd
import streamlit as st

from dashboard.db_helper import run_query
from dashboard.theme import top_bar, page_title, badge, BAND_BG, NAVY

_DIMS = [
    ("Strategic Saturation",      "strategic_saturation_score",      "strategic_saturation_class"),
    ("Governance Responsiveness", "governance_responsiveness_score", "governance_responsiveness_class"),
    ("Execution Visibility",      "execution_visibility_score",      "execution_visibility_class"),
    ("Reporting Integrity",       "reporting_integrity_score",       "reporting_integrity_class"),
    ("Org Sustainability",        "org_sustainability_score",        "org_sustainability_class"),
]


def render() -> None:
    cid = st.session_state.get("auth_client_id")
    name = st.session_state.get("auth_client_name", "Your organization")

    top_bar(tagline="Secure Client Portal")
    page_title(name, "Operational Executive Intelligence — current period")

    client = run_query(
        "SELECT engagement_type, engagement_end_date FROM clients WHERE client_id = ?", (cid,)
    )
    if client and client[0].get("engagement_end_date"):
        end = client[0]["engagement_end_date"]
        try:
            days = (date.fromisoformat(str(end)) - date.today()).days
            if days >= 0:
                st.caption(f"Engagement active · next review window ends {end} ({days} days)")
        except ValueError:
            pass

    scores = run_query(
        "SELECT * FROM oei_scores WHERE client_id = ? ORDER BY period_date DESC LIMIT 1", (cid,)
    )
    if not scores:
        st.info(
            "Your intelligence analysis is in progress. Your results will appear here once your "
            "first reporting period has been processed."
        )
        return
    current = scores[0]

    prior = run_query(
        "SELECT * FROM oei_scores WHERE client_id = ? AND period_date < ? ORDER BY period_date DESC LIMIT 1",
        (cid, current["period_date"]),
    )
    prior = prior[0] if prior else None

    # ---- Composite hero ----
    delta_caption = ""
    if prior:
        delta = current["oei_composite_score"] - prior["oei_composite_score"]
        if delta != 0:
            direction = "higher risk" if delta > 0 else "improved"
            delta_caption = f"{'+' if delta > 0 else ''}{delta} vs prior period ({direction})"
        else:
            delta_caption = "Unchanged vs prior period"

    hero, dims = st.columns([1, 2.4])
    with hero:
        st.html(
            f'<div class="cp-card" style="text-align:center;border-left-color:{NAVY};">'
            f'<div class="cp-stat-label">OEI Composite</div>'
            f'<div class="cp-stat">{current["oei_composite_score"]}</div>'
            f'{badge(current["composite_class"])}</div>'
        )
        if delta_caption:
            st.caption(delta_caption)

    with dims:
        rows = []
        for label, score_col, class_col in _DIMS:
            rows.append({
                "Dimension": label,
                "Score": current[score_col],
                "Classification": current[class_col],
            })
        df = pd.DataFrame(rows)
        st.dataframe(
            df.style.map(
                lambda v: f"background-color: {BAND_BG.get(v,'')};", subset=["Classification"]
            ),
            use_container_width=True, hide_index=True,
        )

    # ---- Trajectory (up to 4 prior periods) ----
    history = run_query(
        "SELECT period_date, oei_composite_score FROM oei_scores WHERE client_id = ? ORDER BY period_date ASC",
        (cid,),
    )
    if len(history) >= 2:
        st.markdown("#### Score Trajectory")
        rendered = False
        try:
            from reports.chart import generate_trajectory_chart
            b64 = generate_trajectory_chart(history[-5:])
            if b64:
                st.image(base64.b64decode(b64), use_container_width=True)
                rendered = True
        except Exception:
            rendered = False
        if not rendered:
            st.line_chart(pd.DataFrame(history[-5:]).set_index("period_date")["oei_composite_score"])

    # ---- Active alerts: severity + description only (NO raw signal values) ----
    st.markdown("#### Active Intelligence Alerts")
    alerts = run_query(
        """
        SELECT alert_severity, alert_message, triggered_at
        FROM alerts WHERE client_id = ? AND acknowledged = 0 ORDER BY triggered_at DESC
        """,
        (cid,),
    )
    if not alerts:
        st.success("No active alerts for the current period.")
    else:
        for a in alerts:
            with st.container(border=True):
                cs, ct = st.columns([4, 1])
                cs.markdown(f"**{(a['alert_severity'] or '').upper()}**")
                cs.caption(a["alert_message"] or "An intelligence threshold was breached this period.")
                ct.caption(str(a["triggered_at"])[:10])
