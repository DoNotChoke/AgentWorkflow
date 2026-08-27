"""Policy engine: một test cho mỗi `rule_id`, và thứ tự giữa chúng."""

from __future__ import annotations

import pytest
from conftest import make_tool

from app.core.context import PrincipalKind
from app.domains.integration.application.policy_engine import PolicyEngine
from app.domains.integration.domain.policy import (
    OrgToolRule,
    PolicyOutcome,
    PolicyRequest,
    QuotaVerdict,
    decide,
    rule_ids,
)
from app.domains.integration.domain.tools import ApprovalPolicy, ApprovalScope, SideEffect

USER_ROLES = frozenset({"org:member", "ws:member"})


def request(
    *,
    side_effect: SideEffect = SideEffect.READ,
    principal_kind: PrincipalKind = "user",
    roles: frozenset[str] = USER_ROLES,
    permissions: frozenset[str] = frozenset(),
    approval: ApprovalPolicy | None = None,
    enabled: bool = True,
    org_rule: OrgToolRule | None = None,
    quota: QuotaVerdict | None = None,
    plan_restricted: bool = False,
) -> PolicyRequest:
    return PolicyRequest(
        tenant_id="org_1",
        workspace_id="ws_1",
        principal_id="usr_1",
        principal_kind=principal_kind,
        principal_roles=roles,
        connection_id="conn_1",
        provider="demo",
        tool_id="demo.ping",
        tool_version="1.0",
        required_scopes=frozenset(),
        side_effect=side_effect,
        tool_permissions=permissions,
        tool_approval=approval or ApprovalPolicy(),
        tool_enabled=enabled,
        org_rule=org_rule,
        quota=quota or QuotaVerdict(),
        plan_restricted=plan_restricted,
    )


# ─── Từng luật ───────────────────────────────────────────────────────────────


def test_tool_da_tat_thi_tu_choi() -> None:
    decision = decide(request(enabled=False))
    assert decision.outcome is PolicyOutcome.DENY
    assert decision.rule_id == "tool_disabled"


def test_goi_cuoc_chan_thi_tu_choi() -> None:
    decision = decide(request(plan_restricted=True))
    assert decision.rule_id == "plan_restricted"


def test_het_han_muc_thi_tu_choi() -> None:
    decision = decide(request(quota=QuotaVerdict(exhausted=True, detail="hết 1000 lượt")))
    assert decision.rule_id == "quota_exhausted"
    assert "1000" in decision.detail


def test_organization_chan_thi_tu_choi() -> None:
    decision = decide(request(org_rule=OrgToolRule(outcome=PolicyOutcome.DENY)))
    assert decision.rule_id == "org_denied"


def test_thieu_vai_tro_thi_tu_choi_va_noi_ro_thieu_gi() -> None:
    decision = decide(request(permissions=frozenset({"ws:admin"})))
    assert decision.rule_id == "permission_denied"
    assert "ws:admin" in decision.detail


def test_organization_yeu_cau_duyet() -> None:
    decision = decide(
        request(
            org_rule=OrgToolRule(
                outcome=PolicyOutcome.REQUIRE_APPROVAL,
                approval_scope=ApprovalScope.WORKSPACE_ADMIN,
            )
        )
    )
    assert decision.outcome is PolicyOutcome.REQUIRE_APPROVAL
    assert decision.rule_id == "org_requires_approval"
    assert decision.approval_scope is ApprovalScope.WORKSPACE_ADMIN


def test_tool_tu_khai_can_duyet() -> None:
    decision = decide(
        request(approval=ApprovalPolicy(required=True, scope=ApprovalScope.WORKSPACE_ADMIN))
    )
    assert decision.outcome is PolicyOutcome.REQUIRE_APPROVAL
    assert decision.rule_id == "tool_requires_approval"
    assert decision.approval_scope is ApprovalScope.WORKSPACE_ADMIN


def test_thao_tac_pha_huy_do_may_khoi_xuong_can_duyet() -> None:
    """§7 — thao tác phá huỷ phải chịu sự điều khiển của chính sách.

    Một người thật bấm nút thì có người chịu trách nhiệm. Một agent hay một
    workflow tự chạy thì không.
    """
    decision = decide(request(side_effect=SideEffect.DESTRUCTIVE, principal_kind="workflow"))
    assert decision.outcome is PolicyOutcome.REQUIRE_APPROVAL
    assert decision.rule_id == "destructive_non_human"


def test_nguoi_that_van_xoa_duoc() -> None:
    decision = decide(request(side_effect=SideEffect.DESTRUCTIVE, principal_kind="user"))
    assert decision.outcome is PolicyOutcome.ALLOW


def test_truong_hop_binh_thuong_duoc_cho_phep() -> None:
    decision = decide(request())
    assert decision.allowed
    assert decision.rule_id == "allowed"


# ─── Thứ tự giữa các luật ────────────────────────────────────────────────────


def test_tu_choi_thang_yeu_cau_duyet() -> None:
    """Không có lý do gì đi hỏi người duyệt một thao tác vốn đã bị cấm."""
    decision = decide(
        request(
            plan_restricted=True,
            approval=ApprovalPolicy(required=True),
            side_effect=SideEffect.DESTRUCTIVE,
            principal_kind="workflow",
        )
    )
    assert decision.outcome is PolicyOutcome.DENY
    assert decision.rule_id == "plan_restricted"


def test_tool_tat_thang_moi_luat_khac() -> None:
    decision = decide(request(enabled=False, plan_restricted=True, permissions=frozenset({"x"})))
    assert decision.rule_id == "tool_disabled"


def test_thieu_quyen_thang_yeu_cau_duyet() -> None:
    decision = decide(
        request(permissions=frozenset({"ws:admin"}), approval=ApprovalPolicy(required=True))
    )
    assert decision.rule_id == "permission_denied"


# ─── Nghĩa vụ kèm theo ───────────────────────────────────────────────────────


def test_tool_doc_chi_phai_ghi_usage() -> None:
    assert decide(request(side_effect=SideEffect.READ)).obligations == frozenset({"record_usage"})


@pytest.mark.parametrize("side_effect", [SideEffect.WRITE, SideEffect.DESTRUCTIVE])
def test_tool_co_tac_dong_phai_ghi_ca_audit(side_effect: SideEffect) -> None:
    """§34 xếp thao tác phá huỷ được duyệt vào nhóm sự kiện audit."""
    obligations = decide(request(side_effect=side_effect, principal_kind="user")).obligations
    assert obligations == frozenset({"record_usage", "audit"})


def test_nghia_vu_van_di_kem_ca_khi_bi_tu_choi() -> None:
    """Một lần bị chặn cũng là chuyện đáng ghi lại."""
    decision = decide(request(side_effect=SideEffect.WRITE, enabled=False))
    assert "audit" in decision.obligations


# ─── Đồng bộ danh sách rule_id ───────────────────────────────────────────────


def test_moi_rule_id_deu_co_test() -> None:
    """Thêm một luật mà quên viết test thì test này đỏ.

    `rule_ids()` là danh sách khai báo; tập dưới đây là những gì thực sự được
    khẳng định ở trên. Hai bên lệch nhau nghĩa là có một nhánh chính sách chưa
    ai chạy qua.
    """
    tested = {
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
    assert tested == rule_ids()


# ─── Engine với ba bản đọc mặc định ──────────────────────────────────────────


class FakeOrgPolicy:
    def __init__(self, rule: OrgToolRule | None = None) -> None:
        self.rule = rule
        self.calls: list[tuple[str, str]] = []

    async def tool_rule(self, *, tenant_id: str, tool_id: str) -> OrgToolRule | None:
        self.calls.append((tenant_id, tool_id))
        return self.rule


class FakeQuota:
    def __init__(self, verdict: QuotaVerdict | None = None) -> None:
        self.verdict = verdict or QuotaVerdict()

    async def check(self, *, tenant_id: str, tool_id: str) -> QuotaVerdict:
        return self.verdict


class FakePlan:
    def __init__(self, restricted: frozenset[str] = frozenset()) -> None:
        self.restricted = restricted

    async def restricted_tools(self, *, tenant_id: str) -> frozenset[str]:
        return self.restricted


def build_engine(
    *,
    org: FakeOrgPolicy | None = None,
    quota: FakeQuota | None = None,
    plan: FakePlan | None = None,
) -> tuple[PolicyEngine, FakeOrgPolicy, FakeQuota, FakePlan]:
    org = org or FakeOrgPolicy()
    quota = quota or FakeQuota()
    plan = plan or FakePlan()
    return PolicyEngine(org_policy=org, quota=quota, plan=plan), org, quota, plan


async def test_engine_hoi_du_ba_nguon_roi_moi_quyet_dinh() -> None:
    engine, org, _, _ = build_engine()

    decision = await engine.decide(
        tool=make_tool(),
        tenant_id="org_1",
        workspace_id="ws_1",
        principal_id="usr_1",
        principal_kind="user",
        principal_roles=USER_ROLES,
        connection_id="conn_1",
    )

    assert decision.allowed
    assert org.calls == [("org_1", "demo.ping")]


async def test_engine_ap_dung_gioi_han_goi_cuoc() -> None:
    engine, _, _, _ = build_engine(plan=FakePlan(frozenset({"demo.ping"})))

    decision = await engine.decide(
        tool=make_tool(),
        tenant_id="org_1",
        workspace_id="ws_1",
        principal_id="usr_1",
        principal_kind="user",
        principal_roles=USER_ROLES,
        connection_id="conn_1",
    )

    assert decision.rule_id == "plan_restricted"


async def test_ban_mac_dinh_cua_phase_2_cho_qua() -> None:
    """Ba hiện thực mặc định phải trung tính, không phải chặn ngầm."""
    from app.domains.integration.infrastructure.policy_defaults import (
        NoOrgToolRules,
        NoPlanRestrictions,
        UnlimitedQuota,
    )

    engine = PolicyEngine(
        org_policy=NoOrgToolRules(), quota=UnlimitedQuota(), plan=NoPlanRestrictions()
    )

    decision = await engine.decide(
        tool=make_tool(),
        tenant_id="org_1",
        workspace_id="ws_1",
        principal_id="usr_1",
        principal_kind="user",
        principal_roles=USER_ROLES,
        connection_id="conn_1",
    )

    assert decision.allowed


# ─── Bất biến: org chỉ siết được, không nới được ─────────────────────────────


def test_to_chuc_siet_duoc_mot_tool_von_tu_do() -> None:
    decision = decide(
        request(
            approval=ApprovalPolicy(required=False),
            org_rule=OrgToolRule(outcome=PolicyOutcome.REQUIRE_APPROVAL),
        )
    )
    assert decision.outcome is PolicyOutcome.REQUIRE_APPROVAL
    assert decision.rule_id == "org_requires_approval"


def test_to_chuc_chan_duoc_mot_tool_von_tu_do() -> None:
    decision = decide(
        request(
            approval=ApprovalPolicy(required=False),
            org_rule=OrgToolRule(outcome=PolicyOutcome.DENY),
        )
    )
    assert decision.outcome is PolicyOutcome.DENY


def test_to_chuc_khong_bo_duoc_yeu_cau_duyet_cua_nen_tang() -> None:
    """Tài liệu tool là **sàn**, không phải giá trị mặc định.

    Org đặt `ALLOW` không gỡ được yêu cầu duyệt mà nền tảng đã khai.
    """
    decision = decide(
        request(
            approval=ApprovalPolicy(required=True, scope=ApprovalScope.ORG_OWNER),
            org_rule=OrgToolRule(outcome=PolicyOutcome.ALLOW),
        )
    )

    assert decision.outcome is PolicyOutcome.REQUIRE_APPROVAL
    assert decision.rule_id == "tool_requires_approval"
    assert decision.approval_scope is ApprovalScope.ORG_OWNER


def test_to_chuc_khong_ha_duoc_nguoi_ky_ma_nen_tang_doi() -> None:
    """Ca duy nhất thật sự phân biệt được "siết" với "nới".

    Hai nguồn cùng đòi duyệt, nhưng org chỉ định người ký **lỏng hơn** tool.
    Nếu org thắng thì nó vừa hạ yêu cầu của nền tảng xuống — bên chặt hơn phải
    thắng.

    Ba câu `if` nối tiếp không bắt được ca này dù xếp theo thứ tự nào: bên đứng
    trước luôn thắng, nên một trong hai chiều sẽ bị nới. Đó là lý do ba nguồn
    được gom lại và so bằng `strictness`.
    """
    decision = decide(
        request(
            approval=ApprovalPolicy(required=True, scope=ApprovalScope.ORG_OWNER),
            org_rule=OrgToolRule(
                outcome=PolicyOutcome.REQUIRE_APPROVAL,
                approval_scope=ApprovalScope.REQUESTER,
            ),
        )
    )

    assert decision.approval_scope is ApprovalScope.ORG_OWNER
    assert decision.rule_id == "tool_requires_approval"


def test_to_chuc_nang_duoc_nguoi_ky_len_muc_chat_hon() -> None:
    """Chiều ngược lại vẫn phải chạy: siết thì được."""
    decision = decide(
        request(
            approval=ApprovalPolicy(required=True, scope=ApprovalScope.REQUESTER),
            org_rule=OrgToolRule(
                outcome=PolicyOutcome.REQUIRE_APPROVAL,
                approval_scope=ApprovalScope.ORG_OWNER,
            ),
        )
    )

    assert decision.approval_scope is ApprovalScope.ORG_OWNER
    assert decision.rule_id == "org_requires_approval"


def test_thu_hang_chat_che_cua_pham_vi_duyet_xep_dung_thu_tu() -> None:
    ranked = sorted(ApprovalScope, key=lambda scope: scope.strictness)
    assert ranked == [
        ApprovalScope.REQUESTER,
        ApprovalScope.CONNECTION_OWNER,
        ApprovalScope.WORKSPACE_ADMIN,
        ApprovalScope.ORG_OWNER,
    ]


def test_to_chuc_khong_noi_duoc_san_cho_thao_tac_pha_huy() -> None:
    """Sàn thứ hai cũng không nới được: máy tự quyết xoá thì luôn phải hỏi."""
    decision = decide(
        request(
            side_effect=SideEffect.DESTRUCTIVE,
            principal_kind="workflow",
            org_rule=OrgToolRule(outcome=PolicyOutcome.ALLOW),
        )
    )

    assert decision.outcome is PolicyOutcome.REQUIRE_APPROVAL
    assert decision.rule_id == "destructive_non_human"


# ─── Ai được ký duyệt ────────────────────────────────────────────────────────


def test_may_tu_quyet_xoa_thi_chinh_nguoi_yeu_cau_ky_duoc() -> None:
    """`REQUESTER` là mặc định đúng cho `destructive_non_human`.

    Luật đó tồn tại vì máy tự quyết mà không ai chịu trách nhiệm. Người dùng
    bấm xác nhận là đã giải quyết xong đúng vấn đề đó — họ đang xác nhận thứ
    agent **suy ra**, không phải thứ họ đã nói.
    """
    decision = decide(request(side_effect=SideEffect.DESTRUCTIVE, principal_kind="workflow"))
    assert decision.approval_scope is ApprovalScope.REQUESTER


def test_chinh_sach_cua_to_chuc_khong_de_nguoi_yeu_cau_tu_ky() -> None:
    """Chính sách sinh ra để kiểm soát chính người đó thì họ không tự ký được."""
    decision = decide(
        request(
            org_rule=OrgToolRule(
                outcome=PolicyOutcome.REQUIRE_APPROVAL,
                approval_scope=ApprovalScope.WORKSPACE_ADMIN,
            )
        )
    )
    assert decision.approval_scope is ApprovalScope.WORKSPACE_ADMIN
