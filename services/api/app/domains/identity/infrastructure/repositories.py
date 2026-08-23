"""Hiện thực hoá các port của Identity trên PostgreSQL."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import session_scope
from app.domains.identity.application.ports import EmailAlreadyRegisteredError
from app.domains.identity.domain.entities import RefreshToken, User, UserStatus
from app.domains.identity.infrastructure.models import RefreshTokenModel, UserModel


def _to_user(row: UserModel) -> User:
    return User(
        id=row.id,
        email=row.email,
        password_hash=row.password_hash,
        display_name=row.display_name,
        status=UserStatus(row.status),
        email_verified_at=row.email_verified_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_refresh_token(row: RefreshTokenModel) -> RefreshToken:
    return RefreshToken(
        id=row.id,
        user_id=row.user_id,
        family_id=row.family_id,
        token_hash=row.token_hash,
        issued_at=row.issued_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        rotated_to_id=row.rotated_to_id,
    )


class SqlAlchemyUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_email(self, email: str) -> User | None:
        row = await self._session.scalar(select(UserModel).where(UserModel.email == email))
        return None if row is None else _to_user(row)

    async def get_by_id(self, user_id: str) -> User | None:
        row = await self._session.scalar(select(UserModel).where(UserModel.id == user_id))
        return None if row is None else _to_user(row)

    async def list_by_ids(self, user_ids: Sequence[str]) -> list[User]:
        # Lô rỗng thì đừng bắn câu truy vấn nào — `IN ()` vừa vô nghĩa vừa là
        # cú pháp không hợp lệ ở một số phương ngữ SQL.
        if not user_ids:
            return []
        rows = await self._session.scalars(
            select(UserModel).where(UserModel.id.in_(list(user_ids)))
        )
        return [_to_user(row) for row in rows]

    async def add(self, user: User) -> None:
        try:
            # SAVEPOINT chứ không phải transaction ngoài: khi ràng buộc unique
            # nổ, chỉ câu INSERT này bị huỷ. Nếu ở đây gọi thẳng
            # ``session.rollback()`` thì cả unit of work của request bị cuốn
            # theo, và phía gọi không còn session dùng được để xử lý lỗi.
            async with self._session.begin_nested():
                self._session.add(
                    UserModel(
                        id=user.id,
                        email=user.email,
                        password_hash=user.password_hash,
                        display_name=user.display_name,
                        status=str(user.status),
                        email_verified_at=user.email_verified_at,
                        created_at=user.created_at,
                        updated_at=user.updated_at,
                    )
                )
                # Đẩy xuống database ngay để ràng buộc unique lên tiếng tại
                # đây, thay vì lúc commit — khi đó đã ra khỏi phạm vi xử lý.
                await self._session.flush()
        except IntegrityError as exc:
            raise EmailAlreadyRegisteredError(user.email) from exc

    async def update_password_hash(self, user_id: str, password_hash: str, *, at: datetime) -> None:
        await self._session.execute(
            update(UserModel)
            .where(UserModel.id == user_id)
            .values(password_hash=password_hash, updated_at=at)
        )


class SqlAlchemyRefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, token: RefreshToken) -> None:
        self._session.add(
            RefreshTokenModel(
                id=token.id,
                user_id=token.user_id,
                family_id=token.family_id,
                token_hash=token.token_hash,
                issued_at=token.issued_at,
                expires_at=token.expires_at,
                revoked_at=token.revoked_at,
                rotated_to_id=token.rotated_to_id,
            )
        )
        await self._session.flush()

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        row = await self._session.scalar(
            select(RefreshTokenModel).where(RefreshTokenModel.token_hash == token_hash)
        )
        return None if row is None else _to_refresh_token(row)

    async def mark_rotated(self, token_id: str, *, rotated_to_id: str, at: datetime) -> None:
        await self._session.execute(
            update(RefreshTokenModel)
            .where(RefreshTokenModel.id == token_id)
            .values(rotated_to_id=rotated_to_id, revoked_at=at)
        )

    async def revoke_family(self, family_id: str, *, at: datetime) -> None:
        """Thu hồi cả họ token, trong transaction riêng của chính nó."""
        async with session_scope() as session:
            await session.execute(
                update(RefreshTokenModel)
                .where(
                    RefreshTokenModel.family_id == family_id,
                    RefreshTokenModel.revoked_at.is_(None),
                )
                .values(revoked_at=at)
            )
