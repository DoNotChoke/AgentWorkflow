"""GĐ 3 của compiler: câu hỏi, tầng kiểm 1, và bộ lắp IR — trên conversation thật.

Câu trả lời trong `fixtures/compiled/*.answers.json` viết tay, đóng vai LLM. Catalog là
catalog tool thật: IR lắp ra phải qua đúng validator mà một IR viết tay phải qua
(ADR-0008, quyết định 4).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from app.domains.integration.infrastructure.catalog_view import tool_rows
from app.domains.workflow.application.validation import validate
from app.domains.workflow.domain.compiler.answers import AnswerIssue, Answers, read_answers
from app.domains.workflow.domain.compiler.assemble import Assembled, assemble
from app.domains.workflow.domain.compiler.normalize import read_source
from app.domains.workflow.domain.compiler.provenance import find_sources
from app.domains.workflow.domain.compiler.questions import (
    Choice,
    QuestionSet,
    answer_schema,
    build_questions,
    current_version,
)
from app.domains.workflow.domain.compiler.skeleton import Skeleton, build_skeleton
from app.domains.workflow.domain.ir import parse
from app.domains.workflow.domain.validation import Severity
from app.domains.workflow.infrastructure.catalog import RowToolCatalog

FIXTURES = Path(__file__).parent / "fixtures"
EXAMPLES = Path(__file__).resolve().parents[3] / "packages" / "schemas" / "workflow" / "examples"

CATALOG = RowToolCatalog(tool_rows())
TOOLS = {(row["tool_id"], row["version"]): row["input_schema"] for row in tool_rows()}
MUTATING = {(row["tool_id"], row["version"]) for row in tool_rows() if row["mutates"]}

COMPILED = [
    "kb1-slack-ghi",
    "kb2-slack-doc-roi-ghi",
    "kb3-notion-doc-sau",
    "kb4-bac-cau",
    "kb5b-co-transform-khong-dung",
    "kb7-buoc-thua",
    "kb8-ba-luot",
    "kb9-transform-doi-hinh-dang",
    "kb10-transform-dem",
]


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _questions(name: str) -> tuple[Skeleton, QuestionSet]:
    skeleton = build_skeleton(read_source(_json(FIXTURES / "sources" / f"{name}.json")))
    return skeleton, build_questions(skeleton, find_sources(skeleton), TOOLS, MUTATING)


def _raw(name: str) -> dict[str, Any]:
    return _json(FIXTURES / "compiled" / f"{name}.answers.json")


def _read(name: str, raw: dict[str, Any] | None = None) -> tuple[Answers | None, list[AnswerIssue]]:
    skeleton, questions = _questions(name)
    answers, issues = read_answers(questions, skeleton, raw if raw is not None else _raw(name))
    return answers, list(issues)


def _codes(name: str, raw: dict[str, Any]) -> list[tuple[str, str]]:
    return [(issue.question, issue.code) for issue in _read(name, raw)[1]]


def _assemble(name: str) -> Assembled:
    skeleton, questions = _questions(name)
    answers, issues = read_answers(questions, skeleton, _raw(name))
    assert issues == ()
    assert answers is not None
    return assemble(skeleton, questions, answers, TOOLS, llm_model="claude-sonnet-5")


# ─── Lắp ra đúng đáp án, và đáp án chạy được ─────────────────────────────────


@pytest.mark.parametrize("name", COMPILED)
def test_cau_tra_loi_viet_tay_lap_ra_dung_ir_dap_an(name: str) -> None:
    assert _assemble(name).document == _json(FIXTURES / "compiled" / f"{name}.ir.json")


@pytest.mark.parametrize("name", COMPILED)
def test_ir_lap_ra_qua_dung_validator_cua_ir_viet_tay(name: str) -> None:
    report = validate(parse(_assemble(name).document), CATALOG)

    assert report.errors == ()
    assert {issue.code for issue in report.issues if issue.severity is Severity.WARNING} <= {
        "chi_so_tho",
        "llm_trong_loop",
    }


def test_lap_hai_lan_ra_cung_mot_tai_lieu() -> None:
    assert _assemble("kb4-bac-cau").document == _assemble("kb4-bac-cau").document


def test_kich_ban_2_lap_ra_dung_ir_da_viet_tay_o_gd0() -> None:
    """Ví dụ trong `packages/schemas` viết tay từ ý định, trước khi có compiler. Từ trace
    thật và sáu lựa chọn, bộ lắp ra đúng tài liệu đó."""
    assert _assemble("kb2-slack-doc-roi-ghi").document == _json(
        EXAMPLES / "kb2-slack-doc-roi-ghi.json"
    )


def test_khong_buoc_nao_dung_duoc_thi_khong_co_workflow() -> None:
    """Bước duy nhất của kịch bản 6 đã hỏng; không có gì để giữ."""
    answers, issues = _read("kb6-hong-giua-chung")

    assert answers is not None
    assert [(issue.question, issue.code) for issue in issues] == [("", "khong_co_gi_de_bien_dich")]


# ─── Từng thứ bộ lắp dựng ────────────────────────────────────────────────────


def _node(document: dict[str, Any], node_id: str) -> dict[str, Any]:
    return next(node for node in document["nodes"] if node["id"] == node_id)


def test_buoc_thua_bi_bo_con_buoc_la_nguon_du_lieu_thi_o_lai() -> None:
    """kb7: lượt 1 trông như việc vặt, nhưng id kênh của lượt 4 đến từ đó."""
    compiled = _assemble("kb7-buoc-thua")

    assert [node["id"] for node in compiled.document["nodes"]] == ["t", "kenh", "tim", "dang"]
    assert [(dropped.step, dropped.tool_id) for dropped in compiled.dropped] == [
        ("l2.b1", "slack.post_message"),
        ("l3.b1", "notion.search"),
    ]
    assert all(dropped.reason for dropped in compiled.dropped)
    assert _node(compiled.document, "dang")["input"]["channel"] == {
        "from_node": {
            "node": "kenh",
            "path": ["channels"],
            "where": {"name": {"const": "test"}},
            "pick": ["id"],
        }
    }


def test_dau_vao_gom_tu_cac_lua_chon_va_mang_kieu_cua_gia_tri_mau() -> None:
    trigger = _node(_assemble("kb9-transform-doi-hinh-dang").document, "t")

    assert trigger["input_schema"] == {
        "type": "object",
        "additionalProperties": False,
        "required": ["so_tin"],
        "properties": {"so_tin": {"type": "integer"}},
    }


def test_tham_so_de_mac_dinh_cua_tool_thi_khong_co_trong_ir() -> None:
    """`limit: 6` là thứ model nắn theo ngưỡng 5 (ADR-0016, quyết định 6)."""
    lich_su = _node(_assemble("kb5b-co-transform-khong-dung").document, "lich_su")

    assert set(lich_su["input"]) == {"channel"}


def test_bon_loi_goi_thanh_mot_vong_lap() -> None:
    document = _assemble("kb3-notion-doc-sau").document

    assert [node["id"] for node in document["nodes"]] == ["t", "tim", "con", "moi_trang"]
    loop = _node(document, "moi_trang")
    assert loop["over"] == {"from_node": {"node": "con", "path": ["results"]}}
    assert loop["collect"] == "doc_trang"
    (body,) = loop["body"]
    assert body["input"] == {"page_id": {"from_item": ["id"]}}


def test_version_tool_khong_con_trong_catalog_thi_len_minor_moi_nhat() -> None:
    """Trace kb3 ghi `notion.search` 1.0; catalog giờ chỉ còn 1.1."""
    skeleton, _ = _questions("kb3-notion-doc-sau")

    assert skeleton.step("l1.b1").tool_version == "1.0"
    assert _node(_assemble("kb3-notion-doc-sau").document, "tim")["tool_version"] == "1.2"


@pytest.mark.parametrize(
    ("recorded", "expected"),
    [("1.0", "1.2"), ("1.1", "1.2"), ("1.2", "1.2"), ("2.0", "2.0"), ("0.9", "0.9")],
)
def test_khac_major_thi_giu_nguyen_de_validator_bao(recorded: str, expected: str) -> None:
    assert current_version(TOOLS, "notion.search", recorded) == expected


def test_node_llm_sinh_dung_thu_ma_truong_tieu_thu_no_doi() -> None:
    """`output_schema` cắt từ tài liệu tool, không do người trả lời khai."""
    document = _assemble("kb4-bac-cau").document
    tom_tat = _node(document, "tom_tat")
    blocks = TOOLS[("notion.create_page", "2.0")]["properties"]["blocks"]

    assert tom_tat["output_schema"]["properties"] == {"blocks": blocks}
    assert tom_tat["output_schema"]["required"] == ["blocks"]
    assert "block" in tom_tat["output_schema"]["$defs"]
    assert tom_tat["input"] == {"messages": {"from_node": {"node": "doc", "path": ["messages"]}}}
    assert _node(document, "tao")["input"]["blocks"] == {
        "from_node": {"node": "tom_tat", "path": ["blocks"]}
    }


def test_code_agent_da_chay_duoc_be_nguyen_va_args_tro_ve_ca_mang() -> None:
    """kb9: model chỉ chép trường `text` vào `args`; binding lấy cả `messages`."""
    doi = _node(_assemble("kb9-transform-doi-hinh-dang").document, "doi")

    assert doi["type"] == "transform"
    assert doi["code"].startswith("return [{")
    assert doi["args"] == {"messages": {"from_node": {"node": "doc", "path": ["messages"]}}}
    assert doi["output_schema"] == {"type": "array"}


def test_dieu_kien_trace_khong_ghi_thanh_transform_va_condition() -> None:
    """ADR-0016: phép đếm do người trả lời viết, ngưỡng nằm ở vế phải nên làm đầu vào được."""
    document = _assemble("kb5b-co-transform-khong-dung").document

    assert document["schema_version"] == "2.1"
    assert [node["id"] for node in document["nodes"]] == ["t", "lich_su", "dem", "du_nhieu", "dang"]
    assert _node(document, "dem") == {
        "id": "dem",
        "type": "transform",
        "code": "return len(args['messages'])",
        "args": {"messages": {"from_node": {"node": "lich_su", "path": ["messages"]}}},
        "output_schema": {"type": "integer"},
    }
    assert _node(document, "du_nhieu") == {
        "id": "du_nhieu",
        "type": "condition",
        "left": {"from_node": {"node": "dem", "path": []}},
        "op": "gt",
        "right": {"from_input": "nguong"},
    }
    assert document["edges"][-1] == {"from": "du_nhieu", "to": "dang", "branch": "true"}


def test_moi_cho_trong_ir_truy_ve_duoc_cau_hoi_da_quyet_dinh_no() -> None:
    compiled = _assemble("kb5b-co-transform-khong-dung")

    assert compiled.origin[("lich_su", ("input", "channel"))] == "N2"
    assert compiled.owners == {
        "lich_su": "l1.b1",
        "dem": "tinh_toan:dem",
        "du_nhieu": "dieu_kien:du_nhieu",
        "dang": "l1.b2",
    }
    assert compiled.written == {"dem"}
    assert compiled.samples == {"nguong": 5}

    loop = _assemble("kb3-notion-doc-sau")
    assert loop.origin[("moi_trang", ("over",))] == "V1"
    assert (
        loop.traced["moi_trang"] == loop.traced["doc_trang"] == ("l1.b4", "l1.b5", "l1.b6", "l1.b7")
    )


# ─── Câu hỏi ─────────────────────────────────────────────────────────────────


def test_gia_tri_co_nguon_o_buoc_truoc_duoc_chon_theo_chi_so_hoac_theo_ten() -> None:
    _, questions = _questions("kb2-slack-doc-roi-ghi")
    channel = questions.sources[1]

    assert channel.path == ("channel",)
    assert channel.choices == (Choice.CONST, Choice.INPUT, Choice.FROM_STEP)
    (candidate,) = channel.candidates
    assert candidate.element is not None
    assert [key.field for key in candidate.element.keys] == ["name"]


def test_tham_so_bat_buoc_khong_duoc_de_mac_dinh() -> None:
    _, questions = _questions("kb4-bac-cau")
    by_field = {question.field: question for question in questions.sources}

    assert Choice.TOOL_DEFAULT in by_field["limit"].choices
    assert Choice.TOOL_DEFAULT not in by_field["channel"].choices
    assert by_field["limit"].said_in == (1,)


def test_ca_mot_mang_khong_nguon_la_mot_cau_hoi_khong_phai_hai_muoi_hai() -> None:
    """P4: 22 literal của `blocks` không cái nào có nguồn. Binding của IR chỉ tới tầng
    trường, nên câu hỏi cũng vậy."""
    _, questions = _questions("kb4-bac-cau")
    blocks = next(question for question in questions.sources if question.field == "blocks")

    assert len(questions.sources) == 5
    assert blocks.choices == (Choice.CONST, Choice.TOOL_DEFAULT, Choice.LLM)


def test_mang_co_nguon_thi_khong_moi_node_llm() -> None:
    _, questions = _questions("kb9-transform-doi-hinh-dang")
    messages = questions.sources[-1]

    assert messages.path == ("args", "messages")
    assert messages.choices == (Choice.CONST, Choice.FROM_STEP)
    assert messages.candidates[0].exact is False


def test_node_llm_va_transform_chi_doc_duoc_output_da_liet_ke() -> None:
    _, questions = _questions("kb9-transform-doi-hinh-dang")

    assert [ref.key for ref in questions.data] == [
        "l1.b1:has_more",
        "l1.b1:messages",
        "l1.b1:next_cursor",
        "l1.b2:result",
    ]


@pytest.mark.parametrize("name", [*COMPILED, "kb6-hong-giua-chung"])
def test_hinh_dang_cau_tra_loi_la_json_schema_hop_le_va_nhan_dap_an(name: str) -> None:
    schema = answer_schema(_questions(name)[1])

    jsonschema.Draft202012Validator.check_schema(schema)
    assert list(jsonschema.Draft202012Validator(schema).iter_errors(_raw(name))) == []


# ─── Tầng kiểm 1 ─────────────────────────────────────────────────────────────


def _changed(name: str, mutate: Any) -> dict[str, Any]:
    raw = copy.deepcopy(_raw(name))
    mutate(raw)
    return raw


def test_lay_gia_tri_tu_mot_buoc_la_giu_buoc_do() -> None:
    """kb7: bỏ lượt *liệt kê kênh* trong khi id kênh của lượt 4 lấy từ nó."""
    raw = _changed("kb7-buoc-thua", lambda r: r["buoc"].update({"l1.b1": {"chon": "bo"}}))

    assert _codes("kb7-buoc-thua", raw) == [("N6", "nguon_la_buoc_da_bo")]


def test_lua_chon_ngoai_tap_bi_chan_o_hinh_dang_va_mang_id_cau_hoi() -> None:
    """kb1 không có bước nào trước, nên `from_step` không nằm trong tập của N1."""
    raw = _changed("kb1-slack-ghi", lambda r: r["gia_tri"].update({"N1": {"chon": "from_step"}}))

    answers, issues = _read("kb1-slack-ghi", raw)

    assert answers is None
    assert {(issue.question, issue.code) for issue in issues} == {("N1", "sai_hinh_dang")}


def test_cau_hoi_khong_ton_tai_bi_chan_o_hinh_dang() -> None:
    raw = _changed("kb1-slack-ghi", lambda r: r["gia_tri"].update({"N99": {"chon": "const"}}))

    assert _codes("kb1-slack-ghi", raw) == [("", "sai_hinh_dang")]


def test_buoc_duoc_giu_ma_thieu_cau_tra_loi() -> None:
    raw = _changed("kb7-buoc-thua", lambda r: r["gia_tri"].pop("N4"))

    assert _codes("kb7-buoc-thua", raw) == [("N4", "thieu_cau_tra_loi")]


def test_buoc_da_bo_thi_khong_can_tra_loi_ve_no() -> None:
    """kb7 bỏ `l2.b1` và `l3.b1`; đáp án không có N1, N2, N3."""
    raw = _raw("kb7-buoc-thua")

    assert set(raw["gia_tri"]) == {"N4", "N5", "N6"}
    assert _codes("kb7-buoc-thua", raw) == []


def test_hai_dau_vao_trung_ten_ma_khac_gia_tri() -> None:
    raw = _changed(
        "kb1-slack-ghi",
        lambda r: r["gia_tri"].update({"N2": {"chon": "workflow_input", "ten": "noi_dung"}}),
    )

    assert _codes("kb1-slack-ghi", raw) == [("N2", "dau_vao_trung_ten")]


def test_hai_cho_cung_gia_tri_dung_chung_mot_dau_vao() -> None:
    """Kênh được đọc và kênh được đăng là một: một đầu vào, không phải hai."""

    def chung(raw: dict[str, Any]) -> None:
        raw["gia_tri"]["N2"] = {"chon": "workflow_input", "ten": "kenh"}
        raw["gia_tri"]["N4"] = {"chon": "workflow_input", "ten": "kenh"}

    name = "kb5b-co-transform-khong-dung"
    skeleton, questions = _questions(name)
    answers, issues = read_answers(questions, skeleton, _changed(name, chung))

    assert issues == ()
    assert answers is not None
    document = assemble(skeleton, questions, answers, TOOLS, llm_model="m").document
    assert _node(document, "t")["input_schema"]["required"] == ["kenh", "nguong"]


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda r: r["gia_tri"].update({"N1": {"chon": "workflow_input"}}),
            ("N1", "ten_dau_vao_sai"),
        ),
        (lambda r: r["gia_tri"].update({"N2": {"chon": "from_step"}}), ("N2", "thieu_ung_vien")),
        (
            lambda r: r["gia_tri"].update(
                {"N2": {"chon": "from_step", "ung_vien": 1, "khoa": "name"}}
            ),
            ("N2", "thieu_nguon_khoa"),
        ),
        (
            lambda r: r["buoc"].update({"l1.b1": {"chon": "giu", "id": "dang"}}),
            ("l1.b2", "id_trung"),
        ),
        (
            lambda r: r["buoc"].update({"l1.b2": {"chon": "giu", "id": "t"}}),
            ("l1.b2", "id_trung"),
        ),
    ],
    ids=["thieu_ten_dau_vao", "thieu_ung_vien", "thieu_nguon_khoa", "id_trung", "id_cua_trigger"],
)
def test_cau_tra_loi_thieu_hoac_trung(mutate: Any, expected: tuple[str, str]) -> None:
    assert _codes("kb2-slack-doc-roi-ghi", _changed("kb2-slack-doc-roi-ghi", mutate)) == [expected]


def test_gop_vong_lap_thi_moi_buoc_cua_no_phai_duoc_giu() -> None:
    raw = _changed("kb3-notion-doc-sau", lambda r: r["buoc"].update({"l1.b5": {"chon": "bo"}}))

    assert _codes("kb3-notion-doc-sau", raw) == [("V1", "vong_lap_thieu_buoc")]


def test_cum_buoc_lap_ma_chua_chon_gop_hay_de_rieng() -> None:
    raw = _changed("kb3-notion-doc-sau", lambda r: r.pop("vong_lap"))

    assert ("V1", "thieu_cau_tra_loi") in _codes("kb3-notion-doc-sau", raw)


def test_de_rieng_thi_tung_buoc_phai_co_cau_tra_loi_va_ir_trai_phang() -> None:
    def trai_phang(raw: dict[str, Any]) -> None:
        raw["vong_lap"]["V1"] = {"chon": "trai_phang"}
        for qid in ("N4", "N5", "N6", "N7"):
            raw["gia_tri"][qid] = {"chon": "from_step", "ung_vien": 1}

    name = "kb3-notion-doc-sau"
    skeleton, questions = _questions(name)
    answers, issues = read_answers(questions, skeleton, _changed(name, trai_phang))

    assert issues == ()
    assert answers is not None
    document = assemble(skeleton, questions, answers, TOOLS, llm_model="m").document
    assert [node["type"] for node in document["nodes"]] == ["trigger", *["tool"] * 6]
    assert document["nodes"][-1]["input"]["page_id"] == {
        "from_node": {"node": "con", "path": ["results", "3", "id"]}
    }


KB8 = "kb8-ba-luot"


def _bo(raw: dict[str, Any], *steps: str) -> None:
    for step in steps:
        raw["buoc"][step] = {"chon": "bo", "ly_do": "Không thuộc nhiệm vụ."}


def _chi_lan_dang_tin(raw: dict[str, Any]) -> None:
    """Thân vòng lặp chỉ còn đọc khối và đăng tin; lượt 3 đứng ngoài."""
    raw["vong_lap"]["V1"]["lan"] = [1]
    _bo(raw, *(f"l3.b{n}" for n in range(2, 13)))
    raw["llm"] = [llm for llm in raw["llm"] if llm["id"] == "soan_cau_hoi"]
    del raw["gia_tri"]["N27"], raw["gia_tri"]["N28"]


def test_ben_ngoai_khong_doc_duoc_mot_buoc_trong_than_vong_lap() -> None:
    """kb8, lượt 3: đọc khối con của một kết quả nằm trong vòng lặp của lượt 1."""

    def doc_tu_ngoai(raw: dict[str, Any]) -> None:
        _chi_lan_dang_tin(raw)
        raw["gia_tri"]["N21"] = {"chon": "from_step", "ung_vien": 1}

    assert _codes(KB8, _changed(KB8, doc_tu_ngoai)) == [("N21", "nguon_trong_vong_lap")]


# ─── Vòng lặp nhiều làn ──────────────────────────────────────────────────────


def test_ba_luot_cua_kb8_thanh_mot_vong_lap_sau_node() -> None:
    document = _assemble(KB8).document

    # Thân vòng lặp đọc danh sách kênh, bước mà trace chạy sau làn gốc.
    assert [node["id"] for node in document["nodes"]] == ["t", "tim", "khoi", "kenh", "moi_cau"]
    loop = _node(document, "moi_cau")
    assert [(node["id"], node["type"]) for node in loop["body"]] == [
        ("doc_khoi", "tool"),
        ("doc_dap_an", "tool"),
        ("soan_cau_hoi", "llm"),
        ("dang_cau_hoi", "tool"),
        ("soan_dap_an", "llm"),
        ("tra_loi", "tool"),
    ]
    assert loop["collect"] == "tra_loi"


# ─── Đọc trước, ghi sau ──────────────────────────────────────────────────────


def _than(mutate: Any = None, *, mutating: Any = MUTATING) -> list[str]:
    skeleton = build_skeleton(read_source(_json(FIXTURES / "sources" / f"{KB8}.json")))
    questions = build_questions(skeleton, find_sources(skeleton), TOOLS, mutating)
    raw = _changed(KB8, mutate) if mutate is not None else _raw(KB8)
    answers, issues = read_answers(questions, skeleton, raw)
    assert issues == ()
    assert answers is not None
    document = assemble(skeleton, questions, answers, TOOLS, llm_model="m").document
    return [node["id"] for node in _node(document, "moi_cau")["body"] if node["type"] != "llm"]


def test_buoc_doc_len_truoc_buoc_ghi_trong_moi_vong() -> None:
    """Lần chạy mẫu đăng câu hỏi rồi ở một lượt sau mới đọc đáp án. Đọc đáp án không cần gì
    của tin đã đăng, nên nó chạy trước: hỏng thì hỏng khi chưa có gì lên Slack."""
    assert _than() == ["doc_khoi", "doc_dap_an", "dang_cau_hoi", "tra_loi"]


def test_buoc_ghi_can_ket_qua_cua_buoc_ghi_khac_van_dung_sau_no() -> None:
    than = _than()

    # `tra_loi.thread_ts` lấy từ `dang_cau_hoi.message_ts` của cùng vòng.
    assert than.index("dang_cau_hoi") < than.index("tra_loi")


def test_khong_biet_tool_nao_ghi_thi_giu_thu_tu_cua_lan_chay_mau() -> None:
    assert _than(mutating=frozenset()) == ["doc_khoi", "dang_cau_hoi", "doc_dap_an", "tra_loi"]


def test_node_llm_van_dung_ngay_truoc_buoc_dung_no_va_sau_du_lieu_no_doc() -> None:
    body = [node["id"] for node in _node(_assemble(KB8).document, "moi_cau")["body"]]

    assert body.index("doc_dap_an") < body.index("soan_dap_an") < body.index("tra_loi")
    assert body.index("soan_cau_hoi") + 1 == body.index("dang_cau_hoi")


# ─── Bộ lọc đề nghị ──────────────────────────────────────────────────────────


def test_bo_loc_duoc_chon_di_vao_vong_lap() -> None:
    assert _node(_assemble(KB8).document, "moi_cau")["filter"] == {
        "type": {"const": "numbered_list_item"}
    }


def test_khong_chon_bo_loc_thi_vong_lap_chay_tren_moi_phan_tu() -> None:
    skeleton, questions = _questions(KB8)
    raw = _changed(KB8, lambda r: r["vong_lap"]["V1"].pop("loc"))
    answers, issues = read_answers(questions, skeleton, raw)

    assert issues == ()
    assert answers is not None
    document = assemble(skeleton, questions, answers, TOOLS, llm_model="m").document
    assert "filter" not in _node(document, "moi_cau")


def test_truong_khong_duoc_de_nghi_thi_bi_hinh_dang_chan() -> None:
    raw = _changed(KB8, lambda r: r["vong_lap"]["V1"].update({"loc": ["has_children"]}))

    assert _codes(KB8, raw) == [("V1", "sai_hinh_dang")]


def test_kb3_cung_duoc_de_nghi_loc_theo_loai_khoi() -> None:
    """Bốn khối của kb3 đều là `child_page` và đều được đọc: không bắt buộc lọc, nhưng lọc được."""
    skeleton, questions = _questions("kb3-notion-doc-sau")
    (question,) = questions.loops
    assert (question.loop.filter, question.loop.suggested) == ((), (("type", "child_page"),))

    raw = _changed("kb3-notion-doc-sau", lambda r: r["vong_lap"]["V1"].update({"loc": ["type"]}))
    answers, issues = read_answers(questions, skeleton, raw)
    assert issues == ()
    assert answers is not None
    document = assemble(skeleton, questions, answers, TOOLS, llm_model="m").document
    assert _node(document, "moi_trang")["filter"] == {"type": {"const": "child_page"}}


def test_truong_cua_lan_lay_tu_node_cung_vong() -> None:
    body = {node["id"]: node for node in _node(_assemble(KB8).document, "moi_cau")["body"]}

    assert body["doc_khoi"]["input"]["block_id"] == {"from_item": ["id"]}
    assert body["doc_dap_an"]["input"]["block_id"] == {
        "from_node": {
            "node": "doc_khoi",
            "path": ["results"],
            "where": {"type": {"const": "toggle"}},
            "pick": ["id"],
        }
    }
    assert body["tra_loi"]["input"]["thread_ts"] == {
        "from_node": {"node": "dang_cau_hoi", "path": ["message_ts"]}
    }
    assert body["soan_dap_an"]["input"] == {
        "dap_an": {"from_node": {"node": "doc_dap_an", "path": ["results"]}}
    }


def test_loi_tren_truong_cua_lan_truy_ve_cau_hoi_vong_lap() -> None:
    origin = _assemble(KB8).origin

    assert origin[("doc_dap_an", ("input", "block_id"))] == "V1"
    assert origin[("tra_loi", ("input", "thread_ts"))] == "V1"
    assert origin[("tra_loi", ("input", "text"))] == "N27"


def test_node_llm_trong_than_doc_duoc_phan_tu_dang_lap() -> None:
    def doc_phan_tu(raw: dict[str, Any]) -> None:
        raw["llm"][0]["dau_vao"] = {"khoi": "V1:phan_tu"}

    skeleton, questions = _questions(KB8)
    answers, issues = read_answers(questions, skeleton, _changed(KB8, doc_phan_tu))

    assert issues == ()
    assert answers is not None
    document = assemble(skeleton, questions, answers, TOOLS, llm_model="m").document
    body = {node["id"]: node for node in _node(document, "moi_cau")["body"]}
    assert body["soan_cau_hoi"]["input"] == {"khoi": {"from_item": []}}


def _thieu_lan_nguon(raw: dict[str, Any]) -> None:
    raw["vong_lap"]["V1"]["lan"] = [2, 3]
    _bo(raw, "l2.b1", *(f"l2.b{n}" for n in range(2, 8)))
    raw["llm"] = raw["llm"][1:]
    del raw["gia_tri"]["N9"], raw["gia_tri"]["N10"]
    raw["gia_tri"]["N28"] = {"chon": "const"}


def _bo_mot_buoc_cua_lan(raw: dict[str, Any]) -> None:
    _bo(raw, "l3.b9")


def _llm_ngoai_doc_phan_tu(raw: dict[str, Any]) -> None:
    _chi_lan_dang_tin(raw)
    _bo(raw, "l3.b1")
    raw["vong_lap"]["V1"]["lan"] = []
    _bo(raw, *(f"l2.b{n}" for n in range(3, 8)))
    raw["llm"][0]["dau_vao"] = {"khoi": "V1:phan_tu"}


def _llm_ngoai_doc_buoc_trong_than(raw: dict[str, Any]) -> None:
    _llm_ngoai_doc_phan_tu(raw)
    raw["llm"][0]["dau_vao"] = {"khoi": "l1.b3:results"}


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (_thieu_lan_nguon, [("V1", "lan_thieu_nguon")]),
        (_bo_mot_buoc_cua_lan, [("V1", "vong_lap_thieu_buoc")]),
        (_llm_ngoai_doc_phan_tu, [("llm:soan_cau_hoi", "phan_tu_ngoai_vong_lap")]),
        (_llm_ngoai_doc_buoc_trong_than, [("llm:soan_cau_hoi", "nguon_trong_vong_lap")]),
    ],
)
def test_lan_gop_sai_bi_tang_mot_chan(mutate: Any, expected: list[tuple[str, str]]) -> None:
    assert _codes(KB8, _changed(KB8, mutate)) == expected


def test_so_lan_ngoai_tap_bi_hinh_dang_chan() -> None:
    raw = _changed(KB8, lambda r: r["vong_lap"]["V1"].update({"lan": [4]}))

    assert _codes(KB8, raw) == [("V1", "sai_hinh_dang")]


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda r: r.update({"llm": []}), [("N4", "llm_khong_ton_tai")]),
        (
            lambda r: r["gia_tri"].update({"N4": {"chon": "const"}}),
            [("llm:tom_tat", "llm_khong_ai_dung")],
        ),
        (
            lambda r: r["llm"][0]["dau_vao"].update({"trang": "l1.b2:url"}),
            [("llm:tom_tat", "doc_buoc_chua_chay")],
        ),
    ],
    ids=["node_chua_khai", "node_khong_ai_dung", "doc_ket_qua_cua_chinh_buoc_dung_no"],
)
def test_node_llm_phai_co_nguoi_dung_va_chi_doc_buoc_da_chay(
    mutate: Any, expected: list[tuple[str, str]]
) -> None:
    assert _codes("kb4-bac-cau", _changed("kb4-bac-cau", mutate)) == expected


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda r: r["dieu_kien"][0].update({"trai": "khong_co"}), "ve_trai_khong_ton_tai"),
        (lambda r: r["dieu_kien"][0].pop("phai"), "thieu_ve_phai"),
        (
            lambda r: r["dieu_kien"][0].update(
                {"phai": {"chon": "du_lieu", "nguon": "l1.b1:messages"}}
            ),
            "ve_phai_sai",
        ),
        (lambda r: r["dieu_kien"][0].update({"op": "exists"}), "ve_phai_thua"),
        (lambda r: r["dieu_kien"][0].update({"truoc_buoc": "l1.b1"}), "doc_buoc_chua_chay"),
    ],
    ids=[
        "ve_trai_la",
        "thieu_ve_phai",
        "ve_phai_la_du_lieu",
        "exists_co_ve_phai",
        "tinh_sau_khi_so",
    ],
)
def test_dieu_kien_phai_dung_duoc(mutate: Any, expected: str) -> None:
    name = "kb5b-co-transform-khong-dung"

    assert _codes(name, _changed(name, mutate)) == [("dieu_kien:du_nhieu", expected)]


def test_phep_tinh_khong_doc_duoc_buoc_chay_sau_no() -> None:
    name = "kb5b-co-transform-khong-dung"
    raw = _changed(
        name,
        lambda r: r["tinh_toan"][0]["args"].update(
            {"da_dang": {"chon": "du_lieu", "nguon": "l1.b2:message_ts"}}
        ),
    )

    assert _codes(name, raw) == [("tinh_toan:dem", "doc_buoc_chua_chay")]
