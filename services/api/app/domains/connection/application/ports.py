"""Interface mà tầng ``infrastructure`` của Connection phải hiện thực hoá."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domains.connection.domain.entities import (
    Connection,
    ConnectionPolicy,
    ConnectionStatus,
    OAuthTokens,
    SharingPolicy,
)


class ConnectionRepository(Protocol):
    async def add(self, connection: Connection) -> None: ...

    async def get_by_id(self, connection_id: str) -> Connection | None:
        """Tra theo id, không lọc tenant"""

    async def list_visible(self, *, workspace_id: str, user_id: str) -> list[Connection]:
        """Kết nối mà `user_id` được phép nhìn thấy trong workspace này."""

    async def list_all_for_audit(self, workspace_id: str) -> list[Connection]:
        """Mọi kết nối trong workspace, kể cả PRIVATE của người khác."""

    async def find_active_for_account(
        self,
        *,
        workspace_id: str,
        provider: str,
        external_account_id: str,
        sharing: SharingPolicy,
        owner_user_id: str,
    ) -> Connection | None:
        """Kết nối còn sống của cùng một tài khoản provider, trong cùng phạm vi.

        Dùng để lần uỷ quyền thứ hai cập nhật kết nối cũ thay vì đẻ ra bản sao.
        """

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
        """Ghi lại kết quả của một lần uỷ quyền thành công. Xoá ``last_error``.

        ``connected_by`` bị ghi đè mỗi lần, vì nó là dấu vết "ai bấm nút gần
        nhất" chứ không phải "ai tạo ra bản ghi". Quyền sở hữu nằm ở
        ``owner_user_id`` và **không** chuyển tay qua một lần uỷ quyền lại.
        """

    async def update_status(
        self,
        connection_id: str,
        *,
        status: ConnectionStatus,
        at: datetime,
        last_error: str | None = None,
        revoked_at: datetime | None = None,
    ) -> None: ...

    async def update_expiry(
        self, connection_id: str, *, expires_at: datetime | None, at: datetime
    ) -> None:
        """Cập nhật hạn token sau một lần làm mới."""

    async def rename(self, connection_id: str, *, display_name: str, at: datetime) -> None: ...

    async def disable_private_in_organization(self, organization_id: str, *, at: datetime) -> int:
        """Tạm dừng mọi kết nối riêng của một org. Trả về số dòng bị đổi.

        Chạy khi org siết chính sách sang MANAGED — kết nối riêng không còn hợp
        lệ dưới chính sách mới.
        """


class CredentialStore(Protocol):
    """Nơi cất token. Hiện thực phải mã hóa trước khi ghi."""

    async def save(
        self, *, connection_id: str, tenant_id: str, tokens: OAuthTokens, at: datetime
    ) -> None: ...

    async def load(self, *, connection_id: str, tenant_id: str) -> OAuthTokens | None: ...

    async def delete(self, connection_id: str) -> None:
        """Xoá hẳn. Thu hồi một kết nối phải xoá được credential mà giữ lại
        bản ghi metadata, để lịch sử vẫn đọc được."""


@dataclass(frozen=True, slots=True)
class PendingAuthorization:
    """State của một vòng ủy quyền đang dang dở."""

    provider: str
    organization_id: str
    workspace_id: str
    user_id: str
    redirect_uri: str
    requested_scopes: frozenset[str]
    sharing: SharingPolicy = SharingPolicy.PRIVATE
    code_verifier: str | None = None  # cho PKCE
    reconnect_connection_id: str | None = None

    def __repr__(self) -> str:
        # `code_verifier` là bí mật. Không để nó lọt vào log hay traceback.
        return (
            f"PendingAuthorization(provider={self.provider!r}, "
            f"organization_id={self.organization_id!r}, "
            f"workspace_id={self.workspace_id!r})"
        )


class OAuthStateStore(Protocol):
    """Lưu ``PendingAuthorization`` giữa lúc chuyển hướng đi và lúc callback về."""

    async def put(self, state: str, pending: PendingAuthorization, *, ttl_seconds: int) -> None: ...

    async def pop(self, state: str) -> PendingAuthorization | None:
        """Đọc **và xoá**, nguyên tử."""


@dataclass(frozen=True, slots=True)
class ProviderAccount:
    """Danh tính tài khoản phía provider."""

    id: str | None
    label: str | None


class OAuthClient(Protocol):
    """Nói chuyện với endpoint OAuth của provider."""

    async def exchange_code(
        self,
        *,
        provider: str,
        code: str,
        redirect_uri: str,
        code_verifier: str | None,
    ) -> tuple[OAuthTokens, dict[str, object]]:
        """Đổi authorization code lấy token.

        Trả kèm phần thân đã bỏ token đi, để nơi gọi đọc danh tính tài khoản mà
        không phải gọi lại provider lần nữa.
        """

    async def refresh(self, *, provider: str, refresh_token: str) -> OAuthTokens: ...

    async def fetch_account(self, *, provider: str, access_token: str) -> ProviderAccount:
        """Tra danh tính tài khoản ở endpoint mà descriptor khai báo."""

    async def revoke(self, *, provider: str, tokens: OAuthTokens) -> None:
        """Thu hồi token phía provider. Không ném lỗi khi provider từ chối —
        xem hiện thực để biết vì sao."""


@dataclass(frozen=True, slots=True)
class ClientCredentials:
    """Danh tính của **app mình** ở phía provider, không phải của người dùng."""

    client_id: str
    client_secret: str

    def __repr__(self) -> str:
        return f"ClientCredentials(client_id={self.client_id!r})"


class ProviderCredentials(Protocol):
    """Tra ``client_id`` / ``client_secret`` của app mình ở từng provider."""

    def get(self, provider_id: str) -> ClientCredentials | None: ...


class RefreshLock(Protocol):
    """Độc quyền khi làm mới token của một connection."""

    def guard(self, connection_id: str) -> AbstractAsyncContextManager[None]:
        """Khối lệnh bên trong chỉ được chạy bởi một tiến trình tại một thời điểm."""


class OrganizationSettingsRepository(Protocol):
    """Chính sách kết nối ở cấp organization."""

    async def get_policy(self, organization_id: str) -> ConnectionPolicy | None:
        """``None`` nghĩa là org chưa cấu hình — nơi gọi áp dụng mặc định.

        Nhờ vậy org mới không cần tạo row, và không cần backfill khi thêm bảng.
        """

    async def set_policy(
        self, organization_id: str, *, policy: ConnectionPolicy, at: datetime
    ) -> None: ...


# ─────────────────────────────────────────────────────────────────────────────
# Cầu nối sang domain Tenant
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class WorkspaceContext:
    """Quyền của người gọi trên đúng workspace mà request đang nhắm tới."""

    user_id: str
    organization_id: str
    workspace_id: str
    #: `ws:admin` — quản lý được kết nối **dùng chung**. Không đụng tới kết nối
    #: riêng của người khác; cái đó do chính chủ quản lý.
    can_manage_connections: bool
    #: `org:owner` hoặc `org:admin` — thu hồi được kết nối riêng của người khác
    #: (chỉ thu hồi, không xem chi tiết, không dùng).
    is_org_admin: bool = False
    #: `org:owner` — đổi được chính sách kết nối của organization.
    is_org_owner: bool = False
    #: Vai trò đã mã hoá thành chuỗi (``org:owner``, ``ws:admin``)
    roles: frozenset[str] = frozenset()


class TenantGuard(Protocol):
    async def require_workspace_context(
        self, *, user_id: str, organization_id: str | None, workspace_id: str | None
    ) -> WorkspaceContext:
        """Phân giải tenant của request và xác minh người gọi có quyền vào.

        ``None`` nghĩa là client không chỉ định — hiện thực chọn organization và
        workspace mặc định của user, đúng như router của Tenant đang làm.
        """


TenantGuardFactory = Callable[[object], TenantGuard]
