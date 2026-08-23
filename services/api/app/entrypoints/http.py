"""Entrypoint Control Plane API (§4.2).

uvicorn app.entrypoints.http:app --reload
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import dispose_engine, get_engine, init_engine
from app.core.errors import AppError
from app.core.logging import configure_logging, get_logger
from app.core.observability import (
    configure_tracing,
    instrument_clients,
    instrument_fastapi,
    instrument_sqlalchemy,
)
from app.core.redis import close_redis, init_redis, redis_healthy
from app.domains.identity.infrastructure.http import TENANT_PROVISIONER_FACTORY
from app.domains.identity.infrastructure.http import router as identity_router
from app.domains.tenant.infrastructure.http import USER_DIRECTORY_FACTORY, build_tenant_service
from app.domains.tenant.infrastructure.http import router as tenant_router
from app.entrypoints.middleware import (
    RequestContextMiddleware,
    app_error_handler,
    unhandled_error_handler,
)
from app.entrypoints.wiring import build_user_directory

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.assert_production_safe()

    configure_logging(settings)
    configure_tracing(settings, component="api")

    engine = init_engine(settings)
    init_redis(settings)
    instrument_sqlalchemy(engine)
    instrument_clients()

    log.info("api_starting", environment=settings.environment)
    yield
    await close_redis()
    await dispose_engine()
    log.info("api_stopped")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="AgentWorkflow Control Plane",
        version="0.0.1",
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
    )

    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    _register_health_routes(app)

    # Nối hai domain vào nhau, theo cả hai chiều.
    #
    # Đây là chỗ duy nhất trong hệ thống biết cả hai domain. Bên cần khai báo
    # một Protocol ở tầng application của mình và lấy hiện thực qua `app.state`;
    # bên có cung cấp hiện thực. Không bên nào import bên kia, nên contract 1
    # của import-linter vẫn giữ (quy tắc 1 của ADR-0001).
    #
    # Identity ← Tenant: cấp organization và workspace cho user vừa đăng ký.
    # Tenant ← Identity: tra tên và email để hiển thị danh sách thành viên.
    setattr(
        app.state,
        TENANT_PROVISIONER_FACTORY,
        lambda session: build_tenant_service(session, build_user_directory(session)),
    )
    setattr(app.state, USER_DIRECTORY_FACTORY, build_user_directory)

    # Router của từng domain được mount ở đây. Entrypoint chỉ chạm tới tầng
    # `infrastructure` của domain, không bao giờ import thẳng tầng `domain`
    # (contract 5 trong pyproject.toml).
    app.include_router(identity_router)
    app.include_router(tenant_router)

    instrument_fastapi(app)
    return app


def _register_health_routes(app: FastAPI) -> None:
    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        """Liveness: tiến trình còn sống. Không chạm dependency."""
        return {"status": "ok"}

    @app.get("/readyz", tags=["ops"])
    async def readyz() -> dict[str, Any]:
        """Readiness: các dependency bắt buộc có sẵn sàng nhận traffic không.

        Chỉ PostgreSQL là **bắt buộc**. Redis được báo cáo nhưng không chặn
        readiness: nó chỉ giữ state phù du (§4.6), và rate limit đã hỏng mở khi
        Redis chết. Nếu để Redis chặn readiness thì một sự cố cache sẽ rút toàn
        bộ pod khỏi load balancer — biến lỗi chịu đựng được thành lỗi toàn phần.
        """
        required: dict[str, str] = {}
        optional: dict[str, str] = {}

        try:
            async with get_engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
            required["postgres"] = "ok"
        except Exception as exc:  # noqa: BLE001
            required["postgres"] = f"error: {type(exc).__name__}"

        optional["redis"] = "ok" if await redis_healthy() else "unavailable"

        healthy = all(v == "ok" for v in required.values())
        return {
            "status": "ready" if healthy else "degraded",
            "checks": {**required, **optional},
        }


app = create_app()
