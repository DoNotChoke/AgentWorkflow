"""Gom đủ 14 đầu vào của §7 rồi giao cho luật thuần ở `domain/policy.py`."""

from __future__ import annotations

from app.core.context import PrincipalKind
from app.domains.integration.application.ports import (
    OrgPolicyReader,
    PlanReader,
    QuotaChecker,
)
from app.domains.integration.domain.policy import (
    PolicyDecision,
    PolicyRequest,
    decide,
)
from app.domains.integration.domain.tools import ToolSpec


class PolicyEngine:
    """Ba bản đọc, một lời gọi hàm thuần.

    Chia như vậy để luật kiểm được mà không cần dựng gì: `decide()` không chạm
    I/O, còn lớp này chỉ đi lấy dữ liệu.
    """

    __slots__ = ("_org_policy", "_plan", "_quota")

    def __init__(
        self,
        *,
        org_policy: OrgPolicyReader,
        quota: QuotaChecker,
        plan: PlanReader,
    ) -> None:
        self._org_policy = org_policy
        self._quota = quota
        self._plan = plan

    async def decide(
        self,
        *,
        tool: ToolSpec,
        tenant_id: str,
        workspace_id: str,
        principal_id: str,
        principal_kind: PrincipalKind,
        principal_roles: frozenset[str],
        connection_id: str,
        agent_id: str | None = None,
        workflow_id: str | None = None,
        execution_id: str | None = None,
    ) -> PolicyDecision:
        org_rule = await self._org_policy.tool_rule(tenant_id=tenant_id, tool_id=tool.tool_id)
        quota = await self._quota.check(tenant_id=tenant_id, tool_id=tool.tool_id)
        restricted = await self._plan.restricted_tools(tenant_id=tenant_id)

        return decide(
            PolicyRequest(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                principal_id=principal_id,
                principal_kind=principal_kind,
                principal_roles=principal_roles,
                connection_id=connection_id,
                provider=tool.provider,
                tool_id=tool.tool_id,
                tool_version=tool.version,
                required_scopes=tool.required_scopes,
                side_effect=tool.side_effect,
                tool_permissions=tool.permissions,
                tool_approval=tool.approval,
                tool_enabled=tool.enabled,
                agent_id=agent_id,
                workflow_id=workflow_id,
                execution_id=execution_id,
                org_rule=org_rule,
                quota=quota,
                plan_restricted=tool.tool_id in restricted,
            )
        )
