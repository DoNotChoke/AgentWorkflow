"""Bảng của domain Identity: users và refresh_tokens.

Nằm trọn trong schema ``identity``. Không có khoá ngoại nào trỏ ra ngoài schema
này (§18, quy tắc 2 của ADR-0001).

Revision ID: 0002_identity
Revises: 0001_create_domain_schemas
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_identity"
down_revision: str | None = "0001_create_domain_schemas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "identity"


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'active'")),
        sa.Column("email_verified_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    # Email luôn được hạ chữ thường ở tầng domain trước khi ghi, nên ràng buộc
    # unique thường là đủ và không cần extension citext.
    op.create_unique_constraint("uq_users_email", "users", ["email"], schema=SCHEMA)

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("family_id", sa.String(64), nullable=False),
        # SHA-256 hex. Giá trị thô của token không tồn tại ở bất kỳ đâu.
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("rotated_to_id", sa.String(64)),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{SCHEMA}.users.id"],
            name="fk_refresh_tokens_user_id_users",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    # Tra cứu ở mỗi lần refresh.
    op.create_unique_constraint(
        "uq_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], schema=SCHEMA
    )
    # Thu hồi cả họ khi phát hiện token bị dùng lại, và khi đăng xuất.
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("refresh_tokens", schema=SCHEMA)
    op.drop_table("users", schema=SCHEMA)
