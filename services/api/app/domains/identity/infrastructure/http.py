"""Router HTTP của Identity."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.core.errors import AuthenticationError
from app.core.http_auth import require_user_id
from app.core.redis import enforce_rate_limit, global_key
from app.domains.identity.application.ports import TenantProvisioner
from app.domains.identity.application.service import AuthResult, IdentityService
from app.domains.identity.infrastructure.repositories import (
    SqlAlchemyRefreshTokenRepository,
    SqlAlchemyUserRepository,
)

router = APIRouter(prefix="/v1/auth", tags=["identity"])

_LOGIN_ATTEMPTS_PER_EMAIL = 5
_LOGIN_ATTEMPTS_PER_IP = 20
_LOGIN_WINDOW_SECONDS = 300

_REGISTER_ATTEMPTS_PER_IP = 5
_REGISTER_WINDOW_SECONDS = 3600


# ─── Schema vào/ra ───────────────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=1024)
    display_name: str = Field(default="", max_length=200)


class LoginRequest(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=1024)


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    status: str
    email_verified: bool
    created_at: datetime


class SessionResponse(BaseModel):
    """Không bao giờ chứa ``password_hash``, và cũng không chứa refresh token."""

    user: UserResponse
    access_token: str
    token_type: str = "Bearer"  # noqa: S105 — nhãn theo RFC 6750, không phải bí mật
    access_token_expires_at: datetime
    refresh_token_expires_at: datetime


def _to_session_response(result: AuthResult) -> SessionResponse:
    return SessionResponse(
        user=UserResponse(
            id=result.user.id,
            email=result.user.email,
            display_name=result.user.display_name,
            status=str(result.user.status),
            email_verified=result.user.email_verified_at is not None,
            created_at=result.user.created_at,
        ),
        access_token=result.access_token,
        access_token_expires_at=result.access_token_expires_at,
        refresh_token_expires_at=result.refresh_token_expires_at,
    )


# ─── Cookie chứa refresh token ───────────────────────────────────────────────


def _set_refresh_cookie(response: Response, settings: Settings, result: AuthResult) -> None:
    """Gắn refresh token vào cookie httpOnly."""
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=result.refresh_token,
        expires=result.refresh_token_expires_at,
        path=settings.refresh_cookie_path,
        domain=settings.refresh_cookie_domain,
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite=settings.refresh_cookie_samesite,
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    """Xoá cookie. ``path`` và ``domain`` phải khớp lúc set, nếu không trình
    duyệt sẽ để lại một cookie thứ hai và phiên trông như chưa đăng xuất."""
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=settings.refresh_cookie_path,
        domain=settings.refresh_cookie_domain,
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite=settings.refresh_cookie_samesite,
    )


def require_refresh_cookie(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> str:
    """Đọc refresh token từ cookie.

    Không nhận từ body: nếu chấp nhận cả hai đường thì client trình duyệt vẫn
    có thể tự cầm token, và lợi ích của httpOnly biến mất.
    """
    token = request.cookies.get(settings.refresh_cookie_name)
    if not token:
        raise AuthenticationError("Phiên làm việc không còn hiệu lực. Vui lòng đăng nhập lại.")
    return token


# ─── Dependency ──────────────────────────────────────────────────────────────


#: Khoá trong ``app.state`` chứa hàm dựng ``TenantProvisioner`` từ một session.
TENANT_PROVISIONER_FACTORY = "tenant_provisioner_factory"


def get_tenant_provisioner(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TenantProvisioner:
    """Lấy hiện thực ``TenantProvisioner`` mà entrypoint đã gắn vào app."""
    factory = getattr(request.app.state, TENANT_PROVISIONER_FACTORY, None)
    if factory is None:
        raise RuntimeError(
            "Chưa gắn tenant_provisioner_factory vào app.state. "
            "Xem app/entrypoints/http.py — mọi entrypoint phục vụ router này "
            "đều phải nối domain Tenant vào."
        )
    provisioner: TenantProvisioner = factory(session)
    return provisioner


def get_identity_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    tenant_provisioner: Annotated[TenantProvisioner, Depends(get_tenant_provisioner)],
) -> IdentityService:
    return IdentityService(
        settings=settings,
        users=SqlAlchemyUserRepository(session),
        refresh_tokens=SqlAlchemyRefreshTokenRepository(session),
        tenant_provisioner=tenant_provisioner,
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ─── Endpoint ────────────────────────────────────────────────────────────────


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[IdentityService, Depends(get_identity_service)],
) -> SessionResponse:
    await enforce_rate_limit(
        global_key("register", "ip", _client_ip(request)),
        limit=_REGISTER_ATTEMPTS_PER_IP,
        window_seconds=_REGISTER_WINDOW_SECONDS,
        message="Quá nhiều lượt đăng ký từ địa chỉ này. Vui lòng thử lại sau.",
    )
    result = await service.register(
        email=payload.email,
        password=payload.password,
        display_name=payload.display_name,
    )
    _set_refresh_cookie(response, settings, result)
    return _to_session_response(result)


@router.post("/login")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[IdentityService, Depends(get_identity_service)],
) -> SessionResponse:
    # Đếm theo email đã hạ chữ thường, nếu không thì đổi hoa/thường là né được.
    await enforce_rate_limit(
        global_key("login", "email", payload.email.strip().lower()),
        limit=_LOGIN_ATTEMPTS_PER_EMAIL,
        window_seconds=_LOGIN_WINDOW_SECONDS,
    )
    await enforce_rate_limit(
        global_key("login", "ip", _client_ip(request)),
        limit=_LOGIN_ATTEMPTS_PER_IP,
        window_seconds=_LOGIN_WINDOW_SECONDS,
    )
    result = await service.authenticate(email=payload.email, password=payload.password)
    _set_refresh_cookie(response, settings, result)
    return _to_session_response(result)


@router.post("/refresh")
async def refresh(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[IdentityService, Depends(get_identity_service)],
    presented: Annotated[str, Depends(require_refresh_cookie)],
) -> SessionResponse:
    result = await service.refresh(presented)
    _set_refresh_cookie(response, settings, result)
    return _to_session_response(result)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[IdentityService, Depends(get_identity_service)],
) -> None:
    """Thu hồi cả họ token và xoá cookie. Lặp lại nhiều lần vẫn trả 204.

    Không dùng ``require_refresh_cookie``: đăng xuất khi đã không còn cookie
    vẫn phải thành công, nếu không client sẽ kẹt ở trạng thái không thoát được.
    """
    presented = request.cookies.get(settings.refresh_cookie_name)
    if presented:
        await service.logout(presented)
    _clear_refresh_cookie(response, settings)


@router.get("/me")
async def me(
    user_id: Annotated[str, Depends(require_user_id)],
    service: Annotated[IdentityService, Depends(get_identity_service)],
) -> UserResponse:
    user = await service.get_user(user_id)
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        status=str(user.status),
        email_verified=user.email_verified_at is not None,
        created_at=user.created_at,
    )
