"""Model SQLAlchemy của domain Workflow."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

SCHEMA = "workflow"


class WorkflowModel(Base):
    __tablename__ = "workflows"
    __table_args__ = (
        Index("ix_workflows_tenant_workspace", "tenant_id", "workspace_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Không khai ForeignKey: xem migration 0013.
    current_version_id: Mapped[str | None] = mapped_column(String(64))

    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkflowDraftModel(Base):
    __tablename__ = "workflow_drafts"
    __table_args__ = ({"schema": SCHEMA},)

    workflow_id: Mapped[str] = mapped_column(
        String(64), ForeignKey(f"{SCHEMA}.workflows.id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: IR đang sửa. Được phép chưa hợp lệ (Q14).
    ir: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    intent: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    compiled_from: Mapped[str | None] = mapped_column(String(64))
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkflowVersionModel(Base):
    __tablename__ = "workflow_versions"
    __table_args__ = (
        UniqueConstraint("workflow_id", "number", name="uq_workflow_versions_number"),
        Index("ix_workflow_versions_tenant_workflow", "tenant_id", "workflow_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        String(64), ForeignKey(f"{SCHEMA}.workflows.id", ondelete="RESTRICT"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    ir: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ir_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    intent: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    compiled_from: Mapped[str | None] = mapped_column(String(64))
    published_by: Mapped[str] = mapped_column(String(64), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
