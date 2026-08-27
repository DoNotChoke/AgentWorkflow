"""Integration Gateway — mọi lời gọi provider đi qua đây."""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import jsonschema

from app.core.context import current_context
from app.core.errors import AppError, ErrorCategory, InternalError, RetryExhaustedError
from app.core.ids import ResourceKind, new_id
from app.core.logging import get_logger
from app.core.observability import current_otel_ids
from app.domains.integration.application.policy_engine import PolicyEngine
from app.domains.integration.application.ports import (
    CircuitBreaker,
    Connector,
    CredentialProvider,
    IdempotencyStore,
    RateLimiter,
    ToolInvocationSink,
    UsageRecorder,
)
from app.domains.integration.domain.catalog import ToolRegistry
from app.domains.integration.domain.connectors import ConnectorSpec
from app.domains.integration.domain.idempotency import (
    IdempotencyClaim,
    IdempotencyRecord,
    ensure_key_present,
    ensure_same_operation,
    fingerprint,
)
from app.domains.integration.domain.invocation import (
    PayloadRef,
    RateLimitTarget,
    ToolInvocation,
    ToolInvocationRecord,
    ToolResult,
    ToolUsage,
)
from app.domains.integration.domain.policy import PolicyOutcome
from app.domains.integration.domain.retry import may_retry, wait_for
from app.domains.integration.domain.tools import ToolSpec

log = get_logger(__name__)


class IntegrationGateway:
    """Điều phối một lời gọi tool từ đầu tới cuối."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        policy: PolicyEngine,
        credentials: CredentialProvider,
        connector: Connector,
        rate_limiter: RateLimiter,
        breaker: CircuitBreaker,
        idempotency: IdempotencyStore,
        trace: ToolInvocationSink,
        usage: UsageRecorder,
        sleep: Any = None,
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._credentials = credentials
        self._connector = connector
        self._rate_limiter = rate_limiter
        self._breaker = breaker
        self._idempotency = idempotency
        self._trace = trace
        self._usage = usage
        # Bơm vào được để test không phải chờ thật.
        self._sleep = sleep

    async def invoke(self, command: ToolInvocation) -> ToolResult:
        started = datetime.now(UTC)
        clock = time.perf_counter()

        tool = self._registry.get_tool(command.tool_id, command.tool_version)
        connector = self._registry.connector_for(tool)

        self._assert_tenant(command)
        self._validate_input(tool, command.input)
        await self._decide(tool, command)

        key = ensure_key_present(
            command.idempotency_key,
            tool_id=tool.tool_id,
            required=tool.idempotency.required,
        )

        replay = await self._claim(tool, command, key) if key else None
        if replay is not None:
            return await self._replayed(tool, connector, command, replay, started, clock)

        attempts = 0
        last_error: AppError | None = None

        try:
            for attempt in range(1, tool.retry.max_attempts + 1):
                attempts = attempt

                # Cổng nằm NGOÀI khối bắt lỗi retry. Mạch hở hay hết hạn
                # mức là quyết định "đừng gọi", không phải một lời gọi hỏng
                await self._gate(tool, connector, command)

                try:
                    response = await self._call(tool, connector, command)
                except AppError as error:
                    last_error = error
                    await self._breaker.record_failure(
                        connector=connector,
                        tenant_id=command.tenant_id,
                        connection_id=command.connection_id,
                        category=error.category,
                    )
                    await self._report_if_unusable(command, error)

                    if attempt >= tool.retry.max_attempts or not may_retry(
                        tool, error, idempotent=key is not None
                    ):
                        raise

                    await self._pause(wait_for(tool, attempt + 1, error))
                    continue

                await self._breaker.record_success(
                    connector=connector,
                    tenant_id=command.tenant_id,
                    connection_id=command.connection_id,
                )
                return await self._succeeded(
                    tool, connector, command, key, response, attempts, started, clock
                )

            # Vòng lặp chỉ kết thúc bằng `return` hoặc `raise`; tới đây là lỗi
            # lập trình, không phải trạng thái hợp lệ.
            raise InternalError("Vòng thử lại kết thúc mà không có kết quả.")

        except AppError as error:
            final = self._exhausted(tool, error, attempts, last_error)
            await self._failed(tool, connector, command, key, final, attempts, started, clock)
            raise final from (None if final is error else error)

    # ─── Các chặng ───────────────────────────────────────────────────────────

    def _assert_tenant(self, command: ToolInvocation) -> None:
        context = current_context()
        if context is not None and context.tenant_id not in (None, command.tenant_id):
            raise InternalError("Tenant của request không khớp tenant của lời gọi tool.")

    def _validate_input(self, tool: ToolSpec, payload: Mapping[str, Any]) -> None:
        """Trước cả phân quyền: input sai không bao giờ retryable, và không
        đáng tiêu một suất quota."""
        from app.core.errors import ValidationError

        try:
            jsonschema.validate(instance=dict(payload), schema=dict(tool.input_schema.json_schema))
        except jsonschema.ValidationError as exc:
            where = "/".join(str(part) for part in exc.absolute_path) or "<gốc>"
            raise ValidationError(
                f"Input của {tool.tool_id} sai tại {where}: {exc.message}",
                details={"tool_id": tool.tool_id, "path": where},
            ) from exc

    async def _decide(self, tool: ToolSpec, command: ToolInvocation) -> None:
        from app.core.errors import ApprovalRejectedError, AuthorizationError

        decision = await self._policy.decide(
            tool=tool,
            tenant_id=command.tenant_id,
            workspace_id=command.workspace_id,
            principal_id=command.acting_user_id,
            principal_kind=command.principal_kind,
            principal_roles=command.principal_roles,
            connection_id=command.connection_id,
            agent_id=command.caller.agent_id,
            workflow_id=command.caller.workflow_id,
            execution_id=command.caller.execution_id,
        )

        if decision.outcome is PolicyOutcome.ALLOW:
            return

        details = {"rule_id": decision.rule_id, "tool_id": tool.tool_id}
        if decision.outcome is PolicyOutcome.DENY:
            raise AuthorizationError(decision.detail, details=details)

        # Domain `approval` là Phase 4. Tới lúc đó chặng này chờ bền vững trên
        # Temporal; bây giờ nó từ chối, và nói rõ ai đáng lẽ phải ký.
        raise ApprovalRejectedError(
            f"{decision.detail} Luồng phê duyệt có từ Phase 4.",
            details={**details, "approval_scope": str(decision.approval_scope)},
        )

    async def _claim(
        self, tool: ToolSpec, command: ToolInvocation, key: str
    ) -> IdempotencyRecord | None:
        """Giành quyền chạy. Trả về bản ghi cũ nếu đã có ai làm xong."""
        now = datetime.now(UTC)
        claim = IdempotencyClaim(
            tenant_id=command.tenant_id,
            workspace_id=command.workspace_id,
            connection_id=command.connection_id,
            tool_id=tool.tool_id,
            tool_version=tool.version,
            key=key,
            request_fingerprint=fingerprint(command.input),
            expires_at=now + timedelta(seconds=tool.idempotency.ttl_seconds),
            at=now,
        )

        result = await self._idempotency.claim(claim)
        if result.fresh:
            return None

        ensure_same_operation(result.record, request_fingerprint=claim.request_fingerprint)
        return result.record if result.record.replayable else None

    async def _gate(
        self, tool: ToolSpec, connector: ConnectorSpec, command: ToolInvocation
    ) -> None:
        """Hai cổng chạy mỗi lượt thử, vì mỗi lượt là một lời gọi thật.
        Từ chối ở đây không phải một lời gọi hỏng, mà là một quyết định không gọi."""
        await self._breaker.check(
            connector=connector,
            tenant_id=command.tenant_id,
            connection_id=command.connection_id,
        )
        await self._rate_limiter.acquire(target=_target(command), tool=tool, connector=connector)

    async def _call(self, tool: ToolSpec, connector: ConnectorSpec, command: ToolInvocation) -> Any:
        credential = await self._credentials.resolve(
            connection_id=command.connection_id,
            tenant_id=command.tenant_id,
            workspace_id=command.workspace_id,
            acting_user_id=command.acting_user_id,
            required_scopes=tool.required_scopes,
        )

        return await self._connector.call(
            tool=tool, connector=connector, credential=credential, payload=command.input
        )

    async def _report_if_unusable(self, command: ToolInvocation, error: AppError) -> None:
        """Provider vừa nói token này chết. Báo ngược cho domain `connection`.

        Slack không có refresh token, nên đây là tín hiệu duy nhất khi app bị
        gỡ — thiếu nó thì hàng kết nối nằm `active` vĩnh viễn.
        """
        if error.category is not ErrorCategory.CONNECTION_EXPIRED:
            return
        await self._credentials.report_unusable(
            connection_id=command.connection_id,
            tenant_id=command.tenant_id,
            reason=str(error.details.get("provider_code", "provider_rejected_token")),
        )

    def _exhausted(
        self,
        tool: ToolSpec,
        error: AppError,
        attempts: int,
        last: AppError | None,
    ) -> AppError:
        """Hết lượt thử thì bọc lại thành `RetryExhaustedError`.

        `RETRY_EXHAUSTED` **không** nằm trong `RETRYABLE`, nên Temporal ở Phase
        4 sẽ không chồng thêm một vòng retry nữa lên trên vòng đã cạn.
        """
        if attempts <= 1 or not may_retry(tool, error, idempotent=True):
            return error

        # `RateLimited` giữ nguyên hình. Nó mang `Retry-After` — một chỉ dẫn
        # hành động được — còn `RetryExhausted` thì thành 502 và nói với người
        # gọi rằng provider hỏng, điều không đúng: provider vẫn khoẻ, mình chỉ
        # đang bị tiết lưu.
        if error.category is ErrorCategory.RATE_LIMITED:
            return error
        return RetryExhaustedError(
            f"{tool.tool_id} thất bại sau {attempts} lần thử.",
            details={"tool_id": tool.tool_id, "attempts": str(attempts)},
            cause=last or error,
        )

    # ─── Ba lối ra ───────────────────────────────────────────────────────────

    async def _succeeded(
        self,
        tool: ToolSpec,
        connector: ConnectorSpec,
        command: ToolInvocation,
        key: str | None,
        response: Any,
        attempts: int,
        started: datetime,
        clock: float,
    ) -> ToolResult:
        output: Mapping[str, Any] = response.output
        self._check_output(tool, output)

        if key is not None:
            await self._idempotency.complete(
                tenant_id=command.tenant_id,
                key=key,
                result=output,
                provider_reference=response.provider_reference,
                attempts=attempts,
                at=datetime.now(UTC),
            )

        return await self._finish(
            tool,
            connector,
            command,
            key,
            output=output,
            provider_reference=response.provider_reference,
            status="succeeded",
            attempts=attempts,
            started=started,
            clock=clock,
            error=None,
            replayed=False,
        )

    async def _replayed(
        self,
        tool: ToolSpec,
        connector: ConnectorSpec,
        command: ToolInvocation,
        record: IdempotencyRecord,
        started: datetime,
        clock: float,
    ) -> ToolResult:
        log.info(
            "tool_invocation_replayed",
            tool_id=tool.tool_id,
            idempotency_key=record.key,
            provider_reference=record.provider_reference,
        )
        return await self._finish(
            tool,
            connector,
            command,
            record.key,
            output=record.result or {},
            provider_reference=record.provider_reference,
            status="replayed",
            attempts=record.attempts,
            started=started,
            clock=clock,
            error=None,
            replayed=True,
        )

    async def _failed(
        self,
        tool: ToolSpec,
        connector: ConnectorSpec,
        command: ToolInvocation,
        key: str | None,
        error: AppError,
        attempts: int,
        started: datetime,
        clock: float,
    ) -> None:
        if key is not None:
            await self._idempotency.fail(
                tenant_id=command.tenant_id,
                key=key,
                category=error.category,
                attempts=attempts,
                at=datetime.now(UTC),
                detail=str(error),
            )

        await self._finish(
            tool,
            connector,
            command,
            key,
            output={},
            provider_reference=None,
            status="failed",
            attempts=attempts,
            started=started,
            clock=clock,
            error=error,
            replayed=False,
        )

    async def _finish(
        self,
        tool: ToolSpec,
        connector: ConnectorSpec,
        command: ToolInvocation,
        key: str | None,
        *,
        output: Mapping[str, Any],
        provider_reference: str | None,
        status: Any,
        attempts: int,
        started: datetime,
        clock: float,
        error: AppError | None,
        replayed: bool,
    ) -> ToolResult:
        """Phát trace và usage rồi trả kết quả. Chạy trên cả ba lối ra."""
        completed = datetime.now(UTC)
        duration_ms = (time.perf_counter() - clock) * 1000
        invocation_id = new_id(ResourceKind.TOOL_INVOCATION)
        trace_id, span_id = current_otel_ids()

        await self._trace.record(
            ToolInvocationRecord(
                invocation_id=invocation_id,
                tenant_id=command.tenant_id,
                workspace_id=command.workspace_id,
                connection_id=command.connection_id,
                provider=connector.connector_id,
                tool_id=tool.tool_id,
                tool_version=tool.version,
                input_schema_version=tool.input_schema.version,
                output_schema_version=tool.output_schema.version,
                input_ref=PayloadRef.of(command.input),
                output_ref=PayloadRef.of(output),
                started_at=started,
                completed_at=completed,
                status=status,
                attempt=attempts,
                side_effect=tool.side_effect,
                error_category=error.category.value if error else None,
                idempotency_key=key,
                otel_trace_id=trace_id,
                otel_span_id=span_id,
                execution_trace_id=command.caller.execution_trace_id,
                parent_step_id=command.caller.parent_step_id,
            )
        )

        await self._usage.record(
            ToolUsage(
                tenant_id=command.tenant_id,
                workspace_id=command.workspace_id,
                tool_id=tool.tool_id,
                provider=connector.connector_id,
                side_effect=tool.side_effect,
                attempts=attempts,
                duration_ms=duration_ms,
                at=completed,
            )
        )

        return ToolResult(
            invocation_id=invocation_id,
            tool_id=tool.tool_id,
            tool_version=tool.version,
            output=output,
            provider_reference=provider_reference,
            replayed=replayed,
            attempts=attempts,
            duration_ms=duration_ms,
        )

    # ─── Phụ ─────────────────────────────────────────────────────────────────

    def _check_output(self, tool: ToolSpec, output: Mapping[str, Any]) -> None:
        """Kiểm output nhưng **chỉ log** khi lệch. không ném lỗi"""
        try:
            jsonschema.validate(instance=dict(output), schema=dict(tool.output_schema.json_schema))
        except jsonschema.ValidationError as exc:
            log.warning(
                "tool_output_lech_schema",
                tool_id=tool.tool_id,
                output_schema_version=tool.output_schema.version,
                problem=exc.message,
            )

    async def _pause(self, seconds: float) -> None:
        if seconds <= 0:
            return
        if self._sleep is not None:
            await self._sleep(seconds)
            return
        import asyncio

        await asyncio.sleep(seconds)


def _target(command: ToolInvocation) -> RateLimitTarget:
    return RateLimitTarget(
        tenant_id=command.tenant_id,
        workspace_id=command.workspace_id,
        user_id=command.acting_user_id,
        connection_id=command.connection_id,
    )
