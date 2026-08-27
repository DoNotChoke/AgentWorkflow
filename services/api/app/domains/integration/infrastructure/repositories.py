"""Sổ idempotency trên PostgreSQL."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ErrorCategory
from app.domains.integration.domain.idempotency import (
    ClaimResult,
    IdempotencyClaim,
    IdempotencyRecord,
    InvocationStatus,
)
from app.domains.integration.infrastructure.models import ToolInvocationIdempotencyModel


def _to_record(row: ToolInvocationIdempotencyModel) -> IdempotencyRecord:
    return IdempotencyRecord(
        tenant_id=row.tenant_id,
        key=row.idempotency_key,
        workspace_id=row.workspace_id,
        connection_id=row.connection_id,
        tool_id=row.tool_id,
        tool_version=row.tool_version,
        request_fingerprint=row.request_fingerprint,
        status=InvocationStatus(row.status),
        attempts=row.attempts,
        created_at=row.created_at,
        updated_at=row.updated_at,
        expires_at=row.expires_at,
        provider_reference=row.provider_reference,
        result=row.result,
        error_category=ErrorCategory(row.error_category) if row.error_category else None,
    )


class SqlAlchemyIdempotencyStore:
    """Ghi nhận trước, gọi provider sau — thứ tự làm cho việc chống lặp có nghĩa."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(self, claim: IdempotencyClaim) -> ClaimResult:
        """Giành quyền chạy thao tác này."""
        statement = (
            insert(ToolInvocationIdempotencyModel)
            .values(
                tenant_id=claim.tenant_id,
                idempotency_key=claim.key,
                workspace_id=claim.workspace_id,
                connection_id=claim.connection_id,
                tool_id=claim.tool_id,
                tool_version=claim.tool_version,
                request_fingerprint=claim.request_fingerprint,
                status=InvocationStatus.IN_FLIGHT.value,
                attempts=1,
                created_at=claim.at,
                updated_at=claim.at,
                expires_at=claim.expires_at,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "idempotency_key"])
            .returning(ToolInvocationIdempotencyModel)
        )

        inserted = (await self._session.execute(statement)).scalar_one_or_none()
        if inserted is not None:
            await self._session.flush()
            return ClaimResult(record=_to_record(inserted), fresh=True)

        existing = await self._require(claim.tenant_id, claim.key)
        return ClaimResult(record=existing, fresh=False)

    async def get(self, *, tenant_id: str, key: str) -> IdempotencyRecord | None:
        row = await self._session.get(ToolInvocationIdempotencyModel, (tenant_id, key))
        return None if row is None else _to_record(row)

    async def complete(
        self,
        *,
        tenant_id: str,
        key: str,
        result: Mapping[str, Any],
        provider_reference: str | None,
        attempts: int,
        at: datetime,
    ) -> None:
        await self._set(
            tenant_id=tenant_id,
            key=key,
            status=InvocationStatus.SUCCEEDED,
            attempts=attempts,
            at=at,
            result=dict(result),
            provider_reference=provider_reference,
        )

    async def fail(
        self,
        *,
        tenant_id: str,
        key: str,
        category: ErrorCategory,
        attempts: int,
        at: datetime,
        detail: str = "",
    ) -> None:
        await self._set(
            tenant_id=tenant_id,
            key=key,
            status=InvocationStatus.FAILED,
            attempts=attempts,
            at=at,
            error_category=category.value,
            last_error=detail[:1000] or None,
        )

    async def _set(
        self,
        *,
        tenant_id: str,
        key: str,
        status: InvocationStatus,
        attempts: int,
        at: datetime,
        **columns: Any,
    ) -> None:
        await self._session.execute(
            update(ToolInvocationIdempotencyModel)
            .where(
                ToolInvocationIdempotencyModel.tenant_id == tenant_id,
                ToolInvocationIdempotencyModel.idempotency_key == key,
            )
            .values(status=status.value, attempts=attempts, updated_at=at, **columns)
        )
        await self._session.flush()

    async def _require(self, tenant_id: str, key: str) -> IdempotencyRecord:
        row = (
            await self._session.execute(
                select(ToolInvocationIdempotencyModel).where(
                    ToolInvocationIdempotencyModel.tenant_id == tenant_id,
                    ToolInvocationIdempotencyModel.idempotency_key == key,
                )
            )
        ).scalar_one()
        return _to_record(row)
