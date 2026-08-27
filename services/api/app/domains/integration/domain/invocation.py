"""Một lời gọi tool: yêu cầu, kết quả, và bản ghi để lại."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from app.core.context import PrincipalKind
from app.domains.integration.domain.tools import SideEffect


@dataclass(frozen=True, slots=True)
class CallerRef:
    """Ai khởi xướng"""

    agent_id: str | None = None
    workflow_id: str | None = None
    execution_id: str | None = None
    node_id: str | None = None
    execution_trace_id: str | None = None
    parent_step_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    tool_id: str
    connection_id: str
    tenant_id: str
    workspace_id: str
    acting_user_id: str
    principal_kind: PrincipalKind
    principal_roles: frozenset[str]
    input: Mapping[str, Any]
    tool_version: str | None = None
    idempotency_key: str | None = None
    caller: CallerRef = field(default_factory=CallerRef)


@dataclass(frozen=True, slots=True)
class RateLimitTarget:
    """Bốn trục định danh của một lời gọi, đủ để dựng khoá cho cả sáu cấp."""

    tenant_id: str
    workspace_id: str
    user_id: str
    connection_id: str


@dataclass(frozen=True, slots=True)
class ToolResult:
    invocation_id: str
    tool_id: str
    tool_version: str
    output: Mapping[str, Any]
    provider_reference: str | None
    replayed: bool
    attempts: int
    duration_ms: float


@dataclass(frozen=True, slots=True)
class PayloadRef:
    """Vân tay của payload, không phải payload."""

    sha256: str
    byte_size: int

    @classmethod
    def of(cls, payload: Mapping[str, Any]) -> PayloadRef:
        import json

        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        encoded = raw.encode("utf-8")
        return cls(sha256=hashlib.sha256(encoded).hexdigest(), byte_size=len(encoded))


@dataclass(frozen=True, slots=True)
class ToolInvocationRecord:
    """Hình dạng đầy đủ. Domain Trace sở hữu dữ liệu này."""

    invocation_id: str
    tenant_id: str
    workspace_id: str
    connection_id: str
    provider: str
    tool_id: str
    tool_version: str
    input_schema_version: str
    output_schema_version: str
    input_ref: PayloadRef
    output_ref: PayloadRef
    started_at: datetime
    completed_at: datetime
    status: Literal["succeeded", "failed", "replayed"]
    attempt: int
    side_effect: SideEffect
    error_category: str | None = None
    idempotency_key: str | None = None
    otel_trace_id: str | None = None
    otel_span_id: str | None = None
    execution_trace_id: str | None = None
    parent_step_id: str | None = None

    @property
    def duration_ms(self) -> float:
        return (self.completed_at - self.started_at).total_seconds() * 1000


@dataclass(frozen=True, slots=True)
class ToolUsage:
    tenant_id: str
    workspace_id: str
    tool_id: str
    provider: str
    side_effect: SideEffect
    attempts: int
    duration_ms: float
    at: datetime
