"""Bản ghi trace và usage của Phase 2: chỉ log, không cất giữ."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core.logging import get_logger
from app.domains.integration.domain.invocation import ToolInvocationRecord, ToolUsage

log = get_logger(__name__)


class LoggingToolInvocationSink:
    """Ghi một dòng log có cấu trúc cho mỗi lời gọi tool.

    Payload chỉ đi ra dưới dạng băm và kích thước, không bao giờ là nội dung:
    input của tool mang dữ liệu người dùng, và dòng log thì đi xa hơn hẳn database.
    """

    __slots__ = ()

    async def record(
        self,
        invocation: ToolInvocationRecord,
        *,
        input_payload: Mapping[str, Any],  # noqa: ARG002 — chỉ log vân tay
        output_payload: Mapping[str, Any],  # noqa: ARG002
    ) -> None:
        log.info(
            "tool_invoked",
            invocation_id=invocation.invocation_id,
            tool_id=invocation.tool_id,
            tool_version=invocation.tool_version,
            provider=invocation.provider,
            connection_id=invocation.connection_id,
            status=invocation.status,
            attempt=invocation.attempt,
            duration_ms=round(invocation.duration_ms, 1),
            side_effect=invocation.side_effect.value,
            error_category=invocation.error_category,
            input_sha256=invocation.input_ref.sha256,
            input_bytes=invocation.input_ref.byte_size,
            output_sha256=invocation.output_ref.sha256,
            output_bytes=invocation.output_ref.byte_size,
            otel_trace_id=invocation.otel_trace_id,
            execution_trace_id=invocation.execution_trace_id,
        )


class LoggingUsageRecorder:
    """Sau thay bằng sổ usage thật."""

    __slots__ = ()

    async def record(self, usage: ToolUsage) -> None:
        log.info(
            "tool_usage",
            tool_id=usage.tool_id,
            provider=usage.provider,
            side_effect=usage.side_effect.value,
            attempts=usage.attempts,
            duration_ms=round(usage.duration_ms, 1),
        )
