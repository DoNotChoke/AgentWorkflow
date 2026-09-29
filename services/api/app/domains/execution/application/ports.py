"""Cổng mà tầng application của Execution cần hạ tầng hiện thực."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.domains.execution.domain.entities import Execution, NodeExecution


class ExecutionRepository(Protocol):
    async def add(self, execution: Execution) -> None: ...

    async def get(
        self, *, tenant_id: str, execution_id: str, for_update: bool = False
    ) -> Execution | None:
        """`for_update` khoá hàng tới hết transaction — cho đọc-sửa-ghi."""

    async def update(self, execution: Execution) -> None: ...

    async def list_for_workflow(
        self, *, tenant_id: str, workflow_id: str, limit: int
    ) -> list[Execution]:
        """Mới nhất trước."""

    async def seed_nodes(self, nodes: Sequence[NodeExecution]) -> None:
        """Chèn hàng `pending`; `node_path` đã có thì để nguyên hàng cũ."""

    async def get_node(
        self, *, tenant_id: str, execution_id: str, node_path: str, for_update: bool = False
    ) -> NodeExecution | None: ...

    async def save_node(self, node: NodeExecution) -> None:
        """Ghi đè theo `(execution_id, node_path)`, chèn nếu chưa có."""

    async def list_nodes(self, *, tenant_id: str, execution_id: str) -> list[NodeExecution]:
        """Theo thứ tự chèn."""
