"""Rate limit theo sáu cấp của §22, dựng trên `core/redis.check_rate_limit`."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import RateLimitedError
from app.core.logging import get_logger
from app.core.redis import check_rate_limit, global_key
from app.domains.integration.domain.connectors import ConnectorSpec
from app.domains.integration.domain.tools import RateLimitRule, ToolSpec

log = get_logger(__name__)


def tenant_key(tenant_id: str, *parts: str) -> str:
    """Key có tiền tố tenant, dựng từ `tenant_id` được truyền vào."""
    return ":".join(("aw", "t", tenant_id, *parts))


@dataclass(frozen=True, slots=True)
class RateLimitTarget:
    """Ai đang gọi, đủ để dựng khoá cho cả sáu cấp."""

    tenant_id: str
    workspace_id: str
    user_id: str
    connection_id: str


class RedisRateLimiter:
    """Đếm một lượt dùng ở mọi cấp mà tool hoặc connector có khai ngưỡng."""

    __slots__ = ()

    def keys(
        self, target: RateLimitTarget, tool: ToolSpec, connector: ConnectorSpec
    ) -> list[tuple[str, str, RateLimitRule]]:
        """`(cấp, khoá, ngưỡng)` cho mọi cấp có ngưỡng, rộng → hẹp."""
        profile = tool.rate_limits.merged_over(connector.default_rate_limits)
        provider = connector.connector_id

        # Slack tính hạn mức theo từng method trên từng workspace, nên khoá cấp
        # `tool` phải mang cả connection.
        by_level: dict[str, str] = {
            "provider": global_key("rl", "provider", provider),
            "installation": tenant_key(target.tenant_id, "rl", "install", target.connection_id),
            "tenant": tenant_key(target.tenant_id, "rl", "tenant", provider),
            "connection": tenant_key(target.tenant_id, "rl", "conn", target.connection_id),
            "user": tenant_key(target.tenant_id, "rl", "user", target.user_id, provider),
            "tool": tenant_key(target.tenant_id, "rl", "tool", tool.tool_id, target.connection_id),
        }
        return [(level, by_level[level], rule) for level, rule in profile.rules()]

    async def acquire(
        self, *, target: RateLimitTarget, tool: ToolSpec, connector: ConnectorSpec
    ) -> None:
        """Đếm một lượt ở mọi cấp; vượt bất kỳ cấp nào thì ném `RateLimitedError`."""
        blocked: list[tuple[str, int]] = []

        for level, key, rule in self.keys(target, tool, connector):
            result = await check_rate_limit(
                key, limit=rule.limit, window_seconds=rule.window_seconds
            )
            if not result.allowed:
                blocked.append((level, result.retry_after_seconds))
                continue
            log.debug(
                "integration_rate_limit_consumed",
                level=level,
                tool_id=tool.tool_id,
                remaining=result.remaining,
            )

        if not blocked:
            return

        # Chờ theo cấp chặt nhất — hết cấp đó rồi vẫn còn cấp khác thì không
        # giúp gì cho người gọi.
        retry_after = max(seconds for _, seconds in blocked)
        levels = ", ".join(level for level, _ in blocked)

        log.info(
            "integration_rate_limited",
            levels=levels,
            tool_id=tool.tool_id,
            provider=connector.connector_id,
            retry_after_seconds=retry_after,
        )
        raise RateLimitedError(
            f"Đã chạm hạn mức gọi {connector.label} ({levels}). Thử lại sau {retry_after}s.",
            retry_after_seconds=retry_after,
            details={"levels": levels, "tool_id": tool.tool_id},
        )
