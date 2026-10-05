"""Phiên biên dịch trên PostgreSQL."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.domains.workflow.domain.compiler.session import (
    OPEN,
    CompileSession,
    MessageKind,
    SessionMessage,
    SessionStatus,
)
from app.domains.workflow.infrastructure.compile_models import CompileSessionModel


def _to_session(row: CompileSessionModel) -> CompileSession:
    return CompileSession(
        id=row.id,
        tenant_id=row.tenant_id,
        workspace_id=row.workspace_id,
        conversation_id=row.conversation_id,
        created_by=row.created_by,
        trace_ids=tuple(row.trace_ids),
        source_sha256=row.source_sha256,
        status=SessionStatus(row.status),
        revision=row.revision,
        messages=tuple(
            SessionMessage(
                MessageKind(item["kind"]), item["text"], datetime.fromisoformat(item["at"])
            )
            for item in row.messages
        ),
        question_set_version=row.question_set_version,
        prompt_version=row.prompt_version,
        intent_model=row.intent_model,
        answer_model=row.answer_model,
        created_at=row.created_at,
        updated_at=row.updated_at,
        intent=row.intent,
        answers=row.answers,
        proposal=row.proposal,
        ir=row.ir,
        workflow_id=row.workflow_id,
    )


def _changing(session: CompileSession) -> dict[str, Any]:
    """Những cột đổi được sau khi phiên đã mở."""
    return {
        "status": str(session.status),
        "revision": session.revision,
        "messages": [
            {"kind": str(message.kind), "text": message.text, "at": message.at.isoformat()}
            for message in session.messages
        ],
        "intent": None if session.intent is None else dict(session.intent),
        "answers": None if session.answers is None else dict(session.answers),
        "proposal": None if session.proposal is None else dict(session.proposal),
        "ir": None if session.ir is None else dict(session.ir),
        "workflow_id": session.workflow_id,
        "updated_at": session.updated_at,
    }


class SqlAlchemyCompileSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, session: CompileSession) -> None:
        self._session.add(
            CompileSessionModel(
                id=session.id,
                tenant_id=session.tenant_id,
                workspace_id=session.workspace_id,
                conversation_id=session.conversation_id,
                created_by=session.created_by,
                trace_ids=list(session.trace_ids),
                source_sha256=session.source_sha256,
                question_set_version=session.question_set_version,
                prompt_version=session.prompt_version,
                intent_model=session.intent_model,
                answer_model=session.answer_model,
                created_at=session.created_at,
                **_changing(session),
            )
        )
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # Index duy nhất của phiên đang mở: một request khác vừa mở trước.
            raise ValidationError(
                "Vừa có một phiên biên dịch khác được mở cho cuộc hội thoại này. Tải lại.",
                details={"conversation_id": session.conversation_id},
            ) from exc

    async def get(self, *, tenant_id: str, session_id: str) -> CompileSession | None:
        row = await self._session.scalar(
            select(CompileSessionModel).where(
                CompileSessionModel.tenant_id == tenant_id, CompileSessionModel.id == session_id
            )
        )
        return None if row is None else _to_session(row)

    async def save(self, session: CompileSession, *, expected_revision: int) -> None:
        result: CursorResult[Any] = await self._session.execute(  # type: ignore[assignment]
            update(CompileSessionModel)
            .where(
                CompileSessionModel.tenant_id == session.tenant_id,
                CompileSessionModel.id == session.id,
                CompileSessionModel.revision == expected_revision,
            )
            .values(**_changing(session))
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise ValidationError(
                "Phiên biên dịch vừa được đổi ở nơi khác. Tải lại rồi thử lại.",
                details={"session_id": session.id},
            )

    async def list_open(
        self, *, tenant_id: str, conversation_id: str, created_by: str
    ) -> list[CompileSession]:
        rows = await self._session.scalars(
            select(CompileSessionModel).where(
                CompileSessionModel.tenant_id == tenant_id,
                CompileSessionModel.conversation_id == conversation_id,
                CompileSessionModel.created_by == created_by,
                CompileSessionModel.status.in_([str(status) for status in OPEN]),
            )
        )
        return [_to_session(row) for row in rows]
