"""Smoke test cho Control Plane API.

Không dùng ``TestClient`` với lifespan thật vì lifespan cần PostgreSQL. Ở đây
chỉ kiểm tra phần lắp ráp ứng dụng và middleware — kiểm thử tích hợp có chạm
hạ tầng thuộc về test đánh dấu ``@pytest.mark.integration``.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import AppError, RateLimitedError
from app.entrypoints.middleware import (
    CORRELATION_HEADER,
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
    app_error_handler,
)


def build_test_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(AppError, app_error_handler)

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"pong": "ok"}

    @app.get("/boom")
    async def boom() -> dict[str, str]:
        raise RateLimitedError("provider gioi han", retry_after_seconds=12)

    return app


def test_moi_response_deu_co_request_id_va_correlation_id() -> None:
    with TestClient(build_test_app()) as client:
        resp = client.get("/ping")
    assert resp.status_code == 200
    assert resp.headers[REQUEST_ID_HEADER].startswith("req_")
    assert resp.headers[CORRELATION_HEADER]


def test_correlation_id_do_client_gui_duoc_giu_nguyen() -> None:
    with TestClient(build_test_app()) as client:
        resp = client.get("/ping", headers={CORRELATION_HEADER: "corr-tu-upstream"})
    assert resp.headers[CORRELATION_HEADER] == "corr-tu-upstream"


def test_app_error_duoc_anh_xa_sang_http_theo_error_model() -> None:
    with TestClient(build_test_app()) as client:
        resp = client.get("/boom")

    assert resp.status_code == 429
    assert resp.json()["error"]["category"] == "RateLimited"
    assert resp.headers["retry-after"] == "12"
