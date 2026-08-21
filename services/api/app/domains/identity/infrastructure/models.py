"""Model SQLAlchemy của domain Identity"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

SCHEMA = "identity"


class UserModel(Base):
    __tablename__ = "users"
    __table_args__ = ({"schema": SCHEMA},)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)

    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'active'"))

    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RefreshTokenModel(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_family_id", "family_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False
    )
    family_id: Mapped[str] = mapped_column(String(64), nullable=False)

    #: SHA-256 dạng hex. Giá trị thô của token không tồn tại ở bất kỳ đâu trong
    #: database.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rotated_to_id: Mapped[str | None] = mapped_column(String(64))
