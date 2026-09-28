"""Router HTTP của Workflow."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Path, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext, current_context, set_context
from app.core.db import get_session
from app.core.http_auth import require_user_id, user_principal
from app.domains.workflow.application.ports import ConnectionReader, TenantGuard, ToolCatalogReader
from app.domains.workflow.application.service import WorkflowService
from app.domains.workflow.domain.entities import Workflow, WorkflowDraft, WorkflowVersion
from app.domains.workflow.domain.validation import Issue, ValidationReport
from app.domains.workflow.infrastructure.repositories import SqlAlchemyWorkflowRepository

router = APIRouter(prefix="/v1/workflows", tags=["workflow"])

ORGANIZATION_HEADER = "x-organization-id"
WORKSPACE_HEADER = "x-workspace-id"

#: Khoá trong ``app.state``, gắn bởi entrypoint đang mount router này.
WORKFLOW_TENANT_GUARD_FACTORY = "workflow_tenant_guard_factory"
WORKFLOW_CATALOG_FACTORY = "workflow_catalog_factory"
WORKFLOW_CONNECTION_READER_FACTORY = "workflow_connection_reader_factory"


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
    guard: TenantGuard = _from_state(request, WORKFLOW_TENANT_GUARD_FACTORY)(session)
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


def get_service(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WorkflowService:
    catalog: ToolCatalogReader = _from_state(request, WORKFLOW_CATALOG_FACTORY)()
    connections: ConnectionReader = _from_state(request, WORKFLOW_CONNECTION_READER_FACTORY)(
        session
    )
    return WorkflowService(
        repository=SqlAlchemyWorkflowRepository(session),
        catalog=catalog,
        connections=connections,
    )


def _can_manage(context: Any) -> bool:
    return "ws:admin" in context.roles


# ─── Schema vào/ra ───────────────────────────────────────────────────────────


class CreateWorkflowRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class SaveDraftRequest(BaseModel):
    #: IR chưa chắc hợp lệ — đó là chủ đích (Q14). Trần kích thước do domain giữ.
    ir: dict[str, Any]


class IssueResponse(BaseModel):
    code: str
    message: str
    severity: str
    node: str | None
    field: list[str]


class ValidationResponse(BaseModel):
    ok: bool
    errors: list[IssueResponse]
    warnings: list[IssueResponse]


class WorkflowResponse(BaseModel):
    id: str
    name: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    current_version_id: str | None
    archived_at: datetime | None


class DraftResponse(BaseModel):
    workflow_id: str
    ir: dict[str, Any]
    updated_by: str
    updated_at: datetime
    validation: ValidationResponse


class VersionResponse(BaseModel):
    id: str
    workflow_id: str
    number: int
    ir: dict[str, Any]
    ir_sha256: str
    published_by: str
    published_at: datetime


class VersionSummaryResponse(BaseModel):
    """Danh sách version không mang IR — cùng lý do `workflows` không mang nó."""

    id: str
    number: int
    ir_sha256: str
    published_by: str
    published_at: datetime


def _issue(issue: Issue) -> IssueResponse:
    return IssueResponse(
        code=issue.code,
        message=issue.message,
        severity=str(issue.severity),
        node=issue.node,
        field=list(issue.field),
    )


def _validation(report: ValidationReport) -> ValidationResponse:
    return ValidationResponse(
        ok=report.ok,
        errors=[_issue(issue) for issue in report.errors],
        warnings=[_issue(issue) for issue in report.warnings],
    )


def _workflow(workflow: Workflow) -> WorkflowResponse:
    return WorkflowResponse(
        id=workflow.id,
        name=workflow.name,
        created_by=workflow.created_by,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
        current_version_id=workflow.current_version_id,
        archived_at=workflow.archived_at,
    )


def _draft(draft: WorkflowDraft, report: ValidationReport) -> DraftResponse:
    return DraftResponse(
        workflow_id=draft.workflow_id,
        ir=dict(draft.ir),
        updated_by=draft.updated_by,
        updated_at=draft.updated_at,
        validation=_validation(report),
    )


def _version(version: WorkflowVersion) -> VersionResponse:
    return VersionResponse(
        id=version.id,
        workflow_id=version.workflow_id,
        number=version.number,
        ir=dict(version.ir),
        ir_sha256=version.ir_sha256,
        published_by=version.published_by,
        published_at=version.published_at,
    )


def _summary(version: WorkflowVersion) -> VersionSummaryResponse:
    return VersionSummaryResponse(
        id=version.id,
        number=version.number,
        ir_sha256=version.ir_sha256,
        published_by=version.published_by,
        published_at=version.published_at,
    )


# ─── Route ───────────────────────────────────────────────────────────────────


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_workflow(
    payload: CreateWorkflowRequest,
    context: CurrentContext,
    service: Annotated[WorkflowService, Depends(get_service)],
) -> WorkflowResponse:
    workflow = await service.create(
        tenant_id=context.organization_id,
        workspace_id=context.workspace_id,
        name=payload.name,
        actor_id=context.user_id,
    )
    return _workflow(workflow)


@router.get("")
async def list_workflows(
    context: CurrentContext,
    service: Annotated[WorkflowService, Depends(get_service)],
) -> list[WorkflowResponse]:
    found = await service.list_workflows(
        tenant_id=context.organization_id, workspace_id=context.workspace_id
    )
    return [_workflow(workflow) for workflow in found]


@router.get("/{workflow_id}")
async def get_workflow(
    workflow_id: Annotated[str, Path(max_length=64)],
    context: CurrentContext,
    service: Annotated[WorkflowService, Depends(get_service)],
) -> WorkflowResponse:
    workflow = await service.get_workflow(
        tenant_id=context.organization_id, workflow_id=workflow_id
    )
    return _workflow(workflow)


@router.get("/{workflow_id}/draft")
async def get_draft(
    workflow_id: Annotated[str, Path(max_length=64)],
    context: CurrentContext,
    service: Annotated[WorkflowService, Depends(get_service)],
) -> DraftResponse:
    draft = await service.get_draft(tenant_id=context.organization_id, workflow_id=workflow_id)
    return _draft(draft, service.inspect(draft.ir))


@router.put("/{workflow_id}/draft")
async def save_draft(
    workflow_id: Annotated[str, Path(max_length=64)],
    payload: SaveDraftRequest,
    context: CurrentContext,
    service: Annotated[WorkflowService, Depends(get_service)],
) -> DraftResponse:
    """Lưu rồi mới soi: một IR chưa hợp lệ vẫn lưu được, và báo cáo đi kèm phản
    hồi để editor tô đúng chỗ sai (Q14)."""
    draft, report = await service.save_draft(
        tenant_id=context.organization_id,
        workflow_id=workflow_id,
        ir=payload.ir,
        actor_id=context.user_id,
        can_manage_workspace=_can_manage(context),
    )
    return _draft(draft, report)


@router.post("/{workflow_id}/publish", status_code=status.HTTP_201_CREATED)
async def publish_workflow(
    workflow_id: Annotated[str, Path(max_length=64)],
    context: CurrentContext,
    service: Annotated[WorkflowService, Depends(get_service)],
) -> VersionResponse:
    version = await service.publish(
        tenant_id=context.organization_id,
        workspace_id=context.workspace_id,
        workflow_id=workflow_id,
        actor_id=context.user_id,
        can_manage_workspace=_can_manage(context),
    )
    return _version(version)


@router.get("/{workflow_id}/versions")
async def list_versions(
    workflow_id: Annotated[str, Path(max_length=64)],
    context: CurrentContext,
    service: Annotated[WorkflowService, Depends(get_service)],
) -> list[VersionSummaryResponse]:
    versions = await service.list_versions(
        tenant_id=context.organization_id, workflow_id=workflow_id
    )
    return [_summary(version) for version in versions]


@router.get("/{workflow_id}/versions/{number}")
async def get_version(
    workflow_id: Annotated[str, Path(max_length=64)],
    number: Annotated[int, Path(ge=1)],
    context: CurrentContext,
    service: Annotated[WorkflowService, Depends(get_service)],
) -> VersionResponse:
    version = await service.get_version(
        tenant_id=context.organization_id, workflow_id=workflow_id, number=number
    )
    return _version(version)


@router.post("/{workflow_id}/archive")
async def archive_workflow(
    workflow_id: Annotated[str, Path(max_length=64)],
    context: CurrentContext,
    service: Annotated[WorkflowService, Depends(get_service)],
) -> WorkflowResponse:
    """Lưu trữ là mềm và đảo ngược được — không có endpoint xoá."""
    workflow = await service.archive_workflow(
        tenant_id=context.organization_id,
        workflow_id=workflow_id,
        actor_id=context.user_id,
        can_manage_workspace=_can_manage(context),
    )
    return _workflow(workflow)


@router.post("/{workflow_id}/unarchive")
async def unarchive_workflow(
    workflow_id: Annotated[str, Path(max_length=64)],
    context: CurrentContext,
    service: Annotated[WorkflowService, Depends(get_service)],
) -> WorkflowResponse:
    workflow = await service.unarchive_workflow(
        tenant_id=context.organization_id,
        workflow_id=workflow_id,
        actor_id=context.user_id,
        can_manage_workspace=_can_manage(context),
    )
    return _workflow(workflow)
