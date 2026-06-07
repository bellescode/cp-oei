"""
scoring/overrides.py
CPOI Platform — Managing Partner Score Override Module

Implements the manual override protocol defined in spec Part 2.

What can be overridden (three override_type values):
  sub_category  — any individual sub-category score (e.g. "1.1", "3.4")
  dimension     — any dimension composite score (target_id "1" through "5")
  composite     — the OEI composite score (target_id "composite")

What cannot be overridden:
  Raw intake data, signal calculation outputs, audit log entries.

Every override requires:
  1. The original calculated score (stored automatically from OEIScoreResult)
  2. The override score (must be in [0.0, 100.0])
  3. A minimum 50-word justification note (enforced here before any DB write)
  4. An evidence source from the approved vocabulary
  5. Timestamp and actor identity (Managing Partner)

Override cascade behavior (apply_overrides):
  - Sub-category overrides replace the sub-category score, then the affected
    dimension is recomputed from the updated sub-category scores.
  - Dimension overrides replace the dimension score after any sub-category
    cascade, without altering sub-category scores.
  - Composite overrides replace only the composite score.
  - All overrides are applied in chronological order (oldest applied_at first);
    the most recently applied override for a given target wins.

Storage:
  Each override is persisted to the score_overrides table and produces an
  audit log entry with event_type "score_overridden". Superseded overrides
  on the same target_id are marked is_active = 0 before the new one is
  written, preserving the full history.

Public interface:
  create_override(conn, submission_id, client_id, override_type, target_id,
                  original_score, override_score, justification, evidence_source,
                  applied_by) -> str  (override_id)

  get_active_overrides(conn, submission_id) -> list[OverrideRecord]

  apply_overrides(original, overrides) -> OEIScoreResult
"""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

from db.audit import write_audit_log
from scoring.constants import (
    COMPOSITE_WEIGHTS,
    DIMENSION_WEIGHTS,
    SCORE_BANDS,
)
from scoring.dimensions import (
    DIMENSION_NAMES,
    DimensionResult,
    OEIScoreResult,
    assign_impact_indicator,
    classify_score,
    compute_dimension,
)
from scoring.sub_categories import SubCategoryResult

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


log = _build_logger("cpoi.scoring.overrides")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OVERRIDE_TYPES: Final[frozenset[str]] = frozenset({
    "sub_category",
    "dimension",
    "composite",
})

EVIDENCE_SOURCES: Final[frozenset[str]] = frozenset({
    "discovery_call",
    "review_session",
    "direct_observation",
    "client_disclosure",
    "document_review",
    "third_party_data",
})

VALID_DIMENSION_IDS: Final[frozenset[str]] = frozenset(DIMENSION_WEIGHTS.keys())
VALID_SUB_CATEGORY_IDS: Final[frozenset[str]] = frozenset(SCORE_BANDS.keys())

JUSTIFICATION_MIN_WORDS: Final[int] = 50


# ---------------------------------------------------------------------------
# OverrideRecord dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OverrideRecord:
    """
    A single Managing Partner override record, as stored in score_overrides.

    Attributes:
        override_id:     UUID string identifying this override record.
        submission_id:   FK to intake_submissions.submission_id.
        client_id:       FK to clients.client_id.
        override_type:   "sub_category", "dimension", or "composite".
        target_id:       Sub-category ID, dimension ID, or "composite".
        original_score:  The score calculated by the engine before override.
        override_score:  The score set by the Managing Partner.
        justification:   Written justification. Minimum 50 words.
        evidence_source: Source of the contradicting evidence.
        applied_by:      Actor who applied the override. Always "managing_partner"
                         in the current single-operator build.
        applied_at:      ISO 8601 UTC timestamp of when the override was applied.
        is_active:       True while this is the current active override for this
                         target. False when superseded by a newer override.
    """

    override_id: str
    submission_id: str
    client_id: str
    override_type: str
    target_id: str
    original_score: float
    override_score: float
    justification: str
    evidence_source: str
    applied_by: str
    applied_at: str
    is_active: bool


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def _validate_override_input(
    override_type: str,
    target_id: str,
    override_score: float,
    justification: str,
    evidence_source: str,
) -> None:
    """
    Validate all override input fields before any database operation.

    Validates:
      - override_type is in OVERRIDE_TYPES
      - target_id is valid for the given override_type
      - override_score is in [0.0, 100.0]
      - justification meets the 50-word minimum
      - evidence_source is in EVIDENCE_SOURCES

    Args:
        override_type:   The type of score being overridden.
        target_id:       The specific score target within the type.
        override_score:  The replacement score value.
        justification:   Written rationale for the override.
        evidence_source: Category of evidence supporting the override.

    Raises:
        ValueError: on any validation failure. The message names the failing
                    field and states the requirement clearly.
    """
    if override_type not in OVERRIDE_TYPES:
        raise ValueError(
            f"override_type '{override_type}' is not valid. "
            f"Allowed values: {sorted(OVERRIDE_TYPES)}."
        )

    if override_type == "sub_category":
        if target_id not in VALID_SUB_CATEGORY_IDS:
            raise ValueError(
                f"target_id '{target_id}' is not a valid sub-category ID. "
                f"Valid IDs: {sorted(VALID_SUB_CATEGORY_IDS)}."
            )
    elif override_type == "dimension":
        if target_id not in VALID_DIMENSION_IDS:
            raise ValueError(
                f"target_id '{target_id}' is not a valid dimension ID. "
                f"Valid IDs: {sorted(VALID_DIMENSION_IDS)}."
            )
    elif override_type == "composite":
        if target_id != "composite":
            raise ValueError(
                "target_id must be the literal string 'composite' "
                "when override_type is 'composite'."
            )

    if not isinstance(override_score, (int, float)):
        raise ValueError(
            "override_score must be a numeric value in [0.0, 100.0]."
        )
    if not (0.0 <= float(override_score) <= 100.0):
        raise ValueError(
            f"override_score {override_score} is out of range. "
            "Valid range is [0.0, 100.0]."
        )

    if not justification or not justification.strip():
        raise ValueError(
            "justification must be a non-empty string of at least "
            f"{JUSTIFICATION_MIN_WORDS} words."
        )
    word_count = len(justification.split())
    if word_count < JUSTIFICATION_MIN_WORDS:
        raise ValueError(
            f"justification has {word_count} word(s). "
            f"The minimum requirement is {JUSTIFICATION_MIN_WORDS} words. "
            "Provide a substantive explanation of the contradicting evidence "
            "and why the calculated score does not reflect operational reality."
        )

    if evidence_source not in EVIDENCE_SOURCES:
        raise ValueError(
            f"evidence_source '{evidence_source}' is not valid. "
            f"Allowed values: {sorted(EVIDENCE_SOURCES)}."
        )


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def create_override(
    conn: Any,
    submission_id: str,
    client_id: str,
    override_type: str,
    target_id: str,
    original_score: float,
    override_score: float,
    justification: str,
    evidence_source: str,
    applied_by: str = "managing_partner",
) -> str:
    """
    Validate, persist, and audit-log a Managing Partner score override.

    Validates all inputs before any database operation. If validation passes:
      1. Any existing active override on the same (submission_id, target_id)
         pair is marked is_active = 0 (superseded).
      2. The new override record is inserted into score_overrides.
      3. An audit log entry is written with event_type "score_overridden".

    The caller is responsible for committing the transaction after this call.
    If this function is part of a larger workflow, include it within that
    workflow's transaction block.

    Args:
        conn:            Open, authenticated database connection.
        submission_id:   UUID of the intake_submission being adjusted.
        client_id:       Client this submission belongs to.
        override_type:   "sub_category", "dimension", or "composite".
        target_id:       Sub-category ID (e.g. "1.1"), dimension ID (e.g. "3"),
                         or the string "composite".
        original_score:  The score produced by the engine before this override.
        override_score:  The replacement score in [0.0, 100.0].
        justification:   Written rationale. Minimum 50 words required.
        evidence_source: One of: discovery_call, review_session,
                         direct_observation, client_disclosure,
                         document_review, third_party_data.
        applied_by:      Actor identity. Defaults to "managing_partner".

    Returns:
        str: UUID of the newly created override record.

    Raises:
        ValueError: if any input fails validation (before any DB write).
        Exception:  any database error is propagated without wrapping.
    """
    _validate_override_input(
        override_type, target_id, override_score, justification, evidence_source
    )

    override_id = str(uuid.uuid4())
    applied_at = datetime.now(timezone.utc).isoformat()

    # Mark any existing active override for this target as superseded.
    conn.execute(
        """
        UPDATE score_overrides
           SET is_active = 0
         WHERE submission_id = ?
           AND target_id     = ?
           AND is_active     = 1
        """,
        (submission_id, target_id),
    )

    # Insert the new override record.
    conn.execute(
        """
        INSERT INTO score_overrides (
            override_id, submission_id, client_id,
            override_type, target_id,
            original_score, override_score,
            justification, evidence_source,
            applied_by, applied_at, is_active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            override_id,
            submission_id,
            client_id,
            override_type,
            target_id,
            float(original_score),
            float(override_score),
            justification.strip(),
            evidence_source,
            applied_by,
            applied_at,
        ),
    )

    # Write audit log entry.
    score_delta = float(override_score) - float(original_score)
    write_audit_log(
        conn=conn,
        event_type="score_overridden",
        entity_type="score_override",
        entity_id=override_id,
        description=(
            f"Score override applied by {applied_by}. "
            f"Type: {override_type}, target: {target_id}. "
            f"Original score: {original_score:.1f}, "
            f"override score: {override_score:.1f} "
            f"(delta: {score_delta:+.1f})."
        ),
        performed_by=applied_by,
        metadata={
            "submission_id":  submission_id,
            "client_id":      client_id,
            "override_type":  override_type,
            "target_id":      target_id,
            "original_score": float(original_score),
            "override_score": float(override_score),
            "score_delta":    score_delta,
            "evidence_source": evidence_source,
            "word_count":     len(justification.split()),
        },
    )

    log.info(
        "Override created: override_id=%s, type=%s, target=%s, "
        "original=%.1f, override=%.1f, delta=%+.1f",
        override_id, override_type, target_id,
        original_score, override_score, score_delta,
    )

    return override_id


def get_active_overrides(
    conn: Any,
    submission_id: str,
) -> list[OverrideRecord]:
    """
    Retrieve all active override records for a submission.

    Returns only records where is_active = 1. Superseded overrides
    (is_active = 0) are excluded — they remain in the database for audit
    purposes but do not influence score computation.

    Records are returned in chronological order of applied_at (oldest first)
    so that apply_overrides processes them in the correct cascade sequence,
    with the most recently applied override winning when multiple overrides
    target the same score.

    Args:
        conn:          Open, authenticated database connection.
        submission_id: UUID of the intake_submission to query.

    Returns:
        list[OverrideRecord]: Active overrides sorted by applied_at ascending.
                              Empty list if no active overrides exist.
    """
    cursor = conn.execute(
        """
        SELECT override_id, submission_id, client_id,
               override_type, target_id,
               original_score, override_score,
               justification, evidence_source,
               applied_by, applied_at, is_active
          FROM score_overrides
         WHERE submission_id = ?
           AND is_active     = 1
         ORDER BY applied_at ASC
        """,
        (submission_id,),
    )

    records: list[OverrideRecord] = []
    for row in cursor.fetchall():
        records.append(
            OverrideRecord(
                override_id=row[0],
                submission_id=row[1],
                client_id=row[2],
                override_type=row[3],
                target_id=row[4],
                original_score=float(row[5]),
                override_score=float(row[6]),
                justification=row[7],
                evidence_source=row[8],
                applied_by=row[9],
                applied_at=row[10],
                is_active=bool(row[11]),
            )
        )

    log.info(
        "Retrieved %d active override(s) for submission %s",
        len(records), submission_id,
    )
    return records


def apply_overrides(
    original: OEIScoreResult,
    overrides: list[OverrideRecord],
) -> OEIScoreResult:
    """
    Apply a list of active override records to an OEIScoreResult.

    Returns a new OEIScoreResult with the overridden scores in place.
    The original OEIScoreResult is not mutated.

    Override cascade sequence (applied in chronological order):
      1. Sub-category overrides: replace the target sub-category score.
         The dimension that contains the overridden sub-category is then
         recomputed from the updated sub-category scores. The OEI composite
         is recomputed from all five dimension scores.
      2. Dimension overrides: replace the target dimension score directly.
         Does not alter sub-category scores. The composite is recomputed.
      3. Composite override: replace the composite score directly.
         Does not alter dimension or sub-category scores.

    When multiple overrides target the same score (e.g. two overrides for
    sub-category "1.1"), they are applied in chronological order and the
    last one written wins.

    If overrides is empty, the original OEIScoreResult is returned unchanged.

    Args:
        original:  OEIScoreResult produced by compute_oei_score().
        overrides: Active override records, typically from get_active_overrides().
                   Must be sorted by applied_at ascending (get_active_overrides
                   guarantees this ordering).

    Returns:
        OEIScoreResult: New result with all overrides applied and dimensions
                        and composite recomputed where affected.
    """
    if not overrides:
        return original

    # --- Phase 1: apply sub-category overrides ---
    # Build a mutable copy of sub-category results.
    sub_results: dict[str, SubCategoryResult] = dict(original.sub_category_results)

    sub_cat_overrides = [ov for ov in overrides if ov.override_type == "sub_category"]
    for ov in sub_cat_overrides:
        old = sub_results[ov.target_id]
        sub_results[ov.target_id] = SubCategoryResult(
            sub_category_id=old.sub_category_id,
            score=ov.override_score,
            is_missing_data=False,
            is_non_computable=old.is_non_computable,
            computed_value=old.computed_value,   # preserve original measured value
        )
        log.info(
            "Applied sub-category override: %s -> %.1f (was %.1f)",
            ov.target_id, ov.override_score, old.score,
        )

    # Recompute all five dimensions with the (possibly) updated sub-category scores.
    # Dimensions whose sub-categories were not changed will produce the same score.
    dim_results: dict[str, DimensionResult] = {
        dim_id: compute_dimension(dim_id, sub_results)
        for dim_id in DIMENSION_WEIGHTS
    }

    # --- Phase 2: apply dimension overrides ---
    dim_overrides = [ov for ov in overrides if ov.override_type == "dimension"]
    for ov in dim_overrides:
        old_dim = dim_results[ov.target_id]
        # Replace the dimension score. Sub-category scores and impact
        # indicators are preserved as computed; only the dimension total changes.
        dim_results[ov.target_id] = DimensionResult(
            dimension_id=old_dim.dimension_id,
            dimension_name=old_dim.dimension_name,
            score=ov.override_score,
            classification=classify_score(ov.override_score),
            sub_category_results=old_dim.sub_category_results,
            impact_indicators=old_dim.impact_indicators,
        )
        log.info(
            "Applied dimension override: dim %s -> %.1f (was %.1f)",
            ov.target_id, ov.override_score, old_dim.score,
        )

    # --- Phase 3: recompute composite from (possibly overridden) dimension scores ---
    composite_score: float = sum(
        dim_results[dim_id].score * COMPOSITE_WEIGHTS[dim_id]
        for dim_id in COMPOSITE_WEIGHTS
    )

    # --- Phase 4: apply composite override ---
    composite_overrides = [ov for ov in overrides if ov.override_type == "composite"]
    for ov in composite_overrides:
        log.info(
            "Applied composite override: %.1f (was %.1f)",
            ov.override_score, composite_score,
        )
        composite_score = ov.override_score

    composite_classification = classify_score(composite_score)

    log.info(
        "Overrides applied: composite=%.2f (%s), "
        "%d sub-category, %d dimension, %d composite override(s)",
        composite_score,
        composite_classification,
        len(sub_cat_overrides),
        len(dim_overrides),
        len(composite_overrides),
    )

    return OEIScoreResult(
        composite_score=composite_score,
        composite_classification=composite_classification,
        dimension_results=dim_results,
        sub_category_results=sub_results,
        calculated_at=original.calculated_at,
    )
