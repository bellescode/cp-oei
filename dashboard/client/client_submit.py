"""
dashboard/client/client_submit.py
CPOI Platform -- client portal "Submit Data" page.

The client downloads the standardized intake template, fills it in, and
uploads it. The upload is queued for Managing Partner review and is NOT
processed (no scoring, no alerts, no published results) until Criterion
Partners reviews and approves it.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard import uploads
from dashboard.theme import top_bar, page_title

_STATUS_LABEL = {
    "pending_review": "Awaiting review",
    "approved": "Approved & processed",
    "rejected": "Returned for correction",
}


def render() -> None:
    cid = st.session_state.get("auth_client_id")
    uploaded_by = _current_email()

    top_bar(tagline="Secure Client Portal")
    page_title("Submit Data", "Provide your reporting-period workbook for review")

    # ---- Step 1: download the template ----
    st.markdown("#### 1. Download the intake template")
    st.caption("Use the official Criterion Partners workbook. Only this format can be processed.")
    try:
        from intake.template import build_blank_template, template_filename
        st.download_button(
            "Download blank template (.xlsx)",
            data=build_blank_template(),
            file_name=template_filename(),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="client_tmpl",
        )
    except Exception as exc:
        st.warning(f"Template is temporarily unavailable: {exc}")

    st.divider()

    # ---- Step 2: upload ----
    st.markdown("#### 2. Upload your completed workbook")
    st.info(
        "Your submission will be reviewed by Criterion Partners before it is processed. "
        "You will see the status update below once it has been reviewed."
    )
    file = st.file_uploader("Completed workbook (.xlsx)", type=["xlsx"], key="client_submit_file")

    if file is not None and st.button("Submit for review", type="primary", key="client_submit_btn"):
        validation_ok: bool | None = None
        feedback = None
        try:
            from intake.validator import validate_workbook
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
                tmp.write(file.getvalue())
                tmp_path = tmp.name
            result = validate_workbook(Path(tmp_path))
            validation_ok = bool(result.valid)
            if not result.valid:
                feedback = result.errors
        except Exception:
            validation_ok = None  # could not pre-check; MP will review regardless

        uploads.save_client_upload(
            client_id=cid, original_filename=file.name, data=file.getvalue(),
            uploaded_by=uploaded_by, validation_ok=validation_ok,
        )

        st.success(
            "Thank you. Your workbook has been submitted to Criterion Partners for review."
        )
        if validation_ok is False and feedback:
            with st.expander("We noticed some formatting issues (your reviewer will advise)"):
                for err in feedback[:25]:
                    st.markdown(f"- {err}")
        st.rerun()

    st.divider()

    # ---- Submission history ----
    st.markdown("#### Your submissions")
    history = uploads.list_for_client(cid)
    if not history:
        st.caption("You have not submitted any workbooks yet.")
        return
    rows = []
    for h in history:
        rows.append({
            "File": h["original_filename"],
            "Submitted": str(h["uploaded_at"])[:16],
            "Status": _STATUS_LABEL.get(h["status"], h["status"]),
            "Reviewer note": h.get("review_note") or "",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _current_email() -> str:
    from dashboard.db_helper import run_query
    user_id = st.session_state.get("auth_user_id")
    rows = run_query("SELECT email FROM client_users WHERE user_id = ?", (user_id,))
    return rows[0]["email"] if rows else "client"
