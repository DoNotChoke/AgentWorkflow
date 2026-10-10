"""Một lần chạy workflow để lại Execution Trace. Activity thật, mọi cổng là giả; không hạ tầng."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from dataclasses import replace as dataclasses_replace
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from app.core.errors import ProviderUnavailableError
from app.domains.workflow.application.ports import (
    ExecutionRecorder,
    ModelAnswer,
    Rejected,
    ToolCatalogReader,
)
from app.domains.workflow.infrastructure.temporal.activities import ExecutorActivities
from app.domains.workflow.infrastructure.temporal.messages import (
    NodeRefMessage,
    RecordExecutionInput,
    RecordNodeInput,
    RunLlmInput,
    RunToolInput,
    RunTransformInput,
)

NOW = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
TENANT = "org_1"
EXECUTION = "exe_1"


@dataclass
class FakeTrace:
    """`RunTrace` giả. `trace_id=None` là lần chạy không có trace; `broken` là mọi lần ghi
    đều hỏng."""

    trace_id: str | None = "trc_1"
    broken: bool = False
    opened: list[str] = field(default_factory=list)
    closed: list[tuple[str, str, str | None]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)

    def _check(self) -> None:
        if self.broken:
            raise RuntimeError("database của trace không trả lời")

    async def open(self, *, tenant_id: str, execution_id: str, at: datetime) -> None:
        self._check()
        self.opened.append(execution_id)

    async def find(self, *, tenant_id: str, execution_id: str) -> str | None:
        self._check()
        return self.trace_id

    async def step(self, **step: Any) -> None:
        self._check()
        self.steps.append(step)

    async def close(
        self,
        *,
        tenant_id: str,
        execution_id: str,
        status: str,
        at: datetime,
        error_category: str | None = None,
    ) -> None:
        self._check()
        self.closed.append((execution_id, status, error_category))


@dataclass
class FakeTools:
    output: Mapping[str, Any] | Exception = field(default_factory=lambda: {"message_ts": "1.0"})
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def invoke(self, **call: Any) -> Mapping[str, Any]:
        self.calls.append(call)
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


class FakeRunner:
    def run(self, code: str, args: Mapping[str, Any]) -> Any:
        return len(args["messages"])


class FakeModel:
    async def answer(
        self,
        *,
        model: str,
        system: str,
        data: str,
        output_schema: Mapping[str, Any],
        rejected: Sequence[Rejected],
    ) -> ModelAnswer:
        return ModelAnswer(output={"text": "tóm tắt"}, input_tokens=100, output_tokens=20)


class FakeRecorder:
    async def mark_running(self, **_: Any) -> None: ...

    async def seed_nodes(self, **_: Any) -> None: ...

    async def record_node(self, **_: Any) -> None: ...

    async def finish(self, **_: Any) -> None: ...


def _activities(trace: FakeTrace, tools: FakeTools | None = None) -> ExecutorActivities:
    return ExecutorActivities(
        recorder=cast(ExecutionRecorder, FakeRecorder()),
        catalog=cast(ToolCatalogReader, None),
        runner=FakeRunner(),
        models=FakeModel(),
        tools=tools or FakeTools(),
        trace=trace,
    )


def _tool_request() -> RunToolInput:
    return RunToolInput(
        tenant_id=TENANT,
        execution_id=EXECUTION,
        node_path="moi_cau[2].dang",
        tool_id="slack.post_message",
        tool_version="1.0",
        input={"channel": "C1", "text": "xin chào"},
        node_id="dang",
        idempotency_key="idm_exe_1:moi_cau[2].dang:slack.post_message",
    )


# ─── Node `tool` ─────────────────────────────────────────────────────────────


async def test_node_tool_de_lai_mot_buoc_co_input_va_output() -> None:
    trace, tools = FakeTrace(), FakeTools()

    output = await ActivityEnvironment().run(
        _activities(trace, tools).run_tool_node, _tool_request()
    )

    assert output == {"message_ts": "1.0"}
    (step,) = trace.steps
    assert (step["kind"], step["status"], step["trace_id"]) == ("tool_call", "succeeded", "trc_1")
    assert step["input_payload"] == {"channel": "C1", "text": "xin chào"}
    assert step["output_payload"] == {"message_ts": "1.0"}
    assert step["attributes"] == {
        "node_path": "moi_cau[2].dang",
        "node_id": "dang",
        "node_type": "tool",
        "tool_id": "slack.post_message",
        "tool_version": "1.0",
        "attempt": 1,
    }
    assert step["started_at"] <= step["completed_at"]


async def test_buoc_cua_gateway_nam_duoi_buoc_cua_node() -> None:
    trace, tools = FakeTrace(), FakeTools()

    await ActivityEnvironment().run(_activities(trace, tools).run_tool_node, _tool_request())

    (call,) = tools.calls
    assert call["trace_id"] == "trc_1"
    assert call["parent_step_id"] == trace.steps[0]["step_id"]


async def test_tool_hong_thi_buoc_ghi_failed_kem_hang_loi_va_loi_van_di_tiep() -> None:
    trace = FakeTrace()
    tools = FakeTools(output=ProviderUnavailableError("Slack không trả lời."))

    with pytest.raises(ApplicationError) as raised:
        await ActivityEnvironment().run(_activities(trace, tools).run_tool_node, _tool_request())

    assert raised.value.type == str(ProviderUnavailableError.category)
    (step,) = trace.steps
    assert step["status"] == "failed"
    assert step["error_category"] == str(ProviderUnavailableError.category)
    assert step["input_payload"] == {"channel": "C1", "text": "xin chào"}
    assert step["output_payload"] is None


async def test_loi_khong_phai_cua_platform_ghi_la_internal() -> None:
    trace = FakeTrace()
    tools = FakeTools(output=RuntimeError("chưa đặt khoá ký"))

    with pytest.raises(RuntimeError):
        await ActivityEnvironment().run(_activities(trace, tools).run_tool_node, _tool_request())

    assert trace.steps[0]["error_category"] == "InternalError"


async def test_lan_chay_khong_co_trace_thi_node_van_chay_va_khong_ghi_gi() -> None:
    trace, tools = FakeTrace(trace_id=None), FakeTools()

    output = await ActivityEnvironment().run(
        _activities(trace, tools).run_tool_node, _tool_request()
    )

    assert output == {"message_ts": "1.0"}
    assert trace.steps == []
    assert (tools.calls[0]["trace_id"], tools.calls[0]["parent_step_id"]) == (None, None)


async def test_trace_hong_khong_lam_hong_node() -> None:
    trace, tools = FakeTrace(broken=True), FakeTools()

    output = await ActivityEnvironment().run(
        _activities(trace, tools).run_tool_node, _tool_request()
    )

    assert output == {"message_ts": "1.0"}
    assert len(tools.calls) == 1


# ─── Node `transform`, `llm`, `condition` ────────────────────────────────────


async def test_node_transform_ghi_nhu_tool_system_transform_cua_agent() -> None:
    trace = FakeTrace()
    request = RunTransformInput(
        tenant_id=TENANT,
        execution_id=EXECUTION,
        node_path="dem",
        code="return len(args['messages'])",
        args={"messages": [{"ts": "1"}, {"ts": "2"}]},
        output_schema={"type": "integer"},
    )

    await ActivityEnvironment().run(_activities(trace).run_transform_node, request)

    (step,) = trace.steps
    assert step["kind"] == "tool_call"
    assert step["attributes"]["tool_id"] == "system.transform"
    assert step["input_payload"] == {
        "code": "return len(args['messages'])",
        "args": {"messages": [{"ts": "1"}, {"ts": "2"}]},
    }
    assert step["output_payload"] == {"value": 2}


async def test_transform_tra_sai_kieu_thi_buoc_failed() -> None:
    trace = FakeTrace()
    request = RunTransformInput(
        tenant_id=TENANT,
        execution_id=EXECUTION,
        node_path="dem",
        code="return len(args['messages'])",
        args={"messages": []},
        output_schema={"type": "string"},
    )

    with pytest.raises(ApplicationError):
        await ActivityEnvironment().run(_activities(trace).run_transform_node, request)

    assert trace.steps[0]["status"] == "failed"
    assert trace.steps[0]["output_payload"] is None


async def test_node_llm_ghi_instructions_du_lieu_ket_qua_va_token() -> None:
    trace = FakeTrace()
    request = RunLlmInput(
        tenant_id=TENANT,
        execution_id=EXECUTION,
        node_path="tom_tat",
        model="gpt-6.1-sol",
        instructions="Tóm tắt trang trong `trang`.",
        input={"trang": {"id": "p1"}},
        output_schema={
            "type": "object",
            "required": ["text"],
            "properties": {"text": {"type": "string"}},
        },
    )

    await ActivityEnvironment().run(_activities(trace).run_llm_node, request)

    (step,) = trace.steps
    assert step["kind"] == "model_call"
    assert step["input_payload"] == {
        "instructions": "Tóm tắt trang trong `trang`.",
        "input": {"trang": {"id": "p1"}},
    }
    assert step["output_payload"] == {"text": "tóm tắt"}
    assert step["attributes"] == {
        "node_path": "tom_tat",
        "node_type": "llm",
        "model": "gpt-6.1-sol",
        "attempt": 1,
        "input_tokens": 100,
        "output_tokens": 20,
        "model_calls": 1,
    }


def _condition(detail: dict[str, Any] | None) -> RecordNodeInput:
    return RecordNodeInput(
        tenant_id=TENANT,
        execution_id=EXECUTION,
        node=NodeRefMessage(node_path="du_nhieu", node_id="du_nhieu", node_type="condition"),
        status="succeeded",
        at=NOW,
        detail=detail,
    )


async def test_condition_ghi_nhanh_da_chon_va_ghi_lai_thi_cung_mot_buoc() -> None:
    trace = FakeTrace()
    activities = _activities(trace)

    await ActivityEnvironment().run(
        activities.record_node, _condition({"op": "gt", "result": True})
    )
    await ActivityEnvironment().run(
        activities.record_node, _condition({"op": "gt", "result": True})
    )

    first, second = trace.steps
    assert (first["kind"], first["output_payload"]) == ("decision", {"op": "gt", "result": True})
    assert first["attributes"]["node_path"] == "du_nhieu"
    assert first["step_id"] == second["step_id"]


async def test_ghi_trang_thai_cua_node_khac_khong_them_buoc_nao() -> None:
    trace = FakeTrace()

    await ActivityEnvironment().run(_activities(trace).record_node, _condition(None))

    assert trace.steps == []


# ─── Mở và đóng ──────────────────────────────────────────────────────────────


def _execution(status: str, error_category: str | None = None) -> RecordExecutionInput:
    return RecordExecutionInput(
        tenant_id=TENANT,
        execution_id=EXECUTION,
        status=status,
        at=NOW,
        error_category=error_category,
    )


async def test_trace_mo_khi_lan_chay_bat_dau_va_dong_cung_trang_thai_ket() -> None:
    trace = FakeTrace()
    activities = _activities(trace)

    await ActivityEnvironment().run(activities.record_execution, _execution("running"))
    assert (trace.opened, trace.closed) == ([EXECUTION], [])

    await ActivityEnvironment().run(
        activities.record_execution, _execution("failed", "ProviderUnavailable")
    )
    assert trace.closed == [(EXECUTION, "failed", "ProviderUnavailable")]


async def test_khong_mo_duoc_trace_thi_lan_chay_van_bat_dau() -> None:
    trace = FakeTrace(broken=True)

    await ActivityEnvironment().run(_activities(trace).record_execution, _execution("running"))
    await ActivityEnvironment().run(_activities(trace).record_execution, _execution("succeeded"))


# ─── Số lần thử ──────────────────────────────────────────────────────────────


class CountingRecorder(FakeRecorder):
    def __init__(self) -> None:
        self.nodes: list[dict[str, Any]] = []

    async def record_node(self, **node: Any) -> None:
        self.nodes.append(node)


def _on_attempt(attempt: int) -> ActivityEnvironment:
    import dataclasses

    env = ActivityEnvironment()
    env.info = dataclasses.replace(env.info, attempt=attempt)
    return env


def _counting(recorder: CountingRecorder, trace: FakeTrace) -> ExecutorActivities:
    return ExecutorActivities(
        recorder=cast(ExecutionRecorder, recorder),
        catalog=cast(ToolCatalogReader, None),
        runner=FakeRunner(),
        models=FakeModel(),
        tools=FakeTools(),
        trace=trace,
    )


async def test_lan_thu_dau_khong_ghi_gi_them_vao_node() -> None:
    recorder = CountingRecorder()

    await _on_attempt(1).run(_counting(recorder, FakeTrace()).run_tool_node, _tool_request())

    assert recorder.nodes == []


async def test_lan_thu_lai_ghi_so_lan_thu_cua_node_truoc_khi_chay() -> None:
    recorder, trace = CountingRecorder(), FakeTrace()

    await _on_attempt(3).run(_counting(recorder, trace).run_tool_node, _tool_request())

    (node,) = recorder.nodes
    assert (node["status"], node["attempt"]) == ("running", 3)
    assert (node["node"].node_path, node["node"].node_id, node["node"].node_type) == (
        "moi_cau[2].dang",
        "dang",
        "tool",
    )
    assert trace.steps[0]["attributes"]["attempt"] == 3


async def test_thoi_han_cua_tool_di_toi_ben_goi_gateway() -> None:
    tools = FakeTools()
    request = dataclasses_replace(_tool_request(), timeout_seconds=90.0)

    await ActivityEnvironment().run(_activities(FakeTrace(), tools).run_tool_node, request)

    assert tools.calls[0]["timeout_seconds"] == 90.0
