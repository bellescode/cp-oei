"""
scoring/signals.py
CPOI Platform -- Module 2: Raw Signal Computation and Persistence

Computes the 11 operational signals defined in the SDLC spec Part 4 from
validated intake DataFrames and writes one signal_readings row per signal.

This module fills the Module 2 gap: the scoring engine (engine.py) operates
on sub-category scores derived from the same data, but the signal_readings
table must be populated separately so that the alert detector (alerts/detector.py)
and the dashboard signal display have raw signal values to query.

Signal computation follows the spec exactly:
  Signal formulas: cpoi-sdlc-spec.md Part 4
  Thresholds:      alerts/detector.py _SIGNAL_SEVERITY_THRESHOLDS (single source)

Threshold source: alerts/detector.py _SIGNAL_SEVERITY_THRESHOLDS (watch level).
The watch threshold is the lowest breach level; storing it in signal_readings
ensures the alert detector can determine breach status from threshold_breached
alone without re-implementing the threshold map.

Public interface:
    compute_and_write_signal_readings(
        conn, submission_id, client_id, period_date, dataframes
    ) -> list[str]

    Returns list of reading_ids written. Caller owns the transaction and
    must call conn.commit() after this function returns.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Final

import pandas as pd

from db.audit import write_audit_log

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _build_logger(name: str) -> logging.Logger:
    import json

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

    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


log = _build_logger("cpoi.scoring.signals")

# ---------------------------------------------------------------------------
# Watch-level thresholds (lowest breach level per signal)
# Source: alerts/detector.py _SIGNAL_SEVERITY_THRESHOLDS, first element of tuple
# ---------------------------------------------------------------------------

_WATCH_THRESHOLDS: Final[dict[str, float]] = {
    "governance_latency_index":       10.0,
    "escalation_suppression_rate":     0.60,
    "false_green_indicator":           1.0,
    "reporting_divergence_score":      0.30,
    "priority_collision_index":        2.0,
    "platform_utilization_pressure": 120.0,
    "initiative_saturation_ratio":     3.0,
    "dependency_fragility_score":      5.0,
    "headcount_stability_index":       4.0,
    "reprioritization_frequency":      0.70,
    "reactive_work_ratio":             0.30,
}

# ---------------------------------------------------------------------------
# Raw signal computation functions (spec Part 4, verbatim logic)
# ---------------------------------------------------------------------------


def _calc_governance_latency_index(escalations: pd.DataFrame) -> float:
    """
    Average days from escalation raised to resolved.

    Spec: avg Resolution_Days across resolved escalations.
    Returns 0.0 when no resolved escalations exist (no latency to measure).
    """
    if escalations.empty:
        return 0.0
    resolved = escalations[escalations["Date_Resolved"].notna()]
    if resolved.empty:
        return 0.0
    try:
        avg = pd.to_numeric(resolved["Resolution_Days"], errors="coerce").dropna().mean()
        return float(avg) if not pd.isna(avg) else 0.0
    except (KeyError, TypeError):
        return 0.0


def _calc_escalation_suppression_rate(escalations: pd.DataFrame) -> float:
    """
    Percentage of escalations resolved below VP level (IC / Manager / Director).

    Spec: len(below_vp) / len(escalations) if escalations > 0 else 0.
    """
    if escalations.empty:
        return 0.0
    try:
        below_vp = escalations[
            escalations["Resolved_At_Level"].isin(["IC", "Manager", "Director"])
        ]
        return round(len(below_vp) / len(escalations), 4)
    except KeyError:
        return 0.0


def _calc_false_green_indicator(initiatives: pd.DataFrame) -> float:
    """
    Count of programs reporting Green with more than 2 open critical blockers.

    Spec: count of (Status_Reported == 'Green') AND (Critical_Blockers > 2).
    """
    if initiatives.empty:
        return 0.0
    try:
        mask = (
            (initiatives["Status_Reported"] == "Green") &
            (pd.to_numeric(initiatives["Critical_Blockers"], errors="coerce").fillna(0) > 2)
        )
        return float(mask.sum())
    except KeyError:
        return 0.0


def _calc_reporting_divergence_score(initiatives: pd.DataFrame) -> float:
    """
    Gap between reported status and actual blocker density.

    Spec: % of green-reporting programs that have any open blockers.
    """
    if initiatives.empty:
        return 0.0
    try:
        green = initiatives[initiatives["Status_Reported"] == "Green"]
        if green.empty:
            return 0.0
        with_blockers = green[
            pd.to_numeric(green["Open_Blockers"], errors="coerce").fillna(0) > 0
        ]
        return round(len(with_blockers) / len(green), 4)
    except KeyError:
        return 0.0


def _calc_priority_collision_index(initiatives: pd.DataFrame) -> float:
    """
    Programs sharing top-3 resource dependencies.

    Spec: max(0, critical_programs - 3).
    """
    if initiatives.empty:
        return 0.0
    try:
        critical_count = (initiatives["Priority_Classification"] == "Critical").sum()
        return float(max(0, int(critical_count) - 3))
    except KeyError:
        return 0.0


def _calc_platform_utilization_pressure(resources: pd.DataFrame) -> float:
    """
    Maximum utilization percentage across all teams.

    Spec: resources_df['Estimated_Utilization_Pct'].max().
    """
    if resources.empty:
        return 0.0
    try:
        max_util = pd.to_numeric(resources["Estimated_Utilization_Pct"], errors="coerce").dropna().max()
        return float(max_util) if not pd.isna(max_util) else 0.0
    except KeyError:
        return 0.0


def _calc_initiative_saturation_ratio(
    initiatives: pd.DataFrame, resources: pd.DataFrame
) -> float:
    """
    Active programs per delivery team.

    Spec: active_initiatives / teams if teams > 0 else 0.
    Active = Status_Reported != 'Cancelled'.
    """
    teams = len(resources)
    if teams == 0 or initiatives.empty:
        return 0.0
    try:
        active = (initiatives["Status_Reported"] != "Cancelled").sum()
        return round(float(active) / teams, 4)
    except KeyError:
        return 0.0


def _calc_dependency_fragility_score(dependencies: pd.DataFrame) -> float:
    """
    At-risk cross-program dependencies open more than 7 days.

    Spec: count of At-Risk dependencies with Days_Open > 7.
    """
    if dependencies.empty:
        return 0.0
    try:
        fragile = dependencies[
            (dependencies["Status"] == "At-Risk") &
            (pd.to_numeric(dependencies["Days_Open"], errors="coerce").fillna(0) > 7)
        ]
        return float(len(fragile))
    except KeyError:
        return 0.0


def _calc_headcount_stability_index(resources: pd.DataFrame) -> float:
    """
    Open requisition risk: open_reqs * (avg_days_open / 30).

    Spec: open_reqs.sum() * (avg_days.mean() / 30).
    """
    if resources.empty:
        return 0.0
    try:
        open_reqs = pd.to_numeric(resources["Open_Requisitions"], errors="coerce").fillna(0).sum()
        avg_days = pd.to_numeric(resources["Avg_Days_Open_Reqs"], errors="coerce").fillna(0).mean()
        if pd.isna(avg_days):
            avg_days = 0.0
        return round(float(open_reqs) * (float(avg_days) / 30.0), 4)
    except KeyError:
        return 0.0


def _calc_reprioritization_frequency(initiatives: pd.DataFrame) -> float:
    """
    Priority inflation: percentage of initiatives classified Critical.

    Spec: critical_count / total if total > 0 else 0.
    """
    if initiatives.empty:
        return 0.0
    total = len(initiatives)
    if total == 0:
        return 0.0
    try:
        critical = (initiatives["Priority_Classification"] == "Critical").sum()
        return round(float(critical) / total, 4)
    except KeyError:
        return 0.0


def _calc_reactive_work_ratio(resources: pd.DataFrame) -> float:
    """
    Inverse of planned utilization headroom.

    Spec: max(0, (avg_util - 80) / avg_util) if avg_util > 0 else 0.
    """
    if resources.empty:
        return 0.0
    try:
        avg_util = pd.to_numeric(
            resources["Estimated_Utilization_Pct"], errors="coerce"
        ).fillna(0).mean()
        if pd.isna(avg_util) or avg_util <= 0:
            return 0.0
        return round(max(0.0, (avg_util - 80.0) / avg_util), 4)
    except KeyError:
        return 0.0


# ---------------------------------------------------------------------------
# Signal computation dispatch table
# ---------------------------------------------------------------------------

def _compute_all_signals(dataframes: dict[str, pd.DataFrame]) -> dict[str, float]:
    """
    Compute all 11 raw signals from the validated intake DataFrames.

    Args:
        dataframes: dict keyed by tab name, as returned by ingestor.py.

    Returns:
        dict[str, float]: signal_name -> computed float value.
    """
    ini  = dataframes.get("INITIATIVES", pd.DataFrame())
    esc  = dataframes.get("ESCALATIONS", pd.DataFrame())
    dep  = dataframes.get("DEPENDENCIES", pd.DataFrame())
    res  = dataframes.get("RESOURCE_UTILIZATION", pd.DataFrame())

    return {
        "governance_latency_index":       _calc_governance_latency_index(esc),
        "escalation_suppression_rate":    _calc_escalation_suppression_rate(esc),
        "false_green_indicator":          _calc_false_green_indicator(ini),
        "reporting_divergence_score":     _calc_reporting_divergence_score(ini),
        "priority_collision_index":       _calc_priority_collision_index(ini),
        "platform_utilization_pressure":  _calc_platform_utilization_pressure(res),
        "initiative_saturation_ratio":    _calc_initiative_saturation_ratio(ini, res),
        "dependency_fragility_score":     _calc_dependency_fragility_score(dep),
        "headcount_stability_index":      _calc_headcount_stability_index(res),
        "reprioritization_frequency":     _calc_reprioritization_frequency(ini),
        "reactive_work_ratio":            _calc_reactive_work_ratio(res),
    }


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def compute_and_write_signal_readings(
    conn: Any,
    submission_id: str,
    client_id: str,
    period_date: str,
    dataframes: dict[str, pd.DataFrame],
) -> list[str]:
    """
    Compute all 11 raw signals and write one signal_readings row per signal.

    Implements the Module 2 persistence step. Called from run_scoring() in
    scoring/engine.py before dimension scoring so that the signal_readings
    table is populated for the alert detector and dashboard signal display.

    The caller owns the database transaction. This function does not call
    conn.commit().

    Args:
        conn:          Open, authenticated database connection.
        submission_id: UUID of the intake_submission being processed.
        client_id:     UUID of the client.
        period_date:   Reporting period end date (YYYY-MM-DD).
        dataframes:    Validated intake DataFrames from ingestor.py.

    Returns:
        list[str]: reading_ids of the inserted signal_readings rows (11 items).

    Raises:
        Exception: any database error propagates to the caller.
    """
    now = datetime.now(timezone.utc).isoformat()
    signal_values = _compute_all_signals(dataframes)
    reading_ids: list[str] = []

    for signal_name, signal_value in signal_values.items():
        threshold = _WATCH_THRESHOLDS.get(signal_name, 0.0)
        breached = signal_value >= threshold and threshold > 0.0

        reading_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO signal_readings (
                reading_id,
                submission_id,
                client_id,
                signal_name,
                signal_value,
                threshold_value,
                threshold_breached,
                period_date,
                calculated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reading_id,
                submission_id,
                client_id,
                signal_name,
                round(signal_value, 4),
                round(threshold, 4),
                int(breached),
                period_date,
                now,
            ),
        )
        reading_ids.append(reading_id)

        log.info(
            "Signal written: %s = %.4f (threshold=%.4f breached=%s)",
            signal_name, signal_value, threshold, breached,
        )

    breach_count = sum(
        1 for name, val in signal_values.items()
        if _WATCH_THRESHOLDS.get(name, 0.0) > 0.0 and val >= _WATCH_THRESHOLDS[name]
    )

    write_audit_log(
        conn=conn,
        event_type="signals_calculated",
        entity_type="submission",
        entity_id=submission_id,
        description=(
            f"11 signal readings computed and written for submission {submission_id}. "
            f"Period: {period_date}. Breaches detected: {breach_count}."
        ),
        performed_by="system",
        metadata={
            "submission_id": submission_id,
            "client_id": client_id,
            "period_date": period_date,
            "signal_count": len(signal_values),
            "signal_values": {k: round(v, 4) for k, v in signal_values.items()},
        },
    )

    log.info(
        "Signal readings written: submission_id=%s, count=%d",
        submission_id, len(reading_ids),
    )

    return reading_ids
