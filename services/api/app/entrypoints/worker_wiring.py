"""Nối worker (domain `workflow`) với domain `execution`, catalog tool, sandbox và
Model Gateway (ADR-0011)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

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
from app.domains.workflow.application.llm_node import RESULT_TOOL
from app.domains.workflow.application.ports import (
    ModelAnswer,
    NodeModel,
    NodeRef,
    Rejected,
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
) -> ExecutorActivities:
    """`models` và `tools` để test đưa bản giả vào; mặc định là Model Gateway và Integration
    Gateway thật."""
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
        llm_max_attempts=settings.workflow_llm_max_attempts,
    )
