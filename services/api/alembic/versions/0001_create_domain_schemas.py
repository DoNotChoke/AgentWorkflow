"""Tạo một PostgreSQL schema cho mỗi bounded context.

Migration này hiện thực hoá §18 và quy tắc 2 của ADR-0001: mỗi domain sở hữu
schema riêng, không JOIN chéo, không ghi chéo. Phải là migration đầu tiên —
mọi bảng sau này đều nằm trong một trong các schema ở đây.

Revision ID: 0001_create_domain_schemas
Revises:
Create Date: 2026-08-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_create_domain_schemas"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMAS: tuple[str, ...] = (
    "identity",
    "tenant",
    "connection",
    "integration",
    "agent",
    "trace",
    "workflow",
    "execution",
    "approval",
    "usage",
    "billing",
    "audit",
    "notification",
)


def upgrade() -> None:
    for schema in SCHEMAS:
        op.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))


def downgrade() -> None:
    # RESTRICT (mặc định) thay vì CASCADE: nếu schema còn bảng, migration phải
    # thất bại chứ không được âm thầm xoá dữ liệu.
    for schema in reversed(SCHEMAS):
        op.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}"'))
