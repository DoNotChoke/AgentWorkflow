"""Interface mà tầng `infrastructure` phải hiện thực hoá."""

from __future__ import annotations

from typing import Protocol

from app.domains.trace.domain.entities import ExecutionTrace, TraceStep


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

    async def list_traces_for_conversation(
        self, *, tenant_id: str, conversation_id: str
    ) -> list[ExecutionTrace]:
        """Mọi trace của một cuộc hội thoại, mới nhất trước."""


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
