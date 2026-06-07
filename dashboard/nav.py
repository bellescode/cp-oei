"""
dashboard/nav.py
CPOI Platform -- programmatic navigation between st.navigation pages.

app.py registers the page objects under stable keys. Any view can then jump
to another page (e.g. Client Portfolio -> Client Profile) via goto(key).
Falls back gracefully if programmatic switching is unavailable.
"""

from __future__ import annotations

import streamlit as st


def register(pages: dict) -> None:
    st.session_state["_nav_pages"] = pages


def goto(key: str) -> None:
    """Switch to a registered page by key. No-op if not found."""
    page = st.session_state.get("_nav_pages", {}).get(key)
    if page is None:
        return
    try:
        st.switch_page(page)
    except Exception:
        # Older Streamlit or callable-page limitation: the target page reads
        # the relevant session_state id on its own, so the user can also use
        # the sidebar. Surface a gentle hint rather than crashing.
        st.info("Open the target page from the sidebar to continue.")
