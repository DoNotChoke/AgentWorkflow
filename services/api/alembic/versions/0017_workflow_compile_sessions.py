"""workflow: phiên biên dịch (ADR-0015).

Một hàng cho một lần biến một conversation thành workflow: các trace đã ghim, đoạn chat
của phiên, ý đồ, câu trả lời, bảng giả định và IR đã qua ba tầng kiểm. Tin nhắn của phiên
nằm ở đây, **không** ở `agent.messages`: conversation là nguyên liệu, ghi thêm vào nó là
sửa nguyên liệu giữa lúc đang đọc.

`revision` là khoá lạc quan. Một lời gọi model mất hàng chục giây, và giữ một transaction
mở suốt chừng đó để khoá hàng là cái giá không đáng: ai ghi trước thì thắng, người sau
được bảo tải lại.

Index duy nhất một phần giữ quyết định *mỗi người một phiên đang mở trên một
conversation* ở tầng database, không chỉ ở service.

Không FK sang `agent.conversations` hay `workflow.workflows`: phiên là lịch sử, và nó
không nên chặn việc xoá hai thứ kia.

Revision ID: 0017_compile_sessions
Revises: 0016_workflow_intent
Create Date: 2026-10-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0017_compile_sessions"
down_revision: str | None = "0016_workflow_intent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "workflow"
TABLE = "compile_sessions"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("conversation_id", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("trace_ids", postgresql.JSONB, nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("messages", postgresql.JSONB, nullable=False),
        sa.Column("intent", postgresql.JSONB),
        sa.Column("answers", postgresql.JSONB),
        sa.Column("proposal", postgresql.JSONB),
        sa.Column("ir", postgresql.JSONB),
        sa.Column("workflow_id", sa.String(64)),
        sa.Column("question_set_version", sa.String(16), nullable=False),
        sa.Column("prompt_version", sa.String(16), nullable=False),
        sa.Column("intent_model", sa.String(100), nullable=False),
        sa.Column("answer_model", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_compile_sessions_tenant_conversation",
        TABLE,
        ["tenant_id", "conversation_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "uq_compile_sessions_open",
        TABLE,
        ["conversation_id", "created_by"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status IN ('awaiting_intent', 'awaiting_confirmation')"),
    )


def downgrade() -> None:
    op.drop_index("uq_compile_sessions_open", table_name=TABLE, schema=SCHEMA)
    op.drop_index("ix_compile_sessions_tenant_conversation", table_name=TABLE, schema=SCHEMA)
    op.drop_table(TABLE, schema=SCHEMA)
