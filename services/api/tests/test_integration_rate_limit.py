"""Rate limiter: khoá theo sáu cấp của §22, và cách gộp ngưỡng.

Không cần Redis. Thay `check_rate_limit` ở khớp `core.redis` — đó là ranh giới
mà module này dựng lên trên, và hành vi của chính nó (Lua, cửa sổ cố định,
hỏng mở) đã có test riêng ở `test_core_redis.py`.
"""

from __future__ import annotations

import dataclasses

import pytest
from conftest import make_connector, make_tool

from app.core.context import new_request_context, tenant_cache_key, use_context
from app.core.errors import RateLimitedError
from app.core.redis import RateLimitResult
from app.domains.integration.domain.tools import RateLimitProfile, RateLimitRule
from app.domains.integration.infrastructure import rate_limit as module
from app.domains.integration.infrastructure.rate_limit import (
    RateLimitTarget,
    RedisRateLimiter,
    tenant_key,
)

TARGET = RateLimitTarget(
    tenant_id="org_1",
    workspace_id="ws_1",
    user_id="usr_1",
    connection_id="conn_1",
)


class FakeCounter:
    """Ghi lại từng lần đếm; từ chối những khoá được liệt kê."""

    def __init__(self, deny: dict[str, int] | None = None) -> None:
        self.deny = deny or {}
        self.seen: list[tuple[str, int, int]] = []

    async def __call__(self, key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
        self.seen.append((key, limit, window_seconds))
        if key in self.deny:
            return RateLimitResult(allowed=False, remaining=0, retry_after_seconds=self.deny[key])
        return RateLimitResult(allowed=True, remaining=limit - 1, retry_after_seconds=0)


@pytest.fixture
def counter(monkeypatch: pytest.MonkeyPatch) -> FakeCounter:
    fake = FakeCounter()
    monkeypatch.setattr(module, "check_rate_limit", fake)
    return fake


def connector_with(profile: RateLimitProfile):  # type: ignore[no-untyped-def]
    return dataclasses.replace(make_connector(), default_rate_limits=profile)


def tool_with(profile: RateLimitProfile):  # type: ignore[no-untyped-def]
    return dataclasses.replace(make_tool(), rate_limits=profile)


# ─── Dựng khoá ───────────────────────────────────────────────────────────────


def test_khoa_tenant_khop_voi_tenant_cache_key() -> None:
    """`tenant_key` phải sinh đúng hình dạng mà `core.context` quy định.

    Hai hàm tồn tại song song vì rate limiter nhận `tenant_id` tường minh còn
    `tenant_cache_key` đọc từ contextvar. Chúng phải cho cùng một chuỗi, nếu
    không thì cùng một tenant sẽ có hai bộ đếm khác nhau tuỳ chỗ gọi.
    """
    with use_context(new_request_context(tenant_id="org_1")):
        assert tenant_key("org_1", "rl", "tenant", "slack") == tenant_cache_key(
            "rl", "tenant", "slack"
        )


def test_chi_dem_nhung_cap_co_nguong() -> None:
    limiter = RedisRateLimiter()
    connector = connector_with(RateLimitProfile(provider=RateLimitRule(600, 60)))
    tool = tool_with(RateLimitProfile(tool=RateLimitRule(20, 60)))

    levels = [level for level, _, _ in limiter.keys(TARGET, tool, connector)]

    assert levels == ["provider", "tool"]


def test_khoa_cap_tool_mang_ca_ket_noi() -> None:
    """Slack tính hạn mức theo method trên từng workspace.

    Thiếu `connection_id` trong khoá thì hai workspace dùng chung một bộ đếm,
    và workspace đông người hơn bóp nghẹt workspace kia.
    """
    limiter = RedisRateLimiter()
    tool = tool_with(RateLimitProfile(tool=RateLimitRule(20, 60)))

    keys = {level: key for level, key, _ in limiter.keys(TARGET, tool, make_connector())}

    assert TARGET.connection_id in keys["tool"]
    assert tool.tool_id in keys["tool"]


def test_cap_provider_khong_gan_tenant() -> None:
    """Hạn mức của cả app ở phía provider là chung cho mọi tenant."""
    limiter = RedisRateLimiter()
    connector = connector_with(RateLimitProfile(provider=RateLimitRule(600, 60)))

    keys = {level: key for level, key, _ in limiter.keys(TARGET, make_tool(), connector)}

    assert keys["provider"].startswith("aw:g:")
    assert TARGET.tenant_id not in keys["provider"]


def test_moi_cap_co_khoa_rieng_biet() -> None:
    limiter = RedisRateLimiter()
    full = RateLimitProfile(
        provider=RateLimitRule(1, 60),
        installation=RateLimitRule(1, 60),
        tenant=RateLimitRule(1, 60),
        connection=RateLimitRule(1, 60),
        user=RateLimitRule(1, 60),
        tool=RateLimitRule(1, 60),
    )
    keys = [key for _, key, _ in limiter.keys(TARGET, tool_with(full), make_connector())]

    assert len(keys) == 6
    assert len(set(keys)) == 6


# ─── Đếm và từ chối ──────────────────────────────────────────────────────────


async def test_dem_du_moi_cap_khi_con_han_muc(counter: FakeCounter) -> None:
    connector = connector_with(RateLimitProfile(provider=RateLimitRule(600, 60)))
    tool = tool_with(RateLimitProfile(tool=RateLimitRule(20, 60)))

    await RedisRateLimiter().acquire(target=TARGET, tool=tool, connector=connector)

    assert [limit for _, limit, _ in counter.seen] == [600, 20]


async def test_vuot_mot_cap_thi_tu_choi_ca_loi_goi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limiter = RedisRateLimiter()
    tool = tool_with(RateLimitProfile(tool=RateLimitRule(20, 60)))
    connector = make_connector()

    tool_key = {lv: k for lv, k, _ in limiter.keys(TARGET, tool, connector)}["tool"]
    monkeypatch.setattr(module, "check_rate_limit", FakeCounter(deny={tool_key: 12}))

    with pytest.raises(RateLimitedError) as caught:
        await limiter.acquire(target=TARGET, tool=tool, connector=connector)

    assert caught.value.retry_after_seconds == 12
    assert "tool" in str(caught.value)


async def test_cho_theo_cap_lau_nhat_khi_nhieu_cap_cung_vuot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hết cấp chặt nhất mà cấp rộng vẫn khoá thì con số kia vô dụng."""
    limiter = RedisRateLimiter()
    connector = connector_with(RateLimitProfile(provider=RateLimitRule(600, 60)))
    tool = tool_with(RateLimitProfile(tool=RateLimitRule(20, 60)))

    keys = {lv: k for lv, k, _ in limiter.keys(TARGET, tool, connector)}
    monkeypatch.setattr(
        module,
        "check_rate_limit",
        FakeCounter(deny={keys["tool"]: 5, keys["provider"]: 90}),
    )

    with pytest.raises(RateLimitedError) as caught:
        await limiter.acquire(target=TARGET, tool=tool, connector=connector)

    assert caught.value.retry_after_seconds == 90


async def test_khong_khai_nguong_nao_thi_khong_dem_gi(counter: FakeCounter) -> None:
    await RedisRateLimiter().acquire(target=TARGET, tool=make_tool(), connector=make_connector())
    assert counter.seen == []


async def test_tool_ghi_de_nguong_cua_connector_o_cap_do(counter: FakeCounter) -> None:
    connector = connector_with(RateLimitProfile(tool=RateLimitRule(100, 60)))
    tool = tool_with(RateLimitProfile(tool=RateLimitRule(20, 60)))

    await RedisRateLimiter().acquire(target=TARGET, tool=tool, connector=connector)

    assert [limit for _, limit, _ in counter.seen] == [20]
