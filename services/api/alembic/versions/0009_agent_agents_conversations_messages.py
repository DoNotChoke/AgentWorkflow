"""Agent, Conversation, Message (§5.2, §18).

Ba bảng, đúng hình dạng của `domains/agent/domain/entities.py`:

- `agents` — một trợ lý đã cấu hình. `enabled_tool_ids` là whitelist tường
  minh trải phẳng thành mảng JSON — rỗng nghĩa là không gọi được tool nào.
- `conversations` — một phiên chat với một agent, riêng của người tạo.
- `messages` — một dòng trong hội thoại. `tool_calls` trải phẳng thành mảng
  JSON các `{call_id, tool_id, input}`; `tenant_id` denormalize từ conversation
  cha, cùng cách `trace_steps.tenant_id` đã làm ở migration 0008 — cho kiểm
  tenant ngay ở câu query của message mà không phải JOIN ngược lên trước (§19).

`agents.id` được `conversations.agent_id` tham chiếu bằng FK trong cùng schema
— khác cách domain khác tránh JOIN chéo schema, ở đây không phải JOIN chéo
domain, chỉ là hai bảng của cùng một domain `agent`.

Revision ID: 0009_agent_agents_conversations
Revises: 0008_trace_execution_traces
Create Date: 2026-09-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009_agent_agents_conversations"
down_revision: str | None = "0008_trace_execution_traces"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "agent"
AGENTS = "agents"
CONVERSATIONS = "conversations"
MESSAGES = "messages"


def upgrade() -> None:
    op.create_table(
        AGENTS,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column(
            "enabled_tool_ids",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("max_tool_calls_per_turn", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_agents_tenant_workspace", AGENTS, ["tenant_id", "workspace_id"], schema=SCHEMA
    )

    op.create_table(
        CONVERSATIONS,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "agent_id",
            sa.String(64),
            sa.ForeignKey(f"{SCHEMA}.{AGENTS}.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_conversations_tenant_agent_user",
        CONVERSATIONS,
        ["tenant_id", "agent_id", "user_id"],
        schema=SCHEMA,
    )

    op.create_table(
        MESSAGES,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(64),
            sa.ForeignKey(f"{SCHEMA}.{CONVERSATIONS}.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "tool_calls",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("tool_call_id", sa.String(64)),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_messages_conversation_created_at",
        MESSAGES,
        ["conversation_id", "created_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_messages_conversation_created_at", MESSAGES, schema=SCHEMA)
    op.drop_table(MESSAGES, schema=SCHEMA)
    op.drop_index("ix_conversations_tenant_agent_user", CONVERSATIONS, schema=SCHEMA)
    op.drop_table(CONVERSATIONS, schema=SCHEMA)
    op.drop_index("ix_agents_tenant_workspace", AGENTS, schema=SCHEMA)
    op.drop_table(AGENTS, schema=SCHEMA)
