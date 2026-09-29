"""Model SQLAlchemy của Connection."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Computed, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

SCHEMA = "connection"

#: Biểu thức của cột sinh ``sharing_scope``. Migration 0006 dùng lại đúng chuỗi
#: này — để cùng một nơi định nghĩa, hai bản không có cơ hội trôi khỏi nhau.
SHARING_SCOPE_EXPRESSION = "CASE WHEN sharing = 'private' THEN owner_user_id ELSE '@workspace' END"


class ConnectionModel(Base):
    """Metadata của một kết nối. Không có cột nào chứa credential."""

    __tablename__ = "connections"
    __table_args__ = (
        Index(
            "uq_connections_workspace_provider_account_live",
            "workspace_id",
            "provider",
            "external_account_id",
            "sharing_scope",
            unique=True,
            postgresql_where=text("status <> 'revoked' AND external_account_id IS NOT NULL"),
        ),
        # Tên index khai tay cho khớp migration 0005/0006: `index=True` sẽ đặt tên
        # theo naming convention, kèm tên schema, và `alembic check` thấy lệch.
        Index("ix_connections_organization_id", "organization_id"),
        Index("ix_connections_workspace_id", "workspace_id"),
        Index("ix_connections_workspace_owner", "workspace_id", "owner_user_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    organization_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)

    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    granted_scopes: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")

    external_account_id: Mapped[str | None] = mapped_column(String(255))
    external_account_label: Mapped[str | None] = mapped_column(String(255))

    #: Ai bấm nút uỷ quyền gần nhất — dấu vết để truy vết, không phải quyền sở
    #: hữu. Quyền sở hữu nằm ở ``owner_user_id`` và không đổi theo lần uỷ quyền
    #: lại, kể cả khi người bấm là một quản trị viên khác.
    connected_by: Mapped[str] = mapped_column(String(64), nullable=False)

    #: ``workspace`` — tài khoản chung. ``private`` — chỉ ``owner_user_id`` dùng.
    sharing: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False)

    #: Khoá phạm vi, **do PostgreSQL tự tính**
    sharing_scope: Mapped[str] = mapped_column(
        Text,
        Computed(SHARING_SCOPE_EXPRESSION, persisted=True),
        nullable=False,
    )

    #: Hạn của access token. NULL với provider phát token không hết hạn.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    last_error: Mapped[str | None] = mapped_column(String(128))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConnectionCredentialModel(Base):
    """Credential đã mã hoá bằng envelope encryption."""

    __tablename__ = "connection_credentials"
    __table_args__ = ({"schema": SCHEMA},)

    connection_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(f"{SCHEMA}.connections.id", ondelete="CASCADE"),
        primary_key=True,
    )

    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    nonce: Mapped[str] = mapped_column(Text, nullable=False)
    wrapped_data_key: Mapped[str] = mapped_column(Text, nullable=False)
    key_id: Mapped[str] = mapped_column(String(128), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(32), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OrganizationSettingsModel(Base):
    """Chính sách kết nối của một organization."""

    __tablename__ = "organization_settings"
    __table_args__ = ({"schema": SCHEMA},)

    #: Không có ForeignKey sang ``tenant.organizations`` — cấm JOIN chéo schema.
    organization_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    connection_policy: Mapped[str] = mapped_column(String(32), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
