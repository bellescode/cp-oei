"""
dashboard/views/client_management.py
CPOI Platform -- Client Management (Managing Partner).

Add and maintain client engagements, including:
  - Engagement lifecycle (type, start, end, renewal status)
  - Contacts (primary contact, executive sponsor, alert recipient)
  - Status (active / inactive / closed)
  - Portal access: create a login (email + temporary password, optional emailed
    invite), reset the password, and activate/deactivate the account.

The Managing Partner sets the client's email and an initial password here.
The client is then required to choose their own password on first sign in.
"""

from __future__ import annotations

import uuid

import streamlit as st

from dashboard import auth
from dashboard.db_helper import run_query, run_write
from dashboard.email_helper import email_configured, send_client_invite
from dashboard.theme import top_bar, page_title, badge

import os

_RENEWAL_OPTIONS = ["unknown", "renewing", "not_renewing"]
_STATUS_OPTIONS = ["active", "inactive", "closed"]


def _add_client_form() -> None:
    st.markdown("#### Add a new client")
    with st.form("add_client", clear_on_submit=True):
        c1, c2, c3 = st.columns([3, 2, 2])
        name = c1.text_input("Client name *")
        etype = c2.selectbox("Engagement type *", ["snapshot", "oeil"])
        retainer = c3.number_input("Monthly retainer ($)", min_value=0.0, step=500.0, value=0.0)

        c4, c5, c6 = st.columns(3)
        start = c4.date_input("Engagement start")
        end = c5.date_input("Engagement end", value=None)
        renewal = c6.selectbox("Renewal status", _RENEWAL_OPTIONS)

        st.markdown("**Contacts**")
        c7, c8 = st.columns(2)
        contact_name = c7.text_input("Primary contact name")
        contact_title = c8.text_input("Primary contact title")
        c9, c10 = st.columns(2)
        contact_email = c9.text_input("Primary contact email")
        alert_email = c10.text_input("Alert recipient email")
        sponsor = st.text_input("Executive sponsor")

        if st.form_submit_button("Add client", type="primary"):
            if not name.strip():
                st.error("Client name is required.")
                return
            new_id = str(uuid.uuid4())
            run_write(
                """
                INSERT INTO clients
                    (client_id, client_name, engagement_type, engagement_start_date,
                     engagement_end_date, monthly_retainer, renewal_status,
                     primary_contact_name, primary_contact_title, primary_contact_email,
                     executive_sponsor, alert_recipient_email, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
                """,
                (
                    new_id, name.strip(), etype, start.isoformat(),
                    end.isoformat() if end else None,
                    retainer if retainer > 0 else None, renewal,
                    contact_name.strip() or None, contact_title.strip() or None,
                    contact_email.strip() or None, sponsor.strip() or None,
                    alert_email.strip() or None,
                ),
            )
            st.success(f"Client '{name.strip()}' added. Create their portal login below.")


def _edit_engagement(client: dict) -> None:
    cid = client["client_id"]
    with st.form(f"edit_{cid}"):
        c1, c2, c3 = st.columns(3)
        end_val = None
        if client.get("engagement_end_date"):
            try:
                from datetime import date
                end_val = date.fromisoformat(str(client["engagement_end_date"]))
            except ValueError:
                end_val = None
        new_end = c1.date_input("Engagement end", value=end_val, key=f"end_{cid}")
        cur_renew = client.get("renewal_status") or "unknown"
        new_renewal = c2.selectbox(
            "Renewal status", _RENEWAL_OPTIONS,
            index=_RENEWAL_OPTIONS.index(cur_renew) if cur_renew in _RENEWAL_OPTIONS else 0,
            key=f"renew_{cid}",
        )
        cur_status = client.get("status") or "active"
        new_status = c3.selectbox(
            "Engagement status", _STATUS_OPTIONS,
            index=_STATUS_OPTIONS.index(cur_status) if cur_status in _STATUS_OPTIONS else 0,
            key=f"status_{cid}",
        )
        c4, c5 = st.columns(2)
        new_contact = c4.text_input("Primary contact name", value=client.get("primary_contact_name") or "", key=f"cn_{cid}")
        new_contact_email = c5.text_input("Primary contact email", value=client.get("primary_contact_email") or "", key=f"ce_{cid}")
        new_alert_email = st.text_input("Alert recipient email", value=client.get("alert_recipient_email") or "", key=f"ae_{cid}")

        if st.form_submit_button("Save engagement details", type="primary"):
            run_write(
                """
                UPDATE clients
                SET engagement_end_date = ?, renewal_status = ?, status = ?,
                    primary_contact_name = ?, primary_contact_email = ?, alert_recipient_email = ?
                WHERE client_id = ?
                """,
                (
                    new_end.isoformat() if new_end else None, new_renewal, new_status,
                    new_contact.strip() or None, new_contact_email.strip() or None,
                    new_alert_email.strip() or None, cid,
                ),
            )
            st.success("Engagement details updated.")
            st.rerun()


def _portal_account(client: dict) -> None:
    cid = client["client_id"]
    account = auth.client_user_for(cid)
    st.markdown("**Portal access**")

    if account is None:
        default_email = client.get("primary_contact_email") or ""
        col1, col2 = st.columns(2)
        email = col1.text_input("Login email", value=default_email, key=f"login_email_{cid}")
        gen = col2.checkbox("Auto-generate temporary password", value=True, key=f"gen_{cid}")
        temp_pw = ""
        if not gen:
            temp_pw = col2.text_input("Temporary password", type="password", key=f"temp_pw_{cid}")
        send_invite = st.checkbox(
            "Email the client an invite with their credentials",
            value=email_configured(), key=f"invite_{cid}",
        )
        if st.button("Create portal login", key=f"create_login_{cid}", type="primary"):
            if not email.strip():
                st.error("A login email is required.")
                return
            password = auth.generate_temp_password() if gen else temp_pw
            if not gen and len(password) < 8:
                st.error("Temporary password must be at least 8 characters.")
                return
            try:
                auth.create_client_user(cid, email, password)
            except ValueError as exc:
                st.error(str(exc))
                return
            st.success(f"Portal login created for {email.strip().lower()}.")
            if gen:
                st.info(f"Temporary password: **{password}** (share securely).")
            if send_invite:
                portal_url = os.environ.get("CPOI_PORTAL_URL", "your Criterion Partners portal")
                try:
                    send_client_invite(email.strip().lower(), client["client_name"], portal_url, password)
                    st.success("Invite email sent.")
                except Exception:
                    st.warning(
                        "Login created, but the invite email could not be sent "
                        "(check the SendGrid configuration). Share the credentials above manually."
                    )
            st.rerun()
    else:
        status = "Active" if account["is_active"] else "Inactive"
        pending = " — must set password on next login" if account["must_change_password"] else ""
        st.caption(f"{account['email']} | {status}{pending} | last login: {account['last_login'] or 'never'}")
        col1, col2 = st.columns(2)
        if col1.button("Reset password", key=f"reset_{cid}"):
            new_pw = auth.generate_temp_password()
            auth.admin_reset_password(account["user_id"], new_pw)
            st.info(f"New temporary password: **{new_pw}** (share securely). Client must change it on next login.")
        toggle_label = "Deactivate account" if account["is_active"] else "Reactivate account"
        if col2.button(toggle_label, key=f"toggle_{cid}"):
            auth.set_client_active(account["user_id"], not account["is_active"])
            st.rerun()


def render() -> None:
    top_bar()
    page_title("Client Management", "Engagements, contacts, and portal access")

    _add_client_form()
    st.divider()

    st.markdown("#### All clients")
    clients = run_query("SELECT * FROM clients ORDER BY client_name")
    if not clients:
        st.info("No clients yet. Add one above.")
        return

    focus = st.session_state.pop("manage_client_id", None)
    for client in clients:
        cid = client["client_id"]
        status = client.get("status") or "active"
        label = f"{client['client_name']}  ·  {(client.get('engagement_type') or '').upper()}  ·  {status}"
        with st.expander(label, expanded=(cid == focus)):
            st.html(badge(_RENEWAL_LABEL(client)))
            _edit_engagement(client)
            st.divider()
            _portal_account(client)


def _RENEWAL_LABEL(client: dict) -> str:
    mapping = {"renewing": "Renewing", "not_renewing": "Not renewing", "unknown": "Renewal TBC"}
    return mapping.get(client.get("renewal_status") or "unknown", "Renewal TBC")
