"""Tài liệu trong `packages/schemas/tool/` phải hợp lệ và khớp với code.

Đây là tầng kiểm cho **nguồn chân lý**. Test registry ở
`test_integration_registry.py` kiểm bản Python sinh ra; test này kiểm bản JSON
mà bản Python kia được sinh từ đó.

Đường dẫn tới `packages/` được suy ra từ vị trí file test. Ở đây làm vậy là an
toàn — test chỉ chạy trong repo đã checkout, và nếu bố cục repo đổi thì test
đỏ ngay và ồn ào. Code ứng dụng thì không được phép làm thế; xem docstring của
`app/domains/integration/infrastructure/specs.py`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, get_args

import jsonschema
import pytest

from app.domains.integration.domain.template import key_placeholders, placeholders
from app.domains.integration.domain.tools import (
    HttpMethod,
    IdempotencyStrategy,
    SideEffect,
    path_placeholders,
)
from app.domains.integration.infrastructure.specs import (
    CONNECTOR_META_SCHEMA,
    TOOL_META_SCHEMA,
    load_documents,
)

SCHEMA_ROOT = Path(__file__).resolve().parents[3] / "packages" / "schemas" / "tool"


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


#: Chuỗi mà một `pattern` của chỗ điền phải từ chối hết.
#:
#: `fill_path` percent-encode nên không cái nào trong số này *thoát* được ra
#: khỏi một segment kể cả khi lọt qua — nhưng một pattern nhận chúng là một
#: pattern không siết gì cả, và ADR-0005 nói rõ lớp tài liệu tồn tại để chặn
#: đúng loại tool đó.
_CHUOI_THU_DICH = (
    "",
    "..",
    "../secrets",
    "a/b",
    "a?b=1",
    "a#b",
    "a%2Fb",
    "a:b",
    "//ke-tan-cong.example",
    "http://ke-tan-cong.example/",
)


@pytest.mark.parametrize("path", _tool_documents(), ids=lambda p: p.stem)
def test_moi_cho_dien_trong_duong_dan_ung_voi_mot_truong_da_bi_siet(path: Path) -> None:
    """ADR-0005 — lớp chặn thứ hai, kiểm ở tầng tài liệu.

    Một `{x}` trong `request.path` phải là một property `required`, kiểu chuỗi,
    có `pattern`, và pattern ấy phải từ chối mọi chuỗi thù địch. Không có test
    này thì `{page_id}` khai `"type": "string"` trơn cũng nạp được, và thứ giữ
    an toàn còn lại đúng một dòng `quote()` trong driver.
    """
    document = _load(path)
    placeholders = path_placeholders(document["request"]["path"])
    if not placeholders:
        return

    schema = document["input_schema"]["json_schema"]
    required = set(schema.get("required", ()))
    properties = schema.get("properties", {})

    for name in placeholders:
        assert name in properties, f"{name!r} nằm trong path nhưng không có trong input_schema"
        assert name in required, f"{name!r} nằm trong path nên phải là trường bắt buộc"

        prop = properties[name]
        assert prop.get("type") == "string", f"{name!r} phải là chuỗi"

        pattern = prop.get("pattern")
        assert pattern, f"{name!r} nằm trong path nên phải có `pattern`"

        for hostile in _CHUOI_THU_DICH:
            assert re.fullmatch(pattern, hostile) is None, (
                f"`pattern` của {name!r} nhận {hostile!r} — nó không siết gì cả."
            )


@pytest.mark.parametrize("path", _tool_documents(), ids=lambda p: p.stem)
def test_input_mapping_phu_dung_cac_truong_cua_input_schema(path: Path) -> None:
    """Mặt gương của test `output_mapping` ở trên, và hỏng theo cùng hai kiểu.

    Một `{{ten}}` không có trong `input_schema` là một chỗ không bao giờ được
    điền — provider nhận về một thân thiếu trường và từ chối. Một property
    không được template nào đọc tới là một tham số mà LLM gửi đi rồi rơi vào
    hư không, im lặng.

    Trường nằm trong `request.path` được trừ ra: nó đã đi vào URL và driver
    loại nó khỏi thân trước khi dựng.
    """
    document = _load(path)
    template = document.get("input_mapping")
    if template is None:
        return

    schema = document["input_schema"]["json_schema"]
    declared = set(schema.get("properties", {})) - set(
        path_placeholders(document["request"]["path"])
    )
    used = set(placeholders(template))

    assert used <= declared, f"template đọc {sorted(used - declared)} không có trong input_schema"
    assert declared <= used, f"input_schema có {sorted(declared - used)} không template nào đọc tới"


def _properties_named(schema: Any, field: str) -> list[dict[str, Any]]:
    """Mọi khai báo của property `field` ở bất kỳ độ sâu nào, kể cả trong `$defs`.

    Đi tìm theo khoá `properties` chứ không theo tên: `"type": "string"` là từ
    khoá của JSON Schema, còn `properties.type` mới là một trường thật.
    """
    found: list[dict[str, Any]] = []
    if isinstance(schema, dict):
        declared = schema.get("properties")
        if isinstance(declared, dict) and isinstance(declared.get(field), dict):
            found.append(declared[field])
        for value in schema.values():
            found.extend(_properties_named(value, field))
    elif isinstance(schema, list):
        for value in schema:
            found.extend(_properties_named(value, field))
    return found


@pytest.mark.parametrize("path", _tool_documents(), ids=lambda p: p.stem)
def test_khoa_dong_chi_dung_truong_da_bi_gioi_han_bang_enum(path: Path) -> None:
    """Một placeholder ở vị trí KHOÁ quyết định hình dạng thân request.

    Driver đã có hai hàng rào lúc chạy — khoá phải là chuỗi không rỗng, và
    không ghi đè khoá hằng nào. Test này là lớp thứ hai, cùng vai trò với lớp
    tài liệu của ADR-0005: `enum` biến tập khoá mà người gọi tạo ra được thành
    một danh sách đóng, đọc được bằng mắt ngay trong tài liệu.

    Không có nó, `{{item.type}}` khai kiểu chuỗi trơn vẫn nạp được — và LLM sẽ
    đoán tên khối rồi nhận lỗi từ provider thay vì từ tầng validate của mình.
    """
    document = _load(path)
    template = document.get("input_mapping")
    if template is None:
        return

    schema = document["input_schema"]["json_schema"]
    for name in key_placeholders(template):
        field = name.rsplit(".", 1)[-1]
        declarations = _properties_named(schema, field)
        assert declarations, f"khoá động {{{{{name}}}}} không ứng với property nào"
        for declared in declarations:
            assert "enum" in declared, (
                f"{field!r} được dùng làm khoá động nên phải khai `enum` — "
                "nếu không, tập khoá của thân request là mở."
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


def test_enum_trong_meta_schema_khop_voi_enum_python() -> None:
    """Hai danh sách này phải khớp từng phần tử.

    Chúng nằm ở hai ngôn ngữ và không bên nào sinh ra bên kia, nên chỉ có test
    giữ chúng đồng bộ. Lệch nhau thì một tài liệu hợp lệ sẽ làm parser ném ra
    `ValueError` lúc sinh code — hoặc tệ hơn, một giá trị Python hợp lệ lại
    không viết được thành tài liệu.
    """
    meta = _load(SCHEMA_ROOT / TOOL_META_SCHEMA)

    assert meta["properties"]["side_effect"]["enum"] == [member.value for member in SideEffect]
    assert tuple(meta["$defs"]["providerRequest"]["properties"]["method"]["enum"]) == get_args(
        HttpMethod
    )
    assert meta["$defs"]["idempotencyPolicy"]["properties"]["strategy"]["enum"] == [
        member.value for member in IdempotencyStrategy
    ]


def test_cap_rate_limit_trong_meta_schema_khop_voi_domain() -> None:
    from app.domains.integration.domain.tools import RATE_LIMIT_LEVELS

    meta = _load(SCHEMA_ROOT / TOOL_META_SCHEMA)
    levels = tuple(meta["$defs"]["rateLimitProfile"]["properties"])
    assert levels == RATE_LIMIT_LEVELS


def test_nap_duoc_toan_bo_catalog_tu_json() -> None:
    """Đường mà `build_catalog.py` đi, chạy trong test."""
    connectors, tools = load_documents(SCHEMA_ROOT)

    assert [connector.connector_id for connector in connectors] == ["notion", "slack"]
    assert {tool.tool_id for tool in tools} == {
        "slack.list_channels",
        "slack.post_message",
        "slack.list_users",
        "slack.get_channel_history",
        "notion.search",
        "notion.list_users",
        "notion.get_page",
        "notion.get_block_children",
        "notion.query_database",
        "notion.create_page",
        "notion.append_blocks",
        "notion.create_comment",
    }


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
