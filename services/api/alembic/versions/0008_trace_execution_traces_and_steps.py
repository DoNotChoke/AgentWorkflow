"""Execution Trace và các bước của nó (§9, §9.1).

Hai bảng, đúng hình dạng của `domains/trace/domain/entities.py`:

- `execution_traces` — một lần thực thi hoàn chỉnh (một lượt Agent, hoặc sau
  này một lần chạy Workflow). `TraceSubject` trải phẳng thành năm cột định
  danh tuỳ chọn; đúng một nhóm có giá trị theo `source`.
- `trace_steps` — cây các bước bên trong một trace, nối bằng `parent_step_id`.
  Không có ForeignKey tự tham chiếu: `ON DELETE CASCADE` từ `execution_traces`
  đã xoá cả cây trong cùng một lượt.

Cả hai chỉ giữ vân tay của payload (sha256 + kích thước), không giữ nội dung —
nội dung đi qua object storage hoặc log tuỳ nguồn (§9.1).

Revision ID: 0008_trace_execution_traces
Revises: 0007_integration_idempotency
Create Date: 2026-09-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008_trace_execution_traces"
down_revision: str | None = "0007_integration_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "trace"
TRACES = "execution_traces"
STEPS = "trace_steps"


def upgrade() -> None:
    op.create_table(
        TRACES,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        # `TraceSubject` trải phẳng — không ForeignKey sang agent.* hay
        # workflow.*: cấm JOIN chéo schema (§18, quy tắc 3 của ADR-0001).
        sa.Column("agent_id", sa.String(64)),
        sa.Column("conversation_id", sa.String(64)),
        sa.Column("workflow_id", sa.String(64)),
        sa.Column("workflow_version_id", sa.String(64)),
        sa.Column("execution_id", sa.String(64)),
        sa.Column("initiated_by", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_category", sa.String(64)),
        schema=SCHEMA,
    )
    # Lịch sử hội thoại (§27 bước 1): mọi trace của một cuộc hội thoại, mới
    # nhất trước.
    op.create_index(
        "ix_execution_traces_tenant_conversation",
        TRACES,
        ["tenant_id", "conversation_id"],
        schema=SCHEMA,
    )

    op.create_table(
        STEPS,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "trace_id",
            sa.String(64),
            sa.ForeignKey(f"{SCHEMA}.{TRACES}.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Trùng `tenant_id` của trace cha, không chuẩn hoá — cho kiểm tenant
        # ngay ở câu query của bước mà không phải JOIN ngược lên trước (§19).
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("parent_step_id", sa.String(64)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_ref_sha256", sa.String(64)),
        sa.Column("input_ref_byte_size", sa.Integer()),
        sa.Column("output_ref_sha256", sa.String(64)),
        sa.Column("output_ref_byte_size", sa.Integer()),
        sa.Column(
            "attributes",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error_category", sa.String(64)),
        sa.Column("otel_trace_id", sa.String(64)),
        sa.Column("otel_span_id", sa.String(32)),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_trace_steps_trace_id_started_at",
        STEPS,
        ["trace_id", "started_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_trace_steps_trace_id_started_at", STEPS, schema=SCHEMA)
    op.drop_table(STEPS, schema=SCHEMA)
    op.drop_index("ix_execution_traces_tenant_conversation", TRACES, schema=SCHEMA)
    op.drop_table(TRACES, schema=SCHEMA)
