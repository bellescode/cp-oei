"""
dashboard/views/mp_messages.py
CPOI Platform -- Managing Partner secure-message inbox.

A single place to see every client conversation and which have unread
messages. Opening a thread marks it read, which clears its badge.
"""

from __future__ import annotations

import streamlit as st

from dashboard import messaging
from dashboard.db_helper import run_query
from dashboard.message_ui import render_thread
from dashboard.theme import top_bar, page_title


def render() -> None:
    top_bar()
    page_title("Secure Messages", "Encrypted conversations with your clients")

    clients = run_query(
        "SELECT client_id, client_name FROM clients WHERE status = 'active' ORDER BY client_name"
    )
    if not clients:
        st.info("No active clients yet.")
        return

    unread = messaging.unread_by_client_for_mp()
    total = sum(unread.values())
    if total:
        n_clients = len([v for v in unread.values() if v])
        st.warning(f"{total} unread message(s) across {n_clients} client(s).")

    # Sort clients with unread first, then by name.
    clients.sort(key=lambda c: (-unread.get(c["client_id"], 0), c["client_name"]))
    options = {}
    for c in clients:
        n = unread.get(c["client_id"], 0)
        label = f"{c['client_name']}  \U0001F534 {n}" if n else c["client_name"]
        options[label] = c["client_id"]

    chosen = st.selectbox("Conversation", list(options.keys()))
    cid = options[chosen]
    render_thread(
        cid, viewer_role="managing_partner", viewer_label="Managing Partner",
        key_prefix=f"mpinbox_{cid}",
    )
