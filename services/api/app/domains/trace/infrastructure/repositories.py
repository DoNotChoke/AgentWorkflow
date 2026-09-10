"""Hiện thực hoá `ExecutionTraceRepository` trên PostgreSQL."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.trace.domain.entities import (
    ExecutionTrace,
    PayloadLocation,
    PayloadRef,
    StepKind,
    StepStatus,
    StoredPayload,
    TraceSource,
    TraceStatus,
    TraceStep,
    TraceSubject,
)
from app.domains.trace.infrastructure.models import ExecutionTraceModel, TraceStepModel


def _to_trace(row: ExecutionTraceModel) -> ExecutionTrace:
    return ExecutionTrace(
        id=row.id,
        tenant_id=row.tenant_id,
        workspace_id=row.workspace_id,
        source=TraceSource(row.source),
        status=TraceStatus(row.status),
        subject=TraceSubject(
            agent_id=row.agent_id,
            conversation_id=row.conversation_id,
            workflow_id=row.workflow_id,
            workflow_version_id=row.workflow_version_id,
            execution_id=row.execution_id,
        ),
        initiated_by=row.initiated_by,
        started_at=row.started_at,
        completed_at=row.completed_at,
        error_category=row.error_category,
    )


def _stored_payload(
    sha256: str | None,
    byte_size: int | None,
    location: str | None,
    inline: dict[str, Any] | None,
) -> StoredPayload | None:
    """Hàng ghi trước migration 0011 không có `location` — đọc thành ABSENT."""
    if sha256 is None or byte_size is None:
        return None
    return StoredPayload(
        ref=PayloadRef(sha256=sha256, byte_size=byte_size),
        location=PayloadLocation(location) if location else PayloadLocation.ABSENT,
        inline=inline,
    )


def _to_step(row: TraceStepModel) -> TraceStep:
    return TraceStep(
        id=row.id,
        trace_id=row.trace_id,
        kind=StepKind(row.kind),
        status=StepStatus(row.status),
        started_at=row.started_at,
        completed_at=row.completed_at,
        parent_step_id=row.parent_step_id,
        input_payload=_stored_payload(
            row.input_ref_sha256, row.input_ref_byte_size, row.input_location, row.input_inline
        ),
        output_payload=_stored_payload(
            row.output_ref_sha256, row.output_ref_byte_size, row.output_location, row.output_inline
        ),
        attributes=dict(row.attributes),
        error_category=row.error_category,
        otel_trace_id=row.otel_trace_id,
        otel_span_id=row.otel_span_id,
    )


def _sha256(payload: StoredPayload | None) -> str | None:
    return payload.ref.sha256 if payload else None


def _byte_size(payload: StoredPayload | None) -> int | None:
    return payload.ref.byte_size if payload else None


def _location(payload: StoredPayload | None) -> str | None:
    return str(payload.location) if payload else None


def _inline(payload: StoredPayload | None) -> dict[str, Any] | None:
    if payload is None or payload.inline is None:
        return None
    return dict(payload.inline)


class SqlAlchemyExecutionTraceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_trace(self, trace: ExecutionTrace) -> None:
        self._session.add(
            ExecutionTraceModel(
                id=trace.id,
                tenant_id=trace.tenant_id,
                workspace_id=trace.workspace_id,
                source=str(trace.source),
                status=str(trace.status),
                agent_id=trace.subject.agent_id,
                conversation_id=trace.subject.conversation_id,
                workflow_id=trace.subject.workflow_id,
                workflow_version_id=trace.subject.workflow_version_id,
                execution_id=trace.subject.execution_id,
                initiated_by=trace.initiated_by,
                started_at=trace.started_at,
                completed_at=trace.completed_at,
                error_category=trace.error_category,
            )
        )
        await self._session.flush()

    async def add_step(self, step: TraceStep, *, tenant_id: str) -> None:
        self._session.add(
            TraceStepModel(
                id=step.id,
                trace_id=step.trace_id,
                tenant_id=tenant_id,
                kind=str(step.kind),
                status=str(step.status),
                parent_step_id=step.parent_step_id,
                started_at=step.started_at,
                completed_at=step.completed_at,
                input_ref_sha256=_sha256(step.input_payload),
                input_ref_byte_size=_byte_size(step.input_payload),
                input_location=_location(step.input_payload),
                input_inline=_inline(step.input_payload),
                output_ref_sha256=_sha256(step.output_payload),
                output_ref_byte_size=_byte_size(step.output_payload),
                output_location=_location(step.output_payload),
                output_inline=_inline(step.output_payload),
                attributes=dict(step.attributes),
                error_category=step.error_category,
                otel_trace_id=step.otel_trace_id,
                otel_span_id=step.otel_span_id,
            )
        )
        await self._session.flush()

    async def update_trace(self, trace: ExecutionTrace) -> None:
        row = await self._session.get(ExecutionTraceModel, trace.id)
        if row is None:
            return
        row.status = str(trace.status)
        row.completed_at = trace.completed_at
        row.error_category = trace.error_category
        await self._session.flush()

    async def get_trace(self, *, tenant_id: str, trace_id: str) -> ExecutionTrace | None:
        row = await self._session.scalar(
            select(ExecutionTraceModel).where(
                ExecutionTraceModel.id == trace_id,
                ExecutionTraceModel.tenant_id == tenant_id,
            )
        )
        return None if row is None else _to_trace(row)

    async def list_steps(self, *, tenant_id: str, trace_id: str) -> list[TraceStep]:
        rows = await self._session.scalars(
            select(TraceStepModel)
            .where(TraceStepModel.trace_id == trace_id, TraceStepModel.tenant_id == tenant_id)
            .order_by(TraceStepModel.started_at)
        )
        return [_to_step(row) for row in rows]

    async def get_step(self, *, tenant_id: str, trace_id: str, step_id: str) -> TraceStep | None:
        row = await self._session.scalar(
            select(TraceStepModel).where(
                TraceStepModel.id == step_id,
                TraceStepModel.trace_id == trace_id,
                TraceStepModel.tenant_id == tenant_id,
            )
        )
        return None if row is None else _to_step(row)

    async def list_traces_for_conversation(
        self, *, tenant_id: str, conversation_id: str
    ) -> list[ExecutionTrace]:
        rows = await self._session.scalars(
            select(ExecutionTraceModel)
            .where(
                ExecutionTraceModel.tenant_id == tenant_id,
                ExecutionTraceModel.conversation_id == conversation_id,
            )
            .order_by(ExecutionTraceModel.started_at.desc())
        )
        return [_to_trace(row) for row in rows]
