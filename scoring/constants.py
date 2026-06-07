"""
scoring/constants.py
CPOI Platform — Scoring Constants and Band Tables

All scoring parameters for the OEI (Operational Executive Intelligence) scoring
model, derived from the CPOI Scoring and Platform Specification v2.0.

This module is the single source of truth for:
  - ScoreBand dataclass and interpolate_score helper function
  - All 21 sub-category scoring band tables
  - Per-sub-category missing data defaults
  - Sub-category weights within each dimension
  - OEI composite dimension weights
  - Alert thresholds and risk classification bands

No scoring parameter exists outside this module. Any change to the scoring
model requires updating this file, incrementing the VERSION constant, and
creating a version-controlled record of the rationale. Per spec Part 5,
weighting adjustments apply only to new submissions and are never retroactively
applied to prior period scores.

Specification version: 2.0
Supersedes: SDLC Spec v1.0 (scoring section)
Author: Bernadette Akpeko Thompson, Criterion Partners
"""

import math
from dataclasses import dataclass
from typing import Final

# ---------------------------------------------------------------------------
# Specification version
# ---------------------------------------------------------------------------

VERSION: Final[str] = "2.0"

# ---------------------------------------------------------------------------
# ScoreBand dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoreBand:
    """
    A single scored condition band from the OEI specification.

    Attributes:
        condition_lower:  Inclusive lower boundary of the condition range.
        condition_upper:  Upper boundary of the condition range.
                          Set to float('inf') for the unbounded top band.
        score_at_lower:   The interpolated score when the measured condition
                          value equals condition_lower.
        score_at_upper:   The interpolated score when the measured condition
                          value equals condition_upper.

    For normal sub-categories (higher measured value = higher risk):
        score_at_lower < score_at_upper within each band.

    For inverted sub-categories (higher measured value = lower risk):
        score_at_lower > score_at_upper within each band.
        The linear interpolation formula is identical in both cases; the
        direction of the score change is encoded in the field values.

    Band ordering:
        All band tuples are sorted by condition_lower ascending. The
        interpolation function selects the last band whose condition_lower
        does not exceed the input value.
    """

    condition_lower: float
    condition_upper: float
    score_at_lower: float
    score_at_upper: float


# ---------------------------------------------------------------------------
# Interpolation helper
# ---------------------------------------------------------------------------


def interpolate_score(value: float, bands: tuple[ScoreBand, ...]) -> float:
    """
    Return the OEI risk score for a condition value against a scoring band table.

    Band selection:
        Iterates bands sorted by condition_lower ascending. The applicable band
        is the last one whose condition_lower does not exceed the input value.
        If the value falls below the first band's lower boundary, the first
        band is used with the interpolation ratio clamped to 0.0, returning
        score_at_lower of that band.

    Interpolation rules:
        Bounded band (condition_upper != inf):
            ratio = clamp((value - condition_lower) / (condition_upper - condition_lower), 0.0, 1.0)
            score = score_at_lower + ratio * (score_at_upper - score_at_lower)

        Unbounded top band (condition_upper == inf):
            score = (score_at_lower + score_at_upper) / 2.0
            Rationale: unbounded bands have no finite upper interpolation
            endpoint. The midpoint of the score range is the unbiased estimate.

        Point band (condition_lower == condition_upper):
            score = score_at_lower

    This formula handles both normal sub-categories (score_at_lower <
    score_at_upper) and inverted sub-categories (score_at_lower >
    score_at_upper) without branching.

    Args:
        value: The measured condition value (e.g., a percentage, day count,
               or rate). Must be a finite float.
        bands: Tuple of ScoreBand entries sorted by condition_lower ascending.

    Returns:
        float: Risk score. For normal sub-categories this falls in [0.0, 100.0].
               For inverted sub-categories the same range applies; the score
               decreases as the condition value increases.

    Raises:
        ValueError: if bands is empty.
    """
    if not bands:
        raise ValueError("bands must contain at least one ScoreBand entry.")

    applicable: ScoreBand | None = None
    for band in bands:
        if band.condition_lower <= value:
            applicable = band
        else:
            # Bands are sorted ascending; no subsequent band can match.
            break

    if applicable is None:
        # Value is below the first band's lower boundary; clamp to first band.
        applicable = bands[0]

    # Unbounded top band: return the midpoint of the score range.
    if math.isinf(applicable.condition_upper):
        return (applicable.score_at_lower + applicable.score_at_upper) / 2.0

    # Point band (degenerate case where lower == upper).
    span = applicable.condition_upper - applicable.condition_lower
    if span == 0.0:
        return float(applicable.score_at_lower)

    # Linear interpolation within bounded band; ratio clamped to [0.0, 1.0].
    ratio = (value - applicable.condition_lower) / span
    ratio = max(0.0, min(1.0, ratio))
    return (
        applicable.score_at_lower
        + ratio * (applicable.score_at_upper - applicable.score_at_lower)
    )


# ---------------------------------------------------------------------------
# Sub-category scoring band tables
# ---------------------------------------------------------------------------
#
# Key format: "<dimension>.<sub-category>" (e.g., "1.1", "3.4")
# Bands sorted by condition_lower ascending within each tuple.
#
# Inverted sub-categories (score_at_lower > score_at_upper per band):
#   2.4  Accountability Clarity Index
#   3.1  Telemetry Coverage Ratio
#   3.2  Dependency Transparency Score
#   3.4  Leadership Visibility Index
#   4.4  Confidence Reliability Score
#   5.4  Adaptive Capacity Reserve
#
# Non-computable sub-categories (always score at MISSING_DATA_DEFAULTS):
#   1.3  Priority Change Frequency   — no reclassification history from single intake
#   4.2  Manual Curation Index       — qualitative; requires client self-report or MP assessment
#   4.3  Reporting Incentive Alignment — requires Managing Partner direct observation
#   5.2  Reprioritization Impact Rate — no event log available from single intake
#
# Bands for non-computable sub-categories are retained in this table for
# completeness and to support manual override score entry by the Managing Partner.
# ---------------------------------------------------------------------------

SCORE_BANDS: Final[dict[str, tuple[ScoreBand, ...]]] = {

    # =======================================================================
    # DIMENSION 1 — STRATEGIC SATURATION
    # =======================================================================

    # 1.1  Priority Inflation Index
    # Condition: percentage of active initiatives labeled Critical or High Priority
    # Normal: higher percentage → higher risk score
    # Spec bands: 0–30%→0–15, 31–50%→16–30, 51–65%→31–50, 66–80%→51–70,
    #             81–100%→71–90, 100% (all critical)→91–100
    # Missing data default: 75
    "1.1": (
        ScoreBand(  0.0,  30.0,   0.0,  15.0),
        ScoreBand( 30.0,  50.0,  16.0,  30.0),
        ScoreBand( 50.0,  65.0,  31.0,  50.0),
        ScoreBand( 65.0,  80.0,  51.0,  70.0),
        ScoreBand( 80.0, 100.0,  71.0,  90.0),
        ScoreBand(100.0, float("inf"), 91.0, 100.0),
    ),

    # 1.2  Concurrent Transformation Density
    # Condition: active initiatives per delivery team
    # Normal: higher ratio → higher risk score
    # Spec bands: 1–2→0–15, 3→16–35, 4→36–55, 5→56–75, 6+→76–100
    # Condition upper boundaries set to the next band's lower bound for
    # continuous interpolation across the "1–2 per team" range.
    # Missing data default: 75 (general rule — no explicit spec note)
    "1.2": (
        ScoreBand(1.0, 3.0,          0.0,  15.0),
        ScoreBand(3.0, 4.0,         16.0,  35.0),
        ScoreBand(4.0, 5.0,         36.0,  55.0),
        ScoreBand(5.0, 6.0,         56.0,  75.0),
        ScoreBand(6.0, float("inf"), 76.0, 100.0),
    ),

    # 1.3  Priority Change Frequency  [NON-COMPUTABLE]
    # Condition: reclassifications per initiative per quarter
    # Normal: more reclassifications → higher risk score
    # Spec bands: 0→0, 1/qtr→5–20, 2/qtr→21–50, 3/qtr→51–75, 4+/qtr→76–100
    # This sub-category always scores at its missing-data default (65).
    # Bands are retained for reference and to support override score entry.
    "1.3": (
        ScoreBand(0.0, 1.0,          0.0,   0.0),
        ScoreBand(1.0, 2.0,          5.0,  20.0),
        ScoreBand(2.0, 3.0,         21.0,  50.0),
        ScoreBand(3.0, 4.0,         51.0,  75.0),
        ScoreBand(4.0, float("inf"), 76.0, 100.0),
    ),

    # 1.4  Initiative Persistence Rate
    # Condition: percentage of initiatives past their original target completion
    #            date without a formal scope change or deferral decision
    # Normal: higher percentage → higher risk score
    # Spec bands: 0–10%→0–10, 11–25%→11–30, 26–40%→31–55,
    #             41–60%→56–75, 61%+→76–100
    # Missing data default: 70
    "1.4": (
        ScoreBand( 0.0, 10.0,          0.0,  10.0),
        ScoreBand(10.0, 25.0,         11.0,  30.0),
        ScoreBand(25.0, 40.0,         31.0,  55.0),
        ScoreBand(40.0, 60.0,         56.0,  75.0),
        ScoreBand(60.0, float("inf"), 76.0, 100.0),
    ),

    # =======================================================================
    # DIMENSION 2 — GOVERNANCE RESPONSIVENESS
    # =======================================================================

    # 2.1  Escalation Resolution Latency
    # Condition: average days from escalation creation to documented resolution
    # Normal: more days → higher risk score
    # Spec bands: 0–5d→0–10, 6–10d→11–30, 11–15d→31–55, 16–21d→56–75,
    #             22–30d→76–90, 31+d→91–100
    # Missing data default: 80
    "2.1": (
        ScoreBand( 0.0,  5.0,          0.0,  10.0),
        ScoreBand( 5.0, 10.0,         11.0,  30.0),
        ScoreBand(10.0, 15.0,         31.0,  55.0),
        ScoreBand(15.0, 21.0,         56.0,  75.0),
        ScoreBand(21.0, 30.0,         76.0,  90.0),
        ScoreBand(30.0, float("inf"), 91.0, 100.0),
    ),

    # 2.2  Escalation Suppression Rate
    # Condition: percentage of escalations resolved below VP level without
    #            executive visibility
    # Normal: higher percentage → higher risk score
    # Spec bands: 0–30%→0–15, 31–50%→16–35, 51–65%→36–55,
    #             66–75%→56–75, 76%+→76–100
    # Missing data default: 75
    "2.2": (
        ScoreBand( 0.0, 30.0,          0.0,  15.0),
        ScoreBand(30.0, 50.0,         16.0,  35.0),
        ScoreBand(50.0, 65.0,         36.0,  55.0),
        ScoreBand(65.0, 75.0,         56.0,  75.0),
        ScoreBand(75.0, float("inf"), 76.0, 100.0),
    ),

    # 2.3  Decision Latency
    # Condition: average days from decision required to decision documented
    # Normal: more days → higher risk score
    # Spec bands: 0–3d→0–10, 4–7d→11–30, 8–14d→31–55,
    #             15–21d→56–75, 22+d→76–100
    # Missing data default: 80
    "2.3": (
        ScoreBand( 0.0,  3.0,          0.0,  10.0),
        ScoreBand( 3.0,  7.0,         11.0,  30.0),
        ScoreBand( 7.0, 14.0,         31.0,  55.0),
        ScoreBand(14.0, 21.0,         56.0,  75.0),
        ScoreBand(21.0, float("inf"), 76.0, 100.0),
    ),

    # 2.4  Accountability Clarity Index  [INVERTED]
    # Condition: percentage of active initiatives with a single named accountable
    #            decision-maker documented
    # Inverted: higher percentage → lower risk score
    # Spec bands: 90–100%→0–10, 75–89%→11–30, 60–74%→31–55,
    #             45–59%→56–75, <45%→76–100
    # Missing data default: 70
    "2.4": (
        ScoreBand( 0.0, 45.0,         100.0,  76.0),
        ScoreBand(45.0, 60.0,          75.0,  56.0),
        ScoreBand(60.0, 75.0,          55.0,  31.0),
        ScoreBand(75.0, 90.0,          30.0,  11.0),
        ScoreBand(90.0, float("inf"),  10.0,   0.0),
    ),

    # =======================================================================
    # DIMENSION 3 — EXECUTION VISIBILITY
    # =======================================================================

    # 3.1  Telemetry Coverage Ratio  [INVERTED]
    # Condition: percentage of active initiatives with structured, documented
    #            operational data being collected
    # Inverted: higher coverage → lower risk score
    # Spec bands: 90–100%→0–10, 75–89%→11–30, 60–74%→31–55,
    #             45–59%→56–75, <45%→76–100
    # Missing data default: 85
    "3.1": (
        ScoreBand( 0.0, 45.0,         100.0,  76.0),
        ScoreBand(45.0, 60.0,          75.0,  56.0),
        ScoreBand(60.0, 75.0,          55.0,  31.0),
        ScoreBand(75.0, 90.0,          30.0,  11.0),
        ScoreBand(90.0, float("inf"),  10.0,   0.0),
    ),

    # 3.2  Dependency Transparency Score  [INVERTED]
    # Condition: percentage of known cross-program dependencies formally
    #            documented and tracked
    # Inverted: higher documentation rate → lower risk score
    # Spec bands: 90–100%→0–10, 70–89%→11–30, 50–69%→31–55,
    #             30–49%→56–75, <30%→76–100
    # Missing data default: 80
    "3.2": (
        ScoreBand( 0.0, 30.0,         100.0,  76.0),
        ScoreBand(30.0, 50.0,          75.0,  56.0),
        ScoreBand(50.0, 70.0,          55.0,  31.0),
        ScoreBand(70.0, 90.0,          30.0,  11.0),
        ScoreBand(90.0, float("inf"),  10.0,   0.0),
    ),

    # 3.3  Reporting Lag Indicator
    # Condition: average calendar days from operational event to executive reporting
    # Normal: more days → higher risk score
    # Spec bands: 0–3d→0–10, 4–7d→11–30, 8–14d→31–55,
    #             15–21d→56–75, 22+d→76–100
    # Missing data default: 65
    "3.3": (
        ScoreBand( 0.0,  3.0,          0.0,  10.0),
        ScoreBand( 3.0,  7.0,         11.0,  30.0),
        ScoreBand( 7.0, 14.0,         31.0,  55.0),
        ScoreBand(14.0, 21.0,         56.0,  75.0),
        ScoreBand(21.0, float("inf"), 76.0, 100.0),
    ),

    # 3.4  Leadership Visibility Index  [INVERTED]
    # Condition: direct executive touchpoints with delivery teams per month
    # Inverted: more touchpoints → lower risk score
    # Spec bands: 4+/mo→0–10, 2–3/mo→11–30, 1/mo→31–55,
    #             1/qtr (≈0.33/mo)→56–75, none→76–100
    # Condition unit: touchpoints per month (1 per quarter = 0.33/month)
    # Band boundaries: 0–0.25/mo = no structured touchpoints,
    #                  0.25–1.0/mo = quarterly cadence,
    #                  1.0–2.0/mo = monthly cadence,
    #                  2.0–4.0/mo = 2–3 per month,
    #                  4.0+/mo = 4 or more per month
    # Missing data default: 65
    "3.4": (
        ScoreBand(0.0,  0.25,         100.0,  76.0),
        ScoreBand(0.25, 1.0,           75.0,  56.0),
        ScoreBand(1.0,  2.0,           55.0,  31.0),
        ScoreBand(2.0,  4.0,           30.0,  11.0),
        ScoreBand(4.0,  float("inf"),  10.0,   0.0),
    ),

    # =======================================================================
    # DIMENSION 4 — REPORTING INTEGRITY
    # =======================================================================

    # 4.1  False-Green Incidence Rate
    # Condition: percentage of programs reporting Green status with one or more
    #            open Critical blockers in the same period
    # Normal: higher percentage → higher risk score
    # Spec bands: 0%→0–5, 1–5%→6–20, 6–15%→21–45,
    #             16–30%→46–70, 31%+→71–100
    # Missing data default: 80
    "4.1": (
        ScoreBand( 0.0,  1.0,          0.0,   5.0),
        ScoreBand( 1.0,  6.0,          6.0,  20.0),
        ScoreBand( 6.0, 16.0,         21.0,  45.0),
        ScoreBand(16.0, 31.0,         46.0,  70.0),
        ScoreBand(31.0, float("inf"), 71.0, 100.0),
    ),

    # 4.2  Manual Curation Index  [NON-COMPUTABLE]
    # Condition: estimated percentage of status reports passing through human
    #            editorial review before reaching executive audiences
    # Normal: higher curation rate → higher risk score
    # Spec bands: 0–10%→0–10, 11–25%→11–25, 26–50%→26–50,
    #             51–75%→51–70, 76–100%→71–100
    # This sub-category always scores at its missing-data default (75).
    # Bands are retained for reference and to support override score entry.
    "4.2": (
        ScoreBand( 0.0, 10.0,          0.0,  10.0),
        ScoreBand(10.0, 25.0,         11.0,  25.0),
        ScoreBand(25.0, 50.0,         26.0,  50.0),
        ScoreBand(50.0, 75.0,         51.0,  70.0),
        ScoreBand(75.0, float("inf"), 71.0, 100.0),
    ),

    # 4.3  Reporting Incentive Alignment  [NON-COMPUTABLE]
    # Condition: qualitative category index assessed by the Managing Partner
    #            during discovery and review sessions
    #   0 = explicit norms rewarding candor; safe to report bad news
    #   1 = neutral environment; neither penalized nor rewarded
    #   2 = some evidence of preference for positive framing
    #   3 = clear pattern of messaging management before escalation
    #   4 = direct evidence of fear-based reporting suppression
    # Normal: higher index → higher risk score
    # Spec bands: 0→0–15, 1→16–35, 2→36–60, 3→61–80, 4→81–100
    # Default behavior if no observation possible: 40 (per spec Part 4).
    # This sub-category always scores at its missing-data default (40).
    # Bands are retained for override score entry by the Managing Partner.
    "4.3": (
        ScoreBand(0.0, 1.0,           0.0,  15.0),
        ScoreBand(1.0, 2.0,          16.0,  35.0),
        ScoreBand(2.0, 3.0,          36.0,  60.0),
        ScoreBand(3.0, 4.0,          61.0,  80.0),
        ScoreBand(4.0, float("inf"), 81.0, 100.0),
    ),

    # 4.4  Confidence Reliability Score  [INVERTED]
    # Condition: percentage of programs where reported confidence levels
    #            (High/Medium/Low) are supported by documented evidence
    # Inverted: higher documentation rate → lower risk score
    # Spec bands: 90–100%→0–10, 70–89%→11–30, 50–69%→31–55,
    #             30–49%→56–75, <30%→76–100
    # Missing data default: 70
    "4.4": (
        ScoreBand( 0.0, 30.0,         100.0,  76.0),
        ScoreBand(30.0, 50.0,          75.0,  56.0),
        ScoreBand(50.0, 70.0,          55.0,  31.0),
        ScoreBand(70.0, 90.0,          30.0,  11.0),
        ScoreBand(90.0, float("inf"),  10.0,   0.0),
    ),

    # =======================================================================
    # DIMENSION 5 — ORGANIZATIONAL SUSTAINABILITY
    # =======================================================================

    # 5.1  Team Utilization Pressure
    # Condition: highest team utilization percentage across the portfolio
    # Normal: higher utilization → higher risk score
    # Spec bands: ≤90%→0–10, 91–100%→11–30, 101–110%→31–55,
    #             111–125%→56–75, 126–140%→76–90, >140%→91–100
    # Missing data default: 75
    "5.1": (
        ScoreBand(  0.0,  90.0,          0.0,  10.0),
        ScoreBand( 90.0, 100.0,         11.0,  30.0),
        ScoreBand(100.0, 110.0,         31.0,  55.0),
        ScoreBand(110.0, 125.0,         56.0,  75.0),
        ScoreBand(125.0, 140.0,         76.0,  90.0),
        ScoreBand(140.0, float("inf"),  91.0, 100.0),
    ),

    # 5.2  Reprioritization Impact Rate  [NON-COMPUTABLE]
    # Condition: average reprioritization events per team per month
    # Normal: higher rate → higher risk score
    # Spec bands: 0–0.5/mo→0–10, 0.6–1.0/mo→11–30, 1.1–2.0/mo→31–55,
    #             2.1–3.0/mo→56–75, 3.1+/mo→76–100
    # This sub-category always scores at its missing-data default (65).
    # Bands are retained for reference and to support override score entry.
    "5.2": (
        ScoreBand(0.0, 0.5,          0.0,  10.0),
        ScoreBand(0.5, 1.0,         11.0,  30.0),
        ScoreBand(1.0, 2.0,         31.0,  55.0),
        ScoreBand(2.0, 3.0,         56.0,  75.0),
        ScoreBand(3.0, float("inf"), 76.0, 100.0),
    ),

    # 5.3  Reactive Work Ratio
    # Condition: estimated percentage of team capacity consumed by unplanned,
    #            reactive work (fire-fighting, rework, unscheduled escalation)
    # Normal: higher percentage → higher risk score
    # Spec bands: 0–10%→0–10, 11–20%→11–30, 21–30%→31–55,
    #             31–40%→56–75, 41%+→76–100
    # Missing data default: 60
    "5.3": (
        ScoreBand( 0.0, 10.0,          0.0,  10.0),
        ScoreBand(10.0, 20.0,         11.0,  30.0),
        ScoreBand(20.0, 30.0,         31.0,  55.0),
        ScoreBand(30.0, 40.0,         56.0,  75.0),
        ScoreBand(40.0, float("inf"), 76.0, 100.0),
    ),

    # 5.4  Adaptive Capacity Reserve  [INVERTED]
    # Condition: estimated percentage of organizational capacity available to
    #            absorb new transformation demand without degrading existing programs
    # Inverted: higher available capacity → lower risk score
    # Spec bands: 25%+→0–10, 15–24%→11–30, 10–14%→31–55,
    #             5–9%→56–75, <5%→76–100
    # Missing data default: 70
    "5.4": (
        ScoreBand( 0.0,  5.0,         100.0,  76.0),
        ScoreBand( 5.0, 10.0,          75.0,  56.0),
        ScoreBand(10.0, 15.0,          55.0,  31.0),
        ScoreBand(15.0, 25.0,          30.0,  11.0),
        ScoreBand(25.0, float("inf"),  10.0,   0.0),
    ),

    # 5.5  Retention Risk Indicator
    # Condition: number of identified high-risk retention flags on
    #            critical-path program staff
    # Normal: more flags → higher risk score
    # Spec bands: 0 flags→0–5, 1–2→6–25, 3–5→26–55, 6–8→56–75, 9+→76–100
    # Missing data default: 55
    "5.5": (
        ScoreBand(0.0, 1.0,          0.0,   5.0),
        ScoreBand(1.0, 3.0,          6.0,  25.0),
        ScoreBand(3.0, 6.0,         26.0,  55.0),
        ScoreBand(6.0, 9.0,         56.0,  75.0),
        ScoreBand(9.0, float("inf"), 76.0, 100.0),
    ),
}


# ---------------------------------------------------------------------------
# Missing data defaults
# ---------------------------------------------------------------------------
# Per-sub-category score applied when required data is absent and no exception
# has been granted by the Managing Partner.
#
# Values are taken directly from the "Missing data behavior" notes in spec
# Part 4. Sub-categories without an explicit spec note apply the general
# default of 75 per spec Part 1 ("Default Rule").
#
# Non-computable sub-categories use these values as their computed score for
# every submission until a manual override is applied.

MISSING_DATA_DEFAULTS: Final[dict[str, float]] = {
    "1.1": 75.0,  # Priority Inflation Index            (spec: scores at 75)
    "1.2": 75.0,  # Concurrent Transformation Density   (general rule)
    "1.3": 65.0,  # Priority Change Frequency           (spec: scores at 65; non-computable)
    "1.4": 70.0,  # Initiative Persistence Rate         (spec: scores at 70)
    "2.1": 80.0,  # Escalation Resolution Latency       (spec: scores at 80)
    "2.2": 75.0,  # Escalation Suppression Rate         (spec: scores at 75)
    "2.3": 80.0,  # Decision Latency                    (spec: scores at 80)
    "2.4": 70.0,  # Accountability Clarity Index        (spec: scores at 70)
    "3.1": 85.0,  # Telemetry Coverage Ratio            (spec: scores at 85)
    "3.2": 80.0,  # Dependency Transparency Score       (spec: scores at 80)
    "3.3": 65.0,  # Reporting Lag Indicator             (spec: scores at 65)
    "3.4": 65.0,  # Leadership Visibility Index         (spec: scores at 65)
    "4.1": 80.0,  # False-Green Incidence Rate          (spec: scores at 80)
    "4.2": 75.0,  # Manual Curation Index               (general rule; non-computable)
    "4.3": 40.0,  # Reporting Incentive Alignment       (spec: 40 moderate; non-computable)
    "4.4": 70.0,  # Confidence Reliability Score        (spec: scores at 70)
    "5.1": 75.0,  # Team Utilization Pressure           (spec: scores at 75)
    "5.2": 65.0,  # Reprioritization Impact Rate        (spec: scores at 65; non-computable)
    "5.3": 60.0,  # Reactive Work Ratio                 (spec: scores at 60)
    "5.4": 70.0,  # Adaptive Capacity Reserve           (spec: scores at 70)
    "5.5": 55.0,  # Retention Risk Indicator            (spec: scores at 55)
}


# ---------------------------------------------------------------------------
# Non-computable sub-categories
# ---------------------------------------------------------------------------
# These four sub-categories cannot be computed from a single intake submission.
# They always score at their MISSING_DATA_DEFAULTS value unless the Managing
# Partner applies a manual override (which requires a minimum 50-word
# justification per spec Part 2).

NON_COMPUTABLE_SUB_CATEGORIES: Final[frozenset[str]] = frozenset({
    "1.3",  # Priority Change Frequency:    requires reclassification history across periods
    "4.2",  # Manual Curation Index:         qualitative; requires client self-report
    "4.3",  # Reporting Incentive Alignment: requires Managing Partner direct observation
    "5.2",  # Reprioritization Impact Rate:  requires event log tracking across periods
})


# ---------------------------------------------------------------------------
# Sub-category weights within each dimension
# ---------------------------------------------------------------------------
# Each weight is the sub-category's contribution to its parent dimension score.
# Weights within a dimension sum to 1.0 (verified below).
# Source: spec Part 4, "DIMENSION N COMPOSITE CALCULATION" block.

DIMENSION_WEIGHTS: Final[dict[str, dict[str, float]]] = {
    "1": {   # Strategic Saturation
        "1.1": 0.30,  # Priority Inflation Index
        "1.2": 0.30,  # Concurrent Transformation Density
        "1.3": 0.25,  # Priority Change Frequency
        "1.4": 0.15,  # Initiative Persistence Rate
    },
    "2": {   # Governance Responsiveness
        "2.1": 0.30,  # Escalation Resolution Latency
        "2.2": 0.30,  # Escalation Suppression Rate
        "2.3": 0.25,  # Decision Latency
        "2.4": 0.15,  # Accountability Clarity Index
    },
    "3": {   # Execution Visibility
        "3.1": 0.30,  # Telemetry Coverage Ratio
        "3.2": 0.30,  # Dependency Transparency Score
        "3.3": 0.25,  # Reporting Lag Indicator
        "3.4": 0.15,  # Leadership Visibility Index
    },
    "4": {   # Reporting Integrity
        "4.1": 0.35,  # False-Green Incidence Rate
        "4.2": 0.25,  # Manual Curation Index
        "4.3": 0.25,  # Reporting Incentive Alignment
        "4.4": 0.15,  # Confidence Reliability Score
    },
    "5": {   # Organizational Sustainability
        "5.1": 0.30,  # Team Utilization Pressure
        "5.2": 0.25,  # Reprioritization Impact Rate
        "5.3": 0.20,  # Reactive Work Ratio
        "5.4": 0.15,  # Adaptive Capacity Reserve
        "5.5": 0.10,  # Retention Risk Indicator
    },
}

# Compile-time weight integrity check.  Each dimension must sum to 1.0.
# A ValueError here means a weight was edited without keeping the sum correct.
for _dim, _weights in DIMENSION_WEIGHTS.items():
    _total = round(sum(_weights.values()), 10)
    if _total != 1.0:
        raise ValueError(
            f"Dimension {_dim} sub-category weights sum to {_total}, expected 1.0. "
            "Update scoring/constants.py to correct the weights."
        )


# ---------------------------------------------------------------------------
# OEI composite dimension weights
# ---------------------------------------------------------------------------
# Each dimension's contribution to the OEI composite score.
# All five dimensions are equally weighted at 20% per spec Part 5.
# Any adjustment to these weights requires a documented rationale and applies
# only to new submissions — never retroactively applied to prior period scores.

COMPOSITE_WEIGHTS: Final[dict[str, float]] = {
    "1": 0.20,  # Strategic Saturation
    "2": 0.20,  # Governance Responsiveness
    "3": 0.20,  # Execution Visibility
    "4": 0.20,  # Reporting Integrity
    "5": 0.20,  # Organizational Sustainability
}

# Compile-time weight integrity check.
_composite_total = round(sum(COMPOSITE_WEIGHTS.values()), 10)
if _composite_total != 1.0:
    raise ValueError(
        f"Composite dimension weights sum to {_composite_total}, expected 1.0. "
        "Update scoring/constants.py to correct the weights."
    )


# ---------------------------------------------------------------------------
# Alert thresholds
# ---------------------------------------------------------------------------
# OEI composite score thresholds that trigger internal alerts to the
# Managing Partner dashboard.  A score >= the threshold activates the
# corresponding alert level.  Levels align with the Risk Classification bands.

ALERT_THRESHOLDS: Final[dict[str, float]] = {
    "watch":    41.0,  # Elevated risk band entry point
    "elevated": 61.0,  # High Risk band entry point
    "critical": 81.0,  # Critical risk band entry point
}


# ---------------------------------------------------------------------------
# Risk classification bands
# ---------------------------------------------------------------------------
# Maps an OEI composite score to its plain-language risk classification.
# Tuple layout: (score_lower_inclusive, score_upper_inclusive, label, plain_language)
# Source: spec Part 1, "Scale Definition" table.

RISK_CLASSIFICATION: Final[tuple[tuple[float, float, str, str], ...]] = (
    (  0.0,  20.0, "Low Risk",  "Systems are functioning with adequate visibility and governance"),
    ( 21.0,  40.0, "Moderate",  "Manageable gaps exist; require monitoring but not immediate action"),
    ( 41.0,  60.0, "Elevated",  "Structural gaps are present; execution risk is increasing"),
    ( 61.0,  80.0, "High Risk", "Material operational gaps; financial exposure is probable without intervention"),
    ( 81.0, 100.0, "Critical",  "Systemic failure conditions; financial exposure is active or imminent"),
)
