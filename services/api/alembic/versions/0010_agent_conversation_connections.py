"""agent: conversation_connections — lựa chọn connection theo (conversation, provider) (ADR-0004).

Một bảng, khoá chính composite `(conversation_id, provider)` — không dùng cột
id riêng qua `new_id()` như các bảng khác của domain: đây là bảng ánh xạ
thuần, không có định danh tài nguyên riêng, và khoá tự nhiên đúng bằng khoá
tra cứu mà `ConnectionSelectionStore.get`/`set` luôn dùng.

`connection_id` là một chuỗi bất kỳ mà domain `agent` cầm và chuyển tiếp —
không phải khoá ngoại sang domain `connection` (agent không được import
connection, xem ADR-0003/0004). `tenant_id` denormalize từ conversation cha,
cùng cách `messages.tenant_id` đã làm ở migration 0009.

Revision ID: 0010_agent_conversation
Revises: 0009_agent_agents_conversations
Create Date: 2026-09-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_agent_conversation"
down_revision: str | None = "0009_agent_agents_conversations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "agent"
CONVERSATIONS = "conversations"
TABLE = "conversation_connections"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column(
            "conversation_id",
            sa.String(64),
            sa.ForeignKey(f"{SCHEMA}.{CONVERSATIONS}.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("provider", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("connection_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table(TABLE, schema=SCHEMA)
