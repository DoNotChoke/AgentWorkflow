"""Entrypoint Agent Runtime (§4.3, §5.2).

uvicorn app.entrypoints.agent:app --port 8001 --reload
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.db import dispose_engine, init_engine
from app.core.errors import AppError
from app.core.logging import configure_logging, get_logger
from app.core.observability import configure_tracing, instrument_clients, instrument_fastapi
from app.core.redis import close_redis, init_redis
from app.entrypoints.middleware import (
    RequestContextMiddleware,
    app_error_handler,
    unhandled_error_handler,
)

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.assert_production_safe()

    configure_logging(settings)
    configure_tracing(settings, component="agent")
    init_engine(settings)
    init_redis(settings)
    instrument_clients()

    log.info("agent_runtime_starting", default_model=settings.anthropic_default_model)
    yield
    await close_redis()
    await dispose_engine()
    log.info("agent_runtime_stopped")


def create_app() -> FastAPI:
    app = FastAPI(title="AgentWorkflow Agent Runtime", version="0.0.1", lifespan=lifespan)

    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # Phase 3: vòng lặp Agent, Model Gateway, ghi Execution Trace, stream SSE.

    instrument_fastapi(app)
    return app


app = create_app()
