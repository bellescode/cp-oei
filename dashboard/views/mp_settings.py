"""
dashboard/views/mp_settings.py
CPOI Platform -- Managing Partner settings.

Lets the Managing Partner change their password while signed in. Forgotten
passwords are reset from the login screen via an emailed code. The
CPOI_DASHBOARD_PASSWORD environment variable remains a server-side
break-glass master password.
"""

from __future__ import annotations

import streamlit as st

from dashboard import auth
from dashboard.theme import top_bar, page_title


def render() -> None:
    top_bar()
    page_title("Settings", "Manage your Managing Partner credentials")

    st.markdown("#### Change your password")
    with st.form("mp_change_pw"):
        current = st.text_input("Current password", type="password")
        new1 = st.text_input("New password", type="password")
        new2 = st.text_input("Confirm new password", type="password")
        submitted = st.form_submit_button("Update password", type="primary")

    if submitted:
        if new1 != new2:
            st.error("New passwords do not match.")
        else:
            ok, err = auth.mp_change_password(current, new1)
            if ok:
                st.success("Your password has been updated.")
            else:
                st.error(err or "Could not update password.")

    st.divider()
    st.caption(
        "If you ever forget your password, use 'Forgot your password?' on the sign-in screen "
        "to receive a reset code by email. The server's CPOI_DASHBOARD_PASSWORD also remains a "
        "master password for recovery."
    )
