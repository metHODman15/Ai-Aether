"""Tests for backend.pkce — PKCE verifier/challenge and OAuth state helpers."""
from __future__ import annotations

import base64
import hashlib
import re
import string

import pytest

from backend.pkce import (
    PKCEPair,
    generate_pkce_pair,
    generate_state,
)


# RFC 7636 §4.1: unreserved ASCII chars only.
_UNRESERVED = set(string.ascii_letters + string.digits + "-._~")


def _b64url_recompute_challenge(verifier: str) -> str:
    """Recompute the SHA-256 challenge from a verifier and return it
    base64url-encoded with no padding — exactly as build_authorize_url
    expects."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def test_generate_pkce_pair_default_length():
    pair = generate_pkce_pair()
    assert isinstance(pair, PKCEPair)
    assert pair.method == "S256"
    # 32 random bytes → 43 base64url chars (no padding) — RFC 7636 minimum.
    assert len(pair.verifier) == 43


def test_pkce_verifier_only_unreserved_chars():
    pair = generate_pkce_pair()
    assert set(pair.verifier).issubset(_UNRESERVED)


def test_pkce_verifier_no_base64_padding():
    pair = generate_pkce_pair()
    assert "=" not in pair.verifier
    assert "=" not in pair.challenge


def test_pkce_challenge_matches_sha256_of_verifier():
    pair = generate_pkce_pair()
    assert pair.challenge == _b64url_recompute_challenge(pair.verifier)


def test_pkce_pairs_are_unique():
    pairs = {generate_pkce_pair().verifier for _ in range(50)}
    assert len(pairs) == 50, "Verifier must be cryptographically random."


def test_pkce_max_length():
    pair = generate_pkce_pair(verifier_bytes=96)
    # 96 bytes → 128 base64url chars (RFC 7636 max).
    assert len(pair.verifier) == 128


@pytest.mark.parametrize("bad", [0, 1, 31, 97, 200])
def test_pkce_invalid_length_raises(bad: int):
    with pytest.raises(ValueError):
        generate_pkce_pair(verifier_bytes=bad)


def test_generate_state_unique_and_url_safe():
    states = {generate_state() for _ in range(50)}
    assert len(states) == 50
    for s in states:
        assert re.fullmatch(r"[A-Za-z0-9\-_]+", s)
        assert "=" not in s
