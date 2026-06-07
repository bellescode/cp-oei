"""
dashboard/app.py
CPOI Platform -- Criterion Partners portal entry point and role router.

One Streamlit app serves two completely separate experiences:

  Managing Partner portal  (role 'mp')   -- full operational intelligence suite
  Client portal            (role 'client') -- a single client's own results only

The login screen authenticates and sets a role in session state. st.navigation
then builds a different sidebar per role, so a client never sees -- and cannot
route to -- Managing Partner pages, and vice versa.

Launch:
    streamlit run dashboard/app.py

Required environment variables:
    CPOI_DB_KEY              database encryption key
    CPOI_DASHBOARD_PASSWORD  Managing Partner password
    SENDGRID_API_KEY         (for client invites / password resets)
    CPOI_PORTAL_URL          (optional) public portal URL used in invite emails
"""

import sys
from pathlib import Path

import streamlit as st

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dashboard import auth
from dashboard import nav
from dashboard.theme import inject_brand_css
from dashboard.login import render_login
from dashboard.views import (
    mp_home, client_management, client_overview, client_profile,
    data_intake, report_center, engagement_journal, audit_log, mp_settings,
    mp_messages,
)
from dashboard import messaging
from dashboard.client import (
    client_home, client_submit, client_reports, client_messages, client_account,
)

st.set_page_config(
    page_title="Criterion Partners | OEI Platform",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_brand_css()

role = auth.current_role()

# ---------------------------------------------------------------------------
# Unauthenticated -> login screen
# ---------------------------------------------------------------------------
if role is None:
    render_login()
    st.stop()

# ---------------------------------------------------------------------------
# Client must set their own password before anything else
# ---------------------------------------------------------------------------
if role == "client" and st.session_state.get("auth_must_change"):
    client_account.render_forced_change()
    st.stop()

# ---------------------------------------------------------------------------
# Sidebar identity + logout (shared)
# ---------------------------------------------------------------------------
with st.sidebar:
    if role == "mp":
        st.markdown("### Managing Partner")
        st.caption("Criterion Partners")
    else:
        st.markdown(f"### {st.session_state.get('auth_client_name', 'Client')}")
        st.caption("Secure Client Portal")
    st.divider()
    if st.button("Sign out", use_container_width=True):
        auth.logout()
        st.rerun()

# ---------------------------------------------------------------------------
# Role-based navigation
# ---------------------------------------------------------------------------
if role == "mp":
    try:
        mp_unread = messaging.total_unread_mp()
    except Exception:
        mp_unread = 0
    mp_msg_title = f"Messages  \U0001F534 {mp_unread}" if mp_unread else "Messages"
    keyed = {
        "home":       st.Page(mp_home.render,            title="Overview",           url_path="overview", default=True),
        "overview":   st.Page(client_overview.render,    title="Client Portfolio",   url_path="portfolio"),
        "profile":    st.Page(client_profile.render,     title="Client Profile",     url_path="client-profile"),
        "management": st.Page(client_management.render,  title="Client Management",  url_path="client-management"),
        "intake":     st.Page(data_intake.render,        title="Data Intake",        url_path="data-intake"),
        "reports":    st.Page(report_center.render,      title="Report Center",      url_path="report-center"),
        "messages":   st.Page(mp_messages.render,        title=mp_msg_title,         url_path="mp-messages"),
        "journal":    st.Page(engagement_journal.render, title="Engagement Journal", url_path="engagement-journal"),
        "audit":      st.Page(audit_log.render,          title="Audit Log",          url_path="audit-log"),
        "settings":   st.Page(mp_settings.render,        title="Settings",           url_path="settings"),
    }
else:
    try:
        _cid = st.session_state.get("auth_client_id")
        cl_unread = messaging.unread_count(_cid, "client") if _cid else 0
    except Exception:
        cl_unread = 0
    cl_msg_title = f"Messages  \U0001F534 {cl_unread}" if cl_unread else "Messages"
    keyed = {
        "home":     st.Page(client_home.render,     title="Dashboard",   url_path="dashboard", default=True),
        "submit":   st.Page(client_submit.render,   title="Submit Data", url_path="submit-data"),
        "reports":  st.Page(client_reports.render,  title="Reports",     url_path="my-reports"),
        "messages": st.Page(client_messages.render, title=cl_msg_title,  url_path="messages"),
        "account":  st.Page(client_account.render,  title="Account",     url_path="account"),
    }

nav.register(keyed)
st.navigation(list(keyed.values())).run()
