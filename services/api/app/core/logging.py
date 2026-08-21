"""Structured logging, tự động đính kèm tenant context và OTel trace id."""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog
from opentelemetry import trace

from app.core.config import Settings
from app.core.context import current_context

#: Chữ ký processor của structlog. Đặt alias để ba processor bên dưới khớp
#: chính xác với kiểu structlog mong đợi khi chạy mypy strict.
type EventDict = MutableMapping[str, Any]

#: Tên trường bị che khi xuất hiện trong log.
_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "api_key",
        "apikey",
        "authorization",
        "client_secret",
        "private_key",
        "credential",
        "credentials",
        "encrypted_payload",
        "data_key",
    }
)

_REDACTED = "***redacted***"


def _add_request_context(_logger: Any, _method: str, event_dict: EventDict) -> EventDict:
    ctx = current_context()
    if ctx is not None:
        event_dict.update(ctx.as_log_fields())
    return event_dict


def _add_otel_ids(_logger: Any, _method: str, event_dict: EventDict) -> EventDict:
    """Gắn OTel trace/span id để nối log với trace hạ tầng (§2.2, §36)."""
    span = trace.get_current_span()
    span_ctx = span.get_span_context()
    if span_ctx.is_valid:
        event_dict["otel_trace_id"] = format(span_ctx.trace_id, "032x")
        event_dict["otel_span_id"] = format(span_ctx.span_id, "016x")
    return event_dict


def _redact_secrets(_logger: Any, _method: str, event_dict: EventDict) -> EventDict:
    def scrub(value: Any, depth: int = 0) -> Any:
        if depth > 6:
            return value
        if isinstance(value, dict):
            return {
                k: (_REDACTED if k.lower() in _SENSITIVE_KEYS else scrub(v, depth + 1))
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            return type(value)(scrub(v, depth + 1) for v in value)
        return value

    return {
        k: (_REDACTED if k.lower() in _SENSITIVE_KEYS else scrub(v)) for k, v in event_dict.items()
    }


def configure_logging(settings: Settings) -> None:
    """Cấu hình structlog + stdlib logging. Gọi một lần khi khởi động process."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )
    # Bớt ồn từ thư viện bên thứ ba.
    for noisy in ("uvicorn.access", "botocore", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if settings.log_json
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _add_request_context,
            _add_otel_ids,
            _redact_secrets,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
