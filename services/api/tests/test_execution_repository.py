"""Repository của domain Execution, chạy trên PostgreSQL thật.

Ba thứ chỉ database kiểm được: upsert theo `(execution_id, node_path)`, thứ tự
chèn của `seq`, và khoá ngoại giữ version còn được trỏ tới.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from conftest import infra_unavailable
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.db import dispose_engine, init_engine, session_scope
from app.core.errors import ErrorCategory
from app.domains.execution.domain.entities import (
    Execution,
    ExecutionStatus,
    Failure,
    NodeExecution,
    NodeStatus,
)
from app.domains.execution.infrastructure.repositories import SqlAlchemyExecutionRepository
from app.domains.workflow.domain.entities import Workflow, WorkflowVersion, fingerprint
from app.domains.workflow.infrastructure.repositories import SqlAlchemyWorkflowRepository

pytestmark = pytest.mark.integration

TENANT = "org_execution_repo"
OTHER_TENANT = "org_execution_repo_khac"
WORKSPACE = "ws_execution_repo"
NOW = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)
IR = {"schema_version": "1.0", "nodes": [], "edges": []}


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
        # node_executions đi theo CASCADE; version phải xoá sau execution (RESTRICT).
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


async def _version(tenant: str = TENANT) -> WorkflowVersion:
    workflow = Workflow(
        id=f"wf_{uuid.uuid4().hex}",
        tenant_id=tenant,
        workspace_id=WORKSPACE,
        name="Đọc trang con",
        created_by="usr_1",
        created_at=NOW,
        updated_at=NOW,
    )
    version = WorkflowVersion(
        id=f"wfv_{uuid.uuid4().hex}",
        workflow_id=workflow.id,
        tenant_id=tenant,
        number=1,
        ir=IR,
        ir_sha256=fingerprint(IR),
        published_at=NOW,
        published_by="usr_1",
    )
    async with session_scope() as session:
        workflows = SqlAlchemyWorkflowRepository(session)
        await workflows.add(workflow)
        await workflows.add_version(version)
    return version


def _execution(version: WorkflowVersion, **changes: object) -> Execution:
    execution = Execution(
        id=f"exe_{uuid.uuid4().hex}",
        tenant_id=version.tenant_id,
        workspace_id=WORKSPACE,
        workflow_id=version.workflow_id,
        workflow_version_id=version.id,
        trigger_type="manual",
        inputs={"ten_trang": "Ghi chú AWS", "so": [1, 2]},
        initiated_by="usr_1",
        connection_bindings={"notion": "conn_notionvidu"},
        status=ExecutionStatus.PENDING,
        created_at=NOW,
        updated_at=NOW,
    )
    return replace(execution, **changes)  # type: ignore[arg-type]


def _node(execution: Execution, path: str, **changes: object) -> NodeExecution:
    node = NodeExecution(
        id=f"nex_{uuid.uuid4().hex}",
        execution_id=execution.id,
        tenant_id=execution.tenant_id,
        node_path=path,
        node_id=path.rsplit(".", 1)[-1],
        node_type="tool",
        status=NodeStatus.PENDING,
        updated_at=NOW,
    )
    return replace(node, **changes)  # type: ignore[arg-type]


async def _add(execution: Execution) -> None:
    async with session_scope() as session:
        await SqlAlchemyExecutionRepository(session).add(execution)


# ─── Execution ───────────────────────────────────────────────────────────────


async def test_luu_roi_doc_lai_nguyen_ven() -> None:
    execution = _execution(await _version())
    await _add(execution)

    async with session_scope() as session:
        found = await SqlAlchemyExecutionRepository(session).get(
            tenant_id=TENANT, execution_id=execution.id
        )
    assert found == execution


async def test_cap_nhat_trang_thai_va_loi() -> None:
    execution = _execution(await _version())
    await _add(execution)
    failed = replace(
        execution,
        status=ExecutionStatus.FAILED,
        updated_at=NOW + timedelta(seconds=4),
        finished_at=NOW + timedelta(seconds=4),
        failure=Failure(ErrorCategory.PROVIDER_PERMANENT, "Notion trả 404"),
    )

    async with session_scope() as session:
        await SqlAlchemyExecutionRepository(session).update(failed)
    async with session_scope() as session:
        found = await SqlAlchemyExecutionRepository(session).get(
            tenant_id=TENANT, execution_id=execution.id, for_update=True
        )
    assert found == failed


async def test_tenant_khac_khong_doc_duoc() -> None:
    execution = _execution(await _version())
    await _add(execution)

    async with session_scope() as session:
        repo = SqlAlchemyExecutionRepository(session)
        assert await repo.get(tenant_id=OTHER_TENANT, execution_id=execution.id) is None
        assert (
            await repo.list_for_workflow(
                tenant_id=OTHER_TENANT, workflow_id=execution.workflow_id, limit=10
            )
            == []
        )
        assert await repo.list_nodes(tenant_id=OTHER_TENANT, execution_id=execution.id) == []


async def test_liet_ke_moi_nhat_truoc() -> None:
    version = await _version()
    older = _execution(version)
    newer = _execution(version, created_at=NOW + timedelta(minutes=1))
    await _add(older)
    await _add(newer)

    async with session_scope() as session:
        found = await SqlAlchemyExecutionRepository(session).list_for_workflow(
            tenant_id=TENANT, workflow_id=version.workflow_id, limit=10
        )
    assert [e.id for e in found] == [newer.id, older.id]


# ─── Khoá ngoại ──────────────────────────────────────────────────────────────


async def test_khong_xoa_duoc_version_con_lan_chay_tro_toi() -> None:
    version = await _version()
    await _add(_execution(version))

    with pytest.raises(IntegrityError):
        async with session_scope() as session:
            await session.execute(
                text("DELETE FROM workflow.workflow_versions WHERE id = :id"), {"id": version.id}
            )


async def test_khong_tao_duoc_lan_chay_cho_version_khong_ton_tai() -> None:
    execution = replace(_execution(await _version()), workflow_version_id="wfv_khongco")
    with pytest.raises(IntegrityError):
        await _add(execution)


async def test_xoa_lan_chay_thi_xoa_theo_hang_node() -> None:
    execution = _execution(await _version())
    await _add(execution)
    async with session_scope() as session:
        await SqlAlchemyExecutionRepository(session).seed_nodes([_node(execution, "tim")])
    async with session_scope() as session:
        await session.execute(
            text("DELETE FROM execution.executions WHERE id = :id"), {"id": execution.id}
        )
        remaining = await session.scalar(
            text("SELECT count(*) FROM execution.node_executions WHERE execution_id = :id"),
            {"id": execution.id},
        )
    assert remaining == 0


# ─── Node ────────────────────────────────────────────────────────────────────


async def test_seed_hai_lan_van_mot_hang_va_giu_hang_cu() -> None:
    execution = _execution(await _version())
    await _add(execution)
    done = _node(execution, "tim", status=NodeStatus.SUCCEEDED, attempt=1)

    async with session_scope() as session:
        await SqlAlchemyExecutionRepository(session).save_node(done)
    async with session_scope() as session:
        await SqlAlchemyExecutionRepository(session).seed_nodes([_node(execution, "tim")])
    async with session_scope() as session:
        nodes = await SqlAlchemyExecutionRepository(session).list_nodes(
            tenant_id=TENANT, execution_id=execution.id
        )
    assert nodes == [done]


async def test_save_node_ghi_de_theo_duong() -> None:
    execution = _execution(await _version())
    await _add(execution)
    pending = _node(execution, "moi_trang[1].doc_trang")
    failed = replace(
        pending,
        status=NodeStatus.FAILED,
        attempt=3,
        updated_at=NOW + timedelta(seconds=2),
        started_at=NOW + timedelta(seconds=1),
        finished_at=NOW + timedelta(seconds=2),
        failure=Failure(ErrorCategory.OUTCOME_UNKNOWN, "Không rõ đã đăng hay chưa."),
    )

    async with session_scope() as session:
        await SqlAlchemyExecutionRepository(session).seed_nodes([pending])
    async with session_scope() as session:
        await SqlAlchemyExecutionRepository(session).save_node(failed)
    async with session_scope() as session:
        repo = SqlAlchemyExecutionRepository(session)
        found = await repo.get_node(
            tenant_id=TENANT,
            execution_id=execution.id,
            node_path="moi_trang[1].doc_trang",
            for_update=True,
        )
        count = len(await repo.list_nodes(tenant_id=TENANT, execution_id=execution.id))
    assert found == failed
    assert count == 1


async def test_tien_trinh_doc_theo_thu_tu_chen_khong_theo_ten() -> None:
    execution = _execution(await _version())
    await _add(execution)
    order = ["t", "tim", "con", "moi_trang"]
    async with session_scope() as session:
        await SqlAlchemyExecutionRepository(session).seed_nodes(
            [_node(execution, path) for path in order]
        )
    async with session_scope() as session:
        await SqlAlchemyExecutionRepository(session).seed_nodes(
            [_node(execution, f"moi_trang[{i}].doc_trang") for i in range(3)]
        )
    async with session_scope() as session:
        nodes = await SqlAlchemyExecutionRepository(session).list_nodes(
            tenant_id=TENANT, execution_id=execution.id
        )
    assert [n.node_path for n in nodes] == [
        *order,
        "moi_trang[0].doc_trang",
        "moi_trang[1].doc_trang",
        "moi_trang[2].doc_trang",
    ]


async def test_seed_rong_khong_lam_gi() -> None:
    async with session_scope() as session:
        await SqlAlchemyExecutionRepository(session).seed_nodes([])
