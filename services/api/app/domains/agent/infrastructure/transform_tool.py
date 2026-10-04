"""Tool dựng sẵn `system.transform`: Agent viết code, sandbox chạy."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any, Final, Protocol

from app.core.codeguard import SAFE_BUILTINS
from app.core.ids import ResourceKind, new_id
from app.core.logging import get_logger
from app.core.sandbox import DEFAULT_LIMITS, Limits, run
from app.domains.agent.application.ports import (
    ModelToolSpec,
    ToolCallContext,
    ToolCatalog,
    ToolExecutor,
    ToolInvocationResult,
)
from app.domains.agent.application.tool_naming import from_model_name, to_model_name

log = get_logger(__name__)


class _Inner(ToolCatalog, ToolExecutor, Protocol):
    """Lớp bên trong phải làm được cả hai việc — `IntegrationClient` là một."""


TOOL_ID: Final = "system.transform"

_DESCRIPTION: Final = (
    "Chạy một đoạn Python ngắn để tính toán hoặc đổi hình dạng dữ liệu. Dùng nó thay vì tự "
    "tính nhẩm: đếm, cộng, lọc, sắp xếp, và nắn kết quả của tool này sang hình dạng mà tool "
    "khác đòi. `code` là thân một hàm nhận đúng một tham số `args` và phải `return` kết quả. "
    "Dữ liệu truyền qua `args`, đừng dán dữ liệu lớn vào trong code. Không đọc file, không gọi "
    "mạng, và **không có `import`** — kể cả `collections` hay `json`. Chỉ có sẵn: "
    + ", ".join(sorted(SAFE_BUILTINS))
    + "."
)


def spec(limits: Limits = DEFAULT_LIMITS) -> ModelToolSpec:
    return ModelToolSpec(
        name=to_model_name(TOOL_ID),
        description=_DESCRIPTION,
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["code"],
            "properties": {
                "code": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": limits.code_bytes,
                    "description": "Thân hàm Python. Ví dụ: return len(args['messages'])",
                },
                "args": {
                    "type": "object",
                    "description": "Dữ liệu cho code đọc, qua biến `args`.",
                },
            },
        },
        version="1.0",
        local=True,
    )


class TransformTool:
    """Hiện thực `ToolCatalog` và `ToolExecutor`, uỷ quyền phần còn lại cho lớp trong."""

    def __init__(
        self,
        inner: _Inner,
        *,
        limits: Limits = DEFAULT_LIMITS,
    ) -> None:
        self._inner = inner
        self._limits = limits
        self._spec = spec(limits)

    async def list_tools(
        self,
        *,
        authorization: str,
        organization_id: str | None,
        workspace_id: str | None,
        allowed_tool_ids: frozenset[str],
    ) -> Sequence[ModelToolSpec]:
        tools = await self._inner.list_tools(
            authorization=authorization,
            organization_id=organization_id,
            workspace_id=workspace_id,
            allowed_tool_ids=allowed_tool_ids,
        )
        # Tool dựng sẵn: agent nào cũng có, không qua whitelist.
        return (*tools, self._spec)

    async def invoke(
        self,
        *,
        name: str,
        tool_input: Mapping[str, Any],
        connection_id: str | None,
        authorization: str,
        organization_id: str | None,
        workspace_id: str | None,
        idempotency_key: str | None = None,
        caller: ToolCallContext | None = None,
    ) -> ToolInvocationResult:
        if from_model_name(name) != TOOL_ID:
            return await self._inner.invoke(
                name=name,
                tool_input=tool_input,
                connection_id=connection_id,
                authorization=authorization,
                organization_id=organization_id,
                workspace_id=workspace_id,
                idempotency_key=idempotency_key,
                caller=caller,
            )

        code = str(tool_input.get("code", ""))
        args = tool_input.get("args") or {}
        # `run` mở một tiến trình con và chờ nó — chặn, nên phải ra khỏi event
        # loop. Cùng lý do `ObjectPayloadStore` đẩy boto3 sang thread.
        result = await asyncio.to_thread(run, code, dict(args), self._limits)

        log.info("transform_tool_ran", code_bytes=len(code.encode("utf-8")))
        return ToolInvocationResult(
            output={"result": result.value},
            invocation_id=new_id(ResourceKind.TOOL_INVOCATION),
            replayed=False,
        )
