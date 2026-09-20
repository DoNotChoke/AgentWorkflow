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
    FromInput,
    FromItem,
    FromNode,
    LlmNode,
    LoopNode,
    ToolNode,
    TriggerNode,
    parse,
    to_document,
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
    condition = parse(_example("kb5-re-nhanh")).nodes[2]
    assert isinstance(condition, ConditionNode)
    assert condition.op is ConditionOp.EXISTS
    assert condition.right is None
    assert isinstance(parse(_example("kb1-slack-ghi")).nodes[0], TriggerNode)


def test_major_khac_bi_tu_choi_bang_loi_noi_ro_ly_do() -> None:
    """Không để `pattern` của schema nói thay: "không khớp ^1\\." không bảo người
    đọc rằng tài liệu đến từ một runtime mới hơn (§39)."""
    document = _example("kb1-slack-ghi") | {"schema_version": "2.0"}
    with pytest.raises(WorkflowDefinitionError) as caught:
        parse(document)
    assert "major 1" in caught.value.message
    assert caught.value.details["schema_version"] == "2.0"


def test_node_chua_ho_tro_bi_tu_choi_va_goi_dung_ten() -> None:
    document = copy.deepcopy(_example("kb1-slack-ghi"))
    document["nodes"].append({"id": "cho", "type": "wait", "seconds": 30})
    with pytest.raises(WorkflowDefinitionError) as caught:
        parse(document)
    assert caught.value.details == {"path": "/nodes/2", "node": "cho", "type": "wait"}


def test_transform_bi_tu_choi_o_v1() -> None:
    """Node `transform` chạy code thuộc WP-C. Cho tới lúc đó, mở một tài liệu có
    nó phải nói rõ là chưa chạy được, không phải im lặng bỏ qua."""
    document = copy.deepcopy(_example("kb1-slack-ghi"))
    document["nodes"].append({"id": "doi", "type": "transform", "code": "return 1"})
    with pytest.raises(WorkflowDefinitionError) as caught:
        parse(document)
    assert caught.value.details["type"] == "transform"


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
