"""execution: lần chạy workflow và trạng thái từng node (§13).

- `executions` — một hàng cho mỗi lần chạy. `workflow_version_id` có khoá ngoại
  `RESTRICT` sang `workflow.workflow_versions`: không xoá được version khi còn
  lần chạy trỏ tới nó — ràng buộc mà 0013 để dành cho chỗ này.
- `node_executions` — một hàng cho mỗi lần chạy node, khoá theo
  `(execution_id, node_path)`. Không có payload: nội dung nằm ở trace (Q11).

Revision ID: 0014_execution_records
Revises: 0013_workflow_definitions
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0014_execution_records"
down_revision: str | None = "0013_workflow_definitions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "execution"
EXECUTIONS = "executions"
NODES = "node_executions"


def upgrade() -> None:
    op.create_table(
        EXECUTIONS,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("workflow_id", sa.String(64), nullable=False),
        sa.Column(
            "workflow_version_id",
            sa.String(64),
            sa.ForeignKey("workflow.workflow_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("trigger_type", sa.String(32), nullable=False),
        sa.Column("inputs", postgresql.JSONB, nullable=False),
        sa.Column("initiated_by", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_category", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_executions_tenant_workflow_created",
        EXECUTIONS,
        ["tenant_id", "workflow_id", "created_at"],
        schema=SCHEMA,
    )

    op.create_table(
        NODES,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "execution_id",
            sa.String(64),
            sa.ForeignKey(f"{SCHEMA}.{EXECUTIONS}.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("node_path", sa.String(200), nullable=False),
        sa.Column("node_id", sa.String(64), nullable=False),
        sa.Column("node_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("error_category", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("execution_id", "node_path", name="uq_node_executions_path"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table(NODES, schema=SCHEMA)
    op.drop_table(EXECUTIONS, schema=SCHEMA)
