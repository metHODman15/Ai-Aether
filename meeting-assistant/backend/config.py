"""Centralized configuration loaded from environment variables."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ConfigError(
            f"Missing required environment variable: {name}. "
            f"Copy .env.example to .env and fill in your credentials."
        )
    return value


def _optional(name: str, default: str) -> str:
    return os.getenv(name) or default


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    openai_api_key: str
    anthropic_api_key: str
    sf_username: str
    sf_password: str
    sf_security_token: str
    sf_domain: str
    host: str
    port: int
    audio_chunk_seconds: float
    audio_sample_rate: int
    whisper_backend: str
    local_whisper_model: str
    local_whisper_device: str
    local_whisper_compute_type: str
    log_level: str
    sf_session_timeout_minutes: int
    skip_startup_validation: bool

    @classmethod
    def from_env(cls) -> "Config":
        whisper_backend = _optional("WHISPER_BACKEND", "openai").lower().strip()
        _VALID_BACKENDS = ("openai", "local")
        if whisper_backend not in _VALID_BACKENDS:
            raise ConfigError(
                f"WHISPER_BACKEND='{whisper_backend}' is not valid. "
                f"Choose one of: {', '.join(_VALID_BACKENDS)}."
            )

        # OPENAI_API_KEY is always required: entity extraction uses it regardless
        # of which transcription backend is selected.
        openai_api_key = _require("OPENAI_API_KEY")

        try:
            sf_session_timeout = int(_optional("SF_SESSION_TIMEOUT_MINUTES", "30"))
            if sf_session_timeout <= 0:
                raise ValueError
        except ValueError:
            raise ConfigError(
                "SF_SESSION_TIMEOUT_MINUTES must be a positive integer."
            )

        return cls(
            openai_api_key=openai_api_key,
            anthropic_api_key=_require("ANTHROPIC_API_KEY"),
            sf_username=_require("SF_USERNAME"),
            sf_password=_require("SF_PASSWORD"),
            sf_security_token=_require("SF_SECURITY_TOKEN"),
            sf_domain=_optional("SF_DOMAIN", "login"),
            host=_optional("HOST", "127.0.0.1"),
            port=int(_optional("PORT", "8000")),
            audio_chunk_seconds=float(_optional("AUDIO_CHUNK_SECONDS", "5")),
            audio_sample_rate=int(_optional("AUDIO_SAMPLE_RATE", "16000")),
            whisper_backend=whisper_backend,
            local_whisper_model=_optional("LOCAL_WHISPER_MODEL", "base"),
            local_whisper_device=_optional("LOCAL_WHISPER_DEVICE", "cpu"),
            local_whisper_compute_type=_optional("LOCAL_WHISPER_COMPUTE_TYPE", "int8"),
            log_level=_optional("LOG_LEVEL", "INFO").upper(),
            sf_session_timeout_minutes=sf_session_timeout,
            skip_startup_validation=_bool("SKIP_STARTUP_VALIDATION", False),
        )


def validate_credentials(config: Config) -> dict[str, str]:
    """Run lightweight live checks on the configured credentials.

    Returns a dict of ``{component: status_message}``.  Raises
    :class:`ConfigError` if a *fatal* problem is detected (currently:
    Anthropic auth failure; Salesforce failure is reported but treated
    as degraded, not fatal — see graceful-degradation behavior).

    Skipped entirely when ``SKIP_STARTUP_VALIDATION=1`` is set, which
    is useful for offline or demo-only development.
    """
    results: dict[str, str] = {}

    if config.skip_startup_validation:
        logger.info(
            "SKIP_STARTUP_VALIDATION=1 set; skipping credential live checks."
        )
        return {"anthropic": "skipped", "salesforce": "skipped"}

    # ── Anthropic ─────────────────────────────────────────────────────────
    try:
        from anthropic import Anthropic, APIError, AuthenticationError
        client = Anthropic(api_key=config.anthropic_api_key)
        try:
            # Cheap call: list a single model. AuthenticationError fires fast.
            client.models.list(limit=1)
            results["anthropic"] = "ok"
            logger.info("Anthropic credential validated.")
        except AuthenticationError as exc:
            raise ConfigError(
                f"ANTHROPIC_API_KEY is invalid: {exc}. "
                "Check the key in your .env file."
            )
        except APIError as exc:
            # Network / 5xx: log but do not fail startup
            logger.warning(
                "Anthropic auth ping failed transiently (%s); continuing.", exc
            )
            results["anthropic"] = f"degraded: {exc}"
    except ConfigError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Anthropic validation error: %s", exc)
        results["anthropic"] = f"degraded: {exc}"

    # ── Salesforce (degraded, not fatal) ──────────────────────────────────
    try:
        from simple_salesforce import Salesforce
        from simple_salesforce.exceptions import SalesforceAuthenticationFailed

        try:
            Salesforce(
                username=config.sf_username,
                password=config.sf_password,
                security_token=config.sf_security_token,
                domain=config.sf_domain,
            )
            results["salesforce"] = "ok"
            logger.info("Salesforce credential validated.")
        except SalesforceAuthenticationFailed as exc:
            logger.warning(
                "Salesforce auth failed at startup (%s). The app will start "
                "in degraded mode — CRM data will be unavailable until "
                "credentials are fixed.", exc,
            )
            results["salesforce"] = f"auth_failed: {exc}"
        except Exception as exc:
            logger.warning(
                "Salesforce validation error at startup (%s); will run in "
                "degraded mode.", exc,
            )
            results["salesforce"] = f"unreachable: {exc}"
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Salesforce validation skipped: %s", exc)
        results["salesforce"] = f"skipped: {exc}"

    return results
