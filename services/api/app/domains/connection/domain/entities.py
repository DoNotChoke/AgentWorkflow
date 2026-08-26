"""Thực thể và quy tắc nghiệp vụ của Connection."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.core.errors import (
    AuthorizationError,
    ConnectionExpiredError,
    MissingScopeError,
    ValidationError,
)


class ConnectionStatus(StrEnum):
    """
    ACTIVE             — dùng được ngay.
    EXPIRED            — credential hết hạn và không tự làm mới được.
    REVOKED            — đã thu hồi. Trạng thái kết, credential đã bị xoá.
    MISSING_SCOPE      — còn sống nhưng thiếu quyền cho thao tác đang cần.
    RECONNECT_REQUIRED — provider từ chối vĩnh viễn, phải uỷ quyền lại.
    DISABLED           — người dùng tự tạm dừng. Bật lại được, không mất token.
    """

    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    MISSING_SCOPE = "missing_scope"
    RECONNECT_REQUIRED = "reconnect_required"
    DISABLED = "disabled"


#: Trạng thái kết. Không có đường quay lại — muốn dùng lại thì tạo connection mới.
TERMINAL: frozenset[ConnectionStatus] = frozenset({ConnectionStatus.REVOKED})


class SharingPolicy(StrEnum):
    """Phạm vi dùng của một kết nối.

    WORKSPACE — tài khoản chung của tổ chức. Cả workspace nhìn thấy và dùng
                được. Chỉ quản trị viên workspace tạo và quản lý.
    PRIVATE   — tài khoản riêng của một người. Người khác **không nhìn thấy**
                và không dùng được. Chính chủ quản lý, không phải ws:admin.
    """

    WORKSPACE = "workspace"
    PRIVATE = "private"


class ConnectionPolicy(StrEnum):
    """Org cho phép những loại kết nối nào.

    MANAGED — chỉ tài khoản chung. Dùng cho tổ chức muốn mọi luồng dữ liệu ra
              ngoài đều đi qua tài khoản do quản trị viên kiểm soát.
    OPEN    — cho phép thành viên tự thêm tài khoản riêng của họ.

    Đây là trục **giới hạn**, không phải trục nới lỏng: nó chỉ đóng/mở cánh cửa
    PRIVATE. Kết nối WORKSPACE luôn cần ws:admin ở cả hai chế độ.
    """

    MANAGED = "managed"
    OPEN = "open"


DEFAULT_CONNECTION_POLICY = ConnectionPolicy.OPEN


@dataclass(frozen=True, slots=True)
class ConnectionActor:
    """Người đang thao tác, ở dạng mà tầng domain hiểu được."""

    user_id: str
    can_manage_workspace: bool
    is_org_admin: bool
    is_org_owner: bool


# ─────────────────────────────────────────────────────────────────────────────
# Thực thể
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Connection:
    """Metadata của một kết nối. Cố ý **không** có trường nào chứa token."""

    id: str
    organization_id: str
    workspace_id: str
    provider: str
    display_name: str
    status: ConnectionStatus
    granted_scopes: frozenset[str]
    connected_by: str
    created_at: datetime
    updated_at: datetime
    #: Phạm vi dùng. Quyết định ai nhìn thấy và ai gọi được kết nối này.
    sharing: SharingPolicy = SharingPolicy.WORKSPACE
    owner_user_id: str = ""
    #: Danh tính tài khoản phía provider — team Slack, tài khoản Google…
    external_account_id: str | None = None
    external_account_label: str | None = None
    #: Hạn của access token. ``None`` với provider phát token không hết hạn.
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    #: Lý do lần hỏng gần nhất, để UI nói được người dùng cần làm gì.
    #: Chỉ chứa mã lỗi đã chuẩn hoá, không bao giờ chứa nội dung phản hồi thô.
    last_error: str | None = None

    def missing_scopes(self, required: Iterable[str]) -> frozenset[str]:
        return frozenset(required) - self.granted_scopes


@dataclass(frozen=True, slots=True)
class OAuthTokens:
    """Token vừa nhận từ provider. **Chỉ tồn tại trong bộ nhớ.**"""

    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    scopes: frozenset[str] = frozenset()
    token_type: str = "Bearer"  # noqa: S105 — nhãn RFC 6750, không phải bí mật

    def __repr__(self) -> str:
        return (
            f"OAuthTokens(token_type={self.token_type!r}, "
            f"has_refresh_token={self.refresh_token is not None}, "
            f"expires_at={self.expires_at!r})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Scope
# ─────────────────────────────────────────────────────────────────────────────

MAX_SCOPE_COUNT = 100
MAX_DISPLAY_NAME_LENGTH = 200


def parse_scopes(raw: str | None, *, delimiter: str = " ") -> frozenset[str]:
    """Tách chuỗi scope của provider thành tập hợp."""
    if not raw:
        return frozenset()
    parts = (part.strip() for part in raw.replace(delimiter, " ").split())
    return frozenset(part for part in parts if part)


def format_scopes(scopes: Iterable[str], *, delimiter: str = " ") -> str:
    """Ghép scope lại theo đúng quy ước của provider. Sắp xếp để ổn định."""
    return delimiter.join(sorted(set(scopes)))


def _is_scope_token(scope: str) -> bool:
    """Ký tự hợp lệ của một scope token, theo RFC 6749 §3.3.

    scope-token = 1*( %x21 / %x23-5B / %x5D-7E )
    """
    return all(0x21 <= ord(ch) <= 0x7E and ch not in '"\\' for ch in scope)


def ensure_scopes_requestable(scopes: Iterable[str]) -> frozenset[str]:
    """Kiểm tra tập scope mà người dùng xin cấp trước khi dựng URL uỷ quyền."""
    requested = frozenset(s.strip() for s in scopes if s.strip())
    if not requested:
        raise ValidationError("Phải xin ít nhất một quyền.")
    if len(requested) > MAX_SCOPE_COUNT:
        raise ValidationError(f"Không xin quá {MAX_SCOPE_COUNT} quyền trong một lần kết nối.")
    for scope in requested:
        if not _is_scope_token(scope):
            raise ValidationError(f"Quyền {scope!r} chứa ký tự không hợp lệ.")
    return requested


def normalize_display_name(raw: str, *, fallback: str) -> str:
    name = " ".join(raw.split())
    if not name:
        return fallback
    if len(name) > MAX_DISPLAY_NAME_LENGTH:
        raise ValidationError(f"Tên kết nối không được dài quá {MAX_DISPLAY_NAME_LENGTH} ký tự.")
    return name


def default_display_name(*, provider_label: str, account_label: str | None) -> str:
    """Tên mặc định: "Slack — Acme Corp"."""
    return f"{provider_label} — {account_label}" if account_label else provider_label


# ─────────────────────────────────────────────────────────────────────────────
# Hạn của access token
# ─────────────────────────────────────────────────────────────────────────────

#: Làm mới token sớm hơn hạn thật một khoảng.
REFRESH_SKEW = timedelta(seconds=60)


def needs_refresh(connection: Connection, *, now: datetime) -> bool:
    """Access token sắp hết hạn tới mức không nên dùng cho request tiếp theo."""
    if connection.expires_at is None:
        return False
    return connection.expires_at - REFRESH_SKEW <= now


# ─────────────────────────────────────────────────────────────────────────────
# Chuyển trạng thái
# ─────────────────────────────────────────────────────────────────────────────


def ensure_usable(connection: Connection, *, required_scopes: Iterable[str] = ()) -> None:
    """Cổng duy nhất trước khi một credential được đem đi dùng."""
    if connection.status is not ConnectionStatus.ACTIVE:
        raise ConnectionExpiredError(
            _unusable_message(connection.status),
            details={"connection_id": connection.id, "status": str(connection.status)},
        )

    missing = connection.missing_scopes(required_scopes)
    if missing:
        raise MissingScopeError(
            "Kết nối chưa được cấp đủ quyền cho thao tác này. Hãy kết nối lại "
            "và chấp thuận các quyền còn thiếu.",
            details={"connection_id": connection.id, "missing_scopes": sorted(missing)},
        )


def _unusable_message(status: ConnectionStatus) -> str:
    return {
        ConnectionStatus.EXPIRED: "Kết nối đã hết hạn. Hãy kết nối lại.",
        ConnectionStatus.REVOKED: "Kết nối đã bị thu hồi.",
        ConnectionStatus.MISSING_SCOPE: "Kết nối chưa được cấp đủ quyền. Hãy kết nối lại.",
        ConnectionStatus.RECONNECT_REQUIRED: ("Provider đã từ chối kết nối này. Hãy kết nối lại."),
        ConnectionStatus.DISABLED: "Kết nối đang tạm dừng. Hãy bật lại trước khi dùng.",
    }.get(status, "Kết nối không dùng được.")


def ensure_not_terminal(connection: Connection) -> None:
    """Chặn mọi thao tác lên một connection đã ở trạng thái kết."""
    if connection.status in TERMINAL:
        raise ValidationError(
            "Kết nối này đã bị thu hồi và không thao tác được nữa. Hãy tạo kết nối mới."
        )


def ensure_can_disable(connection: Connection) -> None:
    ensure_not_terminal(connection)
    if connection.status is ConnectionStatus.DISABLED:
        raise ValidationError("Kết nối đang tạm dừng rồi.")


def ensure_can_enable(connection: Connection) -> None:
    """Bật lại chỉ có nghĩa với connection do chính người dùng tạm dừng."""
    ensure_not_terminal(connection)
    if connection.status is not ConnectionStatus.DISABLED:
        raise ValidationError(
            "Chỉ bật lại được kết nối đang tạm dừng. Kết nối này cần được kết nối lại."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Quyền sở hữu và khả năng nhìn thấy
#
# Bốn hàm dưới đây là toàn bộ luật phân quyền của Connection. Chúng thuần khiết
# và không chạm I/O, nên chúng là chỗ đọc được câu trả lời cho "ai làm được gì"
# mà không phải lần theo tầng application.
# ─────────────────────────────────────────────────────────────────────────────


def visible_to(connection: Connection, *, user_id: str) -> bool:
    """Kết nối này có hiện ra trong danh sách của ``user_id`` không."""
    if connection.sharing is SharingPolicy.WORKSPACE:
        return True
    return connection.owner_user_id == user_id


def ensure_can_create(
    *,
    sharing: SharingPolicy,
    policy: ConnectionPolicy,
    actor: ConnectionActor,
) -> None:
    """Ai được tạo kết nối loại nào, dưới chính sách nào của org."""
    if sharing is SharingPolicy.PRIVATE:
        if policy is ConnectionPolicy.MANAGED:
            raise ValidationError(
                "Organization này chỉ cho phép dùng tài khoản chung do quản trị "
                "viên thiết lập. Hãy liên hệ quản trị viên để được thêm kết nối.",
                details={"connection_policy": str(policy)},
            )
        # Không cần quyền gì thêm: kết nối riêng chỉ chính chủ dùng được
        return

    if not actor.can_manage_workspace:
        raise AuthorizationError(
            "Chỉ quản trị viên workspace mới thêm được tài khoản dùng chung. "
            "Bạn vẫn có thể thêm tài khoản riêng của mình."
            if policy is ConnectionPolicy.OPEN
            else "Chỉ quản trị viên workspace mới thêm được kết nối."
        )


def ensure_can_manage(connection: Connection, *, actor: ConnectionActor) -> None:
    """Đổi tên, tạm dừng, bật lại."""
    if connection.sharing is SharingPolicy.WORKSPACE:
        if not actor.can_manage_workspace:
            raise AuthorizationError(
                "Bạn không có quyền quản lý kết nối dùng chung của workspace này."
            )
        return

    if connection.owner_user_id != actor.user_id:
        raise AuthorizationError("Bạn không có quyền quản lý kết nối này.")


def ensure_can_revoke(connection: Connection, *, actor: ConnectionActor) -> None:
    """Thu hồi."""
    if connection.sharing is SharingPolicy.PRIVATE and actor.is_org_admin:
        return
    ensure_can_manage(connection, actor=actor)


def ensure_can_change_policy(actor: ConnectionActor) -> None:
    """Đổi chính sách kết nối của org — chỉ OWNER."""
    if not actor.is_org_owner:
        raise AuthorizationError("Chỉ chủ sở hữu organization mới đổi được chính sách kết nối.")
