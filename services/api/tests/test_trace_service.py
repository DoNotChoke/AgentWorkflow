"""Kiểm tra use case của Trace bằng repository giả trong bộ nhớ.

Không cần PostgreSQL. Repository giả ở đây hiện thực đúng `ExecutionTraceRepository`
của tầng application, nên nếu port đổi chữ ký mà quên sửa hiện thực thật, mypy
sẽ bắt được ở cả hai phía — cùng cách `test_identity_service.py` làm.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import InMemoryPayloadStore

from app.core.errors import AuthorizationError, ValidationError
from app.domains.trace.application.service import (
    PayloadAvailability,
    PayloadSide,
    TraceService,
)
from app.domains.trace.domain.entities import (
    ExecutionTrace,
    PayloadLocation,
    PayloadRef,
    StepKind,
    StepStatus,
    StoredPayload,
    TraceSource,
    TraceStatus,
    TraceStep,
    TraceSubject,
)

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)

ORG_A = "org_a"
ORG_B = "org_b"
WORKSPACE = "ws_1"


class FakeExecutionTraceRepository:
    def __init__(self) -> None:
        self.traces: dict[str, ExecutionTrace] = {}
        self.steps: dict[str, list[TraceStep]] = {}
        #: Ghi lại `tenant_id` mà `TraceService` truyền cho mỗi step, để test
        #: xác nhận nó luôn khớp trace cha chứ không phải một giá trị bịa ra.
        self.step_tenant_ids: dict[str, str] = {}

    async def add_trace(self, trace: ExecutionTrace) -> None:
        self.traces[trace.id] = trace
        self.steps.setdefault(trace.id, [])

    async def add_step(self, step: TraceStep, *, tenant_id: str) -> None:
        self.steps.setdefault(step.trace_id, []).append(step)
        self.step_tenant_ids[step.id] = tenant_id

    async def update_trace(self, trace: ExecutionTrace) -> None:
        self.traces[trace.id] = trace

    async def get_trace(self, *, tenant_id: str, trace_id: str) -> ExecutionTrace | None:
        trace = self.traces.get(trace_id)
        if trace is None or trace.tenant_id != tenant_id:
            return None
        return trace

    async def list_steps(self, *, tenant_id: str, trace_id: str) -> list[TraceStep]:
        trace = self.traces.get(trace_id)
        if trace is None or trace.tenant_id != tenant_id:
            return []
        return list(self.steps.get(trace_id, []))

    async def get_step(self, *, tenant_id: str, trace_id: str, step_id: str) -> TraceStep | None:
        for step in await self.list_steps(tenant_id=tenant_id, trace_id=trace_id):
            if step.id == step_id:
                return step
        return None

    async def list_traces_for_conversation(
        self, *, tenant_id: str, conversation_id: str
    ) -> list[ExecutionTrace]:
        found = [
            trace
            for trace in self.traces.values()
            if trace.tenant_id == tenant_id and trace.subject.conversation_id == conversation_id
        ]
        return sorted(found, key=lambda trace: trace.started_at, reverse=True)


@pytest.fixture
def repo() -> FakeExecutionTraceRepository:
    return FakeExecutionTraceRepository()


@pytest.fixture
def payloads() -> InMemoryPayloadStore:
    return InMemoryPayloadStore()


@pytest.fixture
def service(repo: FakeExecutionTraceRepository, payloads: InMemoryPayloadStore) -> TraceService:
    return TraceService(traces=repo, payloads=payloads)


# ─── start ───────────────────────────────────────────────────────────────────


async def test_start_tao_trace_dang_chay(service: TraceService) -> None:
    trace = await service.start(
        tenant_id=ORG_A,
        workspace_id=WORKSPACE,
        source=TraceSource.AGENT,
        subject=TraceSubject(agent_id="agt_1", conversation_id="cnv_1"),
        initiated_by="usr_1",
        at=NOW,
    )

    assert trace.status is TraceStatus.RUNNING
    assert trace.id.startswith("trc_")


async def test_start_tu_choi_subject_thieu_truong_bat_buoc(service: TraceService) -> None:
    with pytest.raises(ValidationError):
        await service.start(
            tenant_id=ORG_A,
            workspace_id=WORKSPACE,
            source=TraceSource.AGENT,
            subject=TraceSubject(agent_id="agt_1"),  # thiếu conversation_id
            initiated_by="usr_1",
            at=NOW,
        )


# ─── start_agent_trace ────────────────────────────────────────────────────────
#
# Cửa dành cho `entrypoints/agent_wiring.py` — không cầm được `TraceSource`/
# `TraceSubject` của tầng domain (contract 5).


async def test_start_agent_trace_dung_nguon_va_subject(
    service: TraceService, repo: FakeExecutionTraceRepository
) -> None:
    trace = await service.start_agent_trace(
        tenant_id=ORG_A,
        workspace_id=WORKSPACE,
        agent_id="agt_1",
        conversation_id="cnv_1",
        initiated_by="usr_1",
        at=NOW,
    )

    assert trace.source is TraceSource.AGENT
    assert trace.subject.agent_id == "agt_1"
    assert trace.subject.conversation_id == "cnv_1"
    assert repo.traces[trace.id] == trace


# ─── append_step ─────────────────────────────────────────────────────────────


async def test_append_step_ghi_dung_tenant_cua_trace_cha(
    service: TraceService, repo: FakeExecutionTraceRepository
) -> None:
    trace = await service.start(
        tenant_id=ORG_A,
        workspace_id=WORKSPACE,
        source=TraceSource.AGENT,
        subject=TraceSubject(agent_id="agt_1", conversation_id="cnv_1"),
        initiated_by="usr_1",
        at=NOW,
    )

    step = await service.append_step(
        trace,
        kind=StepKind.MODEL_CALL,
        status=StepStatus.SUCCEEDED,
        started_at=NOW,
        completed_at=NOW + timedelta(milliseconds=200),
    )

    assert repo.step_tenant_ids[step.id] == ORG_A


async def test_append_step_nhieu_lan_khong_can_doc_lai_trace(
    service: TraceService, repo: FakeExecutionTraceRepository
) -> None:
    """`trace` trả về từ `start()` dùng lại được cho mọi `append_step` — không
    cần gọi `get_trace` giữa chừng."""
    trace = await service.start(
        tenant_id=ORG_A,
        workspace_id=WORKSPACE,
        source=TraceSource.AGENT,
        subject=TraceSubject(agent_id="agt_1", conversation_id="cnv_1"),
        initiated_by="usr_1",
        at=NOW,
    )

    model_step = await service.append_step(
        trace,
        kind=StepKind.MODEL_CALL,
        status=StepStatus.SUCCEEDED,
        started_at=NOW,
        completed_at=NOW + timedelta(milliseconds=200),
    )
    await service.append_step(
        trace,
        kind=StepKind.TOOL_CALL,
        status=StepStatus.SUCCEEDED,
        started_at=NOW + timedelta(milliseconds=210),
        completed_at=NOW + timedelta(milliseconds=800),
        parent_step_id=model_step.id,
        attributes={"tool_id": "slack.post_message"},
    )

    assert len(repo.steps[trace.id]) == 2


async def test_append_step_tu_choi_khi_trace_da_ket(service: TraceService) -> None:
    trace = await service.start(
        tenant_id=ORG_A,
        workspace_id=WORKSPACE,
        source=TraceSource.AGENT,
        subject=TraceSubject(agent_id="agt_1", conversation_id="cnv_1"),
        initiated_by="usr_1",
        at=NOW,
    )
    done = await service.complete(trace, status=TraceStatus.SUCCEEDED, at=NOW)

    with pytest.raises(ValidationError):
        await service.append_step(
            done,
            kind=StepKind.FINAL_RESPONSE,
            status=StepStatus.SUCCEEDED,
            started_at=NOW,
            completed_at=NOW,
        )


# ─── append_step_by_id ───────────────────────────────────────────────────────
#
# Đúng tình huống của Integration Gateway (WP3): chỉ cầm `trace_id`, không giữ
# `ExecutionTrace` — vì trace được mở ở một domain khác, có khi một tiến trình
# khác (Agent Runtime), không phải bởi chính lời gọi này.


async def test_append_step_by_id_ghi_duoc_step_khi_trace_dang_chay(
    service: TraceService, repo: FakeExecutionTraceRepository
) -> None:
    trace = await service.start(
        tenant_id=ORG_A,
        workspace_id=WORKSPACE,
        source=TraceSource.AGENT,
        subject=TraceSubject(agent_id="agt_1", conversation_id="cnv_1"),
        initiated_by="usr_1",
        at=NOW,
    )

    step = await service.append_step_by_id(
        tenant_id=ORG_A,
        trace_id=trace.id,
        kind=StepKind.TOOL_CALL,
        status=StepStatus.SUCCEEDED,
        started_at=NOW,
        completed_at=NOW + timedelta(milliseconds=500),
        attributes={"tool_id": "slack.post_message"},
    )

    assert step is not None
    assert repo.steps[trace.id] == [step]


async def test_append_step_by_id_tra_none_khi_trace_khong_ton_tai(
    service: TraceService,
) -> None:
    step = await service.append_step_by_id(
        tenant_id=ORG_A,
        trace_id="trc_khong_ton_tai",
        kind=StepKind.TOOL_CALL,
        status=StepStatus.SUCCEEDED,
        started_at=NOW,
        completed_at=NOW,
    )

    assert step is None


async def test_append_step_by_id_tra_none_khi_trace_da_ket(service: TraceService) -> None:
    trace = await service.start(
        tenant_id=ORG_A,
        workspace_id=WORKSPACE,
        source=TraceSource.AGENT,
        subject=TraceSubject(agent_id="agt_1", conversation_id="cnv_1"),
        initiated_by="usr_1",
        at=NOW,
    )
    await service.complete(trace, status=TraceStatus.SUCCEEDED, at=NOW)

    step = await service.append_step_by_id(
        tenant_id=ORG_A,
        trace_id=trace.id,
        kind=StepKind.TOOL_CALL,
        status=StepStatus.SUCCEEDED,
        started_at=NOW,
        completed_at=NOW,
    )

    assert step is None


async def test_append_step_by_id_khong_lan_sang_trace_cua_tenant_khac(
    service: TraceService,
) -> None:
    """Một `trace_id` có thật nhưng thuộc tenant khác bị đối xử như không tồn
    tại — không phải một quyết định `AuthorizationError`, vì đây là ghi trace
    nền, không phải một request của người dùng đang chờ phản hồi lỗi."""
    trace = await service.start(
        tenant_id=ORG_A,
        workspace_id=WORKSPACE,
        source=TraceSource.AGENT,
        subject=TraceSubject(agent_id="agt_1", conversation_id="cnv_1"),
        initiated_by="usr_1",
        at=NOW,
    )

    step = await service.append_step_by_id(
        tenant_id=ORG_B,
        trace_id=trace.id,
        kind=StepKind.TOOL_CALL,
        status=StepStatus.SUCCEEDED,
        started_at=NOW,
        completed_at=NOW,
    )

    assert step is None


# ─── record_tool_call ────────────────────────────────────────────────────────
#
# Cửa dành cho `entrypoints/wiring.py` — chỉ nhận kiểu nguyên thuỷ, vì wiring
# không được import tầng `domain` (contract 5). Test này thay wiring, truyền
# đúng những gì `ToolInvocationRecord` của Integration mang theo.


async def test_record_tool_call_ghi_thanh_buoc_tool_call(
    service: TraceService, repo: FakeExecutionTraceRepository
) -> None:
    trace = await service.start(
        tenant_id=ORG_A,
        workspace_id=WORKSPACE,
        source=TraceSource.AGENT,
        subject=TraceSubject(agent_id="agt_1", conversation_id="cnv_1"),
        initiated_by="usr_1",
        at=NOW,
    )

    step = await service.record_tool_call(
        tenant_id=ORG_A,
        trace_id=trace.id,
        status="succeeded",
        started_at=NOW,
        completed_at=NOW + timedelta(milliseconds=500),
        input_payload={"channel": "C123", "text": "xin chào"},
        output_payload={"ts": "1700000000.000100"},
        attributes={"tool_id": "slack.post_message"},
    )

    assert step is not None
    assert step.kind is StepKind.TOOL_CALL
    assert step.status is StepStatus.SUCCEEDED
    assert step.input_payload is not None
    assert step.input_payload.inline == {"channel": "C123", "text": "xin chào"}
    assert step.output_payload is not None
    assert step.output_payload.ref.sha256 == PayloadRef.of({"ts": "1700000000.000100"}).sha256


async def test_record_tool_call_replayed_giu_dung_status(
    service: TraceService,
) -> None:
    trace = await service.start(
        tenant_id=ORG_A,
        workspace_id=WORKSPACE,
        source=TraceSource.AGENT,
        subject=TraceSubject(agent_id="agt_1", conversation_id="cnv_1"),
        initiated_by="usr_1",
        at=NOW,
    )

    step = await service.record_tool_call(
        tenant_id=ORG_A,
        trace_id=trace.id,
        status="replayed",
        started_at=NOW,
        completed_at=NOW,
    )

    assert step is not None
    assert step.status is StepStatus.REPLAYED


async def test_record_tool_call_khong_co_payload_thi_de_trong(
    service: TraceService,
) -> None:
    trace = await service.start(
        tenant_id=ORG_A,
        workspace_id=WORKSPACE,
        source=TraceSource.AGENT,
        subject=TraceSubject(agent_id="agt_1", conversation_id="cnv_1"),
        initiated_by="usr_1",
        at=NOW,
    )

    step = await service.record_tool_call(
        tenant_id=ORG_A,
        trace_id=trace.id,
        status="failed",
        started_at=NOW,
        completed_at=NOW,
        error_category="ProviderUnavailable",
    )

    assert step is not None
    assert step.input_payload is None
    assert step.output_payload is None
    assert step.error_category == "ProviderUnavailable"


async def test_record_tool_call_tra_none_khi_khong_co_trace(service: TraceService) -> None:
    step = await service.record_tool_call(
        tenant_id=ORG_A,
        trace_id="trc_khong_ton_tai",
        status="succeeded",
        started_at=NOW,
        completed_at=NOW,
    )

    assert step is None


# ─── record_step ──────────────────────────────────────────────────────────────
#
# Cửa dành cho `entrypoints/agent_wiring.py` — không cầm được `StepKind`/
# `StepStatus` của tầng domain (contract 5), khác `record_tool_call` (cố định
# kind=TOOL_CALL, dành riêng cho Integration).


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("model_call", StepKind.MODEL_CALL),
        ("tool_call", StepKind.TOOL_CALL),
        ("final_response", StepKind.FINAL_RESPONSE),
        ("decision", StepKind.DECISION),
        ("error", StepKind.ERROR),
    ],
)
async def test_record_step_dich_dung_kind(
    service: TraceService, kind: str, expected: StepKind
) -> None:
    trace = await service.start(
        tenant_id=ORG_A,
        workspace_id=WORKSPACE,
        source=TraceSource.AGENT,
        subject=TraceSubject(agent_id="agt_1", conversation_id="cnv_1"),
        initiated_by="usr_1",
        at=NOW,
    )

    step = await service.record_step(
        tenant_id=ORG_A,
        trace_id=trace.id,
        kind=kind,
        status="succeeded",
        started_at=NOW,
        completed_at=NOW,
    )

    assert step is not None
    assert step.kind is expected


async def test_record_step_giu_lai_attributes_va_error_category(
    service: TraceService,
) -> None:
    trace = await service.start(
        tenant_id=ORG_A,
        workspace_id=WORKSPACE,
        source=TraceSource.AGENT,
        subject=TraceSubject(agent_id="agt_1", conversation_id="cnv_1"),
        initiated_by="usr_1",
        at=NOW,
    )

    step = await service.record_step(
        tenant_id=ORG_A,
        trace_id=trace.id,
        kind="model_call",
        status="failed",
        started_at=NOW,
        completed_at=NOW,
        attributes={"model": "claude-sonnet-5"},
        error_category="ProviderUnavailable",
    )

    assert step is not None
    assert step.attributes == {"model": "claude-sonnet-5"}
    assert step.error_category == "ProviderUnavailable"


async def test_record_step_cat_payload_cua_buoc_model_call(
    service: TraceService, payloads: InMemoryPayloadStore
) -> None:
    trace = await service.start(
        tenant_id=ORG_A,
        workspace_id=WORKSPACE,
        source=TraceSource.AGENT,
        subject=TraceSubject(agent_id="agt_1", conversation_id="cnv_1"),
        initiated_by="usr_1",
        at=NOW,
    )

    step = await service.record_step(
        tenant_id=ORG_A,
        trace_id=trace.id,
        kind="model_call",
        status="succeeded",
        started_at=NOW,
        completed_at=NOW,
        input_payload={"system": "Bạn là trợ lý."},
        output_payload={"text": "chào"},
    )

    assert step is not None
    assert step.input_payload is not None
    assert step.input_payload.inline == {"system": "Bạn là trợ lý."}
    assert payloads.puts == [(ORG_A, {"system": "Bạn là trợ lý."}), (ORG_A, {"text": "chào"})]


async def test_record_step_dung_step_id_nguoi_goi_phat_truoc(service: TraceService) -> None:
    trace = await service.start(
        tenant_id=ORG_A,
        workspace_id=WORKSPACE,
        source=TraceSource.AGENT,
        subject=TraceSubject(agent_id="agt_1", conversation_id="cnv_1"),
        initiated_by="usr_1",
        at=NOW,
    )

    given = await service.record_step(
        tenant_id=ORG_A,
        trace_id=trace.id,
        kind="tool_call",
        status="succeeded",
        started_at=NOW,
        completed_at=NOW,
        step_id="stp_phat_truoc",
    )
    generated = await service.record_step(
        tenant_id=ORG_A,
        trace_id=trace.id,
        kind="final_response",
        status="succeeded",
        started_at=NOW,
        completed_at=NOW,
    )

    assert given is not None
    assert given.id == "stp_phat_truoc"
    assert generated is not None
    assert generated.id.startswith("stp_")
    assert generated.id != given.id


async def test_record_step_tra_none_khi_khong_co_trace(service: TraceService) -> None:
    step = await service.record_step(
        tenant_id=ORG_A,
        trace_id="trc_khong_ton_tai",
        kind="model_call",
        status="succeeded",
        started_at=NOW,
        completed_at=NOW,
    )

    assert step is None


# ─── complete ────────────────────────────────────────────────────────────────


async def test_complete_luu_lai_trang_thai_moi(
    service: TraceService, repo: FakeExecutionTraceRepository
) -> None:
    trace = await service.start(
        tenant_id=ORG_A,
        workspace_id=WORKSPACE,
        source=TraceSource.AGENT,
        subject=TraceSubject(agent_id="agt_1", conversation_id="cnv_1"),
        initiated_by="usr_1",
        at=NOW,
    )

    done = await service.complete(
        trace, status=TraceStatus.FAILED, at=NOW, error_category="Timeout"
    )

    assert repo.traces[trace.id].status is TraceStatus.FAILED
    assert repo.traces[trace.id].error_category == "Timeout"
    assert done.status is TraceStatus.FAILED


# ─── complete_by_id ───────────────────────────────────────────────────────────
#
# Cửa dành cho `entrypoints/agent_wiring.py` — không cầm được `ExecutionTrace`
# đã mở ở một tiến trình khác (Agent Runtime tự mở trace của chính nó, nhưng
# hàm này không giả định người gọi giữ object đó trong tay).


async def test_complete_by_id_dong_dung_trace(
    service: TraceService, repo: FakeExecutionTraceRepository
) -> None:
    trace = await service.start(
        tenant_id=ORG_A,
        workspace_id=WORKSPACE,
        source=TraceSource.AGENT,
        subject=TraceSubject(agent_id="agt_1", conversation_id="cnv_1"),
        initiated_by="usr_1",
        at=NOW,
    )

    done = await service.complete_by_id(
        tenant_id=ORG_A, trace_id=trace.id, status="succeeded", at=NOW
    )

    assert done is not None
    assert done.status is TraceStatus.SUCCEEDED
    assert repo.traces[trace.id].status is TraceStatus.SUCCEEDED


async def test_complete_by_id_tra_none_khi_khong_ton_tai(service: TraceService) -> None:
    done = await service.complete_by_id(
        tenant_id=ORG_A, trace_id="trc_khong_ton_tai", status="succeeded", at=NOW
    )

    assert done is None


async def test_complete_by_id_tra_none_khi_da_ket(service: TraceService) -> None:
    trace = await service.start(
        tenant_id=ORG_A,
        workspace_id=WORKSPACE,
        source=TraceSource.AGENT,
        subject=TraceSubject(agent_id="agt_1", conversation_id="cnv_1"),
        initiated_by="usr_1",
        at=NOW,
    )
    await service.complete_by_id(tenant_id=ORG_A, trace_id=trace.id, status="succeeded", at=NOW)

    done_again = await service.complete_by_id(
        tenant_id=ORG_A, trace_id=trace.id, status="failed", at=NOW
    )

    assert done_again is None


# ─── get ─────────────────────────────────────────────────────────────────────


async def test_get_tra_ve_trace_kem_step(service: TraceService) -> None:
    trace = await service.start(
        tenant_id=ORG_A,
        workspace_id=WORKSPACE,
        source=TraceSource.AGENT,
        subject=TraceSubject(agent_id="agt_1", conversation_id="cnv_1"),
        initiated_by="usr_1",
        at=NOW,
    )
    await service.append_step(
        trace,
        kind=StepKind.MODEL_CALL,
        status=StepStatus.SUCCEEDED,
        started_at=NOW,
        completed_at=NOW + timedelta(milliseconds=200),
    )

    detail = await service.get(tenant_id=ORG_A, trace_id=trace.id)

    assert detail.trace.id == trace.id
    assert len(detail.steps) == 1


async def test_get_khong_ton_tai_bao_authorization_error(service: TraceService) -> None:
    with pytest.raises(AuthorizationError):
        await service.get(tenant_id=ORG_A, trace_id="trc_khong_ton_tai")


async def test_get_trace_thuoc_tenant_khac_cung_bao_nhu_khong_ton_tai(
    service: TraceService,
) -> None:
    """Đối xử "không phải của tôi" y hệt "không tồn tại" — không lộ rằng id
    đó có thật, cùng cách `Connection` làm."""
    trace = await service.start(
        tenant_id=ORG_A,
        workspace_id=WORKSPACE,
        source=TraceSource.AGENT,
        subject=TraceSubject(agent_id="agt_1", conversation_id="cnv_1"),
        initiated_by="usr_1",
        at=NOW,
    )

    with pytest.raises(AuthorizationError):
        await service.get(tenant_id=ORG_B, trace_id=trace.id)


# ─── list_for_conversation ───────────────────────────────────────────────────


async def test_list_for_conversation_moi_nhat_truoc(service: TraceService) -> None:
    first = await service.start(
        tenant_id=ORG_A,
        workspace_id=WORKSPACE,
        source=TraceSource.AGENT,
        subject=TraceSubject(agent_id="agt_1", conversation_id="cnv_1"),
        initiated_by="usr_1",
        at=NOW,
    )
    second = await service.start(
        tenant_id=ORG_A,
        workspace_id=WORKSPACE,
        source=TraceSource.AGENT,
        subject=TraceSubject(agent_id="agt_1", conversation_id="cnv_1"),
        initiated_by="usr_1",
        at=NOW + timedelta(minutes=5),
    )

    traces = await service.list_for_conversation(tenant_id=ORG_A, conversation_id="cnv_1")

    assert [trace.id for trace in traces] == [second.id, first.id]


async def test_list_for_conversation_khong_lan_sang_tenant_khac(service: TraceService) -> None:
    await service.start(
        tenant_id=ORG_A,
        workspace_id=WORKSPACE,
        source=TraceSource.AGENT,
        subject=TraceSubject(agent_id="agt_1", conversation_id="cnv_1"),
        initiated_by="usr_1",
        at=NOW,
    )

    traces = await service.list_for_conversation(tenant_id=ORG_B, conversation_id="cnv_1")

    assert traces == []


# ─── get_payload ─────────────────────────────────────────────────────────────


class ForgetfulPayloadStore:
    """Cất ra object storage, rồi quên — hình dạng của một lifecycle rule đã dọn."""

    async def put(self, *, tenant_id: str, payload: Mapping[str, Any]) -> StoredPayload:
        return StoredPayload(ref=PayloadRef.of(payload), location=PayloadLocation.OBJECT)

    async def get(self, *, tenant_id: str, stored: StoredPayload) -> Mapping[str, Any] | None:
        return None


async def _mot_buoc_co_payload(service: TraceService) -> tuple[str, str]:
    trace = await service.start(
        tenant_id=ORG_A,
        workspace_id=WORKSPACE,
        source=TraceSource.AGENT,
        subject=TraceSubject(agent_id="agt_1", conversation_id="cnv_1"),
        initiated_by="usr_1",
        at=NOW,
    )
    step = await service.append_step(
        trace,
        kind=StepKind.TOOL_CALL,
        status=StepStatus.SUCCEEDED,
        started_at=NOW,
        completed_at=NOW + timedelta(milliseconds=500),
        input_payload={"channel": "C123"},
    )
    return trace.id, step.id


async def test_get_payload_tra_ve_noi_dung_da_cat(service: TraceService) -> None:
    trace_id, step_id = await _mot_buoc_co_payload(service)

    view = await service.get_payload(
        tenant_id=ORG_A, trace_id=trace_id, step_id=step_id, side=PayloadSide.INPUT
    )

    assert view.availability is PayloadAvailability.AVAILABLE
    assert view.payload == {"channel": "C123"}
    assert view.ref is not None


async def test_get_payload_phia_khong_co_gi_la_not_stored(service: TraceService) -> None:
    trace_id, step_id = await _mot_buoc_co_payload(service)

    view = await service.get_payload(
        tenant_id=ORG_A, trace_id=trace_id, step_id=step_id, side=PayloadSide.OUTPUT
    )

    assert view.availability is PayloadAvailability.NOT_STORED
    assert view.ref is None


async def test_get_payload_da_cat_roi_mat_la_gone(repo: FakeExecutionTraceRepository) -> None:
    """`gone` khác `not_stored`: một cái từng có, một cái chưa bao giờ.

    Người đang đọc lại một lượt Agent hỏng cần đọc ra hai câu khác nhau.
    """
    service = TraceService(traces=repo, payloads=ForgetfulPayloadStore())
    trace_id, step_id = await _mot_buoc_co_payload(service)

    view = await service.get_payload(
        tenant_id=ORG_A, trace_id=trace_id, step_id=step_id, side=PayloadSide.INPUT
    )

    assert view.availability is PayloadAvailability.GONE
    assert view.payload is None
    assert view.ref is not None


async def test_get_payload_cua_step_khong_ton_tai_bi_tu_choi(service: TraceService) -> None:
    trace_id, _ = await _mot_buoc_co_payload(service)

    with pytest.raises(AuthorizationError):
        await service.get_payload(
            tenant_id=ORG_A,
            trace_id=trace_id,
            step_id="stp_khong_ton_tai",
            side=PayloadSide.INPUT,
        )


async def test_get_payload_khong_lan_sang_tenant_khac(service: TraceService) -> None:
    trace_id, step_id = await _mot_buoc_co_payload(service)

    with pytest.raises(AuthorizationError):
        await service.get_payload(
            tenant_id=ORG_B, trace_id=trace_id, step_id=step_id, side=PayloadSide.INPUT
        )
