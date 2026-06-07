"""
dashboard/login.py
CPOI Platform -- Branded login screen for both portals.

Renders the Criterion Partners hero, then two tabs:
  Managing Partner  -- password (CPOI_DASHBOARD_PASSWORD)
  Client            -- email + password, plus a Forgot-password flow

On success the relevant auth.login_* call sets session state and we rerun.
"""

from __future__ import annotations

import streamlit as st

from dashboard import auth
from dashboard.theme import logo_lockup_html


def _hero() -> None:
    st.html(
        f"""
        <div class="cp-topbar" style="border:none;margin-bottom:8px;">{logo_lockup_html()}</div>
        <div class="cp-hero">
          <h1>Operational Executive Intelligence</h1>
          <p>The operational intelligence layer that exposes hidden execution risk
             before it becomes financially material.</p>
        </div>
        """
    )


def _mp_tab() -> None:
    st.subheader("Managing Partner sign in")
    pw = st.text_input("Password", type="password", key="mp_pw")
    if st.button("Sign in", type="primary", key="mp_signin"):
        try:
            if auth.login_mp(pw):
                st.rerun()
            else:
                st.error("Incorrect password.")
        except RuntimeError as exc:
            st.error(str(exc))

    with st.expander("Forgot your password?"):
        _mp_forgot_flow()


def _mp_forgot_flow() -> None:
    """Emailed-code reset for the Managing Partner password."""
    stage = st.session_state.get("mp_reset_stage", "request")
    if stage == "request":
        st.caption(
            "We will email a reset code to the Managing Partner address on file "
            "(intelligence@criterion-partners.com)."
        )
        if st.button("Send reset code", key="mp_reset_send"):
            try:
                auth.start_mp_password_reset()
            except Exception:
                st.error("Could not send the reset email. Check SendGrid configuration on the server.")
                return
            st.session_state["mp_reset_stage"] = "verify"
            st.rerun()
    else:
        st.caption("Enter the 6-digit code from the email and choose a new password.")
        code = st.text_input("Reset code", key="mp_reset_code")
        new1 = st.text_input("New password", type="password", key="mp_reset_new1")
        new2 = st.text_input("Confirm new password", type="password", key="mp_reset_new2")
        c1, c2 = st.columns(2)
        if c1.button("Set new password", type="primary", key="mp_reset_apply"):
            if new1 != new2:
                st.error("Passwords do not match.")
            else:
                ok, err = auth.complete_mp_password_reset(code, new1)
                if ok:
                    st.success("Password updated. You can now sign in.")
                    st.session_state.pop("mp_reset_stage", None)
                else:
                    st.error(err or "Reset failed.")
        if c2.button("Start over", key="mp_reset_restart"):
            st.session_state.pop("mp_reset_stage", None)
            st.rerun()


def _client_tab() -> None:
    st.subheader("Client sign in")
    stage = st.session_state.get("cl_stage", "credentials")

    if stage == "totp_verify":
        _totp_verify_stage()
        return
    if stage == "totp_enroll":
        _totp_enroll_stage()
        return

    # Stage 1: email + password (first factor)
    email = st.text_input("Email", key="cl_email")
    pw = st.text_input("Password", type="password", key="cl_pw")
    if st.button("Sign in", type="primary", key="cl_signin"):
        data, err = auth.verify_client_credentials(email, pw)
        if not data:
            st.error(err or "Sign in failed.")
            return
        if auth.mfa_required(data):
            st.session_state["mfa_user_id"] = data["user_id"]
            st.session_state["mfa_email"] = data["email"]
            st.session_state["cl_stage"] = "totp_verify" if data["totp_enabled"] else "totp_enroll"
            st.rerun()
        else:
            auth.complete_client_login(data["user_id"])
            st.rerun()

    with st.expander("Forgot your password?"):
        _forgot_flow()


def _reset_client_stage() -> None:
    for k in ("cl_stage", "mfa_user_id", "mfa_email"):
        st.session_state.pop(k, None)


def _totp_verify_stage() -> None:
    st.subheader("Two-factor authentication")
    st.caption("Enter the 6-digit code from your authenticator app.")
    code = st.text_input("Authentication code", key="totp_code")
    c1, c2 = st.columns(2)
    if c1.button("Verify", type="primary", key="totp_verify_btn"):
        user_id = st.session_state.get("mfa_user_id")
        if user_id and auth.verify_totp(user_id, code):
            auth.complete_client_login(user_id)
            _reset_client_stage()
            st.rerun()
        else:
            st.error("Incorrect or expired code.")
    if c2.button("Cancel", key="totp_cancel"):
        _reset_client_stage()
        st.rerun()


def _totp_enroll_stage() -> None:
    from dashboard.qr import qr_svg

    st.subheader("Set up two-factor authentication")
    st.caption(
        "Your engagement requires multi-factor authentication. Scan the code below with "
        "Google Authenticator, Authy, or any TOTP app, then enter the 6-digit code to finish."
    )
    user_id = st.session_state.get("mfa_user_id")
    email = st.session_state.get("mfa_email", "")
    if not user_id:
        _reset_client_stage()
        st.rerun()
        return

    secret = auth.provision_totp_secret(user_id)
    uri = auth.totp_provisioning_uri(email, secret)

    svg = qr_svg(uri)
    if svg:
        st.html(svg)
    st.caption("Can't scan? Enter this key manually:")
    st.code(secret, language=None)

    code = st.text_input("Enter the 6-digit code from your app", key="enroll_code")
    c1, c2 = st.columns(2)
    if c1.button("Enable and sign in", type="primary", key="enroll_btn"):
        if auth.enable_totp(user_id, code):
            auth.complete_client_login(user_id)
            _reset_client_stage()
            st.rerun()
        else:
            st.error("Incorrect code. Try the latest code from your app.")
    if c2.button("Cancel", key="enroll_cancel"):
        _reset_client_stage()
        st.rerun()


def _forgot_flow() -> None:
    """Two-step emailed-code reset, kept inside the login screen."""
    stage = st.session_state.get("reset_stage", "request")

    if stage == "request":
        st.caption("Enter your account email and we will send you a reset code.")
        r_email = st.text_input("Account email", key="reset_email")
        if st.button("Send reset code", key="reset_send"):
            try:
                auth.start_password_reset(r_email)
            except ValueError as exc:
                st.error(str(exc))
                return
            except Exception:
                st.error("Could not send the reset email. Contact Criterion Partners.")
                return
            # Neutral message regardless of whether the email exists.
            st.session_state["reset_stage"] = "verify"
            st.session_state["reset_email_value"] = r_email
            st.rerun()
    else:
        st.caption(
            "If that email is registered, a 6-digit code is on its way. "
            "Enter it below with your new password."
        )
        code = st.text_input("Reset code", key="reset_code")
        new1 = st.text_input("New password", type="password", key="reset_new1")
        new2 = st.text_input("Confirm new password", type="password", key="reset_new2")
        col_a, col_b = st.columns(2)
        if col_a.button("Set new password", type="primary", key="reset_apply"):
            if len(new1) < 8:
                st.error("Password must be at least 8 characters.")
            elif new1 != new2:
                st.error("Passwords do not match.")
            else:
                ok, err = auth.complete_password_reset(
                    st.session_state.get("reset_email_value", ""), code, new1
                )
                if ok:
                    st.success("Password updated. You can now sign in.")
                    for k in ("reset_stage", "reset_email_value"):
                        st.session_state.pop(k, None)
                else:
                    st.error(err or "Reset failed.")
        if col_b.button("Start over", key="reset_reset"):
            for k in ("reset_stage", "reset_email_value"):
                st.session_state.pop(k, None)
            st.rerun()


def render_login() -> None:
    """Render the full login screen. Hides the sidebar until authenticated."""
    st.html("<style>[data-testid='stSidebar']{display:none;}</style>")
    _hero()
    st.write("")
    left, _ = st.columns([1.1, 1])
    with left:
        mp_tab, client_tab = st.tabs(["Managing Partner", "Client"])
        with mp_tab:
            _mp_tab()
        with client_tab:
            _client_tab()
