"""Router của Integration Plane."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Path, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.context import (
    Principal,
    PrincipalKind,
    RequestContext,
    current_context,
    set_context,
)
from app.core.db import get_session
from app.core.errors import AuthenticationError, AuthorizationError, ValidationError
from app.core.http_auth import require_principal, require_user_id, user_principal
from app.core.logging import get_logger
from app.core.redis import enforce_rate_limit, global_key
from app.domains.integration.application.gateway import IntegrationGateway
from app.domains.integration.application.policy_engine import PolicyEngine
from app.domains.integration.application.ports import (
    CredentialProvider,
    RunIdentity,
    RunLookup,
    TenantGuard,
    ToolInvocationSink,
)
from app.domains.integration.domain.catalog import default_registry
from app.domains.integration.domain.invocation import CallerRef, ToolInvocation
from app.domains.integration.infrastructure.circuit_breaker import RedisCircuitBreaker
from app.domains.integration.infrastructure.connectors import HttpConnector
from app.domains.integration.infrastructure.policy_defaults import (
    NoOrgToolRules,
    NoPlanRestrictions,
    UnlimitedQuota,
)
from app.domains.integration.infrastructure.rate_limit import RedisRateLimiter
from app.domains.integration.infrastructure.repositories import SqlAlchemyIdempotencyStore
from app.domains.integration.infrastructure.sinks import LoggingUsageRecorder

log = get_logger(__name__)

router = APIRouter(prefix="/internal/v1/tools", tags=["integration"], include_in_schema=False)

ORGANIZATION_HEADER = "x-organization-id"
WORKSPACE_HEADER = "x-workspace-id"

CREDENTIAL_PROVIDER_FACTORY = "credential_provider_factory"
INTEGRATION_TENANT_GUARD_FACTORY = "integration_tenant_guard_factory"
TRACE_SINK_FACTORY = "trace_sink_factory"
RUN_LOOKUP_FACTORY = "integration_run_lookup_factory"

#: Rate limit ở tầng vận chuyển, đếm theo người gọi.
_INVOKES_PER_USER = 120
_INVOKE_WINDOW_SECONDS = 60

#: Registry nạp một lần cho cả tiến trình. Không I/O, nên dựng lúc import là an
#: toàn — và mọi ràng buộc của catalog được kiểm ngay lúc khởi động.
_REGISTRY = default_registry()


# ─── Dependency ──────────────────────────────────────────────────────────────


def _from_state(request: Request, key: str, what: str) -> Any:
    factory = getattr(request.app.state, key, None)
    if factory is None:
        raise RuntimeError(f"Chưa gắn {key} vào app.state. Xem app/entrypoints/http.py — {what}")
    return factory


def get_credential_provider(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CredentialProvider:
    """Lấy cổng credential mà entrypoint đã gắn vào app.

    Router **không** dựng `CredentialResolver`. Chỉ `entrypoints/wiring.py` làm
    được điều đó (ADR-0002), và việc đi vòng qua `app.state` là thứ giữ cho
    ranh giới ấy không bị một dòng `import` phá vỡ.
    """
    factory = _from_state(
        request,
        CREDENTIAL_PROVIDER_FACTORY,
        "chỉ Control Plane API được nối cổng credential.",
    )
    provider: CredentialProvider = factory(session, settings)
    return provider


def get_tenant_guard(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TenantGuard:
    factory = _from_state(
        request,
        INTEGRATION_TENANT_GUARD_FACTORY,
        "mọi entrypoint phục vụ router này đều phải nối domain Tenant vào.",
    )
    guard: TenantGuard = factory(session)
    return guard


def get_trace_sink(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ToolInvocationSink:
    """Lấy sink ghi Execution Trace mà entrypoint đã gắn vào app.

    Router **không** tự dựng `TraceService`: nó thuộc domain `trace`, và
    import thẳng sẽ phá Contract 1 (các domain không import chéo nhau).
    """
    factory = _from_state(
        request,
        TRACE_SINK_FACTORY,
        "chỉ Control Plane API được nối trace sink (xem entrypoints/wiring.py).",
    )
    sink: ToolInvocationSink = factory(session)
    return sink


def get_run_lookup(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RunLookup:
    factory = _from_state(
        request,
        RUN_LOOKUP_FACTORY,
        "chỉ Control Plane API được nối domain Execution vào (xem entrypoints/wiring.py).",
    )
    lookup: RunLookup = factory(session)
    return lookup


@dataclass(frozen=True, slots=True)
class Caller:
    """Ai đang gọi, đã phân giải xong. Không trường nào đến từ thân request (§19)."""

    #: Người mà quyền của lời gọi được tính theo: người đã xác thực, hoặc người khởi chạy
    #: lần chạy workflow.
    user_id: str
    organization_id: str
    workspace_id: str
    roles: frozenset[str]
    principal_kind: PrincipalKind = "user"
    #: Có khi người gọi là một lần chạy workflow.
    run: RunIdentity | None = None


async def resolve_context(
    principal: Annotated[Principal, Depends(require_principal)],
    guard: Annotated[TenantGuard, Depends(get_tenant_guard)],
    runs: Annotated[RunLookup, Depends(get_run_lookup)],
    header_organization_id: Annotated[str | None, Header(alias=ORGANIZATION_HEADER)] = None,
    header_workspace_id: Annotated[str | None, Header(alias=WORKSPACE_HEADER)] = None,
) -> Caller:
    """Phân giải tenant từ token, không từ thân request."""
    run: RunIdentity | None = None
    if principal.kind == "workflow":
        run = await runs.find(principal.id)
        # Lần chạy đã kết thúc thì token của nó hết tác dụng, kể cả khi chưa hết hạn.
        if run is None or not run.is_running:
            raise AuthenticationError("Token không hợp lệ.")
        # Người khởi chạy phải còn quyền vào workspace: rời tổ chức thì lần chạy dừng ở đây.
        context = await guard.require_workspace_context(
            user_id=run.initiated_by,
            organization_id=run.tenant_id,
            workspace_id=run.workspace_id,
        )
        acting = run.initiated_by
        identity = Principal(kind="workflow", id=run.execution_id, roles=context.roles)
    else:
        context = await guard.require_workspace_context(
            user_id=principal.id,
            organization_id=header_organization_id,
            workspace_id=header_workspace_id,
        )
        acting = principal.id
        identity = user_principal(principal.id, context.roles)

    ctx = current_context()
    if ctx is not None:
        set_context(
            RequestContext(
                request_id=ctx.request_id,
                correlation_id=ctx.correlation_id,
                tenant_id=context.organization_id,
                workspace_id=context.workspace_id,
                principal=identity,
            )
        )
    return Caller(
        user_id=acting,
        organization_id=context.organization_id,
        workspace_id=context.workspace_id,
        roles=context.roles,
        principal_kind=principal.kind,
        run=run,
    )


def get_gateway(
    credentials: Annotated[CredentialProvider, Depends(get_credential_provider)],
    trace: Annotated[ToolInvocationSink, Depends(get_trace_sink)],
) -> IntegrationGateway:
    return IntegrationGateway(
        registry=_REGISTRY,
        policy=PolicyEngine(
            org_policy=NoOrgToolRules(), quota=UnlimitedQuota(), plan=NoPlanRestrictions()
        ),
        credentials=credentials,
        connector=HttpConnector(),
        rate_limiter=RedisRateLimiter(),
        breaker=RedisCircuitBreaker(),
        idempotency=SqlAlchemyIdempotencyStore(),
        trace=trace,
        usage=LoggingUsageRecorder(),
    )


CurrentContext = Annotated[Caller, Depends(resolve_context)]


# ─── Schema vào/ra ───────────────────────────────────────────────────────────


class CallerRequest(BaseModel):
    """Ai/cái gì đang khởi xướng lời gọi — để đính vào Execution Trace (§9.1).

    Mọi trường đều tuỳ chọn: gọi trực tiếp qua Swagger/curl thì để trống, kết
    quả vẫn đúng — chỉ là không có trace nào được ghi (xem `IntegrationTraceSink`
    ở `entrypoints/wiring.py`). `execution_trace_id` sai hoặc thuộc tenant khác
    cũng chỉ lặng lẽ không ghi được gì, không làm lời gọi tool thất bại — ghi
    trace là quan sát, không phải một phần của giao dịch.
    """

    agent_id: str | None = None
    workflow_id: str | None = None
    execution_id: str | None = None
    node_id: str | None = None
    execution_trace_id: str | None = None
    parent_step_id: str | None = None


class InvokeRequest(BaseModel):
    #: Bắt buộc với người dùng. Với một lần chạy workflow thì bị bỏ qua: connection lấy
    #: từ lựa chọn đã ghi của lần chạy cho node `caller.node_id` (ADR-0014).
    connection_id: str | None = Field(default=None, max_length=64)
    input: dict[str, Any] = Field(default_factory=dict)
    tool_version: str | None = Field(default=None, max_length=16)
    idempotency_key: str | None = Field(default=None, max_length=255)
    caller: CallerRequest = Field(default_factory=CallerRequest)

    # Cố ý KHÔNG có `acting_user_id`. Nếu người gọi khai được nó thì mọi kết nối
    # riêng trong workspace mở toang — đây là cái bẫy §2.4 trên chính route này.


class ToolSummary(BaseModel):
    tool_id: str
    version: str
    provider: str
    description: str
    side_effect: str
    required_scopes: list[str]
    input_schema: dict[str, Any]


class InvokeResponse(BaseModel):
    invocation_id: str
    tool_id: str
    tool_version: str
    status: str
    replayed: bool
    attempts: int
    duration_ms: float
    provider_reference: str | None
    output: dict[str, Any]

    # Không có trường nào mang credential, và không có trường tự do nào để một
    # giá trị lạ đi nhờ ra ngoài.


def _summary(tool: Any) -> ToolSummary:
    return ToolSummary(
        tool_id=tool.tool_id,
        version=tool.version,
        provider=tool.provider,
        description=tool.description,
        side_effect=tool.side_effect.value,
        required_scopes=sorted(tool.required_scopes),
        input_schema=dict(tool.input_schema.json_schema),
    )


# ─── Route ───────────────────────────────────────────────────────────────────


@router.get("")
async def list_tools(_context: CurrentContext) -> list[ToolSummary]:
    """Danh mục tool. Phase 3 đọc đúng đường này để dựng tool list cho LLM."""
    return [_summary(tool) for tool in _REGISTRY.list_tools()]


@router.get("/{tool_id}")
async def get_tool(
    _context: CurrentContext,
    tool_id: Annotated[str, Path(max_length=128)],
) -> ToolSummary:
    return _summary(_REGISTRY.get_tool(tool_id))


@router.post("/{tool_id}/invoke")
async def invoke(
    context: CurrentContext,
    gateway: Annotated[IntegrationGateway, Depends(get_gateway)],
    settings: Annotated[Settings, Depends(get_settings)],
    payload: InvokeRequest,
    tool_id: Annotated[str, Path(max_length=128)],
) -> InvokeResponse:
    if not settings.integration_internal_tools_enabled:
        raise AuthorizationError("Endpoint gọi tool nội bộ đang tắt.")

    await enforce_rate_limit(
        global_key("tool-invoke", context.user_id),
        limit=_INVOKES_PER_USER,
        window_seconds=_INVOKE_WINDOW_SECONDS,
    )

    execution_id = payload.caller.execution_id
    if context.run is not None:
        # Worker không chọn connection. Nó chỉ nói node nào đang chạy; connection là thứ
        # người chạy đã chọn cho node đó lúc tạo lần chạy.
        connection_id = context.run.connections.get(payload.caller.node_id or "")
        if connection_id is None:
            raise AuthorizationError(
                "Lần chạy này không có connection nào cho node đang gọi tool.",
                details={"node_id": payload.caller.node_id},
            )
        execution_id = context.run.execution_id
    elif payload.connection_id is None:
        raise ValidationError("Thiếu connection_id.")
    else:
        connection_id = payload.connection_id

    result = await gateway.invoke(
        ToolInvocation(
            tool_id=tool_id,
            connection_id=connection_id,
            tenant_id=context.organization_id,
            workspace_id=context.workspace_id,
            # Người đang hành động là người đã xác thực, không phải một trường
            # trong thân request. Kết nối PRIVATE dựa hoàn toàn vào điều này.
            acting_user_id=context.user_id,
            # Tool `DESTRUCTIVE` gọi bởi một lần chạy bị policy đòi phê duyệt.
            principal_kind=context.principal_kind,
            principal_roles=context.roles,
            input=payload.input,
            tool_version=payload.tool_version,
            idempotency_key=payload.idempotency_key,
            caller=CallerRef(
                agent_id=payload.caller.agent_id,
                workflow_id=payload.caller.workflow_id,
                execution_id=execution_id,
                node_id=payload.caller.node_id,
                execution_trace_id=payload.caller.execution_trace_id,
                parent_step_id=payload.caller.parent_step_id,
            ),
        )
    )

    return InvokeResponse(
        invocation_id=result.invocation_id,
        tool_id=result.tool_id,
        tool_version=result.tool_version,
        status="replayed" if result.replayed else "succeeded",
        replayed=result.replayed,
        attempts=result.attempts,
        duration_ms=round(result.duration_ms, 1),
        provider_reference=result.provider_reference,
        output=dict(result.output),
    )


# ─── Danh mục tool, đọc công khai ────────────────────────────────────────────
#
# `router` ở trên có tiền tố `/internal` và là ranh giới tiến trình của §2.4 —
# nó nhận `connection_id`, giải mã credential, thực thi side effect. Cái dưới
# đây chỉ trả về *mô tả* tool (id, provider, mô tả, đọc/ghi), thứ giao diện
# "tạo Agent" cần để dựng danh sách tool cho người dùng chọn. Không chạm
# credential, không thực thi gì — nên nó là `/v1` bình thường và chỉ cần một
# token hợp lệ, không cần tenant context (danh mục là cấu hình triển khai,
# không đổi theo workspace).

public_router = APIRouter(prefix="/v1/tools", tags=["integration"])


@public_router.get("")
async def list_tool_catalog(
    _user_id: Annotated[str, Depends(require_user_id)],
) -> list[ToolSummary]:
    """Danh mục tool cho giao diện tạo Agent."""
    return [_summary(tool) for tool in _REGISTRY.list_tools()]
