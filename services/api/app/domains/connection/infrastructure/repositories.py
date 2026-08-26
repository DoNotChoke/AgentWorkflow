"""Hiện thực ``ConnectionRepository`` trên PostgreSQL.

Không có phương thức nào ở đây đọc hay ghi credential. Bảng credential do
``credential_store.py`` sở hữu riêng, và ranh giới đó là cố ý — xem docstring
của ``models.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.connection.domain.entities import (
    Connection,
    ConnectionPolicy,
    ConnectionStatus,
    SharingPolicy,
    format_scopes,
    parse_scopes,
)
from app.domains.connection.infrastructure.models import (
    ConnectionModel,
    OrganizationSettingsModel,
)


def _to_connection(row: ConnectionModel) -> Connection:
    return Connection(
        id=row.id,
        organization_id=row.organization_id,
        workspace_id=row.workspace_id,
        provider=row.provider,
        display_name=row.display_name,
        status=ConnectionStatus(row.status),
        granted_scopes=parse_scopes(row.granted_scopes),
        connected_by=row.connected_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        sharing=SharingPolicy(row.sharing),
        owner_user_id=row.owner_user_id,
        external_account_id=row.external_account_id,
        external_account_label=row.external_account_label,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        last_error=row.last_error,
    )


class SqlAlchemyConnectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, connection: Connection) -> None:
        self._session.add(
            ConnectionModel(
                id=connection.id,
                organization_id=connection.organization_id,
                workspace_id=connection.workspace_id,
                provider=connection.provider,
                display_name=connection.display_name,
                status=str(connection.status),
                granted_scopes=format_scopes(connection.granted_scopes),
                external_account_id=connection.external_account_id,
                external_account_label=connection.external_account_label,
                connected_by=connection.connected_by,
                sharing=str(connection.sharing),
                owner_user_id=connection.owner_user_id,
                expires_at=connection.expires_at,
                revoked_at=connection.revoked_at,
                last_error=connection.last_error,
                created_at=connection.created_at,
                updated_at=connection.updated_at,
            )
        )
        await self._session.flush()

    async def get_by_id(self, connection_id: str) -> Connection | None:
        row = await self._session.scalar(
            select(ConnectionModel).where(ConnectionModel.id == connection_id)
        )
        return None if row is None else _to_connection(row)

    async def list_visible(self, *, workspace_id: str, user_id: str) -> list[Connection]:
        """Lọc ngay trong mệnh đề WHERE, không lọc lại trong Python.

        Kết nối PRIVATE của người khác không được rời khỏi database. Lấy hết về
        rồi lọc sau nghĩa là chỉ cần một chỗ quên gọi bộ lọc là chúng lọt thẳng
        vào phản hồi.
        """
        rows = await self._session.scalars(
            select(ConnectionModel)
            .where(
                ConnectionModel.workspace_id == workspace_id,
                or_(
                    ConnectionModel.sharing == str(SharingPolicy.WORKSPACE),
                    ConnectionModel.owner_user_id == user_id,
                ),
            )
            .order_by(ConnectionModel.created_at)
        )
        return [_to_connection(row) for row in rows]

    async def list_all_for_audit(self, workspace_id: str) -> list[Connection]:
        """Mọi kết nối, kể cả PRIVATE của người khác. Nơi gọi phải kiểm quyền."""
        rows = await self._session.scalars(
            select(ConnectionModel)
            .where(ConnectionModel.workspace_id == workspace_id)
            .order_by(ConnectionModel.created_at)
        )
        return [_to_connection(row) for row in rows]

    async def find_active_for_account(
        self,
        *,
        workspace_id: str,
        provider: str,
        external_account_id: str,
        sharing: SharingPolicy,
        owner_user_id: str,
    ) -> Connection | None:
        # Phải khớp cả phạm vi, nếu không thì lần uỷ quyền riêng của An sẽ ghi
        # đè lên kết nối dùng chung của workspace — cùng tài khoản provider,
        # nhưng là hai kết nối khác nhau.
        scope = (
            ConnectionModel.owner_user_id == owner_user_id
            if sharing is SharingPolicy.PRIVATE
            else ConnectionModel.sharing == str(SharingPolicy.WORKSPACE)
        )
        row = await self._session.scalar(
            select(ConnectionModel).where(
                ConnectionModel.workspace_id == workspace_id,
                ConnectionModel.provider == provider,
                ConnectionModel.external_account_id == external_account_id,
                ConnectionModel.sharing == str(sharing),
                scope,
                # Kết nối đã thu hồi không được nhận nhầm thành đích cập nhật:
                # trạng thái kết là kết, và người dùng vừa cố tình tạo cái mới.
                ConnectionModel.status != str(ConnectionStatus.REVOKED),
            )
        )
        return None if row is None else _to_connection(row)

    async def update_authorization(
        self,
        connection_id: str,
        *,
        status: ConnectionStatus,
        granted_scopes: frozenset[str],
        expires_at: datetime | None,
        external_account_id: str | None,
        external_account_label: str | None,
        connected_by: str,
        at: datetime,
    ) -> None:
        await self._session.execute(
            update(ConnectionModel)
            .where(ConnectionModel.id == connection_id)
            .values(
                status=str(status),
                granted_scopes=format_scopes(granted_scopes),
                expires_at=expires_at,
                external_account_id=external_account_id,
                external_account_label=external_account_label,
                # Ai bấm nút gần nhất. `owner_user_id` không đổi — quyền sở hữu
                # không chuyển tay qua một lần uỷ quyền lại.
                connected_by=connected_by,
                # Uỷ quyền lại thành công thì lỗi cũ hết ý nghĩa. Để nguyên nó
                # sẽ khiến UI vẫn mời người dùng kết nối lại một kết nối đang tốt.
                last_error=None,
                revoked_at=None,
                updated_at=at,
            )
        )

    async def update_status(
        self,
        connection_id: str,
        *,
        status: ConnectionStatus,
        at: datetime,
        last_error: str | None = None,
        revoked_at: datetime | None = None,
    ) -> None:
        values: dict[str, object] = {
            "status": str(status),
            "last_error": last_error,
            "updated_at": at,
        }
        if revoked_at is not None:
            values["revoked_at"] = revoked_at

        await self._session.execute(
            update(ConnectionModel).where(ConnectionModel.id == connection_id).values(**values)
        )

    async def update_expiry(
        self, connection_id: str, *, expires_at: datetime | None, at: datetime
    ) -> None:
        await self._session.execute(
            update(ConnectionModel)
            .where(ConnectionModel.id == connection_id)
            .values(expires_at=expires_at, updated_at=at)
        )

    async def rename(self, connection_id: str, *, display_name: str, at: datetime) -> None:
        await self._session.execute(
            update(ConnectionModel)
            .where(ConnectionModel.id == connection_id)
            .values(display_name=display_name, updated_at=at)
        )

    async def disable_private_in_organization(self, organization_id: str, *, at: datetime) -> int:
        """Tạm dừng mọi kết nối riêng của một organization.

        Chạy khi org siết chính sách từ OPEN sang MANAGED. Dùng ``DISABLED``
        chứ không xoá: nó giữ nguyên credential, nên nếu org đổi ý thì người
        dùng bật lại được. Xoá thẳng là hành động không hoàn tác được, trên dữ
        liệu của người khác, do một quyết định cấu hình.
        """
        statement = (
            update(ConnectionModel)
            .where(
                ConnectionModel.organization_id == organization_id,
                ConnectionModel.sharing == str(SharingPolicy.PRIVATE),
                ConnectionModel.status.notin_(
                    [str(ConnectionStatus.REVOKED), str(ConnectionStatus.DISABLED)]
                ),
            )
            .values(status=str(ConnectionStatus.DISABLED), updated_at=at)
        )
        # `session.execute` khai kiểu trả về là `Result`, nhưng với một câu
        # UPDATE thì nó luôn là `CursorResult` — chỉ lớp đó mới có `rowcount`.
        result = cast("CursorResult[Any]", await self._session.execute(statement))
        return result.rowcount


class SqlAlchemyOrganizationSettingsRepository:
    """Hiện thực ``OrganizationSettingsRepository``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_policy(self, organization_id: str) -> ConnectionPolicy | None:
        row = await self._session.scalar(
            select(OrganizationSettingsModel).where(
                OrganizationSettingsModel.organization_id == organization_id
            )
        )
        return None if row is None else ConnectionPolicy(row.connection_policy)

    async def set_policy(
        self, organization_id: str, *, policy: ConnectionPolicy, at: datetime
    ) -> None:
        # UPSERT: org chưa có row là trạng thái bình thường, không phải lỗi.
        await self._session.execute(
            pg_insert(OrganizationSettingsModel)
            .values(
                organization_id=organization_id,
                connection_policy=str(policy),
                created_at=at,
                updated_at=at,
            )
            .on_conflict_do_update(
                index_elements=[OrganizationSettingsModel.organization_id],
                set_={"connection_policy": str(policy), "updated_at": at},
            )
        )
