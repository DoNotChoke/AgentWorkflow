"""Xác thực người dùng: hash mật khẩu, JWT phiên làm việc, refresh token."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError

from app.core.config import Settings
from app.core.context import PrincipalKind
from app.core.errors import AuthenticationError

# ─────────────────────────────────────────────────────────────────────────────
# Mật khẩu — Argon2id
# ─────────────────────────────────────────────────────────────────────────────

_hasher: Final = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)

#: Mật khẩu giả dùng để tạo hash mồi cho ``verify_password_of_missing_user``
_DUMMY_PASSWORD: Final = "khong-phai-mat-khau-that"  # noqa: S105
_dummy_hash: str | None = None


def hash_password(plaintext: str) -> str:
    """Băm mật khẩu. Chuỗi trả về đã chứa sẵn salt và tham số."""
    return _hasher.hash(plaintext)


def verify_password(stored_hash: str, plaintext: str) -> bool:
    """So khớp mật khẩu."""
    try:
        return _hasher.verify(stored_hash, plaintext)
    except (Argon2Error, InvalidHashError):
        return False


def password_needs_rehash(stored_hash: str) -> bool:
    """Hash được tạo bằng tham số cũ hơn tham số hiện tại."""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True


def verify_password_of_missing_user() -> bool:
    """Luôn trả về ``False``, nhưng tốn đúng chừng ấy thời gian như một lần verify thật."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = _hasher.hash(_DUMMY_PASSWORD)
    verify_password(_dummy_hash, "gia-tri-khong-bao-gio-khop")
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Access token — JWT ngắn hạn
# ─────────────────────────────────────────────────────────────────────────────

#: Chỉ chấp nhận HMAC. ``jwt_secret`` là khoá đối xứng; cấu hình RS256/ES256 kèm
#: một secret dạng chuỗi là lỗi cấu hình, và phải hỏng lúc khởi động chứ không
#: phải hỏng lúc có người đăng nhập.
_ALLOWED_ALGORITHMS: Final = frozenset({"HS256", "HS384", "HS512"})

#: Dung sai lệch đồng hồ giữa các tiến trình.
_CLOCK_SKEW_LEEWAY_SECONDS: Final = 10

#: Phân biệt loại token. Không có claim này thì một token bất kỳ do hệ thống ký
#: (ví dụ token xác thực email sau này) đều dùng được như access token.
_TOKEN_TYPE_ACCESS: Final = "access"  # noqa: S105 — nhãn phân loại token, không phải bí mật


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    """Nội dung access token sau khi đã xác minh chữ ký."""

    subject: str
    token_id: str
    issued_at: datetime
    expires_at: datetime
    principal_kind: PrincipalKind = "user"


def issue_access_token(
    settings: Settings,
    *,
    subject: str,
    principal_kind: PrincipalKind = "user",
) -> tuple[str, AccessTokenClaims]:
    """Ký một access token. Trả về ``(token, claims)``."""
    algorithm = _require_supported_algorithm(settings)

    now = datetime.now(tz=UTC)
    claims = AccessTokenClaims(
        subject=subject,
        token_id=f"jti_{uuid.uuid4().hex}",
        issued_at=now,
        expires_at=now + timedelta(seconds=settings.access_token_ttl_seconds),
        principal_kind=principal_kind,
    )

    payload: dict[str, Any] = {
        "sub": claims.subject,
        "jti": claims.token_id,
        "iat": int(claims.issued_at.timestamp()),
        "exp": int(claims.expires_at.timestamp()),
        "iss": settings.jwt_issuer,
        "typ": _TOKEN_TYPE_ACCESS,
        "pk": claims.principal_kind,
    }
    token = jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=algorithm)
    return token, claims


def decode_access_token(settings: Settings, token: str) -> AccessTokenClaims:
    """Xác minh và giải mã access token."""
    algorithm = _require_supported_algorithm(settings)

    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[algorithm],
            issuer=settings.jwt_issuer,
            leeway=_CLOCK_SKEW_LEEWAY_SECONDS,
            options={"require": ["sub", "jti", "iat", "exp", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Token không hợp lệ.", cause=exc) from exc

    if payload.get("typ") != _TOKEN_TYPE_ACCESS:
        raise AuthenticationError("Token không hợp lệ.")

    principal_kind = payload.get("pk", "user")
    if principal_kind not in ("user", "service_account", "workflow", "system"):
        raise AuthenticationError("Token không hợp lệ.")

    return AccessTokenClaims(
        subject=str(payload["sub"]),
        token_id=str(payload["jti"]),
        issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
        expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        principal_kind=principal_kind,
    )


def _require_supported_algorithm(settings: Settings) -> str:
    if settings.jwt_algorithm not in _ALLOWED_ALGORITHMS:
        raise RuntimeError(
            f"AW_JWT_ALGORITHM={settings.jwt_algorithm!r} không được hỗ trợ. "
            f"Chỉ chấp nhận {sorted(_ALLOWED_ALGORITHMS)} vì AW_JWT_SECRET là khoá đối xứng."
        )
    return settings.jwt_algorithm


def extract_bearer_token(authorization_header: str | None) -> str:
    """Tách token khỏi header ``Authorization: Bearer <token>``."""
    if not authorization_header:
        raise AuthenticationError("Thiếu header Authorization.")

    scheme, _, token = authorization_header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthenticationError("Header Authorization phải có dạng 'Bearer <token>'.")
    return token.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Refresh token — chuỗi ngẫu nhiên mờ, xoay vòng mỗi lần dùng
# ─────────────────────────────────────────────────────────────────────────────

#: 48 byte ngẫu nhiên → 384 bit entropy.
_REFRESH_TOKEN_BYTES: Final = 48


def generate_refresh_token() -> tuple[str, str]:
    """Sinh refresh token. Trả về ``(gia_tri_tho, hash_de_luu)``."""
    raw = secrets.token_urlsafe(_REFRESH_TOKEN_BYTES)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    """Băm refresh token bằng SHA-256."""
    return hashlib.sha256(raw.encode()).hexdigest()


def refresh_token_matches(raw: str, stored_hash: str) -> bool:
    """So khớp theo thời gian hằng định."""
    return hmac.compare_digest(hash_refresh_token(raw), stored_hash)


def refresh_token_expires_at(settings: Settings) -> datetime:
    """Thời điểm hết hạn tuyệt đối của một chuỗi refresh token mới."""
    return datetime.now(tz=UTC) + timedelta(seconds=settings.refresh_token_ttl_seconds)
