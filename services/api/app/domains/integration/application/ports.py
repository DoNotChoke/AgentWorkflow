"""Những gì Integration Plane cần từ bên ngoài, khai bằng Protocol."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

from app.core.errors import ErrorCategory
from app.domains.integration.domain.connectors import ConnectorSpec
from app.domains.integration.domain.idempotency import ClaimResult, IdempotencyClaim
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
    """`claim` giành quyền chạy; `complete`/`fail` chốt kết quả."""

    async def claim(self, claim: IdempotencyClaim) -> ClaimResult: ...

    async def complete(
        self,
        *,
        tenant_id: str,
        key: str,
        result: Mapping[str, Any],
        provider_reference: str | None,
        attempts: int,
        at: datetime,
    ) -> None: ...

    async def fail(
        self,
        *,
        tenant_id: str,
        key: str,
        category: ErrorCategory,
        attempts: int,
        at: datetime,
        detail: str = "",
    ) -> None: ...


class ToolInvocationSink(Protocol):
    """Nơi Execution Trace sẽ được ghi"""

    async def record(self, invocation: ToolInvocationRecord) -> None: ...


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
