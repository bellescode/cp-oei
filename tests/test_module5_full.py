"""
tests/test_module5_full.py
CPOI Platform -- Full Module 5 validation test suite

Spec validation criteria (Module 5):
  1. Template renders with all variables populated (no Jinja2 TemplateError)
  2. No unfilled placeholders in rendered output (no {{ }}, no NARRATIVE_PLACEHOLDER__)
  3. Both .docx and .pdf file formats generate and are non-empty
  4. Watermark text is present in the generated PDF

These tests exercise the full pipeline end-to-end: Jinja2 rendering,
docx generation via python-docx, and PDF generation via ReportLab.
Narrative generation is patched to avoid requiring a live ollama instance.
"""

import sys
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.init_db import initialize_database, open_connection

TEST_KEY = "cpoi-test-key-module5"
_NARRATIVE_TEXT = " ".join(["word"] * 60)  # 60 words -- within 50-300 range


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Fresh encrypted DB connection for each test."""
    monkeypatch.setenv("CPOI_DB_KEY", TEST_KEY)
    db_path = tmp_path / "test_module5.db"
    initialize_database(db_path)
    connection = open_connection(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    yield connection
    connection.close()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_client(conn: Any, name: str = "Pinnacle Systems Group") -> str:
    cid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO clients (client_id, client_name, engagement_type) VALUES (?,?,?)",
        (cid, name, "oeil"),
    )
    return cid


def _seed_submission(conn: Any, client_id: str,
                     period_start: str = "2026-04-01",
                     period_end: str = "2026-04-30") -> str:
    sid = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO intake_submissions
           (submission_id, client_id, reporting_period_start, reporting_period_end,
            submitted_at, file_path, ingestion_status)
           VALUES (?,?,?,?,?,?,?)""",
        (sid, client_id, period_start, period_end,
         "2026-05-01T09:00:00", "/data/intake.xlsx", "processed"),
    )
    return sid


def _seed_scores(conn: Any, client_id: str, sub_id: str,
                 period_date: str = "2026-04-30",
                 composite: int = 63) -> None:
    conn.execute(
        """INSERT INTO oei_scores
           (score_id, client_id, submission_id, period_date,
            strategic_saturation_score, governance_responsiveness_score,
            execution_visibility_score, reporting_integrity_score,
            org_sustainability_score, oei_composite_score,
            strategic_saturation_class, governance_responsiveness_class,
            execution_visibility_class, reporting_integrity_class,
            org_sustainability_class, composite_class)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()), client_id, sub_id, period_date,
         70, 65, 60, 58, 62, composite,
         "High Risk", "Elevated", "Elevated", "Elevated", "Elevated", "High Risk"),
    )


def _seed_signals(conn: Any, client_id: str, sub_id: str,
                  period_date: str = "2026-04-30") -> None:
    rows = [
        ("governance_latency_index",      5.1, 3.0, True),
        ("escalation_suppression_rate",   0.28, 0.20, True),
        ("false_green_indicator",         0.15, 0.25, False),
        ("reporting_divergence_score",    18,   20,   False),
        ("priority_collision_index",      5,    4,    True),
        ("platform_utilization_pressure", 0.92, 0.80, True),
        ("initiative_saturation_ratio",   2.1,  1.5,  True),
        ("dependency_fragility_score",    0.45, 0.50, False),
        ("headcount_stability_index",     0.70, 0.85, True),
        ("reprioritization_frequency",    3,    4,    False),
        ("reactive_work_ratio",           0.50, 0.35, True),
    ]
    for name, val, thresh, breached in rows:
        conn.execute(
            """INSERT INTO signal_readings
               (reading_id, submission_id, client_id, signal_name,
                signal_value, threshold_value, threshold_breached, period_date)
               VALUES (?,?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), sub_id, client_id,
             name, val, thresh, int(breached), period_date),
        )


def _patched_generate_narratives(conn, report_type, submission_id,
                                  client_id, base_context, report_id):
    updated = dict(base_context)
    updated["narrative_executive_summary"] = _NARRATIVE_TEXT
    if report_type == "snapshot":
        updated["narrative_key_findings"]      = _NARRATIVE_TEXT
        updated["narrative_recommended_focus"] = _NARRATIVE_TEXT
    else:
        updated["narrative_dimension_deep_dives"] = {
            "1": _NARRATIVE_TEXT,
            "2": _NARRATIVE_TEXT,
            "3": _NARRATIVE_TEXT,
            "4": _NARRATIVE_TEXT,
            "5": _NARRATIVE_TEXT,
        }
        updated["narrative_forward_focus"] = _NARRATIVE_TEXT
    return updated


def _build_approved_report(conn: Any, tmp_path: Path,
                            report_type: str = "snapshot") -> tuple[str, Path, Path]:
    """
    Seed DB, run full pipeline, approve all sections, finalize.
    Returns (report_id, docx_path, pdf_path).
    """
    client_id = _seed_client(conn)
    sub_id = _seed_submission(conn, client_id)
    _seed_scores(conn, client_id, sub_id)
    _seed_signals(conn, client_id, sub_id)

    if report_type == "monthly_brief":
        # Second period for trajectory chart.
        sub_id2 = _seed_submission(conn, client_id, "2026-05-01", "2026-05-31")
        _seed_scores(conn, client_id, sub_id2, period_date="2026-05-31", composite=55)
        _seed_signals(conn, client_id, sub_id2, period_date="2026-05-31")
        active_sub = sub_id2
    else:
        active_sub = sub_id

    with patch("reports.generator.generate_narratives",
               side_effect=_patched_generate_narratives):
        from reports.generator import generate_report_for_submission
        result = generate_report_for_submission(conn, active_sub, report_type)
        conn.commit()

    from reports.editor import approve_section, get_draft_for_review
    for draft in get_draft_for_review(conn, result.report_id):
        approve_section(conn, result.report_id, draft.section_name)
    conn.commit()

    from reports.generator import finalize_and_build
    docx_path, pdf_path = finalize_and_build(conn, result.report_id, tmp_path)
    conn.commit()

    return result.report_id, docx_path, pdf_path


# ---------------------------------------------------------------------------
# Criterion 1 & 2: Template renders; no unfilled placeholders
# ---------------------------------------------------------------------------


def test_snapshot_template_renders_no_placeholders(conn: Any, tmp_path: Path) -> None:
    """
    Snapshot template must render without error and contain no {{ }}
    tokens or NARRATIVE_PLACEHOLDER__ sentinels.
    """
    from reports.builder import _render_template

    client_id = _seed_client(conn)
    sub_id = _seed_submission(conn, client_id)
    _seed_scores(conn, client_id, sub_id)
    _seed_signals(conn, client_id, sub_id)

    from reports.generator import (
        _build_dimension_contexts,
        _build_signal_reading_contexts,
        _fetch_oei_scores,
        _fetch_signal_readings,
        _fetch_submission,
    )
    submission = _fetch_submission(conn, sub_id)
    scores = _fetch_oei_scores(conn, sub_id)
    signals = _fetch_signal_readings(conn, sub_id)
    dims = _build_dimension_contexts(scores, {})
    signal_ctxs = _build_signal_reading_contexts(signals)

    rid = str(uuid.uuid4())
    context = {
        "report_id":                  rid,
        "report_type":                "snapshot",
        "report_date":                "2026-04-30",
        "generated_at":               "2026-04-30T12:00:00+00:00",
        "client_name":                "Pinnacle Systems Group",
        "sponsor_name":               "Pinnacle Systems Group",
        "engagement_type":            "oeil",
        "engagement_start_date":      "2026-01-01",
        "submission_id":              sub_id,
        "reporting_period_start":     "2026-04-01",
        "reporting_period_end":       "2026-04-30",
        "submitted_at":               "2026-05-01T09:00:00",
        "oei_composite_score":        63,
        "composite_class":            "High Risk",
        "composite_description":      "Material operational gaps.",
        "prior_composite_score":      55,
        "score_delta":                8,
        "score_direction":            "worsened",
        "dimensions":                 dims,
        "signal_readings":            signal_ctxs,
        "has_override":               False,
        "override_footnote":          "",
        "narrative_executive_summary": _NARRATIVE_TEXT,
        "narrative_key_findings":      _NARRATIVE_TEXT,
        "narrative_recommended_focus": _NARRATIVE_TEXT,
        "qualitative_notes":           [],
    }

    rendered = _render_template(context, "snapshot")

    assert "{{" not in rendered, "Rendered template contains unfilled {{ }} tokens."
    assert "}}" not in rendered, "Rendered template contains unfilled {{ }} tokens."
    assert "NARRATIVE_PLACEHOLDER__" not in rendered, (
        "Rendered template contains NARRATIVE_PLACEHOLDER__ sentinel."
    )
    # Spot-check key content is present.
    assert "Pinnacle Systems Group" in rendered
    assert "63" in rendered
    assert "High Risk" in rendered


def test_monthly_brief_template_renders_no_placeholders(conn: Any, tmp_path: Path) -> None:
    """
    Monthly brief template must render without error and contain no unfilled tokens.
    """
    from reports.builder import _render_template
    from reports.chart import generate_trajectory_chart
    from reports.generator import (
        _build_dimension_contexts,
        _build_signal_reading_contexts,
        _fetch_oei_scores,
        _fetch_signal_readings,
        _fetch_submission,
    )

    client_id = _seed_client(conn)
    sub_id1 = _seed_submission(conn, client_id, "2026-03-01", "2026-03-31")
    _seed_scores(conn, client_id, sub_id1, period_date="2026-03-31", composite=58)
    _seed_signals(conn, client_id, sub_id1, period_date="2026-03-31")

    sub_id2 = _seed_submission(conn, client_id, "2026-04-01", "2026-04-30")
    _seed_scores(conn, client_id, sub_id2, period_date="2026-04-30", composite=63)
    _seed_signals(conn, client_id, sub_id2, period_date="2026-04-30")

    submission = _fetch_submission(conn, sub_id2)
    scores = _fetch_oei_scores(conn, sub_id2)
    signals = _fetch_signal_readings(conn, sub_id2)
    dims = _build_dimension_contexts(scores, {})
    signal_ctxs = _build_signal_reading_contexts(signals)

    score_history = [
        {"period_date": "2026-03-31", "oei_composite_score": 58},
        {"period_date": "2026-04-30", "oei_composite_score": 63},
    ]
    chart_b64 = generate_trajectory_chart(score_history)

    rid = str(uuid.uuid4())
    context = {
        "report_id":                       rid,
        "report_type":                     "monthly_brief",
        "report_date":                     "2026-04-30",
        "generated_at":                    "2026-04-30T12:00:00+00:00",
        "client_name":                     "Pinnacle Systems Group",
        "sponsor_name":                    "Pinnacle Systems Group",
        "engagement_type":                 "oeil",
        "engagement_start_date":           "2026-01-01",
        "period_label":                    "April 2026",
        "prior_period_label":              "March 2026",
        "submission_id":                   sub_id2,
        "reporting_period_start":          "2026-04-01",
        "reporting_period_end":            "2026-04-30",
        "submitted_at":                    "2026-05-01T09:00:00",
        "oei_composite_score":             63,
        "composite_class":                 "High Risk",
        "composite_description":           "Material operational gaps.",
        "prior_composite_score":           58,
        "score_delta":                     5,
        "score_direction":                 "worsened",
        "dimensions":                      dims,
        "trajectory_chart_b64":            chart_b64,
        "signal_readings":                 signal_ctxs,
        "breached_signals":                [s for s in signal_ctxs if s["threshold_breached"]],
        "cleared_signals":                 [],
        "active_alerts":                   [],
        "has_override":                    False,
        "override_footnote":               "",
        "narrative_executive_summary":     _NARRATIVE_TEXT,
        "narrative_dimension_deep_dives":  {
            "1": _NARRATIVE_TEXT,
            "2": _NARRATIVE_TEXT,
            "3": _NARRATIVE_TEXT,
            "4": _NARRATIVE_TEXT,
            "5": _NARRATIVE_TEXT,
        },
        "narrative_forward_focus":         _NARRATIVE_TEXT,
        "qualitative_notes":               [],
    }

    rendered = _render_template(context, "monthly_brief")

    assert "{{" not in rendered
    assert "}}" not in rendered
    assert "NARRATIVE_PLACEHOLDER__" not in rendered
    assert "Pinnacle Systems Group" in rendered
    assert "63" in rendered
    assert "April 2026" in rendered


# ---------------------------------------------------------------------------
# Criterion 3: Both file formats generate and are non-empty
# ---------------------------------------------------------------------------


def test_snapshot_both_formats_generated(conn: Any, tmp_path: Path) -> None:
    """Snapshot report produces both .docx and .pdf files that are non-empty."""
    _, docx_path, pdf_path = _build_approved_report(conn, tmp_path, "snapshot")

    assert docx_path.suffix == ".docx", f"Expected .docx, got {docx_path.suffix}"
    assert pdf_path.suffix == ".pdf",   f"Expected .pdf, got {pdf_path.suffix}"
    assert docx_path.exists(), f"docx not found: {docx_path}"
    assert pdf_path.exists(),  f"pdf not found: {pdf_path}"
    assert docx_path.stat().st_size > 4096, (
        f"docx file suspiciously small: {docx_path.stat().st_size} bytes"
    )
    assert pdf_path.stat().st_size > 1024, (
        f"pdf file suspiciously small: {pdf_path.stat().st_size} bytes"
    )


def test_monthly_brief_both_formats_generated(conn: Any, tmp_path: Path) -> None:
    """Monthly brief report produces both .docx and .pdf files that are non-empty."""
    _, docx_path, pdf_path = _build_approved_report(conn, tmp_path, "monthly_brief")

    assert docx_path.suffix == ".docx"
    assert pdf_path.suffix == ".pdf"
    assert docx_path.exists()
    assert pdf_path.exists()
    assert docx_path.stat().st_size > 4096
    assert pdf_path.stat().st_size > 1024


# ---------------------------------------------------------------------------
# Criterion 4: Watermark present in PDF
# ---------------------------------------------------------------------------


def test_snapshot_pdf_contains_watermark(conn: Any, tmp_path: Path) -> None:
    """
    Watermark text must appear in the PDF byte stream.

    ReportLab embeds text as PDF content streams. The watermark string
    is written by _WatermarkCanvas.showPage() on every page. We verify
    it is present in the raw bytes of the generated file.
    """
    _, _docx, pdf_path = _build_approved_report(conn, tmp_path, "snapshot")

    raw = pdf_path.read_bytes()
    # The watermark contains 'Confidential' and 'Criterion Partners'
    # encoded as PDF text. PDF stores strings as UTF-16BE or PDFDocEncoding
    # but short ASCII strings are typically stored verbatim. Check for
    # the presence of the key phrase in the raw byte stream.
    assert b"Confidential" in raw, (
        "Watermark text 'Confidential' not found in PDF byte stream."
    )
    assert b"Criterion Partners" in raw, (
        "Watermark text 'Criterion Partners' not found in PDF byte stream."
    )


def test_monthly_brief_pdf_contains_watermark(conn: Any, tmp_path: Path) -> None:
    """Watermark is present in the monthly brief PDF."""
    _, _docx, pdf_path = _build_approved_report(conn, tmp_path, "monthly_brief")

    raw = pdf_path.read_bytes()
    assert b"Confidential" in raw
    assert b"Criterion Partners" in raw


# ---------------------------------------------------------------------------
# Criterion 5: Filename convention matches spec
# ---------------------------------------------------------------------------


def test_snapshot_filename_convention(conn: Any, tmp_path: Path) -> None:
    """Filenames must follow CP_OEI_{type}_{client_slug}_{date}_{report_id[:8]} pattern."""
    _, docx_path, pdf_path = _build_approved_report(conn, tmp_path, "snapshot")

    assert docx_path.name.startswith("CP_OEI_snapshot_"), (
        f"docx filename does not match convention: {docx_path.name}"
    )
    assert pdf_path.name.startswith("CP_OEI_snapshot_"), (
        f"pdf filename does not match convention: {pdf_path.name}"
    )


def test_monthly_brief_filename_convention(conn: Any, tmp_path: Path) -> None:
    """Monthly brief filenames must follow the same naming convention."""
    _, docx_path, pdf_path = _build_approved_report(conn, tmp_path, "monthly_brief")

    assert "monthly" in docx_path.name and docx_path.name.startswith("CP_OEI_"), (
        f"docx filename does not match convention: {docx_path.name}"
    )
    assert "monthly" in pdf_path.name and pdf_path.name.startswith("CP_OEI_"), (
        f"pdf filename does not match convention: {pdf_path.name}"
    )
