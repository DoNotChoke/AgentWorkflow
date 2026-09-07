"""Repository của domain Agent, chạy trên PostgreSQL thật.

Chạy thật vì thứ đáng kiểm là việc trải phẳng/dựng lại `frozenset`/`tuple` qua
JSONB, và việc lọc theo tenant có đúng không — giả session đi thì test chỉ còn
khẳng định lại chính đoạn code đang viết.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from conftest import infra_unavailable
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import dispose_engine, init_engine, session_scope
from app.domains.agent.domain.entities import (
    Agent,
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
    ToolCallRequest,
    archive,
    archive_conversation,
)
from app.domains.agent.infrastructure.repositories import (
    SqlAlchemyAgentRepository,
    SqlAlchemyConnectionSelectionRepository,
    SqlAlchemyConversationRepository,
    SqlAlchemyMessageRepository,
)

pytestmark = pytest.mark.integration

TENANT = "org_agent_repo"
OTHER_TENANT = "org_agent_repo_khac"
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
async def engine() -> AsyncIterator[None]:
    init_engine(get_settings())
    try:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        await dispose_engine()
        infra_unavailable(f"PostgreSQL chưa sẵn sàng: {type(exc).__name__}. {exc}")

    yield

    async with session_scope() as session:
        ids = [TENANT, OTHER_TENANT]
        # Thứ tự xoá theo FK: message/conversation_connections -> conversation -> agent.
        await session.execute(
            text("DELETE FROM agent.conversation_connections WHERE tenant_id = ANY(:ids)"),
            {"ids": ids},
        )
        await session.execute(
            text("DELETE FROM agent.messages WHERE tenant_id = ANY(:ids)"), {"ids": ids}
        )
        await session.execute(
            text("DELETE FROM agent.conversations WHERE tenant_id = ANY(:ids)"), {"ids": ids}
        )
        await session.execute(
            text("DELETE FROM agent.agents WHERE tenant_id = ANY(:ids)"), {"ids": ids}
        )
    await dispose_engine()


def make_agent(**overrides: object) -> Agent:
    base: dict[str, object] = {
        "id": f"agt_{uuid.uuid4().hex}",
        "tenant_id": TENANT,
        "workspace_id": "ws_1",
        "name": "Trợ lý Slack",
        "system_prompt": "Bạn là trợ lý hữu ích.",
        "model": "claude-sonnet-5",
        "created_by": "usr_1",
        "created_at": NOW,
        "updated_at": NOW,
        "enabled_tool_ids": frozenset({"slack.post_message", "slack.list_channels"}),
    }
    base.update(overrides)
    return Agent(**base)  # type: ignore[arg-type]


def make_conversation(agent_id: str, **overrides: object) -> Conversation:
    base: dict[str, object] = {
        "id": f"cnv_{uuid.uuid4().hex}",
        "agent_id": agent_id,
        "tenant_id": TENANT,
        "workspace_id": "ws_1",
        "user_id": "usr_1",
        "status": ConversationStatus.ACTIVE,
        "created_at": NOW,
        "updated_at": NOW,
    }
    base.update(overrides)
    return Conversation(**base)  # type: ignore[arg-type]


def make_message(conversation_id: str, **overrides: object) -> Message:
    base: dict[str, object] = {
        "id": f"msg_{uuid.uuid4().hex}",
        "conversation_id": conversation_id,
        "role": MessageRole.USER,
        "created_at": NOW,
        "content": "Xin chào",
    }
    base.update(overrides)
    return Message(**base)  # type: ignore[arg-type]


# ─── Agent ───────────────────────────────────────────────────────────────────


async def test_agent_luu_va_doc_lai_dung_hinh_dang() -> None:
    agent = make_agent()
    async with session_scope() as session:
        await SqlAlchemyAgentRepository(session).add(agent)

    async with session_scope() as session:
        found = await SqlAlchemyAgentRepository(session).get(tenant_id=TENANT, agent_id=agent.id)

    assert found is not None
    assert found.enabled_tool_ids == frozenset({"slack.post_message", "slack.list_channels"})
    assert found.max_tool_calls_per_turn == agent.max_tool_calls_per_turn
    assert found.archived_at is None


async def test_agent_thuoc_tenant_khac_khong_doc_duoc() -> None:
    agent = make_agent()
    async with session_scope() as session:
        await SqlAlchemyAgentRepository(session).add(agent)

    async with session_scope() as session:
        found = await SqlAlchemyAgentRepository(session).get(
            tenant_id=OTHER_TENANT, agent_id=agent.id
        )

    assert found is None


async def test_agent_update_ghi_lai_luu_tru() -> None:
    agent = make_agent()
    async with session_scope() as session:
        await SqlAlchemyAgentRepository(session).add(agent)

    archived = archive(agent, at=NOW + timedelta(days=1))
    async with session_scope() as session:
        await SqlAlchemyAgentRepository(session).update(archived)

    async with session_scope() as session:
        found = await SqlAlchemyAgentRepository(session).get(tenant_id=TENANT, agent_id=agent.id)

    assert found is not None
    assert found.is_archived is True


async def test_agent_list_for_workspace_loc_dung_tenant_va_workspace() -> None:
    workspace_a = make_agent(workspace_id="ws_a")
    workspace_b = make_agent(workspace_id="ws_b")
    async with session_scope() as session:
        repo = SqlAlchemyAgentRepository(session)
        await repo.add(workspace_a)
        await repo.add(workspace_b)

    async with session_scope() as session:
        found = await SqlAlchemyAgentRepository(session).list_for_workspace(
            tenant_id=TENANT, workspace_id="ws_a"
        )

    assert [a.id for a in found] == [workspace_a.id]


# ─── Conversation ────────────────────────────────────────────────────────────


async def test_conversation_luu_va_doc_lai() -> None:
    agent = make_agent()
    conversation = make_conversation(agent.id)
    async with session_scope() as session:
        await SqlAlchemyAgentRepository(session).add(agent)
        await SqlAlchemyConversationRepository(session).add(conversation)

    async with session_scope() as session:
        found = await SqlAlchemyConversationRepository(session).get(
            tenant_id=TENANT, conversation_id=conversation.id
        )

    assert found is not None
    assert found.status is ConversationStatus.ACTIVE
    assert found.user_id == "usr_1"


async def test_conversation_list_for_user_moi_cap_nhat_truoc() -> None:
    agent = make_agent()
    first = make_conversation(agent.id, updated_at=NOW)
    second = make_conversation(agent.id, updated_at=NOW + timedelta(minutes=5))
    async with session_scope() as session:
        await SqlAlchemyAgentRepository(session).add(agent)
        repo = SqlAlchemyConversationRepository(session)
        await repo.add(first)
        await repo.add(second)

    async with session_scope() as session:
        found = await SqlAlchemyConversationRepository(session).list_for_user(
            tenant_id=TENANT, agent_id=agent.id, user_id="usr_1"
        )

    assert [c.id for c in found] == [second.id, first.id]


async def test_conversation_update_doi_trang_thai() -> None:
    agent = make_agent()
    conversation = make_conversation(agent.id)
    async with session_scope() as session:
        await SqlAlchemyAgentRepository(session).add(agent)
        await SqlAlchemyConversationRepository(session).add(conversation)

    archived = archive_conversation(conversation, at=NOW + timedelta(hours=1))
    async with session_scope() as session:
        await SqlAlchemyConversationRepository(session).update(archived)

    async with session_scope() as session:
        found = await SqlAlchemyConversationRepository(session).get(
            tenant_id=TENANT, conversation_id=conversation.id
        )

    assert found is not None
    assert found.status is ConversationStatus.ARCHIVED


# ─── Message ─────────────────────────────────────────────────────────────────


async def test_message_add_many_va_doc_lai_theo_thu_tu() -> None:
    agent = make_agent()
    conversation = make_conversation(agent.id)
    async with session_scope() as session:
        await SqlAlchemyAgentRepository(session).add(agent)
        await SqlAlchemyConversationRepository(session).add(conversation)

    messages = [
        make_message(
            conversation.id, role=MessageRole.USER, content="Gửi tin nhắn", created_at=NOW
        ),
        make_message(
            conversation.id,
            role=MessageRole.ASSISTANT,
            content="",
            created_at=NOW + timedelta(seconds=1),
            tool_calls=(
                ToolCallRequest(
                    call_id="call_1", tool_id="slack.post_message", input={"channel": "C1"}
                ),
            ),
        ),
        make_message(
            conversation.id,
            role=MessageRole.TOOL,
            content='{"channel_id": "C1"}',
            created_at=NOW + timedelta(seconds=2),
            tool_call_id="call_1",
        ),
    ]
    async with session_scope() as session:
        await SqlAlchemyMessageRepository(session).add_many(messages)

    async with session_scope() as session:
        found = await SqlAlchemyMessageRepository(session).list_for_conversation(
            tenant_id=TENANT, conversation_id=conversation.id
        )

    assert [m.role for m in found] == [MessageRole.USER, MessageRole.ASSISTANT, MessageRole.TOOL]
    assert found[1].tool_calls == (
        ToolCallRequest(call_id="call_1", tool_id="slack.post_message", input={"channel": "C1"}),
    )
    assert found[2].tool_call_id == "call_1"


async def test_message_add_many_rong_khong_lam_gi() -> None:
    async with session_scope() as session:
        await SqlAlchemyMessageRepository(session).add_many([])
    # Không ném lỗi là đủ — không có conversation nào để tra tenant.


async def test_message_khong_lan_sang_hoi_thoai_khac() -> None:
    agent = make_agent()
    mine = make_conversation(agent.id)
    other = make_conversation(agent.id, user_id="usr_2")
    async with session_scope() as session:
        await SqlAlchemyAgentRepository(session).add(agent)
        repo = SqlAlchemyConversationRepository(session)
        await repo.add(mine)
        await repo.add(other)

    async with session_scope() as session:
        await SqlAlchemyMessageRepository(session).add_many(
            [
                make_message(mine.id, content="của tôi"),
                make_message(other.id, content="của người khác"),
            ]
        )

    async with session_scope() as session:
        found = await SqlAlchemyMessageRepository(session).list_for_conversation(
            tenant_id=TENANT, conversation_id=mine.id
        )

    assert [m.content for m in found] == ["của tôi"]


# ─── ConnectionSelection (ADR-0004) ───────────────────────────────────────────


async def test_connection_selection_get_tra_ve_none_khi_chua_co() -> None:
    agent = make_agent()
    conversation = make_conversation(agent.id)
    async with session_scope() as session:
        await SqlAlchemyAgentRepository(session).add(agent)
        await SqlAlchemyConversationRepository(session).add(conversation)

    async with session_scope() as session:
        found = await SqlAlchemyConnectionSelectionRepository(session).get(
            conversation_id=conversation.id, provider="slack"
        )

    assert found is None


async def test_connection_selection_set_roi_get_doc_lai_dung() -> None:
    agent = make_agent()
    conversation = make_conversation(agent.id)
    async with session_scope() as session:
        await SqlAlchemyAgentRepository(session).add(agent)
        await SqlAlchemyConversationRepository(session).add(conversation)

    async with session_scope() as session:
        await SqlAlchemyConnectionSelectionRepository(session).set(
            conversation_id=conversation.id, provider="slack", connection_id="conn_a"
        )

    async with session_scope() as session:
        found = await SqlAlchemyConnectionSelectionRepository(session).get(
            conversation_id=conversation.id, provider="slack"
        )

    assert found == "conn_a"


async def test_connection_selection_set_lan_hai_ghi_de_khong_tao_dong_moi() -> None:
    """Lựa chọn là "sticky" nhưng đổi được bất cứ lúc nào (ADR-0004) — set lần
    hai cùng `(conversation_id, provider)` phải ghi đè, không tạo thêm dòng."""
    agent = make_agent()
    conversation = make_conversation(agent.id)
    async with session_scope() as session:
        await SqlAlchemyAgentRepository(session).add(agent)
        await SqlAlchemyConversationRepository(session).add(conversation)

    async with session_scope() as session:
        repo = SqlAlchemyConnectionSelectionRepository(session)
        await repo.set(conversation_id=conversation.id, provider="slack", connection_id="conn_a")
        await repo.set(conversation_id=conversation.id, provider="slack", connection_id="conn_b")

    async with session_scope() as session:
        found = await SqlAlchemyConnectionSelectionRepository(session).get(
            conversation_id=conversation.id, provider="slack"
        )
        count = await session.scalar(
            text(
                "SELECT count(*) FROM agent.conversation_connections "
                "WHERE conversation_id = :cid AND provider = 'slack'"
            ),
            {"cid": conversation.id},
        )

    assert found == "conn_b"
    assert count == 1
