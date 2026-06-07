"""
dashboard/auth.py
CPOI Platform -- Authentication, roles, and client account management.

Two roles:
  'mp'     Managing Partner. Single operator. Authenticates against the
           CPOI_DASHBOARD_PASSWORD environment variable. Full access.
  'client' Executive sponsor. Authenticates against the client_users table
           (email + PBKDF2 password hash). Scoped to one client_id.

Passwords are hashed with PBKDF2-HMAC-SHA256 (dependency-free, stdlib only)
and stored as 'pbkdf2_sha256$iterations$salt_hex$hash_hex'. Plain passwords
are never stored or logged.

Password reset uses a 6-digit code emailed to the client (see email_helper).
Only the hash of the code is stored, with a 60-minute expiry.

Session state keys (set on successful login):
  auth_role        'mp' | 'client'
  auth_user_id     client_users.user_id   (client only)
  auth_client_id   clients.client_id      (client only)
  auth_client_name display name           (client only)
  auth_must_change True if the client must set a new password before continuing
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import streamlit as st

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from db.init_db import open_connection
from db.audit import write_audit_log

_PBKDF2_ITERATIONS = 200_000
_RESET_TTL_MINUTES = 60
_MAX_FAILED_ATTEMPTS = 8


# ---------------------------------------------------------------------------
# Connection helper (compound writes that also touch audit_log)
# ---------------------------------------------------------------------------

def _conn() -> Any:
    return open_connection()


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Return a PBKDF2-HMAC-SHA256 hash string for storage."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verify a password against a stored PBKDF2 hash string."""
    try:
        algo, iters_s, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters_s)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


def _hash_code(code: str) -> str:
    """Hash a reset code (SHA-256) so the raw code is never stored."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Session / role helpers
# ---------------------------------------------------------------------------

def current_role() -> str | None:
    return st.session_state.get("auth_role")


def is_mp() -> bool:
    return current_role() == "mp"


def is_client() -> bool:
    return current_role() == "client"


def current_client_id() -> str | None:
    return st.session_state.get("auth_client_id")


def logout() -> None:
    for key in (
        "auth_role", "auth_user_id", "auth_client_id",
        "auth_client_name", "auth_must_change",
    ):
        st.session_state.pop(key, None)


# ---------------------------------------------------------------------------
# Managing Partner login
# ---------------------------------------------------------------------------

_ADMIN_ID = "managing_partner"


def _load_admin() -> dict | None:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT password_hash, reset_token_hash, reset_token_expires FROM admin_credentials WHERE admin_id = ?",
            (_ADMIN_ID,),
        ).fetchone()
        if row is None:
            return None
        return {"password_hash": row[0], "reset_token_hash": row[1], "reset_token_expires": row[2]}
    finally:
        conn.close()


def verify_mp_password(password: str) -> bool:
    """
    Verify the Managing Partner password WITHOUT establishing a session.

    Two valid credentials:
      1. CPOI_DASHBOARD_PASSWORD env var -- the server-side break-glass master
         password (always works; lets the operator recover via the server).
      2. The DB-stored password hash -- what the MP sets/resets in-app.
    """
    env_pw = os.environ.get("CPOI_DASHBOARD_PASSWORD", "")
    if env_pw and hmac.compare_digest(password, env_pw):
        return True
    admin = _load_admin()
    if admin and admin["password_hash"]:
        return verify_password(password, admin["password_hash"])
    return False


def mp_password_configured() -> bool:
    """True if any MP credential exists (env var or DB hash)."""
    if os.environ.get("CPOI_DASHBOARD_PASSWORD"):
        return True
    admin = _load_admin()
    return bool(admin and admin["password_hash"])


def login_mp(password: str) -> bool:
    """Authenticate the Managing Partner and establish the session on success."""
    if not mp_password_configured():
        raise RuntimeError(
            "No Managing Partner password is configured. Set CPOI_DASHBOARD_PASSWORD "
            "on the server before launching the dashboard."
        )
    if verify_mp_password(password):
        st.session_state["auth_role"] = "mp"
        return True
    return False


def set_mp_password(new_password: str) -> None:
    """Set/replace the DB-stored Managing Partner password hash."""
    conn = _conn()
    try:
        conn.execute(
            """
            INSERT INTO admin_credentials (admin_id, password_hash, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(admin_id) DO UPDATE SET
                password_hash = excluded.password_hash,
                updated_at = excluded.updated_at,
                reset_token_hash = NULL,
                reset_token_expires = NULL
            """,
            (_ADMIN_ID, hash_password(new_password), datetime.now(timezone.utc).isoformat()),
        )
        write_audit_log(
            conn, event_type="mp_password_changed", entity_type="admin",
            entity_id=_ADMIN_ID, description="Managing Partner password was set/changed.",
            performed_by="managing_partner",
        )
        conn.commit()
    finally:
        conn.close()


def mp_change_password(current: str, new_password: str) -> tuple[bool, str | None]:
    """Change the MP password after verifying the current one. Returns (ok, error)."""
    if not verify_mp_password(current):
        return False, "Current password is incorrect."
    if len(new_password) < 8:
        return False, "New password must be at least 8 characters."
    set_mp_password(new_password)
    return True, None


def start_mp_password_reset() -> None:
    """
    Issue a Managing Partner reset code and email it to the MP notification
    address. Raises if the email cannot be sent (so the UI can report it).
    """
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires = (datetime.now(timezone.utc) + timedelta(minutes=_RESET_TTL_MINUTES)).isoformat()
    conn = _conn()
    try:
        conn.execute(
            """
            INSERT INTO admin_credentials (admin_id, reset_token_hash, reset_token_expires, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(admin_id) DO UPDATE SET
                reset_token_hash = excluded.reset_token_hash,
                reset_token_expires = excluded.reset_token_expires
            """,
            (_ADMIN_ID, _hash_code(code), expires, datetime.now(timezone.utc).isoformat()),
        )
        write_audit_log(
            conn, event_type="mp_password_reset_requested", entity_type="admin",
            entity_id=_ADMIN_ID, description="Managing Partner password reset code issued.",
            performed_by="managing_partner",
        )
        conn.commit()
    finally:
        conn.close()

    from dashboard.email_helper import send_mp_reset_code
    send_mp_reset_code(code)


def complete_mp_password_reset(code: str, new_password: str) -> tuple[bool, str | None]:
    """Verify the MP reset code and set the new password. Returns (ok, error)."""
    admin = _load_admin()
    if not admin or not admin["reset_token_hash"]:
        return False, "No active reset request found. Request a new code."
    expires = admin["reset_token_expires"]
    if expires and datetime.fromisoformat(expires) < datetime.now(timezone.utc):
        return False, "That reset code has expired. Request a new one."
    if not hmac.compare_digest(_hash_code(code.strip()), admin["reset_token_hash"]):
        return False, "Incorrect reset code."
    if len(new_password) < 8:
        return False, "New password must be at least 8 characters."
    set_mp_password(new_password)
    return True, None


# ---------------------------------------------------------------------------
# Client login
# ---------------------------------------------------------------------------

def verify_client_credentials(email: str, password: str) -> tuple[dict | None, str | None]:
    """
    Verify email + password WITHOUT establishing a session.

    This is the first factor. The caller then decides whether a second factor
    (TOTP) is required before calling complete_client_login(). Returns
    (data, error). On success, data carries everything the login flow needs to
    decide on MFA: user_id, client_id, client_name, email, must_change,
    totp_enabled, engagement_type.
    """
    email = email.strip().lower()
    conn = _conn()
    try:
        row = conn.execute(
            """
            SELECT u.user_id, u.client_id, u.password_hash, u.must_change_password,
                   u.is_active, u.failed_attempts, u.totp_enabled, c.client_name,
                   c.engagement_type
            FROM client_users u
            JOIN clients c ON c.client_id = u.client_id
            WHERE u.email = ?
            """,
            (email,),
        ).fetchone()

        if row is None:
            return None, "No account found for that email."

        (user_id, client_id, pw_hash, must_change, is_active,
         failed, totp_enabled, client_name, engagement_type) = row

        if not is_active:
            return None, "This account is inactive. Contact Criterion Partners."
        if failed >= _MAX_FAILED_ATTEMPTS:
            return None, "Account locked after too many attempts. Use 'Forgot password'."

        if not verify_password(password, pw_hash):
            conn.execute(
                "UPDATE client_users SET failed_attempts = failed_attempts + 1 WHERE user_id = ?",
                (user_id,),
            )
            conn.commit()
            return None, "Incorrect password."

        return {
            "user_id": user_id,
            "client_id": client_id,
            "client_name": client_name,
            "email": email,
            "must_change": bool(must_change),
            "totp_enabled": bool(totp_enabled),
            "engagement_type": engagement_type,
        }, None
    finally:
        conn.close()


def mfa_required(data: dict) -> bool:
    """OEIL engagements mandate MFA; any client with TOTP enabled also requires it."""
    return bool(data.get("totp_enabled")) or (data.get("engagement_type") == "oeil")


def complete_client_login(user_id: str) -> None:
    """
    Establish the authenticated client session after all required factors pass.
    Resets the failed-attempt counter, stamps last_login, and audits the login.
    """
    conn = _conn()
    try:
        row = conn.execute(
            """
            SELECT u.client_id, u.email, u.must_change_password, c.client_name
            FROM client_users u JOIN clients c ON c.client_id = u.client_id
            WHERE u.user_id = ?
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            return
        client_id, email, must_change, client_name = row
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE client_users SET failed_attempts = 0, last_login = ? WHERE user_id = ?",
            (now, user_id),
        )
        write_audit_log(
            conn,
            event_type="session_login",
            description=f"Client portal login for '{client_name}'.",
            entity_type="client_user",
            entity_id=user_id,
            performed_by=f"client:{email}",
            metadata={"client_id": client_id},
        )
        conn.commit()
    finally:
        conn.close()

    st.session_state["auth_role"] = "client"
    st.session_state["auth_user_id"] = user_id
    st.session_state["auth_client_id"] = client_id
    st.session_state["auth_client_name"] = client_name
    st.session_state["auth_must_change"] = bool(must_change)


def verify_password_for_user(user_id: str, password: str) -> bool:
    """Verify a password for an already-identified user (used by the account page)."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT password_hash FROM client_users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return bool(row) and verify_password(password, row[0])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# TOTP / MFA
# ---------------------------------------------------------------------------

def _totp(secret: str):
    import pyotp
    return pyotp.TOTP(secret)


def provision_totp_secret(user_id: str) -> str:
    """
    Return a TOTP secret for enrollment, generating and storing one if needed.
    The secret is stored but totp_enabled stays 0 until enable_totp() succeeds,
    so a half-finished enrollment never locks anyone out.
    """
    import pyotp
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT totp_secret, totp_enabled FROM client_users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row and row[0] and not row[1]:
            return row[0]  # reuse pending secret across reruns
        secret = pyotp.random_base32()
        conn.execute(
            "UPDATE client_users SET totp_secret = ?, totp_enabled = 0 WHERE user_id = ?",
            (secret, user_id),
        )
        conn.commit()
        return secret
    finally:
        conn.close()


def totp_provisioning_uri(email: str, secret: str) -> str:
    """otpauth:// URI for authenticator apps (Google Authenticator, Authy, etc.)."""
    return _totp(secret).provisioning_uri(name=email, issuer_name="Criterion Partners")


def enable_totp(user_id: str, code: str) -> bool:
    """Verify the first code and turn MFA on. Returns True on success."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT totp_secret FROM client_users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row or not row[0]:
            return False
        if not _totp(row[0]).verify(code.strip(), valid_window=1):
            return False
        conn.execute("UPDATE client_users SET totp_enabled = 1 WHERE user_id = ?", (user_id,))
        write_audit_log(
            conn, event_type="client_user_mfa_enabled", entity_type="client_user",
            entity_id=user_id, description="Client enabled TOTP multi-factor authentication.",
            performed_by="client",
        )
        conn.commit()
        return True
    finally:
        conn.close()


def verify_totp(user_id: str, code: str) -> bool:
    """Verify a TOTP code for a user whose MFA is enabled."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT totp_secret, totp_enabled FROM client_users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row or not row[0] or not row[1]:
            return False
        return _totp(row[0]).verify(code.strip(), valid_window=1)
    finally:
        conn.close()


def disable_totp(user_id: str) -> None:
    """Turn MFA off and clear the secret (Managing Partner or client action)."""
    conn = _conn()
    try:
        conn.execute(
            "UPDATE client_users SET totp_enabled = 0, totp_secret = NULL WHERE user_id = ?",
            (user_id,),
        )
        write_audit_log(
            conn, event_type="client_user_mfa_disabled", entity_type="client_user",
            entity_id=user_id, description="TOTP multi-factor authentication disabled.",
            performed_by="client",
        )
        conn.commit()
    finally:
        conn.close()


def totp_enabled_for(user_id: str) -> bool:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT totp_enabled FROM client_users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return bool(row and row[0])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Client account management (Managing Partner actions)
# ---------------------------------------------------------------------------

def client_user_for(client_id: str) -> dict | None:
    """Return the client_users row for a client, or None if no account exists."""
    conn = _conn()
    try:
        row = conn.execute(
            """
            SELECT user_id, email, must_change_password, is_active, last_login, created_at
            FROM client_users WHERE client_id = ?
            """,
            (client_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "user_id": row[0], "email": row[1], "must_change_password": row[2],
            "is_active": row[3], "last_login": row[4], "created_at": row[5],
        }
    finally:
        conn.close()


def generate_temp_password() -> str:
    """Generate a readable temporary password for a new client account."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "CP-" + "".join(secrets.choice(alphabet) for _ in range(8))


def create_client_user(client_id: str, email: str, password: str) -> str:
    """
    Create a portal login for a client. The client must change the password
    on first login. Returns the new user_id.

    Raises ValueError if an account already exists for this client or email.
    """
    email = email.strip().lower()
    conn = _conn()
    try:
        existing = conn.execute(
            "SELECT 1 FROM client_users WHERE client_id = ? OR email = ?",
            (client_id, email),
        ).fetchone()
        if existing:
            raise ValueError("An account already exists for this client or email address.")

        user_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO client_users
                (user_id, client_id, email, password_hash, must_change_password, is_active)
            VALUES (?, ?, ?, ?, 1, 1)
            """,
            (user_id, client_id, email, hash_password(password)),
        )
        write_audit_log(
            conn,
            event_type="client_user_created",
            description=f"Client portal account created for {email}.",
            entity_type="client_user",
            entity_id=user_id,
            performed_by="managing_partner",
            metadata={"client_id": client_id, "email": email},
        )
        conn.commit()
        return user_id
    finally:
        conn.close()


def set_client_active(user_id: str, active: bool) -> None:
    """Activate or deactivate a client's portal account."""
    conn = _conn()
    try:
        conn.execute(
            "UPDATE client_users SET is_active = ? WHERE user_id = ?",
            (1 if active else 0, user_id),
        )
        write_audit_log(
            conn,
            event_type="client_user_updated",
            description=f"Client account {'activated' if active else 'deactivated'}.",
            entity_type="client_user",
            entity_id=user_id,
            performed_by="managing_partner",
            metadata={"is_active": active},
        )
        conn.commit()
    finally:
        conn.close()


def admin_reset_password(user_id: str, new_password: str) -> None:
    """Managing Partner sets a new temporary password; forces change on next login."""
    conn = _conn()
    try:
        conn.execute(
            """
            UPDATE client_users
            SET password_hash = ?, must_change_password = 1, failed_attempts = 0,
                reset_token_hash = NULL, reset_token_expires = NULL
            WHERE user_id = ?
            """,
            (hash_password(new_password), user_id),
        )
        write_audit_log(
            conn,
            event_type="client_user_password_reset",
            description="Managing Partner reset a client portal password.",
            entity_type="client_user",
            entity_id=user_id,
            performed_by="managing_partner",
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Client self-service password change
# ---------------------------------------------------------------------------

def change_own_password(user_id: str, new_password: str) -> None:
    """Client sets their own password and clears the must-change flag."""
    conn = _conn()
    try:
        conn.execute(
            """
            UPDATE client_users
            SET password_hash = ?, must_change_password = 0, failed_attempts = 0
            WHERE user_id = ?
            """,
            (hash_password(new_password), user_id),
        )
        write_audit_log(
            conn,
            event_type="client_user_password_changed",
            description="Client changed their own portal password.",
            entity_type="client_user",
            entity_id=user_id,
            performed_by="client",
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Forgot-password (emailed reset code) flow
# ---------------------------------------------------------------------------

def start_password_reset(email: str) -> bool:
    """
    Begin a reset: generate a 6-digit code, store its hash + expiry, and email
    it. Returns True if an account exists (caller should always show the same
    neutral message to avoid leaking which emails are registered).
    """
    email = email.strip().lower()
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT user_id FROM client_users WHERE email = ? AND is_active = 1",
            (email,),
        ).fetchone()
        if row is None:
            return False

        user_id = row[0]
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires = (datetime.now(timezone.utc) + timedelta(minutes=_RESET_TTL_MINUTES)).isoformat()
        conn.execute(
            "UPDATE client_users SET reset_token_hash = ?, reset_token_expires = ? WHERE user_id = ?",
            (_hash_code(code), expires, user_id),
        )
        write_audit_log(
            conn,
            event_type="client_user_reset_requested",
            description=f"Password reset code issued for {email}.",
            entity_type="client_user",
            entity_id=user_id,
            performed_by="client",
        )
        conn.commit()

        # Send after commit so a mail failure does not roll back the token.
        from dashboard.email_helper import send_reset_code
        send_reset_code(email, code)
        return True
    finally:
        conn.close()


def complete_password_reset(email: str, code: str, new_password: str) -> tuple[bool, str | None]:
    """Verify a reset code and set the new password. Returns (ok, error)."""
    email = email.strip().lower()
    conn = _conn()
    try:
        row = conn.execute(
            """
            SELECT user_id, reset_token_hash, reset_token_expires
            FROM client_users WHERE email = ? AND is_active = 1
            """,
            (email,),
        ).fetchone()
        if row is None or not row[1]:
            return False, "No active reset request found for that email."

        user_id, token_hash, expires = row
        if expires and datetime.fromisoformat(expires) < datetime.now(timezone.utc):
            return False, "That reset code has expired. Request a new one."
        if not hmac.compare_digest(_hash_code(code.strip()), token_hash):
            return False, "Incorrect reset code."

        conn.execute(
            """
            UPDATE client_users
            SET password_hash = ?, must_change_password = 0, failed_attempts = 0,
                reset_token_hash = NULL, reset_token_expires = NULL
            WHERE user_id = ?
            """,
            (hash_password(new_password), user_id),
        )
        write_audit_log(
            conn,
            event_type="client_user_password_reset",
            description=f"Client completed password reset for {email}.",
            entity_type="client_user",
            entity_id=user_id,
            performed_by="client",
        )
        conn.commit()
        return True, None
    finally:
        conn.close()
