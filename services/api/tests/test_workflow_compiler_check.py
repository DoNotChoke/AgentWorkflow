"""GĐ 4 của compiler: ba tầng kiểm trong một lần, và mỗi lỗi chỉ đúng câu hỏi gây ra nó.

Sandbox ở đây là sandbox thật: code của node `transform` được chạy, không giả lập.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from app.core.errors import WorkflowExecutionError
from app.core.sandbox import run as run_in_sandbox
from app.domains.integration.infrastructure.catalog_view import tool_rows
from app.domains.workflow.application.compile_check import Checked, check_answers
from app.domains.workflow.domain.bindings import Scope
from app.domains.workflow.domain.compiler.answers import read_answers
from app.domains.workflow.domain.compiler.assemble import Assembled, assemble
from app.domains.workflow.domain.compiler.normalize import read_source
from app.domains.workflow.domain.compiler.provenance import find_sources
from app.domains.workflow.domain.compiler.questions import QuestionSet, build_questions
from app.domains.workflow.domain.compiler.replay import replay
from app.domains.workflow.domain.compiler.report import Tier, from_document
from app.domains.workflow.domain.compiler.skeleton import Skeleton, build_skeleton
from app.domains.workflow.domain.compiler.source import SourceMaterial, SourceStep, Turn
from app.domains.workflow.domain.conditions import holds
from app.domains.workflow.domain.ir import ConditionNode, ConditionOp, Const, FromNode
from app.domains.workflow.domain.validation import Issue, Severity
from app.domains.workflow.infrastructure.catalog import RowToolCatalog

FIXTURES = Path(__file__).parent / "fixtures"
CATALOG = RowToolCatalog(tool_rows())
TOOLS = {(row["tool_id"], row["version"]): row["input_schema"] for row in tool_rows()}

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


class Sandbox:
    """`TransformRunner` thật, và đếm số lần code được chạy."""

    def __init__(self) -> None:
        self.runs = 0

    def run(self, code: str, args: Mapping[str, Any]) -> Any:
        self.runs += 1
        return run_in_sandbox(code, dict(args)).value


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _material(name: str) -> tuple[Skeleton, QuestionSet]:
    skeleton = build_skeleton(read_source(_json(FIXTURES / "sources" / f"{name}.json")))
    return skeleton, build_questions(skeleton, find_sources(skeleton), TOOLS)


def _raw(name: str, mutate: Any = None) -> dict[str, Any]:
    raw = copy.deepcopy(_json(FIXTURES / "compiled" / f"{name}.answers.json"))
    if mutate is not None:
        mutate(raw)
    return raw


def _check(
    skeleton: Skeleton, questions: QuestionSet, raw: dict[str, Any], sandbox: Sandbox | None = None
) -> Checked:
    return check_answers(
        skeleton,
        questions,
        raw,
        tools=TOOLS,
        catalog=CATALOG,
        runner=sandbox or Sandbox(),
        llm_model="claude-sonnet-5",
    )


def _checked(name: str, mutate: Any = None, sandbox: Sandbox | None = None) -> Checked:
    return _check(*_material(name), _raw(name, mutate), sandbox)


def _errors(checked: Checked) -> list[tuple[str, str, str]]:
    return [(str(issue.tier), issue.question, issue.code) for issue in checked.report.errors]


# ─── Đáp án đi qua cả ba tầng ────────────────────────────────────────────────


@pytest.mark.parametrize("name", COMPILED)
def test_dap_an_viet_tay_qua_ca_ba_tang(name: str) -> None:
    checked = _checked(name)

    assert _errors(checked) == []
    assert checked.assembled is not None
    assert {issue.code for issue in checked.report.warnings} <= {"chi_so_tho"}


def test_canh_bao_cung_mang_id_cau_hoi() -> None:
    """kb3 chọn trang đầu tiên của kết quả tìm kiếm: chạy được, và N3 là chỗ cần xem lại."""
    (warning,) = _checked("kb3-notion-doc-sau").report.warnings

    assert (warning.tier, warning.question, warning.code) == (Tier.DOCUMENT, "N3", "chi_so_tho")


def test_code_cua_agent_duoc_chay_lai_tren_du_lieu_day_du() -> None:
    """kb10: Agent chạy code trên bản tin nhắn nó chép tay, chỉ còn `ts` và `user`. Binding
    đưa vào cả mảng. Đếm theo người gửi phải ra đúng con số cũ."""
    sandbox = Sandbox()

    assert _errors(_checked("kb10-transform-dem", sandbox=sandbox)) == []
    assert sandbox.runs == 1


def test_khong_co_transform_thi_khong_chay_gi_ca() -> None:
    sandbox = Sandbox()

    _checked("kb2-slack-doc-roi-ghi", sandbox=sandbox)

    assert sandbox.runs == 0


# ─── Tầng sau chỉ chạy khi tầng trước sạch ───────────────────────────────────


def test_loi_o_tang_1_thi_khong_lap_va_khong_chay_code() -> None:
    sandbox = Sandbox()
    checked = _checked(
        "kb5b-co-transform-khong-dung",
        lambda raw: raw["buoc"].update({"l1.b1": {"chon": "bo"}}),
        sandbox,
    )

    assert checked.assembled is None
    assert {issue.tier for issue in checked.report.issues} == {Tier.ANSWERS}
    assert sandbox.runs == 0


def test_sai_hinh_dang_thi_khong_co_ca_cau_tra_loi() -> None:
    checked = _checked("kb1-slack-ghi", lambda raw: raw.pop("ten"))

    assert checked.answers is None
    assert _errors(checked) == [("cau_tra_loi", "", "sai_hinh_dang")]


# ─── ADR-0016: code do người trả lời viết ────────────────────────────────────

KB5 = "kb5b-co-transform-khong-dung"
GOC = "00000000-0000-0000-0000-000000000001"


def _code(code: str, result: str = "integer") -> Any:
    def mutate(raw: dict[str, Any]) -> None:
        raw["tinh_toan"][0].update({"code": code, "ket_qua": result})

    return mutate


def test_dieu_kien_phai_ra_dung_nhanh_ma_trace_da_di() -> None:
    """Kênh có 6 tin, Agent đã đăng. `< 5` thì workflow sẽ không bao giờ đăng."""
    checked = _checked(KB5, lambda raw: raw["dieu_kien"][0].update({"op": "lt"}))

    assert _errors(checked) == [("doi_chieu", "dieu_kien:du_nhieu", "dieu_kien_sai_nhanh")]


def test_code_dem_sai_lam_dieu_kien_ra_sai_nhanh() -> None:
    checked = _checked(KB5, _code("return 0 - len(args['messages'])"))

    assert _errors(checked) == [("doi_chieu", "dieu_kien:du_nhieu", "dieu_kien_sai_nhanh")]


def test_code_vo_luc_chay_la_loi_cua_phep_tinh() -> None:
    checked = _checked(KB5, _code("return len(args['tin_nhan'])"))

    assert _errors(checked) == [("doi_chieu", "tinh_toan:dem", "code_khong_chay_duoc")]
    assert "KeyError" in checked.report.errors[0].message


def test_ket_qua_khong_dung_kieu_da_khai() -> None:
    checked = _checked(KB5, _code("return str(len(args['messages']))"))

    assert ("doi_chieu", "tinh_toan:dem", "ket_qua_sai_kieu") in _errors(checked)


def test_code_khong_qua_vong_duyet_bi_chan_truoc_khi_chay() -> None:
    sandbox = Sandbox()
    checked = _checked(KB5, _code("import os\nreturn 1"), sandbox)

    assert _errors(checked) == [("tai_lieu", "tinh_toan:dem", "code_khong_an_toan")]
    assert sandbox.runs == 0


def test_nguong_viet_cung_trong_code_la_loi_voi_code_do_nguoi_tra_loi_viet() -> None:
    """Số 5 nằm trong chuỗi code thì không có địa chỉ, không hỏi được *hằng hay đầu vào*."""

    def viet_cung(raw: dict[str, Any]) -> None:
        raw["tinh_toan"][0].update(
            {"code": "return len(args['messages']) > 5", "ket_qua": "boolean"}
        )
        raw["dieu_kien"][0].update({"op": "eq", "phai": {"chon": "const", "gia_tri": True}})

    checked = _checked(KB5, viet_cung)

    assert _errors(checked) == [("tai_lieu", "tinh_toan:dem", "hang_viet_cung")]


def test_hang_trong_code_agent_da_chay_van_chi_la_canh_bao() -> None:
    document = _assembled("kb9-transform-doi-hinh-dang")
    issue = Issue("hang_viet_cung", "…", severity=Severity.WARNING, node="doi", field=("code",))

    (located,) = from_document(Tier.DOCUMENT, [issue], document).issues

    assert (located.question, located.severity) == ("l1.b2", Severity.WARNING)


# ─── Tầng 3 trên IR bị làm hỏng ──────────────────────────────────────────────


def _assembled(name: str) -> Assembled:
    skeleton, questions = _material(name)
    answers, issues = read_answers(questions, skeleton, _raw(name))
    assert issues == ()
    assert answers is not None
    return assemble(skeleton, questions, answers, TOOLS, llm_model="claude-sonnet-5")


def _replayed(name: str, mutate: Any) -> list[tuple[str, str, str | None]]:
    skeleton, _ = _material(name)
    assembled = _assembled(name)
    mutate(assembled.document)
    found = replay(skeleton, assembled, Sandbox().run)
    return [
        (issue.question, issue.code, issue.node)
        for issue in from_document(Tier.REPLAY, found, assembled).issues
    ]


def _node(document: dict[str, Any], node_id: str) -> dict[str, Any]:
    return next(node for node in document["nodes"] if node["id"] == node_id)


def test_binding_tro_sai_phan_tu_thi_khong_tai_tao_duoc_loi_goi() -> None:
    """Đúng kiểu, sai chỗ: kênh đầu tiên thay vì kênh `test`. Validator không thấy."""

    def kenh_dau(document: dict[str, Any]) -> None:
        _node(document, "dang")["input"]["channel"] = {
            "from_node": {"node": "kenh", "path": ["channels", "0", "id"]}
        }

    assert _replayed("kb2-slack-doc-roi-ghi", kenh_dau) == [("N2", "khong_tai_tao_duoc", "dang")]


def test_khoa_khong_chon_duoc_phan_tu_nao_thi_khong_tinh_duoc() -> None:
    def ten_khac(document: dict[str, Any]) -> None:
        where = _node(document, "dang")["input"]["channel"]["from_node"]["where"]
        where["name"] = {"const": "kenh-khong-co"}

    assert _replayed("kb7-buoc-thua", ten_khac) == [("N6", "khong_tinh_duoc", "dang")]


def test_trong_than_vong_lap_tung_vong_duoc_so_voi_tung_loi_goi() -> None:
    def lay_url(document: dict[str, Any]) -> None:
        body = _node(document, "moi_trang")["body"][0]
        body["input"]["page_id"] = {"from_item": ["type"]}

    found = _replayed("kb3-notion-doc-sau", lay_url)

    assert found == [("V1", "khong_tai_tao_duoc", "doc_trang")] * 4


# ─── Tầng 3 trên nguyên liệu viết tay ────────────────────────────────────────


def _call(
    ref: str, tool: str, sent: dict[str, Any], received: dict[str, Any], version: str | None = "1.0"
) -> SourceStep:
    return SourceStep(
        ref=ref,
        step_id=f"stp_{ref}",
        tool_id=tool,
        tool_version=version,
        input=sent,
        output=received,
    )


def _made(request: str, steps: list[SourceStep]) -> tuple[Skeleton, QuestionSet]:
    skeleton = build_skeleton(
        SourceMaterial(
            conversation_id="cnv_1",
            turns=(
                Turn(
                    index=1,
                    trace_id="trc_1",
                    status="succeeded",
                    request=request,
                    reply=None,
                    steps=tuple(steps),
                ),
            ),
        )
    )
    return skeleton, build_questions(skeleton, find_sources(skeleton), TOOLS)


def test_vong_lap_chay_nhieu_vong_hon_trace_ma_khong_co_gi_de_loc() -> None:
    """Ba khối, Agent chỉ đọc hai, và không trường nào phân biệt hai khối đó với khối kia."""
    khoi = {
        "results": [
            {"id": "blk_aaa", "type": "child_page"},
            {"id": "blk_bbb", "type": "child_page"},
            {"id": "blk_ccc", "type": "child_page"},
        ]
    }
    skeleton, questions = _made(
        "đọc các trang con",
        [
            _call("l1.b1", "notion.get_block_children", {"block_id": GOC}, khoi),
            _call("l1.b2", "notion.get_page", {"page_id": "blk_aaa"}, {"id": "blk_aaa"}),
            _call("l1.b3", "notion.get_page", {"page_id": "blk_ccc"}, {"id": "blk_ccc"}),
        ],
    )
    raw = {
        "ten": "Đọc trang con",
        "buoc": {"l1.b1": {"chon": "giu"}, "l1.b2": {"chon": "giu"}, "l1.b3": {"chon": "giu"}},
        "gia_tri": {"N1": {"chon": "const"}},
        "vong_lap": {"V1": {"chon": "loop"}},
    }

    assert _errors(_check(skeleton, questions, raw)) == [
        ("doi_chieu", "V1", "vong_lap_lech_so_phan_tu")
    ]

    raw["vong_lap"]["V1"] = {"chon": "trai_phang"}
    raw["gia_tri"] |= {
        "N2": {"chon": "from_step", "ung_vien": 1},
        "N3": {"chon": "from_step", "ung_vien": 1},
    }
    assert _errors(_check(skeleton, questions, raw)) == []


def test_code_agent_cho_ket_qua_khac_tren_du_lieu_day_du() -> None:
    """Code đếm số trường của tin nhắn đầu tiên. Trên bản Agent chép tay nó ra 1; trên tin
    nhắn thật nó ra 3. Binding `args.messages ← messages` vì vậy không tái tạo được."""
    lich_su = {
        "messages": [
            {"ts": "1789638292.427749", "text": "tin thứ nhất", "user": "U0VIDU00001"},
            {"ts": "1789638200.709979", "text": "tin thứ hai", "user": "U0VIDU00002"},
        ]
    }
    skeleton, questions = _made(
        "mỗi tin nhắn có mấy trường",
        [
            _call("l1.b1", "slack.get_channel_history", {"channel": "C0VIDU00001"}, lich_su),
            _call(
                "l1.b2",
                "system.transform",
                {
                    "code": "return len(args['messages'][0])",
                    "args": {"messages": [{"text": "tin thứ nhất"}, {"text": "tin thứ hai"}]},
                },
                {"result": 1},
                version=None,
            ),
        ],
    )
    raw = {
        "ten": "Đếm trường",
        "buoc": {"l1.b1": {"chon": "giu"}, "l1.b2": {"chon": "giu"}},
        "gia_tri": {"N1": {"chon": "const"}, "N2": {"chon": "from_step", "ung_vien": 1}},
    }

    assert _errors(_check(skeleton, questions, raw)) == [
        ("doi_chieu", "l1.b2", "ket_qua_khac_lan_chay_mau")
    ]


# ─── Lỗi của validator truy về câu hỏi ───────────────────────────────────────


def test_tool_khong_con_trong_catalog_truy_ve_buoc_da_goi_no() -> None:
    document = _json(FIXTURES / "sources" / "kb1-slack-ghi.json")
    for step in document["traces"][0]["steps"]:
        if "tool_version" in step["attributes"]:
            step["attributes"]["tool_version"] = "9.0"
    skeleton = build_skeleton(read_source(document))
    questions = build_questions(skeleton, find_sources(skeleton), TOOLS)

    checked = _check(skeleton, questions, _raw("kb1-slack-ghi"))

    assert _errors(checked) == [("tai_lieu", "l1.b1", "tool_khong_ton_tai")]


@pytest.mark.parametrize(
    ("field", "question"),
    [
        (("input", "channel"), "N2"),
        (("input", "channel", "where", "name"), "N2"),
        (("tool_version",), "l1.b2"),
        ((), "l1.b2"),
    ],
)
def test_loi_truy_ve_truong_cu_the_nhat_roi_lui_ve_node(
    field: tuple[str, ...], question: str
) -> None:
    assembled = _assembled("kb2-slack-doc-roi-ghi")

    (located,) = from_document(
        Tier.DOCUMENT, [Issue("x", "…", node="dang", field=field)], assembled
    ).issues

    assert located.question == question
    assert not located.is_internal


@pytest.mark.parametrize("node", [None, "t", "node_khong_co"])
def test_loi_khong_truy_duoc_la_loi_cua_bo_lap_khong_phai_cua_cau_tra_loi(node: str | None) -> None:
    assembled = _assembled("kb2-slack-doc-roi-ghi")

    report = from_document(Tier.DOCUMENT, [Issue("x", "…", node=node)], assembled)

    assert [issue.question for issue in report.issues] == [""]
    assert len(report.internal) == 1


# ─── Điều kiện ───────────────────────────────────────────────────────────────


def _holds(left: Any, op: str, right: Any = None, *, has_right: bool = True) -> bool:
    node = ConditionNode(
        id="c",
        left=FromNode(node="a", path=()),
        op=ConditionOp(op),
        right=Const(right) if has_right else None,
    )
    return holds(node, Scope(inputs={}, outputs={"a": left}))


@pytest.mark.parametrize(
    ("left", "op", "right", "expected"),
    [
        (6, "gt", 5, True),
        (5, "gt", 5, False),
        (4, "lt", 5, True),
        (5.5, "gt", 5, True),
        ("b", "gt", "a", True),
        ("test", "eq", "test", True),
        ("test", "ne", "social", True),
        (1, "eq", True, False),
        (True, "eq", True, True),
        ("kênh test", "contains", "test", True),
        (["a", "b"], "contains", "b", True),
        (["a", "b"], "contains", "c", False),
        ({"a": 1}, "contains", "a", True),
    ],
)
def test_tung_toan_tu(left: Any, op: str, right: Any, expected: bool) -> None:
    assert _holds(left, op, right) is expected


def test_exists_hoi_ve_trai_co_gia_tri_khong() -> None:
    node = ConditionNode(
        id="c", left=FromNode(node="a", path=("messages", "5")), op=ConditionOp.EXISTS
    )

    assert holds(node, Scope(inputs={}, outputs={"a": {"messages": [0, 1, 2, 3, 4, 5]}})) is True
    assert holds(node, Scope(inputs={}, outputs={"a": {"messages": [0, 1]}})) is False


@pytest.mark.parametrize(
    ("left", "op", "right"),
    [(6, "gt", "5"), (True, "lt", 2), ([1], "gt", 0), (5, "contains", 5)],
    ids=["so_voi_chuoi", "bool_khong_co_thu_tu", "mang_khong_co_thu_tu", "so_khong_chua_gi"],
)
def test_hai_ve_khong_so_duoc_la_loi_cua_lan_chay(left: Any, op: str, right: Any) -> None:
    with pytest.raises(WorkflowExecutionError):
        _holds(left, op, right)


def test_ve_trai_vang_mat_la_loi_tru_khi_hoi_exists() -> None:
    node = ConditionNode(
        id="c", left=FromNode(node="chua_chay", path=()), op=ConditionOp.GT, right=Const(5)
    )

    with pytest.raises(WorkflowExecutionError):
        holds(node, Scope(inputs={}, outputs={}))


# ─── Một node llm, nhiều bước đọc ─────────────────────────────────────────────


def _hai_tin(thu_hai: str) -> tuple[Skeleton, QuestionSet, dict[str, Any]]:
    kenh = "C0VIDU00001"
    skeleton, questions = _made(
        "soạn một lời chào rồi đăng vào hai kênh",
        [
            _call(
                "l1.b1",
                "slack.post_message",
                {"channel": kenh, "text": "Chào cả nhà, tuần mới vui vẻ!"},
                {"message_ts": "1.1"},
            ),
            _call(
                "l1.b2",
                "slack.post_message",
                {"channel": kenh, "text": thu_hai},
                {"message_ts": "1.2"},
            ),
        ],
    )
    by_field = {(question.step, question.field): question.id for question in questions.sources}
    raw = {
        "ten": "Chào hai kênh",
        "buoc": {"l1.b1": {"chon": "giu"}, "l1.b2": {"chon": "giu"}},
        "gia_tri": {
            by_field[("l1.b1", "channel")]: {"chon": "const"},
            by_field[("l1.b2", "channel")]: {"chon": "const"},
            by_field[("l1.b1", "text")]: {"chon": "llm", "node": "soan"},
            by_field[("l1.b2", "text")]: {"chon": "llm", "node": "soan"},
        },
        "llm": [{"id": "soan", "instructions": "Soạn một lời chào ngắn.", "dau_vao": {}}],
    }
    return skeleton, questions, raw


def test_mot_node_llm_nhieu_buoc_doc_chi_duoc_phat_mot_lan() -> None:
    """Chạy thật trên kb8: model cho sáu bước cùng đọc một node llm. Bộ lắp phát node đó
    sáu lần — id trùng, đồ thị có chu trình — và lỗi không truy được về câu hỏi nào."""
    skeleton, questions, raw = _hai_tin("Chào cả nhà, tuần mới vui vẻ!")

    checked = _check(skeleton, questions, raw)

    assert _errors(checked) == []
    assert checked.assembled is not None
    assert [node["id"] for node in checked.assembled.document["nodes"]] == [
        "t",
        "soan",
        "l1_b1",
        "l1_b2",
    ]


def test_mot_node_llm_khong_sinh_duoc_hai_noi_dung_khac_nhau() -> None:
    """Hai bước gửi hai câu khác nhau thì không thể cùng lấy từ một lần sinh."""
    skeleton, questions, raw = _hai_tin("Hẹn gặp mọi người ở buổi họp chiều nay.")

    checked = _check(skeleton, questions, raw)

    (error,) = checked.report.errors
    assert (error.tier, error.code, error.node) == (Tier.REPLAY, "khong_tai_tao_duoc", "l1_b2")
    assert error.question.startswith("N")
