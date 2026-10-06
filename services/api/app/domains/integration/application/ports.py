"""Những gì Integration Plane cần từ bên ngoài, khai bằng Protocol."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from app.core.errors import ErrorCategory
from app.domains.integration.domain.connectors import ConnectorSpec
from app.domains.integration.domain.idempotency import (
    ClaimResult,
    IdempotencyClaim,
    IdempotencyRecord,
    InvocationStatus,
)
from app.domains.integration.domain.invocation import ToolInvocationRecord, ToolUsage
from app.domains.integration.domain.policy import OrgToolRule, QuotaVerdict
from app.domains.integration.domain.tools import ToolSpec

# ─── Credential (§2.4, ADR-0002) ─────────────────────────────────────────────


class ProviderCredential(Protocol):
    """Token đã giải mã, ở dạng hẹp nhất mà connector cần."""

    @property
    def connection_id(self) -> str: ...

    @property
    def provider(self) -> str: ...

    @property
    def access_token(self) -> str: ...

    @property
    def expires_at(self) -> datetime | None: ...


# ─── Connector ───────────────────────────────────────────────────────────────


class ConnectorResponse(Protocol):
    """Kết quả một lời gọi provider đã được chuẩn hoá."""

    @property
    def output(self) -> Mapping[str, Any]: ...

    @property
    def provider_reference(self) -> str | None: ...

    @property
    def retry_after_seconds(self) -> float | None: ...


class Connector(Protocol):
    """Thứ duy nhất trong hệ thống thực sự chạm tới mạng của provider."""

    async def call(
        self,
        *,
        tool: ToolSpec,
        connector: ConnectorSpec,
        credential: ProviderCredential,
        payload: Mapping[str, Any],
    ) -> ConnectorResponse:
        """Gọi provider, hoặc ném ra một `AppError` đã chuẩn hoá."""
        ...


# ─── Chính sách: ba nguồn dữ liệu bị hoãn ────────────────────────────────────
#
# Hiện thực dễ dãi cho Phase 2 nằm ở `infrastructure/policy_defaults.py`.
# Phase 4 và Phase 7 thay chúng trong `wiring.py`; `PolicyEngine` và Gateway
# không đổi một dòng nào.


class OrgPolicyReader(Protocol):
    """Luật riêng của organization cho một tool."""

    async def tool_rule(self, *, tenant_id: str, tool_id: str) -> OrgToolRule | None: ...


class QuotaChecker(Protocol):
    """Hạn mức usage"""

    async def check(self, *, tenant_id: str, tool_id: str) -> QuotaVerdict: ...


class PlanReader(Protocol):
    """Tool bị gói cước chặn"""

    async def restricted_tools(self, *, tenant_id: str) -> frozenset[str]: ...


# ─── Hạ tầng mà Gateway điều phối ────────────────────────────────────────────


class RateLimiter(Protocol):
    async def acquire(self, *, target: Any, tool: ToolSpec, connector: ConnectorSpec) -> None: ...


class CircuitBreaker(Protocol):
    async def check(
        self, *, connector: ConnectorSpec, tenant_id: str, connection_id: str
    ) -> None: ...

    async def record_success(
        self, *, connector: ConnectorSpec, tenant_id: str, connection_id: str
    ) -> None: ...

    async def record_failure(
        self,
        *,
        connector: ConnectorSpec,
        tenant_id: str,
        connection_id: str,
        category: ErrorCategory,
    ) -> None: ...


class IdempotencyStore(Protocol):
    """`claim` giành quyền chạy; `complete`/`fail`/`mark_unknown` chốt kết quả.

    Mỗi thao tác tự commit, độc lập với transaction của request. Các lần chốt là
    compare-and-set theo `claim_token`: trả `False` khi người khác đã đổi hàng.
    """

    async def claim(self, claim: IdempotencyClaim) -> ClaimResult: ...

    async def get(self, *, tenant_id: str, key: str) -> IdempotencyRecord | None: ...

    async def take_over(
        self,
        *,
        tenant_id: str,
        key: str,
        expected: InvocationStatus,
        claim_token: str,
        lease_expires_at: datetime,
        at: datetime,
    ) -> IdempotencyRecord | None: ...

    async def abandon_stale(self, *, tenant_id: str, key: str, at: datetime) -> bool: ...

    async def complete(
        self,
        *,
        tenant_id: str,
        key: str,
        claim_token: str,
        result: Mapping[str, Any],
        provider_reference: str | None,
        attempts: int,
        at: datetime,
    ) -> bool: ...

    async def fail(
        self,
        *,
        tenant_id: str,
        key: str,
        claim_token: str,
        category: ErrorCategory,
        attempts: int,
        at: datetime,
        detail: str = "",
    ) -> bool: ...

    async def mark_unknown(
        self,
        *,
        tenant_id: str,
        key: str,
        claim_token: str,
        category: ErrorCategory,
        attempts: int,
        at: datetime,
        detail: str = "",
    ) -> bool: ...


class ToolInvocationSink(Protocol):
    """Nơi Execution Trace sẽ được ghi"""

    async def record(
        self,
        invocation: ToolInvocationRecord,
        *,
        input_payload: Mapping[str, Any],
        output_payload: Mapping[str, Any],
    ) -> None:
        """Payload đi cạnh bản ghi, không nằm trong nó."""


class UsageRecorder(Protocol):
    """Ghi lại Usage"""

    async def record(self, usage: ToolUsage) -> None: ...


class CredentialProvider(Protocol):
    """Cửa duy nhất Integration Plane lấy được token đã giải mã."""

    async def resolve(
        self,
        *,
        connection_id: str,
        tenant_id: str,
        workspace_id: str,
        acting_user_id: str,
        required_scopes: frozenset[str] = frozenset(),
    ) -> ProviderCredential: ...

    async def report_unusable(self, *, connection_id: str, tenant_id: str, reason: str) -> None:
        """Provider vừa từ chối token này."""
        ...


# ─── Tenant ──────────────────────────────────────────────────────────────────


class InvocationContext(Protocol):
    """Tenant của lời gọi, đã phân giải từ token và membership (§19).

    Hẹp hơn `WorkspaceContext` của Connection có chủ đích: Integration Plane chỉ
    cần bốn trường này. Protocol là structural typing, nên cùng một adapter
    trong `wiring.py` thoả được cả hai mà hai domain không phải biết nhau.
    """

    @property
    def user_id(self) -> str: ...

    @property
    def organization_id(self) -> str: ...

    @property
    def workspace_id(self) -> str: ...

    @property
    def roles(self) -> frozenset[str]: ...


@dataclass(frozen=True, slots=True)
class RunIdentity:
    """Một lần chạy workflow, đủ để Gateway biết nó gọi tool thay ai và bằng connection nào."""

    execution_id: str
    tenant_id: str
    workspace_id: str
    #: Người khởi chạy. Connection riêng chỉ dùng được khi nó là của người này.
    initiated_by: str
    is_running: bool
    #: Node tool → connection đã chọn lúc tạo lần chạy. Bất biến.
    connections: Mapping[str, str]


class RunLookup(Protocol):
    """Đọc bản ghi lần chạy theo id trong token. Hiện thực ở `entrypoints`: bản ghi thuộc
    domain `execution`."""

    async def find(self, execution_id: str) -> RunIdentity | None: ...


class TenantGuard(Protocol):
    async def require_workspace_context(
        self, *, user_id: str, organization_id: str | None, workspace_id: str | None
    ) -> InvocationContext: ...
