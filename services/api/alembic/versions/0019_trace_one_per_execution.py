"""trace: mỗi lần chạy workflow có nhiều nhất một trace.

Trace của một lần chạy được mở bởi một activity của Temporal, và activity có thể chạy
hai lần. Bên mở tra theo `execution_id` trước khi tạo; index này giữ điều đó ở database,
và cũng là đường tra của mọi bước ghi sau đó.

Revision ID: 0019_trace_one_per_execution
Revises: 0018_execution_node_usage
Create Date: 2026-10-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019_trace_one_per_execution"
down_revision: str | None = "0018_execution_node_usage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "uq_execution_traces_execution"


def upgrade() -> None:
    op.create_index(
        INDEX,
        "execution_traces",
        ["execution_id"],
        unique=True,
        schema="trace",
        postgresql_where=sa.text("source = 'workflow'"),
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name="execution_traces", schema="trace")
