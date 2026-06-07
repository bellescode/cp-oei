"""
dashboard/message_ui.py
CPOI Platform -- shared secure-thread renderer for both portals.

render_thread() shows the decrypted conversation as left/right bubbles and a
compose box. It marks inbound messages read for the viewer on display.
"""

from __future__ import annotations

import streamlit as st

from dashboard import crypto, messaging
from dashboard.theme import NAVY, GOLD, CREAM


def _bubble(label: str, body: str, when: str, mine: bool) -> str:
    align = "right" if mine else "left"
    bg = NAVY if mine else CREAM
    fg = "#FFFFFF" if mine else "#1F2A37"
    return (
        f'<div style="text-align:{align};margin:8px 0;">'
        f'<div style="display:inline-block;max-width:78%;text-align:left;background:{bg};'
        f'color:{fg};padding:10px 14px;border-radius:10px;border:1px solid #E6E1D5;">'
        f'<div style="font-size:11px;opacity:0.8;margin-bottom:3px;">{label} · {str(when)[:16]}</div>'
        f'<div style="font-size:14px;white-space:pre-wrap;">{body}</div>'
        f'</div></div>'
    )


def render_thread(client_id: str, viewer_role: str, viewer_label: str, key_prefix: str) -> None:
    """Render the secure thread for a client and a compose box for the viewer."""
    if not crypto.encryption_available():
        st.caption(
            "Note: messages are protected by database encryption at rest. Set CPOI_MESSAGE_KEY "
            "on the server to add application-layer encryption."
        )

    messaging.mark_thread_read(client_id, viewer_role)
    thread = messaging.get_thread(client_id)

    if not thread:
        st.caption("No messages yet. Start the conversation below.")
    else:
        html = ""
        for m in thread:
            mine = (m["sender_role"] == viewer_role)
            html += _bubble(m["sender_label"], m["body"], m["created_at"], mine)
        st.html(html)

    with st.form(f"{key_prefix}_compose", clear_on_submit=True):
        body = st.text_area("Your message", height=90, key=f"{key_prefix}_body")
        if st.form_submit_button("Send", type="primary"):
            if body.strip():
                messaging.send_message(client_id, viewer_role, viewer_label, body)
                st.rerun()
            else:
                st.error("Enter a message before sending.")
