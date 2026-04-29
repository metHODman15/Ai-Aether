"""Tests for backend.config.validate_credentials.

These tests cover the contract — Anthropic auth failures are fatal,
Salesforce is validated implicitly through the OAuth UI flow (not at
startup), and SKIP_STARTUP_VALIDATION short-circuits the entire flow —
without making any real network calls.
"""
from __future__ import annotations

import sys
import types
import pytest

from backend import config as config_mod
from backend.config import Config, ConfigError, validate_credentials


def _make_config(skip: bool = False) -> Config:
    return Config(
        openai_api_key="sk-test",
        anthropic_api_key="sk-ant-test",
        sf_client_id="3MVGtest_client_id",
        sf_client_secret="",  # public ECA — no secret
        sf_login_url="https://login.salesforce.com",
        sf_mcp_server_url="https://example.com/mcp",
        sf_mcp_scopes="api refresh_token offline_access",
        encryption_key="deadbeef" * 8,  # 64-char hex string
        host="0.0.0.0",
        port=8000,
        audio_chunk_seconds=5.0,
        audio_sample_rate=16000,
        whisper_backend="openai",
        local_whisper_model="base",
        local_whisper_device="cpu",
        local_whisper_compute_type="int8",
        log_level="INFO",
        sf_session_timeout_minutes=30,
        sf_mcp_timeout_seconds=30.0,
        skip_startup_validation=skip,
    )


def test_skip_startup_validation_short_circuits():
    cfg = _make_config(skip=True)
    result = validate_credentials(cfg)
    assert result == {"anthropic": "skipped", "salesforce": "mcp_oauth_flow"}


def test_salesforce_always_returns_mcp_oauth_flow_status(monkeypatch):
    """Salesforce is validated through the MCP + OAuth UI flow, not at startup.
    validate_credentials must always return 'mcp_oauth_flow' for salesforce,
    regardless of whether credentials are present or not."""

    # Make Anthropic check pass cleanly.
    class FakeAnthropic:
        def __init__(self, api_key):
            self.models = types.SimpleNamespace(list=lambda **_: object())

    monkeypatch.setitem(
        sys.modules, "anthropic",
        types.SimpleNamespace(
            Anthropic=FakeAnthropic, APIError=Exception, AuthenticationError=Exception,
        ),
    )

    result = validate_credentials(_make_config())
    assert result["anthropic"] == "ok"
    assert result["salesforce"] == "mcp_oauth_flow"


def test_anthropic_auth_failure_is_fatal(monkeypatch):
    """A bad ANTHROPIC_API_KEY must abort startup with ConfigError."""

    class FakeAuthError(Exception):
        pass

    class FakeAPIError(Exception):
        pass

    class FakeAnthropic:
        def __init__(self, api_key):
            self.models = types.SimpleNamespace(
                list=lambda **_: (_ for _ in ()).throw(FakeAuthError("bad key")),
            )

    fake_mod = types.SimpleNamespace(
        Anthropic=FakeAnthropic,
        APIError=FakeAPIError,
        AuthenticationError=FakeAuthError,
    )
    monkeypatch.setitem(sys.modules, "anthropic", fake_mod)

    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY is invalid"):
        validate_credentials(_make_config())


def test_anthropic_transient_failure_is_degraded_not_fatal(monkeypatch):
    """A transient Anthropic APIError must not abort startup."""

    class FakeAPIError(Exception):
        pass

    class FakeAnthropic:
        def __init__(self, api_key):
            self.models = types.SimpleNamespace(
                list=lambda **_: (_ for _ in ()).throw(FakeAPIError("timeout")),
            )

    monkeypatch.setitem(
        sys.modules, "anthropic",
        types.SimpleNamespace(
            Anthropic=FakeAnthropic,
            APIError=FakeAPIError,
            AuthenticationError=type("_Never", (Exception,), {}),
        ),
    )

    result = validate_credentials(_make_config())
    assert result["anthropic"].startswith("degraded")
    assert result["salesforce"] == "mcp_oauth_flow"


def test_oauth_redirect_uri_uses_host_and_port():
    cfg = _make_config()
    assert cfg.oauth_redirect_uri == "http://0.0.0.0:8000/oauth/callback"


def test_sf_login_url_defaults_to_production():
    """When neither SF_LOGIN_URL nor SF_DOMAIN is set, the login URL must
    default to the Salesforce production endpoint."""
    import os
    # Provide all required vars; omit SF_DOMAIN and SF_LOGIN_URL.
    env = {
        "OPENAI_API_KEY": "sk-x",
        "ANTHROPIC_API_KEY": "sk-ant-x",
        "SF_CLIENT_ID": "cid",
        "SF_CLIENT_SECRET": "csec",
        "ENCRYPTION_KEY": "a" * 64,
        "SF_MCP_SERVER_URL": "https://example.com/mcp",
    }
    original = {k: os.environ.get(k) for k in env}
    # Remove any existing conflicting vars.
    for k in ("SF_DOMAIN", "SF_LOGIN_URL"):
        os.environ.pop(k, None)
    try:
        for k, v in env.items():
            os.environ[k] = v
        cfg = Config.from_env()
        assert cfg.sf_login_url == "https://login.salesforce.com"
    finally:
        for k, v in original.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for k in ("SF_DOMAIN", "SF_LOGIN_URL"):
            os.environ.pop(k, None)


def test_sf_domain_test_maps_to_sandbox_url(monkeypatch):
    """SF_DOMAIN=test must map to https://test.salesforce.com."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("SF_CLIENT_ID", "cid")
    monkeypatch.setenv("SF_CLIENT_SECRET", "csec")
    monkeypatch.setenv("ENCRYPTION_KEY", "b" * 64)
    monkeypatch.setenv("SF_MCP_SERVER_URL", "https://example.com/mcp")
    monkeypatch.setenv("SF_DOMAIN", "test")
    monkeypatch.delenv("SF_LOGIN_URL", raising=False)
    cfg = Config.from_env()
    assert cfg.sf_login_url == "https://test.salesforce.com"


def test_sf_domain_login_explicitly_maps_to_production_url(monkeypatch):
    """SF_DOMAIN=login (written by old setup scripts) must explicitly map to
    https://login.salesforce.com — not rely on a silent else-branch fallback."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("SF_CLIENT_ID", "cid")
    monkeypatch.setenv("SF_CLIENT_SECRET", "csec")
    monkeypatch.setenv("ENCRYPTION_KEY", "c" * 64)
    monkeypatch.setenv("SF_MCP_SERVER_URL", "https://example.com/mcp")
    monkeypatch.setenv("SF_DOMAIN", "login")
    monkeypatch.delenv("SF_LOGIN_URL", raising=False)
    cfg = Config.from_env()
    assert cfg.sf_login_url == "https://login.salesforce.com"


def test_sf_domain_custom_maps_to_custom_subdomain(monkeypatch):
    """An unknown SF_DOMAIN value (e.g. a My Domain name) must map to
    https://<domain>.salesforce.com rather than silently falling back to
    production."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("SF_CLIENT_ID", "cid")
    monkeypatch.setenv("SF_CLIENT_SECRET", "csec")
    monkeypatch.setenv("ENCRYPTION_KEY", "d" * 64)
    monkeypatch.setenv("SF_MCP_SERVER_URL", "https://example.com/mcp")
    monkeypatch.setenv("SF_DOMAIN", "mycompany")
    monkeypatch.delenv("SF_LOGIN_URL", raising=False)
    cfg = Config.from_env()
    assert cfg.sf_login_url == "https://mycompany.salesforce.com"


def test_sf_mcp_timeout_seconds_defaults_to_thirty(monkeypatch):
    """SF_MCP_TIMEOUT_SECONDS unset must yield the documented 30s default."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("SF_CLIENT_ID", "cid")
    monkeypatch.setenv("SF_CLIENT_SECRET", "csec")
    monkeypatch.setenv("ENCRYPTION_KEY", "f" * 64)
    monkeypatch.setenv("SF_MCP_SERVER_URL", "https://example.com/mcp")
    monkeypatch.delenv("SF_MCP_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("SF_DOMAIN", raising=False)
    monkeypatch.delenv("SF_LOGIN_URL", raising=False)
    cfg = Config.from_env()
    assert cfg.sf_mcp_timeout_seconds == 30.0


def test_sf_mcp_timeout_seconds_custom_value(monkeypatch):
    """A numeric SF_MCP_TIMEOUT_SECONDS must be parsed as float."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("SF_CLIENT_ID", "cid")
    monkeypatch.setenv("SF_CLIENT_SECRET", "csec")
    monkeypatch.setenv("ENCRYPTION_KEY", "g" * 64)
    monkeypatch.setenv("SF_MCP_SERVER_URL", "https://example.com/mcp")
    monkeypatch.setenv("SF_MCP_TIMEOUT_SECONDS", "12.5")
    monkeypatch.delenv("SF_DOMAIN", raising=False)
    monkeypatch.delenv("SF_LOGIN_URL", raising=False)
    cfg = Config.from_env()
    assert cfg.sf_mcp_timeout_seconds == 12.5


def test_sf_mcp_timeout_seconds_rejects_non_positive(monkeypatch):
    """Zero or negative SF_MCP_TIMEOUT_SECONDS must be rejected at startup —
    a missing or zero timeout reintroduces the very hang this guard prevents."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("SF_CLIENT_ID", "cid")
    monkeypatch.setenv("SF_CLIENT_SECRET", "csec")
    monkeypatch.setenv("ENCRYPTION_KEY", "h" * 64)
    monkeypatch.setenv("SF_MCP_SERVER_URL", "https://example.com/mcp")
    monkeypatch.setenv("SF_MCP_TIMEOUT_SECONDS", "0")
    monkeypatch.delenv("SF_DOMAIN", raising=False)
    monkeypatch.delenv("SF_LOGIN_URL", raising=False)
    with pytest.raises(ConfigError, match="SF_MCP_TIMEOUT_SECONDS"):
        Config.from_env()


def test_openai_api_key_not_required_for_local_whisper(monkeypatch):
    """OPENAI_API_KEY must not be required when WHISPER_BACKEND=local;
    entity extraction uses ANTHROPIC_API_KEY, not OpenAI."""
    monkeypatch.setenv("WHISPER_BACKEND", "local")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("SF_CLIENT_ID", "cid")
    monkeypatch.setenv("SF_CLIENT_SECRET", "csec")
    monkeypatch.setenv("ENCRYPTION_KEY", "e" * 64)
    monkeypatch.setenv("SF_MCP_SERVER_URL", "https://example.com/mcp")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SF_DOMAIN", raising=False)
    monkeypatch.delenv("SF_LOGIN_URL", raising=False)
    cfg = Config.from_env()
    assert cfg.openai_api_key == ""
    assert cfg.whisper_backend == "local"
