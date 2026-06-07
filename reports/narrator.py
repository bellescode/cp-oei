"""
reports/narrator.py
CPOI Platform -- Report Narrative Generation

Generates all AI narrative sections for OEI Snapshot and OEIL Monthly
Brief reports using the locally self-hosted ollama model. Client data
never leaves the server: all context is anonymized before any prompt
is built, regardless of model availability.

Public interface:
  generate_narratives(
      conn, report_type, submission_id, client_id, base_context
  ) -> dict

Privacy invariant:
  anonymize_for_ai() (imported from alerts/narrator.py) is called on
  every context dict before any prompt is built. The real client_name
  is replaced with CLIENT_A. Program names and owner names are replaced
  with indexed placeholders. Audit metadata stores the real client_name
  for traceability, but the model never sees it.

Prompt governance:
  The Monthly Brief executive summary prompt is taken verbatim from
  cpoi-sdlc-spec.md Part 5. It must not be modified without a version
  increment and an audit_log entry. The prompt template string is defined
  as a module-level constant and marked with a version comment.

Fallback behavior:
  If ollama is unreachable, times out, or returns text outside the
  50-300 word validation window, a deterministic fallback narrative is
  substituted. The fallback is constructed from signal and score data
  only. No exception propagates to the caller. Every fallback usage is
  flagged in the audit log.

Audit logging:
  One audit_log entry per narrative section drafted, with event_type
  'report_narrative_drafted'. Records: section name, prompt hash
  (SHA-256, never the prompt text itself), model name, fallback flag,
  word count, and report_id. Satisfies spec Part 8 AI generation logging
  requirement.
"""

import hashlib
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from alerts.narrator import anonymize_for_ai
from db.audit import write_audit_log
from reports.context import (
    MONTHLY_BRIEF_NARRATIVE_KEYS,
    NARRATIVE_PLACEHOLDER_PREFIX,
    SNAPSHOT_NARRATIVE_KEYS,
    make_narrative_placeholder,
)
from reports.validator import (
    MAX_WORDS,
    MIN_WORDS,
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


log = _build_logger("cpoi.reports.narrator")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_OLLAMA_MODEL: str = "llama3.2:3b"
_DEFAULT_OLLAMA_URL: str = "http://localhost:11434/api/generate"
_OLLAMA_TIMEOUT_SECONDS: int = 120
# num_predict: generous upper bound; word validator is the true gate.
_NUM_PREDICT: int = 700

# ---------------------------------------------------------------------------
# Governed prompt templates (version-locked -- see governance note above)
# Prompt version: 1.0  Source: cpoi-sdlc-spec.md Part 5
# ---------------------------------------------------------------------------

# Monthly Brief Executive Summary -- SDLC spec Part 5, verbatim structure.
# Variable substitution uses .format() after anonymization is applied.
_MONTHLY_BRIEF_EXEC_SUMMARY_PROMPT_V1 = (
    "SYSTEM: You are the intelligence synthesis layer for Criterion Partners, a boutique "
    "Operational Executive Intelligence firm. Your output will be delivered directly "
    "to C-suite executives. Write with precision. No hedging. No filler. Every sentence "
    "must be specific, grounded in the data provided, and written as if the Managing "
    "Partner is speaking directly to the executive sponsor.\n\n"
    "DATA INPUT:\n"
    "- Client: {client_name}\n"
    "- Reporting Period: {period}\n"
    "- OEI Composite: {composite_score} ({composite_class})\n"
    "- Dimension Scores: {dimension_json}\n"
    "- Signals Breached: {breached_signals}\n"
    "- Prior Month Composite: {prior_composite}\n"
    "- Movement: {score_delta} points ({direction})\n\n"
    "QUALITATIVE CONTEXT:\n"
    "{qualitative_context}\n\n"
    "TASK: Write the Executive Summary narrative section for the monthly "
    "OEI Intelligence Brief.\n"
    "- 3 paragraphs maximum\n"
    "- Paragraph 1: What the composite score movement indicates about the "
    "organization's current operational intelligence posture\n"
    "- Paragraph 2: The single most material finding from this period and "
    "its specific business implication\n"
    "- Paragraph 3: The one governance action most likely to produce "
    "measurable signal improvement in the next 30 days\n"
    "- Do not use em dashes\n"
    "- Do not hedge\n"
    "- Do not list more than one recommendation\n"
    "- Tone: direct, measured, authoritative\n"
    "- Output plain text paragraphs only. No headers, no bullet points."
)

_SNAPSHOT_EXEC_SUMMARY_PROMPT_V1 = (
    "SYSTEM: You are the intelligence synthesis layer for Criterion Partners, a boutique "
    "Operational Executive Intelligence firm. Your output will be delivered directly "
    "to C-suite executives. Write with precision. No hedging. No filler. Every sentence "
    "must be specific, grounded in the data provided.\n\n"
    "DATA INPUT:\n"
    "- Client: {client_name}\n"
    "- Reporting Period: {period}\n"
    "- OEI Composite: {composite_score} ({composite_class})\n"
    "- Dimension Scores: {dimension_json}\n"
    "- Signals Breached: {breached_signals}\n\n"
    "QUALITATIVE CONTEXT:\n"
    "{qualitative_context}\n\n"
    "TASK: Write the Executive Summary for this OEI Snapshot Report. "
    "3 paragraphs maximum. "
    "Paragraph 1: The overall operational intelligence posture indicated by the composite score. "
    "Paragraph 2: The single most significant finding across all five dimensions. "
    "Paragraph 3: The primary risk if no intervention is taken in the next 60 days. "
    "Do not use em dashes. Do not hedge. Tone: direct, measured, authoritative. "
    "Output plain text paragraphs only."
)

_SNAPSHOT_KEY_FINDINGS_PROMPT_V1 = (
    "SYSTEM: You are the intelligence synthesis layer for Criterion Partners.\n\n"
    "DATA INPUT:\n"
    "- Client: {client_name}\n"
    "- OEI Composite: {composite_score} ({composite_class})\n"
    "- Dimension Scores: {dimension_json}\n"
    "- Signals Breached: {breached_signals}\n\n"
    "QUALITATIVE CONTEXT:\n"
    "{qualitative_context}\n\n"
    "TASK: Write the Key Findings section. "
    "One paragraph per dimension that scored Elevated, High Risk, or Critical. "
    "Each paragraph names the dimension, states what the score indicates structurally, "
    "and identifies the specific sub-category that is the primary driver. "
    "Maximum 3 paragraphs total. "
    "Do not use em dashes. Do not hedge. "
    "Output plain text paragraphs only."
)

_SNAPSHOT_RECOMMENDED_FOCUS_PROMPT_V1 = (
    "SYSTEM: You are the intelligence synthesis layer for Criterion Partners.\n\n"
    "DATA INPUT:\n"
    "- Client: {client_name}\n"
    "- OEI Composite: {composite_score} ({composite_class})\n"
    "- Dimension Scores: {dimension_json}\n"
    "- Signals Breached: {breached_signals}\n\n"
    "TASK: Write the Recommended Focus Areas section. "
    "Identify the two to three highest-leverage governance or operational actions "
    "for the next 90 days, based on the dimension scores and breached signals. "
    "Each recommendation must be specific and actionable, not generic. "
    "Do not use em dashes. Do not hedge. Maximum 3 paragraphs. "
    "Output plain text paragraphs only."
)

_DIMENSION_DEEP_DIVE_PROMPT_V1 = (
    "SYSTEM: You are the intelligence synthesis layer for Criterion Partners.\n\n"
    "DATA INPUT:\n"
    "- Client: {client_name}\n"
    "- Dimension: {dimension_name}\n"
    "- Current Score: {score} ({classification})\n"
    "- Prior Score: {prior_score}\n"
    "- Movement: {delta} points ({direction})\n"
    "- Sub-Category Impacts: {sub_categories}\n"
    "- Relevant Signals: {signals}\n\n"
    "TASK: Write a dimension analysis paragraph for the Monthly Intelligence Brief. "
    "2 paragraphs maximum. "
    "Paragraph 1: What this dimension's score and movement indicate about the "
    "organization's operational posture in this specific area. "
    "Paragraph 2: The single structural factor most responsible for this result, "
    "and what a measurable improvement would look like in 30 days. "
    "Do not use em dashes. Do not hedge. Tone: direct, measured, authoritative. "
    "Output plain text paragraphs only."
)

_FORWARD_FOCUS_PROMPT_V1 = (
    "SYSTEM: You are the intelligence synthesis layer for Criterion Partners.\n\n"
    "DATA INPUT:\n"
    "- Client: {client_name}\n"
    "- OEI Composite: {composite_score} ({composite_class})\n"
    "- Dimension Scores: {dimension_json}\n"
    "- Signals Breached: {breached_signals}\n"
    "- Score Movement: {score_delta} points ({direction})\n\n"
    "TASK: Write the Forward Focus section for the Monthly Intelligence Brief. "
    "2 paragraphs maximum. "
    "Paragraph 1: The strategic posture heading into the next reporting period, "
    "based on the current composite trajectory. "
    "Paragraph 2: The single highest-priority governance action and the specific "
    "signal it will most directly improve if executed. "
    "Do not use em dashes. Do not hedge. Maximum 2 paragraphs. "
    "Output plain text paragraphs only."
)


# ---------------------------------------------------------------------------
# Database query helpers
# ---------------------------------------------------------------------------


def _fetch_oei_scores(conn: Any, submission_id: str) -> dict[str, Any]:
    """
    Fetch the OEI score record for a submission.

    Args:
        conn:          Open database connection.
        submission_id: UUID of the intake_submission.

    Returns:
        dict with all oei_scores columns. Empty dict if not found.
    """
    cursor = conn.execute(
        """
        SELECT score_id, client_id, submission_id, period_date,
               strategic_saturation_score, governance_responsiveness_score,
               execution_visibility_score, reporting_integrity_score,
               org_sustainability_score, oei_composite_score,
               strategic_saturation_class, governance_responsiveness_class,
               execution_visibility_class, reporting_integrity_class,
               org_sustainability_class, composite_class, calculated_at
        FROM oei_scores
        WHERE submission_id = ?
        ORDER BY calculated_at DESC
        LIMIT 1
        """,
        (submission_id,),
    )
    row = cursor.fetchone()
    return dict(row) if row else {}


def _fetch_signal_readings(
    conn: Any, submission_id: str
) -> list[dict[str, Any]]:
    """
    Fetch all signal readings for a submission.

    Args:
        conn:          Open database connection.
        submission_id: UUID of the intake_submission.

    Returns:
        List of dicts, one per signal reading row.
    """
    cursor = conn.execute(
        """
        SELECT reading_id, signal_name, signal_value, threshold_value,
               threshold_breached, period_date
        FROM signal_readings
        WHERE submission_id = ?
        ORDER BY signal_name
        """,
        (submission_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def _fetch_prior_score(
    conn: Any, client_id: str, current_period_date: str
) -> dict[str, Any]:
    """
    Fetch the most recent oei_scores record before the current period.

    Args:
        conn:                Open database connection.
        client_id:           UUID of the client.
        current_period_date: ISO date string of the current period.

    Returns:
        dict with oei_composite_score and dimension scores, or empty dict
        if no prior period exists.
    """
    cursor = conn.execute(
        """
        SELECT oei_composite_score,
               strategic_saturation_score, governance_responsiveness_score,
               execution_visibility_score, reporting_integrity_score,
               org_sustainability_score
        FROM oei_scores
        WHERE client_id = ?
          AND period_date < ?
        ORDER BY period_date DESC
        LIMIT 1
        """,
        (client_id, current_period_date),
    )
    row = cursor.fetchone()
    return dict(row) if row else {}


def _fetch_journal_entries(
    conn: Any, client_id: str, submission_id: str
) -> list[dict[str, Any]]:
    """
    Fetch High and Medium materiality journal entries that the Managing
    Partner has marked for surfacing (Yes or Partial).

    Entries are linked either directly to this submission or to the
    client without a specific submission reference.

    Args:
        conn:          Open database connection.
        client_id:     UUID of the client.
        submission_id: UUID of the current submission.

    Returns:
        List of dicts with entry_date, entry_type, program_reference,
        intelligence_note, materiality.
    """
    cursor = conn.execute(
        """
        SELECT entry_date, entry_type, program_reference,
               intelligence_note, materiality
        FROM engagement_journal
        WHERE client_id = ?
          AND materiality IN ('High', 'Medium')
          AND surfaced_in_report IN ('Yes', 'Partial')
          AND (submission_id = ? OR submission_id IS NULL)
        ORDER BY materiality DESC, entry_date DESC
        """,
        (client_id, submission_id),
    )
    return [dict(row) for row in cursor.fetchall()]


# ---------------------------------------------------------------------------
# Anonymization helpers for narrative context
# ---------------------------------------------------------------------------


def _build_anon_narrative_context(
    scores: dict[str, Any],
    signals: list[dict[str, Any]],
    prior_scores: dict[str, Any],
    journal_entries: list[dict[str, Any]],
    period_label: str,
) -> dict[str, Any]:
    """
    Build and anonymize the context dict passed to prompt builders.

    The real client_name is replaced by CLIENT_A via anonymize_for_ai().
    Dimension names, signal names, and numeric values are retained --
    they are operational metrics, not client identifiers.

    Args:
        scores:          OEI scores dict from _fetch_oei_scores().
        signals:         Signal readings list from _fetch_signal_readings().
        prior_scores:    Prior period scores dict or empty dict.
        journal_entries: Surfaced journal entries from _fetch_journal_entries().
        period_label:    Human-readable period label, e.g. 'May 2026'.

    Returns:
        Anonymized dict ready for prompt building.
    """
    dimension_map = {
        "1": ("Strategic Saturation", "strategic_saturation_score", "strategic_saturation_class"),
        "2": ("Governance Responsiveness", "governance_responsiveness_score", "governance_responsiveness_class"),
        "3": ("Execution Visibility", "execution_visibility_score", "execution_visibility_class"),
        "4": ("Reporting Integrity", "reporting_integrity_score", "reporting_integrity_class"),
        "5": ("Organizational Sustainability", "org_sustainability_score", "org_sustainability_class"),
    }

    dimension_json = {}
    for dim_id, (dim_name, score_col, class_col) in dimension_map.items():
        dimension_json[dim_name] = {
            "score": scores.get(score_col),
            "classification": scores.get(class_col),
            "prior_score": prior_scores.get(score_col),
        }

    breached = [
        {"signal": s["signal_name"], "value": s["signal_value"], "threshold": s["threshold_value"]}
        for s in signals
        if s.get("threshold_breached")
    ]

    qualitative_parts = []
    for entry in journal_entries:
        qualitative_parts.append(
            f"[{entry['materiality']} | {entry['entry_type']} | {entry['entry_date']}] "
            f"{entry['intelligence_note']}"
        )
    qualitative_context = "\n".join(qualitative_parts) if qualitative_parts else "No qualitative notes for this period."

    prior_composite = prior_scores.get("oei_composite_score")
    composite = scores.get("oei_composite_score", 0)
    if prior_composite is not None:
        delta = composite - prior_composite
        direction = "worsened" if delta > 0 else ("improved" if delta < 0 else "unchanged")
    else:
        delta = 0
        direction = "no prior period"

    raw = {
        "client_name": "REAL_CLIENT_NAME",  # replaced below
        "period": period_label,
        "composite_score": composite,
        "composite_class": scores.get("composite_class", ""),
        "dimension_json": json.dumps(dimension_json, indent=None),
        "breached_signals": json.dumps(breached, indent=None),
        "prior_composite": str(prior_composite) if prior_composite is not None else "N/A",
        "score_delta": str(abs(delta)),
        "direction": direction,
        "qualitative_context": qualitative_context,
    }

    anonymized = anonymize_for_ai(raw)
    return anonymized


# ---------------------------------------------------------------------------
# ollama call
# ---------------------------------------------------------------------------


def _call_ollama(prompt: str) -> str | None:
    """
    Call the ollama HTTP API and return the generated text, or None on any
    failure. Uses non-streaming generate endpoint.

    Returns None on: connection refused, HTTP error, JSON decode error,
    empty response, response shorter than MIN_WORDS * 4 chars, or any
    unexpected exception. Errors are logged at WARNING; no exception
    propagates to the caller.

    Args:
        prompt: Complete prompt string.

    Returns:
        str | None: Model response text if usable, None otherwise.
    """
    model = os.environ.get("CPOI_OLLAMA_MODEL", _DEFAULT_OLLAMA_MODEL)
    url = os.environ.get("CPOI_OLLAMA_URL", _DEFAULT_OLLAMA_URL)

    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.4,
            "num_predict": _NUM_PREDICT,
        },
    }).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=_OLLAMA_TIMEOUT_SECONDS) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.URLError as exc:
        log.warning("ollama unreachable at '%s': %s. Using fallback.", url, exc)
        return None
    except TimeoutError:
        log.warning(
            "ollama timed out after %ds. Using fallback.", _OLLAMA_TIMEOUT_SECONDS
        )
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning("Unexpected error calling ollama: %s. Using fallback.", exc)
        return None

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        log.warning("ollama response is not valid JSON: %s. Using fallback.", exc)
        return None

    text = data.get("response", "").strip()
    min_chars = MIN_WORDS * 4
    if len(text) < min_chars:
        log.warning(
            "ollama response too short (%d chars, need >%d). Using fallback.",
            len(text),
            min_chars,
        )
        return None

    return text


# ---------------------------------------------------------------------------
# Word-count enforcement on raw model output
# ---------------------------------------------------------------------------


def _enforce_word_count(text: str, section_name: str) -> str | None:
    """
    Return text if it is within the 50-300 word window, None otherwise.

    Logs a warning explaining which bound was violated. The caller falls
    back to the deterministic template when this returns None.

    Args:
        text:         Candidate narrative text.
        section_name: Section identifier for log messages.

    Returns:
        str if 50 <= word_count <= 300, None otherwise.
    """
    word_count = len(text.split())
    if word_count < MIN_WORDS:
        log.warning(
            "Model output for section '%s' too short: %d words (min %d). Using fallback.",
            section_name,
            word_count,
            MIN_WORDS,
        )
        return None
    if word_count > MAX_WORDS:
        log.warning(
            "Model output for section '%s' too long: %d words (max %d). Truncating to %d.",
            section_name,
            word_count,
            MAX_WORDS,
            MAX_WORDS,
        )
        return " ".join(text.split()[:MAX_WORDS])
    return text


# ---------------------------------------------------------------------------
# Fallback narrative builders (deterministic, no model required)
# ---------------------------------------------------------------------------


def _fallback_executive_summary(anon_ctx: dict[str, Any], report_type: str) -> str:
    """
    Return a deterministic executive summary when the local model is
    unavailable.

    Constructed entirely from the anonymized context dict. Guaranteed to
    be between MIN_WORDS and MAX_WORDS in length.

    Args:
        anon_ctx:    Anonymized context dict from _build_anon_narrative_context().
        report_type: 'snapshot' or 'monthly_brief'.

    Returns:
        str: A 3-paragraph plain-text narrative, 60-120 words.
    """
    composite = anon_ctx.get("composite_score", "N/A")
    comp_class = anon_ctx.get("composite_class", "")
    period = anon_ctx.get("period", "this period")
    delta = anon_ctx.get("score_delta", "0")
    direction = anon_ctx.get("direction", "unchanged")
    breached = anon_ctx.get("breached_signals", "[]")

    try:
        breached_list = json.loads(breached) if isinstance(breached, str) else breached
        breach_count = len(breached_list)
    except (json.JSONDecodeError, TypeError):
        breach_count = 0

    if report_type == "monthly_brief":
        p1 = (
            f"The OEI Composite Score for {period} stands at {composite}, "
            f"classified as {comp_class}. "
            f"The composite has {direction} by {delta} points relative to the prior period, "
            f"indicating a shift in the organization's operational intelligence posture "
            f"that warrants the attention of executive leadership."
        )
    else:
        p1 = (
            f"The OEI Composite Score for the assessment period stands at {composite}, "
            f"classified as {comp_class}. "
            f"This score reflects the organization's current operational intelligence posture "
            f"across five dimensions: Strategic Saturation, Governance Responsiveness, "
            f"Execution Visibility, Reporting Integrity, and Organizational Sustainability."
        )

    p2 = (
        f"Across the twelve operational signals evaluated, {breach_count} signal"
        f"{'s are' if breach_count != 1 else ' is'} currently in breach of watch-level thresholds. "
        f"The dimension scores indicate that the organization's most material structural "
        f"gaps are concentrated in the areas contributing most heavily to the composite reading. "
        f"These conditions represent measurable operational risk that is not self-correcting "
        f"without targeted governance intervention."
    )

    p3 = (
        f"The governance action most likely to produce measurable improvement within "
        f"the next reporting period is a structured review of the primary driving "
        f"sub-categories identified in the dimension analysis. "
        f"Establishing a defined accountability structure for each flagged condition "
        f"will create the baseline visibility required for signal improvement to register "
        f"in subsequent submissions."
    )

    return f"{p1}\n\n{p2}\n\n{p3}"


def _fallback_key_findings(anon_ctx: dict[str, Any]) -> str:
    """
    Return a deterministic key findings narrative when the local model is
    unavailable.

    Args:
        anon_ctx: Anonymized context dict.

    Returns:
        str: Plain-text narrative, 50-200 words.
    """
    composite = anon_ctx.get("composite_score", "N/A")
    comp_class = anon_ctx.get("composite_class", "")

    dim_json_str = anon_ctx.get("dimension_json", "{}")
    try:
        dim_data = json.loads(dim_json_str) if isinstance(dim_json_str, str) else dim_json_str
    except (json.JSONDecodeError, TypeError):
        dim_data = {}

    elevated = [
        name for name, vals in dim_data.items()
        if vals.get("classification") in ("Elevated", "High Risk", "Critical")
    ]

    if elevated:
        dims_text = ", ".join(elevated)
        p1 = (
            f"The OEI assessment identifies elevated risk conditions in the following "
            f"dimensions: {dims_text}. "
            f"These dimensions are the primary contributors to the composite score of "
            f"{composite} ({comp_class}) and represent the areas of most material "
            f"structural concern for the current engagement period."
        )
    else:
        p1 = (
            f"The OEI assessment indicates a composite score of {composite} ({comp_class}). "
            f"No dimensions are currently in the Elevated or above classification band, "
            f"though monitoring of developing signals is warranted to prevent score deterioration "
            f"in subsequent periods."
        )

    p2 = (
        f"Sub-category impact indicators across all five dimensions have been evaluated "
        f"against submitted operational telemetry. The DRIVING sub-categories identified "
        f"in the dimension cards represent the specific operational conditions producing "
        f"each dimension's current reading. These are the levers through which measurable "
        f"improvement can be achieved."
    )

    return f"{p1}\n\n{p2}"


def _fallback_recommended_focus(anon_ctx: dict[str, Any]) -> str:
    """
    Return a deterministic recommended focus narrative when the local model
    is unavailable.

    Args:
        anon_ctx: Anonymized context dict.

    Returns:
        str: Plain-text narrative, 50-200 words.
    """
    breached = anon_ctx.get("breached_signals", "[]")
    try:
        breached_list = json.loads(breached) if isinstance(breached, str) else breached
        breach_count = len(breached_list)
    except (json.JSONDecodeError, TypeError):
        breach_count = 0

    p1 = (
        f"The primary focus area for the next 90 days should be the governance "
        f"structures governing the DRIVING sub-categories in the highest-scoring dimensions. "
        f"Establishing named accountability for each breach condition and a defined "
        f"resolution timeline will produce the most direct signal improvement "
        f"in the next submission cycle."
    )

    p2 = (
        f"With {breach_count} signal{'s' if breach_count != 1 else ''} currently in breach, "
        f"the organization should prioritize the elimination of ambiguity in escalation "
        f"pathways and reporting structures. "
        f"Specifically, each breached signal should have a designated owner, a documented "
        f"improvement target, and a checkpoint scheduled before the next data submission date. "
        f"This structural accountability is the fastest route to composite score improvement."
    )

    return f"{p1}\n\n{p2}"


def _fallback_dimension_deep_dive(
    dimension_name: str,
    score: int,
    classification: str,
    delta: int | None,
) -> str:
    """
    Return a deterministic dimension deep-dive narrative when the local
    model is unavailable.

    Args:
        dimension_name: Full dimension name.
        score:          Current integer score.
        classification: Risk band label.
        delta:          Score movement from prior period, or None.

    Returns:
        str: Plain-text narrative, 50-150 words.
    """
    if delta is not None and delta != 0:
        direction = "worsened" if delta > 0 else "improved"
        movement_text = (
            f"The score has {direction} by {abs(delta)} points relative to the prior period, "
            f"indicating a measurable shift in this dimension's risk profile."
        )
    else:
        movement_text = (
            "The score is unchanged from the prior period, "
            "indicating persistent structural conditions in this dimension."
        )

    p1 = (
        f"The {dimension_name} dimension is currently scoring {score}, "
        f"classified as {classification}. "
        f"{movement_text} "
        f"This reading reflects the aggregated impact of the sub-category "
        f"conditions identified in the OEI Scorecard."
    )

    p2 = (
        f"The sub-category classified as DRIVING in this dimension is the primary "
        f"structural factor producing this result. "
        f"A measurable improvement in this dimension within 30 days is achievable "
        f"through direct intervention on the DRIVING condition, with a defined "
        f"owner and documented resolution pathway established before the next review."
    )

    return f"{p1}\n\n{p2}"


def _fallback_forward_focus(anon_ctx: dict[str, Any]) -> str:
    """
    Return a deterministic forward focus narrative when the local model is
    unavailable.

    Args:
        anon_ctx: Anonymized context dict.

    Returns:
        str: Plain-text narrative, 50-150 words.
    """
    composite = anon_ctx.get("composite_score", "N/A")
    comp_class = anon_ctx.get("composite_class", "")
    direction = anon_ctx.get("direction", "unchanged")
    delta = anon_ctx.get("score_delta", "0")

    p1 = (
        f"Heading into the next reporting period, the organization's OEI trajectory "
        f"reflects a composite score of {composite} ({comp_class}), "
        f"having {direction} by {delta} points this period. "
        f"Sustaining or improving this trajectory requires that the governance "
        f"conditions driving the current score receive structured attention "
        f"before the next submission cycle."
    )

    p2 = (
        f"The single highest-priority action is the resolution of the DRIVING "
        f"condition in the dimension carrying the heaviest composite contribution. "
        f"If addressed with a named owner and a defined timeline within the next "
        f"30 days, this action will produce a measurable reduction in the associated "
        f"signal breach and a corresponding improvement in the next period's "
        f"composite score."
    )

    return f"{p1}\n\n{p2}"


# ---------------------------------------------------------------------------
# Prompt-hash helper
# ---------------------------------------------------------------------------


def _hash_prompt(prompt: str) -> str:
    """Return the SHA-256 hex digest of the prompt string."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Single-section draft orchestrator
# ---------------------------------------------------------------------------


def _draft_section(
    conn: Any,
    section_name: str,
    prompt: str,
    fallback_fn: Any,
    report_id: str,
    client_name: str,
) -> tuple[str, bool]:
    """
    Draft one narrative section: call ollama, enforce word count, fall back
    if necessary, write one audit log entry.

    Args:
        conn:         Open database connection.
        section_name: Identifier used in audit log and log messages.
        prompt:       Complete prompt string (already anonymized).
        fallback_fn:  Zero-argument callable that returns fallback text.
        report_id:    UUID of the report record (for audit log entity_id).
        client_name:  Real client name stored only in audit metadata.

    Returns:
        (text, used_fallback): The drafted narrative and a flag indicating
        whether the fallback template was used.
    """
    model_name = os.environ.get("CPOI_OLLAMA_MODEL", _DEFAULT_OLLAMA_MODEL)
    prompt_hash = _hash_prompt(prompt)
    used_fallback = False

    log.info(
        "Drafting section '%s': report_id=%s model=%s prompt_hash=%s",
        section_name,
        report_id,
        model_name,
        prompt_hash[:16],
    )

    ai_response = _call_ollama(prompt)

    if ai_response is not None:
        validated = _enforce_word_count(ai_response, section_name)
    else:
        validated = None

    if validated is not None:
        text = validated
        used_fallback = False
        log.info(
            "Section '%s' drafted by model: words=%d report_id=%s",
            section_name,
            len(text.split()),
            report_id,
        )
    else:
        text = fallback_fn()
        used_fallback = True
        log.info(
            "Section '%s' drafted from fallback: words=%d report_id=%s",
            section_name,
            len(text.split()),
            report_id,
        )

    write_audit_log(
        conn=conn,
        event_type="report_narrative_drafted",
        entity_type="report",
        entity_id=report_id,
        description=(
            f"Narrative section '{section_name}' drafted. "
            f"Model: {model_name}. "
            f"Fallback used: {used_fallback}. "
            f"Word count: {len(text.split())}."
        ),
        performed_by="system",
        metadata={
            "report_id":        report_id,
            "section_name":     section_name,
            "model_name":       model_name,
            "prompt_hash":      prompt_hash,
            "used_fallback":    used_fallback,
            "word_count":       len(text.split()),
            "client_name_logged": client_name,
        },
    )

    return text, used_fallback


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def generate_narratives(
    conn: Any,
    report_type: str,
    submission_id: str,
    client_id: str,
    base_context: dict[str, Any],
    report_id: str,
) -> dict[str, Any]:
    """
    Generate all AI narrative sections for a report and return the
    completed context dict.

    Pulls OEI scores, signal readings, and journal entries from the
    database. Anonymizes the context. Builds prompts. Calls the local
    ollama model. Falls back to deterministic templates on any failure.
    Validates each section's word count (50-300). Writes one audit_log
    entry per section.

    The caller must commit the database transaction after this function
    returns. This function does not call conn.commit().

    Args:
        conn:         Open, authenticated database connection.
        report_type:  'snapshot' or 'monthly_brief'.
        submission_id: UUID of the intake_submission being reported on.
        client_id:    UUID of the client (for journal entry lookup).
        base_context: Partially-built context dict with all fixed fields
                      populated and narrative fields set to
                      NARRATIVE_PLACEHOLDER__ sentinels via
                      make_narrative_placeholder().
        report_id:    UUID of the report record (used in audit log entries
                      and log messages).

    Returns:
        dict: A copy of base_context with all NARRATIVE_PLACEHOLDER__
              sentinels replaced by validated narrative text. The original
              base_context is not modified.

    Raises:
        ValueError: if report_type is not 'snapshot' or 'monthly_brief'.
        KeyError:   if required database records are missing.
        Exception:  any database error from write_audit_log propagates.
                    Model errors are always handled internally.
    """
    if report_type not in ("snapshot", "monthly_brief"):
        raise ValueError(
            f"report_type must be 'snapshot' or 'monthly_brief', got '{report_type}'."
        )

    client_name: str = base_context.get("client_name", "UNKNOWN")
    period_label: str = base_context.get("period_label", base_context.get("report_date", ""))

    log.info(
        "Starting narrative generation: report_id=%s report_type=%s submission_id=%s",
        report_id,
        report_type,
        submission_id,
    )

    # ------------------------------------------------------------------
    # Step 1: Fetch data from database.
    # ------------------------------------------------------------------
    scores = _fetch_oei_scores(conn, submission_id)
    if not scores:
        raise KeyError(
            f"No oei_scores record found for submission_id '{submission_id}'. "
            "Scoring must complete before narrative generation."
        )

    signals = _fetch_signal_readings(conn, submission_id)
    prior_scores = _fetch_prior_score(
        conn, client_id, scores.get("period_date", "9999-12-31")
    )
    journal_entries = _fetch_journal_entries(conn, client_id, submission_id)

    log.info(
        "Data fetched: signals=%d, prior_period=%s, journal_entries=%d",
        len(signals),
        "yes" if prior_scores else "no",
        len(journal_entries),
    )

    # ------------------------------------------------------------------
    # Step 2: Build anonymized context for prompt construction.
    # ------------------------------------------------------------------
    anon_ctx = _build_anon_narrative_context(
        scores=scores,
        signals=signals,
        prior_scores=prior_scores,
        journal_entries=journal_entries,
        period_label=period_label,
    )

    # ------------------------------------------------------------------
    # Step 3: Generate sections and populate the context copy.
    # ------------------------------------------------------------------
    result = base_context.copy()

    if report_type == "snapshot":
        result = _generate_snapshot_narratives(
            conn=conn,
            result=result,
            anon_ctx=anon_ctx,
            report_id=report_id,
            client_name=client_name,
        )
    else:
        result = _generate_monthly_brief_narratives(
            conn=conn,
            result=result,
            anon_ctx=anon_ctx,
            scores=scores,
            signals=signals,
            prior_scores=prior_scores,
            report_id=report_id,
            client_name=client_name,
        )

    # ------------------------------------------------------------------
    # Step 4: Final validation -- all narrative fields must pass
    # word count bounds before returning to the caller.
    # ------------------------------------------------------------------
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

    log.info(
        "Narrative generation complete: report_id=%s sections=%d",
        report_id,
        len(narrative_keys),
    )

    return result


# ---------------------------------------------------------------------------
# Per-report-type generation helpers
# ---------------------------------------------------------------------------


def _generate_snapshot_narratives(
    conn: Any,
    result: dict[str, Any],
    anon_ctx: dict[str, Any],
    report_id: str,
    client_name: str,
) -> dict[str, Any]:
    """
    Generate the three Snapshot narrative sections and insert them into
    the result dict.

    Args:
        conn:        Open database connection.
        result:      Context dict copy being built.
        anon_ctx:    Anonymized context for prompt building.
        report_id:   UUID of the report record.
        client_name: Real client name for audit metadata only.

    Returns:
        Updated result dict with narrative_executive_summary,
        narrative_key_findings, narrative_recommended_focus populated.
    """
    # Executive summary
    exec_prompt = _SNAPSHOT_EXEC_SUMMARY_PROMPT_V1.format(**anon_ctx)
    exec_text, _ = _draft_section(
        conn=conn,
        section_name="executive_summary",
        prompt=exec_prompt,
        fallback_fn=lambda: _fallback_executive_summary(anon_ctx, "snapshot"),
        report_id=report_id,
        client_name=client_name,
    )
    result["narrative_executive_summary"] = exec_text

    # Key findings
    findings_prompt = _SNAPSHOT_KEY_FINDINGS_PROMPT_V1.format(**anon_ctx)
    findings_text, _ = _draft_section(
        conn=conn,
        section_name="key_findings",
        prompt=findings_prompt,
        fallback_fn=lambda: _fallback_key_findings(anon_ctx),
        report_id=report_id,
        client_name=client_name,
    )
    result["narrative_key_findings"] = findings_text

    # Recommended focus
    focus_prompt = _SNAPSHOT_RECOMMENDED_FOCUS_PROMPT_V1.format(**anon_ctx)
    focus_text, _ = _draft_section(
        conn=conn,
        section_name="recommended_focus",
        prompt=focus_prompt,
        fallback_fn=lambda: _fallback_recommended_focus(anon_ctx),
        report_id=report_id,
        client_name=client_name,
    )
    result["narrative_recommended_focus"] = focus_text

    return result


def _generate_monthly_brief_narratives(
    conn: Any,
    result: dict[str, Any],
    anon_ctx: dict[str, Any],
    scores: dict[str, Any],
    signals: list[dict[str, Any]],
    prior_scores: dict[str, Any],
    report_id: str,
    client_name: str,
) -> dict[str, Any]:
    """
    Generate the Monthly Brief narrative sections and insert them into the
    result dict: executive summary (governed prompt), per-dimension deep
    dives (only for breached/moved dimensions), and forward focus.

    Args:
        conn:         Open database connection.
        result:       Context dict copy being built.
        anon_ctx:     Anonymized context for prompt building.
        scores:       Raw OEI scores dict from database.
        signals:      Signal readings for this submission.
        prior_scores: Prior period scores dict or empty dict.
        report_id:    UUID of the report record.
        client_name:  Real client name for audit metadata only.

    Returns:
        Updated result dict with all monthly brief narrative fields populated.
    """
    # Executive summary -- governed prompt, verbatim from spec Part 5.
    exec_prompt = _MONTHLY_BRIEF_EXEC_SUMMARY_PROMPT_V1.format(**anon_ctx)
    exec_text, _ = _draft_section(
        conn=conn,
        section_name="executive_summary",
        prompt=exec_prompt,
        fallback_fn=lambda: _fallback_executive_summary(anon_ctx, "monthly_brief"),
        report_id=report_id,
        client_name=client_name,
    )
    result["narrative_executive_summary"] = exec_text

    # Per-dimension deep dives.
    dimension_map = {
        "1": ("Strategic Saturation", "strategic_saturation_score", "strategic_saturation_class"),
        "2": ("Governance Responsiveness", "governance_responsiveness_score", "governance_responsiveness_class"),
        "3": ("Execution Visibility", "execution_visibility_score", "execution_visibility_class"),
        "4": ("Reporting Integrity", "reporting_integrity_score", "reporting_integrity_class"),
        "5": ("Organizational Sustainability", "org_sustainability_score", "org_sustainability_class"),
    }

    deep_dives: dict[str, str] = {}

    for dim_id, (dim_name, score_col, class_col) in dimension_map.items():
        current_score = scores.get(score_col, 0)
        current_class = scores.get(class_col, "")
        prior_score = prior_scores.get(score_col)
        delta = (current_score - prior_score) if prior_score is not None else None

        # Generate a deep dive only for elevated/high/critical dimensions
        # or dimensions with material movement (abs delta >= 5).
        needs_deep_dive = current_class in ("Elevated", "High Risk", "Critical") or (
            delta is not None and abs(delta) >= 5
        )

        if not needs_deep_dive:
            deep_dives[dim_id] = ""
            continue

        if delta is not None:
            direction = "worsened" if delta > 0 else ("improved" if delta < 0 else "unchanged")
        else:
            direction = "no prior period"

        dim_signals = [
            {"signal": s["signal_name"], "value": s["signal_value"], "breached": s["threshold_breached"]}
            for s in signals
        ]

        dim_sub_cats = []
        for dim_ctx in result.get("dimensions", []):
            if dim_ctx.get("dimension_id") == dim_id:
                dim_sub_cats = [
                    {"name": sc["name"], "impact": sc["impact"]}
                    for sc in dim_ctx.get("sub_categories", [])
                ]
                break

        dim_anon_ctx = {
            "client_name": "CLIENT_A",
            "dimension_name": dim_name,
            "score": current_score,
            "classification": current_class,
            "prior_score": str(prior_score) if prior_score is not None else "N/A",
            "delta": str(abs(delta)) if delta is not None else "N/A",
            "direction": direction,
            "sub_categories": json.dumps(dim_sub_cats, indent=None),
            "signals": json.dumps(dim_signals, indent=None),
        }

        deep_prompt = _DIMENSION_DEEP_DIVE_PROMPT_V1.format(**dim_anon_ctx)
        deep_text, _ = _draft_section(
            conn=conn,
            section_name=f"dimension_{dim_id}_deep_dive",
            prompt=deep_prompt,
            fallback_fn=lambda dn=dim_name, cs=current_score, cc=current_class, d=delta: (
                _fallback_dimension_deep_dive(dn, cs, cc, d)
            ),
            report_id=report_id,
            client_name=client_name,
        )
        deep_dives[dim_id] = deep_text

    result["narrative_dimension_deep_dives"] = deep_dives

    # Forward focus
    focus_prompt = _FORWARD_FOCUS_PROMPT_V1.format(**anon_ctx)
    focus_text, _ = _draft_section(
        conn=conn,
        section_name="forward_focus",
        prompt=focus_prompt,
        fallback_fn=lambda: _fallback_forward_focus(anon_ctx),
        report_id=report_id,
        client_name=client_name,
    )
    result["narrative_forward_focus"] = focus_text

    return result
