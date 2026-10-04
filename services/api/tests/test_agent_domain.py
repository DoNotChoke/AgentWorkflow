"""Kiểm tra quy tắc nghiệp vụ thuần của Agent.

Không có hạ tầng nào trong file này — đó là điều tầng ``domain`` phải bảo đảm
(§30). Nếu một ngày nào đó những test này cần database mới chạy được, nghĩa là
ranh giới đã bị vi phạm.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import AuthorizationError, ValidationError
from app.domains.agent.domain.entities import (
    MAX_MAX_TOOL_CALLS_PER_TURN,
    MAX_MESSAGE_CONTENT_LENGTH,
    MAX_NAME_LENGTH,
    MAX_SYSTEM_PROMPT_LENGTH,
    MIN_MAX_TOOL_CALLS_PER_TURN,
    Agent,
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
    ToolCallRequest,
    archive,
    archive_conversation,
    ensure_active,
    ensure_can_manage,
    ensure_message_shape,
    ensure_not_archived,
    ensure_owns,
    normalize_name,
    normalize_system_prompt,
    unarchive,
    validate_max_tool_calls_per_turn,
)

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def make_agent(**overrides: object) -> Agent:
    base: dict[str, object] = {
        "id": "agt_1",
        "tenant_id": "org_1",
        "workspace_id": "ws_1",
        "name": "Trợ lý Slack",
        "system_prompt": "Bạn là trợ lý hữu ích.",
        "model": "claude-sonnet-5",
        "created_by": "usr_1",
        "created_at": NOW,
        "updated_at": NOW,
    }
    base.update(overrides)
    return Agent(**base)  # type: ignore[arg-type]


def make_conversation(**overrides: object) -> Conversation:
    base: dict[str, object] = {
        "id": "cnv_1",
        "agent_id": "agt_1",
        "tenant_id": "org_1",
        "workspace_id": "ws_1",
        "user_id": "usr_1",
        "status": ConversationStatus.ACTIVE,
        "created_at": NOW,
        "updated_at": NOW,
    }
    base.update(overrides)
    return Conversation(**base)  # type: ignore[arg-type]


def make_message(**overrides: object) -> Message:
    base: dict[str, object] = {
        "id": "msg_1",
        "conversation_id": "cnv_1",
        "role": MessageRole.USER,
        "created_at": NOW,
        "content": "Xin chào",
    }
    base.update(overrides)
    return Message(**base)  # type: ignore[arg-type]


# ─── normalize_name ──────────────────────────────────────────────────────────


def test_ten_duoc_cat_khoang_trang_thua() -> None:
    assert normalize_name("  Trợ   lý   Slack  ") == "Trợ lý Slack"


def test_ten_rong_bi_tu_choi() -> None:
    with pytest.raises(ValidationError):
        normalize_name("   ")


def test_ten_qua_dai_bi_tu_choi() -> None:
    with pytest.raises(ValidationError):
        normalize_name("a" * (MAX_NAME_LENGTH + 1))


# ─── normalize_system_prompt ─────────────────────────────────────────────────


def test_system_prompt_rong_hop_le() -> None:
    assert normalize_system_prompt("   ") == ""


def test_system_prompt_qua_dai_bi_tu_choi() -> None:
    with pytest.raises(ValidationError):
        normalize_system_prompt("a" * (MAX_SYSTEM_PROMPT_LENGTH + 1))


# ─── validate_max_tool_calls_per_turn ────────────────────────────────────────


@pytest.mark.parametrize("value", [MIN_MAX_TOOL_CALLS_PER_TURN, MAX_MAX_TOOL_CALLS_PER_TURN, 8])
def test_so_lan_goi_tool_trong_khoang_hop_le(value: int) -> None:
    assert validate_max_tool_calls_per_turn(value) == value


@pytest.mark.parametrize(
    "value", [MIN_MAX_TOOL_CALLS_PER_TURN - 1, MAX_MAX_TOOL_CALLS_PER_TURN + 1, 0, -1]
)
def test_so_lan_goi_tool_ngoai_khoang_bi_tu_choi(value: int) -> None:
    with pytest.raises(ValidationError):
        validate_max_tool_calls_per_turn(value)


# ─── Agent.allows_tool ───────────────────────────────────────────────────────


def test_agent_rong_khong_goi_duoc_tool_nao_cua_provider() -> None:
    agent = make_agent()
    assert agent.allows_tool("slack.post_message") is False


def test_tool_dung_san_khong_can_bat() -> None:
    assert make_agent().allows_tool("system.transform") is True


def test_agent_chi_goi_duoc_tool_trong_whitelist() -> None:
    agent = make_agent(enabled_tool_ids=frozenset({"slack.post_message"}))
    assert agent.allows_tool("slack.post_message") is True
    assert agent.allows_tool("slack.list_channels") is False


# ─── Agent: lưu trữ ───────────────────────────────────────────────────────────


def test_agent_moi_chua_bi_luu_tru() -> None:
    assert make_agent().is_archived is False


def test_ensure_not_archived_qua_voi_agent_dang_hoat_dong() -> None:
    ensure_not_archived(make_agent())


def test_ensure_not_archived_tu_choi_agent_da_luu_tru() -> None:
    agent = make_agent(archived_at=NOW)
    with pytest.raises(ValidationError):
        ensure_not_archived(agent)


def test_archive_dong_dau_thoi_gian() -> None:
    agent = make_agent()
    at = NOW + timedelta(days=1)

    archived = archive(agent, at=at)

    assert archived.is_archived is True
    assert archived.archived_at == at
    assert archived.updated_at == at
    # Bất biến: hàm thuần không sửa đối tượng gốc.
    assert agent.is_archived is False


def test_archive_tu_choi_agent_da_luu_tru_roi() -> None:
    agent = make_agent(archived_at=NOW)
    with pytest.raises(ValidationError):
        archive(agent, at=NOW + timedelta(days=1))


def test_unarchive_xoa_dau_thoi_gian_luu_tru() -> None:
    agent = make_agent(archived_at=NOW)
    at = NOW + timedelta(days=2)

    restored = unarchive(agent, at=at)

    assert restored.is_archived is False
    assert restored.archived_at is None
    assert restored.updated_at == at
    # Bất biến: hàm thuần không sửa đối tượng gốc.
    assert agent.is_archived is True


def test_unarchive_tu_choi_agent_dang_hoat_dong() -> None:
    with pytest.raises(ValidationError):
        unarchive(make_agent(), at=NOW)


# ─── Agent: quyền quản lý ─────────────────────────────────────────────────────


def test_nguoi_tao_quan_ly_duoc_agent_cua_minh() -> None:
    agent = make_agent(created_by="usr_1")
    ensure_can_manage(agent, actor_id="usr_1", can_manage_workspace=False)


def test_quan_tri_vien_workspace_quan_ly_duoc_agent_nguoi_khac_tao() -> None:
    agent = make_agent(created_by="usr_1")
    ensure_can_manage(agent, actor_id="usr_2", can_manage_workspace=True)


def test_thanh_vien_thuong_khong_quan_ly_duoc_agent_nguoi_khac() -> None:
    agent = make_agent(created_by="usr_1")
    with pytest.raises(AuthorizationError):
        ensure_can_manage(agent, actor_id="usr_2", can_manage_workspace=False)


# ─── Conversation: quyền sở hữu ───────────────────────────────────────────────


def test_ensure_owns_qua_voi_dung_chu() -> None:
    ensure_owns(make_conversation(user_id="usr_1"), user_id="usr_1")


def test_ensure_owns_tu_choi_nguoi_khac() -> None:
    with pytest.raises(AuthorizationError):
        ensure_owns(make_conversation(user_id="usr_1"), user_id="usr_2")


# ─── Conversation: lưu trữ ────────────────────────────────────────────────────


def test_ensure_active_qua_voi_hoi_thoai_dang_mo() -> None:
    ensure_active(make_conversation())


def test_ensure_active_tu_choi_hoi_thoai_da_luu_tru() -> None:
    conversation = make_conversation(status=ConversationStatus.ARCHIVED)
    with pytest.raises(ValidationError):
        ensure_active(conversation)


def test_archive_conversation_doi_trang_thai() -> None:
    conversation = make_conversation()
    at = NOW + timedelta(hours=1)

    archived = archive_conversation(conversation, at=at)

    assert archived.status is ConversationStatus.ARCHIVED
    assert archived.updated_at == at
    assert archived.is_active is False
    assert conversation.status is ConversationStatus.ACTIVE


def test_archive_conversation_tu_choi_khi_da_luu_tru() -> None:
    conversation = make_conversation(status=ConversationStatus.ARCHIVED)
    with pytest.raises(ValidationError):
        archive_conversation(conversation, at=NOW)


# ─── Message: hình dạng theo vai trò ──────────────────────────────────────────


def test_message_user_hop_le() -> None:
    ensure_message_shape(make_message(role=MessageRole.USER, content="Xin chào"))


def test_message_user_rong_bi_tu_choi() -> None:
    with pytest.raises(ValidationError):
        ensure_message_shape(make_message(role=MessageRole.USER, content="   "))


def test_message_user_mang_tool_call_id_bi_tu_choi() -> None:
    with pytest.raises(ValidationError):
        ensure_message_shape(
            make_message(role=MessageRole.USER, content="hi", tool_call_id="call_1")
        )


def test_message_assistant_chi_co_noi_dung_hop_le() -> None:
    ensure_message_shape(make_message(role=MessageRole.ASSISTANT, content="Đã xong."))


def test_message_assistant_chi_co_tool_calls_hop_le() -> None:
    message = make_message(
        role=MessageRole.ASSISTANT,
        content="",
        tool_calls=(ToolCallRequest(call_id="call_1", tool_id="slack.post_message"),),
    )
    ensure_message_shape(message)


def test_message_assistant_khong_co_gi_bi_tu_choi() -> None:
    with pytest.raises(ValidationError):
        ensure_message_shape(make_message(role=MessageRole.ASSISTANT, content=""))


def test_message_assistant_mang_tool_call_id_bi_tu_choi() -> None:
    with pytest.raises(ValidationError):
        ensure_message_shape(
            make_message(role=MessageRole.ASSISTANT, content="hi", tool_call_id="call_1")
        )


def test_message_tool_hop_le() -> None:
    message = make_message(role=MessageRole.TOOL, content="đã gửi", tool_call_id="call_1")
    ensure_message_shape(message)


def test_message_tool_thieu_tool_call_id_bi_tu_choi() -> None:
    with pytest.raises(ValidationError):
        ensure_message_shape(make_message(role=MessageRole.TOOL, content="đã gửi"))


def test_message_tool_mang_tool_calls_moi_bi_tu_choi() -> None:
    message = make_message(
        role=MessageRole.TOOL,
        content="đã gửi",
        tool_call_id="call_1",
        tool_calls=(ToolCallRequest(call_id="call_2", tool_id="slack.post_message"),),
    )
    with pytest.raises(ValidationError):
        ensure_message_shape(message)


def test_message_qua_dai_bi_tu_choi() -> None:
    message = make_message(role=MessageRole.USER, content="a" * (MAX_MESSAGE_CONTENT_LENGTH + 1))
    with pytest.raises(ValidationError):
        ensure_message_shape(message)
