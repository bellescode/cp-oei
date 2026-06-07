"""
dashboard/client/client_messages.py
CPOI Platform -- client portal secure messages tab.

A private, encrypted thread between the client's executive sponsor and the
Managing Partner. Scoped strictly to the authenticated client's own thread.
"""

from __future__ import annotations

import streamlit as st

from dashboard.message_ui import render_thread
from dashboard.theme import top_bar, page_title


def render() -> None:
    cid = st.session_state.get("auth_client_id")
    label = st.session_state.get("auth_client_name", "Client")
    top_bar(tagline="Secure Client Portal")
    page_title("Messages", "A private, encrypted channel with Criterion Partners")
    render_thread(cid, viewer_role="client", viewer_label=label, key_prefix="client_msg")
