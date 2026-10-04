"""Activity của executor. Mọi I/O của một lần chạy workflow nằm ở đây."""

from __future__ import annotations

from typing import Any

from temporalio import activity

from app.core.db import session_scope
from app.core.errors import WorkflowExecutionError
from app.domains.workflow.application.ports import ExecutionRecorder, NodeRef, ToolCatalogReader
from app.domains.workflow.application.runtime import LoadedPlan, PlanLoader
from app.domains.workflow.infrastructure.repositories import SqlAlchemyWorkflowRepository
from app.domains.workflow.infrastructure.temporal.errors import as_temporal_errors
from app.domains.workflow.infrastructure.temporal.messages import (
    LOAD_PLAN,
    RECORD_EXECUTION,
    RECORD_NODE,
    RUN_TOOL_NODE,
    SEED_NODES,
    LoadPlanInput,
    NodeRefMessage,
    RecordExecutionInput,
    RecordNodeInput,
    RunToolInput,
    SeedNodesInput,
)

_RUNNING = "running"


def _ref(node: NodeRefMessage) -> NodeRef:
    return NodeRef(node_path=node.node_path, node_id=node.node_id, node_type=node.node_type)


class ExecutorActivities:
    """Activity cần dữ liệu và bản ghi — chạy ở queue `data-processing`."""

    def __init__(self, *, recorder: ExecutionRecorder, catalog: ToolCatalogReader) -> None:
        self._recorder = recorder
        self._catalog = catalog

    @activity.defn(name=LOAD_PLAN)
    async def load_plan(self, request: LoadPlanInput) -> LoadedPlan:
        with as_temporal_errors():
            async with session_scope() as session:
                loader = PlanLoader(
                    repository=SqlAlchemyWorkflowRepository(session), catalog=self._catalog
                )
                return await loader.load(
                    tenant_id=request.tenant_id,
                    workflow_version_id=request.workflow_version_id,
                )

    @activity.defn(name=SEED_NODES)
    async def seed_nodes(self, request: SeedNodesInput) -> None:
        with as_temporal_errors():
            await self._recorder.seed_nodes(
                tenant_id=request.tenant_id,
                execution_id=request.execution_id,
                nodes=[_ref(node) for node in request.nodes],
                at=request.at,
            )

    @activity.defn(name=RECORD_NODE)
    async def record_node(self, request: RecordNodeInput) -> None:
        with as_temporal_errors():
            await self._recorder.record_node(
                tenant_id=request.tenant_id,
                execution_id=request.execution_id,
                node=_ref(request.node),
                status=request.status,
                at=request.at,
                attempt=request.attempt,
                error_category=request.error_category,
                error_message=request.error_message,
            )

    @activity.defn(name=RECORD_EXECUTION)
    async def record_execution(self, request: RecordExecutionInput) -> None:
        with as_temporal_errors():
            if request.status == _RUNNING:
                await self._recorder.mark_running(
                    tenant_id=request.tenant_id, execution_id=request.execution_id, at=request.at
                )
                return
            await self._recorder.finish(
                tenant_id=request.tenant_id,
                execution_id=request.execution_id,
                status=request.status,
                at=request.at,
                error_category=request.error_category,
                error_message=request.error_message,
            )


@activity.defn(name=RUN_TOOL_NODE)
async def run_tool_node(request: RunToolInput) -> dict[str, Any]:
    """Chỗ giữ cho GĐ 5, khi worker gọi Integration Gateway qua HTTP."""
    with as_temporal_errors():
        raise WorkflowExecutionError(
            f"Node tool chưa chạy được ở bản này ({request.tool_id}).",
            details={"node_path": request.node_path, "tool_id": request.tool_id},
        )
