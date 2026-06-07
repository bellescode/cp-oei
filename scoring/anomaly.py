"""
scoring/anomaly.py
CPOI Platform -- Period-over-period anomaly detection.

Detects four flag types that must be reviewed before scores can be published:

  1. SUSPICIOUS_IMPROVEMENT   -- any dimension improves >= 15 points vs prior
  2. INCONSISTENT_SIGNAL      -- dimension improves but its primary signal worsens
  3. DATA_GAP_CLOSURE         -- prior score near missing-data sentinel (68-82),
                                 current score moves > 15 points from that range
  4. ZERO_MOVEMENT_STAGNATION -- all dimensions < 3 pt change for 2 consecutive
                                 periods (requires 3 periods of history)

AnomalyFlag objects are generated fresh on each call; they are not persisted.
The flag_id is deterministic so the dashboard can use it as a stable key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

_SUSPICIOUS_IMPROVEMENT_THRESHOLD = 15   # points
_STAGNATION_THRESHOLD = 3                # points max movement per dimension
_GAP_SENTINEL_LO = 68
_GAP_SENTINEL_HI = 82
_GAP_MOVE_THRESHOLD = 15                 # points from the sentinel range

# Maps each OEI dimension score column to its primary driving signal
_DIMENSION_PRIMARY_SIGNAL: dict[str, str] = {
    "strategic_saturation_score":      "initiative_saturation_ratio",
    "governance_responsiveness_score": "governance_latency_index",
    "execution_visibility_score":      "dependency_fragility_score",
    "reporting_integrity_score":       "false_green_indicator",
    "org_sustainability_score":        "platform_utilization_pressure",
}

_DIMENSION_LABELS: dict[str, str] = {
    "strategic_saturation_score":      "Strategic Saturation",
    "governance_responsiveness_score": "Governance Responsiveness",
    "execution_visibility_score":      "Execution Visibility",
    "reporting_integrity_score":       "Reporting Integrity",
    "org_sustainability_score":        "Org Sustainability",
}


# ------------------------------------------------------------------
# Data model
# ------------------------------------------------------------------

@dataclass
class AnomalyFlag:
    flag_id: str
    flag_type: str
    dimension: str | None
    description: str
    detected_at: str


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _fetch_scores(conn: Any, submission_id: str) -> dict | None:
    cursor = conn.execute(
        """
        SELECT *
        FROM oei_scores
        WHERE submission_id = ?
        LIMIT 1
        """,
        (submission_id,),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def _fetch_prior_scores(conn: Any, client_id: str, period_date: str) -> dict | None:
    cursor = conn.execute(
        """
        SELECT *
        FROM oei_scores
        WHERE client_id = ? AND period_date < ?
        ORDER BY period_date DESC
        LIMIT 1
        """,
        (client_id, period_date),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def _fetch_prior_prior_scores(conn: Any, client_id: str, prior_period_date: str) -> dict | None:
    """Fetch the period before the prior period (needed for stagnation check)."""
    cursor = conn.execute(
        """
        SELECT *
        FROM oei_scores
        WHERE client_id = ? AND period_date < ?
        ORDER BY period_date DESC
        LIMIT 1
        """,
        (client_id, prior_period_date),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def _fetch_signals(conn: Any, submission_id: str) -> dict[str, float]:
    """Return {signal_name: signal_value} for a submission."""
    cursor = conn.execute(
        "SELECT signal_name, signal_value FROM signal_readings WHERE submission_id = ?",
        (submission_id,),
    )
    return {row["signal_name"]: float(row["signal_value"]) for row in cursor.fetchall()}


def _fetch_submission_for_period(conn: Any, client_id: str, period_date: str) -> str | None:
    """Return submission_id for a given client + period_date."""
    cursor = conn.execute(
        """
        SELECT s.submission_id
        FROM intake_submissions s
        JOIN oei_scores o ON o.submission_id = s.submission_id
        WHERE s.client_id = ? AND o.period_date = ?
        LIMIT 1
        """,
        (client_id, period_date),
    )
    row = cursor.fetchone()
    return row["submission_id"] if row else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------
# Flag detectors
# ------------------------------------------------------------------

def _check_suspicious_improvement(
    current: dict,
    prior: dict,
    submission_id: str,
    now: str,
) -> list[AnomalyFlag]:
    flags: list[AnomalyFlag] = []
    for col, label in _DIMENSION_LABELS.items():
        cur_val = current.get(col)
        pri_val = prior.get(col)
        if cur_val is None or pri_val is None:
            continue
        delta = float(cur_val) - float(pri_val)
        # Lower score = more risk; improvement means score went DOWN
        if delta <= -_SUSPICIOUS_IMPROVEMENT_THRESHOLD:
            flags.append(AnomalyFlag(
                flag_id=f"SUSPICIOUS_IMPROVEMENT_{submission_id}_{col}",
                flag_type="SUSPICIOUS_IMPROVEMENT",
                dimension=label,
                description=(
                    f"{label} improved by {abs(delta):.1f} points "
                    f"({float(pri_val):.0f} → {float(cur_val):.0f}). "
                    f"Gains of {_SUSPICIOUS_IMPROVEMENT_THRESHOLD}+ points require "
                    "verification that underlying operational data supports the change."
                ),
                detected_at=now,
            ))
    return flags


def _check_inconsistent_signal(
    current: dict,
    prior: dict,
    current_signals: dict[str, float],
    prior_signals: dict[str, float],
    submission_id: str,
    now: str,
) -> list[AnomalyFlag]:
    flags: list[AnomalyFlag] = []
    for col, signal_name in _DIMENSION_PRIMARY_SIGNAL.items():
        label = _DIMENSION_LABELS[col]
        cur_dim = current.get(col)
        pri_dim = prior.get(col)
        cur_sig = current_signals.get(signal_name)
        pri_sig = prior_signals.get(signal_name)

        if any(v is None for v in [cur_dim, pri_dim, cur_sig, pri_sig]):
            continue

        dim_improved = float(cur_dim) < float(pri_dim)   # lower = better
        sig_worsened = float(cur_sig) > float(pri_sig)   # higher = worse for these signals

        if dim_improved and sig_worsened:
            flags.append(AnomalyFlag(
                flag_id=f"INCONSISTENT_SIGNAL_{submission_id}_{col}",
                flag_type="INCONSISTENT_SIGNAL",
                dimension=label,
                description=(
                    f"{label} score improved ({float(pri_dim):.0f} → {float(cur_dim):.0f}) "
                    f"but its primary signal '{signal_name}' worsened "
                    f"({float(pri_sig):.4f} → {float(cur_sig):.4f}). "
                    "Verify that sub-category data is consistent with the dimension score."
                ),
                detected_at=now,
            ))
    return flags


def _check_data_gap_closure(
    current: dict,
    prior: dict,
    submission_id: str,
    now: str,
) -> list[AnomalyFlag]:
    flags: list[AnomalyFlag] = []
    for col, label in _DIMENSION_LABELS.items():
        cur_val = current.get(col)
        pri_val = prior.get(col)
        if cur_val is None or pri_val is None:
            continue

        pri_f = float(pri_val)
        cur_f = float(cur_val)

        # Prior score in sentinel range AND large movement away from it
        in_sentinel_range = _GAP_SENTINEL_LO <= pri_f <= _GAP_SENTINEL_HI
        large_move = abs(cur_f - pri_f) > _GAP_MOVE_THRESHOLD

        if in_sentinel_range and large_move:
            flags.append(AnomalyFlag(
                flag_id=f"DATA_GAP_CLOSURE_{submission_id}_{col}",
                flag_type="DATA_GAP_CLOSURE",
                dimension=label,
                description=(
                    f"{label} prior score ({pri_f:.0f}) was near the missing-data default "
                    f"and shifted {abs(cur_f - pri_f):.1f} points to {cur_f:.0f}. "
                    "Confirm that previously missing sub-category data was legitimately "
                    "provided rather than filled with estimated values."
                ),
                detected_at=now,
            ))
    return flags


def _check_zero_movement_stagnation(
    current: dict,
    prior: dict,
    prior_prior: dict | None,
    submission_id: str,
    now: str,
) -> list[AnomalyFlag]:
    if prior_prior is None:
        return []

    def _all_stagnant(a: dict, b: dict) -> bool:
        for col in _DIMENSION_LABELS:
            va = a.get(col)
            vb = b.get(col)
            if va is None or vb is None:
                return False
            if abs(float(va) - float(vb)) >= _STAGNATION_THRESHOLD:
                return False
        return True

    period1_stagnant = _all_stagnant(current, prior)
    period2_stagnant = _all_stagnant(prior, prior_prior)

    if period1_stagnant and period2_stagnant:
        return [AnomalyFlag(
            flag_id=f"ZERO_MOVEMENT_STAGNATION_{submission_id}_portfolio",
            flag_type="ZERO_MOVEMENT_STAGNATION",
            dimension=None,
            description=(
                "All five OEI dimensions have changed less than "
                f"{_STAGNATION_THRESHOLD} points across two consecutive periods. "
                "Verify that the client is actively submitting new operational data "
                "rather than re-submitting unchanged figures."
            ),
            detected_at=now,
        )]
    return []


# ------------------------------------------------------------------
# Public entry point
# ------------------------------------------------------------------

def detect_anomaly_flags(
    conn: Any,
    submission_id: str,
    client_id: str,
) -> list[AnomalyFlag]:
    """
    Run all four anomaly checks for a given submission.

    Returns an empty list if no prior period exists or if no scores are found.
    Flags are not persisted; they are generated fresh on each call.
    """
    now = _now_iso()

    current = _fetch_scores(conn, submission_id)
    if current is None:
        return []

    prior = _fetch_prior_scores(conn, client_id, current["period_date"])
    if prior is None:
        return []

    prior_prior = _fetch_prior_prior_scores(conn, client_id, prior["period_date"])

    current_signals = _fetch_signals(conn, submission_id)
    prior_sub_id = _fetch_submission_for_period(conn, client_id, prior["period_date"])
    prior_signals = _fetch_signals(conn, prior_sub_id) if prior_sub_id else {}

    flags: list[AnomalyFlag] = []
    flags.extend(_check_suspicious_improvement(current, prior, submission_id, now))
    flags.extend(_check_inconsistent_signal(current, prior, current_signals, prior_signals, submission_id, now))
    flags.extend(_check_data_gap_closure(current, prior, submission_id, now))
    flags.extend(_check_zero_movement_stagnation(current, prior, prior_prior, submission_id, now))

    return flags
