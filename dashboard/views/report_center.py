"""
dashboard/views/report_center.py
CPOI Platform -- Report Center (Managing Partner).

List, download, and generate reports, and -- critically -- PUBLISH a report
to a client. Clients only ever see reports that have been published here
(reports.delivered = 1). Publishing is the explicit gate between internal
drafting and client visibility.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from dashboard.db_helper import get_connection, run_query
from dashboard.theme import top_bar, page_title
from db.audit import write_audit_log


def _publish(report_id: str, client_id: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE reports SET delivered = 1, delivered_at = ? WHERE report_id = ?",
            (datetime.now(timezone.utc).isoformat(), report_id),
        )
        write_audit_log(
            conn, event_type="report_published", entity_type="report", entity_id=report_id,
            description="Report published to the client portal.",
            performed_by="managing_partner", metadata={"client_id": client_id},
        )
        conn.commit()
    finally:
        conn.close()


def _unpublish(report_id: str, client_id: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE reports SET delivered = 0, delivered_at = NULL WHERE report_id = ?",
            (report_id,),
        )
        write_audit_log(
            conn, event_type="report_unpublished", entity_type="report", entity_id=report_id,
            description="Report withdrawn from the client portal.",
            performed_by="managing_partner", metadata={"client_id": client_id},
        )
        conn.commit()
    finally:
        conn.close()


def render() -> None:
    top_bar()
    page_title("Report Center", "Generate, review, publish, and deliver reports")

    clients = run_query(
        "SELECT client_id, client_name FROM clients WHERE status = 'active' ORDER BY client_name"
    )
    filter_options = {"All clients": None}
    for c in clients:
        filter_options[c["client_name"]] = c["client_id"]

    c1, c2 = st.columns([3, 2])
    client_filter = c1.selectbox("Filter by client", list(filter_options.keys()))
    pub_filter = c2.selectbox("Filter by status", ["All", "Published", "Not published"])
    selected_client_id = filter_options[client_filter]

    sql = """
        SELECT r.report_id, r.client_id, c.client_name, r.report_type, r.period_date,
               r.generated_at, r.file_path, r.ai_narrative_generated, r.delivered
        FROM reports r JOIN clients c ON c.client_id = r.client_id WHERE 1=1
    """
    params: list = []
    if selected_client_id:
        sql += " AND r.client_id = ?"
        params.append(selected_client_id)
    if pub_filter == "Published":
        sql += " AND r.delivered = 1"
    elif pub_filter == "Not published":
        sql += " AND r.delivered = 0"
    sql += " ORDER BY r.generated_at DESC"

    reports = run_query(sql, tuple(params))
    st.divider()
    st.markdown("#### Reports")

    if not reports:
        st.info("No reports match the current filters.")
    else:
        for r in reports:
            with st.container(border=True):
                cid_, cname, ctype, cperiod, cstatus, caction = st.columns([2, 2, 1.4, 1.4, 1.4, 2])
                cid_.markdown(f"**{r['report_id']}**")
                cname.markdown(r["client_name"])
                ctype.caption(r["report_type"])
                cperiod.caption(r["period_date"] or "")

                if r["delivered"]:
                    cstatus.markdown(
                        '<span class="cp-badge" style="background:#E8F5E9;color:#2E862E;">Published</span>',
                        unsafe_allow_html=True,
                    )
                elif r["file_path"]:
                    cstatus.markdown(
                        '<span class="cp-badge" style="background:#E3F2FD;color:#2E4A6A;">Finalized</span>',
                        unsafe_allow_html=True,
                    )
                else:
                    cstatus.markdown(
                        '<span class="cp-badge" style="background:#FFFDE7;color:#856A00;">Draft</span>',
                        unsafe_allow_html=True,
                    )

                file_path = r.get("file_path")
                if file_path:
                    for dl in [Path(file_path).with_suffix(".pdf"), Path(file_path)]:
                        if dl.exists():
                            with open(dl, "rb") as f:
                                data = f.read()
                            mime = "application/pdf" if dl.suffix == ".pdf" else (
                                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                            )
                            caction.download_button(
                                f"Download {dl.suffix.upper()[1:]}", data=data, file_name=dl.name,
                                mime=mime, key=f"dl_{r['report_id']}_{dl.suffix}",
                            )
                            break

                    if r["delivered"]:
                        if caction.button("Unpublish", key=f"unpub_{r['report_id']}"):
                            _unpublish(r["report_id"], r["client_id"])
                            st.rerun()
                    else:
                        if caction.button("Publish to client", key=f"pub_{r['report_id']}", type="primary"):
                            _publish(r["report_id"], r["client_id"])
                            st.success("Published to the client portal.")
                            st.rerun()
                else:
                    caction.caption("Finalize before publishing")

    st.divider()
    st.markdown("#### Generate a new report")
    with st.expander("Generate report for a client"):
        if not clients:
            st.info("No active clients available.")
            return
        gen_options = {c["client_name"]: c["client_id"] for c in clients}
        gen_client = st.selectbox("Client", list(gen_options.keys()), key="gen_client_rc")
        gen_cid = gen_options[gen_client]
        rtype = st.selectbox("Report type", ["snapshot", "monthly_brief"], key="gen_type_rc")

        latest = run_query(
            """
            SELECT s.submission_id, s.reporting_period_end
            FROM intake_submissions s JOIN oei_scores o ON o.submission_id = s.submission_id
            WHERE s.client_id = ? ORDER BY o.period_date DESC LIMIT 1
            """,
            (gen_cid,),
        )
        if not latest:
            st.warning("No scored submission for this client yet.")
            return
        sub = latest[0]
        st.caption(f"Latest scored submission: {sub['submission_id']} (period end {sub['reporting_period_end']})")
        if st.button("Generate report", key="gen_btn_rc", type="primary"):
            conn = get_connection()
            try:
                from reports.generator import generate_report_for_submission
                result = generate_report_for_submission(conn=conn, submission_id=sub["submission_id"], report_type=rtype)
                conn.commit()
                st.success(f"Report created: {result.report_id} ({result.section_count} sections to review).")
                st.rerun()
            except Exception as exc:
                st.error(f"Report generation failed: {exc}")
            finally:
                conn.close()
