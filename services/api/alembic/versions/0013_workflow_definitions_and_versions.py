"""workflow: định nghĩa, draft, và version bất biến (§12, §2.3).

Ba bảng, đúng hình dạng của `domains/workflow/domain/entities.py`:

- `workflows` — định danh ổn định. Không mang IR: `GET /v1/workflows` không
  việc gì phải kéo theo cả cây JSON của từng cái.
- `workflow_drafts` — một hàng cho mỗi workflow, chứa bản đang sửa. Được phép
  chưa hợp lệ (Q14 của kế hoạch WP-B).
- `workflow_versions` — bản đã publish, đánh số từ 1 trong phạm vi một
  workflow.

`tenant_id` denormalize xuống cả draft lẫn version, cùng cách `trace_steps` và
`agent.messages` đã làm: kiểm tenant ngay trong câu truy vấn, không phải JOIN
ngược lên bảng cha trước (§19).

**Trigger là phần đáng đọc nhất của file này.** Ràng buộc 2 của §47 nói version
đã publish là bất biến. Repository không có phương thức nào sửa nó, nhưng đó là
một quy ước; trigger biến nó thành một bảo đảm, kể cả với một câu UPDATE gõ tay
lúc 3 giờ sáng. Cùng lập luận với việc `lint-imports` canh ranh giới credential
thay vì một dòng trong tài liệu.

**Nó chặn UPDATE, không chặn DELETE.** Bản đầu chặn cả hai, và việc đó sai ở hai
chỗ. Bất biến theo nghĩa §2.3 nói về *nội dung không đổi*, không phải *hàng tồn
tại vĩnh viễn*; còn xoá dữ liệu của một tenant khi họ rời đi là việc hợp lệ mà
Phase 7 sẽ phải làm, và một bảng không xoá được biến nó thành ngoại lệ vận hành.
Thứ thật sự cần canh khi xoá là "còn execution nào tham chiếu version này
không", và đó là việc của khoá ngoại từ `execution.executions` ở GĐ 4 — một
ràng buộc đúng mục tiêu hơn một lệnh cấm toàn bảng.

Revision ID: 0013_workflow_definitions
Revises: 0012_integration_idem_lease
Create Date: 2026-09-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013_workflow_definitions"
down_revision: str | None = "0012_integration_idem_lease"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "workflow"
WORKFLOWS = "workflows"
DRAFTS = "workflow_drafts"
VERSIONS = "workflow_versions"

#: Hai câu lệnh, hai lần `execute`: asyncpg từ chối nhiều lệnh trong một
#: prepared statement.
_GUARD_FUNCTION = f"""
CREATE OR REPLACE FUNCTION {SCHEMA}.workflow_versions_are_immutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'workflow_versions là bất biến (§2.3): không sửa được version %', OLD.id;
END;
$$ LANGUAGE plpgsql
"""

_GUARD_TRIGGER = f"""
CREATE TRIGGER {VERSIONS}_immutable
BEFORE UPDATE ON {SCHEMA}.{VERSIONS}
FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.workflow_versions_are_immutable()
"""


def upgrade() -> None:
    op.create_table(
        WORKFLOWS,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        # Không FK sang workflow_versions: hai bảng trỏ vào nhau thì mỗi lần
        # publish cần một vòng INSERT–UPDATE có thứ tự, đổi lấy một ràng buộc
        # mà service đã giữ.
        sa.Column("current_version_id", sa.String(64)),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workflows_tenant_workspace", WORKFLOWS, ["tenant_id", "workspace_id"], schema=SCHEMA
    )

    op.create_table(
        DRAFTS,
        sa.Column(
            "workflow_id",
            sa.String(64),
            sa.ForeignKey(f"{SCHEMA}.{WORKFLOWS}.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("ir", postgresql.JSONB, nullable=False),
        sa.Column("updated_by", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )

    op.create_table(
        VERSIONS,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "workflow_id",
            sa.String(64),
            sa.ForeignKey(f"{SCHEMA}.{WORKFLOWS}.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("ir", postgresql.JSONB, nullable=False),
        sa.Column("ir_sha256", sa.String(64), nullable=False),
        sa.Column("published_by", sa.String(64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workflow_id", "number", name="uq_workflow_versions_number"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workflow_versions_tenant_workflow",
        VERSIONS,
        ["tenant_id", "workflow_id"],
        schema=SCHEMA,
    )

    op.execute(_GUARD_FUNCTION)
    op.execute(_GUARD_TRIGGER)


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {VERSIONS}_immutable ON {SCHEMA}.{VERSIONS}")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.workflow_versions_are_immutable()")
    op.drop_table(VERSIONS, schema=SCHEMA)
    op.drop_table(DRAFTS, schema=SCHEMA)
    op.drop_table(WORKFLOWS, schema=SCHEMA)
