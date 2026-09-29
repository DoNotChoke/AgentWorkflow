"""Model SQLAlchemy của domain Execution."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

SCHEMA = "execution"


class ExecutionModel(Base):
    __tablename__ = "executions"
    __table_args__ = (
        Index("ix_executions_tenant_workflow_created", "tenant_id", "workflow_id", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Khoá ngoại bằng chuỗi, không import model của `workflow` (contract 1).
    workflow_version_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workflow.workflow_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    initiated_by: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_category: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NodeExecutionModel(Base):
    __tablename__ = "node_executions"
    __table_args__ = (
        UniqueConstraint("execution_id", "node_path", name="uq_node_executions_path"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: Thứ tự chèn — tiến trình đọc theo nó.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)
    execution_id: Mapped[str] = mapped_column(
        String(64), ForeignKey(f"{SCHEMA}.executions.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    node_path: Mapped[str] = mapped_column(String(200), nullable=False)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    node_type: Mapped[str] = mapped_column(String(32), nullable=False)

    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    error_category: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
