"""Workflow, draft, và version — vòng đời."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Final

from app.core.errors import AuthorizationError, ValidationError

#: Version đầu tiên của một workflow. Đánh số từ 1 vì nó là thứ người dùng đọc.
FIRST_VERSION: Final = 1


@dataclass(frozen=True, slots=True)
class Workflow:
    """Định danh ổn định. Nội dung nằm ở draft và các version."""

    id: str
    tenant_id: str
    workspace_id: str
    name: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    #: Version mới nhất đã publish. `None` nghĩa là chưa publish lần nào.
    current_version_id: str | None = None
    archived_at: datetime | None = None

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    @property
    def is_published(self) -> bool:
        return self.current_version_id is not None


@dataclass(frozen=True, slots=True)
class WorkflowDraft:
    """Bản đang sửa. **Được phép chưa hợp lệ**"""

    workflow_id: str
    tenant_id: str
    ir: Mapping[str, Any]
    updated_at: datetime
    updated_by: str


@dataclass(frozen=True, slots=True)
class WorkflowVersion:
    """Một bản đã publish. Bất biến kể từ lúc tồn tại.

    `ir_sha256` để giao diện nói được "giống hệt v3" mà không phải so cả cây —
    publish không khử trùng lặp, vì khử âm thầm làm người dùng tưởng nó hỏng.
    """

    id: str
    workflow_id: str
    tenant_id: str
    number: int
    ir: Mapping[str, Any]
    ir_sha256: str
    published_at: datetime
    published_by: str


def canonical_bytes(ir: Mapping[str, Any]) -> bytes:
    """Byte chuẩn của một IR — thứ được băm."""
    return json.dumps(ir, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def fingerprint(ir: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(ir)).hexdigest()


def ensure_can_manage(workflow: Workflow, *, actor_id: str, can_manage_workspace: bool) -> None:
    """Sửa draft, publish, lưu trữ — người tạo hoặc quản trị viên workspace."""
    if workflow.created_by == actor_id or can_manage_workspace:
        return
    raise AuthorizationError(
        "Bạn không có quyền quản lý workflow này.", details={"workflow_id": workflow.id}
    )


def ensure_not_archived(workflow: Workflow) -> None:
    if workflow.is_archived:
        raise ValidationError(
            f"Workflow {workflow.id} đã được lưu trữ.", details={"workflow_id": workflow.id}
        )


def archive(workflow: Workflow, *, at: datetime) -> Workflow:
    if workflow.is_archived:
        raise ValidationError(f"Workflow {workflow.id} đã được lưu trữ rồi.")
    return replace(workflow, archived_at=at, updated_at=at)


def unarchive(workflow: Workflow, *, at: datetime) -> Workflow:
    """Lưu trữ là mềm và đảo ngược được — cùng hình dạng với Agent."""
    if not workflow.is_archived:
        raise ValidationError(f"Workflow {workflow.id} không ở trạng thái lưu trữ.")
    return replace(workflow, archived_at=None, updated_at=at)


def next_number(published: int | None) -> int:
    """Số của version kế tiếp, từ số lớn nhất đang có."""
    return FIRST_VERSION if published is None else published + 1


def published(workflow: Workflow, version: WorkflowVersion, *, at: datetime) -> Workflow:
    """Workflow sau khi một version vừa được publish."""
    if version.workflow_id != workflow.id:
        raise ValidationError(
            f"Version {version.id} không thuộc workflow {workflow.id}.",
            details={"workflow_id": workflow.id, "version_id": version.id},
        )
    return replace(workflow, current_version_id=version.id, updated_at=at)
