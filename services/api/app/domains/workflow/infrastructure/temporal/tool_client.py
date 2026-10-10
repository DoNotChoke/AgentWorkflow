"""Node `tool` gọi Integration Gateway của Control Plane qua HTTP (ADR-0010)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from app.core.config import Settings
from app.core.control_plane import request_json
from app.core.security import issue_run_token


class GatewayToolInvoker:
    """`ToolInvoker` của domain `workflow`.

    Mỗi lời gọi một token mới, sống vừa đủ một lời gọi: một lần chạy ngủ ba ngày không để
    lại bí mật nào trong lịch sử Temporal."""

    def __init__(
        self,
        settings: Settings,
        *,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._base_url = settings.control_plane_internal_base_url.rstrip("/")
        self._timeout = timeout
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
    ) -> Mapping[str, Any]:
        token = issue_run_token(self._settings, execution_id=execution_id)
        body = await request_json(
            self._base_url,
            "POST",
            f"/internal/v1/tools/{tool_id}/invoke",
            authorization=f"Bearer {token}",
            organization_id=None,
            workspace_id=None,
            timeout_seconds=self._timeout,
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
