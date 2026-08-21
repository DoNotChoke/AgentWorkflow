"""Hiện thực hoá các port của Tenant trên PostgreSQL."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.tenant.application.ports import AlreadyMemberError, SlugAlreadyTakenError
from app.domains.tenant.domain.entities import (
    Organization,
    OrgMembership,
    OrgRole,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.domains.tenant.infrastructure.models import (
    OrganizationModel,
    OrgMembershipModel,
    WorkspaceMembershipModel,
    WorkspaceModel,
)


def _to_organization(row: OrganizationModel) -> Organization:
    return Organization(
        id=row.id,
        name=row.name,
        slug=row.slug,
        created_at=row.created_at,
        updated_at=row.updated_at,
        onboarded_at=row.onboarded_at,
    )


def _to_workspace(row: WorkspaceModel) -> Workspace:
    return Workspace(
        id=row.id,
        organization_id=row.organization_id,
        name=row.name,
        slug=row.slug,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_org_membership(row: OrgMembershipModel) -> OrgMembership:
    return OrgMembership(
        id=row.id,
        organization_id=row.organization_id,
        user_id=row.user_id,
        role=OrgRole(row.role),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_workspace_membership(row: WorkspaceMembershipModel) -> WorkspaceMembership:
    return WorkspaceMembership(
        id=row.id,
        workspace_id=row.workspace_id,
        user_id=row.user_id,
        role=WorkspaceRole(row.role),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyOrganizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, organization: Organization) -> None:
        try:
            # SAVEPOINT: khi slug trùng, chỉ câu INSERT này bị huỷ chứ không
            # cuốn theo cả unit of work — tầng application còn phải thử lại với
            # slug khác trên cùng session.
            async with self._session.begin_nested():
                self._session.add(
                    OrganizationModel(
                        id=organization.id,
                        name=organization.name,
                        slug=organization.slug,
                        created_at=organization.created_at,
                        updated_at=organization.updated_at,
                    )
                )
                await self._session.flush()
        except IntegrityError as exc:
            raise SlugAlreadyTakenError(organization.slug) from exc

    async def get_by_id(self, organization_id: str) -> Organization | None:
        row = await self._session.scalar(
            select(OrganizationModel).where(OrganizationModel.id == organization_id)
        )
        return None if row is None else _to_organization(row)

    async def get_by_slug(self, slug: str) -> Organization | None:
        row = await self._session.scalar(
            select(OrganizationModel).where(OrganizationModel.slug == slug)
        )
        return None if row is None else _to_organization(row)

    async def update_profile(
        self, organization_id: str, *, name: str, slug: str | None, at: datetime
    ) -> None:
        values: dict[str, object] = {"name": name, "updated_at": at}
        if slug is not None:
            values["slug"] = slug

        try:
            # SAVEPOINT vì slug có ràng buộc unique: khi đụng, chỉ câu UPDATE
            # này bị huỷ chứ không cuốn theo cả unit of work của request.
            async with self._session.begin_nested():
                await self._session.execute(
                    update(OrganizationModel)
                    .where(OrganizationModel.id == organization_id)
                    .values(**values)
                )
                await self._session.flush()
        except IntegrityError as exc:
            raise SlugAlreadyTakenError(slug or "") from exc

    async def mark_onboarded(self, organization_id: str, *, at: datetime) -> None:
        await self._session.execute(
            update(OrganizationModel)
            .where(OrganizationModel.id == organization_id)
            .values(onboarded_at=at, updated_at=at)
        )


class SqlAlchemyWorkspaceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, workspace: Workspace) -> None:
        self._session.add(
            WorkspaceModel(
                id=workspace.id,
                organization_id=workspace.organization_id,
                name=workspace.name,
                slug=workspace.slug,
                created_at=workspace.created_at,
                updated_at=workspace.updated_at,
            )
        )
        await self._session.flush()

    async def get_by_id(self, workspace_id: str) -> Workspace | None:
        row = await self._session.scalar(
            select(WorkspaceModel).where(WorkspaceModel.id == workspace_id)
        )
        return None if row is None else _to_workspace(row)

    async def list_for_organization(self, organization_id: str) -> list[Workspace]:
        rows = await self._session.scalars(
            select(WorkspaceModel)
            .where(WorkspaceModel.organization_id == organization_id)
            .order_by(WorkspaceModel.created_at)
        )
        return [_to_workspace(row) for row in rows]

    async def update_profile(
        self, workspace_id: str, *, name: str, slug: str | None, at: datetime
    ) -> None:
        values: dict[str, object] = {"name": name, "updated_at": at}
        if slug is not None:
            values["slug"] = slug

        try:
            # SAVEPOINT: ràng buộc unique là (organization_id, slug), nên khi
            # đụng chỉ câu UPDATE này bị huỷ chứ không cuốn theo cả request.
            async with self._session.begin_nested():
                await self._session.execute(
                    update(WorkspaceModel).where(WorkspaceModel.id == workspace_id).values(**values)
                )
                await self._session.flush()
        except IntegrityError as exc:
            raise SlugAlreadyTakenError(slug or "") from exc

    async def delete(self, workspace_id: str) -> None:
        await self._session.execute(delete(WorkspaceModel).where(WorkspaceModel.id == workspace_id))


class SqlAlchemyMembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ─── Organization ────────────────────────────────────────────────────────

    async def add_org_membership(self, membership: OrgMembership) -> None:
        try:
            async with self._session.begin_nested():
                self._session.add(
                    OrgMembershipModel(
                        id=membership.id,
                        organization_id=membership.organization_id,
                        user_id=membership.user_id,
                        role=str(membership.role),
                        created_at=membership.created_at,
                        updated_at=membership.updated_at,
                    )
                )
                await self._session.flush()
        except IntegrityError as exc:
            raise AlreadyMemberError(membership.user_id) from exc

    async def get_org_membership(
        self, *, organization_id: str, user_id: str
    ) -> OrgMembership | None:
        row = await self._session.scalar(
            select(OrgMembershipModel).where(
                OrgMembershipModel.organization_id == organization_id,
                OrgMembershipModel.user_id == user_id,
            )
        )
        return None if row is None else _to_org_membership(row)

    async def list_org_memberships(self, organization_id: str) -> list[OrgMembership]:
        rows = await self._session.scalars(
            select(OrgMembershipModel)
            .where(OrgMembershipModel.organization_id == organization_id)
            .order_by(OrgMembershipModel.created_at)
        )
        return [_to_org_membership(row) for row in rows]

    async def list_organizations_of_user(self, user_id: str) -> list[OrgMembership]:
        rows = await self._session.scalars(
            select(OrgMembershipModel)
            .where(OrgMembershipModel.user_id == user_id)
            .order_by(OrgMembershipModel.created_at)
        )
        return [_to_org_membership(row) for row in rows]

    async def count_owners(self, organization_id: str) -> int:
        total = await self._session.scalar(
            select(func.count())
            .select_from(OrgMembershipModel)
            .where(
                OrgMembershipModel.organization_id == organization_id,
                OrgMembershipModel.role == str(OrgRole.OWNER),
            )
        )
        return int(total or 0)

    async def set_org_role(
        self, *, organization_id: str, user_id: str, role: OrgRole, at: datetime
    ) -> None:
        await self._session.execute(
            update(OrgMembershipModel)
            .where(
                OrgMembershipModel.organization_id == organization_id,
                OrgMembershipModel.user_id == user_id,
            )
            .values(role=str(role), updated_at=at)
        )

    async def remove_org_membership(self, *, organization_id: str, user_id: str) -> None:
        """Xoá luôn mọi quyền workspace của user trong organization đó.

        Nếu chỉ xoá org membership, các bản ghi workspace_membership còn lại sẽ
        thành quyền mồ côi — và nếu sau này user được thêm lại vào organization,
        họ lập tức lấy lại quyền cũ trên những workspace mà không ai cấp lại.
        """
        workspace_ids = select(WorkspaceModel.id).where(
            WorkspaceModel.organization_id == organization_id
        )
        await self._session.execute(
            delete(WorkspaceMembershipModel).where(
                WorkspaceMembershipModel.user_id == user_id,
                WorkspaceMembershipModel.workspace_id.in_(workspace_ids),
            )
        )
        await self._session.execute(
            delete(OrgMembershipModel).where(
                OrgMembershipModel.organization_id == organization_id,
                OrgMembershipModel.user_id == user_id,
            )
        )

    # ─── Workspace ───────────────────────────────────────────────────────────

    async def add_workspace_membership(self, membership: WorkspaceMembership) -> None:
        self._session.add(
            WorkspaceMembershipModel(
                id=membership.id,
                workspace_id=membership.workspace_id,
                user_id=membership.user_id,
                role=str(membership.role),
                created_at=membership.created_at,
                updated_at=membership.updated_at,
            )
        )
        await self._session.flush()

    async def get_workspace_membership(
        self, *, workspace_id: str, user_id: str
    ) -> WorkspaceMembership | None:
        row = await self._session.scalar(
            select(WorkspaceMembershipModel).where(
                WorkspaceMembershipModel.workspace_id == workspace_id,
                WorkspaceMembershipModel.user_id == user_id,
            )
        )
        return None if row is None else _to_workspace_membership(row)

    async def list_workspace_memberships(self, workspace_id: str) -> list[WorkspaceMembership]:
        rows = await self._session.scalars(
            select(WorkspaceMembershipModel)
            .where(WorkspaceMembershipModel.workspace_id == workspace_id)
            .order_by(WorkspaceMembershipModel.created_at)
        )
        return [_to_workspace_membership(row) for row in rows]

    async def set_workspace_role(
        self, *, workspace_id: str, user_id: str, role: WorkspaceRole, at: datetime
    ) -> None:
        await self._session.execute(
            update(WorkspaceMembershipModel)
            .where(
                WorkspaceMembershipModel.workspace_id == workspace_id,
                WorkspaceMembershipModel.user_id == user_id,
            )
            .values(role=str(role), updated_at=at)
        )

    async def remove_workspace_membership(self, *, workspace_id: str, user_id: str) -> None:
        await self._session.execute(
            delete(WorkspaceMembershipModel).where(
                WorkspaceMembershipModel.workspace_id == workspace_id,
                WorkspaceMembershipModel.user_id == user_id,
            )
        )
