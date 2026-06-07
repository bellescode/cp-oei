"""
tests/test_dimensions.py
CPOI Platform — Unit tests for scoring/dimensions.py

Covers:
  1. classify_score         — boundary-exact classification for all five bands
  2. assign_impact_indicator — threshold-exact indicator assignment
  3. compute_dimension      — weighted sum correctness with hand-calculated values
  4. compute_oei_score      — composite score and classification from known
                              sub-category inputs

All expected scores are derived from the spec weight tables and can be
reproduced by hand from DIMENSION_WEIGHTS and COMPOSITE_WEIGHTS in constants.py.

Run from the project root:
    python -m pytest tests/test_dimensions.py -v
"""

import pytest

from scoring.constants import COMPOSITE_WEIGHTS, DIMENSION_WEIGHTS, MISSING_DATA_DEFAULTS
from scoring.dimensions import (
    IMPACT_CONTRIBUTING,
    IMPACT_DRIVING,
    IMPACT_MONITORED,
    IMPACT_PRESENT,
    DimensionResult,
    OEIScoreResult,
    assign_impact_indicator,
    classify_score,
    compute_dimension,
    compute_oei_score,
)
from scoring.sub_categories import SubCategoryResult

_TOL = 1e-4

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sub_result(sub_id: str, score: float) -> SubCategoryResult:
    """Build a synthetic SubCategoryResult with a specific score."""
    return SubCategoryResult(
        sub_category_id=sub_id,
        score=score,
        is_missing_data=False,
        is_non_computable=False,
        computed_value=None,
    )


def _all_sub_results_at(score: float) -> dict[str, SubCategoryResult]:
    """Return all 21 sub-category results set to the same score."""
    return {
        sub_id: _make_sub_result(sub_id, score)
        for dim_weights in DIMENSION_WEIGHTS.values()
        for sub_id in dim_weights
    }


# ---------------------------------------------------------------------------
# Section 1: classify_score
# ---------------------------------------------------------------------------


class TestClassifyScore:
    """classify_score returns the correct band label at every boundary."""

    # Band definitions from RISK_CLASSIFICATION:
    #   0–20   → Low Risk
    #   21–40  → Moderate
    #   41–60  → Elevated
    #   61–80  → High Risk
    #   81–100 → Critical

    def test_midpoint_low_risk(self):
        assert classify_score(10.0) == "Low Risk"

    def test_midpoint_moderate(self):
        assert classify_score(30.0) == "Moderate"

    def test_midpoint_elevated(self):
        assert classify_score(50.0) == "Elevated"

    def test_midpoint_high_risk(self):
        assert classify_score(70.0) == "High Risk"

    def test_midpoint_critical(self):
        assert classify_score(90.0) == "Critical"

    def test_lower_boundary_low_risk(self):
        assert classify_score(0.0) == "Low Risk"

    def test_upper_boundary_low_risk(self):
        assert classify_score(20.0) == "Low Risk"

    def test_lower_boundary_moderate(self):
        assert classify_score(21.0) == "Moderate"

    def test_upper_boundary_moderate(self):
        assert classify_score(40.0) == "Moderate"

    def test_lower_boundary_elevated(self):
        assert classify_score(41.0) == "Elevated"

    def test_upper_boundary_elevated(self):
        assert classify_score(60.0) == "Elevated"

    def test_lower_boundary_high_risk(self):
        assert classify_score(61.0) == "High Risk"

    def test_upper_boundary_high_risk(self):
        assert classify_score(80.0) == "High Risk"

    def test_lower_boundary_critical(self):
        assert classify_score(81.0) == "Critical"

    def test_upper_boundary_critical(self):
        assert classify_score(100.0) == "Critical"

    def test_above_100_returns_critical(self):
        # Defensive fallback for any rounding edge case.
        assert classify_score(100.1) == "Critical"


# ---------------------------------------------------------------------------
# Section 2: assign_impact_indicator
# ---------------------------------------------------------------------------


class TestAssignImpactIndicator:
    """assign_impact_indicator maps scores to the correct band label."""

    # Thresholds aligned with RISK_CLASSIFICATION band entry points:
    #   score >= 61  → DRIVING
    #   41 <= score < 61  → CONTRIBUTING
    #   21 <= score < 41  → PRESENT
    #   score <= 20  → MONITORED

    def test_monitored_at_zero(self):
        assert assign_impact_indicator(0.0) == IMPACT_MONITORED

    def test_monitored_at_upper_boundary(self):
        assert assign_impact_indicator(20.0) == IMPACT_MONITORED

    def test_present_at_lower_boundary(self):
        assert assign_impact_indicator(21.0) == IMPACT_PRESENT

    def test_present_midpoint(self):
        assert assign_impact_indicator(30.0) == IMPACT_PRESENT

    def test_present_at_upper_boundary(self):
        assert assign_impact_indicator(40.9) == IMPACT_PRESENT

    def test_contributing_at_lower_boundary(self):
        assert assign_impact_indicator(41.0) == IMPACT_CONTRIBUTING

    def test_contributing_midpoint(self):
        assert assign_impact_indicator(50.0) == IMPACT_CONTRIBUTING

    def test_contributing_at_upper_boundary(self):
        assert assign_impact_indicator(60.9) == IMPACT_CONTRIBUTING

    def test_driving_at_lower_boundary(self):
        assert assign_impact_indicator(61.0) == IMPACT_DRIVING

    def test_driving_midpoint(self):
        assert assign_impact_indicator(75.0) == IMPACT_DRIVING

    def test_driving_at_100(self):
        assert assign_impact_indicator(100.0) == IMPACT_DRIVING


# ---------------------------------------------------------------------------
# Section 3: compute_dimension
# ---------------------------------------------------------------------------


class TestComputeDimension:
    """
    compute_dimension computes the weighted sub-category sum for one dimension.

    Hand-calculation examples
    ─────────────────────────
    Dimension 1 weights: {1.1:0.30, 1.2:0.30, 1.3:0.25, 1.4:0.15}

    Test A — uniform input (all sub-categories = 50.0):
      dim1 = 50*0.30 + 50*0.30 + 50*0.25 + 50*0.15 = 50.0

    Test B — mixed input:
      Sub-scores: 1.1=80, 1.2=40, 1.3=60, 1.4=20
      dim1 = 80*0.30 + 40*0.30 + 60*0.25 + 20*0.15
           = 24.0 + 12.0 + 15.0 + 3.0 = 54.0

    Dimension 5 weights: {5.1:0.30, 5.2:0.25, 5.3:0.20, 5.4:0.15, 5.5:0.10}
    Test C — mixed:
      Sub-scores: 5.1=70, 5.2=30, 5.3=50, 5.4=20, 5.5=90
      dim5 = 70*0.30 + 30*0.25 + 50*0.20 + 20*0.15 + 90*0.10
           = 21.0 + 7.5 + 10.0 + 3.0 + 9.0 = 50.5
    """

    def test_dim1_uniform_score_50(self):
        all_sub = _all_sub_results_at(50.0)
        result = compute_dimension("1", all_sub)
        assert result.score == pytest.approx(50.0, abs=_TOL)
        assert result.dimension_id == "1"
        assert result.dimension_name == "Strategic Saturation"

    def test_dim1_mixed_hand_calculated(self):
        """
        1.1=80, 1.2=40, 1.3=60, 1.4=20
        dim1 = 80*0.30 + 40*0.30 + 60*0.25 + 20*0.15 = 54.0
        """
        scores = {"1.1": 80.0, "1.2": 40.0, "1.3": 60.0, "1.4": 20.0}
        # Build full sub_results dict; non-dim-1 entries use 0.0.
        all_sub = _all_sub_results_at(0.0)
        for sub_id, score in scores.items():
            all_sub[sub_id] = _make_sub_result(sub_id, score)

        result = compute_dimension("1", all_sub)
        expected = 80*0.30 + 40*0.30 + 60*0.25 + 20*0.15
        assert result.score == pytest.approx(expected, abs=_TOL)

    def test_dim2_hand_calculated(self):
        """
        Dim 2 weights: {2.1:0.30, 2.2:0.30, 2.3:0.25, 2.4:0.15}
        Scores: 2.1=90, 2.2=10, 2.3=70, 2.4=50
        dim2 = 90*0.30 + 10*0.30 + 70*0.25 + 50*0.15
             = 27.0 + 3.0 + 17.5 + 7.5 = 55.0
        """
        scores = {"2.1": 90.0, "2.2": 10.0, "2.3": 70.0, "2.4": 50.0}
        all_sub = _all_sub_results_at(0.0)
        for sub_id, score in scores.items():
            all_sub[sub_id] = _make_sub_result(sub_id, score)
        result = compute_dimension("2", all_sub)
        expected = 90*0.30 + 10*0.30 + 70*0.25 + 50*0.15
        assert result.score == pytest.approx(expected, abs=_TOL)

    def test_dim5_mixed_hand_calculated(self):
        """
        Dim 5 weights: {5.1:0.30, 5.2:0.25, 5.3:0.20, 5.4:0.15, 5.5:0.10}
        Scores: 5.1=70, 5.2=30, 5.3=50, 5.4=20, 5.5=90
        dim5 = 70*0.30 + 30*0.25 + 50*0.20 + 20*0.15 + 90*0.10 = 50.5
        """
        scores = {"5.1": 70.0, "5.2": 30.0, "5.3": 50.0, "5.4": 20.0, "5.5": 90.0}
        all_sub = _all_sub_results_at(0.0)
        for sub_id, score in scores.items():
            all_sub[sub_id] = _make_sub_result(sub_id, score)
        result = compute_dimension("5", all_sub)
        expected = 70*0.30 + 30*0.25 + 50*0.20 + 20*0.15 + 90*0.10
        assert result.score == pytest.approx(expected, abs=_TOL)

    def test_dimension_result_classification_matches_score(self):
        """DimensionResult.classification must match classify_score(score)."""
        all_sub = _all_sub_results_at(72.0)   # High Risk band
        result = compute_dimension("3", all_sub)
        assert result.classification == "High Risk"
        assert result.score == pytest.approx(72.0, abs=_TOL)

    def test_impact_indicators_assigned_to_all_sub_categories(self):
        all_sub = _all_sub_results_at(50.0)
        result = compute_dimension("1", all_sub)
        weights = DIMENSION_WEIGHTS["1"]
        assert set(result.impact_indicators.keys()) == set(weights.keys())

    def test_impact_indicators_values_valid(self):
        valid_labels = {IMPACT_DRIVING, IMPACT_CONTRIBUTING, IMPACT_PRESENT, IMPACT_MONITORED}
        all_sub = _all_sub_results_at(50.0)
        result = compute_dimension("2", all_sub)
        for sub_id, label in result.impact_indicators.items():
            assert label in valid_labels, f"{sub_id} has unexpected label {label}"

    def test_driving_indicator_assigned_when_score_above_61(self):
        all_sub = _all_sub_results_at(75.0)   # Above DRIVING threshold
        result = compute_dimension("1", all_sub)
        for sub_id in DIMENSION_WEIGHTS["1"]:
            assert result.impact_indicators[sub_id] == IMPACT_DRIVING

    def test_monitored_indicator_assigned_when_score_at_or_below_20(self):
        all_sub = _all_sub_results_at(10.0)
        result = compute_dimension("1", all_sub)
        for sub_id in DIMENSION_WEIGHTS["1"]:
            assert result.impact_indicators[sub_id] == IMPACT_MONITORED

    def test_sub_category_results_included_in_dimension_result(self):
        all_sub = _all_sub_results_at(50.0)
        result = compute_dimension("3", all_sub)
        for sub_id in DIMENSION_WEIGHTS["3"]:
            assert sub_id in result.sub_category_results

    def test_score_at_zero_with_all_zero_inputs(self):
        all_sub = _all_sub_results_at(0.0)
        result = compute_dimension("1", all_sub)
        assert result.score == pytest.approx(0.0, abs=_TOL)

    def test_score_at_100_with_all_100_inputs(self):
        all_sub = _all_sub_results_at(100.0)
        result = compute_dimension("1", all_sub)
        assert result.score == pytest.approx(100.0, abs=_TOL)


# ---------------------------------------------------------------------------
# Section 4: compute_oei_score
# ---------------------------------------------------------------------------


class TestComputeOEIScore:
    """
    compute_oei_score produces correct composite from all 21 sub-category scores.

    All five dimensions equally weighted at 0.20.
    When all sub-categories score 50.0:
      Each dimension score = 50.0
      Composite = 50.0*0.20 * 5 = 50.0 → Elevated band

    Mixed dimension case:
      dim1=60, dim2=40, dim3=80, dim4=20, dim5=100 (via all sub-cats set to dim score)
      composite = (60+40+80+20+100) * 0.20 = 300 * 0.20 = 60.0 → Elevated
    """

    def test_uniform_50_composite_equals_50(self):
        all_sub = _all_sub_results_at(50.0)
        result = compute_oei_score(all_sub)
        assert result.composite_score == pytest.approx(50.0, abs=_TOL)
        assert result.composite_classification == "Elevated"

    def test_uniform_0_composite_equals_0(self):
        all_sub = _all_sub_results_at(0.0)
        result = compute_oei_score(all_sub)
        assert result.composite_score == pytest.approx(0.0, abs=_TOL)
        assert result.composite_classification == "Low Risk"

    def test_uniform_100_composite_equals_100(self):
        all_sub = _all_sub_results_at(100.0)
        result = compute_oei_score(all_sub)
        assert result.composite_score == pytest.approx(100.0, abs=_TOL)
        assert result.composite_classification == "Critical"

    def test_mixed_composite_hand_calculated(self):
        """
        Set all sub-cats in each dimension to the same value so the dimension
        score equals that value (weights sum to 1.0 within each dimension).

        Dim 1 → all sub-cats = 60  → dim1 = 60.0
        Dim 2 → all sub-cats = 40  → dim2 = 40.0
        Dim 3 → all sub-cats = 80  → dim3 = 80.0
        Dim 4 → all sub-cats = 20  → dim4 = 20.0
        Dim 5 → all sub-cats = 50  → dim5 = 50.0
        composite = (60+40+80+20+50) * 0.20 = 250 * 0.20 = 50.0
        """
        dim_target = {"1": 60.0, "2": 40.0, "3": 80.0, "4": 20.0, "5": 50.0}
        all_sub: dict[str, SubCategoryResult] = {}
        for dim_id, target_score in dim_target.items():
            for sub_id in DIMENSION_WEIGHTS[dim_id]:
                all_sub[sub_id] = _make_sub_result(sub_id, target_score)

        result = compute_oei_score(all_sub)
        expected_composite = sum(
            dim_target[d] * COMPOSITE_WEIGHTS[d] for d in COMPOSITE_WEIGHTS
        )
        assert result.composite_score == pytest.approx(expected_composite, abs=_TOL)

    def test_all_five_dimensions_present(self):
        all_sub = _all_sub_results_at(50.0)
        result = compute_oei_score(all_sub)
        assert set(result.dimension_results.keys()) == {"1", "2", "3", "4", "5"}

    def test_dimension_scores_match_compute_dimension_individually(self):
        """
        For each dimension, compute_oei_score and compute_dimension called
        separately must produce identical scores.
        """
        all_sub = _all_sub_results_at(42.0)
        oei_result = compute_oei_score(all_sub)
        for dim_id in ("1", "2", "3", "4", "5"):
            individual = compute_dimension(dim_id, all_sub)
            assert oei_result.dimension_results[dim_id].score == pytest.approx(
                individual.score, abs=_TOL
            )

    def test_sub_category_results_accessible_from_oei_result(self):
        all_sub = _all_sub_results_at(50.0)
        result = compute_oei_score(all_sub)
        for sub_id in all_sub:
            assert sub_id in result.sub_category_results

    def test_calculated_at_is_iso_string(self):
        all_sub = _all_sub_results_at(50.0)
        result = compute_oei_score(all_sub)
        assert isinstance(result.calculated_at, str)
        # Must parse as ISO 8601 — datetime.fromisoformat should not raise.
        from datetime import datetime
        datetime.fromisoformat(result.calculated_at)

    def test_composite_classification_matches_classify_score(self):
        all_sub = _all_sub_results_at(65.0)   # Should be High Risk
        result = compute_oei_score(all_sub)
        assert result.composite_classification == classify_score(result.composite_score)

    def test_high_risk_composite(self):
        # All sub-cats = 70 → all dims = 70 → composite = 70 → High Risk
        all_sub = _all_sub_results_at(70.0)
        result = compute_oei_score(all_sub)
        assert result.composite_classification == "High Risk"

    def test_critical_composite(self):
        all_sub = _all_sub_results_at(85.0)
        result = compute_oei_score(all_sub)
        assert result.composite_classification == "Critical"

    def test_result_is_frozen_dataclass(self):
        """OEIScoreResult must be immutable (frozen dataclass)."""
        all_sub = _all_sub_results_at(50.0)
        result = compute_oei_score(all_sub)
        with pytest.raises((AttributeError, TypeError)):
            result.composite_score = 0.0  # type: ignore

    def test_dimension_result_is_frozen_dataclass(self):
        all_sub = _all_sub_results_at(50.0)
        result = compute_oei_score(all_sub)
        dim1 = result.dimension_results["1"]
        with pytest.raises((AttributeError, TypeError)):
            dim1.score = 0.0  # type: ignore
