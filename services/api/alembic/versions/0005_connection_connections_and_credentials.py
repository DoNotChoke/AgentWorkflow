"""Bảng của domain Connection: metadata kết nối và credential đã mã hoá.

Nằm trọn trong schema ``connection``. Không có khoá ngoại nào trỏ sang
``tenant`` hay ``identity`` — ``organization_id``, ``workspace_id`` và
``connected_by`` đều là chuỗi mờ (§18, quy tắc 2 của ADR-0001).

Credential ở bảng riêng chứ không phải vài cột thêm vào ``connections``: xem
docstring của ``app/domains/connection/infrastructure/models.py``.

Revision ID: 0005_connection
Revises: 0004_tenant_onboarded_at
Create Date: 2026-08-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_connection"
down_revision: str | None = "0004_tenant_onboarded_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "connection"


def upgrade() -> None:
    op.create_table(
        "connections",
        sa.Column("id", sa.String(64), primary_key=True),
        # Không có ForeignKey sang tenant.* — cấm JOIN chéo schema.
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        # Scope ngăn bởi dấu cách — quy ước nội bộ, không phải của provider.
        sa.Column("granted_scopes", sa.Text(), nullable=False, server_default=""),
        sa.Column("external_account_id", sa.String(255), nullable=True),
        sa.Column("external_account_label", sa.String(255), nullable=True),
        sa.Column("connected_by", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        # Mã lỗi đã chuẩn hoá, không bao giờ là phản hồi thô của provider.
        sa.Column("last_error", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_connections_organization_id", "connections", ["organization_id"], schema=SCHEMA
    )
    # Truy vấn nóng nhất: liệt kê kết nối của một workspace.
    op.create_index("ix_connections_workspace_id", "connections", ["workspace_id"], schema=SCHEMA)

    # Một tài khoản provider chỉ gắn vào một workspace đúng một lần — nhưng chỉ
    # tính những kết nối còn sống. Index bộ phận thay vì UniqueConstraint
    # thường, để sau khi thu hồi thì kết nối lại chính tài khoản đó vẫn được.
    op.create_index(
        "uq_connections_workspace_provider_account_live",
        "connections",
        ["workspace_id", "provider", "external_account_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status <> 'revoked' AND external_account_id IS NOT NULL"),
    )

    op.create_table(
        "connection_credentials",
        sa.Column("connection_id", sa.String(64), primary_key=True),
        # Các cột dưới đây ánh xạ một–một với EncryptedPayload.to_storage().
        # Không cột nào đọc được nếu thiếu master key.
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("nonce", sa.Text(), nullable=False),
        sa.Column("wrapped_data_key", sa.Text(), nullable=False),
        # Lưu kèm để một lần xoay khoá không biến cả bảng thành rác.
        sa.Column("key_id", sa.String(128), nullable=False),
        sa.Column("algorithm", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            [f"{SCHEMA}.connections.id"],
            name="fk_connection_credentials_connection_id_connections",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("connection_credentials", schema=SCHEMA)
    op.drop_index("uq_connections_workspace_provider_account_live", "connections", schema=SCHEMA)
    op.drop_index("ix_connections_workspace_id", "connections", schema=SCHEMA)
    op.drop_index("ix_connections_organization_id", "connections", schema=SCHEMA)
    op.drop_table("connections", schema=SCHEMA)
