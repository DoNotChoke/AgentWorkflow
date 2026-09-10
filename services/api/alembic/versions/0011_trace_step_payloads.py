"""trace: payload của bước trace — inline hoặc object storage (WP-A).

Bốn cột trên `trace_steps`, hai cho mỗi phía. `*_location` nhận
`absent | inline | object` và là thứ quyết định đọc ở đâu; `*_inline` giữ chính
payload khi nó đủ nhỏ (§4.7 chỉ đòi payload LỚN ra object storage).

Hàng ghi trước migration này để `location` NULL và repository đọc thành
`absent` — đúng sự thật: chúng chỉ có vân tay, payload đã bị vứt đi lúc ghi.
Vì vậy không có server_default: một giá trị mặc định sẽ nói rằng những hàng ấy
đã được xem xét, trong khi thật ra chúng chưa từng có payload nào.

Revision ID: 0011_trace_step_payloads
Revises: 0010_agent_conversation
Create Date: 2026-09-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011_trace_step_payloads"
down_revision: str | None = "0010_agent_conversation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "trace"
TABLE = "trace_steps"

COLUMNS = ("input_location", "input_inline", "output_location", "output_inline")


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("input_location", sa.String(8)), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("input_inline", postgresql.JSONB), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("output_location", sa.String(8)), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("output_inline", postgresql.JSONB), schema=SCHEMA)


def downgrade() -> None:
    for column in COLUMNS:
        op.drop_column(TABLE, column, schema=SCHEMA)
