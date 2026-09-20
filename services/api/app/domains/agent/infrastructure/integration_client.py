"""Client HTTP gọi Integration Gateway từ Agent Runtime"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.domains.agent.application.ports import (
    ModelToolSpec,
    ToolCallContext,
    ToolInvocationResult,
)
from app.domains.agent.application.tool_naming import from_model_name, to_model_name
from app.domains.agent.infrastructure.control_plane_http import request_json


class IntegrationClient:
    """Hiện thực `ToolCatalog` và `ToolExecutor` bằng HTTP tới Control Plane."""

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def list_tools(
        self,
        *,
        authorization: str,
        organization_id: str | None,
        workspace_id: str | None,
        allowed_tool_ids: frozenset[str],
    ) -> Sequence[ModelToolSpec]:
        """Danh mục tool cho một `Agent` — lọc theo whitelist của nó ngay ở
        đây, trước khi tên tool bị đổi dạng cho model. Whitelist rỗng thì
        không cần gọi HTTP: agent này vốn không được gọi tool nào (§ WP4)."""
        if not allowed_tool_ids:
            return ()

        body = await request_json(
            self._base_url,
            "GET",
            "/internal/v1/tools",
            authorization=authorization,
            organization_id=organization_id,
            workspace_id=workspace_id,
            timeout_seconds=self._timeout,
        )
        return tuple(
            ModelToolSpec(
                name=to_model_name(tool["tool_id"]),
                description=tool["description"],
                input_schema=tool["input_schema"],
                version=tool["version"],
            )
            for tool in body
            if tool["tool_id"] in allowed_tool_ids
        )

    async def invoke(
        self,
        *,
        name: str,
        tool_input: Mapping[str, Any],
        #: Không bao giờ `None` ở đây — tool `local` không đi qua client này.
        connection_id: str | None,
        authorization: str,
        organization_id: str | None,
        workspace_id: str | None,
        idempotency_key: str | None = None,
        caller: ToolCallContext | None = None,
    ) -> ToolInvocationResult:
        tool_id = from_model_name(name)
        ctx = caller or ToolCallContext()

        payload: dict[str, Any] = {
            "connection_id": connection_id,
            "input": dict(tool_input),
            "idempotency_key": idempotency_key,
            "caller": {
                "agent_id": ctx.agent_id,
                "workflow_id": ctx.workflow_id,
                "execution_id": ctx.execution_id,
                "node_id": ctx.node_id,
                "execution_trace_id": ctx.execution_trace_id,
                "parent_step_id": ctx.parent_step_id,
            },
        }

        body = await request_json(
            self._base_url,
            "POST",
            f"/internal/v1/tools/{tool_id}/invoke",
            authorization=authorization,
            organization_id=organization_id,
            workspace_id=workspace_id,
            timeout_seconds=self._timeout,
            json_body=payload,
        )

        return ToolInvocationResult(
            output=body["output"],
            invocation_id=body["invocation_id"],
            replayed=body["replayed"],
        )
