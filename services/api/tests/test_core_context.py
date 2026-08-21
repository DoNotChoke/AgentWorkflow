"""Kiểm tra cơ chế tenant context (§19).

Các test này bảo vệ một bất biến: không thao tác nào chạm dữ liệu thuộc tenant
được phép chạy khi không có tenant context. Truy cập chéo tenant là sự cố bảo
mật nghiêm trọng, nên chế độ thất bại phải là ném lỗi, không phải trả rỗng.
"""

from __future__ import annotations

import pytest

from app.core.context import (
    MissingTenantContextError,
    Principal,
    new_request_context,
    require_tenant_id,
    tenant_cache_key,
    tenant_storage_prefix,
    use_context,
    with_execution,
)


def test_require_tenant_id_khong_co_context_thi_nem_loi() -> None:
    with pytest.raises(MissingTenantContextError):
        require_tenant_id()


def test_require_tenant_id_co_context_nhung_thieu_tenant_thi_nem_loi() -> None:
    ctx = new_request_context()
    with use_context(ctx), pytest.raises(MissingTenantContextError):
        require_tenant_id()


def test_context_duoc_go_bo_khi_thoat_scope() -> None:
    ctx = new_request_context(tenant_id="org_a")
    with use_context(ctx):
        assert require_tenant_id() == "org_a"
    with pytest.raises(MissingTenantContextError):
        require_tenant_id()


def test_context_long_nhau_khong_ro_ri_giua_cac_tenant() -> None:
    outer = new_request_context(tenant_id="org_a")
    inner = new_request_context(tenant_id="org_b")
    with use_context(outer):
        assert require_tenant_id() == "org_a"
        with use_context(inner):
            assert require_tenant_id() == "org_b"
        assert require_tenant_id() == "org_a"


def test_cache_key_luon_mang_tien_to_tenant() -> None:
    with use_context(new_request_context(tenant_id="org_a")):
        assert tenant_cache_key("conn", "slack") == "aw:t:org_a:conn:slack"


def test_storage_prefix_luon_mang_tien_to_tenant() -> None:
    with use_context(new_request_context(tenant_id="org_a")):
        assert tenant_storage_prefix("traces", "trc_1") == "tenants/org_a/traces/trc_1"


def test_cache_key_khong_co_tenant_thi_nem_loi() -> None:
    with pytest.raises(MissingTenantContextError):
        tenant_cache_key("conn")


def test_log_fields_chua_dinh_danh_correlation() -> None:
    ctx = new_request_context(
        tenant_id="org_a",
        workspace_id="ws_1",
        principal=Principal(kind="user", id="usr_1"),
    )
    fields = ctx.as_log_fields()
    assert fields["tenant_id"] == "org_a"
    assert fields["workspace_id"] == "ws_1"
    assert fields["principal_kind"] == "user"
    assert fields["request_id"] == fields["correlation_id"]


def test_with_execution_giu_nguyen_tenant_va_bo_sung_dinh_danh() -> None:
    ctx = new_request_context(tenant_id="org_a", workspace_id="ws_1")
    enriched = with_execution(ctx, execution_id="exe_1", execution_trace_id="trc_1")

    assert enriched.tenant_id == "org_a"
    assert enriched.workspace_id == "ws_1"
    assert enriched.execution_id == "exe_1"
    assert enriched.execution_trace_id == "trc_1"
    # Context là bất biến — bản gốc không bị sửa.
    assert ctx.execution_id is None
