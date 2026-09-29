"""Thứ tự chạy và NodePath. Không hạ tầng."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.core.errors import WorkflowDefinitionError
from app.domains.workflow.domain.ir import parse
from app.domains.workflow.domain.plan import NodePath, build_plan

EXAMPLES = Path(__file__).resolve().parents[3] / "packages" / "schemas" / "workflow" / "examples"

_TRIGGER = {
    "id": "t",
    "type": "trigger",
    "trigger": "manual",
    "input_schema": {"type": "object", "properties": {}},
}


def _example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _tool(node_id: str) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "tool",
        "tool_id": "slack.list_channels",
        "tool_version": "1.0",
        "input": {},
    }


def _graph(nodes: list[str], edges: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "nodes": [_TRIGGER, *(_tool(node_id) for node_id in nodes)],
        "edges": [{"from": source, "to": target} for source, target in edges],
    }


@pytest.mark.parametrize(
    ("name", "order"),
    [
        ("kb1-slack-ghi", ("t", "dang")),
        ("kb2-slack-doc-roi-ghi", ("t", "kenh", "dang")),
        ("kb3-notion-doc-sau", ("t", "tim", "con", "moi_trang")),
        ("kb4-bac-cau", ("t", "doc", "tom_tat", "tao")),
        ("kb5-re-nhanh", ("t", "lich_su", "du_nhieu", "dang")),
        ("kb6-hong-giua-chung", ("t", "doc")),
    ],
)
def test_thu_tu_chay_cua_sau_kich_ban(name: str, order: tuple[str, ...]) -> None:
    assert build_plan(parse(_example(name))).order == order


def test_hoa_thi_theo_thu_tu_khai_bao_khong_theo_ten() -> None:
    plan = build_plan(parse(_graph(["zeta", "alpha"], [("t", "zeta"), ("t", "alpha")])))
    assert plan.order == ("t", "zeta", "alpha")


def test_node_khai_truoc_van_doi_tien_boi_cua_no() -> None:
    document = _graph(["gop", "a", "b"], [("t", "a"), ("t", "b"), ("a", "gop"), ("b", "gop")])
    assert build_plan(parse(document)).order == ("t", "a", "b", "gop")


def test_runnable_doi_du_moi_tien_boi() -> None:
    document = _graph(["a", "b", "gop"], [("t", "a"), ("t", "b"), ("a", "gop"), ("b", "gop")])
    plan = build_plan(parse(document))

    assert plan.runnable(set()) == ("t",)
    assert plan.runnable({"t"}) == ("a", "b")
    assert plan.runnable({"t", "a"}) == ("b",)
    assert plan.runnable({"t", "a", "b"}) == ("gop",)
    assert plan.runnable({"t", "a", "b", "gop"}) == ()


def test_ir_sai_cau_truc_khong_dung_duoc_plan() -> None:
    document = _graph(["a", "b"], [("t", "a"), ("a", "b"), ("b", "a")])
    with pytest.raises(WorkflowDefinitionError):
        build_plan(parse(document))


def test_ten_node_trong_than_loop_mang_chi_so_luot() -> None:
    plan = build_plan(parse(_example("kb3-notion-doc-sau")))
    paths = [str(path) for turn in range(3) for path in plan.iteration_paths("moi_trang", turn)]
    assert paths == [
        "moi_trang[0].doc_trang",
        "moi_trang[1].doc_trang",
        "moi_trang[2].doc_trang",
    ]


def test_body_cua_node_khong_phai_loop_la_loi() -> None:
    plan = build_plan(parse(_example("kb3-notion-doc-sau")))
    with pytest.raises(WorkflowDefinitionError):
        plan.body("tim")


@pytest.mark.parametrize("text", ["dang", "moi_trang[0].doc_trang", "n3[12].b1"])
def test_node_path_doc_lai_dung_chuoi_cua_no(text: str) -> None:
    assert str(NodePath.parse(text)) == text


def test_node_path_tang_ngoai_va_trong_loop() -> None:
    assert NodePath.parse("dang") == NodePath("dang")
    assert NodePath.parse("n3[2].b1") == NodePath.in_loop("n3", 2, "b1")


@pytest.mark.parametrize("text", ["", "n3[].b1", "n3[-1].b1", "n3[1]", "N3", "a.b", "n3[1].b1.c"])
def test_node_path_sai_dinh_dang(text: str) -> None:
    with pytest.raises(ValueError, match="NodePath"):
        NodePath.parse(text)


def test_node_path_loop_va_luot_di_cung_nhau() -> None:
    with pytest.raises(ValueError):
        NodePath("b1", loop="n3")
    with pytest.raises(ValueError):
        NodePath("b1", iteration=0)
    with pytest.raises(ValueError):
        NodePath.in_loop("n3", -1, "b1")
