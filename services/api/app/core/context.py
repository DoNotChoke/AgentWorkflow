"""Tenant context lan truyền xuyên suốt request, event, log và span."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Literal

PrincipalKind = Literal["user", "service_account", "workflow", "system"]


@dataclass(frozen=True, slots=True)
class Principal:
    """Danh tính đang thực hiện hành động."""

    kind: PrincipalKind
    id: str
    display_name: str | None = None
    roles: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Context bất biến gắn với một đơn vị công việc."""

    request_id: str
    correlation_id: str
    tenant_id: str | None = None
    workspace_id: str | None = None
    principal: Principal | None = None

    execution_id: str | None = None
    execution_trace_id: str | None = None
    workflow_id: str | None = None
    workflow_version_id: str | None = None

    def as_log_fields(self) -> dict[str, str]:
        """Các trường được đính vào mọi dòng log và mọi span."""
        fields: dict[str, str] = {
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
        }
        optional = {
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "execution_id": self.execution_id,
            "execution_trace_id": self.execution_trace_id,
            "workflow_id": self.workflow_id,
            "workflow_version_id": self.workflow_version_id,
        }
        fields.update({k: v for k, v in optional.items() if v is not None})
        if self.principal is not None:
            fields["principal_kind"] = self.principal.kind
            fields["principal_id"] = self.principal.id
        return fields


_ctx: ContextVar[RequestContext | None] = ContextVar("aw_request_context", default=None)


def new_request_context(
    *,
    correlation_id: str | None = None,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    principal: Principal | None = None,
) -> RequestContext:
    request_id = f"req_{uuid.uuid4().hex}"
    return RequestContext(
        request_id=request_id,
        correlation_id=correlation_id or request_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        principal=principal,
    )


def current_context() -> RequestContext | None:
    return _ctx.get()


def set_context(ctx: RequestContext) -> Token[RequestContext | None]:
    return _ctx.set(ctx)


def reset_context(token: Token[RequestContext | None]) -> None:
    _ctx.reset(token)


@contextmanager
def use_context(ctx: RequestContext) -> Iterator[RequestContext]:
    token = set_context(ctx)
    try:
        yield ctx
    finally:
        reset_context(token)


def require_tenant_id() -> str:
    """Trả về tenant_id hiện tại, ném lỗi nếu không có."""
    ctx = _ctx.get()
    if ctx is None or ctx.tenant_id is None:
        raise MissingTenantContextError(
            "Không có tenant context. Mọi thao tác trên tài nguyên thuộc tenant "
            "phải chạy bên trong một RequestContext đã có tenant_id (§19)."
        )
    return ctx.tenant_id


def require_context() -> RequestContext:
    ctx = _ctx.get()
    if ctx is None:
        raise MissingTenantContextError("Không có RequestContext cho đơn vị công việc hiện tại.")
    return ctx


class MissingTenantContextError(RuntimeError):
    """Thiếu tenant context ở nơi bắt buộc phải có."""


def with_execution(
    ctx: RequestContext,
    *,
    execution_id: str | None = None,
    execution_trace_id: str | None = None,
    workflow_id: str | None = None,
    workflow_version_id: str | None = None,
) -> RequestContext:
    """Tạo context mới bổ sung định danh nghiệp vụ, giữ nguyên phần còn lại.

    ``execution_trace_id`` là trace ở tầng sản phẩm (§9). Nó KHÁC với OTel
    trace id — hai thứ tồn tại song song và chỉ correlate với nhau (§2.2).
    """
    from dataclasses import replace

    return replace(
        ctx,
        execution_id=execution_id if execution_id is not None else ctx.execution_id,
        execution_trace_id=(
            execution_trace_id if execution_trace_id is not None else ctx.execution_trace_id
        ),
        workflow_id=workflow_id if workflow_id is not None else ctx.workflow_id,
        workflow_version_id=(
            workflow_version_id if workflow_version_id is not None else ctx.workflow_version_id
        ),
    )


def tenant_cache_key(*parts: str) -> str:
    """Sinh Redis key có tiền tố tenant."""
    return ":".join(("aw", "t", require_tenant_id(), *parts))


def tenant_storage_prefix(*parts: str) -> str:
    """Sinh đường dẫn object storage có tiền tố tenant."""
    return "/".join(("tenants", require_tenant_id(), *parts))
