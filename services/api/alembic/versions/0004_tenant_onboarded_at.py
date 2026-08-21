"""Đánh dấu thời điểm organization hoàn tất bước thiết lập.

Còn NULL nghĩa là đang onboarding, và đó là cửa sổ duy nhất slug còn sửa được.
Sau khi có giá trị, slug đóng băng vì link tới run và workflow dựng từ nó (§41).

Revision ID: 0004_tenant_onboarded_at
Revises: 0003_tenant
Create Date: 2026-08-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_tenant_onboarded_at"
down_revision: str | None = "0003_tenant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "tenant"


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("onboarded_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("organizations", "onboarded_at", schema=SCHEMA)
