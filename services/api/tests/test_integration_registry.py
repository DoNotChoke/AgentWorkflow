"""Tool Registry: phân giải version, ràng buộc catalog, và chống trôi.

Không cần Docker. Fake viết tay khi cần dữ liệu tuỳ biến; catalog thật khi cần
khẳng định về bốn tool Slack đã commit.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import make_connector, make_tool

from app.core.errors import ValidationError
from app.domains.integration.domain.catalog import ToolRegistry, default_registry
from app.domains.integration.domain.generated import catalog_v1
from app.domains.integration.domain.tools import (
    RateLimitProfile,
    RateLimitRule,
    SideEffect,
    parse_version,
)
from app.domains.integration.infrastructure.specs import load_documents

SCHEMA_ROOT = Path(__file__).resolve().parents[3] / "packages" / "schemas" / "tool"


# ─── Phân giải version (§41) ─────────────────────────────────────────────────


def test_khong_chi_version_thi_lay_major_cao_nhat() -> None:
    registry = ToolRegistry(
        [make_connector()],
        [make_tool(version="1.4"), make_tool(version="2.1")],
    )
    assert registry.get_tool("demo.ping").version == "2.1"


def test_chi_major_thi_lay_minor_moi_nhat_cua_major_do() -> None:
    registry = ToolRegistry(
        [make_connector()],
        [make_tool(version="1.2"), make_tool(version="1.9"), make_tool(version="2.0")],
    )
    assert registry.get_tool("demo.ping", "1").version == "1.9"


def test_minor_duoc_so_theo_so_khong_theo_chuoi() -> None:
    """`"1.10"` mới hơn `"1.9"`.

    So theo chuỗi thì `"1.10" < "1.9"`, và bản mới hơn bị coi là cũ hơn — sai
    một cách âm thầm, đúng loại lỗi mà §41 không cho phép.
    """
    registry = ToolRegistry(
        [make_connector()],
        [make_tool(version="1.9"), make_tool(version="1.10")],
    )
    assert registry.get_tool("demo.ping", "1").version == "1.10"


def test_chi_dung_mot_ban_thi_lay_dung_ban_do() -> None:
    registry = ToolRegistry(
        [make_connector()],
        [make_tool(version="1.2"), make_tool(version="1.9")],
    )
    assert registry.get_tool("demo.ping", "1.2").version == "1.2"


def test_hai_major_cung_ton_tai_duoc() -> None:
    """§41 — Workflow đã publish ghim major 1 vẫn chạy sau khi major 2 ra đời."""
    registry = ToolRegistry(
        [make_connector()],
        [make_tool(version="1.3"), make_tool(version="2.0")],
    )
    assert registry.get_tool("demo.ping", "1").version == "1.3"
    assert registry.get_tool("demo.ping", "2").version == "2.0"


def test_tool_khong_ton_tai_thi_bao_ban_dang_co() -> None:
    registry = ToolRegistry([make_connector()], [make_tool(version="1.0")])

    with pytest.raises(ValidationError) as caught:
        registry.get_tool("demo.ping", "3.0")

    assert "1.0" in str(caught.value)


def test_version_sai_dinh_dang_bi_tu_choi() -> None:
    registry = ToolRegistry([make_connector()], [make_tool()])

    with pytest.raises(ValidationError):
        registry.get_tool("demo.ping", "khong-phai-so")


@pytest.mark.parametrize("raw", ["1", "1.2.3", "a.b", "-1.0"])
def test_parse_version_tu_choi_dinh_dang_la(raw: str) -> None:
    with pytest.raises(ValidationError):
        parse_version(raw)


# ─── Ràng buộc lúc dựng catalog ──────────────────────────────────────────────


def test_tool_khong_thuoc_connector_nao_lam_catalog_hong() -> None:
    with pytest.raises(ValidationError, match="không thuộc connector nào"):
        ToolRegistry(
            [make_connector(tool_ids=("demo.ping",))],
            [make_tool("demo.ping"), make_tool("demo.pong")],
        )


def test_connector_khai_tool_khong_ton_tai_lam_catalog_hong() -> None:
    with pytest.raises(ValidationError, match="khai tool không tồn tại"):
        ToolRegistry(
            [make_connector(tool_ids=("demo.ping", "demo.thieu"))],
            [make_tool("demo.ping")],
        )


def test_cung_mot_tool_hai_lan_lam_catalog_hong() -> None:
    with pytest.raises(ValidationError, match="khai báo hai lần"):
        ToolRegistry(
            [make_connector()],
            [make_tool(version="1.0"), make_tool(version="1.0")],
        )


def test_tool_khai_provider_chua_co_connector_lam_catalog_hong() -> None:
    with pytest.raises(ValidationError, match="chưa có connector"):
        ToolRegistry(
            [make_connector("demo", tool_ids=("khac.ping",))],
            [make_tool("khac.ping", provider="khac")],
        )


# ─── Liệt kê ─────────────────────────────────────────────────────────────────


def test_liet_ke_bo_qua_tool_da_tat() -> None:
    registry = ToolRegistry(
        [make_connector(tool_ids=("demo.ping", "demo.pong"))],
        [make_tool("demo.ping"), make_tool("demo.pong", enabled=False)],
    )
    assert [tool.tool_id for tool in registry.list_tools()] == ["demo.ping"]
    assert len(registry.list_tools(enabled_only=False)) == 2


def test_liet_ke_chi_tra_ve_minor_moi_nhat_cua_moi_major() -> None:
    registry = ToolRegistry(
        [make_connector()],
        [make_tool(version="1.0"), make_tool(version="1.5"), make_tool(version="2.0")],
    )
    assert [tool.version for tool in registry.list_tools()] == ["1.5", "2.0"]


# ─── Gộp rate limit ──────────────────────────────────────────────────────────


def test_tool_ghi_de_rate_limit_theo_tung_cap_chu_khong_thay_ca_cum() -> None:
    """Siết riêng cấp `tool` không được xoá ngưỡng `provider` của connector."""
    base = RateLimitProfile(
        provider=RateLimitRule(limit=600, window_seconds=60),
        tenant=RateLimitRule(limit=120, window_seconds=60),
    )
    override = RateLimitProfile(tool=RateLimitRule(limit=20, window_seconds=60))

    merged = override.merged_over(base)

    assert merged.provider == base.provider
    assert merged.tenant == base.tenant
    assert merged.tool == override.tool


def test_rules_tra_ve_cac_cap_theo_thu_tu_rong_toi_hep() -> None:
    profile = RateLimitProfile(
        tool=RateLimitRule(limit=1, window_seconds=1),
        provider=RateLimitRule(limit=2, window_seconds=1),
        user=RateLimitRule(limit=3, window_seconds=1),
    )
    assert [level for level, _ in profile.rules()] == ["provider", "user", "tool"]


# ─── Catalog thật đã commit ──────────────────────────────────────────────────


def test_catalog_sinh_ra_khop_voi_json() -> None:
    """Bản Python đã commit phải tương đương ngữ nghĩa với các tài liệu JSON.

    So bằng giá trị chứ không bằng byte: bản đã commit còn đi qua `ruff format`
    nên khác về khoảng trắng. Đây là thứ giữ cho `catalog_v1.py` không trôi
    khỏi nguồn chân lý mà không cần chạy codegen lúc test.
    """
    connectors, tools = load_documents(SCHEMA_ROOT)

    assert tuple(sorted(connectors, key=lambda c: c.connector_id)) == catalog_v1.CONNECTORS
    assert tuple(sorted(tools, key=lambda t: (t.tool_id, t.version))) == catalog_v1.TOOLS


def test_registry_mac_dinh_dung_duoc_ngay() -> None:
    registry = default_registry()

    assert [connector.connector_id for connector in registry.list_connectors()] == ["slack"]
    assert len(registry.list_tools(provider="slack")) == 4

    post = registry.get_tool("slack.post_message")
    assert post.side_effect is SideEffect.WRITE
    assert post.idempotency.required is True
    assert post.required_scopes == frozenset({"chat:write"})


def test_moi_scope_tool_can_deu_nam_trong_scope_mac_dinh_cua_provider() -> None:
    """Ràng buộc liên domain, và đây là tầng duy nhất được phép kiểm nó.

    Contract 1 cấm `integration` import `connection`, nên không code nào trong
    ứng dụng nhìn thấy được cả hai catalog. Thư mục `tests/` nằm ngoài
    `root_packages` của import-linter, nên nó nhìn được — và nó phải nhìn, vì
    nếu không thì một tool khai scope mà vòng OAuth không bao giờ xin sẽ chỉ lộ
    ra lúc người dùng thật bấm nút.
    """
    from app.domains.connection.domain.providers import PROVIDERS

    registry = default_registry()

    for tool in registry.list_tools(enabled_only=False):
        descriptor = PROVIDERS.get(tool.provider)
        assert descriptor is not None, f"{tool.tool_id} khai provider lạ {tool.provider!r}"

        thieu = tool.required_scopes - frozenset(descriptor.default_scopes)
        assert not thieu, (
            f"{tool.tool_id} cần scope {sorted(thieu)} mà vòng OAuth của "
            f"{tool.provider!r} không bao giờ xin. Thêm vào `default_scopes` "
            "trong connection/domain/providers.py."
        )
