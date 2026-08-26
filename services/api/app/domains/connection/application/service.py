"""Use case của Connection: vòng OAuth, vòng đời kết nối, phân giải credential."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.errors import (
    AuthorizationError,
    ConnectionExpiredError,
    ProviderPermanentError,
    ValidationError,
)
from app.core.ids import ResourceKind, new_id
from app.core.logging import get_logger
from app.domains.connection.application.ports import (
    ClientCredentials,
    ConnectionRepository,
    CredentialStore,
    OAuthClient,
    OAuthStateStore,
    OrganizationSettingsRepository,
    PendingAuthorization,
    ProviderAccount,
    ProviderCredentials,
    RefreshLock,
    TenantGuard,
    WorkspaceContext,
)
from app.domains.connection.domain.entities import (
    DEFAULT_CONNECTION_POLICY,
    Connection,
    ConnectionActor,
    ConnectionPolicy,
    ConnectionStatus,
    OAuthTokens,
    SharingPolicy,
    default_display_name,
    ensure_can_change_policy,
    ensure_can_create,
    ensure_can_disable,
    ensure_can_enable,
    ensure_can_manage,
    ensure_can_revoke,
    ensure_not_terminal,
    ensure_scopes_requestable,
    ensure_usable,
    needs_refresh,
    normalize_display_name,
    visible_to,
)
from app.domains.connection.domain.providers import (
    PkcePair,
    ProviderDescriptor,
    build_authorize_url,
    get_provider,
    list_providers,
    new_pkce_pair,
    read_path,
)

log = get_logger(__name__)

#: Người dùng có mười phút để bấm xong nút chấp thuận ở phía provider.
#:
#: Dài hơn thì cửa sổ để một `state` bị đánh cắp còn dùng được cũng dài ra; ngắn
#: hơn thì người dùng phải đăng nhập vào provider giữa chừng sẽ hụt mất.
STATE_TTL_SECONDS = 600

#: Kết nối không thuộc tenant của người gọi được đối xử y như không tồn tại —
#: cùng thông điệp, cùng mã lỗi. Trả lời khác đi là để lộ rằng id đó có thật.
_NOT_FOUND = "Không tìm thấy kết nối này."


# ─────────────────────────────────────────────────────────────────────────────
# Read model
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ProviderView:
    """Một provider trong danh mục, kèm việc nó đã cấu hình được chưa."""

    descriptor: ProviderDescriptor
    configured: bool


@dataclass(frozen=True, slots=True)
class Authorization:
    """Kết quả của bước một: nơi cần đưa trình duyệt người dùng tới."""

    authorize_url: str
    state: str
    expires_in_seconds: int


# ─────────────────────────────────────────────────────────────────────────────
# Use case người dùng
# ─────────────────────────────────────────────────────────────────────────────


class ConnectionService:
    def __init__(
        self,
        *,
        connections: ConnectionRepository,
        credentials: CredentialStore,
        states: OAuthStateStore,
        oauth: OAuthClient,
        provider_credentials: ProviderCredentials,
        org_settings: OrganizationSettingsRepository,
        tenant: TenantGuard,
        redirect_uri: str,
    ) -> None:
        self._connections = connections
        self._credentials = credentials
        self._states = states
        self._oauth = oauth
        self._provider_credentials = provider_credentials
        self._org_settings = org_settings
        self._tenant = tenant
        self._redirect_uri = redirect_uri

    # ─── Danh mục ────────────────────────────────────────────────────────────

    def list_providers(self) -> list[ProviderView]:
        return [
            ProviderView(
                descriptor=descriptor,
                configured=self._provider_credentials.get(descriptor.id) is not None,
            )
            for descriptor in list_providers()
        ]

    # ─── Đọc ─────────────────────────────────────────────────────────────────

    async def list_connections(self, context: WorkspaceContext) -> list[Connection]:
        """Kết nối mà chính người gọi nhìn thấy. Lọc nằm ở SQL."""
        return await self._connections.list_visible(
            workspace_id=context.workspace_id, user_id=context.user_id
        )

    async def get_connection(self, connection_id: str, context: WorkspaceContext) -> Connection:
        return await self._require_connection(connection_id, context)

    async def list_for_audit(self, context: WorkspaceContext) -> list[Connection]:
        """Mọi kết nối trong workspace, cho quản trị viên organization.

        Tồn tại vì bài toán nhân viên nghỉ việc: nếu quản trị viên không nhìn
        thấy kết nối riêng của người khác thì cũng không biết có gì cần thu hồi.
        Router chỉ được trả ra **sự tồn tại** — không scope, không danh tính tài
        khoản, không tên hiển thị (tên mặc định có chứa địa chỉ tài khoản).
        """
        if not context.is_org_admin:
            raise AuthorizationError("Chỉ quản trị viên organization mới xem được danh sách này.")
        return await self._connections.list_all_for_audit(context.workspace_id)

    # ─── Chính sách của organization ─────────────────────────────────────────

    async def get_policy(self, context: WorkspaceContext) -> ConnectionPolicy:
        stored = await self._org_settings.get_policy(context.organization_id)
        return stored if stored is not None else DEFAULT_CONNECTION_POLICY

    async def set_policy(
        self, *, context: WorkspaceContext, policy: ConnectionPolicy
    ) -> tuple[ConnectionPolicy, int]:
        """Đổi chính sách. Trả về chính sách mới và số kết nối bị tạm dừng.

        Siết từ OPEN sang MANAGED thì mọi kết nối riêng đang sống bị chuyển sang
        DISABLED — chúng không còn hợp lệ dưới chính sách mới. Dùng DISABLED chứ
        không xoá để đảo lại được nếu org đổi ý.
        """
        ensure_can_change_policy(_actor(context))

        current = await self.get_policy(context)
        now = datetime.now(tz=UTC)
        await self._org_settings.set_policy(context.organization_id, policy=policy, at=now)

        disabled = 0
        if current is ConnectionPolicy.OPEN and policy is ConnectionPolicy.MANAGED:
            disabled = await self._connections.disable_private_in_organization(
                context.organization_id, at=now
            )

        log.info(
            "connection_policy_changed",
            organization_id=context.organization_id,
            previous=str(current),
            policy=str(policy),
            disabled_private_connections=disabled,
        )
        return policy, disabled

    # ─── Bước một: đưa người dùng sang provider ──────────────────────────────

    async def start_authorization(
        self,
        *,
        context: WorkspaceContext,
        provider: str,
        sharing: SharingPolicy = SharingPolicy.PRIVATE,
        scopes: frozenset[str] | None = None,
        reconnect_connection_id: str | None = None,
    ) -> Authorization:
        """Dựng URL uỷ quyền và ghi nhớ state của vòng này.

        ``sharing`` mặc định PRIVATE. Mặc định an toàn khi ai đó quên truyền:
        kết nối riêng chỉ chính chủ dùng được, còn mặc định ngược lại sẽ âm thầm
        đem tài khoản cá nhân chia cho cả workspace.
        """
        descriptor = get_provider(provider)
        client = self._require_client_credentials(descriptor)

        ensure_can_create(
            sharing=sharing,
            policy=await self.get_policy(context),
            actor=_actor(context),
        )

        requested = ensure_scopes_requestable(
            scopes if scopes is not None else descriptor.default_scopes
        )

        if reconnect_connection_id is not None:
            existing = await self._require_connection(reconnect_connection_id, context)
            ensure_not_terminal(existing)
            ensure_can_manage(existing, actor=_actor(context))
            if existing.provider != descriptor.id:
                raise ValidationError("Kết nối này thuộc provider khác.")
            # Uỷ quyền lại không được đổi phạm vi: biến một kết nối riêng thành
            # dùng chung là hành động chia sẻ dữ liệu, phải tường minh chứ không
            # nấp trong một lần bấm "kết nối lại".
            sharing = existing.sharing

        pkce: PkcePair | None = new_pkce_pair() if descriptor.supports_pkce else None

        # 32 byte ngẫu nhiên: `state` vừa là chống CSRF theo RFC 6749 §10.12,
        # vừa là khoá tra `PendingAuthorization`. Đoán được nó là gắn được một
        # kết nối vào tenant người khác.
        state = secrets.token_urlsafe(32)

        await self._states.put(
            state,
            PendingAuthorization(
                provider=descriptor.id,
                organization_id=context.organization_id,
                workspace_id=context.workspace_id,
                user_id=context.user_id,
                redirect_uri=self._redirect_uri,
                requested_scopes=requested,
                sharing=sharing,
                code_verifier=pkce.verifier if pkce else None,
                reconnect_connection_id=reconnect_connection_id,
            ),
            ttl_seconds=STATE_TTL_SECONDS,
        )

        log.info(
            "connection_authorization_started",
            provider=descriptor.id,
            organization_id=context.organization_id,
            workspace_id=context.workspace_id,
            sharing=str(sharing),
            reconnect=reconnect_connection_id is not None,
        )

        return Authorization(
            authorize_url=build_authorize_url(
                descriptor,
                client_id=client.client_id,
                redirect_uri=self._redirect_uri,
                scopes=requested,
                state=state,
                pkce=pkce,
            ),
            state=state,
            expires_in_seconds=STATE_TTL_SECONDS,
        )

    # ─── Bước hai: provider gọi ngược lại ────────────────────────────────────

    async def complete_authorization(self, *, state: str, code: str) -> Connection:
        """Đổi authorization code lấy token và lưu kết nối.

        Không nhận tenant từ tham số: request này tới từ trình duyệt sau một
        lần chuyển hướng và **không mang access token nào**. Tenant chỉ có một
        nguồn hợp lệ là ``PendingAuthorization`` mà bước một đã cất đi.
        """
        pending = await self._states.pop(state)
        if pending is None:
            # Gộp chung mọi lý do — hết hạn, đã dùng, bịa ra. Phân biệt chúng là
            # nói cho người gửi biết `state` nào từng tồn tại.
            raise ValidationError(
                "Yêu cầu kết nối không còn hiệu lực. Hãy bắt đầu lại từ trang kết nối."
            )

        descriptor = get_provider(pending.provider)
        tokens, body = await self._oauth.exchange_code(
            provider=descriptor.id,
            code=code,
            redirect_uri=pending.redirect_uri,
            code_verifier=pending.code_verifier,
        )

        account = await self._resolve_account(descriptor, tokens, body)

        # Provider không phải lúc nào cũng dội lại scope đã cấp. Im lặng thì
        # hiểu là đã cấp đúng những gì mình xin — chứ không phải là chưa cấp gì.
        granted = tokens.scopes or pending.requested_scopes
        missing = pending.requested_scopes - granted
        status = ConnectionStatus.MISSING_SCOPE if missing else ConnectionStatus.ACTIVE

        now = datetime.now(tz=UTC)
        connection = await self._upsert_connection(
            pending=pending,
            descriptor=descriptor,
            account=account,
            granted=granted,
            status=status,
            expires_at=tokens.expires_at,
            now=now,
        )

        await self._credentials.save(
            connection_id=connection.id,
            tenant_id=pending.organization_id,
            tokens=tokens,
            at=now,
        )

        log.info(
            "connection_authorized",
            connection_id=connection.id,
            provider=descriptor.id,
            organization_id=pending.organization_id,
            workspace_id=pending.workspace_id,
            status=str(status),
            missing_scopes=sorted(missing),
        )
        return connection

    async def _resolve_account(
        self,
        descriptor: ProviderDescriptor,
        tokens: OAuthTokens,
        body: dict[str, object],
    ) -> ProviderAccount:
        """Danh tính tài khoản: có sẵn trong phản hồi token, hoặc phải đi hỏi."""
        if descriptor.reads_identity_from_token_response:
            return ProviderAccount(
                id=read_path(body, descriptor.account_id_path),
                label=read_path(body, descriptor.account_label_path),
            )
        return await self._oauth.fetch_account(
            provider=descriptor.id, access_token=tokens.access_token
        )

    async def _upsert_connection(
        self,
        *,
        pending: PendingAuthorization,
        descriptor: ProviderDescriptor,
        account: ProviderAccount,
        granted: frozenset[str],
        status: ConnectionStatus,
        expires_at: datetime | None,
        now: datetime,
    ) -> Connection:
        """Cập nhật kết nối cũ nếu có, không thì tạo mới.

        Uỷ quyền lại cùng một tài khoản provider phải rơi vào đúng bản ghi cũ.
        Nếu mỗi lần bấm "Connect" lại đẻ ra một dòng mới thì workspace sẽ đầy
        những kết nối trùng nhau, mà chỉ cái mới nhất là còn token dùng được.
        """
        target_id = pending.reconnect_connection_id
        if target_id is None and account.id is not None:
            existing = await self._connections.find_active_for_account(
                workspace_id=pending.workspace_id,
                provider=descriptor.id,
                external_account_id=account.id,
                sharing=pending.sharing,
                owner_user_id=pending.user_id,
            )
            target_id = existing.id if existing else None

        if target_id is not None:
            await self._connections.update_authorization(
                target_id,
                status=status,
                granted_scopes=granted,
                expires_at=expires_at,
                external_account_id=account.id,
                external_account_label=account.label,
                connected_by=pending.user_id,
                at=now,
            )
            refreshed = await self._connections.get_by_id(target_id)
            if refreshed is None:
                raise ValidationError("Không đọc lại được kết nối vừa cập nhật.")
            return refreshed

        connection = Connection(
            id=new_id(ResourceKind.CONNECTION),
            organization_id=pending.organization_id,
            workspace_id=pending.workspace_id,
            provider=descriptor.id,
            display_name=default_display_name(
                provider_label=descriptor.label, account_label=account.label
            ),
            status=status,
            granted_scopes=granted,
            connected_by=pending.user_id,
            created_at=now,
            updated_at=now,
            sharing=pending.sharing,
            owner_user_id=pending.user_id,
            external_account_id=account.id,
            external_account_label=account.label,
            expires_at=expires_at,
        )
        await self._connections.add(connection)
        return connection

    # ─── Vòng đời ────────────────────────────────────────────────────────────

    async def rename(
        self, *, connection_id: str, context: WorkspaceContext, display_name: str
    ) -> Connection:
        connection = await self._require_connection(connection_id, context)
        ensure_can_manage(connection, actor=_actor(context))
        ensure_not_terminal(connection)

        name = normalize_display_name(display_name, fallback=connection.display_name)
        now = datetime.now(tz=UTC)
        await self._connections.rename(connection_id, display_name=name, at=now)
        return await self._reload(connection_id)

    async def disable(self, *, connection_id: str, context: WorkspaceContext) -> Connection:
        """Tạm dừng. Credential giữ nguyên, nên bật lại không phải uỷ quyền lại."""
        connection = await self._require_connection(connection_id, context)
        ensure_can_manage(connection, actor=_actor(context))
        ensure_can_disable(connection)

        now = datetime.now(tz=UTC)
        await self._connections.update_status(
            connection_id, status=ConnectionStatus.DISABLED, at=now
        )
        log.info("connection_disabled", connection_id=connection_id)
        return await self._reload(connection_id)

    async def enable(self, *, connection_id: str, context: WorkspaceContext) -> Connection:
        connection = await self._require_connection(connection_id, context)
        ensure_can_manage(connection, actor=_actor(context))
        ensure_can_enable(connection)

        now = datetime.now(tz=UTC)
        await self._connections.update_status(
            connection_id, status=ConnectionStatus.ACTIVE, at=now, last_error=None
        )
        log.info("connection_enabled", connection_id=connection_id)
        return await self._reload(connection_id)

    async def revoke(self, *, connection_id: str, context: WorkspaceContext) -> Connection:
        """Thu hồi hẳn: xoá credential, giữ lại bản ghi để lịch sử đọc được."""
        # Không dùng `_require_connection`: nó lọc theo tầm nhìn, mà quản trị
        # viên organization thì *không* nhìn thấy kết nối riêng của người khác —
        # họ chỉ được phép thu hồi nó. Tra bằng đường không lọc, rồi để
        # `ensure_can_revoke` quyết định.
        connection = await self._require_connection_in_scope(connection_id, context)
        ensure_can_revoke(connection, actor=_actor(context))
        ensure_not_terminal(connection)

        tokens = await self._credentials.load(
            connection_id=connection_id, tenant_id=context.organization_id
        )
        if tokens is not None:
            # Báo cho provider trước, nhưng không để nó chặn việc thu hồi: nếu
            # provider đang chết mà mình vẫn giữ token thì tình trạng còn tệ hơn.
            # Hiện thực của `revoke` nuốt lỗi, xem `oauth_client.py`.
            await self._oauth.revoke(provider=connection.provider, tokens=tokens)

        await self._credentials.delete(connection_id)

        now = datetime.now(tz=UTC)
        await self._connections.update_status(
            connection_id, status=ConnectionStatus.REVOKED, at=now, revoked_at=now
        )
        log.info(
            "connection_revoked",
            connection_id=connection_id,
            provider=connection.provider,
            organization_id=context.organization_id,
        )
        return await self._reload(connection_id)

    # ─── Nội bộ ──────────────────────────────────────────────────────────────

    def _require_client_credentials(self, descriptor: ProviderDescriptor) -> ClientCredentials:
        client = self._provider_credentials.get(descriptor.id)
        if client is None:
            raise ValidationError(
                f"{descriptor.label} chưa được cấu hình trên hệ thống này. "
                "Quản trị viên cần đặt client id và client secret cho provider.",
                details={"provider": descriptor.id},
            )
        return client

    async def _require_connection_in_scope(
        self, connection_id: str, context: WorkspaceContext
    ) -> Connection:
        """Đúng tenant và đúng workspace, **không** kiểm tầm nhìn.

        Chỉ dùng cho thu hồi. Mọi đường khác phải đi qua ``_require_connection``.
        """
        connection = await self._connections.get_by_id(connection_id)
        if (
            connection is None
            or connection.organization_id != context.organization_id
            or connection.workspace_id != context.workspace_id
        ):
            raise AuthorizationError(_NOT_FOUND)
        return connection

    async def _require_connection(
        self, connection_id: str, context: WorkspaceContext
    ) -> Connection:
        """Kết nối tồn tại, đúng tenant, đúng workspace, **và người gọi thấy được**.

        Kết nối riêng của người khác rơi vào đúng nhánh với kết nối không tồn
        tại: cùng lỗi, cùng thông điệp. Trả lời khác đi là xác nhận rằng người
        kia có một kết nối tới provider đó — chính là thứ "ẩn đi" phải giấu.

        Quản trị viên organization cũng không đi qua được cửa này; đường của họ
        là ``list_for_audit`` và ``revoke``, nơi họ thấy sự tồn tại nhưng không
        thấy chi tiết.
        """
        connection = await self._connections.get_by_id(connection_id)
        if (
            connection is None
            or connection.organization_id != context.organization_id
            or connection.workspace_id != context.workspace_id
            or not visible_to(connection, user_id=context.user_id)
        ):
            raise AuthorizationError(_NOT_FOUND)
        return connection

    async def _reload(self, connection_id: str) -> Connection:
        connection = await self._connections.get_by_id(connection_id)
        if connection is None:
            raise ValidationError("Không đọc lại được kết nối vừa thay đổi.")
        return connection


# ─────────────────────────────────────────────────────────────────────────────
# Phân giải credential — chỉ dành cho Integration Plane
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ResolvedCredential:
    """Token đã giải mã, kèm ngữ cảnh tối thiểu để gọi provider.

    Vòng đời phải ngắn nhất có thể (§21): dùng xong thì bỏ, đừng cất vào cache,
    đừng gắn vào một object sống lâu, và tuyệt đối đừng đưa vào prompt (§2.4).
    """

    connection_id: str
    provider: str
    access_token: str
    expires_at: datetime | None

    def __repr__(self) -> str:
        return (
            f"ResolvedCredential(connection_id={self.connection_id!r}, "
            f"provider={self.provider!r}, expires_at={self.expires_at!r})"
        )


class CredentialResolver:
    """Trả về credential dùng được, tự làm mới khi cần.

    Người dùng duy nhất của lớp này là Integration Gateway (§6.3). Nó **không**
    được dựng từ router — xem docstring đầu module.
    """

    def __init__(
        self,
        *,
        connections: ConnectionRepository,
        credentials: CredentialStore,
        oauth: OAuthClient,
        locks: RefreshLock,
    ) -> None:
        self._connections = connections
        self._credentials = credentials
        self._oauth = oauth
        self._locks = locks

    async def resolve(
        self,
        *,
        connection_id: str,
        tenant_id: str,
        workspace_id: str,
        acting_user_id: str,
        required_scopes: frozenset[str] = frozenset(),
    ) -> ResolvedCredential:
        """Credential dùng được cho **đúng người đang hành động**.

        ``acting_user_id`` là người mà lượt chạy này đại diện — trong một phòng
        chat nhiều người thì đó là người vừa gửi tin nhắn, không phải người tạo
        hội thoại. Truyền sai giá trị này là mở đúng cánh cửa mà kết nối PRIVATE
        sinh ra để đóng.
        """
        connection = await self._require_connection(
            connection_id, tenant_id, workspace_id=workspace_id, acting_user_id=acting_user_id
        )
        ensure_usable(connection, required_scopes=required_scopes)

        tokens = await self._load_tokens(connection, tenant_id)
        now = datetime.now(tz=UTC)

        if not needs_refresh(connection, now=now):
            return _to_resolved(connection, tokens)

        return await self._refresh_under_lock(connection, tenant_id, acting_user_id=acting_user_id)

    async def _refresh_under_lock(
        self, connection: Connection, tenant_id: str, *, acting_user_id: str
    ) -> ResolvedCredential:
        """Làm mới token, mỗi lúc chỉ một tiến trình được làm.

        Không có lock thì mọi Activity đang chạy cùng lúc trên một connection
        sẽ cùng phát hiện token hết hạn và cùng gọi làm mới. Với provider xoay
        refresh token sau mỗi lần dùng, lần làm mới thứ hai sẽ mang theo một
        refresh token vừa bị vô hiệu — provider trả ``invalid_grant``, và kết
        nối bị đánh dấu hỏng dù chẳng có gì hỏng cả.
        """
        async with self._locks.guard(connection.id):
            # Đọc lại sau khi có lock: rất có thể tiến trình vừa nhả lock đã làm
            # mới xong rồi. Bỏ bước này thì lock chỉ xếp hàng các lần làm mới
            # thừa chứ không loại bỏ được cái nào.
            fresh = await self._require_connection(
                connection.id,
                tenant_id,
                workspace_id=connection.workspace_id,
                acting_user_id=acting_user_id,
            )
            tokens = await self._load_tokens(fresh, tenant_id)

            if not needs_refresh(fresh, now=datetime.now(tz=UTC)):
                return _to_resolved(fresh, tokens)

            descriptor = get_provider(fresh.provider)
            if not descriptor.supports_refresh or tokens.refresh_token is None:
                await self._mark_broken(fresh, ConnectionStatus.EXPIRED, "access_token_expired")
                raise ConnectionExpiredError(
                    "Kết nối đã hết hạn và không tự làm mới được. Hãy kết nối lại.",
                    details={"connection_id": fresh.id},
                )

            renewed = await self._call_refresh(fresh, tokens.refresh_token)

            # Nhiều provider chỉ trả refresh token ở lần cấp đầu tiên. Ghi đè
            # bằng `None` ở đây là tự tay vứt mất đường làm mới lần sau.
            merged = OAuthTokens(
                access_token=renewed.access_token,
                refresh_token=renewed.refresh_token or tokens.refresh_token,
                expires_at=renewed.expires_at,
                scopes=renewed.scopes or fresh.granted_scopes,
                token_type=renewed.token_type,
            )

            now = datetime.now(tz=UTC)
            await self._credentials.save(
                connection_id=fresh.id, tenant_id=tenant_id, tokens=merged, at=now
            )
            await self._connections.update_expiry(fresh.id, expires_at=merged.expires_at, at=now)

            log.info(
                "connection_token_refreshed",
                connection_id=fresh.id,
                provider=fresh.provider,
                expires_at=merged.expires_at,
            )
            return ResolvedCredential(
                connection_id=fresh.id,
                provider=fresh.provider,
                access_token=merged.access_token,
                expires_at=merged.expires_at,
            )

    async def _call_refresh(self, connection: Connection, refresh_token: str) -> OAuthTokens:
        try:
            return await self._oauth.refresh(
                provider=connection.provider, refresh_token=refresh_token
            )
        except ProviderPermanentError as exc:
            # Provider từ chối dứt khoát — thường là người dùng đã gỡ app phía
            # họ. Thử lại không giúp được gì, nên đánh dấu để UI mời kết nối lại
            # thay vì để Temporal retry cho tới lúc cạn lượt.
            await self._mark_broken(
                connection, ConnectionStatus.RECONNECT_REQUIRED, "refresh_rejected"
            )
            raise ConnectionExpiredError(
                "Provider đã từ chối kết nối này. Hãy kết nối lại.",
                details={"connection_id": connection.id},
                cause=exc,
            ) from exc

    async def _mark_broken(
        self, connection: Connection, status: ConnectionStatus, reason: str
    ) -> None:
        await self._connections.update_status(
            connection.id,
            status=status,
            at=datetime.now(tz=UTC),
            last_error=reason,
        )
        log.warning(
            "connection_marked_broken",
            connection_id=connection.id,
            provider=connection.provider,
            status=str(status),
            reason=reason,
        )

    async def _require_connection(
        self,
        connection_id: str,
        tenant_id: str,
        *,
        workspace_id: str,
        acting_user_id: str,
    ) -> Connection:
        """Ba tầng ranh giới, kiểm cả ba ở đúng nơi credential được giao ra.

        Organization, workspace, rồi người. Thiếu tầng nào thì tầng đó thành
        đường đi vòng: chỉ kiểm organization là workflow của workspace này gọi
        được kết nối của workspace kia; kiểm tới workspace mà bỏ người là Agent
        chạy cho Bình dùng được tài khoản riêng của An.
        """
        connection = await self._connections.get_by_id(connection_id)
        if (
            connection is None
            or connection.organization_id != tenant_id
            or connection.workspace_id != workspace_id
            or not visible_to(connection, user_id=acting_user_id)
        ):
            raise AuthorizationError(_NOT_FOUND)
        return connection

    async def _load_tokens(self, connection: Connection, tenant_id: str) -> OAuthTokens:
        tokens = await self._credentials.load(connection_id=connection.id, tenant_id=tenant_id)
        if tokens is None:
            # Metadata còn mà credential mất — connection đã bị thu hồi, hoặc dữ
            # liệu không nhất quán. Cả hai đều phải kết nối lại mới dùng được.
            await self._mark_broken(
                connection, ConnectionStatus.RECONNECT_REQUIRED, "credential_missing"
            )
            raise ConnectionExpiredError(
                "Kết nối này không còn credential. Hãy kết nối lại.",
                details={"connection_id": connection.id},
            )
        return tokens


def _to_resolved(connection: Connection, tokens: OAuthTokens) -> ResolvedCredential:
    return ResolvedCredential(
        connection_id=connection.id,
        provider=connection.provider,
        access_token=tokens.access_token,
        expires_at=connection.expires_at,
    )


def _actor(context: WorkspaceContext) -> ConnectionActor:
    """Chuyển ``WorkspaceContext`` (application) sang ``ConnectionActor`` (domain).

    Tầng domain không được biết ``WorkspaceContext`` tồn tại — contract 2 bắt
    hướng phụ thuộc chạy một chiều infrastructure → application → domain. Đây là
    chỗ duy nhất bắc qua, và nó chỉ chuyển mấy cờ boolean.
    """
    return ConnectionActor(
        user_id=context.user_id,
        can_manage_workspace=context.can_manage_connections,
        is_org_admin=context.is_org_admin,
        is_org_owner=context.is_org_owner,
    )
