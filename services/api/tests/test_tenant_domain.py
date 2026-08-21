"""Quy tắc nghiệp vụ của Tenant — thuần, không chạm database."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.errors import AuthorizationError, ValidationError
from app.domains.tenant.domain.entities import (
    MAX_NAME_LENGTH,
    Organization,
    OrgRole,
    WorkspaceRole,
    can_manage_members,
    can_manage_organization,
    default_organization_name,
    ensure_can_assign_org_role,
    ensure_not_last_owner,
    ensure_slug_editable,
    normalize_name,
    normalize_slug,
    resolve_workspace_role,
    slugify,
)

# ─── Tên và slug ─────────────────────────────────────────────────────────────


def test_gop_khoang_trang_thua() -> None:
    assert normalize_name("  Acme   Corp  ") == "Acme Corp"


def test_ten_rong_bi_tu_choi() -> None:
    with pytest.raises(ValidationError):
        normalize_name("   ")


def test_ten_qua_dai_bi_tu_choi() -> None:
    with pytest.raises(ValidationError):
        normalize_name("a" * (MAX_NAME_LENGTH + 1))


def test_slug_bo_ky_tu_dac_biet() -> None:
    assert slugify("Acme Corp!! 2026", fallback="org") == "acme-corp-2026"


def test_dau_tieng_viet_duoc_boc_chu_khong_bi_vut() -> None:
    """Vứt ký tự non-ASCII sẽ cho slug rác kiểu ``c-ng-ty-c-ph-n``."""
    assert slugify("Công ty Cổ phần", fallback="org") == "cong-ty-co-phan"


def test_chu_d_gach_ngang_duoc_anh_xa_tay() -> None:
    """Chữ "đ" không phân rã được bằng NFD — nó là ký tự riêng, không phải d + dấu."""
    assert slugify("Đường Đi Đúng", fallback="org") == "duong-di-dung"


def test_ten_khong_con_gi_sau_khi_loc_thi_roi_ve_fallback() -> None:
    assert slugify("日本語", fallback="org") == "org"
    assert slugify("🚀🚀🚀", fallback="org") == "org"
    assert slugify("!!!", fallback="org") == "org"


def test_slug_khong_bao_gio_bat_dau_hoac_ket_thuc_bang_gach() -> None:
    for raw in ("!!!Acme!!!", "   Acme   ", "-Acme-"):
        slug = slugify(raw, fallback="org")
        assert not slug.startswith("-")
        assert not slug.endswith("-")


def test_ten_organization_mac_dinh_theo_ten_hien_thi() -> None:
    assert default_organization_name("An") == "An's Organization"


# ─── Quyền tổ chức ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("role", "duoc_phep"),
    [(OrgRole.OWNER, True), (OrgRole.ADMIN, True), (OrgRole.MEMBER, False)],
)
def test_ai_duoc_quan_tri_organization(role: OrgRole, duoc_phep: bool) -> None:
    assert can_manage_organization(role) is duoc_phep
    assert can_manage_members(role) is duoc_phep


# ─── Quyền workspace ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("org_role", [OrgRole.OWNER, OrgRole.ADMIN])
def test_quan_tri_organization_co_quyen_admin_moi_workspace(org_role: OrgRole) -> None:
    """Không có quy tắc này thì OWNER tự khoá mình ra khỏi workspace mình tạo."""
    assert resolve_workspace_role(org_role, None) is WorkspaceRole.ADMIN


def test_member_chi_co_quyen_o_workspace_duoc_cap() -> None:
    assert resolve_workspace_role(OrgRole.MEMBER, None) is None
    assert resolve_workspace_role(OrgRole.MEMBER, WorkspaceRole.EDITOR) is WorkspaceRole.EDITOR


def test_quyen_workspace_cua_member_khong_bi_nang_len() -> None:
    assert resolve_workspace_role(OrgRole.MEMBER, WorkspaceRole.VIEWER) is WorkspaceRole.VIEWER


# ─── Cấp vai trò ─────────────────────────────────────────────────────────────


def test_member_khong_duoc_quan_ly_thanh_vien() -> None:
    with pytest.raises(AuthorizationError):
        ensure_can_assign_org_role(
            actor_role=OrgRole.MEMBER, target_current_role=None, new_role=OrgRole.MEMBER
        )


def test_owner_lam_duoc_moi_thu() -> None:
    for target in (None, OrgRole.OWNER, OrgRole.ADMIN, OrgRole.MEMBER):
        for moi in (OrgRole.OWNER, OrgRole.ADMIN, OrgRole.MEMBER):
            ensure_can_assign_org_role(
                actor_role=OrgRole.OWNER, target_current_role=target, new_role=moi
            )


def test_admin_khong_duoc_tu_nang_minh_len_owner() -> None:
    """Thiếu quy tắc này thì ADMIN thực chất là OWNER."""
    with pytest.raises(AuthorizationError, match="OWNER"):
        ensure_can_assign_org_role(
            actor_role=OrgRole.ADMIN, target_current_role=OrgRole.ADMIN, new_role=OrgRole.OWNER
        )


def test_admin_khong_duoc_ha_cap_owner() -> None:
    with pytest.raises(AuthorizationError):
        ensure_can_assign_org_role(
            actor_role=OrgRole.ADMIN, target_current_role=OrgRole.OWNER, new_role=OrgRole.MEMBER
        )


def test_admin_khong_duoc_dung_toi_admin_khac() -> None:
    with pytest.raises(AuthorizationError):
        ensure_can_assign_org_role(
            actor_role=OrgRole.ADMIN, target_current_role=OrgRole.ADMIN, new_role=OrgRole.MEMBER
        )


def test_admin_duoc_them_va_quan_ly_member_thuong() -> None:
    ensure_can_assign_org_role(
        actor_role=OrgRole.ADMIN, target_current_role=None, new_role=OrgRole.MEMBER
    )
    ensure_can_assign_org_role(
        actor_role=OrgRole.ADMIN, target_current_role=OrgRole.MEMBER, new_role=OrgRole.ADMIN
    )


# ─── OWNER cuối cùng ─────────────────────────────────────────────────────────


def test_khong_duoc_bo_owner_cuoi_cung() -> None:
    """Organization không còn OWNER là trạng thái không thể tự thoát ra."""
    with pytest.raises(ValidationError, match="cuối cùng"):
        ensure_not_last_owner(owner_count=1, target_role=OrgRole.OWNER)


def test_con_owner_khac_thi_bo_duoc() -> None:
    ensure_not_last_owner(owner_count=2, target_role=OrgRole.OWNER)


def test_bo_nguoi_khong_phai_owner_thi_khong_bi_chan() -> None:
    ensure_not_last_owner(owner_count=1, target_role=OrgRole.ADMIN)
    ensure_not_last_owner(owner_count=1, target_role=OrgRole.MEMBER)


# ─── Slug do người dùng tự gõ ────────────────────────────────────────────────


def test_slug_hop_le_duoc_chap_nhan() -> None:
    assert normalize_slug("  Acme  ") == "acme"
    assert normalize_slug("acme-corp-2026") == "acme-corp-2026"


def test_slug_qua_ngan_bi_tu_choi() -> None:
    with pytest.raises(ValidationError):
        normalize_slug("ab")


@pytest.mark.parametrize(
    "xau",
    ["-acme", "acme-", "acme--corp", "acme corp", "acme_corp", "Acme!", "công-ty"],
)
def test_slug_sai_dinh_dang_bi_tu_choi(xau: str) -> None:
    """Không tự sửa thành thứ khác — người dùng chủ ý gõ nên phải được báo lỗi."""
    with pytest.raises(ValidationError):
        normalize_slug(xau)


@pytest.mark.parametrize("giu_cho", ["api", "settings", "workflows", "login"])
def test_slug_trung_duong_dan_he_thong_bi_chan(giu_cho: str) -> None:
    """Slug nằm trong URL — một org tên "api" sẽ che mất route thật."""
    with pytest.raises(ValidationError, match="giữ chỗ"):
        normalize_slug(giu_cho)


def test_slug_khoa_lai_sau_khi_hoan_tat_thiet_lap() -> None:
    now = datetime(2026, 8, 21, tzinfo=UTC)
    dang_thiet_lap = Organization(
        id="org_1", name="Acme", slug="acme", created_at=now, updated_at=now
    )
    da_xong = Organization(
        id="org_2",
        name="Acme",
        slug="acme",
        created_at=now,
        updated_at=now,
        onboarded_at=now,
    )

    ensure_slug_editable(dang_thiet_lap)  # không ném

    assert not dang_thiet_lap.is_onboarded
    assert da_xong.is_onboarded
    with pytest.raises(ValidationError, match="đã được chốt"):
        ensure_slug_editable(da_xong)
