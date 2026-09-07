"""Error model chuẩn hoá toàn nền tảng"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCategory(StrEnum):
    AUTHENTICATION = "AuthenticationError"
    AUTHORIZATION = "AuthorizationError"
    CONNECTION_EXPIRED = "ConnectionExpired"
    MISSING_SCOPE = "MissingScope"
    VALIDATION = "ValidationError"
    RATE_LIMITED = "RateLimited"
    PROVIDER_UNAVAILABLE = "ProviderUnavailable"
    PROVIDER_PERMANENT = "ProviderPermanentError"
    TIMEOUT = "Timeout"
    RETRY_EXHAUSTED = "RetryExhausted"
    APPROVAL_REJECTED = "ApprovalRejected"
    WORKFLOW_DEFINITION = "WorkflowDefinitionError"
    WORKFLOW_EXECUTION = "WorkflowExecutionError"
    MISSING_CONNECTION = "MissingConnection"
    CONNECTION_CHOICE_REQUIRED = "ConnectionChoiceRequired"
    INTERNAL = "InternalError"


#: Lỗi nào đáng retry. Temporal Activity retry policy đọc bảng này.
RETRYABLE: frozenset[ErrorCategory] = frozenset(
    {
        ErrorCategory.RATE_LIMITED,
        ErrorCategory.PROVIDER_UNAVAILABLE,
        ErrorCategory.TIMEOUT,
    }
)

_HTTP_STATUS: dict[ErrorCategory, int] = {
    ErrorCategory.AUTHENTICATION: 401,
    ErrorCategory.AUTHORIZATION: 403,
    ErrorCategory.CONNECTION_EXPIRED: 409,
    ErrorCategory.MISSING_SCOPE: 403,
    ErrorCategory.VALIDATION: 422,
    ErrorCategory.RATE_LIMITED: 429,
    ErrorCategory.PROVIDER_UNAVAILABLE: 502,
    ErrorCategory.PROVIDER_PERMANENT: 502,
    ErrorCategory.TIMEOUT: 504,
    ErrorCategory.RETRY_EXHAUSTED: 502,
    ErrorCategory.APPROVAL_REJECTED: 409,
    ErrorCategory.WORKFLOW_DEFINITION: 422,
    ErrorCategory.WORKFLOW_EXECUTION: 500,
    ErrorCategory.MISSING_CONNECTION: 409,
    ErrorCategory.CONNECTION_CHOICE_REQUIRED: 409,
    ErrorCategory.INTERNAL: 500,
}


class AppError(Exception):
    """Lỗi gốc của nền tảng."""

    category: ErrorCategory = ErrorCategory.INTERNAL

    def __init__(
        self,
        message: str,
        *,
        category: ErrorCategory | None = None,
        details: dict[str, Any] | None = None,
        retry_after_seconds: float | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if category is not None:
            self.category = category
        self.details = details or {}
        self.retry_after_seconds = retry_after_seconds
        self.__cause__ = cause

    @property
    def retryable(self) -> bool:
        return self.category in RETRYABLE

    @property
    def http_status(self) -> int:
        return _HTTP_STATUS.get(self.category, 500)

    def to_payload(self) -> dict[str, Any]:
        """Body JSON trả về client. Không bao giờ chứa nội dung nhạy cảm."""
        payload: dict[str, Any] = {
            "error": {
                "category": str(self.category),
                "message": self.message,
            }
        }
        if self.details:
            payload["error"]["details"] = self.details
        if self.retry_after_seconds is not None:
            payload["error"]["retry_after_seconds"] = self.retry_after_seconds
        return payload

    def __repr__(self) -> str:
        return f"{type(self).__name__}(category={self.category}, message={self.message!r})"


# ─── Các lớp con tiện dụng ───────────────────────────────────────────────────


class AuthenticationError(AppError):
    category = ErrorCategory.AUTHENTICATION


class AuthorizationError(AppError):
    category = ErrorCategory.AUTHORIZATION


class ConnectionExpiredError(AppError):
    category = ErrorCategory.CONNECTION_EXPIRED


class MissingScopeError(AppError):
    category = ErrorCategory.MISSING_SCOPE


class ValidationError(AppError):
    category = ErrorCategory.VALIDATION


class RateLimitedError(AppError):
    category = ErrorCategory.RATE_LIMITED


class ProviderUnavailableError(AppError):
    category = ErrorCategory.PROVIDER_UNAVAILABLE


class ProviderPermanentError(AppError):
    category = ErrorCategory.PROVIDER_PERMANENT


class PlatformTimeoutError(AppError):
    """Đặt tên có tiền tố để không che ``TimeoutError`` dựng sẵn của Python."""

    category = ErrorCategory.TIMEOUT


class RetryExhaustedError(AppError):
    category = ErrorCategory.RETRY_EXHAUSTED


class ApprovalRejectedError(AppError):
    category = ErrorCategory.APPROVAL_REJECTED


class WorkflowDefinitionError(AppError):
    category = ErrorCategory.WORKFLOW_DEFINITION


class WorkflowExecutionError(AppError):
    category = ErrorCategory.WORKFLOW_EXECUTION


class MissingConnectionError(AppError):
    """Provider chưa có connection nào khả dụng cho người đang hành động."""

    category = ErrorCategory.MISSING_CONNECTION


class ConnectionChoiceRequiredError(AppError):
    """Provider có từ hai connection khả dụng trở lên và chưa có lựa chọn nào
    được chốt cho cuộc hội thoại đang hành động — xem ADR-0004."""

    category = ErrorCategory.CONNECTION_CHOICE_REQUIRED


class InternalError(AppError):
    category = ErrorCategory.INTERNAL
