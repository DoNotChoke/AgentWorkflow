"""Chặng 2 của compiler: khung, và nguồn gốc của từng giá trị. Không hạ tầng."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.domains.workflow.domain.compiler.normalize import read_source
from app.domains.workflow.domain.compiler.provenance import (
    Element,
    FromRequest,
    LaneEvidence,
    LaneLink,
    SourceReport,
    WhereKey,
    find_sources,
)
from app.domains.workflow.domain.compiler.skeleton import (
    Skeleton,
    StepKind,
    build_skeleton,
    leaves,
)
from app.domains.workflow.domain.compiler.source import SourceMaterial, SourceStep, Turn

SOURCES = Path(__file__).parent / "fixtures" / "sources"


def _skeleton(name: str) -> Skeleton:
    document = json.loads((SOURCES / f"{name}.json").read_text(encoding="utf-8"))
    return build_skeleton(read_source(document))


def _report(name: str) -> SourceReport:
    return find_sources(_skeleton(name))


def _call(
    ref: str,
    sent: dict[str, Any],
    received: dict[str, Any] | None = None,
    *,
    tool: str = "notion.get_page",
    version: str | None = "1.0",
) -> SourceStep:
    return SourceStep(
        ref=ref,
        step_id=f"stp_{ref}",
        tool_id=tool,
        tool_version=version,
        input=sent,
        output=received or {},
    )


def _made(*turns: tuple[str, list[SourceStep]]) -> Skeleton:
    """Khung từ các lượt viết tay: mỗi lượt là câu người dùng và các bước."""
    return build_skeleton(
        SourceMaterial(
            conversation_id="cnv_1",
            turns=tuple(
                Turn(
                    index=index,
                    trace_id=f"trc_{index}",
                    status="succeeded",
                    request=request,
                    reply=None,
                    steps=tuple(steps),
                )
                for index, (request, steps) in enumerate(turns, start=1)
            ),
        )
    )


# ─── Khung ───────────────────────────────────────────────────────────────────


def test_moi_gia_tri_la_co_mot_dia_chi() -> None:
    tree = {"title": "A", "blocks": [{"type": "h2", "text": "B"}, {"children": []}], "meta": {}}

    assert list(leaves(tree)) == [
        (("title",), "A"),
        (("blocks", "0", "type"), "h2"),
        (("blocks", "0", "text"), "B"),
        (("blocks", "1", "children"), []),
        (("meta",), {}),
    ]


def test_input_rong_khong_co_gia_tri_nao() -> None:
    assert list(leaves({})) == []


def test_khung_giu_thu_tu_cac_buoc_xuyen_qua_cac_luot() -> None:
    skeleton = _skeleton("kb7-buoc-thua")

    assert [(step.ref, step.turn, step.tool_id) for step in skeleton.steps] == [
        ("l1.b1", 1, "slack.list_channels"),
        ("l2.b1", 2, "slack.post_message"),
        ("l3.b1", 3, "notion.search"),
        ("l4.b1", 4, "notion.search"),
        ("l4.b2", 4, "slack.post_message"),
    ]
    assert sorted(skeleton.requests) == [1, 2, 3, 4]
    assert skeleton.step("l4.b1").literals[0].value == "AWS"


def test_buoc_transform_that_la_mot_buoc_cua_khung() -> None:
    """Code Agent đã chạy được bê nguyên sang; chỗ có giá trị để hỏi là `args`."""
    lay, dem = _skeleton("kb10-transform-dem").steps

    assert (dem.kind, dem.tool_id, dem.tool_version) == (
        StepKind.TRANSFORM,
        "system.transform",
        None,
    )
    assert dem.code.startswith("messages = args['messages']")
    assert list(dem.fields) == ["messages"]
    assert {literal.path[0] for literal in dem.literals} == {"args"}
    assert lay.kind is StepKind.TOOL


def test_tool_tai_cho_khac_transform_van_nam_ngoai_khung() -> None:
    skeleton = _made(
        ("ghi nhớ giúp tôi", [_call("l1.b1", {"x": 1}, tool="system.memory", version=None)])
    )

    assert skeleton.steps == ()
    assert [step.tool_id for step in skeleton.unsupported] == ["system.memory"]


# ─── Bảng tổng của GĐ 0 ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "tu_cau_goc", "tu_buoc_truoc", "khong_nguon"),
    [
        ("kb1-slack-ghi", 2, 0, 0),
        ("kb2-slack-doc-roi-ghi", 1, 1, 0),
        ("kb3-notion-doc-sau", 1, 6, 0),
        ("kb4-bac-cau", 4, 0, 22),
        ("kb5-re-nhanh", 3, 0, 1),
        ("kb6-hong-giua-chung", 0, 0, 0),
    ],
)
def test_so_lieu_khop_bang_tong_cua_gd0(
    name: str, tu_cau_goc: int, tu_buoc_truoc: int, khong_nguon: int
) -> None:
    literals = _report(name).literals

    assert sum(1 for found in literals if found.requests) == tu_cau_goc
    assert sum(1 for found in literals if found.steps and not found.requests) == tu_buoc_truoc
    assert sum(1 for found in literals if not found.is_sourced) == khong_nguon


# ─── Câu người dùng ──────────────────────────────────────────────────────────


def test_so_khop_voi_cau_goc_phai_ep_kieu() -> None:
    """GĐ 0 so nguyên dạng nên xếp nhầm `limit: 10` của *"10 tin nhắn gần nhất"*."""
    limit = _report("kb4-bac-cau").of("l1.b1", ("limit",))

    assert limit.literal.value == 10
    assert limit.requests == (FromRequest(turn=1, coerced=True),)


@pytest.mark.parametrize(
    ("request_text", "value", "khop"),
    [
        ("đăng vào kênh test, nhanh nhé", "test", True),
        ("đăng vào kênh testing", "test", False),
        ("đọc 10 tin nhắn", 1, False),
        ("đọc 10 tin nhắn", 10, True),
        ("đăng câu: [WP-B] xong.", "[WP-B] xong.", True),
        ("bật thông báo", True, False),
        ("để trống", "", False),
    ],
)
def test_gia_tri_phai_la_mot_tu_tron_ven_trong_cau(
    request_text: str, value: Any, khop: bool
) -> None:
    report = find_sources(_made((request_text, [_call("l1.b1", {"x": value})])))

    assert bool(report.of("l1.b1", ("x",)).requests) is khop


def test_gia_tri_noi_o_luot_truoc_van_tinh_con_luot_sau_thi_khong() -> None:
    report = find_sources(
        _made(
            ("tôi sẽ cần kênh test", [_call("l1.b1", {"q": "social"})]),
            ("đăng vào đó, và cả social", [_call("l2.b1", {"channel": "test"})]),
        )
    )

    assert report.of("l1.b1", ("q",)).requests == ()
    assert report.of("l2.b1", ("channel",)).requests == (FromRequest(turn=1),)


def test_gia_tri_model_tu_chon_khong_co_nguon() -> None:
    """P5: không ai nói 200; model lấy giá trị lớn nhất schema cho phép."""
    limit = _report("kb5-re-nhanh").of("l1.b1", ("limit",))

    assert limit.literal.value == 200
    assert not limit.is_sourced


def test_gioi_han_model_suy_ra_tu_nguong_khong_co_nguon() -> None:
    """Người dùng nói *nhiều hơn 5*; model xin 6. Số 6 không ở đâu trong câu."""
    limit = _report("kb5b-co-transform-khong-dung").of("l1.b1", ("limit",))

    assert limit.literal.value == 6
    assert not limit.is_sourced


def test_noi_dung_model_viet_ra_khong_co_nguon() -> None:
    """P4: không block nào của trang Notion chép nguyên văn từ tin nhắn Slack."""
    blocks = [
        found
        for found in _report("kb4-bac-cau").literals
        if found.literal.step == "l1.b2" and found.literal.path[0] == "blocks"
    ]

    assert len(blocks) == 22
    assert not any(found.is_sourced for found in blocks)


# ─── Output của bước trước ───────────────────────────────────────────────────


def test_phan_tu_mang_duoc_chon_theo_ten_nguoi_dung_da_goi() -> None:
    """P2: thứ ghi lại là *phần tử thứ 4*; ý định là *kênh tên `test`*."""
    (source,) = _report("kb2-slack-doc-roi-ghi").of("l1.b2", ("channel",)).steps

    assert source.step == "l1.b1"
    assert source.path[0] == "channels"
    assert source.path[2] == "id"
    assert source.element == Element(
        array=("channels",),
        index=int(source.path[1]),
        pick=("id",),
        keys=(WhereKey(field="name", value="test", turn=1),),
    )


def test_khoa_chi_dung_duoc_khi_no_chon_dung_phan_tu_dau_tien_khop() -> None:
    kenh = {"channels": [{"id": "C001", "name": "test"}, {"id": "C002", "name": "test"}]}
    report = find_sources(
        _made(
            (
                "đăng vào kênh test",
                [_call("l1.b1", {}, kenh), _call("l1.b2", {"channel": "C002"})],
            )
        )
    )

    (source,) = report.of("l1.b2", ("channel",)).steps

    assert source.element is not None
    assert source.element.index == 1
    assert source.element.keys == ()


def test_output_chep_lai_input_khong_phai_la_nguon() -> None:
    """`post_message` trả `channel_id`. Nguồn của id kênh ở lượt 4 là `list_channels` của
    lượt 1, không phải tin `ping` của lượt 2."""
    (source,) = _report("kb7-buoc-thua").of("l4.b2", ("channel",)).steps

    assert source.step == "l1.b1"
    assert source.element is not None
    assert source.element.keys == (WhereKey(field="name", value="test", turn=2),)


def test_uuid_viet_hai_kieu_van_la_mot_gia_tri() -> None:
    """Model bỏ gạch nối của id trang trước khi gọi tool kế tiếp."""
    (source,) = _report("kb8-ba-luot").of("l1.b2", ("block_id",)).steps

    assert (source.step, source.path) == ("l1.b1", ("results", "0", "id"))
    assert source.exact is False


@pytest.mark.parametrize("value", ["ok", 42, True])
def test_gia_tri_ngan_hoac_khong_phai_chuoi_khong_do_voi_output(value: Any) -> None:
    """Trùng nhau do tình cờ nhiều hơn do phụ thuộc."""
    report = find_sources(
        _made(("làm đi", [_call("l1.b1", {}, {"x": value}), _call("l1.b2", {"y": value})]))
    )

    assert report.of("l1.b2", ("y",)).steps == ()


# ─── Cả một mảng hay đối tượng ───────────────────────────────────────────────


@pytest.mark.parametrize("name", ["kb9-transform-doi-hinh-dang", "kb10-transform-dem"])
def test_du_lieu_model_chep_tay_va_cat_bot_van_tim_ra_nguon(name: str) -> None:
    """Model chép tin nhắn vào `args` và chỉ giữ trường nó cần. So nguyên cây con thì
    không khớp; so từng lá thì một id người gửi lặp ở hàng chục tin làm lạc đường."""
    (source,) = _report(name).container("l1.b2", ("args", "messages"))

    assert (source.step, source.path) == ("l1.b1", ("messages",))
    assert source.exact is False


def test_mang_lay_nguyen_tu_buoc_truoc_la_nguon_giong_het() -> None:
    blocks = [{"type": "paragraph", "text": "một"}, {"type": "paragraph", "text": "hai"}]
    report = find_sources(
        _made(
            (
                "tạo trang",
                [
                    _call(
                        "l1.b1",
                        {"code": "…", "args": {}},
                        {"result": blocks},
                        tool="system.transform",
                        version=None,
                    ),
                    _call("l1.b2", {"title": "A", "blocks": blocks}),
                ],
            )
        )
    )

    (source,) = report.container("l1.b2", ("blocks",))

    assert (source.step, source.path, source.exact) == ("l1.b1", ("result",), True)


@pytest.mark.parametrize(
    "khac",
    [
        [{"text": "một"}],
        [{"text": "hai"}, {"text": "một"}],
        [{"text": "một"}, {"text": "HAI"}],
        [{"text": "một", "them": 1}, {"text": "hai"}],
    ],
    ids=["thieu_phan_tu", "sai_thu_tu", "sai_gia_tri", "thua_truong"],
)
def test_khac_do_dai_thu_tu_hay_gia_tri_thi_khong_phai_cung_mot_mang(khac: list[Any]) -> None:
    goc = {"items": [{"text": "một", "id": "a"}, {"text": "hai", "id": "b"}]}
    report = find_sources(
        _made(("làm đi", [_call("l1.b1", {}, goc), _call("l1.b2", {"items": khac})]))
    )

    assert report.container("l1.b2", ("items",)) == ()


def test_noi_dung_model_tu_viet_khong_co_nguon_ca_o_muc_mang() -> None:
    assert _report("kb4-bac-cau").container("l1.b2", ("blocks",)) == ()


# ─── Vòng lặp ────────────────────────────────────────────────────────────────


def test_bon_loi_goi_cho_bon_phan_tu_la_mot_vong_lap() -> None:
    """P3: trải phẳng thành bốn node thì đúng lần này, sai mọi lần sau."""
    (loop,) = _report("kb3-notion-doc-sau").loops

    assert loop.steps == ("l1.b4", "l1.b5", "l1.b6", "l1.b7")
    assert (loop.field, loop.source, loop.array, loop.pick) == (
        ("page_id",),
        "l1.b3",
        ("results",),
        ("id",),
    )
    assert loop.indices == (0, 1, 2, 3)
    assert loop.covers_all
    assert loop.filter == ()


def test_vong_lap_chi_dung_mot_phan_mang_thi_tim_truong_phan_biet() -> None:
    con = {
        "results": [
            {"id": "blk_aaa", "type": "child_page", "has_children": False},
            {"id": "blk_bbb", "type": "paragraph", "has_children": False},
            {"id": "blk_ccc", "type": "child_page", "has_children": False},
        ]
    }
    report = find_sources(
        _made(
            (
                "đọc các trang con",
                [
                    _call("l1.b1", {}, con, tool="notion.get_block_children"),
                    _call("l1.b2", {"page_id": "blk_aaa"}),
                    _call("l1.b3", {"page_id": "blk_ccc"}),
                ],
            )
        )
    )

    (loop,) = report.loops

    assert loop.steps == ("l1.b2", "l1.b3")
    assert loop.indices == (0, 2)
    assert not loop.covers_all
    assert loop.filter == (("type", "child_page"),)


def test_hai_truong_cung_doi_thi_khong_phai_vong_lap_tren_mot_mang() -> None:
    """Lượt 3 của kb8: mỗi tin trả lời có `text` do model viết và `thread_ts` lấy từ một
    bước khác nhau. Chỉ sáu lần đọc Notion của lượt 1 là một vòng lặp."""
    (loop,) = _report("kb8-ba-luot").loops

    assert loop.steps == tuple(f"l1.b{n}" for n in range(3, 9))
    assert loop.source == "l1.b2"


def test_nhom_buoc_chay_mot_lan_cho_moi_vong_la_mot_lan_cua_vong_lap() -> None:
    """kb8: sáu tin đăng, sáu lần đọc đáp án, sáu tin trả lời — mỗi nhóm ứng với sáu khối
    của vòng lặp ở lượt 1."""
    (loop,) = _report("kb8-ba-luot").loops
    dang, doc_dap_an, tra_loi = loop.lanes

    assert dang.steps == tuple(f"l2.b{n}" for n in range(2, 8))
    assert doc_dap_an.steps == tuple(f"l3.b{n}" for n in range(1, 7))
    assert tra_loi.steps == tuple(f"l3.b{n}" for n in range(7, 13))
    assert loop.lane_steps(0) == loop.steps
    assert loop.lane_steps(3) == tra_loi.steps


def test_lan_doc_du_lieu_cua_dung_vong_cua_no() -> None:
    (loop,) = _report("kb8-ba-luot").loops
    _, doc_dap_an, tra_loi = loop.lanes

    # Khối đáp án đứng ở vị trí 5, 7 hay 6 tuỳ câu; thứ không đổi là nó là khối `toggle`.
    assert doc_dap_an.links == (
        LaneLink("block_id", lane=0, path=("results",), key=("type", "toggle"), pick=("id",)),
    )
    assert doc_dap_an.evidence is LaneEvidence.DATA
    assert tra_loi.links == (LaneLink("thread_ts", lane=1, path=("message_ts",)),)
    assert tra_loi.written == ("text",)


def test_lan_chi_co_so_buoc_bang_so_vong_thi_noi_ro_la_theo_vi_tri() -> None:
    (loop,) = _report("kb8-ba-luot").loops
    dang = loop.lanes[0]

    assert dang.links == ()
    assert dang.written == ("text",)
    assert dang.evidence is LaneEvidence.POSITION


def _hai_trang(*sau: SourceStep) -> SourceReport:
    return find_sources(
        _made(
            (
                "đọc hai trang rồi báo lên kênh",
                [
                    _call("l1.b1", {}, {"results": [{"id": "blk_aaa"}, {"id": "blk_bbb"}]}),
                    _call("l1.b2", {"page_id": "blk_aaa"}, {"title": "Trang A"}),
                    _call("l1.b3", {"page_id": "blk_bbb"}, {"title": "Trang B"}),
                    *sau,
                ],
            )
        )
    )


def _so_le(*, thread: bool = True) -> SourceMaterial:
    """kb8 xếp lại thành một lượt, mỗi câu hỏi làm đủ bốn việc rồi mới sang câu sau: đọc
    câu hỏi, đăng, đọc đáp án, trả lời vào thread."""
    document = json.loads((SOURCES / "kb8-ba-luot.json").read_text(encoding="utf-8"))
    source = read_source(document)
    by_ref = {step.ref: step for turn in source.turns for step in turn.steps}
    order = ["l1.b1", "l1.b2", "l2.b1"]
    for k in range(6):
        order += [f"l1.b{3 + k}", f"l2.b{2 + k}", f"l3.b{1 + k}", f"l3.b{7 + k}"]
    steps = []
    for number, ref in enumerate(order, start=1):
        step = replace(by_ref[ref], ref=f"l1.b{number}")
        if not thread:
            sent = {name: value for name, value in step.input.items() if name != "thread_ts"}
            step = replace(step, input=sent)
        steps.append(step)
    request = " ".join(turn.request or "" for turn in source.turns)
    turn = replace(source.turns[0], request=request, steps=tuple(steps))
    return SourceMaterial(conversation_id=source.conversation_id, turns=(turn,))


def test_cac_buoc_so_le_theo_chu_ky_van_la_mot_vong_lap_va_cac_lan() -> None:
    """`đọc 1, đăng 1, đọc đáp án 1, trả lời 1, đọc 2, …`: không hai bước cùng tool nào
    đứng liền nhau, nhưng vẫn là sáu vòng của cùng một việc."""
    (loop,) = find_sources(build_skeleton(_so_le())).loops

    assert loop.steps == tuple(f"l1.b{n}" for n in range(4, 28, 4))
    assert loop.source == "l1.b2"
    assert [lane.steps[:2] for lane in loop.lanes] == [
        ("l1.b5", "l1.b9"),
        ("l1.b6", "l1.b10"),
        ("l1.b7", "l1.b11"),
    ]


def test_buoc_so_le_cho_cung_cac_moi_noi_voi_buoc_lien_nhau() -> None:
    (so_le,) = find_sources(build_skeleton(_so_le())).loops
    (lien_nhau,) = _report("kb8-ba-luot").loops

    assert [(lane.links, lane.written) for lane in so_le.lanes] == [
        (lane.links, lane.written) for lane in lien_nhau.lanes
    ]


def test_mot_vong_goi_cung_mot_tool_hai_lan_thi_chu_ky_dai_gap_doi() -> None:
    """Bỏ `thread_ts` thì hai lần đăng tin của một vòng trông giống hệt nhau, và chu kỳ
    ngắn nhất là `đọc, đăng`. Đọc theo chu kỳ đó thì không nhóm nào lặp trên một mảng."""
    (loop,) = find_sources(build_skeleton(_so_le(thread=False))).loops

    assert loop.steps == tuple(f"l1.b{n}" for n in range(4, 28, 4))
    assert [lane.steps[0] for lane in loop.lanes] == ["l1.b5", "l1.b6", "l1.b7"]
    assert loop.lanes[2].evidence is LaneEvidence.POSITION


def test_chu_ky_khong_lap_tren_mang_nao_thi_khong_phai_vong_lap() -> None:
    post = {"tool": "slack.post_message"}
    report = find_sources(
        _made(
            (
                "đọc trang rồi báo, hai lần",
                [
                    _call("l1.b1", {"page_id": "blk_aaa"}, {"title": "Trang A"}),
                    _call("l1.b2", {"text": "Xong trang đầu"}, **post),
                    _call("l1.b3", {"page_id": "blk_bbb"}, {"title": "Trang B"}),
                    _call("l1.b4", {"text": "Xong trang sau"}, **post),
                ],
            )
        )
    )

    assert report.loops == ()


def test_so_buoc_khac_so_vong_thi_khong_phai_lan() -> None:
    post = {"tool": "slack.post_message"}
    report = _hai_trang(
        _call("l1.b4", {"text": "Tin thứ nhất"}, **post),
        _call("l1.b5", {"text": "Tin thứ hai"}, **post),
        _call("l1.b6", {"text": "Tin thứ ba"}, **post),
    )

    (loop,) = report.loops
    assert loop.lanes == ()


def test_cac_buoc_giong_het_nhau_khong_phai_lan() -> None:
    post = {"tool": "slack.post_message"}
    report = _hai_trang(
        _call("l1.b4", {"text": "Đã đọc xong"}, **post),
        _call("l1.b5", {"text": "Đã đọc xong"}, **post),
    )

    (loop,) = report.loops
    assert loop.lanes == ()


def test_lan_lay_thang_tu_output_cua_buoc_cung_vong() -> None:
    post = {"tool": "slack.post_message"}
    report = _hai_trang(
        _call("l1.b4", {"text": "Trang A"}, **post),
        _call("l1.b5", {"text": "Trang B"}, **post),
    )

    (loop,) = report.loops
    (lane,) = loop.lanes
    assert lane.links == (LaneLink("text", lane=0, path=("title",)),)


def test_cac_buoc_giong_het_nhau_khong_phai_vong_lap() -> None:
    report = find_sources(
        _made(
            (
                "đọc trang blk_aaa hai lần",
                [
                    _call("l1.b1", {}, {"results": [{"id": "blk_aaa"}]}),
                    _call("l1.b2", {"page_id": "blk_aaa"}),
                    _call("l1.b3", {"page_id": "blk_aaa"}),
                ],
            )
        )
    )

    assert report.loops == ()
