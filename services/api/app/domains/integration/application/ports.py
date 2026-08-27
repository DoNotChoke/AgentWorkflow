"""Những gì Integration Plane cần từ bên ngoài, khai bằng Protocol.

Contract 1 cấm domain import chéo nhau, nên mọi thứ đến từ domain khác — hay từ
một domain chưa tồn tại — vào qua một Protocol ở đây và được
`entrypoints/wiring.py` nối vào.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

from app.domains.integration.domain.connectors import ConnectorSpec
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
