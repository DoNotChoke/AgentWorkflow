"""Thực thể và quy tắc nghiệp vụ của Execution Trace"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.core.errors import ValidationError


class TraceSource(StrEnum):
    """Trace phát sinh từ đâu.

    AGENT    — một lượt hội thoại của Agent Runtime.
    WORKFLOW — một lần thực thi Workflow trên Temporal.
    """

    AGENT = "agent"
    WORKFLOW = "workflow"


class TraceStatus(StrEnum):
    """Vòng đời một Execution Trace.

    RUNNING   — đang thực thi, còn bước tiếp theo sẽ được ghi.
    SUCCEEDED — kết thúc với một phản hồi cuối cùng.
    FAILED    — kết thúc vì lỗi không phục hồi được.
    CANCELLED — người dùng hoặc hệ thống dừng giữa chừng.
    """

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: Trạng thái kết. Không có đường quay lại — trace mới thì tạo trace mới.
TERMINAL: frozenset[TraceStatus] = frozenset(
    {TraceStatus.SUCCEEDED, TraceStatus.FAILED, TraceStatus.CANCELLED}
)


class StepKind(StrEnum):
    """Loại một bước trong trace.

    MODEL_CALL     — một lượt gọi LLM (Model Gateway).
    TOOL_CALL      — một lần gọi tool qua Integration Gateway.
    DECISION       — một quyết định không gọi ra ngoài (chọn tool, rẽ nhánh).
    ERROR          — một lỗi đã dừng hoặc làm gián đoạn bước hiện tại.
    FINAL_RESPONSE — phản hồi cuối cùng trả cho người dùng.
    """

    MODEL_CALL = "model_call"
    TOOL_CALL = "tool_call"
    DECISION = "decision"
    ERROR = "error"
    FINAL_RESPONSE = "final_response"


class StepStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REPLAYED = "replayed"


def canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    """Byte chuẩn của một payload — thứ được băm, và thứ được ghi ra.

    Một hàm duy nhất vì hai việc đó phải khớp nhau: băm một chuỗi rồi ghi ra
    một chuỗi khác thì vân tay không còn tìm lại được nội dung.
    """
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return raw.encode("utf-8")


@dataclass(frozen=True, slots=True)
class PayloadRef:
    """Vân tay của một payload — không phải payload."""

    sha256: str
    byte_size: int

    @classmethod
    def of(cls, payload: Mapping[str, Any]) -> PayloadRef:
        return cls.of_bytes(canonical_bytes(payload))

    @classmethod
    def of_bytes(cls, encoded: bytes) -> PayloadRef:
        """Cho người gọi đã có sẵn byte chuẩn trong tay, khỏi serialize hai lần."""
        return cls(sha256=hashlib.sha256(encoded).hexdigest(), byte_size=len(encoded))


class PayloadLocation(StrEnum):
    """Payload của một bước nằm ở đâu.

    ABSENT — chỉ còn vân tay. Bước ghi trước khi có payload store, hoặc payload
             vượt trần, hoặc một lần ghi lưu trữ thất bại.
    INLINE — nằm ngay trong hàng `trace_steps`, đủ nhỏ để không cần đi xa.
    OBJECT — nằm trong object storage, tra theo `ref.sha256` (§4.7, §9.1).
    """

    ABSENT = "absent"
    INLINE = "inline"
    OBJECT = "object"


@dataclass(frozen=True, slots=True)
class StoredPayload:
    """Vân tay của một payload, kèm chỗ tìm lại nội dung của nó."""

    ref: PayloadRef
    location: PayloadLocation = PayloadLocation.ABSENT
    #: Chỉ có giá trị khi `location` là INLINE.
    inline: Mapping[str, Any] | None = None

    @classmethod
    def absent(cls, ref: PayloadRef) -> StoredPayload:
        return cls(ref=ref, location=PayloadLocation.ABSENT)


@dataclass(frozen=True, slots=True)
class TraceSubject:
    """Cái gì đang được trace."""

    agent_id: str | None = None
    conversation_id: str | None = None
    workflow_id: str | None = None
    workflow_version_id: str | None = None
    execution_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionTrace:
    """Một lần thực thi hoàn chỉnh — một lượt Agent hoặc một lần chạy Workflow."""

    id: str
    tenant_id: str
    workspace_id: str
    source: TraceSource
    status: TraceStatus
    subject: TraceSubject
    initiated_by: str
    started_at: datetime
    completed_at: datetime | None = None
    error_category: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL

    @property
    def duration_ms(self) -> float | None:
        if self.completed_at is None:
            return None
        return (self.completed_at - self.started_at).total_seconds() * 1000


@dataclass(frozen=True, slots=True)
class TraceStep:
    """Một bước trong trace. Cây, không phải danh sách phẳng.

    `parent_step_id` nối một bước vào bước bao nó: `TOOL_CALL` do Gateway ghi
    nằm dưới `TOOL_CALL` của Agent đã gửi lời gọi đó.
    """

    id: str
    trace_id: str
    kind: StepKind
    status: StepStatus
    started_at: datetime
    completed_at: datetime
    #: `None` ở gốc cây.
    parent_step_id: str | None = None

    input_payload: StoredPayload | None = None
    output_payload: StoredPayload | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)

    error_category: str | None = None

    otel_trace_id: str | None = None
    otel_span_id: str | None = None

    @property
    def duration_ms(self) -> float:
        return (self.completed_at - self.started_at).total_seconds() * 1000


MAX_ATTRIBUTE_BYTES = 8192


def ensure_attributes_safe(attributes: Mapping[str, Any]) -> None:
    """Chặn attribute phình to trước khi ghi — đây không phải chỗ cất payload."""
    encoded = json.dumps(attributes, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_ATTRIBUTE_BYTES:
        raise ValidationError(
            f"Attribute của một bước trace không được vượt quá {MAX_ATTRIBUTE_BYTES} byte. "
            "Payload lớn phải đi qua object storage hoặc log, không phải trường tự do này.",
            details={"byte_size": str(len(encoded.encode("utf-8")))},
        )


# ─────────────────────────────────────────────────────────────────────────────
# Quy tắc nghiệp vụ
# ─────────────────────────────────────────────────────────────────────────────


def ensure_subject_matches_source(source: TraceSource, subject: TraceSubject) -> None:
    """Mỗi nguồn trace đòi một nhóm định danh riêng — thiếu thì trace vô nghĩa
    với người sẽ đọc lại nó sau này (không biết trace thuộc hội thoại nào)."""
    if source is TraceSource.AGENT and not (subject.agent_id and subject.conversation_id):
        raise ValidationError(
            "Trace nguồn AGENT phải có agent_id và conversation_id.",
            details={"source": str(source)},
        )
    if source is TraceSource.WORKFLOW and not (subject.workflow_id and subject.execution_id):
        raise ValidationError(
            "Trace nguồn WORKFLOW phải có workflow_id và execution_id.",
            details={"source": str(source)},
        )


def ensure_not_terminal(trace: ExecutionTrace) -> None:
    """Chặn ghi thêm bước vào một trace đã kết thúc."""
    if trace.is_terminal:
        raise ValidationError(
            f"Execution trace {trace.id} đã ở trạng thái kết ({trace.status}), "
            "không ghi thêm bước được nữa.",
            details={"trace_id": trace.id, "status": str(trace.status)},
        )


def complete(
    trace: ExecutionTrace,
    *,
    status: TraceStatus,
    at: datetime,
    error_category: str | None = None,
) -> ExecutionTrace:
    """Đóng một trace. Chỉ chuyển được từ một trạng thái chưa kết sang một
    trạng thái kết — không đóng được trace đã đóng, và không đóng bằng RUNNING."""
    if status not in TERMINAL:
        raise ValidationError(
            f"{status} không phải trạng thái kết, không dùng để đóng trace.",
            details={"status": str(status)},
        )
    ensure_not_terminal(trace)
    return replace(trace, status=status, completed_at=at, error_category=error_category)
