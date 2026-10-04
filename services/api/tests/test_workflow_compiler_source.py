"""Chặng 1 của compiler: trace thô → nguyên liệu. Không hạ tầng."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from app.domains.workflow.domain.compiler.normalize import read_source
from app.domains.workflow.domain.compiler.source import SkipReason

SOURCES = Path(__file__).parent / "fixtures" / "sources"


def _source(name: str) -> dict[str, Any]:
    return json.loads((SOURCES / f"{name}.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _side(payload: dict[str, Any] | None) -> dict[str, Any]:
    return {"state": "available" if payload is not None else "not_stored", "payload": payload}


def _step(
    step_id: str,
    kind: str,
    *,
    at: int,
    status: str = "succeeded",
    parent: str | None = None,
    attributes: dict[str, Any] | None = None,
    sent: dict[str, Any] | None = None,
    received: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": step_id,
        "kind": kind,
        "status": status,
        "parent_step_id": parent,
        "started_at": f"2026-10-01T00:00:{at:02d}+00:00",
        "attributes": attributes or {},
        "error_category": None,
        "input": _side(sent),
        "output": _side(received),
    }


def _gateway_call(
    name: str, *, at: int, status: str = "succeeded", sent: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Một lời gọi tool như trace ghi nó: bước của Agent, và bước Gateway dưới nó."""
    return [
        _step(
            name, "tool_call", at=at, status=status, attributes={"tool_id": "slack.post_message"}
        ),
        _step(
            f"{name}_gw",
            "tool_call",
            at=at,
            status=status,
            parent=name,
            attributes={"tool_id": "slack.post_message", "tool_version": "1.0"},
            sent=sent if sent is not None else {"text": name},
            received={"message_ts": name},
        ),
    ]


def _trace(
    trace_id: str, *, at: int, steps: list[dict[str, Any]], status: str = "succeeded"
) -> dict[str, Any]:
    return {
        "id": trace_id,
        "status": status,
        "started_at": f"2026-10-01T00:{at:02d}:00+00:00",
        "steps": steps,
    }


def _conversation(
    *traces: dict[str, Any], messages: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {"conversation_id": "cnv_1", "messages": messages or [], "traces": list(traces)}


def _message(role: str, content: str, *, minute: int, second: int = 1) -> dict[str, Any]:
    return {
        "role": role,
        "content": content,
        "created_at": f"2026-10-01T00:{minute:02d}:{second:02d}+00:00",
    }


def _user(text: str) -> dict[str, Any]:
    return {"role": "user", "text": text, "tool_calls": [], "tool_results": []}


@pytest.mark.parametrize(
    ("name", "tools"),
    [
        ("kb1-slack-ghi", ["slack.post_message"]),
        ("kb2-slack-doc-roi-ghi", ["slack.list_channels", "slack.post_message"]),
        (
            "kb3-notion-doc-sau",
            [
                "notion.search",
                "notion.get_page",
                "notion.get_block_children",
                "notion.get_page",
                "notion.get_page",
                "notion.get_page",
                "notion.get_page",
            ],
        ),
        ("kb4-bac-cau", ["slack.get_channel_history", "notion.create_page"]),
        ("kb5-re-nhanh", ["slack.get_channel_history", "slack.post_message"]),
        ("kb6-hong-giua-chung", []),
    ],
)
def test_sau_kich_ban_that_doc_ra_dung_cac_buoc(name: str, tools: list[str]) -> None:
    source = read_source(_source(name))

    (turn,) = source.turns
    assert [step.tool_id for step in turn.steps] == tools
    assert [step.ref for step in turn.steps] == [f"l1.b{n}" for n in range(1, len(tools) + 1)]
    assert all(step.tool_version for step in turn.steps)
    assert turn.request
    assert turn.reply


def test_gia_tri_lay_o_buoc_gateway_chu_khong_o_buoc_cua_agent() -> None:
    """P1: bước của Agent không mang payload; đọc nhầm tầng thì thấy một trace rỗng."""
    list_channels, post_message = read_source(_source("kb2-slack-doc-roi-ghi")).turns[0].steps

    channels = {channel["id"]: channel["name"] for channel in list_channels.output["channels"]}
    assert channels[post_message.input["channel"]] == "test"
    assert post_message.tool_version == "1.0"


def test_tin_nhan_mo_luot_la_cau_nguoi_dung_da_go() -> None:
    turn = read_source(_source("kb2-slack-doc-roi-ghi")).turns[0]

    assert turn.request is not None
    assert turn.request.startswith("Tìm id của kênh Slack tên là test")
    assert turn.reply is not None
    assert turn.reply.startswith("Đã gửi tin nhắn")


def test_buoc_hong_bi_loai_va_duoc_ghi_lai_la_da_loai() -> None:
    """P6: một tool call hỏng là ngõ cụt model đã thoát ra."""
    (turn,) = read_source(_source("kb6-hong-giua-chung")).turns

    assert turn.status == "succeeded"
    assert turn.steps == ()
    assert [(skipped.tool_id, skipped.reason) for skipped in turn.skipped] == [
        ("slack.get_channel_history", SkipReason.FAILED)
    ]


@pytest.mark.parametrize("state", ["not_stored", "gone"])
@pytest.mark.parametrize("side", ["input", "output"])
def test_buoc_mat_payload_khong_thanh_mot_buoc_rong(state: str, side: str) -> None:
    document = copy.deepcopy(_source("kb1-slack-ghi"))
    gateway = next(step for step in document["traces"][0]["steps"] if step["parent_step_id"])
    gateway[side] = {"state": state, "payload": None}

    (turn,) = read_source(document).turns

    assert turn.steps == ()
    assert [skipped.reason for skipped in turn.skipped] == [SkipReason.PAYLOAD_MISSING]


def test_buoc_transform_that_doc_duoc_ca_code_lan_ket_qua() -> None:
    """Trace thật: Agent lấy tin nhắn Slack rồi tự viết code đổi chúng thành block Notion.
    Bước `system.transform` không qua Gateway, không có version trong catalog."""
    lay, doi = read_source(_source("kb9-transform-doi-hinh-dang")).turns[0].steps

    assert (doi.tool_id, doi.is_local) == ("system.transform", True)
    assert doi.input["code"].strip().startswith("return [")
    assert len(doi.output["result"]) == len(lay.output["messages"]) == 8
    # Model chép dữ liệu vào `args` bằng tay, và chỉ chép trường nó cần.
    assert set(doi.input["args"]["messages"][0]) == {"text"}
    assert set(lay.output["messages"][0]) > {"text"}


def test_co_transform_trong_tay_model_van_khong_dung_no_de_dem() -> None:
    """Trace thật, Agent được dặn phải dùng `system.transform` khi đếm. Nó xin đúng 6 tin
    nhắn: nhận đủ 6 nghĩa là *nhiều hơn 5*, không cần đếm."""
    (turn,) = read_source(_source("kb5b-co-transform-khong-dung")).turns

    assert [step.tool_id for step in turn.steps] == [
        "slack.get_channel_history",
        "slack.post_message",
    ]
    assert turn.steps[0].input["limit"] == 6


def test_tool_chay_tai_cho_mang_payload_o_chinh_buoc_cua_agent() -> None:
    local = _step(
        "s1",
        "tool_call",
        at=1,
        attributes={"tool_id": "transform"},
        sent={"code": "def run(x): return x"},
        received={"result": 1},
    )

    (step,) = read_source(_conversation(_trace("t1", at=1, steps=[local]))).turns[0].steps

    assert step.is_local
    assert step.input == {"code": "def run(x): return x"}
    assert step.output == {"result": 1}


def test_loi_goi_lap_lai_trong_mot_luot_chi_tinh_mot_lan() -> None:
    steps = [
        *_gateway_call("a", at=1, sent={"text": "xin chào"}),
        *_gateway_call("b", at=2, status="replayed", sent={"text": "xin chào"}),
    ]

    (turn,) = read_source(_conversation(_trace("t1", at=1, steps=steps))).turns

    assert [step.step_id for step in turn.steps] == ["a"]
    assert [(skipped.step_id, skipped.reason) for skipped in turn.skipped] == [
        ("b", SkipReason.REPLAYED)
    ]


def test_gateway_thu_lai_thi_lay_lan_khong_hong() -> None:
    agent, hong = _gateway_call("a", at=1)
    hong["status"] = "failed"
    lai = _step(
        "a_gw2",
        "tool_call",
        at=2,
        parent="a",
        attributes={"tool_id": "slack.post_message", "tool_version": "1.0"},
        sent={"text": "a"},
        received={"message_ts": "lần hai"},
    )
    document = _conversation(_trace("t1", at=1, steps=[agent, hong, lai]))

    (step,) = read_source(document).turns[0].steps

    assert step.output == {"message_ts": "lần hai"}


def test_nhieu_luot_xep_theo_thoi_gian_va_dia_chi_buoc_mang_so_luot() -> None:
    document = _conversation(
        _trace("sau", at=5, steps=_gateway_call("y", at=1)),
        _trace("truoc", at=1, steps=_gateway_call("x", at=1)),
        _trace("dang_chay", at=9, steps=_gateway_call("z", at=1), status="running"),
    )

    source = read_source(document)

    assert [turn.trace_id for turn in source.turns] == ["truoc", "sau"]
    assert [step.ref for step in source.steps()] == ["l1.b1", "l2.b1"]


def test_luot_vu_vo_van_duoc_doc_va_co_the_la_nguon_cua_luot_sau() -> None:
    """Conversation thật: ba lượt đầu là việc thừa — liệt kê kênh, đăng thử `ping`, tìm
    nhầm trang — việc thật ở lượt 4. Nhưng lượt 4 không liệt kê kênh lại: id kênh nó
    dùng đến từ lượt 1."""
    source = read_source(_source("kb7-buoc-thua"))

    assert [[step.tool_id for step in turn.steps] for turn in source.turns] == [
        ["slack.list_channels"],
        ["slack.post_message"],
        ["notion.search"],
        ["notion.search", "slack.post_message"],
    ]
    liet_ke = source.turns[0].steps[0]
    dang = source.turns[3].steps[1]
    kenh = {channel["id"]: channel["name"] for channel in liet_ke.output["channels"]}
    assert kenh[dang.input["channel"]] == "test"
    assert dang.ref == "l4.b2"


def test_cong_viec_trai_ba_luot_doc_ra_du_ba_luot() -> None:
    """Conversation thật: lượt 1 đọc Notion rồi vướng chọn connection, lượt 2 chỉ là câu
    chọn connection mà Agent làm nốt việc của lượt 1, lượt 3 là một yêu cầu mới dựa
    trên kết quả lượt 2."""
    source = read_source(_source("kb8-ba-luot"))

    assert [len(turn.steps) for turn in source.turns] == [8, 7, 12]
    assert [skipped.reason for skipped in source.turns[0].skipped] == [SkipReason.FAILED] * 2
    assert source.turns[1].request == "Use Slack — TuanNghia."
    assert source.turns[2].request is not None
    assert source.turns[2].request.startswith("ok giờ gửi kết quả của mỗi câu lên thread")
    assert [step.ref for step in source.turns[2].steps][-1] == "l3.b12"


def test_tin_nhan_doc_tu_conversation_khi_payload_cua_model_da_mat() -> None:
    """Payload lớn nằm ở object storage và có hạn lưu; tin nhắn thì không."""
    document = _conversation(
        _trace("t1", at=1, steps=[_step("m1", "model_call", at=1)]),
        _trace("t2", at=3, steps=[_step("m2", "model_call", at=1)]),
        messages=[
            _message("user", "đăng vào kênh test", minute=1),
            _message("assistant", "Đã đăng.", minute=1, second=30),
            _message("user", "đăng thêm vào social", minute=3),
            _message("assistant", "Đang làm…", minute=3, second=10),
            _message("assistant", "Xong cả hai.", minute=3, second=40),
        ],
    )

    dau, sau = read_source(document).turns

    assert (dau.request, dau.reply) == ("đăng vào kênh test", "Đã đăng.")
    assert (sau.request, sau.reply) == ("đăng thêm vào social", "Xong cả hai.")


def test_tin_nhan_cua_luot_dang_chay_khong_roi_vao_luot_truoc() -> None:
    document = _conversation(
        _trace("t1", at=1, steps=[]),
        _trace("t2", at=3, steps=[], status="running"),
        messages=[
            _message("user", "việc thứ nhất", minute=1),
            _message("user", "việc thứ hai", minute=3),
        ],
    )

    (turn,) = read_source(document).turns

    assert turn.request == "việc thứ nhất"


def test_luot_khong_goi_tool_van_la_mot_luot() -> None:
    """Người dùng đổi ý giữa chừng bằng một câu: không có bước, nhưng có ngữ cảnh.
    Không có tin nhắn nào được lưu — như một lượt vỡ — thì đọc từ lời gọi model."""
    hoi = _step(
        "m1",
        "model_call",
        at=1,
        sent={
            "turns": [
                _user("đăng vào kênh test"),
                {"role": "assistant", "text": "Đã đăng.", "tool_calls": [], "tool_results": []},
                _user("à mà đổi sang kênh social"),
            ]
        },
        received={"text": "Được, lần sau mình đăng vào social.", "tool_calls": []},
    )

    (turn,) = read_source(_conversation(_trace("t1", at=1, steps=[hoi]))).turns

    assert turn.steps == ()
    assert turn.request == "à mà đổi sang kênh social"
    assert turn.reply == "Được, lần sau mình đăng vào social."


def test_mat_payload_cua_loi_goi_model_thi_khong_doan_tin_nhan() -> None:
    hoi = _step("m1", "model_call", at=1)

    (turn,) = read_source(_conversation(_trace("t1", at=1, steps=[hoi]))).turns

    assert turn.request is None
    assert turn.reply is None
