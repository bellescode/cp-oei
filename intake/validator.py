"""
intake/validator.py
CPOI Platform — Excel Intake Workbook Validator

Validates an uploaded CP_OEI_DataIntake workbook against the 8-tab schema
defined in cpoi-sdlc-spec.md Part 3 before any data is written to the database.

Design contract:
  - Returns a ValidationResult. If valid is False, no dataframes are returned
    and errors contains at least one entry naming the exact tab, row, and field
    that failed.
  - Never writes to the database. Never modifies the source file.
  - All 8 tabs must pass every check, or the entire workbook is rejected.
  - Partial ingestion is not possible: the caller receives either clean
    normalized dataframes or a structured error report, never both.

Usage:
    from intake.validator import validate_workbook

    result = validate_workbook(Path("CP_OEI_DataIntake_Acme_2026_05.xlsx"))
    if not result.valid:
        for error in result.errors:
            print(error)
    else:
        # result.dataframes and result.metadata are ready for the ingestion layer
        pass
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

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


log = _build_logger("cpoi.intake.validator")

# ---------------------------------------------------------------------------
# Schema definitions
# Each entry defines the columns for one tab, classified by validation role.
# These definitions are the source of truth for all validation logic.
# Any change to the intake schema requires a version increment of this file.
# ---------------------------------------------------------------------------

# Required columns: must be present in the header row; null values not allowed.
# Optional columns: must be present in the header row; null values are allowed.
# Date columns: subset of required/optional; must parse as YYYY-MM-DD when non-null.
# Enum columns: map of column name -> allowed value set.
# Numeric columns: subset of required; must be numeric when non-null.
# FK columns: map of column name -> (source_tab, source_column) for cross-tab checks.

TAB_INITIATIVES = "INITIATIVES"
TAB_ESCALATIONS = "ESCALATIONS"
TAB_DEPENDENCIES = "DEPENDENCIES"
TAB_RESOURCE_UTILIZATION = "RESOURCE_UTILIZATION"
TAB_GOVERNANCE_EVENTS = "GOVERNANCE_EVENTS"
TAB_REPORTING_VARIANCE = "REPORTING_VARIANCE"
TAB_HEADCOUNT_SIGNALS = "HEADCOUNT_SIGNALS"
TAB_METADATA = "METADATA"

REQUIRED_TABS: list[str] = [
    TAB_INITIATIVES,
    TAB_ESCALATIONS,
    TAB_DEPENDENCIES,
    TAB_RESOURCE_UTILIZATION,
    TAB_GOVERNANCE_EVENTS,
    TAB_REPORTING_VARIANCE,
    TAB_HEADCOUNT_SIGNALS,
    TAB_METADATA,
]

# -- Tab schemas --

@dataclass(frozen=True)
class TabSchema:
    """Complete validation schema for one data tab (not METADATA)."""
    required_columns: list[str]
    optional_columns: list[str]
    date_columns: list[str]               # validated when non-null
    required_date_columns: list[str]      # subset of required; null not allowed
    enum_columns: dict[str, list[str]]    # column -> allowed values (required cols only)
    optional_enum_columns: dict[str, list[str]]  # column -> allowed values (optional cols)
    numeric_columns: list[str]            # required numeric fields
    optional_numeric_columns: list[str]   # optional numeric fields
    fk_columns: dict[str, tuple[str, str]]       # column -> (source_tab, source_column)
    optional_fk_columns: dict[str, tuple[str, str]]  # optional FK (validated when non-null)


SCHEMA: dict[str, TabSchema] = {

    TAB_INITIATIVES: TabSchema(
        required_columns=[
            "Initiative_ID", "Initiative_Name", "Priority_Classification",
            "Status_Reported", "Program_Owner", "Start_Date",
            "Target_Completion_Date", "Current_Phase",
            "Open_Blockers", "Critical_Blockers", "Last_Status_Update_Date",
        ],
        optional_columns=["Notes"],
        date_columns=["Start_Date", "Target_Completion_Date", "Last_Status_Update_Date"],
        required_date_columns=["Start_Date", "Target_Completion_Date", "Last_Status_Update_Date"],
        enum_columns={
            "Priority_Classification": ["Critical", "High", "Medium", "Low"],
            "Status_Reported": ["Green", "Yellow", "Red"],
        },
        optional_enum_columns={},
        numeric_columns=["Open_Blockers", "Critical_Blockers"],
        optional_numeric_columns=[],
        fk_columns={},
        optional_fk_columns={},
    ),

    TAB_ESCALATIONS: TabSchema(
        required_columns=[
            "Escalation_ID", "Initiative_ID", "Date_Raised",
            "Raised_By_Level", "Escalation_Category", "Description",
        ],
        optional_columns=[
            "Date_Resolved", "Resolved_At_Level", "Resolution_Days", "Outcome",
        ],
        date_columns=["Date_Raised", "Date_Resolved"],
        required_date_columns=["Date_Raised"],
        enum_columns={
            "Raised_By_Level": ["IC", "Manager", "Director", "VP", "C-Suite"],
            "Escalation_Category": [
                "Resource", "Dependency", "Scope", "Governance", "Technical"
            ],
        },
        optional_enum_columns={
            "Resolved_At_Level": ["IC", "Manager", "Director", "VP", "C-Suite"],
        },
        numeric_columns=[],
        optional_numeric_columns=["Resolution_Days"],
        fk_columns={
            "Initiative_ID": (TAB_INITIATIVES, "Initiative_ID"),
        },
        optional_fk_columns={},
    ),

    TAB_DEPENDENCIES: TabSchema(
        required_columns=[
            "Dependency_ID", "Upstream_Initiative_ID", "Downstream_Initiative_ID",
            "Dependency_Type", "Status", "Days_Open", "Owner",
        ],
        optional_columns=["Notes"],
        date_columns=[],
        required_date_columns=[],
        enum_columns={
            "Dependency_Type": ["Blocking", "Enabling", "Informational"],
            "Status": ["Active", "Resolved", "At-Risk"],
        },
        optional_enum_columns={},
        numeric_columns=["Days_Open"],
        optional_numeric_columns=[],
        fk_columns={
            "Upstream_Initiative_ID": (TAB_INITIATIVES, "Initiative_ID"),
            "Downstream_Initiative_ID": (TAB_INITIATIVES, "Initiative_ID"),
        },
        optional_fk_columns={},
    ),

    TAB_RESOURCE_UTILIZATION: TabSchema(
        required_columns=[
            "Team_Name", "Team_Size", "Allocated_Programs",
            "Estimated_Utilization_Pct", "Open_Requisitions", "Avg_Days_Open_Reqs",
        ],
        optional_columns=["Notes"],
        date_columns=[],
        required_date_columns=[],
        enum_columns={},
        optional_enum_columns={},
        numeric_columns=[
            "Team_Size", "Allocated_Programs", "Estimated_Utilization_Pct",
            "Open_Requisitions", "Avg_Days_Open_Reqs",
        ],
        optional_numeric_columns=[],
        fk_columns={},
        optional_fk_columns={},
    ),

    TAB_GOVERNANCE_EVENTS: TabSchema(
        required_columns=[
            "Event_ID", "Event_Type", "Date_Scheduled", "Decision_Made",
        ],
        optional_columns=[
            "Date_Occurred", "Delay_Days", "Initiative_ID", "Notes",
        ],
        date_columns=["Date_Scheduled", "Date_Occurred"],
        required_date_columns=["Date_Scheduled"],
        enum_columns={
            "Event_Type": ["Decision", "Approval", "Review", "Steering Committee"],
            "Decision_Made": ["Y", "N"],
        },
        optional_enum_columns={},
        numeric_columns=[],
        optional_numeric_columns=["Delay_Days"],
        fk_columns={},
        optional_fk_columns={
            "Initiative_ID": (TAB_INITIATIVES, "Initiative_ID"),
        },
    ),

    TAB_REPORTING_VARIANCE: TabSchema(
        required_columns=[
            "Initiative_ID", "Reported_Status", "Actual_Blocker_Count",
            "Milestone_At_Risk", "Leadership_Aware",
        ],
        optional_columns=["Variance_Detected", "Notes"],
        date_columns=[],
        required_date_columns=[],
        enum_columns={
            "Reported_Status": ["Green", "Yellow", "Red"],
            "Milestone_At_Risk": ["Y", "N"],
            "Leadership_Aware": ["Y", "N"],
        },
        optional_enum_columns={},
        numeric_columns=["Actual_Blocker_Count"],
        optional_numeric_columns=[],
        fk_columns={
            "Initiative_ID": (TAB_INITIATIVES, "Initiative_ID"),
        },
        optional_fk_columns={},
    ),

    TAB_HEADCOUNT_SIGNALS: TabSchema(
        required_columns=[
            "Role_Title", "Team", "Tenure_Months",
            "Program_Assignment", "Escalation_Count_Last_90_Days",
            "Retention_Risk_Flag",
        ],
        optional_columns=["Notes"],
        date_columns=[],
        required_date_columns=[],
        enum_columns={
            "Retention_Risk_Flag": ["High", "Medium", "Low"],
        },
        optional_enum_columns={},
        numeric_columns=["Tenure_Months", "Escalation_Count_Last_90_Days"],
        optional_numeric_columns=[],
        fk_columns={},
        optional_fk_columns={},
    ),
}

# METADATA tab fields — key-value layout (column A = key, column B = value)
METADATA_REQUIRED_FIELDS: list[str] = [
    "Client_Name",
    "Reporting_Period_Start",
    "Reporting_Period_End",
    "Submitted_By",
    "Submission_Date",
    "Engagement_Type",
    "Data_Version",
]
METADATA_DATE_FIELDS: list[str] = [
    "Reporting_Period_Start",
    "Reporting_Period_End",
    "Submission_Date",
]

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    """
    Outcome of validating one intake workbook.

    Attributes:
        valid: True only when every check across all 8 tabs passed.
        errors: List of human-readable error strings. Each error names the
                tab, row (1-indexed, header = row 1), and column that failed.
                Empty when valid is True.
        dataframes: Dict mapping tab name to a cleaned pandas DataFrame.
                    Populated only when valid is True. None otherwise.
        metadata: Dict of METADATA tab key-value pairs.
                  Populated only when valid is True. None otherwise.
    """
    valid: bool
    errors: list[str] = field(default_factory=list)
    dataframes: dict[str, pd.DataFrame] | None = None
    metadata: dict[str, str] | None = None


# ---------------------------------------------------------------------------
# Internal validation helpers
# ---------------------------------------------------------------------------

def _check_date_value(value: Any) -> bool:
    """
    Return True if value is a non-null string parseable as YYYY-MM-DD,
    or a datetime/date object (openpyxl may return these directly).

    Args:
        value: cell value from a pandas DataFrame.

    Returns:
        bool: True if the value is a valid date representation.
    """
    if pd.isna(value):
        return False
    if isinstance(value, datetime):
        return True
    if hasattr(value, "year"):  # date object
        return True
    try:
        datetime.strptime(str(value).strip(), "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _normalize_date_value(value: Any) -> str | None:
    """
    Convert a cell value to a YYYY-MM-DD string, or return None if null.

    Args:
        value: cell value that has already passed _check_date_value.

    Returns:
        str | None: ISO date string or None.
    """
    if pd.isna(value):
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if hasattr(value, "year"):
        return value.strftime("%Y-%m-%d")
    return str(value).strip()


def _validate_tab_columns(
    tab_name: str,
    df: pd.DataFrame,
    schema: TabSchema,
) -> list[str]:
    """
    Check that all required and optional columns are present in the dataframe.

    Args:
        tab_name: display name of the tab (for error messages).
        df: raw dataframe read from the workbook tab.
        schema: TabSchema for this tab.

    Returns:
        list[str]: error strings; empty if all columns are present.
    """
    errors: list[str] = []
    all_expected = schema.required_columns + schema.optional_columns
    for col in all_expected:
        if col not in df.columns:
            errors.append(
                f"Tab '{tab_name}', column '{col}' is missing from the header row."
            )
    return errors


def _validate_required_not_null(
    tab_name: str,
    df: pd.DataFrame,
    schema: TabSchema,
) -> list[str]:
    """
    Check that no required column contains a null value in any data row.

    Args:
        tab_name: display name of the tab.
        df: dataframe with header row already consumed (index starts at 0 = data row 1).
        schema: TabSchema for this tab.

    Returns:
        list[str]: error strings; empty if all required fields are populated.
    """
    errors: list[str] = []
    for col in schema.required_columns:
        if col not in df.columns:
            continue  # already reported by column check
        for i, value in enumerate(df[col]):
            row_num = i + 2  # row 1 = header, row 2 = first data row
            if pd.isna(value) or str(value).strip() == "":
                errors.append(
                    f"Tab '{tab_name}', row {row_num}, column '{col}': "
                    f"value is required but is empty or null."
                )
    return errors


def _validate_date_columns(
    tab_name: str,
    df: pd.DataFrame,
    schema: TabSchema,
) -> list[str]:
    """
    Validate that date column values parse as YYYY-MM-DD when non-null.
    Required date columns are additionally checked for nulls here.

    Args:
        tab_name: display name of the tab.
        df: dataframe (index 0 = first data row).
        schema: TabSchema for this tab.

    Returns:
        list[str]: error strings; empty if all date values are valid.
    """
    errors: list[str] = []
    for col in schema.date_columns:
        if col not in df.columns:
            continue
        is_required = col in schema.required_date_columns
        for i, value in enumerate(df[col]):
            row_num = i + 2
            if pd.isna(value) or str(value).strip() == "":
                if is_required:
                    errors.append(
                        f"Tab '{tab_name}', row {row_num}, column '{col}': "
                        f"date value is required but is empty or null."
                    )
                continue  # null in optional date column is allowed
            if not _check_date_value(value):
                errors.append(
                    f"Tab '{tab_name}', row {row_num}, column '{col}': "
                    f"'{value}' is not a valid date. Required format is YYYY-MM-DD."
                )
    return errors


def _validate_enum_columns(
    tab_name: str,
    df: pd.DataFrame,
    schema: TabSchema,
) -> list[str]:
    """
    Validate that required enum columns contain only allowed values.
    Also validates optional enum columns when a value is present.

    Args:
        tab_name: display name of the tab.
        df: dataframe (index 0 = first data row).
        schema: TabSchema for this tab.

    Returns:
        list[str]: error strings; empty if all enum values are valid.
    """
    errors: list[str] = []
    checks = list(schema.enum_columns.items()) + list(schema.optional_enum_columns.items())
    for col, allowed in checks:
        if col not in df.columns:
            continue
        for i, value in enumerate(df[col]):
            row_num = i + 2
            if pd.isna(value) or str(value).strip() == "":
                continue  # nulls in optional enum columns are allowed
            if str(value).strip() not in allowed:
                errors.append(
                    f"Tab '{tab_name}', row {row_num}, column '{col}': "
                    f"'{value}' is not an allowed value. "
                    f"Allowed values are: {allowed}."
                )
    return errors


def _validate_numeric_columns(
    tab_name: str,
    df: pd.DataFrame,
    schema: TabSchema,
) -> list[str]:
    """
    Validate that required numeric columns contain numeric values.
    Also validates optional numeric columns when a value is present.

    Args:
        tab_name: display name of the tab.
        df: dataframe (index 0 = first data row).
        schema: TabSchema for this tab.

    Returns:
        list[str]: error strings; empty if all numeric values are valid.
    """
    errors: list[str] = []
    required_checks = [(c, True) for c in schema.numeric_columns]
    optional_checks = [(c, False) for c in schema.optional_numeric_columns]
    for col, is_required in required_checks + optional_checks:
        if col not in df.columns:
            continue
        for i, value in enumerate(df[col]):
            row_num = i + 2
            if pd.isna(value) or str(value).strip() == "":
                continue  # null handling already covered by required-null check
            try:
                float(value)
            except (ValueError, TypeError):
                errors.append(
                    f"Tab '{tab_name}', row {row_num}, column '{col}': "
                    f"'{value}' is not numeric."
                )
    return errors


def _validate_fk_columns(
    tab_name: str,
    df: pd.DataFrame,
    schema: TabSchema,
    all_dataframes: dict[str, pd.DataFrame],
) -> list[str]:
    """
    Validate that FK column values exist in the referenced source tab.
    Required FKs are checked on every non-null row.
    Optional FKs are checked only when a value is present.

    Args:
        tab_name: display name of the tab being validated.
        df: dataframe for the tab being validated.
        schema: TabSchema for this tab.
        all_dataframes: dict of all already-loaded tab dataframes (for lookup).

    Returns:
        list[str]: error strings; empty if all FK references are valid.
    """
    errors: list[str] = []
    required_fks = [(col, src_tab, src_col) for col, (src_tab, src_col) in schema.fk_columns.items()]
    optional_fks = [(col, src_tab, src_col) for col, (src_tab, src_col) in schema.optional_fk_columns.items()]

    for col, src_tab, src_col in required_fks + optional_fks:
        if col not in df.columns:
            continue
        src_df = all_dataframes.get(src_tab)
        if src_df is None or src_col not in src_df.columns:
            continue  # source tab not yet loaded or column absent; covered elsewhere
        valid_ids: set[str] = set(str(v).strip() for v in src_df[src_col] if not pd.isna(v))
        for i, value in enumerate(df[col]):
            row_num = i + 2
            if pd.isna(value) or str(value).strip() == "":
                continue
            if str(value).strip() not in valid_ids:
                errors.append(
                    f"Tab '{tab_name}', row {row_num}, column '{col}': "
                    f"'{value}' not found in tab '{src_tab}', column '{src_col}'."
                )
    return errors


def _validate_metadata_tab(raw_df: pd.DataFrame) -> tuple[list[str], dict[str, str] | None]:
    """
    Validate the METADATA tab and extract key-value pairs.

    The METADATA tab uses a two-column layout: column A = field name,
    column B = value. The tab does not have a header row — the first row
    is the first key-value pair.

    Args:
        raw_df: raw dataframe read from the METADATA tab (no header assumed).

    Returns:
        tuple[list[str], dict[str, str] | None]:
            - errors: list of error strings; empty if valid.
            - metadata: dict of field->value if valid; None if any error.
    """
    errors: list[str] = []

    if raw_df.shape[1] < 2:
        errors.append(
            "Tab 'METADATA': expected at least two columns (field name, value) "
            "but found fewer. Check that the METADATA tab follows the key-value layout."
        )
        return errors, None

    # Build dict from column A -> column B, stripping whitespace
    kv: dict[str, str] = {}
    for i in range(len(raw_df)):
        key_raw = raw_df.iloc[i, 0]
        val_raw = raw_df.iloc[i, 1]
        if pd.isna(key_raw) or str(key_raw).strip() == "":
            continue
        kv[str(key_raw).strip()] = str(val_raw).strip() if not pd.isna(val_raw) else ""

    for field_name in METADATA_REQUIRED_FIELDS:
        if field_name not in kv:
            errors.append(
                f"Tab 'METADATA', field '{field_name}': "
                f"field is missing from the METADATA tab."
            )
        elif kv[field_name] == "":
            errors.append(
                f"Tab 'METADATA', field '{field_name}': value is required but is empty."
            )

    for field_name in METADATA_DATE_FIELDS:
        if field_name not in kv or kv.get(field_name, "") == "":
            continue  # already reported above
        if not _check_date_value(kv[field_name]):
            errors.append(
                f"Tab 'METADATA', field '{field_name}': "
                f"'{kv[field_name]}' is not a valid date. Required format is YYYY-MM-DD."
            )

    if errors:
        return errors, None
    return [], kv


def _normalize_dataframe(df: pd.DataFrame, schema: TabSchema) -> pd.DataFrame:
    """
    Normalize a validated dataframe: standardize date columns to YYYY-MM-DD
    strings and strip whitespace from string columns.

    Called only after all validation passes — does not re-validate.

    Args:
        df: validated dataframe.
        schema: TabSchema for this tab.

    Returns:
        pd.DataFrame: normalized copy of the dataframe.
    """
    df = df.copy()
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].apply(
                lambda v: str(v).strip() if not pd.isna(v) else v
            )
    for col in schema.date_columns:
        if col in df.columns:
            df[col] = df[col].apply(
                lambda v: _normalize_date_value(v) if not pd.isna(v) else None
            )
    return df


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def validate_workbook(file_path: Path) -> ValidationResult:
    """
    Validate a CP_OEI_DataIntake Excel workbook against the 8-tab intake schema.

    Runs all checks in sequence:
      1. File existence and readability
      2. All 8 required tabs present
      3. Required columns present per tab
      4. No null values in required fields
      5. Date format validity (YYYY-MM-DD)
      6. Enum value validity
      7. Numeric field validity
      8. Cross-tab FK reference validity
      9. METADATA tab completeness and date validity

    If any check fails, a ValidationResult with valid=False is returned
    immediately after exhausting that check category (errors are collected
    within each category before stopping, so the caller receives all errors
    of the same type at once rather than one at a time).

    Args:
        file_path: absolute or relative path to the .xlsx file.

    Returns:
        ValidationResult: valid=True with populated dataframes and metadata,
                          or valid=False with a populated errors list.

    Raises:
        No exceptions are raised. All error conditions are captured in the
        ValidationResult. The caller does not need a try/except.
    """
    log.info("Validation started for '%s'", file_path)
    all_errors: list[str] = []

    # -- Step 1: File existence and readability --
    if not Path(file_path).exists():
        return ValidationResult(
            valid=False,
            errors=[f"File not found: '{file_path}'."],
        )

    try:
        # Read all sheets at once; header=0 for data tabs, handled separately for METADATA.
        raw_sheets: dict[str, pd.DataFrame] = pd.read_excel(
            file_path,
            sheet_name=None,
            header=0,
            dtype=str,   # Read everything as strings to avoid silent coercions.
                         # Numeric and date validation is done explicitly.
            keep_default_na=False,  # Preserve empty strings; handle nulls manually.
        )
    except Exception as exc:
        return ValidationResult(
            valid=False,
            errors=[
                f"File '{file_path}' could not be read as an Excel workbook: {exc}. "
                "Confirm the file is a valid .xlsx file and is not password-protected."
            ],
        )

    # Normalize sheet names: strip whitespace for comparison
    normalized_sheet_names: dict[str, str] = {
        name.strip(): name for name in raw_sheets.keys()
    }

    # -- Step 2: Required tabs present --
    for tab in REQUIRED_TABS:
        if tab not in normalized_sheet_names:
            all_errors.append(
                f"Tab '{tab}' not found in workbook. "
                f"Tabs present: {list(normalized_sheet_names.keys())}."
            )

    if all_errors:
        log.info("Validation failed: missing tabs")
        return ValidationResult(valid=False, errors=all_errors)

    # Load dataframes using the normalized sheet name keys
    dataframes: dict[str, pd.DataFrame] = {}
    for tab in REQUIRED_TABS:
        actual_name = normalized_sheet_names[tab]
        df = raw_sheets[actual_name].copy()
        # Strip whitespace from column names
        df.columns = [str(c).strip() for c in df.columns]
        # Replace empty strings with pd.NA for uniform null handling
        df = df.replace("", pd.NA)
        dataframes[tab] = df

    # -- Steps 3-8: Data tab validation --
    for tab_name, schema in SCHEMA.items():
        df = dataframes[tab_name]

        # Step 3: Column presence
        col_errors = _validate_tab_columns(tab_name, df, schema)
        all_errors.extend(col_errors)

        if col_errors:
            # Cannot validate cell values if columns are missing
            continue

        # Step 4: Required not null
        all_errors.extend(_validate_required_not_null(tab_name, df, schema))

        # Step 5: Date format
        all_errors.extend(_validate_date_columns(tab_name, df, schema))

        # Step 6: Enum values
        all_errors.extend(_validate_enum_columns(tab_name, df, schema))

        # Step 7: Numeric fields
        all_errors.extend(_validate_numeric_columns(tab_name, df, schema))

        # Step 8: FK references (uses already-loaded dataframes for lookup)
        all_errors.extend(_validate_fk_columns(tab_name, df, schema, dataframes))

    if all_errors:
        log.info("Validation failed with %d error(s)", len(all_errors))
        return ValidationResult(valid=False, errors=all_errors)

    # -- Step 9: METADATA tab --
    # METADATA is key-value layout; re-read without a header row.
    try:
        metadata_raw = pd.read_excel(
            file_path,
            sheet_name=normalized_sheet_names[TAB_METADATA],
            header=None,
            dtype=str,
            keep_default_na=False,
        )
        metadata_raw = metadata_raw.replace("", pd.NA)
    except Exception as exc:
        return ValidationResult(
            valid=False,
            errors=[f"Tab 'METADATA' could not be read: {exc}."],
        )

    meta_errors, metadata_dict = _validate_metadata_tab(metadata_raw)
    if meta_errors:
        log.info("Validation failed: METADATA tab errors")
        return ValidationResult(valid=False, errors=meta_errors)

    # -- Normalization: only reached if all checks passed --
    normalized: dict[str, pd.DataFrame] = {}
    for tab_name, schema in SCHEMA.items():
        normalized[tab_name] = _normalize_dataframe(dataframes[tab_name], schema)

    log.info("Validation passed for '%s'", file_path)
    return ValidationResult(
        valid=True,
        errors=[],
        dataframes=normalized,
        metadata=metadata_dict,
    )
