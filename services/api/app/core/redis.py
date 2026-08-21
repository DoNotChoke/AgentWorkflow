"""Redis"""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Final, cast

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.core.errors import AppError, ErrorCategory, RateLimitedError
from app.core.logging import get_logger

log = get_logger(__name__)

_client: Redis | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Vòng đời client
# ─────────────────────────────────────────────────────────────────────────────


def init_redis(settings: Settings) -> None:
    """Khởi tạo connection pool. Gọi một lần lúc khởi động tiến trình."""
    global _client
    if _client is None:
        _client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=settings.redis_max_connections,
            socket_timeout=settings.redis_socket_timeout_seconds,
            socket_connect_timeout=settings.redis_socket_timeout_seconds,
            health_check_interval=30,
        )


def get_redis() -> Redis:
    if _client is None:
        raise RuntimeError("Redis chưa được khởi tạo. Gọi init_redis() lúc startup.")
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _eval(script: str, key: str, *args: str) -> Any:
    """Chạy script Lua."""
    return await cast("Awaitable[Any]", get_redis().eval(script, 1, key, *args))


async def redis_healthy() -> bool:
    """Dùng cho ``/readyz``. Không bao giờ ném lỗi."""
    try:
        await get_redis().ping()
    except (RedisError, RuntimeError, OSError):
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Đặt tên key
# ─────────────────────────────────────────────────────────────────────────────


def global_key(*parts: str) -> str:
    """Key **không** gắn tenant.

    Chỉ dùng cho thứ tồn tại trước khi biết tenant là ai — điển hình là rate
    limit của login và register, xảy ra trước cả khi xác thực xong.
    """
    return ":".join(("aw", "g", *parts))


# ─────────────────────────────────────────────────────────────────────────────
# Rate limit — cửa sổ cố định
# ─────────────────────────────────────────────────────────────────────────────

_RATE_LIMIT_SCRIPT: Final = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return {current, redis.call('TTL', KEYS[1])}
"""


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after_seconds: int


async def check_rate_limit(key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
    """Đếm một lần dùng trong cửa sổ cố định."""
    try:
        raw: Any = await _eval(_RATE_LIMIT_SCRIPT, key, str(window_seconds))
    except (RedisError, RuntimeError, OSError) as exc:
        # Hỏng mở — xem docstring đầu module.
        log.warning("rate_limit_bo_qua_vi_redis_loi", error_type=type(exc).__name__)
        return RateLimitResult(allowed=True, remaining=limit, retry_after_seconds=0)

    current, ttl = int(raw[0]), int(raw[1])
    retry_after = max(ttl, 1) if ttl >= 0 else window_seconds

    if current > limit:
        return RateLimitResult(allowed=False, remaining=0, retry_after_seconds=retry_after)
    return RateLimitResult(
        allowed=True,
        remaining=max(limit - current, 0),
        retry_after_seconds=0,
    )


async def enforce_rate_limit(
    key: str,
    *,
    limit: int,
    window_seconds: int,
    message: str = "Quá nhiều yêu cầu. Vui lòng thử lại sau.",
) -> None:
    """Như ``check_rate_limit`` nhưng ném ``RateLimitedError`` khi vượt ngưỡng.

    Trình xử lý lỗi ở ``entrypoints/middleware.py`` sẽ tự động chuyển nó thành
    HTTP 429 kèm header ``Retry-After``.
    """
    result = await check_rate_limit(key, limit=limit, window_seconds=window_seconds)
    if not result.allowed:
        raise RateLimitedError(message, retry_after_seconds=result.retry_after_seconds)


# ─────────────────────────────────────────────────────────────────────────────
# Lock phân tán
# ─────────────────────────────────────────────────────────────────────────────

_LOCK_RELEASE_SCRIPT: Final = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class LockUnavailableError(AppError):
    """Không lấy được lock trong thời gian chờ cho phép."""

    category = ErrorCategory.TIMEOUT


@asynccontextmanager
async def redis_lock(
    key: str,
    *,
    ttl_seconds: int = 30,
    wait_seconds: float = 5.0,
    poll_interval_seconds: float = 0.05,
) -> AsyncIterator[None]:
    """Lock độc quyền, tự hết hạn."""
    token = secrets.token_urlsafe(16)
    client = get_redis()
    deadline = time.monotonic() + wait_seconds

    try:
        acquired = bool(await client.set(key, token, nx=True, ex=ttl_seconds))
        while not acquired and time.monotonic() < deadline:
            await asyncio.sleep(poll_interval_seconds)
            acquired = bool(await client.set(key, token, nx=True, ex=ttl_seconds))
    except (RedisError, RuntimeError, OSError) as exc:
        raise LockUnavailableError(
            "Không kiểm tra được lock vì Redis không phản hồi.", cause=exc
        ) from exc

    if not acquired:
        raise LockUnavailableError(
            f"Không lấy được lock sau {wait_seconds:g}s. Có thao tác khác đang chạy."
        )

    try:
        yield
    finally:
        try:
            await _eval(_LOCK_RELEASE_SCRIPT, key, token)
        except (RedisError, RuntimeError, OSError) as exc:
            # Không để lỗi nhả lock che mất lỗi thật của khối lệnh bên trong.
            # Lock sẽ tự hết hạn sau ttl_seconds.
            log.warning("nha_lock_that_bai", key=key, error_type=type(exc).__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Giá trị dùng một lần
# ─────────────────────────────────────────────────────────────────────────────


async def put_once(key: str, value: str, *, ttl_seconds: int) -> None:
    """Lưu một giá trị chỉ được đọc đúng một lần, có hạn dùng."""
    await get_redis().set(key, value, ex=ttl_seconds)


async def pop_once(key: str) -> str | None:
    """Đọc và xoá nguyên tử. Lần đọc thứ hai luôn trả về ``None``."""
    value: Any = await get_redis().getdel(key)
    return None if value is None else str(value)
