"""Driver HTTP chung cho mọi connector."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

import httpx

from app.core.errors import (
    AppError,
    AuthenticationError,
    AuthorizationError,
    ConnectionExpiredError,
    ErrorCategory,
    MissingScopeError,
    PlatformTimeoutError,
    ProviderPermanentError,
    ProviderUnavailableError,
    RateLimitedError,
    ValidationError,
)
from app.core.logging import get_logger
from app.domains.integration.application.ports import ProviderCredential
from app.domains.integration.domain.connectors import ConnectorSpec
from app.domains.integration.domain.projection import build_reference, project
from app.domains.integration.domain.template import render_body
from app.domains.integration.domain.tools import ToolSpec, fill_path

log = get_logger(__name__)

#: Mặt còn lại của `ConnectorSpec.error_mapping`: tài liệu nói mã của provider
#: thuộc phân loại nào, bảng này nói phân loại nào thành ngoại lệ nào (§37).
_EXCEPTIONS: Final[dict[ErrorCategory, type[AppError]]] = {
    ErrorCategory.AUTHENTICATION: AuthenticationError,
    ErrorCategory.AUTHORIZATION: AuthorizationError,
    ErrorCategory.CONNECTION_EXPIRED: ConnectionExpiredError,
    ErrorCategory.MISSING_SCOPE: MissingScopeError,
    ErrorCategory.VALIDATION: ValidationError,
    ErrorCategory.RATE_LIMITED: RateLimitedError,
    ErrorCategory.PROVIDER_UNAVAILABLE: ProviderUnavailableError,
    ErrorCategory.PROVIDER_PERMANENT: ProviderPermanentError,
    ErrorCategory.TIMEOUT: PlatformTimeoutError,
}


@dataclass(frozen=True, slots=True)
class HttpConnectorResponse:
    output: Mapping[str, Any] = field(default_factory=dict)
    provider_reference: str | None = None
    retry_after_seconds: float | None = None


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        # `Retry-After` cho phép cả dạng HTTP-date. Không parse được thì bỏ qua
        # — backoff tính toán của tool vẫn còn đó.
        return None


class HttpConnector:
    """Hiện thực `Connector` cho mọi provider nói HTTP + JSON."""

    __slots__ = ()

    async def call(
        self,
        *,
        tool: ToolSpec,
        connector: ConnectorSpec,
        credential: ProviderCredential,
        payload: Mapping[str, Any],
    ) -> HttpConnectorResponse:
        request = self._build(tool, connector, credential, payload)

        try:
            async with httpx.AsyncClient(timeout=tool.timeout.attempt_seconds) as client:
                response = await client.request(**request)
        except httpx.TimeoutException as exc:
            raise PlatformTimeoutError(
                f"{connector.label} không trả lời trong {tool.timeout.attempt_seconds}s.",
                cause=exc,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"Không gọi được {connector.label}.", cause=exc) from exc

        return self._interpret(tool, connector, response)

    # ─── Dựng request ────────────────────────────────────────────────────────

    def _build(
        self,
        tool: ToolSpec,
        connector: ConnectorSpec,
        credential: ProviderCredential,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Khung địa chỉ đến từ tài liệu đã commit; người gọi chỉ điền nội dung.

        `fill_path` percent-encode mọi giá trị điền vào, kể cả `/`, nên một
        tham số không thoát ra khỏi đúng một segment: host đến từ `base_url`,
        cấu trúc đường dẫn đến từ `request.path`, và không input nào đổi được
        hai thứ đó. Đó là ranh giới SSRF sau ADR-0005 (§38).
        """
        # Header cố định trước, credential sau. Thứ tự đó là một ràng buộc chứ
        # không phải thẩm mỹ: một tài liệu connector khai nhầm — hay bị sửa ác
        # ý — trùng tên với `auth.header` sẽ bị ghi đè, không thay được token.
        request: dict[str, Any] = {
            "method": tool.request.method,
            "url": connector.endpoint(fill_path(tool.request.path, self._segments(tool, payload))),
            "headers": {
                **connector.static_headers,
                connector.auth.header: f"{connector.auth.prefix}{credential.access_token}",
            },
        }

        # Tham số đã đi vào đường dẫn thì không đi thêm lần nữa vào query hay
        # thân: provider sẽ coi đó là một trường lạ và từ chối cả lời gọi.
        body: dict[str, Any] = {
            k: v for k, v in payload.items() if k not in tool.request.path_params
        }
        if tool.input_mapping is not None:
            body = render_body(tool.input_mapping, body)

        if tool.request.body == "query":
            request["params"] = body
        elif tool.request.body == "form":
            request["data"] = body
        else:
            request["json"] = body

        return request

    def _segments(self, tool: ToolSpec, payload: Mapping[str, Any]) -> dict[str, str]:
        """Giá trị cho từng chỗ điền, đã kiểm là chuỗi dùng được.

        `input_schema` đã bắt buộc và đã hẹp những trường này, nên tới đây mà
        vẫn thiếu thì tài liệu tool sai chứ không phải người gọi sai — nhưng
        `ValidationError` vẫn đúng hơn `KeyError`, vì nó nói được thiếu cái gì.
        """
        segments: dict[str, str] = {}
        for name in tool.request.path_params:
            value = payload.get(name)
            if not isinstance(value, str) or not value:
                raise ValidationError(
                    f"{tool.tool_id} cần {name!r} là một chuỗi không rỗng.",
                    details={"tool_id": tool.tool_id, "field": name},
                )
            segments[name] = value
        return segments

    # ─── Đọc phản hồi ────────────────────────────────────────────────────────

    def _interpret(
        self, tool: ToolSpec, connector: ConnectorSpec, response: httpx.Response
    ) -> HttpConnectorResponse:
        retry_after = _retry_after(response)

        # 429 xét trước vì nó mang `Retry-After` — con số của provider luôn tốt
        # hơn backoff mình tự tính.
        if response.status_code == 429:
            raise RateLimitedError(
                f"{connector.label} đang tiết lưu lời gọi.",
                retry_after_seconds=retry_after,
                details={"tool_id": tool.tool_id},
            )

        if response.status_code >= 500:
            raise ProviderUnavailableError(
                f"{connector.label} trả về {response.status_code}.",
                retry_after_seconds=retry_after,
                details={"tool_id": tool.tool_id},
            )

        body = self._parse(connector, response)

        # Thân phản hồi đọc TRƯỚC phần mã HTTP còn lại: với provider có envelope,
        # một mã 200 tự nó chưa nói lên điều gì.
        if connector.response_envelope is not None:
            code = connector.response_envelope.failure_code(body)
            if code is not None:
                self._raise_mapped(tool, connector, code, retry_after)

        if response.status_code >= 400:
            self._raise_mapped(tool, connector, str(response.status_code), retry_after)

        return HttpConnectorResponse(
            output=project(body, tool.output_mapping),
            provider_reference=build_reference(body, tool.provider_reference),
            retry_after_seconds=retry_after,
        )

    def _parse(self, connector: ConnectorSpec, response: httpx.Response) -> dict[str, Any]:
        try:
            parsed = response.json()
        except ValueError as exc:
            raise ProviderUnavailableError(
                f"{connector.label} trả về thứ không phải JSON.", cause=exc
            ) from exc
        if not isinstance(parsed, dict):
            raise ProviderUnavailableError(f"{connector.label} trả về JSON không phải object.")
        return parsed

    def _raise_mapped(
        self,
        tool: ToolSpec,
        connector: ConnectorSpec,
        code: str,
        retry_after: float | None,
    ) -> None:
        category = connector.category_for(code)

        log.info(
            "integration_provider_error",
            provider=connector.connector_id,
            tool_id=tool.tool_id,
            provider_code=code,
            category=category.value,
        )

        raise _EXCEPTIONS[category](
            f"{connector.label} từ chối: {code}.",
            retry_after_seconds=retry_after if category is ErrorCategory.RATE_LIMITED else None,
            details={"provider_code": code, "tool_id": tool.tool_id},
        )
