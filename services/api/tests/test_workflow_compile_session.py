"""GĐ 6 của compiler: một phiên biên dịch, từ lúc mở tới draft.

Model ở đây là kịch bản viết sẵn; ba tầng kiểm và sandbox là thật.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.core.errors import AuthorizationError, ValidationError
from app.domains.integration.infrastructure.catalog_view import tool_rows
from app.domains.workflow.application.compile_service import CompileService
from app.domains.workflow.application.ports import (
    AnswerAttempt,
    CompileSource,
    DialogueTurn,
    IntentReply,
)
from app.domains.workflow.application.service import WorkflowService
from app.domains.workflow.domain.compiler.normalize import read_source
from app.domains.workflow.domain.compiler.session import (
    CompileSession,
    MessageKind,
    SessionStatus,
)
from app.domains.workflow.domain.entities import Workflow, WorkflowDraft, WorkflowVersion
from app.domains.workflow.infrastructure.catalog import RowToolCatalog
from app.entrypoints.compiler_wiring import SandboxRunner

FIXTURES = Path(__file__).parent / "fixtures"
TOOLS = {(row["tool_id"], row["version"]): row["input_schema"] for row in tool_rows()}
KB7 = "kb7-buoc-thua"

TENANT = "org_bien_dich"
WORKSPACE = "ws_bien_dich"
USER = "usr_chu_hoi_thoai"
OTHER_USER = "usr_nguoi_khac"
CONVERSATION = "cnv_nguon"

RAW_INTENT = {
    "nhiem_vu": "Tìm một trang Notion theo tên rồi đăng đường link của nó vào kênh test.",
    "thay_doi": ["tên trang cần tìm"],
    "co_dinh": ["kênh Slack test"],
    "dieu_kien": [],
    "khong_thuoc_nhiem_vu": ["tin thử ping", "lần tìm nhầm trang EC2"],
}


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _answers(mutate: Any = None) -> dict[str, Any]:
    raw = copy.deepcopy(_json(FIXTURES / "compiled" / f"{KB7}.answers.json"))
    if mutate is not None:
        mutate(raw)
    return raw


def _bo_nguon(raw: dict[str, Any]) -> None:
    raw["buoc"]["l1.b1"] = {"chon": "bo", "ly_do": "Chỉ liệt kê kênh."}


# ─── Thứ đứng thay hạ tầng ───────────────────────────────────────────────────


class Sessions:
    def __init__(self) -> None:
        self.rows: dict[str, CompileSession] = {}

    async def add(self, session: CompileSession) -> None:
        self.rows[session.id] = session

    async def get(self, *, tenant_id: str, session_id: str) -> CompileSession | None:
        found = self.rows.get(session_id)
        return found if found is not None and found.tenant_id == tenant_id else None

    async def save(self, session: CompileSession, *, expected_revision: int) -> None:
        if self.rows[session.id].revision != expected_revision:
            raise ValidationError("Phiên biên dịch vừa được đổi ở nơi khác.")
        self.rows[session.id] = session

    async def list_open(
        self, *, tenant_id: str, conversation_id: str, created_by: str
    ) -> list[CompileSession]:
        return [
            row
            for row in self.rows.values()
            if row.is_open
            and (row.tenant_id, row.conversation_id, row.created_by)
            == (tenant_id, conversation_id, created_by)
        ]


class Sources:
    """Chỉ có `read`: phiên biên dịch không có đường nào ghi vào conversation nguồn."""

    def __init__(self, name: str = KB7) -> None:
        self.document = _json(FIXTURES / "sources" / f"{name}.json")

    async def read(self, *, tenant_id: str, conversation_id: str) -> CompileSource | None:
        if (tenant_id, conversation_id) != (TENANT, CONVERSATION):
            return None
        return CompileSource(
            document=copy.deepcopy(self.document), user_id=USER, workspace_id=WORKSPACE
        )


class Workflows:
    def __init__(self) -> None:
        self.workflows: dict[str, Workflow] = {}
        self.drafts: dict[str, WorkflowDraft] = {}
        self.versions: list[WorkflowVersion] = []

    async def add(self, workflow: Workflow) -> None:
        self.workflows[workflow.id] = workflow

    async def get(self, *, tenant_id: str, workflow_id: str) -> Workflow | None:
        return self.workflows.get(workflow_id)

    async def update(self, workflow: Workflow) -> None:
        self.workflows[workflow.id] = workflow

    async def save_draft(self, draft: WorkflowDraft) -> None:
        self.drafts[draft.workflow_id] = draft

    async def get_draft(self, *, tenant_id: str, workflow_id: str) -> WorkflowDraft | None:
        return self.drafts.get(workflow_id)

    async def add_version(self, version: WorkflowVersion) -> None:
        self.versions.append(version)

    async def latest_version_number(self, *, tenant_id: str, workflow_id: str) -> int | None:
        return max((version.number for version in self.versions), default=None)


class IntentAdvisor:
    def __init__(self, *replies: IntentReply) -> None:
        self._replies = list(replies) or [IntentReply(proposal=RAW_INTENT)]
        self.seen: list[Sequence[DialogueTurn]] = []

    async def propose(self, *, digest: str, dialogue: Sequence[DialogueTurn]) -> IntentReply:
        self.seen.append(list(dialogue))
        return self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]


class AnswerAdvisor:
    def __init__(self, *script: Mapping[str, Any]) -> None:
        self._script = list(script) or [_answers()]
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
        self.calls.append({"briefing": briefing, "comment": comment, "previous": previous})
        return self._script.pop(0) if len(self._script) > 1 else self._script[0]


class Bench:
    def __init__(
        self,
        *,
        intent: IntentAdvisor | None = None,
        answer: AnswerAdvisor | None = None,
        source: str = KB7,
    ) -> None:
        self.sessions = Sessions()
        self.sources = Sources(source)
        self.workflows = Workflows()
        self.intent = intent or IntentAdvisor()
        self.answer = answer or AnswerAdvisor()
        self.catalog = RowToolCatalog(tool_rows())
        self.service = CompileService(
            sessions=self.sessions,
            sources=self.sources,
            workflows=self.workflows,  # type: ignore[arg-type]
            intent_advisor=self.intent,
            answer_advisor=self.answer,
            tools=TOOLS,
            catalog=self.catalog,
            runner=SandboxRunner(),
            intent_model="model-y-do",
            answer_model="model-tra-loi",
            llm_node_model="claude-sonnet-5",
            max_rounds=2,
        )

    async def open(self, actor: str = USER) -> CompileSession:
        return await self.service.open(
            tenant_id=TENANT, workspace_id=WORKSPACE, conversation_id=CONVERSATION, actor_id=actor
        )

    def on(self, session: CompileSession, actor: str = USER) -> dict[str, str]:
        return {"tenant_id": TENANT, "session_id": session.id, "actor_id": actor}

    @property
    def model_calls(self) -> int:
        return len(self.intent.seen) + len(self.answer.calls)


# ─── Mở phiên ────────────────────────────────────────────────────────────────


async def test_mo_phien_la_mot_de_xuat_y_do_cho_nguoi_dung_doc() -> None:
    bench = Bench()

    session = await bench.open()

    assert session.status is SessionStatus.AWAITING_INTENT
    assert session.intent == RAW_INTENT
    (message,) = session.messages
    assert message.kind is MessageKind.INTENT
    assert message.text.startswith("**Nhiệm vụ.** Tìm một trang Notion")
    assert bench.sessions.rows[session.id] == session


async def test_phien_ghi_lai_no_da_doc_gi_va_bang_model_nao() -> None:
    bench = Bench()

    session = await bench.open()

    material = read_source(bench.sources.document)
    assert session.trace_ids == tuple(turn.trace_id for turn in material.turns)
    assert (session.intent_model, session.answer_model) == ("model-y-do", "model-tra-loi")
    assert (session.question_set_version, session.prompt_version) == ("1", "1")


async def test_model_hoi_lai_thi_chua_co_y_do_de_chap_nhan() -> None:
    bench = Bench(intent=IntentAdvisor(IntentReply(None, "Bạn muốn đăng vào kênh nào?")))

    session = await bench.open()

    assert session.intent is None
    assert session.messages[0].kind is MessageKind.QUESTION
    with pytest.raises(ValidationError, match="Chưa có ý đồ"):
        await bench.service.accept_intent(**bench.on(session))


async def test_chi_chu_conversation_mo_duoc_phien() -> None:
    bench = Bench()

    with pytest.raises(AuthorizationError):
        await bench.open(actor=OTHER_USER)
    assert bench.model_calls == 0


async def test_conversation_khong_co_buoc_nao_dung_duoc_thi_khong_mo_phien() -> None:
    bench = Bench(source="kb6-hong-giua-chung")

    with pytest.raises(ValidationError, match="chưa có lời gọi tool nào thành công"):
        await bench.open()
    assert bench.model_calls == 0


async def test_mo_phien_moi_thi_phien_cu_nhuong_cho() -> None:
    bench = Bench()
    cu = await bench.open()

    moi = await bench.open()

    assert bench.sessions.rows[cu.id].status is SessionStatus.ABANDONED
    assert bench.sessions.rows[moi.id].is_open


# ─── Bước ý đồ ───────────────────────────────────────────────────────────────


async def test_noi_lai_o_buoc_y_do_thi_model_y_do_doc_ca_doan_chat() -> None:
    doi = RAW_INTENT | {"thay_doi": ["tên trang cần tìm", "kênh Slack"], "co_dinh": []}
    bench = Bench(intent=IntentAdvisor(IntentReply(RAW_INTENT), IntentReply(doi)))
    session = await bench.open()

    session = await bench.service.say(**bench.on(session), text="kênh cũng là đầu vào nhé")

    assert session.status is SessionStatus.AWAITING_INTENT
    assert session.intent == doi
    assert [message.kind for message in session.messages] == [
        MessageKind.INTENT,
        MessageKind.USER,
        MessageKind.INTENT,
    ]
    assert [(turn.role, turn.text[:13]) for turn in bench.intent.seen[1]] == [
        ("assistant", "**Nhiệm vụ.**"),
        ("user", "kênh cũng là "),
    ]
    assert bench.answer.calls == []


async def test_tin_nhan_rong_hoac_qua_dai_bi_tu_choi() -> None:
    bench = Bench()
    session = await bench.open()

    for text in ("   ", "a" * 4001):
        with pytest.raises(ValidationError, match="Tin nhắn"):
            await bench.service.say(**bench.on(session), text=text)


# ─── Bước giả định ───────────────────────────────────────────────────────────


async def test_chap_nhan_y_do_thi_co_bang_gia_dinh_va_ir_da_kiem() -> None:
    bench = Bench()
    session = await bench.open()

    session = await bench.service.accept_intent(**bench.on(session))

    assert session.status is SessionStatus.AWAITING_CONFIRMATION
    assert session.is_ready
    assert session.ir == _json(FIXTURES / "compiled" / f"{KB7}.ir.json")
    assert session.answers == _answers()
    assert session.proposal is not None
    assert session.proposal["is_ready"] is True
    assert session.proposal["inputs"] == [["ten_trang", '"AWS"']]
    assert session.messages[-1].kind is MessageKind.ASSUMPTIONS
    assert session.messages[-1].text.startswith("# Đăng link trang Notion vào Slack")
    assert bench.answer.calls[0]["comment"] is None


async def test_doi_o_buoc_gia_dinh_di_toi_model_tra_loi_kem_cau_tra_loi_dang_hien() -> None:
    bench = Bench()
    session = await bench.service.accept_intent(**bench.on(await bench.open()))

    session = await bench.service.say(**bench.on(session), text="giữ luôn tin thử ping đi")

    assert session.status is SessionStatus.AWAITING_CONFIRMATION
    assert bench.answer.calls[1]["comment"] == "giữ luôn tin thử ping đi"
    assert bench.answer.calls[1]["previous"] == _answers()
    # Ý đồ đã chấp nhận không đổi, và model hiểu ý đồ không được gọi lại.
    assert session.intent == RAW_INTENT
    assert len(bench.intent.seen) == 1
    assert [message.kind for message in session.messages[-2:]] == [
        MessageKind.USER,
        MessageKind.ASSUMPTIONS,
    ]


async def test_bang_gia_dinh_con_loi_thi_van_hien_nhung_khong_dong_y_duoc() -> None:
    bench = Bench(answer=AnswerAdvisor(_answers(_bo_nguon)))
    session = await bench.service.accept_intent(**bench.on(await bench.open()))

    assert session.status is SessionStatus.AWAITING_CONFIRMATION
    assert not session.is_ready
    assert session.ir is None
    assert session.proposal is not None
    assert session.proposal["is_ready"] is False
    assert "**Chưa dựng được workflow hợp lệ**" in session.messages[-1].text
    with pytest.raises(ValidationError, match="chưa dựng được workflow hợp lệ"):
        await bench.service.confirm(**bench.on(session))
    assert bench.workflows.workflows == {}


async def test_model_khong_nop_gi_thi_phien_noi_ro_va_van_cho_nguoi_dung() -> None:
    bench = Bench(answer=AnswerAdvisor({}))
    session = await bench.service.accept_intent(**bench.on(await bench.open()))

    assert session.proposal is None
    assert session.messages[-1].text.startswith("Chưa dựng được bảng giả định:")
    assert session.is_open


async def test_quay_lai_y_do_thi_bang_gia_dinh_khong_con() -> None:
    bench = Bench()
    session = await bench.service.accept_intent(**bench.on(await bench.open()))

    session = await bench.service.reopen_intent(**bench.on(session))

    assert session.status is SessionStatus.AWAITING_INTENT
    assert (session.answers, session.proposal, session.ir) == (None, None, None)
    assert session.intent == RAW_INTENT

    # Bảng giả định cũ không nằm trong thứ model hiểu ý đồ đọc lại.
    await bench.service.say(**bench.on(session), text="thật ra chỉ cần tìm trang thôi")
    assert [turn.role for turn in bench.intent.seen[-1]] == ["assistant", "user"]
    assert "# Đăng link" not in bench.intent.seen[-1][0].text


# ─── Đồng ý ──────────────────────────────────────────────────────────────────


async def test_dong_y_tao_draft_mang_ir_y_do_va_phien_da_sinh_ra_no() -> None:
    bench = Bench()
    session = await bench.service.accept_intent(**bench.on(await bench.open()))

    done = await bench.service.confirm(**bench.on(session))

    assert done.status is SessionStatus.COMPILED
    assert done.workflow_id is not None
    workflow = bench.workflows.workflows[done.workflow_id]
    assert (workflow.name, workflow.created_by, workflow.workspace_id) == (
        "Đăng link trang Notion vào Slack",
        USER,
        WORKSPACE,
    )
    draft = bench.workflows.drafts[done.workflow_id]
    assert draft.ir == session.ir
    assert draft.intent == RAW_INTENT
    assert draft.compiled_from == session.id


async def test_dong_y_khong_goi_model_nao() -> None:
    bench = Bench()
    session = await bench.service.accept_intent(**bench.on(await bench.open()))
    before = bench.model_calls

    await bench.service.confirm(**bench.on(session))

    assert bench.model_calls == before


async def test_dong_y_hai_lan_chi_tao_mot_workflow() -> None:
    bench = Bench()
    session = await bench.service.accept_intent(**bench.on(await bench.open()))
    await bench.service.confirm(**bench.on(session))

    with pytest.raises(ValidationError, match="compiled"):
        await bench.service.confirm(**bench.on(session))
    assert len(bench.workflows.workflows) == 1


async def test_publish_chep_y_do_va_nguon_goc_sang_version() -> None:
    bench = Bench()
    session = await bench.service.accept_intent(**bench.on(await bench.open()))
    done = await bench.service.confirm(**bench.on(session))
    assert done.workflow_id is not None
    workflows = WorkflowService(
        repository=bench.workflows,  # type: ignore[arg-type]
        catalog=bench.catalog,
        connections=None,  # type: ignore[arg-type]
    )

    version = await workflows.publish(
        tenant_id=TENANT, workflow_id=done.workflow_id, actor_id=USER, can_manage_workspace=False
    )

    assert version.intent == RAW_INTENT
    assert version.compiled_from == session.id


async def test_sua_draft_bang_tay_khong_lam_mat_y_do() -> None:
    bench = Bench()
    session = await bench.service.accept_intent(**bench.on(await bench.open()))
    done = await bench.service.confirm(**bench.on(session))
    assert done.workflow_id is not None and session.ir is not None
    workflows = WorkflowService(
        repository=bench.workflows,  # type: ignore[arg-type]
        catalog=bench.catalog,
        connections=None,  # type: ignore[arg-type]
    )

    draft, _ = await workflows.save_draft(
        tenant_id=TENANT,
        workflow_id=done.workflow_id,
        ir=session.ir,
        actor_id=USER,
        can_manage_workspace=False,
    )

    assert draft.intent == RAW_INTENT
    assert draft.compiled_from == session.id


# ─── Phiên là của người mở nó, và chỉ đi theo chiều cho phép ─────────────────


async def test_nguoi_khac_thay_phien_nhu_khong_ton_tai() -> None:
    bench = Bench()
    session = await bench.open()

    for call in (bench.service.get, bench.service.accept_intent, bench.service.abandon):
        with pytest.raises(AuthorizationError):
            await call(**bench.on(session, actor=OTHER_USER))
    with pytest.raises(AuthorizationError):
        await bench.service.get(tenant_id="org_khac", session_id=session.id, actor_id=USER)


async def test_phien_da_dong_thi_khong_lam_gi_them_duoc() -> None:
    bench = Bench()
    session = await bench.service.abandon(**bench.on(await bench.open()))

    assert session.status is SessionStatus.ABANDONED
    for call in (bench.service.accept_intent, bench.service.confirm, bench.service.abandon):
        with pytest.raises(ValidationError):
            await call(**bench.on(session))


async def test_chua_chap_nhan_y_do_thi_chua_dong_y_duoc() -> None:
    bench = Bench()
    session = await bench.open()

    with pytest.raises(ValidationError, match="awaiting_intent"):
        await bench.service.confirm(**bench.on(session))
    with pytest.raises(ValidationError, match="awaiting_intent"):
        await bench.service.reopen_intent(**bench.on(session))


async def test_hai_lan_ghi_cung_luc_thi_lan_sau_bi_tu_choi() -> None:
    """Một lời gọi model mất hàng chục giây. Trong lúc đó phiên đã bị bỏ ở tab khác."""
    bench = Bench()
    session = await bench.open()

    class BoGiuaChung(AnswerAdvisor):
        async def answer(self, **kwargs: Any) -> Mapping[str, Any]:
            await bench.service.abandon(**bench.on(session))
            return await super().answer(**kwargs)

    bench.service._answer_advisor = BoGiuaChung()

    with pytest.raises(ValidationError, match="đổi ở nơi khác"):
        await bench.service.accept_intent(**bench.on(session))
    assert bench.sessions.rows[session.id].status is SessionStatus.ABANDONED


# ─── Nguyên liệu đã ghim ─────────────────────────────────────────────────────


async def test_luot_chat_them_sau_khi_mo_phien_khong_vao_nguyen_lieu() -> None:
    bench = Bench()
    session = await bench.open()
    them = copy.deepcopy(bench.sources.document["traces"][0])
    them["id"] = "trc_luot_moi"
    them["started_at"] = "2030-01-01T00:00:00+00:00"
    for step in them["steps"]:
        step["started_at"] = "2030-01-01T00:00:01+00:00"
    bench.sources.document["traces"].append(them)
    bench.sources.document["messages"].append(
        {"role": "user", "content": "à mà xoá kênh test đi", "created_at": them["started_at"]}
    )

    session = await bench.service.accept_intent(**bench.on(session))

    assert session.ir == _json(FIXTURES / "compiled" / f"{KB7}.ir.json")
    assert "xoá kênh test" not in bench.answer.calls[0]["briefing"]


async def test_du_lieu_cua_luot_da_ghim_doi_thi_phai_mo_phien_moi() -> None:
    bench = Bench()
    session = await bench.open()
    for trace in bench.sources.document["traces"]:
        for step in trace["steps"]:
            if step["kind"] == "tool_call":
                step["output"] = {"state": "gone", "payload": None}

    with pytest.raises(ValidationError, match="Mở một phiên mới"):
        await bench.service.accept_intent(**bench.on(session))
    assert bench.answer.calls == []


def test_doc_nguyen_lieu_theo_cac_luot_da_ghim() -> None:
    document = _json(FIXTURES / "sources" / "kb8-ba-luot.json")
    ca_ba = read_source(document)

    hai_luot_dau = read_source(document, only=[turn.trace_id for turn in ca_ba.turns[:2]])

    assert hai_luot_dau.turns == ca_ba.turns[:2]
    # Tin nhắn của lượt ba không rơi vào lượt hai.
    assert hai_luot_dau.turns[1].reply == ca_ba.turns[1].reply
    assert replace(ca_ba, turns=ca_ba.turns[:2]) == hai_luot_dau
