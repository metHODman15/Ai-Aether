"""Tests for backend.config.validate_credentials.

These tests cover the contract — Anthropic auth failures are fatal,
Salesforce auth failures are *not*, and SKIP_STARTUP_VALIDATION short
circuits the entire flow — without making any real network calls.
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
        sf_username="u@example.com",
        sf_password="pw",
        sf_security_token="tok",
        sf_domain="login",
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
        skip_startup_validation=skip,
    )


def test_skip_startup_validation_short_circuits():
    cfg = _make_config(skip=True)
    result = validate_credentials(cfg)
    assert result == {"anthropic": "skipped", "salesforce": "skipped"}


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


def test_salesforce_auth_failure_is_degraded_not_fatal(monkeypatch):
    """Salesforce login failure must not abort startup; the app should
    still come up with CRM marked as degraded so the user sees the
    offline banner instead of a hard crash."""

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

    class FakeSFAuthError(Exception):
        pass

    def _raise(*a, **kw):
        raise FakeSFAuthError("bad sf creds")

    fake_sf_mod = types.SimpleNamespace(Salesforce=_raise)
    fake_sf_exc = types.SimpleNamespace(SalesforceAuthenticationFailed=FakeSFAuthError)
    monkeypatch.setitem(sys.modules, "simple_salesforce", fake_sf_mod)
    monkeypatch.setitem(sys.modules, "simple_salesforce.exceptions", fake_sf_exc)

    result = validate_credentials(_make_config())
    assert result["anthropic"] == "ok"
    assert "salesforce" in result
    assert result["salesforce"] != "ok"
