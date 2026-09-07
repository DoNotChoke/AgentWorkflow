"""Nối Agent Runtime (domain `agent`) với domain `trace`.

Tách biệt khỏi `entrypoints/wiring.py` — **không gộp hai file lại**, dù cả hai
đều là "chỗ nối domain lại với nhau". Contract 6 cấm `app.entrypoints.agent`
chạm `entrypoints.wiring` (kể cả gián tiếp): đó là module duy nhất dựng được
`CredentialResolver`, và một `import` từ tiến trình Agent Runtime tới đó là đủ
để đưa token vào cùng tiến trình với LLM (§2.4, ADR-0002). Import module này
từ `entrypoints/agent.py` là an toàn; import `entrypoints/wiring.py` thì không.

Vì vậy module này chỉ được phép biết hai domain không mang credential:
`agent` và `trace`. Nó không import bất cứ thứ gì từ `app.domains.connection`
hay `app.core.crypto` — kể cả chỉ để đọc metadata (tên kết nối, provider),
không đụng token. Ranh giới ở đây là **domain**, không phải "loại dữ liệu cụ
thể nào rò hay không rò": ngày `agent` cần biết `connection_id` nào dùng cho
một tool (`ConnectionResolver` ở `agent/application/ports.py`), lời giải phải
đi qua một endpoint HTTP mới trên Control Plane — giống hệt cách tool tự nó đã
đi qua `IntegrationClient` — không phải một import thẳng vào đây. Xem
ADR-0003.

Cùng ràng buộc `entrypoints/wiring.py` đã chịu (contract 5): module này không
được import thẳng tầng `domain` của bất kỳ domain nào. Vì thế mọi chỗ chạm
`trace` đều đi qua các cửa nguyên thuỷ của `TraceService`
(`start_agent_trace`, `record_step`, `complete_by_id`) — không phải
`TraceSource`/`TraceStatus`/`StepKind` của domain `trace`.

Mỗi lần ghi mở một transaction riêng (`session_scope()`), không dùng chung
session của request đang xử lý lượt Agent — cùng lý do `IntegrationTraceSink`
ở `entrypoints/wiring.py` làm vậy: một lượt Agent lỗi giữa chừng không được
phép cuốn theo các bước trace đã ghi trước đó, đúng lúc chúng cần nhất để biết
"vì sao nó hỏng".
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import session_scope
from app.domains.trace.application.service import TraceService
from app.domains.trace.infrastructure.repositories import SqlAlchemyExecutionTraceRepository


def _build_trace_service(session: AsyncSession) -> TraceService:
    return TraceService(traces=SqlAlchemyExecutionTraceRepository(session))


class AgentTraceSink:
    """Hiện thực `ExecutionTraceSink` của domain `agent` bằng `TraceService`.

    Nhận `trace_id` cố định lúc dựng — một sink phục vụ đúng một trace, khớp
    vòng đời một lượt `AgentRuntime.run_turn()`. Khác `IntegrationTraceSink`
    (`entrypoints/wiring.py`), nơi một sink dùng chung cho nhiều lời gọi tool
    không biết trước trace nào (`execution_trace_id` tới theo từng request);
    ở đây không cần vậy, vì `agent_wiring` chính là nơi mở trace.
    """

    def __init__(self, *, tenant_id: str, trace_id: str) -> None:
        self._tenant_id = tenant_id
        self._trace_id = trace_id

    async def append_step(
        self,
        *,
        kind: str,
        status: str,
        started_at: datetime,
        completed_at: datetime,
        attributes: Mapping[str, Any] | None = None,
        error_category: str | None = None,
    ) -> None:
        async with session_scope() as session:
            await _build_trace_service(session).record_step(
                tenant_id=self._tenant_id,
                trace_id=self._trace_id,
                kind=kind,
                status=status,
                started_at=started_at,
                completed_at=completed_at,
                attributes=attributes,
                error_category=error_category,
            )


async def start_agent_trace(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    conversation_id: str,
    initiated_by: str,
) -> tuple[str, AgentTraceSink]:
    """Mở một Execution Trace mới cho một lượt Agent.

    Trả về `(trace_id, sink)`. Người gọi (endpoint HTTP của Agent Runtime, xem
    WP10) giữ `trace_id` để gọi `complete_agent_trace(...)` khi lượt kết thúc
    — dù thành công hay lỗi (`try`/`finally`), và truyền `sink` thẳng vào
    `AgentRuntime(trace=sink)`.
    """
    async with session_scope() as session:
        trace = await _build_trace_service(session).start_agent_trace(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            initiated_by=initiated_by,
            at=datetime.now(UTC),
        )
    return trace.id, AgentTraceSink(tenant_id=tenant_id, trace_id=trace.id)


async def complete_agent_trace(
    *,
    tenant_id: str,
    trace_id: str,
    status: str,
    error_category: str | None = None,
) -> None:
    """Đóng trace đã mở bởi `start_agent_trace`.

    Không ném lỗi nếu trace không còn tồn tại hoặc đã kết — đóng trace là dọn
    dẹp quan sát cho một lượt vừa xong, không phải một bước bắt buộc của giao
    dịch nghiệp vụ mà request đang xử lý.
    """
    async with session_scope() as session:
        await _build_trace_service(session).complete_by_id(
            tenant_id=tenant_id,
            trace_id=trace_id,
            status=status,
            at=datetime.now(UTC),
            error_category=error_category,
        )
