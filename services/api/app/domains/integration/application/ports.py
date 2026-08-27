"""Những gì Integration Plane cần từ bên ngoài, khai bằng Protocol."""

from __future__ import annotations

from typing import Protocol

from app.domains.integration.domain.policy import OrgToolRule, QuotaVerdict


class OrgPolicyReader(Protocol):
    """Luật riêng của organization cho một tool."""

    async def tool_rule(self, *, tenant_id: str, tool_id: str) -> OrgToolRule | None: ...


class QuotaChecker(Protocol):
    """Hạn mức usage"""

    async def check(self, *, tenant_id: str, tool_id: str) -> QuotaVerdict: ...


class PlanReader(Protocol):
    """Tool bị gói cước chặn"""

    async def restricted_tools(self, *, tenant_id: str) -> frozenset[str]: ...
