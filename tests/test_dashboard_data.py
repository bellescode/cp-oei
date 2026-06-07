"""
tests/test_dashboard_data.py
CPOI Platform -- Tests for dashboard data query logic.

Uses an in-memory SQLite (not SQLCipher) database seeded with minimal
fixture data. No Streamlit imports. Tests verify that the SQL queries
and Python logic used by the dashboard pages return correct shapes and
values.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ------------------------------------------------------------------
# Fixture: in-memory DB with minimal schema
# ------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE clients (
    client_id TEXT PRIMARY KEY,
    client_name TEXT NOT NULL,
    engagement_type TEXT NOT NULL DEFAULT 'advisory',
    engagement_start_date TEXT,
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE intake_submissions (
    submission_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    reporting_period_end TEXT,
    ingestion_status TEXT DEFAULT 'processed'
);

CREATE TABLE oei_scores (
    score_id TEXT PRIMARY KEY,
    submission_id TEXT,
    client_id TEXT NOT NULL,
    period_date TEXT NOT NULL,
    oei_composite_score REAL,
    composite_class TEXT,
    strategic_saturation_score REAL,
    strategic_saturation_class TEXT,
    governance_responsiveness_score REAL,
    governance_responsiveness_class TEXT,
    execution_visibility_score REAL,
    execution_visibility_class TEXT,
    reporting_integrity_score REAL,
    reporting_integrity_class TEXT,
    org_sustainability_score REAL,
    org_sustainability_class TEXT
);

CREATE TABLE alerts (
    alert_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    signal_name TEXT,
    alert_severity TEXT,
    alert_message TEXT,
    triggered_at TEXT,
    acknowledged INTEGER DEFAULT 0,
    acknowledged_at TEXT
);

CREATE TABLE signal_readings (
    reading_id TEXT PRIMARY KEY,
    submission_id TEXT NOT NULL,
    signal_name TEXT NOT NULL,
    signal_value REAL NOT NULL,
    threshold_value REAL NOT NULL,
    threshold_breached INTEGER DEFAULT 0
);
"""


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)

    # Two clients
    conn.execute(
        "INSERT INTO clients VALUES (?,?,?,?,?)",
        ("c1", "Alpha Corp", "advisory", "2024-01-01", "active"),
    )
    conn.execute(
        "INSERT INTO clients VALUES (?,?,?,?,?)",
        ("c2", "Beta LLC", "fractional", "2024-06-01", "active"),
    )

    # Submissions
    conn.execute(
        "INSERT INTO intake_submissions VALUES (?,?,?,?)",
        ("sub1a", "c1", "2024-03-31", "processed"),
    )
    conn.execute(
        "INSERT INTO intake_submissions VALUES (?,?,?,?)",
        ("sub1b", "c1", "2024-06-30", "processed"),
    )
    conn.execute(
        "INSERT INTO intake_submissions VALUES (?,?,?,?)",
        ("sub2a", "c2", "2024-03-31", "processed"),
    )

    # OEI scores: c1 has 2 periods; c2 has 1 period
    def _scores(score_id, sub_id, client_id, period_date, composite, cls, dim=60.0):
        conn.execute(
            """INSERT INTO oei_scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                score_id, sub_id, client_id, period_date,
                composite, cls,
                dim, "Moderate",
                dim, "Moderate",
                dim, "Moderate",
                dim, "Moderate",
                dim, "Moderate",
            ),
        )

    _scores("s1a", "sub1a", "c1", "2024-03-31", 65.0, "Moderate")
    _scores("s1b", "sub1b", "c1", "2024-06-30", 55.0, "Elevated")
    _scores("s2a", "sub2a", "c2", "2024-03-31", 72.0, "Low Risk")

    # Alerts: c1 has 1 unacknowledged + 1 acknowledged; c2 has none
    conn.execute(
        "INSERT INTO alerts VALUES (?,?,?,?,?,?,?,?)",
        ("a1", "c1", "governance_latency_index", "HIGH", "Threshold breached", "2024-06-15", 0, None),
    )
    conn.execute(
        "INSERT INTO alerts VALUES (?,?,?,?,?,?,?,?)",
        ("a2", "c1", "false_green_indicator", "WATCH", "Minor breach", "2024-05-01", 1, "2024-05-02"),
    )

    # Signal readings for sub1b
    conn.execute(
        "INSERT INTO signal_readings VALUES (?,?,?,?,?,?)",
        ("r1", "sub1b", "governance_latency_index", 0.85, 0.50, 1),
    )
    conn.execute(
        "INSERT INTO signal_readings VALUES (?,?,?,?,?,?)",
        ("r2", "sub1b", "false_green_indicator", 0.30, 0.40, 0),
    )

    conn.commit()
    yield conn
    conn.close()


# ------------------------------------------------------------------
# Test: Client Overview query -- current + prior scores
# ------------------------------------------------------------------

def test_client_overview_query(db):
    rows = db.execute(
        """
        SELECT
            c.client_id,
            c.client_name,
            s.oei_composite_score AS current_score,
            s.composite_class AS classification,
            s.period_date AS last_period,
            prev.oei_composite_score AS prior_score
        FROM clients c
        LEFT JOIN (
            SELECT client_id, oei_composite_score, composite_class, period_date
            FROM oei_scores
            WHERE (client_id, period_date) IN (
                SELECT client_id, MAX(period_date) FROM oei_scores GROUP BY client_id
            )
        ) s ON s.client_id = c.client_id
        LEFT JOIN (
            SELECT o.client_id, o.oei_composite_score, o.period_date
            FROM oei_scores o
            WHERE o.period_date = (
                SELECT MAX(o2.period_date) FROM oei_scores o2
                WHERE o2.client_id = o.client_id
                  AND o2.period_date < (
                      SELECT MAX(o3.period_date) FROM oei_scores o3
                      WHERE o3.client_id = o.client_id
                  )
            )
        ) prev ON prev.client_id = c.client_id
        WHERE c.status = 'active'
        ORDER BY c.client_name
        """
    ).fetchall()

    assert len(rows) == 2

    alpha = next(r for r in rows if r["client_id"] == "c1")
    assert alpha["current_score"] == 55.0
    assert alpha["classification"] == "Elevated"
    assert alpha["last_period"] == "2024-06-30"
    assert alpha["prior_score"] == 65.0


def test_client_overview_no_prior(db):
    rows = db.execute(
        """
        SELECT
            c.client_id,
            prev.oei_composite_score AS prior_score
        FROM clients c
        LEFT JOIN (
            SELECT o.client_id, o.oei_composite_score
            FROM oei_scores o
            WHERE o.period_date = (
                SELECT MAX(o2.period_date) FROM oei_scores o2
                WHERE o2.client_id = o.client_id
                  AND o2.period_date < (
                      SELECT MAX(o3.period_date) FROM oei_scores o3
                      WHERE o3.client_id = o.client_id
                  )
            )
        ) prev ON prev.client_id = c.client_id
        WHERE c.client_id = 'c2'
        """
    ).fetchall()

    assert len(rows) == 1
    assert rows[0]["prior_score"] is None


# ------------------------------------------------------------------
# Test: Alert count map
# ------------------------------------------------------------------

def test_alert_count_map(db):
    rows = db.execute(
        "SELECT client_id, COUNT(*) AS alert_count FROM alerts WHERE acknowledged = 0 GROUP BY client_id"
    ).fetchall()
    alert_map = {r["client_id"]: r["alert_count"] for r in rows}

    assert alert_map.get("c1") == 1
    assert alert_map.get("c2", 0) == 0


# ------------------------------------------------------------------
# Test: Signal severity classification (Python logic, not SQL)
# ------------------------------------------------------------------

def _classify_severity(signal_value: float, threshold: float, breached: bool) -> str:
    if not breached:
        return "CLEAR"
    excess = signal_value - threshold
    if excess >= threshold * 0.5:
        return "CRITICAL"
    elif excess >= threshold * 0.2:
        return "ELEVATED"
    else:
        return "WATCH"


def test_signal_severity_critical():
    # value=0.85, threshold=0.50 → excess=0.35 >= 0.25 (50% of 0.50) → CRITICAL
    assert _classify_severity(0.85, 0.50, True) == "CRITICAL"


def test_signal_severity_elevated():
    # value=0.65, threshold=0.50 → excess=0.15 >= 0.10 (20% of 0.50) but < 0.25 → ELEVATED
    assert _classify_severity(0.65, 0.50, True) == "ELEVATED"


def test_signal_severity_watch():
    # value=0.55, threshold=0.50 → excess=0.05 < 0.10 → WATCH
    assert _classify_severity(0.55, 0.50, True) == "WATCH"


def test_signal_severity_clear():
    assert _classify_severity(0.30, 0.40, False) == "CLEAR"


# ------------------------------------------------------------------
# Test: Scorecard delta calculation
# ------------------------------------------------------------------

def test_scorecard_delta_negative():
    prior_score = 60.0
    current_score = 45.0
    delta = current_score - prior_score
    sign = "+" if delta > 0 else ""
    delta_str = f"{sign}{delta}" if delta is not None else "--"
    assert delta == -15.0
    assert delta_str == "-15.0"


def test_scorecard_delta_positive():
    prior_score = 60.0
    current_score = 75.0
    delta = current_score - prior_score
    sign = "+" if delta > 0 else ""
    delta_str = f"{sign}{delta}"
    assert delta_str == "+15.0"


def test_scorecard_delta_no_prior():
    prior_score = None
    delta = None if prior_score is None else 0
    delta_str = str(delta) if delta is not None else "--"
    assert delta_str == "--"
