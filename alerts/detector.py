"""
alerts/detector.py
CPOI Platform — Alert Detection

Detects threshold breaches in signal_readings and creates exactly one alert
record per breach per submission. Called after signal_readings rows have been
written for a submission.

Public interface:
  create_alerts_for_submission(
      conn, submission_id, client_id, period_date
  ) -> list[str]

Design invariants:
  - Idempotent: calling this function twice for the same submission_id will
    not create duplicate alert records. The reading_id column in alerts is
    the idempotency key.
  - No alert without a signal reading: every inserted alert row carries a
    reading_id FK that references the originating signal_readings record.
  - No commit: the caller owns the transaction and must call conn.commit()
    after this function returns.
  - No client data transmitted externally: this module only reads from and
    writes to the local database. No outbound network calls are made here.

Severity classification:
  Per-signal watch / elevated / critical thresholds are taken directly from
  the classify_severity call sites in cpoi-sdlc-spec.md Part 4. The
  _SIGNAL_SEVERITY_THRESHOLDS map is the single source of truth for these
  values within this module. Any threshold change requires a VERSION update
  in scoring/constants.py and a corresponding audit log entry.

  false_green_indicator is a special case: the spec assigns severity
  'critical' for any count >= 1 (per calc_false_green_indicator). The
  thresholds tuple (1.0, 1.0, 1.0) encodes this: all three levels resolve
  to the same value, so any breached count returns 'critical'.

  Signals not present in _SIGNAL_SEVERITY_THRESHOLDS (defensive case for
  future signals not yet catalogued here) default to 'watch'.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Final

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


log = _build_logger("cpoi.alerts.detector")

# ---------------------------------------------------------------------------
# Per-signal severity thresholds
# ---------------------------------------------------------------------------
# Source: cpoi-sdlc-spec.md Part 4, classify_severity call sites.
# Tuple layout: (watch_threshold, elevated_threshold, critical_threshold)
# A signal value >= the critical threshold → 'critical'.
# A signal value >= the elevated threshold → 'elevated'.
# A signal value >= the watch threshold   → 'watch'.
# All three comparisons use >=.

_SIGNAL_SEVERITY_THRESHOLDS: Final[dict[str, tuple[float, float, float]]] = {
    # Average days escalation raised to resolved.
    "governance_latency_index":       (10.0,  15.0,  21.0),
    # Percentage of escalations resolved below VP level.
    "escalation_suppression_rate":    ( 0.60,  0.70,  0.80),
    # Count of programs reporting Green with > 2 open critical blockers.
    # Spec assigns 'critical' for any count > 0. Thresholds are equal so
    # any breached value (>= 1) immediately resolves to 'critical'.
    "false_green_indicator":          ( 1.0,   1.0,   1.0),
    # Percentage of green-status programs with open blockers.
    "reporting_divergence_score":     ( 0.30,  0.45,  0.60),
    # Critical programs sharing top-3 resource dependencies beyond 3.
    "priority_collision_index":       ( 2.0,   4.0,   6.0),
    # Max team utilization percentage across the portfolio.
    "platform_utilization_pressure":  (120.0, 135.0, 150.0),
    # Active programs per delivery team.
    "initiative_saturation_ratio":    ( 3.0,   4.0,   5.0),
    # At-risk cross-program dependencies open > 7 days.
    "dependency_fragility_score":     ( 5.0,   8.0,  12.0),
    # Open requisition risk score (open_reqs * avg_days_open / 30).
    "headcount_stability_index":      ( 4.0,   7.0,  10.0),
    # Proportion of initiatives classified Critical (priority inflation).
    "reprioritization_frequency":     ( 0.70,  0.80,  0.90),
    # Proportion of team capacity consumed by reactive/unplanned work.
    "reactive_work_ratio":            ( 0.30,  0.40,  0.50),
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _classify_signal_severity(signal_name: str, signal_value: float) -> str:
    """
    Return the OEI severity label for a breached signal value.

    Applies the per-signal watch / elevated / critical thresholds defined in
    _SIGNAL_SEVERITY_THRESHOLDS. For signals not present in that map (defensive
    case), returns 'watch' — the minimum breach severity.

    Args:
        signal_name:  Canonical signal name as stored in signal_readings.
        signal_value: The measured numeric signal value.

    Returns:
        str: One of 'critical', 'elevated', or 'watch'.
    """
    thresholds = _SIGNAL_SEVERITY_THRESHOLDS.get(signal_name)
    if thresholds is None:
        log.warning(
            "Signal '%s' not found in _SIGNAL_SEVERITY_THRESHOLDS. "
            "Defaulting severity to 'watch'. "
            "Update alerts/detector.py if this is a new production signal.",
            signal_name,
        )
        return "watch"

    watch_t, elevated_t, critical_t = thresholds
    if signal_value >= critical_t:
        return "critical"
    if signal_value >= elevated_t:
        return "elevated"
    return "watch"


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def create_alerts_for_submission(
    conn: Any,
    submission_id: str,
    client_id: str,
    period_date: str,
) -> list[str]:
    """
    Create alert records for all threshold breaches in a submission.

    Queries signal_readings for rows where threshold_breached = TRUE and
    submission_id matches the provided argument. For each breached reading,
    checks whether an alert already exists with the same reading_id
    (idempotency guard). If no duplicate is found, inserts one alert record
    and one audit_log entry.

    This function is idempotent: repeated calls for the same submission_id
    produce no additional alert records beyond those created on the first call.

    The caller owns the database transaction. This function does not call
    conn.commit().

    Args:
        conn:          Open, authenticated database connection.
        submission_id: UUID of the intake_submission being processed.
                       Must already exist in intake_submissions.
        client_id:     UUID of the client this submission belongs to.
                       Must already exist in clients.
        period_date:   Reporting period end date as an ISO date string
                       (YYYY-MM-DD). Stored in the audit log metadata.

    Returns:
        list[str]: alert_ids of newly created alert records. Returns an empty
                   list if no threshold breaches exist or all detected breaches
                   already have corresponding alert records.

    Raises:
        Exception: any database error propagates to the caller without
                   wrapping so the caller's transaction handling is not
                   disrupted.
    """
    # ------------------------------------------------------------------
    # Step 1: Fetch all breached signal readings for this submission.
    # ------------------------------------------------------------------
    cursor = conn.execute(
        """
        SELECT reading_id, signal_name, signal_value, threshold_value
        FROM signal_readings
        WHERE submission_id = ?
          AND threshold_breached = TRUE
        ORDER BY signal_name
        """,
        (submission_id,),
    )
    breached_readings = cursor.fetchall()

    if not breached_readings:
        log.info(
            "No breached signal readings found for submission_id=%s.", submission_id
        )
        return []

    log.info(
        "Found %d breached signal reading(s) for submission_id=%s.",
        len(breached_readings),
        submission_id,
    )

    # ------------------------------------------------------------------
    # Step 2: For each breach, check idempotency and insert if new.
    # ------------------------------------------------------------------
    created_alert_ids: list[str] = []
    triggered_at = datetime.now(timezone.utc).isoformat()

    for reading_id, signal_name, signal_value, threshold_value in breached_readings:
        # Idempotency guard: skip if an alert already exists for this reading.
        existing = conn.execute(
            "SELECT alert_id FROM alerts WHERE reading_id = ? LIMIT 1",
            (reading_id,),
        ).fetchone()

        if existing:
            log.info(
                "Alert already exists for reading_id=%s (signal=%s). Skipping.",
                reading_id,
                signal_name,
            )
            continue

        severity = _classify_signal_severity(signal_name, float(signal_value))
        alert_id = str(uuid.uuid4())

        conn.execute(
            """
            INSERT INTO alerts (
                alert_id,
                client_id,
                submission_id,
                reading_id,
                signal_name,
                signal_value,
                threshold_value,
                alert_severity,
                triggered_at,
                acknowledged,
                email_sent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, FALSE, FALSE)
            """,
            (
                alert_id,
                client_id,
                submission_id,
                reading_id,
                signal_name,
                float(signal_value),
                float(threshold_value),
                severity,
                triggered_at,
            ),
        )

        write_audit_log(
            conn=conn,
            event_type="alert_created",
            entity_type="alert",
            entity_id=alert_id,
            description=(
                f"Alert created for signal '{signal_name}' threshold breach "
                f"on submission {submission_id}. "
                f"Signal value: {float(signal_value)}, "
                f"threshold: {float(threshold_value)}, "
                f"severity: {severity}."
            ),
            performed_by="system",
            metadata={
                "submission_id":   submission_id,
                "client_id":       client_id,
                "period_date":     period_date,
                "reading_id":      reading_id,
                "signal_name":     signal_name,
                "signal_value":    float(signal_value),
                "threshold_value": float(threshold_value),
                "severity":        severity,
            },
        )

        log.info(
            "Alert created: alert_id=%s signal=%s severity=%s "
            "value=%.4f threshold=%.4f",
            alert_id,
            signal_name,
            severity,
            float(signal_value),
            float(threshold_value),
        )

        created_alert_ids.append(alert_id)

    log.info(
        "Alert detection complete: submission_id=%s, %d new alert(s) created, "
        "%d already existed.",
        submission_id,
        len(created_alert_ids),
        len(breached_readings) - len(created_alert_ids),
    )

    return created_alert_ids
