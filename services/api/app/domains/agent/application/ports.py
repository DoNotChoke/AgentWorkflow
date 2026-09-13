"""Model Gateway: cổng chung cho mọi provider LLM"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Protocol

from app.domains.agent.domain.entities import Agent, Conversation, Message

TurnRole = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class ModelToolSpec:
    """Định nghĩa một tool để model biết mà gọi — dịch từ `ToolSummary` của
    Integration (`GET /internal/v1/tools`), không phải từ `ToolSpec` của nó."""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    #: Version trong catalog — đủ để tra ngược đúng schema đã gửi cho model.
    version: str


@dataclass(frozen=True, slots=True)
class ModelToolCall:
    """Quyết định gọi tool của model, ở hình dạng chung — dịch được thành
    `ToolCallRequest` của domain `agent` bởi tầng gọi nó."""

    call_id: str
    name: str
    input: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ModelToolResult:
    """Kết quả một lần gọi tool, gửi ngược lại cho model ở lượt kế tiếp."""

    call_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class ModelTurn:
    """Một lượt trong lịch sử hội thoại gửi cho model.

    Không map thẳng 1-1 vào `Message` của domain `agent`: một lượt `TOOL` của
    domain `agent` (một `Message` riêng) gộp vào `tool_results` của lượt
    `user` kế tiếp ở đây — đúng hình dạng OpenAI/Anthropic đều muốn nhận.
    """

    role: TurnRole
    text: str = ""
    tool_calls: tuple[ModelToolCall, ...] = ()
    tool_results: tuple[ModelToolResult, ...] = ()


class StopReason(StrEnum):
    """Chuẩn hoá `stop_reason` (Anthropic) / `finish_reason` (OpenAI)."""

    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class ModelResponse:
    text: str
    tool_calls: tuple[ModelToolCall, ...]
    stop_reason: StopReason
    usage: ModelUsage
    #: Model thật đã trả lời — có thể khác `model` đã yêu cầu nếu provider có
    #: cơ chế fallback riêng. Ghi lại để Trace/observability biết đúng sự thật.
    model: str = ""


class ModelClient(Protocol):
    """Cổng chung cho mọi provider LLM."""

    async def complete(
        self,
        *,
        model: str,
        system: str,
        turns: Sequence[ModelTurn],
        tools: Sequence[ModelToolSpec] = (),
        max_tokens: int = 8192,
    ) -> ModelResponse: ...


@dataclass(frozen=True, slots=True)
class ToolCallContext:
    """Ai/cái gì đang khởi xướng lời gọi tool — để Integration đính đúng chỗ
    vào Execution Trace
    """

    agent_id: str | None = None
    workflow_id: str | None = None
    execution_id: str | None = None
    node_id: str | None = None
    execution_trace_id: str | None = None
    parent_step_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolInvocationResult:
    """Kết quả một lần gọi tool qua Integration Gateway."""

    output: Mapping[str, Any]
    invocation_id: str
    replayed: bool


class ToolCatalog(Protocol):
    """Danh mục tool mà Agent gọi được."""

    async def list_tools(
        self,
        *,
        authorization: str,
        organization_id: str | None,
        workspace_id: str | None,
        allowed_tool_ids: frozenset[str],
    ) -> Sequence[ModelToolSpec]: ...


class ToolExecutor(Protocol):
    """Thực thi một lời gọi tool mà model vừa quyết định."""

    async def invoke(
        self,
        *,
        name: str,
        tool_input: Mapping[str, Any],
        connection_id: str,
        authorization: str,
        organization_id: str | None,
        workspace_id: str | None,
        idempotency_key: str | None = None,
        caller: ToolCallContext | None = None,
    ) -> ToolInvocationResult: ...


class ConnectionResolver(Protocol):
    """Chọn `connection_id` nào dùng cho một lời gọi tool.

    `AgentRuntime` (`service.py`) chỉ biết `tool_id`, không biết người dùng có
    những kết nối nào.
    """

    async def resolve(
        self,
        *,
        tool_id: str,
        authorization: str,
        organization_id: str | None,
        workspace_id: str | None,
    ) -> str: ...


class ConnectionSelectionStore(Protocol):
    """Lựa chọn `connection_id` đã chốt cho một `(conversation_id, provider)`."""

    async def get(self, *, conversation_id: str, provider: str) -> str | None: ...

    async def set(self, *, conversation_id: str, provider: str, connection_id: str) -> None: ...

    async def list_for_conversation(self, *, conversation_id: str) -> dict[str, str]:
        """`{provider: connection_id}` đã chốt cho một conversation — để giao
        diện chat hiện "đang dùng connection nào" khi mở lại một hội thoại cũ."""


class ExecutionTraceSink(Protocol):
    """Ghi các bước của một lượt Agent vào Execution Trace đang mở."""

    async def append_step(
        self,
        *,
        kind: str,
        status: str,
        started_at: datetime,
        completed_at: datetime,
        attributes: Mapping[str, Any] | None = None,
        error_category: str | None = None,
        input_payload: Mapping[str, Any] | None = None,
        output_payload: Mapping[str, Any] | None = None,
        step_id: str | None = None,
    ) -> None: ...


class AgentEventKind(StrEnum):
    """Sự kiện đủ để một endpoint SSE (WP10) vẽ tiến độ một lượt Agent.

    Không có sự kiện cấp token: `ModelClient.complete()` (WP5) trả về nguyên
    một `ModelResponse`, không streaming — cấp độ chi tiết tối đa hiện tại là
    "một tool bắt đầu/xong" và "câu trả lời cuối cùng đã có". Thêm streaming
    cấp token là việc của Model Gateway (WP5), không phải endpoint HTTP.
    """

    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    FINAL = "final"


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """Một sự kiện tiến độ trong lúc `run_turn()` đang chạy."""

    kind: AgentEventKind
    tool_id: str | None = None
    is_error: bool = False
    text: str | None = None
    #: Cấu trúc của lỗi khi `is_error` — tổng quát cho MỌI lỗi tool, không
    #: riêng lỗi connection (ADR-0004), để FE tự quyết định vẽ gì tuỳ
    #: `error_category` thay vì chỉ có một dòng chữ để hiển thị.
    error_category: str | None = None
    error_details: Mapping[str, Any] | None = None


class AgentEventSink(Protocol):
    """Nhận sự kiện tiến độ theo thời gian thực — khác `ExecutionTraceSink`
    (ghi lại vĩnh viễn, không quan tâm ai đang xem). Endpoint SSE (WP10) hiện
    thực Protocol này để đẩy sự kiện ra ngay khi `run_turn()` phát sinh,
    không đợi tới khi cả lượt kết thúc."""

    async def emit(self, event: AgentEvent) -> None: ...


class UnknownModelError(Exception):
    """`model` không có trong catalog — không biết gửi cho provider nào."""

    def __init__(self, model: str, known_models: Sequence[str] = ()) -> None:
        self.model = model
        self.known_models = tuple(known_models)
        available = ", ".join(self.known_models) or "(rỗng)"
        super().__init__(f"Không nhận diện được model {model!r}. Các model đã biết: {available}.")


# ─────────────────────────────────────────────────────────────────────────────
# Bền vững hoá (WP8) — interface mà `infrastructure` phải hiện thực hoá.
# ─────────────────────────────────────────────────────────────────────────────


class AgentRepository(Protocol):
    async def add(self, agent: Agent) -> None: ...

    async def get(self, *, tenant_id: str, agent_id: str) -> Agent | None: ...

    async def list_for_workspace(self, *, tenant_id: str, workspace_id: str) -> list[Agent]: ...

    async def update(self, agent: Agent) -> None:
        """Ghi lại một `Agent` đã đổi trạng thái (ví dụ vừa được lưu trữ)."""


class ConversationRepository(Protocol):
    async def add(self, conversation: Conversation) -> None: ...

    async def get(self, *, tenant_id: str, conversation_id: str) -> Conversation | None: ...

    async def list_for_user(
        self, *, tenant_id: str, agent_id: str, user_id: str
    ) -> list[Conversation]:
        """Lịch sử hội thoại của một người dùng với một agent, mới nhất trước."""

    async def update(self, conversation: Conversation) -> None:
        """Ghi lại một `Conversation` đã đổi trạng thái (ví dụ vừa lưu trữ)."""


class MessageRepository(Protocol):
    async def add_many(self, messages: Sequence[Message]) -> None:
        """Lưu toàn bộ `TurnResult.messages` của một lượt trong một lần gọi."""

    async def list_for_conversation(self, *, tenant_id: str, conversation_id: str) -> list[Message]:
        """Lịch sử đầy đủ, theo thứ tự thời gian — đưa thẳng vào `AgentRuntime.run_turn`."""
