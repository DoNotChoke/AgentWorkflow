"""Use case quản lý Agent/Conversation/Message — CRUD, tách khỏi `AgentRuntime`."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime

from app.core.errors import AuthorizationError
from app.core.ids import ResourceKind, new_id
from app.domains.agent.application.ports import (
    AgentRepository,
    ConversationRepository,
    MessageRepository,
)
from app.domains.agent.domain.entities import (
    Agent,
    Conversation,
    ConversationStatus,
    Message,
    ensure_can_manage,
    ensure_not_archived,
    ensure_owns,
    normalize_name,
    normalize_system_prompt,
    validate_max_tool_calls_per_turn,
)
from app.domains.agent.domain.entities import archive as archive_agent_entity

#: Không tìm thấy hoặc không thuộc tenant/không phải chủ — cùng thông điệp,
#: cùng mã lỗi, để không lộ rằng id đó có thật (cùng cách `Connection` làm).
_AGENT_NOT_FOUND = "Không tìm thấy agent này."
_CONVERSATION_NOT_FOUND = "Không tìm thấy cuộc hội thoại này."


class AgentAdminService:
    def __init__(
        self,
        *,
        agents: AgentRepository,
        conversations: ConversationRepository,
        messages: MessageRepository,
    ) -> None:
        self._agents = agents
        self._conversations = conversations
        self._messages = messages

    # ─── Agent ───────────────────────────────────────────────────────────────

    async def create_agent(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        name: str,
        system_prompt: str,
        model: str,
        enabled_tool_ids: frozenset[str],
        max_tool_calls_per_turn: int,
        created_by: str,
    ) -> Agent:
        now = datetime.now(UTC)
        agent = Agent(
            id=new_id(ResourceKind.AGENT),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            name=normalize_name(name),
            system_prompt=normalize_system_prompt(system_prompt),
            model=model,
            created_by=created_by,
            created_at=now,
            updated_at=now,
            enabled_tool_ids=enabled_tool_ids,
            max_tool_calls_per_turn=validate_max_tool_calls_per_turn(max_tool_calls_per_turn),
        )
        await self._agents.add(agent)
        return agent

    async def get_agent(self, *, tenant_id: str, agent_id: str) -> Agent:
        agent = await self._agents.get(tenant_id=tenant_id, agent_id=agent_id)
        if agent is None:
            raise AuthorizationError(_AGENT_NOT_FOUND)
        return agent

    async def list_agents(self, *, tenant_id: str, workspace_id: str) -> list[Agent]:
        return await self._agents.list_for_workspace(tenant_id=tenant_id, workspace_id=workspace_id)

    async def archive_agent(
        self, *, tenant_id: str, agent_id: str, actor_id: str, can_manage_workspace: bool
    ) -> Agent:
        agent = await self.get_agent(tenant_id=tenant_id, agent_id=agent_id)
        ensure_can_manage(agent, actor_id=actor_id, can_manage_workspace=can_manage_workspace)
        archived = archive_agent_entity(agent, at=datetime.now(UTC))
        await self._agents.update(archived)
        return archived

    # ─── Conversation ──────────────────────────────────────────────────────

    async def create_conversation(
        self, *, tenant_id: str, workspace_id: str, agent_id: str, user_id: str
    ) -> Conversation:
        agent = await self.get_agent(tenant_id=tenant_id, agent_id=agent_id)
        ensure_not_archived(agent)

        now = datetime.now(UTC)
        conversation = Conversation(
            id=new_id(ResourceKind.CONVERSATION),
            agent_id=agent_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            status=ConversationStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        await self._conversations.add(conversation)
        return conversation

    async def get_conversation(
        self, *, tenant_id: str, conversation_id: str, user_id: str
    ) -> Conversation:
        conversation = await self._conversations.get(
            tenant_id=tenant_id, conversation_id=conversation_id
        )
        if conversation is None:
            raise AuthorizationError(_CONVERSATION_NOT_FOUND)
        ensure_owns(conversation, user_id=user_id)
        return conversation

    async def list_conversations(
        self, *, tenant_id: str, agent_id: str, user_id: str
    ) -> list[Conversation]:
        return await self._conversations.list_for_user(
            tenant_id=tenant_id, agent_id=agent_id, user_id=user_id
        )

    # ─── Message ───────────────────────────────────────────────────────────

    async def list_messages(
        self, *, tenant_id: str, conversation_id: str, user_id: str
    ) -> list[Message]:
        """Xác nhận quyền sở hữu trước — `MessageRepository` tự nó không biết
        `Conversation` thuộc về ai."""
        await self.get_conversation(
            tenant_id=tenant_id, conversation_id=conversation_id, user_id=user_id
        )
        return await self._messages.list_for_conversation(
            tenant_id=tenant_id, conversation_id=conversation_id
        )

    async def record_turn(self, *, conversation: Conversation, messages: Sequence[Message]) -> None:
        """Lưu các message mà `AgentRuntime.run_turn` vừa sinh ra và cập nhật
        `updated_at` của hội thoại.

        Nhận thẳng `Conversation` đã xác thực quyền sở hữu từ trước (endpoint
        gọi `get_conversation` trước khi bắt đầu lượt) — không tự kiểm tra lại
        ở đây, vì lúc lượt kết thúc không còn `user_id` của người gọi để so.
        """
        await self._messages.add_many(messages)
        await self._conversations.update(replace(conversation, updated_at=datetime.now(UTC)))
