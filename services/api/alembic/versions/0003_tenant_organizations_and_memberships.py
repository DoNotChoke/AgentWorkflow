"""Bảng của domain Tenant: organization, workspace và hai tầng membership.

Nằm trọn trong schema ``tenant``. Không có khoá ngoại nào trỏ sang schema
``identity`` — ``user_id`` là chuỗi mờ (§18, quy tắc 2 của ADR-0001).

Revision ID: 0003_tenant
Revises: 0002_identity
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_tenant"
down_revision: str | None = "0002_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "tenant"


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(60), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
        schema=SCHEMA,
    )

    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(60), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            [f"{SCHEMA}.organizations.id"],
            name="fk_workspaces_organization_id_organizations",
            ondelete="CASCADE",
        ),
        # Slug workspace chỉ cần duy nhất trong phạm vi một organization: hai
        # khách hàng khác nhau đều được có workspace tên "Default".
        sa.UniqueConstraint("organization_id", "slug", name="uq_workspaces_organization_id_slug"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workspaces_organization_id", "workspaces", ["organization_id"], schema=SCHEMA
    )

    op.create_table(
        "org_memberships",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        # Không có ForeignKey sang identity.users — cấm JOIN chéo schema.
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            [f"{SCHEMA}.organizations.id"],
            name="fk_org_memberships_organization_id_organizations",
            ondelete="CASCADE",
        ),
        # Một user chỉ có đúng một vai trò trong một organization.
        sa.UniqueConstraint(
            "organization_id", "user_id", name="uq_org_memberships_organization_id_user_id"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_org_memberships_organization_id", "org_memberships", ["organization_id"], schema=SCHEMA
    )
    # Tra cứu ở mỗi request: "user này thuộc những organization nào".
    op.create_index("ix_org_memberships_user_id", "org_memberships", ["user_id"], schema=SCHEMA)

    op.create_table(
        "workspace_memberships",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{SCHEMA}.workspaces.id"],
            name="fk_workspace_memberships_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "workspace_id", "user_id", name="uq_workspace_memberships_workspace_id_user_id"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workspace_memberships_workspace_id",
        "workspace_memberships",
        ["workspace_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workspace_memberships_user_id", "workspace_memberships", ["user_id"], schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_table("workspace_memberships", schema=SCHEMA)
    op.drop_table("org_memberships", schema=SCHEMA)
    op.drop_table("workspaces", schema=SCHEMA)
    op.drop_table("organizations", schema=SCHEMA)
