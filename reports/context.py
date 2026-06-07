"""
reports/context.py
CPOI Platform — Report Template Context Types

Defines the TypedDict structures that every caller must pass to the
template renderer. These types are the contract between the database
query layer and the Jinja2 templates; any key that appears in a template
must be declared here, typed, and documented.

Two top-level context types:
  SnapshotReportContext   -- OEI Snapshot (12-section report)
  MonthlyBriefContext     -- OEIL Monthly Brief (8-10 section report)

Sentinel values:
  NARRATIVE_PLACEHOLDER prefix: When an AI narrative field has not yet
  been generated, its value must be set to the module-level constant
  NARRATIVE_UNFILLED (e.g. "NARRATIVE_PLACEHOLDER__executive_summary").
  The validator in reports/validator.py rejects any rendered document
  that still contains this prefix, ensuring no unfilled section reaches
  the file-generation step.
"""

from typing import TypedDict

# ---------------------------------------------------------------------------
# Sentinel prefix
# ---------------------------------------------------------------------------

NARRATIVE_PLACEHOLDER_PREFIX: str = "NARRATIVE_PLACEHOLDER__"


def make_narrative_placeholder(section_name: str) -> str:
    """
    Return the standard unfilled-narrative sentinel for a named section.

    Used by the generator to initialise narrative fields before AI
    generation runs. The validator rejects any document that still
    contains this prefix after AI generation.

    Args:
        section_name: Short identifier for the narrative section,
                      e.g. 'executive_summary', 'key_findings'.

    Returns:
        str: Sentinel string of the form 'NARRATIVE_PLACEHOLDER__<name>'.
    """
    return f"{NARRATIVE_PLACEHOLDER_PREFIX}{section_name}"


# ---------------------------------------------------------------------------
# Shared sub-structures
# ---------------------------------------------------------------------------


class SubCategoryContext(TypedDict):
    """
    One sub-category row within a dimension card.

    Attributes:
        name:           Display name, e.g. 'Priority Inflation Index'.
        impact:         Impact classification label.
                        One of: DRIVING, CONTRIBUTING, PRESENT,
                        MONITORED, EXCLUDED.
        interpretation: One-sentence plain-language interpretation.
                        Required when impact is DRIVING; empty string
                        otherwise.
        score:          Integer score 0-100.
        is_missing_data: True when the sub-category scored at the
                         missing-data default rather than from real data.
    """

    name: str
    impact: str
    interpretation: str
    score: int
    is_missing_data: bool


class DimensionContext(TypedDict):
    """
    One OEI dimension card.

    Attributes:
        dimension_id:    '1' through '5'.
        name:            Full dimension name,
                         e.g. 'Strategic Saturation'.
        score:           Integer score 0-100.
        prior_score:     Prior-period score, or None if no prior period.
        delta:           score minus prior_score (positive = worsened),
                         or None if no prior period.
        classification:  Risk band label (Low / Moderate / Elevated /
                         High Risk / Critical).
        description:     One-sentence description of what the dimension
                         measures.
        sub_categories:  Ordered list of SubCategoryContext, DRIVING
                         first.
    """

    dimension_id: str
    name: str
    score: int
    prior_score: "int | None"
    delta: "int | None"
    classification: str
    description: str
    sub_categories: list[SubCategoryContext]


class SignalReadingContext(TypedDict):
    """
    One row in the Signal Readings table.

    Attributes:
        signal_name:       Snake-case canonical name, e.g.
                           'governance_latency_index'.
        display_name:      Human-readable name for report display.
        signal_value:      Measured value (displayed to 4 decimal
                           places in the table).
        threshold_value:   Watch-level threshold.
        threshold_breached: True if the signal is in watch, elevated,
                            or critical state.
        severity:          'clear', 'watch', 'elevated', or 'critical'.
    """

    signal_name: str
    display_name: str
    signal_value: float
    threshold_value: float
    threshold_breached: bool
    severity: str


class AlertSummaryContext(TypedDict):
    """
    One row in the Active Alerts Summary table.

    Attributes:
        signal_name:    Canonical signal name.
        display_name:   Human-readable name.
        severity:       'critical', 'elevated', or 'watch'.
        alert_message:  The drafted alert message text.
        triggered_at:   ISO datetime string.
        acknowledged:   True if the Managing Partner has acknowledged.
    """

    signal_name: str
    display_name: str
    severity: str
    alert_message: str
    triggered_at: str
    acknowledged: bool


class JournalEntryContext(TypedDict):
    """
    One Engagement Intelligence Journal entry surfaced in the report.

    Only High and Medium materiality entries that the Managing Partner
    has marked Surfaced_in_Report = Yes or Partial are included.

    Attributes:
        entry_date:      ISO date string.
        entry_type:      Discovery Call / Review Session / Ad Hoc
                         Communication / Direct Observation /
                         Third-Party Disclosure / Pattern Note.
        program_reference: Initiative ID or 'General'.
        intelligence_note: The free-text note content. The Managing
                           Partner controls what appears here.
        materiality:     'High' or 'Medium'.
    """

    entry_date: str
    entry_type: str
    program_reference: str
    intelligence_note: str
    materiality: str


# ---------------------------------------------------------------------------
# OEI Snapshot context
# ---------------------------------------------------------------------------


class SnapshotReportContext(TypedDict):
    """
    Full context dict for the OEI Snapshot Jinja2 template.

    All narrative fields must contain either real generated text (50-300
    words validated by reports/validator.py) or the NARRATIVE_PLACEHOLDER__
    sentinel produced by make_narrative_placeholder(). The template
    renderer rejects the latter.

    Attributes:
        report_id:                  UUID string; also embedded as watermark.
        report_type:                Literal 'snapshot'.
        report_date:                ISO date string (YYYY-MM-DD).
        generated_at:               ISO datetime string (UTC).

        client_name:                Full client name as stored in clients.
        sponsor_name:               Executive sponsor name.
        engagement_type:            Literal 'snapshot'.
        engagement_start_date:      ISO date string.

        submission_id:              UUID of the intake_submission scored.
        reporting_period_start:     ISO date string.
        reporting_period_end:       ISO date string.
        submitted_at:               ISO datetime string.

        oei_composite_score:        Integer 0-100.
        composite_class:            Risk band label.
        composite_description:      Plain-language description of the
                                    composite band (from scoring spec
                                    Part 1 table).

        prior_composite_score:      Prior-period composite, or None.
        score_delta:                Current minus prior, or None.
        score_direction:            'improved', 'worsened', or 'unchanged',
                                    or None if no prior period.

        dimensions:                 List of five DimensionContext objects
                                    in dimension_id order 1-5.

        signal_readings:            List of SignalReadingContext for all
                                    signals calculated for this submission.

        has_override:               True if any Managing Partner override
                                    was applied to this score.
        override_footnote:          Footnote text required by spec Part 2
                                    when has_override is True. Empty string
                                    when False.

        narrative_executive_summary:  AI-generated or placeholder.
        narrative_key_findings:       AI-generated or placeholder.
        narrative_recommended_focus:  AI-generated or placeholder.

        qualitative_notes:          List of JournalEntryContext for entries
                                    the Managing Partner has elected to
                                    surface. May be empty.
    """

    report_id: str
    report_type: str
    report_date: str
    generated_at: str

    client_name: str
    sponsor_name: str
    engagement_type: str
    engagement_start_date: str

    submission_id: str
    reporting_period_start: str
    reporting_period_end: str
    submitted_at: str

    oei_composite_score: int
    composite_class: str
    composite_description: str

    prior_composite_score: "int | None"
    score_delta: "int | None"
    score_direction: "str | None"

    dimensions: list[DimensionContext]

    signal_readings: list[SignalReadingContext]

    has_override: bool
    override_footnote: str

    narrative_executive_summary: str
    narrative_key_findings: str
    narrative_recommended_focus: str

    qualitative_notes: list[JournalEntryContext]


# ---------------------------------------------------------------------------
# OEIL Monthly Brief context
# ---------------------------------------------------------------------------


class MonthlyBriefContext(TypedDict):
    """
    Full context dict for the OEIL Monthly Brief Jinja2 template.

    Adds period-over-period comparison fields, trajectory chart, signal
    movement table, active alerts, and per-dimension deep-dive narratives
    that are not present in the Snapshot.

    Attributes (additions beyond SnapshotReportContext):
        report_type:               Literal 'monthly_brief'.
        period_label:              Human-readable period label,
                                   e.g. 'May 2026'.
        prior_period_label:        e.g. 'April 2026', or None.

        trajectory_chart_b64:      Base64-encoded PNG of the score
                                   trajectory chart produced by
                                   reports/chart.py. Empty string when
                                   no prior periods exist.

        breached_signals:          Signals currently in breach (list of
                                   SignalReadingContext, severity != clear).
        cleared_signals:           Signals that breached last period but
                                   are clear this period (same type).

        active_alerts:             List of AlertSummaryContext for all
                                   unacknowledged alerts for this client.

        narrative_executive_summary:   AI-generated, governed 3-paragraph
                                       structure from SDLC spec Part 5.
        narrative_dimension_deep_dives: Dict keyed by dimension_id ('1'-'5')
                                        containing AI-generated deep-dive
                                        text for each dimension with a
                                        non-zero delta or active breach.
                                        Dimensions with no material movement
                                        have an empty string value.
        narrative_forward_focus:       AI-generated or placeholder.

        qualitative_notes:         Same as Snapshot — High and Medium
                                   materiality journal entries surfaced
                                   by the Managing Partner.
    """

    report_id: str
    report_type: str
    report_date: str
    generated_at: str

    client_name: str
    sponsor_name: str
    engagement_type: str
    engagement_start_date: str
    period_label: str
    prior_period_label: "str | None"

    submission_id: str
    reporting_period_start: str
    reporting_period_end: str
    submitted_at: str

    oei_composite_score: int
    composite_class: str
    composite_description: str

    prior_composite_score: "int | None"
    score_delta: "int | None"
    score_direction: "str | None"

    dimensions: list[DimensionContext]

    trajectory_chart_b64: str

    signal_readings: list[SignalReadingContext]
    breached_signals: list[SignalReadingContext]
    cleared_signals: list[SignalReadingContext]

    active_alerts: list[AlertSummaryContext]

    has_override: bool
    override_footnote: str

    narrative_executive_summary: str
    narrative_dimension_deep_dives: dict[str, str]
    narrative_forward_focus: str

    qualitative_notes: list[JournalEntryContext]


# ---------------------------------------------------------------------------
# Required narrative keys per report type
# ---------------------------------------------------------------------------

SNAPSHOT_NARRATIVE_KEYS: tuple[str, ...] = (
    "narrative_executive_summary",
    "narrative_key_findings",
    "narrative_recommended_focus",
)

MONTHLY_BRIEF_NARRATIVE_KEYS: tuple[str, ...] = (
    "narrative_executive_summary",
    "narrative_forward_focus",
)
