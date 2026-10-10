"""Nối worker (domain `workflow`) với domain `execution` và `trace`, catalog tool, sandbox
và Model Gateway (ADR-0011)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import session_scope
from app.domains.agent.application.ports import (
    ModelClient,
    ModelToolCall,
    ModelToolResult,
    ModelToolSpec,
    ModelTurn,
    StopReason,
)
from app.domains.agent.infrastructure.gateway_factory import build_model_gateway
from app.domains.execution.application.service import ExecutionService, NodeSeed
from app.domains.execution.infrastructure.repositories import SqlAlchemyExecutionRepository
from app.domains.integration.infrastructure.catalog_view import tool_rows
from app.domains.trace.application.service import TraceService
from app.domains.trace.infrastructure.payload_store import build_payload_store
from app.domains.trace.infrastructure.repositories import SqlAlchemyExecutionTraceRepository
from app.domains.workflow.application.llm_node import RESULT_TOOL
from app.domains.workflow.application.ports import (
    ModelAnswer,
    NodeModel,
    NodeRef,
    Rejected,
    RunTrace,
    ToolInvoker,
)
from app.domains.workflow.infrastructure.catalog import RowToolCatalog
from app.domains.workflow.infrastructure.temporal.activities import ExecutorActivities
from app.domains.workflow.infrastructure.temporal.tool_client import GatewayToolInvoker
from app.entrypoints.sandbox_runner import SandboxRunner


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
        usage: Mapping[str, int] | None = None,
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
                usage=usage,
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


def _trace_service(session: AsyncSession) -> TraceService:
    return TraceService(
        traces=SqlAlchemyExecutionTraceRepository(session),
        payloads=build_payload_store(get_settings()),
    )


class TraceServiceRunTrace:
    """`RunTrace` của domain `workflow`, bằng `TraceService`. Mỗi lần ghi một transaction
    riêng: một node hỏng không cuốn theo các bước đã ghi trước nó."""

    async def open(self, *, tenant_id: str, execution_id: str, at: datetime) -> None:
        async with session_scope() as session:
            execution = await ExecutionService(
                repository=SqlAlchemyExecutionRepository(session)
            ).get(tenant_id=tenant_id, execution_id=execution_id)
            await _trace_service(session).start_workflow_trace(
                tenant_id=tenant_id,
                workspace_id=execution.workspace_id,
                workflow_id=execution.workflow_id,
                workflow_version_id=execution.workflow_version_id,
                execution_id=execution_id,
                initiated_by=execution.initiated_by,
                at=at,
            )

    async def find(self, *, tenant_id: str, execution_id: str) -> str | None:
        async with session_scope() as session:
            trace = await _trace_service(session).find_for_execution(
                tenant_id=tenant_id, execution_id=execution_id
            )
        return None if trace is None or trace.is_terminal else trace.id

    async def step(
        self,
        *,
        tenant_id: str,
        trace_id: str,
        kind: str,
        status: str,
        started_at: datetime,
        completed_at: datetime,
        attributes: Mapping[str, Any],
        input_payload: Mapping[str, Any] | None = None,
        output_payload: Mapping[str, Any] | None = None,
        error_category: str | None = None,
        step_id: str | None = None,
    ) -> None:
        async with session_scope() as session:
            await _trace_service(session).record_step(
                tenant_id=tenant_id,
                trace_id=trace_id,
                kind=kind,
                status=status,
                started_at=started_at,
                completed_at=completed_at,
                attributes=attributes,
                error_category=error_category,
                input_payload=input_payload,
                output_payload=output_payload,
                step_id=step_id,
            )

    async def close(
        self,
        *,
        tenant_id: str,
        execution_id: str,
        status: str,
        at: datetime,
        error_category: str | None = None,
    ) -> None:
        async with session_scope() as session:
            service = _trace_service(session)
            trace = await service.find_for_execution(tenant_id=tenant_id, execution_id=execution_id)
            if trace is None:
                return
            await service.complete_by_id(
                tenant_id=tenant_id,
                trace_id=trace.id,
                status=status,
                at=at,
                error_category=error_category,
            )


class GatewayNodeModel:
    """`NodeModel` của domain `workflow`, bằng một `ModelClient`.

    Model nộp kết quả qua một tool mà `input_schema` chính là `output_schema` của node:
    kiểu của kết quả do provider ép một lần, rồi `run_llm_node` kiểm lại."""

    def __init__(self, models: ModelClient, *, max_tokens: int) -> None:
        self._models = models
        self._max_tokens = max_tokens

    async def answer(
        self,
        *,
        model: str,
        system: str,
        data: str,
        output_schema: Mapping[str, Any],
        rejected: Sequence[Rejected],
    ) -> ModelAnswer:
        turns = [ModelTurn(role="user", text=data)]
        for number, attempt in enumerate(rejected, start=1):
            if attempt.output is None:
                turns.append(ModelTurn(role="user", text=attempt.reason))
                continue
            call_id = f"lan_{number}"
            turns.append(
                ModelTurn(
                    role="assistant",
                    tool_calls=(
                        ModelToolCall(call_id=call_id, name=RESULT_TOOL, input=attempt.output),
                    ),
                )
            )
            turns.append(
                ModelTurn(
                    role="user",
                    tool_results=(
                        ModelToolResult(call_id=call_id, content=attempt.reason, is_error=True),
                    ),
                )
            )

        response = await self._models.complete(
            model=model,
            system=system,
            turns=turns,
            tools=(
                ModelToolSpec(
                    name=RESULT_TOOL,
                    description="Nộp kết quả của bước này.",
                    input_schema=output_schema,
                    version="1",
                ),
            ),
            max_tokens=self._max_tokens,
        )
        call = next((found for found in response.tool_calls if found.name == RESULT_TOOL), None)
        return ModelAnswer(
            output=None if call is None else call.input,
            text=response.text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            truncated=response.stop_reason is StopReason.MAX_TOKENS,
        )


def build_executor_activities(
    *,
    settings: Settings | None = None,
    models: NodeModel | None = None,
    tools: ToolInvoker | None = None,
    trace: RunTrace | None = None,
) -> ExecutorActivities:
    """`models`, `tools` và `trace` để test đưa bản giả vào; mặc định là Model Gateway,
    Integration Gateway và Execution Trace thật."""
    settings = settings or get_settings()
    return ExecutorActivities(
        recorder=ExecutionServiceRecorder(),
        catalog=RowToolCatalog(tool_rows()),
        runner=SandboxRunner(),
        models=models
        or GatewayNodeModel(
            build_model_gateway(settings), max_tokens=settings.workflow_llm_max_tokens
        ),
        tools=tools or GatewayToolInvoker(settings),
        trace=trace or TraceServiceRunTrace(),
        llm_max_attempts=settings.workflow_llm_max_attempts,
    )
