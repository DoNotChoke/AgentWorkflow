"""Sổ idempotency trên PostgreSQL."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import session_scope
from app.core.errors import ErrorCategory
from app.domains.integration.domain.idempotency import (
    ClaimResult,
    IdempotencyClaim,
    IdempotencyRecord,
    InvocationStatus,
)
from app.domains.integration.infrastructure.models import ToolInvocationIdempotencyModel

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

_Row = ToolInvocationIdempotencyModel


def _to_record(row: _Row) -> IdempotencyRecord:
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
        claim_token=row.claim_token,
        lease_expires_at=row.lease_expires_at,
    )


class SqlAlchemyIdempotencyStore:
    """Mỗi thao tác một transaction riêng, commit ngay.

    Không bao giờ nằm trong transaction của request — request lỗi thì rollback,
    và sổ phải còn đó — và không bao giờ mở trong lúc gọi provider. Mọi chuyển
    trạng thái là compare-and-set trên `status` và `claim_token`.
    """

    def __init__(self, sessions: SessionFactory = session_scope) -> None:
        self._sessions = sessions

    async def claim(self, claim: IdempotencyClaim) -> ClaimResult:
        """Giành quyền chạy thao tác này."""
        statement = (
            insert(_Row)
            .values(
                tenant_id=claim.tenant_id,
                idempotency_key=claim.key,
                workspace_id=claim.workspace_id,
                connection_id=claim.connection_id,
                tool_id=claim.tool_id,
                tool_version=claim.tool_version,
                request_fingerprint=claim.request_fingerprint,
                status=InvocationStatus.IN_FLIGHT.value,
                attempts=0,
                claim_token=claim.claim_token,
                lease_expires_at=claim.lease_expires_at,
                created_at=claim.at,
                updated_at=claim.at,
                expires_at=claim.expires_at,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "idempotency_key"])
            .returning(_Row)
        )

        async with self._sessions() as session:
            inserted = (await session.execute(statement)).scalar_one_or_none()
            if inserted is not None:
                return ClaimResult(record=_to_record(inserted), fresh=True)
            existing = await self._require(session, claim.tenant_id, claim.key)
            return ClaimResult(record=existing, fresh=False)

    async def get(self, *, tenant_id: str, key: str) -> IdempotencyRecord | None:
        async with self._sessions() as session:
            row = await session.get(_Row, (tenant_id, key), populate_existing=True)
            return None if row is None else _to_record(row)

    async def take_over(
        self,
        *,
        tenant_id: str,
        key: str,
        expected: InvocationStatus,
        claim_token: str,
        lease_expires_at: datetime,
        at: datetime,
    ) -> IdempotencyRecord | None:
        """Giành lại quyền chạy từ một hàng đang ở `expected`. `None` nếu người
        khác vừa đổi hàng đó trước."""
        conditions = [_Row.status == expected.value]
        if expected is InvocationStatus.IN_FLIGHT:
            conditions.append(or_(_Row.lease_expires_at.is_(None), _Row.lease_expires_at <= at))

        statement = (
            update(_Row)
            .where(_Row.tenant_id == tenant_id, _Row.idempotency_key == key, *conditions)
            .values(
                status=InvocationStatus.IN_FLIGHT.value,
                claim_token=claim_token,
                lease_expires_at=lease_expires_at,
                error_category=None,
                last_error=None,
                updated_at=at,
            )
            .returning(_Row)
        )
        async with self._sessions() as session:
            row = (await session.execute(statement)).scalar_one_or_none()
            return None if row is None else _to_record(row)

    async def abandon_stale(self, *, tenant_id: str, key: str, at: datetime) -> bool:
        """Hàng `in_flight` đã hết lease → `unknown`. Người giữ đã chết giữa chừng."""
        return await self._transition(
            tenant_id=tenant_id,
            key=key,
            where=[
                _Row.status == InvocationStatus.IN_FLIGHT.value,
                or_(_Row.lease_expires_at.is_(None), _Row.lease_expires_at <= at),
            ],
            values={
                "status": InvocationStatus.UNKNOWN.value,
                "error_category": ErrorCategory.OUTCOME_UNKNOWN.value,
                "last_error": "Lease hết hạn khi thao tác còn đang chạy.",
                "updated_at": at,
            },
        )

    async def complete(
        self,
        *,
        tenant_id: str,
        key: str,
        claim_token: str,
        result: Mapping[str, Any],
        provider_reference: str | None,
        attempts: int,
        at: datetime,
    ) -> bool:
        # `unknown` cũng được ghi đè: provider đã trả thành công là sự thật đúng
        # hơn một lần đánh dấu vì lease lỡ hết hạn.
        return await self._transition(
            tenant_id=tenant_id,
            key=key,
            where=[
                _Row.claim_token == claim_token,
                _Row.status.in_([InvocationStatus.IN_FLIGHT.value, InvocationStatus.UNKNOWN.value]),
            ],
            values={
                "status": InvocationStatus.SUCCEEDED.value,
                "attempts": attempts,
                "result": dict(result),
                "provider_reference": provider_reference,
                "error_category": None,
                "last_error": None,
                "updated_at": at,
            },
        )

    async def fail(
        self,
        *,
        tenant_id: str,
        key: str,
        claim_token: str,
        category: ErrorCategory,
        attempts: int,
        at: datetime,
        detail: str = "",
    ) -> bool:
        return await self._settle(
            tenant_id=tenant_id,
            key=key,
            claim_token=claim_token,
            status=InvocationStatus.FAILED,
            category=category,
            attempts=attempts,
            at=at,
            detail=detail,
        )

    async def mark_unknown(
        self,
        *,
        tenant_id: str,
        key: str,
        claim_token: str,
        category: ErrorCategory,
        attempts: int,
        at: datetime,
        detail: str = "",
    ) -> bool:
        return await self._settle(
            tenant_id=tenant_id,
            key=key,
            claim_token=claim_token,
            status=InvocationStatus.UNKNOWN,
            category=category,
            attempts=attempts,
            at=at,
            detail=detail,
        )

    async def _settle(
        self,
        *,
        tenant_id: str,
        key: str,
        claim_token: str,
        status: InvocationStatus,
        category: ErrorCategory,
        attempts: int,
        at: datetime,
        detail: str,
    ) -> bool:
        return await self._transition(
            tenant_id=tenant_id,
            key=key,
            where=[
                _Row.claim_token == claim_token,
                _Row.status == InvocationStatus.IN_FLIGHT.value,
            ],
            values={
                "status": status.value,
                "attempts": attempts,
                "error_category": category.value,
                "last_error": detail[:1000] or None,
                "updated_at": at,
            },
        )

    async def _transition(
        self, *, tenant_id: str, key: str, where: list[Any], values: dict[str, Any]
    ) -> bool:
        statement = (
            update(_Row)
            .where(_Row.tenant_id == tenant_id, _Row.idempotency_key == key, *where)
            .values(**values)
            .returning(_Row.idempotency_key)
        )
        async with self._sessions() as session:
            return (await session.execute(statement)).scalar_one_or_none() is not None

    async def _require(self, session: AsyncSession, tenant_id: str, key: str) -> IdempotencyRecord:
        row = (
            await session.execute(
                select(_Row).where(_Row.tenant_id == tenant_id, _Row.idempotency_key == key)
            )
        ).scalar_one()
        return _to_record(row)
