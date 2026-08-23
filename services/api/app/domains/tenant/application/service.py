"""Use case của Tenant: organization, workspace, thành viên, phân quyền."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.errors import AuthorizationError, ValidationError
from app.core.ids import ResourceKind, new_id
from app.core.logging import get_logger
from app.domains.tenant.application.ports import (
    AlreadyMemberError,
    MembershipRepository,
    OrganizationRepository,
    SlugAlreadyTakenError,
    UserDirectory,
    UserProfile,
    WorkspaceRepository,
)
from app.domains.tenant.domain.entities import (
    DEFAULT_WORKSPACE_NAME,
    Organization,
    OrgMembership,
    OrgRole,
    TenantAccess,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
    can_manage_organization,
    default_organization_name,
    ensure_can_assign_org_role,
    ensure_not_last_owner,
    ensure_not_last_workspace,
    ensure_slug_editable,
    normalize_name,
    normalize_slug,
    resolve_workspace_role,
    slugify,
)

log = get_logger(__name__)

_NOT_A_MEMBER = "Bạn không có quyền truy cập organization này."

#: Số lần thử thêm hậu tố ngẫu nhiên vào slug khi bị trùng.
_SLUG_ATTEMPTS = 5


# ─────────────────────────────────────────────────────────────────────────────
# Read model
#
# Màn hình thành viên cần ba mảnh ghép cùng lúc: membership, hồ sơ người dùng,
# và quyền workspace. Ghép sẵn ở đây thay vì để router gọi ba vòng — và cũng
# thay vì trả tuple ba phần tử mà nơi gọi phải nhớ thứ tự.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class WorkspaceAccess:
    """Quyền được cấp tường minh trên một workspace."""

    workspace_id: str
    name: str
    slug: str
    role: WorkspaceRole


@dataclass(frozen=True, slots=True)
class MemberView:
    """Một thành viên organization, đủ dữ liệu để hiển thị một dòng."""

    membership: OrgMembership
    profile: UserProfile | None
    workspaces: list[WorkspaceAccess]


@dataclass(frozen=True, slots=True)
class WorkspaceMemberView:
    """Một người có quyền tường minh trên một workspace."""

    membership: WorkspaceMembership
    profile: UserProfile | None


class TenantService:
    def __init__(
        self,
        *,
        organizations: OrganizationRepository,
        workspaces: WorkspaceRepository,
        memberships: MembershipRepository,
        users: UserDirectory,
    ) -> None:
        self._organizations = organizations
        self._workspaces = workspaces
        self._memberships = memberships
        self._users = users

    # ─── Cấp phát khi đăng ký ────────────────────────────────────────────────

    async def provision_for_new_user(self, *, user_id: str, display_name: str) -> TenantAccess:
        """Cấp organization và workspace mặc định cho một user vừa đăng ký."""
        access = await self.create_organization(
            user_id=user_id,
            name=default_organization_name(display_name),
        )
        log.info(
            "tenant_provisioned",
            user_id=user_id,
            organization_id=access.organization_id,
            workspace_id=access.workspace_id,
        )
        return access

    # ─── Organization ────────────────────────────────────────────────────────

    async def create_organization(self, *, user_id: str, name: str) -> TenantAccess:
        """Tạo organization mới. Người tạo trở thành OWNER.
        Kèm luôn một workspace ``Default``
        """
        normalized = normalize_name(name, field="Tên organization")
        now = datetime.now(tz=UTC)
        organization_id = new_id(ResourceKind.ORGANIZATION)

        organization = await self._add_with_unique_slug(
            base_name=normalized,
            build=lambda slug: Organization(
                id=organization_id,
                name=normalized,
                slug=slug,
                created_at=now,
                updated_at=now,
            ),
        )

        await self._memberships.add_org_membership(
            OrgMembership(
                id=new_id(ResourceKind.MEMBERSHIP),
                organization_id=organization.id,
                user_id=user_id,
                role=OrgRole.OWNER,
                created_at=now,
                updated_at=now,
            )
        )
        workspace = await self.create_workspace(
            actor_id=user_id,
            organization_id=organization.id,
            name=DEFAULT_WORKSPACE_NAME,
        )
        log.info(
            "organization_created",
            organization_id=organization.id,
            user_id=user_id,
            workspace_id=workspace.id,
        )
        return TenantAccess(
            user_id=user_id,
            organization_id=organization.id,
            org_role=OrgRole.OWNER,
            workspace_id=workspace.id,
            workspace_role=WorkspaceRole.ADMIN,
        )

    async def _add_with_unique_slug(
        self,
        *,
        base_name: str,
        build: Callable[[str], Organization],
    ) -> Organization:
        """Thử slug gốc trước, trùng thì thêm hậu tố ngẫu nhiên rồi thử lại."""
        base_slug = slugify(base_name, fallback="org")

        for attempt in range(_SLUG_ATTEMPTS):
            slug = base_slug if attempt == 0 else f"{base_slug}-{secrets.token_hex(3)}"
            organization = build(slug)
            try:
                await self._organizations.add(organization)
            except SlugAlreadyTakenError:
                continue
            return organization

        raise ValidationError("Không sinh được định danh duy nhất cho organization. Hãy đổi tên.")

    async def update_organization(
        self,
        *,
        actor_id: str,
        organization_id: str,
        name: str,
        slug: str | None = None,
    ) -> Organization:
        """Đổi tên hiển thị, và đổi cả slug nếu còn đang trong bước thiết lập."""
        access = await self.require_access(user_id=actor_id, organization_id=organization_id)
        if not can_manage_organization(access.org_role):
            raise AuthorizationError("Bạn không có quyền sửa organization này.")

        organization = await self._require_organization(organization_id)
        normalized_name = normalize_name(name, field="Tên organization")

        normalized_slug: str | None = None
        if slug is not None:
            ensure_slug_editable(organization)
            normalized_slug = normalize_slug(slug)

        try:
            await self._organizations.update_profile(
                organization_id,
                name=normalized_name,
                slug=normalized_slug,
                at=datetime.now(tz=UTC),
            )
        except SlugAlreadyTakenError as exc:
            raise ValidationError("Địa chỉ này đã có người dùng. Hãy chọn địa chỉ khác.") from exc

        log.info(
            "organization_updated",
            organization_id=organization_id,
            slug_changed=normalized_slug is not None,
        )
        return await self._require_organization(organization_id)

    async def is_slug_available(self, slug: str) -> bool:
        """Địa chỉ này còn trống không."""
        return await self._organizations.get_by_slug(normalize_slug(slug)) is None

    async def complete_onboarding(self, *, actor_id: str, organization_id: str) -> Organization:
        """Chốt bước thiết lập. Sau đây slug đóng băng."""
        access = await self.require_access(user_id=actor_id, organization_id=organization_id)
        if not can_manage_organization(access.org_role):
            raise AuthorizationError("Bạn không có quyền hoàn tất thiết lập organization này.")

        organization = await self._require_organization(organization_id)
        if organization.is_onboarded:
            return organization

        await self._organizations.mark_onboarded(organization_id, at=datetime.now(tz=UTC))
        log.info("organization_onboarded", organization_id=organization_id)
        return await self._require_organization(organization_id)

    async def list_organizations_of_user(self, user_id: str) -> list[tuple[Organization, OrgRole]]:
        result: list[tuple[Organization, OrgRole]] = []
        for membership in await self._memberships.list_organizations_of_user(user_id):
            organization = await self._organizations.get_by_id(membership.organization_id)
            if organization is not None:
                result.append((organization, membership.role))
        return result

    # ─── Workspace ───────────────────────────────────────────────────────────

    async def create_workspace(
        self, *, actor_id: str, organization_id: str, name: str, slug: str | None = None
    ) -> Workspace:
        """Tạo workspace. Bỏ trống ``slug`` thì địa chỉ tự sinh từ tên."""
        access = await self.require_access(user_id=actor_id, organization_id=organization_id)
        if not can_manage_organization(access.org_role):
            raise AuthorizationError("Bạn không có quyền tạo workspace trong organization này.")

        normalized = normalize_name(name, field="Tên workspace")
        now = datetime.now(tz=UTC)
        existing = {w.slug for w in await self._workspaces.list_for_organization(organization_id)}

        if slug is not None:
            chosen = normalize_slug(slug)
            if chosen in existing:
                raise ValidationError("Organization này đã có workspace dùng địa chỉ đó.")
            slug = chosen
        else:
            base_slug = slugify(normalized, fallback="workspace")
            slug = base_slug
            while slug in existing:
                slug = f"{base_slug}-{secrets.token_hex(3)}"

        workspace = Workspace(
            id=new_id(ResourceKind.WORKSPACE),
            organization_id=organization_id,
            name=normalized,
            slug=slug,
            created_at=now,
            updated_at=now,
        )
        await self._workspaces.add(workspace)
        log.info("workspace_created", workspace_id=workspace.id, organization_id=organization_id)
        return workspace

    async def list_workspaces(self, *, actor_id: str, organization_id: str) -> list[Workspace]:
        """Workspace mà ``actor_id`` thực sự nhìn thấy."""
        access = await self.require_access(user_id=actor_id, organization_id=organization_id)
        workspaces = await self._workspaces.list_for_organization(organization_id)

        if can_manage_organization(access.org_role):
            return workspaces

        visible: list[Workspace] = []
        for workspace in workspaces:
            membership = await self._memberships.get_workspace_membership(
                workspace_id=workspace.id, user_id=actor_id
            )
            if membership is not None:
                visible.append(workspace)
        return visible

    async def rename_workspace(
        self,
        *,
        actor_id: str,
        workspace_id: str,
        name: str,
        slug: str | None = None,
    ) -> Workspace:
        """Đổi tên workspace, và địa chỉ của nó nếu ``slug`` được truyền.

        Quyền thuộc về **ADMIN của chính workspace đó**, không phải chỉ quản trị
        organization — người phụ trách một workspace thì đặt được tên cho nó.
        OWNER và ADMIN của organization tự động có quyền ADMIN ở mọi workspace
        (xem ``resolve_workspace_role``) nên vẫn làm được.

        Khác organization ở chỗ slug không đóng băng: địa chỉ workspace chỉ duy
        nhất trong phạm vi một organization và không phải danh tính công khai
        của khách hàng. Đổi nó vẫn phá link cũ, nên đây là hành động tường minh
        chứ không tự chạy theo tên.
        """
        workspace = await self._require_workspace(workspace_id)
        access = await self.require_access(
            user_id=actor_id,
            organization_id=workspace.organization_id,
            workspace_id=workspace_id,
        )
        if access.workspace_role is not WorkspaceRole.ADMIN:
            raise AuthorizationError("Bạn không có quyền đổi tên workspace này.")

        normalized_name = normalize_name(name, field="Tên workspace")
        normalized_slug = normalize_slug(slug) if slug is not None else None

        try:
            await self._workspaces.update_profile(
                workspace_id,
                name=normalized_name,
                slug=normalized_slug,
                at=datetime.now(tz=UTC),
            )
        except SlugAlreadyTakenError as exc:
            raise ValidationError("Organization này đã có workspace dùng địa chỉ đó.") from exc

        log.info(
            "workspace_renamed",
            workspace_id=workspace_id,
            slug_changed=normalized_slug is not None,
        )
        return await self._require_workspace(workspace_id)

    async def delete_workspace(self, *, actor_id: str, workspace_id: str) -> None:
        workspace = await self._require_workspace(workspace_id)
        access = await self.require_access(
            user_id=actor_id, organization_id=workspace.organization_id
        )
        if not can_manage_organization(access.org_role):
            raise AuthorizationError("Bạn không có quyền xoá workspace này.")

        ensure_not_last_workspace(
            workspace_count=len(
                await self._workspaces.list_for_organization(workspace.organization_id)
            )
        )

        await self._workspaces.delete(workspace_id)
        log.info("workspace_deleted", workspace_id=workspace_id, actor_id=actor_id)

    # ─── Thành viên organization ─────────────────────────────────────────────

    async def add_member(
        self, *, actor_id: str, organization_id: str, email: str, role: OrgRole
    ) -> MemberView:
        """Thêm một người **đã có tài khoản** vào organization, theo email."""
        actor = await self.require_access(user_id=actor_id, organization_id=organization_id)
        ensure_can_assign_org_role(
            actor_role=actor.org_role,
            target_current_role=None,
            new_role=role,
        )

        profile = await self._users.find_by_email(email)
        if profile is None:
            # `reason` để giao diện phân biệt được thất bại này với những lỗi
            # 422 khác của cùng endpoint. Nó là ngõ cụt hay gặp nhất của luồng
            # thêm thành viên, và màn hình phải chỉ đường ra (gửi link đăng ký)
            # chứ không chỉ in lại câu này — bắt theo nội dung thông điệp thì
            # đổi một chữ là hỏng.
            raise ValidationError(
                "Không tìm thấy tài khoản nào dùng email này. "
                "Người được thêm phải đăng ký trước đã.",
                details={"reason": "no_account"},
            )

        now = datetime.now(tz=UTC)
        membership = OrgMembership(
            id=new_id(ResourceKind.MEMBERSHIP),
            organization_id=organization_id,
            user_id=profile.user_id,
            role=role,
            created_at=now,
            updated_at=now,
        )
        try:
            await self._memberships.add_org_membership(membership)
        except AlreadyMemberError as exc:
            raise ValidationError(
                "Người dùng này đã là thành viên.",
                details={"reason": "already_member"},
                cause=exc,
            ) from exc

        log.info(
            "org_member_added",
            organization_id=organization_id,
            user_id=profile.user_id,
            role=str(role),
        )
        # Người vừa được thêm chưa có quyền workspace nào — quyền đó cấp riêng.
        return MemberView(membership=membership, profile=profile, workspaces=[])

    async def change_member_role(
        self, *, actor_id: str, organization_id: str, user_id: str, role: OrgRole
    ) -> None:
        actor = await self.require_access(user_id=actor_id, organization_id=organization_id)
        target = await self._require_membership(organization_id, user_id)

        ensure_can_assign_org_role(
            actor_role=actor.org_role,
            target_current_role=target.role,
            new_role=role,
        )
        if role is not OrgRole.OWNER:
            ensure_not_last_owner(
                owner_count=await self._memberships.count_owners(organization_id),
                target_role=target.role,
            )

        await self._memberships.set_org_role(
            organization_id=organization_id,
            user_id=user_id,
            role=role,
            at=datetime.now(tz=UTC),
        )
        log.info(
            "org_member_role_changed",
            organization_id=organization_id,
            user_id=user_id,
            role=str(role),
        )

    async def remove_member(self, *, actor_id: str, organization_id: str, user_id: str) -> None:
        actor = await self.require_access(user_id=actor_id, organization_id=organization_id)
        target = await self._require_membership(organization_id, user_id)

        # Tự rời organization luôn được phép — trừ khi là OWNER cuối cùng.
        if actor_id != user_id:
            ensure_can_assign_org_role(
                actor_role=actor.org_role,
                target_current_role=target.role,
                new_role=target.role,
            )
        ensure_not_last_owner(
            owner_count=await self._memberships.count_owners(organization_id),
            target_role=target.role,
        )

        await self._memberships.remove_org_membership(
            organization_id=organization_id, user_id=user_id
        )
        log.info("org_member_removed", organization_id=organization_id, user_id=user_id)

    async def list_members(self, *, actor_id: str, organization_id: str) -> list[MemberView]:
        """Thành viên của organization, kèm hồ sơ và quyền workspace."""
        await self.require_access(user_id=actor_id, organization_id=organization_id)
        memberships = await self._memberships.list_org_memberships(organization_id)

        visible = await self.list_workspaces(actor_id=actor_id, organization_id=organization_id)
        by_workspace = {w.id: w for w in visible}
        access = await self._memberships.list_workspace_memberships_in(list(by_workspace))

        granted: dict[str, list[WorkspaceAccess]] = {}
        for membership in access:
            workspace = by_workspace[membership.workspace_id]
            granted.setdefault(membership.user_id, []).append(
                WorkspaceAccess(
                    workspace_id=workspace.id,
                    name=workspace.name,
                    slug=workspace.slug,
                    role=membership.role,
                )
            )

        profiles = await self._users.get_profiles([m.user_id for m in memberships])
        return [
            MemberView(
                membership=m,
                profile=profiles.get(m.user_id),
                workspaces=granted.get(m.user_id, []),
            )
            for m in memberships
        ]

    # ─── Thành viên workspace ────────────────────────────────────────────────

    async def list_workspace_members(
        self, *, actor_id: str, workspace_id: str
    ) -> list[WorkspaceMemberView]:
        """Những người được cấp quyền tường minh trên một workspace."""
        workspace = await self._require_workspace(workspace_id)
        await self.require_access(
            user_id=actor_id,
            organization_id=workspace.organization_id,
            workspace_id=workspace_id,
        )

        memberships = await self._memberships.list_workspace_memberships(workspace_id)
        profiles = await self._users.get_profiles([m.user_id for m in memberships])
        return [
            WorkspaceMemberView(membership=m, profile=profiles.get(m.user_id)) for m in memberships
        ]

    # ─── Thành viên workspace ────────────────────────────────────────────────

    async def set_workspace_member(
        self, *, actor_id: str, workspace_id: str, user_id: str, role: WorkspaceRole
    ) -> None:
        """Cấp hoặc đổi quyền của một user trên một workspace."""
        workspace = await self._require_workspace(workspace_id)
        actor = await self.require_access(
            user_id=actor_id, organization_id=workspace.organization_id, workspace_id=workspace_id
        )
        if actor.workspace_role is not WorkspaceRole.ADMIN:
            raise AuthorizationError("Bạn không có quyền quản lý thành viên của workspace này.")

        # Phải là thành viên organization trước đã — nếu không, quyền workspace
        # sẽ trỏ tới một người ngoài tenant.
        if (
            await self._memberships.get_org_membership(
                organization_id=workspace.organization_id, user_id=user_id
            )
            is None
        ):
            raise ValidationError("Người dùng này chưa phải thành viên của organization.")

        now = datetime.now(tz=UTC)
        existing = await self._memberships.get_workspace_membership(
            workspace_id=workspace_id, user_id=user_id
        )
        if existing is None:
            await self._memberships.add_workspace_membership(
                WorkspaceMembership(
                    id=new_id(ResourceKind.MEMBERSHIP),
                    workspace_id=workspace_id,
                    user_id=user_id,
                    role=role,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            await self._memberships.set_workspace_role(
                workspace_id=workspace_id, user_id=user_id, role=role, at=now
            )
        log.info(
            "workspace_member_set",
            workspace_id=workspace_id,
            user_id=user_id,
            role=str(role),
        )

    async def remove_workspace_member(
        self, *, actor_id: str, workspace_id: str, user_id: str
    ) -> None:
        workspace = await self._require_workspace(workspace_id)
        actor = await self.require_access(
            user_id=actor_id, organization_id=workspace.organization_id, workspace_id=workspace_id
        )
        if actor.workspace_role is not WorkspaceRole.ADMIN:
            raise AuthorizationError("Bạn không có quyền quản lý thành viên của workspace này.")

        await self._memberships.remove_workspace_membership(
            workspace_id=workspace_id, user_id=user_id
        )

    # ─── Phân giải quyền ─────────────────────────────────────────────────────

    async def require_access(
        self,
        *,
        user_id: str,
        organization_id: str,
        workspace_id: str | None = None,
    ) -> TenantAccess:
        """Xác minh user thuộc tenant, trả về quyền thực tế."""
        membership = await self._memberships.get_org_membership(
            organization_id=organization_id, user_id=user_id
        )
        if membership is None:
            raise AuthorizationError(_NOT_A_MEMBER)

        if workspace_id is None:
            return TenantAccess(
                user_id=user_id,
                organization_id=organization_id,
                org_role=membership.role,
            )

        workspace = await self._require_workspace(workspace_id)
        if workspace.organization_id != organization_id:
            # Workspace của tenant khác — đối xử y như không tồn tại.
            raise AuthorizationError(_NOT_A_MEMBER)

        workspace_membership = await self._memberships.get_workspace_membership(
            workspace_id=workspace_id, user_id=user_id
        )
        effective = resolve_workspace_role(
            membership.role,
            workspace_membership.role if workspace_membership else None,
        )
        if effective is None:
            raise AuthorizationError("Bạn không có quyền truy cập workspace này.")

        return TenantAccess(
            user_id=user_id,
            organization_id=organization_id,
            org_role=membership.role,
            workspace_id=workspace_id,
            workspace_role=effective,
        )

    async def resolve_default_access(self, user_id: str) -> TenantAccess | None:
        """Tenant mặc định của user khi request không chỉ định organization."""
        memberships = await self._memberships.list_organizations_of_user(user_id)
        if not memberships:
            return None

        membership = memberships[0]
        return TenantAccess(
            user_id=user_id,
            organization_id=membership.organization_id,
            org_role=membership.role,
        )

    # ─── Nội bộ ──────────────────────────────────────────────────────────────

    async def _require_organization(self, organization_id: str) -> Organization:
        organization = await self._organizations.get_by_id(organization_id)
        if organization is None:
            # Người gọi đã qua `require_access`, nên tới đây mà không thấy
            # organization là dữ liệu không nhất quán, không phải lỗi phân quyền.
            raise ValidationError("Organization không tồn tại.")
        return organization

    async def _require_workspace(self, workspace_id: str) -> Workspace:
        workspace = await self._workspaces.get_by_id(workspace_id)
        if workspace is None:
            raise AuthorizationError(_NOT_A_MEMBER)
        return workspace

    async def _require_membership(self, organization_id: str, user_id: str) -> OrgMembership:
        membership = await self._memberships.get_org_membership(
            organization_id=organization_id, user_id=user_id
        )
        if membership is None:
            raise ValidationError("Người dùng này không phải thành viên của organization.")
        return membership
