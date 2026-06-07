"""
dashboard/views/engagement_journal.py
CPOI Platform -- Engagement Intelligence Journal (Managing Partner).

Free-text intelligence observations recorded during discovery calls, review
sessions, and ad hoc communications. High/Medium materiality entries feed the
(anonymized) AI narrative layer. Journal entries are internal only and are
NEVER shown to clients.
"""

from __future__ import annotations

import uuid
from datetime import date

import pandas as pd
import streamlit as st

from dashboard.db_helper import get_connection, run_query
from dashboard.theme import top_bar, page_title
from db.audit import write_audit_log

_ENTRY_TYPES = [
    "discovery_call", "review_session", "ad_hoc_communication",
    "direct_observation", "third_party_disclosure", "pattern_note",
]
_MATERIALITY = ["High", "Medium", "Low"]
_SURFACED = ["No", "Partial", "Yes"]


def render() -> None:
    top_bar()
    page_title("Engagement Journal", "Internal intelligence — never shown to clients")

    clients = run_query(
        "SELECT client_id, client_name FROM clients WHERE status = 'active' ORDER BY client_name"
    )
    if not clients:
        st.info("No active clients. Add one in Client Management first.")
        return
    options = {c["client_name"]: c["client_id"] for c in clients}
    chosen = st.selectbox("Client", list(options.keys()))
    cid = options[chosen]

    with st.form("add_journal", clear_on_submit=True):
        st.markdown("#### New entry")
        c1, c2, c3 = st.columns(3)
        entry_date = c1.date_input("Date", value=date.today())
        entry_type = c2.selectbox("Type", _ENTRY_TYPES)
        materiality = c3.selectbox("Materiality", _MATERIALITY)
        program = st.text_input("Program reference", value="General")
        note = st.text_area("Intelligence note", height=140)
        surfaced = st.selectbox("Surface in client report?", _SURFACED, index=0)

        if st.form_submit_button("Save entry", type="primary"):
            if not note.strip():
                st.error("An intelligence note is required.")
            else:
                conn = get_connection()
                try:
                    journal_id = str(uuid.uuid4())
                    conn.execute(
                        """
                        INSERT INTO engagement_journal
                            (journal_id, client_id, entry_date, entry_type, program_reference,
                             intelligence_note, materiality, surfaced_in_report)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (journal_id, cid, entry_date.isoformat(), entry_type,
                         program.strip() or "General", note.strip(), materiality, surfaced),
                    )
                    write_audit_log(
                        conn, event_type="journal_entry_created", entity_type="engagement_journal",
                        entity_id=journal_id,
                        description=f"Engagement journal entry added for '{chosen}'.",
                        performed_by="managing_partner",
                        metadata={"client_id": cid, "materiality": materiality, "entry_type": entry_type},
                    )
                    conn.commit()
                    st.success("Journal entry saved.")
                finally:
                    conn.close()

    st.divider()
    st.markdown(f"#### Journal — {chosen}")
    entries = run_query(
        """
        SELECT entry_date, entry_type, program_reference, materiality, surfaced_in_report, intelligence_note
        FROM engagement_journal WHERE client_id = ? ORDER BY entry_date DESC
        """,
        (cid,),
    )
    if not entries:
        st.caption("No journal entries for this client yet.")
        return
    df = pd.DataFrame(entries)
    df.columns = ["Date", "Type", "Program", "Materiality", "Surfaced", "Note"]
    st.dataframe(df, use_container_width=True, hide_index=True)
