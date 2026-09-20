"""`packages/schemas/workflow/` phải hợp lệ và khớp với bản Python sinh ra.

Đường tới `packages/` suy ra từ vị trí file test — an toàn ở test, không ở code
ứng dụng (xem `packages/schemas/tool/README.md`).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from app.domains.workflow.domain.generated.ir_v1 import IR_SCHEMA

SCHEMA_ROOT = Path(__file__).resolve().parents[3] / "packages" / "schemas" / "workflow"
VALIDATOR = jsonschema.Draft202012Validator(IR_SCHEMA)


def _examples() -> list[Path]:
    return sorted((SCHEMA_ROOT / "examples").glob("*.json"))


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_thu_muc_va_vi_du_ton_tai() -> None:
    """`parametrize` trên danh sách rỗng không chạy gì — trông y hệt thành công."""
    assert (SCHEMA_ROOT / "ir.schema.json").is_file()
    assert len(_examples()) == 6


def test_ban_python_khop_voi_json() -> None:
    assert _load(SCHEMA_ROOT / "ir.schema.json") == IR_SCHEMA


def test_schema_tu_no_la_json_schema_hop_le() -> None:
    jsonschema.Draft202012Validator.check_schema(IR_SCHEMA)


@pytest.mark.parametrize("path", _examples(), ids=lambda p: p.stem)
def test_vi_du_cua_sau_kich_ban_dung_hinh_dang(path: Path) -> None:
    errors = [error.message for error in VALIDATOR.iter_errors(_load(path))]
    assert errors == []


def _base() -> dict[str, Any]:
    return _load(SCHEMA_ROOT / "examples" / "kb1-slack-ghi.json")


def _broken(mutate: Any) -> dict[str, Any]:
    document = copy.deepcopy(_base())
    mutate(document)
    return document


@pytest.mark.parametrize(
    ("mutate", "vi_sao"),
    [
        (lambda d: d.pop("edges"), "thiếu edges"),
        (lambda d: d.update(schema_version="1"), "schema_version không có minor"),
        (
            lambda d: d["nodes"][1]["input"].update(text={"const": 1, "from_input": "x"}),
            "binding hai dạng",
        ),
        (
            lambda d: d["nodes"][1]["input"].update(
                text={"from_node": {"node": "t", "path": ["a"], "pick": ["b"]}}
            ),
            "pick không kèm where",
        ),
        (lambda d: d["nodes"][1].update(id="Dang"), "id viết hoa"),
        (lambda d: d["edges"][0].update(branch="maybe"), "nhánh lạ"),
        (lambda d: d["nodes"][1].update(tool_version="1"), "tool_version không có minor"),
        (
            lambda d: d["nodes"].append(
                {
                    "id": "c",
                    "type": "condition",
                    "left": {"const": 1},
                    "op": "exists",
                    "right": {"const": 2},
                }
            ),
            "exists kèm right",
        ),
        (
            lambda d: d["nodes"].append(
                {"id": "c", "type": "condition", "left": {"const": 1}, "op": "eq"}
            ),
            "eq thiếu right",
        ),
        (lambda d: d.update(connections={"slack": "khong-phai-conn"}), "connection id sai dạng"),
    ],
)
def test_hinh_dang_sai_bi_tu_choi(mutate: Any, vi_sao: str) -> None:
    assert list(VALIDATOR.iter_errors(_broken(mutate))), vi_sao


def _loop(**overrides: Any) -> dict[str, Any]:
    loop: dict[str, Any] = {
        "id": "lap",
        "type": "loop",
        "over": {"const": []},
        "max_items": 10,
        "body": [
            {
                "id": "b",
                "type": "tool",
                "tool_id": "slack.post_message",
                "tool_version": "1.0",
                "input": {"text": {"from_item": []}},
            }
        ],
        "collect": "b",
    }
    loop.update(overrides)
    return loop


def test_loop_vuot_tran_bi_tu_choi() -> None:
    document = _broken(lambda d: d["nodes"].append(_loop(max_items=201)))
    assert list(VALIDATOR.iter_errors(document))


@pytest.mark.parametrize("kind", ["trigger", "condition", "loop"])
def test_than_loop_khong_chua_node_dieu_khien(kind: str) -> None:
    """Thân loop v1 là dãy thẳng. Node đã biết mà sai chỗ không được lọt qua cửa
    "node chưa biết"."""
    inner = {"id": "b", "type": kind}
    document = _broken(lambda d: d["nodes"].append(_loop(body=[inner])))
    assert list(VALIDATOR.iter_errors(document))


def test_transform_la_mot_loai_chua_biet_o_v1() -> None:
    """`transform` chạy code là node của WP-C. Ở v1 nó chưa tồn tại, nên schema
    đối xử với nó như mọi loại chưa biết — cho qua, để `parse` từ chối."""
    document = _broken(
        lambda d: d["nodes"].append({"id": "doi", "type": "transform", "code": "return 1"})
    )
    assert list(VALIDATOR.iter_errors(document)) == []


def test_node_chua_biet_van_qua_schema() -> None:
    """Q2: editor và compiler phải mở được tài liệu có node mà định dạng này chưa
    hiểu. Domain mới là chỗ từ chối nó."""
    document = _broken(lambda d: d["nodes"].append({"id": "cho", "type": "wait", "seconds": 30}))
    assert list(VALIDATOR.iter_errors(document)) == []
