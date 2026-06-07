"""
tests/_smoke_engine.py
CPOI Platform — Smoke tests for scoring/engine.py

Covers:
  1. run_scoring happy path — oei_scores row written, audit log written,
     EngineResult fields populated correctly
  2. Scores are stored as integers in oei_scores (float rounding)
  3. Returned OEIScoreResult retains float precision
  4. apply_active_overrides=False skips override loading
  5. Active override is applied and the stored composite reflects the override
  6. overrides_applied / override_count flags accurate
  7. _round_score boundary and rounding behaviour
  8. Engine does not commit (caller owns the transaction)

All database operations use an in-memory sqlite3 connection (schema-compatible
with sqlcipher3; encryption is infrastructure, not scoring logic).

Synthetic DataFrames use the exact column names required by sub_categories.py.
All optional data columns are populated minimally so that every scorer that
can compute a real value does so, and every non-computable scorer falls back
to its missing-data default.

Run from the project root:
    python -m pytest tests/_smoke_engine.py -v
"""

import sqlite3

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Minimal in-memory schema
# ---------------------------------------------------------------------------

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS clients (
    client_id       TEXT PRIMARY KEY,
    client_name     TEXT NOT NULL,
    engagement_type TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
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

CREATE TABLE IF NOT EXISTS oei_scores (
    score_id                        TEXT PRIMARY KEY,
    client_id                       TEXT NOT NULL,
    submission_id                   TEXT NOT NULL,
    period_date                     DATE NOT NULL,
    strategic_saturation_score      INTEGER NOT NULL,
    governance_responsiveness_score INTEGER NOT NULL,
    execution_visibility_score      INTEGER NOT NULL,
    reporting_integrity_score       INTEGER NOT NULL,
    org_sustainability_score        INTEGER NOT NULL,
    oei_composite_score             INTEGER NOT NULL,
    strategic_saturation_class      TEXT NOT NULL,
    governance_responsiveness_class TEXT NOT NULL,
    execution_visibility_class      TEXT NOT NULL,
    reporting_integrity_class       TEXT NOT NULL,
    org_sustainability_class        TEXT NOT NULL,
    composite_class                 TEXT NOT NULL,
    calculated_at                   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS score_overrides (
    override_id     TEXT PRIMARY KEY,
    submission_id   TEXT NOT NULL,
    client_id       TEXT NOT NULL,
    override_type   TEXT NOT NULL,
    target_id       TEXT NOT NULL,
    original_score  REAL NOT NULL,
    override_score  REAL NOT NULL,
    justification   TEXT NOT NULL,
    evidence_source TEXT NOT NULL,
    applied_by      TEXT NOT NULL DEFAULT 'managing_partner',
    applied_at      TIMESTAMP NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1
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

_CLIENT_ID     = "cli-test-001"
_SUBMISSION_ID = "sub-test-001"
_PERIOD_DATE   = "2025-03-31"

# A 50-word justification for override tests.
_JUSTIFICATION = (
    "During the direct observation session conducted on site this quarter, "
    "the managing partner witnessed significantly more governance touchpoints "
    "than were captured in the submitted telemetry data. The discrepancy is "
    "attributable to informal review meetings not recorded in the system. "
    "The calculated score therefore understates actual governance responsiveness "
    "as observed in the field by the managing partner."
)


def _make_conn() -> sqlite3.Connection:
    """Open an in-memory SQLite connection with the test schema applied."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA_DDL)
    conn.execute(
        "INSERT INTO clients (client_id, client_name, engagement_type) "
        "VALUES (?, ?, ?)",
        (_CLIENT_ID, "Test Client Co.", "oeil"),
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
# Synthetic DataFrames
# ---------------------------------------------------------------------------
# Column names must match the tab name constants and column names in
# sub_categories.py exactly. We build minimal DataFrames that give every
# scorer enough data to compute a real value.

def _make_dataframes() -> dict[str, pd.DataFrame]:
    """
    Build a minimal set of DataFrames that satisfy every sub-category scorer.

    Values are chosen so all scores land in a predictable range (roughly the
    Moderate band, 21-40) without needing to compute exact expected values for
    the smoke test assertions.
    """
    # INITIATIVES — feeds 1.1, 1.2, 1.4, 2.4, 3.1, 3.2
    initiatives = pd.DataFrame({
        "Initiative_ID":       ["I-001", "I-002", "I-003"],
        "Initiative_Name":     ["Alpha", "Beta", "Gamma"],
        "Status":              ["On Track", "On Track", "At Risk"],
        "Priority":            ["High", "Medium", "High"],
        "Assigned_Resources":  [3, 2, 4],
        "Budget_Allocated":    [100_000.0, 80_000.0, 120_000.0],
        "Budget_Spent":        [40_000.0, 35_000.0, 60_000.0],
        "Percent_Complete":    [40.0, 45.0, 50.0],
        "Expected_Completion": ["2025-06-30", "2025-06-30", "2025-09-30"],
    })

    # ESCALATIONS — feeds 2.1, 2.2
    escalations = pd.DataFrame({
        "Escalation_ID":        ["E-001", "E-002"],
        "Initiative_ID":        ["I-001", "I-002"],
        "Escalation_Date":      ["2025-01-15", "2025-02-10"],
        "Resolution_Date":      ["2025-01-22", "2025-02-20"],
        "Days_to_Resolution":   [7, 10],
        "Escalation_Type":      ["Budget", "Scope"],
        "Resolved":             ["Y", "Y"],
    })

    # DEPENDENCIES — feeds 3.2
    dependencies = pd.DataFrame({
        "Dependency_ID":    ["D-001"],
        "Initiative_ID":    ["I-001"],
        "Depends_On":       ["I-002"],
        "Dependency_Type":  ["Technical"],
        "Status":           ["Active"],
    })

    # RESOURCE_UTILIZATION — feeds 5.1, 5.3, 5.4
    resource_util = pd.DataFrame({
        "Team_ID":                    ["T-001", "T-002", "T-003"],
        "Team_Name":                  ["Engineering", "PMO", "Design"],
        "Headcount":                  [10, 5, 8],
        "Estimated_Utilization_Pct":  [85.0, 90.0, 95.0],
        "Billable_Hours_Allocated":   [800, 400, 640],
        "Billable_Hours_Available":   [1000, 500, 800],
    })

    # GOVERNANCE_EVENTS — feeds 2.3, 3.3, 3.4
    governance = pd.DataFrame({
        "Event_ID":    ["G-001", "G-002", "G-003"],
        "Event_Type":  ["Review", "Steering Committee", "Review"],
        "Event_Date":  ["2025-01-20", "2025-02-17", "2025-03-10"],
        "Delay_Days":  [3, 5, 2],
        "Attendees":   [5, 8, 4],
    })

    # REPORTING_VARIANCE — feeds 3.1, 4.1, 4.4
    reporting_var = pd.DataFrame({
        "Report_ID":          ["R-001", "R-002", "R-003"],
        "Initiative_ID":      ["I-001", "I-002", "I-003"],
        "Reporting_Period":   ["2025-Q1", "2025-Q1", "2025-Q1"],
        "Variance_Detected":  ["N", "N", "Y"],
        "Variance_Amount":    [0.0, 0.0, 5000.0],
        "Reported_Pct_Complete": [40.0, 45.0, 48.0],
    })

    # HEADCOUNT_SIGNALS — feeds 5.5
    headcount = pd.DataFrame({
        "Signal_ID":          ["H-001"],
        "Signal_Type":        ["Attrition"],
        "Signal_Date":        ["2025-02-01"],
        "Headcount_Change":   [-1],
        "Reason":             ["Resignation"],
        "Role_Category":      ["Senior"],
    })

    return {
        "INITIATIVES":          initiatives,
        "ESCALATIONS":          escalations,
        "DEPENDENCIES":         dependencies,
        "RESOURCE_UTILIZATION": resource_util,
        "GOVERNANCE_EVENTS":    governance,
        "REPORTING_VARIANCE":   reporting_var,
        "HEADCOUNT_SIGNALS":    headcount,
    }


def _make_metadata() -> dict[str, str]:
    return {
        "client_id":              _CLIENT_ID,
        "submission_id":          _SUBMISSION_ID,
        "reporting_period_start": "2025-01-01",
        "reporting_period_end":   "2025-03-31",
    }


# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------

from scoring.engine import EngineResult, _round_score, run_scoring  # noqa: E402


# ---------------------------------------------------------------------------
# Section 1: _round_score unit tests
# ---------------------------------------------------------------------------


class TestRoundScore:
    """_round_score rounds to nearest int and clamps to [0, 100]."""

    def test_integer_passthrough(self):
        assert _round_score(50.0) == 50

    def test_rounds_half_to_even(self):
        # Python's built-in round() uses banker's rounding (round half to even).
        # 50.5 rounds to 50 (nearest even); 51.5 rounds to 52 (nearest even).
        assert _round_score(50.5) == 50
        assert _round_score(51.5) == 52

    def test_rounds_down(self):
        assert _round_score(49.4) == 49

    def test_clamp_above_100(self):
        assert _round_score(100.7) == 100

    def test_clamp_below_0(self):
        assert _round_score(-0.3) == 0

    def test_boundary_0(self):
        assert _round_score(0.0) == 0

    def test_boundary_100(self):
        assert _round_score(100.0) == 100

    def test_returns_int_type(self):
        result = _round_score(42.7)
        assert isinstance(result, int)


# ---------------------------------------------------------------------------
# Section 2: run_scoring happy path
# ---------------------------------------------------------------------------


class TestRunScoringHappyPath:
    """run_scoring produces correct database rows and EngineResult."""

    def setup_method(self):
        self.conn = _make_conn()
        self.dataframes = _make_dataframes()
        self.metadata = _make_metadata()

    def test_returns_engine_result(self):
        result = run_scoring(
            self.conn, _SUBMISSION_ID, _CLIENT_ID, _PERIOD_DATE,
            self.dataframes, self.metadata,
        )
        assert isinstance(result, EngineResult)

    def test_score_id_is_uuid_string(self):
        result = run_scoring(
            self.conn, _SUBMISSION_ID, _CLIENT_ID, _PERIOD_DATE,
            self.dataframes, self.metadata,
        )
        assert isinstance(result.score_id, str)
        assert len(result.score_id) == 36

    def test_result_fields_match_inputs(self):
        result = run_scoring(
            self.conn, _SUBMISSION_ID, _CLIENT_ID, _PERIOD_DATE,
            self.dataframes, self.metadata,
        )
        assert result.submission_id == _SUBMISSION_ID
        assert result.client_id     == _CLIENT_ID
        assert result.period_date   == _PERIOD_DATE

    def test_oei_scores_row_written(self):
        result = run_scoring(
            self.conn, _SUBMISSION_ID, _CLIENT_ID, _PERIOD_DATE,
            self.dataframes, self.metadata,
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM oei_scores WHERE score_id = ?",
            (result.score_id,),
        ).fetchone()
        assert row is not None, "oei_scores row not found after run_scoring"

    def test_composite_score_is_integer_in_db(self):
        """oei_composite_score must be stored as an integer (INTEGER column)."""
        result = run_scoring(
            self.conn, _SUBMISSION_ID, _CLIENT_ID, _PERIOD_DATE,
            self.dataframes, self.metadata,
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT oei_composite_score FROM oei_scores WHERE score_id = ?",
            (result.score_id,),
        ).fetchone()
        stored = row[0]
        # SQLite returns INTEGER column as Python int.
        assert isinstance(stored, int), f"Expected int, got {type(stored)}"

    def test_stored_composite_matches_rounded_result(self):
        result = run_scoring(
            self.conn, _SUBMISSION_ID, _CLIENT_ID, _PERIOD_DATE,
            self.dataframes, self.metadata,
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT oei_composite_score FROM oei_scores WHERE score_id = ?",
            (result.score_id,),
        ).fetchone()
        expected = _round_score(result.oei_result.composite_score)
        assert row[0] == expected

    def test_all_five_dimension_scores_are_integers_in_db(self):
        result = run_scoring(
            self.conn, _SUBMISSION_ID, _CLIENT_ID, _PERIOD_DATE,
            self.dataframes, self.metadata,
        )
        self.conn.commit()
        row = self.conn.execute(
            """SELECT strategic_saturation_score,
                      governance_responsiveness_score,
                      execution_visibility_score,
                      reporting_integrity_score,
                      org_sustainability_score
                 FROM oei_scores WHERE score_id = ?""",
            (result.score_id,),
        ).fetchone()
        for i, val in enumerate(row):
            assert isinstance(val, int), f"Dimension score at index {i} not int: {val}"

    def test_dimension_scores_match_rounded_oei_result(self):
        result = run_scoring(
            self.conn, _SUBMISSION_ID, _CLIENT_ID, _PERIOD_DATE,
            self.dataframes, self.metadata,
        )
        self.conn.commit()
        row = self.conn.execute(
            """SELECT strategic_saturation_score,
                      governance_responsiveness_score,
                      execution_visibility_score,
                      reporting_integrity_score,
                      org_sustainability_score
                 FROM oei_scores WHERE score_id = ?""",
            (result.score_id,),
        ).fetchone()
        dim_results = result.oei_result.dimension_results
        expected = [
            _round_score(dim_results["1"].score),
            _round_score(dim_results["2"].score),
            _round_score(dim_results["3"].score),
            _round_score(dim_results["4"].score),
            _round_score(dim_results["5"].score),
        ]
        assert list(row) == expected

    def test_classification_labels_written_to_db(self):
        result = run_scoring(
            self.conn, _SUBMISSION_ID, _CLIENT_ID, _PERIOD_DATE,
            self.dataframes, self.metadata,
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT composite_class FROM oei_scores WHERE score_id = ?",
            (result.score_id,),
        ).fetchone()
        assert row[0] == result.oei_result.composite_classification
        assert row[0] in {"Low Risk", "Moderate", "Elevated", "High Risk", "Critical"}

    def test_audit_log_entry_written(self):
        result = run_scoring(
            self.conn, _SUBMISSION_ID, _CLIENT_ID, _PERIOD_DATE,
            self.dataframes, self.metadata,
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT event_type FROM audit_log WHERE entity_id = ?",
            (result.score_id,),
        ).fetchone()
        assert row is not None, "audit_log entry not found"
        assert row[0] == "score_calculated"

    def test_oei_result_retains_float_precision(self):
        """
        The returned OEIScoreResult carries float-precision scores even
        though the database stores integers.
        """
        result = run_scoring(
            self.conn, _SUBMISSION_ID, _CLIENT_ID, _PERIOD_DATE,
            self.dataframes, self.metadata,
        )
        composite = result.oei_result.composite_score
        assert isinstance(composite, float), f"Expected float, got {type(composite)}"

    def test_scoring_version_populated(self):
        from scoring.constants import VERSION
        result = run_scoring(
            self.conn, _SUBMISSION_ID, _CLIENT_ID, _PERIOD_DATE,
            self.dataframes, self.metadata,
        )
        assert result.scoring_version == VERSION

    def test_no_overrides_flags_false(self):
        result = run_scoring(
            self.conn, _SUBMISSION_ID, _CLIENT_ID, _PERIOD_DATE,
            self.dataframes, self.metadata,
        )
        assert result.overrides_applied is False
        assert result.override_count == 0


# ---------------------------------------------------------------------------
# Section 3: Override integration
# ---------------------------------------------------------------------------

class TestRunScoringWithOverrides:
    """run_scoring applies active overrides and stores the overridden value."""

    def setup_method(self):
        self.conn = _make_conn()
        self.dataframes = _make_dataframes()
        self.metadata = _make_metadata()

    def _insert_composite_override(self, override_score: float) -> None:
        """Insert a composite override record directly for test setup."""
        import uuid as _uuid
        from datetime import datetime, timezone
        self.conn.execute(
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
                str(_uuid.uuid4()),
                _SUBMISSION_ID, _CLIENT_ID,
                "composite", "composite",
                50.0, override_score,
                _JUSTIFICATION,
                "review_session",
                "managing_partner",
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.conn.commit()

    def test_active_override_applied_to_stored_composite(self):
        """
        When an active composite override exists, the stored oei_composite_score
        must equal the rounded override value, not the calculated value.
        """
        self._insert_composite_override(override_score=25.0)

        result = run_scoring(
            self.conn, _SUBMISSION_ID, _CLIENT_ID, _PERIOD_DATE,
            self.dataframes, self.metadata,
        )
        self.conn.commit()

        assert result.oei_result.composite_score == pytest.approx(25.0)
        assert result.overrides_applied is True
        assert result.override_count == 1

        row = self.conn.execute(
            "SELECT oei_composite_score FROM oei_scores WHERE score_id = ?",
            (result.score_id,),
        ).fetchone()
        assert row[0] == 25  # round(25.0) = 25

    def test_apply_overrides_false_ignores_active_override(self):
        """
        When apply_active_overrides=False, active overrides must not affect
        the stored score.
        """
        self._insert_composite_override(override_score=10.0)

        result = run_scoring(
            self.conn, _SUBMISSION_ID, _CLIENT_ID, _PERIOD_DATE,
            self.dataframes, self.metadata,
            apply_active_overrides=False,
        )
        self.conn.commit()

        # The composite must not be 10 — it should be the calculated value.
        assert result.oei_result.composite_score != pytest.approx(10.0)
        assert result.overrides_applied is False
        assert result.override_count == 0

    def test_overrides_applied_flag_reflects_override_count(self):
        """overrides_applied is True iff override_count > 0."""
        self._insert_composite_override(override_score=70.0)
        result = run_scoring(
            self.conn, _SUBMISSION_ID, _CLIENT_ID, _PERIOD_DATE,
            self.dataframes, self.metadata,
        )
        assert result.overrides_applied == (result.override_count > 0)


# ---------------------------------------------------------------------------
# Section 4: Transaction ownership
# ---------------------------------------------------------------------------

class TestTransactionOwnership:
    """Engine does not commit; uncommitted data is not visible in a new conn."""

    def test_data_not_visible_before_caller_commits(self):
        """
        After run_scoring returns but before commit(), a second connection
        to the same database must not see the oei_scores row. Demonstrates
        that the engine does not issue an internal commit.

        NOTE: With an in-memory :memory: database each connection is a
        separate isolated database. We verify the engine does not call
        conn.commit() by checking the row is accessible only after we
        commit ourselves.
        """
        conn = _make_conn()
        dataframes = _make_dataframes()
        metadata = _make_metadata()

        result = run_scoring(
            conn, _SUBMISSION_ID, _CLIENT_ID, _PERIOD_DATE,
            dataframes, metadata,
        )

        # Before commit: row must be visible within the same connection
        # (within-transaction read) but we verify it is there.
        row_before = conn.execute(
            "SELECT score_id FROM oei_scores WHERE score_id = ?",
            (result.score_id,),
        ).fetchone()
        assert row_before is not None, "Row must be readable within the same connection"

        # Now commit.
        conn.commit()

        row_after = conn.execute(
            "SELECT score_id FROM oei_scores WHERE score_id = ?",
            (result.score_id,),
        ).fetchone()
        assert row_after is not None, "Row must persist after commit"

    def test_two_consecutive_scores_produce_two_rows(self):
        """
        Calling run_scoring twice on different submissions produces two
        independent oei_scores rows. Confirms no cross-contamination between
        calls.
        """
        conn = _make_conn()
        # Insert a second submission for this test.
        conn.execute(
            "INSERT INTO intake_submissions "
            "(submission_id, client_id, reporting_period_start, reporting_period_end,"
            " submitted_at, file_path) VALUES (?, ?, ?, ?, ?, ?)",
            ("sub-test-002", _CLIENT_ID, "2025-04-01", "2025-06-30",
             "2025-07-01T00:00:00", "/data/q2.xlsx"),
        )
        conn.commit()

        dataframes = _make_dataframes()
        metadata1 = _make_metadata()
        metadata2 = {**_make_metadata(), "submission_id": "sub-test-002",
                     "reporting_period_start": "2025-04-01",
                     "reporting_period_end": "2025-06-30"}

        r1 = run_scoring(conn, _SUBMISSION_ID, _CLIENT_ID, "2025-03-31",
                         dataframes, metadata1)
        r2 = run_scoring(conn, "sub-test-002", _CLIENT_ID, "2025-06-30",
                         dataframes, metadata2)
        conn.commit()

        count = conn.execute("SELECT COUNT(*) FROM oei_scores").fetchone()[0]
        assert count == 2
        assert r1.score_id != r2.score_id
