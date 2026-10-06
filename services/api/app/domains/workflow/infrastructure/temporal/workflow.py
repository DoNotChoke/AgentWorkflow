"""Generic Workflow Executor: chạy một version đã publish, node theo node.

Workflow code phải tất định — không I/O, không đồng hồ thật. Mọi thứ chạm ra
ngoài là activity; thời gian lấy từ `workflow.now()`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from functools import partial
from typing import Any, Final

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError
from temporalio.exceptions import TimeoutError as TemporalTimeoutError

with workflow.unsafe.imports_passed_through():
    from app.core.errors import AppError, ErrorCategory
    from app.core.ids import idempotency_key
    from app.domains.workflow.application.runtime import LoadedPlan, tool_key
    from app.domains.workflow.domain.bindings import (
        Scope,
        check_inputs,
        loop_items,
        resolve_inputs,
    )
    from app.domains.workflow.domain.conditions import holds
    from app.domains.workflow.domain.ir import (
        ConditionNode,
        LlmNode,
        LoopNode,
        Node,
        ToolNode,
        TransformNode,
        TriggerNode,
        parse,
    )
    from app.domains.workflow.domain.plan import NodePath, Plan, build_plan
    from app.domains.workflow.infrastructure.temporal.messages import (
        EXECUTOR_WORKFLOW,
        LOAD_PLAN,
        RECORD_EXECUTION,
        RECORD_NODE,
        RUN_LLM_NODE,
        RUN_TOOL_NODE,
        RUN_TRANSFORM_NODE,
        SEED_NODES,
        ExecutorInput,
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

_DATA_TIMEOUT: Final = timedelta(seconds=30)
_DATA_RETRY: Final = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)
#: Tạm cho tới khi timeout của từng tool đến từ catalog.
_TOOL_TIMEOUT: Final = timedelta(seconds=60)
_TOOL_RETRY: Final = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
)
#: Code của node `transform` tất định: chạy lại chỉ có ích khi sandbox không dựng được.
_TRANSFORM_TIMEOUT: Final = timedelta(seconds=30)
_TRANSFORM_RETRY: Final = RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=2)
#: Một lần chạy activity `llm` có thể hỏi model vài lượt khi kết quả sai kiểu.
_LLM_TIMEOUT: Final = timedelta(minutes=5)
_LLM_RETRY: Final = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
)


class _NodeFailedError(Exception):
    """Một node hỏng. Đi ngược lên qua loop cha tới lần chạy."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.message = message

    @classmethod
    def of(cls, error: AppError) -> _NodeFailedError:
        return cls(str(error.category), error.message)

    @classmethod
    def of_activity(cls, error: ActivityError) -> _NodeFailedError:
        cause = error.cause
        if isinstance(cause, ApplicationError):
            return cls(cause.type or str(ErrorCategory.INTERNAL), cause.message)
        if isinstance(cause, TemporalTimeoutError):
            return cls(str(ErrorCategory.TIMEOUT), str(cause))
        return cls(str(ErrorCategory.INTERNAL), str(cause or error))


def _ref(path: NodePath, node: Node) -> NodeRefMessage:
    kind = {
        TriggerNode: "trigger",
        ToolNode: "tool",
        LoopNode: "loop",
        ConditionNode: "condition",
        LlmNode: "llm",
        TransformNode: "transform",
    }[type(node)]
    return NodeRefMessage(node_path=str(path), node_id=node.id, node_type=kind)


@workflow.defn(name=EXECUTOR_WORKFLOW)
class GenericWorkflowExecutor:
    @workflow.run
    async def run(self, request: ExecutorInput) -> None:
        self._request = request
        #: Token của node `llm` vừa chạy, chờ ghi cùng trạng thái `succeeded`.
        self._usage: dict[str, dict[str, int]] = {}
        try:
            plan, mutating = await self._prepare()
            await self._record_execution("running")
            await self._execute(plan, mutating)
        except _NodeFailedError as failed:
            await self._record_execution("failed", failed)
            raise ApplicationError(
                failed.message, type=failed.category, non_retryable=True
            ) from None
        await self._record_execution("succeeded")

    # ─── Chuẩn bị ────────────────────────────────────────────────────────────

    async def _prepare(self) -> tuple[Plan, frozenset[str]]:
        try:
            loaded: LoadedPlan = await workflow.execute_activity(
                LOAD_PLAN,
                LoadPlanInput(self._request.tenant_id, self._request.workflow_version_id),
                result_type=LoadedPlan,
                task_queue=self._request.queues.processing,
                start_to_close_timeout=_DATA_TIMEOUT,
                retry_policy=_DATA_RETRY,
            )
        except ActivityError as error:
            raise _NodeFailedError.of_activity(error) from None
        try:
            ir = parse(loaded.ir)
            plan = build_plan(ir)
        except AppError as error:
            raise _NodeFailedError.of(error) from None

        return plan, frozenset(loaded.mutating_tools)

    # ─── Chạy ────────────────────────────────────────────────────────────────

    async def _execute(self, plan: Plan, mutating: frozenset[str]) -> None:
        top = [(NodePath(node_id), plan.node(node_id)) for node_id in plan.order]
        await self._seed([_ref(path, node) for path, node in top])

        outputs: dict[str, Any] = {}
        settled: set[str] = set()
        #: Kết quả của các `condition` đã chạy, và các node không còn đường nào dẫn tới.
        taken: dict[str, bool] = {}
        skipped: set[str] = set()
        while ready := plan.runnable(settled):
            node = plan.node(ready[0])
            path = NodePath(node.id)
            settled.add(node.id)
            if plan.is_cut_off(node.id, taken, skipped):
                skipped.add(node.id)
                await self._record_node(_ref(path, node), "skipped")
                continue
            scope = Scope(inputs=self._request.inputs, outputs=outputs)

            work: Callable[[], Awaitable[Any]]
            if isinstance(node, TriggerNode):
                work = partial(self._trigger, node)
            elif isinstance(node, LoopNode):
                work = partial(self._loop, plan, node, scope, mutating)
            elif isinstance(node, ConditionNode):
                work = partial(self._condition, node, scope)
            else:
                work = partial(self._step, path, node, scope, mutating)
            outputs[node.id] = await self._node(path, node, work)
            if isinstance(node, ConditionNode):
                taken[node.id] = outputs[node.id]

    async def _trigger(self, trigger: TriggerNode) -> None:
        check_inputs(trigger, self._request.inputs)

    async def _condition(self, condition: ConditionNode, scope: Scope) -> bool:
        return holds(condition, scope)

    async def _step(
        self,
        path: NodePath,
        node: ToolNode | LlmNode | TransformNode,
        scope: Scope,
        mutating: frozenset[str],
    ) -> Any:
        """Một node gọi ra ngoài workflow. Dùng chung cho tầng ngoài và thân loop."""
        if isinstance(node, ToolNode):
            return await self._tool(path, node, scope, mutating)
        if isinstance(node, TransformNode):
            return await self._transform(path, node, scope)
        return await self._llm(path, node, scope)

    async def _transform(self, path: NodePath, node: TransformNode, scope: Scope) -> Any:
        request = RunTransformInput(
            tenant_id=self._request.tenant_id,
            execution_id=self._request.execution_id,
            node_path=str(path),
            code=node.code,
            args=resolve_inputs(node.args, scope, node=str(path)),
            output_schema=dict(node.output_schema),
        )
        try:
            result: dict[str, Any] = await workflow.execute_activity(
                RUN_TRANSFORM_NODE,
                request,
                result_type=dict,
                task_queue=self._request.queues.processing,
                start_to_close_timeout=_TRANSFORM_TIMEOUT,
                retry_policy=_TRANSFORM_RETRY,
            )
        except ActivityError as error:
            raise _NodeFailedError.of_activity(error) from None
        return result["value"]

    async def _llm(self, path: NodePath, node: LlmNode, scope: Scope) -> Any:
        queues = self._request.queues
        request = RunLlmInput(
            tenant_id=self._request.tenant_id,
            execution_id=self._request.execution_id,
            node_path=str(path),
            model=node.model,
            instructions=node.instructions,
            input=resolve_inputs(node.input, scope, node=str(path)),
            output_schema=dict(node.output_schema),
        )
        try:
            result: LlmNodeResult = await workflow.execute_activity(
                RUN_LLM_NODE,
                request,
                result_type=LlmNodeResult,
                task_queue=queues.ai or queues.processing,
                start_to_close_timeout=_LLM_TIMEOUT,
                retry_policy=_LLM_RETRY,
            )
        except ActivityError as error:
            raise _NodeFailedError.of_activity(error) from None
        self._usage[str(path)] = {
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "model_calls": result.attempts,
        }
        return result.output

    async def _tool(
        self, path: NodePath, tool: ToolNode, scope: Scope, mutating: frozenset[str]
    ) -> Any:
        queues = self._request.queues
        writes = tool_key(tool.tool_id, tool.tool_version) in mutating
        request = RunToolInput(
            tenant_id=self._request.tenant_id,
            execution_id=self._request.execution_id,
            node_path=str(path),
            tool_id=tool.tool_id,
            tool_version=tool.tool_version,
            input=resolve_inputs(tool.input, scope, node=str(path)),
            node_id=tool.id,
            # Ổn định qua mọi lần Temporal thử lại activity, và khác nhau giữa các lượt
            # của một loop: `moi_cau[2].dang` không phải `moi_cau[1].dang`.
            idempotency_key=(
                idempotency_key(
                    execution_id=self._request.execution_id,
                    node_id=str(path),
                    operation=tool.tool_id,
                )
                if writes
                else None
            ),
        )
        try:
            return await workflow.execute_activity(
                RUN_TOOL_NODE,
                request,
                result_type=dict,
                task_queue=queues.integration_write if writes else queues.integration_read,
                start_to_close_timeout=_TOOL_TIMEOUT,
                retry_policy=_TOOL_RETRY,
            )
        except ActivityError as error:
            raise _NodeFailedError.of_activity(error) from None

    async def _loop(
        self, plan: Plan, loop: LoopNode, scope: Scope, mutating: frozenset[str]
    ) -> list[Any]:
        items = loop_items(loop, scope)
        body = plan.body(loop.id)
        await self._seed(
            [
                _ref(path, inner)
                for turn in range(len(items))
                for path, inner in zip(plan.iteration_paths(loop.id, turn), body, strict=True)
            ]
        )

        collected: list[Any] = []
        for turn, item in enumerate(items):
            done: dict[str, Any] = {}
            for path, inner in zip(plan.iteration_paths(loop.id, turn), body, strict=True):
                here = scope.with_item(item, {**scope.outputs, **done})
                done[inner.id] = await self._node(
                    path, inner, partial(self._step, path, inner, here, mutating)
                )
            collected.append(done[loop.collect])
        return collected

    # ─── Một node ────────────────────────────────────────────────────────────

    async def _node(self, path: NodePath, node: Node, work: Callable[[], Awaitable[Any]]) -> Any:
        """Ghi `running`, làm việc, ghi kết quả. Hỏng thì ghi `failed` rồi ném tiếp."""
        ref = _ref(path, node)
        await self._record_node(ref, "running", attempt=1)
        try:
            output = await work()
        except AppError as error:
            failed = _NodeFailedError.of(error)
        except _NodeFailedError as error:
            failed = error
        else:
            await self._record_node(ref, "succeeded", usage=self._usage.pop(str(path), None))
            return output
        await self._record_node(ref, "failed", failed)
        raise failed

    # ─── Activity ghi trạng thái ─────────────────────────────────────────────

    async def _seed(self, nodes: list[NodeRefMessage]) -> None:
        if not nodes:
            return
        await self._data(
            SEED_NODES,
            SeedNodesInput(
                self._request.tenant_id, self._request.execution_id, nodes, workflow.now()
            ),
        )

    async def _record_node(
        self,
        node: NodeRefMessage,
        status: str,
        failed: _NodeFailedError | None = None,
        *,
        attempt: int | None = None,
        usage: dict[str, int] | None = None,
    ) -> None:
        await self._data(
            RECORD_NODE,
            RecordNodeInput(
                tenant_id=self._request.tenant_id,
                execution_id=self._request.execution_id,
                node=node,
                status=status,
                at=workflow.now(),
                attempt=attempt,
                error_category=None if failed is None else failed.category,
                error_message=None if failed is None else failed.message,
                usage=usage,
            ),
        )

    async def _record_execution(self, status: str, failed: _NodeFailedError | None = None) -> None:
        await self._data(
            RECORD_EXECUTION,
            RecordExecutionInput(
                tenant_id=self._request.tenant_id,
                execution_id=self._request.execution_id,
                status=status,
                at=workflow.now(),
                error_category=None if failed is None else failed.category,
                error_message=None if failed is None else failed.message,
            ),
        )

    async def _data(self, activity: str, request: object) -> None:
        """Lỗi ở đây không bắt: ghi trạng thái hỏng hết lượt retry là DB có vấn đề."""
        await workflow.execute_activity(
            activity,
            request,
            task_queue=self._request.queues.processing,
            start_to_close_timeout=_DATA_TIMEOUT,
            retry_policy=_DATA_RETRY,
        )
