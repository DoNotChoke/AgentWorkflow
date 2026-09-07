"""Hiện thực hoá các repository của domain Agent trên PostgreSQL."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.agent.domain.entities import (
    Agent,
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
    ToolCallRequest,
)
from app.domains.agent.infrastructure.models import (
    AgentModel,
    ConversationConnectionModel,
    ConversationModel,
    MessageModel,
)


async def _tenant_of(session: AsyncSession, conversation_id: str) -> str:
    """`tenant_id` của conversation cha — dùng để denormalize cột tenant trên
    các bảng con của nó (`messages`, `conversation_connections`), đúng cách
    `TraceStep`/`trace_id` đã làm. `Message`/lựa chọn connection của domain
    không mang `tenant_id` riêng — `conversation_id` đã đủ để định vị."""
    row = await session.scalar(
        select(ConversationModel.tenant_id).where(ConversationModel.id == conversation_id)
    )
    if row is None:
        raise ValueError(f"Không tìm thấy conversation {conversation_id!r}.")
    return row


def _to_agent(row: AgentModel) -> Agent:
    return Agent(
        id=row.id,
        tenant_id=row.tenant_id,
        workspace_id=row.workspace_id,
        name=row.name,
        system_prompt=row.system_prompt,
        model=row.model,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        enabled_tool_ids=frozenset(row.enabled_tool_ids),
        max_tool_calls_per_turn=row.max_tool_calls_per_turn,
        archived_at=row.archived_at,
    )


def _to_conversation(row: ConversationModel) -> Conversation:
    return Conversation(
        id=row.id,
        agent_id=row.agent_id,
        tenant_id=row.tenant_id,
        workspace_id=row.workspace_id,
        user_id=row.user_id,
        status=ConversationStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _tool_calls_to_json(tool_calls: Sequence[ToolCallRequest]) -> list[dict[str, Any]]:
    return [
        {"call_id": call.call_id, "tool_id": call.tool_id, "input": dict(call.input)}
        for call in tool_calls
    ]


def _tool_calls_from_json(raw: Sequence[Mapping[str, Any]]) -> tuple[ToolCallRequest, ...]:
    return tuple(
        ToolCallRequest(call_id=item["call_id"], tool_id=item["tool_id"], input=item["input"])
        for item in raw
    )


def _to_message(row: MessageModel) -> Message:
    return Message(
        id=row.id,
        conversation_id=row.conversation_id,
        role=MessageRole(row.role),
        created_at=row.created_at,
        content=row.content,
        tool_calls=_tool_calls_from_json(row.tool_calls),
        tool_call_id=row.tool_call_id,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
    )


class SqlAlchemyAgentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, agent: Agent) -> None:
        self._session.add(
            AgentModel(
                id=agent.id,
                tenant_id=agent.tenant_id,
                workspace_id=agent.workspace_id,
                name=agent.name,
                system_prompt=agent.system_prompt,
                model=agent.model,
                created_by=agent.created_by,
                enabled_tool_ids=sorted(agent.enabled_tool_ids),
                max_tool_calls_per_turn=agent.max_tool_calls_per_turn,
                archived_at=agent.archived_at,
                created_at=agent.created_at,
                updated_at=agent.updated_at,
            )
        )
        await self._session.flush()

    async def get(self, *, tenant_id: str, agent_id: str) -> Agent | None:
        row = await self._session.scalar(
            select(AgentModel).where(AgentModel.id == agent_id, AgentModel.tenant_id == tenant_id)
        )
        return None if row is None else _to_agent(row)

    async def list_for_workspace(self, *, tenant_id: str, workspace_id: str) -> list[Agent]:
        rows = await self._session.scalars(
            select(AgentModel)
            .where(AgentModel.tenant_id == tenant_id, AgentModel.workspace_id == workspace_id)
            .order_by(AgentModel.created_at)
        )
        return [_to_agent(row) for row in rows]

    async def update(self, agent: Agent) -> None:
        row = await self._session.get(AgentModel, agent.id)
        if row is None:
            return
        row.name = agent.name
        row.system_prompt = agent.system_prompt
        row.model = agent.model
        row.enabled_tool_ids = sorted(agent.enabled_tool_ids)
        row.max_tool_calls_per_turn = agent.max_tool_calls_per_turn
        row.archived_at = agent.archived_at
        row.updated_at = agent.updated_at
        await self._session.flush()


class SqlAlchemyConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, conversation: Conversation) -> None:
        self._session.add(
            ConversationModel(
                id=conversation.id,
                agent_id=conversation.agent_id,
                tenant_id=conversation.tenant_id,
                workspace_id=conversation.workspace_id,
                user_id=conversation.user_id,
                status=str(conversation.status),
                created_at=conversation.created_at,
                updated_at=conversation.updated_at,
            )
        )
        await self._session.flush()

    async def get(self, *, tenant_id: str, conversation_id: str) -> Conversation | None:
        row = await self._session.scalar(
            select(ConversationModel).where(
                ConversationModel.id == conversation_id,
                ConversationModel.tenant_id == tenant_id,
            )
        )
        return None if row is None else _to_conversation(row)

    async def list_for_user(
        self, *, tenant_id: str, agent_id: str, user_id: str
    ) -> list[Conversation]:
        rows = await self._session.scalars(
            select(ConversationModel)
            .where(
                ConversationModel.tenant_id == tenant_id,
                ConversationModel.agent_id == agent_id,
                ConversationModel.user_id == user_id,
            )
            .order_by(ConversationModel.updated_at.desc())
        )
        return [_to_conversation(row) for row in rows]

    async def update(self, conversation: Conversation) -> None:
        row = await self._session.get(ConversationModel, conversation.id)
        if row is None:
            return
        row.status = str(conversation.status)
        row.updated_at = conversation.updated_at
        await self._session.flush()


class SqlAlchemyMessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_many(self, messages: Sequence[Message]) -> None:
        """Mọi message của một `TurnResult` thuộc cùng một `conversation_id`
        trong cách dùng thật — cache một lượt tra tenant thay vì tra lại cho
        từng message, nhưng vẫn đúng nếu ai đó truyền lẫn nhiều hội thoại."""
        if not messages:
            return

        tenant_by_conversation: dict[str, str] = {}
        for message in messages:
            if message.conversation_id not in tenant_by_conversation:
                tenant_by_conversation[message.conversation_id] = await _tenant_of(
                    self._session, message.conversation_id
                )

            self._session.add(
                MessageModel(
                    id=message.id,
                    conversation_id=message.conversation_id,
                    tenant_id=tenant_by_conversation[message.conversation_id],
                    role=str(message.role),
                    content=message.content,
                    tool_calls=_tool_calls_to_json(message.tool_calls),
                    tool_call_id=message.tool_call_id,
                    input_tokens=message.input_tokens,
                    output_tokens=message.output_tokens,
                    created_at=message.created_at,
                )
            )
        await self._session.flush()

    async def list_for_conversation(self, *, tenant_id: str, conversation_id: str) -> list[Message]:
        rows = await self._session.scalars(
            select(MessageModel)
            .where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.tenant_id == tenant_id,
            )
            .order_by(MessageModel.created_at)
        )
        return [_to_message(row) for row in rows]


class SqlAlchemyConnectionSelectionRepository:
    """Hiện thực `ConnectionSelectionStore` — đọc/ghi thuần SQL, không I/O
    mạng nào (khác `ConnectionResolverClient`). Xem ADR-0004."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, *, conversation_id: str, provider: str) -> str | None:
        result: str | None = await self._session.scalar(
            select(ConversationConnectionModel.connection_id).where(
                ConversationConnectionModel.conversation_id == conversation_id,
                ConversationConnectionModel.provider == provider,
            )
        )
        return result

    async def set(self, *, conversation_id: str, provider: str, connection_id: str) -> None:
        """Ghi đè nếu conversation đã có lựa chọn cho provider này — lựa chọn
        là "sticky" nhưng đổi được bất cứ lúc nào (ADR-0004), không phải một
        lần chốt vĩnh viễn."""
        tenant_id = await _tenant_of(self._session, conversation_id)
        now = datetime.now(UTC)
        stmt = pg_insert(ConversationConnectionModel).values(
            conversation_id=conversation_id,
            provider=provider,
            tenant_id=tenant_id,
            connection_id=connection_id,
            created_at=now,
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                ConversationConnectionModel.conversation_id,
                ConversationConnectionModel.provider,
            ],
            set_={"connection_id": connection_id, "updated_at": now},
        )
        await self._session.execute(stmt)
        await self._session.flush()
