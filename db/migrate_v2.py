"""
db/migrate_v2.py
CPOI Platform -- Schema migration v2.

Adds the engagement-lifecycle and contact columns the dashboard needs, plus
the client_users table that backs the client portal login. The migration is
idempotent: it checks for each column/table before adding it, so it is safe
to run repeatedly and safe to run on an already-partially-migrated database.

Usage (on the server, with the DB key set):
    export CPOI_DB_KEY=...        # same key the platform already uses
    python db/migrate_v2.py

Every change is recorded in audit_log with event_type 'schema_migration'.
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from db.init_db import open_connection
from db.audit import write_audit_log

# New columns on the existing clients table: (name, DDL type/clause)
_CLIENT_COLUMNS: list[tuple[str, str]] = [
    ("engagement_end_date",   "DATE"),
    ("renewal_status",        "TEXT NOT NULL DEFAULT 'unknown'"),
    ("primary_contact_name",  "TEXT"),
    ("primary_contact_title", "TEXT"),
    ("primary_contact_email", "TEXT"),
    ("executive_sponsor",     "TEXT"),
    ("alert_recipient_email", "TEXT"),
    ("engagement_notes",      "TEXT"),
]

# client_users backs the client portal. Passwords are stored as PBKDF2 hashes
# produced by dashboard/auth.py -- never in plain text.
_CLIENT_USERS_DDL = """
CREATE TABLE IF NOT EXISTS client_users (
    user_id               TEXT PRIMARY KEY,
    client_id             TEXT NOT NULL REFERENCES clients(client_id),
    email                 TEXT NOT NULL UNIQUE,
    password_hash         TEXT NOT NULL,
    must_change_password  INTEGER NOT NULL DEFAULT 1 CHECK (must_change_password IN (0,1)),
    is_active             INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    reset_token_hash      TEXT,
    reset_token_expires   TIMESTAMP,
    failed_attempts       INTEGER NOT NULL DEFAULT 0,
    created_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login            TIMESTAMP
);
"""

# TOTP/MFA columns added to client_users (OEIL portal mandates MFA).
_CLIENT_USER_COLUMNS: list[tuple[str, str]] = [
    ("totp_secret",  "TEXT"),
    ("totp_enabled", "INTEGER NOT NULL DEFAULT 0"),
]

# Secure message thread between Managing Partner and each client. Bodies are
# AES-256-GCM encrypted at the application layer (see dashboard/crypto.py) on
# top of SQLCipher at-rest encryption.
_MESSAGES_DDL = """
CREATE TABLE IF NOT EXISTS messages (
    message_id       TEXT PRIMARY KEY,
    client_id        TEXT NOT NULL REFERENCES clients(client_id),
    sender_role      TEXT NOT NULL CHECK (sender_role IN ('managing_partner','client')),
    sender_label     TEXT NOT NULL,
    body_ciphertext  TEXT NOT NULL,
    is_encrypted     INTEGER NOT NULL DEFAULT 0 CHECK (is_encrypted IN (0,1)),
    created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    read_by_client   INTEGER NOT NULL DEFAULT 0 CHECK (read_by_client IN (0,1)),
    read_by_mp       INTEGER NOT NULL DEFAULT 0 CHECK (read_by_mp IN (0,1))
);
"""

# Managing Partner credentials. The CPOI_DASHBOARD_PASSWORD env var remains a
# break-glass master password; once the MP sets/resets a password in-app, that
# DB hash also works. Enables in-app change and emailed-code password reset.
_ADMIN_CREDENTIALS_DDL = """
CREATE TABLE IF NOT EXISTS admin_credentials (
    admin_id            TEXT PRIMARY KEY,
    password_hash       TEXT,
    reset_token_hash    TEXT,
    reset_token_expires TIMESTAMP,
    updated_at          TIMESTAMP
);
"""

# Client self-service uploads. A client-submitted workbook is stored here as
# 'pending_review' and is NOT processed until the Managing Partner approves it.
_CLIENT_UPLOADS_DDL = """
CREATE TABLE IF NOT EXISTS client_uploads (
    upload_id         TEXT PRIMARY KEY,
    client_id         TEXT NOT NULL REFERENCES clients(client_id),
    original_filename TEXT NOT NULL,
    stored_path       TEXT NOT NULL,
    uploaded_by       TEXT NOT NULL,
    uploaded_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    validation_ok     INTEGER,
    status            TEXT NOT NULL DEFAULT 'pending_review'
                          CHECK (status IN ('pending_review','approved','rejected')),
    review_note       TEXT,
    reviewed_by       TEXT,
    reviewed_at       TIMESTAMP,
    submission_id     TEXT
);
"""


def _existing_columns(conn, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    # row[1] is the column name in PRAGMA table_info output
    return {row[1] for row in rows}


def run_migration() -> list[str]:
    """Apply the v2 migration. Returns the list of changes actually applied."""
    conn = open_connection()
    applied: list[str] = []
    try:
        existing = _existing_columns(conn, "clients")
        for name, ddl in _CLIENT_COLUMNS:
            if name not in existing:
                conn.execute(f"ALTER TABLE clients ADD COLUMN {name} {ddl}")
                applied.append(f"clients.{name}")

        before = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='client_users'"
        ).fetchone()
        conn.execute(_CLIENT_USERS_DDL)
        if before is None:
            applied.append("table:client_users")

        # TOTP columns on client_users (safe on both new and existing tables).
        cu_existing = _existing_columns(conn, "client_users")
        for name, ddl in _CLIENT_USER_COLUMNS:
            if name not in cu_existing:
                conn.execute(f"ALTER TABLE client_users ADD COLUMN {name} {ddl}")
                applied.append(f"client_users.{name}")

        msg_before = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
        ).fetchone()
        conn.execute(_MESSAGES_DDL)
        if msg_before is None:
            applied.append("table:messages")

        up_before = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='client_uploads'"
        ).fetchone()
        conn.execute(_CLIENT_UPLOADS_DDL)
        if up_before is None:
            applied.append("table:client_uploads")

        admin_before = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='admin_credentials'"
        ).fetchone()
        conn.execute(_ADMIN_CREDENTIALS_DDL)
        if admin_before is None:
            applied.append("table:admin_credentials")

        if applied:
            write_audit_log(
                conn,
                event_type="schema_migration",
                description=f"Schema migration v2 applied: {', '.join(applied)}.",
                entity_type="database",
                entity_id="cpoi.db",
                metadata={"version": "2.0", "changes": applied},
            )
        conn.commit()
    finally:
        conn.close()
    return applied


if __name__ == "__main__":
    changes = run_migration()
    if changes:
        print("Migration v2 applied the following changes:")
        for c in changes:
            print(f"  - {c}")
    else:
        print("Migration v2: database already up to date. No changes needed.")
