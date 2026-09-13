"""Luật thử lại, dựng từ `RetryPolicy` của tool."""

from __future__ import annotations

from app.core.errors import AppError, Delivery
from app.domains.integration.domain.tools import ToolSpec


def may_retry(tool: ToolSpec, error: BaseException) -> bool:
    """Lời gọi vừa hỏng có được thử lại không.

    Tool ghi chỉ được gửi lại khi yêu cầu chắc chắn chưa tới provider. Khoá
    idempotency không che được chỗ này: sổ chặn người gọi lặp lại `invoke`,
    không chặn Gateway tự gọi provider lần hai.
    """
    if not isinstance(error, AppError) or not error.retryable:
        return False
    if not tool.side_effect.mutates:
        return True
    return error.delivery is Delivery.NOT_SENT


def wait_for(tool: ToolSpec, attempt: int, error: BaseException) -> float:
    """Thời gian chờ trước lần thử thứ `attempt`.

    Con số `Retry-After` của provider luôn thắng backoff mình tự tính — nó biết
    hạn mức của chính nó, còn mình thì đoán.
    """
    if isinstance(error, AppError) and error.retry_after_seconds is not None:
        return max(float(error.retry_after_seconds), 0.0)
    return tool.retry.backoff_for(attempt)
