"""Object store S3 (§4.7).

Phần kiểm khoá chạy được ở mọi nơi. Phần cất–đọc cần MinIO thật và mang mark
``integration``: cái đang được kiểm là hành vi của chính S3 — đè khoá, khoá
vắng mặt trả về gì — mà một bản giả trong bộ nhớ thì luôn cư xử đúng như mình
tưởng tượng, kể cả khi giả định ấy sai.

    docker compose up -d minio
    pytest -m integration
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from conftest import infra_unavailable

from app.core.config import Settings
from app.core.errors import ErrorCategory, ValidationError
from app.core.objectstore import (
    ObjectStoreError,
    close_object_store,
    ensure_key_safe,
    get_object,
    get_object_store,
    init_object_store,
    object_store_healthy,
    put_object,
)


@pytest.fixture
async def bucket() -> AsyncIterator[str]:
    """Kết nối MinIO thật, bỏ qua test nếu không có."""
    settings = Settings()
    init_object_store(settings)
    if not await object_store_healthy(bucket=settings.s3_bucket_traces):
        await close_object_store()
        infra_unavailable("chưa kết nối được MinIO: docker compose up -d minio")
    yield settings.s3_bucket_traces
    await close_object_store()


@pytest.fixture
def key() -> str:
    """Khoá riêng cho từng test, tránh test này ảnh hưởng test kia."""
    return f"test/{uuid.uuid4().hex}.json"


# ─── Vòng đời client (không cần MinIO) ───────────────────────────────────────


async def test_chua_khoi_tao_thi_bao_ro_thay_vi_tra_ve_none() -> None:
    """Lỗi phải chỉ thẳng vào việc thiếu `init_object_store()` lúc startup."""
    await close_object_store()

    with pytest.raises(RuntimeError, match="init_object_store"):
        get_object_store()


async def test_khoi_tao_hai_lan_van_la_mot_client() -> None:
    """Hai entrypoint cùng gọi `init` không được dựng hai client."""
    settings = Settings()
    init_object_store(settings)
    first = get_object_store()
    init_object_store(settings)

    assert get_object_store() is first
    await close_object_store()


# ─── Khoá (không cần MinIO) ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "key",
    [
        "org_abc/payloads/ab/abc123.json",
        "a",
        "org_abc/x-y_z.0/file.json",
    ],
)
def test_khoa_hop_le_di_qua(key: str) -> None:
    ensure_key_safe(key)


@pytest.mark.parametrize(
    ("key", "vi_sao"),
    [
        ("/org_abc/payloads/x.json", "mở đầu bằng dấu phân cách"),
        ("org_abc/../org_xyz/x.json", "đi ngược ra khỏi tiền tố tenant"),
        ("org_abc//payloads/x.json", "hai dấu phân cách liền nhau"),
        ("org abc/payloads/x.json", "khoảng trắng"),
        ("org_abc/payloads/../../x.json", "nhiều bậc ngược"),
        ("", "rỗng"),
        ("a" * 513, "quá dài"),
    ],
)
def test_khoa_pha_tien_to_bi_chan(key: str, vi_sao: str) -> None:
    """Tiền tố tenant là ranh giới §19 ở tầng lưu trữ.

    Nó phải giữ được ngay cả khi người gọi ghép sai phần đuôi — không phải chỉ
    khi họ ghép đúng.
    """
    with pytest.raises(ValidationError):
        ensure_key_safe(key)


async def test_khoa_hong_bi_chan_truoc_khi_cham_mang() -> None:
    """Không cần client nào để từ chối một khoá sai."""
    await close_object_store()

    with pytest.raises(ValidationError):
        await put_object(bucket="aw-traces", key="../escape", body=b"{}")


# ─── Cất và đọc (cần MinIO) ──────────────────────────────────────────────────


@pytest.mark.integration
async def test_ghi_roi_doc_lai_dung_nguyen_byte(bucket: str, key: str) -> None:
    payload = '{"channel":"C123","text":"xin chào"}'.encode()

    await put_object(bucket=bucket, key=key, body=payload)

    assert await get_object(bucket=bucket, key=key) == payload


@pytest.mark.integration
async def test_khoa_khong_ton_tai_tra_none_chu_khong_nem_loi(bucket: str, key: str) -> None:
    """Payload hết hạn theo lifecycle rule là chuyện bình thường, không phải sự cố."""
    assert await get_object(bucket=bucket, key=key) is None


@pytest.mark.integration
async def test_ghi_lai_cung_khoa_la_de_len(bucket: str, key: str) -> None:
    """Điều kiện để khoá đánh theo nội dung dùng được.

    Khoá là băm của chính payload, nên ghi lại cùng khoá nghĩa là ghi lại đúng
    những byte đã có ở đó — một lần replay idempotency không được làm hỏng thứ
    đang nằm sẵn.
    """
    payload = b'{"a":1}'

    await put_object(bucket=bucket, key=key, body=payload)
    await put_object(bucket=bucket, key=key, body=payload)

    assert await get_object(bucket=bucket, key=key) == payload


@pytest.mark.integration
async def test_bucket_khong_ton_tai_la_loi_ha_tang_khong_phai_khoa_vang_mat(
    bucket: str, key: str
) -> None:
    """Phân biệt hai thứ dễ lẫn: không có object, và không có chỗ chứa object.

    Cái đầu trả `None` và người gọi đi tiếp; cái sau là cấu hình sai và phải ồn
    ào.
    """
    with pytest.raises(ObjectStoreError) as caught:
        await get_object(bucket=f"khong-ton-tai-{uuid.uuid4().hex}", key=key)

    assert caught.value.category is ErrorCategory.INTERNAL


@pytest.mark.integration
async def test_bao_cao_suc_khoe_theo_tung_bucket(bucket: str) -> None:
    assert await object_store_healthy(bucket=bucket) is True
    assert await object_store_healthy(bucket=f"khong-ton-tai-{uuid.uuid4().hex}") is False
