"""
dashboard/crypto.py
CPOI Platform -- application-layer encryption for portal messages.

Per the platform spec (Part 9 / Part 10), client portal messages are stored as
AES-256 encrypted blobs at the application layer, on top of SQLCipher's
at-rest AES-256 encryption (defense in depth).

The encryption key is read from CPOI_MESSAGE_KEY and is held only by the
server process -- never in the database. Generate one with:

    python -c "import secrets; print(secrets.token_hex(32))"

and set it in the environment (Infisical in production):

    export CPOI_MESSAGE_KEY=<64-hex-characters>

Graceful degradation: if CPOI_MESSAGE_KEY is not set, messages are stored
without the app-layer cipher (still AES-256 encrypted at rest by SQLCipher).
encryption_available() lets the UI surface that state.

Format of an encrypted blob (base64 of):  nonce(12 bytes) || ciphertext+tag
"""

from __future__ import annotations

import base64
import os


def _load_key() -> bytes | None:
    raw = os.environ.get("CPOI_MESSAGE_KEY", "").strip()
    if not raw:
        return None
    # Accept 64-char hex (preferred) or base64 of 32 bytes.
    try:
        if len(raw) == 64:
            key = bytes.fromhex(raw)
        else:
            key = base64.b64decode(raw)
    except (ValueError, base64.binascii.Error):
        return None
    return key if len(key) == 32 else None


def encryption_available() -> bool:
    """True if a valid 256-bit CPOI_MESSAGE_KEY is configured."""
    return _load_key() is not None


def encrypt(plaintext: str) -> tuple[str, bool]:
    """
    Encrypt plaintext. Returns (stored_value, is_encrypted).

    If no key is configured, returns (plaintext, False) so messaging still
    works (relying on SQLCipher at-rest encryption).
    """
    key = _load_key()
    if key is None:
        return plaintext, False
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ct).decode("ascii"), True


def decrypt(stored: str, is_encrypted: bool) -> str:
    """Decrypt a stored value. Returns plaintext (or a notice if undecryptable)."""
    if not is_encrypted:
        return stored
    key = _load_key()
    if key is None:
        return "[Encrypted message -- CPOI_MESSAGE_KEY is not configured on this server.]"
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        blob = base64.b64decode(stored)
        nonce, ct = blob[:12], blob[12:]
        return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8")
    except Exception:
        return "[Unable to decrypt this message.]"
