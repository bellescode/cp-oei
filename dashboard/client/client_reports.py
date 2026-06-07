"""
dashboard/client/client_reports.py
CPOI Platform -- Client portal reports tab.

Only published reports (reports.delivered = 1) for THIS client are listed.
Downloads are served over the same HTTPS session. No other client's reports
are ever queried.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from dashboard.db_helper import run_query
from dashboard.theme import top_bar, page_title

_TYPE_LABEL = {
    "snapshot": "OEI Snapshot",
    "monthly_brief": "Monthly Intelligence Brief",
    "quarterly_review": "Quarterly Intelligence Review",
}


def render() -> None:
    cid = st.session_state.get("auth_client_id")
    top_bar(tagline="Secure Client Portal")
    page_title("Your Reports", "Published intelligence reports for your engagement")

    reports = run_query(
        """
        SELECT report_id, report_type, period_date, delivered_at, file_path
        FROM reports
        WHERE client_id = ? AND delivered = 1
        ORDER BY period_date DESC, generated_at DESC
        """,
        (cid,),
    )

    if not reports:
        st.info(
            "No reports have been published to your portal yet. You will be notified when your "
            "next intelligence report is available."
        )
        return

    for r in reports:
        with st.container(border=True):
            c_title, c_period, c_action = st.columns([3, 2, 2])
            c_title.markdown(f"**{_TYPE_LABEL.get(r['report_type'], r['report_type'])}**")
            c_title.caption(f"Published {str(r['delivered_at'])[:10]}" if r["delivered_at"] else "")
            c_period.caption(f"Period: {r['period_date'] or 'N/A'}")

            served = False
            if r.get("file_path"):
                for dl in [Path(r["file_path"]).with_suffix(".pdf"), Path(r["file_path"])]:
                    if dl.exists():
                        with open(dl, "rb") as f:
                            data = f.read()
                        mime = "application/pdf" if dl.suffix == ".pdf" else (
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                        )
                        c_action.download_button(
                            f"Download {dl.suffix.upper()[1:]}", data=data, file_name=dl.name,
                            mime=mime, key=f"cdl_{r['report_id']}_{dl.suffix}",
                        )
                        served = True
                        break
            if not served:
                c_action.caption("Preparing file…")
