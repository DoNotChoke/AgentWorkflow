"""Cấu hình chung cho test.

Ép môi trường test về giá trị xác định, không phụ thuộc file ``.env`` trên máy
người chạy — nếu không, test sẽ đỗ hay trượt tuỳ theo cấu hình cục bộ.
"""

from __future__ import annotations

import os
from typing import NoReturn

import pytest

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
