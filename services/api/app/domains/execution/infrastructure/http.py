"""Router HTTP của Execution: chạy workflow bằng tay, và xem nó đang ở đâu."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext, current_context, set_context
from app.core.db import get_session, session_scope
from app.core.http_auth import require_user_id, user_principal
from app.domains.execution.application.ports import (
    ExecutionLauncher,
    TenantGuard,
    WorkflowRunSource,
)
from app.domains.execution.application.service import (
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    ExecutionService,
    ExecutionSummary,
)
from app.domains.execution.application.starting import ExecutionStarter
from app.domains.execution.domain.entities import (
    Execution,
    ExecutionStatus,
    Failure,
    NodeExecution,
)
from app.domains.execution.infrastructure.repositories import SqlAlchemyExecutionRepository

router = APIRouter(prefix="/v1/executions", tags=["execution"])

ORGANIZATION_HEADER = "x-organization-id"
WORKSPACE_HEADER = "x-workspace-id"

#: Khoá trong ``app.state``, gắn bởi entrypoint đang mount router này.
EXECUTION_TENANT_GUARD_FACTORY = "execution_tenant_guard_factory"
EXECUTION_RUN_SOURCE_FACTORY = "execution_run_source_factory"
EXECUTION_LAUNCHER_FACTORY = "execution_launcher_factory"


# ─── Dependency ──────────────────────────────────────────────────────────────


def _from_state(request: Request, key: str) -> Any:
    factory = getattr(request.app.state, key, None)
    if factory is None:
        raise RuntimeError(
            f"Chưa gắn {key} vào app.state. Xem app/entrypoints/http.py — mọi entrypoint "
            "phục vụ router này đều phải nối các domain khác vào."
        )
    return factory


async def resolve_context(
    request: Request,
    user_id: Annotated[str, Depends(require_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
    header_organization_id: Annotated[str | None, Header(alias=ORGANIZATION_HEADER)] = None,
    header_workspace_id: Annotated[str | None, Header(alias=WORKSPACE_HEADER)] = None,
) -> Any:
    """Phân giải tenant từ token và membership, **không** từ thân request (§19)."""
    guard: TenantGuard = _from_state(request, EXECUTION_TENANT_GUARD_FACTORY)(session)
    context = await guard.require_workspace_context(
        user_id=user_id,
        organization_id=header_organization_id,
        workspace_id=header_workspace_id,
    )
    ctx = current_context()
    if ctx is not None:
        set_context(
            RequestContext(
                request_id=ctx.request_id,
                correlation_id=ctx.correlation_id,
                tenant_id=context.organization_id,
                workspace_id=context.workspace_id,
                principal=user_principal(user_id, context.roles),
            )
        )
    return context


CurrentContext = Annotated[Any, Depends(resolve_context)]


def get_service(session: Annotated[AsyncSession, Depends(get_session)]) -> ExecutionService:
    return ExecutionService(repository=SqlAlchemyExecutionRepository(session))


@asynccontextmanager
async def execution_records() -> AsyncIterator[ExecutionService]:
    """Một transaction riêng, commit khi thoát — không phải transaction của request."""
    async with session_scope() as session:
        yield ExecutionService(repository=SqlAlchemyExecutionRepository(session))


def get_starter(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> ExecutionStarter:
    runs: WorkflowRunSource = _from_state(request, EXECUTION_RUN_SOURCE_FACTORY)(session)
    launcher: ExecutionLauncher = _from_state(request, EXECUTION_LAUNCHER_FACTORY)()
    return ExecutionStarter(runs=runs, launcher=launcher, records=execution_records)


# ─── Schema vào/ra ───────────────────────────────────────────────────────────


class StartExecutionRequest(BaseModel):
    workflow_id: str = Field(max_length=64)
    #: Bỏ trống thì chạy version hiện tại.
    version: int | None = Field(default=None, ge=1)
    inputs: dict[str, Any] = Field(default_factory=dict)
    #: Node tool → connection. Thắng `connections_by_provider`.
    connections: dict[str, str] = Field(default_factory=dict)
    #: Provider → connection, áp cho mọi node tool của provider đó.
    connections_by_provider: dict[str, str] = Field(default_factory=dict)


class FailureResponse(BaseModel):
    category: str
    message: str


class ExecutionResponse(BaseModel):
    id: str
    workflow_id: str
    workflow_version_id: str
    status: str
    trigger_type: str
    initiated_by: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: FailureResponse | None


class ExecutionSummaryResponse(ExecutionResponse):
    #: Số hàng node đã ghi. Vòng lặp chưa tới thì các lượt của nó chưa được đếm.
    steps: int
    steps_done: int
    input_tokens: int
    output_tokens: int


class NodeResponse(BaseModel):
    node_path: str
    node_id: str
    node_type: str
    status: str
    attempt: int
    started_at: datetime | None
    finished_at: datetime | None
    error: FailureResponse | None
    #: Token model đã dùng, với node `llm`.
    usage: dict[str, int] | None = None


class ExecutionDetailResponse(ExecutionResponse):
    inputs: dict[str, Any]
    #: Node tool → connection mà lần chạy này dùng.
    connections: dict[str, str]
    nodes: list[NodeResponse]


def _failure(failure: Failure | None) -> FailureResponse | None:
    if failure is None:
        return None
    return FailureResponse(category=str(failure.category), message=failure.message)


def _execution(execution: Execution) -> ExecutionResponse:
    return ExecutionResponse(
        id=execution.id,
        workflow_id=execution.workflow_id,
        workflow_version_id=execution.workflow_version_id,
        status=str(execution.status),
        trigger_type=execution.trigger_type,
        initiated_by=execution.initiated_by,
        created_at=execution.created_at,
        started_at=execution.started_at,
        finished_at=execution.finished_at,
        error=_failure(execution.failure),
    )


def _summary(summary: ExecutionSummary) -> ExecutionSummaryResponse:
    return ExecutionSummaryResponse(
        **_execution(summary.execution).model_dump(),
        steps=summary.tally.steps,
        steps_done=summary.tally.done,
        input_tokens=summary.tally.input_tokens,
        output_tokens=summary.tally.output_tokens,
    )


def _node(node: NodeExecution) -> NodeResponse:
    return NodeResponse(
        node_path=node.node_path,
        node_id=node.node_id,
        node_type=node.node_type,
        status=str(node.status),
        attempt=node.attempt,
        started_at=node.started_at,
        finished_at=node.finished_at,
        error=_failure(node.failure),
        usage=None if node.usage is None else dict(node.usage),
    )


# ─── Route ───────────────────────────────────────────────────────────────────


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def start_execution(
    payload: StartExecutionRequest,
    context: CurrentContext,
    response: Response,
    starter: Annotated[ExecutionStarter, Depends(get_starter)],
) -> ExecutionResponse:
    execution = await starter.start_manual(
        tenant_id=context.organization_id,
        workspace_id=context.workspace_id,
        actor_id=context.user_id,
        workflow_id=payload.workflow_id,
        version=payload.version,
        inputs=payload.inputs,
        by_node=payload.connections,
        by_provider=payload.connections_by_provider,
    )
    response.headers["Location"] = f"/v1/executions/{execution.id}"
    return _execution(execution)


@router.get("")
async def list_executions(
    context: CurrentContext,
    service: Annotated[ExecutionService, Depends(get_service)],
    workflow_id: Annotated[str | None, Query(max_length=64)] = None,
    status: Annotated[ExecutionStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIST_LIMIT)] = DEFAULT_LIST_LIMIT,
) -> list[ExecutionSummaryResponse]:
    """Lần chạy của workspace đang chọn, mới nhất trước."""
    found = await service.list_recent(
        tenant_id=context.organization_id,
        workspace_id=context.workspace_id,
        workflow_id=workflow_id,
        status=status,
        limit=limit,
    )
    return [_summary(summary) for summary in found]


@router.get("/{execution_id}")
async def get_execution(
    execution_id: Annotated[str, Path(max_length=64)],
    context: CurrentContext,
    service: Annotated[ExecutionService, Depends(get_service)],
) -> ExecutionDetailResponse:
    progress = await service.get_progress(
        tenant_id=context.organization_id,
        execution_id=execution_id,
        workspace_id=context.workspace_id,
    )
    execution = progress.execution
    return ExecutionDetailResponse(
        **_execution(execution).model_dump(),
        inputs=dict(execution.inputs),
        connections=dict(execution.connection_bindings),
        nodes=[_node(node) for node in progress.nodes],
    )
