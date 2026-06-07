"""
tests/_smoke_overrides.py
CPOI Platform — Smoke tests for scoring/overrides.py

Covers:
  1. _validate_override_input — all rejection cases
  2. create_override           — happy path: DB row + audit log entry
  3. create_override           — supersedes prior active override
  4. get_active_overrides      — ordering and is_active filter
  5. apply_overrides           — empty list returns original unchanged
  6. apply_overrides           — sub-category override cascades to dimension and composite
  7. apply_overrides           — dimension override cascades to composite
  8. apply_overrides           — composite override replaces composite only
  9. apply_overrides           — full cascade: sub-cat + dimension + composite

All database operations use an in-memory sqlite3 connection (schema-compatible
with sqlcipher3; encryption is infrastructure, not logic). The score_overrides
and audit_log tables are created inline from the same DDL used in schema.sql.

Run from the project root:
    python -m pytest tests/_smoke_overrides.py -v
"""

import sqlite3
import textwrap
from dataclasses import replace

import pytest

# ---------------------------------------------------------------------------
# Minimal schema for the tables overrides.py touches.
# Mirrors db/schema.sql exactly for the relevant tables.
# ---------------------------------------------------------------------------

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS clients (
    client_id     TEXT PRIMARY KEY,
    client_name   TEXT NOT NULL,
    engagement_type TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS intake_submissions (
    submission_id          TEXT PRIMARY KEY,
    client_id              TEXT NOT NULL REFERENCES clients(client_id),
    reporting_period_start DATE NOT NULL,
    reporting_period_end   DATE NOT NULL,
    submitted_at           TIMESTAMP NOT NULL,
    file_path              TEXT NOT NULL,
    ingestion_status       TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS score_overrides (
    override_id    TEXT PRIMARY KEY,
    submission_id  TEXT NOT NULL REFERENCES intake_submissions(submission_id),
    client_id      TEXT NOT NULL REFERENCES clients(client_id),
    override_type  TEXT NOT NULL CHECK (override_type IN ('sub_category','dimension','composite')),
    target_id      TEXT NOT NULL,
    original_score REAL NOT NULL,
    override_score REAL NOT NULL CHECK (override_score >= 0.0 AND override_score <= 100.0),
    justification  TEXT NOT NULL,
    evidence_source TEXT NOT NULL CHECK (evidence_source IN (
        'discovery_call','review_session','direct_observation',
        'client_disclosure','document_review','third_party_data'
    )),
    applied_by     TEXT NOT NULL DEFAULT 'managing_partner',
    applied_at     TIMESTAMP NOT NULL,
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS audit_log (
    log_id       TEXT PRIMARY KEY,
    event_type   TEXT NOT NULL,
    entity_type  TEXT,
    entity_id    TEXT,
    description  TEXT NOT NULL,
    performed_by TEXT NOT NULL DEFAULT 'system',
    performed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata     TEXT
);

CREATE TRIGGER IF NOT EXISTS audit_log_no_update
    BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: UPDATE is not permitted');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
    BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: DELETE is not permitted');
END;
"""

# A justification that meets the 50-word minimum.
_LONG_JUSTIFICATION = (
    "During the direct observation session conducted on site this quarter, "
    "the managing partner witnessed the team conducting significantly more "
    "governance touchpoints than were captured in the submitted telemetry data. "
    "The discrepancy is attributable to informal review meetings not entered "
    "into the tracking system. The calculated score therefore understates "
    "actual governance responsiveness as observed in the field."
)

_SUBMISSION_ID = "sub-0001"
_CLIENT_ID     = "cli-0001"


def _make_conn() -> sqlite3.Connection:
    """Open an in-memory SQLite connection with the test schema applied."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA_DDL)
    # Insert minimal foreign-key parents so FK constraints pass when enabled.
    conn.execute(
        "INSERT INTO clients (client_id, client_name, engagement_type) "
        "VALUES (?, ?, ?)",
        (_CLIENT_ID, "Test Client", "oeil"),
    )
    conn.execute(
        "INSERT INTO intake_submissions "
        "(submission_id, client_id, reporting_period_start, reporting_period_end, "
        " submitted_at, file_path) VALUES (?, ?, ?, ?, ?, ?)",
        (_SUBMISSION_ID, _CLIENT_ID, "2025-01-01", "2025-03-31",
         "2025-04-01T00:00:00", "/data/test.xlsx"),
    )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Build a minimal but valid OEIScoreResult for apply_overrides tests.
# ---------------------------------------------------------------------------

def _make_oei_result():
    """
    Build a synthetic OEIScoreResult with known, round numbers.

    All sub-category scores set to 50.0 (CONTRIBUTING band).
    All dimension scores will be 50.0 (since all sub-cat scores are equal
    and weights within each dimension sum to 1.0).
    Composite score will be 50.0.
    """
    from scoring.sub_categories import SubCategoryResult
    from scoring.dimensions import (
        DimensionResult,
        OEIScoreResult,
        assign_impact_indicator,
        classify_score,
    )
    from scoring.constants import DIMENSION_WEIGHTS

    # Build sub-category results at 50.0.
    sub_results: dict = {}
    for dim_weights in DIMENSION_WEIGHTS.values():
        for sub_id in dim_weights:
            sub_results[sub_id] = SubCategoryResult(
                sub_category_id=sub_id,
                score=50.0,
                is_missing_data=False,
                is_non_computable=False,
                computed_value=None,
            )

    # Build dimension results.
    dim_results: dict = {}
    for dim_id, dim_weights in DIMENSION_WEIGHTS.items():
        dim_score = sum(
            sub_results[sub_id].score * w
            for sub_id, w in dim_weights.items()
        )
        impact = {sub_id: assign_impact_indicator(sub_results[sub_id].score)
                  for sub_id in dim_weights}
        from scoring.dimensions import DIMENSION_NAMES
        dim_results[dim_id] = DimensionResult(
            dimension_id=dim_id,
            dimension_name=DIMENSION_NAMES[dim_id],
            score=dim_score,
            classification=classify_score(dim_score),
            sub_category_results={k: sub_results[k] for k in dim_weights},
            impact_indicators=impact,
        )

    from scoring.constants import COMPOSITE_WEIGHTS
    composite = sum(
        dim_results[d].score * COMPOSITE_WEIGHTS[d]
        for d in COMPOSITE_WEIGHTS
    )

    return OEIScoreResult(
        composite_score=composite,
        composite_classification=classify_score(composite),
        dimension_results=dim_results,
        sub_category_results=sub_results,
        calculated_at="2025-04-01T12:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# Section 1: _validate_override_input rejection cases
# ---------------------------------------------------------------------------

from scoring.overrides import _validate_override_input  # noqa: E402 (after helpers)


class TestValidateOverrideInput:
    """_validate_override_input raises ValueError for every invalid input."""

    def test_bad_override_type(self):
        with pytest.raises(ValueError, match="override_type"):
            _validate_override_input(
                override_type="subcategory",       # typo — underscore missing
                target_id="1.1",
                override_score=50.0,
                justification=_LONG_JUSTIFICATION,
                evidence_source="review_session",
            )

    def test_bad_sub_category_target_id(self):
        with pytest.raises(ValueError, match="target_id"):
            _validate_override_input(
                override_type="sub_category",
                target_id="9.9",                   # does not exist
                override_score=50.0,
                justification=_LONG_JUSTIFICATION,
                evidence_source="review_session",
            )

    def test_bad_dimension_target_id(self):
        with pytest.raises(ValueError, match="target_id"):
            _validate_override_input(
                override_type="dimension",
                target_id="6",                     # only 1-5 are valid
                override_score=50.0,
                justification=_LONG_JUSTIFICATION,
                evidence_source="review_session",
            )

    def test_composite_target_id_must_be_literal(self):
        with pytest.raises(ValueError, match="target_id"):
            _validate_override_input(
                override_type="composite",
                target_id="oei_composite",         # wrong literal
                override_score=50.0,
                justification=_LONG_JUSTIFICATION,
                evidence_source="review_session",
            )

    def test_score_above_100(self):
        with pytest.raises(ValueError, match="override_score"):
            _validate_override_input(
                override_type="sub_category",
                target_id="1.1",
                override_score=100.1,
                justification=_LONG_JUSTIFICATION,
                evidence_source="review_session",
            )

    def test_score_below_0(self):
        with pytest.raises(ValueError, match="override_score"):
            _validate_override_input(
                override_type="sub_category",
                target_id="1.1",
                override_score=-1.0,
                justification=_LONG_JUSTIFICATION,
                evidence_source="review_session",
            )

    def test_justification_too_short(self):
        short = "This is too brief."
        with pytest.raises(ValueError, match="justification"):
            _validate_override_input(
                override_type="sub_category",
                target_id="1.1",
                override_score=50.0,
                justification=short,
                evidence_source="review_session",
            )

    def test_justification_empty_string(self):
        with pytest.raises(ValueError, match="justification"):
            _validate_override_input(
                override_type="sub_category",
                target_id="1.1",
                override_score=50.0,
                justification="   ",
                evidence_source="review_session",
            )

    def test_bad_evidence_source(self):
        with pytest.raises(ValueError, match="evidence_source"):
            _validate_override_input(
                override_type="sub_category",
                target_id="1.1",
                override_score=50.0,
                justification=_LONG_JUSTIFICATION,
                evidence_source="email_thread",    # not in approved vocab
            )

    def test_valid_inputs_do_not_raise(self):
        """All three override types must pass validation without raising."""
        for otype, tid in [
            ("sub_category", "2.3"),
            ("dimension", "4"),
            ("composite", "composite"),
        ]:
            _validate_override_input(
                override_type=otype,
                target_id=tid,
                override_score=75.0,
                justification=_LONG_JUSTIFICATION,
                evidence_source="direct_observation",
            )

    def test_score_boundaries_are_inclusive(self):
        """0.0 and 100.0 are both valid override scores."""
        for score in (0.0, 100.0):
            _validate_override_input(
                override_type="sub_category",
                target_id="1.1",
                override_score=score,
                justification=_LONG_JUSTIFICATION,
                evidence_source="client_disclosure",
            )


# ---------------------------------------------------------------------------
# Section 2: create_override — happy path
# ---------------------------------------------------------------------------

from scoring.overrides import create_override, get_active_overrides  # noqa: E402


class TestCreateOverride:
    """create_override writes to score_overrides and audit_log."""

    def test_returns_string_uuid(self):
        conn = _make_conn()
        oid = create_override(
            conn, _SUBMISSION_ID, _CLIENT_ID,
            "sub_category", "1.1",
            original_score=50.0, override_score=75.0,
            justification=_LONG_JUSTIFICATION,
            evidence_source="review_session",
        )
        conn.commit()
        assert isinstance(oid, str)
        assert len(oid) == 36          # UUID4 hyphenated form

    def test_row_written_to_score_overrides(self):
        conn = _make_conn()
        oid = create_override(
            conn, _SUBMISSION_ID, _CLIENT_ID,
            "sub_category", "2.2",
            original_score=40.0, override_score=60.0,
            justification=_LONG_JUSTIFICATION,
            evidence_source="document_review",
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM score_overrides WHERE override_id = ?", (oid,)
        ).fetchone()
        assert row is not None, "override row not found in score_overrides"
        col = dict(zip(
            ["override_id","submission_id","client_id","override_type",
             "target_id","original_score","override_score","justification",
             "evidence_source","applied_by","applied_at","is_active"],
            row,
        ))
        assert col["submission_id"]  == _SUBMISSION_ID
        assert col["client_id"]      == _CLIENT_ID
        assert col["override_type"]  == "sub_category"
        assert col["target_id"]      == "2.2"
        assert col["original_score"] == pytest.approx(40.0)
        assert col["override_score"] == pytest.approx(60.0)
        assert col["evidence_source"] == "document_review"
        assert col["is_active"]      == 1

    def test_audit_log_entry_written(self):
        conn = _make_conn()
        oid = create_override(
            conn, _SUBMISSION_ID, _CLIENT_ID,
            "dimension", "3",
            original_score=55.0, override_score=30.0,
            justification=_LONG_JUSTIFICATION,
            evidence_source="discovery_call",
        )
        conn.commit()
        row = conn.execute(
            "SELECT event_type, entity_id FROM audit_log "
            "WHERE entity_id = ?", (oid,)
        ).fetchone()
        assert row is not None, "audit_log entry not found"
        assert row[0] == "score_overridden"
        assert row[1] == oid

    def test_validation_error_prevents_db_write(self):
        """ValueError from validation must not write any rows."""
        conn = _make_conn()
        with pytest.raises(ValueError):
            create_override(
                conn, _SUBMISSION_ID, _CLIENT_ID,
                "sub_category", "1.1",
                original_score=50.0, override_score=999.0,  # out of range
                justification=_LONG_JUSTIFICATION,
                evidence_source="review_session",
            )
        count = conn.execute(
            "SELECT COUNT(*) FROM score_overrides"
        ).fetchone()[0]
        assert count == 0, "No row should be written when validation fails"

    def test_supersede_prior_active_override(self):
        """
        Creating a second override for the same target marks the first
        is_active = 0 and inserts the new one as is_active = 1.
        """
        conn = _make_conn()
        oid1 = create_override(
            conn, _SUBMISSION_ID, _CLIENT_ID,
            "sub_category", "1.2",
            original_score=50.0, override_score=60.0,
            justification=_LONG_JUSTIFICATION,
            evidence_source="review_session",
        )
        conn.commit()

        oid2 = create_override(
            conn, _SUBMISSION_ID, _CLIENT_ID,
            "sub_category", "1.2",
            original_score=50.0, override_score=70.0,
            justification=_LONG_JUSTIFICATION,
            evidence_source="direct_observation",
        )
        conn.commit()

        first = conn.execute(
            "SELECT is_active FROM score_overrides WHERE override_id = ?",
            (oid1,),
        ).fetchone()
        second = conn.execute(
            "SELECT is_active FROM score_overrides WHERE override_id = ?",
            (oid2,),
        ).fetchone()

        assert first[0] == 0,  "Prior override should be marked is_active=0"
        assert second[0] == 1, "New override should be is_active=1"

    def test_supersede_different_targets_independent(self):
        """
        Overrides for different targets do not supersede each other.
        Both should remain is_active = 1.
        """
        conn = _make_conn()
        oid_a = create_override(
            conn, _SUBMISSION_ID, _CLIENT_ID,
            "sub_category", "1.1",
            original_score=50.0, override_score=60.0,
            justification=_LONG_JUSTIFICATION,
            evidence_source="review_session",
        )
        oid_b = create_override(
            conn, _SUBMISSION_ID, _CLIENT_ID,
            "sub_category", "1.2",
            original_score=50.0, override_score=70.0,
            justification=_LONG_JUSTIFICATION,
            evidence_source="review_session",
        )
        conn.commit()

        for oid in (oid_a, oid_b):
            row = conn.execute(
                "SELECT is_active FROM score_overrides WHERE override_id = ?",
                (oid,),
            ).fetchone()
            assert row[0] == 1, f"Override {oid} should still be active"


# ---------------------------------------------------------------------------
# Section 3: get_active_overrides
# ---------------------------------------------------------------------------

class TestGetActiveOverrides:
    """get_active_overrides returns only is_active=1 rows, oldest first."""

    def test_empty_returns_empty_list(self):
        conn = _make_conn()
        result = get_active_overrides(conn, _SUBMISSION_ID)
        assert result == []

    def test_returns_active_records_only(self):
        conn = _make_conn()
        # Insert two overrides for the same target so the first is superseded.
        create_override(
            conn, _SUBMISSION_ID, _CLIENT_ID,
            "sub_category", "3.1",
            original_score=50.0, override_score=60.0,
            justification=_LONG_JUSTIFICATION,
            evidence_source="review_session",
        )
        conn.commit()
        create_override(
            conn, _SUBMISSION_ID, _CLIENT_ID,
            "sub_category", "3.1",
            original_score=50.0, override_score=70.0,
            justification=_LONG_JUSTIFICATION,
            evidence_source="review_session",
        )
        conn.commit()

        records = get_active_overrides(conn, _SUBMISSION_ID)
        assert len(records) == 1
        assert records[0].override_score == pytest.approx(70.0)
        assert records[0].is_active is True

    def test_multiple_targets_all_returned(self):
        conn = _make_conn()
        for sub_id in ("1.1", "2.1", "3.1"):
            create_override(
                conn, _SUBMISSION_ID, _CLIENT_ID,
                "sub_category", sub_id,
                original_score=50.0, override_score=65.0,
                justification=_LONG_JUSTIFICATION,
                evidence_source="document_review",
            )
        conn.commit()

        records = get_active_overrides(conn, _SUBMISSION_ID)
        assert len(records) == 3
        target_ids = {r.target_id for r in records}
        assert target_ids == {"1.1", "2.1", "3.1"}

    def test_different_submission_not_returned(self):
        """Overrides for a different submission_id must not appear."""
        conn = _make_conn()
        other_sub = "sub-9999"
        # Insert parent rows for the other submission.
        conn.execute(
            "INSERT INTO intake_submissions "
            "(submission_id, client_id, reporting_period_start, reporting_period_end,"
            " submitted_at, file_path) VALUES (?, ?, ?, ?, ?, ?)",
            (other_sub, _CLIENT_ID, "2025-04-01", "2025-06-30",
             "2025-07-01T00:00:00", "/data/other.xlsx"),
        )
        conn.commit()

        create_override(
            conn, other_sub, _CLIENT_ID,
            "sub_category", "1.1",
            original_score=50.0, override_score=65.0,
            justification=_LONG_JUSTIFICATION,
            evidence_source="review_session",
        )
        conn.commit()

        records = get_active_overrides(conn, _SUBMISSION_ID)
        assert records == []


# ---------------------------------------------------------------------------
# Section 4: apply_overrides
# ---------------------------------------------------------------------------

from scoring.overrides import apply_overrides, OverrideRecord  # noqa: E402


def _make_override_record(
    override_type: str,
    target_id: str,
    override_score: float,
    applied_at: str = "2025-04-01T10:00:00+00:00",
) -> OverrideRecord:
    """Convenience factory for test OverrideRecord objects."""
    return OverrideRecord(
        override_id=str(__import__("uuid").uuid4()),
        submission_id=_SUBMISSION_ID,
        client_id=_CLIENT_ID,
        override_type=override_type,
        target_id=target_id,
        original_score=50.0,
        override_score=override_score,
        justification=_LONG_JUSTIFICATION,
        evidence_source="review_session",
        applied_by="managing_partner",
        applied_at=applied_at,
        is_active=True,
    )


class TestApplyOverrides:
    """apply_overrides cascades overrides through the score hierarchy."""

    def setup_method(self):
        """Fresh synthetic OEIScoreResult for each test (all scores = 50.0)."""
        self.original = _make_oei_result()

    def test_empty_overrides_returns_original(self):
        result = apply_overrides(self.original, [])
        assert result is self.original

    # --- sub-category override cascade ---

    def test_sub_category_override_changes_sub_score(self):
        ov = _make_override_record("sub_category", "1.1", override_score=80.0)
        result = apply_overrides(self.original, [ov])
        assert result.sub_category_results["1.1"].score == pytest.approx(80.0)

    def test_sub_category_override_recomputes_dimension(self):
        """
        Sub-category "1.1" has weight 0.30 within Dimension 1.
        Original dim1 score = 50.0.
        After override of 1.1 to 80.0:
          new dim1 = 80.0*0.30 + 50.0*0.70 = 24.0 + 35.0 = 59.0
        """
        from scoring.constants import DIMENSION_WEIGHTS
        ov = _make_override_record("sub_category", "1.1", override_score=80.0)
        result = apply_overrides(self.original, [ov])

        # Recompute expected dimension 1 score manually.
        weights = DIMENSION_WEIGHTS["1"]
        expected_dim1 = sum(
            result.sub_category_results[sid].score * w
            for sid, w in weights.items()
        )
        assert result.dimension_results["1"].score == pytest.approx(expected_dim1, abs=0.01)

    def test_sub_category_override_recomputes_composite(self):
        """
        Composite = mean of 5 dimension scores.
        Only dim1 changes. Composite should reflect the updated dim1.
        """
        ov = _make_override_record("sub_category", "1.1", override_score=80.0)
        result = apply_overrides(self.original, [ov])

        from scoring.constants import COMPOSITE_WEIGHTS
        expected_composite = sum(
            result.dimension_results[d].score * COMPOSITE_WEIGHTS[d]
            for d in COMPOSITE_WEIGHTS
        )
        assert result.composite_score == pytest.approx(expected_composite, abs=0.01)

    def test_sub_category_override_does_not_change_other_dimensions(self):
        """An override of sub-category 1.1 must not change dimensions 2-5."""
        ov = _make_override_record("sub_category", "1.1", override_score=80.0)
        result = apply_overrides(self.original, [ov])

        for dim_id in ("2", "3", "4", "5"):
            assert result.dimension_results[dim_id].score == pytest.approx(
                self.original.dimension_results[dim_id].score, abs=0.01
            ), f"Dimension {dim_id} should be unchanged"

    def test_original_not_mutated_after_sub_category_override(self):
        ov = _make_override_record("sub_category", "2.1", override_score=90.0)
        apply_overrides(self.original, [ov])
        # Original sub-category score must remain 50.0.
        assert self.original.sub_category_results["2.1"].score == pytest.approx(50.0)

    # --- dimension override cascade ---

    def test_dimension_override_changes_dimension_score(self):
        ov = _make_override_record("dimension", "2", override_score=30.0)
        result = apply_overrides(self.original, [ov])
        assert result.dimension_results["2"].score == pytest.approx(30.0)

    def test_dimension_override_does_not_change_sub_category_scores(self):
        ov = _make_override_record("dimension", "2", override_score=30.0)
        result = apply_overrides(self.original, [ov])
        for sub_id in result.sub_category_results:
            assert result.sub_category_results[sub_id].score == pytest.approx(50.0)

    def test_dimension_override_recomputes_composite(self):
        """
        Dim 2 overridden to 30.0. All others remain 50.0.
        Expected composite = (50 + 30 + 50 + 50 + 50) / 5 = 46.0
        """
        ov = _make_override_record("dimension", "2", override_score=30.0)
        result = apply_overrides(self.original, [ov])
        assert result.composite_score == pytest.approx(46.0, abs=0.01)

    def test_dimension_override_classification_updated(self):
        """Override dim 1 to 10.0 — must be classified as Low Risk."""
        ov = _make_override_record("dimension", "1", override_score=10.0)
        result = apply_overrides(self.original, [ov])
        assert result.dimension_results["1"].classification == "Low Risk"

    # --- composite override ---

    def test_composite_override_changes_composite_only(self):
        ov = _make_override_record("composite", "composite", override_score=25.0)
        result = apply_overrides(self.original, [ov])
        assert result.composite_score == pytest.approx(25.0)

    def test_composite_override_does_not_change_dimensions(self):
        ov = _make_override_record("composite", "composite", override_score=25.0)
        result = apply_overrides(self.original, [ov])
        for dim_id in ("1", "2", "3", "4", "5"):
            assert result.dimension_results[dim_id].score == pytest.approx(50.0)

    def test_composite_override_classification_updated(self):
        """Override composite to 85.0 — must classify as Critical."""
        ov = _make_override_record("composite", "composite", override_score=85.0)
        result = apply_overrides(self.original, [ov])
        assert result.composite_classification == "Critical"

    # --- full cascade: all three types ---

    def test_full_cascade_all_three_types(self):
        """
        Apply sub-category, dimension, and composite overrides together.
        The final composite must equal the composite override value.
        The dimension override must appear on the correct dimension.
        """
        overrides = [
            _make_override_record(
                "sub_category", "3.3", override_score=90.0,
                applied_at="2025-04-01T10:00:00+00:00",
            ),
            _make_override_record(
                "dimension", "1", override_score=20.0,
                applied_at="2025-04-01T10:01:00+00:00",
            ),
            _make_override_record(
                "composite", "composite", override_score=55.0,
                applied_at="2025-04-01T10:02:00+00:00",
            ),
        ]
        result = apply_overrides(self.original, overrides)

        # Sub-category change reflected.
        assert result.sub_category_results["3.3"].score == pytest.approx(90.0)
        # Dimension override reflected.
        assert result.dimension_results["1"].score == pytest.approx(20.0)
        # Composite override is the final value.
        assert result.composite_score == pytest.approx(55.0)

    # --- multiple overrides on the same target (last one wins) ---

    def test_last_sub_category_override_wins(self):
        """
        Two overrides for the same sub-category applied in sequence.
        The later one (higher applied_at) must win.
        """
        overrides = [
            _make_override_record(
                "sub_category", "2.3", override_score=60.0,
                applied_at="2025-04-01T09:00:00+00:00",
            ),
            _make_override_record(
                "sub_category", "2.3", override_score=80.0,
                applied_at="2025-04-01T10:00:00+00:00",
            ),
        ]
        result = apply_overrides(self.original, overrides)
        assert result.sub_category_results["2.3"].score == pytest.approx(80.0)

    def test_last_composite_override_wins(self):
        overrides = [
            _make_override_record(
                "composite", "composite", override_score=40.0,
                applied_at="2025-04-01T09:00:00+00:00",
            ),
            _make_override_record(
                "composite", "composite", override_score=75.0,
                applied_at="2025-04-01T10:00:00+00:00",
            ),
        ]
        result = apply_overrides(self.original, overrides)
        assert result.composite_score == pytest.approx(75.0)
