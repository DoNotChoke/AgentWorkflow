"""Use case của Execution trên repository giả trong bộ nhớ."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import AuthorizationError, ErrorCategory, WorkflowExecutionError
from app.domains.execution.application.service import ExecutionService, NodeSeed
from app.domains.execution.domain.entities import (
    Execution,
    ExecutionStatus,
    Failure,
    NodeExecution,
    NodeStatus,
)

T0 = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)
ORG_A = "org_a"
ORG_B = "org_b"

TOP = [
    NodeSeed("t", "t", "trigger"),
    NodeSeed("tim", "tim", "tool"),
    NodeSeed("con", "con", "tool"),
    NodeSeed("moi_trang", "moi_trang", "loop"),
]
DOC = NodeSeed("moi_trang[0].doc_trang", "doc_trang", "tool")


class FakeExecutionRepository:
    def __init__(self) -> None:
        self.executions: dict[str, Execution] = {}
        self.nodes: dict[tuple[str, str], NodeExecution] = {}
        self.writes = 0

    async def add(self, execution: Execution) -> None:
        self.executions[execution.id] = execution

    async def get(
        self, *, tenant_id: str, execution_id: str, for_update: bool = False
    ) -> Execution | None:
        found = self.executions.get(execution_id)
        return found if found is not None and found.tenant_id == tenant_id else None

    async def update(self, execution: Execution) -> None:
        self.executions[execution.id] = execution

    async def list_for_workflow(
        self, *, tenant_id: str, workflow_id: str, limit: int
    ) -> list[Execution]:
        found = [
            e
            for e in self.executions.values()
            if e.tenant_id == tenant_id and e.workflow_id == workflow_id
        ]
        return sorted(found, key=lambda e: e.created_at, reverse=True)[:limit]

    async def seed_nodes(self, nodes: Sequence[NodeExecution]) -> None:
        for node in nodes:
            self.nodes.setdefault((node.execution_id, node.node_path), node)

    async def get_node(
        self, *, tenant_id: str, execution_id: str, node_path: str, for_update: bool = False
    ) -> NodeExecution | None:
        found = self.nodes.get((execution_id, node_path))
        return found if found is not None and found.tenant_id == tenant_id else None

    async def save_node(self, node: NodeExecution) -> None:
        self.writes += 1
        self.nodes[(node.execution_id, node.node_path)] = node

    async def list_nodes(self, *, tenant_id: str, execution_id: str) -> list[NodeExecution]:
        return [
            node
            for (owner, _), node in self.nodes.items()
            if owner == execution_id and node.tenant_id == tenant_id
        ]


@pytest.fixture
def repo() -> FakeExecutionRepository:
    return FakeExecutionRepository()


@pytest.fixture
def service(repo: FakeExecutionRepository) -> ExecutionService:
    return ExecutionService(repository=repo)


async def _started(service: ExecutionService, tenant: str = ORG_A) -> Execution:
    execution = await service.create(
        tenant_id=tenant,
        workspace_id="ws_1",
        workflow_id="wf_1",
        workflow_version_id="wfv_1",
        trigger_type="manual",
        inputs={"ten_trang": "Ghi chú AWS"},
        initiated_by="usr_1",
        at=T0,
    )
    return await service.mark_running(tenant_id=tenant, execution_id=execution.id, at=T0)


async def test_tao_ra_pending_voi_id_exe(service: ExecutionService) -> None:
    execution = await service.create(
        tenant_id=ORG_A,
        workspace_id="ws_1",
        workflow_id="wf_1",
        workflow_version_id="wfv_1",
        trigger_type="manual",
        inputs={},
        initiated_by="usr_1",
        at=T0,
    )
    assert execution.id.startswith("exe_")
    assert execution.status is ExecutionStatus.PENDING


async def test_tenant_khac_khong_thay_lan_chay(service: ExecutionService) -> None:
    execution = await _started(service)
    with pytest.raises(AuthorizationError):
        await service.get(tenant_id=ORG_B, execution_id=execution.id)
    with pytest.raises(AuthorizationError):
        await service.mark_running(tenant_id=ORG_B, execution_id=execution.id, at=T0)


async def test_seed_ra_hang_pending_theo_thu_tu(service: ExecutionService) -> None:
    execution = await _started(service)
    await service.seed_nodes(tenant_id=ORG_A, execution_id=execution.id, nodes=TOP, at=T0)

    progress = await service.get_progress(tenant_id=ORG_A, execution_id=execution.id)
    assert [(n.node_path, n.status) for n in progress.nodes] == [
        (seed.node_path, NodeStatus.PENDING) for seed in TOP
    ]
    assert all(n.id.startswith("nex_") for n in progress.nodes)


async def test_seed_lai_khong_keo_node_da_xong_ve_pending(service: ExecutionService) -> None:
    execution = await _started(service)
    await service.seed_nodes(tenant_id=ORG_A, execution_id=execution.id, nodes=TOP, at=T0)
    await service.record_node(
        tenant_id=ORG_A,
        execution_id=execution.id,
        node=TOP[1],
        status=NodeStatus.RUNNING,
        at=T0,
        attempt=1,
    )
    await service.record_node(
        tenant_id=ORG_A, execution_id=execution.id, node=TOP[1], status=NodeStatus.SUCCEEDED, at=T0
    )

    await service.seed_nodes(tenant_id=ORG_A, execution_id=execution.id, nodes=TOP, at=T0)

    progress = await service.get_progress(tenant_id=ORG_A, execution_id=execution.id)
    assert {n.node_path: n.status for n in progress.nodes}["tim"] is NodeStatus.SUCCEEDED


async def test_record_node_chua_seed_thi_tao_hang(service: ExecutionService) -> None:
    execution = await _started(service)
    node = await service.record_node(
        tenant_id=ORG_A,
        execution_id=execution.id,
        node=DOC,
        status=NodeStatus.RUNNING,
        at=T0,
        attempt=1,
    )
    assert (node.node_path, node.node_id, node.status) == (
        "moi_trang[0].doc_trang",
        "doc_trang",
        NodeStatus.RUNNING,
    )


async def test_ghi_lai_cung_trang_thai_khong_ghi_xuong_db(
    service: ExecutionService, repo: FakeExecutionRepository
) -> None:
    execution = await _started(service)
    for _ in range(3):
        await service.record_node(
            tenant_id=ORG_A,
            execution_id=execution.id,
            node=DOC,
            status=NodeStatus.RUNNING,
            at=T0,
            attempt=1,
        )
    assert repo.writes == 1


async def test_execution_da_ket_thuc_khong_nhan_them_node(service: ExecutionService) -> None:
    execution = await _started(service)
    await service.finish(
        tenant_id=ORG_A,
        execution_id=execution.id,
        status=ExecutionStatus.FAILED,
        at=T0,
        failure=Failure(ErrorCategory.PROVIDER_PERMANENT, "hỏng"),
    )
    with pytest.raises(WorkflowExecutionError, match="đã kết thúc"):
        await service.record_node(
            tenant_id=ORG_A, execution_id=execution.id, node=DOC, status=NodeStatus.RUNNING, at=T0
        )
    with pytest.raises(WorkflowExecutionError, match="đã kết thúc"):
        await service.seed_nodes(tenant_id=ORG_A, execution_id=execution.id, nodes=TOP, at=T0)


async def test_finish_hai_lan_cung_ket_qua_la_khong_doi_gi(service: ExecutionService) -> None:
    execution = await _started(service)
    first = await service.finish(
        tenant_id=ORG_A, execution_id=execution.id, status=ExecutionStatus.SUCCEEDED, at=T0
    )
    again = await service.finish(
        tenant_id=ORG_A,
        execution_id=execution.id,
        status=ExecutionStatus.SUCCEEDED,
        at=T0 + timedelta(seconds=3),
    )
    assert again == first


async def test_liet_ke_moi_nhat_truoc_va_co_tran(
    service: ExecutionService, repo: FakeExecutionRepository
) -> None:
    for minute in range(3):
        execution = await _started(service)
        repo.executions[execution.id] = replace(
            execution, created_at=T0 + timedelta(minutes=minute)
        )
    found = await service.list_for_workflow(tenant_id=ORG_A, workflow_id="wf_1", limit=2)
    assert [e.created_at for e in found] == [T0 + timedelta(minutes=2), T0 + timedelta(minutes=1)]
    assert await service.list_for_workflow(tenant_id=ORG_B, workflow_id="wf_1") == []


# ─── Cửa nguyên thuỷ cho worker_wiring ───────────────────────────────────────


async def test_cua_nguyen_thuy_doc_duoc_chuoi_trang_thai_va_hang_loi(
    service: ExecutionService,
) -> None:
    execution = await _started(service)
    await service.record_node_state(
        tenant_id=ORG_A, execution_id=execution.id, node=DOC, status="running", at=T0, attempt=1
    )
    await service.record_node_state(
        tenant_id=ORG_A,
        execution_id=execution.id,
        node=DOC,
        status="failed",
        at=T0,
        error_category="OutcomeUnknown",
        error_message="Không rõ đã đăng hay chưa.",
    )
    await service.finish_as(
        tenant_id=ORG_A,
        execution_id=execution.id,
        status="failed",
        at=T0,
        error_category="OutcomeUnknown",
        error_message="Không rõ đã đăng hay chưa.",
    )

    progress = await service.get_progress(tenant_id=ORG_A, execution_id=execution.id)
    assert progress.execution.status is ExecutionStatus.FAILED
    assert progress.execution.failure == Failure(
        ErrorCategory.OUTCOME_UNKNOWN, "Không rõ đã đăng hay chưa."
    )
    assert progress.nodes[0].status is NodeStatus.FAILED


@pytest.mark.parametrize(("status", "category"), [("dang_chay", None), ("failed", "LoiKhongCo")])
async def test_chuoi_la_la_loi_cua_lan_chay(
    service: ExecutionService, status: str, category: str | None
) -> None:
    execution = await _started(service)
    with pytest.raises(WorkflowExecutionError):
        await service.record_node_state(
            tenant_id=ORG_A,
            execution_id=execution.id,
            node=DOC,
            status=status,
            at=T0,
            error_category=category,
        )
    with pytest.raises(WorkflowExecutionError):
        await service.finish_as(
            tenant_id=ORG_A,
            execution_id=execution.id,
            status=status,
            at=T0,
            error_category=category,
        )
