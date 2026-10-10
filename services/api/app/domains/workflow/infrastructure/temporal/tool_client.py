"""Node `tool` gọi Integration Gateway của Control Plane qua HTTP (ADR-0010)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from app.core.config import Settings
from app.core.control_plane import request_json
from app.core.security import issue_run_token
from app.domains.workflow.application.ports import DEFAULT_TOOL_TIMEOUT_SECONDS

_HTTP_MARGIN_SECONDS = 5.0


class GatewayToolInvoker:
    """`ToolInvoker` của domain `workflow`.

    Mỗi lời gọi một token mới, sống vừa đủ một lời gọi: một lần chạy ngủ ba ngày không để
    lại bí mật nào trong lịch sử Temporal."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._base_url = settings.control_plane_internal_base_url.rstrip("/")
        self._transport = transport

    async def invoke(
        self,
        *,
        execution_id: str,
        node_id: str,
        tool_id: str,
        tool_version: str,
        tool_input: Mapping[str, Any],
        idempotency_key: str | None,
        trace_id: str | None = None,
        parent_step_id: str | None = None,
        timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
    ) -> Mapping[str, Any]:
        token = issue_run_token(self._settings, execution_id=execution_id)
        body = await request_json(
            self._base_url,
            "POST",
            f"/internal/v1/tools/{tool_id}/invoke",
            authorization=f"Bearer {token}",
            organization_id=None,
            workspace_id=None,
            # Lâu hơn thời hạn của tool: Gateway trả lời trước, không phải dây đứt trước.
            timeout_seconds=timeout_seconds + _HTTP_MARGIN_SECONDS,
            json_body={
                "input": dict(tool_input),
                "tool_version": tool_version,
                "idempotency_key": idempotency_key,
                "caller": {
                    "execution_id": execution_id,
                    "node_id": node_id,
                    "execution_trace_id": trace_id,
                    "parent_step_id": parent_step_id,
                },
            },
            transport=self._transport,
        )
        output: Mapping[str, Any] = body["output"]
        return output
