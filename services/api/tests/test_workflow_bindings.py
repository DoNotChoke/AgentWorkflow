"""Binding → giá trị thật. Không hạ tầng."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.core.errors import WorkflowExecutionError
from app.domains.workflow.domain.bindings import (
    Missing,
    Scope,
    check_inputs,
    lookup,
    loop_items,
    resolve,
    resolve_inputs,
)
from app.domains.workflow.domain.ir import (
    Const,
    FromConnection,
    FromInput,
    FromItem,
    FromNode,
    LoopNode,
    ToolNode,
    TriggerNode,
    parse,
)

EXAMPLES = Path(__file__).resolve().parents[3] / "packages" / "schemas" / "workflow" / "examples"

KENH = {
    "channels": [
        {"id": "C1", "name": "general"},
        {"id": "C2", "name": "test"},
        {"id": "C3", "name": "test"},
    ]
}
KHOI_CON = {
    "results": [
        {"id": "p1", "type": "child_page"},
        {"id": "x1", "type": "paragraph"},
        {"id": "p2", "type": "child_page"},
        {"id": "x2", "type": "heading_1"},
        {"id": "p3", "type": "child_page"},
    ]
}


def _example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _scope(**outputs: Any) -> Scope:
    return Scope(inputs={"ten_kenh": "test", "noi_dung": "xin chào"}, outputs=outputs)


def _loop(max_items: int = 50, **overrides: Any) -> LoopNode:
    loop = parse(_example("kb3-notion-doc-sau")).nodes[3]
    assert isinstance(loop, LoopNode)
    return LoopNode(
        id=loop.id,
        over=overrides.get("over", loop.over),
        max_items=max_items,
        body=loop.body,
        collect=loop.collect,
        filter=overrides.get("filter", loop.filter),
    )


# ─── Năm dạng binding ────────────────────────────────────────────────────────


def test_const_la_chinh_no() -> None:
    assert lookup(Const({"a": [1]}), _scope()) == {"a": [1]}


def test_from_input() -> None:
    assert lookup(FromInput("ten_kenh"), _scope()) == "test"
    assert isinstance(lookup(FromInput("khong_co"), _scope()), Missing)


def test_from_node_di_vao_mang_bang_chi_so() -> None:
    binding = FromNode(node="kenh", path=("channels", "1", "id"))
    assert lookup(binding, _scope(kenh=KENH)) == "C2"


def test_from_node_giu_nguyen_none_cua_provider() -> None:
    binding = FromNode(node="doc", path=("cursor",))
    assert lookup(binding, _scope(doc={"cursor": None})) is None


def test_where_lay_phan_tu_dau_tien_khop_roi_pick() -> None:
    dang = parse(_example("kb2-slack-doc-roi-ghi")).nodes[2]
    assert isinstance(dang, ToolNode)
    assert lookup(dang.input["channel"], _scope(kenh=KENH)) == "C2"


def test_where_khong_pick_tra_ca_phan_tu() -> None:
    binding = FromNode(node="kenh", path=("channels",), where={"name": Const("general")})
    assert lookup(binding, _scope(kenh=KENH)) == {"id": "C1", "name": "general"}


def test_from_item_trong_than_loop() -> None:
    scope = _scope().with_item({"id": "p2", "meta": {"tac_gia": "An"}}, outputs={})
    assert lookup(FromItem(("id",)), scope) == "p2"
    assert lookup(FromItem(("meta", "tac_gia")), scope) == "An"
    assert lookup(FromItem(()), scope) == {"id": "p2", "meta": {"tac_gia": "An"}}


def test_from_item_ngoai_than_loop_la_khong_co() -> None:
    assert isinstance(lookup(FromItem(("id",)), _scope()), Missing)


def test_from_connection_doc_metadata_cua_connection_ma_node_dung() -> None:
    scope = Scope(
        inputs={},
        outputs={},
        connections={"kenh": {"workspace_name": "Acme"}, "dang": {"workspace_name": "Beta"}},
    )
    assert lookup(FromConnection(node="kenh", field="workspace_name"), scope) == "Acme"
    assert lookup(FromConnection(node="dang", field="workspace_name"), scope) == "Beta"
    assert isinstance(lookup(FromConnection(node="tim", field="workspace_name"), scope), Missing)


# ─── Không có giá trị ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("binding", "reached"),
    [
        (FromNode(node="kenh", path=("channels", "9", "id")), ("channels",)),
        (FromNode(node="kenh", path=("channels", "0", "topic")), ("channels", "0")),
        (FromNode(node="kenh", path=("channels", "id")), ("channels",)),
        (FromNode(node="kenh", path=("khong_co",)), ()),
    ],
)
def test_duong_hut_noi_ro_di_duoc_toi_dau(binding: FromNode, reached: tuple[str, ...]) -> None:
    found = lookup(binding, _scope(kenh=KENH))
    assert isinstance(found, Missing)
    assert found.reached == reached


def test_node_chua_chay_thi_khong_co_output() -> None:
    found = lookup(FromNode(node="kenh", path=("channels",)), _scope())
    assert isinstance(found, Missing)
    assert "kenh" in found.reason


def test_where_khong_khop_noi_ro_truong_va_gia_tri() -> None:
    binding = FromNode(node="kenh", path=("channels",), where={"name": Const("khong-co")})
    found = lookup(binding, _scope(kenh=KENH))
    assert isinstance(found, Missing)
    assert "name = 'khong-co'" in found.reason


def test_where_tren_thu_khong_phai_mang() -> None:
    binding = FromNode(node="kenh", path=("channels", "0"), where={"name": Const("general")})
    found = lookup(binding, _scope(kenh=KENH))
    assert isinstance(found, Missing)
    assert "mảng" in found.reason


def test_where_so_bang_khong_ep_kieu() -> None:
    binding = FromNode(node="so", path=("items",), where={"id": Const("1")})
    assert isinstance(lookup(binding, _scope(so={"items": [{"id": 1}]})), Missing)


def test_resolve_bien_khong_co_thanh_loi_cua_lan_chay() -> None:
    binding = FromNode(node="kenh", path=("channels", "9", "id"))
    with pytest.raises(WorkflowExecutionError) as caught:
        resolve(binding, _scope(kenh=KENH), node="dang", at=("input", "channel"))
    assert caught.value.message.startswith("dang.input.channel: ")
    assert caught.value.details == {
        "node": "dang",
        "field": ["input", "channel"],
        "reached": ["channels"],
    }
    assert not caught.value.retryable


# ─── Input của node ──────────────────────────────────────────────────────────


def test_resolve_inputs_cua_kich_ban_hai() -> None:
    dang = parse(_example("kb2-slack-doc-roi-ghi")).nodes[2]
    assert isinstance(dang, ToolNode)
    assert resolve_inputs(dang.input, _scope(kenh=KENH), node="dang") == {
        "channel": "C2",
        "text": "xin chào",
    }


def test_input_tuy_chon_khong_truyen_thi_bo_truong() -> None:
    bindings = {"text": FromInput("noi_dung"), "thread_ts": FromInput("tra_loi")}
    assert resolve_inputs(bindings, _scope(), node="dang") == {"text": "xin chào"}


def test_from_node_hut_trong_input_van_la_loi() -> None:
    bindings = {"channel": FromNode(node="kenh", path=("channels", "9", "id"))}
    with pytest.raises(WorkflowExecutionError):
        resolve_inputs(bindings, _scope(kenh=KENH), node="dang")


# ─── Phần tử của loop ────────────────────────────────────────────────────────


def test_filter_chi_giu_trang_con() -> None:
    items = loop_items(_loop(), _scope(con=KHOI_CON))
    assert [item["id"] for item in items] == ["p1", "p2", "p3"]


def test_khong_filter_thi_giu_het() -> None:
    assert len(loop_items(_loop(filter=None), _scope(con=KHOI_CON))) == 5


def test_max_items_dem_sau_khi_loc() -> None:
    assert len(loop_items(_loop(max_items=3), _scope(con=KHOI_CON))) == 3


def test_vuot_max_items_la_loi_khong_cat() -> None:
    with pytest.raises(WorkflowExecutionError) as caught:
        loop_items(_loop(max_items=2), _scope(con=KHOI_CON))
    assert caught.value.details == {"node": "moi_trang", "count": 3, "max_items": 2}


def test_mang_rong_la_khong_luot_nao() -> None:
    assert loop_items(_loop(), _scope(con={"results": []})) == ()


def test_over_khong_phai_mang_la_loi() -> None:
    with pytest.raises(WorkflowExecutionError, match="không phải mảng"):
        loop_items(_loop(), _scope(con={"results": "khong"}))


def test_phan_tu_khong_phai_object_khong_qua_filter() -> None:
    items = loop_items(_loop(), _scope(con={"results": ["chuoi", 3, {"type": "child_page"}]}))
    assert items == ({"type": "child_page"},)


# ─── Input của lần chạy ──────────────────────────────────────────────────────


def _trigger() -> TriggerNode:
    trigger = parse(_example("kb2-slack-doc-roi-ghi")).nodes[0]
    assert isinstance(trigger, TriggerNode)
    return trigger


def test_input_khop_schema_thi_qua() -> None:
    check_inputs(_trigger(), {"ten_kenh": "test", "noi_dung": "xin chào"})


def test_thieu_input_bat_buoc_la_loi_cua_lan_chay() -> None:
    with pytest.raises(WorkflowExecutionError, match="ten_kenh") as caught:
        check_inputs(_trigger(), {"noi_dung": "xin chào"})
    assert caught.value.details["node"] == "t"
    assert not caught.value.retryable


def test_input_sai_kieu_noi_ro_cho_sai() -> None:
    with pytest.raises(WorkflowExecutionError, match="ten_kenh") as caught:
        check_inputs(_trigger(), {"ten_kenh": 3, "noi_dung": "xin chào"})
    assert caught.value.details["path"] == ["ten_kenh"]
