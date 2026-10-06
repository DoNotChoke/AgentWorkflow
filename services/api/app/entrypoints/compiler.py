"""HTTP của Workflow Compiler, mount vào Agent Runtime"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.redis import enforce_rate_limit, global_key
from app.domains.agent.application.ports import ModelClient
from app.domains.workflow.application.compile_service import MAX_MESSAGE_CHARS, CompileService
from app.domains.workflow.application.ports import Progress
from app.entrypoints.compiler_wiring import build_compile_service

log = get_logger(__name__)

#: Một thao tác của `CompileService`, nhận hàm báo tiến độ, trả phiên sau thao tác.
Operation = Callable[[Progress], Awaitable[Any]]

_DONE: Any = object()


class SayRequest(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class CompileMessageResponse(BaseModel):
    kind: str
    text: str
    at: datetime


class CompileSessionResponse(BaseModel):
    id: str
    conversation_id: str
    status: str
    #: Tăng sau mỗi lần phiên đổi: giao diện biết bản nó đang hiện đã cũ chưa.
    revision: int
    messages: list[CompileMessageResponse]
    intent: dict[str, Any] | None
    #: Bảng giả định ở dạng dữ liệu. `None` khi chưa tới bước đó.
    proposal: dict[str, Any] | None
    #: IR đã qua ba tầng kiểm, để giao diện vẽ các bước sẽ chạy. `None` khi chưa có.
    ir: dict[str, Any] | None
    #: Bấm Đồng ý được hay chưa.
    is_ready: bool
    workflow_id: str | None
    created_at: datetime
    updated_at: datetime


def _response(session: Any) -> CompileSessionResponse:
    return CompileSessionResponse(
        id=session.id,
        conversation_id=session.conversation_id,
        status=str(session.status),
        revision=session.revision,
        messages=[
            CompileMessageResponse(kind=str(message.kind), text=message.text, at=message.at)
            for message in session.messages
        ],
        intent=None if session.intent is None else dict(session.intent),
        proposal=None if session.proposal is None else dict(session.proposal),
        ir=None if session.ir is None else dict(session.ir),
        is_ready=session.is_ready,
        workflow_id=session.workflow_id,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


async def _stream(operation: Operation) -> AsyncIterator[dict[str, Any]]:
    """Chạy `operation` như một task nền và phát tiến độ ngay khi có, không đợi nó xong."""
    queue: asyncio.Queue[Any] = asyncio.Queue()

    async def progress(text: str) -> None:
        await queue.put(text)

    async def run() -> None:
        try:
            await queue.put(_response(await operation(progress)))
        except Exception as exc:  # noqa: BLE001 — mọi lỗi phải tới được generator
            await queue.put(exc)
        finally:
            await queue.put(_DONE)

    task = asyncio.create_task(run())
    try:
        while True:
            item = await queue.get()
            if item is _DONE:
                break
            if isinstance(item, str):
                yield {"event": "status", "data": json.dumps({"text": item}, ensure_ascii=False)}
            elif isinstance(item, CompileSessionResponse):
                yield {"event": "session", "data": item.model_dump_json()}
            elif isinstance(item, AppError):
                # Phiên không đổi: thao tác chỉ ghi sau khi model đã trả lời xong.
                yield {
                    "event": "error",
                    "data": json.dumps(
                        {"message": item.message, "category": str(item.category)},
                        ensure_ascii=False,
                    ),
                }
            else:
                log.error("compile_operation_failed", error=repr(item))
                yield {
                    "event": "error",
                    "data": json.dumps(
                        {"message": "Có lỗi khi biên dịch. Thử lại sau.", "category": "internal"},
                        ensure_ascii=False,
                    ),
                }
    finally:
        await task


def create_router(
    *, context: Callable[..., Awaitable[Any]], models: Callable[..., ModelClient]
) -> APIRouter:
    """`context` phân giải người gọi và tenant; `models` trả Model Gateway. Cả hai là
    dependency của Agent Runtime."""
    router = APIRouter(prefix="/v1", tags=["compiler"])

    Context = Annotated[Any, Depends(context)]  # noqa: N806
    Models = Annotated[ModelClient, Depends(models)]  # noqa: N806
    AppSettings = Annotated[Settings, Depends(get_settings)]  # noqa: N806
    SessionId = Annotated[str, Path(max_length=64)]  # noqa: N806

    def streaming(model_client: Models, settings: AppSettings) -> CompileService:
        return build_compile_service(models=model_client, settings=settings)

    def transactional(
        model_client: Models,
        settings: AppSettings,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> CompileService:
        return build_compile_service(models=model_client, settings=settings, session=session)

    Streaming = Annotated[CompileService, Depends(streaming)]  # noqa: N806
    Transactional = Annotated[CompileService, Depends(transactional)]  # noqa: N806

    async def budget(ctx: Any, settings: Settings) -> None:
        await enforce_rate_limit(
            global_key("compile", ctx.user_id),
            limit=settings.compiler_rate_limit,
            window_seconds=settings.compiler_rate_window_seconds,
            message="Bạn đang biên dịch quá nhanh. Vui lòng thử lại sau ít phút.",
        )

    def on(ctx: Any, session_id: str) -> dict[str, str]:
        return {
            "tenant_id": ctx.organization_id,
            "session_id": session_id,
            "actor_id": ctx.user_id,
        }

    # ─── Có gọi model ────────────────────────────────────────────────────────

    @router.post("/conversations/{conversation_id}/compile-sessions")
    async def open_session(
        conversation_id: Annotated[str, Path(max_length=64)],
        ctx: Context,
        service: Streaming,
        settings: AppSettings,
    ) -> EventSourceResponse:
        await budget(ctx, settings)
        return EventSourceResponse(
            _stream(
                lambda progress: service.open(
                    tenant_id=ctx.organization_id,
                    workspace_id=ctx.workspace_id,
                    conversation_id=conversation_id,
                    actor_id=ctx.user_id,
                    progress=progress,
                )
            )
        )

    @router.post("/compile-sessions/{session_id}/messages")
    async def say(
        session_id: SessionId,
        payload: SayRequest,
        ctx: Context,
        service: Streaming,
        settings: AppSettings,
    ) -> EventSourceResponse:
        await budget(ctx, settings)
        return EventSourceResponse(
            _stream(
                lambda progress: service.say(
                    **on(ctx, session_id), text=payload.content, progress=progress
                )
            )
        )

    @router.post("/compile-sessions/{session_id}/accept-intent")
    async def accept_intent(
        session_id: SessionId, ctx: Context, service: Streaming, settings: AppSettings
    ) -> EventSourceResponse:
        await budget(ctx, settings)
        return EventSourceResponse(
            _stream(
                lambda progress: service.accept_intent(**on(ctx, session_id), progress=progress)
            )
        )

    # ─── Không gọi model ─────────────────────────────────────────────────────

    @router.get("/conversations/{conversation_id}/compile-sessions/current")
    async def current_session(
        conversation_id: Annotated[str, Path(max_length=64)], ctx: Context, service: Transactional
    ) -> CompileSessionResponse | None:
        """Phiên đang mở của người gọi trên conversation này; `null` khi không có."""
        found = await service.current(
            tenant_id=ctx.organization_id, conversation_id=conversation_id, actor_id=ctx.user_id
        )
        return None if found is None else _response(found)

    @router.get("/compile-sessions/{session_id}")
    async def get_compile_session(
        session_id: SessionId, ctx: Context, service: Transactional
    ) -> CompileSessionResponse:
        return _response(await service.get(**on(ctx, session_id)))

    @router.post("/compile-sessions/{session_id}/reopen-intent")
    async def reopen_intent(
        session_id: SessionId, ctx: Context, service: Transactional
    ) -> CompileSessionResponse:
        return _response(await service.reopen_intent(**on(ctx, session_id)))

    @router.post("/compile-sessions/{session_id}/confirm")
    async def confirm(
        session_id: SessionId, ctx: Context, service: Transactional
    ) -> CompileSessionResponse:
        """Phiên, workflow và draft nằm trong cùng một transaction của request này."""
        return _response(await service.confirm(**on(ctx, session_id)))

    @router.post("/compile-sessions/{session_id}/abandon")
    async def abandon(
        session_id: SessionId, ctx: Context, service: Transactional
    ) -> CompileSessionResponse:
        return _response(await service.abandon(**on(ctx, session_id)))

    return router
