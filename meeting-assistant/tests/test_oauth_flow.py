"""Tests for backend.oauth (ECA + PKCE) and backend.token_store.

Covers:
  - build_authorize_url: includes code_challenge, S256, state, scopes
  - exchange_code: happy path (public ECA, no secret), confidential ECA,
    HTTP error path, missing issued_at fallback
  - refresh_access_token: happy path, HTTP error (OAuthError), refresh
    token preserved across rotation
  - TokenStore: round-trip encryption, clear, has_tokens, wrong-key isolation
"""
from __future__ import annotations

import json
import time
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.oauth import (
    DEFAULT_SCOPES,
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
        code_challenge="abc123challenge",
        state="xyz789state",
    )
    assert url.startswith("https://login.salesforce.com/services/oauth2/authorize?")
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    assert qs["response_type"] == ["code"]
    assert qs["client_id"] == ["MY_CLIENT_ID"]
    assert qs["redirect_uri"] == ["http://localhost:8000/oauth/callback"]
    assert qs["code_challenge"] == ["abc123challenge"]
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["state"] == ["xyz789state"]
    assert qs["scope"] == [DEFAULT_SCOPES]


def test_build_authorize_url_sandbox():
    url = build_authorize_url(
        client_id="CID",
        redirect_uri="http://localhost:8000/oauth/callback",
        code_challenge="c",
        state="s",
        login_url="https://test.salesforce.com",
    )
    assert url.startswith("https://test.salesforce.com/services/oauth2/authorize?")


def test_build_authorize_url_strips_trailing_slash():
    url = build_authorize_url(
        client_id="CID",
        redirect_uri="http://localhost/oauth/callback",
        code_challenge="c",
        state="s",
        login_url="https://login.salesforce.com/",  # trailing slash
    )
    assert "//services" not in url


def test_build_authorize_url_custom_scopes():
    url = build_authorize_url(
        client_id="CID",
        redirect_uri="http://localhost/cb",
        code_challenge="c",
        state="s",
        scopes="api refresh_token offline_access mcp",
    )
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert qs["scope"] == ["api refresh_token offline_access mcp"]


# ── exchange_code ─────────────────────────────────────────────────────────────

def _fake_httpx_response(status: int, body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body
    resp.text = json.dumps(body)
    return resp


def test_exchange_code_public_eca_omits_client_secret():
    """A public ECA must not send client_secret to the token endpoint —
    PKCE proves the requester's identity instead."""
    body = {
        "access_token": "ACC",
        "refresh_token": "REF",
        "instance_url": "https://myorg.salesforce.com",
        "issued_at": str(int(time.time() * 1000)),
    }
    with patch("backend.oauth.httpx.post", return_value=_fake_httpx_response(200, body)) as mp:
        result = exchange_code(
            client_id="CID",
            redirect_uri="http://localhost:8000/oauth/callback",
            code="AUTH_CODE",
            code_verifier="VERIFIER",
        )
    assert result["access_token"] == "ACC"
    assert result["refresh_token"] == "REF"
    assert result["instance_url"] == "https://myorg.salesforce.com"
    assert isinstance(result["issued_at"], float)

    # Inspect the actual POST payload — client_secret must be absent.
    _args, kwargs = mp.call_args
    sent = kwargs["data"]
    assert "client_secret" not in sent
    assert sent["code_verifier"] == "VERIFIER"
    assert sent["grant_type"] == "authorization_code"


def test_exchange_code_confidential_eca_includes_client_secret():
    body = {
        "access_token": "A", "refresh_token": "R",
        "instance_url": "https://o.salesforce.com",
        "issued_at": str(int(time.time() * 1000)),
    }
    with patch("backend.oauth.httpx.post", return_value=_fake_httpx_response(200, body)) as mp:
        exchange_code(
            client_id="CID",
            redirect_uri="http://localhost/cb",
            code="CODE",
            code_verifier="V",
            client_secret="CSEC",
        )
    sent = mp.call_args.kwargs["data"]
    assert sent["client_secret"] == "CSEC"


def test_exchange_code_bad_status_raises_oauth_error():
    body = {"error": "invalid_grant", "error_description": "expired code"}
    with patch("backend.oauth.httpx.post", return_value=_fake_httpx_response(400, body)):
        with pytest.raises(OAuthError, match="400"):
            exchange_code(
                client_id="CID",
                redirect_uri="http://localhost:8000/oauth/callback",
                code="BAD_CODE",
                code_verifier="V",
            )


def test_exchange_code_missing_issued_at_defaults_to_now():
    body = {
        "access_token": "ACC", "refresh_token": "REF",
        "instance_url": "https://myorg.salesforce.com",
    }
    before = time.time()
    with patch("backend.oauth.httpx.post", return_value=_fake_httpx_response(200, body)):
        result = exchange_code("CID", "http://localhost/cb", "CODE", "V")
    after = time.time()
    assert before <= result["issued_at"] <= after


# ── refresh_access_token ──────────────────────────────────────────────────────

def test_refresh_access_token_happy_path_public_eca():
    body = {
        "access_token": "NEW_ACC",
        "instance_url": "https://myorg.salesforce.com",
        "issued_at": str(int(time.time() * 1000)),
    }
    with patch("backend.oauth.httpx.post", return_value=_fake_httpx_response(200, body)) as mp:
        result = refresh_access_token(client_id="CID", refresh_token="OLD_REF")
    assert result["access_token"] == "NEW_ACC"
    # Salesforce keeps the same refresh token across the rotation.
    assert result["refresh_token"] == "OLD_REF"
    assert result["instance_url"] == "https://myorg.salesforce.com"
    sent = mp.call_args.kwargs["data"]
    assert "client_secret" not in sent


def test_refresh_access_token_confidential_eca_includes_secret():
    body = {
        "access_token": "NEW",
        "instance_url": "https://o.salesforce.com",
        "issued_at": str(int(time.time() * 1000)),
    }
    with patch("backend.oauth.httpx.post", return_value=_fake_httpx_response(200, body)) as mp:
        refresh_access_token(client_id="CID", refresh_token="R", client_secret="CSEC")
    sent = mp.call_args.kwargs["data"]
    assert sent["client_secret"] == "CSEC"


def test_refresh_access_token_bad_status_raises_oauth_error():
    body = {"error": "invalid_grant", "error_description": "token revoked"}
    with patch("backend.oauth.httpx.post", return_value=_fake_httpx_response(401, body)):
        with pytest.raises(OAuthError, match="401"):
            refresh_access_token("CID", "REVOKED_REF")


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
    store_a = TokenStore(db_path=tmp_path / "shared.db", encryption_key="key_A")
    store_a.save({"access_token": "SECRET", "refresh_token": "R", "instance_url": "U", "issued_at": 0.0})
    store_b = TokenStore(db_path=tmp_path / "shared.db", encryption_key="key_B")
    assert store_b.load() is None


def test_token_store_same_key_different_instances(tmp_path: Path):
    store1 = TokenStore(db_path=tmp_path / "shared.db", encryption_key="shared_key")
    store2 = TokenStore(db_path=tmp_path / "shared.db", encryption_key="shared_key")
    store1.save({"access_token": "ACC", "refresh_token": "R", "instance_url": "U", "issued_at": 0.0})
    loaded = store2.load()
    assert loaded is not None
    assert loaded["access_token"] == "ACC"


def test_token_store_clear_idempotent(tmp_store: TokenStore):
    tmp_store.clear()
    tmp_store.clear()
    assert tmp_store.has_tokens() is False
