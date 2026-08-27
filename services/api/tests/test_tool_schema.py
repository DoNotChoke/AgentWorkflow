"""Tài liệu trong `packages/schemas/tool/` phải hợp lệ.

Đây là tầng kiểm cho **nguồn chân lý**, và nó cố ý chỉ dùng `jsonschema` —
không import gì từ `app`. Contract JSON phải đứng vững một mình: nếu nó chỉ
kiểm được thông qua parser Python thì parser trở thành định nghĩa thật, và
JSON tụt xuống thành tài liệu.

Đường dẫn tới `packages/` được suy ra từ vị trí file test. Ở đây làm vậy là an
toàn — test chỉ chạy trong repo đã checkout, và nếu bố cục repo đổi thì test đỏ
ngay và ồn ào. Code ứng dụng thì không được phép làm thế.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

SCHEMA_ROOT = Path(__file__).resolve().parents[3] / "packages" / "schemas" / "tool"

TOOL_META_SCHEMA = "tool.schema.json"
CONNECTOR_META_SCHEMA = "connector.schema.json"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _tool_documents() -> list[Path]:
    return sorted(SCHEMA_ROOT.glob("*/tools/*.json"))


def _connector_documents() -> list[Path]:
    return sorted(SCHEMA_ROOT.glob("*/connector.json"))


def test_thu_muc_schema_ton_tai() -> None:
    """Bắt lỗi bố cục trước, để các test sau không đỗ vì không tìm thấy gì.

    Một `glob` không khớp gì trả về danh sách rỗng, và `parametrize` trên danh
    sách rỗng thì không chạy test nào — trạng thái nguy hiểm hơn một lần đỏ, vì
    nó trông y hệt thành công.
    """
    assert (SCHEMA_ROOT / TOOL_META_SCHEMA).is_file()
    assert (SCHEMA_ROOT / CONNECTOR_META_SCHEMA).is_file()
    assert _tool_documents(), f"Không có tài liệu tool nào dưới {SCHEMA_ROOT}"
    assert _connector_documents(), f"Không có tài liệu connector nào dưới {SCHEMA_ROOT}"


@pytest.mark.parametrize("path", _tool_documents(), ids=lambda p: p.stem)
def test_moi_tai_lieu_tool_hop_le(path: Path) -> None:
    jsonschema.validate(instance=_load(path), schema=_load(SCHEMA_ROOT / TOOL_META_SCHEMA))


@pytest.mark.parametrize("path", _connector_documents(), ids=lambda p: p.parent.name)
def test_moi_tai_lieu_connector_hop_le(path: Path) -> None:
    jsonschema.validate(instance=_load(path), schema=_load(SCHEMA_ROOT / CONNECTOR_META_SCHEMA))


@pytest.mark.parametrize("path", _tool_documents(), ids=lambda p: p.stem)
def test_input_va_output_schema_tu_chung_hop_le(path: Path) -> None:
    """`json_schema` bên trong tài liệu cũng phải là JSON Schema đúng.

    Meta-schema chỉ nói nó là một object; nó không kiểm nội dung. Một
    `"type": "strng"` gõ sai sẽ lọt qua và chỉ lộ ra lúc Gateway validate
    input thật.
    """
    document = _load(path)
    validator = jsonschema.Draft202012Validator
    for which in ("input_schema", "output_schema"):
        validator.check_schema(document[which]["json_schema"])


@pytest.mark.parametrize("path", _tool_documents(), ids=lambda p: p.stem)
def test_ten_file_khop_voi_tool_id_va_major(path: Path) -> None:
    """`post_message.v1.json` phải chứa `slack.post_message` bản major 1.

    Không có ràng buộc này thì hai major có thể nằm chung một file hoặc một
    file mang tên sai, và §41 (hai major cùng tồn tại) trở nên khó rà bằng mắt.
    """
    document = _load(path)
    stem, _, raw_major = path.stem.partition(".v")
    assert document["tool_id"].endswith(f".{stem}"), (
        f"{path.name} chứa tool_id {document['tool_id']!r}"
    )
    assert document["version"].split(".")[0] == raw_major
    assert document["provider"] == path.parents[1].name


@pytest.mark.parametrize("path", _tool_documents(), ids=lambda p: p.stem)
def test_output_mapping_phu_dung_cac_truong_cua_output_schema(path: Path) -> None:
    """Mọi trường trong `output_schema` phải có đường lấy, và ngược lại.

    Thiếu một chiều thì tool khai một trường không bao giờ được điền. Thừa
    chiều kia thì có dữ liệu được chiếu ra mà không ai khai báo — đúng thứ mà
    `additionalProperties: false` đang ngăn.
    """
    document = _load(path)
    declared = set(document["output_schema"]["json_schema"].get("properties", {}))
    mapped = set(document["output_mapping"])
    assert declared == mapped, (
        f"output_schema có {sorted(declared - mapped)} không được chiếu; "
        f"output_mapping có {sorted(mapped - declared)} không được khai báo."
    )


@pytest.mark.parametrize("path", _tool_documents(), ids=lambda p: p.stem)
def test_tool_ghi_side_effect_thi_phai_co_chien_luoc_idempotency(path: Path) -> None:
    """§2.5 và §15.1 — tác động ra bên ngoài phải có chiến lược idempotency.

    Một tool `write` mà `strategy: none` là một tool mà mỗi lần retry lại tạo
    thêm một bản sao. Đó là ràng buộc không thương lượng, nên nó phải bị chặn
    ở tầng tài liệu chứ không phải trong code review.
    """
    document = _load(path)
    if document["side_effect"] in {"write", "destructive"}:
        assert document["idempotency"]["strategy"] != "none", (
            f"{document['tool_id']} có side_effect {document['side_effect']!r} "
            "nhưng không khai chiến lược idempotency."
        )


@pytest.mark.parametrize("path", _connector_documents(), ids=lambda p: p.parent.name)
def test_connector_nhan_dung_nhung_tool_co_that(path: Path) -> None:
    """`connector.tools` phải khớp đúng tập tài liệu tool nằm cạnh nó.

    Registry cũng kiểm điều này lúc dựng, nhưng ở đó là lúc chạy. Kiểm tại tầng
    tài liệu thì một `tool_id` gõ sai đỏ ngay, không đợi tới lúc ai đó nạp
    catalog.
    """
    connector = _load(path)
    declared = set(connector["tools"])
    present = {_load(tool)["tool_id"] for tool in sorted(path.parent.glob("tools/*.json"))}
    assert declared == present, (
        f"connector khai {sorted(declared - present)} không có tài liệu; "
        f"tài liệu {sorted(present - declared)} không được connector nhận."
    )
