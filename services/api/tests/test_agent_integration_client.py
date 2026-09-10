"""Kiểm tra `IntegrationClient` bằng `httpx.MockTransport` — không mở socket thật.

Cùng kỹ thuật `test_integration_api.py` dùng cho Slack: thay `httpx.AsyncClient`
để chèn `MockTransport`, nên mọi tầng bên trong `IntegrationClient` là đường
chạy thật, chỉ ranh giới mạng ngoài cùng là giả.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.core.errors import (
    AuthorizationError,
    InternalError,
    PlatformTimeoutError,
    ProviderUnavailableError,
    RateLimitedError,
    ValidationError,
)
from app.domains.agent.application.ports import ModelToolSpec, ToolCallContext
from app.domains.agent.infrastructure.integration_client import IntegrationClient


class FakeControlPlane:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.replies: list[httpx.Response | Exception] = []

    def next(self, status: int = 200, **kwargs: Any) -> None:
        self.replies.append(httpx.Response(status, **kwargs))

    def next_error(self, error: Exception) -> None:
        self.replies.append(error)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.replies:
            reply = self.replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        return httpx.Response(200, json=[])


@pytest.fixture
def control_plane(monkeypatch: pytest.MonkeyPatch) -> FakeControlPlane:
    fake = FakeControlPlane()
    real = httpx.AsyncClient

    def build(**kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return real(transport=httpx.MockTransport(fake.handle), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", build)
    return fake


@pytest.fixture
def client() -> IntegrationClient:
    return IntegrationClient("http://control-plane.local")


# ─── list_tools ──────────────────────────────────────────────────────────────


async def test_whitelist_rong_khong_goi_http(
    client: IntegrationClient, control_plane: FakeControlPlane
) -> None:
    result = await client.list_tools(
        authorization="Bearer token",
        organization_id="org_1",
        workspace_id="ws_1",
        allowed_tool_ids=frozenset(),
    )

    assert result == ()
    assert control_plane.requests == []


async def test_loc_theo_whitelist_va_doi_ten_hop_le_cho_model(
    client: IntegrationClient, control_plane: FakeControlPlane
) -> None:
    control_plane.next(
        json=[
            {
                "tool_id": "slack.post_message",
                "version": "1.0",
                "provider": "slack",
                "description": "Gửi tin nhắn",
                "side_effect": "write",
                "required_scopes": ["chat:write"],
                "input_schema": {"type": "object"},
            },
            {
                "tool_id": "slack.list_channels",
                "version": "1.0",
                "provider": "slack",
                "description": "Liệt kê kênh",
                "side_effect": "read",
                "required_scopes": [],
                "input_schema": {"type": "object"},
            },
        ]
    )

    result = await client.list_tools(
        authorization="Bearer token",
        organization_id="org_1",
        workspace_id="ws_1",
        allowed_tool_ids=frozenset({"slack.post_message"}),
    )

    # Đúng một tool lọt qua whitelist, và dấu chấm trong tool_id đã đổi thành
    # `__` — Anthropic/OpenAI đều cấm dấu chấm trong tên tool.
    assert result == (
        ModelToolSpec(
            name="slack__post_message",
            description="Gửi tin nhắn",
            input_schema={"type": "object"},
            version="1.0",
        ),
    )


async def test_gui_dung_header_tenant_va_token(
    client: IntegrationClient, control_plane: FakeControlPlane
) -> None:
    control_plane.next(json=[])

    await client.list_tools(
        authorization="Bearer token-xyz",
        organization_id="org_1",
        workspace_id="ws_1",
        allowed_tool_ids=frozenset({"slack.post_message"}),
    )

    sent = control_plane.requests[0]
    assert sent.headers["authorization"] == "Bearer token-xyz"
    assert sent.headers["x-organization-id"] == "org_1"
    assert sent.headers["x-workspace-id"] == "ws_1"
    assert sent.url.path == "/internal/v1/tools"
    assert sent.method == "GET"


# ─── invoke ──────────────────────────────────────────────────────────────────


def _invoke_response(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "invocation_id": "ti_1",
        "tool_id": "slack.post_message",
        "tool_version": "1.0",
        "status": "succeeded",
        "replayed": False,
        "attempts": 1,
        "duration_ms": 12.3,
        "provider_reference": "C1:123",
        "output": {"channel_id": "C1"},
    }
    base.update(overrides)
    return base


async def test_doi_ten_model_ve_tool_id_va_goi_dung_duong(
    client: IntegrationClient, control_plane: FakeControlPlane
) -> None:
    control_plane.next(json=_invoke_response())

    result = await client.invoke(
        name="slack__post_message",
        tool_input={"channel": "C1", "text": "hi"},
        connection_id="conn_1",
        authorization="Bearer token",
        organization_id="org_1",
        workspace_id="ws_1",
        idempotency_key="idm_1",
    )

    assert result.output == {"channel_id": "C1"}
    assert result.invocation_id == "ti_1"
    assert result.replayed is False

    sent = control_plane.requests[0]
    assert sent.url.path == "/internal/v1/tools/slack.post_message/invoke"
    assert sent.method == "POST"
    body = json.loads(sent.content)
    assert body["connection_id"] == "conn_1"
    assert body["input"] == {"channel": "C1", "text": "hi"}
    assert body["idempotency_key"] == "idm_1"


async def test_dinh_kem_caller_context_de_ghi_trace(
    client: IntegrationClient, control_plane: FakeControlPlane
) -> None:
    control_plane.next(json=_invoke_response())

    await client.invoke(
        name="slack__post_message",
        tool_input={},
        connection_id="conn_1",
        authorization="Bearer token",
        organization_id="org_1",
        workspace_id="ws_1",
        caller=ToolCallContext(
            agent_id="agt_1", execution_trace_id="trc_1", parent_step_id="stp_1"
        ),
    )

    body = json.loads(control_plane.requests[0].content)
    assert body["caller"] == {
        "agent_id": "agt_1",
        "workflow_id": None,
        "execution_id": None,
        "node_id": None,
        "execution_trace_id": "trc_1",
        "parent_step_id": "stp_1",
    }


async def test_khong_khai_caller_thi_gui_toan_none(
    client: IntegrationClient, control_plane: FakeControlPlane
) -> None:
    control_plane.next(json=_invoke_response())

    await client.invoke(
        name="slack__post_message",
        tool_input={},
        connection_id="conn_1",
        authorization="Bearer token",
        organization_id="org_1",
        workspace_id="ws_1",
    )

    body = json.loads(control_plane.requests[0].content)
    assert body["caller"] == {
        "agent_id": None,
        "workflow_id": None,
        "execution_id": None,
        "node_id": None,
        "execution_trace_id": None,
        "parent_step_id": None,
    }


# ─── Chuẩn hoá lỗi (§37) ────────────────────────────────────────────────────


async def test_loi_cua_chinh_app_duoc_map_dung_category(
    client: IntegrationClient, control_plane: FakeControlPlane
) -> None:
    control_plane.next(
        status=429,
        json={
            "error": {
                "category": "RateLimited",
                "message": "Đã chạm hạn mức gọi Slack.",
                "retry_after_seconds": 5.0,
            }
        },
    )

    with pytest.raises(RateLimitedError) as exc_info:
        await client.invoke(
            name="slack__post_message",
            tool_input={},
            connection_id="conn_1",
            authorization="Bearer token",
            organization_id="org_1",
            workspace_id="ws_1",
        )

    assert exc_info.value.retry_after_seconds == 5.0
    assert exc_info.value.message == "Đã chạm hạn mức gọi Slack."


async def test_loi_authorization_duoc_map_dung(
    client: IntegrationClient, control_plane: FakeControlPlane
) -> None:
    control_plane.next(
        status=403,
        json={"error": {"category": "AuthorizationError", "message": "Không đủ quyền."}},
    )

    with pytest.raises(AuthorizationError):
        await client.list_tools(
            authorization="Bearer token",
            organization_id="org_1",
            workspace_id="ws_1",
            allowed_tool_ids=frozenset({"slack.post_message"}),
        )


async def test_loi_422_duoc_map_thanh_validation_error(
    client: IntegrationClient, control_plane: FakeControlPlane
) -> None:
    control_plane.next(
        status=422,
        json={"error": {"category": "ValidationError", "message": "Input sai."}},
    )

    with pytest.raises(ValidationError) as exc_info:
        await client.invoke(
            name="slack__post_message",
            tool_input={},
            connection_id="conn_1",
            authorization="Bearer token",
            organization_id="org_1",
            workspace_id="ws_1",
        )
    assert exc_info.value.retryable is False


async def test_loi_khong_dung_hinh_dang_app_roi_ve_theo_ma_http(
    client: IntegrationClient, control_plane: FakeControlPlane
) -> None:
    """502 từ một proxy hạ tầng, không phải từ chính app — không có `error`
    JSON đúng hình dạng."""
    control_plane.next(status=502, text="Bad Gateway")

    with pytest.raises(ProviderUnavailableError):
        await client.invoke(
            name="slack__post_message",
            tool_input={},
            connection_id="conn_1",
            authorization="Bearer token",
            organization_id="org_1",
            workspace_id="ws_1",
        )


async def test_loi_4xx_khong_dung_hinh_dang_app_roi_ve_internal_error(
    client: IntegrationClient, control_plane: FakeControlPlane
) -> None:
    control_plane.next(status=404, text="Not Found")

    with pytest.raises(InternalError):
        await client.invoke(
            name="slack__post_message",
            tool_input={},
            connection_id="conn_1",
            authorization="Bearer token",
            organization_id="org_1",
            workspace_id="ws_1",
        )


async def test_mat_ket_noi_thanh_provider_unavailable(
    client: IntegrationClient, control_plane: FakeControlPlane
) -> None:
    control_plane.next_error(httpx.ConnectError("boom"))

    with pytest.raises(ProviderUnavailableError):
        await client.list_tools(
            authorization="Bearer token",
            organization_id="org_1",
            workspace_id="ws_1",
            allowed_tool_ids=frozenset({"slack.post_message"}),
        )


async def test_timeout_thanh_platform_timeout_error(
    client: IntegrationClient, control_plane: FakeControlPlane
) -> None:
    control_plane.next_error(httpx.ReadTimeout("hết giờ"))

    with pytest.raises(PlatformTimeoutError):
        await client.list_tools(
            authorization="Bearer token",
            organization_id="org_1",
            workspace_id="ws_1",
            allowed_tool_ids=frozenset({"slack.post_message"}),
        )
