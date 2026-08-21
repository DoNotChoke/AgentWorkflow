"""Middleware tầng transport: context, xử lý lỗi, log truy cập."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.context import new_request_context, use_context
from app.core.errors import AppError, ErrorCategory
from app.core.logging import get_logger
from app.core.observability import bind_context_attributes

log = get_logger(__name__)

CORRELATION_HEADER = "x-correlation-id"
REQUEST_ID_HEADER = "x-request-id"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Tạo ``RequestContext`` cho mỗi request và đưa vào contextvar."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        correlation_id = request.headers.get(CORRELATION_HEADER)

        # Context khởi tạo **không có tenant**. Tenant chỉ được điền bởi
        # `resolve_tenant_context` sau khi token đã xác thực và membership đã
        # được đọc từ database (§19). Không có đường nào để client tự khai.
        ctx = new_request_context(correlation_id=correlation_id)

        started = time.perf_counter()
        with use_context(ctx):
            bind_context_attributes()
            response = await call_next(request)

        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers[REQUEST_ID_HEADER] = ctx.request_id
        response.headers[CORRELATION_HEADER] = ctx.correlation_id

        # Route chạy trong task con nên context nó đặt không lan ngược ra đây.
        # Lấy lại từ `request.state` để dòng access log vẫn có tenant.
        log.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(elapsed_ms, 2),
            **_resolved_tenant_fields(request),
        )
        return response


def _resolved_tenant_fields(request: Request) -> dict[str, str]:
    fields: dict[str, str] = {}
    for name in ("tenant_id", "workspace_id"):
        value = getattr(request.state, name, None)
        if value is not None:
            fields[name] = str(value)
    return fields


async def app_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Ánh xạ ``AppError`` sang HTTP response theo §37."""
    assert isinstance(exc, AppError)
    log.warning(
        "app_error",
        category=str(exc.category),
        message=exc.message,
        retryable=exc.retryable,
    )
    headers = {}
    if exc.retry_after_seconds is not None:
        headers["retry-after"] = str(int(exc.retry_after_seconds))
    return JSONResponse(status_code=exc.http_status, content=exc.to_payload(), headers=headers)


async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Lỗi ngoài dự kiến: log đầy đủ, nhưng không rò chi tiết ra ngoài (§37, §38)."""
    log.exception("unhandled_error", error_type=type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "category": str(ErrorCategory.INTERNAL),
                "message": "Đã xảy ra lỗi nội bộ.",
            }
        },
    )
