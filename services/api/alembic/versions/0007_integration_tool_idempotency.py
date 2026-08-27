"""Sổ idempotency cho tác động ra bên ngoài của tool.

Đúng một bảng, và cố ý chỉ một.

Định nghĩa tool và connector **không** vào database: chúng là tài liệu đã đánh
version và commit trong repo (§40, §41). Một hàng trong bảng cho phép ai đó âm
thầm đổi hành vi của một Workflow đã publish, mà §47 cấm.

Bảng này cũng **không** phải Tool Invocation Trace của §9.1. Trace thuộc về
domain ``trace`` ở Phase 3. Ở đây không có parent step, không có thông tin
model, không có OTel id, không có payload đầy đủ — thêm bất kỳ thứ nào trong số
đó là chiếm quyền sở hữu dữ liệu của domain khác (§18).

Revision ID: 0007_integration_idempotency
Revises: 0006_connection_sharing
Create Date: 2026-08-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_integration_idempotency"
down_revision: str | None = "0006_connection_sharing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "integration"
TABLE = "tool_invocation_idempotency"


def upgrade() -> None:
    op.create_table(
        TABLE,
        # `tenant_id` đứng đầu khoá chính: §15.1 đòi bản ghi idempotency gắn
        # tenant, và đặt nó ở cột đầu khiến mọi lần tra buộc phải mang tenant
        # theo — không có đường nào đọc chéo tenant kể cả do sơ suất.
        sa.Column("tenant_id", sa.String(64), primary_key=True),
        sa.Column("idempotency_key", sa.String(255), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        # Không ForeignKey sang connection.connections — cấm JOIN chéo schema.
        sa.Column("connection_id", sa.String(64), nullable=False),
        sa.Column("tool_id", sa.String(128), nullable=False),
        sa.Column("tool_version", sa.String(16), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("provider_reference", sa.String(255)),
        sa.Column("result", postgresql.JSONB),
        sa.Column("error_category", sa.String(64)),
        sa.Column("last_error", sa.Text()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )

    # Job dọn theo TTL là Phase 7; index để sẵn cho nó.
    op.create_index(
        "ix_tool_invocation_idempotency_expires_at", TABLE, ["expires_at"], schema=SCHEMA
    )
    op.create_index(
        "ix_tool_invocation_idempotency_tenant_tool",
        TABLE,
        ["tenant_id", "tool_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    # Bảng chỉ giữ trạng thái chống lặp, không giữ dữ liệu người dùng, nên xoá
    # thẳng là an toàn — khác với 0006 vốn cần rào chắn.
    op.drop_index("ix_tool_invocation_idempotency_tenant_tool", TABLE, schema=SCHEMA)
    op.drop_index("ix_tool_invocation_idempotency_expires_at", TABLE, schema=SCHEMA)
    op.drop_table(TABLE, schema=SCHEMA)
