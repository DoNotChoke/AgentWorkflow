"""Interface mà tầng `infrastructure` phải hiện thực hoá."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.domains.identity.domain.entities import RefreshToken, User


class UserRepository(Protocol):
    async def get_by_email(self, email: str) -> User | None: ...

    async def get_by_id(self, user_id: str) -> User | None: ...

    async def add(self, user: User) -> None:
        """Thêm user mới."""

    async def update_password_hash(self, user_id: str, password_hash: str, *, at: datetime) -> None:
        """Cập nhật hash"""


class RefreshTokenRepository(Protocol):
    async def add(self, token: RefreshToken) -> None: ...

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None: ...

    async def mark_rotated(self, token_id: str, *, rotated_to_id: str, at: datetime) -> None:
        """Đánh dấu token đã sinh ra token kế tiếp, đồng thời thu hồi nó."""

    async def revoke_family(self, family_id: str, *, at: datetime) -> None:
        """Thu hồi toàn bộ chuỗi xoay vòng sinh ra từ một lần đăng nhập."""


class TenantProvisioner(Protocol):
    """Cấp tenant cho một user vừa đăng ký."""

    async def provision_for_new_user(self, *, user_id: str, display_name: str) -> object:
        """Trả về quyền vừa cấp. Identity không đọc giá trị trả về."""


class EmailAlreadyRegisteredError(Exception):
    """Repository báo lên rằng ràng buộc unique của email bị vi phạm."""
