"""Circuit breaker (§23), chạy trên Redis thật.

Chạy thật vì thứ đáng kiểm ở đây là ba script Lua chuyển trạng thái nguyên tử.
Giả Redis đi thì test chỉ còn khẳng định lại chính đoạn code đang viết.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncIterator

import pytest
from conftest import infra_unavailable, make_connector

from app.core.config import get_settings
from app.core.errors import ErrorCategory, ProviderUnavailableError
from app.core.redis import close_redis, get_redis, init_redis, redis_healthy
from app.domains.integration.domain.connectors import CircuitBreakerSpec
from app.domains.integration.infrastructure.circuit_breaker import (
    TRIPPING,
    BreakerState,
    RedisCircuitBreaker,
)

pytestmark = pytest.mark.integration

TENANT = "org_cb"
CONNECTION = "conn_cb"


def connector(**overrides: int):  # type: ignore[no-untyped-def]
    spec = CircuitBreakerSpec(
        failure_threshold=3,
        global_failure_threshold=12,
        window_seconds=60,
        open_seconds=1,
        probe_timeout_seconds=1,
    )
    return dataclasses.replace(
        make_connector(), circuit_breaker=dataclasses.replace(spec, **overrides)
    )


@pytest.fixture
async def breaker() -> AsyncIterator[RedisCircuitBreaker]:
    init_redis(get_settings())
    if not await redis_healthy():
        await close_redis()
        infra_unavailable("Redis chưa chạy — `docker compose up -d redis`.")

    subject = RedisCircuitBreaker()
    keys = subject.keys(provider="demo", tenant_id=TENANT, connection_id=CONNECTION)
    await get_redis().delete(*keys)
    yield subject
    await get_redis().delete(*keys)
    await close_redis()


async def fail(subject: RedisCircuitBreaker, times: int, **kwargs: int) -> None:
    for _ in range(times):
        await subject.record_failure(
            connector=connector(**kwargs),
            tenant_id=TENANT,
            connection_id=CONNECTION,
            category=ErrorCategory.PROVIDER_UNAVAILABLE,
        )


# ─── Chuyển trạng thái ───────────────────────────────────────────────────────


async def test_duoi_nguong_thi_van_cho_qua(breaker: RedisCircuitBreaker) -> None:
    await fail(breaker, 2)
    await breaker.check(connector=connector(), tenant_id=TENANT, connection_id=CONNECTION)


async def test_du_nguong_thi_ho_mach(breaker: RedisCircuitBreaker) -> None:
    await fail(breaker, 3)

    with pytest.raises(ProviderUnavailableError) as caught:
        await breaker.check(connector=connector(), tenant_id=TENANT, connection_id=CONNECTION)

    assert caught.value.retry_after_seconds is not None
    assert caught.value.retryable


async def test_mot_lan_thanh_cong_xoa_sach_bo_dem(breaker: RedisCircuitBreaker) -> None:
    """Ngưỡng đếm thất bại **liên tiếp**."""
    await fail(breaker, 2)
    await breaker.record_success(connector=connector(), tenant_id=TENANT, connection_id=CONNECTION)
    await fail(breaker, 2)

    await breaker.check(connector=connector(), tenant_id=TENANT, connection_id=CONNECTION)


async def test_het_thoi_gian_mo_thi_tha_dung_mot_request_tham_do(
    breaker: RedisCircuitBreaker,
) -> None:
    await fail(breaker, 3)
    await asyncio.sleep(1.1)

    # Vé thăm dò thứ nhất qua được.
    await breaker.check(connector=connector(), tenant_id=TENANT, connection_id=CONNECTION)

    connection_key, _ = breaker.keys(provider="demo", tenant_id=TENANT, connection_id=CONNECTION)
    assert await breaker.state(connection_key) is BreakerState.HALF_OPEN

    # Request thứ hai cùng lúc vẫn bị chặn — chỉ một vé.
    with pytest.raises(ProviderUnavailableError):
        await breaker.check(connector=connector(), tenant_id=TENANT, connection_id=CONNECTION)


async def test_tham_do_thanh_cong_thi_dong_mach(breaker: RedisCircuitBreaker) -> None:
    await fail(breaker, 3)
    await asyncio.sleep(1.1)
    await breaker.check(connector=connector(), tenant_id=TENANT, connection_id=CONNECTION)

    await breaker.record_success(connector=connector(), tenant_id=TENANT, connection_id=CONNECTION)

    connection_key, _ = breaker.keys(provider="demo", tenant_id=TENANT, connection_id=CONNECTION)
    assert await breaker.state(connection_key) is BreakerState.CLOSED
    await breaker.check(connector=connector(), tenant_id=TENANT, connection_id=CONNECTION)


async def test_ve_tham_do_bi_bo_roi_thi_giao_lai_duoc(
    breaker: RedisCircuitBreaker,
) -> None:
    """Vé bị giành nhưng không lời gọi nào được thực hiện.

    Xảy ra khi tiến trình chết sau lúc giành vé, hoặc khi `check()` giành vé ở
    mạch này rồi bị mạch kia từ chối ngay sau đó. Không có thời hạn cho vé thì
    mạch kẹt cho tới khi key hết TTL — lâu hơn hẳn `open_seconds`.
    """
    await fail(breaker, 3)
    await asyncio.sleep(1.1)

    # Giành vé rồi "chết": không record_success, không record_failure.
    await breaker.check(connector=connector(), tenant_id=TENANT, connection_id=CONNECTION)

    # Vé còn trong hạn — chưa ai được giành lại.
    with pytest.raises(ProviderUnavailableError):
        await breaker.check(connector=connector(), tenant_id=TENANT, connection_id=CONNECTION)

    await asyncio.sleep(1.1)

    # Quá hạn: vé coi như bị bỏ rơi, giao lại được.
    await breaker.check(connector=connector(), tenant_id=TENANT, connection_id=CONNECTION)


async def test_mach_kia_tu_choi_khong_lam_ket_ve_cua_mach_nay(
    breaker: RedisCircuitBreaker,
) -> None:
    """Đường đi cụ thể làm lộ ra lỗi.

    Hai mạch dùng chung `open_seconds`, nên chúng chỉ lệch pha khi mạch toàn
    cục hở **muộn hơn** — tức là bị một kết nối khác làm sập. Lúc đó `check()`
    trên kết nối này giành được vé ở mạch của nó, rồi mạch toàn cục ném lỗi:
    vé đã bị giành mà không lời gọi nào chạy.
    """
    other = "conn_khac"
    other_keys = breaker.keys(provider="demo", tenant_id=TENANT, connection_id=other)
    await get_redis().delete(*other_keys)
    try:
        await fail(breaker, 3)
        await asyncio.sleep(1.1)  # mạch của CONNECTION đã qua thời gian mở

        # Một kết nối khác đẩy mạch toàn cục lên 12 và làm nó hở ngay bây giờ.
        for _ in range(9):
            await breaker.record_failure(
                connector=connector(),
                tenant_id=TENANT,
                connection_id=other,
                category=ErrorCategory.PROVIDER_UNAVAILABLE,
            )

        with pytest.raises(ProviderUnavailableError):
            await breaker.check(connector=connector(), tenant_id=TENANT, connection_id=CONNECTION)

        # Mạch toàn cục hồi phục. Mạch theo kết nối không được kẹt vì cái vé đã
        # bị giành ở lượt trên.
        _, provider_key = breaker.keys(provider="demo", tenant_id=TENANT, connection_id=CONNECTION)
        await get_redis().delete(provider_key)
        await asyncio.sleep(1.1)

        await breaker.check(connector=connector(), tenant_id=TENANT, connection_id=CONNECTION)
    finally:
        await get_redis().delete(*other_keys)


async def test_tham_do_that_bai_thi_mo_lai_ngay_khong_can_du_nguong(
    breaker: RedisCircuitBreaker,
) -> None:
    """Provider vừa được cho một cơ hội và đã trượt — không cần đếm lại từ đầu."""
    await fail(breaker, 3)
    await asyncio.sleep(1.1)
    await breaker.check(connector=connector(), tenant_id=TENANT, connection_id=CONNECTION)

    await fail(breaker, 1)

    with pytest.raises(ProviderUnavailableError):
        await breaker.check(connector=connector(), tenant_id=TENANT, connection_id=CONNECTION)


# ─── Lỗi nào được tính ───────────────────────────────────────────────────────


async def test_rate_limited_khong_lam_sap_mach(breaker: RedisCircuitBreaker) -> None:
    """Luật quan trọng nhất của module này.

    `RateLimited` là backpressure bình thường. Để nó làm hở mạch nghĩa là mỗi
    lần bị tiết lưu lại tự biến thành một sự cố toàn phần do chính mình gây ra.
    """
    for _ in range(10):
        await breaker.record_failure(
            connector=connector(),
            tenant_id=TENANT,
            connection_id=CONNECTION,
            category=ErrorCategory.RATE_LIMITED,
        )

    await breaker.check(connector=connector(), tenant_id=TENANT, connection_id=CONNECTION)


@pytest.mark.parametrize(
    "category",
    [
        ErrorCategory.VALIDATION,
        ErrorCategory.AUTHORIZATION,
        ErrorCategory.MISSING_SCOPE,
        ErrorCategory.CONNECTION_EXPIRED,
        ErrorCategory.PROVIDER_PERMANENT,
    ],
)
async def test_loi_cua_mot_loi_goi_khong_lam_sap_mach(
    breaker: RedisCircuitBreaker, category: ErrorCategory
) -> None:
    """Tham số sai hay thiếu scope nói về lời gọi, không nói về provider."""
    for _ in range(10):
        await breaker.record_failure(
            connector=connector(),
            tenant_id=TENANT,
            connection_id=CONNECTION,
            category=category,
        )

    await breaker.check(connector=connector(), tenant_id=TENANT, connection_id=CONNECTION)


def test_tap_loi_lam_sap_mach_hep_hon_tap_retryable() -> None:
    """Khác đúng một phần tử, và phần tử đó là `RateLimited`."""
    from app.core.errors import RETRYABLE

    assert TRIPPING < RETRYABLE
    assert frozenset({ErrorCategory.RATE_LIMITED}) == RETRYABLE - TRIPPING


# ─── Cách ly giữa các tenant ─────────────────────────────────────────────────


async def test_mot_ket_noi_hong_khong_chan_ket_noi_khac(
    breaker: RedisCircuitBreaker,
) -> None:
    """Lý do tồn tại của mạch theo kết nối.

    Ngưỡng toàn provider phải cao hơn hẳn ngưỡng theo kết nối, nếu không thì
    mạch toàn cục hở cùng lúc và mạch theo kết nối không cách ly được gì.
    """
    other = "conn_khac"
    other_keys = breaker.keys(provider="demo", tenant_id=TENANT, connection_id=other)
    await get_redis().delete(*other_keys)
    try:
        await fail(breaker, 3)

        with pytest.raises(ProviderUnavailableError):
            await breaker.check(connector=connector(), tenant_id=TENANT, connection_id=CONNECTION)

        # 3 thất bại chưa chạm ngưỡng toàn cục (12), nên kết nối lành vẫn chạy.
        await breaker.check(connector=connector(), tenant_id=TENANT, connection_id=other)
    finally:
        await get_redis().delete(*other_keys)


async def test_du_nguong_toan_cuc_thi_chan_moi_ket_noi(
    breaker: RedisCircuitBreaker,
) -> None:
    """Provider sập thật thì từng kết nối không cần tự phát hiện lại."""
    other = "conn_khac"
    other_keys = breaker.keys(provider="demo", tenant_id=TENANT, connection_id=other)
    await get_redis().delete(*other_keys)
    try:
        await fail(breaker, 12)

        with pytest.raises(ProviderUnavailableError):
            await breaker.check(connector=connector(), tenant_id=TENANT, connection_id=other)
    finally:
        await get_redis().delete(*other_keys)


def test_nguong_toan_cuc_cao_hon_nguong_theo_ket_noi() -> None:
    """Ràng buộc mà toàn bộ việc cách ly dựa vào."""
    from app.domains.integration.domain.catalog import default_registry

    for spec in default_registry().list_connectors():
        breaker_spec = spec.circuit_breaker
        assert breaker_spec.global_failure_threshold > breaker_spec.failure_threshold, (
            f"{spec.connector_id}: ngưỡng toàn cục phải cao hơn ngưỡng theo kết nối, "
            "nếu không mạch theo kết nối không cách ly được gì."
        )


def test_han_ve_tham_do_dai_hon_loi_goi_lau_nhat_cua_connector() -> None:
    """Vé phải sống lâu hơn lời gọi mà nó bảo vệ.

    Ngắn hơn thì một vé **đang dùng thật** bị coi là bỏ rơi và bị cướp, và hai
    lời gọi thăm dò cùng chạy — đúng thứ mà cơ chế một-vé sinh ra để ngăn.
    """
    from app.domains.integration.domain.catalog import default_registry

    registry = default_registry()
    for spec in registry.list_connectors():
        tools = registry.list_tools(provider=spec.connector_id, enabled_only=False)
        longest = max(tool.timeout.total_seconds for tool in tools)
        assert spec.circuit_breaker.probe_timeout_seconds >= longest, (
            f"{spec.connector_id}: hạn vé {spec.circuit_breaker.probe_timeout_seconds}s "
            f"ngắn hơn lời gọi lâu nhất ({longest}s)."
        )
