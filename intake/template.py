"""
intake/template.py
CPOI Platform -- Blank intake workbook generator (client-friendly).

Builds the standardized CP_OEI_DataIntake workbook with the exact headers the
validator expects, plus guardrails that make it hard to fill in wrong:
  - Dropdown (data validation) lists on every categorical column, so clients
    pick allowed values instead of typing free text.
  - Date columns pre-formatted as YYYY-MM-DD.
  - An Instructions sheet explaining each tab and the rules.
  - Frozen header rows.

Headers and allowed values are pulled from intake.validator so the template
can never drift from the validation rules.
"""

from __future__ import annotations

import io
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from intake.validator import (
    REQUIRED_TABS, SCHEMA, TAB_METADATA, METADATA_REQUIRED_FIELDS,
)

_METADATA_HINTS = {
    "Reporting_Period_Start": "YYYY-MM-DD",
    "Reporting_Period_End": "YYYY-MM-DD",
    "Submission_Date": "YYYY-MM-DD",
    "Engagement_Type": "Snapshot or OEIL_Month_1",
    "Data_Version": "1.0",
}

# Y/N columns are not in the validator's enum maps (they're checked separately),
# so list them here to attach dropdowns in the template.
_YN_COLUMNS = {"Decision_Made", "Milestone_At_Risk", "Leadership_Aware"}

_HEADER_FONT = Font(bold=True, color="FFFFFF")
_HEADER_FILL = PatternFill(start_color="2E4A6A", end_color="2E4A6A", fill_type="solid")
_KEY_FONT = Font(bold=True)
_TITLE_FONT = Font(bold=True, size=14, color="2E4A6A")
_NOTE_FONT = Font(italic=True, color="6B7280")

_DATA_ROWS = 600  # how far down to extend dropdowns / date formatting


def _add_dropdown(ws, col_letter: str, values: list[str]) -> None:
    options = ",".join(values)
    dv = DataValidation(
        type="list", formula1=f'"{options}"', allow_blank=True, showErrorMessage=True
    )
    dv.errorTitle = "Invalid entry"
    dv.error = f"Please choose one of: {', '.join(values)}"
    dv.promptTitle = "Choose from the list"
    dv.prompt = f"Allowed values: {', '.join(values)}"
    ws.add_data_validation(dv)
    dv.add(f"{col_letter}2:{col_letter}{_DATA_ROWS}")


def _instructions_sheet(wb: Workbook) -> None:
    ws = wb.create_sheet(title="Instructions")
    ws["A1"] = "Criterion Partners - OEI Data Intake"
    ws["A1"].font = _TITLE_FONT
    lines = [
        "",
        "How to complete this workbook:",
        "  1. Fill in each of the 8 tabs at the bottom. Do not rename tabs or columns, and do not delete the header row.",
        "  2. Cells with a dropdown arrow: click the cell and pick a value from the list. Do not type your own.",
        "  3. Dates: enter as YYYY-MM-DD (for example 2026-05-31). Any standard date entry is accepted.",
        "  4. Leave a cell blank only if the column is optional. Required fields must be filled.",
        "  5. On the METADATA tab, type your values in column B next to each label.",
        "",
        "Tabs in this workbook:",
        "  INITIATIVES         - your active initiatives/programs and their reported status",
        "  ESCALATIONS         - issues escalated, when raised/resolved, and at what level",
        "  DEPENDENCIES        - cross-initiative dependencies and their status",
        "  RESOURCE_UTILIZATION- teams, size, allocation, utilization, open requisitions",
        "  GOVERNANCE_EVENTS   - decisions, approvals, reviews, steering committees",
        "  REPORTING_VARIANCE  - reported status vs actual risk per initiative",
        "  HEADCOUNT_SIGNALS   - roles, tenure, retention risk",
        "  METADATA            - reporting period and submission details",
        "",
        "When you are done, save the file and upload it in your Criterion Partners portal",
        "under 'Submit Data'. Your submission is reviewed before it is processed.",
        "",
        "Questions? Contact intelligence@criterion-partners.com",
    ]
    for i, text in enumerate(lines, start=2):
        ws.cell(row=i, column=1, value=text)
        if text.startswith(("How", "Tabs")):
            ws.cell(row=i, column=1).font = _KEY_FONT
    ws.column_dimensions["A"].width = 110


def build_blank_template() -> bytes:
    """Return the blank, guard-railed intake workbook as .xlsx bytes."""
    wb = Workbook()
    wb.remove(wb.active)

    _instructions_sheet(wb)

    for tab in REQUIRED_TABS:
        ws = wb.create_sheet(title=tab)

        if tab == TAB_METADATA:
            for field in METADATA_REQUIRED_FIELDS:
                ws.append([field, _METADATA_HINTS.get(field, "")])
            for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=1, max_col=1):
                row[0].font = _KEY_FONT
            ws.column_dimensions["A"].width = 26
            ws.column_dimensions["B"].width = 34
            continue

        schema = SCHEMA[tab]
        headers = list(schema.required_columns) + list(schema.optional_columns)
        ws.append(headers)
        for cell in ws[1]:
            cell.font = _HEADER_FONT
            cell.fill = _HEADER_FILL
            cell.alignment = Alignment(horizontal="center")

        enum_map = {**schema.enum_columns, **schema.optional_enum_columns}
        date_cols = set(schema.date_columns)

        for idx, header in enumerate(headers, start=1):
            letter = get_column_letter(idx)
            ws.column_dimensions[letter].width = 22
            if header in enum_map:
                _add_dropdown(ws, letter, list(enum_map[header]))
            elif header in _YN_COLUMNS:
                _add_dropdown(ws, letter, ["Y", "N"])
            if header in date_cols:
                for r in range(2, _DATA_ROWS + 1):
                    ws.cell(row=r, column=idx).number_format = "yyyy-mm-dd"

        ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def template_filename() -> str:
    """A friendly default download name including the current month."""
    return f"CP_OEI_DataIntake_Template_{date.today():%Y_%m}.xlsx"
