"""Payload của bước trace: nhỏ thì nằm trong hàng, lớn thì ra object storage."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.objectstore import ObjectStoreError, get_object, put_object
from app.domains.trace.domain.entities import (
    PayloadLocation,
    PayloadRef,
    StoredPayload,
    canonical_bytes,
)

log = get_logger(__name__)


def object_key(*, tenant_id: str, sha256: str) -> str:
    """Khoá đánh theo nội dung, dưới tiền tố của tenant (§19)."""
    return f"{tenant_id}/payloads/{sha256[:2]}/{sha256}.json"


class ObjectPayloadStore:
    """Hiện thực `PayloadStore` trên object storage."""

    __slots__ = ("_bucket", "_inline_max_bytes", "_max_bytes")

    def __init__(self, *, bucket: str, inline_max_bytes: int, max_bytes: int) -> None:
        self._bucket = bucket
        self._inline_max_bytes = inline_max_bytes
        self._max_bytes = max_bytes

    async def put(self, *, tenant_id: str, payload: Mapping[str, Any]) -> StoredPayload:
        encoded = canonical_bytes(payload)
        ref = PayloadRef.of_bytes(encoded)

        if ref.byte_size <= self._inline_max_bytes:
            return StoredPayload(ref=ref, location=PayloadLocation.INLINE, inline=payload)

        if ref.byte_size > self._max_bytes:
            log.warning(
                "trace_payload_vuot_tran",
                byte_size=ref.byte_size,
                max_bytes=self._max_bytes,
                sha256=ref.sha256,
            )
            return StoredPayload.absent(ref)

        try:
            await put_object(
                bucket=self._bucket,
                key=object_key(tenant_id=tenant_id, sha256=ref.sha256),
                body=encoded,
            )
        except (ObjectStoreError, RuntimeError) as exc:
            # Ghi trace là quan sát. Một lời gọi tool đã chạy xong không được
            # báo lỗi chỉ vì chỗ cất payload của nó đang hỏng — kể cả khi cái
            # hỏng là quên `init_object_store()` lúc khởi động (RuntimeError).
            log.warning(
                "trace_payload_khong_cat_duoc",
                sha256=ref.sha256,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return StoredPayload.absent(ref)

        return StoredPayload(ref=ref, location=PayloadLocation.OBJECT)

    async def get(self, *, tenant_id: str, stored: StoredPayload) -> Mapping[str, Any] | None:
        if stored.location is PayloadLocation.INLINE:
            return stored.inline
        if stored.location is not PayloadLocation.OBJECT:
            return None

        raw = await get_object(
            bucket=self._bucket,
            key=object_key(tenant_id=tenant_id, sha256=stored.ref.sha256),
        )
        if raw is None:
            # Lifecycle rule của bucket đã dọn, hoặc payload chưa từng tới nơi.
            return None

        loaded = json.loads(raw)
        return loaded if isinstance(loaded, dict) else None


def build_payload_store(settings: Settings) -> ObjectPayloadStore:
    """Một chỗ đọc cấu hình, cho cả ba nơi dựng `TraceService`."""
    return ObjectPayloadStore(
        bucket=settings.s3_bucket_traces,
        inline_max_bytes=settings.trace_payload_inline_max_bytes,
        max_bytes=settings.trace_payload_max_bytes,
    )
