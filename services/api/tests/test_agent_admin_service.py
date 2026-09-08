"""Kiểm tra `AgentAdminService` bằng repository giả trong bộ nhớ."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.core.errors import AuthorizationError, ValidationError
from app.domains.agent.application.admin_service import AgentAdminService
from app.domains.agent.domain.entities import Agent, Conversation, Message

TENANT = "org_1"
WORKSPACE = "ws_1"


class FakeAgentRepository:
    def __init__(self) -> None:
        self.rows: dict[str, Agent] = {}

    async def add(self, agent: Agent) -> None:
        self.rows[agent.id] = agent

    async def get(self, *, tenant_id: str, agent_id: str) -> Agent | None:
        agent = self.rows.get(agent_id)
        return agent if agent is not None and agent.tenant_id == tenant_id else None

    async def list_for_workspace(self, *, tenant_id: str, workspace_id: str) -> list[Agent]:
        return [
            a
            for a in self.rows.values()
            if a.tenant_id == tenant_id and a.workspace_id == workspace_id
        ]

    async def update(self, agent: Agent) -> None:
        self.rows[agent.id] = agent


class FakeConversationRepository:
    def __init__(self) -> None:
        self.rows: dict[str, Conversation] = {}

    async def add(self, conversation: Conversation) -> None:
        self.rows[conversation.id] = conversation

    async def get(self, *, tenant_id: str, conversation_id: str) -> Conversation | None:
        conversation = self.rows.get(conversation_id)
        return (
            conversation
            if conversation is not None and conversation.tenant_id == tenant_id
            else None
        )

    async def list_for_user(
        self, *, tenant_id: str, agent_id: str, user_id: str
    ) -> list[Conversation]:
        return [
            c
            for c in self.rows.values()
            if c.tenant_id == tenant_id and c.agent_id == agent_id and c.user_id == user_id
        ]

    async def update(self, conversation: Conversation) -> None:
        self.rows[conversation.id] = conversation


class FakeMessageRepository:
    def __init__(self) -> None:
        self.rows: dict[str, list[Message]] = {}

    async def add_many(self, messages: Sequence[Message]) -> None:
        for message in messages:
            self.rows.setdefault(message.conversation_id, []).append(message)

    async def list_for_conversation(self, *, tenant_id: str, conversation_id: str) -> list[Message]:
        return list(self.rows.get(conversation_id, []))


@pytest.fixture
def service() -> AgentAdminService:
    return AgentAdminService(
        agents=FakeAgentRepository(),
        conversations=FakeConversationRepository(),
        messages=FakeMessageRepository(),
    )


async def _create_agent(service: AgentAdminService, **overrides: object) -> Agent:
    base: dict[str, object] = {
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "name": "Trợ lý Slack",
        "system_prompt": "Bạn là trợ lý hữu ích.",
        "model": "claude-sonnet-5",
        "enabled_tool_ids": frozenset({"slack.post_message"}),
        "max_tool_calls_per_turn": 8,
        "created_by": "usr_1",
    }
    base.update(overrides)
    return await service.create_agent(**base)  # type: ignore[arg-type]


# ─── Agent ───────────────────────────────────────────────────────────────────


async def test_create_agent_chuan_hoa_ten_va_luu_lai(service: AgentAdminService) -> None:
    agent = await _create_agent(service, name="  Trợ lý   Slack  ")
    assert agent.name == "Trợ lý Slack"
    assert agent.id.startswith("agt_")

    found = await service.get_agent(tenant_id=TENANT, agent_id=agent.id)
    assert found == agent


async def test_create_agent_so_lan_goi_tool_ngoai_khoang_bi_tu_choi(
    service: AgentAdminService,
) -> None:
    with pytest.raises(ValidationError):
        await _create_agent(service, max_tool_calls_per_turn=0)


async def test_get_agent_khong_ton_tai_bao_authorization_error(
    service: AgentAdminService,
) -> None:
    with pytest.raises(AuthorizationError):
        await service.get_agent(tenant_id=TENANT, agent_id="agt_khong_ton_tai")


async def test_get_agent_thuoc_tenant_khac_cung_bao_nhu_khong_ton_tai(
    service: AgentAdminService,
) -> None:
    agent = await _create_agent(service)
    with pytest.raises(AuthorizationError):
        await service.get_agent(tenant_id="org_khac", agent_id=agent.id)


async def test_list_agents_loc_dung_workspace(service: AgentAdminService) -> None:
    a = await _create_agent(service, workspace_id="ws_a")
    await _create_agent(service, workspace_id="ws_b")

    found = await service.list_agents(tenant_id=TENANT, workspace_id="ws_a")
    assert [x.id for x in found] == [a.id]


async def test_archive_agent_nguoi_tao_lam_duoc(service: AgentAdminService) -> None:
    agent = await _create_agent(service, created_by="usr_1")
    archived = await service.archive_agent(
        tenant_id=TENANT, agent_id=agent.id, actor_id="usr_1", can_manage_workspace=False
    )
    assert archived.is_archived is True


async def test_archive_agent_nguoi_khac_khong_phai_admin_bi_tu_choi(
    service: AgentAdminService,
) -> None:
    agent = await _create_agent(service, created_by="usr_1")
    with pytest.raises(AuthorizationError):
        await service.archive_agent(
            tenant_id=TENANT, agent_id=agent.id, actor_id="usr_2", can_manage_workspace=False
        )


async def test_archive_agent_admin_workspace_lam_duoc_du_khong_phai_nguoi_tao(
    service: AgentAdminService,
) -> None:
    agent = await _create_agent(service, created_by="usr_1")
    archived = await service.archive_agent(
        tenant_id=TENANT, agent_id=agent.id, actor_id="usr_2", can_manage_workspace=True
    )
    assert archived.is_archived is True


async def test_unarchive_agent_dua_ve_trang_thai_hoat_dong(service: AgentAdminService) -> None:
    agent = await _create_agent(service, created_by="usr_1")
    await service.archive_agent(
        tenant_id=TENANT, agent_id=agent.id, actor_id="usr_1", can_manage_workspace=False
    )
    restored = await service.unarchive_agent(
        tenant_id=TENANT, agent_id=agent.id, actor_id="usr_1", can_manage_workspace=False
    )
    assert restored.is_archived is False
    assert restored.archived_at is None


async def test_unarchive_agent_chua_luu_tru_bao_loi(service: AgentAdminService) -> None:
    agent = await _create_agent(service, created_by="usr_1")
    with pytest.raises(ValidationError):
        await service.unarchive_agent(
            tenant_id=TENANT, agent_id=agent.id, actor_id="usr_1", can_manage_workspace=False
        )


async def test_unarchive_agent_nguoi_khac_khong_phai_admin_bi_tu_choi(
    service: AgentAdminService,
) -> None:
    agent = await _create_agent(service, created_by="usr_1")
    await service.archive_agent(
        tenant_id=TENANT, agent_id=agent.id, actor_id="usr_1", can_manage_workspace=False
    )
    with pytest.raises(AuthorizationError):
        await service.unarchive_agent(
            tenant_id=TENANT, agent_id=agent.id, actor_id="usr_2", can_manage_workspace=False
        )


# ─── Conversation ──────────────────────────────────────────────────────────────


async def test_create_conversation_voi_agent_da_luu_tru_bi_tu_choi(
    service: AgentAdminService,
) -> None:
    agent = await _create_agent(service, created_by="usr_1")
    await service.archive_agent(
        tenant_id=TENANT, agent_id=agent.id, actor_id="usr_1", can_manage_workspace=False
    )

    with pytest.raises(ValidationError):
        await service.create_conversation(
            tenant_id=TENANT, workspace_id=WORKSPACE, agent_id=agent.id, user_id="usr_1"
        )


async def test_get_conversation_nguoi_khac_khong_xem_duoc(service: AgentAdminService) -> None:
    agent = await _create_agent(service)
    conversation = await service.create_conversation(
        tenant_id=TENANT, workspace_id=WORKSPACE, agent_id=agent.id, user_id="usr_1"
    )

    with pytest.raises(AuthorizationError):
        await service.get_conversation(
            tenant_id=TENANT, conversation_id=conversation.id, user_id="usr_2"
        )


async def test_list_conversations_chi_thay_cua_chinh_minh(service: AgentAdminService) -> None:
    agent = await _create_agent(service)
    mine = await service.create_conversation(
        tenant_id=TENANT, workspace_id=WORKSPACE, agent_id=agent.id, user_id="usr_1"
    )
    await service.create_conversation(
        tenant_id=TENANT, workspace_id=WORKSPACE, agent_id=agent.id, user_id="usr_2"
    )

    found = await service.list_conversations(tenant_id=TENANT, agent_id=agent.id, user_id="usr_1")
    assert [c.id for c in found] == [mine.id]


# ─── Message ────────────────────────────────────────────────────────────────


async def test_list_messages_nguoi_khac_khong_xem_duoc(service: AgentAdminService) -> None:
    agent = await _create_agent(service)
    conversation = await service.create_conversation(
        tenant_id=TENANT, workspace_id=WORKSPACE, agent_id=agent.id, user_id="usr_1"
    )

    with pytest.raises(AuthorizationError):
        await service.list_messages(
            tenant_id=TENANT, conversation_id=conversation.id, user_id="usr_2"
        )
