"""Lỗi của platform → lỗi Temporal hiểu được (Q10)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from temporalio.exceptions import ApplicationError

from app.core.errors import AppError


def to_application_error(error: AppError) -> ApplicationError:
    """`type` là tên hạng lỗi, nên `non_retryable_error_types` khai bằng đúng tên đó."""
    return ApplicationError(
        error.message,
        dict(error.details),
        type=str(error.category),
        non_retryable=not error.retryable,
    )


@contextmanager
def as_temporal_errors() -> Iterator[None]:
    """Bọc thân một activity. Lỗi không phải `AppError` để nguyên — Temporal retry nó."""
    try:
        yield
    except AppError as error:
        raise to_application_error(error) from error
