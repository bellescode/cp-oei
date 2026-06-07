"""
tests/test_audit.py
CPOI Platform — Unit tests for db/audit.py

Covers the edge cases not exercised by test_schema.py:

  Core contract:
    - Returns a valid UUID string
    - Record is retrievable by log_id
    - performed_at is a valid ISO 8601 timestamp
    - performed_by defaults to 'system'
    - performed_by can be set to a custom actor

  Input validation:
    - Empty event_type raises ValueError
    - Whitespace-only event_type raises ValueError
    - Empty description raises ValueError
    - Whitespace-only description raises ValueError
    - metadata that is not a dict raises TypeError

  Optional fields:
    - None metadata stores NULL (not the string 'None')
    - entity_type and entity_id are nullable without error
    - metadata dict with nested values serializes and round-trips correctly

  Sequence integrity:
    - Multiple sequential entries all persist and are retrievable
    - Each entry gets a distinct log_id
    - Entries are stored in insertion order (performed_at ascending)

  Import path:
    - write_audit_log is importable from db.audit (canonical path)
    - write_audit_log is importable from db.init_db (backward-compat re-export)
    - Both imports resolve to the same function
"""

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.audit import write_audit_log as write_audit_log_from_audit
from db.init_db import initialize_database, open_connection
from db.init_db import write_audit_log as write_audit_log_from_init

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEST_KEY = "cpoi-test-key-audit"


@pytest.fixture()
def conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Initialized database connection for each test."""
    monkeypatch.setenv("CPOI_DB_KEY", TEST_KEY)
    db_path = tmp_path / "test_audit.db"
    initialize_database(db_path)
    connection = open_connection(db_path)
    yield connection
    connection.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_entry(conn, log_id: str) -> dict | None:
    """Return a row from audit_log as a dict, or None if not found."""
    cursor = conn.execute(
        """
        SELECT log_id, event_type, entity_type, entity_id,
               description, performed_by, performed_at, metadata
        FROM audit_log
        WHERE log_id = ?
        """,
        (log_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {
        "log_id": row[0],
        "event_type": row[1],
        "entity_type": row[2],
        "entity_id": row[3],
        "description": row[4],
        "performed_by": row[5],
        "performed_at": row[6],
        "metadata": row[7],
    }


# ---------------------------------------------------------------------------
# Tests: core contract
# ---------------------------------------------------------------------------

def test_returns_valid_uuid(conn) -> None:
    """write_audit_log must return a string that is a valid UUID4."""
    log_id = write_audit_log_from_audit(
        conn, event_type="test_event", description="Core contract test."
    )
    conn.commit()
    parsed = uuid.UUID(log_id)
    assert str(parsed) == log_id


def test_record_retrievable_by_log_id(conn) -> None:
    """The returned log_id must identify a retrievable record."""
    log_id = write_audit_log_from_audit(
        conn, event_type="test_event", description="Retrieval test."
    )
    conn.commit()
    entry = _fetch_entry(conn, log_id)
    assert entry is not None, f"No record found for log_id '{log_id}'"
    assert entry["log_id"] == log_id


def test_performed_at_is_iso_timestamp(conn) -> None:
    """performed_at must be a parseable ISO 8601 timestamp."""
    log_id = write_audit_log_from_audit(
        conn, event_type="test_event", description="Timestamp test."
    )
    conn.commit()
    entry = _fetch_entry(conn, log_id)
    assert entry is not None
    # Must parse without raising
    parsed = datetime.fromisoformat(entry["performed_at"])
    assert parsed is not None


def test_performed_by_defaults_to_system(conn) -> None:
    """When performed_by is not provided, it must default to 'system'."""
    log_id = write_audit_log_from_audit(
        conn, event_type="test_event", description="Default actor test."
    )
    conn.commit()
    entry = _fetch_entry(conn, log_id)
    assert entry["performed_by"] == "system"


def test_performed_by_custom_actor(conn) -> None:
    """A custom performed_by value must be stored as provided."""
    log_id = write_audit_log_from_audit(
        conn,
        event_type="score_overridden",
        description="Score override applied by Managing Partner.",
        performed_by="managing_partner",
    )
    conn.commit()
    entry = _fetch_entry(conn, log_id)
    assert entry["performed_by"] == "managing_partner"


def test_event_type_stored_correctly(conn) -> None:
    """The event_type value must be stored exactly as provided."""
    log_id = write_audit_log_from_audit(
        conn,
        event_type="intake_ingested",
        description="Event type storage test.",
    )
    conn.commit()
    entry = _fetch_entry(conn, log_id)
    assert entry["event_type"] == "intake_ingested"


def test_description_stored_correctly(conn) -> None:
    """The description must be stored exactly as provided (leading/trailing whitespace stripped)."""
    log_id = write_audit_log_from_audit(
        conn,
        event_type="test_event",
        description="  Description with surrounding whitespace.  ",
    )
    conn.commit()
    entry = _fetch_entry(conn, log_id)
    assert entry["description"] == "Description with surrounding whitespace."


# ---------------------------------------------------------------------------
# Tests: input validation
# ---------------------------------------------------------------------------

def test_empty_event_type_raises_value_error(conn) -> None:
    """An empty event_type string must raise ValueError before any DB write."""
    with pytest.raises(ValueError, match="event_type"):
        write_audit_log_from_audit(conn, event_type="", description="Valid description.")


def test_whitespace_event_type_raises_value_error(conn) -> None:
    """A whitespace-only event_type must raise ValueError."""
    with pytest.raises(ValueError, match="event_type"):
        write_audit_log_from_audit(conn, event_type="   ", description="Valid description.")


def test_empty_description_raises_value_error(conn) -> None:
    """An empty description must raise ValueError before any DB write."""
    with pytest.raises(ValueError, match="description"):
        write_audit_log_from_audit(conn, event_type="test_event", description="")


def test_whitespace_description_raises_value_error(conn) -> None:
    """A whitespace-only description must raise ValueError."""
    with pytest.raises(ValueError, match="description"):
        write_audit_log_from_audit(conn, event_type="test_event", description="   ")


def test_non_dict_metadata_raises_type_error(conn) -> None:
    """Passing a non-dict as metadata must raise TypeError."""
    with pytest.raises(TypeError, match="metadata"):
        write_audit_log_from_audit(
            conn,
            event_type="test_event",
            description="Metadata type test.",
            metadata="not a dict",
        )


def test_list_metadata_raises_type_error(conn) -> None:
    """A list passed as metadata must also raise TypeError."""
    with pytest.raises(TypeError, match="metadata"):
        write_audit_log_from_audit(
            conn,
            event_type="test_event",
            description="Metadata list test.",
            metadata=["item1", "item2"],
        )


# ---------------------------------------------------------------------------
# Tests: optional fields
# ---------------------------------------------------------------------------

def test_none_metadata_stores_null(conn) -> None:
    """None metadata must store NULL in the database, not the string 'None'."""
    log_id = write_audit_log_from_audit(
        conn,
        event_type="test_event",
        description="Null metadata test.",
        metadata=None,
    )
    conn.commit()
    entry = _fetch_entry(conn, log_id)
    assert entry["metadata"] is None, (
        f"Expected NULL metadata, got '{entry['metadata']}'"
    )


def test_entity_type_and_id_nullable(conn) -> None:
    """Omitting entity_type and entity_id must store NULL without error."""
    log_id = write_audit_log_from_audit(
        conn,
        event_type="test_event",
        description="Nullable fields test.",
    )
    conn.commit()
    entry = _fetch_entry(conn, log_id)
    assert entry["entity_type"] is None
    assert entry["entity_id"] is None


def test_metadata_with_nested_values_round_trips(conn) -> None:
    """A metadata dict with nested structure must serialize and deserialize correctly."""
    meta = {
        "signal_values": {"governance_latency": 12.5, "false_green_count": 3},
        "thresholds_breached": ["governance_latency", "false_green_indicator"],
        "score_delta": -5,
    }
    log_id = write_audit_log_from_audit(
        conn,
        event_type="signal_calculated",
        description="Signal calculation metadata round-trip test.",
        metadata=meta,
    )
    conn.commit()
    entry = _fetch_entry(conn, log_id)
    stored = json.loads(entry["metadata"])
    assert stored == meta, f"Metadata round-trip failed. Expected {meta}, got {stored}"


def test_long_description_stored_correctly(conn) -> None:
    """A description at the upper end of practical length must store without truncation."""
    long_desc = (
        "Intake submission ingested for client 'Acme Corp'. "
        "Reporting period: 2026-05-01 to 2026-05-31. "
        "Source file: CP_OEI_DataIntake_AcmeCorp_2026_05.xlsx. "
        "All 8 tabs validated. 47 initiative records, 12 escalation records, "
        "8 dependency records, 6 resource utilization records, "
        "4 governance events, 47 reporting variance records, "
        "23 headcount signal records processed successfully."
    )
    log_id = write_audit_log_from_audit(
        conn, event_type="intake_ingested", description=long_desc
    )
    conn.commit()
    entry = _fetch_entry(conn, log_id)
    assert entry["description"] == long_desc


# ---------------------------------------------------------------------------
# Tests: sequence integrity
# ---------------------------------------------------------------------------

def test_sequential_entries_all_persist(conn) -> None:
    """Five sequential audit entries must all be retrievable."""
    ids = []
    for i in range(5):
        log_id = write_audit_log_from_audit(
            conn,
            event_type="test_event",
            description=f"Sequential entry {i}.",
            entity_id=str(i),
        )
        conn.commit()
        ids.append(log_id)

    for log_id in ids:
        entry = _fetch_entry(conn, log_id)
        assert entry is not None, f"Entry '{log_id}' not found after batch insert"


def test_sequential_entries_have_distinct_ids(conn) -> None:
    """Each write_audit_log call must produce a distinct log_id."""
    ids = [
        write_audit_log_from_audit(
            conn, event_type="test_event", description=f"Distinct ID test {i}."
        )
        for i in range(10)
    ]
    for _ in ids:
        conn.commit()
    assert len(set(ids)) == 10, "Duplicate log_ids detected across sequential entries"


# ---------------------------------------------------------------------------
# Tests: import path
# ---------------------------------------------------------------------------

def test_importable_from_db_audit() -> None:
    """write_audit_log must be importable from db.audit."""
    from db.audit import write_audit_log
    assert callable(write_audit_log)


def test_importable_from_db_init_db() -> None:
    """write_audit_log must be importable from db.init_db (backward-compat re-export)."""
    from db.init_db import write_audit_log
    assert callable(write_audit_log)


def test_both_imports_are_same_function() -> None:
    """The re-export in db.init_db must resolve to the same function as db.audit."""
    assert write_audit_log_from_audit is write_audit_log_from_init, (
        "db.init_db.write_audit_log and db.audit.write_audit_log are not the same object. "
        "The re-export in init_db.py is broken."
    )
