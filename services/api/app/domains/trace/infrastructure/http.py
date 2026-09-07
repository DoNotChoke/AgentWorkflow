"""Router HTTP của Trace — chỉ đọc.

Trace được ghi bởi domain khác (Agent, Integration) qua `TraceService`, gọi
trực tiếp trong tiến trình — không có route ghi nào ở đây
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Path, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext, current_context, set_context
from app.core.db import get_session
from app.core.http_auth import require_user_id, user_principal
from app.domains.trace.application.ports import TenantGuard
from app.domains.trace.application.service import TraceDetail, TraceService
from app.domains.trace.infrastructure.repositories import SqlAlchemyExecutionTraceRepository

router = APIRouter(tags=["trace"])

ORGANIZATION_HEADER = "x-organization-id"
WORKSPACE_HEADER = "x-workspace-id"

#: Khoá trong ``app.state``. Gắn bởi entrypoint đang mount router này — router
#: không tự dựng được `TenantGuard`, nó thuộc domain Tenant.
TRACE_TENANT_GUARD_FACTORY = "trace_tenant_guard_factory"


# ─── Dependency ──────────────────────────────────────────────────────────────


def get_tenant_guard(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TenantGuard:
    factory = getattr(request.app.state, TRACE_TENANT_GUARD_FACTORY, None)
    if factory is None:
        raise RuntimeError(
            "Chưa gắn trace_tenant_guard_factory vào app.state. "
            "Xem app/entrypoints/http.py — mọi entrypoint phục vụ router này "
            "đều phải nối domain Tenant vào."
        )
    guard: TenantGuard = factory(session)
    return guard


async def resolve_context(
    user_id: Annotated[str, Depends(require_user_id)],
    guard: Annotated[TenantGuard, Depends(get_tenant_guard)],
    header_organization_id: Annotated[str | None, Header(alias=ORGANIZATION_HEADER)] = None,
    header_workspace_id: Annotated[str | None, Header(alias=WORKSPACE_HEADER)] = None,
) -> Any:
    """Phân giải tenant từ token và membership, **không** từ thân request (§19)."""
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


def get_trace_service(session: Annotated[AsyncSession, Depends(get_session)]) -> TraceService:
    return TraceService(traces=SqlAlchemyExecutionTraceRepository(session))


# ─── Schema ra ───────────────────────────────────────────────────────────────


class TraceStepResponse(BaseModel):
    id: str
    kind: str
    status: str
    parent_step_id: str | None
    started_at: datetime
    completed_at: datetime
    duration_ms: float
    attributes: dict[str, Any]
    error_category: str | None


class TraceSubjectResponse(BaseModel):
    agent_id: str | None
    conversation_id: str | None
    workflow_id: str | None
    workflow_version_id: str | None
    execution_id: str | None


class TraceSummaryResponse(BaseModel):
    id: str
    source: str
    status: str
    subject: TraceSubjectResponse
    initiated_by: str
    started_at: datetime
    completed_at: datetime | None
    duration_ms: float | None
    error_category: str | None


class TraceResponse(TraceSummaryResponse):
    steps: list[TraceStepResponse]


def _subject(trace: Any) -> TraceSubjectResponse:
    subject = trace.subject
    return TraceSubjectResponse(
        agent_id=subject.agent_id,
        conversation_id=subject.conversation_id,
        workflow_id=subject.workflow_id,
        workflow_version_id=subject.workflow_version_id,
        execution_id=subject.execution_id,
    )


def _summary(trace: Any) -> TraceSummaryResponse:
    return TraceSummaryResponse(
        id=trace.id,
        source=str(trace.source),
        status=str(trace.status),
        subject=_subject(trace),
        initiated_by=trace.initiated_by,
        started_at=trace.started_at,
        completed_at=trace.completed_at,
        duration_ms=trace.duration_ms,
        error_category=trace.error_category,
    )


def _detail(detail: TraceDetail) -> TraceResponse:
    return TraceResponse(
        **_summary(detail.trace).model_dump(),
        steps=[
            TraceStepResponse(
                id=step.id,
                kind=str(step.kind),
                status=str(step.status),
                parent_step_id=step.parent_step_id,
                started_at=step.started_at,
                completed_at=step.completed_at,
                duration_ms=step.duration_ms,
                attributes=dict(step.attributes),
                error_category=step.error_category,
            )
            for step in detail.steps
        ],
    )


# ─── Route ───────────────────────────────────────────────────────────────────


@router.get("/v1/traces/{trace_id}")
async def get_trace(
    context: CurrentContext,
    service: Annotated[TraceService, Depends(get_trace_service)],
    trace_id: Annotated[str, Path(max_length=64)],
) -> TraceResponse:
    detail = await service.get(tenant_id=context.organization_id, trace_id=trace_id)
    return _detail(detail)


@router.get("/v1/conversations/{conversation_id}/traces")
async def list_conversation_traces(
    context: CurrentContext,
    service: Annotated[TraceService, Depends(get_trace_service)],
    conversation_id: Annotated[str, Path(max_length=64)],
) -> list[TraceSummaryResponse]:
    traces = await service.list_for_conversation(
        tenant_id=context.organization_id, conversation_id=conversation_id
    )
    return [_summary(trace) for trace in traces]
