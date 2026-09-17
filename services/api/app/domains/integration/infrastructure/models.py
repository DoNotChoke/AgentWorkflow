"""Model SQLAlchemy của Integration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

SCHEMA = "integration"


class ToolInvocationIdempotencyModel(Base):
    """Sổ chống lặp cho tác động ra bên ngoài (§15.1).

    Khoá chính là `(tenant_id, idempotency_key)` với `tenant_id` đứng **trước**.
    """

    __tablename__ = "tool_invocation_idempotency"
    __table_args__ = (
        Index("ix_tool_invocation_idempotency_expires_at", "expires_at"),
        Index("ix_tool_invocation_idempotency_tenant_tool", "tenant_id", "tool_id"),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), primary_key=True)

    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)

    connection_id: Mapped[str] = mapped_column(String(64), nullable=False)

    tool_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(16), nullable=False)

    #: sha256 của input đã chuẩn hoá. Cùng khoá mà khác vân tay nghĩa là khoá bị
    #: dùng lại cho thao tác khác — trả lỗi chứ không trả kết quả của lần trước.
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False)

    #: Định danh mà provider cấp, ví dụ "C123:1712345678.000100" của Slack.
    provider_reference: Mapped[str | None] = mapped_column(String(255))

    #: Chỉ kết quả nhỏ. Payload lớn đi object storage ở Phase 3, cùng với domain
    #: `trace` là nơi sở hữu các tham chiếu đó.
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    error_category: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    claim_token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    #: Dọn dẹp theo TTL là việc của Phase 7; cột này để sẵn cho job đó.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
