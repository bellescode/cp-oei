"""
reports/generator.py
CPOI Platform -- Report Generation Orchestrator

The single entry point for the Module 5 pipeline. External callers
(dashboard, CLI) use two functions:

  generate_report_for_submission(conn, submission_id, report_type)
      -> GenerationResult

      Inserts the reports row, assembles the context, runs narrative
      generation, saves the draft, and returns with status
      'awaiting_review'. No files are written. The Managing Partner
      reviews and approves sections via reports/editor.py before files
      are produced.

  finalize_and_build(conn, report_id, output_dir)
      -> tuple[Path, Path]

      Called after all sections are approved. Finalizes the context,
      generates both .docx and .pdf files, updates the reports row with
      file paths, and logs the completion event.

Spec compliance:
  - Report record is created before any files are written (spec Module 5
    validation criterion).
  - ai_narrative_generated flag is set to True after generate_narratives()
    completes successfully.
  - Both report_record_created and report_files_generated events are
    written to audit_log.
  - The Managing Partner approves every narrative section before
    finalize_and_build() may proceed (enforced by finalize_report() in
    reports/editor.py).
"""

import json
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db.audit import write_audit_log
from reports.context import (
    AlertSummaryContext,
    DimensionContext,
    JournalEntryContext,
    MonthlyBriefContext,
    SignalReadingContext,
    SnapshotReportContext,
    SubCategoryContext,
    make_narrative_placeholder,
)
from reports.editor import finalize_report, save_draft
from reports.narrator import generate_narratives
from reports.builder import generate_report_files

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def _build_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


log = _build_logger("cpoi.reports.generator")

# ---------------------------------------------------------------------------
# Classification band descriptions (scoring spec Part 1)
# ---------------------------------------------------------------------------

_COMPOSITE_DESCRIPTIONS: dict[str, str] = {
    "Low Risk": (
        "Systems are functioning with adequate visibility and governance."
    ),
    "Moderate": (
        "Manageable gaps exist; require monitoring but not immediate action."
    ),
    "Elevated": (
        "Structural gaps are present; execution risk is increasing."
    ),
    "High Risk": (
        "Material operational gaps; financial exposure is probable without intervention."
    ),
    "Critical": (
        "Systemic failure conditions; financial exposure is active or imminent."
    ),
}

# Maps DB column suffixes to dimension display names and IDs.
_DIMENSION_META: list[tuple[str, str, str]] = [
    ("1", "Strategic Saturation",        "strategic_saturation"),
    ("2", "Governance Responsiveness",   "governance_responsiveness"),
    ("3", "Execution Visibility",        "execution_visibility"),
    ("4", "Reporting Integrity",         "reporting_integrity"),
    ("5", "Organizational Sustainability", "org_sustainability"),
]

_DIMENSION_DESCRIPTIONS: dict[str, str] = {
    "1": "The degree to which the organization's transformation portfolio exceeds its sustainable execution capacity.",
    "2": "The velocity, clarity, and accountability of governance and decision-making structures relative to transformation demands.",
    "3": "The degree to which leadership has accurate, timely, and structured visibility into what is actually happening across delivery programs.",
    "4": "The degree to which executive reporting accurately reflects operational conditions rather than curated or delayed information.",
    "5": "The organization's capacity to sustain current transformation demand without degrading its people, processes, or long-term execution capability.",
}

_SIGNAL_DISPLAY_NAMES: dict[str, str] = {
    "governance_latency_index":       "Governance Latency Index",
    "escalation_suppression_rate":    "Escalation Suppression Rate",
    "false_green_indicator":          "False Green Indicator",
    "reporting_divergence_score":     "Reporting Divergence Score",
    "priority_collision_index":       "Priority Collision Index",
    "platform_utilization_pressure":  "Platform Utilization Pressure",
    "initiative_saturation_ratio":    "Initiative Saturation Ratio",
    "dependency_fragility_score":     "Dependency Fragility Score",
    "headcount_stability_index":      "Headcount Stability Index",
    "reprioritization_frequency":     "Reprioritization Frequency",
    "reactive_work_ratio":            "Reactive Work Ratio",
}

# ---------------------------------------------------------------------------
# Report ID helper
# ---------------------------------------------------------------------------


def _make_report_id(period_date: str) -> str:
    """
    Generate a codified report identifier.

    Format: YYYYQ#-XXXXXX
      YYYY  -- four-digit year from period_date
      Q#    -- quarter number (1-4) derived from period_date month
      XXXXXX -- 6 cryptographically random hex characters (lowercase)

    Example: 2026Q2-4f9a31

    Args:
        period_date: ISO date string (YYYY-MM-DD) for the reporting period.

    Returns:
        str: Formatted report ID.
    """
    try:
        dt = datetime.strptime(period_date, "%Y-%m-%d")
        year = dt.year
        quarter = (dt.month - 1) // 3 + 1
    except ValueError:
        now = datetime.now(timezone.utc)
        year = now.year
        quarter = (now.month - 1) // 3 + 1
    suffix = secrets.token_hex(3)  # 3 bytes = 6 hex chars
    return f"{year}Q{quarter}-{suffix}"


# ---------------------------------------------------------------------------
# GenerationResult dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GenerationResult:
    """
    Output of generate_report_for_submission().

    Attributes:
        report_id:   UUID of the newly created reports row.
        client_id:   Client UUID.
        report_type: 'snapshot' or 'monthly_brief'.
        status:      'awaiting_review' after successful generation.
                     The Managing Partner must approve all narrative
                     sections before finalize_and_build() may proceed.
        section_count: Number of narrative sections saved to report_drafts,
                       each requiring Managing Partner approval.
    """

    report_id: str
    client_id: str
    report_type: str
    status: str
    section_count: int


# ---------------------------------------------------------------------------
# Database query helpers
# ---------------------------------------------------------------------------


def _fetch_submission(conn: Any, submission_id: str) -> dict[str, Any]:
    """
    Fetch intake_submission and client records for the given submission.

    Args:
        conn:          Open database connection.
        submission_id: UUID of the intake_submission.

    Returns:
        dict with all relevant fields from both tables.

    Raises:
        KeyError: if the submission does not exist.
    """
    cursor = conn.execute(
        """
        SELECT s.submission_id, s.client_id, s.reporting_period_start,
               s.reporting_period_end, s.submitted_at,
               c.client_name, c.engagement_type, c.engagement_start_date
        FROM intake_submissions s
        JOIN clients c ON c.client_id = s.client_id
        WHERE s.submission_id = ?
        """,
        (submission_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise KeyError(
            f"No intake_submission found for submission_id '{submission_id}'. "
            "The submission must be ingested before a report can be generated."
        )
    return dict(row)


def _fetch_oei_scores(conn: Any, submission_id: str) -> dict[str, Any]:
    """Fetch the oei_scores record for a submission."""
    cursor = conn.execute(
        """
        SELECT * FROM oei_scores WHERE submission_id = ?
        ORDER BY calculated_at DESC LIMIT 1
        """,
        (submission_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise KeyError(
            f"No oei_scores record found for submission_id '{submission_id}'. "
            "Scoring must complete before report generation."
        )
    return dict(row)


def _fetch_prior_scores(
    conn: Any, client_id: str, period_date: str
) -> dict[str, Any]:
    """Fetch the most recent oei_scores record before period_date."""
    cursor = conn.execute(
        """
        SELECT * FROM oei_scores
        WHERE client_id = ? AND period_date < ?
        ORDER BY period_date DESC LIMIT 1
        """,
        (client_id, period_date),
    )
    row = cursor.fetchone()
    return dict(row) if row else {}


def _fetch_all_period_scores(
    conn: Any, client_id: str
) -> list[dict[str, Any]]:
    """Fetch all oei_scores records for a client, oldest first."""
    cursor = conn.execute(
        """
        SELECT period_date, oei_composite_score
        FROM oei_scores
        WHERE client_id = ?
        ORDER BY period_date ASC
        """,
        (client_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def _fetch_signal_readings(
    conn: Any, submission_id: str
) -> list[dict[str, Any]]:
    """Fetch all signal_readings for a submission."""
    cursor = conn.execute(
        """
        SELECT signal_name, signal_value, threshold_value,
               threshold_breached, period_date
        FROM signal_readings
        WHERE submission_id = ?
        ORDER BY signal_name
        """,
        (submission_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def _fetch_active_alerts(
    conn: Any, client_id: str
) -> list[dict[str, Any]]:
    """Fetch unacknowledged alerts for a client."""
    cursor = conn.execute(
        """
        SELECT alert_id, signal_name, signal_value, threshold_value,
               alert_severity, alert_message, triggered_at, acknowledged
        FROM alerts
        WHERE client_id = ? AND acknowledged = 0
        ORDER BY triggered_at DESC
        """,
        (client_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def _fetch_journal_entries_for_context(
    conn: Any, client_id: str, submission_id: str
) -> list[dict[str, Any]]:
    """
    Fetch High/Medium materiality journal entries marked for surfacing.
    """
    cursor = conn.execute(
        """
        SELECT entry_date, entry_type, program_reference,
               intelligence_note, materiality
        FROM engagement_journal
        WHERE client_id = ?
          AND materiality IN ('High', 'Medium')
          AND surfaced_in_report IN ('Yes', 'Partial')
          AND (submission_id = ? OR submission_id IS NULL)
        ORDER BY materiality DESC, entry_date DESC
        """,
        (client_id, submission_id),
    )
    return [dict(row) for row in cursor.fetchall()]


def _fetch_sub_categories_for_submission(
    conn: Any, submission_id: str
) -> dict[str, list[dict[str, Any]]]:
    """
    Attempt to fetch sub-category scores from score_overrides context or
    return empty lists. Sub-category impact indicators are derived from
    the scoring engine results stored in the oei_scores record; for the
    report context we synthesise them from signal readings since
    sub-category detail is held in memory during scoring and not
    separately persisted in v1.

    Returns a dict keyed by dimension_id ('1'-'5') with lists of dicts.
    """
    return {dim_id: [] for dim_id, _, _ in _DIMENSION_META}


# ---------------------------------------------------------------------------
# Context assembly helpers
# ---------------------------------------------------------------------------


def _classify_score(score: int) -> str:
    """Map a 0-100 score to its risk classification label."""
    if score <= 20:
        return "Low Risk"
    if score <= 40:
        return "Moderate"
    if score <= 60:
        return "Elevated"
    if score <= 80:
        return "High Risk"
    return "Critical"


def _build_dimension_contexts(
    scores: dict[str, Any],
    prior_scores: dict[str, Any],
) -> list[DimensionContext]:
    """
    Build the list of DimensionContext objects from oei_scores rows.

    Sub-category impact indicators are not persisted separately in v1;
    the dimension context includes an empty sub_categories list which
    the template renders correctly (no sub-category table shown).

    Args:
        scores:       Current period oei_scores dict.
        prior_scores: Prior period oei_scores dict, or empty dict.

    Returns:
        List of five DimensionContext objects.
    """
    dims: list[DimensionContext] = []
    for dim_id, dim_name, col_prefix in _DIMENSION_META:
        score = scores.get(f"{col_prefix}_score", 0)
        classification = scores.get(f"{col_prefix}_class", _classify_score(score))
        prior_score = prior_scores.get(f"{col_prefix}_score") if prior_scores else None
        delta = (score - prior_score) if prior_score is not None else None

        dims.append(DimensionContext(
            dimension_id=dim_id,
            name=dim_name,
            score=int(score),
            prior_score=int(prior_score) if prior_score is not None else None,
            delta=int(delta) if delta is not None else None,
            classification=classification,
            description=_DIMENSION_DESCRIPTIONS.get(dim_id, ""),
            sub_categories=[],
        ))
    return dims


def _build_signal_reading_contexts(
    signal_rows: list[dict[str, Any]],
) -> list[SignalReadingContext]:
    """
    Build SignalReadingContext list from signal_readings rows.

    Args:
        signal_rows: List of signal_readings dicts from the database.

    Returns:
        List of SignalReadingContext objects.
    """
    result: list[SignalReadingContext] = []
    for sig in signal_rows:
        name = sig["signal_name"]
        value = float(sig["signal_value"])
        threshold = float(sig["threshold_value"])
        breached = bool(sig["threshold_breached"])

        if breached:
            excess = value - threshold
            if excess >= threshold * 0.5:
                severity = "critical"
            elif excess >= threshold * 0.2:
                severity = "elevated"
            else:
                severity = "watch"
        else:
            severity = "clear"

        result.append(SignalReadingContext(
            signal_name=name,
            display_name=_SIGNAL_DISPLAY_NAMES.get(name, name.replace("_", " ").title()),
            signal_value=value,
            threshold_value=threshold,
            threshold_breached=breached,
            severity=severity,
        ))
    return result


def _build_alert_contexts(
    alert_rows: list[dict[str, Any]],
) -> list[AlertSummaryContext]:
    """Build AlertSummaryContext list from alerts rows."""
    return [
        AlertSummaryContext(
            signal_name=a["signal_name"],
            display_name=_SIGNAL_DISPLAY_NAMES.get(
                a["signal_name"],
                a["signal_name"].replace("_", " ").title(),
            ),
            severity=a["alert_severity"],
            alert_message=a.get("alert_message") or "",
            triggered_at=a["triggered_at"],
            acknowledged=bool(a["acknowledged"]),
        )
        for a in alert_rows
    ]


def _build_journal_contexts(
    journal_rows: list[dict[str, Any]],
) -> list[JournalEntryContext]:
    """Build JournalEntryContext list from engagement_journal rows."""
    return [
        JournalEntryContext(
            entry_date=j["entry_date"],
            entry_type=j["entry_type"],
            program_reference=j.get("program_reference", "General"),
            intelligence_note=j["intelligence_note"],
            materiality=j["materiality"],
        )
        for j in journal_rows
    ]


def _period_label(period_start: str, period_end: str) -> str:
    """
    Derive a human-readable period label from ISO date strings.

    Uses the end date's month and year: '2026-04-30' -> 'April 2026'.

    Args:
        period_start: ISO date string.
        period_end:   ISO date string.

    Returns:
        str: e.g. 'April 2026'.
    """
    try:
        dt = datetime.strptime(period_end, "%Y-%m-%d")
        return dt.strftime("%B %Y")
    except ValueError:
        return f"{period_start} to {period_end}"


# ---------------------------------------------------------------------------
# Database write helpers
# ---------------------------------------------------------------------------


def _insert_report_record(
    conn: Any,
    report_id: str,
    client_id: str,
    report_type: str,
    period_date: str,
) -> None:
    """
    Insert a reports row before files are written.

    The spec validation criterion "Report record created before files
    written" is satisfied by calling this before any file generation
    begins.

    Args:
        conn:        Open database connection.
        report_id:   UUID for the new reports row.
        client_id:   FK to clients.
        report_type: 'snapshot' or 'monthly_brief'.
        period_date: ISO date string for the reporting period.
    """
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO reports
            (report_id, client_id, report_type, period_date,
             generated_at, ai_narrative_generated, delivered)
        VALUES (?, ?, ?, ?, ?, 0, 0)
        """,
        (report_id, client_id, report_type, period_date, now),
    )


def _update_report_narrative_flag(conn: Any, report_id: str) -> None:
    """Set ai_narrative_generated = TRUE on the reports row."""
    conn.execute(
        "UPDATE reports SET ai_narrative_generated = 1 WHERE report_id = ?",
        (report_id,),
    )


def _update_report_file_path(
    conn: Any, report_id: str, docx_path: Path, pdf_path: Path
) -> None:
    """
    Update the reports row with the docx file path after generation.

    Stores the docx path as the primary file_path; the pdf path is
    recorded in the audit log metadata.
    """
    conn.execute(
        "UPDATE reports SET file_path = ? WHERE report_id = ?",
        (str(docx_path), report_id),
    )


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def generate_report_for_submission(
    conn: Any,
    submission_id: str,
    report_type: str,
) -> GenerationResult:
    """
    Run the first half of the Module 5 pipeline for one submission.

    Creates the reports row, assembles the full context dict, runs
    narrative generation, saves the draft for Managing Partner review,
    and returns a GenerationResult. No files are written. The Managing
    Partner must approve all sections via reports/editor.approve_section()
    before finalize_and_build() may proceed.

    The caller must commit the database transaction after this function
    returns. This function does not call conn.commit().

    Args:
        conn:          Open, authenticated database connection.
        submission_id: UUID of the intake_submission to report on.
                       Must have been scored (oei_scores row must exist).
        report_type:   'snapshot' or 'monthly_brief'.

    Returns:
        GenerationResult with report_id, client_id, report_type, status,
        and section_count.

    Raises:
        ValueError: if report_type is not recognized.
        KeyError:   if submission, oei_scores, or client records are absent.
        Exception:  any database or narrative generation error propagates.
    """
    if report_type not in ("snapshot", "monthly_brief"):
        raise ValueError(
            f"report_type must be 'snapshot' or 'monthly_brief', "
            f"got '{report_type}'."
        )

    # ------------------------------------------------------------------
    # Step 1: Fetch all data needed to build the context.
    # ------------------------------------------------------------------
    submission = _fetch_submission(conn, submission_id)
    client_id: str = submission["client_id"]
    scores = _fetch_oei_scores(conn, submission_id)
    period_date: str = scores["period_date"]

    report_id = _make_report_id(period_date)
    log.info(
        "Starting report generation: report_id=%s submission_id=%s type=%s",
        report_id,
        submission_id,
        report_type,
    )
    prior_scores = _fetch_prior_scores(conn, client_id, period_date)
    all_period_scores = _fetch_all_period_scores(conn, client_id)
    signal_rows = _fetch_signal_readings(conn, submission_id)
    alert_rows = _fetch_active_alerts(conn, client_id)
    journal_rows = _fetch_journal_entries_for_context(conn, client_id, submission_id)

    # ------------------------------------------------------------------
    # Step 2: Insert the reports row before any file generation.
    # ------------------------------------------------------------------
    _insert_report_record(conn, report_id, client_id, report_type, period_date)
    log.info("reports row inserted: report_id=%s", report_id)

    # ------------------------------------------------------------------
    # Step 3: Build context sub-structures.
    # ------------------------------------------------------------------
    composite = int(scores["oei_composite_score"])
    composite_class: str = scores["composite_class"]
    composite_desc = _COMPOSITE_DESCRIPTIONS.get(composite_class, "")

    prior_composite = (
        int(prior_scores["oei_composite_score"]) if prior_scores else None
    )
    score_delta = (composite - prior_composite) if prior_composite is not None else None
    if score_delta is not None:
        score_direction = (
            "worsened" if score_delta > 0
            else ("improved" if score_delta < 0 else "unchanged")
        )
    else:
        score_direction = None

    dimensions = _build_dimension_contexts(scores, prior_scores)
    signal_reading_ctxs = _build_signal_reading_contexts(signal_rows)
    alert_ctxs = _build_alert_contexts(alert_rows)
    journal_ctxs = _build_journal_contexts(journal_rows)

    has_override = False
    override_footnote = ""

    # Check for active overrides on this submission.
    override_cursor = conn.execute(
        "SELECT COUNT(*) FROM score_overrides WHERE submission_id = ? AND is_active = 1",
        (submission_id,),
    )
    override_count = override_cursor.fetchone()[0]
    if override_count > 0:
        has_override = True
        override_footnote = (
            "This score incorporates qualitative intelligence gathered during "
            "the engagement period in addition to submitted operational data."
        )

    # ------------------------------------------------------------------
    # Step 4: Assemble base_context with NARRATIVE_PLACEHOLDER__ sentinels.
    # ------------------------------------------------------------------
    period_start: str = submission["reporting_period_start"]
    period_end: str = submission["reporting_period_end"]
    report_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    generated_at = datetime.now(timezone.utc).isoformat()

    if report_type == "snapshot":
        base_context: dict[str, Any] = {
            "report_id":                  report_id,
            "report_type":                report_type,
            "report_date":                report_date,
            "generated_at":               generated_at,
            "client_name":                submission["client_name"],
            "sponsor_name":               submission["client_name"],
            "engagement_type":            submission["engagement_type"],
            "engagement_start_date":      submission.get("engagement_start_date", ""),
            "submission_id":              submission_id,
            "reporting_period_start":     period_start,
            "reporting_period_end":       period_end,
            "submitted_at":               submission["submitted_at"],
            "oei_composite_score":        composite,
            "composite_class":            composite_class,
            "composite_description":      composite_desc,
            "prior_composite_score":      prior_composite,
            "score_delta":                score_delta,
            "score_direction":            score_direction,
            "dimensions":                 dimensions,
            "signal_readings":            signal_reading_ctxs,
            "has_override":               has_override,
            "override_footnote":          override_footnote,
            "narrative_executive_summary": make_narrative_placeholder("executive_summary"),
            "narrative_key_findings":      make_narrative_placeholder("key_findings"),
            "narrative_recommended_focus": make_narrative_placeholder("recommended_focus"),
            "qualitative_notes":           journal_ctxs,
        }
    else:
        breached = [s for s in signal_reading_ctxs if s["threshold_breached"]]
        cleared: list[SignalReadingContext] = []

        from reports.chart import generate_trajectory_chart
        chart_b64 = generate_trajectory_chart(all_period_scores)

        prior_period_label = (
            _period_label(
                prior_scores.get("period_date", ""),
                prior_scores.get("period_date", ""),
            )
            if prior_scores
            else None
        )

        base_context = {
            "report_id":                  report_id,
            "report_type":                report_type,
            "report_date":                report_date,
            "generated_at":               generated_at,
            "client_name":                submission["client_name"],
            "sponsor_name":               submission["client_name"],
            "engagement_type":            submission["engagement_type"],
            "engagement_start_date":      submission.get("engagement_start_date", ""),
            "period_label":               _period_label(period_start, period_end),
            "prior_period_label":         prior_period_label,
            "submission_id":              submission_id,
            "reporting_period_start":     period_start,
            "reporting_period_end":       period_end,
            "submitted_at":               submission["submitted_at"],
            "oei_composite_score":        composite,
            "composite_class":            composite_class,
            "composite_description":      composite_desc,
            "prior_composite_score":      prior_composite,
            "score_delta":                score_delta,
            "score_direction":            score_direction,
            "dimensions":                 dimensions,
            "trajectory_chart_b64":       chart_b64,
            "signal_readings":            signal_reading_ctxs,
            "breached_signals":           breached,
            "cleared_signals":            cleared,
            "active_alerts":              alert_ctxs,
            "has_override":               has_override,
            "override_footnote":          override_footnote,
            "narrative_executive_summary":      make_narrative_placeholder("executive_summary"),
            "narrative_dimension_deep_dives":   {
                "1": make_narrative_placeholder("dim_deep_dive_1"),
                "2": make_narrative_placeholder("dim_deep_dive_2"),
                "3": make_narrative_placeholder("dim_deep_dive_3"),
                "4": make_narrative_placeholder("dim_deep_dive_4"),
                "5": make_narrative_placeholder("dim_deep_dive_5"),
            },
            "narrative_forward_focus":    make_narrative_placeholder("forward_focus"),
            "qualitative_notes":          journal_ctxs,
        }

    # ------------------------------------------------------------------
    # Step 5: Run narrative generation (fills placeholders).
    # ------------------------------------------------------------------
    completed_context = generate_narratives(
        conn=conn,
        report_type=report_type,
        submission_id=submission_id,
        client_id=client_id,
        base_context=base_context,
        report_id=report_id,
    )

    # ------------------------------------------------------------------
    # Step 6: Save draft sections for Managing Partner review.
    # ------------------------------------------------------------------
    save_draft(conn, report_id, completed_context, report_type)

    # ------------------------------------------------------------------
    # Step 7: Update ai_narrative_generated flag.
    # ------------------------------------------------------------------
    _update_report_narrative_flag(conn, report_id)

    # ------------------------------------------------------------------
    # Step 8: Audit log entry.
    # ------------------------------------------------------------------
    write_audit_log(
        conn=conn,
        event_type="report_record_created",
        entity_type="report",
        entity_id=report_id,
        description=(
            f"Report record created for submission '{submission_id}'. "
            f"Type: {report_type}. "
            f"AI narrative generated. Awaiting Managing Partner review."
        ),
        performed_by="system",
        metadata={
            "report_id":     report_id,
            "submission_id": submission_id,
            "client_id":     client_id,
            "report_type":   report_type,
            "period_date":   period_date,
            "composite":     composite,
            "override":      has_override,
        },
    )

    # Count sections saved to report_drafts.
    section_count_cursor = conn.execute(
        "SELECT COUNT(*) FROM report_drafts WHERE report_id = ?",
        (report_id,),
    )
    section_count = section_count_cursor.fetchone()[0]

    log.info(
        "Report generation complete: report_id=%s sections=%d status=awaiting_review",
        report_id,
        section_count,
    )

    return GenerationResult(
        report_id=report_id,
        client_id=client_id,
        report_type=report_type,
        status="awaiting_review",
        section_count=section_count,
    )


def finalize_and_build(
    conn: Any,
    report_id: str,
    output_dir: Path,
) -> tuple[Path, Path]:
    """
    Finalize a report and generate the .docx and .pdf files.

    Called after the Managing Partner has approved all narrative sections
    via reports/editor.approve_section(). Calls finalize_report() which
    raises RuntimeError if any section is still pending review.

    Writes both file formats to output_dir, updates the reports row with
    the docx file path, and logs the completion event.

    The caller must commit the database transaction after this function
    returns. This function does not call conn.commit().

    Args:
        conn:       Open, authenticated database connection.
        report_id:  UUID of the reports row created by
                    generate_report_for_submission().
        output_dir: Directory where .docx and .pdf files will be written.
                    Must exist.

    Returns:
        tuple[Path, Path]: (docx_path, pdf_path).

    Raises:
        RuntimeError: if any narrative section is still pending_review.
        KeyError:     if the report_id is not found in the reports table.
        NotADirectoryError: if output_dir does not exist.
        Exception:    any file generation error propagates.
    """
    log.info("Finalizing report: report_id=%s output_dir=%s", report_id, output_dir)

    # Fetch the report record to get type and client.
    cursor = conn.execute(
        "SELECT report_type, client_id, period_date FROM reports WHERE report_id = ?",
        (report_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise KeyError(
            f"No reports record found for report_id '{report_id}'. "
            "Call generate_report_for_submission() first."
        )

    report_type: str = row["report_type"]
    client_id: str = row["client_id"]

    # Fetch the context that was assembled during generation. We need to
    # reconstruct the base_context so finalize_report() can merge approved
    # text back into it. We do this by reading the current draft texts
    # directly -- they are authoritative after MP edits.
    from reports.editor import get_draft_for_review
    drafts = get_draft_for_review(conn, report_id)

    # Build a minimal base_context from the draft texts so finalize_report
    # has something to merge into. All fixed fields are re-fetched from DB.
    # Retrieve submission_id via the report's client and period.
    sub_cursor = conn.execute(
        """
        SELECT s.submission_id
        FROM intake_submissions s
        JOIN oei_scores o ON o.submission_id = s.submission_id
        WHERE o.client_id = ? AND o.period_date = ?
        ORDER BY o.calculated_at DESC LIMIT 1
        """,
        (client_id, row["period_date"]),
    )
    sub_row = sub_cursor.fetchone()
    if not sub_row:
        raise KeyError(
            f"Cannot find submission for report_id '{report_id}'. "
            "Database state may be inconsistent."
        )
    submission_id: str = sub_row["submission_id"]

    submission = _fetch_submission(conn, submission_id)
    scores = _fetch_oei_scores(conn, submission_id)
    prior_scores = _fetch_prior_scores(conn, client_id, row["period_date"])
    all_period_scores = _fetch_all_period_scores(conn, client_id)
    signal_rows = _fetch_signal_readings(conn, submission_id)
    alert_rows = _fetch_active_alerts(conn, client_id)
    journal_rows = _fetch_journal_entries_for_context(conn, client_id, submission_id)

    composite = int(scores["oei_composite_score"])
    composite_class: str = scores["composite_class"]
    composite_desc = _COMPOSITE_DESCRIPTIONS.get(composite_class, "")
    prior_composite = int(prior_scores["oei_composite_score"]) if prior_scores else None
    score_delta = (composite - prior_composite) if prior_composite is not None else None
    score_direction = None
    if score_delta is not None:
        score_direction = (
            "worsened" if score_delta > 0
            else ("improved" if score_delta < 0 else "unchanged")
        )

    dimensions = _build_dimension_contexts(scores, prior_scores)
    signal_reading_ctxs = _build_signal_reading_contexts(signal_rows)
    alert_ctxs = _build_alert_contexts(alert_rows)
    journal_ctxs = _build_journal_contexts(journal_rows)

    override_cursor = conn.execute(
        "SELECT COUNT(*) FROM score_overrides WHERE submission_id = ? AND is_active = 1",
        (submission_id,),
    )
    override_count = override_cursor.fetchone()[0]
    has_override = override_count > 0
    override_footnote = (
        "This score incorporates qualitative intelligence gathered during "
        "the engagement period in addition to submitted operational data."
        if has_override else ""
    )

    period_start: str = submission["reporting_period_start"]
    period_end: str = submission["reporting_period_end"]
    report_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    generated_at = datetime.now(timezone.utc).isoformat()

    base_context: dict[str, Any] = {
        "report_id":              report_id,
        "report_type":            report_type,
        "report_date":            report_date,
        "generated_at":           generated_at,
        "client_name":            submission["client_name"],
        "sponsor_name":           submission["client_name"],
        "engagement_type":        submission["engagement_type"],
        "engagement_start_date":  submission.get("engagement_start_date", ""),
        "submission_id":          submission_id,
        "reporting_period_start": period_start,
        "reporting_period_end":   period_end,
        "submitted_at":           submission["submitted_at"],
        "oei_composite_score":    composite,
        "composite_class":        composite_class,
        "composite_description":  composite_desc,
        "prior_composite_score":  prior_composite,
        "score_delta":            score_delta,
        "score_direction":        score_direction,
        "dimensions":             dimensions,
        "signal_readings":        signal_reading_ctxs,
        "has_override":           has_override,
        "override_footnote":      override_footnote,
        "qualitative_notes":      journal_ctxs,
    }

    if report_type == "monthly_brief":
        from reports.chart import generate_trajectory_chart
        chart_b64 = generate_trajectory_chart(all_period_scores)
        prior_period_label = (
            _period_label(
                prior_scores.get("period_date", ""),
                prior_scores.get("period_date", ""),
            )
            if prior_scores else None
        )
        base_context.update({
            "period_label":           _period_label(period_start, period_end),
            "prior_period_label":     prior_period_label,
            "trajectory_chart_b64":   chart_b64,
            "breached_signals":       [s for s in signal_reading_ctxs if s["threshold_breached"]],
            "cleared_signals":        [],
            "active_alerts":          alert_ctxs,
            "narrative_dimension_deep_dives": {"1": "", "2": "", "3": "", "4": "", "5": ""},
        })

    # Seed placeholder sentinels so finalize_report can overwrite them.
    for draft in drafts:
        if draft.section_name.startswith("narrative_"):
            base_context[draft.section_name] = make_narrative_placeholder(
                draft.section_name
            )

    # finalize_report() enforces all-approved, merges text, validates.
    final_context = finalize_report(conn, report_id, base_context, report_type)

    # Generate files.
    docx_path, pdf_path = generate_report_files(final_context, report_type, output_dir)

    # Update reports row.
    _update_report_file_path(conn, report_id, docx_path, pdf_path)

    # Audit log.
    write_audit_log(
        conn=conn,
        event_type="report_files_generated",
        entity_type="report",
        entity_id=report_id,
        description=(
            f"Report files generated for report_id '{report_id}'. "
            f"docx: {docx_path.name}. pdf: {pdf_path.name}."
        ),
        performed_by="system",
        metadata={
            "report_id":   report_id,
            "report_type": report_type,
            "docx_path":   str(docx_path),
            "pdf_path":    str(pdf_path),
            "docx_bytes":  docx_path.stat().st_size,
            "pdf_bytes":   pdf_path.stat().st_size,
        },
    )

    log.info(
        "Files generated: report_id=%s docx=%s pdf=%s",
        report_id,
        docx_path.name,
        pdf_path.name,
    )

    return docx_path, pdf_path
