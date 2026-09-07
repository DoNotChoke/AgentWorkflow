"""Thực thể và quy tắc nghiệp vụ của Agent"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.core.errors import AuthorizationError, ValidationError

# ─────────────────────────────────────────────────────────────────────────────
# Agent — một trợ lý đã cấu hình
# ─────────────────────────────────────────────────────────────────────────────

MAX_NAME_LENGTH = 200
MAX_SYSTEM_PROMPT_LENGTH = 20_000

DEFAULT_MAX_TOOL_CALLS_PER_TURN = 8
MIN_MAX_TOOL_CALLS_PER_TURN = 1

MAX_MAX_TOOL_CALLS_PER_TURN = 50


@dataclass(frozen=True, slots=True)
class Agent:
    """Một trợ lý đã cấu hình: prompt hệ thống, model, và các tool được phép dùng."""

    id: str
    tenant_id: str
    workspace_id: str
    name: str
    system_prompt: str
    model: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    #: Whitelist tường minh. Rỗng nghĩa là agent này không gọi được tool nào
    enabled_tool_ids: frozenset[str] = frozenset()
    max_tool_calls_per_turn: int = DEFAULT_MAX_TOOL_CALLS_PER_TURN
    archived_at: datetime | None = None

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    def allows_tool(self, tool_id: str) -> bool:
        return tool_id in self.enabled_tool_ids


def normalize_name(raw: str) -> str:
    name = " ".join(raw.split())
    if not name:
        raise ValidationError("Tên agent không được để trống.")
    if len(name) > MAX_NAME_LENGTH:
        raise ValidationError(f"Tên agent không được dài quá {MAX_NAME_LENGTH} ký tự.")
    return name


def normalize_system_prompt(raw: str) -> str:
    """Rỗng là hợp lệ — một agent không cần chỉ dẫn đặc biệt nào cả."""
    prompt = raw.strip()
    if len(prompt) > MAX_SYSTEM_PROMPT_LENGTH:
        raise ValidationError(
            f"Prompt hệ thống không được dài quá {MAX_SYSTEM_PROMPT_LENGTH} ký tự."
        )
    return prompt


def validate_max_tool_calls_per_turn(value: int) -> int:
    if not (MIN_MAX_TOOL_CALLS_PER_TURN <= value <= MAX_MAX_TOOL_CALLS_PER_TURN):
        raise ValidationError(
            f"Số lần gọi tool tối đa mỗi lượt phải trong khoảng "
            f"[{MIN_MAX_TOOL_CALLS_PER_TURN}, {MAX_MAX_TOOL_CALLS_PER_TURN}], nhận được {value}."
        )
    return value


def ensure_not_archived(agent: Agent) -> None:
    """Chặn mở hội thoại mới với một agent đã lưu trữ.

    Hội thoại cũ vẫn đọc lại được — lưu trữ một agent không xoá lịch sử của
    những cuộc hội thoại nó từng tham gia.
    """
    if agent.is_archived:
        raise ValidationError(
            f"Agent {agent.id} đã bị lưu trữ, không dùng để bắt đầu hội thoại mới được nữa.",
            details={"agent_id": agent.id},
        )


def archive(agent: Agent, *, at: datetime) -> Agent:
    if agent.is_archived:
        raise ValidationError(f"Agent {agent.id} đã được lưu trữ rồi.")
    return replace(agent, archived_at=at, updated_at=at)


def ensure_can_manage(agent: Agent, *, actor_id: str, can_manage_workspace: bool) -> None:
    """Sửa cấu hình, lưu trữ — người tạo hoặc quản trị viên workspace."""
    if agent.created_by == actor_id or can_manage_workspace:
        return
    raise AuthorizationError(
        "Bạn không có quyền quản lý agent này.", details={"agent_id": agent.id}
    )


# ─────────────────────────────────────────────────────────────────────────────
# Conversation — một phiên chat với một Agent
# ─────────────────────────────────────────────────────────────────────────────


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


#: Hội thoại không thuộc về người gọi được đối xử y như không tồn tại — cùng
#: thông điệp, cùng mã lỗi, để không lộ rằng id đó có thật
_CONVERSATION_NOT_FOUND = "Không tìm thấy cuộc hội thoại này."


@dataclass(frozen=True, slots=True)
class Conversation:
    """Riêng của người tạo — không có khái niệm chia sẻ ở Phase 3."""

    id: str
    agent_id: str
    tenant_id: str
    workspace_id: str
    user_id: str
    status: ConversationStatus
    created_at: datetime
    updated_at: datetime

    @property
    def is_active(self) -> bool:
        return self.status is ConversationStatus.ACTIVE


def ensure_owns(conversation: Conversation, *, user_id: str) -> None:
    if conversation.user_id != user_id:
        raise AuthorizationError(_CONVERSATION_NOT_FOUND)


def ensure_active(conversation: Conversation) -> None:
    """Chặn ghi thêm tin nhắn vào một hội thoại đã lưu trữ."""
    if not conversation.is_active:
        raise ValidationError(
            f"Cuộc hội thoại {conversation.id} đã được lưu trữ, không ghi tiếp được nữa.",
            details={"conversation_id": conversation.id},
        )


def archive_conversation(conversation: Conversation, *, at: datetime) -> Conversation:
    ensure_active(conversation)
    return replace(conversation, status=ConversationStatus.ARCHIVED, updated_at=at)


# ─────────────────────────────────────────────────────────────────────────────
# Message — một dòng trong hội thoại
# ─────────────────────────────────────────────────────────────────────────────


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


MAX_MESSAGE_CONTENT_LENGTH = 100_000


@dataclass(frozen=True, slots=True)
class ToolCallRequest:
    """Quyết định gọi tool của model — chưa phải một `ToolInvocation` của
    Integration, chỉ là ý định."""

    call_id: str
    tool_id: str
    input: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Message:
    """Một dòng trong hội thoại.

    `tool_call_id` trên một message vai trò `TOOL` trỏ ngược về `call_id` của
    `ToolCallRequest` đã sinh ra nó — cùng nguyên tắc cây mà `TraceStep` dùng
    với `parent_step_id`.
    """

    id: str
    conversation_id: str
    role: MessageRole
    created_at: datetime
    content: str = ""
    tool_calls: tuple[ToolCallRequest, ...] = ()
    tool_call_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


def ensure_message_shape(message: Message) -> None:
    """Mỗi vai trò có đúng một hình dạng hợp lệ.

    Sai hình dạng thì bản ghi này không dựng lại đúng được cuộc hội thoại khi
    đọc lại — ví dụ một message `TOOL` thiếu `tool_call_id` thì không ai biết
    nó là kết quả của lần gọi tool nào.
    """
    if len(message.content) > MAX_MESSAGE_CONTENT_LENGTH:
        raise ValidationError(
            f"Nội dung tin nhắn không được dài quá {MAX_MESSAGE_CONTENT_LENGTH} ký tự.",
            details={"role": str(message.role)},
        )

    if message.role is MessageRole.USER:
        if not message.content.strip():
            raise ValidationError("Tin nhắn của người dùng không được để trống.")
        if message.tool_calls or message.tool_call_id is not None:
            raise ValidationError("Tin nhắn của người dùng không mang tool_calls hay tool_call_id.")

    elif message.role is MessageRole.ASSISTANT:
        if not message.content.strip() and not message.tool_calls:
            raise ValidationError(
                "Tin nhắn của assistant phải có nội dung, hoặc tool_calls, hoặc cả hai."
            )
        if message.tool_call_id is not None:
            raise ValidationError("Tin nhắn của assistant không mang tool_call_id.")

    else:  # MessageRole.TOOL
        if message.tool_call_id is None:
            raise ValidationError("Tin nhắn kết quả tool phải mang tool_call_id.")
        if message.tool_calls:
            raise ValidationError("Tin nhắn kết quả tool không tự mang tool_calls mới.")
