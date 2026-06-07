"""
dashboard/views/client_profile.py
CPOI Platform -- Client Profile (Managing Partner drill-through).

Everything about one client in a single branded page:
  - Engagement header: type, start/end, days remaining, renewal status,
    contacts, and portal-account status
  - OEI scorecard (5 dimensions + composite) with movement vs prior period
  - Score trajectory
  - Current-period signal readings
  - Active alerts with acknowledge
  - Pre-finalization anomaly checklist
  - Recent engagement-journal entries for this client
  - Quick actions: generate a report

Reads st.session_state['profile_client_id'] when arriving via the portfolio
drill-through; otherwise offers a selector.
"""

from __future__ import annotations

import base64
from datetime import date, datetime, timezone

import pandas as pd
import streamlit as st

from dashboard import auth, nav, milestones, billing
from dashboard.db_helper import get_connection, run_query
from dashboard.theme import top_bar, page_title, badge, BAND_BG, NAVY, GOLD
from db.audit import write_audit_log

_DIMS = [
    ("Strategic Saturation",      "strategic_saturation_score",      "strategic_saturation_class"),
    ("Governance Responsiveness", "governance_responsiveness_score", "governance_responsiveness_class"),
    ("Execution Visibility",      "execution_visibility_score",      "execution_visibility_class"),
    ("Reporting Integrity",       "reporting_integrity_score",       "reporting_integrity_class"),
    ("Org Sustainability",        "org_sustainability_score",        "org_sustainability_class"),
]

_SIGNAL_DISPLAY = {
    "governance_latency_index": "Governance Latency Index",
    "escalation_suppression_rate": "Escalation Suppression Rate",
    "false_green_indicator": "False Green Indicator",
    "reporting_divergence_score": "Reporting Divergence Score",
    "priority_collision_index": "Priority Collision Index",
    "platform_utilization_pressure": "Platform Utilization Pressure",
    "initiative_saturation_ratio": "Initiative Saturation Ratio",
    "dependency_fragility_score": "Dependency Fragility Score",
    "headcount_stability_index": "Headcount Stability Index",
    "reprioritization_frequency": "Reprioritization Frequency",
    "reactive_work_ratio": "Reactive Work Ratio",
}

_RENEWAL_LABEL = {
    "renewing": "Renewing",
    "not_renewing": "Not renewing",
    "unknown": "To be confirmed",
}


def _select_client() -> dict | None:
    clients = run_query(
        "SELECT client_id, client_name FROM clients WHERE status = 'active' ORDER BY client_name"
    )
    if not clients:
        st.info("No active clients. Add one in Client Management.")
        return None

    options = {c["client_name"]: c["client_id"] for c in clients}
    names = list(options.keys())
    preselect = st.session_state.get("profile_client_id")
    idx = 0
    if preselect:
        for i, c in enumerate(clients):
            if c["client_id"] == preselect:
                idx = i
                break
    chosen_name = st.selectbox("Client", names, index=idx)
    chosen_id = options[chosen_name]
    st.session_state["profile_client_id"] = chosen_id

    row = run_query("SELECT * FROM clients WHERE client_id = ?", (chosen_id,))
    return row[0] if row else None


def _engagement_header(client: dict) -> None:
    cid = client["client_id"]
    end = client.get("engagement_end_date")
    days_left = None
    if end:
        try:
            days_left = (date.fromisoformat(str(end)) - date.today()).days
        except ValueError:
            days_left = None

    renewal = _RENEWAL_LABEL.get(client.get("renewal_status") or "unknown", "To be confirmed")

    with st.container(border=True):
        a, b, c, d = st.columns(4)
        a.markdown("**Engagement**")
        a.caption((client.get("engagement_type") or "N/A").upper())
        b.markdown("**Start**")
        b.caption(str(client.get("engagement_start_date") or "N/A"))
        c.markdown("**Ends**")
        if end:
            tail = f" ({days_left} days left)" if days_left is not None else ""
            c.caption(f"{end}{tail}")
        else:
            c.caption("Not set")
        d.markdown("**Renewal**")
        d.caption(renewal)

        e, f, g = st.columns(3)
        e.markdown("**Primary contact**")
        e.caption(client.get("primary_contact_name") or "Not set")
        if client.get("primary_contact_title"):
            e.caption(client["primary_contact_title"])
        f.markdown("**Contact email**")
        f.caption(client.get("primary_contact_email") or "Not set")
        g.markdown("**Alert recipient**")
        g.caption(client.get("alert_recipient_email") or "Not set")

        account = auth.client_user_for(cid)
        st.divider()
        ac1, ac2 = st.columns([3, 1])
        if account:
            status = "Active" if account["is_active"] else "Inactive"
            pending = " (must set password)" if account["must_change_password"] else ""
            ac1.markdown(f"**Portal account:** {account['email']} — {status}{pending}")
            ac1.caption(f"Last login: {account['last_login'] or 'never'}")
        else:
            ac1.markdown("**Portal account:** none created yet")
            ac1.caption("Create one in Client Management so the sponsor can sign in.")
        if ac2.button("Manage client", key=f"manage_{cid}"):
            st.session_state["manage_client_id"] = cid
            nav.goto("management")


def _scorecard(client: dict) -> dict | None:
    cid = client["client_id"]
    rows = run_query(
        "SELECT * FROM oei_scores WHERE client_id = ? ORDER BY period_date DESC LIMIT 1", (cid,)
    )
    if not rows:
        st.warning("No OEI scores yet for this client. Ingest a submission in Data Intake.")
        return None
    current = rows[0]
    prior_rows = run_query(
        "SELECT * FROM oei_scores WHERE client_id = ? AND period_date < ? ORDER BY period_date DESC LIMIT 1",
        (cid, current["period_date"]),
    )
    prior = prior_rows[0] if prior_rows else None

    st.markdown("#### OEI Scorecard")
    big1, big2 = st.columns([1, 3])
    with big1:
        st.html(
            f'<div class="cp-card" style="text-align:center;border-left-color:{NAVY};">'
            f'<div class="cp-stat-label">Composite</div>'
            f'<div class="cp-stat">{current["oei_composite_score"]}</div>'
            f'{badge(current["composite_class"])}</div>'
        )

    table_rows = []
    for name, score_col, class_col in _DIMS:
        score = current[score_col]
        prior_score = prior[score_col] if prior else None
        delta = (score - prior_score) if prior_score is not None else None
        delta_str = (f"+{delta}" if delta and delta > 0 else (str(delta) if delta is not None else "--"))
        table_rows.append({
            "Dimension": name, "Score": score,
            "Classification": current[class_col],
            "Prior": prior_score if prior_score is not None else "--",
            "Movement": delta_str,
        })
    df = pd.DataFrame(table_rows)

    def _bg(val: str) -> str:
        c = BAND_BG.get(val, "")
        return f"background-color: {c};" if c else ""

    with big2:
        st.dataframe(
            df.style.map(_bg, subset=["Classification"]),
            use_container_width=True, hide_index=True,
        )
    return current


def _trajectory(cid: str) -> None:
    history = run_query(
        "SELECT period_date, oei_composite_score FROM oei_scores WHERE client_id = ? ORDER BY period_date ASC",
        (cid,),
    )
    if len(history) >= 2:
        st.markdown("#### Score Trajectory")
        try:
            from reports.chart import generate_trajectory_chart
            b64 = generate_trajectory_chart(history)
            if b64:
                st.image(base64.b64decode(b64), use_container_width=True)
                return
        except Exception:
            pass
        chart_df = pd.DataFrame(history).set_index("period_date")
        st.line_chart(chart_df["oei_composite_score"])


def _signals(cid: str) -> str | None:
    sub_rows = run_query(
        "SELECT submission_id FROM intake_submissions WHERE client_id = ? ORDER BY reporting_period_end DESC LIMIT 1",
        (cid,),
    )
    if not sub_rows:
        return None
    sub_id = sub_rows[0]["submission_id"]
    signals = run_query(
        """
        SELECT signal_name, signal_value, threshold_value, threshold_breached
        FROM signal_readings WHERE submission_id = ? ORDER BY signal_name
        """,
        (sub_id,),
    )
    if signals:
        st.markdown("#### Signal Readings — Current Period")
        rows = []
        for s in signals:
            breached = bool(s["threshold_breached"])
            value = float(s["signal_value"])
            threshold = float(s["threshold_value"])
            if breached:
                excess = value - threshold
                sev = "CRITICAL" if excess >= threshold * 0.5 else ("ELEVATED" if excess >= threshold * 0.2 else "WATCH")
            else:
                sev = "CLEAR"
            rows.append({
                "Signal": _SIGNAL_DISPLAY.get(s["signal_name"], s["signal_name"]),
                "Value": round(value, 4), "Threshold": round(threshold, 4),
                "Breach": "Yes" if breached else "No", "Severity": sev,
            })
        sev_bg = {"CRITICAL": "#FCE4E4", "ELEVATED": "#FFF3E0", "WATCH": "#FFFDE7", "CLEAR": "#E8F5E9"}
        sdf = pd.DataFrame(rows)
        st.dataframe(
            sdf.style.map(lambda v: f"background-color: {sev_bg.get(v,'')};", subset=["Severity"]),
            use_container_width=True, hide_index=True,
        )
    return sub_id


def _alerts(cid: str, client_name: str) -> None:
    alerts = run_query(
        """
        SELECT alert_id, signal_name, alert_severity, alert_message, triggered_at
        FROM alerts WHERE client_id = ? AND acknowledged = 0 ORDER BY triggered_at DESC
        """,
        (cid,),
    )
    st.markdown("#### Active Alerts")
    if not alerts:
        st.success("No unacknowledged alerts.")
        return
    for alert in alerts:
        with st.container(border=True):
            cs, cv, ct, ca = st.columns([2, 1, 2, 1])
            cs.markdown(f"**{_SIGNAL_DISPLAY.get(alert['signal_name'], alert['signal_name'])}**")
            cs.caption(alert["alert_message"] or "")
            cv.markdown(f"**{(alert['alert_severity'] or '').upper()}**")
            ct.caption(alert["triggered_at"])
            if ca.button("Acknowledge", key=f"ack_{alert['alert_id']}"):
                conn = get_connection()
                try:
                    conn.execute(
                        "UPDATE alerts SET acknowledged = 1, acknowledged_at = ? WHERE alert_id = ?",
                        (datetime.now(timezone.utc).isoformat(), alert["alert_id"]),
                    )
                    write_audit_log(
                        conn, event_type="alert_acknowledged", entity_type="alert",
                        entity_id=alert["alert_id"],
                        description=f"Alert acknowledged via profile for '{client_name}'.",
                        performed_by="managing_partner",
                        metadata={"client_id": cid, "signal_name": alert["signal_name"]},
                    )
                    conn.commit()
                finally:
                    conn.close()
                st.rerun()


def _anomaly_checklist(cid: str) -> None:
    latest = run_query(
        """
        SELECT s.submission_id FROM intake_submissions s
        JOIN oei_scores o ON o.submission_id = s.submission_id
        WHERE s.client_id = ? ORDER BY o.period_date DESC LIMIT 1
        """,
        (cid,),
    )
    if not latest:
        return
    st.markdown("#### Pre-Finalization Checklist")
    sub_id = latest[0]["submission_id"]
    conn = get_connection()
    try:
        from scoring.anomaly import detect_anomaly_flags
        flags = detect_anomaly_flags(conn, sub_id, cid)
    except Exception as exc:
        flags = []
        st.caption(f"Anomaly detection unavailable: {exc}")
    finally:
        conn.close()

    if not flags:
        st.success("No anomaly flags detected. Scores may be published.")
        return
    st.warning(f"{len(flags)} anomaly flag(s) require review before publication.")
    for flag in flags:
        with st.expander(f"{flag.flag_type} -- {flag.dimension or 'Portfolio-level'}", expanded=True):
            st.markdown(f"**{flag.description}**")
            cleared_key = f"flag_{flag.flag_id}_cleared"
            if st.session_state.get(cleared_key):
                st.success("Cleared this session.")
                continue
            justification = st.text_area(
                "Justification to clear (min 20 characters):", key=f"flag_{flag.flag_id}_just"
            )
            if st.button("Clear flag", key=f"flag_{flag.flag_id}_btn"):
                if len(justification.strip()) < 20:
                    st.error("Justification must be at least 20 characters.")
                else:
                    conn = get_connection()
                    try:
                        write_audit_log(
                            conn, event_type="anomaly_flag_cleared", entity_type="submission",
                            entity_id=sub_id,
                            description=f"Anomaly flag cleared: {flag.flag_type}. Justification: {justification.strip()}",
                            performed_by="managing_partner",
                            metadata={"flag_type": flag.flag_type, "client_id": cid},
                        )
                        conn.commit()
                    finally:
                        conn.close()
                    st.session_state[cleared_key] = True
                    st.rerun()


def _journal(cid: str) -> None:
    entries = run_query(
        """
        SELECT entry_date, entry_type, program_reference, materiality, intelligence_note
        FROM engagement_journal WHERE client_id = ? ORDER BY entry_date DESC LIMIT 5
        """,
        (cid,),
    )
    st.markdown("#### Recent Engagement Journal")
    if not entries:
        st.caption("No journal entries yet. Add intelligence in the Engagement Journal.")
        return
    for e in entries:
        with st.container(border=True):
            st.markdown(
                f"**{e['entry_date']}** | {e['entry_type']} | {e['program_reference']} "
                f"| Materiality: {e['materiality']}"
            )
            st.caption(e["intelligence_note"])


def _quick_actions(cid: str, sub_id: str | None) -> None:
    st.markdown("#### Quick Actions")
    with st.expander("Generate a report for the latest submission"):
        if not sub_id:
            st.info("No submission available.")
            return
        rtype = st.selectbox("Report type", ["snapshot", "monthly_brief"], key="profile_rtype")
        if st.button("Generate", key="profile_gen", type="primary"):
            conn = get_connection()
            try:
                from reports.generator import generate_report_for_submission
                result = generate_report_for_submission(conn=conn, submission_id=sub_id, report_type=rtype)
                conn.commit()
                st.success(f"Report created: {result.report_id} ({result.section_count} sections to review).")
            except Exception as exc:
                st.error(f"Report generation failed: {exc}")
            finally:
                conn.close()


def render() -> None:
    top_bar()
    page_title("Client Profile", "Full intelligence view for one engagement")

    client = _select_client()
    if not client:
        return

    _engagement_header(client)
    st.write("")
    _scorecard(client)
    _trajectory(client["client_id"])
    sub_id = _signals(client["client_id"])
    _alerts(client["client_id"], client["client_name"])
    _anomaly_checklist(client["client_id"])
    _journal(client["client_id"])
    _engagement_admin(client)
    _messages(client["client_id"])
    _quick_actions(client["client_id"], sub_id)


def _engagement_admin(client: dict) -> None:
    cid = client["client_id"]
    st.markdown("#### Engagement Milestones & Billing")

    with st.expander("Milestones (what the client sees on their Engagement tab)"):
        steps = milestones.get_tracker(client)
        with st.form(f"ms_form_{cid}"):
            choices = {}
            for s in steps:
                choices[s["key"]] = st.selectbox(
                    s["label"], milestones.STATUS_OPTIONS,
                    index=milestones.STATUS_OPTIONS.index(s["status"])
                    if s["status"] in milestones.STATUS_OPTIONS else 0,
                    key=f"ms_{cid}_{s['key']}",
                )
            if st.form_submit_button("Save milestones", type="primary"):
                for key, status in choices.items():
                    milestones.set_milestone(cid, key, status)
                st.success("Milestones updated.")
                st.rerun()

    with st.expander("Invoices / billing status"):
        invoices = billing.list_invoices(cid)
        for inv in invoices:
            c1, c2, c3, c4 = st.columns([3, 1.3, 1.6, 1])
            c1.markdown(f"**{inv['label']}**")
            c2.caption(f"${inv['amount']:,.0f}" if inv.get("amount") is not None else "")
            new_status = c3.selectbox(
                "status", billing.STATUS_OPTIONS,
                index=billing.STATUS_OPTIONS.index(inv["status"]),
                key=f"inv_{inv['invoice_id']}", label_visibility="collapsed",
            )
            if new_status != inv["status"]:
                billing.set_status(inv["invoice_id"], new_status)
                st.rerun()
            if c4.button("Delete", key=f"invdel_{inv['invoice_id']}"):
                billing.delete_invoice(inv["invoice_id"])
                st.rerun()

        st.divider()
        col_a, col_b = st.columns(2)
        with col_a:
            with st.form(f"inv_add_{cid}"):
                st.caption("Add an invoice line")
                label = st.text_input("Label", key=f"invlbl_{cid}")
                amount = st.number_input("Amount ($)", min_value=0.0, step=500.0, key=f"invamt_{cid}")
                status = st.selectbox("Status", billing.STATUS_OPTIONS, key=f"invst_{cid}")
                if st.form_submit_button("Add line"):
                    if label.strip():
                        billing.add_invoice(cid, label, amount or None, status)
                        st.rerun()
                    else:
                        st.error("Label is required.")
        with col_b:
            with st.form(f"inv_sched_{cid}"):
                st.caption("Create Snapshot 30/40/30 schedule")
                total = st.number_input("Total engagement value ($)", min_value=0.0,
                                        step=500.0, value=7500.0, key=f"invtot_{cid}")
                if st.form_submit_button("Create schedule"):
                    billing.create_snapshot_schedule(cid, total)
                    st.rerun()


def _messages(cid: str) -> None:
    from dashboard import messaging
    from dashboard.message_ui import render_thread

    unread = messaging.unread_count(cid, "managing_partner")
    header = "#### Secure Messages"
    if unread:
        header += f"  ·  {unread} unread"
    st.markdown(header)
    with st.expander("Open conversation", expanded=bool(unread)):
        render_thread(cid, viewer_role="managing_partner", viewer_label="Managing Partner",
                      key_prefix=f"mp_msg_{cid}")
