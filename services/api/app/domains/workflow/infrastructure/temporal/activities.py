"""Activity của executor. Mọi I/O của một lần chạy workflow nằm ở đây."""

from __future__ import annotations

import asyncio
from typing import Any

import jsonschema
from temporalio import activity

from app.core.db import session_scope
from app.core.errors import WorkflowExecutionError
from app.domains.workflow.application.llm_node import run_llm_node
from app.domains.workflow.application.ports import (
    ExecutionRecorder,
    NodeModel,
    NodeRef,
    ToolCatalogReader,
    ToolInvoker,
    TransformRunner,
)
from app.domains.workflow.application.runtime import LoadedPlan, PlanLoader
from app.domains.workflow.infrastructure.repositories import SqlAlchemyWorkflowRepository
from app.domains.workflow.infrastructure.temporal.errors import as_temporal_errors
from app.domains.workflow.infrastructure.temporal.messages import (
    LOAD_PLAN,
    RECORD_EXECUTION,
    RECORD_NODE,
    RUN_LLM_NODE,
    RUN_TOOL_NODE,
    RUN_TRANSFORM_NODE,
    SEED_NODES,
    LlmNodeResult,
    LoadPlanInput,
    NodeRefMessage,
    RecordExecutionInput,
    RecordNodeInput,
    RunLlmInput,
    RunToolInput,
    RunTransformInput,
    SeedNodesInput,
)

_RUNNING = "running"


def _ref(node: NodeRefMessage) -> NodeRef:
    return NodeRef(node_path=node.node_path, node_id=node.node_id, node_type=node.node_type)


class ExecutorActivities:
    """Activity cần dữ liệu và bản ghi — chạy ở queue `data-processing`. Riêng
    `run_llm_node` chạy ở queue `ai-tasks`, `run_tool_node` ở hai queue integration."""

    def __init__(
        self,
        *,
        recorder: ExecutionRecorder,
        catalog: ToolCatalogReader,
        runner: TransformRunner,
        models: NodeModel,
        tools: ToolInvoker,
        llm_max_attempts: int = 3,
    ) -> None:
        self._recorder = recorder
        self._catalog = catalog
        self._runner = runner
        self._models = models
        self._tools = tools
        self._llm_max_attempts = llm_max_attempts

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
                usage=request.usage,
            )

    @activity.defn(name=RUN_TOOL_NODE)
    async def run_tool_node(self, request: RunToolInput) -> dict[str, Any]:
        """Chạy ở queue `integration-read` hoặc `integration-write`, theo side effect của tool."""
        with as_temporal_errors():
            output = await self._tools.invoke(
                execution_id=request.execution_id,
                node_id=request.node_id,
                tool_id=request.tool_id,
                tool_version=request.tool_version,
                tool_input=request.input,
                idempotency_key=request.idempotency_key,
            )
            return dict(output)

    @activity.defn(name=RUN_TRANSFORM_NODE)
    async def run_transform_node(self, request: RunTransformInput) -> dict[str, Any]:
        """Kết quả bọc trong `value`: code có thể trả về số, chuỗi hay mảng."""
        with as_temporal_errors():
            # Sandbox chạy code trong một tiến trình con và chờ nó.
            value = await asyncio.to_thread(self._runner.run, request.code, request.args)
            error = jsonschema.exceptions.best_match(
                jsonschema.Draft202012Validator(request.output_schema).iter_errors(value)
            )
            if error is not None:
                raise WorkflowExecutionError(
                    f"Code của {request.node_path} trả về kết quả không khớp kiểu đã khai: "
                    f"{error.message}",
                    details={"node_path": request.node_path},
                )
            return {"value": value}

    @activity.defn(name=RUN_LLM_NODE)
    async def run_llm_node(self, request: RunLlmInput) -> LlmNodeResult:
        with as_temporal_errors():
            outcome = await run_llm_node(
                self._models,
                model=request.model,
                instructions=request.instructions,
                data=request.input,
                output_schema=request.output_schema,
                max_attempts=self._llm_max_attempts,
            )
            return LlmNodeResult(
                output=outcome.output,
                input_tokens=outcome.input_tokens,
                output_tokens=outcome.output_tokens,
                attempts=outcome.attempts,
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
