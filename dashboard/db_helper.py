"""
dashboard/db_helper.py
CPOI Platform -- Shared database connection helper for the Streamlit dashboard.

Provides a single cached connection to the encrypted SQLite database.
The connection is opened once per Streamlit process and reused across
all page reruns via st.cache_resource.

The CPOI_DB_KEY environment variable must be set before the dashboard
process starts. The database path defaults to the platform default
(cpoi.db in the project root) but can be overridden via CPOI_DB_PATH.
"""

import os
import sys
from pathlib import Path
from typing import Any

import streamlit as st

# Ensure project root is on sys.path so platform modules are importable
# when the dashboard is launched from the dashboard/ directory.
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from db.init_db import open_connection


def _get_db_path() -> Path | None:
    db_path_env = os.environ.get("CPOI_DB_PATH")
    return Path(db_path_env) if db_path_env else None


def get_connection() -> Any:
    """
    Open a fresh encrypted SQLite connection per call.

    SQLCipher connections cannot be shared across threads, so we open a
    new connection each time rather than caching one globally. Streamlit
    runs page scripts in different threads, which caused the
    'SQLite objects created in a thread can only be used in that same
    thread' error when a cached connection was reused.

    Reads CPOI_DB_KEY from the environment. Reads CPOI_DB_PATH from the
    environment if set; otherwise uses the platform default path.

    Returns:
        sqlcipher3.Connection with row_factory=sqlite3.Row and
        foreign_keys enforced.

    Raises:
        RuntimeError: if CPOI_DB_KEY is not set.
        Exception:    any database open error propagates.
    """
    if not os.environ.get("CPOI_DB_KEY"):
        raise RuntimeError(
            "CPOI_DB_KEY environment variable is not set. "
            "Set it before launching the dashboard."
        )
    conn = open_connection(_get_db_path())
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def run_query(sql: str, params: tuple = ()) -> list[dict]:
    """
    Execute a read-only SQL query and return results as a list of dicts.

    Opens a fresh connection per call to avoid cross-thread SQLite errors.

    Args:
        sql:    SQL SELECT statement.
        params: Positional parameters for the statement.

    Returns:
        list[dict]: Zero or more rows as plain dicts.
    """
    conn = get_connection()
    try:
        cursor = conn.execute(sql, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def run_write(sql: str, params: tuple = ()) -> None:
    """
    Execute a write SQL statement (INSERT, UPDATE) and commit immediately.

    Opens a fresh connection per call to avoid cross-thread SQLite errors.

    Args:
        sql:    SQL statement.
        params: Positional parameters.
    """
    conn = get_connection()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()
