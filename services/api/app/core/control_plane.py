"""HTTP + chuẩn hoá lỗi dùng chung cho mọi tiến trình gọi Control Plane: Agent Runtime
và Temporal worker."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

import httpx

from app.core.errors import (
    AppError,
    ApprovalRejectedError,
    AuthenticationError,
    AuthorizationError,
    ConnectionExpiredError,
    ErrorCategory,
    InternalError,
    InvocationInProgressError,
    MissingScopeError,
    OutcomeUnknownError,
    PlatformTimeoutError,
    ProviderPermanentError,
    ProviderUnavailableError,
    RateLimitedError,
    RetryExhaustedError,
    ValidationError,
    WorkflowDefinitionError,
    WorkflowExecutionError,
)

_EXCEPTIONS: Final[dict[str, type[AppError]]] = {
    ErrorCategory.AUTHENTICATION.value: AuthenticationError,
    ErrorCategory.AUTHORIZATION.value: AuthorizationError,
    ErrorCategory.CONNECTION_EXPIRED.value: ConnectionExpiredError,
    ErrorCategory.MISSING_SCOPE.value: MissingScopeError,
    ErrorCategory.VALIDATION.value: ValidationError,
    ErrorCategory.RATE_LIMITED.value: RateLimitedError,
    ErrorCategory.PROVIDER_UNAVAILABLE.value: ProviderUnavailableError,
    ErrorCategory.PROVIDER_PERMANENT.value: ProviderPermanentError,
    ErrorCategory.TIMEOUT.value: PlatformTimeoutError,
    ErrorCategory.RETRY_EXHAUSTED.value: RetryExhaustedError,
    ErrorCategory.APPROVAL_REJECTED.value: ApprovalRejectedError,
    ErrorCategory.WORKFLOW_DEFINITION.value: WorkflowDefinitionError,
    ErrorCategory.WORKFLOW_EXECUTION.value: WorkflowExecutionError,
    ErrorCategory.OUTCOME_UNKNOWN.value: OutcomeUnknownError,
    ErrorCategory.INVOCATION_IN_PROGRESS.value: InvocationInProgressError,
    ErrorCategory.INTERNAL.value: InternalError,
}


def _headers(
    *, authorization: str, organization_id: str | None, workspace_id: str | None
) -> dict[str, str]:
    headers = {"authorization": authorization}
    if organization_id:
        headers["x-organization-id"] = organization_id
    if workspace_id:
        headers["x-workspace-id"] = workspace_id
    return headers


def map_error(response: httpx.Response) -> AppError:
    try:
        error = response.json()["error"]
        category = str(error["category"])
        message = str(error["message"])
    except (ValueError, KeyError, TypeError):
        # Không phải hình dạng lỗi do chính app phát ra (proxy, 502 hạ tầng...)
        # — rơi về phân loại thô theo mã HTTP.
        if response.status_code >= 500:
            return ProviderUnavailableError(f"Control Plane trả về {response.status_code}.")
        return InternalError(f"Control Plane trả về {response.status_code} không rõ nguyên nhân.")

    exception_cls = _EXCEPTIONS.get(category, InternalError)
    return exception_cls(
        message,
        details=dict(error.get("details") or {}),
        retry_after_seconds=error.get("retry_after_seconds"),
    )


async def request_json(
    base_url: str,
    method: str,
    path: str,
    *,
    authorization: str,
    organization_id: str | None,
    workspace_id: str | None,
    timeout_seconds: float = 30.0,
    json_body: Mapping[str, Any] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Any:
    """Gọi một endpoint của Control Plane, trả về JSON đã parse.

    Ném `AppError` đã chuẩn hoá (§37) cho mọi lối hỏng — timeout, mất kết nối,
    hoặc phản hồi lỗi từ chính app.
    """
    headers = _headers(
        authorization=authorization, organization_id=organization_id, workspace_id=workspace_id
    )
    try:
        async with httpx.AsyncClient(
            base_url=base_url, timeout=timeout_seconds, transport=transport
        ) as client:
            response = await client.request(method, path, headers=headers, json=json_body)
    except httpx.TimeoutException as exc:
        raise PlatformTimeoutError(
            f"Control Plane không phản hồi trong {timeout_seconds}s.", cause=exc
        ) from exc
    except httpx.HTTPError as exc:
        raise ProviderUnavailableError("Không gọi được Control Plane.", cause=exc) from exc

    if response.status_code >= 400:
        raise map_error(response)

    result: Any = response.json()
    return result
