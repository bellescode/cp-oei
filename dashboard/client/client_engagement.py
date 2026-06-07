"""
dashboard/client/client_engagement.py
CPOI Platform -- client portal Engagement tab.

Shows the client their engagement progress (milestone tracker) and a
read-only view of their invoice/billing status. Scoped to the authenticated
client.
"""

from __future__ import annotations

import streamlit as st

from dashboard import billing, milestones
from dashboard.access import access_state
from dashboard.db_helper import run_query
from dashboard.theme import top_bar, page_title, NAVY, GOLD, MUTED

_STATUS_STYLE = {
    "paid":    ("#E8F5E9", "#2E862E", "Paid"),
    "due":     ("#FFFDE7", "#856A00", "Due"),
    "overdue": ("#FCE4E4", "#B52A1C", "Overdue"),
}


def _tracker(client: dict) -> None:
    st.markdown("#### Engagement Progress")
    steps = milestones.get_tracker(client)
    rows_html = ""
    for s in steps:
        if s["status"] == "complete":
            icon, color, weight = "&#10003;", "#2E862E", "500"   # check
        elif s["status"] == "active":
            icon, color, weight = "&#9679;", GOLD, "700"          # filled dot
        else:
            icon, color, weight = "&#9675;", MUTED, "400"         # open circle
        rows_html += (
            f'<div style="display:flex;align-items:center;gap:12px;padding:8px 0;'
            f'border-bottom:1px solid #F0ECE2;">'
            f'<span style="color:{color};font-size:18px;width:20px;text-align:center;">{icon}</span>'
            f'<span style="color:{NAVY};font-weight:{weight};">{s["label"]}</span></div>'
        )
    st.html(f'<div class="cp-card">{rows_html}</div>')


def _billing(client: dict) -> None:
    st.markdown("#### Billing")
    invoices = billing.list_invoices(client["client_id"])
    if not invoices:
        st.caption("No invoices on file yet.")
        return
    for inv in invoices:
        bg, fg, label = _STATUS_STYLE.get(inv["status"], ("#EEE", "#333", inv["status"]))
        amount = f"${inv['amount']:,.0f}" if inv.get("amount") is not None else ""
        due = f" &nbsp;·&nbsp; due {inv['due_date']}" if inv.get("due_date") else ""
        st.html(
            f'<div class="cp-card" style="display:flex;justify-content:space-between;'
            f'align-items:center;">'
            f'<div><strong>{inv["label"]}</strong><span style="color:{MUTED};">{due}</span></div>'
            f'<div style="display:flex;align-items:center;gap:12px;">'
            f'<span style="color:{NAVY};font-weight:600;">{amount}</span>'
            f'<span class="cp-badge" style="background:{bg};color:{fg};">{label}</span></div></div>'
        )


def render() -> None:
    cid = st.session_state.get("auth_client_id")
    top_bar(tagline="Secure Client Portal")
    page_title("Engagement", "Your engagement progress and billing status")

    rows = run_query("SELECT * FROM clients WHERE client_id = ?", (cid,))
    if not rows:
        st.info("Engagement details are not available yet.")
        return
    client = rows[0]

    state = access_state(client)
    if state["expires"] and state["state"] == "active":
        st.caption(f"Portal access available through {state['expires']}.")

    _tracker(client)
    st.write("")
    _billing(client)
