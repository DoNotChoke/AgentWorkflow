"""Đi trọn đường: `ExecutionStarter` → `TemporalExecutionLauncher` thật → executor.

Launcher dựng `ExecutorInput` và lấy tên queue từ `Settings` — thứ duy nhất ở đây
mà test API (launcher giả) không chạm tới.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from typing import Any

import pytest
from conftest import infra_unavailable
from sqlalchemy import text
from temporalio.client import Client
from temporalio.worker import Worker
from test_workflow_executor import DEFAULT_SCRIPT, _calls, _example, _published, _script, fake_tool

from app.core.config import Settings, get_settings
from app.core.db import dispose_engine, init_engine, session_scope
from app.domains.execution.application.ports import RunnableVersion
from app.domains.execution.application.service import ExecutionService
from app.domains.execution.application.starting import ExecutionStarter
from app.domains.execution.infrastructure.http import execution_records
from app.domains.execution.infrastructure.repositories import SqlAlchemyExecutionRepository
from app.domains.workflow.infrastructure.temporal.workflow import GenericWorkflowExecutor
from app.entrypoints.wiring import TemporalExecutionLauncher
from app.entrypoints.worker_wiring import build_executor_activities

pytestmark = pytest.mark.integration

TENANT = "org_workflow_executor"


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


class _Runs:
    def __init__(self, runnable: RunnableVersion) -> None:
        self._runnable = runnable

    async def prepare(self, **_: Any) -> RunnableVersion:
        return self._runnable


async def test_lan_chay_bang_tay_di_het_duong_qua_temporal() -> None:
    base = get_settings()
    try:
        client = await asyncio.wait_for(
            Client.connect(base.temporal_host, namespace=base.temporal_namespace), 5
        )
    except Exception as exc:  # noqa: BLE001
        infra_unavailable(f"Temporal chưa sẵn sàng: {type(exc).__name__}. {exc}")

    prefix = f"test-{uuid.uuid4().hex[:8]}"
    settings = Settings(
        temporal_task_queue_workflow=f"{prefix}-workflow",
        temporal_task_queue_processing=f"{prefix}-processing",
        temporal_task_queue_integration_read=f"{prefix}-read",
        temporal_task_queue_integration_write=f"{prefix}-write",
    )
    version = await _published(_example("kb1-slack-ghi"))
    starter = ExecutionStarter(
        runs=_Runs(RunnableVersion(version.workflow_id, version.id, 1, {"dang": "conn_slack"})),
        launcher=TemporalExecutionLauncher(settings),
        records=execution_records,
    )

    executor = build_executor_activities()
    workers = [
        Worker(
            client,
            task_queue=settings.temporal_task_queue_workflow,
            workflows=[GenericWorkflowExecutor],
        ),
        Worker(
            client,
            task_queue=settings.temporal_task_queue_processing,
            activities=[
                executor.load_plan,
                executor.seed_nodes,
                executor.record_node,
                executor.record_execution,
            ],
        ),
        Worker(
            client, task_queue=settings.temporal_task_queue_integration_read, activities=[fake_tool]
        ),
        Worker(
            client,
            task_queue=settings.temporal_task_queue_integration_write,
            activities=[fake_tool],
        ),
    ]
    async with AsyncExitStack() as stack:
        for worker in workers:
            await stack.enter_async_context(worker)
        execution = await starter.start_manual(
            tenant_id=TENANT,
            workspace_id="ws_1",
            actor_id="usr_1",
            workflow_id=version.workflow_id,
            version=None,
            inputs={"noi_dung": "xin chào"},
            by_node={},
            by_provider={"slack": "conn_slack"},
        )
        handle = client.get_workflow_handle(execution.id)
        await handle.result()
        history = await handle.fetch_history()

    async with session_scope() as session:
        progress = await ExecutionService(
            repository=SqlAlchemyExecutionRepository(session)
        ).get_progress(tenant_id=TENANT, execution_id=execution.id)
    assert str(progress.execution.status) == "succeeded"
    assert progress.execution.connection_bindings == {"dang": "conn_slack"}
    assert [(call.tool_id, call.queue) for call in _calls] == [
        ("slack.post_message", settings.temporal_task_queue_integration_write)
    ]

    started = history.events[0].workflow_execution_started_event_attributes
    assert started.task_queue.name == settings.temporal_task_queue_workflow
    payload = json.loads(started.input.payloads[0].data)
    assert payload["connections"] == {"dang": "conn_slack"}
    assert payload["queues"]["processing"] == settings.temporal_task_queue_processing
