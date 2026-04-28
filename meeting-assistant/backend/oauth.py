"""Salesforce OAuth 2.0 Web Server Flow helpers.

Implements:
  - build_authorize_url   → redirect URL for the Salesforce login page
  - exchange_code         → trade an authorization code for access+refresh tokens
  - refresh_access_token  → use a refresh token to get a new access token

All network calls use ``httpx`` (already a transitive dependency of
``httpcore``).  No access tokens, refresh tokens, or other credential
material is ever logged.
"""
from __future__ import annotations

import logging
import time
import urllib.parse
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Where the Salesforce OAuth endpoints live on a given instance.
_TOKEN_PATH = "/services/oauth2/token"
_AUTHORIZE_PATH = "/services/oauth2/authorize"


def build_authorize_url(
    client_id: str,
    redirect_uri: str,
    login_url: str = "https://login.salesforce.com",
) -> str:
    """Return the Salesforce Authorization endpoint URL for the Web Server Flow.

    The user's browser is redirected here; after login Salesforce
    redirects back to ``redirect_uri`` with ``?code=…``.
    """
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "prompt": "login consent",
    }
    return f"{login_url.rstrip('/')}{_AUTHORIZE_PATH}?" + urllib.parse.urlencode(params)


def exchange_code(
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
    login_url: str = "https://login.salesforce.com",
) -> dict[str, Any]:
    """Exchange an authorization code for an access + refresh token pair.

    Returns a dict with at least:
        access_token, refresh_token, instance_url, issued_at (float epoch)

    Raises ``OAuthError`` on any failure.
    """
    token_url = f"{login_url.rstrip('/')}{_TOKEN_PATH}"
    payload = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "code": code,
    }
    logger.info("Exchanging OAuth authorization code for tokens.")
    response = httpx.post(token_url, data=payload, timeout=15.0)
    if response.status_code != 200:
        raise OAuthError(
            f"Token exchange failed ({response.status_code}): {response.text}"
        )
    data = response.json()
    # Normalize: Salesforce returns issued_at as a millisecond epoch string.
    issued_at_raw = data.get("issued_at", "")
    try:
        issued_at = float(issued_at_raw) / 1000.0
    except (ValueError, TypeError):
        issued_at = time.time()

    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", ""),
        "instance_url": data.get("instance_url", login_url),
        "issued_at": issued_at,
    }


def refresh_access_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    login_url: str = "https://login.salesforce.com",
) -> dict[str, Any]:
    """Obtain a new access token using a refresh token.

    Returns a dict with at least:
        access_token, instance_url, issued_at (float epoch)

    Raises ``OAuthError`` if the refresh token has been revoked or expired
    (caller should clear stored tokens and broadcast auth_required).
    """
    token_url = f"{login_url.rstrip('/')}{_TOKEN_PATH}"
    payload = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }
    logger.info("Refreshing Salesforce access token via refresh_token grant.")
    response = httpx.post(token_url, data=payload, timeout=15.0)
    if response.status_code != 200:
        raise OAuthError(
            f"Token refresh failed ({response.status_code}): {response.text}"
        )
    data = response.json()
    issued_at_raw = data.get("issued_at", "")
    try:
        issued_at = float(issued_at_raw) / 1000.0
    except (ValueError, TypeError):
        issued_at = time.time()

    return {
        "access_token": data["access_token"],
        "refresh_token": refresh_token,   # Salesforce keeps the same refresh token
        "instance_url": data.get("instance_url", login_url),
        "issued_at": issued_at,
    }


class OAuthError(RuntimeError):
    """Raised when an OAuth exchange or refresh fails."""
