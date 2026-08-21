"""Kiểm tra rate limit, lock phân tán và giá trị dùng một lần (§4.6, §38).

Phần lớn test ở đây cần Redis thật — hành vi cần kiểm chính là tính nguyên tử,
mà một bản giả trong bộ nhớ thì luôn nguyên tử, nên nó sẽ đỗ kể cả khi script
Lua sai. Test nào cần Redis đều mang mark ``integration`` và tự bỏ qua khi
không kết nối được:

    docker compose up -d redis
    pytest -m integration
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from conftest import infra_unavailable

from app.core.config import Settings
from app.core.errors import ErrorCategory, RateLimitedError
from app.core.redis import (
    LockUnavailableError,
    check_rate_limit,
    close_redis,
    enforce_rate_limit,
    global_key,
    init_redis,
    pop_once,
    put_once,
    redis_healthy,
    redis_lock,
)


@pytest.fixture
async def redis_san_sang() -> AsyncIterator[None]:
    """Kết nối Redis thật, bỏ qua test nếu không có."""
    init_redis(Settings())
    if not await redis_healthy():
        await close_redis()
        infra_unavailable("chưa kết nối được Redis: docker compose up -d redis")
    yield
    await close_redis()


@pytest.fixture
def key() -> str:
    """Key riêng cho từng test, tránh test này ảnh hưởng test kia."""
    return global_key("test", uuid.uuid4().hex)


# ─── Đặt tên key (không cần Redis) ───────────────────────────────────────────


def test_global_key_co_tien_to_rieng_khong_lan_voi_key_cua_tenant() -> None:
    from app.core.context import Principal, new_request_context, tenant_cache_key, use_context

    ctx = new_request_context(tenant_id="org_a", principal=Principal(kind="user", id="usr_1"))
    with use_context(ctx):
        cua_tenant = tenant_cache_key("login", "a@b.com")

    assert global_key("login", "a@b.com") != cua_tenant
    assert not global_key("login").startswith(cua_tenant.rsplit(":", 1)[0])


def test_lock_unavailable_la_loi_dang_retry() -> None:
    """Tranh chấp lock là nhất thời — Temporal Activity phải thử lại (§15)."""
    err = LockUnavailableError("thu nghiem")
    assert err.category is ErrorCategory.TIMEOUT
    assert err.retryable


# ─── Rate limit ──────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_cho_qua_khi_chua_cham_nguong(redis_san_sang: None, key: str) -> None:
    for _ in range(3):
        assert (await check_rate_limit(key, limit=3, window_seconds=60)).allowed


@pytest.mark.integration
async def test_chan_khi_vuot_nguong(redis_san_sang: None, key: str) -> None:
    for _ in range(3):
        await check_rate_limit(key, limit=3, window_seconds=60)

    ket_qua = await check_rate_limit(key, limit=3, window_seconds=60)
    assert not ket_qua.allowed
    assert ket_qua.retry_after_seconds > 0


@pytest.mark.integration
async def test_so_luot_con_lai_giam_dan(redis_san_sang: None, key: str) -> None:
    assert (await check_rate_limit(key, limit=3, window_seconds=60)).remaining == 2
    assert (await check_rate_limit(key, limit=3, window_seconds=60)).remaining == 1
    assert (await check_rate_limit(key, limit=3, window_seconds=60)).remaining == 0


@pytest.mark.integration
async def test_hai_key_dem_doc_lap_voi_nhau(redis_san_sang: None, key: str) -> None:
    khac = global_key("test", uuid.uuid4().hex)
    for _ in range(3):
        await check_rate_limit(key, limit=3, window_seconds=60)

    assert (await check_rate_limit(khac, limit=3, window_seconds=60)).allowed


@pytest.mark.integration
async def test_cua_so_het_han_thi_dem_lai_tu_dau(redis_san_sang: None, key: str) -> None:
    await check_rate_limit(key, limit=1, window_seconds=1)
    assert not (await check_rate_limit(key, limit=1, window_seconds=1)).allowed

    await asyncio.sleep(1.2)
    assert (await check_rate_limit(key, limit=1, window_seconds=1)).allowed


@pytest.mark.integration
async def test_enforce_nem_ratelimited_kem_retry_after(redis_san_sang: None, key: str) -> None:
    await enforce_rate_limit(key, limit=1, window_seconds=60)

    with pytest.raises(RateLimitedError) as exc:
        await enforce_rate_limit(key, limit=1, window_seconds=60)

    assert exc.value.http_status == 429
    assert exc.value.retry_after_seconds is not None


@pytest.mark.integration
async def test_key_luon_co_ttl(redis_san_sang: None, key: str) -> None:
    """Key không TTL sẽ khoá vĩnh viễn danh tính tương ứng."""
    from app.core.redis import get_redis

    await check_rate_limit(key, limit=5, window_seconds=60)
    assert await get_redis().ttl(key) > 0


# ─── Lock ────────────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_lay_va_nha_duoc_lock(redis_san_sang: None, key: str) -> None:
    async with redis_lock(key, ttl_seconds=5):
        pass
    async with redis_lock(key, ttl_seconds=5, wait_seconds=0.1):
        pass


@pytest.mark.integration
async def test_nguoi_thu_hai_phai_cho(redis_san_sang: None, key: str) -> None:
    async with redis_lock(key, ttl_seconds=5):
        with pytest.raises(LockUnavailableError):
            async with redis_lock(key, ttl_seconds=5, wait_seconds=0.2):
                pytest.fail("Không được vào đây khi lock đang bị giữ")


@pytest.mark.integration
async def test_lock_duoc_nha_ca_khi_ben_trong_nem_loi(redis_san_sang: None, key: str) -> None:
    with pytest.raises(ValueError, match="loi-co-y"):
        async with redis_lock(key, ttl_seconds=5):
            raise ValueError("loi-co-y")

    # Nếu finally không chạy, dòng này sẽ ném LockUnavailableError.
    async with redis_lock(key, ttl_seconds=5, wait_seconds=0.1):
        pass


@pytest.mark.integration
async def test_chi_mot_task_vao_duoc_vung_gang(redis_san_sang: None, key: str) -> None:
    """Đây là hành vi mà refresh OAuth token phụ thuộc vào (§6.3)."""
    dang_chay = 0
    dinh_diem = 0

    async def cong_viec() -> None:
        nonlocal dang_chay, dinh_diem
        async with redis_lock(key, ttl_seconds=5, wait_seconds=5.0):
            dang_chay += 1
            dinh_diem = max(dinh_diem, dang_chay)
            await asyncio.sleep(0.05)
            dang_chay -= 1

    await asyncio.gather(*(cong_viec() for _ in range(5)))
    assert dinh_diem == 1


@pytest.mark.integration
async def test_khong_nha_nham_lock_cua_nguoi_khac(redis_san_sang: None, key: str) -> None:
    """Chạy quá TTL rồi nhả lock không được xoá lock người khác vừa lấy."""
    from app.core.redis import get_redis

    async with redis_lock(key, ttl_seconds=1):
        await asyncio.sleep(1.2)  # lock của mình đã tự hết hạn
        await get_redis().set(key, "chu-so-huu-khac", ex=30)

    assert await get_redis().get(key) == "chu-so-huu-khac"
    await get_redis().delete(key)


# ─── Giá trị dùng một lần ────────────────────────────────────────────────────


@pytest.mark.integration
async def test_doc_duoc_gia_tri_da_luu(redis_san_sang: None, key: str) -> None:
    await put_once(key, "state-cua-oauth", ttl_seconds=60)
    assert await pop_once(key) == "state-cua-oauth"


@pytest.mark.integration
async def test_lan_doc_thu_hai_tra_ve_none(redis_san_sang: None, key: str) -> None:
    """Chống replay cho OAuth state (§24)."""
    await put_once(key, "state-cua-oauth", ttl_seconds=60)
    await pop_once(key)
    assert await pop_once(key) is None


@pytest.mark.integration
async def test_key_chua_ton_tai_tra_ve_none(redis_san_sang: None, key: str) -> None:
    assert await pop_once(key) is None


@pytest.mark.integration
async def test_gia_tri_het_han_theo_ttl(redis_san_sang: None, key: str) -> None:
    await put_once(key, "state-cua-oauth", ttl_seconds=1)
    await asyncio.sleep(1.2)
    assert await pop_once(key) is None
