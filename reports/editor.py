"""
reports/editor.py
CPOI Platform -- Hybrid Editing Flow

Manages the Managing Partner review-and-edit gate between AI narrative
generation and report file production. The AI never publishes directly.
Every narrative section must be explicitly approved by the Managing
Partner before finalize_report() unlocks file generation.

Flow:
  1. generate_narratives() (reports/narrator.py) produces a completed
     context dict with all narrative fields populated.
  2. save_draft(conn, report_id, context) writes one report_drafts row
     per narrative section. All sections start as 'pending_review'.
  3. get_draft_for_review(conn, report_id) returns the current state of
     all sections -- text, word count, status -- for dashboard display.
  4. update_section(conn, report_id, section_name, new_text) lets the
     Managing Partner replace a section's text. Validates 50-300 words.
     Resets status to 'pending_review' on any edit so the MP must
     re-approve after each change.
  5. approve_section(conn, report_id, section_name) marks one section
     as 'approved'. Logs the approval to audit_log.
  6. finalize_report(conn, report_id, base_context) checks that every
     section is 'approved'. Raises RuntimeError if any are still
     'pending_review'. On success, merges approved text back into the
     context dict and returns it ready for builder.py. Logs
     'report_finalized' to audit_log.

Public interface:
  SectionDraft            -- dataclass returned by get_draft_for_review
  save_draft(conn, report_id, context, report_type) -> None
  get_draft_for_review(conn, report_id) -> list[SectionDraft]
  update_section(conn, report_id, section_name, new_text) -> None
  approve_section(conn, report_id, section_name) -> None
  finalize_report(conn, report_id, base_context, report_type) -> dict
"""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from db.audit import write_audit_log
from reports.context import (
    MONTHLY_BRIEF_NARRATIVE_KEYS,
    SNAPSHOT_NARRATIVE_KEYS,
)
from reports.validator import (
    validate_monthly_brief_deep_dives,
    validate_narrative_lengths,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class _JsonFormatter(logging.Formatter):
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


log = _build_logger("cpoi.reports.editor")

# ---------------------------------------------------------------------------
# Section name sets per report type
# ---------------------------------------------------------------------------

_SNAPSHOT_SECTIONS: tuple[str, ...] = (
    "narrative_executive_summary",
    "narrative_key_findings",
    "narrative_recommended_focus",
)

_MONTHLY_BRIEF_TOP_SECTIONS: tuple[str, ...] = (
    "narrative_executive_summary",
    "narrative_forward_focus",
)

_DIMENSION_IDS: tuple[str, ...] = ("1", "2", "3", "4", "5")

# Prefix used for dimension deep-dive section names in report_drafts.
_DEEP_DIVE_PREFIX: str = "narrative_dim_deep_dive_"


def _section_names_for_report(
    report_type: str,
    context: dict[str, Any],
) -> list[str]:
    """
    Return the ordered list of section names that must be saved and
    approved for a report of the given type.

    For monthly briefs, dimension deep-dive sections are included only
    when their text is non-empty in the context dict (empty string means
    the dimension had no material movement and no draft was produced).

    Args:
        report_type: 'snapshot' or 'monthly_brief'.
        context:     The report context dict containing narrative values.

    Returns:
        List of section name strings.
    """
    if report_type == "snapshot":
        return list(_SNAPSHOT_SECTIONS)

    sections = list(_MONTHLY_BRIEF_TOP_SECTIONS)
    deep_dives: dict[str, str] = context.get("narrative_dimension_deep_dives", {})
    for dim_id in _DIMENSION_IDS:
        text = deep_dives.get(dim_id, "")
        if text:
            sections.append(f"{_DEEP_DIVE_PREFIX}{dim_id}")
    return sections


# ---------------------------------------------------------------------------
# SectionDraft dataclass
# ---------------------------------------------------------------------------


@dataclass
class SectionDraft:
    """
    The current state of one narrative section in the editing flow.

    Attributes:
        report_draft_id: UUID of the report_drafts row.
        report_id:       UUID of the parent report record.
        section_name:    Canonical section identifier, e.g.
                         'narrative_executive_summary'.
        display_name:    Human-readable label for dashboard display.
        draft_text:      Current text (AI-generated or MP-edited).
        word_count:      Word count of draft_text.
        status:          'pending_review' or 'approved'.
        created_at:      ISO datetime string when this draft was first saved.
        updated_at:      ISO datetime string of most recent update.
    """

    report_draft_id: str
    report_id: str
    section_name: str
    display_name: str
    draft_text: str
    word_count: int
    status: str
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Display name mapping
# ---------------------------------------------------------------------------

_DISPLAY_NAMES: dict[str, str] = {
    "narrative_executive_summary": "Executive Summary",
    "narrative_key_findings": "Key Findings",
    "narrative_recommended_focus": "Recommended Focus Areas",
    "narrative_forward_focus": "Forward Focus",
    f"{_DEEP_DIVE_PREFIX}1": "Dimension Analysis: Strategic Saturation",
    f"{_DEEP_DIVE_PREFIX}2": "Dimension Analysis: Governance Responsiveness",
    f"{_DEEP_DIVE_PREFIX}3": "Dimension Analysis: Execution Visibility",
    f"{_DEEP_DIVE_PREFIX}4": "Dimension Analysis: Reporting Integrity",
    f"{_DEEP_DIVE_PREFIX}5": "Dimension Analysis: Organizational Sustainability",
}


def _display_name(section_name: str) -> str:
    """Return a human-readable label for a section name."""
    return _DISPLAY_NAMES.get(
        section_name,
        section_name.replace("_", " ").title(),
    )


# ---------------------------------------------------------------------------
# Internal text extraction
# ---------------------------------------------------------------------------


def _get_section_text(context: dict[str, Any], section_name: str) -> str:
    """
    Extract the narrative text for a section from the context dict.

    For top-level sections the key maps directly. For dimension deep-dive
    sections the text lives inside narrative_dimension_deep_dives[dim_id].

    Args:
        context:      Report context dict.
        section_name: Section name as used in report_drafts.

    Returns:
        str: The narrative text for this section.

    Raises:
        KeyError: if the section is not found in context.
    """
    if section_name.startswith(_DEEP_DIVE_PREFIX):
        dim_id = section_name[len(_DEEP_DIVE_PREFIX):]
        deep_dives: dict[str, str] = context.get("narrative_dimension_deep_dives", {})
        if dim_id not in deep_dives:
            raise KeyError(
                f"Dimension deep-dive '{dim_id}' not found in "
                "narrative_dimension_deep_dives."
            )
        return deep_dives[dim_id]

    if section_name not in context:
        raise KeyError(
            f"Section '{section_name}' not found in context dict."
        )
    return context[section_name]


def _set_section_text(
    context: dict[str, Any], section_name: str, text: str
) -> dict[str, Any]:
    """
    Return a copy of context with the named section's text replaced.

    Args:
        context:      Report context dict.
        section_name: Section name as used in report_drafts.
        text:         Replacement text.

    Returns:
        dict: Updated context copy.
    """
    result = context.copy()

    if section_name.startswith(_DEEP_DIVE_PREFIX):
        dim_id = section_name[len(_DEEP_DIVE_PREFIX):]
        deep_dives = dict(result.get("narrative_dimension_deep_dives", {}))
        deep_dives[dim_id] = text
        result["narrative_dimension_deep_dives"] = deep_dives
    else:
        result[section_name] = text

    return result


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def save_draft(
    conn: Any,
    report_id: str,
    context: dict[str, Any],
    report_type: str,
) -> None:
    """
    Write one report_drafts row per narrative section for the given report.

    All sections are created with status 'pending_review'. If a row
    already exists for (report_id, section_name), the existing row is
    replaced so that re-running narrative generation updates the draft
    cleanly.

    Does not call conn.commit(). The caller owns the transaction.

    Args:
        conn:        Open, authenticated database connection.
        report_id:   UUID of the report record in the reports table.
        context:     Completed context dict from generate_narratives().
        report_type: 'snapshot' or 'monthly_brief'.

    Raises:
        ValueError: if report_type is not 'snapshot' or 'monthly_brief'.
        KeyError:   if a required section is missing from context.
        Exception:  any database error propagates to the caller.
    """
    if report_type not in ("snapshot", "monthly_brief"):
        raise ValueError(
            f"report_type must be 'snapshot' or 'monthly_brief', "
            f"got '{report_type}'."
        )

    sections = _section_names_for_report(report_type, context)
    now = datetime.now(timezone.utc).isoformat()

    for section_name in sections:
        text = _get_section_text(context, section_name)
        word_count = len(text.split())
        draft_id = str(uuid.uuid4())

        conn.execute(
            """
            INSERT INTO report_drafts
                (report_draft_id, report_id, section_name, draft_text,
                 word_count, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'pending_review', ?, ?)
            ON CONFLICT (report_id, section_name) DO UPDATE SET
                draft_text  = excluded.draft_text,
                word_count  = excluded.word_count,
                status      = 'pending_review',
                updated_at  = excluded.updated_at
            """,
            (draft_id, report_id, section_name, text, word_count, now, now),
        )

        log.info(
            "Draft section saved: report_id=%s section=%s words=%d",
            report_id,
            section_name,
            word_count,
        )

    write_audit_log(
        conn=conn,
        event_type="report_draft_saved",
        entity_type="report",
        entity_id=report_id,
        description=(
            f"Draft saved for report '{report_id}': "
            f"{len(sections)} section(s) written, all set to 'pending_review'."
        ),
        performed_by="system",
        metadata={
            "report_id":   report_id,
            "report_type": report_type,
            "sections":    sections,
        },
    )

    log.info(
        "Draft saved: report_id=%s report_type=%s sections=%d",
        report_id,
        report_type,
        len(sections),
    )


def get_draft_for_review(
    conn: Any,
    report_id: str,
) -> list[SectionDraft]:
    """
    Return the current draft state of all sections for a report.

    This is the data surfaced to the Managing Partner on the dashboard
    editing screen. Sections are returned in the order they were inserted,
    which matches the logical document order.

    Args:
        conn:      Open, authenticated database connection.
        report_id: UUID of the report record.

    Returns:
        List of SectionDraft objects, one per section. Empty list if no
        draft has been saved for this report_id.

    Raises:
        Exception: any database error propagates to the caller.
    """
    cursor = conn.execute(
        """
        SELECT report_draft_id, report_id, section_name, draft_text,
               word_count, status, created_at, updated_at
        FROM report_drafts
        WHERE report_id = ?
        ORDER BY created_at ASC
        """,
        (report_id,),
    )
    rows = cursor.fetchall()

    return [
        SectionDraft(
            report_draft_id=row["report_draft_id"],
            report_id=row["report_id"],
            section_name=row["section_name"],
            display_name=_display_name(row["section_name"]),
            draft_text=row["draft_text"],
            word_count=row["word_count"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        for row in rows
    ]


def update_section(
    conn: Any,
    report_id: str,
    section_name: str,
    new_text: str,
) -> None:
    """
    Replace the text of one draft section with Managing Partner edits.

    Validates that new_text is between 50 and 300 words. Resets the
    section's status to 'pending_review' so the Managing Partner must
    explicitly approve after each edit. Logs the edit to audit_log.

    Does not call conn.commit(). The caller owns the transaction.

    Args:
        conn:         Open, authenticated database connection.
        report_id:    UUID of the report record.
        section_name: The section to update (must exist in report_drafts
                      for this report_id).
        new_text:     The Managing Partner's edited text. Must be a plain
                      text string, 50-300 words.

    Raises:
        ValueError: if new_text fails word count validation, or if
                    section_name does not exist for this report.
        Exception:  any database error propagates to the caller.
    """
    if not isinstance(new_text, str) or not new_text.strip():
        raise ValueError(
            f"new_text for section '{section_name}' must be a non-empty string."
        )

    word_count = len(new_text.split())
    from reports.validator import MIN_WORDS, MAX_WORDS

    if word_count < MIN_WORDS:
        raise ValueError(
            f"Edited text for section '{section_name}' is too short: "
            f"{word_count} words (minimum {MIN_WORDS})."
        )
    if word_count > MAX_WORDS:
        raise ValueError(
            f"Edited text for section '{section_name}' is too long: "
            f"{word_count} words (maximum {MAX_WORDS})."
        )

    now = datetime.now(timezone.utc).isoformat()

    result = conn.execute(
        """
        UPDATE report_drafts
        SET draft_text  = ?,
            word_count  = ?,
            status      = 'pending_review',
            updated_at  = ?
        WHERE report_id = ?
          AND section_name = ?
        """,
        (new_text, word_count, now, report_id, section_name),
    )

    if result.rowcount == 0:
        raise ValueError(
            f"Section '{section_name}' not found for report_id '{report_id}'. "
            "Call save_draft() before updating sections."
        )

    write_audit_log(
        conn=conn,
        event_type="report_section_edited",
        entity_type="report",
        entity_id=report_id,
        description=(
            f"Section '{section_name}' edited by Managing Partner. "
            f"New word count: {word_count}. Status reset to 'pending_review'."
        ),
        performed_by="managing_partner",
        metadata={
            "report_id":    report_id,
            "section_name": section_name,
            "word_count":   word_count,
        },
    )

    log.info(
        "Section updated: report_id=%s section=%s words=%d status=pending_review",
        report_id,
        section_name,
        word_count,
    )


def approve_section(
    conn: Any,
    report_id: str,
    section_name: str,
) -> None:
    """
    Mark one draft section as approved by the Managing Partner.

    The section must exist in report_drafts for this report_id. Logs the
    approval to audit_log. Does not call conn.commit().

    Args:
        conn:         Open, authenticated database connection.
        report_id:    UUID of the report record.
        section_name: The section to approve.

    Raises:
        ValueError: if section_name does not exist for this report_id.
        Exception:  any database error propagates to the caller.
    """
    now = datetime.now(timezone.utc).isoformat()

    result = conn.execute(
        """
        UPDATE report_drafts
        SET status     = 'approved',
            updated_at = ?
        WHERE report_id    = ?
          AND section_name = ?
        """,
        (now, report_id, section_name),
    )

    if result.rowcount == 0:
        raise ValueError(
            f"Section '{section_name}' not found for report_id '{report_id}'. "
            "Call save_draft() before approving sections."
        )

    write_audit_log(
        conn=conn,
        event_type="report_section_approved",
        entity_type="report",
        entity_id=report_id,
        description=(
            f"Section '{section_name}' approved by Managing Partner."
        ),
        performed_by="managing_partner",
        metadata={
            "report_id":    report_id,
            "section_name": section_name,
        },
    )

    log.info(
        "Section approved: report_id=%s section=%s",
        report_id,
        section_name,
    )


def finalize_report(
    conn: Any,
    report_id: str,
    base_context: dict[str, Any],
    report_type: str,
) -> dict[str, Any]:
    """
    Finalize a report after all sections have been approved.

    Checks that every section saved for this report has status 'approved'.
    If any section is still 'pending_review', raises RuntimeError naming
    each unapproved section. This enforces the invariant that the AI never
    publishes directly and that the Managing Partner has reviewed every
    narrative before files are generated.

    On success, reads each approved section's text from report_drafts,
    merges it back into a copy of base_context, validates all narrative
    lengths, and returns the finalized context dict ready for builder.py.
    Logs 'report_finalized' to audit_log. Does not call conn.commit().

    Args:
        conn:         Open, authenticated database connection.
        report_id:    UUID of the report record.
        base_context: The report context dict (fixed fields + any prior
                      narrative values). Narrative values will be replaced
                      with the approved text from report_drafts.
        report_type:  'snapshot' or 'monthly_brief'.

    Returns:
        dict: A copy of base_context with all narrative fields replaced
              by Managing Partner-approved text from report_drafts.

    Raises:
        RuntimeError: if any section is not 'approved', naming each
                      unapproved section explicitly.
        ValueError:   if report_type is not recognized, or if any approved
                      text fails the final word count validation.
        Exception:    any database error propagates to the caller.
    """
    if report_type not in ("snapshot", "monthly_brief"):
        raise ValueError(
            f"report_type must be 'snapshot' or 'monthly_brief', "
            f"got '{report_type}'."
        )

    drafts = get_draft_for_review(conn, report_id)

    if not drafts:
        raise RuntimeError(
            f"No draft sections found for report_id '{report_id}'. "
            "Call save_draft() before finalizing."
        )

    unapproved = [d.section_name for d in drafts if d.status != "approved"]
    if unapproved:
        raise RuntimeError(
            f"Report '{report_id}' cannot be finalized: "
            f"{len(unapproved)} section(s) still require Managing Partner "
            f"approval: {', '.join(unapproved)}. "
            "Approve all sections before generating files."
        )

    # Merge approved text back into a context copy.
    result = base_context.copy()
    for draft in drafts:
        result = _set_section_text(result, draft.section_name, draft.draft_text)

    # Final validation pass before handing off to builder.
    narrative_keys = (
        SNAPSHOT_NARRATIVE_KEYS
        if report_type == "snapshot"
        else MONTHLY_BRIEF_NARRATIVE_KEYS
    )
    validate_narrative_lengths(result, narrative_keys)

    if report_type == "monthly_brief":
        validate_monthly_brief_deep_dives(
            result.get("narrative_dimension_deep_dives", {})
        )

    write_audit_log(
        conn=conn,
        event_type="report_finalized",
        entity_type="report",
        entity_id=report_id,
        description=(
            f"Report '{report_id}' finalized by Managing Partner. "
            f"{len(drafts)} section(s) approved. Ready for file generation."
        ),
        performed_by="managing_partner",
        metadata={
            "report_id":       report_id,
            "report_type":     report_type,
            "sections_count":  len(drafts),
            "section_names":   [d.section_name for d in drafts],
        },
    )

    log.info(
        "Report finalized: report_id=%s report_type=%s sections=%d",
        report_id,
        report_type,
        len(drafts),
    )

    return result
