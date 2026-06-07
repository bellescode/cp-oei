"""
Standalone integration test for alerts/queue.py.
Run directly: python tests/_queue_integration.py
"""
import os
import sys
import uuid
import tempfile
import json

os.environ["CPOI_DB_KEY"] = "test-key-queue"
os.environ["SENDGRID_API_KEY"] = "SG.test-key"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from datetime import datetime, timezone

from db.init_db import initialize_database, open_connection
from db.audit import write_audit_log
from alerts.detector import create_alerts_for_submission
from alerts.queue import CircuitBreaker, process_send_queue
import alerts.queue as queue_module

# ---------------------------------------------------------------------------
# Mock send_alert_email
# ---------------------------------------------------------------------------

call_log = []
_fail_next_n = [0]


def mock_send_success(conn, alert_id, recipient_email, client_name, drafted_message):
    call_log.append({"alert_id": alert_id, "recipient": recipient_email})
    sent_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE alerts SET email_sent=TRUE, email_sent_at=? WHERE alert_id=?",
        (sent_at, alert_id),
    )
    write_audit_log(conn, "alert_sent", "alert", alert_id,
                    f"Mock sent alert {alert_id}", "system")
    return True


def mock_send_fail(conn, alert_id, recipient_email, client_name, drafted_message):
    if _fail_next_n[0] > 0:
        _fail_next_n[0] -= 1
        raise RuntimeError(f"Simulated SendGrid failure for {alert_id}")
    return mock_send_success(conn, alert_id, recipient_email, client_name, drafted_message)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _setup_db():
    tmp = Path(tempfile.mkdtemp()) / "test_queue.db"
    initialize_database(tmp)
    conn = open_connection(tmp)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _insert_client(conn):
    client_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO clients (client_id, client_name, engagement_type) VALUES (?,?,?)",
        (client_id, "Acme Corp", "oeil"),
    )
    return client_id


def _insert_submission(conn, client_id, period_end):
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


def _insert_breach(conn, sub_id, client_id, signal_name, signal_value,
                   threshold_value, period_date):
    r_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO signal_readings
           (reading_id, submission_id, client_id, signal_name, signal_value,
            threshold_value, threshold_breached, period_date)
           VALUES (?,?,?,?,?,?,TRUE,?)""",
        (r_id, sub_id, client_id, signal_name, signal_value, threshold_value, period_date),
    )
    return r_id


def _insert_alert_direct(conn, client_id, sub_id, reading_id, signal_name,
                          signal_value, threshold_value, severity, message=None):
    a_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO alerts
           (alert_id, client_id, submission_id, reading_id, signal_name,
            signal_value, threshold_value, alert_severity, triggered_at,
            email_sent, acknowledged, alert_message)
           VALUES (?,?,?,?,?,?,?,?,?,FALSE,FALSE,?)""",
        (a_id, client_id, sub_id, reading_id, signal_name,
         signal_value, threshold_value, severity,
         "2026-04-01T00:00:00", message),
    )
    return a_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_happy_path():
    """process_send_queue sends all pending alerts."""
    queue_module.send_alert_email = mock_send_success
    call_log.clear()

    conn = _setup_db()
    client_id = _insert_client(conn)
    sub1 = _insert_submission(conn, client_id, "2026-01-31")
    sub2 = _insert_submission(conn, client_id, "2026-02-28")
    _insert_breach(conn, sub1, client_id, "governance_latency_index", 22.0, 10.0, "2026-01-31")
    _insert_breach(conn, sub2, client_id, "false_green_indicator",    2.0,  1.0,  "2026-02-28")
    conn.commit()

    ids1 = create_alerts_for_submission(conn, sub1, client_id, "2026-01-31")
    ids2 = create_alerts_for_submission(conn, sub2, client_id, "2026-02-28")
    conn.commit()
    assert len(ids1) == 1 and len(ids2) == 1

    cb = CircuitBreaker()
    results = process_send_queue(conn, "test@criterion-partners.com", cb)

    assert results["sent"] == 2, f"Expected 2 sent: {results}"
    assert results["failed"] == 0
    assert results["skipped_circuit_open"] == 0
    assert len(call_log) == 2

    rows = conn.execute("SELECT email_sent FROM alerts").fetchall()
    assert all(r[0] == 1 for r in rows), "All alerts must be email_sent=TRUE"

    conn.close()
    print("PASS: happy path -- 2 alerts sent, both marked email_sent=TRUE")


def test_queue_empty_after_send():
    """Second run returns zeros when queue is empty."""
    queue_module.send_alert_email = mock_send_success
    call_log.clear()

    conn = _setup_db()
    client_id = _insert_client(conn)
    sub1 = _insert_submission(conn, client_id, "2026-01-31")
    _insert_breach(conn, sub1, client_id, "governance_latency_index", 22.0, 10.0, "2026-01-31")
    conn.commit()
    create_alerts_for_submission(conn, sub1, client_id, "2026-01-31")
    conn.commit()

    cb = CircuitBreaker()
    process_send_queue(conn, "test@criterion-partners.com", cb)
    call_log.clear()

    results = process_send_queue(conn, "test@criterion-partners.com", cb)
    assert results["sent"] == 0
    assert results["skipped_circuit_open"] == 0

    conn.close()
    print("PASS: second run returns zeros (queue empty)")


def test_circuit_breaker_trips_at_3_failures():
    """Circuit breaker trips to open after 3 consecutive failures, 4th is skipped."""
    conn = _setup_db()
    client_id = _insert_client(conn)

    alert_ids = []
    for i in range(4):
        sub = _insert_submission(conn, client_id, f"2026-0{i+1}-28")
        r_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO signal_readings
               (reading_id, submission_id, client_id, signal_name, signal_value,
                threshold_value, threshold_breached, period_date)
               VALUES (?,?,?,?,?,?,TRUE,?)""",
            (r_id, sub, client_id, "reactive_work_ratio", 0.45, 0.30, f"2026-0{i+1}-28"),
        )
        a_id = _insert_alert_direct(
            conn, client_id, sub, r_id,
            "reactive_work_ratio", 0.45, 0.30, "elevated",
            message="Pre-drafted message for test.",
        )
        alert_ids.append(a_id)
    conn.commit()

    # All sends fail
    def always_fail(conn, alert_id, recipient_email, client_name, drafted_message):
        raise RuntimeError(f"Simulated failure for {alert_id}")

    queue_module.send_alert_email = always_fail

    cb = CircuitBreaker()
    results = process_send_queue(conn, "test@criterion-partners.com", cb)

    assert results["failed"] == 3, f"Expected 3 failures: {results}"
    assert results["skipped_circuit_open"] == 1, f"Expected 1 skipped: {results}"
    assert cb.state == "open", f"Expected breaker open, got {cb.state}"

    # All 4 alerts remain unsent
    unsent = conn.execute(
        "SELECT COUNT(*) FROM alerts WHERE email_sent=FALSE"
    ).fetchone()[0]
    assert unsent == 4, f"Expected 4 unsent, got {unsent}"

    # State change logged to audit_log
    row = conn.execute(
        "SELECT description FROM audit_log WHERE event_type='circuit_breaker_state_change'"
    ).fetchone()
    assert row is not None, "circuit_breaker_state_change must be logged"
    assert "closed -> open" in row[0], f"Unexpected description: {row[0]}"

    conn.close()
    print("PASS: circuit breaker trips at 3 failures, 4th skipped, all remain unsent")


def test_alerts_remain_unsent_after_failure():
    """Failed alerts stay email_sent=FALSE in the database."""
    conn = _setup_db()
    client_id = _insert_client(conn)
    sub = _insert_submission(conn, client_id, "2026-01-31")
    r_id = _insert_breach(conn, sub, client_id, "governance_latency_index",
                           22.0, 10.0, "2026-01-31")
    a_id = _insert_alert_direct(
        conn, client_id, sub, r_id,
        "governance_latency_index", 22.0, 10.0, "critical",
        message="Pre-drafted.",
    )
    conn.commit()

    def fail_once(conn, alert_id, recipient_email, client_name, drafted_message):
        raise RuntimeError("Simulated single failure")

    queue_module.send_alert_email = fail_once

    cb = CircuitBreaker()
    results = process_send_queue(conn, "test@criterion-partners.com", cb)

    assert results["failed"] == 1
    row = conn.execute(
        "SELECT email_sent FROM alerts WHERE alert_id=?", (a_id,)
    ).fetchone()
    assert row[0] == 0, "email_sent must remain FALSE after failure"

    conn.close()
    print("PASS: failed alert remains email_sent=FALSE (will retry on next run)")


def test_circuit_breaker_resets_on_success_after_cooldown():
    """Successful probe in half_open resets breaker to closed."""
    import time
    queue_module.send_alert_email = mock_send_success
    call_log.clear()

    conn = _setup_db()
    client_id = _insert_client(conn)
    sub = _insert_submission(conn, client_id, "2026-01-31")
    r_id = _insert_breach(conn, sub, client_id, "governance_latency_index",
                           22.0, 10.0, "2026-01-31")
    a_id = _insert_alert_direct(
        conn, client_id, sub, r_id,
        "governance_latency_index", 22.0, 10.0, "critical",
        message="Pre-drafted.",
    )
    conn.commit()

    # Trip the breaker to open, then force half_open
    cb = CircuitBreaker()
    cb.record_failure(); cb.record_failure(); cb.record_failure()
    assert cb.state == "open"
    cb._opened_at = time.monotonic() - 301  # simulate 5 min elapsed
    assert cb.state == "half_open"

    # Successful probe should reset to closed
    results = process_send_queue(conn, "test@criterion-partners.com", cb)
    assert results["sent"] == 1, f"Probe should succeed: {results}"
    assert cb.state == "closed", f"Expected closed after probe success, got {cb.state}"

    conn.close()
    print("PASS: successful probe in half_open resets circuit breaker to closed")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_happy_path()
    test_queue_empty_after_send()
    test_circuit_breaker_trips_at_3_failures()
    test_alerts_remain_unsent_after_failure()
    test_circuit_breaker_resets_on_success_after_cooldown()
    print()
    print("All queue integration tests passed (5/5).")
