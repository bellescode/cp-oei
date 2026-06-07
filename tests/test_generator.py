"""
tests/test_generator.py
CPOI Platform -- Integration tests for Module 5: Report Generation Orchestrator

Coverage:
  reports/generator.py -- generate_report_for_submission, finalize_and_build

Validation criteria (spec Module 5):
  1. reports row is inserted before files are written
  2. ai_narrative_generated flag is set after narrative generation
  3. report_record_created event is written to audit_log
  4. GenerationResult.status == 'awaiting_review'
  5. finalize_and_build() is blocked until all sections approved
  6. After all sections approved, finalize_and_build() writes both files
  7. report_files_generated event is written to audit_log

All narrative generation is patched to return deterministic 60-word text
so tests run without a live ollama instance.
"""

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.init_db import initialize_database, open_connection

TEST_KEY = "cpoi-test-key-generator"

_APPROVED_TEXT = " ".join(["word"] * 60)  # 60 words -- passes 50-300 check


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Fresh encrypted DB connection for each test."""
    monkeypatch.setenv("CPOI_DB_KEY", TEST_KEY)
    db_path = tmp_path / "test_generator.db"
    initialize_database(db_path)
    connection = open_connection(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    yield connection
    connection.close()


def _seed_client(conn: Any) -> str:
    client_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO clients (client_id, client_name, engagement_type) VALUES (?,?,?)",
        (client_id, "Meridian Health Partners", "oeil"),
    )
    return client_id


def _seed_submission(conn: Any, client_id: str, period_end: str = "2026-04-30") -> str:
    sub_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO intake_submissions
           (submission_id, client_id, reporting_period_start, reporting_period_end,
            submitted_at, file_path, ingestion_status)
           VALUES (?,?,?,?,?,?,?)""",
        (sub_id, client_id, "2026-04-01", period_end,
         "2026-05-01T00:00:00", "/tmp/test.xlsx", "processed"),
    )
    return sub_id


def _seed_oei_scores(conn: Any, client_id: str, sub_id: str,
                     period_date: str = "2026-04-30",
                     composite: int = 52) -> str:
    score_id = str(uuid.uuid4())
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
        (score_id, client_id, sub_id, period_date,
         48, 55, 60, 45, 52, composite,
         "Elevated", "Elevated", "Elevated", "Elevated", "Elevated", "Elevated"),
    )
    return score_id


def _seed_signal_readings(conn: Any, client_id: str, sub_id: str,
                          period_date: str = "2026-04-30") -> None:
    signals = [
        ("governance_latency_index", 4.2, 3.0, True),
        ("escalation_suppression_rate", 0.18, 0.20, False),
        ("false_green_indicator", 0.35, 0.25, True),
        ("reporting_divergence_score", 22, 20, True),
        ("priority_collision_index", 3, 4, False),
        ("platform_utilization_pressure", 0.75, 0.80, False),
        ("initiative_saturation_ratio", 1.8, 1.5, True),
        ("dependency_fragility_score", 0.6, 0.5, True),
        ("headcount_stability_index", 0.88, 0.85, False),
        ("reprioritization_frequency", 5, 4, True),
        ("reactive_work_ratio", 0.41, 0.35, True),
    ]
    for name, val, thresh, breached in signals:
        r_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO signal_readings
               (reading_id, submission_id, client_id, signal_name,
                signal_value, threshold_value, threshold_breached, period_date)
               VALUES (?,?,?,?,?,?,?,?)""",
            (r_id, sub_id, client_id, name, val, thresh, int(breached), period_date),
        )


# ---------------------------------------------------------------------------
# Patch target: replace generate_narratives to avoid needing ollama
# ---------------------------------------------------------------------------


def _make_fake_narratives(report_type: str) -> dict[str, Any]:
    """Return a context dict with approved text in every narrative field."""
    if report_type == "snapshot":
        return {
            "narrative_executive_summary": _APPROVED_TEXT,
            "narrative_key_findings":      _APPROVED_TEXT,
            "narrative_recommended_focus": _APPROVED_TEXT,
        }
    return {
        "narrative_executive_summary":    _APPROVED_TEXT,
        "narrative_dimension_deep_dives": {
            "1": _APPROVED_TEXT,
            "2": _APPROVED_TEXT,
            "3": _APPROVED_TEXT,
            "4": _APPROVED_TEXT,
            "5": _APPROVED_TEXT,
        },
        "narrative_forward_focus": _APPROVED_TEXT,
    }


def _patched_generate_narratives(conn, report_type, submission_id,
                                  client_id, base_context, report_id):
    """Patch for reports.narrator.generate_narratives."""
    updated = dict(base_context)
    updated.update(_make_fake_narratives(report_type))
    return updated


# ---------------------------------------------------------------------------
# Test 1: reports row inserted before files written
# ---------------------------------------------------------------------------


def test_report_record_created_before_files(conn: Any, tmp_path: Path) -> None:
    """
    Criterion 1: reports row exists immediately after generate_report_for_submission.
    No files should exist at this stage.
    """
    client_id = _seed_client(conn)
    sub_id = _seed_submission(conn, client_id)
    _seed_oei_scores(conn, client_id, sub_id)
    _seed_signal_readings(conn, client_id, sub_id)

    with patch("reports.generator.generate_narratives", side_effect=_patched_generate_narratives):
        from reports.generator import generate_report_for_submission
        result = generate_report_for_submission(conn, sub_id, "snapshot")
        conn.commit()

    row = conn.execute(
        "SELECT report_id, ai_narrative_generated FROM reports WHERE report_id = ?",
        (result.report_id,),
    ).fetchone()

    assert row is not None, "reports row was not inserted."
    # No files written yet -- file_path must be NULL.
    file_path = conn.execute(
        "SELECT file_path FROM reports WHERE report_id = ?", (result.report_id,)
    ).fetchone()["file_path"]
    assert file_path is None, "file_path should be NULL before finalize_and_build."


# ---------------------------------------------------------------------------
# Test 2: ai_narrative_generated flag is set
# ---------------------------------------------------------------------------


def test_ai_narrative_generated_flag(conn: Any, tmp_path: Path) -> None:
    """Criterion 2: ai_narrative_generated = 1 after generate_report_for_submission."""
    client_id = _seed_client(conn)
    sub_id = _seed_submission(conn, client_id)
    _seed_oei_scores(conn, client_id, sub_id)
    _seed_signal_readings(conn, client_id, sub_id)

    with patch("reports.generator.generate_narratives", side_effect=_patched_generate_narratives):
        from reports.generator import generate_report_for_submission
        result = generate_report_for_submission(conn, sub_id, "snapshot")
        conn.commit()

    flag = conn.execute(
        "SELECT ai_narrative_generated FROM reports WHERE report_id = ?",
        (result.report_id,),
    ).fetchone()["ai_narrative_generated"]
    assert flag == 1, f"Expected ai_narrative_generated=1, got {flag}."


# ---------------------------------------------------------------------------
# Test 3: audit log contains report_record_created
# ---------------------------------------------------------------------------


def test_audit_log_report_record_created(conn: Any, tmp_path: Path) -> None:
    """Criterion 3: report_record_created event is written to audit_log."""
    client_id = _seed_client(conn)
    sub_id = _seed_submission(conn, client_id)
    _seed_oei_scores(conn, client_id, sub_id)
    _seed_signal_readings(conn, client_id, sub_id)

    with patch("reports.generator.generate_narratives", side_effect=_patched_generate_narratives):
        from reports.generator import generate_report_for_submission
        result = generate_report_for_submission(conn, sub_id, "snapshot")
        conn.commit()

    event = conn.execute(
        "SELECT * FROM audit_log WHERE event_type = 'report_record_created' AND entity_id = ?",
        (result.report_id,),
    ).fetchone()
    assert event is not None, "report_record_created audit event not found."


# ---------------------------------------------------------------------------
# Test 4: GenerationResult.status == 'awaiting_review'
# ---------------------------------------------------------------------------


def test_generation_result_status(conn: Any, tmp_path: Path) -> None:
    """Criterion 4: GenerationResult.status is 'awaiting_review'."""
    client_id = _seed_client(conn)
    sub_id = _seed_submission(conn, client_id)
    _seed_oei_scores(conn, client_id, sub_id)
    _seed_signal_readings(conn, client_id, sub_id)

    with patch("reports.generator.generate_narratives", side_effect=_patched_generate_narratives):
        from reports.generator import generate_report_for_submission
        result = generate_report_for_submission(conn, sub_id, "snapshot")
        conn.commit()

    assert result.status == "awaiting_review", (
        f"Expected status='awaiting_review', got '{result.status}'."
    )
    assert result.section_count > 0, "section_count should be > 0."
    assert result.client_id == client_id


# ---------------------------------------------------------------------------
# Test 5: finalize_and_build blocked until all sections approved
# ---------------------------------------------------------------------------


def test_finalize_blocked_until_approved(conn: Any, tmp_path: Path) -> None:
    """Criterion 5: finalize_and_build raises RuntimeError if any section pending."""
    client_id = _seed_client(conn)
    sub_id = _seed_submission(conn, client_id)
    _seed_oei_scores(conn, client_id, sub_id)
    _seed_signal_readings(conn, client_id, sub_id)

    with patch("reports.generator.generate_narratives", side_effect=_patched_generate_narratives):
        from reports.generator import generate_report_for_submission
        result = generate_report_for_submission(conn, sub_id, "snapshot")
        conn.commit()

    from reports.generator import finalize_and_build
    with pytest.raises(RuntimeError, match="approval"):
        finalize_and_build(conn, result.report_id, tmp_path)


# ---------------------------------------------------------------------------
# Test 6: finalize_and_build writes both files after full approval
# ---------------------------------------------------------------------------


def test_finalize_and_build_writes_files(conn: Any, tmp_path: Path) -> None:
    """Criterion 6: finalize_and_build returns both .docx and .pdf paths."""
    client_id = _seed_client(conn)
    sub_id = _seed_submission(conn, client_id)
    _seed_oei_scores(conn, client_id, sub_id)
    _seed_signal_readings(conn, client_id, sub_id)

    with patch("reports.generator.generate_narratives", side_effect=_patched_generate_narratives):
        from reports.generator import generate_report_for_submission
        result = generate_report_for_submission(conn, sub_id, "snapshot")
        conn.commit()

    # Approve all sections.
    from reports.editor import approve_section, get_draft_for_review
    drafts = get_draft_for_review(conn, result.report_id)
    for draft in drafts:
        approve_section(conn, result.report_id, draft.section_name)
    conn.commit()

    from reports.generator import finalize_and_build
    docx_path, pdf_path = finalize_and_build(conn, result.report_id, tmp_path)
    conn.commit()

    assert docx_path.exists(), f"docx file not found: {docx_path}"
    assert pdf_path.exists(), f"pdf file not found: {pdf_path}"
    assert docx_path.stat().st_size > 0, "docx file is empty."
    assert pdf_path.stat().st_size > 0, "pdf file is empty."

    # file_path in reports table should be updated.
    db_path = conn.execute(
        "SELECT file_path FROM reports WHERE report_id = ?", (result.report_id,)
    ).fetchone()["file_path"]
    assert db_path is not None, "reports.file_path not updated after finalize."


# ---------------------------------------------------------------------------
# Test 7: audit log contains report_files_generated
# ---------------------------------------------------------------------------


def test_audit_log_report_files_generated(conn: Any, tmp_path: Path) -> None:
    """Criterion 7: report_files_generated event is written after finalize_and_build."""
    client_id = _seed_client(conn)
    sub_id = _seed_submission(conn, client_id)
    _seed_oei_scores(conn, client_id, sub_id)
    _seed_signal_readings(conn, client_id, sub_id)

    with patch("reports.generator.generate_narratives", side_effect=_patched_generate_narratives):
        from reports.generator import generate_report_for_submission
        result = generate_report_for_submission(conn, sub_id, "snapshot")
        conn.commit()

    from reports.editor import approve_section, get_draft_for_review
    drafts = get_draft_for_review(conn, result.report_id)
    for draft in drafts:
        approve_section(conn, result.report_id, draft.section_name)
    conn.commit()

    from reports.generator import finalize_and_build
    finalize_and_build(conn, result.report_id, tmp_path)
    conn.commit()

    event = conn.execute(
        "SELECT * FROM audit_log WHERE event_type = 'report_files_generated' AND entity_id = ?",
        (result.report_id,),
    ).fetchone()
    assert event is not None, "report_files_generated audit event not found."


# ---------------------------------------------------------------------------
# Test 8: monthly_brief report type generates successfully
# ---------------------------------------------------------------------------


def test_monthly_brief_generation(conn: Any, tmp_path: Path) -> None:
    """monthly_brief report type follows same pipeline; chart is included."""
    client_id = _seed_client(conn)
    sub_id = _seed_submission(conn, client_id)
    _seed_oei_scores(conn, client_id, sub_id)
    _seed_signal_readings(conn, client_id, sub_id)
    # Second period for trajectory chart (requires >= 2 periods).
    sub_id2 = _seed_submission(conn, client_id, period_end="2026-05-31")
    _seed_oei_scores(conn, client_id, sub_id2, period_date="2026-05-31", composite=45)
    _seed_signal_readings(conn, client_id, sub_id2, period_date="2026-05-31")

    with patch("reports.generator.generate_narratives", side_effect=_patched_generate_narratives):
        from reports.generator import generate_report_for_submission
        result = generate_report_for_submission(conn, sub_id2, "monthly_brief")
        conn.commit()

    assert result.status == "awaiting_review"
    assert result.report_type == "monthly_brief"

    from reports.editor import approve_section, get_draft_for_review
    drafts = get_draft_for_review(conn, result.report_id)
    for draft in drafts:
        approve_section(conn, result.report_id, draft.section_name)
    conn.commit()

    from reports.generator import finalize_and_build
    docx_path, pdf_path = finalize_and_build(conn, result.report_id, tmp_path)
    conn.commit()

    assert docx_path.exists()
    assert pdf_path.exists()


# ---------------------------------------------------------------------------
# Test 9: invalid report_type raises ValueError
# ---------------------------------------------------------------------------


def test_invalid_report_type_raises(conn: Any, tmp_path: Path) -> None:
    """generate_report_for_submission rejects unknown report_type."""
    client_id = _seed_client(conn)
    sub_id = _seed_submission(conn, client_id)
    _seed_oei_scores(conn, client_id, sub_id)

    from reports.generator import generate_report_for_submission
    with pytest.raises(ValueError, match="report_type"):
        generate_report_for_submission(conn, sub_id, "quarterly_review")
