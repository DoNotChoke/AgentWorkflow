"""Dependency xác thực dùng chung cho router của mọi domain."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header

from app.core.config import Settings, get_settings
from app.core.context import Principal
from app.core.errors import AuthenticationError
from app.core.security import decode_access_token, decode_run_token, extract_bearer_token


async def require_user_id(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """Xác thực access token, trả về ``user_id``."""
    claims = decode_access_token(settings, extract_bearer_token(authorization))
    if claims.principal_kind != "user":
        raise AuthenticationError("Token không hợp lệ.")
    return claims.subject


async def require_principal(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """Người dùng, hoặc một lần chạy workflow. Chỉ router nào phục vụ cả hai mới dùng
    dependency này; mọi endpoint của người dùng giữ `require_user_id` và vẫn từ chối
    token của lần chạy."""
    token = extract_bearer_token(authorization)
    try:
        claims = decode_access_token(settings, token)
    except AuthenticationError:
        run = decode_run_token(settings, token)
        return Principal(kind="workflow", id=run.subject)
    if claims.principal_kind != "user":
        raise AuthenticationError("Token không hợp lệ.")
    return Principal(kind="user", id=claims.subject)


def user_principal(user_id: str, roles: frozenset[str] = frozenset()) -> Principal:
    """Dựng ``Principal`` cho ``RequestContext``."""
    return Principal(kind="user", id=user_id, roles=roles)
