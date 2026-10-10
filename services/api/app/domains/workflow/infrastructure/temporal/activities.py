"""Activity của executor. Mọi I/O của một lần chạy workflow nằm ở đây."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Awaitable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import jsonschema
from temporalio import activity

from app.core.db import session_scope
from app.core.errors import AppError, ErrorCategory, WorkflowExecutionError
from app.core.ids import ResourceKind, new_id
from app.core.logging import get_logger
from app.domains.workflow.application.llm_node import run_llm_node
from app.domains.workflow.application.ports import (
    ExecutionRecorder,
    NodeModel,
    NodeRef,
    RunTrace,
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

log = get_logger(__name__)

_RUNNING = "running"
#: Tool dựng sẵn mà Agent gọi để chạy code; node `transform` ghi vào trace dưới cùng tên.
_TRANSFORM_TOOL = "system.transform"


@dataclass(slots=True)
class _Step:
    """Bước trace của một lần thử node. `output` và `attributes` được điền trong lúc chạy."""

    id: str
    #: `None` khi lần chạy không có trace: node vẫn chạy, chỉ không để lại gì.
    trace_id: str | None
    attributes: dict[str, Any]
    output: Mapping[str, Any] | None = None


def _decision_id(execution_id: str, node_path: str) -> str:
    """Id ổn định: activity ghi trạng thái chạy lại thì bước `decision` không nhân đôi."""
    digest = hashlib.sha256(f"{execution_id}:{node_path}".encode()).hexdigest()[:32]
    return f"{ResourceKind.TRACE_STEP}_{digest}"


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
        trace: RunTrace,
        llm_max_attempts: int = 3,
    ) -> None:
        self._recorder = recorder
        self._catalog = catalog
        self._runner = runner
        self._models = models
        self._tools = tools
        self._trace = trace
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
        if request.detail is not None:
            await self._decision(request, request.detail)

    @activity.defn(name=RUN_TOOL_NODE)
    async def run_tool_node(self, request: RunToolInput) -> dict[str, Any]:
        """Chạy ở queue `integration-read` hoặc `integration-write`, theo side effect của tool."""
        attributes = {
            "node_path": request.node_path,
            "node_id": request.node_id,
            "node_type": "tool",
            "tool_id": request.tool_id,
            "tool_version": request.tool_version,
        }
        with as_temporal_errors():
            async with self._traced(request, "tool_call", attributes, request.input) as step:
                output = await self._tools.invoke(
                    execution_id=request.execution_id,
                    node_id=request.node_id,
                    tool_id=request.tool_id,
                    tool_version=request.tool_version,
                    tool_input=request.input,
                    idempotency_key=request.idempotency_key,
                    trace_id=step.trace_id,
                    parent_step_id=step.id if step.trace_id else None,
                )
                step.output = output
            return dict(output)

    @activity.defn(name=RUN_TRANSFORM_NODE)
    async def run_transform_node(self, request: RunTransformInput) -> dict[str, Any]:
        """Kết quả bọc trong `value`: code có thể trả về số, chuỗi hay mảng."""
        attributes = {
            "node_path": request.node_path,
            "node_type": "transform",
            "tool_id": _TRANSFORM_TOOL,
        }
        sent = {"code": request.code, "args": request.args}
        with as_temporal_errors():
            async with self._traced(request, "tool_call", attributes, sent) as step:
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
                step.output = {"value": value}
            return {"value": value}

    @activity.defn(name=RUN_LLM_NODE)
    async def run_llm_node(self, request: RunLlmInput) -> LlmNodeResult:
        attributes = {
            "node_path": request.node_path,
            "node_type": "llm",
            "model": request.model,
        }
        sent = {"instructions": request.instructions, "input": request.input}
        with as_temporal_errors():
            async with self._traced(request, "model_call", attributes, sent) as step:
                outcome = await run_llm_node(
                    self._models,
                    model=request.model,
                    instructions=request.instructions,
                    data=request.input,
                    output_schema=request.output_schema,
                    max_attempts=self._llm_max_attempts,
                )
                step.output = outcome.output
                step.attributes.update(
                    input_tokens=outcome.input_tokens,
                    output_tokens=outcome.output_tokens,
                    model_calls=outcome.attempts,
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
                await self._observed(
                    self._trace.open(
                        tenant_id=request.tenant_id,
                        execution_id=request.execution_id,
                        at=request.at,
                    )
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
            await self._observed(
                self._trace.close(
                    tenant_id=request.tenant_id,
                    execution_id=request.execution_id,
                    status=request.status,
                    at=request.at,
                    error_category=request.error_category,
                )
            )

    # ─── Execution Trace ─────────────────────────────────────────────────────

    async def _observed[T](self, work: Awaitable[T]) -> T | None:
        """Ghi trace là quan sát: hỏng thì node và lần chạy vẫn đi tiếp."""
        try:
            return await work
        except Exception:  # noqa: BLE001
            log.warning("workflow_trace_khong_ghi_duoc", exc_info=True)
            return None

    @asynccontextmanager
    async def _traced(
        self,
        request: RunToolInput | RunTransformInput | RunLlmInput,
        kind: str,
        attributes: dict[str, Any],
        sent: Mapping[str, Any],
    ) -> AsyncIterator[_Step]:
        """Một lần thử của node thành một bước trace, kể cả khi nó hỏng."""
        started_at = datetime.now(UTC)
        step = _Step(
            id=new_id(ResourceKind.TRACE_STEP),
            trace_id=await self._observed(
                self._trace.find(tenant_id=request.tenant_id, execution_id=request.execution_id)
            ),
            attributes={**attributes, "attempt": activity.info().attempt},
        )
        failed: str | None = None
        try:
            yield step
        except Exception as error:
            failed = str(error.category if isinstance(error, AppError) else ErrorCategory.INTERNAL)
            raise
        finally:
            if step.trace_id is not None:
                await self._observed(
                    self._trace.step(
                        tenant_id=request.tenant_id,
                        trace_id=step.trace_id,
                        kind=kind,
                        status="failed" if failed else "succeeded",
                        started_at=started_at,
                        completed_at=datetime.now(UTC),
                        attributes=step.attributes,
                        input_payload=sent,
                        output_payload=step.output,
                        error_category=failed,
                        step_id=step.id,
                    )
                )

    async def _decision(self, request: RecordNodeInput, detail: Mapping[str, Any]) -> None:
        trace_id = await self._observed(
            self._trace.find(tenant_id=request.tenant_id, execution_id=request.execution_id)
        )
        if trace_id is None:
            return
        await self._observed(
            self._trace.step(
                tenant_id=request.tenant_id,
                trace_id=trace_id,
                kind="decision",
                status="succeeded",
                started_at=request.at,
                completed_at=request.at,
                attributes={
                    "node_path": request.node.node_path,
                    "node_id": request.node.node_id,
                    "node_type": request.node.node_type,
                },
                output_payload=detail,
                step_id=_decision_id(request.execution_id, request.node.node_path),
            )
        )
