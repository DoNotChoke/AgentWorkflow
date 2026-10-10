"""Activity của executor chạy thật trên PostgreSQL, không cần Temporal server.

`ActivityEnvironment` gọi thẳng hàm activity. Thứ được kiểm là cả đường
activity → `worker_wiring` → `ExecutionService` → bảng, và lỗi ra dạng Temporal.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from conftest import infra_unavailable
from sqlalchemy import text
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from app.core.config import get_settings
from app.core.db import dispose_engine, init_engine, session_scope
from app.domains.execution.application.service import ExecutionService
from app.domains.execution.infrastructure.repositories import SqlAlchemyExecutionRepository
from app.domains.workflow.domain.entities import Workflow, WorkflowVersion, fingerprint
from app.domains.workflow.infrastructure.repositories import SqlAlchemyWorkflowRepository
from app.domains.workflow.infrastructure.temporal.messages import (
    LoadPlanInput,
    NodeRefMessage,
    RecordExecutionInput,
    RecordNodeInput,
    RunToolInput,
    SeedNodesInput,
)
from app.entrypoints.worker_wiring import build_executor_activities

pytestmark = pytest.mark.integration

TENANT = "org_worker_wiring"
OTHER_TENANT = "org_worker_wiring_khac"
NOW = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)
EXAMPLES = Path(__file__).resolve().parents[3] / "packages" / "schemas" / "workflow" / "examples"

ENV = ActivityEnvironment()
ACTIVITIES = build_executor_activities()
TIM = NodeRefMessage("tim", "tim", "tool")


@pytest.fixture(autouse=True)
async def engine() -> AsyncIterator[None]:
    init_engine(get_settings())
    try:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        await dispose_engine()
        infra_unavailable(f"PostgreSQL chưa sẵn sàng: {type(exc).__name__}. {exc}")

    yield

    async with session_scope() as session:
        ids = {"ids": [TENANT, OTHER_TENANT]}
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


async def _published(example: str) -> WorkflowVersion:
    ir: dict[str, Any] = json.loads((EXAMPLES / f"{example}.json").read_text(encoding="utf-8"))
    workflow = Workflow(
        id=f"wf_{uuid.uuid4().hex}",
        tenant_id=TENANT,
        workspace_id="ws_1",
        name=example,
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


async def _execution(version: WorkflowVersion) -> str:
    async with session_scope() as session:
        execution = await ExecutionService(
            repository=SqlAlchemyExecutionRepository(session)
        ).create(
            tenant_id=TENANT,
            workspace_id="ws_1",
            workflow_id=version.workflow_id,
            workflow_version_id=version.id,
            trigger_type="manual",
            inputs={},
            initiated_by="usr_1",
            at=NOW,
        )
    return execution.id


async def _progress(execution_id: str) -> tuple[str, dict[str, tuple[str, int]]]:
    async with session_scope() as session:
        progress = await ExecutionService(
            repository=SqlAlchemyExecutionRepository(session)
        ).get_progress(tenant_id=TENANT, execution_id=execution_id)
    nodes = {n.node_path: (str(n.status), n.attempt) for n in progress.nodes}
    return str(progress.execution.status), nodes


async def test_load_plan_doc_version_va_bang_tool_ghi() -> None:
    version = await _published("kb1-slack-ghi")
    plan = await ENV.run(ACTIVITIES.load_plan, LoadPlanInput(TENANT, version.id))
    assert plan.workflow_version_id == version.id
    assert plan.ir == version.ir
    assert plan.mutating_tools == ["slack.post_message@1.0"]


async def test_load_plan_tenant_khac_la_loi_khong_retry() -> None:
    version = await _published("kb1-slack-ghi")
    with pytest.raises(ApplicationError) as caught:
        await ENV.run(ACTIVITIES.load_plan, LoadPlanInput(OTHER_TENANT, version.id))
    assert (caught.value.type, caught.value.non_retryable) == ("WorkflowExecutionError", True)


async def test_mot_lan_chay_ghi_du_duong_qua_activity() -> None:
    execution_id = await _execution(await _published("kb3-notion-doc-sau"))
    later = NOW + timedelta(seconds=3)

    await ENV.run(
        ACTIVITIES.record_execution, RecordExecutionInput(TENANT, execution_id, "running", NOW)
    )
    await ENV.run(
        ACTIVITIES.seed_nodes,
        SeedNodesInput(
            TENANT,
            execution_id,
            [NodeRefMessage("t", "t", "trigger"), TIM, NodeRefMessage("con", "con", "tool")],
            NOW,
        ),
    )
    await ENV.run(
        ACTIVITIES.record_node, RecordNodeInput(TENANT, execution_id, TIM, "running", NOW, 1)
    )
    await ENV.run(
        ACTIVITIES.record_node,
        RecordNodeInput(
            TENANT, execution_id, TIM, "failed", later, 1, "ProviderPermanentError", "Notion 404"
        ),
    )
    await ENV.run(
        ACTIVITIES.record_execution,
        RecordExecutionInput(
            TENANT, execution_id, "failed", later, "ProviderPermanentError", "Notion 404"
        ),
    )

    assert await _progress(execution_id) == (
        "failed",
        {"t": ("pending", 0), "tim": ("failed", 1), "con": ("pending", 0)},
    )


async def test_ghi_trai_luat_la_loi_khong_retry() -> None:
    execution_id = await _execution(await _published("kb1-slack-ghi"))
    await ENV.run(
        ACTIVITIES.record_execution, RecordExecutionInput(TENANT, execution_id, "running", NOW)
    )
    with pytest.raises(ApplicationError) as caught:
        await ENV.run(
            ACTIVITIES.record_node, RecordNodeInput(TENANT, execution_id, TIM, "succeeded", NOW)
        )
    assert (caught.value.type, caught.value.non_retryable) == ("WorkflowExecutionError", True)


async def test_tenant_khac_khong_ghi_duoc() -> None:
    execution_id = await _execution(await _published("kb1-slack-ghi"))
    with pytest.raises(ApplicationError) as caught:
        await ENV.run(
            ACTIVITIES.record_execution,
            RecordExecutionInput(OTHER_TENANT, execution_id, "running", NOW),
        )
    assert (caught.value.type, caught.value.non_retryable) == ("AuthorizationError", True)


class _Invoker:
    """`ToolInvoker` giả: ghi lại lời gọi, trả output hoặc ném lỗi đã định."""

    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []

    async def invoke(self, **call: Any) -> Any:
        self.calls.append(call)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


_REQUEST = RunToolInput(
    TENANT,
    "exe_1",
    "moi_cau[2].dang",
    "slack.post_message",
    "1.0",
    {"text": "xin chào"},
    node_id="dang",
    idempotency_key="idm_exe_1:moi_cau[2].dang:slack.post_message",
)


async def test_node_tool_goi_gateway_voi_node_va_khoa_cua_no() -> None:
    invoker = _Invoker({"message_ts": "1.0"})
    activities = build_executor_activities(tools=invoker)

    output = await ENV.run(activities.run_tool_node, _REQUEST)

    assert output == {"message_ts": "1.0"}
    assert invoker.calls == [
        {
            "execution_id": "exe_1",
            "node_id": "dang",
            "tool_id": "slack.post_message",
            "tool_version": "1.0",
            "tool_input": {"text": "xin chào"},
            "idempotency_key": "idm_exe_1:moi_cau[2].dang:slack.post_message",
            # `exe_1` không có trace nào đang mở.
            "trace_id": None,
            "parent_step_id": None,
            "timeout_seconds": 60.0,
        }
    ]


async def test_loi_cua_gateway_thanh_loi_temporal_mang_dung_hang_loi() -> None:
    from app.core.errors import OutcomeUnknownError

    activities = build_executor_activities(tools=_Invoker(OutcomeUnknownError("không rõ")))

    with pytest.raises(ApplicationError) as caught:
        await ENV.run(activities.run_tool_node, _REQUEST)

    assert (caught.value.type, caught.value.non_retryable) == ("OutcomeUnknown", True)
