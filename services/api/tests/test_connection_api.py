"""Luồng kết nối end-to-end qua HTTP, trên PostgreSQL và Redis thật.

Cần hạ tầng:

    docker compose up -d postgres redis
    alembic upgrade head

Chỉ ``OAuthClient`` được thay bằng bản giả — mọi thứ còn lại chạy thật. Đó là
chủ đích: những gì `test_connection_service.py` không kiểm được đều nằm ở phần
"thật" đó — credential có thực sự được mã hoá trước khi xuống đĩa không, ràng
buộc AAD có chặn được việc đọc chéo tenant không, và `state` trên Redis có thật
sự chỉ đọc được một lần không.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated, Any

import pytest
from conftest import infra_unavailable
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

MAT_KHAU = "mat-khau-du-dai-12"

# Bật hai provider lên trước khi ứng dụng đọc cấu hình. Thiếu bước này thì mọi
# lời gọi `authorize` đều dừng ở "provider chưa được cấu hình".
os.environ.setdefault("AW_OAUTH_SLACK_CLIENT_ID", "slack-test-cid")
os.environ.setdefault("AW_OAUTH_SLACK_CLIENT_SECRET", "slack-test-secret")
os.environ.setdefault("AW_OAUTH_GOOGLE_CLIENT_ID", "google-test-cid")
os.environ.setdefault("AW_OAUTH_GOOGLE_CLIENT_SECRET", "google-test-secret")

# Import ở mức module, không phải trong fixture.
#
# `from __future__ import annotations` biến mọi annotation thành chuỗi, và
# FastAPI phân giải chúng bằng `get_type_hints` — hàm này chỉ nhìn thấy
# **globals của module**. Đặt các import dưới đây vào trong fixture thì
# `Annotated[AsyncSession, Depends(...)]` của hàm override không phân giải
# được, và FastAPI sẽ coi chúng là query parameter.
from fastapi import Depends  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.config import Settings, get_settings  # noqa: E402
from app.core.db import get_session  # noqa: E402
from app.core.errors import AuthorizationError  # noqa: E402
from app.domains.connection.application.ports import ProviderAccount, TenantGuard  # noqa: E402
from app.domains.connection.application.service import ConnectionService  # noqa: E402
from app.domains.connection.domain.entities import OAuthTokens  # noqa: E402
from app.domains.connection.infrastructure import http as connection_http  # noqa: E402
from app.domains.connection.infrastructure.credential_store import (  # noqa: E402
    build_credential_store,
)
from app.domains.connection.infrastructure.oauth_state import RedisOAuthStateStore  # noqa: E402
from app.domains.connection.infrastructure.provider_credentials import (  # noqa: E402
    SettingsProviderCredentials,
    build_provider_credentials,
)
from app.domains.connection.infrastructure.repositories import (  # noqa: E402
    SqlAlchemyConnectionRepository,
    SqlAlchemyOrganizationSettingsRepository,
)


class FakeOAuthClient:
    """Đứng thay provider. Không có lời gọi mạng nào rời khỏi tiến trình test."""

    def __init__(self) -> None:
        self.access_token = "xoxb-" + uuid.uuid4().hex
        self.team_id = "T" + uuid.uuid4().hex[:8]
        self.revoked: list[str] = []

    async def exchange_code(
        self, *, provider: str, code: str, redirect_uri: str, code_verifier: str | None
    ) -> tuple[Any, dict[str, Any]]:
        return (
            OAuthTokens(
                access_token=self.access_token,
                refresh_token="rt-" + uuid.uuid4().hex,
                scopes=frozenset({"chat:write", "channels:read", "users:read"}),
            ),
            {"team": {"id": self.team_id, "name": "Acme Corp"}},
        )

    async def refresh(self, *, provider: str, refresh_token: str) -> Any:
        return OAuthTokens(access_token="refreshed-" + uuid.uuid4().hex)

    async def fetch_account(self, *, provider: str, access_token: str) -> Any:
        return ProviderAccount(id=self.team_id, label="Acme Corp")

    async def revoke(self, *, provider: str, tokens: Any) -> None:
        self.revoked.append(provider)


@pytest.fixture
def oauth() -> FakeOAuthClient:
    return FakeOAuthClient()


@pytest.fixture
def client(oauth: FakeOAuthClient) -> Iterator[TestClient]:
    from app.entrypoints.http import create_app

    app = create_app()

    def build_service_with_fake_provider(
        session: Annotated[AsyncSession, Depends(get_session)],
        settings: Annotated[Settings, Depends(get_settings)],
        tenant: Annotated[TenantGuard, Depends(connection_http.get_tenant_guard)],
    ) -> ConnectionService:
        return ConnectionService(
            connections=SqlAlchemyConnectionRepository(session),
            credentials=build_credential_store(session, settings),
            states=RedisOAuthStateStore(),
            oauth=oauth,
            provider_credentials=SettingsProviderCredentials(build_provider_credentials(settings)),
            org_settings=SqlAlchemyOrganizationSettingsRepository(session),
            tenant=tenant,
            redirect_uri=settings.oauth_callback_url,
        )

    app.dependency_overrides[connection_http.get_connection_service] = (
        build_service_with_fake_provider
    )

    with TestClient(app) as c:
        checks = c.get("/readyz").json().get("checks", {})
        if checks.get("postgres") != "ok":
            infra_unavailable(
                "chưa kết nối được PostgreSQL: "
                "docker compose up -d postgres && alembic upgrade head"
            )
        if checks.get("redis") != "ok":
            infra_unavailable("chưa kết nối được Redis: docker compose up -d redis")
        _clear_rate_limits(c)
        yield c


def _clear_rate_limits(client: TestClient) -> None:
    from app.core.redis import get_redis

    async def clear() -> None:
        redis = get_redis()
        for pattern in ("aw:g:register:*", "aw:g:login:*", "aw:g:connection-authorize:*"):
            async for key in redis.scan_iter(match=pattern):
                await redis.delete(key)

    assert client.portal is not None
    client.portal.call(clear)


class Nguoi:
    """Một tài khoản đã đăng ký, kèm header xác thực sẵn sàng dùng."""

    def __init__(self, client: TestClient, ten: str) -> None:
        email = f"c-{uuid.uuid4().hex}@example.com"
        resp = client.post(
            "/v1/auth/register",
            json={"email": email, "password": MAT_KHAU, "display_name": ten},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        self.client = client
        self.user_id: str = body["user"]["id"]
        self.headers = {"Authorization": f"Bearer {body['access_token']}"}

    def _h(self, extra: dict[str, str] | None) -> dict[str, str]:
        return {**self.headers, **(extra or {})}

    def get(self, path: str, headers: dict[str, str] | None = None, **kw: Any) -> Any:
        return self.client.get(path, headers=self._h(headers), **kw)

    def post(self, path: str, headers: dict[str, str] | None = None, **kw: Any) -> Any:
        return self.client.post(path, headers=self._h(headers), **kw)

    def patch(self, path: str, headers: dict[str, str] | None = None, **kw: Any) -> Any:
        return self.client.patch(path, headers=self._h(headers), **kw)

    def put(self, path: str, headers: dict[str, str] | None = None, **kw: Any) -> Any:
        return self.client.put(path, headers=self._h(headers), **kw)

    def delete(self, path: str, headers: dict[str, str] | None = None, **kw: Any) -> Any:
        return self.client.delete(path, headers=self._h(headers), **kw)

    def ket_noi_slack(self) -> dict[str, Any]:
        """Chạy trọn vòng OAuth và trả về kết nối vừa tạo."""
        started = self.post("/v1/connections/slack/authorize", json={"sharing": "workspace"})
        assert started.status_code == 200, started.text

        state = _state_from(started.json()["authorize_url"])
        done = self.client.get(
            "/v1/connections/oauth/callback",
            params={"state": state, "code": "code-" + uuid.uuid4().hex},
            follow_redirects=False,
        )
        assert done.status_code == 303, done.text

        connections = self.get("/v1/connections").json()
        assert connections, "vòng uỷ quyền chạy xong mà không có kết nối nào"
        return dict(connections[-1])


def _state_from(authorize_url: str) -> str:
    from urllib.parse import parse_qs, urlparse

    return parse_qs(urlparse(authorize_url).query)["state"][0]


async def _fetch_credential_row(connection_id: str) -> Any:
    from sqlalchemy import select

    from app.core.db import session_scope
    from app.domains.connection.infrastructure.models import ConnectionCredentialModel

    async with session_scope() as session:
        return await session.scalar(
            select(ConnectionCredentialModel).where(
                ConnectionCredentialModel.connection_id == connection_id
            )
        )


# ─── Danh mục provider ───────────────────────────────────────────────────────


def test_danh_muc_provider_can_dang_nhap(client: TestClient) -> None:
    assert client.get("/v1/connections/providers").status_code == 401


def test_danh_muc_provider_bao_cai_nao_da_cau_hinh(client: TestClient) -> None:
    an = Nguoi(client, "An")

    providers = {p["id"]: p for p in an.get("/v1/connections/providers").json()}

    assert providers["slack"]["configured"] is True
    assert providers["slack"]["supports_refresh"] is False
    assert providers["slack"]["default_scopes"]


# ─── Vòng OAuth ──────────────────────────────────────────────────────────────


def test_bat_dau_uy_quyen_tra_ve_url_cua_provider(client: TestClient) -> None:
    an = Nguoi(client, "An")

    resp = an.post("/v1/connections/slack/authorize", json={})

    assert resp.status_code == 200
    body = resp.json()
    assert body["authorize_url"].startswith("https://slack.com/oauth/v2/authorize?")
    assert body["expires_in_seconds"] > 0
    # `state` không được trả trong body: nó đã nằm trong URL, và trả thêm một
    # bản nữa chỉ tạo thêm một chỗ để nó bị ghi vào log.
    assert "state" not in body


def test_chay_tron_vong_uy_quyen(client: TestClient, oauth: FakeOAuthClient) -> None:
    an = Nguoi(client, "An")

    connection = an.ket_noi_slack()

    assert connection["provider"] == "slack"
    assert connection["status"] == "active"
    assert connection["external_account_id"] == oauth.team_id
    assert connection["display_name"] == "Slack — Acme Corp"
    assert sorted(connection["granted_scopes"]) == [
        "channels:read",
        "chat:write",
        "users:read",
    ]


def test_phan_hoi_khong_bao_gio_chua_credential(client: TestClient, oauth: FakeOAuthClient) -> None:
    """Kiểm cả thân phản hồi thô, không chỉ các trường mình biết tên."""
    an = Nguoi(client, "An")
    an.ket_noi_slack()

    raw = an.get("/v1/connections").text

    assert oauth.access_token not in raw
    assert "access_token" not in raw
    assert "refresh_token" not in raw


def test_credential_xuong_dia_o_dang_ma_hoa(client: TestClient, oauth: FakeOAuthClient) -> None:
    """§21 — đọc thẳng bảng credential không được ra token.

    Đây là chỗ duy nhất kiểm được điều đó thật sự: test service dùng store giả,
    còn ở đây thì envelope encryption chạy thật và ghi thật xuống PostgreSQL.
    """
    an = Nguoi(client, "An")
    connection = an.ket_noi_slack()

    assert client.portal is not None
    row = client.portal.call(_fetch_credential_row, connection["id"])

    assert row is not None
    assert oauth.access_token not in row.ciphertext
    assert row.algorithm == "AES-256-GCM"
    assert row.key_id
    assert row.wrapped_data_key


def test_credential_khong_giai_ma_duoc_duoi_danh_nghia_tenant_khac(
    client: TestClient,
) -> None:
    """Ràng buộc AAD (§19). Bê nguyên hàng credential sang tenant khác thì phải
    hỏng, chứ không âm thầm cho ra token của người ta."""
    from app.core.crypto import CredentialDecryptionError

    an = Nguoi(client, "An")
    connection = an.ket_noi_slack()
    organization_id = an.get("/v1/organizations").json()[0]["id"]

    async def doc(tenant_id: str) -> Any:
        from app.core.db import session_scope

        async with session_scope() as session:
            store = build_credential_store(session, get_settings())
            return await store.load(connection_id=connection["id"], tenant_id=tenant_id)

    assert client.portal is not None

    # Đúng tenant thì đọc được.
    assert client.portal.call(doc, organization_id) is not None

    # Sai tenant thì giải mã thất bại — không phải trả về rỗng.
    with pytest.raises(CredentialDecryptionError):
        client.portal.call(doc, "org_gia_mao")


def test_state_chi_dung_duoc_mot_lan(client: TestClient) -> None:
    """Chống replay trên Redis thật, bằng GETDEL."""
    an = Nguoi(client, "An")

    started = an.post("/v1/connections/slack/authorize", json={})
    state = _state_from(started.json()["authorize_url"])

    lan_dau = client.get(
        "/v1/connections/oauth/callback",
        params={"state": state, "code": "c1"},
        follow_redirects=False,
    )
    assert lan_dau.status_code == 303
    assert "error" not in lan_dau.headers["location"]

    lan_hai = client.get(
        "/v1/connections/oauth/callback",
        params={"state": state, "code": "c1"},
        follow_redirects=False,
    )
    assert lan_hai.status_code == 303
    assert "error=" in lan_hai.headers["location"]

    assert len(an.get("/v1/connections").json()) == 1


def test_callback_khong_can_xac_thuc_nhung_van_vao_dung_tenant(
    client: TestClient, oauth: FakeOAuthClient
) -> None:
    """Callback tới từ trình duyệt sau chuyển hướng, không mang token nào.

    Nó vẫn phải gắn kết nối vào đúng tenant đã bắt đầu vòng uỷ quyền — thông
    tin đó chỉ có một nguồn hợp lệ là `state`.
    """
    an = Nguoi(client, "An")
    binh = Nguoi(client, "Binh")

    started = an.post("/v1/connections/slack/authorize", json={})
    state = _state_from(started.json()["authorize_url"])

    # Gọi callback hoàn toàn ẩn danh — không header Authorization nào.
    done = client.get(
        "/v1/connections/oauth/callback",
        params={"state": state, "code": "c1"},
        follow_redirects=False,
    )
    assert done.status_code == 303

    assert len(an.get("/v1/connections").json()) == 1
    assert binh.get("/v1/connections").json() == []


def test_nguoi_dung_bam_huy_thi_quay_ve_khong_kem_loi_he_thong(client: TestClient) -> None:
    resp = client.get(
        "/v1/connections/oauth/callback",
        params={"state": "bat-ky", "error": "access_denied"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error=access_denied" in resp.headers["location"]


def test_callback_thieu_tham_so_thi_quay_ve_chu_khong_no(client: TestClient) -> None:
    resp = client.get("/v1/connections/oauth/callback", follow_redirects=False)
    assert resp.status_code == 303
    assert "error=invalid_callback" in resp.headers["location"]


def test_uy_quyen_lai_cung_tai_khoan_khong_tao_ban_sao(client: TestClient) -> None:
    """Ràng buộc unique bộ phận trên PostgreSQL cũng phải cho phép điều này."""
    an = Nguoi(client, "An")

    dau = an.ket_noi_slack()
    sau = an.ket_noi_slack()

    assert dau["id"] == sau["id"]
    assert len(an.get("/v1/connections").json()) == 1


# ─── Vòng đời ────────────────────────────────────────────────────────────────


def test_thu_hoi_xoa_credential_khoi_database(client: TestClient, oauth: FakeOAuthClient) -> None:
    an = Nguoi(client, "An")
    connection = an.ket_noi_slack()

    resp = an.delete(f"/v1/connections/{connection['id']}")

    assert resp.status_code == 200
    assert resp.json()["status"] == "revoked"
    assert oauth.revoked == ["slack"]

    assert client.portal is not None
    assert client.portal.call(_fetch_credential_row, connection["id"]) is None

    # Bản ghi metadata ở lại để lịch sử đọc được.
    assert [c["id"] for c in an.get("/v1/connections").json()] == [connection["id"]]


def test_ket_noi_lai_sau_khi_thu_hoi(client: TestClient) -> None:
    """Index unique là index bộ phận, nên trạng thái `revoked` không chặn đường về."""
    an = Nguoi(client, "An")
    cu = an.ket_noi_slack()
    an.delete(f"/v1/connections/{cu['id']}")

    moi = an.ket_noi_slack()

    assert moi["id"] != cu["id"]
    assert moi["status"] == "active"


def test_tam_dung_roi_bat_lai(client: TestClient) -> None:
    an = Nguoi(client, "An")
    connection = an.ket_noi_slack()

    paused = an.post(f"/v1/connections/{connection['id']}/disable")
    assert paused.json()["status"] == "disabled"

    # Credential không bị đụng tới — khác hẳn thu hồi.
    assert client.portal is not None
    assert client.portal.call(_fetch_credential_row, connection["id"]) is not None

    resumed = an.post(f"/v1/connections/{connection['id']}/enable")
    assert resumed.json()["status"] == "active"


def test_doi_ten_ket_noi(client: TestClient) -> None:
    an = Nguoi(client, "An")
    connection = an.ket_noi_slack()

    resp = an.patch(f"/v1/connections/{connection['id']}", json={"display_name": "Slack nội bộ"})
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Slack nội bộ"


# ─── Ranh giới tenant ────────────────────────────────────────────────────────


def test_khong_thay_ket_noi_cua_tenant_khac(client: TestClient) -> None:
    an = Nguoi(client, "An")
    binh = Nguoi(client, "Binh")

    connection = an.ket_noi_slack()

    assert binh.get("/v1/connections").json() == []
    assert binh.get(f"/v1/connections/{connection['id']}").status_code == 403
    assert binh.delete(f"/v1/connections/{connection['id']}").status_code == 403


def test_khong_doan_duoc_ket_noi_co_that_hay_khong(client: TestClient) -> None:
    """Kết nối của người khác và kết nối không tồn tại phải trả lời giống hệt nhau."""
    an = Nguoi(client, "An")
    binh = Nguoi(client, "Binh")
    connection = an.ket_noi_slack()

    cua_nguoi_khac = binh.get(f"/v1/connections/{connection['id']}")
    khong_ton_tai = binh.get("/v1/connections/conn_khong-ton-tai")

    assert cua_nguoi_khac.status_code == khong_ton_tai.status_code
    assert cua_nguoi_khac.json()["error"] == khong_ton_tai.json()["error"]


def test_thanh_vien_thuong_khong_tao_duoc_ket_noi(client: TestClient) -> None:
    """Kết nối cho phép hành động dưới danh nghĩa một tài khoản bên ngoài, nên
    tạo nó cần quyền quản trị workspace."""
    chu = Nguoi(client, "Chu")
    khach = Nguoi(client, "Khach")
    organization_id = chu.get("/v1/organizations").json()[0]["id"]

    them = chu.post(
        "/v1/organizations/current/members",
        headers={"x-organization-id": organization_id},
        json={"email": _email_cua(khach, client), "role": "member"},
    )
    assert them.status_code == 201, them.text

    resp = khach.post(
        "/v1/connections/slack/authorize",
        headers={"x-organization-id": organization_id},
        json={},
    )
    assert resp.status_code == 403


def _email_cua(nguoi: Nguoi, client: TestClient) -> str:
    return str(nguoi.get("/v1/auth/me").json()["email"])


# ─── Phân giải credential ────────────────────────────────────────────────────


def test_resolver_lay_lai_dung_token_da_cat(client: TestClient, oauth: FakeOAuthClient) -> None:
    """Đường mà Integration Gateway sẽ đi từ Phase 2, chạy qua mã hoá thật."""
    an = Nguoi(client, "An")
    connection = an.ket_noi_slack()
    organization_id = an.get("/v1/organizations").json()[0]["id"]
    workspace_id = str(an.get("/v1/workspaces").json()[0]["id"])

    async def resolve() -> Any:
        from app.core.db import session_scope
        from app.domains.connection.application.service import CredentialResolver
        from app.domains.connection.infrastructure.oauth_state import RedisRefreshLock

        async with session_scope() as session:
            resolver = CredentialResolver(
                connections=SqlAlchemyConnectionRepository(session),
                credentials=build_credential_store(session, get_settings()),
                oauth=oauth,
                locks=RedisRefreshLock(),
            )
            return await resolver.resolve(
                connection_id=connection["id"],
                tenant_id=organization_id,
                workspace_id=workspace_id,
                acting_user_id=an.user_id,
                required_scopes=frozenset({"chat:write"}),
            )

    assert client.portal is not None
    resolved = client.portal.call(resolve)

    assert resolved.access_token == oauth.access_token
    assert resolved.provider == "slack"
    # Bot token của Slack không hết hạn.
    assert resolved.expires_at is None


def test_gioi_han_tan_suat_bat_dau_uy_quyen(client: TestClient) -> None:
    an = Nguoi(client, "An")

    ma_loi = [an.post("/v1/connections/slack/authorize", json={}).status_code for _ in range(25)]

    assert 429 in ma_loi


def test_dong_ho_khong_bi_lech_mui_gio(client: TestClient) -> None:
    """``created_at`` phải mang timezone — DTZ của ruff canh chuyện này ở code,
    còn đây canh nó ở dữ liệu thật đi qua database."""
    an = Nguoi(client, "An")
    connection = an.ket_noi_slack()

    created = datetime.fromisoformat(connection["created_at"])
    assert created.tzinfo is not None
    assert abs((datetime.now(tz=UTC) - created).total_seconds()) < 300


# ─────────────────────────────────────────────────────────────────────────────
# Quyền sở hữu và chính sách org, chạy trên PostgreSQL thật
#
# Những gì `test_connection_service.py` không kiểm được đều nằm ở đây: cột sinh
# `sharing_scope` có tính đúng không, index bộ phận có cho hai người cùng kết
# nối một provider không, và bộ lọc tầm nhìn có thật sự nằm trong câu SQL không.
# ─────────────────────────────────────────────────────────────────────────────


def _moi_vao_org(chu: Nguoi, khach: Nguoi, client: TestClient, role: str = "member") -> str:
    """Thêm ``khach`` vào organization **và** vào workspace mặc định.

    Hai bước, không phải một: một thành viên thường của organization chưa nhìn
    thấy workspace nào cho tới khi được cấp quyền tường minh (xem
    ``tenant.resolve_workspace_role``). Bỏ bước hai thì mọi lời gọi tới
    ``/v1/connections`` của họ dừng ở "không có quyền truy cập workspace nào" —
    và ta sẽ tưởng là đang kiểm quyền kết nối, trong khi thật ra chưa tới đó.
    """
    organization_id = str(chu.get("/v1/organizations").json()[0]["id"])
    headers = {"x-organization-id": organization_id}

    email = str(khach.get("/v1/auth/me").json()["email"])
    resp = chu.post(
        "/v1/organizations/current/members",
        headers=headers,
        json={"email": email, "role": role},
    )
    assert resp.status_code == 201, resp.text

    workspace_id = str(chu.get("/v1/workspaces", headers=headers).json()[0]["id"])
    cap = chu.put(
        f"/v1/workspaces/{workspace_id}/members",
        headers=headers,
        json={"user_id": khach.user_id, "role": "editor"},
    )
    assert cap.status_code == 204, cap.text
    return organization_id


def _ket_noi_rieng(nguoi: Nguoi, organization_id: str) -> dict[str, Any]:
    """Chạy trọn vòng OAuth ở chế độ PRIVATE."""
    started = nguoi.post(
        "/v1/connections/slack/authorize",
        headers={"x-organization-id": organization_id},
        json={"sharing": "private"},
    )
    assert started.status_code == 200, started.text

    state = _state_from(started.json()["authorize_url"])
    done = nguoi.client.get(
        "/v1/connections/oauth/callback",
        params={"state": state, "code": "code-" + uuid.uuid4().hex},
        follow_redirects=False,
    )
    assert done.status_code == 303, done.headers.get("location")

    connections = nguoi.get(
        "/v1/connections", headers={"x-organization-id": organization_id}
    ).json()
    rieng = [c for c in connections if c["sharing"] == "private"]
    assert rieng, "khong tao duoc ket noi rieng"
    return dict(rieng[-1])


def test_thanh_vien_thuong_tu_them_duoc_tai_khoan_rieng(client: TestClient) -> None:
    """Đảo lại bất đối xứng cũ: trước đây họ không tạo được nhưng lại dùng được
    kết nối người khác tạo. Giờ quyền tạo và quyền dùng trùng nhau."""
    chu = Nguoi(client, "Chu")
    khach = Nguoi(client, "Khach")
    organization_id = _moi_vao_org(chu, khach, client)

    # Tài khoản dùng chung thì vẫn bị chặn.
    chung = khach.post(
        "/v1/connections/slack/authorize",
        headers={"x-organization-id": organization_id},
        json={"sharing": "workspace"},
    )
    assert chung.status_code == 403

    # Tài khoản riêng thì được.
    rieng = khach.post(
        "/v1/connections/slack/authorize",
        headers={"x-organization-id": organization_id},
        json={"sharing": "private"},
    )
    assert rieng.status_code == 200


def test_ket_noi_rieng_khong_hien_ra_voi_nguoi_khac(client: TestClient) -> None:
    """Bộ lọc nằm trong câu SQL, nên đây là chỗ kiểm nó thật sự chạy."""
    chu = Nguoi(client, "Chu")
    khach = Nguoi(client, "Khach")
    organization_id = _moi_vao_org(chu, khach, client)

    cua_khach = _ket_noi_rieng(khach, organization_id)

    thay = chu.get("/v1/connections", headers={"x-organization-id": organization_id}).json()
    assert cua_khach["id"] not in {c["id"] for c in thay}


def test_quan_tri_vien_khong_doc_duoc_chi_tiet_ket_noi_rieng(client: TestClient) -> None:
    chu = Nguoi(client, "Chu")
    khach = Nguoi(client, "Khach")
    organization_id = _moi_vao_org(chu, khach, client)
    cua_khach = _ket_noi_rieng(khach, organization_id)

    resp = chu.get(
        f"/v1/connections/{cua_khach['id']}",
        headers={"x-organization-id": organization_id},
    )
    assert resp.status_code == 403


def test_man_hinh_audit_bao_su_ton_tai_nhung_khong_bao_chi_tiet(client: TestClient) -> None:
    """Quản trị viên cần biết *có* kết nối để thu hồi, không cần biết nó trỏ đâu."""
    chu = Nguoi(client, "Chu")
    khach = Nguoi(client, "Khach")
    organization_id = _moi_vao_org(chu, khach, client)
    cua_khach = _ket_noi_rieng(khach, organization_id)

    audit = chu.get("/v1/connections/audit", headers={"x-organization-id": organization_id})
    assert audit.status_code == 200

    dong = next(c for c in audit.json() if c["id"] == cua_khach["id"])
    assert dong["sharing"] == "private"
    assert dong["owner_user_id"] == khach.user_id
    # Không lộ tên tài khoản: tên mặc định có dạng "Slack — Acme Corp".
    assert "display_name" not in dong
    assert "external_account_label" not in dong
    assert "granted_scopes" not in dong

    # Thành viên thường không xem được danh sách này.
    assert (
        khach.get(
            "/v1/connections/audit", headers={"x-organization-id": organization_id}
        ).status_code
        == 403
    )


def test_quan_tri_vien_thu_hoi_duoc_ket_noi_rieng_cua_nguoi_khac(client: TestClient) -> None:
    """Bài toán nhân viên nghỉ việc, chạy hết đường qua HTTP."""
    chu = Nguoi(client, "Chu")
    khach = Nguoi(client, "Khach")
    organization_id = _moi_vao_org(chu, khach, client)
    cua_khach = _ket_noi_rieng(khach, organization_id)

    resp = chu.delete(
        f"/v1/connections/{cua_khach['id']}",
        headers={"x-organization-id": organization_id},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "revoked"

    assert client.portal is not None
    assert client.portal.call(_fetch_credential_row, cua_khach["id"]) is None


def test_hai_nguoi_ket_noi_cung_tai_khoan_provider(client: TestClient) -> None:
    """Index bộ phận phải có ``sharing_scope``, nếu không hai người sẽ đụng nhau.

    Cả hai đều kết nối cùng một team Slack (fake client trả cùng ``team.id``),
    nhưng đó là hai kết nối riêng khác nhau chứ không phải một.
    """
    chu = Nguoi(client, "Chu")
    khach = Nguoi(client, "Khach")
    organization_id = _moi_vao_org(chu, khach, client)

    cua_chu = _ket_noi_rieng(chu, organization_id)
    cua_khach = _ket_noi_rieng(khach, organization_id)

    assert cua_chu["id"] != cua_khach["id"]
    assert cua_chu["owner_user_id"] == chu.user_id
    assert cua_khach["owner_user_id"] == khach.user_id


def test_ket_noi_rieng_va_ket_noi_chung_cung_tai_khoan_song_song(client: TestClient) -> None:
    """Cột sinh ``sharing_scope`` phân biệt '@workspace' với id người dùng."""
    chu = Nguoi(client, "Chu")
    organization_id = str(chu.get("/v1/organizations").json()[0]["id"])

    chung = chu.ket_noi_slack()
    rieng = _ket_noi_rieng(chu, organization_id)

    assert chung["id"] != rieng["id"]
    assert chung["sharing"] == "workspace"
    assert rieng["sharing"] == "private"


# ─── Chính sách của organization ─────────────────────────────────────────────


def test_org_mac_dinh_la_open(client: TestClient) -> None:
    an = Nguoi(client, "An")
    resp = an.get("/v1/connections/settings")
    assert resp.status_code == 200
    assert resp.json()["connection_policy"] == "open"


def test_siet_sang_managed_thi_chan_tai_khoan_rieng_va_tam_dung_cai_cu(
    client: TestClient,
) -> None:
    chu = Nguoi(client, "Chu")
    organization_id = str(chu.get("/v1/organizations").json()[0]["id"])
    rieng = _ket_noi_rieng(chu, organization_id)

    doi = chu.patch(
        "/v1/connections/settings",
        headers={"x-organization-id": organization_id},
        json={"connection_policy": "managed"},
    )
    assert doi.status_code == 200
    assert doi.json()["connection_policy"] == "managed"
    assert doi.json()["disabled_private_connections"] == 1

    # Kết nối riêng cũ bị tạm dừng, không bị xoá — credential còn nguyên.
    sau = chu.get(
        f"/v1/connections/{rieng['id']}", headers={"x-organization-id": organization_id}
    ).json()
    assert sau["status"] == "disabled"
    assert client.portal is not None
    assert client.portal.call(_fetch_credential_row, rieng["id"]) is not None

    # Không thêm được kết nối riêng mới.
    chan = chu.post(
        "/v1/connections/slack/authorize",
        headers={"x-organization-id": organization_id},
        json={"sharing": "private"},
    )
    assert chan.status_code == 422
    assert chan.json()["error"]["details"]["connection_policy"] == "managed"


def test_chi_owner_doi_duoc_chinh_sach(client: TestClient) -> None:
    chu = Nguoi(client, "Chu")
    khach = Nguoi(client, "Khach")
    organization_id = _moi_vao_org(chu, khach, client, role="admin")

    resp = khach.patch(
        "/v1/connections/settings",
        headers={"x-organization-id": organization_id},
        json={"connection_policy": "managed"},
    )
    assert resp.status_code == 403


def test_resolver_khong_giao_credential_rieng_cho_nguoi_khac(
    client: TestClient, oauth: FakeOAuthClient
) -> None:
    """Đường mà Agent sẽ đi từ Phase 3, chạy qua mã hoá thật trên PostgreSQL."""
    chu = Nguoi(client, "Chu")
    khach = Nguoi(client, "Khach")
    organization_id = _moi_vao_org(chu, khach, client)
    cua_khach = _ket_noi_rieng(khach, organization_id)
    workspace_id = str(
        khach.get("/v1/workspaces", headers={"x-organization-id": organization_id}).json()[0]["id"]
    )

    async def resolve(acting_user_id: str) -> Any:
        from app.core.db import session_scope
        from app.domains.connection.application.service import CredentialResolver
        from app.domains.connection.infrastructure.oauth_state import RedisRefreshLock

        async with session_scope() as session:
            resolver = CredentialResolver(
                connections=SqlAlchemyConnectionRepository(session),
                credentials=build_credential_store(session, get_settings()),
                oauth=oauth,
                locks=RedisRefreshLock(),
            )
            return await resolver.resolve(
                connection_id=cua_khach["id"],
                tenant_id=organization_id,
                workspace_id=workspace_id,
                acting_user_id=acting_user_id,
            )

    assert client.portal is not None

    # Chính chủ lấy được token.
    assert client.portal.call(resolve, khach.user_id).access_token == oauth.access_token

    # Người khác trong cùng workspace thì không — kể cả chủ organization.
    with pytest.raises(AuthorizationError):
        client.portal.call(resolve, chu.user_id)
