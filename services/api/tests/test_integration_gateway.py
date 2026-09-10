"""Integration Gateway: thứ tự các chặng, và các bất biến của §6.3.

Không cần Docker. Fake viết tay cho mọi port — không `unittest.mock` — và tất
cả cùng ghi vào một sổ thứ tự chung, vì thứ tự chính là thứ đáng kiểm nhất ở
đây: §6.3 liệt kê trách nhiệm chứ không liệt kê thứ tự, và ba chỗ dễ làm ngược
đều dẫn tới hậu quả im lặng.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core.errors import (
    ApprovalRejectedError,
    AuthorizationError,
    ConnectionExpiredError,
    ErrorCategory,
    PlatformTimeoutError,
    ProviderPermanentError,
    ProviderUnavailableError,
    RateLimitedError,
    RetryExhaustedError,
    ValidationError,
)
from app.domains.integration.application.gateway import IntegrationGateway
from app.domains.integration.application.policy_engine import PolicyEngine
from app.domains.integration.domain.catalog import ToolRegistry, default_registry
from app.domains.integration.domain.idempotency import (
    ClaimResult,
    IdempotencyClaim,
    IdempotencyRecord,
    InvocationStatus,
)
from app.domains.integration.domain.invocation import ToolInvocation
from app.domains.integration.infrastructure.policy_defaults import (
    NoOrgToolRules,
    NoPlanRestrictions,
    UnlimitedQuota,
)

REGISTRY = default_registry()


def _registry_of(tool: Any) -> ToolRegistry:
    """Registry thật chỉ chứa một tool đã sửa.

    Dùng `ToolRegistry` chứ không phải một lớp giả: mọi ràng buộc lúc dựng
    catalog vẫn chạy, nên một spec test sai hình sẽ đỏ ngay thay vì lặng lẽ
    đi qua.
    """
    import dataclasses

    connector = dataclasses.replace(REGISTRY.get_connector(tool.provider), tool_ids=(tool.tool_id,))
    return ToolRegistry([connector], [tool])


READ_TOOL = "slack.list_channels"
WRITE_TOOL = "slack.post_message"


# ─── Fake, tất cả ghi vào một sổ thứ tự chung ────────────────────────────────


class Journal(list[str]):
    def note(self, what: str) -> None:
        self.append(what)


class FakeCredentials:
    def __init__(self, journal: Journal) -> None:
        self.journal = journal
        self.resolved: list[frozenset[str]] = []
        self.unusable: list[str] = []
        self.error: Exception | None = None

    async def resolve(
        self,
        *,
        connection_id: str,
        tenant_id: str,
        workspace_id: str,
        acting_user_id: str,
        required_scopes: frozenset[str] = frozenset(),
    ) -> Any:
        self.journal.note("credential")
        self.resolved.append(required_scopes)
        if self.error:
            raise self.error

        class Credential:
            access_token = "xoxb-bi-mat"  # noqa: S105

        return Credential()

    async def report_unusable(self, *, connection_id: str, tenant_id: str, reason: str) -> None:
        self.unusable.append(reason)


class FakeConnector:
    def __init__(self, journal: Journal, outcomes: list[Any] | None = None) -> None:
        self.journal = journal
        self.outcomes = outcomes or []
        self.calls = 0

    async def call(self, *, tool: Any, connector: Any, credential: Any, payload: Any) -> Any:
        self.journal.note("provider")
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else _ok()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class Response:
    def __init__(self, output: Mapping[str, Any], reference: str | None = None) -> None:
        self.output = output
        self.provider_reference = reference
        self.retry_after_seconds = None


def _ok() -> Response:
    return Response({"channels": []})


class FakeRateLimiter:
    def __init__(self, journal: Journal, error: Exception | None = None) -> None:
        self.journal = journal
        self.error = error
        self.calls = 0

    async def acquire(self, *, target: Any, tool: Any, connector: Any) -> None:
        self.journal.note("rate_limit")
        self.calls += 1
        if self.error:
            raise self.error


class FakeBreaker:
    def __init__(self, journal: Journal, error: Exception | None = None) -> None:
        self.journal = journal
        self.error = error
        self.failures: list[ErrorCategory] = []
        self.successes = 0

    async def check(self, *, connector: Any, tenant_id: str, connection_id: str) -> None:
        self.journal.note("breaker")
        if self.error:
            raise self.error

    async def record_success(self, *, connector: Any, tenant_id: str, connection_id: str) -> None:
        self.successes += 1

    async def record_failure(
        self, *, connector: Any, tenant_id: str, connection_id: str, category: ErrorCategory
    ) -> None:
        self.failures.append(category)


class FakeIdempotency:
    def __init__(self, journal: Journal, existing: IdempotencyRecord | None = None) -> None:
        self.journal = journal
        self.existing = existing
        self.completed: list[dict[str, Any]] = []
        self.failed: list[dict[str, Any]] = []

    async def claim(self, claim: IdempotencyClaim) -> ClaimResult:
        self.journal.note("idempotency")
        if self.existing is None:
            return ClaimResult(record=_record(claim), fresh=True)
        return ClaimResult(record=self.existing, fresh=False)

    async def complete(self, **kwargs: Any) -> None:
        self.completed.append(kwargs)

    async def fail(self, **kwargs: Any) -> None:
        self.failed.append(kwargs)


def _record(claim: IdempotencyClaim, **overrides: Any) -> IdempotencyRecord:
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "tenant_id": claim.tenant_id,
        "key": claim.key,
        "workspace_id": claim.workspace_id,
        "connection_id": claim.connection_id,
        "tool_id": claim.tool_id,
        "tool_version": claim.tool_version,
        "request_fingerprint": claim.request_fingerprint,
        "status": InvocationStatus.IN_FLIGHT,
        "attempts": 1,
        "created_at": now,
        "updated_at": now,
        "expires_at": now + timedelta(days=7),
    }
    return IdempotencyRecord(**{**base, **overrides})


class FakeTraceSink:
    """Đúng hình `ToolInvocationSink`: payload đi cạnh bản ghi, không trong nó."""

    def __init__(self) -> None:
        self.records: list[Any] = []
        self.payloads: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []

    async def record(
        self,
        invocation: Any,
        *,
        input_payload: Mapping[str, Any],
        output_payload: Mapping[str, Any],
    ) -> None:
        self.records.append(invocation)
        self.payloads.append((input_payload, output_payload))


class FakeSink:
    def __init__(self) -> None:
        self.records: list[Any] = []

    async def record(self, usage: Any) -> None:
        self.records.append(usage)


def build_gateway(
    *,
    outcomes: list[Any] | None = None,
    rate_limit_error: Exception | None = None,
    breaker_error: Exception | None = None,
    existing: IdempotencyRecord | None = None,
    tool: Any = None,
) -> tuple[IntegrationGateway, dict[str, Any]]:
    journal = Journal()
    credentials = FakeCredentials(journal)
    connector = FakeConnector(journal, outcomes)
    rate_limiter = FakeRateLimiter(journal, rate_limit_error)
    breaker = FakeBreaker(journal, breaker_error)
    idempotency = FakeIdempotency(journal, existing)
    trace = FakeTraceSink()
    usage = FakeSink()

    async def instant(_seconds: float) -> None:
        return None

    gateway = IntegrationGateway(
        registry=REGISTRY if tool is None else _registry_of(tool),
        policy=PolicyEngine(
            org_policy=NoOrgToolRules(), quota=UnlimitedQuota(), plan=NoPlanRestrictions()
        ),
        credentials=credentials,
        connector=connector,
        rate_limiter=rate_limiter,
        breaker=breaker,
        idempotency=idempotency,
        trace=trace,
        usage=usage,
        sleep=instant,
    )
    parts: dict[str, Any] = {
        "journal": journal,
        "credentials": credentials,
        "connector": connector,
        "rate_limiter": rate_limiter,
        "breaker": breaker,
        "idempotency": idempotency,
        "trace": trace,
        "usage": usage,
    }
    return gateway, parts


def command(
    tool_id: str = READ_TOOL,
    *,
    payload: Mapping[str, Any] | None = None,
    key: str | None = None,
    principal_kind: Any = "user",
) -> ToolInvocation:
    return ToolInvocation(
        tool_id=tool_id,
        connection_id="conn_1",
        tenant_id="org_1",
        workspace_id="ws_1",
        acting_user_id="usr_1",
        principal_kind=principal_kind,
        principal_roles=frozenset({"org:member", "ws:member"}),
        input=payload if payload is not None else {},
        idempotency_key=key,
    )


def post(**kwargs: Any) -> ToolInvocation:
    return command(
        WRITE_TOOL, payload={"channel": "C123", "text": "xin chào"}, key="idm_1", **kwargs
    )


# ─── Thứ tự các chặng (§6.3) ─────────────────────────────────────────────────


async def test_idempotency_chay_truoc_rate_limit() -> None:
    """Một lần phát lại không được tiêu quota, và cũng không được bị 429 chặn.

    Ngược lại thì bản sao bị từ chối, người gọi thử lại sau khi cửa sổ trôi
    qua, và lần đó đi thẳng tới Slack — tin nhắn thứ hai từ một lời gọi mà toàn
    bộ mục đích là để không lặp.
    """
    gateway, parts = build_gateway()

    await gateway.invoke(post())

    journal = parts["journal"]
    assert journal.index("idempotency") < journal.index("rate_limit")


async def test_breaker_chay_truoc_rate_limit() -> None:
    """Provider đang chết thì đừng đốt một suất quota để phát hiện lại."""
    gateway, parts = build_gateway()

    await gateway.invoke(command())

    journal = parts["journal"]
    assert journal.index("breaker") < journal.index("rate_limit")


async def test_credential_lay_muon_nhat_co_the() -> None:
    """§2.4 — token tồn tại ở dạng rõ càng ngắn càng tốt."""
    gateway, parts = build_gateway()

    await gateway.invoke(post())

    journal = parts["journal"]
    assert journal.index("credential") > journal.index("rate_limit")
    assert journal.index("credential") < journal.index("provider")


async def test_thu_tu_day_du_cua_mot_loi_goi_thanh_cong() -> None:
    gateway, parts = build_gateway()

    await gateway.invoke(post())

    assert list(parts["journal"]) == [
        "idempotency",
        "breaker",
        "rate_limit",
        "credential",
        "provider",
    ]


# ─── Chặn sớm: không gì phía sau được chạy ───────────────────────────────────


async def test_input_sai_thi_khong_cham_toi_gi_ca() -> None:
    """Input sai không bao giờ retryable và không đáng tiêu một suất quota."""
    gateway, parts = build_gateway()

    with pytest.raises(ValidationError, match="Input của"):
        await gateway.invoke(command(WRITE_TOOL, payload={"channel": "khong-hop-le"}, key="idm_1"))

    assert list(parts["journal"]) == []


async def test_chinh_sach_tu_choi_thi_khong_cham_toi_provider() -> None:
    import dataclasses

    disabled = dataclasses.replace(REGISTRY.get_tool(READ_TOOL), enabled=False)
    gateway, parts = build_gateway(tool=disabled)

    with pytest.raises(AuthorizationError):
        await gateway.invoke(command())

    assert parts["connector"].calls == 0
    assert list(parts["journal"]) == []


async def test_mach_ho_thi_khong_giai_ma_credential() -> None:
    gateway, parts = build_gateway(breaker_error=ProviderUnavailableError("Slack đang sập."))

    with pytest.raises(ProviderUnavailableError):
        await gateway.invoke(command())

    assert "credential" not in parts["journal"]
    assert parts["connector"].calls == 0


async def test_het_han_muc_thi_khong_giai_ma_credential() -> None:
    gateway, parts = build_gateway(rate_limit_error=RateLimitedError("Quá tay."))

    with pytest.raises(RateLimitedError):
        await gateway.invoke(command())

    assert "credential" not in parts["journal"]


async def test_thieu_scope_thi_provider_khong_bi_cham_toi() -> None:
    """`CredentialProvider` kiểm scope; Gateway chỉ chuyển yêu cầu xuống."""
    gateway, parts = build_gateway()
    parts["credentials"].error = ConnectionExpiredError("Thiếu scope.")

    with pytest.raises(ConnectionExpiredError):
        await gateway.invoke(command())

    assert parts["connector"].calls == 0


async def test_scope_cua_tool_duoc_chuyen_xuong_cong_credential() -> None:
    gateway, parts = build_gateway()

    await gateway.invoke(command())

    assert parts["credentials"].resolved == [frozenset({"channels:read"})]


# ─── Idempotency (§15.1) ─────────────────────────────────────────────────────


async def test_tool_write_thieu_khoa_thi_bi_tu_choi() -> None:
    gateway, parts = build_gateway()

    with pytest.raises(ValidationError, match="bắt buộc"):
        await gateway.invoke(command(WRITE_TOOL, payload={"channel": "C123", "text": "hi"}))

    assert parts["connector"].calls == 0


async def test_ban_ghi_da_xong_thi_phat_lai_khong_goi_provider() -> None:
    claim = IdempotencyClaim(
        tenant_id="org_1",
        workspace_id="ws_1",
        connection_id="conn_1",
        tool_id=WRITE_TOOL,
        tool_version="1.0",
        key="idm_1",
        request_fingerprint="",
        expires_at=datetime.now(UTC),
        at=datetime.now(UTC),
    )
    from app.domains.integration.domain.idempotency import fingerprint

    done = _record(
        claim,
        request_fingerprint=fingerprint({"channel": "C123", "text": "xin chào"}),
        status=InvocationStatus.SUCCEEDED,
        result={"channel_id": "C123", "message_ts": "1.2"},
        provider_reference="C123:1.2",
    )
    gateway, parts = build_gateway(existing=done)

    result = await gateway.invoke(post())

    assert result.replayed is True
    assert result.provider_reference == "C123:1.2"
    assert parts["connector"].calls == 0
    assert "rate_limit" not in parts["journal"]


async def test_dung_lai_khoa_cho_input_khac_bi_tu_choi() -> None:
    claim = IdempotencyClaim(
        tenant_id="org_1",
        workspace_id="ws_1",
        connection_id="conn_1",
        tool_id=WRITE_TOOL,
        tool_version="1.0",
        key="idm_1",
        request_fingerprint="mot-van-tay-khac",
        expires_at=datetime.now(UTC),
        at=datetime.now(UTC),
    )
    gateway, parts = build_gateway(existing=_record(claim, status=InvocationStatus.SUCCEEDED))

    with pytest.raises(ValidationError, match="thao tác khác"):
        await gateway.invoke(post())

    assert parts["connector"].calls == 0


async def test_thanh_cong_thi_chot_so_idempotency() -> None:
    gateway, parts = build_gateway(
        outcomes=[Response({"channel_id": "C1", "message_ts": "1.2"}, "C1:1.2")]
    )

    await gateway.invoke(post())

    assert parts["idempotency"].completed[0]["provider_reference"] == "C1:1.2"


async def test_that_bai_cung_duoc_chot_vao_so() -> None:
    gateway, parts = build_gateway(outcomes=[ProviderPermanentError("hỏng")])

    with pytest.raises(ProviderPermanentError):
        await gateway.invoke(post())

    assert parts["idempotency"].failed[0]["category"] is ErrorCategory.PROVIDER_PERMANENT


# ─── Retry (§15, §2.5) ───────────────────────────────────────────────────────


async def test_loi_vinh_vien_khong_bao_gio_duoc_thu_lai() -> None:
    gateway, parts = build_gateway(outcomes=[ProviderPermanentError("sai tham số")])

    with pytest.raises(ProviderPermanentError):
        await gateway.invoke(command())

    assert parts["connector"].calls == 1


async def test_loi_tam_thoi_duoc_thu_lai_voi_tool_doc() -> None:
    gateway, parts = build_gateway(
        outcomes=[ProviderUnavailableError("503"), ProviderUnavailableError("503"), _ok()]
    )

    result = await gateway.invoke(command())

    assert parts["connector"].calls == 3
    assert result.attempts == 3


async def test_tool_write_khong_co_khoa_thi_khong_thu_lai() -> None:
    """§2.5 — thất bại khi nhận phản hồi không có nghĩa thao tác đã không xảy ra.

    Tool `post_message` bắt buộc khoá, nên ca này dựng bằng một tool `WRITE`
    không bắt buộc để chứng minh luật nằm ở `may_retry`, không ở chỗ khác.
    """
    import dataclasses

    from app.domains.integration.domain.tools import IdempotencyPolicy

    tool = REGISTRY.get_tool(WRITE_TOOL)
    loose = dataclasses.replace(tool, idempotency=IdempotencyPolicy())

    gateway, parts = build_gateway(
        outcomes=[PlatformTimeoutError("qua han"), PlatformTimeoutError("qua han"), _ok()],
        tool=loose,
    )

    with pytest.raises(PlatformTimeoutError):
        await gateway.invoke(command(WRITE_TOOL, payload={"channel": "C123", "text": "hi"}))

    assert parts["connector"].calls == 1


async def test_tool_write_co_khoa_thi_thu_lai_duoc() -> None:
    """Chiều ngược lại: có khoá thì bản sao bị sổ chặn, nên retry an toàn."""
    gateway, parts = build_gateway(outcomes=[PlatformTimeoutError("qua han"), _ok()])

    await gateway.invoke(post())

    assert parts["connector"].calls == 2


async def test_het_luot_thi_boc_thanh_retry_exhausted() -> None:
    """`RETRY_EXHAUSTED` không nằm trong `RETRYABLE`, nên Temporal ở Phase 4
    không chồng thêm một vòng retry nữa lên trên vòng đã cạn."""
    gateway, parts = build_gateway(outcomes=[ProviderUnavailableError("503")] * 3)

    with pytest.raises(RetryExhaustedError) as caught:
        await gateway.invoke(command())

    assert parts["connector"].calls == 3
    assert caught.value.retryable is False


async def test_retry_after_cua_provider_thang_backoff_tu_tinh() -> None:
    waited: list[float] = []

    gateway, parts = build_gateway(
        outcomes=[RateLimitedError("cham lai", retry_after_seconds=9), _ok()]
    )

    async def record(seconds: float) -> None:
        waited.append(seconds)

    gateway._sleep = record  # noqa: SLF001

    await gateway.invoke(command())

    assert waited == [9.0]


# ─── Báo ngược khi token chết (§23) ──────────────────────────────────────────


async def test_token_bi_thu_hoi_thi_bao_nguoc_cho_domain_connection() -> None:
    """Slack không có refresh token, nên đây là tín hiệu duy nhất khi app bị gỡ.

    Thiếu đường này thì hàng kết nối nằm `active` vĩnh viễn và màn hình
    Reconnect không bao giờ sáng.
    """
    gateway, parts = build_gateway(
        outcomes=[ConnectionExpiredError("token chết", details={"provider_code": "token_revoked"})]
    )

    with pytest.raises(ConnectionExpiredError):
        await gateway.invoke(command())

    assert parts["credentials"].unusable == ["token_revoked"]


async def test_loi_khac_khong_bao_nguoc() -> None:
    gateway, parts = build_gateway(outcomes=[ProviderPermanentError("tham số sai")])

    with pytest.raises(ProviderPermanentError):
        await gateway.invoke(command())

    assert parts["credentials"].unusable == []


# ─── Circuit breaker được nuôi đúng ──────────────────────────────────────────


async def test_thanh_cong_duoc_ghi_nhan_cho_mach() -> None:
    gateway, parts = build_gateway()

    await gateway.invoke(command())

    assert parts["breaker"].successes == 1
    assert parts["breaker"].failures == []


async def test_that_bai_duoc_ghi_nhan_kem_phan_loai() -> None:
    gateway, parts = build_gateway(outcomes=[ProviderUnavailableError("503"), _ok()])

    await gateway.invoke(command())

    assert parts["breaker"].failures == [ErrorCategory.PROVIDER_UNAVAILABLE]
    assert parts["breaker"].successes == 1


# ─── Trace và usage phát ra ở cả ba lối ra (§9.1, §35) ───────────────────────


async def test_trace_phat_ra_khi_thanh_cong() -> None:
    gateway, parts = build_gateway()

    await gateway.invoke(command())

    record = parts["trace"].records[0]
    assert record.status == "succeeded"
    assert record.tool_id == READ_TOOL
    assert record.input_schema_version == "1.0"


async def test_trace_phat_ra_ca_khi_that_bai() -> None:
    """Một lời gọi hỏng là thứ đáng ghi lại nhất."""
    gateway, parts = build_gateway(outcomes=[ProviderPermanentError("hỏng")])

    with pytest.raises(ProviderPermanentError):
        await gateway.invoke(command())

    record = parts["trace"].records[0]
    assert record.status == "failed"
    assert record.error_category == "ProviderPermanentError"


async def test_trace_phat_ra_khi_phat_lai() -> None:
    claim = IdempotencyClaim(
        tenant_id="org_1",
        workspace_id="ws_1",
        connection_id="conn_1",
        tool_id=WRITE_TOOL,
        tool_version="1.0",
        key="idm_1",
        request_fingerprint="",
        expires_at=datetime.now(UTC),
        at=datetime.now(UTC),
    )
    from app.domains.integration.domain.idempotency import fingerprint

    done = _record(
        claim,
        request_fingerprint=fingerprint({"channel": "C123", "text": "xin chào"}),
        status=InvocationStatus.SUCCEEDED,
        result={"channel_id": "C123", "message_ts": "1.2"},
    )
    gateway, parts = build_gateway(existing=done)

    await gateway.invoke(post())

    assert parts["trace"].records[0].status == "replayed"


async def test_trace_chi_mang_van_tay_khong_mang_payload() -> None:
    """§33 — input của tool mang dữ liệu người dùng, và log đi xa hơn database."""
    gateway, parts = build_gateway()

    await gateway.invoke(post())

    record = parts["trace"].records[0]
    assert len(record.input_ref.sha256) == 64
    assert record.input_ref.byte_size > 0
    assert "xin chào" not in str(record)


async def test_payload_den_duoc_sink_qua_duong_rieng() -> None:
    """Bản ghi giữ hàng rào §33; payload vẫn phải tới nơi cất được nó (§9.1).

    Hai đường tách biệt: sink chỉ log thì bỏ qua tham số payload, sink ghi
    trace thì cất nó — mà không sink nào phải bới trong bản ghi.
    """
    gateway, parts = build_gateway()

    await gateway.invoke(post())

    sent_input, sent_output = parts["trace"].payloads[0]
    assert sent_input["text"] == "xin chào"
    assert sent_output == {"channels": []}


async def test_usage_phat_ra_cung_luc() -> None:
    gateway, parts = build_gateway()

    await gateway.invoke(command())

    assert parts["usage"].records[0].tool_id == READ_TOOL


# ─── Phê duyệt (§8) ──────────────────────────────────────────────────────────


async def test_tool_can_duyet_thi_khong_cham_toi_provider() -> None:
    import dataclasses

    from app.domains.integration.domain.tools import ApprovalPolicy, ApprovalScope

    tool = REGISTRY.get_tool(READ_TOOL)
    gated = dataclasses.replace(
        tool, approval=ApprovalPolicy(required=True, scope=ApprovalScope.WORKSPACE_ADMIN)
    )

    gateway, parts = build_gateway(tool=gated)

    with pytest.raises(ApprovalRejectedError) as caught:
        await gateway.invoke(command())

    assert parts["connector"].calls == 0
    assert "workspace_admin" in str(caught.value.details["approval_scope"])


async def test_mach_ho_khong_bi_thu_lai() -> None:
    """Mạch hở là quyết định "đừng gọi", không phải một lời gọi hỏng.

    Cả hai cổng ném ra lỗi thuộc nhóm retryable, nên nếu chúng nằm trong khối
    bắt lỗi retry thì Gateway sẽ hỏi lại đúng câu vừa có trả lời — ba lần —
    rồi báo `RetryExhausted`. Đó là đốt sạch ngân sách retry để không làm gì.
    """
    gateway, parts = build_gateway(breaker_error=ProviderUnavailableError("Slack đang sập."))

    with pytest.raises(ProviderUnavailableError):
        await gateway.invoke(command())

    assert parts["journal"].count("breaker") == 1


async def test_het_han_muc_khong_bi_thu_lai() -> None:
    """Chờ 42 giây rồi thử lại *bên trong* request là giữ request mở 42 giây."""
    gateway, parts = build_gateway(
        rate_limit_error=RateLimitedError("Quá tay.", retry_after_seconds=42)
    )

    with pytest.raises(RateLimitedError):
        await gateway.invoke(command())

    assert parts["journal"].count("rate_limit") == 1


async def test_cong_van_chay_lai_o_moi_luot_thu() -> None:
    """Mỗi lượt thử là một lời gọi thật, nên nó phải tiêu một suất hạn mức.

    Mặt còn lại của hai test trên: cổng không bị *thử lại*, nhưng cũng không
    được bỏ qua ở những lượt sau.
    """
    gateway, parts = build_gateway(
        outcomes=[ProviderUnavailableError("503"), ProviderUnavailableError("503"), _ok()]
    )

    await gateway.invoke(command())

    assert parts["rate_limiter"].calls == 3
    assert parts["journal"].count("breaker") == 3
