"""Cổng mà tầng application của Execution cần hạ tầng hiện thực."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

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


@dataclass(frozen=True, slots=True)
class RunnableVersion:
    """Version sẵn sàng chạy, nhìn từ domain `execution`."""

    workflow_id: str
    workflow_version_id: str
    number: int
    #: Node tool → connection đã chọn cho lần chạy này.
    connections: Mapping[str, str]


class WorkflowRunSource(Protocol):
    """Kiểm một lần chạy trước khi nó tồn tại. Hiện thực ở `entrypoints/wiring.py`."""

    async def prepare(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        workflow_id: str,
        version: int | None,
        inputs: Mapping[str, Any],
        by_node: Mapping[str, str],
        by_provider: Mapping[str, str],
    ) -> RunnableVersion: ...


class ExecutionLauncher(Protocol):
    """Khởi động một lần chạy trên runtime. Hiện thực ở `entrypoints/wiring.py`."""

    async def start(
        self,
        *,
        execution_id: str,
        tenant_id: str,
        workflow_version_id: str,
        inputs: Mapping[str, Any],
        connections: Mapping[str, str],
    ) -> None:
        """Không khởi động được thì ném `ProviderUnavailableError`."""


class InvocationContext(Protocol):
    """Tenant của người gọi, đã phân giải từ token và membership."""

    @property
    def user_id(self) -> str: ...

    @property
    def organization_id(self) -> str: ...

    @property
    def workspace_id(self) -> str: ...

    @property
    def roles(self) -> frozenset[str]: ...


class TenantGuard(Protocol):
    """Cùng hình dạng với Protocol tên này ở các domain khác; một adapter ở `wiring` thoả cả hai."""

    async def require_workspace_context(
        self, *, user_id: str, organization_id: str | None, workspace_id: str | None
    ) -> InvocationContext: ...
