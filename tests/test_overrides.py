"""
tests/test_overrides.py
CPOI Platform — Unit tests for scoring/overrides.py

Covers:
  1. _validate_override_input — every rejection condition with exact error messages
  2. OverrideRecord dataclass  — immutability and field types
  3. apply_overrides — all cascade types, hand-calculated expected scores
  4. apply_overrides — ordering (last override wins), immutability of original

Hand-calculation methodology (apply_overrides):
  All synthetic OEIScoreResult objects start with every sub-category at a
  known uniform score, so dimension scores and the composite are exactly
  that score (weights sum to 1.0). Overrides then shift one score and the
  expected post-override values are derived algebraically.

  Example — sub-category override cascade:
    Start: all sub-cats = 50.0 → all dims = 50.0 → composite = 50.0
    Override sub-cat "1.1" to 80.0.
    Dim 1 weights: {1.1:0.30, 1.2:0.30, 1.3:0.25, 1.4:0.15}
    New dim1 = 80*0.30 + 50*0.30 + 50*0.25 + 50*0.15
             = 24 + 15 + 12.5 + 7.5 = 59.0
    New composite = (59 + 50 + 50 + 50 + 50) / 5 = 259 / 5 = 51.8

Run from the project root:
    python -m pytest tests/test_overrides.py -v
"""

import uuid

import pytest

from scoring.constants import COMPOSITE_WEIGHTS, DIMENSION_WEIGHTS, MISSING_DATA_DEFAULTS
from scoring.dimensions import (
    DIMENSION_NAMES,
    DimensionResult,
    OEIScoreResult,
    assign_impact_indicator,
    classify_score,
)
from scoring.overrides import (
    EVIDENCE_SOURCES,
    JUSTIFICATION_MIN_WORDS,
    OVERRIDE_TYPES,
    OverrideRecord,
    _validate_override_input,
    apply_overrides,
)
from scoring.sub_categories import SubCategoryResult

_TOL = 1e-4

# A justification that always meets the 50-word minimum.
_JUST = (
    "During the direct observation session conducted on site this quarter, "
    "the managing partner witnessed significantly more governance touchpoints "
    "than were captured in the submitted telemetry data. The discrepancy is "
    "attributable to informal review meetings not recorded in the system. "
    "The calculated score therefore understates actual governance responsiveness "
    "as observed in the field by the managing partner."
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sub_result(sub_id: str, score: float) -> SubCategoryResult:
    return SubCategoryResult(
        sub_category_id=sub_id,
        score=score,
        is_missing_data=False,
        is_non_computable=False,
        computed_value=None,
    )


def _uniform_oei(score: float) -> OEIScoreResult:
    """
    Build an OEIScoreResult where every sub-category, dimension, and the
    composite all equal 'score'. Weights sum to 1.0 so the arithmetic holds.
    """
    sub_results: dict[str, SubCategoryResult] = {}
    for dim_weights in DIMENSION_WEIGHTS.values():
        for sub_id in dim_weights:
            sub_results[sub_id] = _make_sub_result(sub_id, score)

    dim_results: dict[str, DimensionResult] = {}
    for dim_id, dim_weights in DIMENSION_WEIGHTS.items():
        impact = {
            sub_id: assign_impact_indicator(score)
            for sub_id in dim_weights
        }
        dim_results[dim_id] = DimensionResult(
            dimension_id=dim_id,
            dimension_name=DIMENSION_NAMES[dim_id],
            score=score,
            classification=classify_score(score),
            sub_category_results={sub_id: sub_results[sub_id] for sub_id in dim_weights},
            impact_indicators=impact,
        )

    composite_score = sum(
        dim_results[d].score * COMPOSITE_WEIGHTS[d]
        for d in COMPOSITE_WEIGHTS
    )
    return OEIScoreResult(
        composite_score=composite_score,
        composite_classification=classify_score(composite_score),
        dimension_results=dim_results,
        sub_category_results=sub_results,
        calculated_at="2025-04-01T12:00:00+00:00",
    )


def _ov(
    override_type: str,
    target_id: str,
    override_score: float,
    applied_at: str = "2025-04-01T10:00:00+00:00",
) -> OverrideRecord:
    return OverrideRecord(
        override_id=str(uuid.uuid4()),
        submission_id="sub-test-001",
        client_id="cli-test-001",
        override_type=override_type,
        target_id=target_id,
        original_score=50.0,
        override_score=override_score,
        justification=_JUST,
        evidence_source="review_session",
        applied_by="managing_partner",
        applied_at=applied_at,
        is_active=True,
    )


# ---------------------------------------------------------------------------
# Section 1: _validate_override_input
# ---------------------------------------------------------------------------


class TestValidateOverrideInput:

    # --- override_type ---

    def test_invalid_override_type_raises(self):
        with pytest.raises(ValueError, match="override_type"):
            _validate_override_input("bad_type", "1.1", 50.0, _JUST, "review_session")

    def test_all_valid_override_types_accepted(self):
        for otype, tid in [
            ("sub_category", "1.1"),
            ("dimension",    "3"),
            ("composite",    "composite"),
        ]:
            _validate_override_input(otype, tid, 50.0, _JUST, "review_session")

    # --- target_id ---

    def test_invalid_sub_category_id_raises(self):
        with pytest.raises(ValueError, match="target_id"):
            _validate_override_input("sub_category", "0.0", 50.0, _JUST, "review_session")

    def test_valid_sub_category_ids_accepted(self):
        for sub_id in ("1.1", "2.4", "3.3", "4.2", "5.5"):
            _validate_override_input("sub_category", sub_id, 50.0, _JUST, "review_session")

    def test_invalid_dimension_id_raises(self):
        with pytest.raises(ValueError, match="target_id"):
            _validate_override_input("dimension", "6", 50.0, _JUST, "review_session")

    def test_valid_dimension_ids_accepted(self):
        for dim_id in ("1", "2", "3", "4", "5"):
            _validate_override_input("dimension", dim_id, 50.0, _JUST, "review_session")

    def test_composite_target_must_be_literal_composite(self):
        with pytest.raises(ValueError, match="target_id"):
            _validate_override_input("composite", "oei", 50.0, _JUST, "review_session")

    def test_composite_target_literal_accepted(self):
        _validate_override_input("composite", "composite", 50.0, _JUST, "review_session")

    # --- override_score ---

    def test_score_above_100_raises(self):
        with pytest.raises(ValueError, match="override_score"):
            _validate_override_input("sub_category", "1.1", 100.1, _JUST, "review_session")

    def test_score_below_0_raises(self):
        with pytest.raises(ValueError, match="override_score"):
            _validate_override_input("sub_category", "1.1", -0.1, _JUST, "review_session")

    def test_score_0_accepted(self):
        _validate_override_input("sub_category", "1.1", 0.0, _JUST, "review_session")

    def test_score_100_accepted(self):
        _validate_override_input("sub_category", "1.1", 100.0, _JUST, "review_session")

    def test_score_integer_accepted(self):
        # Integer should be accepted alongside float.
        _validate_override_input("sub_category", "1.1", 75, _JUST, "review_session")

    # --- justification ---

    def test_empty_justification_raises(self):
        with pytest.raises(ValueError, match="justification"):
            _validate_override_input("sub_category", "1.1", 50.0, "", "review_session")

    def test_whitespace_only_justification_raises(self):
        with pytest.raises(ValueError, match="justification"):
            _validate_override_input("sub_category", "1.1", 50.0, "   \n  ", "review_session")

    def test_49_word_justification_raises(self):
        short = " ".join(["word"] * 49)
        with pytest.raises(ValueError, match="justification"):
            _validate_override_input("sub_category", "1.1", 50.0, short, "review_session")

    def test_50_word_justification_accepted(self):
        exact = " ".join(["word"] * 50)
        _validate_override_input("sub_category", "1.1", 50.0, exact, "review_session")

    def test_51_word_justification_accepted(self):
        over = " ".join(["word"] * 51)
        _validate_override_input("sub_category", "1.1", 50.0, over, "review_session")

    # --- evidence_source ---

    def test_invalid_evidence_source_raises(self):
        with pytest.raises(ValueError, match="evidence_source"):
            _validate_override_input("sub_category", "1.1", 50.0, _JUST, "slack_message")

    def test_all_valid_evidence_sources_accepted(self):
        for source in EVIDENCE_SOURCES:
            _validate_override_input("sub_category", "1.1", 50.0, _JUST, source)


# ---------------------------------------------------------------------------
# Section 2: OverrideRecord immutability
# ---------------------------------------------------------------------------


class TestOverrideRecordImmutability:

    def test_override_record_is_frozen(self):
        rec = _ov("sub_category", "1.1", 75.0)
        with pytest.raises((AttributeError, TypeError)):
            rec.override_score = 0.0  # type: ignore

    def test_override_record_field_types(self):
        rec = _ov("dimension", "2", 30.0)
        assert isinstance(rec.override_id, str)
        assert isinstance(rec.override_score, float)
        assert isinstance(rec.original_score, float)
        assert isinstance(rec.is_active, bool)


# ---------------------------------------------------------------------------
# Section 3: apply_overrides — empty list
# ---------------------------------------------------------------------------


class TestApplyOverridesEmpty:

    def test_empty_list_returns_same_object(self):
        original = _uniform_oei(50.0)
        result = apply_overrides(original, [])
        assert result is original


# ---------------------------------------------------------------------------
# Section 4: apply_overrides — sub-category override cascade
# ---------------------------------------------------------------------------


class TestApplyOverridesSubCategory:
    """
    Hand-calculation for sub-category override cascade.

    Start: all sub-cats = 50.0, all dims = 50.0, composite = 50.0.

    Override sub-cat "1.1" to 80.0:
      Dim 1 weights: {1.1:0.30, 1.2:0.30, 1.3:0.25, 1.4:0.15}
      New dim1 = 80*0.30 + 50*0.30 + 50*0.25 + 50*0.15
               = 24.0 + 15.0 + 12.5 + 7.5 = 59.0
      New composite = (59 + 50 + 50 + 50 + 50) * 0.20 = 51.8
    """

    def setup_method(self):
        self.original = _uniform_oei(50.0)

    def test_sub_cat_score_changed(self):
        result = apply_overrides(self.original, [_ov("sub_category", "1.1", 80.0)])
        assert result.sub_category_results["1.1"].score == pytest.approx(80.0, abs=_TOL)

    def test_other_sub_cats_unchanged(self):
        result = apply_overrides(self.original, [_ov("sub_category", "1.1", 80.0)])
        for sub_id in ("1.2", "1.3", "1.4", "2.1", "3.3"):
            assert result.sub_category_results[sub_id].score == pytest.approx(50.0, abs=_TOL)

    def test_dim1_score_recomputed_hand_calculated(self):
        result = apply_overrides(self.original, [_ov("sub_category", "1.1", 80.0)])
        expected_dim1 = 80*0.30 + 50*0.30 + 50*0.25 + 50*0.15   # = 59.0
        assert result.dimension_results["1"].score == pytest.approx(expected_dim1, abs=_TOL)

    def test_other_dimensions_unchanged(self):
        result = apply_overrides(self.original, [_ov("sub_category", "1.1", 80.0)])
        for dim_id in ("2", "3", "4", "5"):
            assert result.dimension_results[dim_id].score == pytest.approx(50.0, abs=_TOL)

    def test_composite_recomputed_hand_calculated(self):
        result = apply_overrides(self.original, [_ov("sub_category", "1.1", 80.0)])
        # dim1=59, dims 2-5=50 each; composite = (59+50+50+50+50)*0.20 = 259*0.20 = 51.8
        expected_composite = (59.0 + 50.0 + 50.0 + 50.0 + 50.0) * 0.20
        assert result.composite_score == pytest.approx(expected_composite, abs=_TOL)

    def test_composite_classification_updated(self):
        result = apply_overrides(self.original, [_ov("sub_category", "1.1", 80.0)])
        assert result.composite_classification == classify_score(result.composite_score)

    def test_original_sub_cat_score_not_mutated(self):
        apply_overrides(self.original, [_ov("sub_category", "2.1", 90.0)])
        assert self.original.sub_category_results["2.1"].score == pytest.approx(50.0)

    def test_two_sub_cat_overrides_in_same_dimension(self):
        """
        Override 1.1 to 80.0 and 1.2 to 20.0:
          New dim1 = 80*0.30 + 20*0.30 + 50*0.25 + 50*0.15
                   = 24 + 6 + 12.5 + 7.5 = 50.0
        """
        overrides = [
            _ov("sub_category", "1.1", 80.0, "2025-04-01T10:00:00+00:00"),
            _ov("sub_category", "1.2", 20.0, "2025-04-01T10:01:00+00:00"),
        ]
        result = apply_overrides(self.original, overrides)
        expected_dim1 = 80*0.30 + 20*0.30 + 50*0.25 + 50*0.15
        assert result.dimension_results["1"].score == pytest.approx(expected_dim1, abs=_TOL)

    def test_sub_cat_override_in_dim5(self):
        """
        Override 5.1 to 100.0:
          Dim 5 weights: {5.1:0.30, 5.2:0.25, 5.3:0.20, 5.4:0.15, 5.5:0.10}
          New dim5 = 100*0.30 + 50*0.25 + 50*0.20 + 50*0.15 + 50*0.10
                   = 30 + 12.5 + 10 + 7.5 + 5 = 65.0
        """
        result = apply_overrides(self.original, [_ov("sub_category", "5.1", 100.0)])
        expected_dim5 = 100*0.30 + 50*0.25 + 50*0.20 + 50*0.15 + 50*0.10
        assert result.dimension_results["5"].score == pytest.approx(expected_dim5, abs=_TOL)


# ---------------------------------------------------------------------------
# Section 5: apply_overrides — dimension override cascade
# ---------------------------------------------------------------------------


class TestApplyOverridesDimension:
    """
    Hand-calculation for dimension override cascade.

    Start: all dims = 50.0, composite = 50.0.

    Override dim "2" to 30.0:
      New composite = (50 + 30 + 50 + 50 + 50) * 0.20 = 46.0
    """

    def setup_method(self):
        self.original = _uniform_oei(50.0)

    def test_dimension_score_changed(self):
        result = apply_overrides(self.original, [_ov("dimension", "2", 30.0)])
        assert result.dimension_results["2"].score == pytest.approx(30.0, abs=_TOL)

    def test_other_dimensions_unchanged(self):
        result = apply_overrides(self.original, [_ov("dimension", "2", 30.0)])
        for dim_id in ("1", "3", "4", "5"):
            assert result.dimension_results[dim_id].score == pytest.approx(50.0, abs=_TOL)

    def test_sub_category_scores_not_changed_by_dimension_override(self):
        result = apply_overrides(self.original, [_ov("dimension", "2", 30.0)])
        for sub_id in result.sub_category_results:
            assert result.sub_category_results[sub_id].score == pytest.approx(50.0, abs=_TOL)

    def test_composite_recomputed_hand_calculated(self):
        result = apply_overrides(self.original, [_ov("dimension", "2", 30.0)])
        # (50 + 30 + 50 + 50 + 50) * 0.20 = 230 * 0.20 = 46.0
        assert result.composite_score == pytest.approx(46.0, abs=_TOL)

    def test_dimension_classification_updated(self):
        result = apply_overrides(self.original, [_ov("dimension", "1", 15.0)])
        assert result.dimension_results["1"].classification == "Low Risk"

    def test_two_dimension_overrides_hand_calculated(self):
        """
        Override dim "1" to 80.0 and dim "3" to 20.0:
          composite = (80 + 50 + 20 + 50 + 50) * 0.20 = 250 * 0.20 = 50.0
        """
        overrides = [
            _ov("dimension", "1", 80.0, "2025-04-01T10:00:00+00:00"),
            _ov("dimension", "3", 20.0, "2025-04-01T10:01:00+00:00"),
        ]
        result = apply_overrides(self.original, overrides)
        expected = (80 + 50 + 20 + 50 + 50) * 0.20
        assert result.composite_score == pytest.approx(expected, abs=_TOL)


# ---------------------------------------------------------------------------
# Section 6: apply_overrides — composite override
# ---------------------------------------------------------------------------


class TestApplyOverridesComposite:

    def setup_method(self):
        self.original = _uniform_oei(50.0)

    def test_composite_score_changed(self):
        result = apply_overrides(self.original, [_ov("composite", "composite", 25.0)])
        assert result.composite_score == pytest.approx(25.0, abs=_TOL)

    def test_composite_classification_updated(self):
        result = apply_overrides(self.original, [_ov("composite", "composite", 85.0)])
        assert result.composite_classification == "Critical"

    def test_dimension_scores_unchanged_by_composite_override(self):
        result = apply_overrides(self.original, [_ov("composite", "composite", 10.0)])
        for dim_id in ("1", "2", "3", "4", "5"):
            assert result.dimension_results[dim_id].score == pytest.approx(50.0, abs=_TOL)

    def test_sub_category_scores_unchanged_by_composite_override(self):
        result = apply_overrides(self.original, [_ov("composite", "composite", 90.0)])
        for sub_id in result.sub_category_results:
            assert result.sub_category_results[sub_id].score == pytest.approx(50.0, abs=_TOL)

    def test_composite_override_to_zero(self):
        result = apply_overrides(self.original, [_ov("composite", "composite", 0.0)])
        assert result.composite_score == pytest.approx(0.0, abs=_TOL)
        assert result.composite_classification == "Low Risk"

    def test_composite_override_to_100(self):
        result = apply_overrides(self.original, [_ov("composite", "composite", 100.0)])
        assert result.composite_score == pytest.approx(100.0, abs=_TOL)
        assert result.composite_classification == "Critical"


# ---------------------------------------------------------------------------
# Section 7: apply_overrides — full cascade and ordering
# ---------------------------------------------------------------------------


class TestApplyOverridesFullCascade:
    """
    Full cascade: sub-category + dimension + composite overrides together.

    Start: all sub-cats = 50.0, all dims = 50.0, composite = 50.0.

    Apply (in chronological order):
      1. Sub-cat override: 1.1 → 80.0  → dim1 recomputes to 59.0
      2. Dimension override: dim2 → 20.0
      3. Composite override: composite → 77.0

    Expected intermediate states:
      After sub-cat: dim1=59.0, dims 2-5=50.0
      After dim-override: dim1=59.0, dim2=20.0, dims 3-5=50.0
      After composite recompute (before composite override):
        = (59 + 20 + 50 + 50 + 50) * 0.20 = 229 * 0.20 = 45.8
      After composite override: composite = 77.0
    """

    def setup_method(self):
        self.original = _uniform_oei(50.0)

    def test_full_cascade_final_composite(self):
        overrides = [
            _ov("sub_category", "1.1", 80.0,   "2025-04-01T10:00:00+00:00"),
            _ov("dimension",    "2",   20.0,    "2025-04-01T10:01:00+00:00"),
            _ov("composite",    "composite", 77.0, "2025-04-01T10:02:00+00:00"),
        ]
        result = apply_overrides(self.original, overrides)
        assert result.composite_score == pytest.approx(77.0, abs=_TOL)

    def test_full_cascade_sub_cat_and_dimension_visible(self):
        overrides = [
            _ov("sub_category", "1.1", 80.0, "2025-04-01T10:00:00+00:00"),
            _ov("dimension",    "2",   20.0,  "2025-04-01T10:01:00+00:00"),
            _ov("composite",    "composite", 77.0, "2025-04-01T10:02:00+00:00"),
        ]
        result = apply_overrides(self.original, overrides)
        assert result.sub_category_results["1.1"].score == pytest.approx(80.0, abs=_TOL)
        assert result.dimension_results["2"].score == pytest.approx(20.0, abs=_TOL)
        expected_dim1 = 80*0.30 + 50*0.30 + 50*0.25 + 50*0.15   # 59.0
        assert result.dimension_results["1"].score == pytest.approx(expected_dim1, abs=_TOL)

    def test_cascade_without_composite_override_is_computed(self):
        """
        Without a composite override, the composite should be derived from
        the dimension scores after sub-cat and dimension overrides.

        sub-cat 1.1 → 80.0 → dim1 = 59.0
        dim2 override → 20.0
        composite = (59 + 20 + 50 + 50 + 50) * 0.20 = 45.8
        """
        overrides = [
            _ov("sub_category", "1.1", 80.0, "2025-04-01T10:00:00+00:00"),
            _ov("dimension",    "2",   20.0,  "2025-04-01T10:01:00+00:00"),
        ]
        result = apply_overrides(self.original, overrides)
        expected = (59.0 + 20.0 + 50.0 + 50.0 + 50.0) * 0.20
        assert result.composite_score == pytest.approx(expected, abs=_TOL)

    # --- last override wins ---

    def test_last_sub_cat_override_wins(self):
        """Two overrides on 2.3: the later one (80.0) must win."""
        overrides = [
            _ov("sub_category", "2.3", 60.0, "2025-04-01T09:00:00+00:00"),
            _ov("sub_category", "2.3", 80.0, "2025-04-01T10:00:00+00:00"),
        ]
        result = apply_overrides(self.original, overrides)
        assert result.sub_category_results["2.3"].score == pytest.approx(80.0, abs=_TOL)

    def test_last_dimension_override_wins(self):
        overrides = [
            _ov("dimension", "4", 70.0, "2025-04-01T09:00:00+00:00"),
            _ov("dimension", "4", 30.0, "2025-04-01T10:00:00+00:00"),
        ]
        result = apply_overrides(self.original, overrides)
        assert result.dimension_results["4"].score == pytest.approx(30.0, abs=_TOL)

    def test_last_composite_override_wins(self):
        overrides = [
            _ov("composite", "composite", 40.0, "2025-04-01T09:00:00+00:00"),
            _ov("composite", "composite", 75.0, "2025-04-01T10:00:00+00:00"),
        ]
        result = apply_overrides(self.original, overrides)
        assert result.composite_score == pytest.approx(75.0, abs=_TOL)

    def test_original_oei_result_not_mutated(self):
        """apply_overrides must return a new OEIScoreResult; original is unchanged."""
        original = _uniform_oei(50.0)
        original_composite = original.composite_score

        apply_overrides(original, [
            _ov("sub_category", "1.1", 99.0),
            _ov("dimension",    "2",   99.0),
            _ov("composite",    "composite", 99.0),
        ])

        assert original.composite_score == pytest.approx(original_composite, abs=_TOL)
        assert original.sub_category_results["1.1"].score == pytest.approx(50.0, abs=_TOL)
        assert original.dimension_results["2"].score == pytest.approx(50.0, abs=_TOL)

    def test_apply_overrides_returns_new_object(self):
        original = _uniform_oei(50.0)
        result = apply_overrides(original, [_ov("composite", "composite", 77.0)])
        assert result is not original
