"""Catalog tool nhìn từ domain `workflow`.

Hai nửa của cùng một đường: `integration.infrastructure.catalog_view` trả dữ
liệu thuần, `workflow.infrastructure.catalog` dựng lại thành kiểu của domain
`workflow`. Test này nằm ở `tests/` nên nhìn được cả hai — code ứng dụng thì
không (contract 1).
"""

from __future__ import annotations

from app.domains.integration.domain.catalog import default_registry
from app.domains.integration.infrastructure.catalog_view import tool_rows
from app.domains.workflow.infrastructure.catalog import RowToolCatalog


def _catalog() -> RowToolCatalog:
    return RowToolCatalog(tool_rows())


def test_hang_du_lieu_khong_mang_kieu_cua_integration() -> None:
    """Cả ranh giới này tồn tại để không kiểu nào đi qua — chỉ dữ liệu."""
    for row in tool_rows():
        for value in row.values():
            assert isinstance(value, str | bool | dict | list), value


def test_tra_duoc_dung_ban_da_ghim() -> None:
    found = _catalog().find("slack.post_message", "1.0")
    assert found is not None
    assert found.provider == "slack"
    assert found.mutates is True
    assert "channel" in found.input_schema["properties"]


def test_tool_doc_khong_phai_tool_ghi() -> None:
    found = _catalog().find("slack.list_channels", "1.0")
    assert found is not None
    assert found.mutates is False


def test_ban_khong_ton_tai_tra_none_va_liet_ke_ban_dang_co() -> None:
    catalog = _catalog()
    assert catalog.find("slack.post_message", "9.9") is None
    assert catalog.versions("slack.post_message") == ("1.0",)


def test_tool_khong_ton_tai_thi_khong_co_ban_nao() -> None:
    assert _catalog().versions("slack.khong_co") == ()


def test_mang_du_moi_version_chu_khong_chi_minor_moi_nhat() -> None:
    """`ToolRegistry.list_tools()` chỉ đưa minor mới nhất. Một workflow đã
    publish ghim đúng một bản, nên view phải mang cả những bản cũ hơn."""
    catalog = _catalog()
    for tool in default_registry().list_tools():
        assert catalog.find(tool.tool_id, tool.version) is not None

    assert catalog.find("notion.search", "1.2") is not None
    assert "1.2" in catalog.versions("notion.search")
