"""integration: lease cho hàng in_flight của sổ idempotency.

`claim_token` là ai đang giữ quyền chạy; mọi lần chốt kết quả phải mang đúng
token đó. `lease_expires_at` là mốc mà quá nó, hàng vẫn `in_flight` nghĩa là
người giữ đã chết.

Hàng ghi trước migration này để cả hai cột NULL, và domain đọc lease NULL thành
đã quá hạn. Không có server_default vì cùng lý do với 0011: giá trị mặc định sẽ
nói rằng những hàng ấy có một lease, trong khi chúng chưa từng có.

Revision ID: 0012_integration_idem_lease
Revises: 0011_trace_step_payloads
Create Date: 2026-09-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_integration_idem_lease"
down_revision: str | None = "0011_trace_step_payloads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "integration"
TABLE = "tool_invocation_idempotency"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("claim_token", sa.String(64)), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("lease_expires_at", sa.DateTime(timezone=True)), schema=SCHEMA)


def downgrade() -> None:
    op.drop_column(TABLE, "lease_expires_at", schema=SCHEMA)
    op.drop_column(TABLE, "claim_token", schema=SCHEMA)
