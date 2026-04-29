"""Encrypted SQLite storage for Salesforce OAuth tokens.

Tokens are encrypted with Fernet symmetric encryption before being
persisted to disk. The encryption key is derived from the
``ENCRYPTION_KEY`` environment variable via PBKDF2. This means even if
the database file is copied, the tokens are unreadable without the key.

Token shape stored (as JSON, then encrypted):
    {
        "access_token": "...",
        "refresh_token": "...",
        "instance_url": "https://your-org.salesforce.com",
        "issued_at": 1714000000.0   # float epoch — time of last refresh
    }
"""
from __future__ import annotations

import base64
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

logger = logging.getLogger(__name__)

# Row key used for the single Salesforce OAuth token row.
_SF_KEY = "salesforce"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS oauth_tokens (
    key  TEXT PRIMARY KEY,
    data BLOB NOT NULL
);
"""


def _derive_fernet(encryption_key: str) -> Fernet:
    """Derive a Fernet key from an arbitrary-length string via PBKDF2.

    Uses a fixed salt so the same key always derives the same Fernet key.
    The salt is not secret — it only ensures that two different
    ENCRYPTION_KEYs produce different Fernet keys.
    """
    salt = b"ai-aether-sf-oauth-v1"
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100_000,
    )
    raw = kdf.derive(encryption_key.encode())
    fernet_key = base64.urlsafe_b64encode(raw)
    return Fernet(fernet_key)


class TokenStore:
    """Thread-safe encrypted token store backed by SQLite."""

    def __init__(self, db_path: Path, encryption_key: str) -> None:
        self._db_path = str(db_path)
        self._fernet = _derive_fernet(encryption_key)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        # See the matching note in MeetingStore._init_db: this store
        # shares the SQLite file with MeetingStore but owns only the
        # `oauth_tokens` table. Each `CREATE TABLE IF NOT EXISTS` is a
        # no-op once the table exists, so the two stores can both call
        # _init_db at startup safely. There is no migration framework;
        # if the encrypted-token schema ever changes, bump it via a
        # one-shot delete of `meetings.db` (you'll have to re-OAuth).
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # ── Public API ───────────────────────────────────────────────────────

    def save(self, tokens: dict[str, Any]) -> None:
        """Encrypt and persist ``tokens``. Overwrites any previous value."""
        plaintext = json.dumps(tokens).encode()
        ciphertext = self._fernet.encrypt(plaintext)
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO oauth_tokens (key, data) VALUES (?, ?)",
                (_SF_KEY, ciphertext),
            )
        logger.info("OAuth tokens saved (encrypted).")

    def load(self) -> dict[str, Any] | None:
        """Return decrypted tokens, or ``None`` if none are stored."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM oauth_tokens WHERE key = ?", (_SF_KEY,)
            ).fetchone()
        if row is None:
            return None
        try:
            plaintext = self._fernet.decrypt(bytes(row[0]))
            return json.loads(plaintext)
        except (InvalidToken, json.JSONDecodeError) as exc:
            logger.warning(
                "Could not decrypt stored OAuth tokens (%s); treating as absent.", exc
            )
            return None

    def clear(self) -> None:
        """Remove stored tokens (e.g. after revocation)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM oauth_tokens WHERE key = ?", (_SF_KEY,))
        logger.info("OAuth tokens cleared.")

    def has_tokens(self) -> bool:
        """Return True if any tokens are stored (does not validate them)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM oauth_tokens WHERE key = ?", (_SF_KEY,)
            ).fetchone()
        return row is not None
