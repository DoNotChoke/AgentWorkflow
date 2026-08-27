"""Circuit breaker cho lời gọi provider"""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Any, Final, cast

from redis.exceptions import RedisError

from app.core.errors import ErrorCategory, ProviderUnavailableError
from app.core.logging import get_logger
from app.core.redis import get_redis, global_key
from app.domains.integration.domain.connectors import ConnectorSpec
from app.domains.integration.infrastructure.rate_limit import tenant_key

log = get_logger(__name__)

#: Lỗi làm tăng bộ đếm. Nhỏ hơn `core.errors.RETRYABLE` đúng một phần tử —
#: `RATE_LIMITED` — và đó là điểm khác biệt quan trọng nhất của module này.
TRIPPING: Final = frozenset({ErrorCategory.PROVIDER_UNAVAILABLE, ErrorCategory.TIMEOUT})


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


# Trả về {cho_qua, số_giây_chờ}. Khi hết thời gian mở, đúng một request được
# nhận vé thăm dò; những request cùng lúc vẫn bị từ chối.
#
# Vé mang theo dấu thời gian và có hạn. Một vé quá hạn là vé bị bỏ rơi — tiến
# trình giành nó đã chết, hoặc lời gọi bị mạch còn lại chặn ngay sau đó nên
# không bao giờ chạy. Không có hạn thì mạch kẹt tới khi key hết TTL, lâu hơn
# hẳn `open_seconds`, và `record_success`/`record_failure` là hai chỗ duy nhất
# xoá vé nên chúng không cứu được tình huống đó.
_CHECK: Final = """
local state = redis.call('HGET', KEYS[1], 'state')
if state ~= 'open' and state ~= 'half_open' then return {1, 0} end

local now = tonumber(ARGV[1])
local open_ms = tonumber(ARGV[2])
local probe_timeout_ms = tonumber(ARGV[3])

local opened_at = tonumber(redis.call('HGET', KEYS[1], 'opened_at')) or 0
local elapsed = now - opened_at

if elapsed < open_ms then
    return {0, math.ceil((open_ms - elapsed) / 1000)}
end

local probe = tonumber(redis.call('HGET', KEYS[1], 'probe'))
if probe == nil or (now - probe) > probe_timeout_ms then
    redis.call('HSET', KEYS[1], 'probe', now, 'state', 'half_open')
    return {1, 0}
end

-- Có người đang thăm dò thật. Bảo người gọi quay lại sớm chứ không phải chờ
-- hết hạn vé: lời gọi kia thường xong trong một hai giây.
return {0, 1}
"""

# Một thất bại lúc đang thăm dò mở lại mạch ngay — không cần đủ ngưỡng, vì
# provider vừa được cho một cơ hội và đã trượt.
_FAILURE: Final = """
local now = tonumber(ARGV[1])
local threshold = tonumber(ARGV[2])
local window_ms = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

if redis.call('HGET', KEYS[1], 'state') == 'half_open' then
    redis.call('HSET', KEYS[1], 'state', 'open', 'opened_at', now, 'failures', 0)
    redis.call('HDEL', KEYS[1], 'probe')
    redis.call('EXPIRE', KEYS[1], ttl)
    return 'open'
end

local first = tonumber(redis.call('HGET', KEYS[1], 'first_failure_at')) or 0
if first == 0 or (now - first) > window_ms then
    redis.call('HSET', KEYS[1], 'first_failure_at', now, 'failures', 1)
else
    redis.call('HINCRBY', KEYS[1], 'failures', 1)
end

redis.call('EXPIRE', KEYS[1], ttl)

if tonumber(redis.call('HGET', KEYS[1], 'failures')) >= threshold then
    redis.call('HSET', KEYS[1], 'state', 'open', 'opened_at', now)
    redis.call('HDEL', KEYS[1], 'probe')
    return 'open'
end
return 'closed'
"""


class RedisCircuitBreaker:
    """Bộ đếm thất bại **liên tiếp**: một lần thành công xoá sạch trạng thái."""

    __slots__ = ()

    def keys(self, *, provider: str, tenant_id: str, connection_id: str) -> tuple[str, str]:
        """`(khoá theo kết nối, khoá toàn provider)`."""
        return (
            tenant_key(tenant_id, "cb", provider, connection_id),
            global_key("cb", provider),
        )

    def _circuits(
        self, connector: ConnectorSpec, tenant_id: str, connection_id: str
    ) -> tuple[tuple[str, int], tuple[str, int]]:
        """Hai mạch, mỗi mạch kèm ngưỡng riêng của nó."""
        connection_key, provider_key = self.keys(
            provider=connector.connector_id, tenant_id=tenant_id, connection_id=connection_id
        )
        spec = connector.circuit_breaker
        return (
            (connection_key, spec.failure_threshold),
            (provider_key, spec.global_failure_threshold),
        )

    async def check(self, *, connector: ConnectorSpec, tenant_id: str, connection_id: str) -> None:
        """Ném `ProviderUnavailableError` nếu một trong hai mạch đang hở."""
        spec = connector.circuit_breaker
        now_ms = str(int(time.time() * 1000))
        open_ms = str(spec.open_seconds * 1000)
        probe_ms = str(spec.probe_timeout_seconds * 1000)

        for key in self.keys(
            provider=connector.connector_id, tenant_id=tenant_id, connection_id=connection_id
        ):
            allowed, retry_after = await self._eval(
                _CHECK, key, now_ms, open_ms, probe_ms, fallback=(1, 0)
            )
            if allowed:
                continue

            log.info(
                "integration_circuit_open",
                provider=connector.connector_id,
                connection_id=connection_id,
                retry_after_seconds=retry_after,
            )
            raise ProviderUnavailableError(
                f"{connector.label} đang không phản hồi ổn định. Thử lại sau {retry_after}s.",
                retry_after_seconds=float(retry_after),
                details={"provider": connector.connector_id},
            )

    async def record_success(
        self, *, connector: ConnectorSpec, tenant_id: str, connection_id: str
    ) -> None:
        """Xoá trạng thái của cả hai mạch.

        Xoá thay vì giảm dần: ngưỡng đếm thất bại **liên tiếp**, nên một lần
        thành công đã trả lời xong câu hỏi mà mạch đang hỏi.
        """
        keys = self.keys(
            provider=connector.connector_id, tenant_id=tenant_id, connection_id=connection_id
        )
        try:
            await get_redis().delete(*keys)
        except (RedisError, RuntimeError, OSError) as exc:
            log.warning("circuit_breaker_bo_qua_vi_redis_loi", error_type=type(exc).__name__)

    async def record_failure(
        self,
        *,
        connector: ConnectorSpec,
        tenant_id: str,
        connection_id: str,
        category: ErrorCategory,
    ) -> None:
        """Đếm một thất bại, nhưng chỉ khi nó nói lên điều gì đó về provider."""
        if category not in TRIPPING:
            return

        spec = connector.circuit_breaker
        now_ms = str(int(time.time() * 1000))
        window_ms = str(spec.window_seconds * 1000)
        ttl = str(spec.window_seconds * 4)

        for key, threshold in self._circuits(connector, tenant_id, connection_id):
            state = await self._eval(
                _FAILURE, key, now_ms, str(threshold), window_ms, ttl, fallback="closed"
            )
            if state == BreakerState.OPEN:
                log.warning(
                    "integration_circuit_tripped",
                    provider=connector.connector_id,
                    connection_id=connection_id,
                    category=category.value,
                    key=key,
                )

    async def state(self, key: str) -> BreakerState:
        """Trạng thái hiện tại của một mạch. Dùng cho test và cho telemetry."""
        try:
            raw = await cast("Any", get_redis().hget(key, "state"))
        except (RedisError, RuntimeError, OSError):
            return BreakerState.CLOSED
        return BreakerState(raw) if raw else BreakerState.CLOSED

    async def _eval(self, script: str, key: str, *args: str, fallback: Any) -> Any:
        """Chạy script Lua; Redis lỗi thì trả về `fallback` — hỏng mở."""
        try:
            return await cast("Any", get_redis().eval(script, 1, key, *args))
        except (RedisError, RuntimeError, OSError) as exc:
            log.warning("circuit_breaker_bo_qua_vi_redis_loi", error_type=type(exc).__name__)
            return fallback
