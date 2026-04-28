"""Structured logging with per-chunk request-ID context.

Every audio chunk gets a short hex ID at capture time and that ID is
propagated through transcription, context evaluation, entity extraction,
Salesforce queries, and the WebSocket broadcast hub via a contextvar so
a single chunk can be greppable end-to-end in the logs.
"""
from __future__ import annotations

import contextvars
import logging
import os
import secrets

request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # pragma: no cover
        record.request_id = request_id_ctx.get()
        return True


_LOG_FORMAT = "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"


def setup_logging(level: str | None = None) -> None:
    """Install the request-id-aware log handler on the root logger."""
    lvl_name = (level or os.getenv("LOG_LEVEL") or "INFO").upper().strip()
    lvl = getattr(logging, lvl_name, logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    handler.addFilter(_RequestIdFilter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(lvl)


def new_request_id() -> str:
    """Return a short opaque ID safe for log correlation."""
    return secrets.token_hex(4)


def set_request_id(rid: str) -> contextvars.Token:
    return request_id_ctx.set(rid)


def reset_request_id(token: contextvars.Token) -> None:
    request_id_ctx.reset(token)


def current_request_id() -> str:
    return request_id_ctx.get()
