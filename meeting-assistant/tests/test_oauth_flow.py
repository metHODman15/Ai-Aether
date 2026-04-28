"""Tests for backend.oauth and backend.token_store.

Covers:
  - build_authorize_url: correct URL shape and query parameters
  - exchange_code: happy path, HTTP error path
  - refresh_access_token: happy path, HTTP error (OAuthError) path
  - TokenStore: round-trip encryption, clear, has_tokens, wrong-key isolation
"""
from __future__ import annotations

import json
import tempfile
import time
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.oauth import (
    OAuthError,
    build_authorize_url,
    exchange_code,
    refresh_access_token,
)
from backend.token_store import TokenStore


# ── build_authorize_url ───────────────────────────────────────────────────────

def test_build_authorize_url_contains_required_params():
    url = build_authorize_url(
        client_id="MY_CLIENT_ID",
        redirect_uri="http://localhost:8000/oauth/callback",
        login_url="https://login.salesforce.com",
    )
    assert url.startswith("https://login.salesforce.com/services/oauth2/authorize?")
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    assert qs["response_type"] == ["code"]
    assert qs["client_id"] == ["MY_CLIENT_ID"]
    assert qs["redirect_uri"] == ["http://localhost:8000/oauth/callback"]


def test_build_authorize_url_sandbox():
    url = build_authorize_url(
        client_id="CID",
        redirect_uri="http://localhost:8000/oauth/callback",
        login_url="https://test.salesforce.com",
    )
    assert url.startswith("https://test.salesforce.com/services/oauth2/authorize?")


def test_build_authorize_url_strips_trailing_slash():
    url = build_authorize_url(
        client_id="CID",
        redirect_uri="http://localhost/oauth/callback",
        login_url="https://login.salesforce.com/",  # trailing slash
    )
    # Must not double-slash
    assert "//services" not in url


# ── exchange_code ─────────────────────────────────────────────────────────────

def _fake_httpx_response(status: int, body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body
    resp.text = json.dumps(body)
    return resp


def test_exchange_code_happy_path():
    body = {
        "access_token": "ACC",
        "refresh_token": "REF",
        "instance_url": "https://myorg.salesforce.com",
        "issued_at": str(int(time.time() * 1000)),
    }
    with patch("backend.oauth.httpx.post", return_value=_fake_httpx_response(200, body)):
        result = exchange_code(
            client_id="CID",
            client_secret="CSEC",
            redirect_uri="http://localhost:8000/oauth/callback",
            code="AUTH_CODE",
        )
    assert result["access_token"] == "ACC"
    assert result["refresh_token"] == "REF"
    assert result["instance_url"] == "https://myorg.salesforce.com"
    assert isinstance(result["issued_at"], float)
    assert result["issued_at"] > 0


def test_exchange_code_bad_status_raises_oauth_error():
    body = {"error": "invalid_grant", "error_description": "expired code"}
    with patch("backend.oauth.httpx.post", return_value=_fake_httpx_response(400, body)):
        with pytest.raises(OAuthError, match="400"):
            exchange_code(
                client_id="CID",
                client_secret="CSEC",
                redirect_uri="http://localhost:8000/oauth/callback",
                code="BAD_CODE",
            )


def test_exchange_code_missing_issued_at_defaults_to_now():
    """If Salesforce omits issued_at, fall back to time.time()."""
    body = {
        "access_token": "ACC",
        "refresh_token": "REF",
        "instance_url": "https://myorg.salesforce.com",
        # no issued_at
    }
    before = time.time()
    with patch("backend.oauth.httpx.post", return_value=_fake_httpx_response(200, body)):
        result = exchange_code("CID", "CSEC", "http://localhost/cb", "CODE")
    after = time.time()
    assert before <= result["issued_at"] <= after


# ── refresh_access_token ──────────────────────────────────────────────────────

def test_refresh_access_token_happy_path():
    body = {
        "access_token": "NEW_ACC",
        "instance_url": "https://myorg.salesforce.com",
        "issued_at": str(int(time.time() * 1000)),
    }
    with patch("backend.oauth.httpx.post", return_value=_fake_httpx_response(200, body)):
        result = refresh_access_token(
            client_id="CID",
            client_secret="CSEC",
            refresh_token="OLD_REF",
        )
    assert result["access_token"] == "NEW_ACC"
    # Salesforce does not rotate the refresh token — same value preserved.
    assert result["refresh_token"] == "OLD_REF"
    assert result["instance_url"] == "https://myorg.salesforce.com"


def test_refresh_access_token_bad_status_raises_oauth_error():
    body = {"error": "invalid_grant", "error_description": "token revoked"}
    with patch("backend.oauth.httpx.post", return_value=_fake_httpx_response(401, body)):
        with pytest.raises(OAuthError, match="401"):
            refresh_access_token("CID", "CSEC", "REVOKED_REF")


# ── TokenStore ────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_store(tmp_path: Path) -> TokenStore:
    return TokenStore(db_path=tmp_path / "test.db", encryption_key="test_key_12345")


def test_token_store_round_trip(tmp_store: TokenStore):
    tokens = {
        "access_token": "ACC",
        "refresh_token": "REF",
        "instance_url": "https://myorg.salesforce.com",
        "issued_at": 1714000000.0,
    }
    assert tmp_store.has_tokens() is False
    assert tmp_store.load() is None

    tmp_store.save(tokens)
    assert tmp_store.has_tokens() is True

    loaded = tmp_store.load()
    assert loaded is not None
    assert loaded["access_token"] == "ACC"
    assert loaded["refresh_token"] == "REF"
    assert loaded["instance_url"] == "https://myorg.salesforce.com"
    assert loaded["issued_at"] == pytest.approx(1714000000.0)


def test_token_store_clear(tmp_store: TokenStore):
    tmp_store.save({"access_token": "X", "refresh_token": "Y", "instance_url": "Z", "issued_at": 0.0})
    assert tmp_store.has_tokens() is True

    tmp_store.clear()
    assert tmp_store.has_tokens() is False
    assert tmp_store.load() is None


def test_token_store_overwrite(tmp_store: TokenStore):
    tmp_store.save({"access_token": "OLD", "refresh_token": "R", "instance_url": "U", "issued_at": 1.0})
    tmp_store.save({"access_token": "NEW", "refresh_token": "R2", "instance_url": "U2", "issued_at": 2.0})

    loaded = tmp_store.load()
    assert loaded is not None
    assert loaded["access_token"] == "NEW"


def test_token_store_wrong_key_returns_none(tmp_path: Path):
    """Tokens encrypted with key A must not be readable by key B."""
    store_a = TokenStore(db_path=tmp_path / "shared.db", encryption_key="key_A")
    store_a.save({"access_token": "SECRET", "refresh_token": "R", "instance_url": "U", "issued_at": 0.0})

    store_b = TokenStore(db_path=tmp_path / "shared.db", encryption_key="key_B")
    # Wrong key → decrypt fails gracefully → returns None.
    result = store_b.load()
    assert result is None


def test_token_store_same_key_different_instances(tmp_path: Path):
    """Two stores sharing the same key and DB file must share the same tokens."""
    store1 = TokenStore(db_path=tmp_path / "shared.db", encryption_key="shared_key")
    store2 = TokenStore(db_path=tmp_path / "shared.db", encryption_key="shared_key")

    store1.save({"access_token": "ACC", "refresh_token": "R", "instance_url": "U", "issued_at": 0.0})
    loaded = store2.load()
    assert loaded is not None
    assert loaded["access_token"] == "ACC"


def test_token_store_clear_idempotent(tmp_store: TokenStore):
    """Clearing an empty store must not raise."""
    tmp_store.clear()  # no tokens yet
    tmp_store.clear()  # again — should not raise
    assert tmp_store.has_tokens() is False
