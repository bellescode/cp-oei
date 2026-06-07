"""
dashboard/views/data_intake.py
CPOI Platform -- Data Intake (Managing Partner).

Three capabilities:
  1. Download the blank standardized intake template to send to clients.
  2. Review the queue of client self-service uploads. Client uploads are NEVER
     processed automatically -- the Managing Partner approves (validate ->
     ingest -> score -> alerts) or rejects each one here.
  3. Upload and process a workbook directly (MP acting on the client's behalf).
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard import uploads
from dashboard.db_helper import get_connection, run_query, run_write
from dashboard.theme import top_bar, page_title


# ---------------------------------------------------------------------------
# Shared ingest pipeline (used by manual ingest AND client-upload approval)
# ---------------------------------------------------------------------------

def _ingest_pipeline(client_id: str, source_path: Path, val_result):
    """Run ingest -> score -> alerts for an already-validated workbook."""
    from intake.ingestor import ingest_submission
    from scoring.engine import run_scoring
    from alerts.detector import create_alerts_for_submission

    ingestion = ingest_submission(
        source_file_path=source_path, validation_result=val_result, client_id=client_id
    )
    conn = get_connection()
    try:
        engine_result = run_scoring(
            conn=conn, submission_id=ingestion.submission_id, client_id=client_id,
            period_date=ingestion.reporting_period_end, dataframes=ingestion.dataframes,
            metadata=ingestion.metadata, apply_active_overrides=False,
        )
        conn.commit()
        alert_ids = create_alerts_for_submission(
            conn=conn, submission_id=ingestion.submission_id, client_id=client_id,
            period_date=ingestion.reporting_period_end,
        )
        conn.commit()
    finally:
        conn.close()
    return ingestion, engine_result, alert_ids


def _result_metrics(engine_result, alert_ids) -> None:
    composite = round(engine_result.oei_result.composite_score)
    classification = engine_result.oei_result.composite_classification
    a, b, c = st.columns(3)
    a.metric("OEI Composite", composite)
    b.metric("Classification", classification)
    c.metric("Alerts Generated", len(alert_ids))


# ---------------------------------------------------------------------------
# Template download
# ---------------------------------------------------------------------------

def _template_button() -> None:
    try:
        from intake.template import build_blank_template, template_filename
        st.download_button(
            "Download blank intake template (.xlsx)",
            data=build_blank_template(),
            file_name=template_filename(),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="mp_tmpl",
        )
    except Exception as exc:
        st.warning(f"Template generation unavailable: {exc}")


# ---------------------------------------------------------------------------
# Pending client-upload review queue
# ---------------------------------------------------------------------------

def _pending_queue() -> None:
    pending = uploads.list_pending()
    header = "#### Client submissions awaiting review"
    if pending:
        header += f"  ·  {len(pending)} pending"
    st.markdown(header)

    if not pending:
        st.caption("No client submissions are waiting for review.")
        return

    for up in pending:
        with st.container(border=True):
            top = st.columns([2.4, 2, 1.4, 2])
            top[0].markdown(f"**{up['client_name']}**")
            top[0].caption(f"By {up['uploaded_by']}")
            top[1].caption(up["original_filename"])
            top[2].caption(str(up["uploaded_at"])[:16])
            precheck = up.get("validation_ok")
            if precheck == 1:
                top[3].caption("Pre-check: passed")
            elif precheck == 0:
                top[3].caption("Pre-check: had issues")
            else:
                top[3].caption("Pre-check: not run")

            stored = Path(up["stored_path"])
            dl, approve, reject = st.columns([1.4, 1.4, 1.4])
            if stored.exists():
                with open(stored, "rb") as f:
                    dl.download_button(
                        "Download", data=f.read(), file_name=up["original_filename"],
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key=f"dlpending_{up['upload_id']}",
                    )

            if approve.button("Approve & process", key=f"approve_{up['upload_id']}", type="primary"):
                if not stored.exists():
                    st.error("Stored file is missing; ask the client to re-submit.")
                else:
                    from intake.validator import validate_workbook
                    val = validate_workbook(stored)
                    if not val.valid:
                        st.error(f"Cannot process: {len(val.errors)} validation error(s).")
                        with st.expander("Validation errors", expanded=True):
                            for err in val.errors[:25]:
                                st.markdown(f"- {err}")
                        st.caption("Reject this submission with a note so the client can correct it.")
                    else:
                        try:
                            ingestion, engine_result, alert_ids = _ingest_pipeline(
                                up["client_id"], stored, val
                            )
                            uploads.mark_approved(up["upload_id"], ingestion.submission_id)
                            st.success(f"Approved and processed. Submission {ingestion.submission_id}.")
                            _result_metrics(engine_result, alert_ids)
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Processing failed: {exc}")

            with reject:
                note = st.text_input("Reason", key=f"rejnote_{up['upload_id']}",
                                     label_visibility="collapsed", placeholder="Reason for rejection")
                if st.button("Reject", key=f"reject_{up['upload_id']}"):
                    uploads.mark_rejected(up["upload_id"], note)
                    st.rerun()


# ---------------------------------------------------------------------------
# Manual upload (MP acting directly)
# ---------------------------------------------------------------------------

def _client_selector():
    clients = run_query(
        "SELECT client_id, client_name, engagement_type FROM clients ORDER BY client_name"
    )
    options = {c["client_name"]: c for c in clients}
    names = ["-- New client --"] + list(options.keys())

    col_c, col_t = st.columns([3, 2])
    selected = col_c.selectbox("Client", names, key="intake_client")
    if selected == "-- New client --":
        new_name = col_c.text_input("New client name", key="intake_new_name")
        etype = col_t.selectbox("Engagement type", ["snapshot", "oeil"], key="intake_type_new")
        return None, new_name, etype
    existing = options[selected]
    types = ["snapshot", "oeil"]
    idx = types.index(existing["engagement_type"]) if existing["engagement_type"] in types else 0
    etype = col_t.selectbox("Engagement type", types, index=idx, key="intake_type_exist")
    return existing["client_id"], selected, etype


def _manual_upload() -> None:
    st.markdown("#### Upload directly (on the client's behalf)")
    client_id, client_name, engagement_type = _client_selector()
    uploaded = st.file_uploader("Upload client workbook (.xlsx)", type=["xlsx"], key="intake_upload")

    if uploaded is None:
        st.info("Upload a workbook to validate and ingest.")
        return

    col_v, col_i = st.columns(2)
    do_validate = col_v.button("Validate workbook", type="secondary")
    do_ingest = col_i.button("Ingest submission", type="primary")

    if do_validate:
        for k in ("intake_validation_result", "intake_validated", "intake_tmp_path"):
            st.session_state.pop(k, None)
        try:
            from intake.validator import validate_workbook
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
                tmp.write(uploaded.getvalue())
                tmp_path = tmp.name
            result = validate_workbook(Path(tmp_path))
            st.session_state["intake_validation_result"] = result
            st.session_state["intake_tmp_path"] = tmp_path
            st.session_state["intake_validated"] = result.valid
            if result.valid:
                st.success("Validation passed. Ready to ingest.")
            else:
                st.error(f"Validation failed: {len(result.errors)} error(s) to correct.")
            if result.errors:
                with st.expander("Errors", expanded=True):
                    for err in result.errors:
                        st.markdown(f"- {err}")
        except Exception as exc:
            st.error(f"Validation error: {exc}")
            st.session_state["intake_validated"] = False
    elif st.session_state.get("intake_validation_result") is not None:
        result = st.session_state["intake_validation_result"]
        if result.valid:
            st.success("Validation passed. Ready to ingest.")
        else:
            st.error(f"Validation failed: {len(result.errors)} error(s).")

    if do_ingest:
        val_result = st.session_state.get("intake_validation_result")
        tmp_path = st.session_state.get("intake_tmp_path")
        if not st.session_state.get("intake_validated"):
            st.warning("Run validation first.")
            return
        if val_result is None or not val_result.valid:
            st.error("Cannot ingest: validation errors remain.")
            return
        if not tmp_path:
            st.error("Temporary file missing. Re-upload and validate.")
            return

        effective_name = client_name.strip() if client_id is None else client_name
        if not effective_name:
            st.error("Client name is required for a new client.")
            return

        try:
            if client_id is None:
                new_id = str(uuid.uuid4())
                run_write(
                    "INSERT INTO clients (client_id, client_name, engagement_type, status) VALUES (?, ?, ?, 'active')",
                    (new_id, effective_name, engagement_type),
                )
                effective_client_id = new_id
            else:
                effective_client_id = client_id

            with st.spinner("Ingesting and scoring..."):
                _ingestion, engine_result, alert_ids = _ingest_pipeline(
                    effective_client_id, Path(tmp_path), val_result
                )

            for k in ("intake_validated", "intake_validation_result", "intake_tmp_path"):
                st.session_state.pop(k, None)

            st.success("Submission ingested and scored.")
            _result_metrics(engine_result, alert_ids)
            if alert_ids:
                st.warning(f"{len(alert_ids)} threshold breach(es). Review on the client's profile.")
        except Exception as exc:
            st.error(f"Ingestion failed: {exc}")


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def render() -> None:
    top_bar()
    page_title("Data Intake", "Template, client submission review, and direct ingestion")

    _template_button()
    st.divider()
    _pending_queue()
    st.divider()
    _manual_upload()

    st.divider()
    st.markdown("#### Recent ingestion activity")
    events = run_query(
        """
        SELECT performed_at, event_type, entity_id, description, performed_by
        FROM audit_log
        WHERE event_type IN ('intake_ingested','score_calculated','signals_calculated',
                             'alert_created','submission_created','client_upload_received',
                             'client_upload_approved','client_upload_rejected')
        ORDER BY performed_at DESC LIMIT 12
        """
    )
    if events:
        df = pd.DataFrame(events)
        df.columns = ["Timestamp", "Event", "Entity ID", "Detail", "By"]
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.caption("No ingestion events recorded yet.")
