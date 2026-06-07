"""
tests/test_sub_categories.py
CPOI Platform — Unit tests for scoring/sub_categories.py

Every scorer is tested with:
  1. A fully specified input that produces a computable result, verified
     against a hand-calculated expected score.
  2. The missing-data path (empty or missing DataFrame) confirmed to return
     the correct MISSING_DATA_DEFAULTS value and is_missing_data=True.
  3. Non-computable scorers (1.3, 4.2, 4.3, 5.2) confirmed to return
     is_non_computable=True regardless of what is in the DataFrames.

Hand-calculation methodology:
  For each scorer, the input is chosen to land inside a bounded interpolation
  band. The expected score is derived using the formula:
    score = score_at_lower + ratio * (score_at_upper - score_at_lower)
    ratio = clamp((value - condition_lower) / (condition_upper - condition_lower), 0, 1)
  For unbounded top bands the expected score is:
    score = (score_at_lower + score_at_upper) / 2.0
  All calculations are shown inline as comments.

Run from the project root:
    python -m pytest tests/test_sub_categories.py -v
"""

from datetime import datetime

import pandas as pd
import pytest

from scoring.constants import MISSING_DATA_DEFAULTS
from scoring.sub_categories import (
    SubCategoryResult,
    compute_all_sub_categories,
    score_1_1,
    score_1_2,
    score_1_3,
    score_1_4,
    score_2_1,
    score_2_2,
    score_2_3,
    score_2_4,
    score_3_1,
    score_3_2,
    score_3_3,
    score_3_4,
    score_4_1,
    score_4_2,
    score_4_3,
    score_4_4,
    score_5_1,
    score_5_2,
    score_5_3,
    score_5_4,
    score_5_5,
)

# ---------------------------------------------------------------------------
# Standard metadata used across tests unless overridden.
# Q1 2025: 2025-01-01 to 2025-03-31 = 89 days.
# ---------------------------------------------------------------------------

_META = {
    "Reporting_Period_Start": "2025-01-01",
    "Reporting_Period_End":   "2025-03-31",
}

# Tolerance for floating-point comparisons (one part in one million of scale).
_TOL = 1e-4


# ---------------------------------------------------------------------------
# 1.1  Priority Inflation Index
# ---------------------------------------------------------------------------

class TestScore1_1:
    """
    Scorer: % of initiatives labeled Critical or High Priority.

    Test case:
      3 of 4 initiatives are High → pct = 75.0%
      Band 65–80%: score_at_lower=51, score_at_upper=70
      ratio = (75 - 65) / (80 - 65) = 10/15 = 0.6667
      expected = 51 + 0.6667 * (70 - 51) = 51 + 12.667 = 63.667
    """

    def _make_df(self, priorities: list[str]) -> dict:
        return {
            "INITIATIVES": pd.DataFrame({
                "Priority_Classification": priorities
            })
        }

    def test_computed_score_hand_calculated(self):
        dfs = self._make_df(["High", "High", "High", "Low"])
        # 3/4 = 75.0%; ratio = 10/15 = 2/3
        expected = 51.0 + (10.0 / 15.0) * (70.0 - 51.0)
        result = score_1_1(dfs, _META)
        assert result.score == pytest.approx(expected, abs=_TOL)
        assert result.is_missing_data is False
        assert result.computed_value == pytest.approx(75.0, abs=_TOL)

    def test_critical_labeled_as_high_priority(self):
        # Use 2 of 5 = 40.0% (inside Band 30–50, away from the upper boundary).
        # A value exactly at 50.0% would be assigned to the 50–65 band (boundary rule).
        dfs = self._make_df(["Critical", "Critical", "Low", "Low", "Low"])
        # 2/5 = 40.0%; Band 30–50%: score_at_lower=16, score_at_upper=30
        # ratio = (40-30)/(50-30) = 10/20 = 0.5; expected = 16 + 0.5*14 = 23.0
        result = score_1_1(dfs, _META)
        assert result.score == pytest.approx(23.0, abs=_TOL)

    def test_zero_high_priority(self):
        dfs = self._make_df(["Low", "Medium", "Low"])
        # 0% → Band 0–30%: score_at_lower=0, score_at_upper=15
        # ratio = 0.0; expected = 0.0
        result = score_1_1(dfs, _META)
        assert result.score == pytest.approx(0.0, abs=_TOL)

    def test_all_critical(self):
        dfs = self._make_df(["Critical", "Critical", "Critical"])
        # 100% → unbounded top band; midpoint = (91 + 100) / 2 = 95.5
        result = score_1_1(dfs, _META)
        assert result.score == pytest.approx(95.5, abs=_TOL)

    def test_missing_data_empty_df(self):
        result = score_1_1({"INITIATIVES": pd.DataFrame()}, _META)
        assert result.score == pytest.approx(MISSING_DATA_DEFAULTS["1.1"])
        assert result.is_missing_data is True
        assert result.is_non_computable is False

    def test_missing_data_absent_tab(self):
        result = score_1_1({}, _META)
        assert result.score == pytest.approx(MISSING_DATA_DEFAULTS["1.1"])
        assert result.is_missing_data is True

    def test_missing_column(self):
        dfs = {"INITIATIVES": pd.DataFrame({"Other_Column": ["A", "B"]})}
        result = score_1_1(dfs, _META)
        assert result.is_missing_data is True


# ---------------------------------------------------------------------------
# 1.2  Concurrent Transformation Density
# ---------------------------------------------------------------------------

class TestScore1_2:
    """
    Scorer: average Allocated_Programs per team.

    Test case:
      Allocated_Programs = [2, 3, 4] → avg = 3.0
      Band 3–4: score_at_lower=16, score_at_upper=35
      ratio = (3.0 - 3.0) / (4.0 - 3.0) = 0.0
      expected = 16.0
    """

    def test_computed_score_hand_calculated(self):
        dfs = {"RESOURCE_UTILIZATION": pd.DataFrame({
            "Allocated_Programs": ["2", "3", "4"]
        })}
        result = score_1_2(dfs, _META)
        assert result.score == pytest.approx(16.0, abs=_TOL)
        assert result.computed_value == pytest.approx(3.0, abs=_TOL)
        assert result.is_missing_data is False

    def test_high_density(self):
        # avg = 7 → unbounded band; midpoint = (76 + 100) / 2 = 88.0
        dfs = {"RESOURCE_UTILIZATION": pd.DataFrame({
            "Allocated_Programs": ["7", "7"]
        })}
        result = score_1_2(dfs, _META)
        assert result.score == pytest.approx(88.0, abs=_TOL)

    def test_missing_data_empty_df(self):
        result = score_1_2({"RESOURCE_UTILIZATION": pd.DataFrame()}, _META)
        assert result.is_missing_data is True
        assert result.score == pytest.approx(MISSING_DATA_DEFAULTS["1.2"])

    def test_missing_column(self):
        dfs = {"RESOURCE_UTILIZATION": pd.DataFrame({"Other": ["1"]})}
        result = score_1_2(dfs, _META)
        assert result.is_missing_data is True


# ---------------------------------------------------------------------------
# 1.3  Priority Change Frequency  [NON-COMPUTABLE]
# ---------------------------------------------------------------------------

class TestScore1_3:
    """Non-computable: always returns missing-data default and is_non_computable=True."""

    def test_non_computable_always(self):
        result = score_1_3({"INITIATIVES": pd.DataFrame({"x": [1, 2, 3]})}, _META)
        assert result.is_non_computable is True
        assert result.is_missing_data is True
        assert result.score == pytest.approx(MISSING_DATA_DEFAULTS["1.3"])
        assert result.computed_value is None

    def test_non_computable_empty_dfs(self):
        result = score_1_3({}, _META)
        assert result.is_non_computable is True
        assert result.score == pytest.approx(65.0)


# ---------------------------------------------------------------------------
# 1.4  Initiative Persistence Rate
# ---------------------------------------------------------------------------

class TestScore1_4:
    """
    Scorer: % of initiatives past Target_Completion_Date.

    Test case:
      Targets = ["2025-01-01", "2025-02-01", "2026-01-01"]
      Reporting_Period_End = "2025-03-31"
      Overdue: 2025-01-01 and 2025-02-01 → 2 of 3 = 66.67%
      Band 60%+: unbounded; midpoint = (76 + 100) / 2 = 88.0
    """

    def _make_df(self, dates: list[str]) -> dict:
        return {
            "INITIATIVES": pd.DataFrame({"Target_Completion_Date": dates})
        }

    def test_computed_score_unbounded_band(self):
        dfs = self._make_df(["2025-01-01", "2025-02-01", "2026-01-01"])
        result = score_1_4(dfs, _META)
        assert result.score == pytest.approx(88.0, abs=_TOL)
        assert result.is_missing_data is False

    def test_zero_overdue(self):
        # All future dates → 0% → Band 0–10%: ratio=0 → score = 0.0
        dfs = self._make_df(["2026-01-01", "2026-06-01", "2027-01-01"])
        result = score_1_4(dfs, _META)
        assert result.score == pytest.approx(0.0, abs=_TOL)
        assert result.computed_value == pytest.approx(0.0, abs=_TOL)

    def test_partial_overdue_interpolation(self):
        # Use 1 of 5 overdue = 20.0% (inside Band 10–25%, away from the upper boundary).
        # A value of 25.0% would move to the 25–40 band (boundary rule).
        # 20.0%; Band 10–25%: score_at_lower=11, score_at_upper=30
        # ratio = (20-10)/(25-10) = 10/15 = 0.6667
        # expected = 11 + (10/15)*19 = 11 + 12.667 = 23.667
        dfs = self._make_df(["2025-01-01", "2026-01-01", "2026-06-01", "2027-01-01", "2028-01-01"])
        result = score_1_4(dfs, _META)
        expected = 11.0 + (10.0 / 15.0) * (30.0 - 11.0)
        assert result.score == pytest.approx(expected, abs=_TOL)

    def test_missing_data_no_parseable_dates(self):
        dfs = self._make_df(["not-a-date", "also-bad", "still-bad"])
        result = score_1_4(dfs, _META)
        assert result.is_missing_data is True
        assert result.score == pytest.approx(MISSING_DATA_DEFAULTS["1.4"])

    def test_missing_data_absent_tab(self):
        result = score_1_4({}, _META)
        assert result.is_missing_data is True


# ---------------------------------------------------------------------------
# 2.1  Escalation Resolution Latency
# ---------------------------------------------------------------------------

class TestScore2_1:
    """
    Scorer: average Resolution_Days.

    Test case:
      Resolution_Days = [3, 7, 11] → avg = 7.0
      Band 5–10: score_at_lower=11, score_at_upper=30
      ratio = (7 - 5) / (10 - 5) = 2/5 = 0.4
      expected = 11 + 0.4 * 19 = 18.6
    """

    def test_computed_score_hand_calculated(self):
        dfs = {"ESCALATIONS": pd.DataFrame({
            "Resolution_Days": ["3", "7", "11"]
        })}
        result = score_2_1(dfs, _META)
        expected = 11.0 + (2.0 / 5.0) * (30.0 - 11.0)   # = 18.6
        assert result.score == pytest.approx(expected, abs=_TOL)
        assert result.computed_value == pytest.approx(7.0, abs=_TOL)

    def test_fast_resolution(self):
        # avg = 2.5 days → Band 0–5: score_at_lower=0, score_at_upper=10
        # ratio = 2.5/5 = 0.5; expected = 0 + 0.5*10 = 5.0
        dfs = {"ESCALATIONS": pd.DataFrame({"Resolution_Days": ["2", "3"]})}
        result = score_2_1(dfs, _META)
        assert result.score == pytest.approx(5.0, abs=_TOL)

    def test_slow_resolution_unbounded(self):
        # avg = 35 → unbounded band: (91 + 100) / 2 = 95.5
        dfs = {"ESCALATIONS": pd.DataFrame({"Resolution_Days": ["35", "35"]})}
        result = score_2_1(dfs, _META)
        assert result.score == pytest.approx(95.5, abs=_TOL)

    def test_missing_data_empty_df(self):
        result = score_2_1({"ESCALATIONS": pd.DataFrame()}, _META)
        assert result.is_missing_data is True
        assert result.score == pytest.approx(MISSING_DATA_DEFAULTS["2.1"])

    def test_negative_days_excluded(self):
        # Negative values are invalid and excluded; only 5.0 is valid.
        dfs = {"ESCALATIONS": pd.DataFrame({"Resolution_Days": ["-1", "5", "-3"]})}
        result = score_2_1(dfs, _META)
        # avg = 5.0 → Band 5–10: ratio = 0; expected = 11.0
        assert result.score == pytest.approx(11.0, abs=_TOL)


# ---------------------------------------------------------------------------
# 2.2  Escalation Suppression Rate
# ---------------------------------------------------------------------------

class TestScore2_2:
    """
    Scorer: % of resolved escalations closed below VP level.

    Test case:
      Resolved_At_Level = ["IC", "VP", "Manager", "SVP"]
      Below VP: "IC", "Manager" → 2 of 4 = 50.0%
      Band 30–50%: score_at_lower=16, score_at_upper=35
      ratio = (50 - 30) / (50 - 30) = 1.0
      expected = 35.0
    """

    def test_computed_score_hand_calculated(self):
        # Use 2 below-VP out of 5 total = 40.0% (inside Band 30–50, not on the boundary).
        # A value of 50.0% would be assigned to the 50–65 band (boundary rule).
        dfs = {"ESCALATIONS": pd.DataFrame({
            "Resolved_At_Level": ["IC", "VP", "Manager", "SVP", "C-Suite"]
        })}
        # 2/5 = 40.0%; Band 30–50%: score_at_lower=16, score_at_upper=35
        # ratio = (40-30)/(50-30) = 0.5; expected = 16 + 0.5*19 = 25.5
        result = score_2_2(dfs, _META)
        assert result.score == pytest.approx(25.5, abs=_TOL)
        assert result.computed_value == pytest.approx(40.0, abs=_TOL)

    def test_all_resolved_at_vp_or_above(self):
        # 0 below VP → 0% → Band 0–30%: score=0+0=0.0
        dfs = {"ESCALATIONS": pd.DataFrame({
            "Resolved_At_Level": ["VP", "SVP", "C-Suite"]
        })}
        result = score_2_2(dfs, _META)
        assert result.score == pytest.approx(0.0, abs=_TOL)

    def test_null_resolved_at_level_excluded(self):
        # Null entries are not resolved; excluded from denominator
        dfs = {"ESCALATIONS": pd.DataFrame({
            "Resolved_At_Level": ["Manager", None, "VP", None]
        })}
        # 1 below VP (Manager) out of 2 non-null = 50.0%
        result = score_2_2(dfs, _META)
        assert result.computed_value == pytest.approx(50.0, abs=_TOL)

    def test_missing_column_returns_default(self):
        dfs = {"ESCALATIONS": pd.DataFrame({"Other": ["x"]})}
        result = score_2_2(dfs, _META)
        assert result.is_missing_data is True


# ---------------------------------------------------------------------------
# 2.3  Decision Latency
# ---------------------------------------------------------------------------

class TestScore2_3:
    """
    Scorer: average Delay_Days for Decision events where Decision_Made == "Y".

    Test case:
      Events: Decision/Y/5, Decision/Y/9, Review/Y/3
      Only Decision+Y rows: [5, 9] → avg = 7.0
      Band 3–7: score_at_lower=11, score_at_upper=30
      ratio = (7 - 3) / (7 - 3) = 1.0
      expected = 30.0
    """

    def test_computed_score_hand_calculated(self):
        # Use Decision delays [4, 6] → avg = 5.0 (inside Band 3–7, not on the boundary).
        # A value of exactly 7.0 would be assigned to the 7–14 band (boundary rule).
        dfs = {"GOVERNANCE_EVENTS": pd.DataFrame({
            "Event_Type":     ["Decision", "Decision", "Review"],
            "Decision_Made":  ["Y",        "Y",        "Y"],
            "Delay_Days":     ["4",        "6",        "3"],
        })}
        # avg = 5.0; Band 3–7: score_at_lower=11, score_at_upper=30
        # ratio = (5-3)/(7-3) = 2/4 = 0.5; expected = 11 + 0.5*19 = 20.5
        result = score_2_3(dfs, _META)
        assert result.score == pytest.approx(20.5, abs=_TOL)
        assert result.computed_value == pytest.approx(5.0, abs=_TOL)

    def test_non_decision_events_excluded(self):
        # Only Review events — no Decision rows → missing data
        dfs = {"GOVERNANCE_EVENTS": pd.DataFrame({
            "Event_Type":    ["Review", "Review"],
            "Decision_Made": ["Y",      "Y"],
            "Delay_Days":    ["5",      "8"],
        })}
        result = score_2_3(dfs, _META)
        assert result.is_missing_data is True

    def test_decision_not_made_excluded(self):
        # Decision_Made == "N" → excluded
        dfs = {"GOVERNANCE_EVENTS": pd.DataFrame({
            "Event_Type":    ["Decision"],
            "Decision_Made": ["N"],
            "Delay_Days":    ["5"],
        })}
        result = score_2_3(dfs, _META)
        assert result.is_missing_data is True

    def test_missing_data_empty_df(self):
        result = score_2_3({"GOVERNANCE_EVENTS": pd.DataFrame()}, _META)
        assert result.is_missing_data is True
        assert result.score == pytest.approx(MISSING_DATA_DEFAULTS["2.3"])


# ---------------------------------------------------------------------------
# 2.4  Accountability Clarity Index  [INVERTED]
# ---------------------------------------------------------------------------

class TestScore2_4:
    """
    Scorer: % of initiatives with a named Program_Owner (inverted).

    Test case:
      Program_Owner = ["Alice", "Bob", "", "", ""]
      named: 2 (Alice, Bob); total: 5 → pct = 40.0%
      Band 0–45% (inverted): score_at_lower=100, score_at_upper=76
      ratio = (40 - 0) / (45 - 0) = 40/45 = 0.8889
      expected = 100 + (40/45) * (76 - 100) = 100 - 21.333 = 78.667
    """

    def test_computed_score_hand_calculated(self):
        dfs = {"INITIATIVES": pd.DataFrame({
            "Program_Owner": ["Alice", "Bob", "", "", ""]
        })}
        expected = 100.0 + (40.0 / 45.0) * (76.0 - 100.0)
        result = score_2_4(dfs, _META)
        assert result.score == pytest.approx(expected, abs=_TOL)
        assert result.computed_value == pytest.approx(40.0, abs=_TOL)

    def test_full_accountability(self):
        # 100% named → unbounded band 90+; midpoint = (10 + 0) / 2 = 5.0
        dfs = {"INITIATIVES": pd.DataFrame({
            "Program_Owner": ["Alice", "Bob", "Carol"]
        })}
        result = score_2_4(dfs, _META)
        assert result.score == pytest.approx(5.0, abs=_TOL)

    def test_null_owners_counted_as_unnamed(self):
        # None and empty string both count as not named
        dfs = {"INITIATIVES": pd.DataFrame({
            "Program_Owner": ["Alice", None, ""]
        })}
        # 1/3 = 33.33% → Band 0–45: ratio = 33.33/45 = 0.7407
        # expected = 100 + 0.7407 * (76 - 100) = 100 - 17.778 = 82.222
        expected = 100.0 + (100.0 / 3.0 / 45.0) * (76.0 - 100.0)
        result = score_2_4(dfs, _META)
        assert result.score == pytest.approx(expected, abs=_TOL)

    def test_missing_data_empty_df(self):
        result = score_2_4({"INITIATIVES": pd.DataFrame()}, _META)
        assert result.is_missing_data is True
        assert result.score == pytest.approx(MISSING_DATA_DEFAULTS["2.4"])


# ---------------------------------------------------------------------------
# 3.1  Telemetry Coverage Ratio  [INVERTED]
# ---------------------------------------------------------------------------

class TestScore3_1:
    """
    Scorer: % of initiatives appearing in REPORTING_VARIANCE (inverted).

    Test case:
      Initiatives: I-001, I-002, I-003, I-004
      In REPORTING_VARIANCE: I-001, I-002 → covered=2/4=50.0%
      Band 45–60% (inverted): score_at_lower=75, score_at_upper=56
      ratio = (50 - 45) / (60 - 45) = 5/15 = 0.3333
      expected = 75 + (5/15) * (56 - 75) = 75 - 6.333 = 68.667
    """

    def test_computed_score_hand_calculated(self):
        dfs = {
            "INITIATIVES": pd.DataFrame({
                "Initiative_ID": ["I-001", "I-002", "I-003", "I-004"]
            }),
            "REPORTING_VARIANCE": pd.DataFrame({
                "Initiative_ID": ["I-001", "I-002"]
            }),
        }
        expected = 75.0 + (5.0 / 15.0) * (56.0 - 75.0)
        result = score_3_1(dfs, _META)
        assert result.score == pytest.approx(expected, abs=_TOL)
        assert result.computed_value == pytest.approx(50.0, abs=_TOL)

    def test_full_coverage(self):
        # All 3 initiatives in RV → 100% → unbounded band 90+: (10+0)/2 = 5.0
        dfs = {
            "INITIATIVES": pd.DataFrame({"Initiative_ID": ["I-001","I-002","I-003"]}),
            "REPORTING_VARIANCE": pd.DataFrame({"Initiative_ID": ["I-001","I-002","I-003"]}),
        }
        result = score_3_1(dfs, _META)
        assert result.score == pytest.approx(5.0, abs=_TOL)

    def test_zero_coverage_when_rv_absent(self):
        # No RV tab at all → 0% → Band 0–45 (inverted): ratio=0 → score=100.0
        dfs = {"INITIATIVES": pd.DataFrame({"Initiative_ID": ["I-001","I-002"]})}
        result = score_3_1(dfs, _META)
        assert result.score == pytest.approx(100.0, abs=_TOL)

    def test_missing_data_no_initiatives(self):
        result = score_3_1({"INITIATIVES": pd.DataFrame()}, _META)
        assert result.is_missing_data is True
        assert result.score == pytest.approx(MISSING_DATA_DEFAULTS["3.1"])


# ---------------------------------------------------------------------------
# 3.2  Dependency Transparency Score  [INVERTED]
# ---------------------------------------------------------------------------

class TestScore3_2:
    """
    Scorer: % of initiatives with at least one DEPENDENCIES record (inverted).

    Test case:
      Initiatives: I-001, I-002, I-003
      DEPENDENCIES: Upstream=I-001, Downstream=I-002 → dep_ids={I-001,I-002}
      covered=2/3=66.67%
      Band 50–70% (inverted): score_at_lower=55, score_at_upper=31
      ratio = (66.67 - 50) / (70 - 50) = 16.67/20 = 0.8333
      expected = 55 + 0.8333 * (31 - 55) = 55 - 20.0 = 35.0
    """

    def test_computed_score_hand_calculated(self):
        dfs = {
            "INITIATIVES": pd.DataFrame({
                "Initiative_ID": ["I-001", "I-002", "I-003"]
            }),
            "DEPENDENCIES": pd.DataFrame({
                "Upstream_Initiative_ID":   ["I-001"],
                "Downstream_Initiative_ID": ["I-002"],
            }),
        }
        two_thirds = 200.0 / 3.0
        expected = 55.0 + ((two_thirds - 50.0) / 20.0) * (31.0 - 55.0)
        result = score_3_2(dfs, _META)
        assert result.score == pytest.approx(expected, abs=_TOL)

    def test_zero_coverage_no_dependencies(self):
        # No DEPENDENCIES tab → pct=0 → Band 0–30 (inverted): ratio=0 → score=100.0
        dfs = {"INITIATIVES": pd.DataFrame({"Initiative_ID": ["I-001","I-002"]})}
        result = score_3_2(dfs, _META)
        assert result.score == pytest.approx(100.0, abs=_TOL)

    def test_full_coverage(self):
        # Both initiatives covered → 100% → unbounded band 90+: (10+0)/2 = 5.0
        dfs = {
            "INITIATIVES": pd.DataFrame({"Initiative_ID": ["I-001","I-002"]}),
            "DEPENDENCIES": pd.DataFrame({
                "Upstream_Initiative_ID":   ["I-001"],
                "Downstream_Initiative_ID": ["I-002"],
            }),
        }
        result = score_3_2(dfs, _META)
        assert result.score == pytest.approx(5.0, abs=_TOL)


# ---------------------------------------------------------------------------
# 3.3  Reporting Lag Indicator
# ---------------------------------------------------------------------------

class TestScore3_3:
    """
    Scorer: mean Delay_Days for Review + Steering Committee events.

    Test case:
      Events: Review/6, Steering Committee/4, Decision/2
      Included: 6, 4 (Review + SC only) → avg = 5.0
      Band 3–7: score_at_lower=11, score_at_upper=30
      ratio = (5 - 3) / (7 - 3) = 2/4 = 0.5
      expected = 11 + 0.5 * 19 = 20.5
    """

    def test_computed_score_hand_calculated(self):
        dfs = {"GOVERNANCE_EVENTS": pd.DataFrame({
            "Event_Type": ["Review", "Steering Committee", "Decision"],
            "Delay_Days": ["6",      "4",                  "2"],
        })}
        expected = 11.0 + 0.5 * (30.0 - 11.0)   # 20.5
        result = score_3_3(dfs, _META)
        assert result.score == pytest.approx(expected, abs=_TOL)
        assert result.computed_value == pytest.approx(5.0, abs=_TOL)

    def test_decision_events_excluded(self):
        # Only Decision rows → no eligible rows → missing data
        dfs = {"GOVERNANCE_EVENTS": pd.DataFrame({
            "Event_Type": ["Decision", "Decision"],
            "Delay_Days": ["10",       "20"],
        })}
        result = score_3_3(dfs, _META)
        assert result.is_missing_data is True

    def test_missing_data_empty_df(self):
        result = score_3_3({"GOVERNANCE_EVENTS": pd.DataFrame()}, _META)
        assert result.is_missing_data is True
        assert result.score == pytest.approx(MISSING_DATA_DEFAULTS["3.3"])


# ---------------------------------------------------------------------------
# 3.4  Leadership Visibility Index  [INVERTED]
# ---------------------------------------------------------------------------

class TestScore3_4:
    """
    Scorer: (Review + Steering Committee count) / months_in_period (inverted).

    Test case:
      4 Review/SC events in Q1 2025-01-01 to 2025-03-31
      months = 89 / 30.44 = 2.9238...
      touchpoints_per_month = 4 / 2.9238 = 1.3682...
      Band 1–2 (inverted): score_at_lower=55, score_at_upper=31
      ratio = (1.3682 - 1.0) / (2.0 - 1.0) = 0.3682
      expected = 55 + 0.3682 * (31 - 55) = 55 - 8.837 = 46.163
    """

    def _meta_q1(self) -> dict:
        return {
            "Reporting_Period_Start": "2025-01-01",
            "Reporting_Period_End":   "2025-03-31",
        }

    def test_computed_score_hand_calculated(self):
        dfs = {"GOVERNANCE_EVENTS": pd.DataFrame({
            "Event_Type": ["Review", "Steering Committee", "Review", "Review", "Decision"],
        })}
        meta = self._meta_q1()
        # Compute expected precisely using the same formula.
        days = (datetime(2025, 3, 31) - datetime(2025, 1, 1)).days   # 89
        months = max(1.0, days / 30.44)
        tpm = 4.0 / months   # 4 Review/SC events
        # Band 1.0–2.0 (inverted): score_at_lower=55, score_at_upper=31
        ratio = (tpm - 1.0) / (2.0 - 1.0)
        ratio = max(0.0, min(1.0, ratio))
        expected = 55.0 + ratio * (31.0 - 55.0)

        result = score_3_4(dfs, meta)
        assert result.score == pytest.approx(expected, abs=_TOL)

    def test_zero_leadership_events(self):
        # 0 Review/SC events → tpm = 0 → Band 0–0.25 (inverted): ratio=0 → score=100.0
        dfs = {"GOVERNANCE_EVENTS": pd.DataFrame({
            "Event_Type": ["Decision", "Decision"]
        })}
        result = score_3_4(dfs, _META)
        assert result.score == pytest.approx(100.0, abs=_TOL)

    def test_missing_data_empty_df(self):
        result = score_3_4({"GOVERNANCE_EVENTS": pd.DataFrame()}, _META)
        assert result.is_missing_data is True
        assert result.score == pytest.approx(MISSING_DATA_DEFAULTS["3.4"])


# ---------------------------------------------------------------------------
# 4.1  False-Green Incidence Rate
# ---------------------------------------------------------------------------

class TestScore4_1:
    """
    Scorer: % of programs reporting Green with actual blockers > 0.

    Test case:
      Rows: Green/2, Green/0, Yellow/1, Green/0
      False green: row 0 (Green + 2 blockers) → 1 of 4 = 25.0%
      Band 16–31%: score_at_lower=46, score_at_upper=70
      ratio = (25 - 16) / (31 - 16) = 9/15 = 0.6
      expected = 46 + 0.6 * 24 = 60.4
    """

    def test_computed_score_hand_calculated(self):
        dfs = {"REPORTING_VARIANCE": pd.DataFrame({
            "Reported_Status":      ["Green", "Green", "Yellow", "Green"],
            "Actual_Blocker_Count": ["2",     "0",     "1",      "0"],
        })}
        expected = 46.0 + (9.0 / 15.0) * (70.0 - 46.0)   # = 60.4
        result = score_4_1(dfs, _META)
        assert result.score == pytest.approx(expected, abs=_TOL)
        assert result.computed_value == pytest.approx(25.0, abs=_TOL)

    def test_zero_false_green(self):
        # No Green programs → 0% → Band 0–1: score_at_lower=0, score_at_upper=5
        # ratio = 0/1 = 0; expected = 0.0
        dfs = {"REPORTING_VARIANCE": pd.DataFrame({
            "Reported_Status":      ["Yellow", "Red"],
            "Actual_Blocker_Count": ["0",      "2"],
        })}
        result = score_4_1(dfs, _META)
        assert result.score == pytest.approx(0.0, abs=_TOL)

    def test_missing_column_returns_default(self):
        dfs = {"REPORTING_VARIANCE": pd.DataFrame({"Other": ["x"]})}
        result = score_4_1(dfs, _META)
        assert result.is_missing_data is True


# ---------------------------------------------------------------------------
# 4.2  Manual Curation Index  [NON-COMPUTABLE]
# ---------------------------------------------------------------------------

class TestScore4_2:
    def test_non_computable_always(self):
        result = score_4_2({"REPORTING_VARIANCE": pd.DataFrame({"x": [1]})}, _META)
        assert result.is_non_computable is True
        assert result.is_missing_data is True
        assert result.score == pytest.approx(MISSING_DATA_DEFAULTS["4.2"])


# ---------------------------------------------------------------------------
# 4.3  Reporting Incentive Alignment  [NON-COMPUTABLE]
# ---------------------------------------------------------------------------

class TestScore4_3:
    def test_non_computable_always(self):
        result = score_4_3({}, _META)
        assert result.is_non_computable is True
        assert result.is_missing_data is True
        assert result.score == pytest.approx(MISSING_DATA_DEFAULTS["4.3"])
        assert result.score == pytest.approx(40.0)


# ---------------------------------------------------------------------------
# 4.4  Confidence Reliability Score  [INVERTED]
# ---------------------------------------------------------------------------

class TestScore4_4:
    """
    Scorer: % of RV rows where Variance_Detected is null or "N" (inverted).

    Test case:
      Variance_Detected = ["N","Y","N","N"] → 3 reliable of 4 = 75.0%
      Band 70–90% (inverted): score_at_lower=30, score_at_upper=11
      ratio = (75 - 70) / (90 - 70) = 5/20 = 0.25
      expected = 30 + 0.25 * (11 - 30) = 30 - 4.75 = 25.25
    """

    def test_computed_score_hand_calculated(self):
        dfs = {"REPORTING_VARIANCE": pd.DataFrame({
            "Variance_Detected": ["N", "Y", "N", "N"]
        })}
        expected = 30.0 + 0.25 * (11.0 - 30.0)   # = 25.25
        result = score_4_4(dfs, _META)
        assert result.score == pytest.approx(expected, abs=_TOL)
        assert result.computed_value == pytest.approx(75.0, abs=_TOL)

    def test_all_variance_detected(self):
        # All "Y" → 0 reliable = 0% → Band 0–30 (inverted): ratio=0 → score=100.0
        dfs = {"REPORTING_VARIANCE": pd.DataFrame({
            "Variance_Detected": ["Y", "Y", "Y"]
        })}
        result = score_4_4(dfs, _META)
        assert result.score == pytest.approx(100.0, abs=_TOL)

    def test_all_null_variance_detected_returns_missing(self):
        # All null → treated as no active monitoring → missing data
        dfs = {"REPORTING_VARIANCE": pd.DataFrame({
            "Variance_Detected": [None, None, None]
        })}
        result = score_4_4(dfs, _META)
        assert result.is_missing_data is True

    def test_missing_column_returns_missing(self):
        dfs = {"REPORTING_VARIANCE": pd.DataFrame({"Other": ["x"]})}
        result = score_4_4(dfs, _META)
        assert result.is_missing_data is True


# ---------------------------------------------------------------------------
# 5.1  Team Utilization Pressure
# ---------------------------------------------------------------------------

class TestScore5_1:
    """
    Scorer: max(Estimated_Utilization_Pct).

    Test case:
      Utilizations = [85.0, 105.0, 95.0] → max = 105.0
      Band 100–110%: score_at_lower=31, score_at_upper=55
      ratio = (105 - 100) / (110 - 100) = 5/10 = 0.5
      expected = 31 + 0.5 * 24 = 43.0
    """

    def test_computed_score_hand_calculated(self):
        dfs = {"RESOURCE_UTILIZATION": pd.DataFrame({
            "Estimated_Utilization_Pct": ["85.0", "105.0", "95.0"]
        })}
        result = score_5_1(dfs, _META)
        assert result.score == pytest.approx(43.0, abs=_TOL)
        assert result.computed_value == pytest.approx(105.0, abs=_TOL)

    def test_low_utilization(self):
        # max = 80% → Band 0–90: ratio = 80/90 = 0.8889; expected = 0 + 0.8889*10 = 8.889
        dfs = {"RESOURCE_UTILIZATION": pd.DataFrame({
            "Estimated_Utilization_Pct": ["70.0", "80.0", "65.0"]
        })}
        expected = 0.0 + (80.0 / 90.0) * (10.0 - 0.0)
        result = score_5_1(dfs, _META)
        assert result.score == pytest.approx(expected, abs=_TOL)

    def test_missing_data_empty_df(self):
        result = score_5_1({"RESOURCE_UTILIZATION": pd.DataFrame()}, _META)
        assert result.is_missing_data is True
        assert result.score == pytest.approx(MISSING_DATA_DEFAULTS["5.1"])


# ---------------------------------------------------------------------------
# 5.2  Reprioritization Impact Rate  [NON-COMPUTABLE]
# ---------------------------------------------------------------------------

class TestScore5_2:
    def test_non_computable_always(self):
        result = score_5_2({}, _META)
        assert result.is_non_computable is True
        assert result.score == pytest.approx(MISSING_DATA_DEFAULTS["5.2"])
        assert result.score == pytest.approx(65.0)


# ---------------------------------------------------------------------------
# 5.3  Reactive Work Ratio
# ---------------------------------------------------------------------------

class TestScore5_3:
    """
    Scorer: % of teams with Estimated_Utilization_Pct > 100% (unbounded band).

    Test case:
      Utilizations = [85.0, 105.0, 95.0, 110.0]
      Over 100%: rows 1 (105) and 3 (110) → 2 of 4 = 50.0%
      Band 40%+: unbounded; midpoint = (76 + 100) / 2 = 88.0
    """

    def test_computed_score_hand_calculated(self):
        dfs = {"RESOURCE_UTILIZATION": pd.DataFrame({
            "Estimated_Utilization_Pct": ["85.0", "105.0", "95.0", "110.0"]
        })}
        result = score_5_3(dfs, _META)
        assert result.score == pytest.approx(88.0, abs=_TOL)
        assert result.computed_value == pytest.approx(50.0, abs=_TOL)

    def test_no_teams_over_capacity(self):
        # All under 100% → 0% → Band 0–10: ratio=0 → score=0.0
        dfs = {"RESOURCE_UTILIZATION": pd.DataFrame({
            "Estimated_Utilization_Pct": ["80.0", "90.0", "75.0"]
        })}
        result = score_5_3(dfs, _META)
        assert result.score == pytest.approx(0.0, abs=_TOL)

    def test_all_teams_over_capacity(self):
        # 100% over → unbounded; midpoint = 88.0
        dfs = {"RESOURCE_UTILIZATION": pd.DataFrame({
            "Estimated_Utilization_Pct": ["120.0", "130.0"]
        })}
        result = score_5_3(dfs, _META)
        assert result.score == pytest.approx(88.0, abs=_TOL)

    def test_missing_data_empty_df(self):
        result = score_5_3({"RESOURCE_UTILIZATION": pd.DataFrame()}, _META)
        assert result.is_missing_data is True
        assert result.score == pytest.approx(MISSING_DATA_DEFAULTS["5.3"])


# ---------------------------------------------------------------------------
# 5.4  Adaptive Capacity Reserve  [INVERTED]
# ---------------------------------------------------------------------------

class TestScore5_4:
    """
    Scorer: 100 - (Team_Size-weighted avg utilization), clamped (inverted).

    Test case:
      Utilizations = [80.0, 90.0, 70.0], Team_Size = [10, 5, 8]
      weighted_avg = (80*10 + 90*5 + 70*8) / (10+5+8)
                   = (800 + 450 + 560) / 23 = 1810/23 = 78.6957%
      available    = 100 - 78.6957 = 21.3043%
      Band 15–25% (inverted): score_at_lower=30, score_at_upper=11
      ratio = (21.3043 - 15) / (25 - 15) = 6.3043/10 = 0.6304
      expected = 30 + 0.6304 * (11 - 30) = 30 - 11.978 = 18.022
    """

    def test_computed_score_hand_calculated(self):
        dfs = {"RESOURCE_UTILIZATION": pd.DataFrame({
            "Estimated_Utilization_Pct": ["80.0", "90.0", "70.0"],
            "Team_Size":                 ["10",   "5",    "8"],
        })}
        weighted_avg = (80.0*10 + 90.0*5 + 70.0*8) / 23.0
        available = 100.0 - weighted_avg
        ratio = (available - 15.0) / (25.0 - 15.0)
        ratio = max(0.0, min(1.0, ratio))
        expected = 30.0 + ratio * (11.0 - 30.0)

        result = score_5_4(dfs, _META)
        assert result.score == pytest.approx(expected, abs=_TOL)
        assert result.computed_value == pytest.approx(available, abs=_TOL)

    def test_fallback_to_unweighted_when_team_size_absent(self):
        # No Team_Size column → unweighted avg
        dfs = {"RESOURCE_UTILIZATION": pd.DataFrame({
            "Estimated_Utilization_Pct": ["60.0", "80.0", "100.0"]
        })}
        # unweighted avg = 80.0; available = 20.0
        # Band 15–25 (inverted): ratio = (20-15)/10 = 0.5; expected = 30 + 0.5*(11-30) = 20.5
        result = score_5_4(dfs, _META)
        assert result.score == pytest.approx(20.5, abs=_TOL)
        assert result.computed_value == pytest.approx(20.0, abs=_TOL)

    def test_zero_available_capacity_clamped(self):
        # 120% utilization → available = max(0, 100 - 120) = 0.0
        # Band 0–5 (inverted): ratio=0 → score = 100.0
        dfs = {"RESOURCE_UTILIZATION": pd.DataFrame({
            "Estimated_Utilization_Pct": ["120.0"]
        })}
        result = score_5_4(dfs, _META)
        assert result.score == pytest.approx(100.0, abs=_TOL)
        assert result.computed_value == pytest.approx(0.0, abs=_TOL)

    def test_missing_data_empty_df(self):
        result = score_5_4({"RESOURCE_UTILIZATION": pd.DataFrame()}, _META)
        assert result.is_missing_data is True
        assert result.score == pytest.approx(MISSING_DATA_DEFAULTS["5.4"])


# ---------------------------------------------------------------------------
# 5.5  Retention Risk Indicator
# ---------------------------------------------------------------------------

class TestScore5_5:
    """
    Scorer: count of Retention_Risk_Flag == "High".

    Test case:
      Flags = ["High","High","Medium","Low","High"] → count = 3
      Band 3–6: score_at_lower=26, score_at_upper=55
      ratio = (3 - 3) / (6 - 3) = 0/3 = 0.0
      expected = 26.0
    """

    def test_computed_score_hand_calculated(self):
        dfs = {"HEADCOUNT_SIGNALS": pd.DataFrame({
            "Retention_Risk_Flag": ["High", "High", "Medium", "Low", "High"]
        })}
        result = score_5_5(dfs, _META)
        assert result.score == pytest.approx(26.0, abs=_TOL)
        assert result.computed_value == pytest.approx(3.0, abs=_TOL)

    def test_zero_high_flags(self):
        # 0 High flags → Band 0–1: ratio=0 → score=0.0
        dfs = {"HEADCOUNT_SIGNALS": pd.DataFrame({
            "Retention_Risk_Flag": ["Medium", "Low", "Low"]
        })}
        result = score_5_5(dfs, _META)
        assert result.score == pytest.approx(0.0, abs=_TOL)

    def test_many_high_flags_unbounded(self):
        # 10 High flags → unbounded band 9+: midpoint = (76 + 100) / 2 = 88.0
        dfs = {"HEADCOUNT_SIGNALS": pd.DataFrame({
            "Retention_Risk_Flag": ["High"] * 10
        })}
        result = score_5_5(dfs, _META)
        assert result.score == pytest.approx(88.0, abs=_TOL)

    def test_missing_data_empty_df(self):
        result = score_5_5({"HEADCOUNT_SIGNALS": pd.DataFrame()}, _META)
        assert result.is_missing_data is True
        assert result.score == pytest.approx(MISSING_DATA_DEFAULTS["5.5"])


# ---------------------------------------------------------------------------
# compute_all_sub_categories — integration
# ---------------------------------------------------------------------------

class TestComputeAllSubCategories:
    """compute_all_sub_categories returns all 21 keys, never raises."""

    def _minimal_dfs(self) -> dict:
        return {
            "INITIATIVES": pd.DataFrame({
                "Initiative_ID":           ["I-001"],
                "Priority_Classification": ["High"],
                "Program_Owner":           ["Alice"],
                "Target_Completion_Date":  ["2026-01-01"],
            }),
            "ESCALATIONS": pd.DataFrame({
                "Resolution_Days":  ["5"],
                "Resolved_At_Level": ["VP"],
            }),
            "DEPENDENCIES": pd.DataFrame({
                "Upstream_Initiative_ID":   ["I-001"],
                "Downstream_Initiative_ID": ["I-001"],
            }),
            "RESOURCE_UTILIZATION": pd.DataFrame({
                "Estimated_Utilization_Pct": ["80.0"],
                "Team_Size":                 ["10"],
                "Allocated_Programs":        ["2"],
            }),
            "GOVERNANCE_EVENTS": pd.DataFrame({
                "Event_Type":    ["Review"],
                "Delay_Days":    ["2"],
                "Decision_Made": ["N"],
            }),
            "REPORTING_VARIANCE": pd.DataFrame({
                "Initiative_ID":      ["I-001"],
                "Reported_Status":    ["Green"],
                "Actual_Blocker_Count": ["0"],
                "Variance_Detected":  ["N"],
            }),
            "HEADCOUNT_SIGNALS": pd.DataFrame({
                "Retention_Risk_Flag": ["Low"],
            }),
        }

    def test_returns_all_21_keys(self):
        from scoring.constants import SCORE_BANDS
        results = compute_all_sub_categories(self._minimal_dfs(), _META)
        assert set(results.keys()) == set(SCORE_BANDS.keys())

    def test_all_scores_in_valid_range(self):
        results = compute_all_sub_categories(self._minimal_dfs(), _META)
        for sub_id, r in results.items():
            assert 0.0 <= r.score <= 100.0, (
                f"Sub-category {sub_id} score {r.score} is out of [0, 100]"
            )

    def test_non_computable_flagged(self):
        results = compute_all_sub_categories(self._minimal_dfs(), _META)
        for sub_id in ("1.3", "4.2", "4.3", "5.2"):
            assert results[sub_id].is_non_computable is True
            assert results[sub_id].is_missing_data is True

    def test_empty_dfs_returns_all_missing_defaults(self):
        results = compute_all_sub_categories({}, _META)
        from scoring.constants import MISSING_DATA_DEFAULTS
        for sub_id, r in results.items():
            assert r.is_missing_data is True
            assert r.score == pytest.approx(MISSING_DATA_DEFAULTS[sub_id])

    def test_never_raises(self):
        # Passing completely broken data must not propagate exceptions.
        broken = {"INITIATIVES": "not a dataframe"}
        results = compute_all_sub_categories(broken, _META)  # type: ignore
        assert len(results) == 21
