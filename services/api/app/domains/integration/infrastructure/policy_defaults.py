"""Hiện thực dễ dãi cho ba port chính sách chưa có nguồn cấp."""

from __future__ import annotations

from app.domains.integration.domain.policy import UNLIMITED, OrgToolRule, QuotaVerdict


class NoOrgToolRules:
    async def tool_rule(self, *, tenant_id: str, tool_id: str) -> OrgToolRule | None:  # noqa: ARG002
        return None


class UnlimitedQuota:
    async def check(self, *, tenant_id: str, tool_id: str) -> QuotaVerdict:  # noqa: ARG002
        return UNLIMITED


class NoPlanRestrictions:
    async def restricted_tools(self, *, tenant_id: str) -> frozenset[str]:  # noqa: ARG002
        return frozenset()


#: Bộ ba mặc định của Phase 2, gom lại một chỗ để `wiring.py` thay một lần.
PolicyDefaults = (NoOrgToolRules, UnlimitedQuota, NoPlanRestrictions)
