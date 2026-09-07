"""Model SQLAlchemy của domain Agent."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

SCHEMA = "agent"


class AgentModel(Base):
    __tablename__ = "agents"
    __table_args__ = (
        Index("ix_agents_tenant_workspace", "tenant_id", "workspace_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)

    #: `frozenset[str]` trải phẳng thành mảng JSON — whitelist tường minh, xem
    #: `Agent.enabled_tool_ids` ở domain.
    enabled_tool_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    max_tool_calls_per_turn: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="8"
    )

    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConversationModel(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_tenant_agent_user", "tenant_id", "agent_id", "user_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[str] = mapped_column(
        String(64), ForeignKey(f"{SCHEMA}.agents.id", ondelete="RESTRICT"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MessageModel(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_conversation_created_at", "conversation_id", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(64), ForeignKey(f"{SCHEMA}.conversations.id", ondelete="CASCADE"), nullable=False
    )
    #: Trùng `tenant_id` của conversation cha, không chuẩn hoá — cho kiểm
    #: tenant ngay ở câu query của message mà không phải JOIN ngược lên trước
    #: (§19), cùng cách `trace_steps.tenant_id` đã làm.
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)

    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    #: `tuple[ToolCallRequest, ...]` trải phẳng: `[{"call_id", "tool_id", "input"}, ...]`.
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    tool_call_id: Mapped[str | None] = mapped_column(String(64))

    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConversationConnectionModel(Base):
    """Lựa chọn `connection_id` đã chốt cho một `(conversation, provider)`."""

    __tablename__ = "conversation_connections"
    __table_args__ = ({"schema": SCHEMA},)

    conversation_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(f"{SCHEMA}.conversations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    #: Không phải id riêng — provider là chuỗi ngắn (`slack`, `google`...).
    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: Trùng `tenant_id` của conversation cha, không chuẩn hoá — cùng cách
    #: `MessageModel.tenant_id` đã làm.
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Chuỗi bất kỳ domain `agent` cầm hộ — không phải khoá ngoại, domain này
    #: không hiểu `Connection` là gì (ADR-0004).
    connection_id: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
