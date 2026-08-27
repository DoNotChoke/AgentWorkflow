"""Quyết định chính sách đứng trước mọi lần thực thi tool"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from app.core.context import PrincipalKind
from app.domains.integration.domain.tools import ApprovalPolicy, ApprovalScope, SideEffect


class PolicyOutcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True, slots=True)
class QuotaVerdict:
    """Kết quả của `QuotaChecker`"""

    exhausted: bool = False
    detail: str = ""


UNLIMITED = QuotaVerdict()


@dataclass(frozen=True, slots=True)
class OrgToolRule:
    """Luật riêng của một organization cho một tool."""

    outcome: PolicyOutcome
    detail: str = ""
    approval_scope: ApprovalScope = ApprovalScope.WORKSPACE_ADMIN


@dataclass(frozen=True, slots=True)
class PolicyRequest:
    tenant_id: str
    workspace_id: str
    principal_id: str
    principal_kind: PrincipalKind
    principal_roles: frozenset[str]

    connection_id: str
    provider: str
    tool_id: str
    tool_version: str
    required_scopes: frozenset[str]
    side_effect: SideEffect
    tool_permissions: frozenset[str]
    tool_approval: ApprovalPolicy
    tool_enabled: bool

    #: Ai đang thực thi.
    agent_id: str | None = None
    workflow_id: str | None = None
    execution_id: str | None = None

    #: Ba đầu vào bị hoãn. Xem `application/policy_engine.py`.
    org_rule: OrgToolRule | None = None
    quota: QuotaVerdict = UNLIMITED
    plan_restricted: bool = False


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    outcome: PolicyOutcome

    #: Mã máy đọc, ổn định qua các phiên bản
    rule_id: str

    #: Câu giải thích cho người dùng.
    detail: str

    approval_scope: ApprovalScope | None = None

    #: Nghĩa vụ kèm theo quyết định: `{"record_usage", "audit"}`.
    obligations: frozenset[str] = frozenset()

    details: Mapping[str, str] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.outcome is PolicyOutcome.ALLOW


#: Tool nào cũng phải ghi usage. Tool có tác động ra ngoài thì ghi thêm audit
def _obligations(side_effect: SideEffect) -> frozenset[str]:
    if side_effect.mutates:
        return frozenset({"record_usage", "audit"})
    return frozenset({"record_usage"})


def decide(request: PolicyRequest) -> PolicyDecision:
    """Áp các luật theo thứ tự. Luật đầu tiên khớp thắng.

    Thứ tự là DENY trước REQUIRE_APPROVAL trước ALLOW: một thao tác bị cấm thì
    không có lý do gì để đi hỏi ai đó phê duyệt nó.
    """
    obligations = _obligations(request.side_effect)

    def deny(rule_id: str, detail: str) -> PolicyDecision:
        return PolicyDecision(
            outcome=PolicyOutcome.DENY,
            rule_id=rule_id,
            detail=detail,
            obligations=obligations,
        )

    if not request.tool_enabled:
        return deny("tool_disabled", f"Tool {request.tool_id} đang tắt.")

    if request.plan_restricted:
        return deny("plan_restricted", f"Gói hiện tại không có {request.tool_id}.")

    if request.quota.exhausted:
        return deny(
            "quota_exhausted",
            request.quota.detail or "Đã dùng hết hạn mức cho kỳ này.",
        )

    if request.org_rule is not None and request.org_rule.outcome is PolicyOutcome.DENY:
        return deny(
            "org_denied",
            request.org_rule.detail or f"Organization đã chặn {request.tool_id}.",
        )

    if missing := request.tool_permissions - request.principal_roles:
        return deny(
            "permission_denied",
            f"Cần vai trò {', '.join(sorted(missing))} để dùng {request.tool_id}.",
        )

    # Ba nguồn có thể đòi duyệt. Gom lại một chỗ thay vì ba câu `if` nối tiếp
    # là có chủ đích: bên **chặt hơn** thắng, nên không nguồn nào hạ được yêu
    # cầu của nguồn kia. Đó là "chỉ được siết, không được nới" ở dạng cấu trúc,
    # không phụ thuộc thứ tự dòng code.
    #
    # Hoà thì nguồn đứng trước thắng, nên org được ưu tiên báo tên luật.
    demands: list[tuple[str, str, ApprovalScope]] = []

    if request.org_rule is not None and request.org_rule.outcome is PolicyOutcome.REQUIRE_APPROVAL:
        demands.append(
            (
                "org_requires_approval",
                request.org_rule.detail or f"Organization yêu cầu duyệt {request.tool_id}.",
                request.org_rule.approval_scope,
            )
        )

    if request.tool_approval.required:
        demands.append(
            (
                "tool_requires_approval",
                f"{request.tool_id} luôn cần người duyệt.",
                request.tool_approval.scope,
            )
        )

    # Máy tự quyết xoá thì không có ai chịu trách nhiệm cho quyết định đó.
    if request.side_effect is SideEffect.DESTRUCTIVE and request.principal_kind != "user":
        demands.append(
            (
                "destructive_non_human",
                f"{request.tool_id} xoá dữ liệu và đang được gọi tự động.",
                request.tool_approval.scope,
            )
        )

    if demands:
        rule_id, detail, scope = max(demands, key=lambda demand: demand[2].strictness)
        return PolicyDecision(
            outcome=PolicyOutcome.REQUIRE_APPROVAL,
            rule_id=rule_id,
            detail=detail,
            approval_scope=scope,
            obligations=obligations,
        )

    return PolicyDecision(
        outcome=PolicyOutcome.ALLOW,
        rule_id="allowed",
        detail="",
        obligations=obligations,
    )


def rule_ids() -> frozenset[str]:
    """Mọi `rule_id` mà `decide` phát ra. Dùng cho test và cho audit."""
    return frozenset(
        {
            "tool_disabled",
            "plan_restricted",
            "quota_exhausted",
            "org_denied",
            "permission_denied",
            "org_requires_approval",
            "tool_requires_approval",
            "destructive_non_human",
            "allowed",
        }
    )


def missing_permissions(required: frozenset[str], held: Iterable[str]) -> frozenset[str]:
    return required - frozenset(held)
