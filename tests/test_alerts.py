"""
tests/test_alerts.py
CPOI Platform -- Unit tests for Module 4: Alert and Notification Engine

Coverage:
  alerts/detector.py  -- _classify_signal_severity, create_alerts_for_submission
  alerts/narrator.py  -- anonymize_for_ai, draft_alert_message
  alerts/sender.py    -- _build_subject, _build_plain_body, _build_html_body,
                         send_alert_email
  alerts/queue.py     -- CircuitBreaker, process_send_queue

All tests that touch the database use a fresh encrypted SQLite database
created in a pytest tmp_path directory. No test shares state with another.
SendGrid is never called live; the send path is patched in all sender and
queue tests.
"""

import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.audit import write_audit_log
from db.init_db import initialize_database, open_connection

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

TEST_KEY = "cpoi-test-key-alerts"
TEST_RECIPIENT = "test@criterion-partners.com"
TEST_SENDER = "intelligence@criterion-partners.com"


@pytest.fixture()
def conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Fresh encrypted DB connection for each test."""
    monkeypatch.setenv("CPOI_DB_KEY", TEST_KEY)
    db_path = tmp_path / "test_alerts.db"
    initialize_database(db_path)
    connection = open_connection(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    yield connection
    connection.close()


def _insert_client(conn: Any, name: str = "Acme Corp") -> str:
    client_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO clients (client_id, client_name, engagement_type) VALUES (?,?,?)",
        (client_id, name, "oeil"),
    )
    return client_id


def _insert_submission(conn: Any, client_id: str, period_end: str = "2026-01-31") -> str:
    sub_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO intake_submissions
           (submission_id, client_id, reporting_period_start, reporting_period_end,
            submitted_at, file_path, ingestion_status)
           VALUES (?,?,?,?,?,?,?)""",
        (sub_id, client_id, "2026-01-01", period_end,
         "2026-02-01T00:00:00", "/tmp/t.xlsx", "processed"),
    )
    return sub_id


def _insert_reading(
    conn: Any,
    sub_id: str,
    client_id: str,
    signal_name: str,
    signal_value: float,
    threshold_value: float,
    threshold_breached: bool,
    period_date: str = "2026-01-31",
) -> str:
    r_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO signal_readings
           (reading_id, submission_id, client_id, signal_name, signal_value,
            threshold_value, threshold_breached, period_date)
           VALUES (?,?,?,?,?,?,?,?)""",
        (r_id, sub_id, client_id, signal_name, signal_value,
         threshold_value, int(threshold_breached), period_date),
    )
    return r_id


def _insert_alert_with_message(
    conn: Any,
    client_id: str,
    sub_id: str,
    reading_id: str,
    signal_name: str,
    signal_value: float,
    threshold_value: float,
    severity: str,
    message: str = "Pre-drafted message.",
    email_sent: bool = False,
) -> str:
    a_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO alerts
           (alert_id, client_id, submission_id, reading_id, signal_name,
            signal_value, threshold_value, alert_severity, triggered_at,
            email_sent, acknowledged, alert_message)
           VALUES (?,?,?,?,?,?,?,?,?,?,FALSE,?)""",
        (a_id, client_id, sub_id, reading_id, signal_name,
         signal_value, threshold_value, severity,
         "2026-02-01T00:00:00", int(email_sent), message),
    )
    return a_id


# ===========================================================================
# DETECTOR TESTS
# ===========================================================================

class TestClassifySignalSeverity:
    """Unit tests for alerts.detector._classify_signal_severity."""

    def setup_method(self):
        from alerts.detector import _classify_signal_severity
        self.fn = _classify_signal_severity

    @pytest.mark.parametrize("signal,value,expected", [
        # governance_latency_index
        ("governance_latency_index",  9.9,  "watch"),
        ("governance_latency_index", 10.0,  "watch"),
        ("governance_latency_index", 14.9,  "watch"),
        ("governance_latency_index", 15.0,  "elevated"),
        ("governance_latency_index", 20.9,  "elevated"),
        ("governance_latency_index", 21.0,  "critical"),
        ("governance_latency_index", 30.0,  "critical"),
        # escalation_suppression_rate
        ("escalation_suppression_rate", 0.60, "watch"),
        ("escalation_suppression_rate", 0.69, "watch"),
        ("escalation_suppression_rate", 0.70, "elevated"),
        ("escalation_suppression_rate", 0.79, "elevated"),
        ("escalation_suppression_rate", 0.80, "critical"),
        # false_green_indicator -- any breach is critical per spec
        ("false_green_indicator", 1.0, "critical"),
        ("false_green_indicator", 5.0, "critical"),
        # reporting_divergence_score
        ("reporting_divergence_score", 0.30, "watch"),
        ("reporting_divergence_score", 0.45, "elevated"),
        ("reporting_divergence_score", 0.60, "critical"),
        # priority_collision_index
        ("priority_collision_index", 2.0, "watch"),
        ("priority_collision_index", 4.0, "elevated"),
        ("priority_collision_index", 6.0, "critical"),
        # platform_utilization_pressure
        ("platform_utilization_pressure", 120.0, "watch"),
        ("platform_utilization_pressure", 135.0, "elevated"),
        ("platform_utilization_pressure", 150.0, "critical"),
        # initiative_saturation_ratio
        ("initiative_saturation_ratio", 3.0, "watch"),
        ("initiative_saturation_ratio", 4.0, "elevated"),
        ("initiative_saturation_ratio", 5.0, "critical"),
        # dependency_fragility_score
        ("dependency_fragility_score", 5.0, "watch"),
        ("dependency_fragility_score", 8.0, "elevated"),
        ("dependency_fragility_score", 12.0, "critical"),
        # headcount_stability_index
        ("headcount_stability_index", 4.0, "watch"),
        ("headcount_stability_index", 7.0, "elevated"),
        ("headcount_stability_index", 10.0, "critical"),
        # reprioritization_frequency
        ("reprioritization_frequency", 0.70, "watch"),
        ("reprioritization_frequency", 0.80, "elevated"),
        ("reprioritization_frequency", 0.90, "critical"),
        # reactive_work_ratio
        ("reactive_work_ratio", 0.30, "watch"),
        ("reactive_work_ratio", 0.40, "elevated"),
        ("reactive_work_ratio", 0.50, "critical"),
    ])
    def test_severity_boundary(self, signal, value, expected):
        assert self.fn(signal, value) == expected

    def test_unknown_signal_defaults_to_watch(self):
        assert self.fn("nonexistent_signal_xyz", 9999.0) == "watch"

    def test_return_type_is_str(self):
        result = self.fn("governance_latency_index", 22.0)
        assert isinstance(result, str)

    def test_all_valid_return_values_are_in_vocabulary(self):
        valid = {"watch", "elevated", "critical"}
        for value in [10.0, 15.0, 21.0]:
            assert self.fn("governance_latency_index", value) in valid


class TestCreateAlertsForSubmission:
    """Unit tests for alerts.detector.create_alerts_for_submission."""

    def test_returns_empty_list_when_no_breaches(self, conn):
        from alerts.detector import create_alerts_for_submission
        client_id = _insert_client(conn)
        sub_id = _insert_submission(conn, client_id)
        _insert_reading(conn, sub_id, client_id, "reactive_work_ratio",
                        0.10, 0.30, False)
        conn.commit()
        result = create_alerts_for_submission(conn, sub_id, client_id, "2026-01-31")
        assert result == []

    def test_creates_one_alert_per_breached_reading(self, conn):
        from alerts.detector import create_alerts_for_submission
        client_id = _insert_client(conn)
        sub_id = _insert_submission(conn, client_id)
        _insert_reading(conn, sub_id, client_id, "governance_latency_index",
                        22.0, 10.0, True)
        _insert_reading(conn, sub_id, client_id, "false_green_indicator",
                        2.0, 1.0, True)
        _insert_reading(conn, sub_id, client_id, "reactive_work_ratio",
                        0.10, 0.30, False)
        conn.commit()
        result = create_alerts_for_submission(conn, sub_id, client_id, "2026-01-31")
        assert len(result) == 2

    def test_non_breached_reading_does_not_produce_alert(self, conn):
        from alerts.detector import create_alerts_for_submission
        client_id = _insert_client(conn)
        sub_id = _insert_submission(conn, client_id)
        _insert_reading(conn, sub_id, client_id, "reactive_work_ratio",
                        0.10, 0.30, False)
        conn.commit()
        result = create_alerts_for_submission(conn, sub_id, client_id, "2026-01-31")
        count = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        assert count == 0
        assert result == []

    def test_alert_ids_are_valid_uuids(self, conn):
        from alerts.detector import create_alerts_for_submission
        client_id = _insert_client(conn)
        sub_id = _insert_submission(conn, client_id)
        _insert_reading(conn, sub_id, client_id, "governance_latency_index",
                        22.0, 10.0, True)
        conn.commit()
        result = create_alerts_for_submission(conn, sub_id, client_id, "2026-01-31")
        for alert_id in result:
            parsed = uuid.UUID(alert_id)
            assert str(parsed) == alert_id

    def test_alert_row_has_correct_fields(self, conn):
        from alerts.detector import create_alerts_for_submission
        client_id = _insert_client(conn)
        sub_id = _insert_submission(conn, client_id)
        r_id = _insert_reading(conn, sub_id, client_id, "governance_latency_index",
                                22.0, 10.0, True)
        conn.commit()
        result = create_alerts_for_submission(conn, sub_id, client_id, "2026-01-31")
        conn.commit()
        row = conn.execute(
            """SELECT alert_id, client_id, submission_id, reading_id,
                      signal_name, signal_value, threshold_value,
                      alert_severity, email_sent, acknowledged
               FROM alerts WHERE alert_id = ?""",
            (result[0],),
        ).fetchone()
        assert row is not None
        assert row[1] == client_id
        assert row[2] == sub_id
        assert row[3] == r_id
        assert row[4] == "governance_latency_index"
        assert float(row[5]) == 22.0
        assert float(row[6]) == 10.0
        assert row[7] == "critical"   # 22.0 >= 21.0 = critical
        assert row[8] == 0            # email_sent = FALSE
        assert row[9] == 0            # acknowledged = FALSE

    def test_email_sent_defaults_to_false(self, conn):
        from alerts.detector import create_alerts_for_submission
        client_id = _insert_client(conn)
        sub_id = _insert_submission(conn, client_id)
        _insert_reading(conn, sub_id, client_id, "governance_latency_index",
                        22.0, 10.0, True)
        conn.commit()
        result = create_alerts_for_submission(conn, sub_id, client_id, "2026-01-31")
        conn.commit()
        row = conn.execute(
            "SELECT email_sent FROM alerts WHERE alert_id = ?", (result[0],)
        ).fetchone()
        assert row[0] == 0

    def test_acknowledged_defaults_to_false(self, conn):
        from alerts.detector import create_alerts_for_submission
        client_id = _insert_client(conn)
        sub_id = _insert_submission(conn, client_id)
        _insert_reading(conn, sub_id, client_id, "false_green_indicator",
                        2.0, 1.0, True)
        conn.commit()
        result = create_alerts_for_submission(conn, sub_id, client_id, "2026-01-31")
        conn.commit()
        row = conn.execute(
            "SELECT acknowledged FROM alerts WHERE alert_id = ?", (result[0],)
        ).fetchone()
        assert row[0] == 0

    def test_writes_audit_log_entry_per_alert(self, conn):
        from alerts.detector import create_alerts_for_submission
        client_id = _insert_client(conn)
        sub_id = _insert_submission(conn, client_id)
        _insert_reading(conn, sub_id, client_id, "governance_latency_index",
                        22.0, 10.0, True)
        _insert_reading(conn, sub_id, client_id, "false_green_indicator",
                        2.0, 1.0, True)
        conn.commit()
        result = create_alerts_for_submission(conn, sub_id, client_id, "2026-01-31")
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE event_type = 'alert_created'"
        ).fetchone()[0]
        assert count == 2

    def test_audit_log_entity_id_matches_alert_id(self, conn):
        from alerts.detector import create_alerts_for_submission
        client_id = _insert_client(conn)
        sub_id = _insert_submission(conn, client_id)
        _insert_reading(conn, sub_id, client_id, "governance_latency_index",
                        22.0, 10.0, True)
        conn.commit()
        result = create_alerts_for_submission(conn, sub_id, client_id, "2026-01-31")
        conn.commit()
        row = conn.execute(
            "SELECT entity_id FROM audit_log WHERE event_type = 'alert_created'"
        ).fetchone()
        assert row[0] == result[0]

    def test_idempotency_second_call_creates_no_new_alerts(self, conn):
        from alerts.detector import create_alerts_for_submission
        client_id = _insert_client(conn)
        sub_id = _insert_submission(conn, client_id)
        _insert_reading(conn, sub_id, client_id, "governance_latency_index",
                        22.0, 10.0, True)
        conn.commit()
        create_alerts_for_submission(conn, sub_id, client_id, "2026-01-31")
        conn.commit()
        result2 = create_alerts_for_submission(conn, sub_id, client_id, "2026-01-31")
        conn.commit()
        assert result2 == []
        total = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        assert total == 1

    def test_idempotency_total_alert_count_unchanged(self, conn):
        from alerts.detector import create_alerts_for_submission
        client_id = _insert_client(conn)
        sub_id = _insert_submission(conn, client_id)
        _insert_reading(conn, sub_id, client_id, "governance_latency_index",
                        22.0, 10.0, True)
        _insert_reading(conn, sub_id, client_id, "false_green_indicator",
                        1.0, 1.0, True)
        conn.commit()
        create_alerts_for_submission(conn, sub_id, client_id, "2026-01-31")
        conn.commit()
        create_alerts_for_submission(conn, sub_id, client_id, "2026-01-31")
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        assert total == 2

    def test_different_submissions_produce_independent_alerts(self, conn):
        from alerts.detector import create_alerts_for_submission
        client_id = _insert_client(conn)
        sub1 = _insert_submission(conn, client_id, "2026-01-31")
        sub2 = _insert_submission(conn, client_id, "2026-02-28")
        _insert_reading(conn, sub1, client_id, "governance_latency_index",
                        22.0, 10.0, True)
        _insert_reading(conn, sub2, client_id, "governance_latency_index",
                        18.0, 10.0, True)
        conn.commit()
        r1 = create_alerts_for_submission(conn, sub1, client_id, "2026-01-31")
        r2 = create_alerts_for_submission(conn, sub2, client_id, "2026-02-28")
        conn.commit()
        assert len(r1) == 1
        assert len(r2) == 1
        assert r1[0] != r2[0]


# ===========================================================================
# NARRATOR TESTS
# ===========================================================================

class TestAnonymizeForAi:
    """Unit tests for alerts.narrator.anonymize_for_ai."""

    def test_client_name_replaced_with_CLIENT_A(self):
        from alerts.narrator import anonymize_for_ai
        result = anonymize_for_ai({"client_name": "Acme Corp"})
        assert result["client_name"] == "CLIENT_A"

    def test_program_names_replaced_with_placeholders(self):
        from alerts.narrator import anonymize_for_ai
        result = anonymize_for_ai({
            "client_name": "X",
            "program_names": ["Alpha", "Beta", "Gamma"],
        })
        assert result["program_names"] == ["PROGRAM_0", "PROGRAM_1", "PROGRAM_2"]

    def test_owner_names_replaced_with_OWNER_REDACTED(self):
        from alerts.narrator import anonymize_for_ai
        result = anonymize_for_ai({
            "client_name": "X",
            "owner_names": ["Jane Doe", "John Smith"],
        })
        assert result["owner_names"] == ["OWNER_REDACTED", "OWNER_REDACTED"]

    def test_non_pii_fields_preserved(self):
        from alerts.narrator import anonymize_for_ai
        result = anonymize_for_ai({
            "client_name": "X",
            "oei_composite": 75,
            "signal_value": 22.0,
            "severity": "critical",
        })
        assert result["oei_composite"] == 75
        assert result["signal_value"] == 22.0
        assert result["severity"] == "critical"

    def test_original_dict_not_mutated(self):
        from alerts.narrator import anonymize_for_ai
        original = {
            "client_name": "Acme Corp",
            "program_names": ["Alpha", "Beta"],
            "owner_names": ["Jane"],
        }
        anonymize_for_ai(original)
        assert original["client_name"] == "Acme Corp"
        assert original["program_names"] == ["Alpha", "Beta"]
        assert original["owner_names"] == ["Jane"]

    def test_missing_program_names_produces_empty_list(self):
        from alerts.narrator import anonymize_for_ai
        result = anonymize_for_ai({"client_name": "X"})
        assert result["program_names"] == []

    def test_missing_owner_names_produces_empty_list(self):
        from alerts.narrator import anonymize_for_ai
        result = anonymize_for_ai({"client_name": "X"})
        assert result["owner_names"] == []

    def test_empty_program_names_list_stays_empty(self):
        from alerts.narrator import anonymize_for_ai
        result = anonymize_for_ai({"client_name": "X", "program_names": []})
        assert result["program_names"] == []

    def test_single_program_name_produces_PROGRAM_0(self):
        from alerts.narrator import anonymize_for_ai
        result = anonymize_for_ai({"client_name": "X", "program_names": ["Only"]})
        assert result["program_names"] == ["PROGRAM_0"]

    def test_real_name_absent_from_result(self):
        from alerts.narrator import anonymize_for_ai
        result = anonymize_for_ai({
            "client_name": "Acme Corp",
            "program_names": ["Alpha Initiative"],
            "owner_names": ["Jane Doe"],
        })
        assert "Acme Corp" not in str(result)
        assert "Alpha Initiative" not in str(result)
        assert "Jane Doe" not in str(result)


class TestDraftAlertMessage:
    """Unit tests for alerts.narrator.draft_alert_message."""

    def test_returns_string(self, conn, monkeypatch):
        from alerts.narrator import draft_alert_message
        monkeypatch.setenv("CPOI_OLLAMA_URL", "http://localhost:19999/api/generate")
        client_id = _insert_client(conn)
        sub_id = _insert_submission(conn, client_id)
        conn.commit()
        result = draft_alert_message(
            conn=conn, alert_id=str(uuid.uuid4()),
            signal_name="governance_latency_index",
            signal_value=22.0, threshold_value=10.0,
            severity="critical", client_name="Acme Corp",
            context={"client_name": "Acme Corp"},
        )
        assert isinstance(result, str)

    def test_fallback_fires_when_model_unreachable(self, conn, monkeypatch):
        from alerts.narrator import draft_alert_message
        monkeypatch.setenv("CPOI_OLLAMA_URL", "http://localhost:19999/api/generate")
        client_id = _insert_client(conn)
        _insert_submission(conn, client_id)
        conn.commit()
        result = draft_alert_message(
            conn=conn, alert_id=str(uuid.uuid4()),
            signal_name="governance_latency_index",
            signal_value=22.0, threshold_value=10.0,
            severity="critical", client_name="Acme Corp",
            context={"client_name": "Acme Corp"},
        )
        assert len(result) >= 40

    def test_real_client_name_absent_from_fallback_message(self, conn, monkeypatch):
        from alerts.narrator import draft_alert_message
        monkeypatch.setenv("CPOI_OLLAMA_URL", "http://localhost:19999/api/generate")
        client_id = _insert_client(conn)
        _insert_submission(conn, client_id)
        conn.commit()
        result = draft_alert_message(
            conn=conn, alert_id=str(uuid.uuid4()),
            signal_name="governance_latency_index",
            signal_value=22.0, threshold_value=10.0,
            severity="critical", client_name="Acme Corp",
            context={"client_name": "Acme Corp", "owner_names": ["Jane Doe"]},
        )
        assert "Acme Corp" not in result
        assert "Jane Doe" not in result

    def test_fallback_message_contains_signal_name(self, conn, monkeypatch):
        from alerts.narrator import draft_alert_message
        monkeypatch.setenv("CPOI_OLLAMA_URL", "http://localhost:19999/api/generate")
        client_id = _insert_client(conn)
        _insert_submission(conn, client_id)
        conn.commit()
        result = draft_alert_message(
            conn=conn, alert_id=str(uuid.uuid4()),
            signal_name="governance_latency_index",
            signal_value=22.0, threshold_value=10.0,
            severity="critical", client_name="Acme Corp",
            context={},
        )
        # The fallback constructs signal display name from snake_case
        assert "Governance Latency Index" in result

    def test_audit_log_entry_written(self, conn, monkeypatch):
        from alerts.narrator import draft_alert_message
        monkeypatch.setenv("CPOI_OLLAMA_URL", "http://localhost:19999/api/generate")
        alert_id = str(uuid.uuid4())
        client_id = _insert_client(conn)
        _insert_submission(conn, client_id)
        conn.commit()
        draft_alert_message(
            conn=conn, alert_id=alert_id,
            signal_name="governance_latency_index",
            signal_value=22.0, threshold_value=10.0,
            severity="critical", client_name="Acme Corp",
            context={},
        )
        row = conn.execute(
            "SELECT event_type, entity_id FROM audit_log "
            "WHERE event_type = 'alert_message_drafted'"
        ).fetchone()
        assert row is not None
        assert row[1] == alert_id

    def test_audit_log_has_prompt_hash(self, conn, monkeypatch):
        from alerts.narrator import draft_alert_message
        monkeypatch.setenv("CPOI_OLLAMA_URL", "http://localhost:19999/api/generate")
        alert_id = str(uuid.uuid4())
        client_id = _insert_client(conn)
        _insert_submission(conn, client_id)
        conn.commit()
        draft_alert_message(
            conn=conn, alert_id=alert_id,
            signal_name="governance_latency_index",
            signal_value=22.0, threshold_value=10.0,
            severity="critical", client_name="Acme Corp",
            context={},
        )
        row = conn.execute(
            "SELECT metadata FROM audit_log WHERE event_type = 'alert_message_drafted'"
        ).fetchone()
        meta = json.loads(row[0])
        assert "prompt_hash" in meta
        assert len(meta["prompt_hash"]) == 64   # SHA-256 hex digest

    def test_audit_log_records_fallback_flag_true(self, conn, monkeypatch):
        from alerts.narrator import draft_alert_message
        monkeypatch.setenv("CPOI_OLLAMA_URL", "http://localhost:19999/api/generate")
        client_id = _insert_client(conn)
        _insert_submission(conn, client_id)
        conn.commit()
        draft_alert_message(
            conn=conn, alert_id=str(uuid.uuid4()),
            signal_name="governance_latency_index",
            signal_value=22.0, threshold_value=10.0,
            severity="critical", client_name="Acme Corp",
            context={},
        )
        row = conn.execute(
            "SELECT metadata FROM audit_log WHERE event_type = 'alert_message_drafted'"
        ).fetchone()
        meta = json.loads(row[0])
        assert meta["used_fallback"] is True

    def test_audit_log_records_client_name_in_metadata_only(self, conn, monkeypatch):
        from alerts.narrator import draft_alert_message
        monkeypatch.setenv("CPOI_OLLAMA_URL", "http://localhost:19999/api/generate")
        client_id = _insert_client(conn)
        _insert_submission(conn, client_id)
        conn.commit()
        draft_alert_message(
            conn=conn, alert_id=str(uuid.uuid4()),
            signal_name="governance_latency_index",
            signal_value=22.0, threshold_value=10.0,
            severity="critical", client_name="Acme Corp",
            context={},
        )
        row = conn.execute(
            "SELECT metadata FROM audit_log WHERE event_type = 'alert_message_drafted'"
        ).fetchone()
        meta = json.loads(row[0])
        assert meta["client_name_logged"] == "Acme Corp"

    def test_audit_log_records_response_chars(self, conn, monkeypatch):
        from alerts.narrator import draft_alert_message
        monkeypatch.setenv("CPOI_OLLAMA_URL", "http://localhost:19999/api/generate")
        client_id = _insert_client(conn)
        _insert_submission(conn, client_id)
        conn.commit()
        result = draft_alert_message(
            conn=conn, alert_id=str(uuid.uuid4()),
            signal_name="governance_latency_index",
            signal_value=22.0, threshold_value=10.0,
            severity="critical", client_name="Acme Corp",
            context={},
        )
        row = conn.execute(
            "SELECT metadata FROM audit_log WHERE event_type = 'alert_message_drafted'"
        ).fetchone()
        meta = json.loads(row[0])
        assert meta["response_chars"] == len(result)


# ===========================================================================
# SENDER TESTS
# ===========================================================================

class TestBuildSubject:
    """Unit tests for alerts.sender._build_subject."""

    def test_critical_subject_format(self):
        from alerts.sender import _build_subject
        result = _build_subject("critical", "Acme Corp", "governance_latency_index")
        assert result == (
            "[CRITICAL] Operational Intelligence Alert"
            " -- Acme Corp"
            " -- Governance Latency Index"
        )

    def test_elevated_subject_format(self):
        from alerts.sender import _build_subject
        result = _build_subject("elevated", "Summit LLC", "false_green_indicator")
        assert result == (
            "[ELEVATED] Operational Intelligence Alert"
            " -- Summit LLC"
            " -- False Green Indicator"
        )

    def test_watch_subject_format(self):
        from alerts.sender import _build_subject
        result = _build_subject("watch", "Atlas Inc", "reactive_work_ratio")
        assert result == (
            "[WATCH] Operational Intelligence Alert"
            " -- Atlas Inc"
            " -- Reactive Work Ratio"
        )

    def test_severity_uppercased_in_subject(self):
        from alerts.sender import _build_subject
        result = _build_subject("critical", "X", "governance_latency_index")
        assert "[CRITICAL]" in result

    def test_signal_name_title_cased_in_subject(self):
        from alerts.sender import _build_subject
        result = _build_subject("watch", "X", "reactive_work_ratio")
        assert "Reactive Work Ratio" in result


class TestBuildPlainBody:
    """Unit tests for alerts.sender._build_plain_body."""

    @pytest.fixture()
    def body(self):
        from alerts.sender import _build_plain_body
        return _build_plain_body(
            client_name="Acme Corp",
            signal_name="governance_latency_index",
            signal_value=22.0,
            threshold_value=10.0,
            severity="critical",
            period_date="2026-01-31",
            alert_message="This is the 4-sentence alert message.",
        )

    def test_contains_cp_letterhead(self, body):
        assert "CRITERION PARTNERS" in body

    def test_contains_client_name(self, body):
        assert "Acme Corp" in body

    def test_contains_period_date(self, body):
        assert "2026-01-31" in body

    def test_contains_severity_uppercased(self, body):
        assert "CRITICAL" in body

    def test_contains_alert_message(self, body):
        assert "This is the 4-sentence alert message." in body

    def test_contains_signal_value_in_table(self, body):
        assert "22.0000" in body

    def test_contains_threshold_value_in_table(self, body):
        assert "10.0000" in body

    def test_contains_footer_platform_text(self, body):
        assert "This alert was generated by the CPOI Platform." in body

    def test_contains_footer_brief_reference(self, body):
        assert "Monthly Intelligence Brief" in body

    def test_contains_signal_intelligence_section(self, body):
        assert "SIGNAL INTELLIGENCE" in body


class TestBuildHtmlBody:
    """Unit tests for alerts.sender._build_html_body."""

    @pytest.fixture()
    def html(self):
        from alerts.sender import _build_html_body
        return _build_html_body(
            client_name="Acme Corp",
            signal_name="governance_latency_index",
            signal_value=22.0,
            threshold_value=10.0,
            severity="critical",
            period_date="2026-01-31",
            alert_message="Alert message text.",
        )

    def test_contains_cp_letterhead(self, html):
        assert "CRITERION PARTNERS" in html

    def test_contains_client_name(self, html):
        assert "Acme Corp" in html

    def test_critical_color_applied(self, html):
        assert "#8B0000" in html

    def test_contains_signal_value(self, html):
        assert "22.0000" in html

    def test_contains_threshold_value(self, html):
        assert "10.0000" in html

    def test_contains_footer_text(self, html):
        assert "This alert was generated by the CPOI Platform." in html

    def test_elevated_color_differs_from_critical(self):
        from alerts.sender import _build_html_body
        html_e = _build_html_body("X", "governance_latency_index",
                                   22.0, 10.0, "elevated", "2026-01-31", "msg")
        html_c = _build_html_body("X", "governance_latency_index",
                                   22.0, 10.0, "critical", "2026-01-31", "msg")
        assert html_e != html_c


class TestSendAlertEmail:
    """Unit tests for alerts.sender.send_alert_email."""

    def test_returns_false_when_alert_not_found(self, conn, monkeypatch):
        from alerts.sender import send_alert_email
        monkeypatch.setenv("SENDGRID_API_KEY", "SG.test")
        result = send_alert_email(
            conn, str(uuid.uuid4()), TEST_RECIPIENT, "Acme Corp", "msg"
        )
        assert result is False

    def test_returns_false_when_already_sent(self, conn, monkeypatch):
        from alerts.sender import send_alert_email
        monkeypatch.setenv("SENDGRID_API_KEY", "SG.test")
        client_id = _insert_client(conn)
        sub_id = _insert_submission(conn, client_id)
        r_id = _insert_reading(conn, sub_id, client_id,
                                "governance_latency_index", 22.0, 10.0, True)
        a_id = _insert_alert_with_message(
            conn, client_id, sub_id, r_id,
            "governance_latency_index", 22.0, 10.0, "critical",
            email_sent=True,
        )
        conn.commit()
        result = send_alert_email(conn, a_id, TEST_RECIPIENT, "Acme Corp", "msg")
        assert result is False

    def test_raises_value_error_when_api_key_not_set(self, conn, monkeypatch):
        from alerts.sender import send_alert_email
        monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
        with pytest.raises(ValueError, match="SENDGRID_API_KEY"):
            send_alert_email(conn, str(uuid.uuid4()), TEST_RECIPIENT, "X", "msg")

    def test_raises_runtime_error_on_non_202_response(self, conn, monkeypatch):
        from alerts.sender import send_alert_email
        monkeypatch.setenv("SENDGRID_API_KEY", "SG.test")
        monkeypatch.setenv("CPOI_SENDER_EMAIL", TEST_SENDER)

        client_id = _insert_client(conn)
        sub_id = _insert_submission(conn, client_id)
        r_id = _insert_reading(conn, sub_id, client_id,
                                "governance_latency_index", 22.0, 10.0, True)
        a_id = _insert_alert_with_message(
            conn, client_id, sub_id, r_id,
            "governance_latency_index", 22.0, 10.0, "critical",
        )
        conn.commit()

        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.body = b"Service Unavailable"

        with patch("alerts.sender.SendGridAPIClient") as mock_sg:
            mock_sg.return_value.send.return_value = mock_response
            with pytest.raises(RuntimeError, match="503"):
                send_alert_email(conn, a_id, TEST_RECIPIENT, "Acme Corp", "msg")

    def test_alert_remains_unsent_after_non_202(self, conn, monkeypatch):
        from alerts.sender import send_alert_email
        monkeypatch.setenv("SENDGRID_API_KEY", "SG.test")
        monkeypatch.setenv("CPOI_SENDER_EMAIL", TEST_SENDER)

        client_id = _insert_client(conn)
        sub_id = _insert_submission(conn, client_id)
        r_id = _insert_reading(conn, sub_id, client_id,
                                "governance_latency_index", 22.0, 10.0, True)
        a_id = _insert_alert_with_message(
            conn, client_id, sub_id, r_id,
            "governance_latency_index", 22.0, 10.0, "critical",
        )
        conn.commit()

        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.body = b"error"

        with patch("alerts.sender.SendGridAPIClient") as mock_sg:
            mock_sg.return_value.send.return_value = mock_response
            try:
                send_alert_email(conn, a_id, TEST_RECIPIENT, "Acme Corp", "msg")
            except RuntimeError:
                pass

        row = conn.execute(
            "SELECT email_sent FROM alerts WHERE alert_id = ?", (a_id,)
        ).fetchone()
        assert row[0] == 0

    def test_returns_true_on_success(self, conn, monkeypatch):
        from alerts.sender import send_alert_email
        monkeypatch.setenv("SENDGRID_API_KEY", "SG.test")
        monkeypatch.setenv("CPOI_SENDER_EMAIL", TEST_SENDER)

        client_id = _insert_client(conn)
        sub_id = _insert_submission(conn, client_id)
        r_id = _insert_reading(conn, sub_id, client_id,
                                "governance_latency_index", 22.0, 10.0, True)
        a_id = _insert_alert_with_message(
            conn, client_id, sub_id, r_id,
            "governance_latency_index", 22.0, 10.0, "critical",
        )
        conn.commit()

        mock_response = MagicMock()
        mock_response.status_code = 202

        with patch("alerts.sender.SendGridAPIClient") as mock_sg:
            mock_sg.return_value.send.return_value = mock_response
            result = send_alert_email(
                conn, a_id, TEST_RECIPIENT, "Acme Corp", "CLIENT_A has a breach."
            )

        assert result is True

    def test_email_sent_updated_to_true_on_success(self, conn, monkeypatch):
        from alerts.sender import send_alert_email
        monkeypatch.setenv("SENDGRID_API_KEY", "SG.test")
        monkeypatch.setenv("CPOI_SENDER_EMAIL", TEST_SENDER)

        client_id = _insert_client(conn)
        sub_id = _insert_submission(conn, client_id)
        r_id = _insert_reading(conn, sub_id, client_id,
                                "governance_latency_index", 22.0, 10.0, True)
        a_id = _insert_alert_with_message(
            conn, client_id, sub_id, r_id,
            "governance_latency_index", 22.0, 10.0, "critical",
        )
        conn.commit()

        mock_response = MagicMock()
        mock_response.status_code = 202

        with patch("alerts.sender.SendGridAPIClient") as mock_sg:
            mock_sg.return_value.send.return_value = mock_response
            send_alert_email(conn, a_id, TEST_RECIPIENT, "Acme Corp", "msg")

        row = conn.execute(
            "SELECT email_sent, email_sent_at FROM alerts WHERE alert_id = ?", (a_id,)
        ).fetchone()
        assert row[0] == 1
        assert row[1] is not None

    def test_audit_log_written_on_success(self, conn, monkeypatch):
        from alerts.sender import send_alert_email
        monkeypatch.setenv("SENDGRID_API_KEY", "SG.test")
        monkeypatch.setenv("CPOI_SENDER_EMAIL", TEST_SENDER)

        client_id = _insert_client(conn)
        sub_id = _insert_submission(conn, client_id)
        r_id = _insert_reading(conn, sub_id, client_id,
                                "governance_latency_index", 22.0, 10.0, True)
        a_id = _insert_alert_with_message(
            conn, client_id, sub_id, r_id,
            "governance_latency_index", 22.0, 10.0, "critical",
        )
        conn.commit()

        mock_response = MagicMock()
        mock_response.status_code = 202

        with patch("alerts.sender.SendGridAPIClient") as mock_sg:
            mock_sg.return_value.send.return_value = mock_response
            send_alert_email(conn, a_id, TEST_RECIPIENT, "Acme Corp", "msg")

        row = conn.execute(
            "SELECT event_type, entity_id FROM audit_log WHERE event_type = 'alert_sent'"
        ).fetchone()
        assert row is not None
        assert row[1] == a_id

    def test_client_a_substituted_in_delivered_body(self, conn, monkeypatch):
        """CLIENT_A in the drafted message is replaced with the real client name."""
        from alerts.sender import send_alert_email
        monkeypatch.setenv("SENDGRID_API_KEY", "SG.test")
        monkeypatch.setenv("CPOI_SENDER_EMAIL", TEST_SENDER)

        client_id = _insert_client(conn)
        sub_id = _insert_submission(conn, client_id)
        r_id = _insert_reading(conn, sub_id, client_id,
                                "governance_latency_index", 22.0, 10.0, True)
        a_id = _insert_alert_with_message(
            conn, client_id, sub_id, r_id,
            "governance_latency_index", 22.0, 10.0, "critical",
        )
        conn.commit()

        captured_content = []

        class MockMail:
            def __init__(self, **kwargs): pass
            def add_content(self, content):
                captured_content.append(content.content)

        mock_response = MagicMock()
        mock_response.status_code = 202

        with patch("alerts.sender.Mail", MockMail), \
             patch("alerts.sender.SendGridAPIClient") as mock_sg:
            mock_sg.return_value.send.return_value = mock_response
            send_alert_email(
                conn, a_id, TEST_RECIPIENT, "Acme Corp",
                "CLIENT_A has a critical governance latency breach.",
            )

        # At least the plain text body should have real client name, not CLIENT_A
        all_content = " ".join(captured_content)
        assert "Acme Corp" in all_content
        assert "CLIENT_A" not in all_content


# ===========================================================================
# CIRCUIT BREAKER TESTS
# ===========================================================================

class TestCircuitBreaker:
    """Unit tests for alerts.queue.CircuitBreaker."""

    def test_initial_state_is_closed(self):
        from alerts.queue import CircuitBreaker
        cb = CircuitBreaker()
        assert cb.state == "closed"

    def test_is_open_false_initially(self):
        from alerts.queue import CircuitBreaker
        cb = CircuitBreaker()
        assert not cb.is_open()

    def test_allow_probe_false_initially(self):
        from alerts.queue import CircuitBreaker
        cb = CircuitBreaker()
        assert not cb.allow_probe()

    def test_two_failures_do_not_trip(self):
        from alerts.queue import CircuitBreaker
        cb = CircuitBreaker()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "closed"

    def test_third_consecutive_failure_trips_to_open(self):
        from alerts.queue import CircuitBreaker
        cb = CircuitBreaker()
        cb.record_failure(); cb.record_failure()
        result = cb.record_failure()
        assert result == "open"
        assert cb.state == "open"
        assert cb.is_open()

    def test_record_failure_returns_none_below_threshold(self):
        from alerts.queue import CircuitBreaker
        cb = CircuitBreaker()
        result = cb.record_failure()
        assert result is None

    def test_record_success_from_closed_returns_none(self):
        from alerts.queue import CircuitBreaker
        cb = CircuitBreaker()
        result = cb.record_success()
        assert result is None

    def test_record_success_from_open_returns_closed(self):
        from alerts.queue import CircuitBreaker
        cb = CircuitBreaker()
        cb.record_failure(); cb.record_failure(); cb.record_failure()
        result = cb.record_success()
        assert result == "closed"
        assert cb.state == "closed"

    def test_consecutive_failures_reset_on_success(self):
        from alerts.queue import CircuitBreaker
        cb = CircuitBreaker()
        cb.record_failure(); cb.record_failure()
        cb.record_success()
        assert cb.consecutive_failures == 0

    def test_open_transitions_to_half_open_after_cooldown(self):
        from alerts.queue import CircuitBreaker
        cb = CircuitBreaker()
        cb.record_failure(); cb.record_failure(); cb.record_failure()
        assert cb.state == "open"
        cb._opened_at = time.monotonic() - (CircuitBreaker.COOLDOWN_SECONDS + 1)
        assert cb.state == "half_open"

    def test_is_open_false_in_half_open(self):
        from alerts.queue import CircuitBreaker
        cb = CircuitBreaker()
        cb.record_failure(); cb.record_failure(); cb.record_failure()
        cb._opened_at = time.monotonic() - (CircuitBreaker.COOLDOWN_SECONDS + 1)
        assert cb.state == "half_open"
        assert not cb.is_open()

    def test_allow_probe_true_in_half_open(self):
        from alerts.queue import CircuitBreaker
        cb = CircuitBreaker()
        cb.record_failure(); cb.record_failure(); cb.record_failure()
        cb._opened_at = time.monotonic() - (CircuitBreaker.COOLDOWN_SECONDS + 1)
        assert cb.allow_probe()

    def test_success_in_half_open_resets_to_closed(self):
        from alerts.queue import CircuitBreaker
        cb = CircuitBreaker()
        cb.record_failure(); cb.record_failure(); cb.record_failure()
        cb._opened_at = time.monotonic() - (CircuitBreaker.COOLDOWN_SECONDS + 1)
        assert cb.state == "half_open"
        result = cb.record_success()
        assert result == "closed"
        assert cb.state == "closed"

    def test_failure_in_half_open_trips_back_to_open(self):
        from alerts.queue import CircuitBreaker
        cb = CircuitBreaker()
        cb.record_failure(); cb.record_failure(); cb.record_failure()
        cb._opened_at = time.monotonic() - (CircuitBreaker.COOLDOWN_SECONDS + 1)
        assert cb.state == "half_open"
        result = cb.record_failure()
        assert result == "open"
        assert cb.state == "open"

    def test_cooldown_not_elapsed_stays_open(self):
        from alerts.queue import CircuitBreaker
        cb = CircuitBreaker()
        cb.record_failure(); cb.record_failure(); cb.record_failure()
        # _opened_at is just now -- cooldown not elapsed
        assert cb.state == "open"


# ===========================================================================
# PROCESS SEND QUEUE TESTS
# ===========================================================================

class TestProcessSendQueue:
    """Unit tests for alerts.queue.process_send_queue."""

    def _patch_send(self, monkeypatch, behavior="success"):
        """
        Patch alerts.queue.send_alert_email with controlled behavior.
        behavior: 'success' | 'fail' | callable
        """
        import alerts.queue as qm
        from db.audit import write_audit_log as wal

        if behavior == "success":
            def mock_send(conn, alert_id, recipient_email, client_name, drafted_message):
                sent_at = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "UPDATE alerts SET email_sent=TRUE, email_sent_at=? WHERE alert_id=?",
                    (sent_at, alert_id),
                )
                wal(conn, "alert_sent", "alert", alert_id, f"Sent {alert_id}", "system")
                return True
        elif behavior == "fail":
            def mock_send(conn, alert_id, recipient_email, client_name, drafted_message):
                raise RuntimeError(f"Simulated failure for {alert_id}")
        else:
            mock_send = behavior

        monkeypatch.setattr(qm, "send_alert_email", mock_send)

    def test_empty_queue_returns_all_zeros(self, conn, monkeypatch):
        from alerts.queue import CircuitBreaker, process_send_queue
        monkeypatch.setenv("SENDGRID_API_KEY", "SG.test")
        self._patch_send(monkeypatch, "success")
        cb = CircuitBreaker()
        result = process_send_queue(conn, TEST_RECIPIENT, cb)
        assert result["sent"] == 0
        assert result["skipped_circuit_open"] == 0
        assert result["failed"] == 0

    def test_sends_all_pending_alerts(self, conn, monkeypatch):
        from alerts.queue import CircuitBreaker, process_send_queue
        monkeypatch.setenv("SENDGRID_API_KEY", "SG.test")
        self._patch_send(monkeypatch, "success")

        client_id = _insert_client(conn)
        for i in range(3):
            sub_id = _insert_submission(conn, client_id, f"2026-0{i+1}-28")
            r_id = _insert_reading(conn, sub_id, client_id,
                                    "governance_latency_index", 22.0, 10.0, True,
                                    f"2026-0{i+1}-28")
            _insert_alert_with_message(
                conn, client_id, sub_id, r_id,
                "governance_latency_index", 22.0, 10.0, "critical",
            )
        conn.commit()

        cb = CircuitBreaker()
        result = process_send_queue(conn, TEST_RECIPIENT, cb)
        assert result["sent"] == 3
        assert result["failed"] == 0

    def test_sent_alerts_marked_email_sent_true(self, conn, monkeypatch):
        from alerts.queue import CircuitBreaker, process_send_queue
        monkeypatch.setenv("SENDGRID_API_KEY", "SG.test")
        self._patch_send(monkeypatch, "success")

        client_id = _insert_client(conn)
        sub_id = _insert_submission(conn, client_id)
        r_id = _insert_reading(conn, sub_id, client_id,
                                "governance_latency_index", 22.0, 10.0, True)
        a_id = _insert_alert_with_message(
            conn, client_id, sub_id, r_id,
            "governance_latency_index", 22.0, 10.0, "critical",
        )
        conn.commit()

        process_send_queue(conn, TEST_RECIPIENT, CircuitBreaker())
        row = conn.execute(
            "SELECT email_sent FROM alerts WHERE alert_id = ?", (a_id,)
        ).fetchone()
        assert row[0] == 1

    def test_open_circuit_at_start_skips_all(self, conn, monkeypatch):
        from alerts.queue import CircuitBreaker, process_send_queue
        monkeypatch.setenv("SENDGRID_API_KEY", "SG.test")
        self._patch_send(monkeypatch, "success")

        client_id = _insert_client(conn)
        for i in range(3):
            sub_id = _insert_submission(conn, client_id, f"2026-0{i+1}-28")
            r_id = _insert_reading(conn, sub_id, client_id,
                                    "governance_latency_index", 22.0, 10.0, True,
                                    f"2026-0{i+1}-28")
            _insert_alert_with_message(
                conn, client_id, sub_id, r_id,
                "governance_latency_index", 22.0, 10.0, "critical",
            )
        conn.commit()

        cb = CircuitBreaker()
        # Pre-trip the breaker
        cb.record_failure(); cb.record_failure(); cb.record_failure()
        assert cb.is_open()

        result = process_send_queue(conn, TEST_RECIPIENT, cb)
        assert result["sent"] == 0
        assert result["skipped_circuit_open"] == 3

    def test_circuit_trips_at_3_failures_skips_remainder(self, conn, monkeypatch):
        from alerts.queue import CircuitBreaker, process_send_queue
        monkeypatch.setenv("SENDGRID_API_KEY", "SG.test")
        self._patch_send(monkeypatch, "fail")

        client_id = _insert_client(conn)
        for i in range(5):
            sub_id = _insert_submission(conn, client_id, f"2026-0{i+1}-28")
            r_id = _insert_reading(conn, sub_id, client_id,
                                    "governance_latency_index", 22.0, 10.0, True,
                                    f"2026-0{i+1}-28")
            _insert_alert_with_message(
                conn, client_id, sub_id, r_id,
                "governance_latency_index", 22.0, 10.0, "critical",
            )
        conn.commit()

        cb = CircuitBreaker()
        result = process_send_queue(conn, TEST_RECIPIENT, cb)
        assert result["failed"] == 3
        assert result["skipped_circuit_open"] == 2
        assert cb.state == "open"

    def test_circuit_state_change_logged_to_audit(self, conn, monkeypatch):
        from alerts.queue import CircuitBreaker, process_send_queue
        monkeypatch.setenv("SENDGRID_API_KEY", "SG.test")
        self._patch_send(monkeypatch, "fail")

        client_id = _insert_client(conn)
        for i in range(3):
            sub_id = _insert_submission(conn, client_id, f"2026-0{i+1}-28")
            r_id = _insert_reading(conn, sub_id, client_id,
                                    "governance_latency_index", 22.0, 10.0, True,
                                    f"2026-0{i+1}-28")
            _insert_alert_with_message(
                conn, client_id, sub_id, r_id,
                "governance_latency_index", 22.0, 10.0, "critical",
            )
        conn.commit()

        process_send_queue(conn, TEST_RECIPIENT, CircuitBreaker())

        row = conn.execute(
            "SELECT event_type, description FROM audit_log "
            "WHERE event_type = 'circuit_breaker_state_change'"
        ).fetchone()
        assert row is not None
        assert "closed -> open" in row[1]

    def test_failed_alerts_remain_email_sent_false(self, conn, monkeypatch):
        from alerts.queue import CircuitBreaker, process_send_queue
        monkeypatch.setenv("SENDGRID_API_KEY", "SG.test")
        self._patch_send(monkeypatch, "fail")

        client_id = _insert_client(conn)
        sub_id = _insert_submission(conn, client_id)
        r_id = _insert_reading(conn, sub_id, client_id,
                                "governance_latency_index", 22.0, 10.0, True)
        a_id = _insert_alert_with_message(
            conn, client_id, sub_id, r_id,
            "governance_latency_index", 22.0, 10.0, "critical",
        )
        conn.commit()

        process_send_queue(conn, TEST_RECIPIENT, CircuitBreaker())

        row = conn.execute(
            "SELECT email_sent FROM alerts WHERE alert_id = ?", (a_id,)
        ).fetchone()
        assert row[0] == 0

    def test_null_message_is_drafted_and_saved_before_send(self, conn, monkeypatch):
        from alerts.queue import CircuitBreaker, process_send_queue
        monkeypatch.setenv("SENDGRID_API_KEY", "SG.test")
        monkeypatch.setenv("CPOI_OLLAMA_URL", "http://localhost:19999/api/generate")
        self._patch_send(monkeypatch, "success")

        client_id = _insert_client(conn)
        sub_id = _insert_submission(conn, client_id)
        r_id = _insert_reading(conn, sub_id, client_id,
                                "governance_latency_index", 22.0, 10.0, True)
        # Insert alert with NULL message
        a_id = _insert_alert_with_message(
            conn, client_id, sub_id, r_id,
            "governance_latency_index", 22.0, 10.0, "critical",
            message=None,
        )
        conn.commit()

        process_send_queue(conn, TEST_RECIPIENT, CircuitBreaker())

        row = conn.execute(
            "SELECT alert_message FROM alerts WHERE alert_id = ?", (a_id,)
        ).fetchone()
        assert row[0] is not None
        assert len(row[0]) >= 40

    def test_no_new_instance_needed_for_continuity(self, conn, monkeypatch):
        """Passing the same CircuitBreaker across two calls preserves failure count."""
        from alerts.queue import CircuitBreaker, process_send_queue
        monkeypatch.setenv("SENDGRID_API_KEY", "SG.test")
        self._patch_send(monkeypatch, "fail")

        client_id = _insert_client(conn)
        cb = CircuitBreaker()

        # First call: 2 failures (below threshold)
        for i in range(2):
            sub_id = _insert_submission(conn, client_id, f"2026-0{i+1}-28")
            r_id = _insert_reading(conn, sub_id, client_id,
                                    "governance_latency_index", 22.0, 10.0, True,
                                    f"2026-0{i+1}-28")
            _insert_alert_with_message(
                conn, client_id, sub_id, r_id,
                "governance_latency_index", 22.0, 10.0, "critical",
            )
        conn.commit()
        process_send_queue(conn, TEST_RECIPIENT, cb)
        assert cb.state == "closed"
        assert cb.consecutive_failures == 2

        # Second call: 1 more failure tips to open
        sub_id = _insert_submission(conn, client_id, "2026-03-31")
        r_id = _insert_reading(conn, sub_id, client_id,
                                "governance_latency_index", 22.0, 10.0, True, "2026-03-31")
        _insert_alert_with_message(
            conn, client_id, sub_id, r_id,
            "governance_latency_index", 22.0, 10.0, "critical",
        )
        conn.commit()
        process_send_queue(conn, TEST_RECIPIENT, cb)
        assert cb.state == "open"
