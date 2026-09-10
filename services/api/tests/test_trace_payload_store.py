"""Payload của bước trace: chọn chỗ cất, và tìm lại được thứ đã cất.

Ba nhánh quyết định (inline, object, vượt trần) kiểm được mà không cần hạ tầng
— chúng chỉ so kích thước. Nhánh `object` thì cần MinIO thật và mang mark
``integration``: cái đáng kiểm ở đó là byte đi ra rồi quay về đúng nguyên vẹn,
mà một bản giả trong bộ nhớ luôn làm được điều ấy.

    docker compose up -d minio
    pytest -m integration
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from conftest import infra_unavailable

from app.core.config import Settings
from app.core.objectstore import (
    close_object_store,
    get_object,
    init_object_store,
    object_store_healthy,
)
from app.domains.trace.domain.entities import PayloadLocation, PayloadRef, StoredPayload
from app.domains.trace.infrastructure.payload_store import (
    ObjectPayloadStore,
    build_payload_store,
    object_key,
)

TENANT = "org_test"

#: Ngưỡng nhỏ để test không phải dựng payload hàng chục KiB.
INLINE_MAX = 32
HARD_MAX = 4096


def make_store(bucket: str = "aw-traces") -> ObjectPayloadStore:
    return ObjectPayloadStore(bucket=bucket, inline_max_bytes=INLINE_MAX, max_bytes=HARD_MAX)


#: `{"v":"…"}` — phần khung quanh giá trị.
_OVERHEAD = 8


def payload_of(byte_size: int) -> dict[str, str]:
    """Một payload dài đúng `byte_size` byte sau khi serialize."""
    return {"v": "x" * (byte_size - _OVERHEAD)}


@pytest.fixture
async def bucket() -> AsyncIterator[str]:
    settings = Settings()
    init_object_store(settings)
    if not await object_store_healthy(bucket=settings.s3_bucket_traces):
        await close_object_store()
        infra_unavailable("chưa kết nối được MinIO: docker compose up -d minio")
    yield settings.s3_bucket_traces
    await close_object_store()


# ─── Khoá ────────────────────────────────────────────────────────────────────


def test_khoa_nam_duoi_tien_to_cua_tenant() -> None:
    """Tiền tố tenant là ranh giới §19 ở tầng lưu trữ."""
    sha = "abc123" + "0" * 58

    assert object_key(tenant_id="org_a", sha256=sha) == f"org_a/payloads/ab/{sha}.json"


def test_hai_tenant_co_cung_payload_van_la_hai_doi_tuong() -> None:
    """Khử trùng lặp chỉ được xảy ra TRONG một tenant, không xuyên qua."""
    sha = "a" * 64

    assert object_key(tenant_id="org_a", sha256=sha) != object_key(tenant_id="org_b", sha256=sha)


# ─── Chọn chỗ cất (không cần MinIO) ──────────────────────────────────────────


async def test_payload_nho_nam_luon_trong_hang() -> None:
    payload = {"channel": "C123"}

    stored = await make_store().put(tenant_id=TENANT, payload=payload)

    assert stored.location is PayloadLocation.INLINE
    assert stored.inline == payload
    assert stored.ref == PayloadRef.of(payload)


async def test_payload_vuot_tran_chi_con_van_tay() -> None:
    """Một phản hồi khổng lồ không được biến bucket trace thành hoá đơn."""
    payload = payload_of(HARD_MAX + 1)

    stored = await make_store().put(tenant_id=TENANT, payload=payload)

    assert stored.location is PayloadLocation.ABSENT
    assert stored.inline is None
    assert stored.ref.byte_size > HARD_MAX


async def test_luu_tru_hong_thi_ha_ve_absent_chu_khong_nem_loi() -> None:
    """Ghi trace là quan sát.

    Ở đây object store còn chưa được khởi tạo — đúng hình dạng của một tiến
    trình quên `init_object_store()`. Một lời gọi tool đã chạy xong không được
    báo lỗi vì chuyện đó.
    """
    await close_object_store()

    stored = await make_store().put(tenant_id=TENANT, payload=payload_of(INLINE_MAX + 10))

    assert stored.location is PayloadLocation.ABSENT
    assert stored.ref.byte_size > INLINE_MAX


async def test_doc_lai_payload_inline_khong_cham_luu_tru() -> None:
    await close_object_store()
    payload = {"ts": "1700000000.000100"}
    stored = StoredPayload(
        ref=PayloadRef.of(payload), location=PayloadLocation.INLINE, inline=payload
    )

    assert await make_store().get(tenant_id=TENANT, stored=stored) == payload


async def test_doc_lai_payload_absent_tra_none() -> None:
    await close_object_store()
    stored = StoredPayload.absent(PayloadRef.of({"a": 1}))

    assert await make_store().get(tenant_id=TENANT, stored=stored) is None


# ─── Vòng cất–đọc thật (cần MinIO) ───────────────────────────────────────────


@pytest.mark.integration
async def test_payload_lon_ra_object_storage_va_doc_lai_duoc(bucket: str) -> None:
    payload = {"blocks": [{"text": "x" * 200}], "id": uuid.uuid4().hex}
    store = make_store(bucket)

    stored = await store.put(tenant_id=TENANT, payload=payload)

    assert stored.location is PayloadLocation.OBJECT
    assert stored.inline is None
    assert await store.get(tenant_id=TENANT, stored=stored) == payload


@pytest.mark.integration
async def test_van_tay_khop_dung_byte_da_ghi(bucket: str) -> None:
    """Băm một chuỗi rồi ghi ra một chuỗi khác thì vân tay vô dụng."""
    payload = {"note": "tiếng Việt có dấu", "n": 1, "id": uuid.uuid4().hex, "pad": "y" * 200}
    store = make_store(bucket)

    stored = await store.put(tenant_id=TENANT, payload=payload)
    raw = await get_object(
        bucket=bucket, key=object_key(tenant_id=TENANT, sha256=stored.ref.sha256)
    )

    assert raw is not None
    assert PayloadRef.of_bytes(raw) == stored.ref


@pytest.mark.integration
async def test_object_bien_mat_thi_doc_ra_none(bucket: str) -> None:
    """Lifecycle rule dọn payload cũ là chuyện bình thường, không phải sự cố."""
    store = make_store(bucket)
    stored = StoredPayload(
        ref=PayloadRef.of({"da": "xoa", "id": uuid.uuid4().hex}),
        location=PayloadLocation.OBJECT,
    )

    assert await store.get(tenant_id=TENANT, stored=stored) is None


@pytest.mark.integration
async def test_hai_lan_cat_cung_payload_ra_cung_mot_doi_tuong(bucket: str) -> None:
    """Khoá đánh theo nội dung: một replay idempotency không nhân đôi lưu trữ."""
    payload = {"pad": "z" * 200, "id": uuid.uuid4().hex}
    store = make_store(bucket)

    first = await store.put(tenant_id=TENANT, payload=payload)
    second = await store.put(tenant_id=TENANT, payload=payload)

    assert first.ref == second.ref
    assert await store.get(tenant_id=TENANT, stored=second) == payload


@pytest.mark.integration
async def test_build_payload_store_doc_dung_cau_hinh(bucket: str) -> None:
    settings = Settings()
    store = build_payload_store(settings)

    stored = await store.put(tenant_id=TENANT, payload={"nho": True})
    assert stored.location is PayloadLocation.INLINE

    big = await store.put(
        tenant_id=TENANT,
        payload={
            "lon": "x" * (settings.trace_payload_inline_max_bytes + 1),
            "id": uuid.uuid4().hex,
        },
    )
    assert big.location is PayloadLocation.OBJECT
