"""
dashboard/client/client_account.py
CPOI Platform -- Client portal account tab.

Lets a signed-in client change their own password. Also exposes
render_forced_change(), shown by the router on first login when the client
still holds a Managing-Partner-issued temporary password.
"""

from __future__ import annotations

import streamlit as st

from dashboard import auth
from dashboard.theme import top_bar, page_title, inject_brand_css, logo_lockup_html


def _password_form(form_key: str, require_current: bool) -> None:
    user_id = st.session_state.get("auth_user_id")
    with st.form(form_key):
        current = None
        if require_current:
            current = st.text_input("Current password", type="password")
        new1 = st.text_input("New password", type="password")
        new2 = st.text_input("Confirm new password", type="password")
        submitted = st.form_submit_button("Update password", type="primary")

    if submitted:
        if require_current:
            if not auth.verify_password_for_user(user_id, current or ""):
                st.error("Current password is incorrect.")
                return
        if len(new1) < 8:
            st.error("New password must be at least 8 characters.")
            return
        if new1 != new2:
            st.error("Passwords do not match.")
            return
        auth.change_own_password(user_id, new1)
        st.session_state["auth_must_change"] = False
        st.success("Your password has been updated.")
        if st.session_state.get("_forced_change"):
            st.session_state.pop("_forced_change", None)
            st.rerun()


def _mfa_section() -> None:
    from dashboard.qr import qr_svg

    user_id = st.session_state.get("auth_user_id")
    email = ""
    from dashboard.db_helper import run_query
    rows = run_query("SELECT email FROM client_users WHERE user_id = ?", (user_id,))
    if rows:
        email = rows[0]["email"]

    st.markdown("#### Two-factor authentication")
    if auth.totp_enabled_for(user_id):
        st.success("Multi-factor authentication is enabled on your account.")
        with st.expander("Turn off two-factor authentication"):
            st.caption(
                "If your engagement requires MFA you will be asked to set it up again at next sign in."
            )
            confirm = st.text_input("Type DISABLE to confirm", key="mfa_disable_confirm")
            if st.button("Disable MFA", key="mfa_disable_btn"):
                if confirm.strip().upper() == "DISABLE":
                    auth.disable_totp(user_id)
                    st.success("Two-factor authentication disabled.")
                    st.rerun()
                else:
                    st.error("Type DISABLE to confirm.")
        return

    st.caption("Add a second layer of security using an authenticator app.")
    if not st.session_state.get("acct_enroll_started"):
        if st.button("Set up two-factor authentication", type="primary", key="acct_enroll_start"):
            st.session_state["acct_enroll_started"] = True
            st.rerun()
        return

    secret = auth.provision_totp_secret(user_id)
    uri = auth.totp_provisioning_uri(email, secret)
    svg = qr_svg(uri)
    if svg:
        st.html(svg)
    st.caption("Can't scan? Enter this key manually:")
    st.code(secret, language=None)
    code = st.text_input("Enter the 6-digit code from your app", key="acct_enroll_code")
    c1, c2 = st.columns(2)
    if c1.button("Enable", type="primary", key="acct_enroll_enable"):
        if auth.enable_totp(user_id, code):
            st.session_state.pop("acct_enroll_started", None)
            st.success("Two-factor authentication is now enabled.")
            st.rerun()
        else:
            st.error("Incorrect code. Use the latest code from your app.")
    if c2.button("Cancel", key="acct_enroll_cancel"):
        st.session_state.pop("acct_enroll_started", None)
        st.rerun()


def render() -> None:
    top_bar(tagline="Secure Client Portal")
    page_title("Account", "Manage your sign-in credentials")
    st.markdown("#### Change your password")
    _password_form("change_pw", require_current=True)
    st.divider()
    _mfa_section()


def render_forced_change() -> None:
    """Full-screen forced password change on first login (no sidebar)."""
    inject_brand_css()
    st.html("<style>[data-testid='stSidebar']{display:none;}</style>")
    st.html(f'<div class="cp-topbar" style="border:none;">{logo_lockup_html()}</div>')
    st.html(
        '<div class="cp-hero"><h1>Set your password</h1>'
        '<p>For your security, please choose a new password before continuing to your portal.</p></div>'
    )
    st.write("")
    st.session_state["_forced_change"] = True
    left, _ = st.columns([1.1, 1])
    with left:
        _password_form("forced_change_pw", require_current=False)
        if st.button("Sign out"):
            auth.logout()
            st.rerun()
