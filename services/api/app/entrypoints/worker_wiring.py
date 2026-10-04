"""Nối worker (domain `workflow`) với domain `execution` và catalog tool."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from app.core.db import session_scope
from app.domains.execution.application.service import ExecutionService, NodeSeed
from app.domains.execution.infrastructure.repositories import SqlAlchemyExecutionRepository
from app.domains.integration.infrastructure.catalog_view import tool_rows
from app.domains.workflow.application.ports import NodeRef
from app.domains.workflow.infrastructure.catalog import RowToolCatalog
from app.domains.workflow.infrastructure.temporal.activities import ExecutorActivities


def _seed(node: NodeRef) -> NodeSeed:
    return NodeSeed(node_path=node.node_path, node_id=node.node_id, node_type=node.node_type)


class ExecutionServiceRecorder:
    """`ExecutionRecorder` của domain `workflow`. Mỗi lần ghi một transaction riêng."""

    async def mark_running(self, *, tenant_id: str, execution_id: str, at: datetime) -> None:
        async with session_scope() as session:
            service = ExecutionService(repository=SqlAlchemyExecutionRepository(session))
            await service.mark_running(tenant_id=tenant_id, execution_id=execution_id, at=at)

    async def seed_nodes(
        self, *, tenant_id: str, execution_id: str, nodes: Sequence[NodeRef], at: datetime
    ) -> None:
        async with session_scope() as session:
            service = ExecutionService(repository=SqlAlchemyExecutionRepository(session))
            await service.seed_nodes(
                tenant_id=tenant_id,
                execution_id=execution_id,
                nodes=[_seed(node) for node in nodes],
                at=at,
            )

    async def record_node(
        self,
        *,
        tenant_id: str,
        execution_id: str,
        node: NodeRef,
        status: str,
        at: datetime,
        attempt: int | None = None,
        error_category: str | None = None,
        error_message: str | None = None,
    ) -> None:
        async with session_scope() as session:
            service = ExecutionService(repository=SqlAlchemyExecutionRepository(session))
            await service.record_node_state(
                tenant_id=tenant_id,
                execution_id=execution_id,
                node=_seed(node),
                status=status,
                at=at,
                attempt=attempt,
                error_category=error_category,
                error_message=error_message,
            )

    async def finish(
        self,
        *,
        tenant_id: str,
        execution_id: str,
        status: str,
        at: datetime,
        error_category: str | None = None,
        error_message: str | None = None,
    ) -> None:
        async with session_scope() as session:
            service = ExecutionService(repository=SqlAlchemyExecutionRepository(session))
            await service.finish_as(
                tenant_id=tenant_id,
                execution_id=execution_id,
                status=status,
                at=at,
                error_category=error_category,
                error_message=error_message,
            )


def build_executor_activities() -> ExecutorActivities:
    return ExecutorActivities(
        recorder=ExecutionServiceRecorder(), catalog=RowToolCatalog(tool_rows())
    )
