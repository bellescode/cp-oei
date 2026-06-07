"""
scoring/engine.py
CPOI Platform — Scoring Engine

The single orchestration entry point for the OEI scoring pipeline.

Responsibility chain:
  1. Accept validated intake DataFrames and submission metadata.
  2. Compute all 21 sub-category scores via sub_categories.py.
  3. Compute all 5 dimension scores and the OEI composite via dimensions.py.
  4. Optionally load and apply any active Managing Partner overrides via
     overrides.py (enabled by default).
  5. Persist the final scored result to the oei_scores table.
  6. Write a score_calculated audit log entry.
  7. Return an EngineResult to the caller.

Score storage precision:
  All float scores are rounded to the nearest integer before database storage.
  The oei_scores table uses INTEGER columns (per schema.sql). The full-precision
  floats are preserved in the returned EngineResult.OEIScoreResult for any
  subsequent in-memory processing (report generation, alert evaluation).

Transaction ownership:
  This module never calls conn.commit(). The caller owns the transaction and
  must commit after run_scoring() returns. This allows the caller to include
  the oei_scores INSERT and audit log INSERT within a single atomic transaction
  with the intake ingestion workflow.

Public interface:
  run_scoring(
      conn, submission_id, client_id, period_date,
      dataframes, metadata,
      apply_active_overrides=True
  ) -> EngineResult
"""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

import pandas as pd

from db.audit import write_audit_log
from scoring.constants import VERSION
from scoring.dimensions import OEIScoreResult, compute_oei_score
from scoring.overrides import apply_overrides, get_active_overrides
from scoring.signals import compute_and_write_signal_readings
from scoring.sub_categories import compute_all_sub_categories

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class _JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

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


log = _build_logger("cpoi.scoring.engine")

# ---------------------------------------------------------------------------
# Column name constants for oei_scores table (match schema.sql exactly)
# ---------------------------------------------------------------------------

_DIM_SCORE_COLS: Final[dict[str, str]] = {
    "1": "strategic_saturation_score",
    "2": "governance_responsiveness_score",
    "3": "execution_visibility_score",
    "4": "reporting_integrity_score",
    "5": "org_sustainability_score",
}

_DIM_CLASS_COLS: Final[dict[str, str]] = {
    "1": "strategic_saturation_class",
    "2": "governance_responsiveness_class",
    "3": "execution_visibility_class",
    "4": "reporting_integrity_class",
    "5": "org_sustainability_class",
}

# ---------------------------------------------------------------------------
# EngineResult dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EngineResult:
    """
    Output of run_scoring(). Contains the complete scored result plus
    provenance metadata.

    Attributes:
        score_id:           UUID of the oei_scores row written to the database.
        submission_id:      The intake submission that was scored.
        client_id:          Client this submission belongs to.
        period_date:        Reporting period date stored in oei_scores.
        oei_result:         Full OEIScoreResult with float-precision scores,
                            all five DimensionResult objects, and all 21
                            SubCategoryResult objects.
        overrides_applied:  True if at least one active override was found
                            and applied. False if apply_active_overrides=False
                            or no active overrides existed.
        override_count:     Number of active override records applied.
                            Zero when overrides_applied is False.
        scoring_version:    Value of scoring/constants.py VERSION at the time
                            this score was computed.
        calculated_at:      ISO 8601 UTC timestamp from the OEIScoreResult.
    """

    score_id: str
    submission_id: str
    client_id: str
    period_date: str
    oei_result: OEIScoreResult
    overrides_applied: bool
    override_count: int
    scoring_version: str
    calculated_at: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _round_score(score: float) -> int:
    """
    Round a float score to the nearest integer for database storage.

    Uses standard half-up rounding. Clamps to [0, 100] to prevent edge-case
    float arithmetic from producing a value outside the valid score range.

    Args:
        score: Float score in [0.0, 100.0].

    Returns:
        int: Rounded score in [0, 100].
    """
    return max(0, min(100, round(score)))


def _write_oei_scores_row(
    conn: Any,
    score_id: str,
    client_id: str,
    submission_id: str,
    period_date: str,
    oei_result: OEIScoreResult,
) -> None:
    """
    Insert one row into the oei_scores table.

    All five dimension scores, the composite, and all classification labels
    are written. Scores are rounded to integer before storage per the schema.

    Args:
        conn:          Open, authenticated database connection.
        score_id:      UUID for this oei_scores record.
        client_id:     FK to clients.client_id.
        submission_id: FK to intake_submissions.submission_id.
        period_date:   ISO date string (YYYY-MM-DD) for the reporting period.
        oei_result:    Full OEIScoreResult to be persisted.

    Raises:
        Exception: any database error propagates to the caller.
    """
    dim = oei_result.dimension_results

    conn.execute(
        """
        INSERT INTO oei_scores (
            score_id,
            client_id,
            submission_id,
            period_date,
            strategic_saturation_score,
            governance_responsiveness_score,
            execution_visibility_score,
            reporting_integrity_score,
            org_sustainability_score,
            oei_composite_score,
            strategic_saturation_class,
            governance_responsiveness_class,
            execution_visibility_class,
            reporting_integrity_class,
            org_sustainability_class,
            composite_class,
            calculated_at
        ) VALUES (
            ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?,
            ?, ?, ?, ?, ?,
            ?,
            ?
        )
        """,
        (
            score_id,
            client_id,
            submission_id,
            period_date,
            # Dimension scores — rounded to integer.
            _round_score(dim["1"].score),
            _round_score(dim["2"].score),
            _round_score(dim["3"].score),
            _round_score(dim["4"].score),
            _round_score(dim["5"].score),
            # Composite — rounded to integer.
            _round_score(oei_result.composite_score),
            # Classification labels.
            dim["1"].classification,
            dim["2"].classification,
            dim["3"].classification,
            dim["4"].classification,
            dim["5"].classification,
            oei_result.composite_classification,
            oei_result.calculated_at,
        ),
    )


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def run_scoring(
    conn: Any,
    submission_id: str,
    client_id: str,
    period_date: str,
    dataframes: dict[str, pd.DataFrame],
    metadata: dict[str, str],
    apply_active_overrides: bool = True,
) -> EngineResult:
    """
    Run the full OEI scoring pipeline for one intake submission.

    Orchestrates sub-category computation, dimension aggregation, composite
    calculation, optional override application, database persistence, and
    audit logging in a single call.

    The caller must commit the database transaction after this function
    returns. This function does not call conn.commit().

    Args:
        conn:                  Open, authenticated database connection.
        submission_id:         UUID of the intake_submission being scored.
                               Must already exist in intake_submissions.
        client_id:             UUID of the client this submission belongs to.
                               Must already exist in clients.
        period_date:           Reporting period date as an ISO date string
                               (YYYY-MM-DD). Stored in oei_scores.period_date.
        dataframes:            Dict of validated DataFrames keyed by tab name,
                               as produced by intake/ingestor.py. Required keys:
                               INITIATIVES, ESCALATIONS, DEPENDENCIES,
                               RESOURCE_UTILIZATION, GOVERNANCE_EVENTS,
                               REPORTING_VARIANCE, HEADCOUNT_SIGNALS.
        metadata:              Dict of submission metadata as produced by
                               intake/ingestor.py. Required keys:
                               reporting_period_start, reporting_period_end,
                               client_id, submission_id.
        apply_active_overrides: When True (default), load any active Managing
                               Partner overrides from score_overrides and apply
                               them before storing the result. When False,
                               the calculated scores are stored as-is.

    Returns:
        EngineResult: Contains the full OEIScoreResult (float precision),
                      the score_id written to oei_scores, override metadata,
                      and the scoring version applied.

    Raises:
        KeyError:  if a required DataFrame tab or metadata key is absent.
                   This indicates the caller did not pass the full output of
                   ingestor.py.
        Exception: any database error propagates without wrapping so the
                   caller's transaction handling is not disrupted.
    """
    log.info(
        "Scoring started: submission_id=%s, client_id=%s, period_date=%s, "
        "apply_overrides=%s, scoring_version=%s",
        submission_id, client_id, period_date, apply_active_overrides, VERSION,
    )

    # ------------------------------------------------------------------
    # Step 1: Compute and persist the 11 raw signals (Module 2).
    # signal_readings rows are written before sub-category scoring so the
    # alert detector and dashboard signal display have values to query.
    # ------------------------------------------------------------------
    compute_and_write_signal_readings(
        conn=conn,
        submission_id=submission_id,
        client_id=client_id,
        period_date=period_date,
        dataframes=dataframes,
    )

    log.info("Signal readings written for submission_id=%s", submission_id)

    # ------------------------------------------------------------------
    # Step 2: Compute all 21 sub-category scores.
    # ------------------------------------------------------------------
    sub_category_results = compute_all_sub_categories(dataframes, metadata)

    log.info(
        "Sub-category computation complete: %d results",
        len(sub_category_results),
    )

    # ------------------------------------------------------------------
    # Step 3: Aggregate sub-category scores into dimension and composite.
    # ------------------------------------------------------------------
    raw_oei_result: OEIScoreResult = compute_oei_score(sub_category_results)

    log.info(
        "Raw OEI composite=%.2f (%s)",
        raw_oei_result.composite_score,
        raw_oei_result.composite_classification,
    )

    # ------------------------------------------------------------------
    # Step 3: Apply active Managing Partner overrides (if enabled).
    # ------------------------------------------------------------------
    overrides_applied: bool = False
    override_count: int = 0

    if apply_active_overrides:
        active_overrides = get_active_overrides(conn, submission_id)
        if active_overrides:
            oei_result = apply_overrides(raw_oei_result, active_overrides)
            overrides_applied = True
            override_count = len(active_overrides)
            log.info(
                "Applied %d override(s): composite adjusted %.2f -> %.2f",
                override_count,
                raw_oei_result.composite_score,
                oei_result.composite_score,
            )
        else:
            oei_result = raw_oei_result
            log.info("No active overrides found for submission %s", submission_id)
    else:
        oei_result = raw_oei_result
        log.info("Override application skipped (apply_active_overrides=False)")

    # ------------------------------------------------------------------
    # Step 4: Persist the final scored result to oei_scores.
    # ------------------------------------------------------------------
    score_id = str(uuid.uuid4())
    _write_oei_scores_row(conn, score_id, client_id, submission_id, period_date, oei_result)

    log.info(
        "oei_scores row written: score_id=%s, composite=%d",
        score_id,
        _round_score(oei_result.composite_score),
    )

    # ------------------------------------------------------------------
    # Step 5: Write audit log entry.
    # ------------------------------------------------------------------
    missing_count = sum(
        1 for r in oei_result.sub_category_results.values()
        if r.is_missing_data
    )
    non_computable_count = sum(
        1 for r in oei_result.sub_category_results.values()
        if r.is_non_computable
    )

    write_audit_log(
        conn=conn,
        event_type="score_calculated",
        entity_type="oei_score",
        entity_id=score_id,
        description=(
            f"OEI score calculated for submission {submission_id}. "
            f"Composite score: {_round_score(oei_result.composite_score)} "
            f"({oei_result.composite_classification}). "
            f"Overrides applied: {override_count}."
        ),
        performed_by="system",
        metadata={
            "submission_id":          submission_id,
            "client_id":              client_id,
            "period_date":            period_date,
            "scoring_version":        VERSION,
            "composite_score_raw":    oei_result.composite_score,
            "composite_score_stored": _round_score(oei_result.composite_score),
            "composite_class":        oei_result.composite_classification,
            "dimension_scores": {
                dim_id: _round_score(dr.score)
                for dim_id, dr in oei_result.dimension_results.items()
            },
            "dimension_classes": {
                dim_id: dr.classification
                for dim_id, dr in oei_result.dimension_results.items()
            },
            "overrides_applied":      overrides_applied,
            "override_count":         override_count,
            "missing_data_count":     missing_count,
            "non_computable_count":   non_computable_count,
        },
    )

    log.info(
        "Scoring complete: score_id=%s, submission_id=%s, "
        "composite=%d (%s), overrides=%d, missing=%d, non_computable=%d",
        score_id, submission_id,
        _round_score(oei_result.composite_score),
        oei_result.composite_classification,
        override_count, missing_count, non_computable_count,
    )

    return EngineResult(
        score_id=score_id,
        submission_id=submission_id,
        client_id=client_id,
        period_date=period_date,
        oei_result=oei_result,
        overrides_applied=overrides_applied,
        override_count=override_count,
        scoring_version=VERSION,
        calculated_at=oei_result.calculated_at,
    )
