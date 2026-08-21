"""Thực thể và quy tắc nghiệp vụ của Tenant."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.core.errors import AuthorizationError, ValidationError


class OrgRole(StrEnum):
    """Vai trò trong organization.

    OWNER  — toàn quyền, kể cả cấp/thu hồi quyền OWNER.
    ADMIN  — quản lý workspace và thành viên thường; không đụng tới OWNER/ADMIN.
    MEMBER — chỉ tham gia workspace được cấp quyền tường minh.
    """

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class WorkspaceRole(StrEnum):
    """Vai trò trong một workspace cụ thể."""

    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


#: Vai trò được phép quản trị organization.
ORG_MANAGERS: frozenset[OrgRole] = frozenset({OrgRole.OWNER, OrgRole.ADMIN})


# ─────────────────────────────────────────────────────────────────────────────
# Thực thể
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Organization:
    id: str
    name: str
    slug: str
    created_at: datetime
    updated_at: datetime
    onboarded_at: datetime | None = None

    @property
    def is_onboarded(self) -> bool:
        return self.onboarded_at is not None


@dataclass(frozen=True, slots=True)
class Workspace:
    id: str
    organization_id: str
    name: str
    slug: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class OrgMembership:
    """Liên kết một user với một organization."""

    id: str
    organization_id: str
    user_id: str
    role: OrgRole
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class WorkspaceMembership:
    id: str
    workspace_id: str
    user_id: str
    role: WorkspaceRole
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class TenantAccess:
    """Quyền thực tế của một user trên một tenant, đã tính cả hai tầng vai trò."""

    user_id: str
    organization_id: str
    org_role: OrgRole
    workspace_id: str | None = None
    workspace_role: WorkspaceRole | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Tên và slug
# ─────────────────────────────────────────────────────────────────────────────

MAX_NAME_LENGTH = 200
MAX_SLUG_LENGTH = 60

_NON_SLUG_CHARS = re.compile(r"[^a-z0-9]+")

_STROKED_LETTERS = str.maketrans({"đ": "d", "Đ": "D"})


def _strip_diacritics(raw: str) -> str:
    """Bóc dấu về ASCII: "Công ty Cổ phần" -> "Cong ty Co phan"."""
    decomposed = unicodedata.normalize("NFD", raw.translate(_STROKED_LETTERS))
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def normalize_name(raw: str, *, field: str = "Tên") -> str:
    """Gộp khoảng trắng thừa và kiểm tra độ dài."""
    name = " ".join(raw.split())
    if not name:
        raise ValidationError(f"{field} không được để trống.")
    if len(name) > MAX_NAME_LENGTH:
        raise ValidationError(f"{field} không được dài quá {MAX_NAME_LENGTH} ký tự.")
    return name


def slugify(raw: str, *, fallback: str) -> str:
    """Sinh slug từ tên."""
    ascii_only = _strip_diacritics(raw).lower()
    slug = _NON_SLUG_CHARS.sub("-", ascii_only).strip("-")[:MAX_SLUG_LENGTH].strip("-")
    return slug or fallback


MIN_SLUG_LENGTH = 3

_VALID_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: Slug bị cấm vì trùng với đường dẫn của chính hệ thống.
RESERVED_SLUGS: frozenset[str] = frozenset(
    {
        "admin",
        "agent",
        "api",
        "app",
        "approvals",
        "assets",
        "auth",
        "billing",
        "connections",
        "docs",
        "health",
        "healthz",
        "help",
        "login",
        "logout",
        "me",
        "new",
        "organizations",
        "readyz",
        "register",
        "runs",
        "settings",
        "signup",
        "static",
        "status",
        "support",
        "v1",
        "workflows",
        "workspaces",
        "www",
    }
)


def normalize_slug(raw: str) -> str:
    """Chuẩn hoá và kiểm tra một slug **do người dùng tự gõ**."""
    slug = raw.strip().lower()

    if not slug:
        raise ValidationError("Địa chỉ không được để trống.")
    if len(slug) < MIN_SLUG_LENGTH:
        raise ValidationError(f"Địa chỉ phải dài ít nhất {MIN_SLUG_LENGTH} ký tự.")
    if len(slug) > MAX_SLUG_LENGTH:
        raise ValidationError(f"Địa chỉ không được dài quá {MAX_SLUG_LENGTH} ký tự.")
    if not _VALID_SLUG.match(slug):
        raise ValidationError(
            "Địa chỉ chỉ được gồm chữ thường không dấu, số và dấu gạch nối ở giữa."
        )
    if slug in RESERVED_SLUGS:
        raise ValidationError("Địa chỉ này được hệ thống giữ chỗ. Hãy chọn địa chỉ khác.")
    return slug


def ensure_slug_editable(organization: Organization) -> None:
    """Chặn đổi slug sau khi organization đã hoàn tất thiết lập."""
    if organization.is_onboarded:
        raise ValidationError(
            "Địa chỉ đã được chốt khi kết thúc bước thiết lập và không đổi được nữa. "
            "Tên hiển thị thì vẫn đổi được bất cứ lúc nào."
        )


DEFAULT_WORKSPACE_NAME = "Default"


def default_organization_name(display_name: str) -> str:
    """Tên organization cấp tự động ngay khi một user đăng ký."""
    return normalize_name(f"{display_name}'s Organization"[:MAX_NAME_LENGTH])


# ─────────────────────────────────────────────────────────────────────────────
# Quy tắc phân quyền
# ─────────────────────────────────────────────────────────────────────────────


def can_manage_organization(role: OrgRole) -> bool:
    """Đổi tên organization, tạo hoặc xoá workspace."""
    return role in ORG_MANAGERS


def can_manage_members(role: OrgRole) -> bool:
    """Thêm, xoá, đổi vai trò của thành viên."""
    return role in ORG_MANAGERS


def resolve_workspace_role(
    org_role: OrgRole,
    workspace_role: WorkspaceRole | None,
) -> WorkspaceRole | None:
    """Quyền thực tế của một user trên một workspace."""
    if org_role in ORG_MANAGERS:
        return WorkspaceRole.ADMIN
    return workspace_role


def ensure_can_assign_org_role(
    *,
    actor_role: OrgRole,
    target_current_role: OrgRole | None,
    new_role: OrgRole,
) -> None:
    """``actor_role`` có được phép đặt vai trò ``new_role`` cho người kia không."""
    if not can_manage_members(actor_role):
        raise AuthorizationError("Bạn không có quyền quản lý thành viên của organization này.")

    if actor_role is OrgRole.OWNER:
        return

    if new_role is OrgRole.OWNER:
        raise AuthorizationError("Chỉ OWNER mới được cấp quyền OWNER.")

    if target_current_role in ORG_MANAGERS:
        raise AuthorizationError("Chỉ OWNER mới được thay đổi vai trò của OWNER hoặc ADMIN.")


def ensure_not_last_owner(*, owner_count: int, target_role: OrgRole) -> None:
    """Chặn thao tác làm organization không còn OWNER nào."""
    if target_role is OrgRole.OWNER and owner_count <= 1:
        raise ValidationError(
            "Đây là OWNER cuối cùng của organization. Hãy cấp quyền OWNER cho "
            "người khác trước khi thực hiện thao tác này."
        )
