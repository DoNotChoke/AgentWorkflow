"""Use case của Connection, chạy trên port giả lập trong bộ nhớ.

Không cần Docker. Những gì kiểm ở đây mà `test_connection_domain.py` không kiểm
được: vòng OAuth hai bước có khớp vào nhau không, tenant có thật sự đi ra từ
`state` chứ không từ tham số của callback không, và việc làm mới token có tự
loại bỏ những lần làm mới thừa không.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core.errors import (
    AuthorizationError,
    ConnectionExpiredError,
    MissingScopeError,
    ProviderPermanentError,
    ValidationError,
)
from app.domains.connection.application.ports import (
    ClientCredentials,
    PendingAuthorization,
    ProviderAccount,
    WorkspaceContext,
)
from app.domains.connection.application.service import (
    ConnectionService,
    CredentialResolver,
)
from app.domains.connection.domain.entities import (
    Connection,
    ConnectionPolicy,
    ConnectionStatus,
    OAuthTokens,
    SharingPolicy,
    visible_to,
)
from app.domains.connection.domain.providers import SLACK

ORG = "org_1"
WS = "ws_1"
USER = "usr_1"

OTHER_ORG = "org_2"
OTHER_WS = "ws_2"

CALLBACK = "https://app.example.com/v1/connections/oauth/callback"


# ─────────────────────────────────────────────────────────────────────────────
# Port giả lập
# ─────────────────────────────────────────────────────────────────────────────


class FakeConnections:
    def __init__(self) -> None:
        self.rows: dict[str, Connection] = {}

    async def add(self, connection: Connection) -> None:
        self.rows[connection.id] = connection

    async def get_by_id(self, connection_id: str) -> Connection | None:
        return self.rows.get(connection_id)

    async def list_visible(self, *, workspace_id: str, user_id: str) -> list[Connection]:
        return [
            c
            for c in self.rows.values()
            if c.workspace_id == workspace_id and visible_to(c, user_id=user_id)
        ]

    async def list_all_for_audit(self, workspace_id: str) -> list[Connection]:
        return [c for c in self.rows.values() if c.workspace_id == workspace_id]

    async def find_active_for_account(
        self,
        *,
        workspace_id: str,
        provider: str,
        external_account_id: str,
        sharing: SharingPolicy,
        owner_user_id: str,
    ) -> Connection | None:
        return next(
            (
                c
                for c in self.rows.values()
                if c.workspace_id == workspace_id
                and c.provider == provider
                and c.external_account_id == external_account_id
                and c.sharing is sharing
                and (sharing is SharingPolicy.WORKSPACE or c.owner_user_id == owner_user_id)
                and c.status is not ConnectionStatus.REVOKED
            ),
            None,
        )

    async def disable_private_in_organization(self, organization_id: str, *, at: datetime) -> int:
        changed = 0
        for key, row in list(self.rows.items()):
            if (
                row.organization_id == organization_id
                and row.sharing is SharingPolicy.PRIVATE
                and row.status not in (ConnectionStatus.REVOKED, ConnectionStatus.DISABLED)
            ):
                self.rows[key] = replace(row, status=ConnectionStatus.DISABLED, updated_at=at)
                changed += 1
        return changed

    async def update_authorization(
        self,
        connection_id: str,
        *,
        status: ConnectionStatus,
        granted_scopes: frozenset[str],
        expires_at: datetime | None,
        external_account_id: str | None,
        external_account_label: str | None,
        connected_by: str,
        at: datetime,
    ) -> None:
        self.rows[connection_id] = replace(
            self.rows[connection_id],
            status=status,
            granted_scopes=granted_scopes,
            expires_at=expires_at,
            external_account_id=external_account_id,
            external_account_label=external_account_label,
            connected_by=connected_by,
            last_error=None,
            revoked_at=None,
            updated_at=at,
        )

    async def update_status(
        self,
        connection_id: str,
        *,
        status: ConnectionStatus,
        at: datetime,
        last_error: str | None = None,
        revoked_at: datetime | None = None,
    ) -> None:
        row = self.rows[connection_id]
        self.rows[connection_id] = replace(
            row,
            status=status,
            last_error=last_error,
            revoked_at=revoked_at if revoked_at is not None else row.revoked_at,
            updated_at=at,
        )

    async def update_expiry(
        self, connection_id: str, *, expires_at: datetime | None, at: datetime
    ) -> None:
        self.rows[connection_id] = replace(
            self.rows[connection_id], expires_at=expires_at, updated_at=at
        )

    async def rename(self, connection_id: str, *, display_name: str, at: datetime) -> None:
        self.rows[connection_id] = replace(
            self.rows[connection_id], display_name=display_name, updated_at=at
        )


class FakeCredentials:
    """Giả lập ràng buộc AAD: đọc bằng tenant khác thì coi như không có gì."""

    def __init__(self) -> None:
        self.rows: dict[str, tuple[str, OAuthTokens]] = {}
        self.deleted: list[str] = []

    async def save(
        self, *, connection_id: str, tenant_id: str, tokens: OAuthTokens, at: datetime
    ) -> None:
        self.rows[connection_id] = (tenant_id, tokens)

    async def load(self, *, connection_id: str, tenant_id: str) -> OAuthTokens | None:
        found = self.rows.get(connection_id)
        if found is None or found[0] != tenant_id:
            return None
        return found[1]

    async def delete(self, connection_id: str) -> None:
        self.rows.pop(connection_id, None)
        self.deleted.append(connection_id)


class FakeSettings:
    def __init__(self, policy: ConnectionPolicy | None = None) -> None:
        self.policy = policy

    async def get_policy(self, organization_id: str) -> ConnectionPolicy | None:
        return self.policy

    async def set_policy(
        self, organization_id: str, *, policy: ConnectionPolicy, at: datetime
    ) -> None:
        self.policy = policy


class FakeStates:
    def __init__(self) -> None:
        self.rows: dict[str, PendingAuthorization] = {}

    async def put(self, state: str, pending: PendingAuthorization, *, ttl_seconds: int) -> None:
        self.rows[state] = pending

    async def pop(self, state: str) -> PendingAuthorization | None:
        return self.rows.pop(state, None)


class FakeOAuth:
    def __init__(
        self,
        *,
        tokens: OAuthTokens | None = None,
        body: dict[str, Any] | None = None,
        account: ProviderAccount | None = None,
        refreshed: OAuthTokens | None = None,
        refresh_error: Exception | None = None,
    ) -> None:
        self._tokens = tokens or OAuthTokens(access_token="at-1")
        self._body = body or {}
        self._account = account or ProviderAccount(id=None, label=None)
        self._refreshed = refreshed
        self._refresh_error = refresh_error
        self.refresh_calls = 0
        self.revoked: list[str] = []
        self.exchange_calls: list[dict[str, Any]] = []

    async def exchange_code(
        self, *, provider: str, code: str, redirect_uri: str, code_verifier: str | None
    ) -> tuple[OAuthTokens, dict[str, Any]]:
        self.exchange_calls.append(
            {
                "provider": provider,
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            }
        )
        return self._tokens, self._body

    async def refresh(self, *, provider: str, refresh_token: str) -> OAuthTokens:
        self.refresh_calls += 1
        if self._refresh_error is not None:
            raise self._refresh_error
        assert self._refreshed is not None
        return self._refreshed

    async def fetch_account(self, *, provider: str, access_token: str) -> ProviderAccount:
        return self._account

    async def revoke(self, *, provider: str, tokens: OAuthTokens) -> None:
        self.revoked.append(provider)


class FakeProviderCredentials:
    def __init__(
        self, configured: frozenset[str] = frozenset({"slack", "google", "notion"})
    ) -> None:
        self._configured = configured

    def get(self, provider_id: str) -> ClientCredentials | None:
        if provider_id not in self._configured:
            return None
        return ClientCredentials(client_id=f"{provider_id}-cid", client_secret="secret")


class FakeLock:
    """Đếm số lần lấy lock, và cho phép chen một tác dụng phụ vào giữa."""

    def __init__(self, on_acquire: Callable[[], None] | None = None) -> None:
        self.acquisitions = 0
        self._on_acquire = on_acquire

    def guard(self, connection_id: str) -> AbstractAsyncContextManager[None]:
        return self._guard()

    @asynccontextmanager
    async def _guard(self) -> AsyncIterator[None]:
        self.acquisitions += 1
        if self._on_acquire is not None:
            self._on_acquire()
        yield


class FakeTenantGuard:
    def __init__(self, *, can_manage: bool = True) -> None:
        self._can_manage = can_manage

    async def require_workspace_context(
        self, *, user_id: str, organization_id: str | None, workspace_id: str | None
    ) -> WorkspaceContext:
        return context(can_manage=self._can_manage)


def context(
    *,
    can_manage: bool = True,
    org: str = ORG,
    ws: str = WS,
    user: str = USER,
    is_org_admin: bool | None = None,
    is_org_owner: bool | None = None,
) -> WorkspaceContext:
    owner = can_manage if is_org_owner is None else is_org_owner
    admin = owner if is_org_admin is None else is_org_admin
    return WorkspaceContext(
        user_id=user,
        organization_id=org,
        workspace_id=ws,
        can_manage_connections=can_manage,
        is_org_admin=admin,
        is_org_owner=owner,
        roles=frozenset({"org:owner", "ws:admin"} if can_manage else {"org:member", "ws:viewer"}),
    )


def build_service(
    *,
    connections: FakeConnections | None = None,
    credentials: FakeCredentials | None = None,
    states: FakeStates | None = None,
    oauth: FakeOAuth | None = None,
    provider_credentials: FakeProviderCredentials | None = None,
    settings: FakeSettings | None = None,
) -> tuple[ConnectionService, FakeConnections, FakeCredentials, FakeStates, FakeOAuth]:
    conns = connections or FakeConnections()
    creds = credentials or FakeCredentials()
    sts = states or FakeStates()
    auth = oauth or FakeOAuth()

    service = ConnectionService(
        connections=conns,
        credentials=creds,
        states=sts,
        oauth=auth,
        provider_credentials=provider_credentials or FakeProviderCredentials(),
        org_settings=settings or FakeSettings(),
        tenant=FakeTenantGuard(),
        redirect_uri=CALLBACK,
    )
    return service, conns, creds, sts, auth


def make_connection(
    *,
    connection_id: str = "conn_1",
    org: str = ORG,
    ws: str = WS,
    provider: str = "google",
    status: ConnectionStatus = ConnectionStatus.ACTIVE,
    scopes: frozenset[str] = frozenset({"openid"}),
    expires_at: datetime | None = None,
    account_id: str | None = "acct-1",
    sharing: SharingPolicy = SharingPolicy.WORKSPACE,
    owner: str = USER,
) -> Connection:
    now = datetime.now(tz=UTC)
    return Connection(
        id=connection_id,
        organization_id=org,
        workspace_id=ws,
        provider=provider,
        display_name="Google",
        status=status,
        granted_scopes=scopes,
        connected_by=owner,
        created_at=now,
        updated_at=now,
        sharing=sharing,
        owner_user_id=owner,
        external_account_id=account_id,
        expires_at=expires_at,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Bước một — bắt đầu uỷ quyền
# ─────────────────────────────────────────────────────────────────────────────


async def test_bat_dau_uy_quyen_cat_state_va_tra_ve_url() -> None:
    service, _, _, states, _ = build_service()

    authorization = await service.start_authorization(
        context=context(), provider="slack", sharing=SharingPolicy.WORKSPACE
    )

    assert authorization.authorize_url.startswith("https://slack.com/oauth/v2/authorize?")
    assert len(states.rows) == 1

    pending = next(iter(states.rows.values()))
    assert pending.organization_id == ORG
    assert pending.workspace_id == WS
    assert pending.user_id == USER
    assert pending.redirect_uri == CALLBACK


async def test_uy_quyen_provider_khong_dung_scope_xin_tap_rong() -> None:
    """Notion cấp quyền theo từng trang được chia sẻ, không theo scope.

    Tập rỗng phải đi trót lọt tới cuối: `ensure_scopes_requestable` chặn tập
    rỗng, nên nếu nó vẫn nằm trên đường này thì nút "Connect" của Notion ném
    lỗi trước cả khi trình duyệt rời trang.
    """
    service, _, _, states, _ = build_service()

    authorization = await service.start_authorization(
        context=context(), provider="notion", sharing=SharingPolicy.WORKSPACE
    )

    assert authorization.authorize_url.startswith("https://api.notion.com/v1/oauth/authorize?")
    assert next(iter(states.rows.values())).requested_scopes == frozenset()


async def test_uy_quyen_tu_choi_scope_gui_toi_provider_khong_dung_scope() -> None:
    service, _, _, _, _ = build_service()

    with pytest.raises(ValidationError):
        await service.start_authorization(
            context=context(),
            provider="notion",
            scopes=frozenset({"read_content"}),
            sharing=SharingPolicy.WORKSPACE,
        )


async def test_state_du_dai_de_khong_doan_duoc() -> None:
    """`state` vừa chống CSRF vừa là khoá tra tenant. Đoán được nó là gắn được
    một kết nối vào tenant người khác."""
    service, _, _, states, _ = build_service()

    await service.start_authorization(
        context=context(), provider="slack", sharing=SharingPolicy.WORKSPACE
    )

    state = next(iter(states.rows))
    assert len(state) >= 32


async def test_provider_ho_tro_pkce_thi_verifier_duoc_cat_lai() -> None:
    service, _, _, states, _ = build_service()

    await service.start_authorization(
        context=context(), provider="google", sharing=SharingPolicy.WORKSPACE
    )
    assert next(iter(states.rows.values())).code_verifier is not None


async def test_provider_khong_ho_tro_pkce_thi_khong_co_verifier() -> None:
    service, _, _, states, _ = build_service()

    await service.start_authorization(
        context=context(), provider="slack", sharing=SharingPolicy.WORKSPACE
    )
    assert next(iter(states.rows.values())).code_verifier is None


async def test_thanh_vien_thuong_khong_tao_duoc_ket_noi_dung_chung() -> None:
    """Tài khoản dùng chung cho cả workspace hành động dưới danh nghĩa tổ chức,
    nên chỉ quản trị viên workspace mới thiết lập được."""
    service, _, _, states, _ = build_service()

    with pytest.raises(AuthorizationError):
        await service.start_authorization(
            context=context(can_manage=False), provider="slack", sharing=SharingPolicy.WORKSPACE
        )
    assert not states.rows


async def test_thanh_vien_thuong_van_tao_duoc_ket_noi_rieng() -> None:
    """Đây là điểm khiến quyền tạo và quyền dùng trùng nhau.

    Trước đây thành viên thường không tạo được kết nối nào nhưng lại dùng được
    kết nối người khác tạo. Giờ họ tự thêm tài khoản của mình, và chỉ mình họ
    dùng được nó.
    """
    service, _, _, states, _ = build_service()

    await service.start_authorization(
        context=context(can_manage=False), provider="slack", sharing=SharingPolicy.PRIVATE
    )

    assert len(states.rows) == 1
    assert next(iter(states.rows.values())).sharing is SharingPolicy.PRIVATE


async def test_provider_chua_cau_hinh_thi_bao_som() -> None:
    """Chặn trước khi người dùng rời trang, chứ không để hỏng ở bước đổi token."""
    service, _, _, _, _ = build_service(
        provider_credentials=FakeProviderCredentials(configured=frozenset())
    )

    with pytest.raises(ValidationError) as exc:
        await service.start_authorization(
            context=context(), provider="slack", sharing=SharingPolicy.WORKSPACE
        )
    assert exc.value.details["provider"] == "slack"


async def test_provider_la_khong_biet_thi_bao_loi() -> None:
    service, _, _, _, _ = build_service()

    with pytest.raises(ValidationError):
        await service.start_authorization(context=context(), provider="khong-ton-tai")


async def test_ket_noi_lai_khong_cham_duoc_connection_cua_tenant_khac() -> None:
    connections = FakeConnections()
    connections.rows["conn_x"] = make_connection(connection_id="conn_x", org=OTHER_ORG, ws=OTHER_WS)
    service, _, _, states, _ = build_service(connections=connections)

    with pytest.raises(AuthorizationError):
        await service.start_authorization(
            context=context(), provider="google", reconnect_connection_id="conn_x"
        )
    assert not states.rows


async def test_ket_noi_lai_phai_dung_provider() -> None:
    connections = FakeConnections()
    connections.rows["conn_1"] = make_connection(provider="google")
    service, _, _, _, _ = build_service(connections=connections)

    with pytest.raises(ValidationError):
        await service.start_authorization(
            context=context(), provider="slack", reconnect_connection_id="conn_1"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Bước hai — callback
# ─────────────────────────────────────────────────────────────────────────────


async def test_hoan_tat_uy_quyen_tao_ket_noi_va_luu_credential() -> None:
    oauth = FakeOAuth(
        tokens=OAuthTokens(
            access_token="xoxb-1",
            refresh_token="rt-1",
            scopes=frozenset(SLACK.default_scopes),
        ),
        body={"team": {"id": "T1", "name": "Acme"}},
    )
    service, connections, credentials, states, _ = build_service(oauth=oauth)

    await service.start_authorization(
        context=context(), provider="slack", sharing=SharingPolicy.WORKSPACE
    )
    state = next(iter(states.rows))

    connection = await service.complete_authorization(state=state, code="code-1")

    assert connection.status is ConnectionStatus.ACTIVE
    assert connection.organization_id == ORG
    assert connection.workspace_id == WS
    assert connection.external_account_id == "T1"
    assert connection.display_name == "Slack — Acme"
    assert connections.rows[connection.id] == connection

    # Credential được cất dưới đúng tenant của kết nối.
    assert await credentials.load(connection_id=connection.id, tenant_id=ORG) is not None
    assert await credentials.load(connection_id=connection.id, tenant_id=OTHER_ORG) is None


async def test_tenant_lay_tu_state_chu_khong_tu_request() -> None:
    """Callback không mang access token nào, nên `state` là nguồn tenant duy nhất.

    Đây là bất biến bảo mật cốt lõi của cả vòng OAuth: nếu tenant có thể tới từ
    một tham số của callback thì ai cũng gắn được kết nối vào tenant người khác.
    """
    service, connections, _, states, _ = build_service()

    await service.start_authorization(context=context(org=ORG, ws=WS), provider="slack")
    state = next(iter(states.rows))

    connection = await service.complete_authorization(state=state, code="code-1")

    assert connection.organization_id == ORG
    assert connection.workspace_id == WS
    assert connections.rows[connection.id].organization_id == ORG


async def test_state_chi_dung_duoc_mot_lan() -> None:
    """Chống replay: một authorization code bị chặn rồi gửi lần hai phải hỏng."""
    service, _, _, states, _ = build_service()

    await service.start_authorization(
        context=context(), provider="slack", sharing=SharingPolicy.WORKSPACE
    )
    state = next(iter(states.rows))

    await service.complete_authorization(state=state, code="code-1")

    with pytest.raises(ValidationError):
        await service.complete_authorization(state=state, code="code-1")


async def test_state_bia_ra_thi_bi_tu_choi() -> None:
    service, _, _, _, _ = build_service()

    with pytest.raises(ValidationError):
        await service.complete_authorization(state="khong-ton-tai", code="code-1")


async def test_pkce_verifier_duoc_gui_kem_khi_doi_token() -> None:
    service, _, _, states, oauth = build_service()

    await service.start_authorization(
        context=context(), provider="google", sharing=SharingPolicy.WORKSPACE
    )
    state = next(iter(states.rows))
    verifier = states.rows[state].code_verifier

    await service.complete_authorization(state=state, code="code-1")

    assert oauth.exchange_calls[0]["code_verifier"] == verifier


async def test_provider_khong_doi_lai_scope_thi_hieu_la_da_cap_du() -> None:
    """Im lặng nghĩa là cấp đúng những gì mình xin, không phải chưa cấp gì."""
    service, _, _, states, _ = build_service(
        oauth=FakeOAuth(tokens=OAuthTokens(access_token="at", scopes=frozenset()))
    )

    await service.start_authorization(
        context=context(), provider="slack", scopes=frozenset({"chat:write"})
    )
    connection = await service.complete_authorization(state=next(iter(states.rows)), code="code-1")

    assert connection.status is ConnectionStatus.ACTIVE
    assert connection.granted_scopes == frozenset({"chat:write"})


async def test_thieu_scope_thi_ket_noi_mang_trang_thai_missing_scope() -> None:
    """Người dùng bỏ tick một quyền — phải lộ ra ngay, không đợi tới lần gọi tool."""
    service, _, _, states, _ = build_service(
        oauth=FakeOAuth(tokens=OAuthTokens(access_token="at", scopes=frozenset({"chat:write"})))
    )

    await service.start_authorization(
        context=context(), provider="slack", scopes=frozenset({"chat:write", "files:write"})
    )
    connection = await service.complete_authorization(state=next(iter(states.rows)), code="code-1")

    assert connection.status is ConnectionStatus.MISSING_SCOPE


async def test_uy_quyen_lai_cung_tai_khoan_thi_cap_nhat_chu_khong_tao_ban_sao() -> None:
    """Mỗi lần bấm Connect lại đẻ một dòng mới thì chỉ dòng mới nhất còn dùng được."""
    oauth = FakeOAuth(
        tokens=OAuthTokens(access_token="at", scopes=frozenset({"chat:write"})),
        body={"team": {"id": "T1", "name": "Acme"}},
    )
    service, connections, _, states, _ = build_service(oauth=oauth)

    await service.start_authorization(
        context=context(), provider="slack", scopes=frozenset({"chat:write"})
    )
    first = await service.complete_authorization(state=next(iter(states.rows)), code="c1")

    await service.start_authorization(
        context=context(), provider="slack", scopes=frozenset({"chat:write"})
    )
    second = await service.complete_authorization(state=next(iter(states.rows)), code="c2")

    assert first.id == second.id
    assert len(connections.rows) == 1


async def test_uy_quyen_lai_xoa_dau_vet_loi_cu() -> None:
    """Kết nối vừa chữa xong mà UI vẫn mời kết nối lại là một lỗi hiển thị."""
    connections = FakeConnections()
    connections.rows["conn_1"] = make_connection(
        provider="slack", status=ConnectionStatus.RECONNECT_REQUIRED, account_id="T1"
    )
    connections.rows["conn_1"] = replace(connections.rows["conn_1"], last_error="refresh_rejected")

    oauth = FakeOAuth(
        tokens=OAuthTokens(access_token="at", scopes=frozenset({"chat:write"})),
        body={"team": {"id": "T1", "name": "Acme"}},
    )
    service, _, _, states, _ = build_service(connections=connections, oauth=oauth)

    await service.start_authorization(
        context=context(),
        provider="slack",
        sharing=SharingPolicy.WORKSPACE,
        scopes=frozenset({"chat:write"}),
    )
    connection = await service.complete_authorization(state=next(iter(states.rows)), code="c1")

    assert connection.id == "conn_1"
    assert connection.status is ConnectionStatus.ACTIVE
    assert connection.last_error is None


async def test_ket_noi_da_thu_hoi_khong_bi_nhan_nham_thanh_dich_cap_nhat() -> None:
    connections = FakeConnections()
    connections.rows["conn_old"] = make_connection(
        connection_id="conn_old",
        provider="slack",
        status=ConnectionStatus.REVOKED,
        account_id="T1",
    )
    oauth = FakeOAuth(
        tokens=OAuthTokens(access_token="at", scopes=frozenset({"chat:write"})),
        body={"team": {"id": "T1", "name": "Acme"}},
    )
    service, _, _, states, _ = build_service(connections=connections, oauth=oauth)

    await service.start_authorization(
        context=context(), provider="slack", scopes=frozenset({"chat:write"})
    )
    connection = await service.complete_authorization(state=next(iter(states.rows)), code="c1")

    assert connection.id != "conn_old"
    assert connections.rows["conn_old"].status is ConnectionStatus.REVOKED


# ─────────────────────────────────────────────────────────────────────────────
# Vòng đời
# ─────────────────────────────────────────────────────────────────────────────


async def test_thu_hoi_xoa_credential_va_giu_lai_ban_ghi() -> None:
    connections = FakeConnections()
    connections.rows["conn_1"] = make_connection()
    credentials = FakeCredentials()
    await credentials.save(
        connection_id="conn_1",
        tenant_id=ORG,
        tokens=OAuthTokens(access_token="at"),
        at=datetime.now(tz=UTC),
    )
    service, _, _, _, oauth = build_service(connections=connections, credentials=credentials)

    connection = await service.revoke(connection_id="conn_1", context=context())

    assert connection.status is ConnectionStatus.REVOKED
    assert connection.revoked_at is not None
    assert "conn_1" in credentials.deleted
    assert await credentials.load(connection_id="conn_1", tenant_id=ORG) is None
    # Bản ghi metadata ở lại để lịch sử đọc được.
    assert "conn_1" in connections.rows
    assert oauth.revoked == ["google"]


async def test_thu_hoi_hai_lan_thi_bi_chan() -> None:
    connections = FakeConnections()
    connections.rows["conn_1"] = make_connection(status=ConnectionStatus.REVOKED)
    service, _, _, _, _ = build_service(connections=connections)

    with pytest.raises(ValidationError):
        await service.revoke(connection_id="conn_1", context=context())


async def test_tam_dung_roi_bat_lai_khong_can_uy_quyen_lai() -> None:
    connections = FakeConnections()
    connections.rows["conn_1"] = make_connection()
    credentials = FakeCredentials()
    await credentials.save(
        connection_id="conn_1",
        tenant_id=ORG,
        tokens=OAuthTokens(access_token="at"),
        at=datetime.now(tz=UTC),
    )
    service, _, _, _, _ = build_service(connections=connections, credentials=credentials)

    paused = await service.disable(connection_id="conn_1", context=context())
    assert paused.status is ConnectionStatus.DISABLED
    # Credential không bị đụng tới — đó mới là điểm khác biệt với thu hồi.
    assert await credentials.load(connection_id="conn_1", tenant_id=ORG) is not None

    resumed = await service.enable(connection_id="conn_1", context=context())
    assert resumed.status is ConnectionStatus.ACTIVE


async def test_doi_ten_ket_noi() -> None:
    connections = FakeConnections()
    connections.rows["conn_1"] = make_connection()
    service, _, _, _, _ = build_service(connections=connections)

    renamed = await service.rename(
        connection_id="conn_1", context=context(), display_name="  Drive  cua  Acme "
    )
    assert renamed.display_name == "Drive cua Acme"


@pytest.mark.parametrize("action", ["disable", "enable", "revoke"])
async def test_thanh_vien_thuong_khong_doi_duoc_vong_doi_ket_noi(action: str) -> None:
    connections = FakeConnections()
    connections.rows["conn_1"] = make_connection(status=ConnectionStatus.DISABLED)
    service, _, _, _, _ = build_service(connections=connections)

    with pytest.raises(AuthorizationError):
        await getattr(service, action)(connection_id="conn_1", context=context(can_manage=False))


async def test_khong_doc_duoc_ket_noi_cua_tenant_khac() -> None:
    """Trả về đúng lỗi như khi không tồn tại — trả lời khác đi là xác nhận id có thật."""
    connections = FakeConnections()
    connections.rows["conn_x"] = make_connection(connection_id="conn_x", org=OTHER_ORG, ws=OTHER_WS)
    service, _, _, _, _ = build_service(connections=connections)

    with pytest.raises(AuthorizationError) as foreign:
        await service.get_connection("conn_x", context())
    with pytest.raises(AuthorizationError) as absent:
        await service.get_connection("conn_khong-ton-tai", context())

    assert str(foreign.value) == str(absent.value)


async def test_cung_organization_nhung_khac_workspace_cung_khong_doc_duoc() -> None:
    connections = FakeConnections()
    connections.rows["conn_x"] = make_connection(connection_id="conn_x", org=ORG, ws=OTHER_WS)
    service, _, _, _, _ = build_service(connections=connections)

    with pytest.raises(AuthorizationError):
        await service.get_connection("conn_x", context())


async def test_liet_ke_chi_thay_ket_noi_cua_workspace_hien_tai() -> None:
    connections = FakeConnections()
    connections.rows["conn_1"] = make_connection(connection_id="conn_1", ws=WS)
    connections.rows["conn_2"] = make_connection(connection_id="conn_2", ws=OTHER_WS)
    service, _, _, _, _ = build_service(connections=connections)

    found = await service.list_connections(context())
    assert [c.id for c in found] == ["conn_1"]


async def test_danh_muc_provider_bao_dung_cai_nao_da_cau_hinh() -> None:
    service, _, _, _, _ = build_service(
        provider_credentials=FakeProviderCredentials(configured=frozenset({"slack"}))
    )

    views = {v.descriptor.id: v.configured for v in service.list_providers()}
    assert views == {"slack": True, "google": False, "notion": False}


# ─────────────────────────────────────────────────────────────────────────────
# Phân giải credential
# ─────────────────────────────────────────────────────────────────────────────


def build_resolver(
    connections: FakeConnections,
    credentials: FakeCredentials,
    oauth: FakeOAuth,
    lock: FakeLock | None = None,
) -> tuple[CredentialResolver, FakeLock]:
    locks = lock or FakeLock()
    resolver = CredentialResolver(
        connections=connections, credentials=credentials, oauth=oauth, locks=locks
    )
    return resolver, locks


async def seed(
    *,
    status: ConnectionStatus = ConnectionStatus.ACTIVE,
    scopes: frozenset[str] = frozenset({"openid"}),
    expires_at: datetime | None = None,
    tokens: OAuthTokens | None = None,
    tenant: str = ORG,
) -> tuple[FakeConnections, FakeCredentials]:
    connections = FakeConnections()
    connections.rows["conn_1"] = make_connection(
        status=status, scopes=scopes, expires_at=expires_at
    )
    credentials = FakeCredentials()
    if tokens is not None:
        await credentials.save(
            connection_id="conn_1",
            tenant_id=tenant,
            tokens=tokens,
            at=datetime.now(tz=UTC),
        )
    return connections, credentials


async def test_ket_noi_con_han_thi_tra_token_va_khong_lam_moi() -> None:
    connections, credentials = await seed(
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
        tokens=OAuthTokens(access_token="at-1", refresh_token="rt-1"),
    )
    oauth = FakeOAuth()
    resolver, lock = build_resolver(connections, credentials, oauth)

    resolved = await resolver.resolve(
        connection_id="conn_1", tenant_id=ORG, workspace_id=WS, acting_user_id=USER
    )

    assert resolved.access_token == "at-1"
    assert oauth.refresh_calls == 0
    assert lock.acquisitions == 0


async def test_repr_cua_resolved_credential_khong_chua_token() -> None:
    connections, credentials = await seed(tokens=OAuthTokens(access_token="at-bi-mat"))
    resolver, _ = build_resolver(connections, credentials, FakeOAuth())

    resolved = await resolver.resolve(
        connection_id="conn_1", tenant_id=ORG, workspace_id=WS, acting_user_id=USER
    )
    assert "at-bi-mat" not in repr(resolved)


async def test_sap_het_han_thi_lam_moi_va_ghi_lai_han_moi() -> None:
    moi = datetime.now(tz=UTC) + timedelta(hours=2)
    connections, credentials = await seed(
        expires_at=datetime.now(tz=UTC) + timedelta(seconds=5),
        tokens=OAuthTokens(access_token="at-cu", refresh_token="rt-1"),
    )
    oauth = FakeOAuth(refreshed=OAuthTokens(access_token="at-moi", expires_at=moi))
    resolver, lock = build_resolver(connections, credentials, oauth)

    resolved = await resolver.resolve(
        connection_id="conn_1", tenant_id=ORG, workspace_id=WS, acting_user_id=USER
    )

    assert resolved.access_token == "at-moi"
    assert oauth.refresh_calls == 1
    assert lock.acquisitions == 1
    assert connections.rows["conn_1"].expires_at == moi


async def test_lam_moi_giu_lai_refresh_token_cu_khi_provider_khong_tra_lai() -> None:
    """Nhiều provider chỉ trả refresh token ở lần cấp đầu tiên.

    Ghi đè bằng ``None`` là tự tay vứt mất đường làm mới của lần sau — kết nối
    sẽ chết ở chu kỳ kế tiếp, cách xa nguyên nhân.
    """
    connections, credentials = await seed(
        expires_at=datetime.now(tz=UTC) + timedelta(seconds=5),
        tokens=OAuthTokens(access_token="at-cu", refresh_token="rt-goc"),
    )
    oauth = FakeOAuth(
        refreshed=OAuthTokens(
            access_token="at-moi",
            refresh_token=None,
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
        )
    )
    resolver, _ = build_resolver(connections, credentials, oauth)

    await resolver.resolve(
        connection_id="conn_1", tenant_id=ORG, workspace_id=WS, acting_user_id=USER
    )

    stored = await credentials.load(connection_id="conn_1", tenant_id=ORG)
    assert stored is not None
    assert stored.refresh_token == "rt-goc"


async def test_lay_lock_xong_thay_nguoi_khac_da_lam_moi_thi_thoi() -> None:
    """Đọc lại sau khi có lock. Bỏ bước này thì lock chỉ xếp hàng các lần làm
    mới thừa chứ không loại bỏ được cái nào."""
    connections, credentials = await seed(
        expires_at=datetime.now(tz=UTC) + timedelta(seconds=5),
        tokens=OAuthTokens(access_token="at-cu", refresh_token="rt-1"),
    )

    def nguoi_khac_vua_lam_moi() -> None:
        connections.rows["conn_1"] = replace(
            connections.rows["conn_1"],
            expires_at=datetime.now(tz=UTC) + timedelta(hours=3),
        )

    oauth = FakeOAuth()
    resolver, lock = build_resolver(
        connections, credentials, oauth, FakeLock(on_acquire=nguoi_khac_vua_lam_moi)
    )

    resolved = await resolver.resolve(
        connection_id="conn_1", tenant_id=ORG, workspace_id=WS, acting_user_id=USER
    )

    assert lock.acquisitions == 1
    assert oauth.refresh_calls == 0
    assert resolved.access_token == "at-cu"


async def test_khong_co_refresh_token_thi_danh_dau_het_han() -> None:
    connections, credentials = await seed(
        expires_at=datetime.now(tz=UTC) - timedelta(seconds=1),
        tokens=OAuthTokens(access_token="at-cu", refresh_token=None),
    )
    resolver, _ = build_resolver(connections, credentials, FakeOAuth())

    with pytest.raises(ConnectionExpiredError):
        await resolver.resolve(
            connection_id="conn_1", tenant_id=ORG, workspace_id=WS, acting_user_id=USER
        )

    assert connections.rows["conn_1"].status is ConnectionStatus.EXPIRED
    assert connections.rows["conn_1"].last_error == "access_token_expired"


async def test_provider_tu_choi_vinh_vien_thi_doi_sang_reconnect_required() -> None:
    """Thử lại không giúp được gì — phải có người vào bấm kết nối lại."""
    connections, credentials = await seed(
        expires_at=datetime.now(tz=UTC) - timedelta(seconds=1),
        tokens=OAuthTokens(access_token="at-cu", refresh_token="rt-het-hieu-luc"),
    )
    oauth = FakeOAuth(refresh_error=ProviderPermanentError("invalid_grant"))
    resolver, _ = build_resolver(connections, credentials, oauth)

    with pytest.raises(ConnectionExpiredError) as exc:
        await resolver.resolve(
            connection_id="conn_1", tenant_id=ORG, workspace_id=WS, acting_user_id=USER
        )

    assert exc.value.retryable is False
    assert connections.rows["conn_1"].status is ConnectionStatus.RECONNECT_REQUIRED
    assert connections.rows["conn_1"].last_error == "refresh_rejected"


async def test_mat_credential_thi_danh_dau_can_ket_noi_lai() -> None:
    connections, credentials = await seed(tokens=None)
    resolver, _ = build_resolver(connections, credentials, FakeOAuth())

    with pytest.raises(ConnectionExpiredError):
        await resolver.resolve(
            connection_id="conn_1", tenant_id=ORG, workspace_id=WS, acting_user_id=USER
        )

    assert connections.rows["conn_1"].status is ConnectionStatus.RECONNECT_REQUIRED
    assert connections.rows["conn_1"].last_error == "credential_missing"


@pytest.mark.parametrize(
    "status",
    [
        ConnectionStatus.DISABLED,
        ConnectionStatus.REVOKED,
        ConnectionStatus.EXPIRED,
        ConnectionStatus.RECONNECT_REQUIRED,
    ],
)
async def test_ket_noi_khong_active_thi_khong_lay_duoc_credential(
    status: ConnectionStatus,
) -> None:
    connections, credentials = await seed(status=status, tokens=OAuthTokens(access_token="at"))
    resolver, _ = build_resolver(connections, credentials, FakeOAuth())

    with pytest.raises(ConnectionExpiredError):
        await resolver.resolve(
            connection_id="conn_1", tenant_id=ORG, workspace_id=WS, acting_user_id=USER
        )


async def test_thieu_scope_thi_khong_lay_duoc_credential() -> None:
    connections, credentials = await seed(
        scopes=frozenset({"openid"}), tokens=OAuthTokens(access_token="at")
    )
    resolver, _ = build_resolver(connections, credentials, FakeOAuth())

    with pytest.raises(MissingScopeError):
        await resolver.resolve(
            connection_id="conn_1",
            tenant_id=ORG,
            workspace_id=WS,
            acting_user_id=USER,
            required_scopes=frozenset({"drive.readonly"}),
        )


async def test_khong_lay_duoc_credential_cua_tenant_khac() -> None:
    """Ranh giới tenant ở đường nóng nhất — nơi credential thật sự được giao ra."""
    connections, credentials = await seed(tokens=OAuthTokens(access_token="at"))
    resolver, _ = build_resolver(connections, credentials, FakeOAuth())

    with pytest.raises(AuthorizationError):
        await resolver.resolve(
            connection_id="conn_1",
            tenant_id=OTHER_ORG,
            workspace_id=WS,
            acting_user_id=USER,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Quyền sở hữu và chính sách của organization
#
# Nhóm này canh đúng câu hỏi đã sinh ra nó: "Agent có dùng nhầm tài khoản của
# người khác không?"
# ─────────────────────────────────────────────────────────────────────────────

BINH = "usr_binh"


def private_of(user: str, *, connection_id: str = "conn_p") -> Connection:
    return make_connection(
        connection_id=connection_id,
        sharing=SharingPolicy.PRIVATE,
        owner=user,
        account_id=f"acct-{user}",
    )


async def test_ket_noi_rieng_khong_hien_ra_voi_nguoi_khac() -> None:
    connections = FakeConnections()
    connections.rows["conn_an"] = private_of(USER, connection_id="conn_an")
    connections.rows["conn_binh"] = private_of(BINH, connection_id="conn_binh")
    connections.rows["conn_chung"] = make_connection(connection_id="conn_chung")
    service, _, _, _, _ = build_service(connections=connections)

    thay = await service.list_connections(context(user=USER))

    assert {c.id for c in thay} == {"conn_an", "conn_chung"}


async def test_khong_doc_duoc_ket_noi_rieng_cua_nguoi_khac() -> None:
    """Cùng org, cùng workspace, chỉ khác người — vẫn phải bị chặn."""
    connections = FakeConnections()
    connections.rows["conn_binh"] = private_of(BINH, connection_id="conn_binh")
    service, _, _, _, _ = build_service(connections=connections)

    with pytest.raises(AuthorizationError):
        await service.get_connection("conn_binh", context(user=USER))


async def test_quan_tri_vien_cung_khong_doc_duoc_ket_noi_rieng() -> None:
    """Ẩn đi nghĩa là ẩn với cả quản trị viên. Họ chỉ được thu hồi."""
    connections = FakeConnections()
    connections.rows["conn_binh"] = private_of(BINH, connection_id="conn_binh")
    service, _, _, _, _ = build_service(connections=connections)

    with pytest.raises(AuthorizationError):
        await service.get_connection("conn_binh", context(user=USER, can_manage=True))


async def test_ws_admin_khong_tam_dung_duoc_ket_noi_rieng_cua_nguoi_khac() -> None:
    """Quản trị viên workspace quản lý tài khoản chung, không quản lý tài khoản
    cá nhân của thành viên."""
    connections = FakeConnections()
    connections.rows["conn_binh"] = private_of(BINH, connection_id="conn_binh")
    service, _, _, _, _ = build_service(connections=connections)

    with pytest.raises(AuthorizationError):
        await service.disable(connection_id="conn_binh", context=context(user=USER))


async def test_chinh_chu_quan_ly_duoc_ket_noi_rieng_du_khong_phai_ws_admin() -> None:
    connections = FakeConnections()
    connections.rows["conn_binh"] = private_of(BINH, connection_id="conn_binh")
    service, _, _, _, _ = build_service(connections=connections)

    paused = await service.disable(
        connection_id="conn_binh", context=context(user=BINH, can_manage=False)
    )
    assert paused.status is ConnectionStatus.DISABLED


async def test_quan_tri_vien_org_thu_hoi_duoc_ket_noi_rieng_cua_nguoi_khac() -> None:
    """Bài toán nhân viên nghỉ việc.

    Người duy nhất quản lý được một kết nối riêng là chính chủ — mà chính chủ
    thì có thể không còn đăng nhập được nữa. Không có ngoại lệ này thì credential
    của họ nằm lại trong hệ thống vĩnh viễn.
    """
    connections = FakeConnections()
    connections.rows["conn_binh"] = private_of(BINH, connection_id="conn_binh")
    credentials = FakeCredentials()
    await credentials.save(
        connection_id="conn_binh",
        tenant_id=ORG,
        tokens=OAuthTokens(access_token="at"),
        at=datetime.now(tz=UTC),
    )
    service, _, _, _, _ = build_service(connections=connections, credentials=credentials)

    revoked = await service.revoke(
        connection_id="conn_binh", context=context(user=USER, is_org_admin=True)
    )

    assert revoked.status is ConnectionStatus.REVOKED
    assert "conn_binh" in credentials.deleted


async def test_thanh_vien_thuong_khong_thu_hoi_duoc_ket_noi_rieng_cua_nguoi_khac() -> None:
    connections = FakeConnections()
    connections.rows["conn_binh"] = private_of(BINH, connection_id="conn_binh")
    service, _, _, _, _ = build_service(connections=connections)

    with pytest.raises(AuthorizationError):
        await service.revoke(
            connection_id="conn_binh",
            context=context(user=USER, can_manage=False, is_org_admin=False),
        )


async def test_hai_nguoi_ket_noi_cung_provider_thi_ra_hai_ban_ghi() -> None:
    """Gmail của An và Gmail của Bình là hai kết nối, không phải một."""
    connections = FakeConnections()
    oauth = FakeOAuth(
        tokens=OAuthTokens(access_token="at", scopes=frozenset({"openid"})),
        account=ProviderAccount(id="acct-chung", label="cong-ty"),
    )
    service, _, _, states, _ = build_service(connections=connections, oauth=oauth)

    for who in (USER, BINH):
        await service.start_authorization(
            context=context(user=who, can_manage=False),
            provider="google",
            sharing=SharingPolicy.PRIVATE,
            scopes=frozenset({"openid"}),
        )
        await service.complete_authorization(state=next(iter(states.rows)), code="c")

    assert len(connections.rows) == 2
    assert {c.owner_user_id for c in connections.rows.values()} == {USER, BINH}


async def test_uy_quyen_lai_khong_doi_duoc_pham_vi() -> None:
    """Biến kết nối riêng thành dùng chung là hành động chia sẻ dữ liệu.

    Nó phải tường minh, không được nấp trong một lần bấm "kết nối lại".
    """
    connections = FakeConnections()
    connections.rows["conn_p"] = private_of(USER)
    service, _, _, states, _ = build_service(connections=connections)

    await service.start_authorization(
        context=context(user=USER),
        provider="google",
        sharing=SharingPolicy.WORKSPACE,
        reconnect_connection_id="conn_p",
    )

    assert next(iter(states.rows.values())).sharing is SharingPolicy.PRIVATE


# ─── Chính sách của organization ─────────────────────────────────────────────


async def test_org_managed_thi_khong_them_duoc_tai_khoan_rieng() -> None:
    service, _, _, states, _ = build_service(settings=FakeSettings(ConnectionPolicy.MANAGED))

    with pytest.raises(ValidationError) as exc:
        await service.start_authorization(
            context=context(can_manage=False),
            provider="slack",
            sharing=SharingPolicy.PRIVATE,
        )

    assert exc.value.details["connection_policy"] == "managed"
    assert not states.rows


async def test_org_managed_van_them_duoc_tai_khoan_chung() -> None:
    service, _, _, states, _ = build_service(settings=FakeSettings(ConnectionPolicy.MANAGED))

    await service.start_authorization(
        context=context(), provider="slack", sharing=SharingPolicy.WORKSPACE
    )
    assert len(states.rows) == 1


async def test_org_chua_cau_hinh_thi_mac_dinh_la_open() -> None:
    """Không có row nghĩa là chưa cấu hình, không phải là bị cấm."""
    service, _, _, _, _ = build_service(settings=FakeSettings(policy=None))
    assert await service.get_policy(context()) is ConnectionPolicy.OPEN


async def test_chi_owner_doi_duoc_chinh_sach() -> None:
    service, _, _, _, _ = build_service()

    with pytest.raises(AuthorizationError):
        await service.set_policy(
            context=context(is_org_owner=False, is_org_admin=True),
            policy=ConnectionPolicy.MANAGED,
        )


async def test_siet_chinh_sach_thi_tam_dung_ket_noi_rieng_dang_song() -> None:
    """Siết sang MANAGED thì kết nối riêng không còn hợp lệ — nhưng dùng
    DISABLED chứ không xoá, để đảo lại được nếu org đổi ý."""
    connections = FakeConnections()
    connections.rows["conn_an"] = private_of(USER, connection_id="conn_an")
    connections.rows["conn_binh"] = private_of(BINH, connection_id="conn_binh")
    connections.rows["conn_chung"] = make_connection(connection_id="conn_chung")
    service, _, _, _, _ = build_service(
        connections=connections, settings=FakeSettings(ConnectionPolicy.OPEN)
    )

    policy, disabled = await service.set_policy(context=context(), policy=ConnectionPolicy.MANAGED)

    assert policy is ConnectionPolicy.MANAGED
    assert disabled == 2
    assert connections.rows["conn_an"].status is ConnectionStatus.DISABLED
    assert connections.rows["conn_binh"].status is ConnectionStatus.DISABLED
    # Kết nối dùng chung không bị đụng tới.
    assert connections.rows["conn_chung"].status is ConnectionStatus.ACTIVE


async def test_noi_long_chinh_sach_khong_tu_bat_lai_gi_ca() -> None:
    """Bật lại là quyết định của chính chủ, không phải hệ quả tự động."""
    connections = FakeConnections()
    connections.rows["conn_an"] = replace(
        private_of(USER, connection_id="conn_an"), status=ConnectionStatus.DISABLED
    )
    service, _, _, _, _ = build_service(
        connections=connections, settings=FakeSettings(ConnectionPolicy.MANAGED)
    )

    _, disabled = await service.set_policy(context=context(), policy=ConnectionPolicy.OPEN)

    assert disabled == 0
    assert connections.rows["conn_an"].status is ConnectionStatus.DISABLED


async def test_chi_quan_tri_vien_org_xem_duoc_danh_sach_audit() -> None:
    connections = FakeConnections()
    connections.rows["conn_binh"] = private_of(BINH, connection_id="conn_binh")
    service, _, _, _, _ = build_service(connections=connections)

    with pytest.raises(AuthorizationError):
        await service.list_for_audit(context(is_org_admin=False, is_org_owner=False))

    thay = await service.list_for_audit(context(is_org_admin=True))
    assert [c.id for c in thay] == ["conn_binh"]


# ─── Phân giải credential theo người ─────────────────────────────────────────


async def test_resolver_tu_choi_ket_noi_rieng_cua_nguoi_khac() -> None:
    """Câu hỏi cốt lõi: Agent chạy cho Bình có dùng được tài khoản của An không.

    Đây là chỗ credential thật sự được giao ra, nên đây là chỗ phải trả lời.
    """
    connections = FakeConnections()
    connections.rows["conn_1"] = private_of(USER, connection_id="conn_1")
    credentials = FakeCredentials()
    await credentials.save(
        connection_id="conn_1",
        tenant_id=ORG,
        tokens=OAuthTokens(access_token="at-cua-an"),
        at=datetime.now(tz=UTC),
    )
    resolver, _ = build_resolver(connections, credentials, FakeOAuth())

    # Chính chủ thì lấy được.
    resolved = await resolver.resolve(
        connection_id="conn_1", tenant_id=ORG, workspace_id=WS, acting_user_id=USER
    )
    assert resolved.access_token == "at-cua-an"

    # Người khác thì không.
    with pytest.raises(AuthorizationError):
        await resolver.resolve(
            connection_id="conn_1", tenant_id=ORG, workspace_id=WS, acting_user_id=BINH
        )


async def test_resolver_tu_choi_ket_noi_cua_workspace_khac() -> None:
    """Cùng organization nhưng khác workspace — lỗ hổng đã có trước thay đổi này."""
    connections = FakeConnections()
    connections.rows["conn_1"] = make_connection(connection_id="conn_1", ws=OTHER_WS)
    credentials = FakeCredentials()
    await credentials.save(
        connection_id="conn_1",
        tenant_id=ORG,
        tokens=OAuthTokens(access_token="at"),
        at=datetime.now(tz=UTC),
    )
    resolver, _ = build_resolver(connections, credentials, FakeOAuth())

    with pytest.raises(AuthorizationError):
        await resolver.resolve(
            connection_id="conn_1", tenant_id=ORG, workspace_id=WS, acting_user_id=USER
        )


async def test_ket_noi_dung_chung_thi_ai_trong_workspace_cung_dung_duoc() -> None:
    """Mặt còn lại của cùng một luật: tài khoản chung vẫn phải dùng chung được."""
    connections = FakeConnections()
    connections.rows["conn_1"] = make_connection(connection_id="conn_1")
    credentials = FakeCredentials()
    await credentials.save(
        connection_id="conn_1",
        tenant_id=ORG,
        tokens=OAuthTokens(access_token="at-chung"),
        at=datetime.now(tz=UTC),
    )
    resolver, _ = build_resolver(connections, credentials, FakeOAuth())

    resolved = await resolver.resolve(
        connection_id="conn_1", tenant_id=ORG, workspace_id=WS, acting_user_id=BINH
    )
    assert resolved.access_token == "at-chung"
