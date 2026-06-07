"""
db/init_db.py
CPOI Platform — Database Initialization

Applies the SQLCipher-encrypted schema to cpoi.db.
Run this once before any other module is used.

Usage:
    set CPOI_DB_KEY=<your-encryption-key>
    python db/init_db.py

The encryption key must be set in the CPOI_DB_KEY environment variable.
It is never hardcoded, never written to a file, never logged.
For production: store the key in Infisical and inject it at runtime.
For local dev: use a .env file that is listed in .gitignore.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import sqlcipher3

# write_audit_log lives in db.audit — imported here so callers that
# already import from db.init_db continue to work without change.
from db.audit import write_audit_log as write_audit_log  # noqa: F401

# ---------------------------------------------------------------------------
# Logging configuration
# Structured JSON output so log entries are machine-readable and consistent
# with the audit trail standard used across the platform.
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
    """Return a logger that writes structured JSON to stdout."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


log = _build_logger("cpoi.db.init")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
DB_ENV_VAR = "CPOI_DB_KEY"
# SQLCipher hardening parameters — match the values agreed in the spec.
CIPHER_PAGE_SIZE = 4096
KDF_ITER = 256000


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def get_db_path() -> Path:
    """
    Return the canonical path to cpoi.db.

    The database file lives at the project root (one level above db/).
    This keeps it out of the db/ source directory while remaining
    predictable and easy to back up.

    Returns:
        Path: absolute path to cpoi.db
    """
    return Path(__file__).parent.parent / "cpoi.db"


def open_connection(db_path: Path | None = None) -> sqlcipher3.Connection:
    """
    Open an encrypted connection to cpoi.db.

    Reads the encryption key from the CPOI_DB_KEY environment variable.
    Applies SQLCipher PRAGMA settings and enables foreign key enforcement.
    This function is the single point through which all database connections
    are established; no other module calls sqlcipher3.connect() directly.

    Args:
        db_path: Optional override for the database file path.
                 Defaults to the value returned by get_db_path().

    Returns:
        sqlcipher3.Connection: open, configured, encrypted connection.

    Raises:
        RuntimeError: if CPOI_DB_KEY is not set.
        sqlcipher3.DatabaseError: if the key is wrong or the file is corrupt.
    """
    key = os.environ.get(DB_ENV_VAR)
    if not key:
        raise RuntimeError(
            f"Environment variable '{DB_ENV_VAR}' is not set. "
            "The database encryption key must be provided before connecting. "
            "For local dev, set it in a .env file (never commit that file). "
            "For production, inject it from Infisical at startup."
        )

    path = db_path if db_path is not None else get_db_path()
    conn = sqlcipher3.connect(str(path))

    # Apply encryption key — must be the first PRAGMA on a new connection.
    conn.execute(f"PRAGMA key = '{key}'")

    # SQLCipher hardening: larger page size and more PBKDF2 iterations.
    conn.execute(f"PRAGMA cipher_page_size = {CIPHER_PAGE_SIZE}")
    conn.execute(f"PRAGMA kdf_iter = {KDF_ITER}")

    # Enforce referential integrity on every connection.
    conn.execute("PRAGMA foreign_keys = ON")

    conn.row_factory = sqlcipher3.Row
    return conn


def apply_schema(conn: sqlcipher3.Connection, schema_path: Path | None = None) -> None:
    """
    Execute schema.sql against an open connection.

    Uses CREATE TABLE IF NOT EXISTS and CREATE TRIGGER IF NOT EXISTS
    so this function is safe to call on an existing database — it will
    not drop or overwrite existing data.

    Args:
        conn: open, authenticated sqlcipher3 connection.
        schema_path: Optional override for the schema file path.
                     Defaults to db/schema.sql.

    Raises:
        FileNotFoundError: if schema.sql does not exist at the expected path.
        sqlcipher3.OperationalError: if the SQL is malformed.
    """
    path = schema_path if schema_path is not None else SCHEMA_PATH

    if not path.exists():
        raise FileNotFoundError(
            f"Schema file not found at '{path}'. "
            "Ensure db/schema.sql exists before calling apply_schema()."
        )

    sql = path.read_text(encoding="utf-8")
    conn.executescript(sql)
    log.info("Schema applied from '%s'", path)


def initialize_database(db_path: Path | None = None) -> Path:
    """
    Full initialization sequence: open connection, apply schema, log the event.

    This is the entry point called at startup and from the CLI block below.
    Safe to call on an already-initialized database — schema application is
    idempotent (IF NOT EXISTS guards), and the audit log entry simply records
    that initialization was verified.

    Args:
        db_path: Optional override for the database file path.

    Returns:
        Path: the path to the initialized database file.

    Raises:
        RuntimeError: if CPOI_DB_KEY is not set.
        FileNotFoundError: if schema.sql is missing.
        sqlcipher3.DatabaseError: if connection or schema application fails.
    """
    path = db_path if db_path is not None else get_db_path()
    log.info("Initializing CPOI database at '%s'", path)

    conn = open_connection(path)
    try:
        apply_schema(conn)
        write_audit_log(
            conn,
            event_type="schema_init",
            description="CPOI database schema initialized (CREATE IF NOT EXISTS applied).",
            entity_type="database",
            entity_id=str(path),
            metadata={"schema_version": "1.0", "cipher_page_size": CIPHER_PAGE_SIZE, "kdf_iter": KDF_ITER},
        )
        conn.commit()
        log.info("Database initialization complete at '%s'", path)
    except Exception as exc:
        conn.close()
        raise RuntimeError(
            f"Database initialization failed: {exc}"
        ) from exc

    conn.close()
    return path


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    initialized_path = initialize_database()
    log.info("CPOI database ready at '%s'", initialized_path)
