"""Cấu hình chung cho test.

Ép môi trường test về giá trị xác định, không phụ thuộc file ``.env`` trên máy
người chạy — nếu không, test sẽ đỗ hay trượt tuỳ theo cấu hình cục bộ.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, NoReturn

import pytest

from app.core.errors import ErrorCategory
from app.domains.integration.domain.connectors import AuthSpec, ConnectorSpec
from app.domains.integration.domain.tools import (
    ApprovalPolicy,
    FieldMapping,
    IdempotencyPolicy,
    PayloadSchema,
    ProviderRequest,
    RetryPolicy,
    SideEffect,
    TimeoutPolicy,
    ToolSpec,
)
from app.domains.trace.domain.entities import PayloadLocation, PayloadRef, StoredPayload

os.environ.setdefault("AW_ENVIRONMENT", "local")
os.environ.setdefault("AW_DEBUG", "false")
os.environ.setdefault("AW_OTEL_ENABLED", "false")
os.environ.setdefault("AW_LOG_JSON", "false")
os.environ.setdefault("AW_LOG_LEVEL", "WARNING")


#: Ở CI, hạ tầng luôn có sẵn, nên "thiếu hạ tầng" ở đó không phải lý do chính
#: đáng để bỏ qua — nó là một lỗi cấu hình pipeline.
_INFRA_REQUIRED = os.getenv("AW_TEST_REQUIRE_INFRA") == "1"


def infra_unavailable(reason: str) -> NoReturn:
    """Bỏ qua test vì thiếu hạ tầng — trừ khi hạ tầng được khai là bắt buộc.

    Trên máy cá nhân, chạy ``pytest`` khi chưa ``docker compose up`` thì bỏ qua
    là hành vi đúng.

    Trên CI thì không. Một lần bỏ qua ở đó nghĩa là cả nhóm test integration
    không chạy mà pipeline vẫn xanh — trạng thái nguy hiểm hơn hẳn một lần đỏ,
    vì nó trông y hệt như thành công. CI đặt ``AW_TEST_REQUIRE_INFRA=1`` để
    biến mọi lần bỏ qua kiểu này thành thất bại.

    Chỉ dùng cho hạ tầng thật sự vắng mặt. Đừng dùng để nuốt lỗi của ứng dụng —
    lỗi ứng dụng phải làm test đỏ ở mọi nơi.
    """
    if _INFRA_REQUIRED:
        pytest.fail(f"AW_TEST_REQUIRE_INFRA=1 nhưng {reason}")
    pytest.skip(reason)


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    """Xoá cache của ``get_settings`` giữa các test.

    ``get_settings`` dùng ``lru_cache``, nên test nào sửa biến môi trường sẽ
    ảnh hưởng sang test sau nếu không xoá.
    """
    from app.core.config import get_settings

    get_settings.cache_clear()


# ─── Builder dùng chung cho domain `integration` ─────────────────────────────
#
# Để ở đây chứ không ở một module riêng: ``tests/`` không phải package, nên một
# module test được nạp dưới hai tên và mypy từ chối kiểm tiếp. ``conftest`` đã
# là tên duy nhất mà cả pytest lẫn mypy đồng ý — bốn file test hiện có đã import
# ``infra_unavailable`` theo đúng đường này.


def make_tool(
    tool_id: str = "demo.ping",
    *,
    version: str = "1.0",
    provider: str = "demo",
    side_effect: SideEffect = SideEffect.READ,
    enabled: bool = True,
    permissions: frozenset[str] = frozenset(),
    approval: ApprovalPolicy | None = None,
    idempotency: IdempotencyPolicy | None = None,
    required_scopes: frozenset[str] = frozenset(),
) -> ToolSpec:
    empty = PayloadSchema(version="1.0", json_schema={"type": "object"})
    return ToolSpec(
        tool_id=tool_id,
        provider=provider,
        version=version,
        description="Một tool để test, không gọi ra ngoài.",
        input_schema=empty,
        output_schema=empty,
        required_scopes=required_scopes,
        side_effect=side_effect,
        retry=RetryPolicy(),
        timeout=TimeoutPolicy(),
        idempotency=idempotency or IdempotencyPolicy(),
        approval=approval or ApprovalPolicy(),
        request=ProviderRequest(method="GET", path="/ping", body="query"),
        output_mapping={"ok": FieldMapping(path=("ok",))},
        permissions=permissions,
        enabled=enabled,
    )


def make_connector(
    connector_id: str = "demo",
    *,
    tool_ids: tuple[str, ...] = ("demo.ping",),
) -> ConnectorSpec:
    return ConnectorSpec(
        connector_id=connector_id,
        version="1.0",
        label=connector_id.title(),
        base_url="https://example.test/api",
        auth=AuthSpec(kind="oauth2", header="authorization", prefix="Bearer "),
        tool_ids=tool_ids,
        error_mapping={"boom": ErrorCategory.PROVIDER_UNAVAILABLE},
    )


class InMemoryPayloadStore:
    """`PayloadStore` giả: cất tất cả inline, và nhớ đã cất những gì.

    Ngưỡng inline và object storage là chuyện của `ObjectPayloadStore` thật,
    kiểm riêng ở `test_trace_payload_store.py`. Ở đây cái cần kiểm là
    `TraceService` có gọi cất đúng payload nào không.
    """

    def __init__(self) -> None:
        self.puts: list[tuple[str, Mapping[str, Any]]] = []

    async def put(self, *, tenant_id: str, payload: Mapping[str, Any]) -> StoredPayload:
        self.puts.append((tenant_id, payload))
        return StoredPayload(
            ref=PayloadRef.of(payload),
            location=PayloadLocation.INLINE,
            inline=payload,
        )

    async def get(self, *, tenant_id: str, stored: StoredPayload) -> Mapping[str, Any] | None:
        return stored.inline
