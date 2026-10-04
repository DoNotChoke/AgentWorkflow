"""Parse và dựng lại Workflow IR. Không hạ tầng, không catalog."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from app.core.errors import WorkflowDefinitionError
from app.domains.workflow.domain.ir import (
    MAX_DOCUMENT_BYTES,
    ConditionNode,
    ConditionOp,
    Const,
    FromConnection,
    FromInput,
    FromItem,
    FromNode,
    LlmNode,
    LoopNode,
    ToolNode,
    TransformNode,
    TriggerNode,
    parse,
    to_document,
    upgrade_v1,
)

EXAMPLES = Path(__file__).resolve().parents[3] / "packages" / "schemas" / "workflow" / "examples"


def _example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.json")), ids=lambda p: p.stem)
def test_parse_roi_dung_lai_ra_dung_tai_lieu_cu(path: Path) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    ir = parse(document)
    assert to_document(ir) == document
    assert parse(to_document(ir)) == ir


def test_where_va_pick_thanh_kieu() -> None:
    ir = parse(_example("kb2-slack-doc-roi-ghi"))
    dang = ir.nodes[2]
    assert isinstance(dang, ToolNode)
    assert dang.input["channel"] == FromNode(
        node="kenh", path=("channels",), where={"name": FromInput("ten_kenh")}, pick=("id",)
    )


def test_loop_mang_than_filter_va_from_item() -> None:
    ir = parse(_example("kb3-notion-doc-sau"))
    loop = ir.nodes[3]
    assert isinstance(loop, LoopNode)
    assert loop.filter == {"type": Const("child_page")}
    assert loop.collect == "doc_trang"
    (inner,) = loop.body
    assert isinstance(inner, ToolNode)
    assert inner.input["page_id"] == FromItem(("id",))
    assert [(outer.id if outer else None, node.id) for outer, node in ir.walk()][-1] == (
        "moi_trang",
        "doc_trang",
    )


def test_node_llm_va_condition() -> None:
    assert isinstance(parse(_example("kb4-bac-cau")).nodes[2], LlmNode)
    condition = parse(_example("kb5-re-nhanh")).nodes[3]
    assert isinstance(condition, ConditionNode)
    assert condition.op is ConditionOp.GT
    assert condition.left == FromNode(node="dem", path=())
    assert condition.right == Const(5)
    assert isinstance(parse(_example("kb1-slack-ghi")).nodes[0], TriggerNode)


def test_major_khac_bi_tu_choi_bang_loi_noi_ro_ly_do() -> None:
    """Không để `pattern` của schema nói thay: "không khớp ^1\\." không bảo người
    đọc rằng tài liệu đến từ một runtime mới hơn (§39)."""
    document = _example("kb1-slack-ghi") | {"schema_version": "3.0"}
    with pytest.raises(WorkflowDefinitionError) as caught:
        parse(document)
    assert "major 1, 2" in caught.value.message
    assert caught.value.details["schema_version"] == "3.0"


# ─── IR v1 vẫn diễn giải được ────────────────────────────────────────────────


def _v1(document: dict[str, Any], connections: dict[str, str]) -> dict[str, Any]:
    return copy.deepcopy(document) | {"schema_version": "1.0", "connections": connections}


def test_v1_duoc_nang_len_v2_va_bo_connection() -> None:
    v1 = _v1(_example("kb3-notion-doc-sau"), {"notion": "conn_notionvidu"})
    ir = parse(v1)
    assert ir.schema_version == "2.0"
    assert to_document(ir) == _example("kb3-notion-doc-sau")


def test_from_connection_v1_tro_vao_node_tool_duy_nhat_cua_provider() -> None:
    v1 = _v1(_example("kb1-slack-ghi"), {"slack": "conn_slackvidu"})
    v1["nodes"][1]["input"]["text"] = {
        "from_connection": {"provider": "slack", "field": "external_account_label"}
    }
    dang = parse(v1).nodes[1]
    assert isinstance(dang, ToolNode)
    assert dang.input["text"] == FromConnection(node="dang", field="external_account_label")


def test_from_connection_v1_mo_ho_thi_khong_doan() -> None:
    v1 = _v1(_example("kb2-slack-doc-roi-ghi"), {"slack": "conn_slackvidu"})
    v1["nodes"][2]["input"]["text"] = {
        "from_connection": {"provider": "slack", "field": "external_account_label"}
    }
    with pytest.raises(WorkflowDefinitionError) as caught:
        parse(v1)
    assert caught.value.details == {"provider": "slack", "nodes": ["kenh", "dang"]}


def test_hang_so_giong_binding_khong_bi_nang_nham() -> None:
    v1 = _v1(_example("kb1-slack-ghi"), {})
    literal = {"from_connection": {"provider": "slack", "field": "x"}}
    v1["nodes"][1]["input"]["text"] = {"const": literal}
    assert upgrade_v1(v1)["nodes"][1]["input"]["text"] == {"const": literal}


def test_v1_sai_hinh_dang_bao_theo_schema_v1() -> None:
    v1 = _v1(_example("kb1-slack-ghi"), {"slack": "khong-phai-conn"})
    with pytest.raises(WorkflowDefinitionError, match="sai hình dạng"):
        parse(v1)


def test_node_chua_ho_tro_bi_tu_choi_va_goi_dung_ten() -> None:
    document = copy.deepcopy(_example("kb1-slack-ghi"))
    document["nodes"].append({"id": "cho", "type": "wait", "seconds": 30})
    with pytest.raises(WorkflowDefinitionError) as caught:
        parse(document)
    assert caught.value.details == {"path": "/nodes/2", "node": "cho", "type": "wait"}


def _transform(node_id: str = "doi", **overrides: Any) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "transform",
        "code": "return len(args['messages'])",
        "args": {"messages": {"from_node": {"node": "lich_su", "path": ["messages"]}}},
        "output_schema": {"type": "integer"},
    } | overrides


def test_node_transform_thanh_kieu() -> None:
    node = parse(_example("kb5-re-nhanh")).nodes[2]

    assert node == TransformNode(
        id="dem",
        code="return len(args['messages'])",
        args={"messages": FromNode(node="lich_su", path=("messages",))},
        output_schema={"type": "integer", "minimum": 0},
    )


def test_transform_nam_duoc_trong_than_loop() -> None:
    document = copy.deepcopy(_example("kb3-notion-doc-sau")) | {"schema_version": "2.1"}
    document["nodes"][3]["body"].append(
        _transform("rut", args={"trang": {"from_node": {"node": "doc_trang", "path": []}}})
    )

    loop = parse(document).nodes[3]

    assert isinstance(loop, LoopNode)
    assert isinstance(loop.body[1], TransformNode)


@pytest.mark.parametrize("version", ["2.0", "1.0"])
def test_transform_trong_tai_lieu_khai_version_cu_bi_tu_choi(version: str) -> None:
    """Trình đọc chỉ hiểu 2.0 tin vào con số đó; một tài liệu 2.0 không được chứa thứ
    chỉ có từ 2.1 (§41)."""
    document = copy.deepcopy(_example("kb1-slack-ghi")) | {"schema_version": version}
    document["nodes"].append(_transform())
    with pytest.raises(WorkflowDefinitionError) as caught:
        parse(document)
    assert caught.value.details["type"] == "transform"
    assert caught.value.details["since"] == "2.1"


def test_sai_hinh_dang_bao_duong_tro_toi_cho_sai() -> None:
    document = copy.deepcopy(_example("kb1-slack-ghi"))
    document["nodes"][1]["tool_version"] = "mot"
    with pytest.raises(WorkflowDefinitionError) as caught:
        parse(document)
    paths = [error["path"] for error in caught.value.details["errors"]]
    assert any(path.startswith("/nodes/1") for path in paths)


def test_tai_lieu_qua_tran_bi_tu_choi_truoc_moi_buoc_khac() -> None:
    document = copy.deepcopy(_example("kb4-bac-cau"))
    document["nodes"][2]["instructions"] = "x" * 19_000
    document["nodes"] += [
        {**document["nodes"][2], "id": f"llm_{n}"} for n in range(MAX_DOCUMENT_BYTES // 19_000)
    ]
    with pytest.raises(WorkflowDefinitionError) as caught:
        parse(document)
    assert caught.value.details["max_bytes"] == MAX_DOCUMENT_BYTES
