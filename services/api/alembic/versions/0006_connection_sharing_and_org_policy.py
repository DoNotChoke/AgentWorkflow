"""Quyền sở hữu kết nối và chính sách kết nối cấp organization.

Một kết nối giờ có hai trục:

- ``sharing`` — ``workspace`` (tài khoản chung) hay ``private`` (tài khoản riêng);
- ``owner_user_id`` — với ``private`` là người **duy nhất** dùng được.

Và org có một chính sách quyết định trục trên được nhận giá trị nào.

Backfill không mơ hồ, và đó là lý do migration này nên chạy sớm: mọi kết nối
đang tồn tại đều do quản trị viên workspace tạo và đều dùng chung, vì trước đây
hệ thống chưa có khái niệm kết nối riêng. Để lâu hơn thì sẽ phải *đoán* kết nối
nào từng là cá nhân.

Revision ID: 0006_connection_sharing
Revises: 0005_connection
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_connection_sharing"
down_revision: str | None = "0005_connection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "connection"

#: Phải khớp với ``models.SHARING_SCOPE_EXPRESSION``.
SHARING_SCOPE_EXPRESSION = "CASE WHEN sharing = 'private' THEN owner_user_id ELSE '@workspace' END"

_LIVE_ACCOUNT = "status <> 'revoked' AND external_account_id IS NOT NULL"


def upgrade() -> None:
    # ─── Quyền sở hữu ────────────────────────────────────────────────────────
    # Thêm ở dạng nullable trước, backfill, rồi mới siết NOT NULL. Thêm thẳng
    # NOT NULL sẽ thất bại trên bảng đã có dữ liệu.
    op.add_column("connections", sa.Column("sharing", sa.String(32)), schema=SCHEMA)
    op.add_column("connections", sa.Column("owner_user_id", sa.String(64)), schema=SCHEMA)

    # Dùng bảng đã khai báo thay vì ghép chuỗi SQL: tên schema là hằng trong
    # code chứ không phải input, nhưng viết kiểu này thì không ai phải chứng
    # minh điều đó lại mỗi lần đọc.
    connections = sa.table(
        "connections",
        sa.column("sharing", sa.String),
        sa.column("owner_user_id", sa.String),
        sa.column("connected_by", sa.String),
        schema=SCHEMA,
    )
    op.execute(
        connections.update()
        .where(connections.c.sharing.is_(None))
        .values(sharing="workspace", owner_user_id=connections.c.connected_by)
    )

    op.alter_column("connections", "sharing", nullable=False, schema=SCHEMA)
    op.alter_column("connections", "owner_user_id", nullable=False, schema=SCHEMA)

    # ─── Khoá phạm vi ────────────────────────────────────────────────────────
    # Cột sinh, do PostgreSQL tự tính. Ứng dụng không ghi được vào nó, nên
    # không có đường nào để khoá phạm vi lệch khỏi `sharing`/`owner_user_id`.
    op.add_column(
        "connections",
        sa.Column(
            "sharing_scope",
            sa.Text(),
            sa.Computed(SHARING_SCOPE_EXPRESSION, persisted=True),
            nullable=False,
        ),
        schema=SCHEMA,
    )

    # Index cũ chỉ gồm (workspace, provider, account) nên sẽ báo trùng oan giữa
    # ba thứ vốn khác nhau: kết nối chung của workspace, kết nối riêng của An,
    # và kết nối riêng của Bình — dù cả ba cùng trỏ tới một tài khoản provider.
    op.drop_index("uq_connections_workspace_provider_account_live", "connections", schema=SCHEMA)
    op.create_index(
        "uq_connections_workspace_provider_account_live",
        "connections",
        ["workspace_id", "provider", "external_account_id", "sharing_scope"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text(_LIVE_ACCOUNT),
    )

    # Đường đọc nóng mới: "kết nối mà người này nhìn thấy trong workspace".
    op.create_index(
        "ix_connections_workspace_owner",
        "connections",
        ["workspace_id", "owner_user_id"],
        schema=SCHEMA,
    )

    # ─── Chính sách cấp organization ─────────────────────────────────────────
    # Không có row = chưa cấu hình, và ứng dụng áp dụng mặc định. Nhờ vậy bảng
    # này sinh ra rỗng và không cần backfill.
    op.create_table(
        "organization_settings",
        sa.Column("organization_id", sa.String(64), primary_key=True),
        sa.Column("connection_policy", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Đảo ngược được, nhưng **chỉ khi năng lực mới chưa được dùng tới**.

    Index cũ không có ``sharing_scope``, nên nó coi ba thứ vốn khác nhau là
    trùng lặp: kết nối chung của workspace, kết nối riêng của An, và kết nối
    riêng của Bình — khi cả ba cùng trỏ tới một tài khoản provider.

    Khi dữ liệu như vậy đã tồn tại thì không có cách đảo ngược nào **không mất
    dữ liệu**. Migration này chọn dừng lại và nói rõ, thay vì tự quyết định xoá
    kết nối của ai. Người vận hành phải tự thu hồi bớt rồi chạy lại.
    """
    _assert_downgradable()

    op.drop_table("organization_settings", schema=SCHEMA)

    op.drop_index("ix_connections_workspace_owner", "connections", schema=SCHEMA)
    op.drop_index("uq_connections_workspace_provider_account_live", "connections", schema=SCHEMA)
    op.create_index(
        "uq_connections_workspace_provider_account_live",
        "connections",
        ["workspace_id", "provider", "external_account_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text(_LIVE_ACCOUNT),
    )

    op.drop_column("connections", "sharing_scope", schema=SCHEMA)
    op.drop_column("connections", "owner_user_id", schema=SCHEMA)
    op.drop_column("connections", "sharing", schema=SCHEMA)


def _assert_downgradable() -> None:
    """Dừng sớm với thông điệp đọc được, thay vì để PostgreSQL ném lỗi thô.

    Không có bước này thì người vận hành nhận được ``UniqueViolationError: could
    not create unique index`` — đúng về mặt kỹ thuật nhưng không nói được rằng
    nguyên nhân là có hai người cùng kết nối một tài khoản, và cũng không nói
    được phải làm gì tiếp.
    """
    # Dựng bằng SQLAlchemy expression thay vì ghép chuỗi: không có giá trị nào
    # ở đây tới từ input, nhưng viết kiểu này thì không ai phải chứng minh lại
    # điều đó mỗi lần đọc.
    connections = sa.table(
        "connections",
        sa.column("workspace_id", sa.String),
        sa.column("provider", sa.String),
        sa.column("external_account_id", sa.String),
        sa.column("status", sa.String),
        schema=SCHEMA,
    )
    grouping = [
        connections.c.workspace_id,
        connections.c.provider,
        connections.c.external_account_id,
    ]
    query = (
        sa.select(*grouping, sa.func.count().label("n"))
        .where(
            connections.c.status != "revoked",
            connections.c.external_account_id.is_not(None),
        )
        .group_by(*grouping)
        .having(sa.func.count() > 1)
    )
    duplicates = op.get_bind().execute(query).fetchall()
    if not duplicates:
        return

    chi_tiet = ", ".join(
        f"{r.provider}/{r.external_account_id} ({r.n} kết nối)" for r in duplicates
    )
    raise RuntimeError(
        "Không hạ được migration 0006: có nhiều kết nối cùng trỏ tới một tài "
        "khoản provider trong cùng một workspace, mà lược đồ cũ không phân biệt "
        f"được chúng. Cần thu hồi bớt trước khi hạ. Trùng ở: {chi_tiet}."
    )
