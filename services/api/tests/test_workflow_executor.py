"""Executor chạy thật trên Temporal và PostgreSQL, với tool giả.

Mỗi test dựng worker trong tiến trình, trên bộ queue riêng: executor thật, các
activity ghi trạng thái thật, chỉ `run_tool_node` là giả. Tool giả ghi lại mọi
lần bị gọi — gọi tool nào, với input gì, trên queue nào.

`AW_RECORD_HISTORY=1` ghi lại lịch sử của kịch bản 3 làm fixture cho
`test_workflow_replay.py`.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import infra_unavailable
from sqlalchemy import text
from temporalio import activity
from temporalio.client import Client, WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from app.core.config import get_settings
from app.core.db import dispose_engine, init_engine, session_scope
from app.domains.execution.application.service import ExecutionService
from app.domains.execution.infrastructure.repositories import SqlAlchemyExecutionRepository
from app.domains.workflow.domain.entities import Workflow, WorkflowVersion, fingerprint
from app.domains.workflow.infrastructure.repositories import SqlAlchemyWorkflowRepository
from app.domains.workflow.infrastructure.temporal.messages import (
    EXECUTOR_WORKFLOW,
    RUN_TOOL_NODE,
    ExecutorInput,
    ExecutorQueues,
    RunToolInput,
)
from app.domains.workflow.infrastructure.temporal.workflow import GenericWorkflowExecutor
from app.entrypoints.worker_wiring import build_executor_activities

pytestmark = pytest.mark.integration

TENANT = "org_workflow_executor"
NOW = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)
EXAMPLES = Path(__file__).resolve().parents[3] / "packages" / "schemas" / "workflow" / "examples"
HISTORY = Path(__file__).resolve().parent / "fixtures" / "histories" / "kb3.json"

KENH = {"channels": [{"id": "C1", "name": "general"}, {"id": "C2", "name": "test"}]}
KHOI_CON = {
    "results": [
        {"id": "p1", "type": "child_page"},
        {"id": "x1", "type": "paragraph"},
        {"id": "p2", "type": "child_page"},
        {"id": "x2", "type": "heading_1"},
        {"id": "p3", "type": "child_page"},
    ]
}


# ─── Tool giả ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Call:
    tool_id: str
    input: dict[str, Any]
    queue: str
    node_path: str


Behaviour = Callable[[dict[str, Any]], dict[str, Any]]

_calls: list[Call] = []
_script: dict[str, Behaviour] = {}


def _fails(category: str) -> Behaviour:
    def behaviour(_: dict[str, Any]) -> dict[str, Any]:
        raise ApplicationError(f"{category} giả", type=category, non_retryable=True)

    return behaviour


def _page(inp: dict[str, Any]) -> dict[str, Any]:
    return {"id": inp["page_id"], "title": f"Trang {inp['page_id']}"}


DEFAULT_SCRIPT: dict[str, Behaviour] = {
    "slack.list_channels": lambda _: KENH,
    "slack.post_message": lambda inp: {"ok": True, "channel": inp["channel"], "ts": "1.0"},
    "notion.search": lambda _: {"results": [{"id": "trang_aws"}]},
    "notion.get_block_children": lambda _: KHOI_CON,
    "notion.get_page": _page,
}


@activity.defn(name=RUN_TOOL_NODE)
async def fake_tool(request: RunToolInput) -> dict[str, Any]:
    _calls.append(
        Call(request.tool_id, dict(request.input), activity.info().task_queue, request.node_path)
    )
    return _script[request.tool_id](request.input)


# ─── Hạ tầng ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def engine() -> AsyncIterator[None]:
    init_engine(get_settings())
    try:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        await dispose_engine()
        infra_unavailable(f"PostgreSQL chưa sẵn sàng: {type(exc).__name__}. {exc}")

    _calls.clear()
    _script.clear()
    _script.update(DEFAULT_SCRIPT)
    yield

    async with session_scope() as session:
        ids = {"ids": [TENANT]}
        await session.execute(
            text("DELETE FROM execution.executions WHERE tenant_id = ANY(:ids)"), ids
        )
        await session.execute(
            text("DELETE FROM workflow.workflow_versions WHERE tenant_id = ANY(:ids)"), ids
        )
        await session.execute(
            text("DELETE FROM workflow.workflows WHERE tenant_id = ANY(:ids)"), ids
        )
    await dispose_engine()


@dataclass
class Outcome:
    execution_id: str
    status: str
    failure: tuple[str | None, str | None]
    nodes: dict[str, str]
    error: WorkflowFailureError | None
    history_json: str = field(default="", repr=False)


@dataclass
class Harness:
    client: Client
    workflow_queue: str
    queues: ExecutorQueues

    async def run(self, ir: dict[str, Any], inputs: dict[str, Any]) -> Outcome:
        version = await _published(ir)
        async with session_scope() as session:
            execution = await ExecutionService(
                repository=SqlAlchemyExecutionRepository(session)
            ).create(
                tenant_id=TENANT,
                workspace_id="ws_1",
                workflow_id=version.workflow_id,
                workflow_version_id=version.id,
                trigger_type="manual",
                inputs=inputs,
                initiated_by="usr_1",
                at=NOW,
            )
        handle = await self.client.start_workflow(
            EXECUTOR_WORKFLOW,
            ExecutorInput(
                execution_id=execution.id,
                tenant_id=TENANT,
                workflow_version_id=version.id,
                inputs=inputs,
                queues=self.queues,
            ),
            id=execution.id,
            task_queue=self.workflow_queue,
        )
        error: WorkflowFailureError | None = None
        try:
            await handle.result()
        except WorkflowFailureError as raised:
            error = raised

        async with session_scope() as session:
            progress = await ExecutionService(
                repository=SqlAlchemyExecutionRepository(session)
            ).get_progress(tenant_id=TENANT, execution_id=execution.id)
        failure = progress.execution.failure
        history = await handle.fetch_history()
        return Outcome(
            execution_id=execution.id,
            status=str(progress.execution.status),
            failure=(None, None) if failure is None else (str(failure.category), failure.message),
            nodes={n.node_path: str(n.status) for n in progress.nodes},
            error=error,
            history_json=history.to_json(),
        )


@pytest.fixture
async def harness() -> AsyncIterator[Harness]:
    settings = get_settings()
    try:
        client = await asyncio.wait_for(
            Client.connect(settings.temporal_host, namespace=settings.temporal_namespace), 5
        )
    except Exception as exc:  # noqa: BLE001
        infra_unavailable(f"Temporal chưa sẵn sàng: {type(exc).__name__}. {exc}")

    prefix = f"test-{uuid.uuid4().hex[:8]}"
    queues = ExecutorQueues(
        processing=f"{prefix}-processing",
        integration_read=f"{prefix}-read",
        integration_write=f"{prefix}-write",
    )
    executor = build_executor_activities()
    workers = [
        Worker(client, task_queue=f"{prefix}-workflow", workflows=[GenericWorkflowExecutor]),
        Worker(
            client,
            task_queue=queues.processing,
            activities=[
                executor.load_plan,
                executor.seed_nodes,
                executor.record_node,
                executor.record_execution,
            ],
        ),
        Worker(client, task_queue=queues.integration_read, activities=[fake_tool]),
        Worker(client, task_queue=queues.integration_write, activities=[fake_tool]),
    ]
    async with AsyncExitStack() as stack:
        for worker in workers:
            await stack.enter_async_context(worker)
        yield Harness(client=client, workflow_queue=f"{prefix}-workflow", queues=queues)


def _example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


async def _published(ir: dict[str, Any]) -> WorkflowVersion:
    workflow = Workflow(
        id=f"wf_{uuid.uuid4().hex}",
        tenant_id=TENANT,
        workspace_id="ws_1",
        name="executor",
        created_by="usr_1",
        created_at=NOW,
        updated_at=NOW,
    )
    version = WorkflowVersion(
        id=f"wfv_{uuid.uuid4().hex}",
        workflow_id=workflow.id,
        tenant_id=TENANT,
        number=1,
        ir=ir,
        ir_sha256=fingerprint(ir),
        published_at=NOW,
        published_by="usr_1",
    )
    async with session_scope() as session:
        repository = SqlAlchemyWorkflowRepository(session)
        await repository.add(workflow)
        await repository.add_version(version)
    return version


def _tools() -> list[str]:
    return [call.tool_id for call in _calls]


# ─── Chạy hết ────────────────────────────────────────────────────────────────


async def test_kich_ban_mot_chay_het_va_tool_ghi_di_queue_ghi(harness: Harness) -> None:
    outcome = await harness.run(_example("kb1-slack-ghi"), {"noi_dung": "xin chào"})

    assert outcome.error is None
    assert outcome.status == "succeeded"
    assert outcome.nodes == {"t": "succeeded", "dang": "succeeded"}
    assert _calls == [
        Call(
            "slack.post_message",
            {"channel": "C0VIDU0001", "text": "xin chào"},
            harness.queues.integration_write,
            "dang",
        )
    ]


async def test_kich_ban_ba_loop_qua_trang_con(harness: Harness) -> None:
    outcome = await harness.run(_example("kb3-notion-doc-sau"), {"ten_trang": "Ghi chú AWS"})

    assert outcome.error is None
    assert outcome.status == "succeeded"
    assert outcome.nodes == {
        "t": "succeeded",
        "tim": "succeeded",
        "con": "succeeded",
        "moi_trang": "succeeded",
        "moi_trang[0].doc_trang": "succeeded",
        "moi_trang[1].doc_trang": "succeeded",
        "moi_trang[2].doc_trang": "succeeded",
    }
    assert [call.input for call in _calls if call.tool_id == "notion.get_page"] == [
        {"page_id": "p1"},
        {"page_id": "p2"},
        {"page_id": "p3"},
    ]
    assert {call.queue for call in _calls} == {harness.queues.integration_read}

    if os.environ.get("AW_RECORD_HISTORY") == "1":
        HISTORY.parent.mkdir(parents=True, exist_ok=True)
        HISTORY.write_text(outcome.history_json, encoding="utf-8")


async def test_where_chon_kenh_theo_ten(harness: Harness) -> None:
    outcome = await harness.run(
        _example("kb2-slack-doc-roi-ghi"), {"ten_kenh": "test", "noi_dung": "xin chào"}
    )
    assert outcome.status == "succeeded"
    assert _calls[-1].input == {"channel": "C2", "text": "xin chào"}


# ─── Hỏng ────────────────────────────────────────────────────────────────────


async def test_tool_hong_thi_node_sau_van_pending(harness: Harness) -> None:
    _script["notion.get_block_children"] = _fails("ProviderPermanentError")
    outcome = await harness.run(_example("kb3-notion-doc-sau"), {"ten_trang": "Ghi chú AWS"})

    assert outcome.error is not None
    assert outcome.status == "failed"
    assert outcome.failure == ("ProviderPermanentError", "ProviderPermanentError giả")
    assert outcome.nodes == {
        "t": "succeeded",
        "tim": "succeeded",
        "con": "failed",
        "moi_trang": "pending",
    }


async def test_luot_giua_hong_thi_luot_sau_pending_va_loop_hong(harness: Harness) -> None:
    def page(inp: dict[str, Any]) -> dict[str, Any]:
        if inp["page_id"] == "p2":
            raise ApplicationError("Notion 404", type="ProviderPermanentError", non_retryable=True)
        return _page(inp)

    _script["notion.get_page"] = page
    outcome = await harness.run(_example("kb3-notion-doc-sau"), {"ten_trang": "Ghi chú AWS"})

    assert outcome.status == "failed"
    assert outcome.nodes["moi_trang"] == "failed"
    assert [outcome.nodes[f"moi_trang[{i}].doc_trang"] for i in range(3)] == [
        "succeeded",
        "failed",
        "pending",
    ]


async def test_vuot_max_items_thi_than_loop_khong_duoc_seed(harness: Harness) -> None:
    ir = _example("kb3-notion-doc-sau")
    ir["nodes"][3]["max_items"] = 2
    outcome = await harness.run(ir, {"ten_trang": "Ghi chú AWS"})

    assert outcome.status == "failed"
    assert outcome.failure[0] == "WorkflowExecutionError"
    assert outcome.nodes["moi_trang"] == "failed"
    assert not any(path.startswith("moi_trang[") for path in outcome.nodes)
    assert "notion.get_page" not in _tools()


async def test_where_khong_khop_noi_ro_gia_tri(harness: Harness) -> None:
    outcome = await harness.run(
        _example("kb2-slack-doc-roi-ghi"), {"ten_kenh": "khong-co", "noi_dung": "xin chào"}
    )
    assert outcome.status == "failed"
    assert outcome.nodes["dang"] == "failed"
    assert "name = 'khong-co'" in (outcome.failure[1] or "")
    assert "slack.post_message" not in _tools()


async def test_input_sai_schema_thi_trigger_hong_va_khong_goi_tool(harness: Harness) -> None:
    outcome = await harness.run(_example("kb2-slack-doc-roi-ghi"), {"noi_dung": "xin chào"})
    assert outcome.status == "failed"
    assert outcome.nodes == {"t": "failed", "kenh": "pending", "dang": "pending"}
    assert _calls == []


async def test_node_chua_ho_tro_thi_hong_truoc_khi_goi_tool_nao(harness: Harness) -> None:
    outcome = await harness.run(_example("kb5-re-nhanh"), {})
    assert outcome.status == "failed"
    assert outcome.failure[0] == "WorkflowDefinitionError"
    assert "du_nhieu" in (outcome.failure[1] or "")
    assert outcome.nodes == {}
    assert _calls == []


async def test_lich_su_vua_chay_replay_lai_duoc(harness: Harness) -> None:
    from temporalio.client import WorkflowHistory
    from temporalio.worker import Replayer

    outcome = await harness.run(
        copy.deepcopy(_example("kb3-notion-doc-sau")), {"ten_trang": "Ghi chú AWS"}
    )
    history = WorkflowHistory.from_json(outcome.execution_id, outcome.history_json)
    await Replayer(workflows=[GenericWorkflowExecutor]).replay_workflow(history)
