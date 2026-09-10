"""Router HTTP + SSE của Agent Runtime (`entrypoints/agent.py`), chạy trên
PostgreSQL và Redis thật.

`ModelClient`, `ToolCatalog`/`ToolExecutor` (`IntegrationClient`) và
`ConnectionResolver` đều bị thay bằng giả qua `app.dependency_overrides` —
ba collaborator duy nhất Agent Runtime gọi ra ngoài tiến trình của chính nó
(§2.4: nó không được phép cầm credential để tự gọi Anthropic/OpenAI/Slack
thật). Mọi thứ bên trong tiến trình — phân giải tenant, `AgentAdminService`,
`AgentRuntime`, ghi Execution Trace, luồng SSE — là đường chạy thật.

Tenant (organization/workspace/membership) được dựng thẳng qua `TenantService`
trong cùng một transaction, không qua HTTP của Control Plane — Agent Runtime
là một tiến trình riêng, không mount router của Tenant. Access token được ký
thẳng bằng `issue_access_token`, cùng bí mật `AW_JWT_SECRET` mà
`require_user_id` dùng để giải mã, nên không cần trải qua `/v1/auth/register`.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

import pytest
from conftest import infra_unavailable

os.environ.setdefault("AW_JWT_SECRET", "test-secret-khong-dung-that-nhung-du-dai-32")
os.environ.setdefault("AW_KMS_LOCAL_MASTER_KEY", "test-master-key-khong-dung-that")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

import app.entrypoints.agent as agent_entrypoint  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import session_scope  # noqa: E402
from app.core.errors import ConnectionChoiceRequiredError  # noqa: E402
from app.core.security import issue_access_token  # noqa: E402
from app.domains.agent.application.ports import (  # noqa: E402
    ModelResponse,
    ModelToolCall,
    ModelToolSpec,
    ModelUsage,
    StopReason,
    ToolCallContext,
    ToolInvocationResult,
)
from app.domains.agent.application.tool_naming import to_model_name  # noqa: E402
from app.domains.tenant.application.service import TenantService  # noqa: E402
from app.domains.tenant.infrastructure.repositories import (  # noqa: E402
    SqlAlchemyMembershipRepository,
    SqlAlchemyOrganizationRepository,
    SqlAlchemyWorkspaceRepository,
)
from app.entrypoints.agent import create_app as create_agent_app  # noqa: E402

pytestmark = pytest.mark.integration

ORG_HEADER = "x-organization-id"
WS_HEADER = "x-workspace-id"

TOOL_ID = "slack.post_message"
TOOL_SPEC = ModelToolSpec(
    name=to_model_name(TOOL_ID),
    description="Gửi tin nhắn Slack",
    input_schema={"type": "object"},
    version="1.0",
)


# ─── Model, Integration, Connection giả ──────────────────────────────────────


class ScriptedModelClient:
    """Trả về đúng response/exception đã định trước, theo thứ tự — cùng cách
    `test_agent_service.py` giả `ModelClient`, chỉ khác là ở đây nó chạy sau
    một request HTTP thật thay vì được gọi trực tiếp."""

    def __init__(self, script: Sequence[ModelResponse | Exception]) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        *,
        model: str,
        system: str,
        turns: Sequence[Any],
        tools: Sequence[ModelToolSpec] = (),
        max_tokens: int = 8192,
    ) -> ModelResponse:
        self.calls.append({"model": model, "tools": list(tools)})
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _end_turn(text_: str) -> ModelResponse:
    return ModelResponse(
        text=text_,
        tool_calls=(),
        stop_reason=StopReason.END_TURN,
        usage=ModelUsage(input_tokens=10, output_tokens=5),
        model="claude-sonnet-5",
    )


def _tool_use(*calls: ModelToolCall, text_: str = "") -> ModelResponse:
    return ModelResponse(
        text=text_,
        tool_calls=calls,
        stop_reason=StopReason.TOOL_USE,
        usage=ModelUsage(input_tokens=10, output_tokens=5),
        model="claude-sonnet-5",
    )


class FakeIntegration:
    """Một instance đóng cả `ToolCatalog` lẫn `ToolExecutor` — đúng cách
    `IntegrationClient` thật làm, và cách `get_integration_client` ở
    `entrypoints/agent.py` nối nó vào `AgentRuntime`."""

    def __init__(self, specs: Sequence[ModelToolSpec] = ()) -> None:
        self._specs = tuple(specs)
        self.invoke_calls: list[dict[str, Any]] = []

    async def list_tools(
        self,
        *,
        authorization: str,
        organization_id: str | None,
        workspace_id: str | None,
        allowed_tool_ids: frozenset[str],
    ) -> Sequence[ModelToolSpec]:
        return tuple(spec for spec in self._specs if to_model_name(TOOL_ID) == spec.name)

    async def invoke(
        self,
        *,
        name: str,
        tool_input: Mapping[str, Any],
        connection_id: str,
        authorization: str,
        organization_id: str | None,
        workspace_id: str | None,
        idempotency_key: str | None = None,
        caller: ToolCallContext | None = None,
    ) -> ToolInvocationResult:
        self.invoke_calls.append(
            {
                "name": name,
                "connection_id": connection_id,
                "tool_input": dict(tool_input),
                "idempotency_key": idempotency_key,
            }
        )
        return ToolInvocationResult(output={"ok": True}, invocation_id="ti_fake", replayed=False)


class FakeConnectionResolver:
    def __init__(self, connection_id: str | Exception = "conn_fake") -> None:
        self.connection_id = connection_id
        self.calls: list[str] = []

    async def resolve(
        self,
        *,
        tool_id: str,
        authorization: str,
        organization_id: str | None,
        workspace_id: str | None,
    ) -> str:
        self.calls.append(tool_id)
        if isinstance(self.connection_id, Exception):
            raise self.connection_id
        return self.connection_id


# ─── Hạ tầng test: app thật, tenant thật, engine thật ────────────────────────


class _UnusedUserDirectory:
    """`TenantService.create_organization`/`require_access` không chạm
    `UserDirectory` — xem lý do tương tự ở `entrypoints/agent.py`."""

    async def get_profiles(self, user_ids: Any) -> dict[str, Any]:
        raise NotImplementedError

    async def find_by_email(self, email: str) -> Any:
        raise NotImplementedError


async def _ping_postgres() -> None:
    async with session_scope() as session:
        await session.execute(text("SELECT 1"))


async def _provision_tenant(user_id: str, org_name: str) -> tuple[str, str]:
    async with session_scope() as session:
        service = TenantService(
            organizations=SqlAlchemyOrganizationRepository(session),
            workspaces=SqlAlchemyWorkspaceRepository(session),
            memberships=SqlAlchemyMembershipRepository(session),
            users=_UnusedUserDirectory(),
        )
        access = await service.create_organization(user_id=user_id, name=org_name)
    assert access.workspace_id is not None
    return access.organization_id, access.workspace_id


@contextmanager
def agent_client(
    model: Any = None, integration: Any = None, connections: Any = None
) -> Iterator[TestClient]:
    """Dựng `entrypoints.agent`'s app thật, thay ba collaborator ra ngoài
    tiến trình bằng giả qua `dependency_overrides`.

    Engine/Redis được `create_app()`'s lifespan tự khởi tạo lúc
    `TestClient.__enter__` — không tự gọi `init_engine` ở đây, để tránh dựng
    một pool asyncpg gắn với event loop khác với loop mà `TestClient` dùng để
    chạy app (xem `seed()` ở `test_integration_api.py`).
    """
    app = create_agent_app()
    app.dependency_overrides[agent_entrypoint.get_model_client] = lambda: model
    app.dependency_overrides[agent_entrypoint.get_integration_client] = lambda: integration
    app.dependency_overrides[agent_entrypoint.get_connection_resolver] = lambda: connections

    with TestClient(app) as running:
        assert running.portal is not None
        try:
            running.portal.call(_ping_postgres)
        except Exception as exc:  # noqa: BLE001
            infra_unavailable(f"PostgreSQL chưa sẵn sàng: {type(exc).__name__}. {exc}")
        yield running


class Nguoi:
    def __init__(
        self, *, user_id: str, organization_id: str, workspace_id: str, token: str
    ) -> None:
        self.user_id = user_id
        self.organization_id = organization_id
        self.workspace_id = workspace_id
        self.token = token

    def headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self.token}",
            ORG_HEADER: self.organization_id,
            WS_HEADER: self.workspace_id,
        }


def _make_user(client: TestClient, ten: str) -> Nguoi:
    assert client.portal is not None
    user_id = f"usr_{uuid.uuid4().hex}"
    org_id, workspace_id = client.portal.call(
        _provision_tenant, user_id, f"Org {ten} {uuid.uuid4().hex[:6]}"
    )
    token, _claims = issue_access_token(get_settings(), subject=user_id)
    return Nguoi(user_id=user_id, organization_id=org_id, workspace_id=workspace_id, token=token)


def _sse_events(body: str) -> list[tuple[str | None, dict[str, Any]]]:
    events: list[tuple[str | None, dict[str, Any]]] = []
    event_name: str | None = None
    for line in body.splitlines():
        if line.startswith("event:"):
            event_name = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            raw = line.removeprefix("data:").strip()
            events.append((event_name, json.loads(raw) if raw else {}))
    return events


# ─── Agent / Conversation — CRUD ─────────────────────────────────────────────


def test_tao_va_liet_ke_agent_va_hoi_thoai() -> None:
    with agent_client(
        ScriptedModelClient([]), FakeIntegration(), FakeConnectionResolver()
    ) as agent:
        nguoi = _make_user(agent, "An")

        created = agent.post(
            "/v1/agents",
            headers=nguoi.headers(),
            json={
                "name": "  Trợ lý   Slack  ",
                "model": "claude-sonnet-5",
                "enabled_tool_ids": [TOOL_ID],
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["name"] == "Trợ lý Slack"
        assert body["id"].startswith("agt_")
        assert body["enabled_tool_ids"] == [TOOL_ID]
        assert body["archived_at"] is None

        listed = agent.get("/v1/agents", headers=nguoi.headers()).json()
        assert [a["id"] for a in listed] == [body["id"]]

        conversation = agent.post(f"/v1/agents/{body['id']}/conversations", headers=nguoi.headers())
        assert conversation.status_code == 201, conversation.text
        conv_body = conversation.json()
        assert conv_body["agent_id"] == body["id"]
        assert conv_body["status"] == "active"

        conv_list = agent.get(
            f"/v1/agents/{body['id']}/conversations", headers=nguoi.headers()
        ).json()
        assert [c["id"] for c in conv_list] == [conv_body["id"]]


def test_luu_tru_roi_khoi_phuc_mot_agent() -> None:
    with agent_client(
        ScriptedModelClient([]), FakeIntegration(), FakeConnectionResolver()
    ) as agent:
        nguoi = _make_user(agent, "Bình")
        agent_row = agent.post(
            "/v1/agents",
            headers=nguoi.headers(),
            json={"name": "Trợ lý", "model": "claude-sonnet-5", "enabled_tool_ids": []},
        ).json()

        archived = agent.post(f"/v1/agents/{agent_row['id']}/archive", headers=nguoi.headers())
        assert archived.status_code == 200, archived.text
        assert archived.json()["archived_at"] is not None

        # Lưu trữ hai lần bị từ chối.
        again = agent.post(f"/v1/agents/{agent_row['id']}/archive", headers=nguoi.headers())
        assert again.status_code == 422, again.text

        restored = agent.post(f"/v1/agents/{agent_row['id']}/unarchive", headers=nguoi.headers())
        assert restored.status_code == 200, restored.text
        assert restored.json()["archived_at"] is None

        # Khôi phục một agent đang hoạt động cũng bị từ chối.
        noop = agent.post(f"/v1/agents/{agent_row['id']}/unarchive", headers=nguoi.headers())
        assert noop.status_code == 422, noop.text


# ─── Gửi tin nhắn — đường chạy đầy đủ qua SSE ─────────────────────────────────


def test_gui_tin_nhan_khong_can_tool_tra_ve_final_qua_sse() -> None:
    model = ScriptedModelClient([_end_turn("Xin chào! Tôi giúp được gì?")])

    with agent_client(model, FakeIntegration(), FakeConnectionResolver()) as agent:
        nguoi = _make_user(agent, "Binh")
        agent_row = agent.post(
            "/v1/agents",
            headers=nguoi.headers(),
            json={"name": "Trợ lý", "model": "claude-sonnet-5"},
        ).json()
        conversation = agent.post(
            f"/v1/agents/{agent_row['id']}/conversations", headers=nguoi.headers()
        ).json()

        reply = agent.post(
            f"/v1/conversations/{conversation['id']}/messages",
            headers=nguoi.headers(),
            json={"content": "Chào bạn"},
        )
        assert reply.status_code == 200, reply.text
        events = _sse_events(reply.text)
        assert [name for name, _ in events] == ["final"]
        assert events[0][1]["text"] == "Xin chào! Tôi giúp được gì?"

        messages = agent.get(
            f"/v1/conversations/{conversation['id']}/messages", headers=nguoi.headers()
        ).json()
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[1]["content"] == "Xin chào! Tôi giúp được gì?"


def test_gui_tin_nhan_goi_tool_thanh_cong_phat_du_su_kien() -> None:
    model = ScriptedModelClient(
        [
            _tool_use(
                ModelToolCall(
                    call_id="call_1",
                    name=to_model_name(TOOL_ID),
                    input={"channel": "#general", "text": "hi"},
                )
            ),
            _end_turn("Đã gửi xong."),
        ]
    )
    integration = FakeIntegration(specs=(TOOL_SPEC,))
    connections = FakeConnectionResolver()

    with agent_client(model, integration, connections) as agent:
        nguoi = _make_user(agent, "Chi")
        agent_row = agent.post(
            "/v1/agents",
            headers=nguoi.headers(),
            json={
                "name": "Trợ lý Slack",
                "model": "claude-sonnet-5",
                "enabled_tool_ids": [TOOL_ID],
            },
        ).json()
        conversation = agent.post(
            f"/v1/agents/{agent_row['id']}/conversations", headers=nguoi.headers()
        ).json()

        reply = agent.post(
            f"/v1/conversations/{conversation['id']}/messages",
            headers=nguoi.headers(),
            json={"content": "Gửi tin nhắn cho #general giúp tôi"},
        )
        assert reply.status_code == 200, reply.text
        events = _sse_events(reply.text)
        assert [name for name, _ in events] == [
            "tool_call_started",
            "tool_call_completed",
            "final",
        ]
        assert events[0][1]["tool_id"] == TOOL_ID
        assert events[1][1].get("is_error", False) is False
        assert events[2][1]["text"] == "Đã gửi xong."

        assert connections.calls == [TOOL_ID]
        assert integration.invoke_calls[0]["connection_id"] == "conn_fake"

        messages = agent.get(
            f"/v1/conversations/{conversation['id']}/messages", headers=nguoi.headers()
        ).json()
        assert [m["role"] for m in messages] == ["user", "assistant", "tool", "assistant"]


def test_agent_da_luu_tru_bao_loi_qua_su_kien_error_khong_phai_http_500() -> None:
    """Lỗi phát sinh giữa lượt (ở đây: agent đã lưu trữ) phải thoát ra như một
    sự kiện `error` trên cùng luồng SSE — HTTP status đã là 200 từ lúc luồng
    bắt đầu, không còn cách nào đổi lại thành 4xx/5xx nữa."""
    with agent_client(
        ScriptedModelClient([]), FakeIntegration(), FakeConnectionResolver()
    ) as agent:
        nguoi = _make_user(agent, "Dung")
        agent_row = agent.post(
            "/v1/agents",
            headers=nguoi.headers(),
            json={"name": "Trợ lý", "model": "claude-sonnet-5"},
        ).json()
        conversation = agent.post(
            f"/v1/agents/{agent_row['id']}/conversations", headers=nguoi.headers()
        ).json()

        archived = agent.post(f"/v1/agents/{agent_row['id']}/archive", headers=nguoi.headers())
        assert archived.status_code == 200, archived.text

        reply = agent.post(
            f"/v1/conversations/{conversation['id']}/messages",
            headers=nguoi.headers(),
            json={"content": "Còn dùng được không?"},
        )
        assert reply.status_code == 200, reply.text
        events = _sse_events(reply.text)
        assert [name for name, _ in events] == ["error"]
        assert "lưu trữ" in events[0][1]["message"]


def test_can_chon_connection_qua_sse_roi_luu_va_dung_lai_o_luot_sau() -> None:
    """ADR-0004: mơ hồ (≥2 ứng viên) không được đoán — lỗi phải mang cấu trúc
    qua SSE (`error_category`/`error_details`), người dùng chọn qua
    `POST .../connections`, lượt gửi tin kế tiếp trong CÙNG conversation dùng
    lại lựa chọn đó mà không resolve lại lần nữa."""
    candidates = [
        {
            "connection_id": "conn_a",
            "display_name": "Acme HQ",
            "sharing": "workspace",
            "external_account_label": None,
        },
        {
            "connection_id": "conn_b",
            "display_name": "Cá nhân",
            "sharing": "private",
            "external_account_label": None,
        },
    ]
    model = ScriptedModelClient(
        [
            _tool_use(
                ModelToolCall(
                    call_id="call_1",
                    name=to_model_name(TOOL_ID),
                    input={"channel": "#general", "text": "hi"},
                )
            ),
            _end_turn("Bạn cần chọn tài khoản Slack trước đã."),
            _tool_use(
                ModelToolCall(
                    call_id="call_2",
                    name=to_model_name(TOOL_ID),
                    input={"channel": "#general", "text": "hi lại"},
                )
            ),
            _end_turn("Đã gửi xong."),
        ]
    )
    integration = FakeIntegration(specs=(TOOL_SPEC,))
    connections = FakeConnectionResolver(
        ConnectionChoiceRequiredError(
            "Có 2 kết nối slack khả dụng — cần chọn một trước khi dùng tool này.",
            details={"provider": "slack", "candidates": candidates},
        )
    )

    with agent_client(model, integration, connections) as agent:
        nguoi = _make_user(agent, "Ha")
        agent_row = agent.post(
            "/v1/agents",
            headers=nguoi.headers(),
            json={
                "name": "Trợ lý Slack",
                "model": "claude-sonnet-5",
                "enabled_tool_ids": [TOOL_ID],
            },
        ).json()
        conversation = agent.post(
            f"/v1/agents/{agent_row['id']}/conversations", headers=nguoi.headers()
        ).json()

        first = agent.post(
            f"/v1/conversations/{conversation['id']}/messages",
            headers=nguoi.headers(),
            json={"content": "Gửi tin nhắn cho #general giúp tôi"},
        )
        assert first.status_code == 200, first.text
        events = _sse_events(first.text)
        completed = next(data for name, data in events if name == "tool_call_completed")
        assert completed["is_error"] is True
        assert completed["error_category"] == "ConnectionChoiceRequired"
        assert completed["error_details"]["candidates"] == candidates
        assert connections.calls == ["slack.post_message"]

        picked = agent.post(
            f"/v1/conversations/{conversation['id']}/connections",
            headers=nguoi.headers(),
            json={"provider": "slack", "connection_id": "conn_b"},
        )
        assert picked.status_code == 204, picked.text

        # Đọc lại được — dải "đang dùng" của giao diện chat dựng từ đây.
        listed = agent.get(
            f"/v1/conversations/{conversation['id']}/connections", headers=nguoi.headers()
        )
        assert listed.status_code == 200, listed.text
        assert listed.json() == [{"provider": "slack", "connection_id": "conn_b"}]

        second = agent.post(
            f"/v1/conversations/{conversation['id']}/messages",
            headers=nguoi.headers(),
            json={"content": "Gửi lại giúp tôi"},
        )
        assert second.status_code == 200, second.text
        events2 = _sse_events(second.text)
        assert [name for name, _ in events2] == [
            "tool_call_started",
            "tool_call_completed",
            "final",
        ]
        assert events2[1][1].get("is_error", False) is False

        # Store đã chặn — resolver không bị gọi lại lần thứ hai.
        assert connections.calls == ["slack.post_message"]
        assert integration.invoke_calls[-1]["connection_id"] == "conn_b"
        # Tác động ghi luôn đi kèm khoá idempotency — thiếu thì Integration
        # Gateway thật từ chối thẳng (ValidationError).
        last = integration.invoke_calls[-1]
        assert last["idempotency_key"] and "slack.post_message" in last["idempotency_key"]


# ─── Tenant — header bắt buộc ────────────────────────────────────────────────


def test_thieu_x_workspace_id_header_tra_ve_422() -> None:
    with agent_client(
        ScriptedModelClient([]), FakeIntegration(), FakeConnectionResolver()
    ) as agent:
        nguoi = _make_user(agent, "Em")
        reply = agent.get(
            "/v1/agents",
            headers={
                "authorization": f"Bearer {nguoi.token}",
                ORG_HEADER: nguoi.organization_id,
            },
        )
        assert reply.status_code == 422


def test_thieu_authorization_header_tra_ve_401() -> None:
    with agent_client(
        ScriptedModelClient([]), FakeIntegration(), FakeConnectionResolver()
    ) as agent:
        reply = agent.get("/v1/agents", headers={ORG_HEADER: "org_x", WS_HEADER: "ws_x"})
        assert reply.status_code == 401


def test_khong_phai_thanh_vien_organization_tra_ve_403() -> None:
    with agent_client(
        ScriptedModelClient([]), FakeIntegration(), FakeConnectionResolver()
    ) as agent:
        nguoi = _make_user(agent, "Giang")
        assert agent.portal is not None
        another_org, another_workspace = agent.portal.call(
            _provision_tenant, f"usr_{uuid.uuid4().hex}", f"Org khác {uuid.uuid4().hex[:6]}"
        )

        reply = agent.get(
            "/v1/agents",
            headers={
                "authorization": f"Bearer {nguoi.token}",
                ORG_HEADER: another_org,
                WS_HEADER: another_workspace,
            },
        )
        assert reply.status_code == 403
