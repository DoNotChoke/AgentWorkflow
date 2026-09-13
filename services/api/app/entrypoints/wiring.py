"""Chỗ nối các domain lại với nhau.

Contract 1 của import-linter cấm domain import chéo nhau, nên mọi lần một domain
cần dữ liệu của domain khác đều đi theo cùng một hình: bên cần khai một Protocol
ở tầng `application` của mình, bên có hiện thực hoá, và **module này** là nơi
duy nhất biết cả hai để buộc dây.

Adapter phải nằm ở đây chứ không nằm trong domain nào: đặt nó vào Identity thì
Identity phải import kiểu dữ liệu của Tenant, đặt vào Tenant thì ngược lại — cả
hai đều phá contract 1.

Module này cũng không được import thẳng tầng `domain` của bất kỳ ai (contract 5).
Vì thế nó chỉ đọc thuộc tính của entity Identity trả về chứ không chú kiểu cho
chúng — đọc `user.email` không cần biết `User` là gì.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AuthorizationError
from app.domains.connection.application.ports import TenantGuard, WorkspaceContext
from app.domains.connection.application.service import CredentialResolver, ResolvedCredential
from app.domains.connection.infrastructure.credential_store import build_credential_store
from app.domains.connection.infrastructure.oauth_client import HttpxOAuthClient
from app.domains.connection.infrastructure.oauth_state import RedisRefreshLock
from app.domains.connection.infrastructure.provider_credentials import (
    build_provider_credentials,
)
from app.domains.connection.infrastructure.repositories import (
    SqlAlchemyConnectionRepository,
)
from app.domains.identity.infrastructure.repositories import SqlAlchemyUserRepository
from app.domains.tenant.application.ports import UserDirectory, UserProfile
from app.domains.tenant.application.service import TenantService


def _to_profile(user: object) -> UserProfile:
    """Chuyển một ``User`` của Identity sang DTO mà Tenant đặt hàng.

    Tham số để kiểu ``object`` là có chủ đích: chú đúng kiểu ``User`` sẽ phải
    import ``identity.domain``, mà contract 5 cấm entrypoint làm thế. Đọc thuộc
    tính thì không cần biết kiểu.
    """
    return UserProfile(
        user_id=user.id,  # type: ignore[attr-defined]
        email=user.email,  # type: ignore[attr-defined]
        display_name=user.display_name,  # type: ignore[attr-defined]
    )


class IdentityUserDirectory:
    """Hiện thực ``UserDirectory`` của Tenant bằng dữ liệu của Identity."""

    def __init__(self, users: SqlAlchemyUserRepository) -> None:
        self._users = users

    async def get_profiles(self, user_ids: Sequence[str]) -> dict[str, UserProfile]:
        found = await self._users.list_by_ids(user_ids)
        return {user.id: _to_profile(user) for user in found}

    async def find_by_email(self, email: str) -> UserProfile | None:
        # Identity lưu email đã `strip().lower()`, nên tra cũng phải cùng dạng.
        # Đây là chỗ đúng để làm việc đó: module này biết dữ liệu Identity nằm ở
        # hình dạng nào, còn Tenant thì không — và cũng không được phép biết.
        #
        # Không tái dùng `normalize_email` của Identity vì nó nằm ở tầng `domain`
        # mà entrypoint không được import thẳng (contract 5). Ở đây chỉ cần khớp
        # khoá tra cứu, không cần kiểm định dạng — email sai cú pháp đơn giản là
        # không khớp ai cả.
        found = await self._users.get_by_email(email.strip().lower())
        return None if found is None else _to_profile(found)


def build_user_directory(session: AsyncSession) -> UserDirectory:
    """Dựng ``UserDirectory`` từ một session, để gắn vào ``app.state``."""
    return IdentityUserDirectory(SqlAlchemyUserRepository(session))


# ─────────────────────────────────────────────────────────────────────────────
# Connection ← Tenant
# ─────────────────────────────────────────────────────────────────────────────

#: Vai trò workspace được phép quản lý kết nối, ở dạng chuỗi đã mã hoá.
#:
#: So bằng chuỗi chứ không bằng enum ``WorkspaceRole`` là có chủ đích: contract
#: 5 cấm entrypoint import thẳng tầng `domain` của một domain. Chính vì thế mà
#: Tenant đã chọn mã hoá vai trò thành chuỗi (`org:owner`, `ws:admin`) cho các
#: domain khác dùng — xem `core/http_auth.user_principal`. Đây dùng lại đúng
#: cách mã hoá đó, không phát minh cách thứ hai.
_WORKSPACE_MANAGER_ROLE = "ws:admin"

#: Thu hồi được kết nối riêng của người khác — cần cho việc dọn dẹp khi có
#: người rời tổ chức.
_ORG_ADMIN_ROLES = frozenset({"org:owner", "org:admin"})

#: Đổi được chính sách kết nối. Chặt hơn `_ORG_ADMIN_ROLES` một bậc: đây là
#: thiết lập quyết định dữ liệu công ty có được đi ra qua tài khoản cá nhân hay
#: không, nên nó theo quy tắc "chỉ OWNER" giống việc cấp quyền OWNER.
_ORG_OWNER_ROLE = "org:owner"


def _encode_roles(access: object) -> frozenset[str]:
    """Mã hoá vai trò của một ``TenantAccess`` thành chuỗi.

    Giữ đồng bộ với ``tenant.infrastructure.http._encode_roles``. Hai bản này
    không dùng chung được: một bản nằm trong domain Tenant, bản kia ở entrypoint
    và không được import tầng `domain`.
    """
    roles = {f"org:{access.org_role}"}  # type: ignore[attr-defined]
    workspace_role = access.workspace_role  # type: ignore[attr-defined]
    if workspace_role is not None:
        roles.add(f"ws:{workspace_role}")
    return frozenset(roles)


class TenantServiceGuard:
    """Hiện thực ``TenantGuard`` của Connection bằng ``TenantService``."""

    def __init__(self, tenant: TenantService) -> None:
        self._tenant = tenant

    async def require_workspace_context(
        self, *, user_id: str, organization_id: str | None, workspace_id: str | None
    ) -> WorkspaceContext:
        resolved_org = organization_id or await self._default_organization(user_id)
        resolved_workspace = workspace_id or await self._default_workspace(user_id, resolved_org)

        access = await self._tenant.require_access(
            user_id=user_id,
            organization_id=resolved_org,
            workspace_id=resolved_workspace,
        )

        roles = _encode_roles(access)
        return WorkspaceContext(
            user_id=user_id,
            organization_id=resolved_org,
            workspace_id=resolved_workspace,
            can_manage_connections=_WORKSPACE_MANAGER_ROLE in roles,
            is_org_admin=bool(roles & _ORG_ADMIN_ROLES),
            is_org_owner=_ORG_OWNER_ROLE in roles,
            roles=roles,
        )

    async def _default_organization(self, user_id: str) -> str:
        access = await self._tenant.resolve_default_access(user_id)
        if access is None:
            raise AuthorizationError("Tài khoản này chưa thuộc organization nào.")
        return access.organization_id

    async def _default_workspace(self, user_id: str, organization_id: str) -> str:
        """Workspace mặc định khi client không chỉ định.

        ``list_workspaces`` đã lọc theo quyền và sắp theo ``created_at``, nên
        cái đầu tiên là workspace ``Default`` được cấp lúc tạo organization —
        đúng thứ người dùng mong đợi khi họ chưa chọn gì.
        """
        workspaces = await self._tenant.list_workspaces(
            actor_id=user_id, organization_id=organization_id
        )
        if not workspaces:
            raise AuthorizationError(
                "Bạn không có quyền truy cập workspace nào trong organization này."
            )
        return workspaces[0].id


def build_tenant_guard(session: AsyncSession) -> TenantGuard:
    """Dựng ``TenantGuard`` từ một session, để gắn vào ``app.state``."""
    from app.domains.tenant.infrastructure.http import build_tenant_service

    return TenantServiceGuard(build_tenant_service(session, build_user_directory(session)))


# ─────────────────────────────────────────────────────────────────────────────
# Integration ← Connection
#
# Đây là chỗ DUY NHẤT trong hệ thống dựng được `CredentialResolver`, và do đó
# là ranh giới credential thật — không phải `domains.integration.infrastructure`
# như ADR-0001 mô tả. Contract 1 cấm `integration` import `connection`; contract
# 4 liệt kê mọi domain chứ không liệt kê `app.entrypoints`. Xem ADR-0002.
#
# Token đi từ đây sang Integration Plane qua **tham số**, không qua `import`.
# ─────────────────────────────────────────────────────────────────────────────


class ConnectionCredentialProvider:
    """Hiện thực `CredentialProvider` của Integration bằng `CredentialResolver`.

    Dịch giữa hai kiểu cùng hình dạng ở hai domain không được biết nhau. Đó là
    cái giá cố định của contract 1, và đã trả đúng cái giá đó cho `TenantGuard`.
    """

    def __init__(self, resolver: CredentialResolver, settings: Settings) -> None:
        self._resolver = resolver
        self._settings = settings
        self._oauth = HttpxOAuthClient(build_provider_credentials(settings))

    def _credentials_for(self, session: AsyncSession) -> Any:
        return build_credential_store(session, self._settings)

    async def resolve(
        self,
        *,
        connection_id: str,
        tenant_id: str,
        workspace_id: str,
        acting_user_id: str,
        required_scopes: frozenset[str] = frozenset(),
    ) -> ResolvedCredential:
        return await self._resolver.resolve(
            connection_id=connection_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            acting_user_id=acting_user_id,
            required_scopes=required_scopes,
        )

    async def report_unusable(self, *, connection_id: str, tenant_id: str, reason: str) -> None:
        """Ghi trong một transaction RIÊNG, không phải transaction của request.

        Gateway chỉ gọi vào đây khi một lời gọi tool đã hỏng, và request đó sắp
        kết thúc bằng lỗi — `session_scope` sẽ rollback. Dùng chung session thì
        bản ghi "token này chết" bị cuốn theo, và kết nối nằm `active` vĩnh
        viễn dù provider đã nói ngược lại.

        Đây là báo cáo, không phải một phần công việc của request.
        """
        from app.core.db import session_scope

        async with session_scope() as session:
            await CredentialResolver(
                connections=SqlAlchemyConnectionRepository(session),
                credentials=self._credentials_for(session),
                oauth=self._oauth,
                locks=RedisRefreshLock(),
            ).report_unusable(connection_id=connection_id, tenant_id=tenant_id, reason=reason)


def build_credential_provider(
    session: AsyncSession, settings: Settings
) -> ConnectionCredentialProvider:
    """Dựng cổng credential, để gắn vào ``app.state``.

    `CredentialResolver` gom sáu gạch đầu dòng của §6.3 vào một lời gọi: phân
    giải kết nối, kiểm tenant, kiểm quyền nhìn thấy, nạp credential đã mã hoá,
    giải mã, làm mới token, kiểm scope. Gateway **không** được tự làm bất kỳ
    bước nào trong số đó.
    """
    credentials = build_provider_credentials(settings)
    return ConnectionCredentialProvider(
        CredentialResolver(
            connections=SqlAlchemyConnectionRepository(session),
            credentials=build_credential_store(session, settings),
            oauth=HttpxOAuthClient(credentials),
            locks=RedisRefreshLock(),
        ),
        settings,
    )


def build_integration_tenant_guard(session: AsyncSession) -> TenantGuard:
    """`TenantGuard` cho Integration Plane.

    Cùng một adapter với Connection: hai domain khai hai Protocol riêng, nhưng
    chúng cùng hình dạng và `Protocol` là structural typing, nên `TenantServiceGuard`
    thoả cả hai mà không domain nào phải biết domain kia tồn tại.
    """
    return build_tenant_guard(session)


# ─────────────────────────────────────────────────────────────────────────────
# Trace ← Integration
# ─────────────────────────────────────────────────────────────────────────────


class IntegrationTraceSink:
    """Hiện thực `ToolInvocationSink` của Integration bằng `TraceService`.

    Chỉ ghi khi lời gọi tool mang `execution_trace_id` trên `CallerRef` — do
    Agent Runtime gắn vào khi nó gọi Gateway qua HTTP (Phase 3, phần còn lại).
    Không có `execution_trace_id` thì rơi về `fallback`: hiện tại
    `/internal/v1/tools/{id}/invoke` còn gọi được trực tiếp không qua Agent
    Runtime, và khi đó không có trace nào đang mở để ghi vào — đó không phải
    một lỗi, chỉ là không có gì để đính kèm.

    Ghi trong một transaction **riêng** (`session_scope()`), không phải
    transaction của request: một lời gọi tool lỗi giữa chừng không được cuốn
    theo bước trace đã ghi trước đó, đúng lúc chúng cần nhất để biết vì sao
    nó hỏng.
    """

    def __init__(self, fallback: Any) -> None:
        self._fallback = fallback

    async def record(
        self,
        invocation: object,
        *,
        input_payload: Mapping[str, Any],
        output_payload: Mapping[str, Any],
    ) -> None:
        trace_id = invocation.execution_trace_id  # type: ignore[attr-defined]
        if trace_id is None:
            await self._fallback.record(
                invocation, input_payload=input_payload, output_payload=output_payload
            )
            return

        idempotency_key = invocation.idempotency_key  # type: ignore[attr-defined]

        attributes: dict[str, Any] = {
            "invocation_id": invocation.invocation_id,  # type: ignore[attr-defined]
            "tool_id": invocation.tool_id,  # type: ignore[attr-defined]
            "tool_version": invocation.tool_version,  # type: ignore[attr-defined]
            "provider": invocation.provider,  # type: ignore[attr-defined]
            "connection_id": invocation.connection_id,  # type: ignore[attr-defined]
            "attempt": invocation.attempt,  # type: ignore[attr-defined]
        }
        if idempotency_key:
            attributes["idempotency_key"] = idempotency_key

        from app.core.config import get_settings
        from app.core.db import session_scope
        from app.domains.trace.application.service import TraceService
        from app.domains.trace.infrastructure.payload_store import build_payload_store
        from app.domains.trace.infrastructure.repositories import (
            SqlAlchemyExecutionTraceRepository,
        )

        async with session_scope() as session:
            traces = TraceService(
                traces=SqlAlchemyExecutionTraceRepository(session),
                payloads=build_payload_store(get_settings()),
            )
            await traces.record_tool_call(
                tenant_id=invocation.tenant_id,  # type: ignore[attr-defined]
                trace_id=trace_id,
                status=invocation.status,  # type: ignore[attr-defined]
                started_at=invocation.started_at,  # type: ignore[attr-defined]
                completed_at=invocation.completed_at,  # type: ignore[attr-defined]
                parent_step_id=invocation.parent_step_id,  # type: ignore[attr-defined]
                input_payload=input_payload,
                output_payload=output_payload,
                attributes=attributes,
                error_category=invocation.error_category,  # type: ignore[attr-defined]
                otel_trace_id=invocation.otel_trace_id,  # type: ignore[attr-defined]
                otel_span_id=invocation.otel_span_id,  # type: ignore[attr-defined]
            )


def build_execution_trace_sink(_session: AsyncSession) -> IntegrationTraceSink:
    """Dựng sink thật của Integration Gateway, để gắn vào ``app.state``.

    Nhận `_session` chỉ để khớp chữ ký chung của mọi factory trong module này
    (`get_gateway` gọi `factory(session)` như với credential provider) — nhưng
    không dùng nó: `IntegrationTraceSink` tự mở transaction riêng cho mỗi lần
    ghi, xem docstring của nó.

    Usage (`LoggingUsageRecorder`) không đổi ở đây: sổ usage thật là domain
    `usage`, thuộc Phase 7 (§18) — gộp nó vào `trace` bây giờ sẽ lẫn quyền sở
    hữu dữ liệu giữa hai domain, đúng điều ADR-0001 quy tắc 3 cấm.
    """
    from app.domains.integration.infrastructure.sinks import LoggingToolInvocationSink

    return IntegrationTraceSink(fallback=LoggingToolInvocationSink())
