"""
tests/test_validator.py
CPOI Platform — Unit tests for intake/validator.py

Covers:
  Valid input:
    - A fully compliant workbook passes validation
    - Returns populated dataframes and metadata dict
    - All 8 tabs represented in dataframes
    - Date values normalized to YYYY-MM-DD strings

  Tab-level failures:
    - Missing tab returns a specific error naming the tab
    - File not found returns a clear error

  Column-level failures:
    - Missing required column returns error naming tab and column

  Row-level failures (required null):
    - Null in required field returns error naming tab, row, and column

  Date format failures:
    - Non-YYYY-MM-DD date returns error naming tab, row, and column

  Enum failures:
    - Invalid Priority_Classification value caught and named
    - Invalid Status_Reported value caught and named
    - Invalid Raised_By_Level value caught and named
    - Invalid Retention_Risk_Flag value caught and named

  Numeric failures:
    - Non-numeric value in numeric column caught and named

  FK failures:
    - Initiative_ID in ESCALATIONS not present in INITIATIVES caught and named
    - Upstream_Initiative_ID in DEPENDENCIES not present in INITIATIVES caught
    - Initiative_ID in REPORTING_VARIANCE not present in INITIATIVES caught

  METADATA failures:
    - Missing required METADATA field caught
    - Empty required METADATA value caught
    - Invalid date in METADATA date field caught

  No partial ingestion:
    - When valid=False, dataframes is None
    - When valid=True, errors is empty
"""

import sys
from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from intake.validator import (
    TAB_DEPENDENCIES,
    TAB_ESCALATIONS,
    TAB_GOVERNANCE_EVENTS,
    TAB_HEADCOUNT_SIGNALS,
    TAB_INITIATIVES,
    TAB_METADATA,
    TAB_REPORTING_VARIANCE,
    TAB_RESOURCE_UTILIZATION,
    validate_workbook,
)

# ---------------------------------------------------------------------------
# Workbook builder helpers
# ---------------------------------------------------------------------------

def _make_initiatives_df() -> pd.DataFrame:
    return pd.DataFrame([{
        "Initiative_ID": "INI-001",
        "Initiative_Name": "ERP Modernization",
        "Priority_Classification": "Critical",
        "Status_Reported": "Green",
        "Program_Owner": "Alice Smith",
        "Start_Date": "2026-01-01",
        "Target_Completion_Date": "2026-12-31",
        "Current_Phase": "Design",
        "Open_Blockers": "2",
        "Critical_Blockers": "0",
        "Last_Status_Update_Date": "2026-05-01",
        "Notes": "",
    }])


def _make_escalations_df() -> pd.DataFrame:
    return pd.DataFrame([{
        "Escalation_ID": "ESC-001",
        "Initiative_ID": "INI-001",
        "Date_Raised": "2026-04-01",
        "Raised_By_Level": "Manager",
        "Escalation_Category": "Resource",
        "Description": "Headcount gap on integration team",
        "Date_Resolved": "2026-04-10",
        "Resolved_At_Level": "Director",
        "Resolution_Days": "9",
        "Outcome": "Additional contractor approved",
    }])


def _make_dependencies_df() -> pd.DataFrame:
    return pd.DataFrame([{
        "Dependency_ID": "DEP-001",
        "Upstream_Initiative_ID": "INI-001",
        "Downstream_Initiative_ID": "INI-001",
        "Dependency_Type": "Blocking",
        "Status": "Active",
        "Days_Open": "14",
        "Owner": "Bob Jones",
        "Notes": "",
    }])


def _make_resource_utilization_df() -> pd.DataFrame:
    return pd.DataFrame([{
        "Team_Name": "Integration Team",
        "Team_Size": "8",
        "Allocated_Programs": "3",
        "Estimated_Utilization_Pct": "110",
        "Open_Requisitions": "2",
        "Avg_Days_Open_Reqs": "45",
        "Notes": "",
    }])


def _make_governance_events_df() -> pd.DataFrame:
    return pd.DataFrame([{
        "Event_ID": "GOV-001",
        "Event_Type": "Decision",
        "Date_Scheduled": "2026-04-15",
        "Decision_Made": "Y",
        "Date_Occurred": "2026-04-15",
        "Delay_Days": "0",
        "Initiative_ID": "INI-001",
        "Notes": "",
    }])


def _make_reporting_variance_df() -> pd.DataFrame:
    return pd.DataFrame([{
        "Initiative_ID": "INI-001",
        "Reported_Status": "Green",
        "Actual_Blocker_Count": "2",
        "Milestone_At_Risk": "N",
        "Leadership_Aware": "Y",
        "Variance_Detected": "",
        "Notes": "",
    }])


def _make_headcount_signals_df() -> pd.DataFrame:
    return pd.DataFrame([{
        "Role_Title": "Integration Architect",
        "Team": "Integration Team",
        "Tenure_Months": "18",
        "Program_Assignment": "INI-001",
        "Escalation_Count_Last_90_Days": "1",
        "Retention_Risk_Flag": "Medium",
        "Notes": "",
    }])


def _make_metadata_df() -> pd.DataFrame:
    """METADATA tab as key-value pairs (no header row)."""
    return pd.DataFrame([
        ["Client_Name", "Acme Corporation"],
        ["Reporting_Period_Start", "2026-05-01"],
        ["Reporting_Period_End", "2026-05-31"],
        ["Submitted_By", "Jane Doe"],
        ["Submission_Date", "2026-06-01"],
        ["Engagement_Type", "OEIL_Month_1"],
        ["Data_Version", "1.0"],
    ])


def _write_workbook(
    tmp_path: Path,
    overrides: dict[str, pd.DataFrame | None] | None = None,
    filename: str = "test_intake.xlsx",
) -> Path:
    """
    Write a complete valid workbook to tmp_path, applying any overrides.
    Pass None as a tab value to omit that tab entirely.

    Args:
        tmp_path: directory in which to write the file.
        overrides: dict of tab_name -> DataFrame to replace default data,
                   or tab_name -> None to omit the tab.
        filename: output filename.

    Returns:
        Path: path to the written .xlsx file.
    """
    defaults: dict[str, pd.DataFrame] = {
        TAB_INITIATIVES: _make_initiatives_df(),
        TAB_ESCALATIONS: _make_escalations_df(),
        TAB_DEPENDENCIES: _make_dependencies_df(),
        TAB_RESOURCE_UTILIZATION: _make_resource_utilization_df(),
        TAB_GOVERNANCE_EVENTS: _make_governance_events_df(),
        TAB_REPORTING_VARIANCE: _make_reporting_variance_df(),
        TAB_HEADCOUNT_SIGNALS: _make_headcount_signals_df(),
        TAB_METADATA: _make_metadata_df(),
    }
    if overrides:
        for key, value in overrides.items():
            if value is None:
                defaults.pop(key, None)
            else:
                defaults[key] = value

    out_path = tmp_path / filename
    with pd.ExcelWriter(str(out_path), engine="openpyxl") as writer:
        for tab_name, df in defaults.items():
            if tab_name == TAB_METADATA:
                df.to_excel(writer, sheet_name=tab_name, index=False, header=False)
            else:
                df.to_excel(writer, sheet_name=tab_name, index=False)
    return out_path


# ---------------------------------------------------------------------------
# Tests: valid workbook
# ---------------------------------------------------------------------------

def test_valid_workbook_passes(tmp_path: Path) -> None:
    """A fully compliant workbook must pass with valid=True and no errors."""
    path = _write_workbook(tmp_path)
    result = validate_workbook(path)
    assert result.valid is True, f"Expected valid=True but got errors: {result.errors}"
    assert result.errors == []


def test_valid_workbook_returns_dataframes(tmp_path: Path) -> None:
    """A valid workbook must return populated dataframes for all 7 data tabs."""
    path = _write_workbook(tmp_path)
    result = validate_workbook(path)
    assert result.dataframes is not None
    for tab in [
        TAB_INITIATIVES, TAB_ESCALATIONS, TAB_DEPENDENCIES,
        TAB_RESOURCE_UTILIZATION, TAB_GOVERNANCE_EVENTS,
        TAB_REPORTING_VARIANCE, TAB_HEADCOUNT_SIGNALS,
    ]:
        assert tab in result.dataframes, f"Dataframe for tab '{tab}' missing"


def test_valid_workbook_returns_metadata(tmp_path: Path) -> None:
    """A valid workbook must return a populated metadata dict."""
    path = _write_workbook(tmp_path)
    result = validate_workbook(path)
    assert result.metadata is not None
    assert result.metadata["Client_Name"] == "Acme Corporation"
    assert result.metadata["Engagement_Type"] == "OEIL_Month_1"
    assert result.metadata["Data_Version"] == "1.0"


def test_valid_workbook_dates_normalized(tmp_path: Path) -> None:
    """Date columns must be normalized to YYYY-MM-DD strings in the output dataframes."""
    path = _write_workbook(tmp_path)
    result = validate_workbook(path)
    assert result.dataframes is not None
    start = result.dataframes[TAB_INITIATIVES]["Start_Date"].iloc[0]
    assert start == "2026-01-01", f"Date not normalized: '{start}'"


# ---------------------------------------------------------------------------
# Tests: file-level failures
# ---------------------------------------------------------------------------

def test_file_not_found_returns_error() -> None:
    """A non-existent file path must return valid=False with a clear error."""
    result = validate_workbook(Path("/nonexistent/path/intake.xlsx"))
    assert result.valid is False
    assert any("not found" in e for e in result.errors)
    assert result.dataframes is None


# ---------------------------------------------------------------------------
# Tests: missing tab
# ---------------------------------------------------------------------------

def test_missing_tab_returns_named_error(tmp_path: Path) -> None:
    """Omitting a required tab must return an error that names the missing tab."""
    path = _write_workbook(tmp_path, overrides={TAB_ESCALATIONS: None})
    result = validate_workbook(path)
    assert result.valid is False
    assert any(TAB_ESCALATIONS in e for e in result.errors), (
        f"Expected error mentioning '{TAB_ESCALATIONS}' but got: {result.errors}"
    )
    assert result.dataframes is None


# ---------------------------------------------------------------------------
# Tests: missing columns
# ---------------------------------------------------------------------------

def test_missing_required_column_named_in_error(tmp_path: Path) -> None:
    """Dropping a required column must produce an error naming the tab and column."""
    bad_df = _make_initiatives_df().drop(columns=["Priority_Classification"])
    path = _write_workbook(tmp_path, overrides={TAB_INITIATIVES: bad_df})
    result = validate_workbook(path)
    assert result.valid is False
    assert any("Priority_Classification" in e for e in result.errors)
    assert any(TAB_INITIATIVES in e for e in result.errors)


# ---------------------------------------------------------------------------
# Tests: null in required field
# ---------------------------------------------------------------------------

def test_null_in_required_field_names_row_and_column(tmp_path: Path) -> None:
    """A null in a required field must produce an error naming the row and column."""
    bad_df = _make_initiatives_df().copy()
    bad_df.loc[0, "Initiative_Name"] = None
    path = _write_workbook(tmp_path, overrides={TAB_INITIATIVES: bad_df})
    result = validate_workbook(path)
    assert result.valid is False
    assert any("Initiative_Name" in e for e in result.errors)
    assert any("row 2" in e for e in result.errors)  # data row 1 = row 2 in sheet


# ---------------------------------------------------------------------------
# Tests: date format
# ---------------------------------------------------------------------------

def test_invalid_date_format_names_tab_row_column(tmp_path: Path) -> None:
    """A date value that is not YYYY-MM-DD must be caught and named precisely."""
    bad_df = _make_initiatives_df().copy()
    bad_df.loc[0, "Start_Date"] = "01/01/2026"  # US format, not ISO
    path = _write_workbook(tmp_path, overrides={TAB_INITIATIVES: bad_df})
    result = validate_workbook(path)
    assert result.valid is False
    assert any("Start_Date" in e for e in result.errors)
    assert any(TAB_INITIATIVES in e for e in result.errors)


def test_invalid_date_in_optional_date_column_caught(tmp_path: Path) -> None:
    """An invalid date in an optional date column is still caught when present."""
    bad_df = _make_escalations_df().copy()
    bad_df.loc[0, "Date_Resolved"] = "not-a-date"
    path = _write_workbook(tmp_path, overrides={TAB_ESCALATIONS: bad_df})
    result = validate_workbook(path)
    assert result.valid is False
    assert any("Date_Resolved" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Tests: enum validation
# ---------------------------------------------------------------------------

def test_invalid_priority_classification_caught(tmp_path: Path) -> None:
    bad_df = _make_initiatives_df().copy()
    bad_df.loc[0, "Priority_Classification"] = "Urgent"
    path = _write_workbook(tmp_path, overrides={TAB_INITIATIVES: bad_df})
    result = validate_workbook(path)
    assert result.valid is False
    assert any("Priority_Classification" in e for e in result.errors)
    assert any("Urgent" in e for e in result.errors)


def test_invalid_status_reported_caught(tmp_path: Path) -> None:
    bad_df = _make_initiatives_df().copy()
    bad_df.loc[0, "Status_Reported"] = "Blue"
    path = _write_workbook(tmp_path, overrides={TAB_INITIATIVES: bad_df})
    result = validate_workbook(path)
    assert result.valid is False
    assert any("Status_Reported" in e for e in result.errors)


def test_invalid_raised_by_level_caught(tmp_path: Path) -> None:
    bad_df = _make_escalations_df().copy()
    bad_df.loc[0, "Raised_By_Level"] = "Intern"
    path = _write_workbook(tmp_path, overrides={TAB_ESCALATIONS: bad_df})
    result = validate_workbook(path)
    assert result.valid is False
    assert any("Raised_By_Level" in e for e in result.errors)


def test_invalid_retention_risk_flag_caught(tmp_path: Path) -> None:
    bad_df = _make_headcount_signals_df().copy()
    bad_df.loc[0, "Retention_Risk_Flag"] = "Critical"
    path = _write_workbook(tmp_path, overrides={TAB_HEADCOUNT_SIGNALS: bad_df})
    result = validate_workbook(path)
    assert result.valid is False
    assert any("Retention_Risk_Flag" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Tests: numeric validation
# ---------------------------------------------------------------------------

def test_non_numeric_in_numeric_column_caught(tmp_path: Path) -> None:
    bad_df = _make_initiatives_df().copy()
    bad_df.loc[0, "Open_Blockers"] = "TBD"
    path = _write_workbook(tmp_path, overrides={TAB_INITIATIVES: bad_df})
    result = validate_workbook(path)
    assert result.valid is False
    assert any("Open_Blockers" in e for e in result.errors)
    assert any("TBD" in e for e in result.errors)


def test_non_numeric_utilization_pct_caught(tmp_path: Path) -> None:
    bad_df = _make_resource_utilization_df().copy()
    bad_df.loc[0, "Estimated_Utilization_Pct"] = "high"
    path = _write_workbook(tmp_path, overrides={TAB_RESOURCE_UTILIZATION: bad_df})
    result = validate_workbook(path)
    assert result.valid is False
    assert any("Estimated_Utilization_Pct" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Tests: FK validation
# ---------------------------------------------------------------------------

def test_fk_escalation_initiative_id_not_found(tmp_path: Path) -> None:
    """An Initiative_ID in ESCALATIONS not present in INITIATIVES must be caught."""
    bad_df = _make_escalations_df().copy()
    bad_df.loc[0, "Initiative_ID"] = "INI-999"
    path = _write_workbook(tmp_path, overrides={TAB_ESCALATIONS: bad_df})
    result = validate_workbook(path)
    assert result.valid is False
    assert any("INI-999" in e for e in result.errors)
    assert any(TAB_ESCALATIONS in e for e in result.errors)


def test_fk_dependency_upstream_not_found(tmp_path: Path) -> None:
    bad_df = _make_dependencies_df().copy()
    bad_df.loc[0, "Upstream_Initiative_ID"] = "INI-888"
    path = _write_workbook(tmp_path, overrides={TAB_DEPENDENCIES: bad_df})
    result = validate_workbook(path)
    assert result.valid is False
    assert any("INI-888" in e for e in result.errors)


def test_fk_reporting_variance_not_found(tmp_path: Path) -> None:
    bad_df = _make_reporting_variance_df().copy()
    bad_df.loc[0, "Initiative_ID"] = "INI-777"
    path = _write_workbook(tmp_path, overrides={TAB_REPORTING_VARIANCE: bad_df})
    result = validate_workbook(path)
    assert result.valid is False
    assert any("INI-777" in e for e in result.errors)


def test_optional_fk_governance_events_validated_when_present(tmp_path: Path) -> None:
    """The optional Initiative_ID FK in GOVERNANCE_EVENTS must be checked when non-null."""
    bad_df = _make_governance_events_df().copy()
    bad_df.loc[0, "Initiative_ID"] = "INI-404"
    path = _write_workbook(tmp_path, overrides={TAB_GOVERNANCE_EVENTS: bad_df})
    result = validate_workbook(path)
    assert result.valid is False
    assert any("INI-404" in e for e in result.errors)


def test_optional_fk_governance_events_null_is_valid(tmp_path: Path) -> None:
    """A null Initiative_ID in GOVERNANCE_EVENTS must not produce a FK error."""
    ok_df = _make_governance_events_df().copy()
    ok_df.loc[0, "Initiative_ID"] = None
    path = _write_workbook(tmp_path, overrides={TAB_GOVERNANCE_EVENTS: ok_df})
    result = validate_workbook(path)
    assert result.valid is True, f"Expected valid=True but got: {result.errors}"


# ---------------------------------------------------------------------------
# Tests: METADATA failures
# ---------------------------------------------------------------------------

def test_metadata_missing_field_caught(tmp_path: Path) -> None:
    """A missing METADATA field must be caught and named."""
    bad_meta = pd.DataFrame([
        ["Client_Name", "Acme Corporation"],
        # Reporting_Period_Start omitted
        ["Reporting_Period_End", "2026-05-31"],
        ["Submitted_By", "Jane Doe"],
        ["Submission_Date", "2026-06-01"],
        ["Engagement_Type", "OEIL_Month_1"],
        ["Data_Version", "1.0"],
    ])
    path = _write_workbook(tmp_path, overrides={TAB_METADATA: bad_meta})
    result = validate_workbook(path)
    assert result.valid is False
    assert any("Reporting_Period_Start" in e for e in result.errors)


def test_metadata_empty_value_caught(tmp_path: Path) -> None:
    """An empty value for a required METADATA field must be caught."""
    bad_meta = pd.DataFrame([
        ["Client_Name", ""],   # empty
        ["Reporting_Period_Start", "2026-05-01"],
        ["Reporting_Period_End", "2026-05-31"],
        ["Submitted_By", "Jane Doe"],
        ["Submission_Date", "2026-06-01"],
        ["Engagement_Type", "OEIL_Month_1"],
        ["Data_Version", "1.0"],
    ])
    path = _write_workbook(tmp_path, overrides={TAB_METADATA: bad_meta})
    result = validate_workbook(path)
    assert result.valid is False
    assert any("Client_Name" in e for e in result.errors)


def test_metadata_invalid_date_caught(tmp_path: Path) -> None:
    """An invalid date in a METADATA date field must be caught."""
    bad_meta = pd.DataFrame([
        ["Client_Name", "Acme Corporation"],
        ["Reporting_Period_Start", "May 2026"],  # not YYYY-MM-DD
        ["Reporting_Period_End", "2026-05-31"],
        ["Submitted_By", "Jane Doe"],
        ["Submission_Date", "2026-06-01"],
        ["Engagement_Type", "OEIL_Month_1"],
        ["Data_Version", "1.0"],
    ])
    path = _write_workbook(tmp_path, overrides={TAB_METADATA: bad_meta})
    result = validate_workbook(path)
    assert result.valid is False
    assert any("Reporting_Period_Start" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Tests: no partial ingestion contract
# ---------------------------------------------------------------------------

def test_invalid_result_has_no_dataframes(tmp_path: Path) -> None:
    """When valid=False, dataframes must be None (no partial ingestion)."""
    path = _write_workbook(tmp_path, overrides={TAB_ESCALATIONS: None})
    result = validate_workbook(path)
    assert result.valid is False
    assert result.dataframes is None
    assert result.metadata is None


def test_valid_result_has_no_errors(tmp_path: Path) -> None:
    """When valid=True, errors must be an empty list."""
    path = _write_workbook(tmp_path)
    result = validate_workbook(path)
    assert result.valid is True
    assert result.errors == []


# ---------------------------------------------------------------------------
# Tests: empty data tabs are accepted
# ---------------------------------------------------------------------------

def test_empty_data_tab_is_valid(tmp_path: Path) -> None:
    """
    A tab with a header row but no data rows must be accepted as valid.
    Some clients may have no governance events or headcount flags to report.
    """
    empty_gov = pd.DataFrame(columns=[
        "Event_ID", "Event_Type", "Date_Scheduled", "Decision_Made",
        "Date_Occurred", "Delay_Days", "Initiative_ID", "Notes",
    ])
    path = _write_workbook(tmp_path, overrides={TAB_GOVERNANCE_EVENTS: empty_gov})
    result = validate_workbook(path)
    assert result.valid is True, f"Expected valid=True but got: {result.errors}"
