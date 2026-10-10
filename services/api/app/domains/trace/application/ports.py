"""Interface mà tầng `infrastructure` phải hiện thực hoá."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from app.domains.trace.domain.entities import ExecutionTrace, StoredPayload, TraceStep


class ExecutionTraceRepository(Protocol):
    """Bền vững hoá trace. `TraceService` là nơi duy nhất gọi vào đây."""

    async def add_trace(self, trace: ExecutionTrace) -> None: ...

    async def add_step(self, step: TraceStep, *, tenant_id: str) -> None:
        """`tenant_id` không nằm trên `TraceStep` (tầng domain không cần nó —
        `trace_id` đã đủ)."""

    async def update_trace(self, trace: ExecutionTrace) -> None:
        """Ghi lại một trace đã đổi trạng thái (đóng trace)."""

    async def get_trace(self, *, tenant_id: str, trace_id: str) -> ExecutionTrace | None: ...

    async def list_steps(self, *, tenant_id: str, trace_id: str) -> list[TraceStep]:
        """Các bước của một trace, theo thứ tự bắt đầu."""

    async def get_step(self, *, tenant_id: str, trace_id: str, step_id: str) -> TraceStep | None:
        """Một bước, hoặc `None` nếu nó không thuộc trace và tenant đó."""

    async def list_traces_for_conversation(
        self, *, tenant_id: str, conversation_id: str
    ) -> list[ExecutionTrace]:
        """Mọi trace của một cuộc hội thoại, mới nhất trước."""

    async def get_trace_for_execution(
        self, *, tenant_id: str, execution_id: str
    ) -> ExecutionTrace | None:
        """Trace của một lần chạy workflow. Mỗi lần chạy có nhiều nhất một."""


class PayloadStore(Protocol):
    """Cất và đọc lại payload của một bước trace (§4.7, §9.1)."""

    async def put(self, *, tenant_id: str, payload: Mapping[str, Any]) -> StoredPayload:
        """Cất payload và cho biết nó nằm ở đâu.

        Không bao giờ ném lỗi lưu trữ: hỏng thì trả về `location=ABSENT`. Ghi
        trace là quan sát, không được làm hỏng thao tác đang được quan sát.
        """

    async def get(self, *, tenant_id: str, stored: StoredPayload) -> Mapping[str, Any] | None:
        """Nội dung đã cất, hoặc `None` nếu không còn ở đó."""


class InvocationContext(Protocol):
    """Tenant của lời gọi, đã phân giải từ token và membership"""

    @property
    def user_id(self) -> str: ...

    @property
    def organization_id(self) -> str: ...

    @property
    def workspace_id(self) -> str: ...

    @property
    def roles(self) -> frozenset[str]: ...


class TenantGuard(Protocol):
    async def require_workspace_context(
        self, *, user_id: str, organization_id: str | None, workspace_id: str | None
    ) -> InvocationContext: ...
