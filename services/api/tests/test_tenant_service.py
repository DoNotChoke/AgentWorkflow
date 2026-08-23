"""Use case của Tenant, chạy trên repository trong bộ nhớ.

Không cần Docker. Những gì kiểm ở đây mà `test_tenant_domain.py` không kiểm
được: các bất biến phải đọc nhiều bản ghi cùng lúc — đếm OWNER, lọc workspace
theo quyền, dọn quyền workspace mồ côi khi một người rời organization.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import pytest

from app.core.errors import AuthorizationError, ValidationError
from app.domains.tenant.application.ports import (
    AlreadyMemberError,
    SlugAlreadyTakenError,
    UserProfile,
)
from app.domains.tenant.application.service import TenantService
from app.domains.tenant.domain.entities import (
    Organization,
    OrgMembership,
    OrgRole,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
)


class FakeOrganizations:
    def __init__(self) -> None:
        self.rows: dict[str, Organization] = {}

    async def add(self, organization: Organization) -> None:
        if any(o.slug == organization.slug for o in self.rows.values()):
            raise SlugAlreadyTakenError(organization.slug)
        self.rows[organization.id] = organization

    async def get_by_id(self, organization_id: str) -> Organization | None:
        return self.rows.get(organization_id)

    async def get_by_slug(self, slug: str) -> Organization | None:
        return next((o for o in self.rows.values() if o.slug == slug), None)

    async def update_profile(
        self, organization_id: str, *, name: str, slug: str | None, at: datetime
    ) -> None:
        row = self.rows[organization_id]
        if slug is not None and any(
            o.slug == slug and o.id != organization_id for o in self.rows.values()
        ):
            raise SlugAlreadyTakenError(slug)
        self.rows[organization_id] = Organization(
            id=row.id,
            name=name,
            slug=slug if slug is not None else row.slug,
            created_at=row.created_at,
            updated_at=at,
            onboarded_at=row.onboarded_at,
        )

    async def mark_onboarded(self, organization_id: str, *, at: datetime) -> None:
        row = self.rows[organization_id]
        self.rows[organization_id] = Organization(
            id=row.id,
            name=row.name,
            slug=row.slug,
            created_at=row.created_at,
            updated_at=at,
            onboarded_at=at,
        )


class FakeWorkspaces:
    def __init__(self) -> None:
        self.rows: dict[str, Workspace] = {}

    async def add(self, workspace: Workspace) -> None:
        self.rows[workspace.id] = workspace

    async def get_by_id(self, workspace_id: str) -> Workspace | None:
        return self.rows.get(workspace_id)

    async def list_for_organization(self, organization_id: str) -> list[Workspace]:
        return [w for w in self.rows.values() if w.organization_id == organization_id]

    async def update_profile(
        self, workspace_id: str, *, name: str, slug: str | None, at: datetime
    ) -> None:
        row = self.rows[workspace_id]
        if slug is not None and any(
            w.slug == slug and w.organization_id == row.organization_id and w.id != workspace_id
            for w in self.rows.values()
        ):
            raise SlugAlreadyTakenError(slug)
        self.rows[workspace_id] = Workspace(
            id=row.id,
            organization_id=row.organization_id,
            name=name,
            slug=slug if slug is not None else row.slug,
            created_at=row.created_at,
            updated_at=at,
        )

    async def delete(self, workspace_id: str) -> None:
        self.rows.pop(workspace_id, None)


class FakeMemberships:
    def __init__(self, workspaces: FakeWorkspaces) -> None:
        self.org: dict[tuple[str, str], OrgMembership] = {}
        self.ws: dict[tuple[str, str], WorkspaceMembership] = {}
        self._workspaces = workspaces

    async def add_org_membership(self, membership: OrgMembership) -> None:
        key = (membership.organization_id, membership.user_id)
        if key in self.org:
            raise AlreadyMemberError(membership.user_id)
        self.org[key] = membership

    async def get_org_membership(
        self, *, organization_id: str, user_id: str
    ) -> OrgMembership | None:
        return self.org.get((organization_id, user_id))

    async def list_org_memberships(self, organization_id: str) -> list[OrgMembership]:
        return [m for m in self.org.values() if m.organization_id == organization_id]

    async def list_organizations_of_user(self, user_id: str) -> list[OrgMembership]:
        return [m for m in self.org.values() if m.user_id == user_id]

    async def count_owners(self, organization_id: str) -> int:
        return sum(
            1
            for m in self.org.values()
            if m.organization_id == organization_id and m.role is OrgRole.OWNER
        )

    async def set_org_role(
        self, *, organization_id: str, user_id: str, role: OrgRole, at: datetime
    ) -> None:
        row = self.org[(organization_id, user_id)]
        self.org[(organization_id, user_id)] = OrgMembership(
            id=row.id,
            organization_id=row.organization_id,
            user_id=row.user_id,
            role=role,
            created_at=row.created_at,
            updated_at=at,
        )

    async def remove_org_membership(self, *, organization_id: str, user_id: str) -> None:
        ws_ids = {
            w.id for w in self._workspaces.rows.values() if w.organization_id == organization_id
        }
        for key in [k for k in self.ws if k[0] in ws_ids and k[1] == user_id]:
            del self.ws[key]
        self.org.pop((organization_id, user_id), None)

    async def add_workspace_membership(self, membership: WorkspaceMembership) -> None:
        self.ws[(membership.workspace_id, membership.user_id)] = membership

    async def get_workspace_membership(
        self, *, workspace_id: str, user_id: str
    ) -> WorkspaceMembership | None:
        return self.ws.get((workspace_id, user_id))

    async def list_workspace_memberships(self, workspace_id: str) -> list[WorkspaceMembership]:
        return [m for m in self.ws.values() if m.workspace_id == workspace_id]

    async def list_workspace_memberships_in(
        self, workspace_ids: Sequence[str]
    ) -> list[WorkspaceMembership]:
        wanted = set(workspace_ids)
        return [m for m in self.ws.values() if m.workspace_id in wanted]

    async def set_workspace_role(
        self, *, workspace_id: str, user_id: str, role: WorkspaceRole, at: datetime
    ) -> None:
        row = self.ws[(workspace_id, user_id)]
        self.ws[(workspace_id, user_id)] = WorkspaceMembership(
            id=row.id,
            workspace_id=row.workspace_id,
            user_id=row.user_id,
            role=role,
            created_at=row.created_at,
            updated_at=at,
        )

    async def remove_workspace_membership(self, *, workspace_id: str, user_id: str) -> None:
        self.ws.pop((workspace_id, user_id), None)


class FakeUsers:
    """Danh bạ người dùng. Chỉ ai được nạp vào đây mới tra ra hồ sơ."""

    def __init__(self) -> None:
        self.rows: dict[str, UserProfile] = {}

    def add(self, user_id: str, *, email: str, display_name: str) -> None:
        self.rows[user_id] = UserProfile(user_id=user_id, email=email, display_name=display_name)

    async def get_profiles(self, user_ids: Sequence[str]) -> dict[str, UserProfile]:
        return {uid: self.rows[uid] for uid in user_ids if uid in self.rows}

    async def find_by_email(self, email: str) -> UserProfile | None:
        wanted = email.strip().lower()
        return next((p for p in self.rows.values() if p.email == wanted), None)


#: Email của từng nhân vật. `add_member` nhận email, nên danh bạ phải biết họ
#: trước — người chưa có tài khoản thì chưa thêm vào organization được.
EMAIL_OF = {
    "usr_chu": "chu@acme.com",
    "usr_pho": "pho@acme.com",
    "usr_nhan_vien": "nhanvien@acme.com",
    "usr_nguoi_la": "nguoila@example.com",
}


@pytest.fixture
def users() -> FakeUsers:
    directory = FakeUsers()
    for user_id, email in EMAIL_OF.items():
        directory.add(user_id, email=email, display_name=user_id)
    return directory


@pytest.fixture
def service(users: FakeUsers) -> TenantService:
    workspaces = FakeWorkspaces()
    return TenantService(
        organizations=FakeOrganizations(),
        workspaces=workspaces,
        memberships=FakeMemberships(workspaces),
        users=users,
    )


CHU = "usr_chu"
PHO = "usr_pho"
NHAN_VIEN = "usr_nhan_vien"
NGUOI_LA = "usr_nguoi_la"


# ─── Cấp phát khi đăng ký ────────────────────────────────────────────────────


async def test_dang_ky_duoc_cap_org_va_workspace(service: TenantService) -> None:
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")

    assert access.org_role is OrgRole.OWNER
    assert access.workspace_id is not None
    assert access.workspace_role is WorkspaceRole.ADMIN


async def test_org_tao_them_cung_co_workspace_mac_dinh(service: TenantService) -> None:
    """Org rỗng không dùng được vào việc gì, nên không có đường nào tạo ra org rỗng."""
    await service.provision_for_new_user(user_id=CHU, display_name="An")
    them = await service.create_organization(user_id=CHU, name="Acme")

    workspaces = await service.list_workspaces(actor_id=CHU, organization_id=them.organization_id)

    assert [w.name for w in workspaces] == ["Default"]
    assert them.workspace_id == workspaces[0].id
    assert them.workspace_role is WorkspaceRole.ADMIN


async def test_hai_user_trung_ten_van_cap_duoc_org(service: TenantService) -> None:
    """Slug organization duy nhất toàn hệ thống, mà tên hiển thị thì hay trùng."""
    a = await service.provision_for_new_user(user_id=CHU, display_name="An")
    b = await service.provision_for_new_user(user_id=PHO, display_name="An")

    assert a.organization_id != b.organization_id


# ─── Workspace ───────────────────────────────────────────────────────────────


async def test_member_khong_tao_duoc_workspace(service: TenantService) -> None:
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")
    await service.add_member(
        actor_id=CHU,
        organization_id=access.organization_id,
        email=EMAIL_OF[NHAN_VIEN],
        role=OrgRole.MEMBER,
    )

    with pytest.raises(AuthorizationError):
        await service.create_workspace(
            actor_id=NHAN_VIEN, organization_id=access.organization_id, name="Lam lut"
        )


async def test_member_chi_thay_workspace_duoc_cap(service: TenantService) -> None:
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")
    org = access.organization_id
    marketing = await service.create_workspace(actor_id=CHU, organization_id=org, name="Marketing")
    await service.create_workspace(actor_id=CHU, organization_id=org, name="Engineering")

    await service.add_member(
        actor_id=CHU, organization_id=org, email=EMAIL_OF[NHAN_VIEN], role=OrgRole.MEMBER
    )
    await service.set_workspace_member(
        actor_id=CHU,
        workspace_id=marketing.id,
        user_id=NHAN_VIEN,
        role=WorkspaceRole.EDITOR,
    )

    cua_chu = await service.list_workspaces(actor_id=CHU, organization_id=org)
    cua_nhan_vien = await service.list_workspaces(actor_id=NHAN_VIEN, organization_id=org)

    assert len(cua_chu) == 3  # Default + Marketing + Engineering
    assert [w.id for w in cua_nhan_vien] == [marketing.id]


async def test_khong_xoa_duoc_workspace_cuoi_cung(service: TenantService) -> None:
    """Org không còn workspace nào là org không dùng được vào việc gì."""
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")
    org = access.organization_id
    mac_dinh = (await service.list_workspaces(actor_id=CHU, organization_id=org))[0]

    with pytest.raises(ValidationError):
        await service.delete_workspace(actor_id=CHU, workspace_id=mac_dinh.id)

    # Có cái thứ hai thì cái thứ nhất xoá được.
    await service.create_workspace(actor_id=CHU, organization_id=org, name="Marketing")
    await service.delete_workspace(actor_id=CHU, workspace_id=mac_dinh.id)

    assert [w.name for w in await service.list_workspaces(actor_id=CHU, organization_id=org)] == [
        "Marketing"
    ]


async def test_dia_chi_workspace_tu_chon_duoc_giu_nguyen(service: TenantService) -> None:
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")

    ws = await service.create_workspace(
        actor_id=CHU,
        organization_id=access.organization_id,
        name="Marketing",
        slug="mkt",
    )

    assert ws.slug == "mkt"


async def test_dia_chi_workspace_tu_chon_bi_trung_thi_bao_loi(service: TenantService) -> None:
    """Địa chỉ tự sinh thì né trùng bằng hậu tố; địa chỉ người dùng chọn thì không."""
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")
    org = access.organization_id
    await service.create_workspace(actor_id=CHU, organization_id=org, name="Marketing", slug="mkt")

    with pytest.raises(ValidationError):
        await service.create_workspace(
            actor_id=CHU, organization_id=org, name="Marketing lan hai", slug="mkt"
        )


async def test_workspace_cua_tenant_khac_bi_tu_choi(service: TenantService) -> None:
    """Ranh giới tenant — §19. Đây là loại rò rỉ nghiêm trọng nhất."""
    a = await service.provision_for_new_user(user_id=CHU, display_name="An")
    b = await service.provision_for_new_user(user_id=NGUOI_LA, display_name="La")
    cua_nguoi_la = (
        await service.list_workspaces(actor_id=NGUOI_LA, organization_id=b.organization_id)
    )[0]

    with pytest.raises(AuthorizationError):
        await service.require_access(
            user_id=CHU,
            organization_id=a.organization_id,
            workspace_id=cua_nguoi_la.id,
        )


# ─── Thành viên ──────────────────────────────────────────────────────────────


async def test_khong_them_duoc_thanh_vien_hai_lan(service: TenantService) -> None:
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")
    await service.add_member(
        actor_id=CHU,
        organization_id=access.organization_id,
        email=EMAIL_OF[NHAN_VIEN],
        role=OrgRole.MEMBER,
    )

    with pytest.raises(ValidationError):
        await service.add_member(
            actor_id=CHU,
            organization_id=access.organization_id,
            email=EMAIL_OF[NHAN_VIEN],
            role=OrgRole.ADMIN,
        )


async def test_nguoi_ngoai_khong_doc_duoc_danh_sach_thanh_vien(service: TenantService) -> None:
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")

    with pytest.raises(AuthorizationError):
        await service.list_members(actor_id=NGUOI_LA, organization_id=access.organization_id)


async def test_them_thanh_vien_bang_email(service: TenantService) -> None:
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")

    view = await service.add_member(
        actor_id=CHU,
        organization_id=access.organization_id,
        email=EMAIL_OF[NHAN_VIEN],
        role=OrgRole.MEMBER,
    )

    assert view.membership.user_id == NHAN_VIEN
    assert view.profile is not None
    assert view.profile.email == EMAIL_OF[NHAN_VIEN]
    # Vừa vào thì chưa có quyền workspace nào — quyền đó cấp riêng.
    assert view.workspaces == []


async def test_email_thua_khoang_trang_va_hoa_thuong_van_khop(service: TenantService) -> None:
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")

    view = await service.add_member(
        actor_id=CHU,
        organization_id=access.organization_id,
        email=f"  {EMAIL_OF[NHAN_VIEN].upper()}  ",
        role=OrgRole.MEMBER,
    )

    assert view.membership.user_id == NHAN_VIEN


async def test_email_chua_co_tai_khoan_thi_bao_loi(service: TenantService) -> None:
    """Lát này chưa mời được người chưa đăng ký — nhưng phải nói rõ lý do."""
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")

    with pytest.raises(ValidationError, match="đăng ký"):
        await service.add_member(
            actor_id=CHU,
            organization_id=access.organization_id,
            email="chua-ton-tai@example.com",
            role=OrgRole.MEMBER,
        )


async def test_khong_du_quyen_thi_khong_do_duoc_email_nao_ton_tai(
    service: TenantService,
) -> None:
    """Endpoint này không được biến thành máy dò tài khoản.

    Người không có quyền thêm thành viên phải nhận **cùng một lỗi phân quyền**
    cho cả email có thật lẫn email bịa ra. Nếu quyền được kiểm sau khi tra, hai
    trường hợp sẽ trả về hai lỗi khác nhau và đó chính là một máy dò.
    """
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")
    org = access.organization_id
    await service.add_member(
        actor_id=CHU, organization_id=org, email=EMAIL_OF[NHAN_VIEN], role=OrgRole.MEMBER
    )

    with pytest.raises(AuthorizationError):
        await service.add_member(
            actor_id=NHAN_VIEN, organization_id=org, email=EMAIL_OF[PHO], role=OrgRole.MEMBER
        )

    with pytest.raises(AuthorizationError):
        await service.add_member(
            actor_id=NHAN_VIEN,
            organization_id=org,
            email="khong-he-ton-tai@example.com",
            role=OrgRole.MEMBER,
        )


async def test_danh_sach_thanh_vien_kem_ho_so(service: TenantService, users: FakeUsers) -> None:
    """Tenant chỉ lưu user_id; tên và email tới từ danh bạ ngoài."""
    users.add(CHU, email=EMAIL_OF[CHU], display_name="An Hoang")
    users.add(NHAN_VIEN, email=EMAIL_OF[NHAN_VIEN], display_name="Chi Le")
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")
    await service.add_member(
        actor_id=CHU,
        organization_id=access.organization_id,
        email=EMAIL_OF[NHAN_VIEN],
        role=OrgRole.MEMBER,
    )

    members = await service.list_members(actor_id=CHU, organization_id=access.organization_id)

    assert {v.membership.user_id: v.profile.display_name for v in members if v.profile} == {
        CHU: "An Hoang",
        NHAN_VIEN: "Chi Le",
    }


async def test_thanh_vien_khong_tra_duoc_ho_so_van_hien_ra(
    service: TenantService, users: FakeUsers
) -> None:
    """Không đọc được tên một người thì vẫn phải thấy họ đang có quyền."""
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")
    await service.add_member(
        actor_id=CHU,
        organization_id=access.organization_id,
        email=EMAIL_OF[NHAN_VIEN],
        role=OrgRole.MEMBER,
    )
    # Tài khoản biến mất sau khi đã được thêm — membership ở lại, hồ sơ thì không.
    del users.rows[NHAN_VIEN]

    members = await service.list_members(actor_id=CHU, organization_id=access.organization_id)

    assert len(members) == 2
    assert [v.membership.user_id for v in members if v.profile is None] == [NHAN_VIEN]


async def test_danh_sach_thanh_vien_kem_quyen_workspace(
    service: TenantService, users: FakeUsers
) -> None:
    users.add(CHU, email=EMAIL_OF[CHU], display_name="An Hoang")
    users.add(NHAN_VIEN, email=EMAIL_OF[NHAN_VIEN], display_name="Chi Le")
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")
    org = access.organization_id
    marketing = await service.create_workspace(actor_id=CHU, organization_id=org, name="Marketing")
    await service.add_member(
        actor_id=CHU, organization_id=org, email=EMAIL_OF[NHAN_VIEN], role=OrgRole.MEMBER
    )
    await service.set_workspace_member(
        actor_id=CHU, workspace_id=marketing.id, user_id=NHAN_VIEN, role=WorkspaceRole.EDITOR
    )

    theo_id = {
        v.membership.user_id: v
        for v in await service.list_members(actor_id=CHU, organization_id=org)
    }

    assert [(w.name, w.role) for w in theo_id[NHAN_VIEN].workspaces] == [
        ("Marketing", WorkspaceRole.EDITOR)
    ]
    # OWNER với tới mọi workspace nhờ vai trò org, không qua bản ghi nào.
    assert theo_id[CHU].workspaces == []


async def test_member_khong_thay_workspace_ngoai_tam_qua_danh_sach_thanh_vien(
    service: TenantService, users: FakeUsers
) -> None:
    """Rò rỉ gián tiếp cũng là rò rỉ.

    Nếu quyền của người khác được liệt kê nguyên vẹn, một MEMBER sẽ biết được
    organization có những workspace nào mà chính họ không được vào.
    """
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")
    org = access.organization_id
    marketing = await service.create_workspace(actor_id=CHU, organization_id=org, name="Marketing")
    bi_mat = await service.create_workspace(actor_id=CHU, organization_id=org, name="Bi Mat")

    for nguoi in (NHAN_VIEN, PHO):
        await service.add_member(
            actor_id=CHU, organization_id=org, email=EMAIL_OF[nguoi], role=OrgRole.MEMBER
        )
    await service.set_workspace_member(
        actor_id=CHU, workspace_id=marketing.id, user_id=NHAN_VIEN, role=WorkspaceRole.EDITOR
    )
    # PHO có quyền ở cả hai, trong đó một cái NHAN_VIEN không được thấy.
    for ws in (marketing.id, bi_mat.id):
        await service.set_workspace_member(
            actor_id=CHU, workspace_id=ws, user_id=PHO, role=WorkspaceRole.VIEWER
        )

    theo_id = {
        v.membership.user_id: v
        for v in await service.list_members(actor_id=NHAN_VIEN, organization_id=org)
    }

    assert [w.name for w in theo_id[PHO].workspaces] == ["Marketing"]
    assert all(w.name != "Bi Mat" for v in theo_id.values() for w in v.workspaces)


async def test_thanh_vien_cua_mot_workspace(service: TenantService, users: FakeUsers) -> None:
    users.add(NHAN_VIEN, email=EMAIL_OF[NHAN_VIEN], display_name="Chi Le")
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")
    org = access.organization_id
    marketing = await service.create_workspace(actor_id=CHU, organization_id=org, name="Marketing")
    await service.add_member(
        actor_id=CHU, organization_id=org, email=EMAIL_OF[NHAN_VIEN], role=OrgRole.MEMBER
    )
    await service.set_workspace_member(
        actor_id=CHU, workspace_id=marketing.id, user_id=NHAN_VIEN, role=WorkspaceRole.EDITOR
    )

    members = await service.list_workspace_members(actor_id=CHU, workspace_id=marketing.id)

    # Chỉ người được cấp tường minh; CHU là OWNER nên không có bản ghi nào.
    assert [
        (v.membership.user_id, v.profile.display_name if v.profile else None) for v in members
    ] == [(NHAN_VIEN, "Chi Le")]


async def test_nguoi_ngoai_khong_doc_duoc_thanh_vien_workspace(service: TenantService) -> None:
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")
    org = access.organization_id
    marketing = await service.create_workspace(actor_id=CHU, organization_id=org, name="Marketing")
    await service.add_member(
        actor_id=CHU, organization_id=org, email=EMAIL_OF[NHAN_VIEN], role=OrgRole.MEMBER
    )

    # Là thành viên organization nhưng chưa được cấp quyền trên workspace này.
    with pytest.raises(AuthorizationError):
        await service.list_workspace_members(actor_id=NHAN_VIEN, workspace_id=marketing.id)

    with pytest.raises(AuthorizationError):
        await service.list_workspace_members(actor_id=NGUOI_LA, workspace_id=marketing.id)


async def test_owner_cuoi_cung_khong_the_tu_ha_cap(service: TenantService) -> None:
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")

    with pytest.raises(ValidationError, match="cuối cùng"):
        await service.change_member_role(
            actor_id=CHU,
            organization_id=access.organization_id,
            user_id=CHU,
            role=OrgRole.MEMBER,
        )


async def test_owner_cuoi_cung_khong_the_tu_roi_di(service: TenantService) -> None:
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")

    with pytest.raises(ValidationError, match="cuối cùng"):
        await service.remove_member(
            actor_id=CHU, organization_id=access.organization_id, user_id=CHU
        )


async def test_co_owner_thu_hai_thi_ha_cap_duoc(service: TenantService) -> None:
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")
    org = access.organization_id
    await service.add_member(
        actor_id=CHU, organization_id=org, email=EMAIL_OF[PHO], role=OrgRole.OWNER
    )

    await service.change_member_role(
        actor_id=CHU, organization_id=org, user_id=CHU, role=OrgRole.MEMBER
    )

    con_lai = await service.require_access(user_id=CHU, organization_id=org)
    assert con_lai.org_role is OrgRole.MEMBER


async def test_thanh_vien_thuong_duoc_tu_roi_organization(service: TenantService) -> None:
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")
    org = access.organization_id
    await service.add_member(
        actor_id=CHU, organization_id=org, email=EMAIL_OF[NHAN_VIEN], role=OrgRole.MEMBER
    )

    await service.remove_member(actor_id=NHAN_VIEN, organization_id=org, user_id=NHAN_VIEN)

    with pytest.raises(AuthorizationError):
        await service.require_access(user_id=NHAN_VIEN, organization_id=org)


async def test_roi_organization_thi_mat_luon_quyen_workspace(service: TenantService) -> None:
    """Quyền workspace mồ côi sẽ sống lại nếu user được thêm vào lần nữa."""
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")
    org = access.organization_id
    ws = await service.create_workspace(actor_id=CHU, organization_id=org, name="Marketing")
    await service.add_member(
        actor_id=CHU, organization_id=org, email=EMAIL_OF[NHAN_VIEN], role=OrgRole.MEMBER
    )
    await service.set_workspace_member(
        actor_id=CHU, workspace_id=ws.id, user_id=NHAN_VIEN, role=WorkspaceRole.EDITOR
    )

    await service.remove_member(actor_id=CHU, organization_id=org, user_id=NHAN_VIEN)
    await service.add_member(
        actor_id=CHU, organization_id=org, email=EMAIL_OF[NHAN_VIEN], role=OrgRole.MEMBER
    )

    assert await service.list_workspaces(actor_id=NHAN_VIEN, organization_id=org) == []


async def test_khong_cap_duoc_quyen_workspace_cho_nguoi_ngoai_tenant(
    service: TenantService,
) -> None:
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")
    ws = await service.create_workspace(
        actor_id=CHU, organization_id=access.organization_id, name="Marketing"
    )

    with pytest.raises(ValidationError, match="chưa phải thành viên"):
        await service.set_workspace_member(
            actor_id=CHU, workspace_id=ws.id, user_id=NGUOI_LA, role=WorkspaceRole.EDITOR
        )


# ─── Đổi tên workspace ───────────────────────────────────────────────────────


async def test_doi_duoc_ten_va_dia_chi_workspace(service: TenantService) -> None:
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")
    ws = (await service.list_workspaces(actor_id=CHU, organization_id=access.organization_id))[0]

    doi = await service.rename_workspace(
        actor_id=CHU, workspace_id=ws.id, name="Marketing", slug="marketing"
    )

    assert doi.name == "Marketing"
    assert doi.slug == "marketing"


async def test_khong_truyen_slug_thi_dia_chi_giu_nguyen(service: TenantService) -> None:
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")
    ws = (await service.list_workspaces(actor_id=CHU, organization_id=access.organization_id))[0]

    doi = await service.rename_workspace(actor_id=CHU, workspace_id=ws.id, name="Marketing")

    assert doi.name == "Marketing"
    assert doi.slug == ws.slug


async def test_admin_cua_workspace_doi_duoc_ten_du_chi_la_member_cua_org(
    service: TenantService,
) -> None:
    """Người phụ trách một workspace thì đặt được tên cho nó."""
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")
    org = access.organization_id
    ws = await service.create_workspace(actor_id=CHU, organization_id=org, name="Marketing")
    await service.add_member(
        actor_id=CHU, organization_id=org, email=EMAIL_OF[NHAN_VIEN], role=OrgRole.MEMBER
    )
    await service.set_workspace_member(
        actor_id=CHU, workspace_id=ws.id, user_id=NHAN_VIEN, role=WorkspaceRole.ADMIN
    )

    doi = await service.rename_workspace(actor_id=NHAN_VIEN, workspace_id=ws.id, name="Growth")

    assert doi.name == "Growth"


async def test_editor_cua_workspace_khong_doi_duoc_ten(service: TenantService) -> None:
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")
    org = access.organization_id
    ws = await service.create_workspace(actor_id=CHU, organization_id=org, name="Marketing")
    await service.add_member(
        actor_id=CHU, organization_id=org, email=EMAIL_OF[NHAN_VIEN], role=OrgRole.MEMBER
    )
    await service.set_workspace_member(
        actor_id=CHU, workspace_id=ws.id, user_id=NHAN_VIEN, role=WorkspaceRole.EDITOR
    )

    with pytest.raises(AuthorizationError):
        await service.rename_workspace(actor_id=NHAN_VIEN, workspace_id=ws.id, name="Cua toi")


async def test_dia_chi_trung_trong_cung_org_bi_tu_choi(service: TenantService) -> None:
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")
    org = access.organization_id
    marketing = await service.create_workspace(actor_id=CHU, organization_id=org, name="Marketing")
    await service.create_workspace(actor_id=CHU, organization_id=org, name="Engineering")

    with pytest.raises(ValidationError, match="đã có workspace"):
        await service.rename_workspace(
            actor_id=CHU, workspace_id=marketing.id, name="Marketing", slug="engineering"
        )


async def test_hai_org_khac_nhau_dung_chung_dia_chi_workspace(service: TenantService) -> None:
    """Địa chỉ workspace chỉ duy nhất trong phạm vi một organization."""
    a = await service.provision_for_new_user(user_id=CHU, display_name="An")
    b = await service.provision_for_new_user(user_id=NGUOI_LA, display_name="La")
    cua_a = (await service.list_workspaces(actor_id=CHU, organization_id=a.organization_id))[0]
    cua_b = (await service.list_workspaces(actor_id=NGUOI_LA, organization_id=b.organization_id))[0]

    await service.rename_workspace(
        actor_id=CHU, workspace_id=cua_a.id, name="Marketing", slug="marketing"
    )
    doi_b = await service.rename_workspace(
        actor_id=NGUOI_LA, workspace_id=cua_b.id, name="Marketing", slug="marketing"
    )

    assert doi_b.slug == "marketing"


async def test_nguoi_ngoai_tenant_khong_doi_duoc_ten_workspace(service: TenantService) -> None:
    access = await service.provision_for_new_user(user_id=CHU, display_name="An")
    await service.provision_for_new_user(user_id=NGUOI_LA, display_name="La")
    ws = (await service.list_workspaces(actor_id=CHU, organization_id=access.organization_id))[0]

    with pytest.raises(AuthorizationError):
        await service.rename_workspace(actor_id=NGUOI_LA, workspace_id=ws.id, name="Chiem doat")
