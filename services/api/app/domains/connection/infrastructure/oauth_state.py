"""State của vòng uỷ quyền, và lock khi làm mới token. Cả hai nằm trên Redis.

Vì sao state không nằm ở PostgreSQL: nó phù du theo đúng nghĩa §4.6 nói tới —
sống mười phút, đọc đúng một lần, rồi biến mất. Đặt vào PostgreSQL thì phải kèm
một job dọn rác cho những vòng uỷ quyền mà người dùng bỏ dở, mà số đó không nhỏ.

Redis chết thì lần kết nối đang dang dở hỏng và người dùng bấm lại — phiền,
nhưng không mất dữ liệu và không có gì cần khôi phục. Đó đúng là mức độ bền
vững mà một `state` OAuth cần, không hơn.

Chỗ Redis **không** được dùng làm nguồn chân lý là bản thân Connection và
credential; cả hai đều nằm ở PostgreSQL (§4.6, quy tắc 6 của §47).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from app.core.logging import get_logger
from app.core.redis import global_key, pop_once, put_once, redis_lock
from app.domains.connection.application.ports import PendingAuthorization
from app.domains.connection.domain.entities import SharingPolicy

log = get_logger(__name__)

#: Lock giữ tối đa 30 giây — dài hơn hẳn một lần gọi endpoint token, nhưng vẫn
#: đủ ngắn để một worker chết giữa chừng không khoá connection quá lâu.
_REFRESH_LOCK_TTL_SECONDS = 30
_REFRESH_LOCK_WAIT_SECONDS = 10.0


def _state_key(state: str) -> str:
    """Khoá tra state, đặt tên từ **hash** của state chứ không từ state.

    Cùng lý do refresh token được lưu ở dạng hash: ai đọc được một bản dump
    Redis cũng không lấy ra được `state` còn dùng được từ đó.

    Dùng ``global_key`` vì lúc callback quay về, hệ thống chưa biết tenant nào —
    chính giá trị đang tra mới nói ra điều đó. Đây đúng là trường hợp mà
    docstring của ``global_key`` mô tả.
    """
    digest = hashlib.sha256(state.encode()).hexdigest()
    return global_key("oauth", "state", digest)


class RedisOAuthStateStore:
    """Hiện thực ``OAuthStateStore``."""

    async def put(self, state: str, pending: PendingAuthorization, *, ttl_seconds: int) -> None:
        payload = json.dumps(
            {
                "provider": pending.provider,
                "organization_id": pending.organization_id,
                "workspace_id": pending.workspace_id,
                "user_id": pending.user_id,
                "redirect_uri": pending.redirect_uri,
                "requested_scopes": sorted(pending.requested_scopes),
                "sharing": str(pending.sharing),
                "code_verifier": pending.code_verifier,
                "reconnect_connection_id": pending.reconnect_connection_id,
            },
            separators=(",", ":"),
        )
        await put_once(_state_key(state), payload, ttl_seconds=ttl_seconds)

    async def pop(self, state: str) -> PendingAuthorization | None:
        """Đọc và xoá nguyên tử — ``pop_once`` chạy bằng ``GETDEL``.

        Tính nguyên tử ở đây chính là cơ chế chống replay: một authorization
        code bị chặn lại rồi gửi lần hai sẽ không còn state nào để khớp.
        """
        raw = await pop_once(_state_key(state))
        if raw is None:
            return None

        data: dict[str, Any] = json.loads(raw)
        return PendingAuthorization(
            provider=data["provider"],
            organization_id=data["organization_id"],
            workspace_id=data["workspace_id"],
            user_id=data["user_id"],
            redirect_uri=data["redirect_uri"],
            requested_scopes=frozenset(data["requested_scopes"]),
            sharing=SharingPolicy(data["sharing"]),
            code_verifier=data.get("code_verifier"),
            reconnect_connection_id=data.get("reconnect_connection_id"),
        )


class RedisRefreshLock:
    """Hiện thực ``RefreshLock``."""

    def guard(self, connection_id: str) -> AbstractAsyncContextManager[None]:
        return self._guard(connection_id)

    @asynccontextmanager
    async def _guard(self, connection_id: str) -> AsyncIterator[None]:
        # Khoá theo connection chứ không theo tenant: hai connection của cùng
        # một workspace làm mới song song là chuyện bình thường và không đụng
        # nhau. Khoá rộng hơn chỉ tạo ra hàng đợi không cần thiết.
        async with redis_lock(
            global_key("oauth", "refresh", connection_id),
            ttl_seconds=_REFRESH_LOCK_TTL_SECONDS,
            wait_seconds=_REFRESH_LOCK_WAIT_SECONDS,
        ):
            yield
