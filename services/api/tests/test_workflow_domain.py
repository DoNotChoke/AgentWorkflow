"""Quy tắc thuần của vòng đời workflow (§12).

Không có hạ tầng nào ở đây — phần chạm PostgreSQL nằm ở
`test_workflow_repository.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import AuthorizationError, ValidationError
from app.domains.workflow.domain.entities import (
    FIRST_VERSION,
    Workflow,
    WorkflowVersion,
    archive,
    ensure_can_manage,
    ensure_not_archived,
    fingerprint,
    next_number,
    published,
    unarchive,
)

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(hours=1)


def make_workflow(**overrides: object) -> Workflow:
    base: dict[str, object] = {
        "id": "wf_1",
        "tenant_id": "org_1",
        "workspace_id": "ws_1",
        "name": "Tổng hợp #support",
        "created_by": "usr_1",
        "created_at": NOW,
        "updated_at": NOW,
    }
    base.update(overrides)
    return Workflow(**base)  # type: ignore[arg-type]


def make_version(**overrides: object) -> WorkflowVersion:
    ir = {"schema_version": "1.0", "nodes": [], "edges": []}
    base: dict[str, object] = {
        "id": "wfv_1",
        "workflow_id": "wf_1",
        "tenant_id": "org_1",
        "number": FIRST_VERSION,
        "ir": ir,
        "ir_sha256": fingerprint(ir),
        "published_at": NOW,
        "published_by": "usr_1",
    }
    base.update(overrides)
    return WorkflowVersion(**base)  # type: ignore[arg-type]


# ─── Đánh số ─────────────────────────────────────────────────────────────────


def test_version_dau_tien_la_mot() -> None:
    """Số version là thứ người dùng đọc, nên đếm từ 1 chứ không từ 0."""
    assert next_number(None) == 1


def test_version_sau_tang_dan_tu_so_lon_nhat() -> None:
    assert next_number(3) == 4


# ─── Publish ─────────────────────────────────────────────────────────────────


def test_publish_tro_current_version_sang_ban_vua_ra() -> None:
    workflow = make_workflow()
    assert workflow.is_published is False

    after = published(workflow, make_version(), at=LATER)

    assert after.current_version_id == "wfv_1"
    assert after.is_published is True
    assert after.updated_at == LATER
    # Bản gốc không đổi: entity là frozen, publish trả về một giá trị mới.
    assert workflow.current_version_id is None


def test_khong_publish_duoc_version_cua_workflow_khac() -> None:
    with pytest.raises(ValidationError):
        published(make_workflow(), make_version(workflow_id="wf_khac"), at=LATER)


def test_van_tay_khong_phu_thuoc_thu_tu_khoa() -> None:
    """Hai IR cùng nội dung khác thứ tự khoá phải cho cùng một vân tay — nếu
    không, giao diện sẽ nói "khác v3" cho hai bản giống hệt nhau."""
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})


def test_van_tay_doi_khi_noi_dung_doi() -> None:
    assert fingerprint({"a": 1}) != fingerprint({"a": 2})


# ─── Bất biến ────────────────────────────────────────────────────────────────


def test_version_khong_co_cua_nao_de_sua() -> None:
    """§2.3 bắt đầu ở đây: không có hàm nào trong domain nhận một version và
    trả về một version khác. `dataclasses.replace` vẫn dựng được một giá trị
    mới, nhưng không có đường nào ghi nó đè lên bản cũ — repository không có
    `update_version`, và migration 0013 còn một trigger nữa."""
    import app.domains.workflow.domain.entities as entities

    mutators = [
        name
        for name in dir(entities)
        if name.startswith(("update", "edit", "set_", "replace_")) and "version" in name.lower()
    ]
    assert mutators == []


# ─── Lưu trữ ─────────────────────────────────────────────────────────────────


def test_luu_tru_roi_dua_lai_duoc() -> None:
    archived = archive(make_workflow(), at=LATER)
    assert archived.is_archived is True

    back = unarchive(archived, at=LATER)
    assert back.is_archived is False


def test_luu_tru_hai_lan_bi_tu_choi() -> None:
    archived = archive(make_workflow(), at=LATER)
    with pytest.raises(ValidationError):
        archive(archived, at=LATER)


def test_dua_lai_thu_chua_luu_tru_bi_tu_choi() -> None:
    with pytest.raises(ValidationError):
        unarchive(make_workflow(), at=LATER)


def test_workflow_da_luu_tru_thi_khong_sua_duoc_nua() -> None:
    with pytest.raises(ValidationError):
        ensure_not_archived(archive(make_workflow(), at=LATER))


# ─── Quyền ───────────────────────────────────────────────────────────────────


def test_nguoi_tao_quan_ly_duoc() -> None:
    ensure_can_manage(make_workflow(), actor_id="usr_1", can_manage_workspace=False)


def test_quan_tri_vien_workspace_quan_ly_duoc() -> None:
    ensure_can_manage(make_workflow(), actor_id="usr_khac", can_manage_workspace=True)


def test_thanh_vien_khac_khong_quan_ly_duoc() -> None:
    """Cùng luật với Agent: workflow là tài nguyên của workspace, nhưng sửa nó
    cần là người tạo hoặc quản trị viên."""
    with pytest.raises(AuthorizationError):
        ensure_can_manage(make_workflow(), actor_id="usr_khac", can_manage_workspace=False)
