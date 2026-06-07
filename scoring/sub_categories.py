"""
scoring/sub_categories.py
CPOI Platform — Sub-Category Scoring Functions

One scoring function per OEI sub-category (21 total across 5 dimensions).
Each function accepts the full set of validated intake dataframes and the
validated METADATA dict, computes the condition value from the available
data, and returns a SubCategoryResult containing the interpolated score.

Data source mapping (tab name → sub-categories it feeds):
  INITIATIVES         1.1, 1.2, 1.4, 2.4, 3.1, 3.2
  ESCALATIONS         2.1, 2.2
  DEPENDENCIES        3.2
  RESOURCE_UTILIZATION 5.1, 5.3, 5.4
  GOVERNANCE_EVENTS   2.3, 3.3, 3.4
  REPORTING_VARIANCE  3.1, 4.1, 4.4
  HEADCOUNT_SIGNALS   5.5
  METADATA            1.4 (reporting_period_end), 3.4 (period length)

Proxy notes (where exact data is unavailable, a documented proxy is used):
  3.1  Telemetry Coverage: % of initiatives that appear in REPORTING_VARIANCE.
       Presence in the reporting variance tab indicates structured status
       data collection is occurring for that initiative.
  3.2  Dependency Transparency: % of initiatives with at least one record
       in DEPENDENCIES. Measures whether cross-program dependency tracking
       is happening at all.
  3.3  Reporting Lag: mean Delay_Days for Review and Steering Committee events.
       Delay_Days represents days past the scheduled event date; it is the
       closest available proxy for reporting latency in the intake schema.
  3.4  Leadership Visibility: (Review + Steering Committee event count) /
       months in reporting period. Governance review events are the closest
       proxy for direct executive touchpoints in the intake schema.
  4.4  Confidence Reliability: % of REPORTING_VARIANCE rows where
       Variance_Detected is null or "N". Absence of variance = confidence
       levels are supported by the underlying data.
  5.3  Reactive Work Ratio: % of teams with Estimated_Utilization_Pct > 100.
       Teams above 100% capacity are structurally forced into reactive mode.
  5.4  Adaptive Capacity Reserve: 100 minus the team-size-weighted average
       utilization across the portfolio. Clamped to [0.0, 100.0].

Non-computable sub-categories (always score at MISSING_DATA_DEFAULTS):
  1.3  Priority Change Frequency   — requires reclassification history
  4.2  Manual Curation Index       — qualitative; requires client self-report
  4.3  Reporting Incentive Alignment — requires Managing Partner observation
  5.2  Reprioritization Impact Rate — requires event log across periods

All functions share the same signature:
    f(dataframes: dict[str, pd.DataFrame], metadata: dict[str, str]) -> SubCategoryResult
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pandas as pd

from scoring.constants import (
    MISSING_DATA_DEFAULTS,
    NON_COMPUTABLE_SUB_CATEGORIES,
    SCORE_BANDS,
    interpolate_score,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

import json
from datetime import timezone


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


log = _build_logger("cpoi.scoring.sub_categories")

# ---------------------------------------------------------------------------
# Tab name constants
# ---------------------------------------------------------------------------
# Declared here to avoid importing from intake.validator (circular dependency
# risk) while keeping the names consistent with the validated dataframe keys.

_TAB_INITIATIVES          = "INITIATIVES"
_TAB_ESCALATIONS          = "ESCALATIONS"
_TAB_DEPENDENCIES         = "DEPENDENCIES"
_TAB_RESOURCE_UTILIZATION = "RESOURCE_UTILIZATION"
_TAB_GOVERNANCE_EVENTS    = "GOVERNANCE_EVENTS"
_TAB_REPORTING_VARIANCE   = "REPORTING_VARIANCE"
_TAB_HEADCOUNT_SIGNALS    = "HEADCOUNT_SIGNALS"

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubCategoryResult:
    """
    Outcome of computing a single OEI sub-category score.

    Attributes:
        sub_category_id:  The sub-category identifier, e.g. "1.1", "3.4".
        score:            The final risk score in [0.0, 100.0]. Either the
                          interpolated score from the band table or the
                          missing-data default.
        is_missing_data:  True when the score equals the missing-data default
                          because required data was absent or not computable.
        is_non_computable: True when the sub-category is structurally
                           non-computable from a single intake submission
                           (1.3, 4.2, 4.3, 5.2). A subset of is_missing_data.
        computed_value:   The raw condition value that was passed to
                          interpolate_score. None when is_missing_data is True.
    """

    sub_category_id: str
    score: float
    is_missing_data: bool
    is_non_computable: bool
    computed_value: float | None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _missing(sub_id: str) -> SubCategoryResult:
    """
    Return a SubCategoryResult scored at the missing-data default for sub_id.

    Used when the required source data is absent, empty, or entirely null.
    Also used for all non-computable sub-categories.
    """
    return SubCategoryResult(
        sub_category_id=sub_id,
        score=MISSING_DATA_DEFAULTS[sub_id],
        is_missing_data=True,
        is_non_computable=(sub_id in NON_COMPUTABLE_SUB_CATEGORIES),
        computed_value=None,
    )


def _computed(sub_id: str, value: float) -> SubCategoryResult:
    """
    Return a SubCategoryResult with an interpolated score for sub_id.

    Calls interpolate_score using the band table for sub_id. The raw
    condition value is stored in computed_value for auditability.
    """
    score = interpolate_score(value, SCORE_BANDS[sub_id])
    return SubCategoryResult(
        sub_category_id=sub_id,
        score=score,
        is_missing_data=False,
        is_non_computable=False,
        computed_value=value,
    )


def _to_float(value: object) -> float | None:
    """
    Convert a cell value from the validated dataframe to float.

    Returns None if the value is null (pd.NA, None, NaN) or cannot be
    parsed as a number. All cell values arrive as strings after the
    validator's dtype=str read; numeric validation has already passed.
    """
    if pd.isna(value):
        return None
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return None


def _tab(
    dataframes: dict[str, pd.DataFrame],
    name: str,
) -> pd.DataFrame:
    """
    Return the named dataframe, or an empty DataFrame if not present.

    Never raises; callers check for empty using df.empty or len(df).
    """
    return dataframes.get(name, pd.DataFrame())


def _months_in_period(metadata: dict[str, str]) -> float:
    """
    Return the number of months in the reporting period.

    Computed as (Reporting_Period_End - Reporting_Period_Start).days / 30.44.
    Returns 1.0 if dates cannot be parsed, so callers always get a safe
    denominator (at least one month assumed).

    Args:
        metadata: validated metadata dict with Reporting_Period_Start and
                  Reporting_Period_End keys in YYYY-MM-DD format.

    Returns:
        float: months in period, minimum 1.0.
    """
    try:
        start = datetime.strptime(
            metadata["Reporting_Period_Start"].strip(), "%Y-%m-%d"
        )
        end = datetime.strptime(
            metadata["Reporting_Period_End"].strip(), "%Y-%m-%d"
        )
        months = (end - start).days / 30.44
        return max(1.0, months)
    except (KeyError, ValueError):
        return 1.0


# ---------------------------------------------------------------------------
# Dimension 1 — Strategic Saturation
# ---------------------------------------------------------------------------


def score_1_1(
    dataframes: dict[str, pd.DataFrame],
    metadata: dict[str, str],
) -> SubCategoryResult:
    """
    1.1  Priority Inflation Index

    Condition: percentage of active initiatives labeled Critical or High Priority.
    Source:    INITIATIVES.Priority_Classification

    Returns missing-data default (75) if the INITIATIVES tab is empty.
    """
    df = _tab(dataframes, _TAB_INITIATIVES)
    if df.empty or "Priority_Classification" not in df.columns:
        return _missing("1.1")

    total = len(df)
    if total == 0:
        return _missing("1.1")

    high_priority = df["Priority_Classification"].apply(
        lambda v: str(v).strip() in {"Critical", "High"} if not pd.isna(v) else False
    ).sum()

    pct = (high_priority / total) * 100.0
    log.info("1.1 Priority Inflation: %.1f%% (%d of %d)", pct, high_priority, total)
    return _computed("1.1", pct)


def score_1_2(
    dataframes: dict[str, pd.DataFrame],
    metadata: dict[str, str],
) -> SubCategoryResult:
    """
    1.2  Concurrent Transformation Density

    Condition: active initiatives per delivery team (average across all teams).
    Source:    RESOURCE_UTILIZATION.Allocated_Programs

    Allocated_Programs records the number of active programs assigned to each
    team, which is the direct per-team measurement the spec describes.
    Average is taken across all reporting teams.

    Returns missing-data default (75) if RESOURCE_UTILIZATION is empty or
    if Allocated_Programs contains no parseable values.
    """
    df = _tab(dataframes, _TAB_RESOURCE_UTILIZATION)
    if df.empty or "Allocated_Programs" not in df.columns:
        return _missing("1.2")

    values = [_to_float(v) for v in df["Allocated_Programs"] if _to_float(v) is not None]
    if not values:
        return _missing("1.2")

    avg = sum(values) / len(values)
    log.info("1.2 Transformation Density: %.2f avg programs/team", avg)
    return _computed("1.2", avg)


def score_1_3(
    dataframes: dict[str, pd.DataFrame],
    metadata: dict[str, str],
) -> SubCategoryResult:
    """
    1.3  Priority Change Frequency  [NON-COMPUTABLE]

    Requires reclassification history across multiple reporting periods.
    A single intake submission contains only the current state, not the
    history of priority changes needed to compute this metric.

    Always scores at the missing-data default (65).
    """
    return _missing("1.3")


def score_1_4(
    dataframes: dict[str, pd.DataFrame],
    metadata: dict[str, str],
) -> SubCategoryResult:
    """
    1.4  Initiative Persistence Rate

    Condition: percentage of initiatives whose Target_Completion_Date is
               in the past (before Reporting_Period_End) without a formal
               scope change or deferral on record.
    Source:    INITIATIVES.Target_Completion_Date, METADATA.Reporting_Period_End

    All initiatives in the tab are treated as active (the intake schema does
    not include a completion status field). An initiative with a target date
    before the reporting period end is considered overdue.

    Returns missing-data default (70) if:
      - INITIATIVES tab is empty
      - Reporting_Period_End cannot be parsed from metadata
      - All Target_Completion_Date values fail to parse
    """
    df = _tab(dataframes, _TAB_INITIATIVES)
    if df.empty or "Target_Completion_Date" not in df.columns:
        return _missing("1.4")

    try:
        period_end = datetime.strptime(
            metadata["Reporting_Period_End"].strip(), "%Y-%m-%d"
        )
    except (KeyError, ValueError):
        return _missing("1.4")

    total = 0
    overdue = 0
    for value in df["Target_Completion_Date"]:
        if pd.isna(value):
            continue
        try:
            target = datetime.strptime(str(value).strip(), "%Y-%m-%d")
            total += 1
            if target < period_end:
                overdue += 1
        except ValueError:
            continue

    if total == 0:
        return _missing("1.4")

    pct = (overdue / total) * 100.0
    log.info("1.4 Persistence Rate: %.1f%% (%d of %d past target)", pct, overdue, total)
    return _computed("1.4", pct)


# ---------------------------------------------------------------------------
# Dimension 2 — Governance Responsiveness
# ---------------------------------------------------------------------------


def score_2_1(
    dataframes: dict[str, pd.DataFrame],
    metadata: dict[str, str],
) -> SubCategoryResult:
    """
    2.1  Escalation Resolution Latency

    Condition: average days from escalation creation to documented resolution.
    Source:    ESCALATIONS.Resolution_Days (preferred)
               ESCALATIONS.Date_Raised + Date_Resolved (fallback)

    Only resolved escalations are included (those with a Resolution_Days
    value or both Date_Raised and Date_Resolved present). Unresolved
    escalations are excluded from the average.

    Returns missing-data default (80) if:
      - ESCALATIONS tab is empty
      - No escalation has resolution timing data
      - Computed days are all negative (data anomaly)
    """
    df = _tab(dataframes, _TAB_ESCALATIONS)
    if df.empty:
        return _missing("2.1")

    days_list: list[float] = []

    # Prefer the pre-computed Resolution_Days column.
    if "Resolution_Days" in df.columns:
        for value in df["Resolution_Days"]:
            v = _to_float(value)
            if v is not None and v >= 0.0:
                days_list.append(v)

    # Fallback: compute from Date_Raised and Date_Resolved.
    if not days_list and "Date_Raised" in df.columns and "Date_Resolved" in df.columns:
        for _, row in df.iterrows():
            if pd.isna(row.get("Date_Raised")) or pd.isna(row.get("Date_Resolved")):
                continue
            try:
                raised = datetime.strptime(
                    str(row["Date_Raised"]).strip(), "%Y-%m-%d"
                )
                resolved = datetime.strptime(
                    str(row["Date_Resolved"]).strip(), "%Y-%m-%d"
                )
                delta = (resolved - raised).days
                if delta >= 0:
                    days_list.append(float(delta))
            except ValueError:
                continue

    if not days_list:
        return _missing("2.1")

    avg = sum(days_list) / len(days_list)
    log.info("2.1 Escalation Resolution Latency: %.1f avg days (%d resolved)", avg, len(days_list))
    return _computed("2.1", avg)


def score_2_2(
    dataframes: dict[str, pd.DataFrame],
    metadata: dict[str, str],
) -> SubCategoryResult:
    """
    2.2  Escalation Suppression Rate

    Condition: percentage of resolved escalations closed below VP level
               without executive visibility.
    Source:    ESCALATIONS.Resolved_At_Level

    "Below VP level" is defined as resolution at IC, Manager, or Director.
    Denominator is the count of escalations where Resolved_At_Level is
    not null (i.e., has been resolved and the resolution level was recorded).
    Unresolved escalations (Resolved_At_Level null) are excluded.

    Returns missing-data default (75) if:
      - ESCALATIONS tab is empty
      - No escalation has Resolved_At_Level populated
    """
    df = _tab(dataframes, _TAB_ESCALATIONS)
    if df.empty or "Resolved_At_Level" not in df.columns:
        return _missing("2.2")

    below_vp_levels = {"IC", "Manager", "Director"}
    resolved_total = 0
    below_vp_count = 0

    for value in df["Resolved_At_Level"]:
        if pd.isna(value) or str(value).strip() == "":
            continue
        resolved_total += 1
        if str(value).strip() in below_vp_levels:
            below_vp_count += 1

    if resolved_total == 0:
        return _missing("2.2")

    pct = (below_vp_count / resolved_total) * 100.0
    log.info(
        "2.2 Escalation Suppression: %.1f%% (%d of %d resolved below VP)",
        pct, below_vp_count, resolved_total,
    )
    return _computed("2.2", pct)


def score_2_3(
    dataframes: dict[str, pd.DataFrame],
    metadata: dict[str, str],
) -> SubCategoryResult:
    """
    2.3  Decision Latency

    Condition: average days between when a decision was required and when
               it was documented as made.
    Source:    GOVERNANCE_EVENTS.Delay_Days where Event_Type == "Decision"
               and Decision_Made == "Y"

    Only Decision events where Decision_Made == "Y" are included — these
    represent decisions that were eventually made and where the delay is
    known. Delay_Days is an optional column; rows where it is null are
    excluded from the average.

    Returns missing-data default (80) if:
      - GOVERNANCE_EVENTS tab is empty
      - No Decision event with Decision_Made == "Y" and non-null Delay_Days
    """
    df = _tab(dataframes, _TAB_GOVERNANCE_EVENTS)
    if df.empty or "Delay_Days" not in df.columns:
        return _missing("2.3")

    required_cols = {"Event_Type", "Decision_Made", "Delay_Days"}
    if not required_cols.issubset(df.columns):
        return _missing("2.3")

    days_list: list[float] = []
    for _, row in df.iterrows():
        if str(row.get("Event_Type", "")).strip() != "Decision":
            continue
        if str(row.get("Decision_Made", "")).strip() != "Y":
            continue
        v = _to_float(row.get("Delay_Days"))
        if v is not None and v >= 0.0:
            days_list.append(v)

    if not days_list:
        return _missing("2.3")

    avg = sum(days_list) / len(days_list)
    log.info("2.3 Decision Latency: %.1f avg days (%d decisions)", avg, len(days_list))
    return _computed("2.3", avg)


def score_2_4(
    dataframes: dict[str, pd.DataFrame],
    metadata: dict[str, str],
) -> SubCategoryResult:
    """
    2.4  Accountability Clarity Index  [INVERTED]

    Condition: percentage of initiatives with a single named accountable
               decision-maker documented.
    Source:    INITIATIVES.Program_Owner

    Program_Owner is the intake schema's closest mapping to "named,
    accountable decision-maker." It is a required field, so any validated
    submission will typically show 100% accountability — the Managing Partner
    uses the override protocol when Program_Owner values are placeholder
    entries (e.g., "TBD", "N/A") that passed validation but do not represent
    genuine ownership.

    Returns missing-data default (70) if INITIATIVES tab is empty.
    """
    df = _tab(dataframes, _TAB_INITIATIVES)
    if df.empty or "Program_Owner" not in df.columns:
        return _missing("2.4")

    total = len(df)
    if total == 0:
        return _missing("2.4")

    named = df["Program_Owner"].apply(
        lambda v: (not pd.isna(v)) and str(v).strip() != ""
    ).sum()

    pct = (named / total) * 100.0
    log.info("2.4 Accountability Clarity: %.1f%% (%d of %d with named owner)", pct, named, total)
    return _computed("2.4", pct)


# ---------------------------------------------------------------------------
# Dimension 3 — Execution Visibility
# ---------------------------------------------------------------------------


def score_3_1(
    dataframes: dict[str, pd.DataFrame],
    metadata: dict[str, str],
) -> SubCategoryResult:
    """
    3.1  Telemetry Coverage Ratio  [INVERTED]

    Condition: percentage of active initiatives with structured, documented
               operational data being collected.
    Proxy:     % of INITIATIVES.Initiative_ID values that appear in
               REPORTING_VARIANCE.Initiative_ID.
               Presence in REPORTING_VARIANCE indicates that structured status
               data is being collected and variance-checked for that initiative.
    Source:    INITIATIVES, REPORTING_VARIANCE

    Returns missing-data default (85) if:
      - INITIATIVES tab is empty
      - Initiative_ID column is absent from either tab
    """
    df_init = _tab(dataframes, _TAB_INITIATIVES)
    df_rv   = _tab(dataframes, _TAB_REPORTING_VARIANCE)

    if df_init.empty or "Initiative_ID" not in df_init.columns:
        return _missing("3.1")

    total = len(df_init)
    if total == 0:
        return _missing("3.1")

    if df_rv.empty or "Initiative_ID" not in df_rv.columns:
        # No reporting variance data at all: coverage = 0%
        pct = 0.0
    else:
        rv_ids: set[str] = {
            str(v).strip()
            for v in df_rv["Initiative_ID"]
            if not pd.isna(v) and str(v).strip()
        }
        covered = df_init["Initiative_ID"].apply(
            lambda v: str(v).strip() in rv_ids if not pd.isna(v) else False
        ).sum()
        pct = (covered / total) * 100.0

    log.info("3.1 Telemetry Coverage: %.1f%%", pct)
    return _computed("3.1", pct)


def score_3_2(
    dataframes: dict[str, pd.DataFrame],
    metadata: dict[str, str],
) -> SubCategoryResult:
    """
    3.2  Dependency Transparency Score  [INVERTED]

    Condition: percentage of known cross-program dependencies formally
               documented and tracked.
    Proxy:     % of INITIATIVES that appear in at least one DEPENDENCIES
               record (as either upstream or downstream initiative).
               An initiative with at least one documented dependency has
               cross-program dependency tracking in place for that program.
    Source:    INITIATIVES, DEPENDENCIES

    Returns missing-data default (80) if INITIATIVES tab is empty.
    If DEPENDENCIES tab is empty (no dependencies documented), coverage = 0%.
    """
    df_init = _tab(dataframes, _TAB_INITIATIVES)
    df_dep  = _tab(dataframes, _TAB_DEPENDENCIES)

    if df_init.empty or "Initiative_ID" not in df_init.columns:
        return _missing("3.2")

    total = len(df_init)
    if total == 0:
        return _missing("3.2")

    if df_dep.empty:
        pct = 0.0
    else:
        dep_ids: set[str] = set()
        for col in ("Upstream_Initiative_ID", "Downstream_Initiative_ID"):
            if col in df_dep.columns:
                for v in df_dep[col]:
                    if not pd.isna(v) and str(v).strip():
                        dep_ids.add(str(v).strip())

        covered = df_init["Initiative_ID"].apply(
            lambda v: str(v).strip() in dep_ids if not pd.isna(v) else False
        ).sum()
        pct = (covered / total) * 100.0

    log.info("3.2 Dependency Transparency: %.1f%%", pct)
    return _computed("3.2", pct)


def score_3_3(
    dataframes: dict[str, pd.DataFrame],
    metadata: dict[str, str],
) -> SubCategoryResult:
    """
    3.3  Reporting Lag Indicator

    Condition: average calendar days from when an operational event occurs
               to when it appears in executive-level reporting.
    Proxy:     Mean Delay_Days for Review and Steering Committee events in
               GOVERNANCE_EVENTS. Delay_Days represents days past the
               scheduled date — the closest available proxy for reporting
               latency in the current intake schema.
    Source:    GOVERNANCE_EVENTS.Delay_Days where Event_Type in
               {"Review", "Steering Committee"}

    Returns missing-data default (65) if:
      - GOVERNANCE_EVENTS tab is empty
      - No Review or Steering Committee events have non-null Delay_Days
    """
    df = _tab(dataframes, _TAB_GOVERNANCE_EVENTS)
    if df.empty or "Delay_Days" not in df.columns or "Event_Type" not in df.columns:
        return _missing("3.3")

    reporting_event_types = {"Review", "Steering Committee"}
    days_list: list[float] = []

    for _, row in df.iterrows():
        if str(row.get("Event_Type", "")).strip() not in reporting_event_types:
            continue
        v = _to_float(row.get("Delay_Days"))
        if v is not None and v >= 0.0:
            days_list.append(v)

    if not days_list:
        return _missing("3.3")

    avg = sum(days_list) / len(days_list)
    log.info("3.3 Reporting Lag: %.1f avg days (%d events)", avg, len(days_list))
    return _computed("3.3", avg)


def score_3_4(
    dataframes: dict[str, pd.DataFrame],
    metadata: dict[str, str],
) -> SubCategoryResult:
    """
    3.4  Leadership Visibility Index  [INVERTED]

    Condition: direct executive touchpoints with delivery teams per month.
    Proxy:     Count of Review and Steering Committee events in the reporting
               period divided by the number of months in the period.
               These event types are the closest proxies for structured
               executive touchpoints available in the intake schema.
    Source:    GOVERNANCE_EVENTS.Event_Type, METADATA period dates

    Returns missing-data default (65) if GOVERNANCE_EVENTS tab is empty.
    If the tab has rows but none are Review or Steering Committee events,
    touchpoints per month = 0 (maps to the highest risk band).
    """
    df = _tab(dataframes, _TAB_GOVERNANCE_EVENTS)
    if df.empty or "Event_Type" not in df.columns:
        return _missing("3.4")

    leadership_event_types = {"Review", "Steering Committee"}
    event_count = df["Event_Type"].apply(
        lambda v: str(v).strip() in leadership_event_types if not pd.isna(v) else False
    ).sum()

    months = _months_in_period(metadata)
    touchpoints_per_month = float(event_count) / months

    log.info(
        "3.4 Leadership Visibility: %.2f touchpoints/month (%d events, %.1f months)",
        touchpoints_per_month, event_count, months,
    )
    return _computed("3.4", touchpoints_per_month)


# ---------------------------------------------------------------------------
# Dimension 4 — Reporting Integrity
# ---------------------------------------------------------------------------


def score_4_1(
    dataframes: dict[str, pd.DataFrame],
    metadata: dict[str, str],
) -> SubCategoryResult:
    """
    4.1  False-Green Incidence Rate

    Condition: percentage of programs reporting Green status that have one
               or more open Critical blockers documented in the same period.
    Source:    REPORTING_VARIANCE.Reported_Status == "Green"
               AND REPORTING_VARIANCE.Actual_Blocker_Count > 0

    A program is counted as False Green when it self-reports Green status
    while the reporting variance tab records actual blockers present.

    Returns missing-data default (80) if:
      - REPORTING_VARIANCE tab is empty
      - Required columns are absent
    """
    df = _tab(dataframes, _TAB_REPORTING_VARIANCE)
    required = {"Reported_Status", "Actual_Blocker_Count"}
    if df.empty or not required.issubset(df.columns):
        return _missing("4.1")

    total = len(df)
    if total == 0:
        return _missing("4.1")

    false_green = 0
    for _, row in df.iterrows():
        status = str(row.get("Reported_Status", "")).strip()
        blocker_count = _to_float(row.get("Actual_Blocker_Count"))
        if status == "Green" and blocker_count is not None and blocker_count > 0:
            false_green += 1

    pct = (false_green / total) * 100.0
    log.info(
        "4.1 False-Green Rate: %.1f%% (%d of %d programs)",
        pct, false_green, total,
    )
    return _computed("4.1", pct)


def score_4_2(
    dataframes: dict[str, pd.DataFrame],
    metadata: dict[str, str],
) -> SubCategoryResult:
    """
    4.2  Manual Curation Index  [NON-COMPUTABLE]

    Requires qualitative assessment of what percentage of status reports
    pass through human editorial review. This cannot be derived from the
    structured intake data in a single submission. Requires either a direct
    client self-report field or Managing Partner discovery observation.

    Always scores at the missing-data default (75).
    """
    return _missing("4.2")


def score_4_3(
    dataframes: dict[str, pd.DataFrame],
    metadata: dict[str, str],
) -> SubCategoryResult:
    """
    4.3  Reporting Incentive Alignment  [NON-COMPUTABLE]

    Requires direct Managing Partner observation during discovery sessions
    and review calls. Scored on a qualitative five-category scale assessing
    whether organizational culture encourages accurate or optimistic reporting.
    This cannot be derived from submitted operational data.

    Spec default behavior: scores at 40 (Moderate) when no observation
    has been recorded.

    Always scores at the missing-data default (40).
    """
    return _missing("4.3")


def score_4_4(
    dataframes: dict[str, pd.DataFrame],
    metadata: dict[str, str],
) -> SubCategoryResult:
    """
    4.4  Confidence Reliability Score  [INVERTED]

    Condition: percentage of programs where reported confidence levels are
               supported by documented evidence.
    Proxy:     % of REPORTING_VARIANCE rows where Variance_Detected is null
               or "N". Absence of a detected variance between reported and
               actual state indicates that the reported confidence level
               (Green/Yellow/Red) is supported by the underlying data.
    Source:    REPORTING_VARIANCE.Variance_Detected

    When Variance_Detected is absent for all rows (not filled in), the
    assumption is that no active variance monitoring exists, which means
    confidence levels are unverified. Score falls to missing-data default (70).
    """
    df = _tab(dataframes, _TAB_REPORTING_VARIANCE)
    if df.empty:
        return _missing("4.4")

    total = len(df)
    if total == 0:
        return _missing("4.4")

    # If the optional Variance_Detected column is not present at all, we have
    # no evidence of variance monitoring; treat as missing data.
    if "Variance_Detected" not in df.columns:
        return _missing("4.4")

    # Count rows where Variance_Detected is null (no variance recorded) or "N".
    all_null = df["Variance_Detected"].apply(pd.isna).all()
    if all_null:
        # Column present but entirely unfilled — no active monitoring.
        return _missing("4.4")

    reliable = df["Variance_Detected"].apply(
        lambda v: pd.isna(v) or str(v).strip() == "N"
    ).sum()

    pct = (reliable / total) * 100.0
    log.info("4.4 Confidence Reliability: %.1f%% (%d of %d reliable)", pct, int(reliable), total)
    return _computed("4.4", pct)


# ---------------------------------------------------------------------------
# Dimension 5 — Organizational Sustainability
# ---------------------------------------------------------------------------


def score_5_1(
    dataframes: dict[str, pd.DataFrame],
    metadata: dict[str, str],
) -> SubCategoryResult:
    """
    5.1  Team Utilization Pressure

    Condition: highest team utilization percentage across the portfolio.
    Source:    RESOURCE_UTILIZATION.Estimated_Utilization_Pct (max value)

    The spec explicitly uses the single highest team utilization rather than
    the average, because a platform team at 145% is an existential delivery
    risk regardless of what the other teams are doing.

    Returns missing-data default (75) if:
      - RESOURCE_UTILIZATION tab is empty
      - Estimated_Utilization_Pct contains no parseable values
    """
    df = _tab(dataframes, _TAB_RESOURCE_UTILIZATION)
    if df.empty or "Estimated_Utilization_Pct" not in df.columns:
        return _missing("5.1")

    values = [_to_float(v) for v in df["Estimated_Utilization_Pct"] if _to_float(v) is not None]
    if not values:
        return _missing("5.1")

    peak = max(values)
    log.info("5.1 Team Utilization Pressure: %.1f%% (peak)", peak)
    return _computed("5.1", peak)


def score_5_2(
    dataframes: dict[str, pd.DataFrame],
    metadata: dict[str, str],
) -> SubCategoryResult:
    """
    5.2  Reprioritization Impact Rate  [NON-COMPUTABLE]

    Requires an event log tracking reprioritization frequency across periods.
    A single intake submission captures current state, not the rate of change
    events over time. This metric requires at least two submission periods
    with intermediate reprioritization event logging between them.

    Always scores at the missing-data default (65).
    """
    return _missing("5.2")


def score_5_3(
    dataframes: dict[str, pd.DataFrame],
    metadata: dict[str, str],
) -> SubCategoryResult:
    """
    5.3  Reactive Work Ratio

    Condition: estimated percentage of team capacity consumed by unplanned,
               reactive work (fire-fighting, rework, unscheduled escalation).
    Proxy:     Percentage of teams whose Estimated_Utilization_Pct exceeds
               100%. Teams above planned capacity are structurally forced to
               consume the excess in reactive mode — re-prioritizing,
               handling spillover, and absorbing unplanned demand.
    Source:    RESOURCE_UTILIZATION.Estimated_Utilization_Pct

    This proxy is distinct from 5.1 (which measures peak overload) and
    5.4 (which measures average available capacity). 5.3 measures the
    breadth of overload — how many teams are in reactive territory.

    Returns missing-data default (60) if:
      - RESOURCE_UTILIZATION tab is empty
      - No parseable utilization values
    """
    df = _tab(dataframes, _TAB_RESOURCE_UTILIZATION)
    if df.empty or "Estimated_Utilization_Pct" not in df.columns:
        return _missing("5.3")

    values = [_to_float(v) for v in df["Estimated_Utilization_Pct"] if _to_float(v) is not None]
    if not values:
        return _missing("5.3")

    over_capacity = sum(1 for v in values if v > 100.0)
    pct = (over_capacity / len(values)) * 100.0
    log.info(
        "5.3 Reactive Work Ratio: %.1f%% (%d of %d teams over 100%%)",
        pct, over_capacity, len(values),
    )
    return _computed("5.3", pct)


def score_5_4(
    dataframes: dict[str, pd.DataFrame],
    metadata: dict[str, str],
) -> SubCategoryResult:
    """
    5.4  Adaptive Capacity Reserve  [INVERTED]

    Condition: estimated percentage of organizational capacity available to
               absorb new transformation demand without degrading existing programs.
    Formula:   100 - weighted_average(Estimated_Utilization_Pct, weight = Team_Size)
               Clamped to [0.0, 100.0] (negative available capacity is set to 0).
    Source:    RESOURCE_UTILIZATION.Estimated_Utilization_Pct, Team_Size

    Team_Size-weighted average provides a more accurate portfolio-level
    utilization figure than a simple mean, since larger teams represent
    more total capacity in the portfolio.

    Falls back to unweighted average if Team_Size is absent or all null.
    Returns missing-data default (70) if:
      - RESOURCE_UTILIZATION tab is empty
      - No parseable utilization values
    """
    df = _tab(dataframes, _TAB_RESOURCE_UTILIZATION)
    if df.empty or "Estimated_Utilization_Pct" not in df.columns:
        return _missing("5.4")

    util_values = [_to_float(v) for v in df["Estimated_Utilization_Pct"]]
    if all(v is None for v in util_values):
        return _missing("5.4")

    # Build parallel lists of (utilization, team_size) for non-null rows.
    utils: list[float] = []
    sizes: list[float] = []

    has_size_col = "Team_Size" in df.columns

    for i, u in enumerate(util_values):
        if u is None:
            continue
        utils.append(u)
        if has_size_col:
            s = _to_float(df["Team_Size"].iloc[i])
            sizes.append(s if (s is not None and s > 0.0) else 1.0)
        else:
            sizes.append(1.0)  # Unweighted fallback

    if not utils:
        return _missing("5.4")

    weighted_avg = sum(u * s for u, s in zip(utils, sizes)) / sum(sizes)
    available = max(0.0, 100.0 - weighted_avg)
    log.info(
        "5.4 Adaptive Capacity Reserve: %.1f%% available (weighted avg util: %.1f%%)",
        available, weighted_avg,
    )
    return _computed("5.4", available)


def score_5_5(
    dataframes: dict[str, pd.DataFrame],
    metadata: dict[str, str],
) -> SubCategoryResult:
    """
    5.5  Retention Risk Indicator

    Condition: number of identified high-risk retention flags on
               critical-path program staff.
    Source:    HEADCOUNT_SIGNALS.Retention_Risk_Flag == "High"

    Counts the absolute number of staff with a High retention risk flag.
    If the tab has rows but zero High flags, this scores near zero (healthy).
    If the tab is empty, returns the missing-data default (55) because
    the absence of any headcount signal data means retention risk has not
    been assessed, which is itself a governance gap.

    Returns missing-data default (55) if HEADCOUNT_SIGNALS tab is empty.
    """
    df = _tab(dataframes, _TAB_HEADCOUNT_SIGNALS)
    if df.empty or "Retention_Risk_Flag" not in df.columns:
        return _missing("5.5")

    if len(df) == 0:
        return _missing("5.5")

    high_risk_count = df["Retention_Risk_Flag"].apply(
        lambda v: str(v).strip() == "High" if not pd.isna(v) else False
    ).sum()

    count = float(high_risk_count)
    log.info("5.5 Retention Risk: %d High-flag records", int(count))
    return _computed("5.5", count)


# ---------------------------------------------------------------------------
# Scorer registry and public interface
# ---------------------------------------------------------------------------

_SCORERS: dict[str, Callable[[dict[str, pd.DataFrame], dict[str, str]], SubCategoryResult]] = {
    "1.1": score_1_1,
    "1.2": score_1_2,
    "1.3": score_1_3,
    "1.4": score_1_4,
    "2.1": score_2_1,
    "2.2": score_2_2,
    "2.3": score_2_3,
    "2.4": score_2_4,
    "3.1": score_3_1,
    "3.2": score_3_2,
    "3.3": score_3_3,
    "3.4": score_3_4,
    "4.1": score_4_1,
    "4.2": score_4_2,
    "4.3": score_4_3,
    "4.4": score_4_4,
    "5.1": score_5_1,
    "5.2": score_5_2,
    "5.3": score_5_3,
    "5.4": score_5_4,
    "5.5": score_5_5,
}


def compute_all_sub_categories(
    dataframes: dict[str, pd.DataFrame],
    metadata: dict[str, str],
) -> dict[str, SubCategoryResult]:
    """
    Compute all 21 OEI sub-category scores for one intake submission.

    Calls each sub-category scoring function in turn. Functions that cannot
    compute their metric from the available data return the corresponding
    missing-data default. Non-computable sub-categories always return their
    spec-defined default regardless of data availability.

    Args:
        dataframes: dict mapping tab name to validated, normalized pandas
                    DataFrame (as produced by intake.validator.validate_workbook).
        metadata:   dict of METADATA tab key-value pairs (as produced by
                    intake.validator.validate_workbook). Must include
                    Reporting_Period_Start and Reporting_Period_End.

    Returns:
        dict[str, SubCategoryResult]: all 21 results keyed by sub-category ID
        (e.g. "1.1", "3.4"). Every key in SCORE_BANDS is guaranteed to be
        present in the returned dict.

    Raises:
        No exceptions are raised. All data errors are absorbed into the
        is_missing_data flag of the relevant SubCategoryResult.
    """
    results: dict[str, SubCategoryResult] = {}
    for sub_id, scorer in _SCORERS.items():
        try:
            results[sub_id] = scorer(dataframes, metadata)
        except Exception as exc:
            # Defensive catch: any unexpected error in a scorer falls back to
            # the missing-data default rather than halting the entire run.
            log.info(
                "Sub-category %s scorer raised an unexpected error; "
                "falling back to missing-data default. Error: %s",
                sub_id, exc,
            )
            results[sub_id] = _missing(sub_id)

    log.info(
        "Sub-category scoring complete: %d computed, %d at missing-data default",
        sum(1 for r in results.values() if not r.is_missing_data),
        sum(1 for r in results.values() if r.is_missing_data),
    )
    return results
