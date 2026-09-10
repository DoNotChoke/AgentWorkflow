"""Use case của Trace: mở một trace, ghi bước, đóng trace, đọc lại."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.errors import AuthorizationError
from app.core.ids import ResourceKind, new_id
from app.core.logging import get_logger
from app.domains.trace.application.ports import ExecutionTraceRepository, PayloadStore
from app.domains.trace.domain.entities import (
    ExecutionTrace,
    StepKind,
    StepStatus,
    StoredPayload,
    TraceSource,
    TraceStatus,
    TraceStep,
    TraceSubject,
    ensure_attributes_safe,
    ensure_not_terminal,
    ensure_subject_matches_source,
)
from app.domains.trace.domain.entities import complete as _close_trace

log = get_logger(__name__)

#: Trace không thuộc tenant của người gọi được đối xử y như không tồn tại —
#: cùng thông điệp, cùng mã lỗi. Trả lời khác đi là để lộ rằng id đó có thật.
_NOT_FOUND = "Không tìm thấy execution trace này."


@dataclass(frozen=True, slots=True)
class TraceDetail:
    """Một trace kèm toàn bộ các bước của nó — hình dạng mà UI cần để vẽ lại
    cây thực thi."""

    trace: ExecutionTrace
    steps: list[TraceStep]


class TraceService:
    __slots__ = ("_payloads", "_traces")

    def __init__(self, *, traces: ExecutionTraceRepository, payloads: PayloadStore) -> None:
        self._traces = traces
        self._payloads = payloads

    # ─── Ghi ─────────────────────────────────────────────────────────────────

    async def start(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        source: TraceSource,
        subject: TraceSubject,
        initiated_by: str,
        at: datetime,
    ) -> ExecutionTrace:
        """Mở một trace mới."""
        ensure_subject_matches_source(source, subject)

        trace = ExecutionTrace(
            id=new_id(ResourceKind.EXECUTION_TRACE),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            source=source,
            status=TraceStatus.RUNNING,
            subject=subject,
            initiated_by=initiated_by,
            started_at=at,
        )
        await self._traces.add_trace(trace)
        log.info(
            "execution_trace_started",
            trace_id=trace.id,
            source=str(source),
            conversation_id=subject.conversation_id,
        )
        return trace

    async def start_agent_trace(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        agent_id: str,
        conversation_id: str,
        initiated_by: str,
        at: datetime,
    ) -> ExecutionTrace:
        """Mở trace cho một lượt Agent, từ dữ liệu nguyên thuỷ.

        Cửa dành cho `entrypoints/agent_wiring.py` — nơi không được import
        `TraceSource`/`TraceSubject` của tầng `domain` (contract 5). Không có
        biến thể tương tự cho `source=WORKFLOW`: khi Phase 4 cần, thêm
        `start_workflow_trace` riêng theo cùng khuôn, không mở rộng chữ ký này.
        """
        return await self.start(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            source=TraceSource.AGENT,
            subject=TraceSubject(agent_id=agent_id, conversation_id=conversation_id),
            initiated_by=initiated_by,
            at=at,
        )

    async def append_step(
        self,
        trace: ExecutionTrace,
        *,
        kind: StepKind,
        status: StepStatus,
        started_at: datetime,
        completed_at: datetime,
        parent_step_id: str | None = None,
        input_payload: Mapping[str, Any] | None = None,
        output_payload: Mapping[str, Any] | None = None,
        attributes: Mapping[str, Any] | None = None,
        error_category: str | None = None,
        otel_trace_id: str | None = None,
        otel_span_id: str | None = None,
    ) -> TraceStep:
        """Ghi một bước vào `trace`. Payload được cất trước, bước ghi sau."""
        ensure_not_terminal(trace)
        attrs = dict(attributes or {})
        ensure_attributes_safe(attrs)

        step = TraceStep(
            id=new_id(ResourceKind.TRACE_STEP),
            trace_id=trace.id,
            kind=kind,
            status=status,
            started_at=started_at,
            completed_at=completed_at,
            parent_step_id=parent_step_id,
            input_payload=await self._store(trace.tenant_id, input_payload),
            output_payload=await self._store(trace.tenant_id, output_payload),
            attributes=attrs,
            error_category=error_category,
            otel_trace_id=otel_trace_id,
            otel_span_id=otel_span_id,
        )
        await self._traces.add_step(step, tenant_id=trace.tenant_id)
        return step

    async def _store(
        self, tenant_id: str, payload: Mapping[str, Any] | None
    ) -> StoredPayload | None:
        if payload is None:
            return None
        return await self._payloads.put(tenant_id=tenant_id, payload=payload)

    async def append_step_by_id(
        self,
        *,
        tenant_id: str,
        trace_id: str,
        kind: StepKind,
        status: StepStatus,
        started_at: datetime,
        completed_at: datetime,
        parent_step_id: str | None = None,
        input_payload: Mapping[str, Any] | None = None,
        output_payload: Mapping[str, Any] | None = None,
        attributes: Mapping[str, Any] | None = None,
        error_category: str | None = None,
        otel_trace_id: str | None = None,
        otel_span_id: str | None = None,
    ) -> TraceStep | None:
        """Biến thể của `append_step` cho người gọi chỉ cầm `trace_id`, không
        giữ `ExecutionTrace` trong tay.

        Trả `None` thay vì ném lỗi khi trace không tồn tại hoặc đã kết thúc —
        ghi trace là quan sát
        """
        trace = await self._traces.get_trace(tenant_id=tenant_id, trace_id=trace_id)
        if trace is None or trace.is_terminal:
            log.warning(
                "execution_trace_step_bo_qua",
                trace_id=trace_id,
                reason="khong_ton_tai" if trace is None else "da_ket_thuc",
            )
            return None

        return await self.append_step(
            trace,
            kind=kind,
            status=status,
            started_at=started_at,
            completed_at=completed_at,
            parent_step_id=parent_step_id,
            input_payload=input_payload,
            output_payload=output_payload,
            attributes=attributes,
            error_category=error_category,
            otel_trace_id=otel_trace_id,
            otel_span_id=otel_span_id,
        )

    async def record_tool_call(
        self,
        *,
        tenant_id: str,
        trace_id: str,
        status: str,
        started_at: datetime,
        completed_at: datetime,
        parent_step_id: str | None = None,
        input_payload: Mapping[str, Any] | None = None,
        output_payload: Mapping[str, Any] | None = None,
        attributes: Mapping[str, Any] | None = None,
        error_category: str | None = None,
        otel_trace_id: str | None = None,
        otel_span_id: str | None = None,
    ) -> TraceStep | None:
        """Ghi một bước `TOOL_CALL` từ dữ liệu nguyên thuỷ (chuỗi, số, dict).

        `status` nhận đúng ba giá trị của `StepStatus` dưới dạng chuỗi
        (`"succeeded"`, `"failed"`, `"replayed"`) — không tình cờ trùng khớp:
        đây cũng là hình dạng `status` của `ToolInvocationRecord` bên
        Integration.
        """
        return await self.append_step_by_id(
            tenant_id=tenant_id,
            trace_id=trace_id,
            kind=StepKind.TOOL_CALL,
            status=StepStatus(status),
            started_at=started_at,
            completed_at=completed_at,
            parent_step_id=parent_step_id,
            input_payload=input_payload,
            output_payload=output_payload,
            attributes=attributes,
            error_category=error_category,
            otel_trace_id=otel_trace_id,
            otel_span_id=otel_span_id,
        )

    async def record_step(
        self,
        *,
        tenant_id: str,
        trace_id: str,
        kind: str,
        status: str,
        started_at: datetime,
        completed_at: datetime,
        parent_step_id: str | None = None,
        attributes: Mapping[str, Any] | None = None,
        error_category: str | None = None,
    ) -> TraceStep | None:
        """Ghi một bước bất kỳ từ dữ liệu nguyên thuỷ — cửa dành cho
        `entrypoints/agent_wiring.py`, tương tự `record_tool_call` dành cho
        Integration nhưng không cố định `kind` và không mang `PayloadRef`
        (bước của Agent không có payload tool để đính vân tay).

        `kind`/`status` nhận đúng giá trị của `StepKind`/`StepStatus` dưới
        dạng chuỗi (`"model_call"`, `"tool_call"`, `"final_response"`, ...).
        """
        return await self.append_step_by_id(
            tenant_id=tenant_id,
            trace_id=trace_id,
            kind=StepKind(kind),
            status=StepStatus(status),
            started_at=started_at,
            completed_at=completed_at,
            parent_step_id=parent_step_id,
            attributes=attributes,
            error_category=error_category,
        )

    async def complete(
        self,
        trace: ExecutionTrace,
        *,
        status: TraceStatus,
        at: datetime,
        error_category: str | None = None,
    ) -> ExecutionTrace:
        """Đóng trace. Trả về entity mới — `trace` truyền vào không đổi."""
        done = _close_trace(trace, status=status, at=at, error_category=error_category)
        await self._traces.update_trace(done)
        log.info("execution_trace_completed", trace_id=done.id, status=str(status))
        return done

    async def complete_by_id(
        self,
        *,
        tenant_id: str,
        trace_id: str,
        status: str,
        at: datetime,
        error_category: str | None = None,
    ) -> ExecutionTrace | None:
        """Đóng trace từ dữ liệu nguyên thuỷ — cửa dành cho `entrypoints/agent_wiring.py`.

        Trả `None` nếu trace không tồn tại hoặc đã kết, thay vì ném lỗi — cùng
        triết lý `append_step_by_id`: đóng trace là dọn dẹp quan sát khi một
        lượt Agent vừa xong, không phải một phần giao dịch nghiệp vụ của
        request đang xử lý.
        """
        trace = await self._traces.get_trace(tenant_id=tenant_id, trace_id=trace_id)
        if trace is None or trace.is_terminal:
            log.warning(
                "execution_trace_complete_bo_qua",
                trace_id=trace_id,
                reason="khong_ton_tai" if trace is None else "da_ket_thuc",
            )
            return None
        return await self.complete(
            trace, status=TraceStatus(status), at=at, error_category=error_category
        )

    # ─── Đọc ─────────────────────────────────────────────────────────────────

    async def get(self, *, tenant_id: str, trace_id: str) -> TraceDetail:
        trace = await self._traces.get_trace(tenant_id=tenant_id, trace_id=trace_id)
        if trace is None:
            raise AuthorizationError(_NOT_FOUND)
        steps = await self._traces.list_steps(tenant_id=tenant_id, trace_id=trace_id)
        return TraceDetail(trace=trace, steps=steps)

    async def list_for_conversation(
        self, *, tenant_id: str, conversation_id: str
    ) -> Sequence[ExecutionTrace]:
        """Lịch sử trace của một cuộc hội thoại — mỗi lượt chat một trace."""
        return await self._traces.list_traces_for_conversation(
            tenant_id=tenant_id, conversation_id=conversation_id
        )
