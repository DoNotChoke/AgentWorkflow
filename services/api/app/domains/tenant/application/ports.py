"""Interface mà tầng `infrastructure` của Tenant phải hiện thực hoá."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domains.tenant.domain.entities import (
    Organization,
    OrgMembership,
    OrgRole,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
)


class OrganizationRepository(Protocol):
    async def add(self, organization: Organization) -> None:
        """Thêm organization mới. Ném ``SlugAlreadyTakenError`` nếu slug trùng."""

    async def get_by_id(self, organization_id: str) -> Organization | None: ...

    async def get_by_slug(self, slug: str) -> Organization | None:
        """Dùng để kiểm một địa chỉ còn trống hay không trước khi người dùng gửi."""

    async def update_profile(
        self, organization_id: str, *, name: str, slug: str | None, at: datetime
    ) -> None:
        """Đổi tên hiển thị, và slug nếu ``slug`` khác ``None``."""

    async def mark_onboarded(self, organization_id: str, *, at: datetime) -> None:
        """Chốt bước thiết lập. Sau lời gọi này slug không đổi được nữa."""


class WorkspaceRepository(Protocol):
    async def add(self, workspace: Workspace) -> None:
        """Thêm workspace mới. Slug chỉ cần duy nhất trong phạm vi organization."""

    async def get_by_id(self, workspace_id: str) -> Workspace | None: ...

    async def list_for_organization(self, organization_id: str) -> list[Workspace]: ...

    async def update_profile(
        self, workspace_id: str, *, name: str, slug: str | None, at: datetime
    ) -> None:
        """Đổi tên hiển thị, và slug nếu ``slug`` khác ``None``.

        Ném ``SlugAlreadyTakenError`` khi organization đã có workspace khác dùng
        slug đó. Slug workspace chỉ cần duy nhất trong phạm vi organization.
        """

    async def delete(self, workspace_id: str) -> None: ...


class MembershipRepository(Protocol):
    """Cả hai tầng membership nằm chung một port."""

    # ─── Organization ────────────────────────────────────────────────────────

    async def add_org_membership(self, membership: OrgMembership) -> None:
        """Ném ``AlreadyMemberError`` nếu user đã thuộc organization đó."""

    async def get_org_membership(
        self, *, organization_id: str, user_id: str
    ) -> OrgMembership | None: ...

    async def list_org_memberships(self, organization_id: str) -> list[OrgMembership]: ...

    async def list_organizations_of_user(self, user_id: str) -> list[OrgMembership]: ...

    async def count_owners(self, organization_id: str) -> int:
        """Số OWNER hiện tại — dữ liệu cho bất biến "không bao giờ hết OWNER"."""

    async def set_org_role(
        self, *, organization_id: str, user_id: str, role: OrgRole, at: datetime
    ) -> None: ...

    async def remove_org_membership(self, *, organization_id: str, user_id: str) -> None:
        """Xoá luôn mọi workspace membership của user trong organization đó."""

    # ─── Workspace ───────────────────────────────────────────────────────────

    async def add_workspace_membership(self, membership: WorkspaceMembership) -> None: ...

    async def get_workspace_membership(
        self, *, workspace_id: str, user_id: str
    ) -> WorkspaceMembership | None: ...

    async def list_workspace_memberships(self, workspace_id: str) -> list[WorkspaceMembership]: ...

    async def list_workspace_memberships_in(
        self, workspace_ids: Sequence[str]
    ) -> list[WorkspaceMembership]:
        """Quyền workspace của nhiều workspace một lượt."""

    async def set_workspace_role(
        self, *, workspace_id: str, user_id: str, role: WorkspaceRole, at: datetime
    ) -> None: ...

    async def remove_workspace_membership(self, *, workspace_id: str, user_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class UserProfile:
    """Hồ sơ người dùng, ở đúng hình dạng Tenant cần để hiển thị thành viên."""

    user_id: str
    email: str
    display_name: str


class UserDirectory(Protocol):
    """Nguồn tra hồ sơ người dùng cho danh sách thành viên."""

    async def get_profiles(self, user_ids: Sequence[str]) -> dict[str, UserProfile]:
        """Tra theo lô, trả về map ``user_id -> hồ sơ``."""

    async def find_by_email(self, email: str) -> UserProfile | None:
        """Tìm người dùng theo email, để thêm họ vào organization."""


class SlugAlreadyTakenError(Exception):
    """Repository báo lên rằng ràng buộc unique của slug bị vi phạm."""


class AlreadyMemberError(Exception):
    """Repository báo lên rằng user đã là thành viên."""
