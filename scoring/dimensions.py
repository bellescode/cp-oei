"""
scoring/dimensions.py
CPOI Platform — Dimension and OEI Composite Score Computation

Accepts the 21 SubCategoryResult objects produced by sub_categories.py and
computes:
  1. Five dimension scores — weighted sums of sub-category scores within
     each dimension, per DIMENSION_WEIGHTS in constants.py.
  2. An OEI composite score — weighted sum of the five dimension scores,
     per COMPOSITE_WEIGHTS in constants.py (all five equally weighted at 20%).
  3. A risk classification label for every score.
  4. An impact indicator for every sub-category within its dimension,
     ranked DRIVING → CONTRIBUTING → PRESENT → MONITORED.

Impact indicator assignment:
  Thresholds are aligned with the spec's risk classification bands, making
  the connection between raw risk level and narrative impact explicit:
    score >= 61  →  DRIVING       (High Risk or Critical band)
    41 <= score < 61  →  CONTRIBUTING  (Elevated band)
    21 <= score < 41  →  PRESENT       (Moderate band)
    score <= 20  →  MONITORED     (Low Risk band)
  EXCLUDED is reserved for Managing Partner exceptions and is never assigned
  automatically by this module.

All scores are returned as float. The engine layer (engine.py) is
responsible for rounding to integer for database storage (oei_scores table
uses INTEGER columns per schema.sql).

Public entry point:
    compute_oei_score(sub_category_results) -> OEIScoreResult
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Final

from scoring.constants import (
    COMPOSITE_WEIGHTS,
    DIMENSION_WEIGHTS,
    RISK_CLASSIFICATION,
)
from scoring.sub_categories import SubCategoryResult

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


log = _build_logger("cpoi.scoring.dimensions")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DIMENSION_NAMES: Final[dict[str, str]] = {
    "1": "Strategic Saturation",
    "2": "Governance Responsiveness",
    "3": "Execution Visibility",
    "4": "Reporting Integrity",
    "5": "Organizational Sustainability",
}

# Impact indicator labels (spec Part 3).
IMPACT_DRIVING:      Final[str] = "DRIVING"
IMPACT_CONTRIBUTING: Final[str] = "CONTRIBUTING"
IMPACT_PRESENT:      Final[str] = "PRESENT"
IMPACT_MONITORED:    Final[str] = "MONITORED"
IMPACT_EXCLUDED:     Final[str] = "EXCLUDED"

# Score thresholds for automatic indicator assignment.
# Aligned with RISK_CLASSIFICATION band boundaries so the narrative label
# directly reflects the band the sub-category score falls in.
_DRIVING_THRESHOLD:      Final[float] = 61.0  # High Risk band entry point
_CONTRIBUTING_THRESHOLD: Final[float] = 41.0  # Elevated band entry point
_PRESENT_THRESHOLD:      Final[float] = 21.0  # Moderate band entry point
# score <= 20 -> MONITORED (Low Risk band)

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DimensionResult:
    """
    Scored result for one OEI dimension.

    Attributes:
        dimension_id:          "1" through "5".
        dimension_name:        Human-readable dimension name from the spec.
        score:                 Weighted composite of sub-category scores in
                               [0.0, 100.0]. Higher = higher risk.
        classification:        Risk classification label from RISK_CLASSIFICATION
                               (e.g. "High Risk", "Elevated").
        sub_category_results:  Dict of SubCategoryResult for the sub-categories
                               belonging to this dimension, keyed by sub-category ID.
        impact_indicators:     Dict mapping each sub-category ID to its impact
                               label (DRIVING / CONTRIBUTING / PRESENT / MONITORED).
                               EXCLUDED is never assigned automatically.
    """

    dimension_id: str
    dimension_name: str
    score: float
    classification: str
    sub_category_results: dict[str, SubCategoryResult]
    impact_indicators: dict[str, str]


@dataclass(frozen=True)
class OEIScoreResult:
    """
    Complete OEI scoring outcome for one intake submission.

    Contains all five dimension results plus the composite score and
    classification. This is the primary output of the scoring engine and
    the input to the report generation and alert modules.

    Attributes:
        composite_score:          Weighted sum of five dimension scores.
                                  All dimensions equally weighted at 20%.
        composite_classification: Risk classification label for the composite.
        dimension_results:        Dict of DimensionResult keyed by dimension ID.
        sub_category_results:     All 21 SubCategoryResult objects, keyed by
                                  sub-category ID. Convenience reference —
                                  the same objects appear inside dimension_results.
        calculated_at:            ISO 8601 UTC timestamp of when this result
                                  was computed.
    """

    composite_score: float
    composite_classification: str
    dimension_results: dict[str, DimensionResult]
    sub_category_results: dict[str, SubCategoryResult]
    calculated_at: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def classify_score(score: float) -> str:
    """
    Return the risk classification label for a given OEI score.

    Iterates RISK_CLASSIFICATION bands from lowest to highest. Returns the
    label of the first band whose upper boundary is >= score. Falls back to
    "Critical" for any score above 100 (should not occur in normal operation).

    Args:
        score: A numeric OEI risk score, typically in [0.0, 100.0].

    Returns:
        str: One of "Low Risk", "Moderate", "Elevated", "High Risk", "Critical".
    """
    for lower, upper, label, _ in RISK_CLASSIFICATION:
        if lower <= score <= upper:
            return label
    # Score above 100 — treat as Critical.
    return "Critical"


def assign_impact_indicator(score: float) -> str:
    """
    Return the impact indicator label for a sub-category score.

    Thresholds are aligned with the RISK_CLASSIFICATION band boundaries:
      score >= 61  →  DRIVING       (in the High Risk or Critical band)
      41 <= score < 61  →  CONTRIBUTING  (in the Elevated band)
      21 <= score < 41  →  PRESENT       (in the Moderate band)
      score <= 20  →  MONITORED     (in the Low Risk band)

    Args:
        score: Sub-category risk score in [0.0, 100.0].

    Returns:
        str: One of DRIVING, CONTRIBUTING, PRESENT, MONITORED.
    """
    if score >= _DRIVING_THRESHOLD:
        return IMPACT_DRIVING
    if score >= _CONTRIBUTING_THRESHOLD:
        return IMPACT_CONTRIBUTING
    if score >= _PRESENT_THRESHOLD:
        return IMPACT_PRESENT
    return IMPACT_MONITORED


def compute_dimension(
    dimension_id: str,
    all_sub_results: dict[str, SubCategoryResult],
) -> DimensionResult:
    """
    Compute one dimension score from the relevant sub-category results.

    Performs the weighted sum defined in DIMENSION_WEIGHTS. All sub-category
    weights within a dimension sum to 1.0 (enforced at import time in
    constants.py), so the dimension score is guaranteed to remain in
    [0.0, 100.0] as long as each sub-category score is in [0.0, 100.0].

    Public so that scoring/overrides.py can recompute affected dimensions
    after applying a sub-category override without duplicating the weighted-sum
    logic.

    Args:
        dimension_id:    "1" through "5".
        all_sub_results: Full dict of 21 SubCategoryResult objects.

    Returns:
        DimensionResult with score, classification, and impact indicators.

    Raises:
        KeyError: if a sub-category ID required by this dimension is absent
                  from all_sub_results. This indicates a programming error —
                  compute_all_sub_categories guarantees all 21 keys.
    """
    weights = DIMENSION_WEIGHTS[dimension_id]
    dim_sub_results: dict[str, SubCategoryResult] = {}
    dim_score: float = 0.0

    for sub_id, weight in weights.items():
        result = all_sub_results[sub_id]
        dim_sub_results[sub_id] = result
        dim_score += result.score * weight

    impact_indicators: dict[str, str] = {
        sub_id: assign_impact_indicator(result.score)
        for sub_id, result in dim_sub_results.items()
    }

    classification = classify_score(dim_score)

    log.info(
        "Dimension %s (%s): score=%.2f, classification=%s, "
        "DRIVING=%s, CONTRIBUTING=%s",
        dimension_id,
        DIMENSION_NAMES[dimension_id],
        dim_score,
        classification,
        [s for s, ind in impact_indicators.items() if ind == IMPACT_DRIVING],
        [s for s, ind in impact_indicators.items() if ind == IMPACT_CONTRIBUTING],
    )

    return DimensionResult(
        dimension_id=dimension_id,
        dimension_name=DIMENSION_NAMES[dimension_id],
        score=dim_score,
        classification=classification,
        sub_category_results=dim_sub_results,
        impact_indicators=impact_indicators,
    )


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def compute_oei_score(
    sub_category_results: dict[str, SubCategoryResult],
) -> OEIScoreResult:
    """
    Compute the full OEI score from all 21 sub-category results.

    Computes all five dimension scores and the OEI composite in a single
    pass. Every sub-category in DIMENSION_WEIGHTS must be present in
    sub_category_results; compute_all_sub_categories() in sub_categories.py
    guarantees this.

    Calculation sequence:
      1. For each dimension: weighted sum of its sub-category scores.
      2. OEI composite: weighted sum of the five dimension scores
         (all equally weighted at 0.20).
      3. Risk classification assigned to each dimension score and the composite.
      4. Impact indicators assigned to each sub-category within its dimension.

    Args:
        sub_category_results: dict[str, SubCategoryResult] keyed by
                              sub-category ID (e.g. "1.1"), as returned by
                              compute_all_sub_categories().

    Returns:
        OEIScoreResult with all five DimensionResult objects, the composite
        score, classification, and a UTC timestamp.

    Raises:
        KeyError: if a required sub-category ID is absent from
                  sub_category_results. This indicates the caller did not
                  pass the full output of compute_all_sub_categories().
    """
    calculated_at = datetime.now(timezone.utc).isoformat()

    # Compute all five dimension results.
    dimension_results: dict[str, DimensionResult] = {
        dim_id: compute_dimension(dim_id, sub_category_results)
        for dim_id in DIMENSION_WEIGHTS
    }

    # Compute OEI composite as the weighted sum of dimension scores.
    composite_score: float = sum(
        dimension_results[dim_id].score * COMPOSITE_WEIGHTS[dim_id]
        for dim_id in COMPOSITE_WEIGHTS
    )

    composite_classification = classify_score(composite_score)

    log.info(
        "OEI composite score=%.2f, classification=%s, calculated_at=%s",
        composite_score,
        composite_classification,
        calculated_at,
    )

    return OEIScoreResult(
        composite_score=composite_score,
        composite_classification=composite_classification,
        dimension_results=dimension_results,
        sub_category_results=sub_category_results,
        calculated_at=calculated_at,
    )
