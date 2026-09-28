"""Luật §39 của Workflow IR — phần thuần và phần cần catalog.

Catalog ở đây là catalog tool THẬT, dựng qua một adapter viết trong test. Thư mục
`tests/` nằm ngoài `root_packages` của import-linter nên nhìn được cả hai domain;
code ứng dụng thì không (contract 1).
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.core.errors import WorkflowDefinitionError
from app.domains.integration.infrastructure.catalog_view import tool_rows
from app.domains.workflow.application.ports import BoundConnection, ToolContract
from app.domains.workflow.application.validation import validate, validate_connections
from app.domains.workflow.domain.ir import parse
from app.domains.workflow.domain.validation import (
    LiteralSite,
    Severity,
    ValidationReport,
    ensure_valid,
    literals,
    validate_structure,
)
from app.domains.workflow.infrastructure.catalog import RowToolCatalog

EXAMPLES = Path(__file__).resolve().parents[3] / "packages" / "schemas" / "workflow" / "examples"


#: Adapter thật của ứng dụng, không phải bản tự viết trong test: nếu hai bản
#: lệch nhau thì test này xanh trong khi publish thật đỏ.
CATALOG = RowToolCatalog(tool_rows())


def _example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _codes(report: ValidationReport, severity: Severity = Severity.ERROR) -> list[str]:
    return [issue.code for issue in report.issues if issue.severity is severity]


def _structure(document: dict[str, Any]) -> ValidationReport:
    return validate_structure(parse(document))


def _full(document: dict[str, Any]) -> ValidationReport:
    return validate(parse(document), CATALOG)


def _flow(*nodes: dict[str, Any], edges: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    """Workflow thẳng: t → nodes[0] → nodes[1] → … trừ khi truyền `edges`."""
    trigger = {
        "id": "t",
        "type": "trigger",
        "trigger": "manual",
        "input_schema": {"type": "object", "properties": {"ten": {"type": "string"}}},
    }
    chain = ["t", *(node["id"] for node in nodes)]
    pairs = edges if edges is not None else list(zip(chain, chain[1:], strict=False))
    return {
        "schema_version": "1.0",
        "nodes": [trigger, *nodes],
        "edges": [{"from": a, "to": b} for a, b in pairs],
    }


def _llm(node_id: str, **inputs: Any) -> dict[str, Any]:
    """Node trung tính cho test cấu trúc: không cần catalog, và `output_schema`
    khai sẵn nên đường `from_node` vào nó vẫn kiểm được."""
    return {
        "id": node_id,
        "type": "llm",
        "model": "claude-sonnet-5",
        "instructions": "Không làm gì.",
        "input": inputs or {"x": {"const": 1}},
        "output_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"x": {"type": "integer"}},
        },
    }


def _tool(node_id: str, **inputs: Any) -> dict[str, Any]:
    """Node cho thân loop — `llm` trong thân sẽ kéo theo cảnh báo `llm_trong_loop`."""
    return {
        "id": node_id,
        "type": "tool",
        "tool_id": "slack.post_message",
        "tool_version": "1.0",
        "input": inputs or {"channel": {"const": "C0VIDU0001"}, "text": {"const": "x"}},
    }


def _from(node: str, *path: str, **extra: Any) -> dict[str, Any]:
    return {"from_node": {"node": node, "path": list(path), **extra}}


# ─── Sáu kịch bản của GĐ 0 ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "warnings"),
    [
        ("kb1-slack-ghi", []),
        ("kb2-slack-doc-roi-ghi", []),
        ("kb3-notion-doc-sau", ["chi_so_tho"]),
        ("kb4-bac-cau", []),
        ("kb5-re-nhanh", ["chi_so_tho"]),
        ("kb6-hong-giua-chung", []),
    ],
)
def test_sau_kich_ban_hop_le_voi_catalog_that(name: str, warnings: list[str]) -> None:
    report = _full(_example(name))
    assert _codes(report) == []
    assert _codes(report, Severity.WARNING) == warnings


# ─── Đồ thị ──────────────────────────────────────────────────────────────────


def test_dung_mot_trigger() -> None:
    document = _flow(_llm("a"))
    document["nodes"].append({**document["nodes"][0], "id": "t2"})
    assert "trigger_so_luong" in _codes(_structure(document))


def test_id_trong_than_loop_khong_duoc_trung_voi_tang_ngoai() -> None:
    loop = {
        "id": "lap",
        "type": "loop",
        "over": {"const": [1]},
        "max_items": 5,
        "body": [_tool("a")],
        "collect": "a",
    }
    assert "id_trung" in _codes(_structure(_flow(_llm("a"), loop)))


def test_canh_tro_vao_node_khong_co() -> None:
    assert "canh_node_khong_ton_tai" in _codes(
        _structure(_flow(_llm("a"), edges=[("t", "a"), ("a", "ma")]))
    )


def test_khong_canh_nao_vao_trigger() -> None:
    assert "canh_vao_trigger" in _codes(
        _structure(_flow(_llm("a"), edges=[("t", "a"), ("a", "t")]))
    )


def test_chu_trinh_bi_bat_va_luat_binding_khong_vo() -> None:
    document = _flow(
        _llm("a", x=_from("b", "x")),
        _llm("b", x=_from("a", "x")),
        edges=[("t", "a"), ("a", "b"), ("b", "a")],
    )
    codes = _codes(_structure(document))
    assert "chu_trinh" in codes
    assert not [code for code in codes if code.startswith("binding_")]


def test_node_khong_toi_duoc_tu_trigger() -> None:
    document = _flow(_llm("a"), _llm("mo_coi"), edges=[("t", "a")])
    report = _structure(document)
    assert "node_mo_coi" in _codes(report)
    assert report.errors[0].node == "mo_coi"


def test_canh_ra_tu_condition_phai_co_nhanh_va_nguoc_lai() -> None:
    condition = {"id": "c", "type": "condition", "left": {"const": 1}, "op": "exists"}
    document = _flow(condition, _llm("a"))
    codes = _codes(_structure(document))
    assert "nhanh_thieu" in codes

    document = _flow(_llm("a"), _llm("b"))
    document["edges"][1]["branch"] = "true"
    assert "nhanh_thua" in _codes(_structure(document))


def test_condition_khong_dan_toi_dau() -> None:
    condition = {"id": "c", "type": "condition", "left": {"const": 1}, "op": "exists"}
    assert "condition_khong_loi_ra" in _codes(_structure(_flow(condition)))


def test_input_schema_cua_trigger_phai_la_json_schema() -> None:
    document = _flow(_llm("a"))
    document["nodes"][0]["input_schema"] = {"type": "khong_co_kieu_nay"}
    assert "json_schema_sai" in _codes(_structure(document))


# ─── Loop ────────────────────────────────────────────────────────────────────


def _loop(body: list[dict[str, Any]], collect: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": "lap",
        "type": "loop",
        "over": {"const": [{"id": "1"}]},
        "max_items": 5,
        "body": body,
        "collect": collect,
        **extra,
    }


def test_collect_phai_la_node_trong_than() -> None:
    assert "collect_khong_trong_than" in _codes(_structure(_flow(_loop([_tool("b")], collect="t"))))


def test_llm_trong_loop_la_canh_bao_khong_phai_loi() -> None:
    llm = {
        "id": "hoi",
        "type": "llm",
        "model": "claude-sonnet-5",
        "instructions": "Tóm tắt.",
        "input": {"x": {"from_item": []}},
        "output_schema": {"type": "object"},
    }
    report = _structure(_flow(_loop([llm], collect="hoi")))
    assert _codes(report) == []
    assert _codes(report, Severity.WARNING) == ["llm_trong_loop"]


def test_than_loop_doc_node_truoc_trong_than_nhung_khong_doc_node_sau() -> None:
    body = [
        _tool("dau", x={"from_item": ["id"]}),
        _tool("giua", x=_from("dau", "x"), y=_from("cuoi", "x")),
        _tool("cuoi"),
    ]
    report = _structure(_flow(_loop(body, collect="cuoi")))
    assert _codes(report) == ["binding_node_khong_o_phia_tren"]
    assert report.errors[0].field == ("input", "y")


def test_ngoai_than_loop_chi_doc_duoc_output_cua_loop() -> None:
    after = _llm("sau", x=_from("b", "x"), y=_from("lap", "0", "x"))
    report = _structure(_flow(_loop([_tool("b")], collect="b"), after))
    assert _codes(report) == ["binding_doc_than_loop"]
    assert _codes(report, Severity.WARNING) == ["chi_so_tho"]


def test_than_loop_khong_doc_chinh_loop_bao_no() -> None:
    body = [_tool("b", x=_from("lap", "0"))]
    assert "binding_node_khong_o_phia_tren" in _codes(_structure(_flow(_loop(body, collect="b"))))


def test_from_item_ngoai_than_loop_va_trong_filter_deu_sai() -> None:
    report = _structure(
        _flow(
            _llm("a", x={"from_item": []}),
            _loop([_tool("b")], collect="b", filter={"id": {"from_item": ["id"]}}),
        )
    )
    assert _codes(report) == ["from_item_sai_cho", "from_item_sai_cho"]
    assert "trong filter" in report.errors[1].message


# ─── Binding ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("reader", "code"),
    [
        (_llm("b", x=_from("khong_co", "x")), "binding_node_khong_ton_tai"),
        (_llm("b", x=_from("t", "ten")), "binding_doc_trigger"),
        (_llm("b", x={"from_input": "tuoi"}), "from_input_khong_khai"),
    ],
)
def test_binding_tro_sai_cho(reader: dict[str, Any], code: str) -> None:
    assert _codes(_structure(_flow(_llm("a"), reader))) == [code]


def test_doc_condition_khong_co_output() -> None:
    condition = {"id": "c", "type": "condition", "left": {"const": 1}, "op": "exists"}
    document = _flow(condition, _llm("a", x=_from("c", "x")))
    document["edges"][1]["branch"] = "true"
    assert _codes(_structure(document)) == ["binding_doc_condition"]


def test_nhanh_ben_canh_khong_phai_phia_tren() -> None:
    """a và b cùng đi ra từ t; b không chắc a đã chạy xong khi tới lượt nó."""
    document = _flow(_llm("a"), _llm("b", x=_from("a", "x")), edges=[("t", "a"), ("t", "b")])
    assert _codes(_structure(document)) == ["binding_node_khong_o_phia_tren"]


def test_ensure_valid_nem_khi_co_loi_va_bo_qua_canh_bao() -> None:
    ensure_valid(_structure(_example("kb3-notion-doc-sau")))

    with pytest.raises(WorkflowDefinitionError) as caught:
        ensure_valid(_structure(_flow(_llm("a", x={"from_input": "tuoi"}))))
    (issue,) = caught.value.details["issues"]
    assert issue["code"] == "from_input_khong_khai"
    assert issue["field"] == ["input", "x"]


# ─── Literal (ADR-0008, quyết định 3) ────────────────────────────────────────


def test_literal_trong_filter_cua_loop() -> None:
    assert literals(parse(_example("kb3-notion-doc-sau"))) == (
        LiteralSite(node=("moi_trang",), field=("filter", "type"), value="child_page"),
    )


def test_literal_trong_than_loop_mang_duong_hai_cap() -> None:
    ir = parse(_flow(_loop([_tool("b", x={"const": "C1"})], collect="b")))
    assert LiteralSite(node=("lap", "b"), field=("input", "x"), value="C1") in literals(ir)


def test_literal_trong_where() -> None:
    document = copy.deepcopy(_example("kb2-slack-doc-roi-ghi"))
    where = document["nodes"][2]["input"]["channel"]["from_node"]["where"]
    where["name"] = {"const": "test"}
    assert literals(parse(document)) == (
        LiteralSite(node=("dang",), field=("input", "channel", "where", "name"), value="test"),
    )


def test_literal_so_nguyen_van_la_literal() -> None:
    """GĐ 0: `limit: 10` từng bị bộ so khớp bỏ sót vì là số."""
    values = {site.field: site.value for site in literals(parse(_example("kb4-bac-cau")))}
    assert values[("input", "limit")] == 10


# ─── Catalog ─────────────────────────────────────────────────────────────────


def _kb(name: str) -> dict[str, Any]:
    return copy.deepcopy(_example(name))


def test_tool_khong_co_ban_do_goi_ten_ban_dang_co() -> None:
    document = _kb("kb1-slack-ghi")
    document["nodes"][1]["tool_version"] = "9.9"
    (issue,) = _full(document).errors
    assert issue.code == "tool_khong_ton_tai"
    assert issue.field == ("tool_version",)
    assert "1.0" in issue.message


def test_tool_khong_ton_tai_chi_vao_tool_id() -> None:
    document = _kb("kb1-slack-ghi")
    document["nodes"][1]["tool_id"] = "slack.khong_co"
    (issue,) = [i for i in _full(document).errors if i.code == "tool_khong_ton_tai"]
    assert issue.field == ("tool_id",)


def test_tool_can_phe_duyet_bi_chan_vi_v1_khong_co_node_phe_duyet() -> None:
    class ApprovalCatalog:
        def find(self, tool_id: str, version: str) -> ToolContract | None:
            found = CATALOG.find(tool_id, version)
            return None if found is None else replace(found, needs_approval=True, enabled=False)

        def versions(self, tool_id: str) -> tuple[str, ...]:
            return CATALOG.versions(tool_id)

    codes = _codes(validate(parse(_example("kb1-slack-ghi")), ApprovalCatalog()))
    assert codes == ["tool_da_tat", "tool_can_phe_duyet"]


@pytest.mark.parametrize(
    ("mutate", "code", "field"),
    [
        (lambda i: i.pop("text"), "input_thieu", ("input", "text")),
        (lambda i: i.update(mau={"const": "do"}), "input_truong_la", ("input", "mau")),
        (
            lambda i: i.update(channel={"const": "kenh-thuong"}),
            "input_hang_sai",
            ("input", "channel"),
        ),
    ],
)
def test_input_cua_tool(mutate: Any, code: str, field: tuple[str, ...]) -> None:
    document = _kb("kb1-slack-ghi")
    mutate(document["nodes"][1]["input"])
    (issue,) = _full(document).errors
    assert (issue.code, issue.field) == (code, field)


def test_hang_so_vuot_gioi_han_cua_tool() -> None:
    document = _kb("kb4-bac-cau")
    document["nodes"][1]["input"]["limit"] = {"const": 999}
    assert _codes(_full(document)) == ["input_hang_sai"]


def test_hang_so_kiem_duoc_qua_ref_cua_schema_tool() -> None:
    """`notion.create_page.blocks.items` là `$ref` vào `$defs` — tách property ra
    khỏi gốc để kiểm thì tham chiếu gãy và lỗi này lọt qua."""
    document = _kb("kb4-bac-cau")
    document["nodes"][3]["input"]["blocks"] = {"const": [{"type": "video", "text": "x"}]}
    (issue,) = _full(document).errors
    assert issue.code == "input_hang_sai"
    assert issue.field[:2] == ("input", "blocks")


def test_connection_phai_bind_va_bind_thua_la_canh_bao() -> None:
    document = _kb("kb1-slack-ghi")
    document["connections"] = {"notion": "conn_notionvidu"}
    report = _full(document)
    assert _codes(report) == ["connection_chua_bind"]
    assert _codes(report, Severity.WARNING) == ["connection_khong_dung"]


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda s: s.update(pick=["idd"]), "duong_khong_ton_tai"),
        (lambda s: s.update(where={"ten": {"from_input": "ten_kenh"}}), "duong_khong_ton_tai"),
        (lambda s: s.update(path=["next_cursor"]), "where_khong_tren_mang"),
    ],
)
def test_duong_from_node_doi_chieu_voi_output_cua_tool(mutate: Any, code: str) -> None:
    document = _kb("kb2-slack-doc-roi-ghi")
    mutate(document["nodes"][2]["input"]["channel"]["from_node"])
    assert code in _codes(_full(document))


def test_duong_khong_ton_tai_noi_ro_segment_hong() -> None:
    document = _kb("kb3-notion-doc-sau")
    document["nodes"][2]["input"]["block_id"] = _from("tim", "results", "0", "tieu_de")
    (issue,) = _full(document).errors
    assert issue.code == "duong_khong_ton_tai"
    assert "results.0.tieu_de" in issue.message


def test_loop_tren_gia_tri_khong_phai_mang() -> None:
    document = _kb("kb3-notion-doc-sau")
    document["nodes"][3]["over"] = _from("con", "has_more")
    assert "loop_over_khong_phai_mang" in _codes(_full(document))


def test_filter_va_from_item_doi_chieu_voi_phan_tu_dang_lap() -> None:
    document = _kb("kb3-notion-doc-sau")
    loop = document["nodes"][3]
    loop["filter"] = {"loai": {"const": "child_page"}}
    loop["body"][0]["input"]["page_id"] = {"from_item": ["ma"]}
    fields = [issue.field for issue in _full(document).errors]
    assert fields == [("filter", "loai"), ("input", "page_id")]


def test_duong_vao_output_cua_loop() -> None:
    """Output của loop là mảng output của node `collect`, nên `0.title` tới nơi còn
    `0.tieu_de` thì không — `notion.get_page` đóng schema output của nó."""
    document = _kb("kb3-notion-doc-sau")
    document["nodes"].append(
        _llm("gom", dung=_from("moi_trang", "0", "title"), sai=_from("moi_trang", "0", "tieu_de"))
    )
    document["edges"].append({"from": "moi_trang", "to": "gom"})
    assert [(i.node, i.field) for i in _full(document).errors] == [("gom", ("input", "sai"))]


# ─── Connection (Q7) ─────────────────────────────────────────────────────────


class FakeConnections:
    """`ConnectionReader` giả — bảng tra theo id, cùng khuôn adapter thật."""

    def __init__(self, known: dict[str, BoundConnection]) -> None:
        self._known = known
        self.asked: list[str] = []

    async def describe(
        self, *, tenant_id: str, workspace_id: str, connection_id: str
    ) -> BoundConnection | None:
        self.asked.append(connection_id)
        return self._known.get(connection_id)


def _bound(**overrides: Any) -> BoundConnection:
    base: dict[str, Any] = {
        "connection_id": "conn_slackvidu",
        "provider": "slack",
        "shared_with_workspace": True,
        "status": "active",
        "is_usable": True,
    }
    base.update(overrides)
    return BoundConnection(**base)


async def _connections(document: dict[str, Any], known: dict[str, BoundConnection]) -> list[str]:
    report = await validate_connections(
        parse(document), FakeConnections(known), tenant_id="org_1", workspace_id="ws_1"
    )
    return _codes(report)


async def test_connection_chung_cua_workspace_thi_publish_duoc() -> None:
    codes = await _connections(_kb("kb1-slack-ghi"), {"conn_slackvidu": _bound()})
    assert codes == []


async def test_connection_rieng_tu_bi_tu_choi() -> None:
    """Q7: workflow chạy lúc 3 giờ sáng, cả khi người tạo đã rời tổ chức, nên
    nó không bám vào tài khoản riêng của ai."""
    codes = await _connections(
        _kb("kb1-slack-ghi"), {"conn_slackvidu": _bound(shared_with_workspace=False)}
    )
    assert codes == ["connection_rieng_tu"]


async def test_connection_khong_con_dung_duoc_bi_tu_choi() -> None:
    codes = await _connections(
        _kb("kb1-slack-ghi"),
        {"conn_slackvidu": _bound(status="expired", is_usable=False)},
    )
    assert codes == ["connection_khong_dung_duoc"]


async def test_connection_khong_ton_tai_hoac_thuoc_workspace_khac() -> None:
    """Adapter trả `None` cho cả hai trường hợp — từ phía workflow chúng giống
    hệt nhau, và nói rõ hơn là rò rỉ sự tồn tại của tài nguyên tenant khác."""
    assert await _connections(_kb("kb1-slack-ghi"), {}) == ["connection_khong_ton_tai"]


async def test_connection_sai_provider_bi_tu_choi() -> None:
    codes = await _connections(_kb("kb1-slack-ghi"), {"conn_slackvidu": _bound(provider="notion")})
    assert codes == ["connection_sai_provider"]


async def test_kich_ban_bac_cau_hoi_ca_hai_provider() -> None:
    known = {
        "conn_slackvidu": _bound(),
        "conn_notionvidu": _bound(connection_id="conn_notionvidu", provider="notion"),
    }
    reader = FakeConnections(known)
    report = await validate_connections(
        parse(_kb("kb4-bac-cau")), reader, tenant_id="org_1", workspace_id="ws_1"
    )
    assert _codes(report) == []
    assert sorted(reader.asked) == ["conn_notionvidu", "conn_slackvidu"]
