"""Vòng đời một workflow: tạo, sửa draft, publish, lưu trữ (§12).

Hai cửa, hai mức khắt khe (Q14). Lưu draft dễ tính — người đang sửa dở có quyền
lưu một IR chưa hợp lệ, nếu không thì editor không lưu nổi trạng thái giữa
chừng. Publish thì khắt khe: đủ bốn nhóm của §39 mới đóng băng thành version.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final

from app.core.errors import AuthorizationError, ValidationError, WorkflowDefinitionError
from app.core.ids import ResourceKind, new_id
from app.core.logging import get_logger
from app.domains.workflow.application.ports import (
    ConnectionReader,
    ToolCatalogReader,
    WorkflowRepository,
)
from app.domains.workflow.application.validation import validate, validate_connections
from app.domains.workflow.domain.entities import (
    Workflow,
    WorkflowDraft,
    WorkflowVersion,
    archive,
    ensure_can_manage,
    ensure_not_archived,
    fingerprint,
    next_number,
    published,
    unarchive,
)
from app.domains.workflow.domain.ir import parse
from app.domains.workflow.domain.validation import Issue, ValidationReport, ensure_valid

log = get_logger(__name__)

#: Không tìm thấy hoặc không thuộc tenant — cùng thông điệp, cùng mã lỗi, để
#: không lộ rằng id đó có thật. Cùng cách `AgentAdminService` làm.
_WORKFLOW_NOT_FOUND = "Không tìm thấy workflow này."

#: Draft của một workflow vừa tạo: đúng một trigger, chưa có gì khác. Hợp lệ
#: nhưng chưa làm gì — chỗ để người dùng bắt đầu, không phải một ví dụ.
EMPTY_DRAFT: Final[Mapping[str, Any]] = {
    "schema_version": "1.0",
    "nodes": [
        {
            "id": "t",
            "type": "trigger",
            "trigger": "manual",
            "input_schema": {"type": "object", "properties": {}},
        }
    ],
    "edges": [],
}


class WorkflowService:
    def __init__(
        self,
        *,
        repository: WorkflowRepository,
        catalog: ToolCatalogReader,
        connections: ConnectionReader,
    ) -> None:
        self._workflows = repository
        self._catalog = catalog
        self._connections = connections

    # ─── Đọc ─────────────────────────────────────────────────────────────────

    async def list_workflows(self, *, tenant_id: str, workspace_id: str) -> list[Workflow]:
        return await self._workflows.list_for_workspace(
            tenant_id=tenant_id, workspace_id=workspace_id
        )

    async def get_workflow(self, *, tenant_id: str, workflow_id: str) -> Workflow:
        found = await self._workflows.get(tenant_id=tenant_id, workflow_id=workflow_id)
        if found is None:
            raise AuthorizationError(_WORKFLOW_NOT_FOUND, details={"workflow_id": workflow_id})
        return found

    async def get_draft(self, *, tenant_id: str, workflow_id: str) -> WorkflowDraft:
        await self.get_workflow(tenant_id=tenant_id, workflow_id=workflow_id)
        draft = await self._workflows.get_draft(tenant_id=tenant_id, workflow_id=workflow_id)
        if draft is None:
            # Mọi workflow đều được tạo kèm draft, nên thiếu draft là dữ liệu
            # hỏng chứ không phải trạng thái hợp lệ.
            raise ValidationError(
                f"Workflow {workflow_id} không có draft.", details={"workflow_id": workflow_id}
            )
        return draft

    async def list_versions(self, *, tenant_id: str, workflow_id: str) -> list[WorkflowVersion]:
        await self.get_workflow(tenant_id=tenant_id, workflow_id=workflow_id)
        return await self._workflows.list_versions(tenant_id=tenant_id, workflow_id=workflow_id)

    async def get_version(
        self, *, tenant_id: str, workflow_id: str, number: int
    ) -> WorkflowVersion:
        await self.get_workflow(tenant_id=tenant_id, workflow_id=workflow_id)
        found = await self._workflows.get_version(
            tenant_id=tenant_id, workflow_id=workflow_id, number=number
        )
        if found is None:
            # Người gọi đã chứng minh được quyền đọc workflow ở câu trên, nên ở
            # đây không cần giấu sự tồn tại nữa.
            raise ValidationError(
                f"Workflow này không có version {number}.",
                details={"workflow_id": workflow_id, "number": number},
            )
        return found

    # ─── Sửa ─────────────────────────────────────────────────────────────────

    async def create(
        self, *, tenant_id: str, workspace_id: str, name: str, actor_id: str
    ) -> Workflow:
        at = datetime.now(UTC)
        workflow = Workflow(
            id=new_id(ResourceKind.WORKFLOW),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            name=name,
            created_by=actor_id,
            created_at=at,
            updated_at=at,
        )
        await self._workflows.add(workflow)
        await self._workflows.save_draft(
            WorkflowDraft(
                workflow_id=workflow.id,
                tenant_id=tenant_id,
                ir=EMPTY_DRAFT,
                updated_at=at,
                updated_by=actor_id,
            )
        )
        log.info("workflow_created", workflow_id=workflow.id, workspace_id=workspace_id)
        return workflow

    async def save_draft(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        ir: Mapping[str, Any],
        actor_id: str,
        can_manage_workspace: bool,
    ) -> tuple[WorkflowDraft, ValidationReport]:
        """Lưu rồi mới soi. Trả kèm báo cáo để editor tô đúng chỗ sai."""
        workflow = await self.get_workflow(tenant_id=tenant_id, workflow_id=workflow_id)
        ensure_not_archived(workflow)
        ensure_can_manage(workflow, actor_id=actor_id, can_manage_workspace=can_manage_workspace)

        at = datetime.now(UTC)
        draft = WorkflowDraft(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            ir=ir,
            updated_at=at,
            updated_by=actor_id,
        )
        await self._workflows.save_draft(draft)
        return draft, self.inspect(ir)

    def inspect(self, ir: Mapping[str, Any]) -> ValidationReport:
        """Báo cáo về một IR, **không bao giờ ném**.

        `parse` ném khi tài liệu sai hình dạng; ở đây lỗi đó thành một issue như
        mọi issue khác, vì một draft sai hình dạng vẫn phải lưu được và vẫn phải
        nói được nó sai ở đâu.
        """
        try:
            parsed = parse(ir)
        except WorkflowDefinitionError as exc:
            return ValidationReport((Issue("khong_doc_duoc", exc.message),))
        return validate(parsed, self._catalog)

    # ─── Publish ─────────────────────────────────────────────────────────────

    async def publish(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        workflow_id: str,
        actor_id: str,
        can_manage_workspace: bool,
    ) -> WorkflowVersion:
        workflow = await self.get_workflow(tenant_id=tenant_id, workflow_id=workflow_id)
        ensure_not_archived(workflow)
        ensure_can_manage(workflow, actor_id=actor_id, can_manage_workspace=can_manage_workspace)
        draft = await self.get_draft(tenant_id=tenant_id, workflow_id=workflow_id)

        # Ở đây `parse` được phép ném: publish là cửa khắt khe.
        parsed = parse(draft.ir)
        report = validate(parsed, self._catalog).merged(
            await validate_connections(
                parsed, self._connections, tenant_id=tenant_id, workspace_id=workspace_id
            )
        )
        ensure_valid(report)

        at = datetime.now(UTC)
        latest = await self._workflows.latest_version_number(
            tenant_id=tenant_id, workflow_id=workflow_id
        )
        version = WorkflowVersion(
            id=new_id(ResourceKind.WORKFLOW_VERSION),
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            number=next_number(latest),
            ir=draft.ir,
            ir_sha256=fingerprint(draft.ir),
            published_at=at,
            published_by=actor_id,
        )
        await self._workflows.add_version(version)
        await self._workflows.update(published(workflow, version, at=at))
        log.info(
            "workflow_published",
            workflow_id=workflow_id,
            version_id=version.id,
            number=version.number,
        )
        return version

    # ─── Lưu trữ ─────────────────────────────────────────────────────────────

    async def archive_workflow(
        self, *, tenant_id: str, workflow_id: str, actor_id: str, can_manage_workspace: bool
    ) -> Workflow:
        workflow = await self.get_workflow(tenant_id=tenant_id, workflow_id=workflow_id)
        ensure_can_manage(workflow, actor_id=actor_id, can_manage_workspace=can_manage_workspace)
        archived = archive(workflow, at=datetime.now(UTC))
        await self._workflows.update(archived)
        return archived

    async def unarchive_workflow(
        self, *, tenant_id: str, workflow_id: str, actor_id: str, can_manage_workspace: bool
    ) -> Workflow:
        workflow = await self.get_workflow(tenant_id=tenant_id, workflow_id=workflow_id)
        ensure_can_manage(workflow, actor_id=actor_id, can_manage_workspace=can_manage_workspace)
        restored = unarchive(workflow, at=datetime.now(UTC))
        await self._workflows.update(restored)
        return restored
