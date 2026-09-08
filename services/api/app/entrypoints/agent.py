"""Entrypoint Agent Runtime (§4.3, §5.2, §26).

uvicorn app.entrypoints.agent:app --port 8001 --reload
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any

import anthropic
import openai
from fastapi import APIRouter, Depends, FastAPI, Header, Path, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.config import Settings, get_settings
from app.core.context import RequestContext, current_context, set_context
from app.core.db import dispose_engine, get_session, init_engine
from app.core.errors import AppError, AuthenticationError
from app.core.http_auth import require_user_id, user_principal
from app.core.logging import configure_logging, get_logger
from app.core.observability import configure_tracing, instrument_clients, instrument_fastapi
from app.core.redis import close_redis, enforce_rate_limit, global_key, init_redis
from app.domains.agent.application.admin_service import AgentAdminService
from app.domains.agent.application.ports import (
    AgentEvent,
    AgentRepository,
    ConnectionResolver,
    ConnectionSelectionStore,
    ConversationRepository,
    MessageRepository,
    ModelClient,
    ToolCallContext,
)
from app.domains.agent.application.service import AgentRuntime, TurnResult
from app.domains.agent.infrastructure.anthropic_client import AnthropicModelClient
from app.domains.agent.infrastructure.connection_client import ConnectionResolverClient
from app.domains.agent.infrastructure.integration_client import IntegrationClient
from app.domains.agent.infrastructure.model_catalog import ModelProvider
from app.domains.agent.infrastructure.model_gateway import ModelGateway
from app.domains.agent.infrastructure.openai_client import OpenAIModelClient
from app.domains.agent.infrastructure.repositories import (
    SqlAlchemyAgentRepository,
    SqlAlchemyConnectionSelectionRepository,
    SqlAlchemyConversationRepository,
    SqlAlchemyMessageRepository,
)
from app.domains.tenant.application.service import TenantService
from app.domains.tenant.infrastructure.repositories import (
    SqlAlchemyMembershipRepository,
    SqlAlchemyOrganizationRepository,
    SqlAlchemyWorkspaceRepository,
)
from app.entrypoints import agent_wiring
from app.entrypoints.middleware import (
    RequestContextMiddleware,
    app_error_handler,
    unhandled_error_handler,
)

log = get_logger(__name__)

ORGANIZATION_HEADER = "x-organization-id"
WORKSPACE_HEADER = "x-workspace-id"

#: Ngân sách cho endpoint gửi tin nhắn — mỗi tin nhắn có thể kéo theo nhiều
#: lượt gọi model và tool, nên hạn mức chặt hơn nhiều so với một API đọc thường.
_SEND_MESSAGE_PER_USER = 20
_SEND_MESSAGE_WINDOW_SECONDS = 60


# ─────────────────────────────────────────────────────────────────────────────
# Tenant — lặp lại có chủ đích, không dùng `entrypoints/wiring.py` (Contract 6)
# ─────────────────────────────────────────────────────────────────────────────


class _NoopUserDirectory:
    async def get_profiles(self, user_ids: Any) -> dict[str, Any]:
        raise NotImplementedError

    async def find_by_email(self, email: str) -> Any:
        raise NotImplementedError


def _build_tenant_service(session: AsyncSession) -> TenantService:
    return TenantService(
        organizations=SqlAlchemyOrganizationRepository(session),
        workspaces=SqlAlchemyWorkspaceRepository(session),
        memberships=SqlAlchemyMembershipRepository(session),
        users=_NoopUserDirectory(),
    )


class AgentRequestContext(BaseModel):
    model_config = {"frozen": True}

    user_id: str
    organization_id: str
    workspace_id: str
    can_manage_workspace: bool


async def resolve_context(
    request: Request,
    user_id: Annotated[str, Depends(require_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
    organization_id: Annotated[str, Header(alias=ORGANIZATION_HEADER)],
    workspace_id: Annotated[str, Header(alias=WORKSPACE_HEADER)],
) -> AgentRequestContext:
    """Phân giải tenant từ token và membership"""
    access = await _build_tenant_service(session).require_access(
        user_id=user_id, organization_id=organization_id, workspace_id=workspace_id
    )
    roles = {f"org:{access.org_role}"}
    workspace_role = access.workspace_role
    if workspace_role is not None:
        roles.add(f"ws:{workspace_role}")

    ctx = current_context()
    if ctx is not None:
        set_context(
            RequestContext(
                request_id=ctx.request_id,
                correlation_id=ctx.correlation_id,
                tenant_id=organization_id,
                workspace_id=workspace_id,
                principal=user_principal(user_id, frozenset(roles)),
            )
        )

    request.state.tenant_id = organization_id
    request.state.workspace_id = workspace_id

    return AgentRequestContext(
        user_id=user_id,
        organization_id=organization_id,
        workspace_id=workspace_id,
        can_manage_workspace="ws:admin" in roles,
    )


async def require_authorization_header(
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """Giá trị thô của header — để forward nguyên vẹn sang Control Plane
    (§2.4). Khác `require_user_id`, vốn giải mã token thành `user_id` và bỏ
    chuỗi gốc đi."""
    if not authorization:
        raise AuthenticationError("Thiếu Authorization header.")
    return authorization


CurrentContext = Annotated[AgentRequestContext, Depends(resolve_context)]


# ─────────────────────────────────────────────────────────────────────────────
# Dependency — hạ tầng của chính domain `agent`, không cross-domain
# ─────────────────────────────────────────────────────────────────────────────


def get_agent_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentRepository:
    return SqlAlchemyAgentRepository(session)


def get_conversation_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConversationRepository:
    return SqlAlchemyConversationRepository(session)


def get_message_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MessageRepository:
    return SqlAlchemyMessageRepository(session)


def get_admin_service(
    agents: Annotated[AgentRepository, Depends(get_agent_repository)],
    conversations: Annotated[ConversationRepository, Depends(get_conversation_repository)],
    messages: Annotated[MessageRepository, Depends(get_message_repository)],
) -> AgentAdminService:
    return AgentAdminService(agents=agents, conversations=conversations, messages=messages)


def get_model_client(settings: Annotated[Settings, Depends(get_settings)]) -> ModelClient:
    """Chỉ ghi nhận provider nào có API key — thiếu key thì `ModelGateway` báo
    `UnconfiguredProviderError` rõ ràng thay vì một SDK lỗi khó hiểu lúc gọi."""
    clients: dict[ModelProvider, ModelClient] = {}

    anthropic_key = settings.anthropic_api_key.get_secret_value()
    if anthropic_key:
        clients[ModelProvider.ANTHROPIC] = AnthropicModelClient(
            anthropic.AsyncAnthropic(api_key=anthropic_key)
        )

    openai_key = settings.openai_api_key.get_secret_value()
    if openai_key:
        clients[ModelProvider.OPENAI] = OpenAIModelClient(openai.AsyncOpenAI(api_key=openai_key))

    return ModelGateway(clients)


def get_integration_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> IntegrationClient:
    return IntegrationClient(settings.control_plane_internal_base_url)


def get_connection_resolver(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ConnectionResolver:
    return ConnectionResolverClient(settings.control_plane_internal_base_url)


def get_connection_selection_store(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConnectionSelectionStore:
    """Đọc/ghi thuần SQL, không phải collaborator ngoài tiến trình — cùng
    khuôn `get_agent_repository`/`get_conversation_repository`, khác
    `get_connection_resolver` (đi HTTP tới Control Plane)."""
    return SqlAlchemyConnectionSelectionRepository(session)


# ─────────────────────────────────────────────────────────────────────────────
# Schema vào/ra
# ─────────────────────────────────────────────────────────────────────────────


class CreateAgentRequest(BaseModel):
    name: str = Field(max_length=200)
    system_prompt: str = Field(default="", max_length=20_000)
    model: str = Field(max_length=128)
    enabled_tool_ids: list[str] = Field(default_factory=list, max_length=100)
    max_tool_calls_per_turn: int = Field(default=8, ge=1, le=50)


class AgentResponse(BaseModel):
    id: str
    name: str
    system_prompt: str
    model: str
    enabled_tool_ids: list[str]
    max_tool_calls_per_turn: int
    created_by: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class ConversationResponse(BaseModel):
    id: str
    agent_id: str
    user_id: str
    status: str
    created_at: datetime
    updated_at: datetime


class ToolCallRequestResponse(BaseModel):
    call_id: str
    tool_id: str
    input: dict[str, Any]


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    tool_calls: list[ToolCallRequestResponse]
    tool_call_id: str | None
    created_at: datetime


class SendMessageRequest(BaseModel):
    content: str = Field(max_length=100_000)


class SetConversationConnectionRequest(BaseModel):
    provider: str = Field(max_length=64)
    connection_id: str = Field(max_length=64)


def _agent_response(agent: Any) -> AgentResponse:
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        system_prompt=agent.system_prompt,
        model=agent.model,
        enabled_tool_ids=sorted(agent.enabled_tool_ids),
        max_tool_calls_per_turn=agent.max_tool_calls_per_turn,
        created_by=agent.created_by,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
        archived_at=agent.archived_at,
    )


def _conversation_response(conversation: Any) -> ConversationResponse:
    return ConversationResponse(
        id=conversation.id,
        agent_id=conversation.agent_id,
        user_id=conversation.user_id,
        status=str(conversation.status),
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _message_response(message: Any) -> MessageResponse:
    return MessageResponse(
        id=message.id,
        role=str(message.role),
        content=message.content,
        tool_calls=[
            ToolCallRequestResponse(call_id=c.call_id, tool_id=c.tool_id, input=dict(c.input))
            for c in message.tool_calls
        ],
        tool_call_id=message.tool_call_id,
        created_at=message.created_at,
    )


def _sse_event(event: AgentEvent) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if event.tool_id is not None:
        payload["tool_id"] = event.tool_id
    if event.is_error:
        payload["is_error"] = True
    if event.text is not None:
        payload["text"] = event.text
    if event.error_category is not None:
        payload["error_category"] = event.error_category
    if event.error_details is not None:
        payload["error_details"] = dict(event.error_details)
    return {"event": str(event.kind), "data": json.dumps(payload, ensure_ascii=False)}


# ─────────────────────────────────────────────────────────────────────────────
# Router
# ─────────────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/v1", tags=["agent"])


@router.post("/agents", status_code=status.HTTP_201_CREATED)
async def create_agent(
    payload: CreateAgentRequest,
    context: CurrentContext,
    service: Annotated[AgentAdminService, Depends(get_admin_service)],
) -> AgentResponse:
    agent = await service.create_agent(
        tenant_id=context.organization_id,
        workspace_id=context.workspace_id,
        name=payload.name,
        system_prompt=payload.system_prompt,
        model=payload.model,
        enabled_tool_ids=frozenset(payload.enabled_tool_ids),
        max_tool_calls_per_turn=payload.max_tool_calls_per_turn,
        created_by=context.user_id,
    )
    return _agent_response(agent)


@router.get("/agents")
async def list_agents(
    context: CurrentContext,
    service: Annotated[AgentAdminService, Depends(get_admin_service)],
) -> list[AgentResponse]:
    agents = await service.list_agents(
        tenant_id=context.organization_id, workspace_id=context.workspace_id
    )
    return [_agent_response(a) for a in agents]


@router.post("/agents/{agent_id}/archive")
async def archive_agent(
    agent_id: Annotated[str, Path(max_length=64)],
    context: CurrentContext,
    service: Annotated[AgentAdminService, Depends(get_admin_service)],
) -> AgentResponse:
    agent = await service.archive_agent(
        tenant_id=context.organization_id,
        agent_id=agent_id,
        actor_id=context.user_id,
        can_manage_workspace=context.can_manage_workspace,
    )
    return _agent_response(agent)


@router.post("/agents/{agent_id}/unarchive")
async def unarchive_agent(
    agent_id: Annotated[str, Path(max_length=64)],
    context: CurrentContext,
    service: Annotated[AgentAdminService, Depends(get_admin_service)],
) -> AgentResponse:
    agent = await service.unarchive_agent(
        tenant_id=context.organization_id,
        agent_id=agent_id,
        actor_id=context.user_id,
        can_manage_workspace=context.can_manage_workspace,
    )
    return _agent_response(agent)


@router.post("/agents/{agent_id}/conversations", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    agent_id: Annotated[str, Path(max_length=64)],
    context: CurrentContext,
    service: Annotated[AgentAdminService, Depends(get_admin_service)],
) -> ConversationResponse:
    conversation = await service.create_conversation(
        tenant_id=context.organization_id,
        workspace_id=context.workspace_id,
        agent_id=agent_id,
        user_id=context.user_id,
    )
    return _conversation_response(conversation)


@router.get("/agents/{agent_id}/conversations")
async def list_conversations(
    agent_id: Annotated[str, Path(max_length=64)],
    context: CurrentContext,
    service: Annotated[AgentAdminService, Depends(get_admin_service)],
) -> list[ConversationResponse]:
    conversations = await service.list_conversations(
        tenant_id=context.organization_id, agent_id=agent_id, user_id=context.user_id
    )
    return [_conversation_response(c) for c in conversations]


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: Annotated[str, Path(max_length=64)],
    context: CurrentContext,
    service: Annotated[AgentAdminService, Depends(get_admin_service)],
) -> list[MessageResponse]:
    messages = await service.list_messages(
        tenant_id=context.organization_id, conversation_id=conversation_id, user_id=context.user_id
    )
    return [_message_response(m) for m in messages]


class _QueueEventSink:
    """Cầu nối giữa `on_event` (gọi đồng bộ bên trong `run_turn`) và một async
    generator SSE đang chạy song song — xem `_stream_turn`."""

    def __init__(self, queue: asyncio.Queue[Any]) -> None:
        self._queue = queue

    async def emit(self, event: AgentEvent) -> None:
        await self._queue.put(event)


#: Đánh dấu vòng lặp gọi model/tool đã xong hẳn — phân biệt với việc queue
#: tạm thời rỗng giữa hai sự kiện.
_DONE: Any = object()


async def _stream_turn(
    *,
    admin: AgentAdminService,
    agent: Any,
    conversation: Any,
    history: Sequence[Any],
    models: ModelClient,
    integration: IntegrationClient,
    connections: ConnectionResolver,
    selections: ConnectionSelectionStore,
    user_message: str,
    context: AgentRequestContext,
    authorization: str,
) -> AsyncIterator[dict[str, Any]]:
    """Chạy `AgentRuntime.run_turn` như một task nền, phát sự kiện ra ngay khi
    nó phát sinh — không đợi cả lượt xong mới trả về (khác việc chỉ đợi rồi
    trả một cục, thứ sẽ vô hiệu hoá lý do tồn tại của SSE cho một lượt có thể
    kéo dài nhiều vòng gọi tool).

    Model Gateway (WP5) chưa hỗ trợ streaming cấp token — sự kiện ở đây dừng ở
    mức "một tool bắt đầu/xong" và "câu trả lời cuối cùng đã có", không phải
    từng chữ một lúc model đang sinh ra nó.
    """
    trace_id, sink = await agent_wiring.start_agent_trace(
        tenant_id=context.organization_id,
        workspace_id=context.workspace_id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        initiated_by=context.user_id,
    )

    queue: asyncio.Queue[Any] = asyncio.Queue()
    runtime = AgentRuntime(
        models=models,
        tools=integration,
        executor=integration,
        connections=connections,
        selections=selections,
        trace=sink,
    )

    async def run() -> None:
        try:
            result = await runtime.run_turn(
                agent=agent,
                conversation=conversation,
                history=history,
                user_message=user_message,
                acting_user_id=context.user_id,
                authorization=authorization,
                organization_id=context.organization_id,
                workspace_id=context.workspace_id,
                caller=ToolCallContext(agent_id=agent.id, execution_trace_id=trace_id),
                on_event=_QueueEventSink(queue),
            )
        except Exception as exc:  # noqa: BLE001 — mọi lỗi phải tới được generator
            await queue.put(exc)
        else:
            await queue.put(result)
        finally:
            await queue.put(_DONE)

    task = asyncio.create_task(run())

    try:
        while True:
            item = await queue.get()

            if item is _DONE:
                break

            if isinstance(item, AgentEvent):
                yield _sse_event(item)
                continue

            if isinstance(item, TurnResult):
                await admin.record_turn(conversation=conversation, messages=item.messages)
                await agent_wiring.complete_agent_trace(
                    tenant_id=context.organization_id, trace_id=trace_id, status="succeeded"
                )
                continue

            # Exception — lượt hỏng ở tầng model/hạ tầng, không phải một tool
            # đơn lẻ (những cái đó đã tự biến thành message lỗi bên trong
            # run_turn và không bao giờ tới đây).
            category = item.category.value if isinstance(item, AppError) else "InternalError"
            await agent_wiring.complete_agent_trace(
                tenant_id=context.organization_id,
                trace_id=trace_id,
                status="failed",
                error_category=category,
            )
            yield {"event": "error", "data": json.dumps({"message": str(item)})}
    finally:
        await task


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: Annotated[str, Path(max_length=64)],
    payload: SendMessageRequest,
    context: CurrentContext,
    authorization: Annotated[str, Depends(require_authorization_header)],
    admin: Annotated[AgentAdminService, Depends(get_admin_service)],
    models: Annotated[ModelClient, Depends(get_model_client)],
    integration: Annotated[IntegrationClient, Depends(get_integration_client)],
    connections: Annotated[ConnectionResolver, Depends(get_connection_resolver)],
    selections: Annotated[ConnectionSelectionStore, Depends(get_connection_selection_store)],
) -> EventSourceResponse:
    await enforce_rate_limit(
        global_key("agent-message", context.user_id),
        limit=_SEND_MESSAGE_PER_USER,
        window_seconds=_SEND_MESSAGE_WINDOW_SECONDS,
        message="Bạn đang gửi tin nhắn quá nhanh. Vui lòng thử lại sau ít phút.",
    )

    conversation = await admin.get_conversation(
        tenant_id=context.organization_id, conversation_id=conversation_id, user_id=context.user_id
    )
    agent = await admin.get_agent(tenant_id=context.organization_id, agent_id=conversation.agent_id)
    history = await admin.list_messages(
        tenant_id=context.organization_id, conversation_id=conversation_id, user_id=context.user_id
    )

    return EventSourceResponse(
        _stream_turn(
            admin=admin,
            agent=agent,
            conversation=conversation,
            history=history,
            models=models,
            integration=integration,
            connections=connections,
            selections=selections,
            user_message=payload.content,
            context=context,
            authorization=authorization,
        )
    )


@router.post("/conversations/{conversation_id}/connections", status_code=status.HTTP_204_NO_CONTENT)
async def set_conversation_connection(
    conversation_id: Annotated[str, Path(max_length=64)],
    payload: SetConversationConnectionRequest,
    context: CurrentContext,
    admin: Annotated[AgentAdminService, Depends(get_admin_service)],
    selections: Annotated[ConnectionSelectionStore, Depends(get_connection_selection_store)],
) -> None:
    """Chốt connection dùng cho một provider trong cuộc hội thoại này — nơi
    UI ghi lại lựa chọn của người dùng sau khi thấy thẻ "cần chọn connection"
    (ADR-0004). Không qua model, không tính là tin nhắn.

    Không xác thực `connection_id` với Control Plane trước khi ghi — nếu sai,
    lời gọi tool thật sẽ tự báo lỗi khi đó (ADR-0004 mục 5), không thêm một
    round-trip xác thực trùng ở đây.
    """
    await admin.get_conversation(
        tenant_id=context.organization_id, conversation_id=conversation_id, user_id=context.user_id
    )
    await selections.set(
        conversation_id=conversation_id,
        provider=payload.provider,
        connection_id=payload.connection_id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────


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

    app.include_router(router)

    instrument_fastapi(app)
    return app


app = create_app()
