"""Tool dựng sẵn `system.transform` — Agent viết code, sandbox chạy.

Hai tầng: bản thân adapter, và cách `AgentRuntime` đối xử với một tool `local`
(không connection, và tự ghi payload vào trace vì không có bước Gateway bên dưới).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from app.core.codeguard import SAFE_BUILTINS
from app.core.sandbox import Limits, SandboxError
from app.domains.agent.application.ports import (
    ModelToolSpec,
    ToolCallContext,
    ToolInvocationResult,
)
from app.domains.agent.domain.entities import BUILT_IN_TOOL_IDS
from app.domains.agent.infrastructure.transform_tool import TOOL_ID, TransformTool, spec

ENABLED = frozenset({TOOL_ID, "slack.post_message"})
FAST = Limits(timeout_seconds=5.0)


class FakeInner:
    """`IntegrationClient` giả — ghi lại mọi thứ được uỷ quyền xuống."""

    def __init__(self) -> None:
        self.listed: list[frozenset[str]] = []
        self.invoked: list[dict[str, Any]] = []

    async def list_tools(
        self,
        *,
        authorization: str,
        organization_id: str | None,
        workspace_id: str | None,
        allowed_tool_ids: frozenset[str],
    ) -> Sequence[ModelToolSpec]:
        self.listed.append(allowed_tool_ids)
        return (
            ModelToolSpec(
                name="slack__post_message",
                description="Gửi tin nhắn",
                input_schema={"type": "object"},
                version="1.0",
            ),
        )

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
        self.invoked.append({"name": name, "connection_id": connection_id})
        return ToolInvocationResult(output={"ok": True}, invocation_id="ti_1", replayed=False)


def _tool(inner: FakeInner | None = None) -> TransformTool:
    return TransformTool(inner or FakeInner(), limits=FAST)


async def _run(tool: TransformTool, code: str, args: Mapping[str, Any]) -> Any:
    result = await tool.invoke(
        name="system__transform",
        tool_input={"code": code, "args": args},
        connection_id=None,
        authorization="Bearer token",
        organization_id="org_1",
        workspace_id="ws_1",
    )
    return result.output["result"]


# ─── Danh mục ────────────────────────────────────────────────────────────────


async def test_agent_bat_thi_tool_co_trong_danh_muc() -> None:
    tool = _tool()
    specs = await tool.list_tools(
        authorization="Bearer token",
        organization_id="org_1",
        workspace_id="ws_1",
        allowed_tool_ids=ENABLED,
    )
    added = specs[-1]
    assert added.name == "system__transform"
    assert added.local is True
    assert "code" in added.input_schema["properties"]


def test_mo_ta_liet_ke_dung_nhung_builtin_sandbox_cho_phep() -> None:
    """Chạy thật cho thấy model **thử `from collections import Counter`** dù mô tả
    đã nói "không import"; nó chỉ dừng khi sandbox chặn, và mất một vòng gọi model.
    Liệt kê sẵn builtin thì lần đầu đã đúng — nhưng danh sách ấy phải luôn khớp với
    whitelist thật, nếu không nó dạy model sai một cách còn khó thấy hơn."""
    description = spec().description
    assert "import" in description
    for name in SAFE_BUILTINS:
        assert name in description, name


@pytest.mark.parametrize("allowed", [frozenset({"slack.post_message"}), frozenset()])
async def test_agent_khong_bat_van_thay(allowed: frozenset[str]) -> None:
    """Tool dựng sẵn không qua whitelist: giao diện tạo Agent chỉ liệt kê tool của catalog,
    nên bắt người dùng bật nó là bắt họ bật một thứ họ không nhìn thấy."""
    tool = _tool()
    specs = await tool.list_tools(
        authorization="Bearer token",
        organization_id="org_1",
        workspace_id="ws_1",
        allowed_tool_ids=allowed,
    )
    assert specs[-1].name == "system__transform"
    assert len(specs) == 2


def test_ten_tool_khop_voi_danh_sach_dung_san_cua_domain() -> None:
    """Hai chỗ phải nói cùng một tên: chỗ gửi tool cho model, và chỗ cho phép gọi nó."""
    assert TOOL_ID in BUILT_IN_TOOL_IDS


# ─── Chạy ────────────────────────────────────────────────────────────────────


async def test_dem_bang_code_thay_vi_bang_model() -> None:
    assert await _run(_tool(), "return len(args['messages'])", {"messages": [1, 2, 3]}) == 3


async def test_nan_hinh_dang_giua_hai_tool() -> None:
    """Chính kịch bản 4 của GĐ 0, nhưng không nhờ model chép lại chữ."""
    result = await _run(
        _tool(),
        "return [{'type': 'bulleted_list_item', 'text': m['text']} for m in args['m']]",
        {"m": [{"ts": "1", "text": "[WP-B] kịch bản 2"}]},
    )
    assert result == [{"type": "bulleted_list_item", "text": "[WP-B] kịch bản 2"}]


async def test_ket_qua_khong_phai_mapping_van_tra_ve_duoc() -> None:
    """`ToolInvocationResult.output` phải là Mapping, mà code trả được mảng."""
    assert await _run(_tool(), "return [1, 2]", {}) == [1, 2]


async def test_loi_sandbox_noi_len_de_model_tu_sua() -> None:
    with pytest.raises(SandboxError):
        await _run(_tool(), "import os\nreturn 1", {})


async def test_tool_khac_van_di_xuong_gateway() -> None:
    inner = FakeInner()
    tool = _tool(inner)

    await tool.invoke(
        name="slack__post_message",
        tool_input={"channel": "C1", "text": "x"},
        connection_id="conn_1",
        authorization="Bearer token",
        organization_id="org_1",
        workspace_id="ws_1",
    )

    assert inner.invoked == [{"name": "slack__post_message", "connection_id": "conn_1"}]
