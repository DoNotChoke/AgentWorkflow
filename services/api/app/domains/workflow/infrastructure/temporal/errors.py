"""Lỗi của platform → lỗi Temporal hiểu được."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

from temporalio.exceptions import ApplicationError

from app.core.errors import AppError


def to_application_error(error: AppError) -> ApplicationError:
    """`type` là tên hạng lỗi, nên `non_retryable_error_types` khai bằng đúng tên đó."""
    delay = error.retry_after_seconds
    return ApplicationError(
        error.message,
        dict(error.details),
        type=str(error.category),
        non_retryable=not error.retryable,
        # Gateway nói phải đợi bao lâu (bị tiết lưu, hoặc một lời gọi khác đang chạy cùng
        # thao tác): thử lại sớm hơn thì chỉ nhận lại đúng lỗi đó.
        next_retry_delay=None if delay is None else timedelta(seconds=delay),
    )


@contextmanager
def as_temporal_errors() -> Iterator[None]:
    """Bọc thân một activity. Lỗi không phải `AppError` để nguyên — Temporal retry nó."""
    try:
        yield
    except AppError as error:
        raise to_application_error(error) from error
