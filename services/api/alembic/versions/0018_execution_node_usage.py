"""execution: token model mà một node đã dùng.

Node `llm` gọi model mỗi lần workflow chạy, và trong thân một vòng lặp thì gọi một lần
cho mỗi phần tử. Số token nằm cạnh trạng thái của chính lần chạy node đó; bảng tổng hợp
chi phí theo workflow chưa có.

Revision ID: 0018_execution_node_usage
Revises: 0017_compile_sessions
Create Date: 2026-10-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0018_execution_node_usage"
down_revision: str | None = "0017_compile_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("node_executions", sa.Column("usage", postgresql.JSONB), schema="execution")


def downgrade() -> None:
    op.drop_column("node_executions", "usage", schema="execution")
