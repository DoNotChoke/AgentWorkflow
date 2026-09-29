"""Lỗi của platform qua Temporal (Q10), và dữ liệu đi qua ranh giới activity."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from temporalio.converter import DataConverter
from temporalio.exceptions import ApplicationError

from app.core.errors import (
    ProviderUnavailableError,
    RateLimitedError,
    ValidationError,
    WorkflowExecutionError,
)
from app.domains.workflow.application.runtime import LoadedPlan
from app.domains.workflow.infrastructure.temporal.errors import (
    as_temporal_errors,
    to_application_error,
)
from app.domains.workflow.infrastructure.temporal.messages import (
    NodeRefMessage,
    RecordNodeInput,
)


def test_ten_hang_loi_la_type_cua_temporal() -> None:
    error = to_application_error(WorkflowExecutionError("hụt đường", details={"node": "dang"}))
    assert (error.type, error.message, error.non_retryable) == (
        "WorkflowExecutionError",
        "hụt đường",
        True,
    )
    assert error.details == ({"node": "dang"},)


@pytest.mark.parametrize(
    ("error", "non_retryable"),
    [
        (RateLimitedError("chậm lại"), False),
        (ProviderUnavailableError("sập"), False),
        (ValidationError("sai"), True),
        (WorkflowExecutionError("hỏng"), True),
    ],
)
def test_retry_theo_bang_retryable(error: Exception, non_retryable: bool) -> None:
    with pytest.raises(ApplicationError) as caught, as_temporal_errors():
        raise error
    assert caught.value.non_retryable is non_retryable
    assert caught.value.__cause__ is error


def test_loi_khong_phai_cua_platform_de_nguyen_cho_temporal_retry() -> None:
    with pytest.raises(ConnectionError), as_temporal_errors():
        raise ConnectionError("mất kết nối database")


async def test_du_lieu_di_qua_temporal_nguyen_ven() -> None:
    converter = DataConverter.default
    values = [
        LoadedPlan(
            workflow_version_id="wfv_1",
            ir={"schema_version": "1.0", "nodes": [{"id": "t"}]},
            mutating_tools=["slack.post_message@1.0"],
        ),
        RecordNodeInput(
            tenant_id="org_1",
            execution_id="exe_1",
            node=NodeRefMessage("moi_trang[2].doc_trang", "doc_trang", "tool"),
            status="failed",
            at=datetime(2026, 9, 29, 9, 0, tzinfo=UTC),
            attempt=3,
            error_category="OutcomeUnknown",
            error_message="Không rõ.",
        ),
    ]
    encoded = await converter.encode(values)
    assert await converter.decode(encoded, [LoadedPlan, RecordNodeInput]) == values
