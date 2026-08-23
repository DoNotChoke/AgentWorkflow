"""Chỗ nối các domain lại với nhau.

Contract 1 của import-linter cấm domain import chéo nhau, nên mọi lần một domain
cần dữ liệu của domain khác đều đi theo cùng một hình: bên cần khai một Protocol
ở tầng `application` của mình, bên có hiện thực hoá, và **module này** là nơi
duy nhất biết cả hai để buộc dây.

Adapter phải nằm ở đây chứ không nằm trong domain nào: đặt nó vào Identity thì
Identity phải import kiểu dữ liệu của Tenant, đặt vào Tenant thì ngược lại — cả
hai đều phá contract 1.

Module này cũng không được import thẳng tầng `domain` của bất kỳ ai (contract 5).
Vì thế nó chỉ đọc thuộc tính của entity Identity trả về chứ không chú kiểu cho
chúng — đọc `user.email` không cần biết `User` là gì.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity.infrastructure.repositories import SqlAlchemyUserRepository
from app.domains.tenant.application.ports import UserDirectory, UserProfile


def _to_profile(user: object) -> UserProfile:
    """Chuyển một ``User`` của Identity sang DTO mà Tenant đặt hàng.

    Tham số để kiểu ``object`` là có chủ đích: chú đúng kiểu ``User`` sẽ phải
    import ``identity.domain``, mà contract 5 cấm entrypoint làm thế. Đọc thuộc
    tính thì không cần biết kiểu.
    """
    return UserProfile(
        user_id=user.id,  # type: ignore[attr-defined]
        email=user.email,  # type: ignore[attr-defined]
        display_name=user.display_name,  # type: ignore[attr-defined]
    )


class IdentityUserDirectory:
    """Hiện thực ``UserDirectory`` của Tenant bằng dữ liệu của Identity."""

    def __init__(self, users: SqlAlchemyUserRepository) -> None:
        self._users = users

    async def get_profiles(self, user_ids: Sequence[str]) -> dict[str, UserProfile]:
        found = await self._users.list_by_ids(user_ids)
        return {user.id: _to_profile(user) for user in found}

    async def find_by_email(self, email: str) -> UserProfile | None:
        # Identity lưu email đã `strip().lower()`, nên tra cũng phải cùng dạng.
        # Đây là chỗ đúng để làm việc đó: module này biết dữ liệu Identity nằm ở
        # hình dạng nào, còn Tenant thì không — và cũng không được phép biết.
        #
        # Không tái dùng `normalize_email` của Identity vì nó nằm ở tầng `domain`
        # mà entrypoint không được import thẳng (contract 5). Ở đây chỉ cần khớp
        # khoá tra cứu, không cần kiểm định dạng — email sai cú pháp đơn giản là
        # không khớp ai cả.
        found = await self._users.get_by_email(email.strip().lower())
        return None if found is None else _to_profile(found)


def build_user_directory(session: AsyncSession) -> UserDirectory:
    """Dựng ``UserDirectory`` từ một session, để gắn vào ``app.state``."""
    return IdentityUserDirectory(SqlAlchemyUserRepository(session))
