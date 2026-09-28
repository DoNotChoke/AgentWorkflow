"""Hiện thực hoá repository của domain Workflow trên PostgreSQL."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.workflow.domain.entities import Workflow, WorkflowDraft, WorkflowVersion
from app.domains.workflow.infrastructure.models import (
    WorkflowDraftModel,
    WorkflowModel,
    WorkflowVersionModel,
)


def _to_workflow(row: WorkflowModel) -> Workflow:
    return Workflow(
        id=row.id,
        tenant_id=row.tenant_id,
        workspace_id=row.workspace_id,
        name=row.name,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        current_version_id=row.current_version_id,
        archived_at=row.archived_at,
    )


def _to_draft(row: WorkflowDraftModel) -> WorkflowDraft:
    return WorkflowDraft(
        workflow_id=row.workflow_id,
        tenant_id=row.tenant_id,
        ir=row.ir,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
    )


def _to_version(row: WorkflowVersionModel) -> WorkflowVersion:
    return WorkflowVersion(
        id=row.id,
        workflow_id=row.workflow_id,
        tenant_id=row.tenant_id,
        number=row.number,
        ir=row.ir,
        ir_sha256=row.ir_sha256,
        published_at=row.published_at,
        published_by=row.published_by,
    )


class SqlAlchemyWorkflowRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ─── Workflow ────────────────────────────────────────────────────────────

    async def add(self, workflow: Workflow) -> None:
        self._session.add(
            WorkflowModel(
                id=workflow.id,
                tenant_id=workflow.tenant_id,
                workspace_id=workflow.workspace_id,
                name=workflow.name,
                created_by=workflow.created_by,
                current_version_id=workflow.current_version_id,
                archived_at=workflow.archived_at,
                created_at=workflow.created_at,
                updated_at=workflow.updated_at,
            )
        )
        await self._session.flush()

    async def get(self, *, tenant_id: str, workflow_id: str) -> Workflow | None:
        row = await self._session.scalar(
            select(WorkflowModel).where(
                WorkflowModel.tenant_id == tenant_id, WorkflowModel.id == workflow_id
            )
        )
        return None if row is None else _to_workflow(row)

    async def list_for_workspace(self, *, tenant_id: str, workspace_id: str) -> list[Workflow]:
        """Workflow của một workspace, mới sửa gần đây nhất trước."""
        rows = await self._session.scalars(
            select(WorkflowModel)
            .where(
                WorkflowModel.tenant_id == tenant_id,
                WorkflowModel.workspace_id == workspace_id,
            )
            .order_by(WorkflowModel.updated_at.desc())
        )
        return [_to_workflow(row) for row in rows]

    async def update(self, workflow: Workflow) -> None:
        """Ghi lại một `Workflow` đã đổi — đổi tên, publish, lưu trữ."""
        await self._session.execute(
            update(WorkflowModel)
            .where(WorkflowModel.tenant_id == workflow.tenant_id, WorkflowModel.id == workflow.id)
            .values(
                name=workflow.name,
                current_version_id=workflow.current_version_id,
                archived_at=workflow.archived_at,
                updated_at=workflow.updated_at,
            )
        )
        await self._session.flush()

    # ─── Draft ───────────────────────────────────────────────────────────────

    async def save_draft(self, draft: WorkflowDraft) -> None:
        """Một workflow có đúng một draft, nên lưu là ghi đè."""
        statement = pg_insert(WorkflowDraftModel).values(
            workflow_id=draft.workflow_id,
            tenant_id=draft.tenant_id,
            ir=dict(draft.ir),
            updated_by=draft.updated_by,
            updated_at=draft.updated_at,
        )
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=["workflow_id"],
                set_={
                    "ir": statement.excluded.ir,
                    "updated_by": statement.excluded.updated_by,
                    "updated_at": statement.excluded.updated_at,
                },
            )
        )
        await self._session.flush()

    async def get_draft(self, *, tenant_id: str, workflow_id: str) -> WorkflowDraft | None:
        row = await self._session.scalar(
            select(WorkflowDraftModel).where(
                WorkflowDraftModel.tenant_id == tenant_id,
                WorkflowDraftModel.workflow_id == workflow_id,
            )
        )
        return None if row is None else _to_draft(row)

    # ─── Version ─────────────────────────────────────────────────────────────

    async def add_version(self, version: WorkflowVersion) -> None:
        self._session.add(
            WorkflowVersionModel(
                id=version.id,
                workflow_id=version.workflow_id,
                tenant_id=version.tenant_id,
                number=version.number,
                ir=dict(version.ir),
                ir_sha256=version.ir_sha256,
                published_by=version.published_by,
                published_at=version.published_at,
            )
        )
        await self._session.flush()

    async def get_version(
        self, *, tenant_id: str, workflow_id: str, number: int
    ) -> WorkflowVersion | None:
        row = await self._session.scalar(
            select(WorkflowVersionModel).where(
                WorkflowVersionModel.tenant_id == tenant_id,
                WorkflowVersionModel.workflow_id == workflow_id,
                WorkflowVersionModel.number == number,
            )
        )
        return None if row is None else _to_version(row)

    async def get_version_by_id(self, *, tenant_id: str, version_id: str) -> WorkflowVersion | None:
        """Đường mà execution sẽ dùng ở GĐ 4: nó giữ `workflow_version_id`."""
        row = await self._session.scalar(
            select(WorkflowVersionModel).where(
                WorkflowVersionModel.tenant_id == tenant_id,
                WorkflowVersionModel.id == version_id,
            )
        )
        return None if row is None else _to_version(row)

    async def list_versions(self, *, tenant_id: str, workflow_id: str) -> list[WorkflowVersion]:
        """Mọi version của một workflow, mới nhất trước."""
        rows = await self._session.scalars(
            select(WorkflowVersionModel)
            .where(
                WorkflowVersionModel.tenant_id == tenant_id,
                WorkflowVersionModel.workflow_id == workflow_id,
            )
            .order_by(WorkflowVersionModel.number.desc())
        )
        return [_to_version(row) for row in rows]

    async def latest_version_number(self, *, tenant_id: str, workflow_id: str) -> int | None:
        """Số lớn nhất đang có, hoặc `None` khi chưa publish lần nào."""
        found: Any = await self._session.scalar(
            select(func.max(WorkflowVersionModel.number)).where(
                WorkflowVersionModel.tenant_id == tenant_id,
                WorkflowVersionModel.workflow_id == workflow_id,
            )
        )
        return None if found is None else int(found)
