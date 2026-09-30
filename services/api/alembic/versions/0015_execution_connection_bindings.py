"""execution: connection mà mỗi lần chạy dùng, theo node tool (ADR-0014).

Revision ID: 0015_execution_bindings
Revises: 0014_execution_records
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0015_execution_bindings"
down_revision: str | None = "0014_execution_records"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "executions",
        sa.Column(
            "connection_bindings",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema="execution",
    )


def downgrade() -> None:
    op.drop_column("executions", "connection_bindings", schema="execution")
