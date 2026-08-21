"""Thực thể và quy tắc nghiệp vụ của Identity."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.core.errors import ValidationError


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class User:
    id: str
    email: str
    password_hash: str
    display_name: str
    status: UserStatus
    email_verified_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @property
    def can_authenticate(self) -> bool:
        return self.status is UserStatus.ACTIVE


# ─────────────────────────────────────────────────────────────────────────────
# Email
# ─────────────────────────────────────────────────────────────────────────────

_EMAIL_SHAPE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

MAX_EMAIL_LENGTH = 320  # RFC 3696


def normalize_email(raw: str) -> str:
    """Chuẩn hoá email về dạng duy nhất dùng để so khớp và lưu trữ."""
    email = raw.strip().lower()

    if not email:
        raise ValidationError("Email không được để trống.")
    if len(email) > MAX_EMAIL_LENGTH:
        raise ValidationError(f"Email không được dài quá {MAX_EMAIL_LENGTH} ký tự.")
    if not _EMAIL_SHAPE.match(email):
        raise ValidationError("Email không đúng định dạng.")
    return email


# ─────────────────────────────────────────────────────────────────────────────
# Mật khẩu
# ─────────────────────────────────────────────────────────────────────────────

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024


def validate_password_strength(password: str) -> None:
    """Chỉ ràng buộc độ dài, không ràng buộc thành phần ký tự."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(f"Mật khẩu phải dài ít nhất {MIN_PASSWORD_LENGTH} ký tự.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValidationError(f"Mật khẩu không được dài quá {MAX_PASSWORD_LENGTH} ký tự.")


MAX_DISPLAY_NAME_LENGTH = 200


def normalize_display_name(raw: str, *, fallback_email: str) -> str:
    """Tên hiển thị rỗng thì lấy phần trước ``@`` của email."""
    name = raw.strip()
    if not name:
        name = fallback_email.partition("@")[0]
    return name[:MAX_DISPLAY_NAME_LENGTH]


# ─────────────────────────────────────────────────────────────────────────────
# Refresh token
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RefreshToken:
    """Một mắt xích trong chuỗi refresh token xoay vòng."""

    id: str
    user_id: str
    family_id: str
    token_hash: str
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    rotated_to_id: str | None = None

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def was_rotated(self) -> bool:
        return self.rotated_to_id is not None


class TokenVerdict(StrEnum):
    """Kết luận về một refresh token do client trình lên."""

    VALID = "valid"
    UNKNOWN = "unknown"
    EXPIRED = "expired"
    REUSED = "reused"


def classify_refresh_token(token: RefreshToken | None, now: datetime) -> TokenVerdict:
    """Phân loại token được trình lên. Hàm thuần, không chạm database."""
    if token is None:
        return TokenVerdict.UNKNOWN
    if token.was_rotated or token.is_revoked:
        return TokenVerdict.REUSED
    if token.is_expired(now):
        return TokenVerdict.EXPIRED
    return TokenVerdict.VALID
