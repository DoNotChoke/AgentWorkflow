"""Router HTTP của Connection.

Router này chỉ dựng được ``ConnectionService``. ``CredentialResolver`` — thứ
duy nhất trả về token đã giải mã — cố ý không có dependency nào ở đây, nên
không endpoint nào chạm tới nó được (§2.4).

Rate limit đặt ở tầng này, cùng lý do với Identity: nó đếm theo người gọi và
theo IP, là mối quan tâm của tầng vận chuyển chứ không phải quy tắc nghiệp vụ.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Header, Path, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.context import RequestContext, current_context, set_context
from app.core.db import get_session
from app.core.errors import AppError
from app.core.http_auth import require_user_id, user_principal
from app.core.logging import get_logger
from app.core.redis import enforce_rate_limit, global_key
from app.domains.connection.application.ports import TenantGuard, WorkspaceContext
from app.domains.connection.application.service import ConnectionService
from app.domains.connection.domain.entities import (
    Connection,
    ConnectionPolicy,
    SharingPolicy,
)
from app.domains.connection.infrastructure.credential_store import build_credential_store
from app.domains.connection.infrastructure.oauth_client import HttpxOAuthClient
from app.domains.connection.infrastructure.oauth_state import RedisOAuthStateStore
from app.domains.connection.infrastructure.provider_credentials import (
    SettingsProviderCredentials,
    build_provider_credentials,
)
from app.domains.connection.infrastructure.repositories import (
    SqlAlchemyConnectionRepository,
    SqlAlchemyOrganizationSettingsRepository,
)

log = get_logger(__name__)

router = APIRouter(prefix="/v1/connections", tags=["connection"])

ORGANIZATION_HEADER = "x-organization-id"
WORKSPACE_HEADER = "x-workspace-id"

#: Khoá trong ``app.state`` chứa hàm dựng ``TenantGuard`` từ một session.
TENANT_GUARD_FACTORY = "tenant_guard_factory"

#: Bắt đầu một vòng uỷ quyền là thao tác rẻ với mình nhưng tốn với provider,
#: và mỗi lần lại ghi một khoá vào Redis. Giới hạn để một vòng lặp hỏng phía
#: client không thành một cuộc tấn công vào provider dưới tên app của mình.
_AUTHORIZE_ATTEMPTS_PER_USER = 20
_AUTHORIZE_WINDOW_SECONDS = 600


# ─── Dependency ──────────────────────────────────────────────────────────────


def get_tenant_guard(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TenantGuard:
    """Lấy hiện thực ``TenantGuard`` mà entrypoint đã gắn vào app.

    Đi vòng qua ``app.state`` thay vì import thẳng ``domains.tenant``: đó là
    contract 1 của import-linter. Cùng một hình với cách Identity lấy
    ``TenantProvisioner``.
    """
    factory = getattr(request.app.state, TENANT_GUARD_FACTORY, None)
    if factory is None:
        raise RuntimeError(
            "Chưa gắn tenant_guard_factory vào app.state. "
            "Xem app/entrypoints/http.py — mọi entrypoint phục vụ router này "
            "đều phải nối domain Tenant vào."
        )
    guard: TenantGuard = factory(session)
    return guard


def get_connection_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    tenant: Annotated[TenantGuard, Depends(get_tenant_guard)],
) -> ConnectionService:
    credentials = build_provider_credentials(settings)
    return ConnectionService(
        connections=SqlAlchemyConnectionRepository(session),
        credentials=build_credential_store(session, settings),
        states=RedisOAuthStateStore(),
        oauth=HttpxOAuthClient(credentials),
        provider_credentials=SettingsProviderCredentials(credentials),
        org_settings=SqlAlchemyOrganizationSettingsRepository(session),
        tenant=tenant,
        redirect_uri=settings.oauth_callback_url,
    )


async def resolve_workspace_context(
    user_id: Annotated[str, Depends(require_user_id)],
    guard: Annotated[TenantGuard, Depends(get_tenant_guard)],
    header_organization_id: Annotated[str | None, Header(alias=ORGANIZATION_HEADER)] = None,
    header_workspace_id: Annotated[str | None, Header(alias=WORKSPACE_HEADER)] = None,
) -> WorkspaceContext:
    """Phân giải tenant của request và ghi vào ``RequestContext``.

    Lặp lại vài dòng của ``tenant.infrastructure.http.resolve_tenant_context``
    là cái giá của contract 1 — import hàm đó từ đây sẽ phá ranh giới domain.
    Phần logic thật (kiểm membership, tính vai trò thực tế) không bị lặp: nó
    nằm sau ``TenantGuard`` và vẫn do domain Tenant thực hiện.
    """
    context = await guard.require_workspace_context(
        user_id=user_id,
        organization_id=header_organization_id,
        workspace_id=header_workspace_id,
    )

    ctx = current_context()
    if ctx is not None:
        set_context(
            RequestContext(
                request_id=ctx.request_id,
                correlation_id=ctx.correlation_id,
                tenant_id=context.organization_id,
                workspace_id=context.workspace_id,
                principal=user_principal(user_id, context.roles),
            )
        )
    return context


CurrentWorkspace = Annotated[WorkspaceContext, Depends(resolve_workspace_context)]


# ─── Schema vào/ra ───────────────────────────────────────────────────────────


class ProviderResponse(BaseModel):
    id: str
    label: str
    default_scopes: list[str]
    supports_refresh: bool
    #: ``false`` nghĩa là bản triển khai này chưa có client id/secret cho
    #: provider đó — nó hiện trong danh mục nhưng bấm kết nối sẽ báo lỗi.
    configured: bool
    notes: str


class ConnectionResponse(BaseModel):
    """Hình chiếu công khai của một Connection.

    Không có trường nào ở đây, và không bao giờ được có trường nào ở đây, mang
    giá trị credential.
    """

    id: str
    provider: str
    display_name: str
    status: str
    sharing: str
    owner_user_id: str
    granted_scopes: list[str]
    external_account_id: str | None
    external_account_label: str | None
    connected_by: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    last_error: str | None


class AuthorizationResponse(BaseModel):
    """Nơi client cần đưa trình duyệt tới, và vòng này sống được bao lâu."""

    authorize_url: str
    expires_in_seconds: int


class StartAuthorizationRequest(BaseModel):
    #: Mặc định PRIVATE — an toàn khi client quên gửi. Chọn WORKSPACE là hành
    #: động chia sẻ tài khoản cho cả workspace, nên nó phải tường minh.
    sharing: SharingPolicy = SharingPolicy.PRIVATE
    #: Bỏ trống thì dùng scope mặc định của provider.
    scopes: list[str] | None = Field(default=None, max_length=100)
    #: Uỷ quyền lại một kết nối đã tồn tại thay vì tạo bản ghi mới.
    reconnect_connection_id: str | None = Field(default=None, max_length=64)


class ConnectionAuditResponse(BaseModel):
    """Kết nối nhìn từ màn hình audit của quản trị viên organization.

    Cố ý **thiếu** ``display_name``, ``granted_scopes`` và danh tính tài khoản.
    Tên mặc định của một kết nối riêng có dạng "Google — an@gmail.com", nên trả
    nó ra là trả luôn địa chỉ tài khoản cá nhân — đúng thứ mà chế độ ẩn phải
    giấu. Quản trị viên cần biết *có* kết nối để thu hồi, không cần biết nó
    trỏ tới đâu.
    """

    id: str
    provider: str
    sharing: str
    owner_user_id: str
    status: str
    created_at: datetime


class ConnectionPolicyResponse(BaseModel):
    connection_policy: str
    #: Số kết nối riêng vừa bị tạm dừng do siết chính sách. 0 ở mọi trường hợp khác.
    disabled_private_connections: int = 0


class UpdateConnectionPolicyRequest(BaseModel):
    connection_policy: ConnectionPolicy


class RenameConnectionRequest(BaseModel):
    display_name: str = Field(max_length=200)


def _to_response(connection: Connection) -> ConnectionResponse:
    return ConnectionResponse(
        id=connection.id,
        provider=connection.provider,
        display_name=connection.display_name,
        status=str(connection.status),
        sharing=str(connection.sharing),
        owner_user_id=connection.owner_user_id,
        granted_scopes=sorted(connection.granted_scopes),
        external_account_id=connection.external_account_id,
        external_account_label=connection.external_account_label,
        connected_by=connection.connected_by,
        created_at=connection.created_at,
        updated_at=connection.updated_at,
        expires_at=connection.expires_at,
        last_error=connection.last_error,
    )


# ─── Danh mục provider ───────────────────────────────────────────────────────


@router.get("/providers")
async def list_providers(
    _user_id: Annotated[str, Depends(require_user_id)],
    service: Annotated[ConnectionService, Depends(get_connection_service)],
) -> list[ProviderResponse]:
    """Provider nào kết nối được trên bản triển khai này."""
    return [
        ProviderResponse(
            id=view.descriptor.id,
            label=view.descriptor.label,
            default_scopes=list(view.descriptor.default_scopes),
            supports_refresh=view.descriptor.supports_refresh,
            configured=view.configured,
            notes=view.descriptor.notes,
        )
        for view in service.list_providers()
    ]


# ─── Callback OAuth ──────────────────────────────────────────────────────────
#
# Khai báo **trước** các route có `{connection_id}`: FastAPI khớp theo thứ tự,
# nên đặt sau sẽ khiến "oauth" bị nuốt vào tham số đường dẫn.


@router.get("/oauth/callback", include_in_schema=False)
async def oauth_callback(
    service: Annotated[ConnectionService, Depends(get_connection_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    state: Annotated[str, Query(max_length=512)] = "",
    code: Annotated[str, Query(max_length=2048)] = "",
    error: Annotated[str | None, Query(max_length=200)] = None,
) -> RedirectResponse:
    """Provider gọi ngược lại đây sau khi người dùng bấm chấp thuận.

    Endpoint này **không có xác thực**, và không thể có: trình duyệt tới đây
    sau một lần chuyển hướng từ tên miền của provider, không mang theo access
    token nào. Thứ thay thế xác thực là ``state`` — ngẫu nhiên 32 byte, chỉ đọc
    được một lần, và chính nó là nơi chứa tenant. Không có tham số nào của
    request này được phép ảnh hưởng tới việc kết nối rơi vào tenant nào.

    Trả về chuyển hướng chứ không phải JSON, vì đầu bên kia là một trình duyệt
    đang chờ quay lại giao diện. Đích đến lấy từ cấu hình, không từ request.
    """
    if error:
        # Người dùng bấm "Cancel", hoặc provider từ chối. Không phải lỗi hệ thống.
        return _back_to_app(settings, {"error": "access_denied"})

    if not state or not code:
        return _back_to_app(settings, {"error": "invalid_callback"})

    try:
        connection = await service.complete_authorization(state=state, code=code)
    except AppError as exc:
        # Người dùng đang ở trong trình duyệt: một body JSON ở đây là ngõ cụt.
        # Chỉ trả về phân loại lỗi, không trả thông điệp — thông điệp có thể
        # mang chi tiết của provider, và query string thì đi thẳng vào log.
        log.warning(
            "connection_callback_failed",
            error_category=str(exc.category),
        )
        return _back_to_app(settings, {"error": str(exc.category)})

    return _back_to_app(settings, {"connection_id": connection.id, "status": "connected"})


def _back_to_app(settings: Settings, params: dict[str, str]) -> RedirectResponse:
    target = settings.oauth_post_connect_redirect_url
    separator = "&" if "?" in target else "?"
    return RedirectResponse(
        url=f"{target}{separator}{urlencode(params)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ─── Kết nối ─────────────────────────────────────────────────────────────────


@router.get("")
async def list_connections(
    context: CurrentWorkspace,
    service: Annotated[ConnectionService, Depends(get_connection_service)],
) -> list[ConnectionResponse]:
    connections = await service.list_connections(context)
    return [_to_response(c) for c in connections]


@router.post("/{provider}/authorize")
async def start_authorization(
    provider: Annotated[str, Path(max_length=64)],
    payload: StartAuthorizationRequest,
    context: CurrentWorkspace,
    service: Annotated[ConnectionService, Depends(get_connection_service)],
) -> AuthorizationResponse:
    """Bước một: dựng URL để đưa người dùng sang trang chấp thuận của provider."""
    await enforce_rate_limit(
        global_key("connection-authorize", context.user_id),
        limit=_AUTHORIZE_ATTEMPTS_PER_USER,
        window_seconds=_AUTHORIZE_WINDOW_SECONDS,
        message="Bạn đã thử kết nối quá nhiều lần. Vui lòng thử lại sau ít phút.",
    )

    authorization = await service.start_authorization(
        context=context,
        provider=provider,
        sharing=payload.sharing,
        scopes=frozenset(payload.scopes) if payload.scopes is not None else None,
        reconnect_connection_id=payload.reconnect_connection_id,
    )
    # `state` cố ý không nằm trong phản hồi: nó đã có sẵn trong `authorize_url`,
    # và trả thêm một bản nữa chỉ tạo thêm một chỗ để nó bị ghi vào log.
    return AuthorizationResponse(
        authorize_url=authorization.authorize_url,
        expires_in_seconds=authorization.expires_in_seconds,
    )


@router.get("/audit")
async def list_connections_for_audit(
    context: CurrentWorkspace,
    service: Annotated[ConnectionService, Depends(get_connection_service)],
) -> list[ConnectionAuditResponse]:
    """Mọi kết nối trong workspace — chỉ quản trị viên organization.

    Tồn tại vì bài toán nhân viên nghỉ việc: người duy nhất có quyền quản lý một
    kết nối riêng là chính chủ, mà chính chủ thì có thể không còn đăng nhập được
    nữa. Không có màn hình này thì quản trị viên không biết còn gì cần thu hồi.

    Chỉ trả **sự tồn tại**, không trả chi tiết — xem ``ConnectionAuditResponse``.
    """
    connections = await service.list_for_audit(context)
    return [
        ConnectionAuditResponse(
            id=c.id,
            provider=c.provider,
            sharing=str(c.sharing),
            owner_user_id=c.owner_user_id,
            status=str(c.status),
            created_at=c.created_at,
        )
        for c in connections
    ]


@router.get("/settings")
async def get_connection_policy(
    context: CurrentWorkspace,
    service: Annotated[ConnectionService, Depends(get_connection_service)],
) -> ConnectionPolicyResponse:
    """Chính sách kết nối hiện tại của organization. Mọi thành viên đọc được —
    họ cần biết mình có được thêm tài khoản riêng hay không."""
    return ConnectionPolicyResponse(connection_policy=str(await service.get_policy(context)))


@router.patch("/settings")
async def update_connection_policy(
    payload: UpdateConnectionPolicyRequest,
    context: CurrentWorkspace,
    service: Annotated[ConnectionService, Depends(get_connection_service)],
) -> ConnectionPolicyResponse:
    """Đổi chính sách kết nối. Chỉ chủ sở hữu organization.

    Siết sang ``managed`` sẽ tạm dừng mọi kết nối riêng đang sống trong org —
    số lượng trả về ở ``disabled_private_connections`` để giao diện nói được
    hậu quả thay vì để người dùng tự phát hiện.
    """
    policy, disabled = await service.set_policy(context=context, policy=payload.connection_policy)
    return ConnectionPolicyResponse(
        connection_policy=str(policy), disabled_private_connections=disabled
    )


@router.get("/{connection_id}")
async def get_connection(
    connection_id: Annotated[str, Path(max_length=64)],
    context: CurrentWorkspace,
    service: Annotated[ConnectionService, Depends(get_connection_service)],
) -> ConnectionResponse:
    return _to_response(await service.get_connection(connection_id, context))


@router.patch("/{connection_id}")
async def rename_connection(
    connection_id: Annotated[str, Path(max_length=64)],
    payload: RenameConnectionRequest,
    context: CurrentWorkspace,
    service: Annotated[ConnectionService, Depends(get_connection_service)],
) -> ConnectionResponse:
    connection = await service.rename(
        connection_id=connection_id, context=context, display_name=payload.display_name
    )
    return _to_response(connection)


@router.post("/{connection_id}/disable")
async def disable_connection(
    connection_id: Annotated[str, Path(max_length=64)],
    context: CurrentWorkspace,
    service: Annotated[ConnectionService, Depends(get_connection_service)],
) -> ConnectionResponse:
    """Tạm dừng. Credential giữ nguyên nên bật lại không cần uỷ quyền lại."""
    return _to_response(await service.disable(connection_id=connection_id, context=context))


@router.post("/{connection_id}/enable")
async def enable_connection(
    connection_id: Annotated[str, Path(max_length=64)],
    context: CurrentWorkspace,
    service: Annotated[ConnectionService, Depends(get_connection_service)],
) -> ConnectionResponse:
    return _to_response(await service.enable(connection_id=connection_id, context=context))


@router.delete("/{connection_id}")
async def revoke_connection(
    connection_id: Annotated[str, Path(max_length=64)],
    context: CurrentWorkspace,
    service: Annotated[ConnectionService, Depends(get_connection_service)],
) -> ConnectionResponse:
    """Thu hồi hẳn: xoá credential, giữ bản ghi lại làm lịch sử.

    Trả về bản ghi thay vì 204 chính vì lẽ đó — kết nối vẫn còn đó, ở trạng
    thái ``revoked``, và giao diện cần hiển thị đúng như vậy.
    """
    return _to_response(await service.revoke(connection_id=connection_id, context=context))
