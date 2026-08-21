"""Dependency xác thực dùng chung cho router của mọi domain.

Đặt ở ``app.core`` chứ không ở domain Identity là có lý do: router của Tenant,
Connection, Workflow… đều cần biết "user nào đang gọi", mà import
``domains.identity.infrastructure.http`` từ một domain khác sẽ phá contract 1
của import-linter.

Ở đây chỉ có giải mã token — không chạm database, không biết gì về tenant.
Việc phân giải tenant cần ``TenantService`` nên nằm ở domain Tenant.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header

from app.core.config import Settings, get_settings
from app.core.context import Principal
from app.core.errors import AuthenticationError
from app.core.security import decode_access_token, extract_bearer_token


async def require_user_id(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """Xác thực access token, trả về ``user_id``.

    Chỉ chấp nhận token của người dùng thật. Service account và workflow
    principal có đường xác thực riêng và không đi qua đây (§20).
    """
    claims = decode_access_token(settings, extract_bearer_token(authorization))
    if claims.principal_kind != "user":
        raise AuthenticationError("Token không hợp lệ.")
    return claims.subject


def user_principal(user_id: str, roles: frozenset[str] = frozenset()) -> Principal:
    """Dựng ``Principal`` cho ``RequestContext``.

    ``roles`` là chuỗi đã mã hoá sẵn (``org:owner``, ``ws:admin``) chứ không
    phải enum của domain Tenant — nhờ vậy các domain khác đọc được vai trò từ
    ``RequestContext`` mà không phải import Tenant.
    """
    return Principal(kind="user", id=user_id, roles=roles)
