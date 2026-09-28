"""Repository của domain Workflow, chạy trên PostgreSQL thật.

Chạy thật vì ba thứ đáng kiểm chỉ tồn tại ở tầng database: IR đi qua JSONB rồi
quay về nguyên vẹn, việc lọc theo tenant, và **trigger cấm sửa một version đã
publish** — thứ không một test giả session nào chạm tới.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from conftest import infra_unavailable
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import dispose_engine, init_engine, session_scope
from app.domains.workflow.domain.entities import (
    Workflow,
    WorkflowDraft,
    WorkflowVersion,
    archive,
    fingerprint,
    next_number,
    published,
)
from app.domains.workflow.infrastructure.repositories import SqlAlchemyWorkflowRepository

pytestmark = pytest.mark.integration

TENANT = "org_workflow_repo"
OTHER_TENANT = "org_workflow_repo_khac"
WORKSPACE = "ws_workflow_repo"
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)

IR = {
    "schema_version": "1.0",
    "connections": {"slack": "conn_slackvidu"},
    "nodes": [
        {
            "id": "t",
            "type": "trigger",
            "trigger": "manual",
            "input_schema": {"type": "object", "properties": {"noi_dung": {"type": "string"}}},
        },
        {
            "id": "dang",
            "type": "tool",
            "tool_id": "slack.post_message",
            "tool_version": "1.0",
            "input": {"channel": {"const": "C0VIDU0001"}, "text": {"from_input": "noi_dung"}},
        },
    ],
    "edges": [{"from": "t", "to": "dang"}],
}


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
        ids = [TENANT, OTHER_TENANT]
        # Thứ tự xoá theo FK: version/draft -> workflow.
        await session.execute(
            text("DELETE FROM workflow.workflow_versions WHERE tenant_id = ANY(:ids)"),
            {"ids": ids},
        )
        await session.execute(
            text("DELETE FROM workflow.workflow_drafts WHERE tenant_id = ANY(:ids)"), {"ids": ids}
        )
        await session.execute(
            text("DELETE FROM workflow.workflows WHERE tenant_id = ANY(:ids)"), {"ids": ids}
        )
    await dispose_engine()


def make_workflow(*, tenant: str = TENANT, name: str = "Tổng hợp #support") -> Workflow:
    return Workflow(
        id=f"wf_{uuid.uuid4().hex}",
        tenant_id=tenant,
        workspace_id=WORKSPACE,
        name=name,
        created_by="usr_1",
        created_at=NOW,
        updated_at=NOW,
    )


async def _add(workflow: Workflow) -> None:
    async with session_scope() as session:
        await SqlAlchemyWorkflowRepository(session).add(workflow)


def make_version(workflow: Workflow, *, number: int = 1) -> WorkflowVersion:
    return WorkflowVersion(
        id=f"wfv_{uuid.uuid4().hex}",
        workflow_id=workflow.id,
        tenant_id=workflow.tenant_id,
        number=number,
        ir=IR,
        ir_sha256=fingerprint(IR),
        published_at=NOW,
        published_by="usr_1",
    )


# ─── Workflow ────────────────────────────────────────────────────────────────


async def test_luu_roi_doc_lai_duoc() -> None:
    workflow = make_workflow()
    await _add(workflow)

    async with session_scope() as session:
        found = await SqlAlchemyWorkflowRepository(session).get(
            tenant_id=TENANT, workflow_id=workflow.id
        )

    assert found == workflow


async def test_tenant_khac_khong_doc_duoc() -> None:
    workflow = make_workflow()
    await _add(workflow)

    async with session_scope() as session:
        found = await SqlAlchemyWorkflowRepository(session).get(
            tenant_id=OTHER_TENANT, workflow_id=workflow.id
        )

    assert found is None


async def test_liet_ke_theo_workspace_moi_sua_gan_nhat_truoc() -> None:
    cu = make_workflow(name="Cũ")
    moi = make_workflow(name="Mới")
    await _add(cu)
    await _add(moi)

    async with session_scope() as session:
        await SqlAlchemyWorkflowRepository(session).update(
            replace(moi, updated_at=NOW + timedelta(hours=1))
        )

    async with session_scope() as session:
        found = await SqlAlchemyWorkflowRepository(session).list_for_workspace(
            tenant_id=TENANT, workspace_id=WORKSPACE
        )

    assert [item.name for item in found] == ["Mới", "Cũ"]


async def test_luu_tru_duoc_ghi_lai() -> None:
    workflow = make_workflow()
    await _add(workflow)

    async with session_scope() as session:
        await SqlAlchemyWorkflowRepository(session).update(
            archive(workflow, at=NOW + timedelta(hours=1))
        )

    async with session_scope() as session:
        found = await SqlAlchemyWorkflowRepository(session).get(
            tenant_id=TENANT, workflow_id=workflow.id
        )

    assert found is not None
    assert found.is_archived is True


# ─── Draft ───────────────────────────────────────────────────────────────────


async def test_draft_luu_de_len_chinh_no() -> None:
    """Một workflow có đúng một draft, nên lưu lần hai là ghi đè lần một."""
    workflow = make_workflow()
    await _add(workflow)

    async with session_scope() as session:
        repository = SqlAlchemyWorkflowRepository(session)
        await repository.save_draft(
            WorkflowDraft(
                workflow_id=workflow.id,
                tenant_id=TENANT,
                ir={"schema_version": "1.0", "nodes": [], "edges": []},
                updated_at=NOW,
                updated_by="usr_1",
            )
        )
        await repository.save_draft(
            WorkflowDraft(
                workflow_id=workflow.id,
                tenant_id=TENANT,
                ir=IR,
                updated_at=NOW + timedelta(hours=1),
                updated_by="usr_2",
            )
        )

    async with session_scope() as session:
        draft = await SqlAlchemyWorkflowRepository(session).get_draft(
            tenant_id=TENANT, workflow_id=workflow.id
        )

    assert draft is not None
    assert draft.ir == IR
    assert draft.updated_by == "usr_2"


async def test_draft_giu_nguyen_ir_qua_jsonb() -> None:
    workflow = make_workflow()
    await _add(workflow)

    async with session_scope() as session:
        await SqlAlchemyWorkflowRepository(session).save_draft(
            WorkflowDraft(
                workflow_id=workflow.id,
                tenant_id=TENANT,
                ir=IR,
                updated_at=NOW,
                updated_by="usr_1",
            )
        )

    async with session_scope() as session:
        draft = await SqlAlchemyWorkflowRepository(session).get_draft(
            tenant_id=TENANT, workflow_id=workflow.id
        )

    assert draft is not None
    assert draft.ir == IR


# ─── Version ─────────────────────────────────────────────────────────────────


async def test_publish_hai_lan_thi_so_tang_dan() -> None:
    workflow = make_workflow()
    await _add(workflow)

    async with session_scope() as session:
        repository = SqlAlchemyWorkflowRepository(session)
        assert (
            await repository.latest_version_number(tenant_id=TENANT, workflow_id=workflow.id)
            is None
        )

        first = make_version(workflow, number=next_number(None))
        await repository.add_version(first)
        await repository.update(published(workflow, first, at=NOW))

        latest = await repository.latest_version_number(tenant_id=TENANT, workflow_id=workflow.id)
        second = make_version(workflow, number=next_number(latest))
        await repository.add_version(second)

    async with session_scope() as session:
        versions = await SqlAlchemyWorkflowRepository(session).list_versions(
            tenant_id=TENANT, workflow_id=workflow.id
        )

    assert [version.number for version in versions] == [2, 1]


async def test_doc_version_theo_id_va_theo_so() -> None:
    """Hai đường tra: giao diện đi theo số, execution của GĐ 4 giữ id."""
    workflow = make_workflow()
    await _add(workflow)
    version = make_version(workflow)

    async with session_scope() as session:
        await SqlAlchemyWorkflowRepository(session).add_version(version)

    async with session_scope() as session:
        repository = SqlAlchemyWorkflowRepository(session)
        theo_so = await repository.get_version(tenant_id=TENANT, workflow_id=workflow.id, number=1)
        theo_id = await repository.get_version_by_id(tenant_id=TENANT, version_id=version.id)

    assert theo_so == version
    assert theo_id == version


async def test_hai_version_cung_so_bi_tu_choi() -> None:
    workflow = make_workflow()
    await _add(workflow)

    async with session_scope() as session:
        await SqlAlchemyWorkflowRepository(session).add_version(make_version(workflow, number=1))

    with pytest.raises(Exception, match="uq_workflow_versions_number"):
        async with session_scope() as session:
            await SqlAlchemyWorkflowRepository(session).add_version(
                make_version(workflow, number=1)
            )


async def test_version_da_publish_khong_sua_duoc_bang_sql() -> None:
    """Ràng buộc 2 của §47, canh ở tầng database.

    Repository không có cửa nào để sửa một version; test này đi vòng qua nó
    bằng SQL trực tiếp — đúng thứ sẽ xảy ra lúc 3 giờ sáng — và trigger của
    migration 0013 phải chặn."""
    workflow = make_workflow()
    await _add(workflow)
    version = make_version(workflow)

    async with session_scope() as session:
        await SqlAlchemyWorkflowRepository(session).add_version(version)

    with pytest.raises(Exception, match="bất biến"):
        async with session_scope() as session:
            await session.execute(
                text("UPDATE workflow.workflow_versions SET number = 99 WHERE id = :id"),
                {"id": version.id},
            )

    async with session_scope() as session:
        found = await SqlAlchemyWorkflowRepository(session).get_version_by_id(
            tenant_id=TENANT, version_id=version.id
        )
    assert found == version


async def test_xoa_van_duoc_vi_bat_bien_noi_ve_noi_dung() -> None:
    """Trigger chặn UPDATE chứ không chặn DELETE. Bất biến theo nghĩa §2.3 là
    nội dung không đổi, không phải hàng tồn tại vĩnh viễn — xoá dữ liệu của một
    tenant rời đi là việc hợp lệ. Thứ canh việc xoá nhầm một version đang được
    dùng là khoá ngoại từ `execution.executions`, đến ở GĐ 4."""
    workflow = make_workflow()
    await _add(workflow)
    version = make_version(workflow)

    async with session_scope() as session:
        await SqlAlchemyWorkflowRepository(session).add_version(version)

    async with session_scope() as session:
        await session.execute(
            text("DELETE FROM workflow.workflow_versions WHERE id = :id"), {"id": version.id}
        )

    async with session_scope() as session:
        found = await SqlAlchemyWorkflowRepository(session).get_version_by_id(
            tenant_id=TENANT, version_id=version.id
        )
    assert found is None
