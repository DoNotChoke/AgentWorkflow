"""Bản ghi của các lần chạy workflow: tạo, ghi trạng thái, đọc tiến trình."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from app.core.errors import AuthorizationError, ErrorCategory, WorkflowExecutionError
from app.core.ids import ResourceKind, new_id
from app.core.logging import get_logger
from app.domains.execution.application.ports import ExecutionRepository
from app.domains.execution.domain.entities import (
    Execution,
    ExecutionStatus,
    Failure,
    NodeExecution,
    NodeStatus,
    Tally,
    ensure_inputs_size,
    move_execution,
    move_node,
)

log = get_logger(__name__)

_NOT_FOUND = "Không tìm thấy lần chạy này."

DEFAULT_LIST_LIMIT: Final = 50
MAX_LIST_LIMIT: Final = 200


@dataclass(frozen=True, slots=True)
class NodeSeed:
    node_path: str
    node_id: str
    node_type: str


@dataclass(frozen=True, slots=True)
class ExecutionProgress:
    execution: Execution
    nodes: tuple[NodeExecution, ...]


@dataclass(frozen=True, slots=True)
class ExecutionSummary:
    execution: Execution
    tally: Tally


class ExecutionService:
    def __init__(self, *, repository: ExecutionRepository) -> None:
        self._executions = repository

    # ─── Đọc ─────────────────────────────────────────────────────────────────

    async def get(
        self, *, tenant_id: str, execution_id: str, workspace_id: str | None = None
    ) -> Execution:
        """`workspace_id` có thì lần chạy của workspace khác trông như không tồn tại."""
        found = await self._executions.get(tenant_id=tenant_id, execution_id=execution_id)
        if found is None or (workspace_id is not None and found.workspace_id != workspace_id):
            raise AuthorizationError(_NOT_FOUND, details={"execution_id": execution_id})
        return found

    async def get_progress(
        self, *, tenant_id: str, execution_id: str, workspace_id: str | None = None
    ) -> ExecutionProgress:
        execution = await self.get(
            tenant_id=tenant_id, execution_id=execution_id, workspace_id=workspace_id
        )
        nodes = await self._executions.list_nodes(tenant_id=tenant_id, execution_id=execution_id)
        return ExecutionProgress(execution=execution, nodes=tuple(nodes))

    async def list_recent(
        self,
        *,
        tenant_id: str,
        limit: int = DEFAULT_LIST_LIMIT,
        workspace_id: str | None = None,
        workflow_id: str | None = None,
        status: ExecutionStatus | None = None,
    ) -> list[ExecutionSummary]:
        found = await self._executions.list_recent(
            tenant_id=tenant_id,
            limit=max(1, min(limit, MAX_LIST_LIMIT)),
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            status=None if status is None else str(status),
        )
        tallies = await self._executions.tally(
            tenant_id=tenant_id, execution_ids=[execution.id for execution in found]
        )
        return [
            ExecutionSummary(execution=execution, tally=tallies.get(execution.id, Tally()))
            for execution in found
        ]

    # ─── Ghi ─────────────────────────────────────────────────────────────────

    async def create(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        workflow_id: str,
        workflow_version_id: str,
        trigger_type: str,
        inputs: Mapping[str, Any],
        initiated_by: str,
        at: datetime,
        connection_bindings: Mapping[str, str] | None = None,
    ) -> Execution:
        ensure_inputs_size(inputs)
        execution = Execution(
            id=new_id(ResourceKind.EXECUTION),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            workflow_version_id=workflow_version_id,
            trigger_type=trigger_type,
            inputs=dict(inputs),
            initiated_by=initiated_by,
            status=ExecutionStatus.PENDING,
            created_at=at,
            updated_at=at,
            connection_bindings=dict(connection_bindings or {}),
        )
        await self._executions.add(execution)
        log.info("execution_created", execution_id=execution.id, workflow_id=workflow_id)
        return execution

    async def mark_running(self, *, tenant_id: str, execution_id: str, at: datetime) -> Execution:
        return await self._move(tenant_id, execution_id, ExecutionStatus.RUNNING, at, None)

    async def finish(
        self,
        *,
        tenant_id: str,
        execution_id: str,
        status: ExecutionStatus,
        at: datetime,
        failure: Failure | None = None,
    ) -> Execution:
        return await self._move(tenant_id, execution_id, status, at, failure)

    async def seed_nodes(
        self, *, tenant_id: str, execution_id: str, nodes: Sequence[NodeSeed], at: datetime
    ) -> None:
        """Hàng `pending` cho những node sắp chạy, để tiến trình thấy cả phần chưa tới."""
        await self._open(tenant_id, execution_id)
        await self._executions.seed_nodes(
            [
                NodeExecution(
                    id=new_id(ResourceKind.NODE_EXECUTION),
                    execution_id=execution_id,
                    tenant_id=tenant_id,
                    node_path=seed.node_path,
                    node_id=seed.node_id,
                    node_type=seed.node_type,
                    status=NodeStatus.PENDING,
                    updated_at=at,
                )
                for seed in nodes
            ]
        )

    async def record_node(
        self,
        *,
        tenant_id: str,
        execution_id: str,
        node: NodeSeed,
        status: NodeStatus,
        at: datetime,
        attempt: int | None = None,
        failure: Failure | None = None,
        usage: Mapping[str, int] | None = None,
    ) -> NodeExecution:
        await self._open(tenant_id, execution_id)
        current = await self._executions.get_node(
            tenant_id=tenant_id,
            execution_id=execution_id,
            node_path=node.node_path,
            for_update=True,
        )
        is_new = current is None
        if current is None:
            current = NodeExecution(
                id=new_id(ResourceKind.NODE_EXECUTION),
                execution_id=execution_id,
                tenant_id=tenant_id,
                node_path=node.node_path,
                node_id=node.node_id,
                node_type=node.node_type,
                status=NodeStatus.PENDING,
                updated_at=at,
            )
        moved = move_node(current, status, at=at, attempt=attempt, failure=failure, usage=usage)
        if is_new or moved is not current:
            await self._executions.save_node(moved)
        return moved

    # ─── Cửa nguyên thuỷ cho `entrypoints/worker_wiring.py` (contract 5) ─────

    async def record_node_state(
        self,
        *,
        tenant_id: str,
        execution_id: str,
        node: NodeSeed,
        status: str,
        at: datetime,
        attempt: int | None = None,
        error_category: str | None = None,
        error_message: str | None = None,
        usage: Mapping[str, int] | None = None,
    ) -> None:
        await self.record_node(
            tenant_id=tenant_id,
            execution_id=execution_id,
            node=node,
            status=_parse(NodeStatus, status),
            at=at,
            attempt=attempt,
            failure=_failure(error_category, error_message),
            usage=usage,
        )

    async def finish_as(
        self,
        *,
        tenant_id: str,
        execution_id: str,
        status: str,
        at: datetime,
        error_category: str | None = None,
        error_message: str | None = None,
    ) -> None:
        await self.finish(
            tenant_id=tenant_id,
            execution_id=execution_id,
            status=_parse(ExecutionStatus, status),
            at=at,
            failure=_failure(error_category, error_message),
        )

    async def _move(
        self,
        tenant_id: str,
        execution_id: str,
        to: ExecutionStatus,
        at: datetime,
        failure: Failure | None,
    ) -> Execution:
        current = await self._executions.get(
            tenant_id=tenant_id, execution_id=execution_id, for_update=True
        )
        if current is None:
            raise AuthorizationError(_NOT_FOUND, details={"execution_id": execution_id})
        moved = move_execution(current, to, at=at, failure=failure)
        if moved is not current:
            await self._executions.update(moved)
            log.info("execution_moved", execution_id=execution_id, status=str(to))
        return moved

    async def _open(self, tenant_id: str, execution_id: str) -> Execution:
        """Execution đã kết thúc thì không nhận thêm trạng thái node."""
        execution = await self.get(tenant_id=tenant_id, execution_id=execution_id)
        if execution.is_terminal:
            raise WorkflowExecutionError(
                f"Execution {execution_id} đã kết thúc ({execution.status}).",
                details={"execution_id": execution_id, "status": str(execution.status)},
            )
        return execution


def _parse[S: StrEnum](kind: type[S], value: str) -> S:
    try:
        return kind(value)
    except ValueError:
        raise WorkflowExecutionError(
            f"{value!r} không phải {kind.__name__}.", details={"value": value}
        ) from None


def _failure(category: str | None, message: str | None) -> Failure | None:
    if category is None:
        return None
    return Failure(category=_parse(ErrorCategory, category), message=message or "")
