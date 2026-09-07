"""Model SQLAlchemy của domain Trace."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

SCHEMA = "trace"


class ExecutionTraceModel(Base):
    __tablename__ = "execution_traces"
    __table_args__ = (
        Index("ix_execution_traces_tenant_conversation", "tenant_id", "conversation_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)

    source: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)

    #: `TraceSubject` trải phẳng. Đúng một nhóm có giá trị, theo `source` —
    #: xem `ensure_subject_matches_source` ở tầng domain.
    agent_id: Mapped[str | None] = mapped_column(String(64))
    conversation_id: Mapped[str | None] = mapped_column(String(64))
    workflow_id: Mapped[str | None] = mapped_column(String(64))
    workflow_version_id: Mapped[str | None] = mapped_column(String(64))
    execution_id: Mapped[str | None] = mapped_column(String(64))

    initiated_by: Mapped[str] = mapped_column(String(64), nullable=False)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_category: Mapped[str | None] = mapped_column(String(64))


class TraceStepModel(Base):
    __tablename__ = "trace_steps"
    __table_args__ = (
        Index("ix_trace_steps_trace_id_started_at", "trace_id", "started_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(f"{SCHEMA}.execution_traces.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Trùng với `tenant_id` của trace cha
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)

    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)

    #: `None` ở gốc cây. Không đặt `ForeignKey` tự tham chiếu: một `ON DELETE
    #: CASCADE` từ `execution_traces` xoá cả cây step trong cùng một lượt, và
    #: thứ tự xoá giữa các step anh em với nhau không quan trọng.
    parent_step_id: Mapped[str | None] = mapped_column(String(64))

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    #: `PayloadRef` trải phẳng — vân tay, không phải nội dung (§9.1).
    input_ref_sha256: Mapped[str | None] = mapped_column(String(64))
    input_ref_byte_size: Mapped[int | None] = mapped_column(Integer)
    output_ref_sha256: Mapped[str | None] = mapped_column(String(64))
    output_ref_byte_size: Mapped[int | None] = mapped_column(Integer)

    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error_category: Mapped[str | None] = mapped_column(String(64))

    otel_trace_id: Mapped[str | None] = mapped_column(String(64))
    otel_span_id: Mapped[str | None] = mapped_column(String(32))
