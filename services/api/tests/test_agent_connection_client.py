"""Kiểm tra `ConnectionResolverClient` bằng `httpx.MockTransport`.

Cùng kỹ thuật `test_agent_integration_client.py`: thay `httpx.AsyncClient` để
chèn `MockTransport`, nên mọi tầng bên trong là đường chạy thật, chỉ ranh giới
mạng ngoài cùng là giả.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.core.errors import (
    AuthenticationError,
    ConnectionChoiceRequiredError,
    MissingConnectionError,
)
from app.domains.agent.infrastructure.connection_client import ConnectionResolverClient


class FakeControlPlane:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.replies: list[httpx.Response] = []

    def next(self, status: int = 200, **kwargs: Any) -> None:
        self.replies.append(httpx.Response(status, **kwargs))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.replies:
            return self.replies.pop(0)
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
def client() -> ConnectionResolverClient:
    return ConnectionResolverClient("http://control-plane.local")


def _connection(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "conn_1",
        "provider": "slack",
        "status": "active",
        "sharing": "workspace",
    }
    base.update(overrides)
    return base


async def test_loc_dung_provider_tu_tool_id(
    client: ConnectionResolverClient, control_plane: FakeControlPlane
) -> None:
    control_plane.next(
        json=[_connection(id="conn_slack"), _connection(id="conn_google", provider="google")]
    )

    connection_id = await client.resolve(
        tool_id="slack.post_message",
        authorization="Bearer token",
        organization_id="org_1",
        workspace_id="ws_1",
    )

    assert connection_id == "conn_slack"


async def test_bo_qua_ket_noi_khong_active(
    client: ConnectionResolverClient, control_plane: FakeControlPlane
) -> None:
    control_plane.next(
        json=[_connection(id="conn_disabled", status="disabled"), _connection(id="conn_active")]
    )

    connection_id = await client.resolve(
        tool_id="slack.post_message",
        authorization="Bearer token",
        organization_id="org_1",
        workspace_id="ws_1",
    )

    assert connection_id == "conn_active"


async def test_hai_ket_noi_kha_dung_bao_can_chon_khong_tu_doan(
    client: ConnectionResolverClient, control_plane: FakeControlPlane
) -> None:
    """ADR-0004: mơ hồ (≥2 ứng viên) không còn tự chọn — kể cả khi một cái
    dùng chung workspace, một cái riêng tư (hành vi cũ đã bỏ)."""
    control_plane.next(
        json=[
            _connection(
                id="conn_private",
                sharing="private",
                display_name="Cá nhân",
                external_account_label="me@acme.com",
            ),
            _connection(
                id="conn_workspace",
                sharing="workspace",
                display_name="Acme HQ",
                external_account_label=None,
            ),
        ]
    )

    with pytest.raises(ConnectionChoiceRequiredError) as exc_info:
        await client.resolve(
            tool_id="slack.post_message",
            authorization="Bearer token",
            organization_id="org_1",
            workspace_id="ws_1",
        )

    details = exc_info.value.details
    assert details["provider"] == "slack"
    assert {c["connection_id"] for c in details["candidates"]} == {"conn_private", "conn_workspace"}
    private = next(c for c in details["candidates"] if c["connection_id"] == "conn_private")
    assert private["display_name"] == "Cá nhân"
    assert private["sharing"] == "private"
    assert private["external_account_label"] == "me@acme.com"


async def test_khong_co_ket_noi_nao_bao_missing_connection_error(
    client: ConnectionResolverClient, control_plane: FakeControlPlane
) -> None:
    control_plane.next(json=[_connection(provider="google")])

    with pytest.raises(MissingConnectionError) as exc_info:
        await client.resolve(
            tool_id="slack.post_message",
            authorization="Bearer token",
            organization_id="org_1",
            workspace_id="ws_1",
        )

    assert exc_info.value.details["provider"] == "slack"


async def test_gui_dung_header_tenant_va_token(
    client: ConnectionResolverClient, control_plane: FakeControlPlane
) -> None:
    control_plane.next(json=[_connection()])

    await client.resolve(
        tool_id="slack.post_message",
        authorization="Bearer token-xyz",
        organization_id="org_1",
        workspace_id="ws_1",
    )

    sent = control_plane.requests[0]
    assert sent.headers["authorization"] == "Bearer token-xyz"
    assert sent.headers["x-organization-id"] == "org_1"
    assert sent.headers["x-workspace-id"] == "ws_1"
    assert sent.url.path == "/v1/connections"


async def test_loi_cua_control_plane_duoc_chuan_hoa(
    client: ConnectionResolverClient, control_plane: FakeControlPlane
) -> None:
    control_plane.next(
        status=401, json={"error": {"category": "AuthenticationError", "message": "Token sai."}}
    )

    with pytest.raises(AuthenticationError):
        await client.resolve(
            tool_id="slack.post_message",
            authorization="Bearer token-sai",
            organization_id="org_1",
            workspace_id="ws_1",
        )
