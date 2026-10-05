"""workflow: ý đồ và nguồn gốc đi cùng draft và version (ADR-0015).

`intent` mô tả đúng IR nằm cạnh nó, nên nó ở draft và ở version, không ở `workflows`:
v1 và v5 của cùng một workflow có thể làm hai việc khác nhau. `compiled_from` là id
phiên biên dịch đã sinh ra IR đó.

Thêm cột vào `workflow_versions` không đụng trigger bất biến của 0013: trigger chặn
UPDATE trên hàng, không chặn đổi hình dạng bảng. Version đã có thì hai cột là NULL.

Revision ID: 0016_workflow_intent
Revises: 0015_execution_bindings
Create Date: 2026-10-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0016_workflow_intent"
down_revision: str | None = "0015_execution_bindings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "workflow"
TABLES = ("workflow_drafts", "workflow_versions")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("intent", postgresql.JSONB), schema=SCHEMA)
        op.add_column(table, sa.Column("compiled_from", sa.String(64)), schema=SCHEMA)


def downgrade() -> None:
    for table in TABLES:
        op.drop_column(table, "compiled_from", schema=SCHEMA)
        op.drop_column(table, "intent", schema=SCHEMA)
