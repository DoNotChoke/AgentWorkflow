"""Endpoint gọi tool nội bộ, chạy trên hạ tầng thật.

Postgres và Redis thật, `create_app()` thật, cổng credential thật đi qua mã hoá
thật. Chỉ Slack bị giả, và giả ở **ranh giới ngoài cùng** — `httpx` — nên mọi
chặng bên trong đều là đường chạy thật.

Đây là chỗ duy nhất chứng minh Gateway, sổ idempotency, rate limit, breaker và
cổng credential thực sự nối được với nhau.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from conftest import infra_unavailable

os.environ.setdefault("AW_JWT_SECRET", "test-secret-khong-dung-that-nhung-du-dai-32")
os.environ.setdefault("AW_KMS_LOCAL_MASTER_KEY", "test-master-key-khong-dung-that")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.db import session_scope  # noqa: E402
from app.domains.connection.domain.entities import ConnectionStatus, OAuthTokens  # noqa: E402
from app.domains.connection.domain.providers import SLACK  # noqa: E402
from app.entrypoints.http import create_app  # noqa: E402

pytestmark = pytest.mark.integration

TOOLS = "/internal/v1/tools"


# ─── Slack giả, chỉ ở ranh giới httpx ────────────────────────────────────────


class FakeSlack:
    """Thay `httpx.AsyncClient` — không gói tin nào rời khỏi tiến trình."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.replies: list[httpx.Response] = []

    def next(self, status: int = 200, **kwargs: Any) -> None:
        self.replies.append(httpx.Response(status, **kwargs))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.replies:
            return self.replies.pop(0)
        return httpx.Response(200, json={"ok": True, "channels": []})


@pytest.fixture
def slack(monkeypatch: pytest.MonkeyPatch) -> FakeSlack:
    fake = FakeSlack()
    real = httpx.AsyncClient

    def build(**kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return real(transport=httpx.MockTransport(fake.handle), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", build)
    return fake


# ─── App thật ────────────────────────────────────────────────────────────────


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as running:
        health = running.get("/readyz").json()
        if health["checks"].get("postgres") != "ok":
            infra_unavailable("PostgreSQL chưa chạy — `docker compose up -d postgres`.")
        if health["checks"].get("redis") != "ok":
            infra_unavailable("Redis chưa chạy — `docker compose up -d redis`.")
        _clear_rate_limits(running)
        yield running


def _clear_rate_limits(client: TestClient) -> None:
    """Mỗi test dựng một người dùng mới, nên hạn mức đăng ký chạm trần rất nhanh.

    Cùng cách mà `test_connection_api.py` làm — hạn mức ở đó bảo vệ production,
    không phải để làm khó test.
    """
    from app.core.redis import get_redis

    async def clear() -> None:
        redis = get_redis()
        for pattern in ("aw:g:register:*", "aw:g:login:*", "aw:g:tool-invoke:*"):
            async for key in redis.scan_iter(match=pattern):
                await redis.delete(key)

    assert client.portal is not None
    client.portal.call(clear)


class Nguoi:
    """Một người dùng thật: đăng ký qua API, mang token trong mọi request."""

    def __init__(self, client: TestClient, ten: str) -> None:
        self.client = client
        self.email = f"{ten}-{uuid.uuid4().hex[:8]}@test.local"
        created = client.post(
            "/v1/auth/register",
            json={"email": self.email, "password": "Mat-khau-du-dai-123", "display_name": ten},
        )
        assert created.status_code == 201, created.text
        self.token = created.json()["access_token"]

    def post(self, path: str, **kwargs: Any) -> Any:
        return self.client.post(path, headers=self._headers(), **kwargs)

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.client.get(path, headers=self._headers(), **kwargs)

    def _headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.token}"}


async def _seed_connection(owner_user_id: str, org: str, workspace: str, sharing: str) -> str:
    """Cắm một kết nối Slack đã hoạt động, kèm credential mã hoá thật."""
    from datetime import UTC, datetime

    from app.core.config import get_settings
    from app.domains.connection.domain.entities import Connection, SharingPolicy
    from app.domains.connection.infrastructure.credential_store import build_credential_store
    from app.domains.connection.infrastructure.repositories import SqlAlchemyConnectionRepository

    connection_id = f"conn_{uuid.uuid4().hex}"
    now = datetime.now(UTC)

    async with session_scope() as session:
        await SqlAlchemyConnectionRepository(session).add(
            Connection(
                id=connection_id,
                organization_id=org,
                workspace_id=workspace,
                provider="slack",
                display_name="Slack — Test",
                status=ConnectionStatus.ACTIVE,
                granted_scopes=frozenset(SLACK.default_scopes),
                connected_by=owner_user_id,
                created_at=now,
                updated_at=now,
                sharing=SharingPolicy(sharing),
                owner_user_id=owner_user_id,
                external_account_id=f"T{uuid.uuid4().hex[:8].upper()}",
                external_account_label="Acme",
            )
        )
        await build_credential_store(session, get_settings()).save(
            connection_id=connection_id,
            tenant_id=org,
            tokens=OAuthTokens(
                access_token="xoxb-token-that", scopes=frozenset(SLACK.default_scopes)
            ),
            at=now,
        )

    return connection_id


def seed(client: TestClient, owner_user_id: str, org: str, workspace: str, sharing: str) -> str:
    """Cắm kết nối qua portal của `TestClient`.

    Engine gắn với event loop mà `TestClient` dựng, nên một `async def test_`
    của pytest-asyncio sẽ chạy ở loop khác và asyncpg từ chối. `portal.call`
    đưa coroutine về đúng loop — cùng cách `test_connection_api.py` đọc hàng
    credential thô.
    """
    assert client.portal is not None
    return str(client.portal.call(_seed_connection, owner_user_id, org, workspace, sharing))


async def _status_of(connection_id: str) -> str:
    async with session_scope() as session:
        row = await session.execute(
            text("SELECT status FROM connection.connections WHERE id = :id"),
            {"id": connection_id},
        )
    return str(row.scalar_one())


def _tenant_of(nguoi: Nguoi) -> tuple[str, str]:
    org = nguoi.get("/v1/organizations").json()[0]["id"]
    workspace = nguoi.get("/v1/workspaces").json()[0]["id"]
    return org, workspace


# ─── Danh mục ────────────────────────────────────────────────────────────────


def test_liet_ke_du_bon_tool_slack(client: TestClient) -> None:
    an = Nguoi(client, "An")

    tools = an.get(TOOLS).json()

    assert {tool["tool_id"] for tool in tools} == {
        "slack.list_channels",
        "slack.post_message",
        "slack.list_users",
        "slack.get_channel_history",
    }


def test_danh_muc_mang_du_thu_agent_can_o_phase_3(client: TestClient) -> None:
    """Phase 3 dựng tool list cho LLM từ đúng phản hồi này."""
    an = Nguoi(client, "An")

    post = next(t for t in an.get(TOOLS).json() if t["tool_id"] == "slack.post_message")

    assert post["side_effect"] == "write"
    assert post["required_scopes"] == ["chat:write"]
    assert post["input_schema"]["required"] == ["channel", "text"]
    assert len(post["description"]) > 40


def test_chua_dang_nhap_thi_khong_xem_duoc(client: TestClient) -> None:
    assert client.get(TOOLS).status_code == 401


# ─── Gọi tool thật, xuyên toàn bộ đường chạy ─────────────────────────────────


def test_goi_tool_doc_chay_tron_duong(client: TestClient, slack: FakeSlack) -> None:
    an = Nguoi(client, "An")
    org, workspace = _tenant_of(an)
    connection = seed(client, _user_id(an), org, workspace, "workspace")

    slack.next(json={"ok": True, "channels": [{"id": "C1", "name": "general"}]})

    reply = an.post(
        f"{TOOLS}/slack.list_channels/invoke",
        json={"connection_id": connection, "input": {"limit": 5}},
    )

    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["output"]["channels"] == [{"id": "C1", "name": "general"}]
    assert body["attempts"] == 1
    assert body["replayed"] is False
    assert body["invocation_id"].startswith("ti_")


def _user_id(nguoi: Nguoi) -> str:
    return str(nguoi.get("/v1/auth/me").json()["id"])


def test_token_da_ma_hoa_duoc_giai_ma_va_gan_vao_request(
    client: TestClient, slack: FakeSlack
) -> None:
    """Đường mà `test_connection_api` đã dự đoán từ Phase 1, giờ chạy thật."""
    an = Nguoi(client, "An")
    org, workspace = _tenant_of(an)
    connection = seed(client, _user_id(an), org, workspace, "workspace")

    an.post(
        f"{TOOLS}/slack.list_channels/invoke",
        json={"connection_id": connection, "input": {}},
    )

    assert slack.requests[0].headers["authorization"] == "Bearer xoxb-token-that"


def test_phan_hoi_khong_bao_gio_chua_token(client: TestClient, slack: FakeSlack) -> None:
    """§2.4 — kiểm cả thân phản hồi thô, không chỉ các trường mình biết tên."""
    an = Nguoi(client, "An")
    org, workspace = _tenant_of(an)
    connection = seed(client, _user_id(an), org, workspace, "workspace")

    raw = an.post(
        f"{TOOLS}/slack.list_channels/invoke",
        json={"connection_id": connection, "input": {}},
    ).text

    assert "xoxb" not in raw


# ─── Idempotency xuyên qua database thật ─────────────────────────────────────


def test_cung_khoa_hai_lan_thi_slack_chi_bi_goi_mot_lan(
    client: TestClient, slack: FakeSlack
) -> None:
    """Bất biến quan trọng nhất của Phase 2, chạy qua PostgreSQL thật."""
    an = Nguoi(client, "An")
    org, workspace = _tenant_of(an)
    connection = seed(client, _user_id(an), org, workspace, "workspace")

    slack.next(json={"ok": True, "channel": "C123", "ts": "1712345678.000100"})
    body = {
        "connection_id": connection,
        "input": {"channel": "C123", "text": "xin chào"},
        "idempotency_key": f"idm_{uuid.uuid4().hex}",
    }

    first = an.post(f"{TOOLS}/slack.post_message/invoke", json=body)
    second = an.post(f"{TOOLS}/slack.post_message/invoke", json=body)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["replayed"] is False
    assert second.json()["replayed"] is True
    assert second.json()["provider_reference"] == first.json()["provider_reference"]
    assert len(slack.requests) == 1


def test_tool_write_thieu_khoa_bi_tu_choi(client: TestClient, slack: FakeSlack) -> None:
    an = Nguoi(client, "An")
    org, workspace = _tenant_of(an)
    connection = seed(client, _user_id(an), org, workspace, "workspace")

    reply = an.post(
        f"{TOOLS}/slack.post_message/invoke",
        json={"connection_id": connection, "input": {"channel": "C123", "text": "hi"}},
    )

    assert reply.status_code == 422
    assert slack.requests == []


# ─── Ranh giới §2.4 trên chính route này ─────────────────────────────────────


def test_khong_dung_duoc_ket_noi_rieng_cua_nguoi_khac(client: TestClient, slack: FakeSlack) -> None:
    """Kết nối PRIVATE dựa hoàn toàn vào `acting_user_id`."""
    an = Nguoi(client, "An")
    org, workspace = _tenant_of(an)
    cua_nguoi_khac = seed(client, "usr_nguoi_khac", org, workspace, "private")

    reply = an.post(
        f"{TOOLS}/slack.list_channels/invoke",
        json={"connection_id": cua_nguoi_khac, "input": {}},
    )

    assert reply.status_code == 403
    assert slack.requests == []


def test_khai_acting_user_id_trong_than_request_khong_co_tac_dung(
    client: TestClient, slack: FakeSlack
) -> None:
    """Cái bẫy §2.4 của route này.

    Nếu người gọi khai được mình đang hành động thay ai thì mọi kết nối riêng
    trong workspace mở toang. `InvokeRequest` cố ý không có trường đó, nên một
    trường thừa bị Pydantic bỏ qua và người thật vẫn là người đã xác thực.
    """
    an = Nguoi(client, "An")
    org, workspace = _tenant_of(an)
    cua_nguoi_khac = seed(client, "usr_nguoi_khac", org, workspace, "private")

    reply = an.post(
        f"{TOOLS}/slack.list_channels/invoke",
        json={
            "connection_id": cua_nguoi_khac,
            "input": {},
            "acting_user_id": "usr_nguoi_khac",
        },
    )

    assert reply.status_code == 403
    assert slack.requests == []


# ─── Lỗi provider được chuẩn hoá tới tận HTTP ────────────────────────────────


def test_slack_tiet_luu_thanh_429_kem_retry_after(client: TestClient, slack: FakeSlack) -> None:
    an = Nguoi(client, "An")
    org, workspace = _tenant_of(an)
    connection = seed(client, _user_id(an), org, workspace, "workspace")

    # `retry-after: 1` chứ không phải 30: Gateway tôn trọng con số của provider,
    # nên một test dùng 30 sẽ ngủ thật 60 giây trước khi khẳng định được gì.
    for _ in range(4):
        slack.next(status=429, json={}, headers={"retry-after": "1"})

    reply = an.post(
        f"{TOOLS}/slack.list_channels/invoke",
        json={"connection_id": connection, "input": {}},
    )

    assert reply.status_code == 429
    assert reply.headers.get("retry-after") is not None


def test_token_bi_thu_hoi_thi_ket_noi_chuyen_sang_can_ket_noi_lai(
    client: TestClient, slack: FakeSlack
) -> None:
    """Slack không có refresh token, nên đây là tín hiệu duy nhất khi app bị gỡ.

    Thiếu đường báo ngược này thì hàng kết nối nằm `active` vĩnh viễn và màn
    hình Reconnect không bao giờ sáng.
    """
    an = Nguoi(client, "An")
    org, workspace = _tenant_of(an)
    connection = seed(client, _user_id(an), org, workspace, "workspace")

    slack.next(json={"ok": False, "error": "token_revoked"})

    reply = an.post(
        f"{TOOLS}/slack.list_channels/invoke",
        json={"connection_id": connection, "input": {}},
    )

    assert reply.status_code == 409

    assert client.portal is not None
    assert client.portal.call(_status_of, connection) == ConnectionStatus.RECONNECT_REQUIRED.value


def test_input_sai_thi_slack_khong_bi_cham_toi(client: TestClient, slack: FakeSlack) -> None:
    an = Nguoi(client, "An")
    org, workspace = _tenant_of(an)
    connection = seed(client, _user_id(an), org, workspace, "workspace")

    reply = an.post(
        f"{TOOLS}/slack.post_message/invoke",
        json={
            "connection_id": connection,
            "input": {"channel": "khong-hop-le", "text": "hi"},
            "idempotency_key": "idm_1",
        },
    )

    assert reply.status_code == 422
    assert slack.requests == []


def test_tool_khong_ton_tai(client: TestClient) -> None:
    an = Nguoi(client, "An")
    org, workspace = _tenant_of(an)
    connection = seed(client, _user_id(an), org, workspace, "workspace")

    reply = an.post(
        f"{TOOLS}/slack.khong_co_that/invoke",
        json={"connection_id": connection, "input": {}},
    )

    assert reply.status_code == 422
