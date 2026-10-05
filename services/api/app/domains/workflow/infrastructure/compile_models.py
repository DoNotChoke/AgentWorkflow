"""Model SQLAlchemy của phiên biên dịch."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domains.workflow.infrastructure.models import SCHEMA


class CompileSessionModel(Base):
    __tablename__ = "compile_sessions"
    __table_args__ = (
        Index("ix_compile_sessions_tenant_conversation", "tenant_id", "conversation_id"),
        # Mỗi người một phiên đang mở trên một conversation.
        Index(
            "uq_compile_sessions_open",
            "conversation_id",
            "created_by",
            unique=True,
            postgresql_where=text("status IN ('awaiting_intent', 'awaiting_confirmation')"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Không khai ForeignKey: conversation thuộc schema `agent`.
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)

    trace_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)

    intent: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    answers: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    proposal: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ir: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    #: Không khai ForeignKey: phiên sống lâu hơn workflow nó sinh ra thì vẫn là lịch sử.
    workflow_id: Mapped[str | None] = mapped_column(String(64))

    question_set_version: Mapped[str] = mapped_column(String(16), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(16), nullable=False)
    intent_model: Mapped[str] = mapped_column(String(100), nullable=False)
    answer_model: Mapped[str] = mapped_column(String(100), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
