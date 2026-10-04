"""Interface mà tầng `infrastructure` hoặc `entrypoints` phải hiện thực hoá."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from app.domains.workflow.domain.entities import Workflow, WorkflowDraft, WorkflowVersion


@dataclass(frozen=True, slots=True)
class ToolContract:
    """Phần của một tool mà validator workflow cần biết."""

    tool_id: str
    version: str
    provider: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    mutates: bool
    #: Tool đòi phê duyệt, hoặc xoá dữ liệu. Không có node `human_approval` ở
    #: v1 nên workflow gọi nó tự động sẽ không bao giờ đi qua được policy.
    needs_approval: bool
    enabled: bool = True


class ToolCatalogReader(Protocol):
    """Tra catalog tool. Đồng bộ: catalog là dữ liệu đã biên dịch vào tiến trình."""

    def find(self, tool_id: str, version: str) -> ToolContract | None:
        """Đúng bản `major.minor`, hoặc `None`."""

    def versions(self, tool_id: str) -> tuple[str, ...]:
        """Mọi bản đang có của `tool_id` — để thông báo lỗi nói được nên dùng bản nào."""


@dataclass(frozen=True, slots=True)
class BoundConnection:
    """Kết nối mà một workflow đang bind, nhìn từ domain `workflow`."""

    connection_id: str
    provider: str
    #: `False` là tài khoản riêng: không làm mặc định của version được, và chỉ
    #: chính chủ chọn được nó cho lần chạy.
    shared_with_workspace: bool
    #: `active`, `expired`, `revoked`… Đưa nguyên vào thông báo lỗi.
    status: str
    is_usable: bool
    owner_user_id: str = ""


class ConnectionReader(Protocol):
    """Đọc metadata của một connection. Hiện thực ở `entrypoints/wiring.py`"""

    async def describe(
        self, *, tenant_id: str, workspace_id: str, connection_id: str
    ) -> BoundConnection | None:
        """`None` khi không tồn tại, hoặc thuộc tenant/workspace khác."""


@dataclass(frozen=True, slots=True)
class NodeRef:
    """Một lần chạy node, nhìn từ bên ghi trạng thái."""

    #: `str(NodePath)`: `tim`, `moi_trang[2].doc_trang`.
    node_path: str
    node_id: str
    node_type: str


class ExecutionRecorder(Protocol):
    """Ghi trạng thái lần chạy. Hiện thực ở `entrypoints/worker_wiring.py`.

    Trạng thái và hạng lỗi là chuỗi: domain này không biết domain `execution`.
    """

    async def mark_running(self, *, tenant_id: str, execution_id: str, at: datetime) -> None: ...

    async def seed_nodes(
        self, *, tenant_id: str, execution_id: str, nodes: Sequence[NodeRef], at: datetime
    ) -> None: ...

    async def record_node(
        self,
        *,
        tenant_id: str,
        execution_id: str,
        node: NodeRef,
        status: str,
        at: datetime,
        attempt: int | None = None,
        error_category: str | None = None,
        error_message: str | None = None,
    ) -> None: ...

    async def finish(
        self,
        *,
        tenant_id: str,
        execution_id: str,
        status: str,
        at: datetime,
        error_category: str | None = None,
        error_message: str | None = None,
    ) -> None: ...


class InvocationContext(Protocol):
    """Tenant của người gọi, đã phân giải từ token và membership."""

    @property
    def user_id(self) -> str: ...

    @property
    def organization_id(self) -> str: ...

    @property
    def workspace_id(self) -> str: ...

    @property
    def roles(self) -> frozenset[str]: ...


class TenantGuard(Protocol):
    """Cùng hình dạng với Protocol tên này ở Connection và Trace. `Protocol` là
    structural typing, nên một adapter duy nhất ở `wiring` thoả cả ba mà không
    domain nào phải biết domain kia tồn tại."""

    async def require_workspace_context(
        self, *, user_id: str, organization_id: str | None, workspace_id: str | None
    ) -> InvocationContext: ...


class WorkflowRepository(Protocol):
    """Bền vững hoá workflow, draft và version."""

    async def add(self, workflow: Workflow) -> None: ...

    async def get(self, *, tenant_id: str, workflow_id: str) -> Workflow | None: ...

    async def list_for_workspace(self, *, tenant_id: str, workspace_id: str) -> list[Workflow]: ...

    async def update(self, workflow: Workflow) -> None: ...

    async def save_draft(self, draft: WorkflowDraft) -> None: ...

    async def get_draft(self, *, tenant_id: str, workflow_id: str) -> WorkflowDraft | None: ...

    async def add_version(self, version: WorkflowVersion) -> None: ...

    async def get_version(
        self, *, tenant_id: str, workflow_id: str, number: int
    ) -> WorkflowVersion | None: ...

    async def get_version_by_id(
        self, *, tenant_id: str, version_id: str
    ) -> WorkflowVersion | None: ...

    async def list_versions(self, *, tenant_id: str, workflow_id: str) -> list[WorkflowVersion]: ...

    async def latest_version_number(self, *, tenant_id: str, workflow_id: str) -> int | None: ...


class TransformRunner(Protocol):
    """Chạy code của một node `transform` trong sandbox. Hiện thực ở tiến trình được phép
    nạp sandbox — Agent Runtime, worker — không bao giờ ở Control Plane (contract 8)."""

    def run(self, code: str, args: Mapping[str, Any]) -> Any:
        """Giá trị `code` trả về. Ném `AppError` khi code không chạy được."""


@dataclass(frozen=True, slots=True)
class DialogueTurn:
    """Một lượt trong cửa sổ chat của phiên biên dịch."""

    role: Literal["user", "assistant"]
    text: str


@dataclass(frozen=True, slots=True)
class IntentReply:
    #: Đề xuất ý đồ ở dạng thô, hoặc `None` khi model chọn hỏi lại.
    proposal: Mapping[str, Any] | None
    text: str = ""


class IntentAdvisor(Protocol):
    """Model hiểu ý đồ. Chỉ thấy bản tóm tắt conversation, không thấy payload."""

    async def propose(self, *, digest: str, dialogue: Sequence[DialogueTurn]) -> IntentReply: ...


@dataclass(frozen=True, slots=True)
class AnswerAttempt:
    """Một lần trả lời đã bị ba tầng kiểm từ chối, kèm lý do theo từng câu hỏi."""

    answers: Mapping[str, Any]
    errors: Sequence[str]


class AnswerAdvisor(Protocol):
    """Model trả lời tập câu hỏi theo ý đồ đã chấp nhận."""

    async def answer(
        self,
        *,
        briefing: str,
        schema: Mapping[str, Any],
        attempts: Sequence[AnswerAttempt],
        comment: str | None = None,
        previous: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        """Câu trả lời thô. Rỗng khi model không nộp gì — tầng kiểm 1 sẽ báo."""
