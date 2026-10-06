"""Hiện thực hoá repository của domain Execution trên PostgreSQL."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import ColumnElement, Integer, cast, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ErrorCategory
from app.domains.execution.domain.entities import (
    Execution,
    ExecutionStatus,
    Failure,
    NodeExecution,
    NodeStatus,
    Tally,
)
from app.domains.execution.infrastructure.models import ExecutionModel, NodeExecutionModel


def _failure(category: str | None, message: str | None) -> Failure | None:
    if category is None:
        return None
    return Failure(category=ErrorCategory(category), message=message or "")


def _to_execution(row: ExecutionModel) -> Execution:
    return Execution(
        id=row.id,
        tenant_id=row.tenant_id,
        workspace_id=row.workspace_id,
        workflow_id=row.workflow_id,
        workflow_version_id=row.workflow_version_id,
        trigger_type=row.trigger_type,
        inputs=row.inputs,
        initiated_by=row.initiated_by,
        status=ExecutionStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        failure=_failure(row.error_category, row.error_message),
        connection_bindings=dict(row.connection_bindings),
    )


def _tokens(key: str) -> ColumnElement[int]:
    return cast(NodeExecutionModel.usage[key].astext, Integer)


def _to_node(row: NodeExecutionModel) -> NodeExecution:
    return NodeExecution(
        id=row.id,
        execution_id=row.execution_id,
        tenant_id=row.tenant_id,
        node_path=row.node_path,
        node_id=row.node_id,
        node_type=row.node_type,
        status=NodeStatus(row.status),
        updated_at=row.updated_at,
        attempt=row.attempt,
        started_at=row.started_at,
        finished_at=row.finished_at,
        failure=_failure(row.error_category, row.error_message),
        usage=row.usage,
    )


def _node_values(node: NodeExecution) -> dict[str, object]:
    return {
        "id": node.id,
        "execution_id": node.execution_id,
        "tenant_id": node.tenant_id,
        "node_path": node.node_path,
        "node_id": node.node_id,
        "node_type": node.node_type,
        "status": str(node.status),
        "attempt": node.attempt,
        "error_category": None if node.failure is None else str(node.failure.category),
        "error_message": None if node.failure is None else node.failure.message,
        "usage": None if node.usage is None else dict(node.usage),
        "updated_at": node.updated_at,
        "started_at": node.started_at,
        "finished_at": node.finished_at,
    }


class SqlAlchemyExecutionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ─── Execution ───────────────────────────────────────────────────────────

    async def add(self, execution: Execution) -> None:
        self._session.add(
            ExecutionModel(
                id=execution.id,
                tenant_id=execution.tenant_id,
                workspace_id=execution.workspace_id,
                workflow_id=execution.workflow_id,
                workflow_version_id=execution.workflow_version_id,
                trigger_type=execution.trigger_type,
                inputs=dict(execution.inputs),
                initiated_by=execution.initiated_by,
                connection_bindings=dict(execution.connection_bindings),
                status=str(execution.status),
                error_category=None,
                error_message=None,
                created_at=execution.created_at,
                updated_at=execution.updated_at,
                started_at=execution.started_at,
                finished_at=execution.finished_at,
            )
        )
        await self._session.flush()

    async def get(
        self, *, tenant_id: str, execution_id: str, for_update: bool = False
    ) -> Execution | None:
        statement = select(ExecutionModel).where(
            ExecutionModel.tenant_id == tenant_id, ExecutionModel.id == execution_id
        )
        if for_update:
            statement = statement.with_for_update()
        row = await self._session.scalar(statement)
        return None if row is None else _to_execution(row)

    async def find(self, execution_id: str) -> Execution | None:
        """Không lọc theo tenant: người gọi là Gateway đang phân giải một token của lần
        chạy, và tenant chính là thứ nó đọc ra từ hàng này (ADR-0010)."""
        row = await self._session.scalar(
            select(ExecutionModel).where(ExecutionModel.id == execution_id)
        )
        return None if row is None else _to_execution(row)

    async def update(self, execution: Execution) -> None:
        failure = execution.failure
        await self._session.execute(
            update(ExecutionModel)
            .where(
                ExecutionModel.tenant_id == execution.tenant_id,
                ExecutionModel.id == execution.id,
            )
            .values(
                status=str(execution.status),
                error_category=None if failure is None else str(failure.category),
                error_message=None if failure is None else failure.message,
                updated_at=execution.updated_at,
                started_at=execution.started_at,
                finished_at=execution.finished_at,
            )
        )
        await self._session.flush()

    async def list_recent(
        self,
        *,
        tenant_id: str,
        limit: int,
        workspace_id: str | None = None,
        workflow_id: str | None = None,
        status: str | None = None,
    ) -> list[Execution]:
        query = select(ExecutionModel).where(ExecutionModel.tenant_id == tenant_id)
        if workspace_id is not None:
            query = query.where(ExecutionModel.workspace_id == workspace_id)
        if workflow_id is not None:
            query = query.where(ExecutionModel.workflow_id == workflow_id)
        if status is not None:
            query = query.where(ExecutionModel.status == status)
        rows = await self._session.scalars(
            query.order_by(ExecutionModel.created_at.desc(), ExecutionModel.id.desc()).limit(limit)
        )
        return [_to_execution(row) for row in rows]

    async def tally(self, *, tenant_id: str, execution_ids: Sequence[str]) -> dict[str, Tally]:
        if not execution_ids:
            return {}
        done = NodeExecutionModel.status.in_((NodeStatus.SUCCEEDED, NodeStatus.SKIPPED))
        rows = await self._session.execute(
            select(
                NodeExecutionModel.execution_id,
                func.count(),
                func.count().filter(done),
                func.coalesce(func.sum(_tokens("input_tokens")), 0),
                func.coalesce(func.sum(_tokens("output_tokens")), 0),
            )
            .where(
                NodeExecutionModel.tenant_id == tenant_id,
                NodeExecutionModel.execution_id.in_(execution_ids),
            )
            .group_by(NodeExecutionModel.execution_id)
        )
        return {
            execution_id: Tally(
                steps=steps,
                done=finished,
                input_tokens=int(tokens_in),
                output_tokens=int(tokens_out),
            )
            for execution_id, steps, finished, tokens_in, tokens_out in rows
        }

    # ─── Node ────────────────────────────────────────────────────────────────

    async def seed_nodes(self, nodes: Sequence[NodeExecution]) -> None:
        if not nodes:
            return
        await self._session.execute(
            pg_insert(NodeExecutionModel)
            .values([_node_values(node) for node in nodes])
            .on_conflict_do_nothing(constraint="uq_node_executions_path")
        )
        await self._session.flush()

    async def get_node(
        self, *, tenant_id: str, execution_id: str, node_path: str, for_update: bool = False
    ) -> NodeExecution | None:
        statement = select(NodeExecutionModel).where(
            NodeExecutionModel.tenant_id == tenant_id,
            NodeExecutionModel.execution_id == execution_id,
            NodeExecutionModel.node_path == node_path,
        )
        if for_update:
            statement = statement.with_for_update()
        row = await self._session.scalar(statement)
        return None if row is None else _to_node(row)

    async def save_node(self, node: NodeExecution) -> None:
        values = _node_values(node)
        statement = pg_insert(NodeExecutionModel).values(values)
        changed = {
            key: statement.excluded[key]
            for key in values
            if key not in {"id", "execution_id", "tenant_id", "node_path"}
        }
        await self._session.execute(
            statement.on_conflict_do_update(constraint="uq_node_executions_path", set_=changed)
        )
        await self._session.flush()

    async def list_nodes(self, *, tenant_id: str, execution_id: str) -> list[NodeExecution]:
        rows = await self._session.scalars(
            select(NodeExecutionModel)
            .where(
                NodeExecutionModel.tenant_id == tenant_id,
                NodeExecutionModel.execution_id == execution_id,
            )
            .order_by(NodeExecutionModel.seq)
        )
        return [_to_node(row) for row in rows]
