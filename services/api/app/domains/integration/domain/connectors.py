"""Connector"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from app.core.errors import ErrorCategory
from app.domains.integration.domain.tools import RateLimitProfile

AuthKind = Literal["oauth2"]

#: Mã lỗi không có trong bảng của connector rơi về đây.
UNMAPPED_ERROR_CATEGORY = ErrorCategory.PROVIDER_PERMANENT


@dataclass(frozen=True, slots=True)
class AuthSpec:
    kind: AuthKind
    header: str
    prefix: str


@dataclass(frozen=True, slots=True)
class CircuitBreakerSpec:
    """Chỉ lỗi thuộc nhóm retryable mới làm tăng bộ đếm."""

    failure_threshold: int = 5
    global_failure_threshold: int = 20
    window_seconds: int = 60
    open_seconds: int = 30
    probe_timeout_seconds: int = 120


@dataclass(frozen=True, slots=True)
class WebhookCapabilities:
    supported: bool = False
    signature_header: str | None = None
    timestamp_header: str | None = None


@dataclass(frozen=True, slots=True)
class EventCapabilities:
    supported: bool = False
    event_types: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ConnectorSpec:
    connector_id: str
    version: str
    label: str
    base_url: str
    auth: AuthSpec
    tool_ids: tuple[str, ...]
    error_mapping: Mapping[str, ErrorCategory]
    webhooks: WebhookCapabilities = field(default_factory=WebhookCapabilities)
    events: EventCapabilities = field(default_factory=EventCapabilities)
    default_rate_limits: RateLimitProfile = field(default_factory=RateLimitProfile)
    circuit_breaker: CircuitBreakerSpec = field(default_factory=CircuitBreakerSpec)
    notes: str = ""

    def category_for(self, provider_code: str) -> ErrorCategory:
        """Chuẩn hoá một mã lỗi của provider thành phân loại nền tảng."""
        return self.error_mapping.get(provider_code.strip().lower(), UNMAPPED_ERROR_CATEGORY)

    def endpoint(self, path: str) -> str:
        """Nối `base_url` với `path` của tool."""
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
