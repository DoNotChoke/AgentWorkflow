"""GĐ 5 của compiler: hai lời gọi model, vòng tự sửa, và thứ hiện cho người dùng.

Model ở đây là model giả diễn theo kịch bản. Ba tầng kiểm và sandbox là thật.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.core.errors import ValidationError
from app.domains.agent.application.ports import (
    ModelResponse,
    ModelToolCall,
    ModelToolSpec,
    ModelTurn,
    ModelUsage,
    StopReason,
)
from app.domains.agent.infrastructure.model_catalog import ModelProvider, resolve_provider
from app.domains.integration.infrastructure.catalog_view import tool_rows
from app.domains.workflow.application.compile_check import Checked, check_answers
from app.domains.workflow.application.compile_flow import (
    ANSWER_TOOL,
    ANSWERS_PROMPT,
    INTENT_PROMPT,
    INTENT_TOOL,
    build,
    propose_intent,
)
from app.domains.workflow.application.ports import AnswerAttempt, DialogueTurn, IntentReply
from app.domains.workflow.domain.compiler.briefing import briefing
from app.domains.workflow.domain.compiler.digest import MAX_REPLY_CHARS, digest
from app.domains.workflow.domain.compiler.intent import Intent, read_intent, render_intent
from app.domains.workflow.domain.compiler.normalize import read_source
from app.domains.workflow.domain.compiler.proposal import render_proposal
from app.domains.workflow.domain.compiler.provenance import find_sources
from app.domains.workflow.domain.compiler.questions import QuestionSet, build_questions
from app.domains.workflow.domain.compiler.report import CompileIssue, CompileReport, Tier
from app.domains.workflow.domain.compiler.skeleton import Skeleton, build_skeleton
from app.domains.workflow.domain.compiler.source import SourceMaterial
from app.domains.workflow.infrastructure.catalog import RowToolCatalog
from app.entrypoints.compiler_wiring import ModelAnswerAdvisor, ModelIntentAdvisor, SandboxRunner

FIXTURES = Path(__file__).parent / "fixtures"
CATALOG = RowToolCatalog(tool_rows())
TOOLS = {(row["tool_id"], row["version"]): row["input_schema"] for row in tool_rows()}

INTENT = Intent(
    task="Tìm một trang Notion theo tên rồi đăng đường link của nó vào kênh test.",
    varies=("tên trang cần tìm",),
    fixed=("kênh Slack test",),
    excluded=("tin thử ping", "lần tìm nhầm trang EC2"),
)


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _source(name: str) -> SourceMaterial:
    return read_source(_json(FIXTURES / "sources" / f"{name}.json"))


def _material(name: str) -> tuple[Skeleton, QuestionSet]:
    skeleton = build_skeleton(_source(name))
    return skeleton, build_questions(skeleton, find_sources(skeleton), TOOLS)


def _answers(name: str, mutate: Any = None) -> dict[str, Any]:
    raw = copy.deepcopy(_json(FIXTURES / "compiled" / f"{name}.answers.json"))
    if mutate is not None:
        mutate(raw)
    return raw


def _checker(name: str) -> Any:
    skeleton, questions = _material(name)

    def check(raw: Mapping[str, Any]) -> Checked:
        return check_answers(
            skeleton,
            questions,
            raw,
            tools=TOOLS,
            catalog=CATALOG,
            runner=SandboxRunner(),
            llm_model="claude-sonnet-5",
        )

    return check


# ─── Model hiểu ý đồ chỉ thấy lời nói và tên tool ────────────────────────────


def test_ban_tom_tat_co_loi_nguoi_dung_va_ten_tool_khong_co_payload() -> None:
    text = digest(_source("kb2-slack-doc-roi-ghi"))

    assert "Người dùng: Tìm id của kênh Slack tên là test" in text
    assert "- slack.list_channels — thành công" in text
    assert "- slack.post_message — thành công" in text
    # Tham số và output của tool không có mặt: tên trường của chúng cũng không.
    for leaked in ('"channels"', "num_members", "message_ts", '"channel"', "is_private"):
        assert leaked not in text, leaked


def test_tool_hong_van_duoc_ke_ten_kem_trang_thai() -> None:
    text = digest(_source("kb6-hong-giua-chung"))

    assert "- slack.get_channel_history — hỏng" in text


def test_moi_luot_cua_conversation_deu_co_mat() -> None:
    text = digest(_source("kb7-buoc-thua"))

    assert [line for line in text.splitlines() if line.startswith("## ")] == [
        "## Lượt 1",
        "## Lượt 2",
        "## Lượt 3",
        "## Lượt 4",
    ]
    assert "À nhầm, tôi cần trang AWS" in text


def test_cau_tra_loi_dai_cua_agent_bi_cat() -> None:
    text = digest(_source("kb8-ba-luot"))
    replies = [line for line in text.splitlines() if line.startswith("Agent trả lời:")]

    assert replies
    assert all(len(line) <= MAX_REPLY_CHARS + 40 for line in replies)


# ─── Ý đồ ────────────────────────────────────────────────────────────────────

RAW_INTENT = {
    "nhiem_vu": "Tìm một trang Notion theo tên rồi đăng đường link của nó vào kênh test.",
    "thay_doi": ["tên trang cần tìm"],
    "co_dinh": ["kênh Slack test"],
    "dieu_kien": [],
    "khong_thuoc_nhiem_vu": ["tin thử ping", "lần tìm nhầm trang EC2"],
}


def test_de_xuat_y_do_thanh_kieu_va_thanh_van_ban_cho_nguoi_doc() -> None:
    intent = read_intent(RAW_INTENT)

    assert intent == INTENT
    text = render_intent(intent)
    assert text.startswith("**Nhiệm vụ.** Tìm một trang Notion")
    assert "**Không thuộc nhiệm vụ**\n- tin thử ping\n- lần tìm nhầm trang EC2" in text
    assert "Điều kiện" not in text


def test_de_xuat_sai_hinh_dang_bi_tu_choi() -> None:
    with pytest.raises(ValidationError):
        read_intent({"nhiem_vu": "Thiếu các phần còn lại."})


class ScriptedIntentAdvisor:
    def __init__(self, reply: IntentReply) -> None:
        self._reply = reply
        self.seen: list[tuple[str, Sequence[DialogueTurn]]] = []

    async def propose(self, *, digest: str, dialogue: Sequence[DialogueTurn]) -> IntentReply:
        self.seen.append((digest, dialogue))
        return self._reply


async def test_model_de_xuat_thi_nguoi_dung_nhan_ban_da_trinh_bay() -> None:
    advisor = ScriptedIntentAdvisor(IntentReply(proposal=RAW_INTENT))
    dialogue = [DialogueTurn("user", "kênh cũng là đầu vào nhé")]

    outcome = await propose_intent(_source("kb7-buoc-thua"), dialogue, advisor)

    assert outcome.intent == INTENT
    assert outcome.message == render_intent(INTENT)
    ((sent, history),) = advisor.seen
    assert sent == digest(_source("kb7-buoc-thua"))
    assert history == dialogue


async def test_model_hoi_lai_thi_chua_co_y_do() -> None:
    advisor = ScriptedIntentAdvisor(IntentReply(proposal=None, text="Bạn muốn lặp lại việc nào?"))

    outcome = await propose_intent(_source("kb6-hong-giua-chung"), [], advisor)

    assert outcome.intent is None
    assert outcome.message == "Bạn muốn lặp lại việc nào?"


# ─── Tài liệu cho model trả lời câu hỏi ──────────────────────────────────────


def test_tai_lieu_mang_y_do_cau_hoi_va_ung_vien() -> None:
    text = briefing(*_material("kb7-buoc-thua"), INTENT)

    assert text.startswith("# Ý đồ đã được người dùng chấp nhận")
    assert INTENT.task in text
    assert "## l4.b2 — slack.post_message (lượt 4)" in text
    assert "- **N6** `channel` =" in text
    assert "lựa chọn: const · workflow_input · from_step" in text
    assert (
        '`l1.b1` → `channels.4.id` — phần tử thứ 4 của mảng; chọn được theo khoá: `name` = "test"'
        in text
    )
    assert "`l4.b1:results`" in text


def test_output_chi_dua_hinh_dang_va_mot_phan_tu_mau() -> None:
    skeleton, questions = _material("kb10-transform-dem")
    text = briefing(skeleton, questions, INTENT)

    assert "`messages`: mảng 52 phần tử; phần tử đầu:" in text
    assert len(text) < 3000
    assert len(json.dumps(skeleton.step("l1.b1").output)) > 3 * len(text)


def test_gia_tri_dai_bi_cat_va_noi_ro_la_da_cat() -> None:
    text = briefing(*_material("kb4-bac-cau"), INTENT)

    assert "- **N4** `blocks` = " in text
    assert "… (đã cắt, " in text


def test_cum_buoc_lap_va_code_cua_agent_co_trong_tai_lieu() -> None:
    assert "- **V1**: l1.b4, l1.b5, l1.b6, l1.b7 — `page_id`" in briefing(
        *_material("kb3-notion-doc-sau"), INTENT
    )
    assert "Code Agent đã chạy:\n```python\nreturn [{" in briefing(
        *_material("kb9-transform-doi-hinh-dang"), INTENT
    )


def test_tai_lieu_ke_cac_lan_va_noi_lan_nao_chi_theo_vi_tri() -> None:
    text = briefing(*_material("kb8-ba-luot"), INTENT)

    assert "  - phần tử đang lặp đọc được qua `V1:phan_tu`" in text
    assert "  - làn 1: l2.b2, l2.b3, l2.b4, l2.b5, l2.b6, l2.b7 (`slack.post_message`)" in text
    assert "`text` do Agent tự soạn. Không dữ liệu nào nối làn này với từng phần tử" in text
    assert (
        "`block_id` lấy từ làn gốc của cùng vòng → phần tử của `results` có `type` = "
        '"toggle" → `id`' in text
    )
    assert "`thread_ts` lấy từ làn 1 của cùng vòng → `message_ts`" in text


def test_conversation_qua_dai_bi_tu_choi_chu_khong_cat_am_tham() -> None:
    with pytest.raises(ValidationError) as caught:
        briefing(*_material("kb8-ba-luot"), INTENT, max_chars=5000)

    assert caught.value.details["max_chars"] == 5000
    assert caught.value.details["steps"] == 27


# ─── Vòng tự sửa ─────────────────────────────────────────────────────────────


class ScriptedAnswerAdvisor:
    """Nộp lần lượt các câu trả lời đã định; hết kịch bản thì nộp lại cái cuối."""

    def __init__(self, *script: Mapping[str, Any]) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def answer(
        self,
        *,
        briefing: str,
        schema: Mapping[str, Any],
        attempts: Sequence[AnswerAttempt],
        comment: str | None = None,
        previous: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        self.calls.append(
            {
                "briefing": briefing,
                "attempts": list(attempts),
                "comment": comment,
                "previous": previous,
            }
        )
        return self._script.pop(0) if len(self._script) > 1 else self._script[0]


KB7 = "kb7-buoc-thua"


def _bo_nguon(raw: dict[str, Any]) -> None:
    raw["buoc"]["l1.b1"] = {"chon": "bo", "ly_do": "Chỉ liệt kê kênh."}


async def _build(advisor: ScriptedAnswerAdvisor, name: str = KB7, **options: Any) -> Any:
    skeleton, questions = _material(name)
    return await build(skeleton, questions, INTENT, advisor, _checker(name), **options)


async def test_tra_loi_dung_ngay_thi_mot_vong_la_xong() -> None:
    advisor = ScriptedAnswerAdvisor(_answers(KB7))

    built = await _build(advisor)

    assert built.rounds == 1
    assert built.checked.report.ok
    assert built.proposal is not None
    assert built.proposal.is_ready
    assert advisor.calls[0]["attempts"] == []


async def test_loi_quay_lai_model_kem_ma_cau_hoi_roi_model_sua_duoc() -> None:
    """Lần đầu model bỏ lượt *liệt kê kênh* mà vẫn lấy id kênh từ nó."""
    advisor = ScriptedAnswerAdvisor(_answers(KB7, _bo_nguon), _answers(KB7))

    built = await _build(advisor)

    assert built.rounds == 2
    assert built.checked.report.ok
    (attempt,) = advisor.calls[1]["attempts"]
    assert attempt.answers["buoc"]["l1.b1"]["chon"] == "bo"
    assert attempt.errors[0].startswith("N6 [nguon_la_buoc_da_bo]:")


async def test_sai_mai_thi_dung_o_tran_va_loi_con_lai_duoc_neu_ra() -> None:
    advisor = ScriptedAnswerAdvisor(_answers(KB7, _bo_nguon))

    built = await _build(advisor, max_rounds=3)

    assert built.rounds == 3
    assert len(advisor.calls) == 3
    assert not built.checked.report.ok
    assert built.proposal is not None
    assert not built.proposal.is_ready
    assert "N6" in built.proposal.errors[0]


async def test_model_khong_nop_gi_thi_khong_co_gi_de_trinh_bay() -> None:
    built = await _build(ScriptedAnswerAdvisor({}), max_rounds=2)

    assert built.rounds == 2
    assert built.proposal is None
    assert {issue.code for issue in built.checked.report.errors} == {"sai_hinh_dang"}


async def test_loi_cua_bo_lap_thi_khong_hoi_lai_model() -> None:
    """Không câu trả lời nào sửa được một lỗi không thuộc về câu hỏi nào."""
    advisor = ScriptedAnswerAdvisor(_answers(KB7))
    broken = CompileReport((CompileIssue(Tier.DOCUMENT, "", "ir_sai_hinh_dang", "…"),))
    skeleton, questions = _material(KB7)

    built = await build(skeleton, questions, INTENT, advisor, lambda raw: Checked(broken))

    assert built.rounds == 1
    assert len(advisor.calls) == 1


async def test_nguoi_dung_doi_mot_gia_dinh_thi_model_nhan_loi_ho_noi_va_cau_tra_loi_cu() -> None:
    advisor = ScriptedAnswerAdvisor(_answers(KB7))

    await _build(advisor, comment="kênh cũng là đầu vào", previous=_answers(KB7))

    assert advisor.calls[0]["comment"] == "kênh cũng là đầu vào"
    assert advisor.calls[0]["previous"] == _answers(KB7)


async def test_code_do_model_viet_duoc_chay_that_trong_vong_tu_sua() -> None:
    """kb5b: lần đầu model viết điều kiện ngược chiều; tầng 3 chạy code và bắt được."""
    name = "kb5b-co-transform-khong-dung"
    nguoc = _answers(name, lambda raw: raw["dieu_kien"][0].update({"op": "lt"}))
    advisor = ScriptedAnswerAdvisor(nguoc, _answers(name))

    built = await _build(advisor, name)

    assert built.rounds == 2
    assert built.checked.report.ok
    assert (
        advisor.calls[1]["attempts"][0]
        .errors[0]
        .startswith("dieu_kien:du_nhieu [dieu_kien_sai_nhanh]:")
    )


# ─── Bảng giả định ───────────────────────────────────────────────────────────


async def test_bang_gia_dinh_noi_buoc_nao_bo_va_gia_tri_lay_tu_dau() -> None:
    built = await _build(ScriptedAnswerAdvisor(_answers(KB7)))
    proposal = built.proposal
    assert proposal is not None

    assert proposal.inputs == (("ten_trang", '"AWS"'),)
    assert [(step.turn, step.tool_id) for step in proposal.dropped] == [
        (2, "slack.post_message"),
        (3, "notion.search"),
    ]
    channel = next(item for item in proposal.linked if item.field == "channel")
    assert channel.detail == 'lấy từ `kenh`: phần tử của `channels` có `name` = "test"'
    link = next(item for item in proposal.linked if item.field == "text")
    assert "chọn theo vị trí" in link.detail
    assert any("chi_so_tho" in line or "vị trí" in line for line in proposal.warnings)

    text = render_proposal(proposal)
    assert text.startswith("# Đăng link trang Notion vào Slack")
    assert "**Bước bị bỏ**\n- lượt 2, `slack.post_message`: Tin thử `ping`" in text
    assert '- `ten_trang` — lần chạy mẫu: "AWS"' in text


async def test_bang_gia_dinh_noi_ro_than_vong_lap_va_dieu_da_suy_ra() -> None:
    built = await _build(ScriptedAnswerAdvisor(_answers("kb8-ba-luot")), "kb8-ba-luot")
    proposal = built.proposal
    assert proposal is not None
    assert proposal.is_ready

    (loop,) = proposal.loops
    lines = loop.split("\n  - ")
    assert lines[0] == (
        "`moi_cau`: lặp trên từng phần tử của `khoi` → `results` (lần chạy mẫu: 6 phần tử)"
    )
    assert lines[1].startswith(
        "mỗi vòng chạy lần lượt `doc_khoi` → `dang_cau_hoi` → `doc_dap_an` → `tra_loi`. "
        "Lần chạy mẫu thì làm xong từng việc cho cả 6 phần tử"
    )
    assert lines[2].startswith("`dang_cau_hoi` nằm trong vòng lặp vì nó chạy đúng 6 lần")
    assert lines[3] == (
        "`doc_dap_an`.block_id: lấy từ `doc_khoi` của cùng vòng: phần tử của `results` có "
        '`type` = "toggle" (khoá suy ra từ lần chạy mẫu: cả 6 vòng đều chọn đúng phần tử đó)'
    )
    assert lines[4] == "`tra_loi`.thread_ts: lấy từ `dang_cau_hoi` của cùng vòng → `message_ts`"
    # Sáu tin đăng chỉ còn một dòng: bước đầu của làn nói thay cả làn.
    assert [item.node for item in proposal.linked if item.field == "text"] == [
        "dang_cau_hoi",
        "tra_loi",
    ]


async def test_code_va_dieu_kien_hien_nguyen_van() -> None:
    """ADR-0016, quyết định 5 và 6."""
    name = "kb5b-co-transform-khong-dung"
    built = await _build(ScriptedAnswerAdvisor(_answers(name)), name)
    proposal = built.proposal
    assert proposal is not None

    (code,) = proposal.written
    assert (code.id, code.kind, code.text) == ("dem", "code", "return len(args['messages'])")
    assert proposal.conditions == (
        "Chỉ chạy từ `dang` trở đi khi `dem` lớn hơn đầu vào `nguong` (lần chạy mẫu: 5)",
    )
    # `limit: 6` là thứ Agent nắn theo ngưỡng 5: phải được nêu ra, không lặng lẽ biến mất.
    (limit,) = proposal.defaults
    assert (limit.field, limit.value) == ("limit", "6")
    assert "```python\nreturn len(args['messages'])\n```" in render_proposal(proposal)


async def test_instructions_va_code_agent_da_chay_cung_hien_nguyen_van() -> None:
    llm = (await _build(ScriptedAnswerAdvisor(_answers("kb4-bac-cau")), "kb4-bac-cau")).proposal
    doi = (
        await _build(
            ScriptedAnswerAdvisor(_answers("kb9-transform-doi-hinh-dang")),
            "kb9-transform-doi-hinh-dang",
        )
    ).proposal
    assert llm is not None
    assert doi is not None

    assert llm.written[0].kind == "instructions"
    assert llm.written[0].text.startswith("Tóm tắt các tin nhắn Slack")
    assert (doi.written[0].id, doi.written[0].detail) == (
        "doi",
        "code Agent đã chạy trong hội thoại",
    )


# ─── Nối với model thật ──────────────────────────────────────────────────────


class RecordingModelClient:
    def __init__(self, *, text: str = "", call: ModelToolCall | None = None) -> None:
        self._text = text
        self._call = call
        self.requests: list[dict[str, Any]] = []

    async def complete(
        self,
        *,
        model: str,
        system: str,
        turns: Sequence[ModelTurn],
        tools: Sequence[ModelToolSpec] = (),
        max_tokens: int = 8192,
    ) -> ModelResponse:
        self.requests.append(
            {"model": model, "system": system, "turns": list(turns), "tools": list(tools)}
        )
        return ModelResponse(
            text=self._text,
            tool_calls=(self._call,) if self._call else (),
            stop_reason=StopReason.TOOL_USE if self._call else StopReason.END_TURN,
            usage=ModelUsage(input_tokens=1, output_tokens=1),
        )


async def test_model_y_do_nhan_ban_tom_tat_roi_toi_phan_chat_cua_phien() -> None:
    models = RecordingModelClient(call=ModelToolCall("c1", INTENT_TOOL, RAW_INTENT))
    advisor = ModelIntentAdvisor(models, model="gpt-6.1-sol")

    reply = await advisor.propose(
        digest="## Lượt 1\n…",
        dialogue=[DialogueTurn("assistant", "**Nhiệm vụ.** …"), DialogueTurn("user", "đổi kênh")],
    )

    assert reply.proposal == RAW_INTENT
    (request,) = models.requests
    assert (request["model"], request["system"]) == ("gpt-6.1-sol", INTENT_PROMPT)
    assert [(turn.role, turn.text) for turn in request["turns"]] == [
        ("user", "## Lượt 1\n…"),
        ("assistant", "**Nhiệm vụ.** …"),
        ("user", "đổi kênh"),
    ]
    assert [tool.name for tool in request["tools"]] == [INTENT_TOOL]


async def test_model_y_do_tra_loi_bang_loi_thi_khong_co_de_xuat() -> None:
    advisor = ModelIntentAdvisor(RecordingModelClient(text="Bạn muốn lặp lại việc nào?"), model="m")

    reply = await advisor.propose(digest="…", dialogue=[])

    assert reply == IntentReply(proposal=None, text="Bạn muốn lặp lại việc nào?")


async def test_lan_tra_loi_bi_tu_choi_di_vao_lich_su_duoi_dang_loi_goi_tool_va_ket_qua_loi() -> (
    None
):
    models = RecordingModelClient(call=ModelToolCall("c9", ANSWER_TOOL, {"ten": "x"}))
    advisor = ModelAnswerAdvisor(models, model="gpt-6.1-sol")
    schema = {"type": "object"}

    answer = await advisor.answer(
        briefing="# Ý đồ…",
        schema=schema,
        attempts=[AnswerAttempt(answers={"ten": "cũ"}, errors=["N6 [nguon_la_buoc_da_bo]: …"])],
    )

    assert answer == {"ten": "x"}
    (request,) = models.requests
    assert request["system"] == ANSWERS_PROMPT
    assert (request["tools"][0].name, request["tools"][0].input_schema) == (ANSWER_TOOL, schema)
    opening, called, rejected = request["turns"]
    assert opening.text == "# Ý đồ…"
    assert called.tool_calls[0].input == {"ten": "cũ"}
    assert rejected.tool_results[0].call_id == called.tool_calls[0].call_id
    assert rejected.tool_results[0].is_error
    assert "N6 [nguon_la_buoc_da_bo]" in rejected.tool_results[0].content


async def test_loi_nguoi_dung_muon_doi_di_cung_cau_tra_loi_dang_hien() -> None:
    models = RecordingModelClient(call=ModelToolCall("c1", ANSWER_TOOL, {}))

    await ModelAnswerAdvisor(models, model="m").answer(
        briefing="# Ý đồ…",
        schema={},
        attempts=[],
        comment="kênh là đầu vào",
        previous={"ten": "cũ"},
    )

    opening = models.requests[0]["turns"][0].text
    assert "# Câu trả lời đang hiện cho người dùng" in opening
    assert '"ten": "cũ"' in opening
    assert opening.endswith("# Người dùng muốn đổi\n\nkênh là đầu vào")


async def test_model_khong_goi_tool_thi_cau_tra_loi_rong() -> None:
    advisor = ModelAnswerAdvisor(RecordingModelClient(text="Tôi không chắc."), model="m")

    assert await advisor.answer(briefing="…", schema={}, attempts=[]) == {}


def test_sandbox_runner_chay_code_that() -> None:
    assert SandboxRunner().run("return len(args['a'])", {"a": [1, 2, 3]}) == 3


# ─── Cấu hình và prompt ──────────────────────────────────────────────────────


def test_hai_model_cua_compiler_la_hai_setting_rieng_cung_mot_mac_dinh() -> None:
    fields = Settings.model_fields

    assert fields["compiler_intent_model"].default == "gpt-6.1-sol"
    assert fields["compiler_answer_model"].default == "gpt-6.1-sol"
    assert resolve_provider("gpt-6.1-sol") is ModelProvider.OPENAI


def test_prompt_noi_dung_ten_tool_ma_model_phai_goi() -> None:
    assert f"`{INTENT_TOOL}`" in INTENT_PROMPT
    assert f"`{ANSWER_TOOL}`" in ANSWERS_PROMPT
    for part in ("buoc", "gia_tri", "vong_lap", "llm", "tinh_toan", "dieu_kien", "ten"):
        assert f"`{part}`" in ANSWERS_PROMPT, part


async def test_cau_tra_loi_bi_cat_giua_chung_thi_dung_khong_hoi_lai() -> None:
    """Chạy thật trên conversation 27 bước: câu trả lời vượt trần token, model nộp một
    JSON cụt, và mỗi vòng hỏi lại chỉ ra đúng thứ đó."""

    class Truncated(RecordingModelClient):
        async def complete(self, **request: Any) -> ModelResponse:
            return ModelResponse(
                text="",
                tool_calls=(ModelToolCall("c1", ANSWER_TOOL, {}),),
                stop_reason=StopReason.MAX_TOKENS,
                usage=ModelUsage(input_tokens=1, output_tokens=8192),
            )

    with pytest.raises(ValidationError, match="quá nhiều bước"):
        await ModelAnswerAdvisor(Truncated(), model="m").answer(
            briefing="…", schema={}, attempts=[]
        )


async def test_de_xuat_y_do_sai_hinh_dang_thi_loi_quay_lai_model() -> None:
    """Chạy thật: model nộp `thay_doi` là một chuỗi thay vì một mảng."""

    class Flaky:
        def __init__(self) -> None:
            self.dialogues: list[list[DialogueTurn]] = []

        async def propose(self, *, digest: str, dialogue: Sequence[DialogueTurn]) -> IntentReply:
            self.dialogues.append(list(dialogue))
            if len(self.dialogues) == 1:
                return IntentReply(proposal=RAW_INTENT | {"thay_doi": "tên trang cần tìm"})
            return IntentReply(proposal=RAW_INTENT)

    advisor = Flaky()

    outcome = await propose_intent(_source("kb7-buoc-thua"), [], advisor)

    assert outcome.intent == INTENT
    assert advisor.dialogues[0] == []
    hong, nhac = advisor.dialogues[1]
    assert (hong.role, nhac.role) == ("assistant", "user")
    assert "thay_doi" in nhac.text
    assert f"`{INTENT_TOOL}`" in nhac.text


async def test_de_xuat_y_do_sai_mai_thi_bao_loi() -> None:
    advisor = ScriptedIntentAdvisor(IntentReply(proposal={"nhiem_vu": "thiếu phần còn lại"}))

    with pytest.raises(ValidationError):
        await propose_intent(_source("kb7-buoc-thua"), [], advisor, max_rounds=2)

    assert len(advisor.seen) == 2
