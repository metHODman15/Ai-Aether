"""PKCE (Proof Key for Code Exchange — RFC 7636) and OAuth state helpers.

Used by the External Client App (ECA) Salesforce flow. The verifier is a
high-entropy random string the client keeps secret; the challenge is the
URL-safe base64 SHA-256 of the verifier (no padding) sent to the
authorization server. On the callback, the client presents the same
verifier so the authorization server can prove the original requester is
the one redeeming the code — even when no client secret is configured
(public ECAs).

These helpers are pure; they have no I/O and never log secret material.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
from typing import NamedTuple

# RFC 7636 §4.1: code_verifier length must be 43–128 characters from the
# unreserved set [A-Z / a-z / 0-9 / "-" / "." / "_" / "~"].
_VERIFIER_MIN_BYTES = 32   # 32 random bytes → 43 base64url chars (no padding)
_VERIFIER_MAX_BYTES = 96   # 96 random bytes → 128 base64url chars

# Length of the OAuth ``state`` parameter in random bytes.
_STATE_BYTES = 32


class PKCEPair(NamedTuple):
    """A matched (code_verifier, code_challenge) pair plus the method tag."""

    verifier: str
    challenge: str
    method: str  # always "S256"


def _b64url_no_pad(raw: bytes) -> str:
    """URL-safe base64 with the trailing '=' padding stripped (RFC 7636)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def generate_pkce_pair(verifier_bytes: int = _VERIFIER_MIN_BYTES) -> PKCEPair:
    """Generate a fresh PKCE verifier+challenge pair using SHA-256.

    The verifier is ``verifier_bytes`` of cryptographic randomness encoded
    as URL-safe base64 (no padding). Defaults to 32 bytes → 43 chars,
    the RFC 7636 minimum, which is what Salesforce documents as supported.
    """
    if not (_VERIFIER_MIN_BYTES <= verifier_bytes <= _VERIFIER_MAX_BYTES):
        raise ValueError(
            f"verifier_bytes must be between {_VERIFIER_MIN_BYTES} and "
            f"{_VERIFIER_MAX_BYTES} (got {verifier_bytes})."
        )
    verifier = _b64url_no_pad(secrets.token_bytes(verifier_bytes))
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = _b64url_no_pad(digest)
    return PKCEPair(verifier=verifier, challenge=challenge, method="S256")


def generate_state() -> str:
    """Return a URL-safe random ``state`` parameter for CSRF protection."""
    return _b64url_no_pad(secrets.token_bytes(_STATE_BYTES))
