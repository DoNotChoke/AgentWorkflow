"""Sổ idempotency cho tác động ra bên ngoài"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.core.errors import ErrorCategory, ValidationError


class InvocationStatus(StrEnum):
    IN_FLIGHT = "in_flight"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    """Ý định chạy một thao tác. Ghi trước khi chạm tới provider."""

    tenant_id: str
    workspace_id: str
    connection_id: str
    tool_id: str
    tool_version: str
    key: str
    request_fingerprint: str
    expires_at: datetime
    at: datetime


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    tenant_id: str
    key: str
    workspace_id: str
    connection_id: str
    tool_id: str
    tool_version: str
    request_fingerprint: str
    status: InvocationStatus
    attempts: int
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    provider_reference: str | None = None
    result: Mapping[str, object] | None = None
    error_category: ErrorCategory | None = None

    @property
    def replayable(self) -> bool:
        """Đã xong và có kết quả để trả lại thay vì gọi provider lần nữa."""
        return self.status is InvocationStatus.SUCCEEDED


@dataclass(frozen=True, slots=True)
class ClaimResult:
    record: IdempotencyRecord

    #: `True` nghĩa là bản ghi vừa được tạo bởi chính lời gọi này, và người gọi
    #: được đi tiếp. `False` nghĩa là đã có ai đó tới trước.
    fresh: bool


def fingerprint(payload: Mapping[str, object]) -> str:
    """Vân tay của input, chuẩn hoá rồi băm.

    `sort_keys` để hai dict cùng nội dung khác thứ tự cho cùng một vân tay;
    `ensure_ascii=False` để tiếng Việt không nở thành escape sequence và làm
    vân tay phụ thuộc vào cách serialize.
    """
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def ensure_same_operation(record: IdempotencyRecord, *, request_fingerprint: str) -> None:
    """Cùng khoá thì phải là cùng thao tác.

    Khoá bị dùng lại cho một input khác là lỗi của người gọi, và trả về kết quả
    cũ trong tình huống đó còn tệ hơn báo lỗi: người gọi sẽ tin rằng thao tác
    mới đã chạy.
    """
    if record.request_fingerprint != request_fingerprint:
        raise ValidationError(
            f"Khoá idempotency {record.key!r} đã được dùng cho một thao tác khác "
            f"({record.tool_id}). Dùng khoá mới cho input mới.",
            details={"tool_id": record.tool_id, "key": record.key},
        )


def ensure_key_present(key: str | None, *, tool_id: str, required: bool) -> str | None:
    """Tool bắt buộc idempotency thì không được gọi mà thiếu khoá."""
    if key:
        return key
    if required:
        raise ValidationError(
            f"{tool_id} có tác động ra bên ngoài nên bắt buộc phải có khoá idempotency.",
            details={"tool_id": tool_id},
        )
    return None
