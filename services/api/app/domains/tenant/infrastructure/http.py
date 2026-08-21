"""Router HTTP của Tenant, và nơi tenant context được phân giải."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext, current_context, set_context
from app.core.db import get_session
from app.core.errors import AuthorizationError
from app.core.http_auth import require_user_id, user_principal
from app.domains.tenant.application.service import TenantService
from app.domains.tenant.domain.entities import (
    Organization,
    OrgRole,
    TenantAccess,
    Workspace,
    WorkspaceRole,
)
from app.domains.tenant.infrastructure.repositories import (
    SqlAlchemyMembershipRepository,
    SqlAlchemyOrganizationRepository,
    SqlAlchemyWorkspaceRepository,
)

router = APIRouter(prefix="/v1", tags=["tenant"])

#: Header client dùng để chọn tenant và workspace cho request hiện tại.
ORGANIZATION_HEADER = "x-organization-id"
WORKSPACE_HEADER = "x-workspace-id"


def build_tenant_service(session: AsyncSession) -> TenantService:
    """Dựng ``TenantService`` từ một session.

    Entrypoint gắn chính hàm này vào ``app.state`` để domain Identity lấy được
    một ``TenantProvisioner`` mà không phải import Tenant.
    """
    return TenantService(
        organizations=SqlAlchemyOrganizationRepository(session),
        workspaces=SqlAlchemyWorkspaceRepository(session),
        memberships=SqlAlchemyMembershipRepository(session),
    )


def get_tenant_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TenantService:
    return build_tenant_service(session)


# ─── Phân giải tenant context ────────────────────────────────────────────────


def _encode_roles(access: TenantAccess) -> frozenset[str]:
    """Mã hoá vai trò thành chuỗi cho ``Principal.roles``.

    Domain khác đọc được vai trò từ ``RequestContext`` mà không phải import
    enum của Tenant.
    """
    roles = {f"org:{access.org_role}"}
    if access.workspace_role is not None:
        roles.add(f"ws:{access.workspace_role}")
    return frozenset(roles)


async def resolve_tenant_context(
    request: Request,
    user_id: Annotated[str, Depends(require_user_id)],
    service: Annotated[TenantService, Depends(get_tenant_service)],
    header_organization_id: Annotated[str | None, Header(alias=ORGANIZATION_HEADER)] = None,
    header_workspace_id: Annotated[str | None, Header(alias=WORKSPACE_HEADER)] = None,
) -> TenantAccess:
    """Xác định tenant của request hiện tại và ghi vào ``RequestContext``."""
    if header_organization_id is None:
        access = await service.resolve_default_access(user_id)
        if access is None:
            raise AuthorizationError("Tài khoản này chưa thuộc organization nào.")
        if header_workspace_id is not None:
            access = await service.require_access(
                user_id=user_id,
                organization_id=access.organization_id,
                workspace_id=header_workspace_id,
            )
    else:
        access = await service.require_access(
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
                tenant_id=access.organization_id,
                workspace_id=access.workspace_id,
                principal=user_principal(user_id, _encode_roles(access)),
            )
        )

    request.state.tenant_id = access.organization_id
    request.state.workspace_id = access.workspace_id
    return access


TenantContext = Annotated[TenantAccess, Depends(resolve_tenant_context)]


# ─── Schema vào/ra ───────────────────────────────────────────────────────────


class OrganizationResponse(BaseModel):
    id: str
    name: str
    slug: str
    role: str
    created_at: datetime
    onboarded: bool


class SlugAvailabilityResponse(BaseModel):
    slug: str
    available: bool


class WorkspaceResponse(BaseModel):
    id: str
    organization_id: str
    name: str
    slug: str
    created_at: datetime


class MemberResponse(BaseModel):
    user_id: str
    role: str
    created_at: datetime


class CreateOrganizationRequest(BaseModel):
    name: str = Field(max_length=200)


class UpdateOrganizationRequest(BaseModel):
    name: str = Field(max_length=200)
    slug: str | None = Field(default=None, max_length=60)


class CreateWorkspaceRequest(BaseModel):
    name: str = Field(max_length=200)


class UpdateWorkspaceRequest(BaseModel):
    name: str = Field(max_length=200)
    #: Bỏ trống thì giữ nguyên địa chỉ hiện tại. Khác organization, địa chỉ
    #: workspace không đóng băng — nhưng đổi nó vẫn phá link cũ nên phải là
    #: hành động tường minh, không tự chạy theo tên.
    slug: str | None = Field(default=None, max_length=60)


class AddMemberRequest(BaseModel):
    user_id: str = Field(max_length=64)
    role: OrgRole = OrgRole.MEMBER


class ChangeRoleRequest(BaseModel):
    role: OrgRole


class SetWorkspaceMemberRequest(BaseModel):
    user_id: str = Field(max_length=64)
    role: WorkspaceRole


def _to_workspace_response(workspace: Workspace) -> WorkspaceResponse:
    return WorkspaceResponse(
        id=workspace.id,
        organization_id=workspace.organization_id,
        name=workspace.name,
        slug=workspace.slug,
        created_at=workspace.created_at,
    )


def _to_organization_response(organization: Organization, role: OrgRole) -> OrganizationResponse:
    return OrganizationResponse(
        id=organization.id,
        name=organization.name,
        slug=organization.slug,
        role=str(role),
        created_at=organization.created_at,
        onboarded=organization.is_onboarded,
    )


# ─── Organization ────────────────────────────────────────────────────────────


@router.get("/organizations")
async def list_organizations(
    user_id: Annotated[str, Depends(require_user_id)],
    service: Annotated[TenantService, Depends(get_tenant_service)],
) -> list[OrganizationResponse]:
    """Danh sách organization của chính người gọi."""
    pairs = await service.list_organizations_of_user(user_id)
    return [_to_organization_response(org, role) for org, role in pairs]


@router.post("/organizations", status_code=status.HTTP_201_CREATED)
async def create_organization(
    payload: CreateOrganizationRequest,
    user_id: Annotated[str, Depends(require_user_id)],
    service: Annotated[TenantService, Depends(get_tenant_service)],
) -> OrganizationResponse:
    access = await service.create_organization(user_id=user_id, name=payload.name)
    pairs = await service.list_organizations_of_user(user_id)
    for organization, role in pairs:
        if organization.id == access.organization_id:
            return _to_organization_response(organization, role)
    raise AuthorizationError("Không đọc lại được organization vừa tạo.")


@router.get("/organizations/slug-available")
async def check_slug_available(
    _user_id: Annotated[str, Depends(require_user_id)],
    service: Annotated[TenantService, Depends(get_tenant_service)],
    slug: Annotated[str, Query(max_length=60)],
) -> SlugAvailabilityResponse:
    """Địa chỉ này còn trống không — để wizard thiết lập báo ngay lúc gõ."""
    return SlugAvailabilityResponse(
        slug=slug.strip().lower(),
        available=await service.is_slug_available(slug),
    )


@router.patch("/organizations/current")
async def update_current_organization(
    payload: UpdateOrganizationRequest,
    access: TenantContext,
    service: Annotated[TenantService, Depends(get_tenant_service)],
) -> OrganizationResponse:
    organization = await service.update_organization(
        actor_id=access.user_id,
        organization_id=access.organization_id,
        name=payload.name,
        slug=payload.slug,
    )
    return _to_organization_response(organization, access.org_role)


@router.post("/organizations/current/complete-onboarding")
async def complete_onboarding(
    access: TenantContext,
    service: Annotated[TenantService, Depends(get_tenant_service)],
) -> OrganizationResponse:
    """Chốt bước thiết lập. Sau lời gọi này địa chỉ đóng băng."""
    organization = await service.complete_onboarding(
        actor_id=access.user_id,
        organization_id=access.organization_id,
    )
    return _to_organization_response(organization, access.org_role)


# ─── Thành viên organization ─────────────────────────────────────────────────


@router.get("/organizations/current/members")
async def list_members(
    access: TenantContext,
    service: Annotated[TenantService, Depends(get_tenant_service)],
) -> list[MemberResponse]:
    memberships = await service.list_members(
        actor_id=access.user_id, organization_id=access.organization_id
    )
    return [
        MemberResponse(user_id=m.user_id, role=str(m.role), created_at=m.created_at)
        for m in memberships
    ]


@router.post("/organizations/current/members", status_code=status.HTTP_201_CREATED)
async def add_member(
    payload: AddMemberRequest,
    access: TenantContext,
    service: Annotated[TenantService, Depends(get_tenant_service)],
) -> MemberResponse:
    membership = await service.add_member(
        actor_id=access.user_id,
        organization_id=access.organization_id,
        user_id=payload.user_id,
        role=payload.role,
    )
    return MemberResponse(
        user_id=membership.user_id,
        role=str(membership.role),
        created_at=membership.created_at,
    )


@router.patch("/organizations/current/members/{member_user_id}")
async def change_member_role(
    member_user_id: Annotated[str, Path(max_length=64)],
    payload: ChangeRoleRequest,
    access: TenantContext,
    service: Annotated[TenantService, Depends(get_tenant_service)],
) -> None:
    await service.change_member_role(
        actor_id=access.user_id,
        organization_id=access.organization_id,
        user_id=member_user_id,
        role=payload.role,
    )


@router.delete(
    "/organizations/current/members/{member_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_member(
    member_user_id: Annotated[str, Path(max_length=64)],
    access: TenantContext,
    service: Annotated[TenantService, Depends(get_tenant_service)],
) -> None:
    await service.remove_member(
        actor_id=access.user_id,
        organization_id=access.organization_id,
        user_id=member_user_id,
    )


# ─── Workspace ───────────────────────────────────────────────────────────────


@router.get("/workspaces")
async def list_workspaces(
    access: TenantContext,
    service: Annotated[TenantService, Depends(get_tenant_service)],
) -> list[WorkspaceResponse]:
    workspaces = await service.list_workspaces(
        actor_id=access.user_id, organization_id=access.organization_id
    )
    return [_to_workspace_response(w) for w in workspaces]


@router.post("/workspaces", status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: CreateWorkspaceRequest,
    access: TenantContext,
    service: Annotated[TenantService, Depends(get_tenant_service)],
) -> WorkspaceResponse:
    workspace = await service.create_workspace(
        actor_id=access.user_id,
        organization_id=access.organization_id,
        name=payload.name,
    )
    return _to_workspace_response(workspace)


@router.patch("/workspaces/{workspace_id}")
async def rename_workspace(
    workspace_id: Annotated[str, Path(max_length=64)],
    payload: UpdateWorkspaceRequest,
    access: TenantContext,
    service: Annotated[TenantService, Depends(get_tenant_service)],
) -> WorkspaceResponse:
    workspace = await service.rename_workspace(
        actor_id=access.user_id,
        workspace_id=workspace_id,
        name=payload.name,
        slug=payload.slug,
    )
    return _to_workspace_response(workspace)


@router.delete("/workspaces/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    workspace_id: Annotated[str, Path(max_length=64)],
    access: TenantContext,
    service: Annotated[TenantService, Depends(get_tenant_service)],
) -> None:
    await service.delete_workspace(actor_id=access.user_id, workspace_id=workspace_id)


@router.put("/workspaces/{workspace_id}/members", status_code=status.HTTP_204_NO_CONTENT)
async def set_workspace_member(
    workspace_id: Annotated[str, Path(max_length=64)],
    payload: SetWorkspaceMemberRequest,
    access: TenantContext,
    service: Annotated[TenantService, Depends(get_tenant_service)],
) -> None:
    await service.set_workspace_member(
        actor_id=access.user_id,
        workspace_id=workspace_id,
        user_id=payload.user_id,
        role=payload.role,
    )


@router.delete(
    "/workspaces/{workspace_id}/members/{member_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_workspace_member(
    workspace_id: Annotated[str, Path(max_length=64)],
    member_user_id: Annotated[str, Path(max_length=64)],
    access: TenantContext,
    service: Annotated[TenantService, Depends(get_tenant_service)],
) -> None:
    await service.remove_workspace_member(
        actor_id=access.user_id,
        workspace_id=workspace_id,
        user_id=member_user_id,
    )
