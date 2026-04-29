"""Salesforce OAuth 2.0 (External Client App + PKCE) helpers.

Implements the OAuth 2.0 Authorization Code flow with PKCE (RFC 7636) for
Salesforce External Client Apps (ECAs). PKCE replaces the static client
secret on the redemption step, so this module supports both:

* **Public ECA** (recommended): no ``client_secret`` is sent to the token
  endpoint — possession of the matching ``code_verifier`` proves the
  requester's identity.
* **Confidential ECA** (legacy): ``client_secret`` is also sent. Pass it
  via the ``client_secret`` argument; an empty string means "public".

Functions:
  - build_authorize_url   → redirect URL with ``code_challenge``, ``state``,
                            and the configured scopes
  - exchange_code         → trade an authorization code + verifier for tokens
  - refresh_access_token  → use a refresh token to get a new access token

All network calls use ``httpx``. No access tokens, refresh tokens, code
verifiers, or other credential material is ever logged.
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

# Scopes the External Client App requires by default. ``api`` for SOQL/MCP
# tool calls, ``refresh_token`` + ``offline_access`` so we can refresh the
# access token without bouncing the user back through the browser.
DEFAULT_SCOPES: str = "api refresh_token offline_access"


class OAuthError(RuntimeError):
    """Raised when an OAuth exchange or refresh fails."""


def build_authorize_url(
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    state: str,
    login_url: str = "https://login.salesforce.com",
    scopes: str = DEFAULT_SCOPES,
) -> str:
    """Return the Salesforce Authorization endpoint URL for ECA + PKCE.

    The user's browser is redirected here; after login Salesforce redirects
    back to ``redirect_uri`` with ``?code=…&state=…``. The caller must
    remember the matching ``code_verifier`` (server-side, keyed by
    ``state``) so it can be presented to :func:`exchange_code`.
    """
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "scope": scopes,
        "prompt": "login consent",
    }
    return f"{login_url.rstrip('/')}{_AUTHORIZE_PATH}?" + urllib.parse.urlencode(params)


def exchange_code(
    client_id: str,
    redirect_uri: str,
    code: str,
    code_verifier: str,
    login_url: str = "https://login.salesforce.com",
    client_secret: str = "",
) -> dict[str, Any]:
    """Exchange an authorization code + PKCE verifier for tokens.

    The token endpoint receives:
      * ``client_id``
      * ``code``
      * ``code_verifier`` (proves we're the original requester)
      * ``client_secret`` **only when** one was configured (confidential
        ECA). Public ECAs omit it entirely — the verifier is the proof.

    Returns a dict with at least:
        access_token, refresh_token, instance_url, issued_at (float epoch)

    Raises :class:`OAuthError` on any failure.
    """
    token_url = f"{login_url.rstrip('/')}{_TOKEN_PATH}"
    payload: dict[str, str] = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code": code,
        "code_verifier": code_verifier,
    }
    if client_secret:
        payload["client_secret"] = client_secret

    logger.info("Exchanging OAuth authorization code for tokens (PKCE).")
    response = httpx.post(token_url, data=payload, timeout=15.0)
    if response.status_code != 200:
        raise OAuthError(
            f"Token exchange failed ({response.status_code}): {response.text}"
        )
    data = response.json()
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
    refresh_token: str,
    login_url: str = "https://login.salesforce.com",
    client_secret: str = "",
) -> dict[str, Any]:
    """Obtain a new access token using a refresh token.

    For public ECAs ``client_secret`` is omitted. For confidential ECAs
    the secret is also sent.

    Returns a dict with at least:
        access_token, refresh_token, instance_url, issued_at (float epoch)

    Raises :class:`OAuthError` if the refresh token has been revoked or
    expired (caller should clear stored tokens and broadcast
    ``auth_required``).
    """
    token_url = f"{login_url.rstrip('/')}{_TOKEN_PATH}"
    payload: dict[str, str] = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token,
    }
    if client_secret:
        payload["client_secret"] = client_secret

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
