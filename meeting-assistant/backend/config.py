"""Centralized configuration loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass


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
        )
