"""Kiểm tra `AgentRuntime.run_turn` — vòng lặp Agent, không SDK/HTTP/DB thật.

Đây là test quan trọng nhất của Phase 3: chứng minh vòng lặp gọi model, gọi
tool, đưa kết quả lại cho model, và dừng đúng lúc, khớp §26 của design doc.
Mọi collaborator (`ModelClient`, `ToolCatalog`, `ToolExecutor`,
`ConnectionResolver`, `ExecutionTraceSink`) đều giả — cùng triết lý
`test_identity_service.py` dùng repository giả trong bộ nhớ.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from app.core.errors import (
    AppError,
    AuthorizationError,
    ConnectionChoiceRequiredError,
    MissingConnectionError,
    ProviderUnavailableError,
    ValidationError,
)
from app.domains.agent.application.ports import (
    ModelResponse,
    ModelToolCall,
    ModelToolSpec,
    ModelUsage,
    StopReason,
    ToolCallContext,
    ToolInvocationResult,
)
from app.domains.agent.application.service import AgentRuntime
from app.domains.agent.domain.entities import (
    Agent,
    Conversation,
    ConversationStatus,
    MessageRole,
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
        "enabled_tool_ids": frozenset({"slack.post_message"}),
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


TOOL_SPEC = ModelToolSpec(
    name="slack__post_message", description="Gửi tin nhắn", input_schema={"type": "object"}
)


def _end_turn(text: str) -> ModelResponse:
    return ModelResponse(
        text=text,
        tool_calls=(),
        stop_reason=StopReason.END_TURN,
        usage=ModelUsage(input_tokens=10, output_tokens=5),
        model="claude-sonnet-5",
    )


def _tool_use(*calls: ModelToolCall, text: str = "") -> ModelResponse:
    return ModelResponse(
        text=text,
        tool_calls=calls,
        stop_reason=StopReason.TOOL_USE,
        usage=ModelUsage(input_tokens=10, output_tokens=5),
        model="claude-sonnet-5",
    )


class ScriptedModelClient:
    """Trả về đúng response/exception đã định trước, theo thứ tự, mỗi lần
    `complete()` một cái — không đọc lịch sử để "nghĩ", chỉ diễn kịch bản."""

    def __init__(self, script: Sequence[ModelResponse | Exception]) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        *,
        model: str,
        system: str,
        turns: Sequence[Any],
        tools: Sequence[ModelToolSpec] = (),
        max_tokens: int = 8192,
    ) -> ModelResponse:
        self.calls.append(
            {"model": model, "system": system, "turns": list(turns), "tools": list(tools)}
        )
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeToolCatalog:
    def __init__(self, specs: Sequence[ModelToolSpec] = (TOOL_SPEC,)) -> None:
        self._specs = tuple(specs)

    async def list_tools(
        self,
        *,
        authorization: str,
        organization_id: str | None,
        workspace_id: str | None,
        allowed_tool_ids: frozenset[str],
    ) -> Sequence[ModelToolSpec]:
        return self._specs


class FakeToolExecutor:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._results: dict[str, ToolInvocationResult | Exception] = {}

    def set_result(self, name: str, result: ToolInvocationResult | Exception) -> None:
        self._results[name] = result

    async def invoke(
        self,
        *,
        name: str,
        tool_input: Mapping[str, Any],
        connection_id: str,
        authorization: str,
        organization_id: str | None,
        workspace_id: str | None,
        idempotency_key: str | None = None,
        caller: ToolCallContext | None = None,
    ) -> ToolInvocationResult:
        self.calls.append(
            {
                "name": name,
                "tool_input": dict(tool_input),
                "connection_id": connection_id,
                "idempotency_key": idempotency_key,
            }
        )
        result = self._results.get(
            name,
            ToolInvocationResult(output={"ok": True}, invocation_id="ti_default", replayed=False),
        )
        if isinstance(result, Exception):
            raise result
        return result


class FakeConnectionResolver:
    def __init__(self, connection_id: str | Exception = "conn_1") -> None:
        self.connection_id = connection_id
        self.calls: list[str] = []

    async def resolve(
        self,
        *,
        tool_id: str,
        authorization: str,
        organization_id: str | None,
        workspace_id: str | None,
    ) -> str:
        self.calls.append(tool_id)
        if isinstance(self.connection_id, Exception):
            raise self.connection_id
        return self.connection_id


class FakeConnectionSelectionStore:
    """`ConnectionSelectionStore` giả — bộ nhớ, khoá theo `(conversation_id,
    provider)`, cùng khuôn `FakeConnectionResolver`."""

    def __init__(self, initial: dict[tuple[str, str], str] | None = None) -> None:
        self._store: dict[tuple[str, str], str] = dict(initial or {})
        self.get_calls: list[tuple[str, str]] = []
        self.set_calls: list[tuple[str, str, str]] = []

    async def get(self, *, conversation_id: str, provider: str) -> str | None:
        self.get_calls.append((conversation_id, provider))
        return self._store.get((conversation_id, provider))

    async def set(self, *, conversation_id: str, provider: str, connection_id: str) -> None:
        self.set_calls.append((conversation_id, provider, connection_id))
        self._store[(conversation_id, provider)] = connection_id

    async def list_for_conversation(self, *, conversation_id: str) -> dict[str, str]:
        return {
            provider: connection_id
            for (cid, provider), connection_id in self._store.items()
            if cid == conversation_id
        }


class FakeTraceSink:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []

    async def append_step(
        self,
        *,
        kind: str,
        status: str,
        started_at: datetime,
        completed_at: datetime,
        attributes: Mapping[str, Any] | None = None,
        error_category: str | None = None,
    ) -> None:
        self.steps.append(
            {
                "kind": kind,
                "status": status,
                "attributes": dict(attributes or {}),
                "error_category": error_category,
            }
        )


class FakeEventSink:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def emit(self, event: Any) -> None:
        self.events.append(event)


def make_runtime(
    *,
    models: ScriptedModelClient,
    tools: FakeToolCatalog | None = None,
    executor: FakeToolExecutor | None = None,
    connections: FakeConnectionResolver | None = None,
    selections: FakeConnectionSelectionStore | None = None,
    trace: FakeTraceSink | None = None,
) -> AgentRuntime:
    return AgentRuntime(
        models=models,
        tools=tools or FakeToolCatalog(),
        executor=executor or FakeToolExecutor(),
        connections=connections or FakeConnectionResolver(),
        selections=selections or FakeConnectionSelectionStore(),
        trace=trace,
    )


async def run(
    runtime: AgentRuntime, agent: Agent, conversation: Conversation, **kwargs: Any
) -> Any:
    return await runtime.run_turn(
        agent=agent,
        conversation=conversation,
        history=kwargs.pop("history", ()),
        user_message=kwargs.pop("user_message", "Xin chào"),
        acting_user_id=kwargs.pop("acting_user_id", conversation.user_id),
        authorization="Bearer token",
        organization_id="org_1",
        workspace_id="ws_1",
        **kwargs,
    )


# ─── Không cần gọi tool ───────────────────────────────────────────────────────


async def test_khong_can_goi_tool_tra_loi_thang() -> None:
    models = ScriptedModelClient([_end_turn("Xin chào bạn.")])
    executor = FakeToolExecutor()
    runtime = make_runtime(models=models, executor=executor)

    result = await run(runtime, make_agent(), make_conversation())

    assert [m.role for m in result.messages] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert result.final_text == "Xin chào bạn."
    assert len(models.calls) == 1
    assert executor.calls == []


async def test_phan_hoi_rong_dung_fallback() -> None:
    models = ScriptedModelClient([_end_turn("")])
    runtime = make_runtime(models=models)

    result = await run(runtime, make_agent(), make_conversation())

    # Domain cấm message ASSISTANT rỗng-và-không-tool_calls — service phải
    # thay bằng một câu giữ chỗ, không phải để trống.
    assert result.final_text and result.final_text != ""


# ─── Một vòng gọi tool ────────────────────────────────────────────────────────


async def test_mot_vong_goi_tool_roi_tra_loi() -> None:
    call = ModelToolCall(call_id="call_1", name="slack__post_message", input={"channel": "C1"})
    models = ScriptedModelClient([_tool_use(call), _end_turn("Đã gửi xong.")])
    executor = FakeToolExecutor()
    executor.set_result(
        "slack__post_message",
        ToolInvocationResult(output={"channel_id": "C1"}, invocation_id="ti_1", replayed=False),
    )
    connections = FakeConnectionResolver("conn_slack")
    runtime = make_runtime(models=models, executor=executor, connections=connections)

    result = await run(runtime, make_agent(), make_conversation())

    roles = [m.role for m in result.messages]
    assert roles == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    assert result.messages[1].tool_calls[0].tool_id == "slack.post_message"
    assert result.messages[2].tool_call_id == "call_1"
    assert "C1" in result.messages[2].content
    assert result.final_text == "Đã gửi xong."

    assert len(executor.calls) == 1
    only_call = executor.calls[0]
    assert only_call["name"] == "slack__post_message"
    assert only_call["tool_input"] == {"channel": "C1"}
    assert only_call["connection_id"] == "conn_slack"
    # Tác động ghi bắt buộc phải kèm khoá idempotency — thiếu thì Integration
    # Gateway từ chối thẳng. Khoá gắn với lượt và với input.
    key = only_call["idempotency_key"]
    assert key and result.messages[0].id in key and "slack.post_message" in key
    assert connections.calls == ["slack.post_message"]

    # Lượt gọi model thứ hai phải thấy lại đúng lịch sử: assistant đã gọi tool,
    # rồi một lượt user mang tool_result.
    second_call_turns = models.calls[1]["turns"]
    assert second_call_turns[-2].role == "assistant"
    assert second_call_turns[-2].tool_calls[0].name == "slack__post_message"
    assert second_call_turns[-1].role == "user"
    assert second_call_turns[-1].tool_results[0].call_id == "call_1"


async def test_nhieu_tool_call_song_song_gop_thanh_mot_luot_ket_qua() -> None:
    call_a = ModelToolCall(call_id="call_a", name="slack__post_message", input={"channel": "A"})
    call_b = ModelToolCall(call_id="call_b", name="slack__post_message", input={"channel": "B"})
    models = ScriptedModelClient([_tool_use(call_a, call_b), _end_turn("Xong cả hai.")])
    executor = FakeToolExecutor()
    runtime = make_runtime(models=models, executor=executor)

    result = await run(runtime, make_agent(), make_conversation())

    tool_messages = [m for m in result.messages if m.role is MessageRole.TOOL]
    assert len(tool_messages) == 2
    assert len(executor.calls) == 2

    # Cả hai tool_result phải gộp vào ĐÚNG MỘT lượt `user` ở lần gọi model kế
    # tiếp — rải ra hai lượt sẽ dạy model ngừng gọi song song (skill claude-api).
    second_call_turns = models.calls[1]["turns"]
    result_turn = second_call_turns[-1]
    assert result_turn.role == "user"
    assert len(result_turn.tool_results) == 2
    assert {r.call_id for r in result_turn.tool_results} == {"call_a", "call_b"}


async def test_khoa_idempotency_on_dinh_trong_luot_doi_giua_cac_luot() -> None:
    """Model gọi lại đúng cùng một thao tác trong một lượt (thường vì thấy lỗi)
    phải cho **cùng** khoá idempotency — Gateway nhờ đó phát lại kết quả cũ
    thay vì gửi Slack lần nữa. Input khác thì khoá khác. Lượt sau thì khoá
    khác hẳn, nên "gửi lại tin đó" vẫn là một lần gửi thật."""
    same = {"channel": "C1", "text": "hi"}
    other = {"channel": "C1", "text": "khác"}
    models = ScriptedModelClient(
        [
            _tool_use(ModelToolCall(call_id="c1", name="slack__post_message", input=same)),
            _tool_use(ModelToolCall(call_id="c2", name="slack__post_message", input=dict(same))),
            _tool_use(ModelToolCall(call_id="c3", name="slack__post_message", input=other)),
            _end_turn("Xong."),
        ]
    )
    executor = FakeToolExecutor()
    runtime = make_runtime(models=models, executor=executor)

    result = await run(runtime, make_agent(), make_conversation())

    keys = [c["idempotency_key"] for c in executor.calls]
    assert keys[0] == keys[1]  # cùng thao tác, cùng lượt → cùng khoá
    assert keys[2] != keys[0]  # input khác → khoá khác
    assert all(result.messages[0].id in k for k in keys)

    # Lượt thứ hai: cùng input hệt lượt đầu, nhưng khoá phải khác.
    models_2 = ScriptedModelClient(
        [
            _tool_use(ModelToolCall(call_id="d1", name="slack__post_message", input=dict(same))),
            _end_turn("Xong."),
        ]
    )
    executor_2 = FakeToolExecutor()
    runtime_2 = make_runtime(models=models_2, executor=executor_2)
    await run(runtime_2, make_agent(), make_conversation())

    assert executor_2.calls[0]["idempotency_key"] != keys[0]


# ─── Tool hỏng không làm vỡ lượt ──────────────────────────────────────────────


async def test_tool_khong_duoc_agent_cho_phep_thanh_loi_khong_goi_executor() -> None:
    call = ModelToolCall(call_id="call_1", name="slack__list_channels", input={})
    models = ScriptedModelClient([_tool_use(call), _end_turn("Không gọi được.")])
    executor = FakeToolExecutor()
    connections = FakeConnectionResolver()
    runtime = make_runtime(models=models, executor=executor, connections=connections)

    result = await run(runtime, make_agent(), make_conversation())  # chỉ enable post_message

    tool_message = next(m for m in result.messages if m.role is MessageRole.TOOL)
    assert tool_message.content.startswith("Lỗi:")
    assert executor.calls == []
    assert connections.calls == []


async def test_ket_noi_khong_giai_quyet_duoc_thanh_loi_tool() -> None:
    call = ModelToolCall(call_id="call_1", name="slack__post_message", input={})
    models = ScriptedModelClient([_tool_use(call), _end_turn("ok")])
    executor = FakeToolExecutor()
    connections = FakeConnectionResolver(ValidationError("Chưa kết nối Slack nào."))
    runtime = make_runtime(models=models, executor=executor, connections=connections)

    result = await run(runtime, make_agent(), make_conversation())

    tool_message = next(m for m in result.messages if m.role is MessageRole.TOOL)
    assert "Chưa kết nối Slack nào." in tool_message.content
    assert executor.calls == []


async def test_thuc_thi_tool_that_bai_thanh_loi_tool_khong_nem_ra_ngoai() -> None:
    call = ModelToolCall(call_id="call_1", name="slack__post_message", input={})
    models = ScriptedModelClient([_tool_use(call), _end_turn("Slack đang lỗi, để tôi báo bạn.")])
    executor = FakeToolExecutor()
    executor.set_result("slack__post_message", ProviderUnavailableError("Slack đang bận."))
    runtime = make_runtime(models=models, executor=executor)

    result = await run(runtime, make_agent(), make_conversation())

    tool_message = next(m for m in result.messages if m.role is MessageRole.TOOL)
    assert "Slack đang bận." in tool_message.content
    assert result.final_text == "Slack đang lỗi, để tôi báo bạn."


# ─── Lựa chọn connection (ADR-0004) ───────────────────────────────────────────


async def test_lua_chon_da_luu_thi_khong_resolve_lai() -> None:
    call = ModelToolCall(call_id="call_1", name="slack__post_message", input={})
    models = ScriptedModelClient([_tool_use(call), _end_turn("xong")])
    executor = FakeToolExecutor()
    connections = FakeConnectionResolver("khong_duoc_dung_toi")
    selections = FakeConnectionSelectionStore({("cnv_1", "slack"): "conn_da_chon"})
    runtime = make_runtime(
        models=models, executor=executor, connections=connections, selections=selections
    )

    await run(runtime, make_agent(), make_conversation())

    assert connections.calls == []  # đã có lựa chọn lưu sẵn, không resolve lại
    assert executor.calls[0]["connection_id"] == "conn_da_chon"


async def test_resolve_thanh_cong_tu_luu_dung_lai_cho_lan_goi_sau() -> None:
    call_1 = ModelToolCall(call_id="call_1", name="slack__post_message", input={})
    call_2 = ModelToolCall(call_id="call_2", name="slack__post_message", input={})
    models = ScriptedModelClient([_tool_use(call_1), _tool_use(call_2), _end_turn("xong")])
    executor = FakeToolExecutor()
    connections = FakeConnectionResolver("conn_slack")
    selections = FakeConnectionSelectionStore()
    runtime = make_runtime(
        models=models, executor=executor, connections=connections, selections=selections
    )

    await run(runtime, make_agent(), make_conversation())

    # Chỉ resolve đúng một lần — lần gọi tool thứ hai dùng lại lựa chọn vừa lưu.
    assert connections.calls == ["slack.post_message"]
    assert selections.set_calls == [("cnv_1", "slack", "conn_slack")]
    assert [c["connection_id"] for c in executor.calls] == ["conn_slack", "conn_slack"]


async def test_thieu_connection_thanh_loi_tool_mang_dung_category() -> None:
    from app.domains.agent.application.ports import AgentEventKind

    call = ModelToolCall(call_id="call_1", name="slack__post_message", input={})
    models = ScriptedModelClient([_tool_use(call), _end_turn("ok")])
    connections = FakeConnectionResolver(
        MissingConnectionError("Chưa có kết nối slack nào.", details={"provider": "slack"})
    )
    selections = FakeConnectionSelectionStore()
    events = FakeEventSink()
    runtime = make_runtime(models=models, connections=connections, selections=selections)

    await run(runtime, make_agent(), make_conversation(), on_event=events)

    completed = next(e for e in events.events if e.kind is AgentEventKind.TOOL_CALL_COMPLETED)
    assert completed.is_error is True
    assert completed.error_category == "MissingConnection"
    assert completed.error_details == {"provider": "slack"}
    assert selections.set_calls == []  # không có gì để lưu khi resolve lỗi


async def test_nhieu_connection_kha_dung_thanh_loi_tool_mang_dung_candidates() -> None:
    from app.domains.agent.application.ports import AgentEventKind

    call = ModelToolCall(call_id="call_1", name="slack__post_message", input={})
    models = ScriptedModelClient([_tool_use(call), _end_turn("ok")])
    candidates = [
        {
            "connection_id": "conn_a",
            "display_name": "Acme HQ",
            "sharing": "workspace",
            "external_account_label": None,
        },
        {
            "connection_id": "conn_b",
            "display_name": "Cá nhân",
            "sharing": "private",
            "external_account_label": None,
        },
    ]
    connections = FakeConnectionResolver(
        ConnectionChoiceRequiredError(
            "Có 2 kết nối slack khả dụng — cần chọn một trước khi dùng tool này.",
            details={"provider": "slack", "candidates": candidates},
        )
    )
    selections = FakeConnectionSelectionStore()
    events = FakeEventSink()
    runtime = make_runtime(models=models, connections=connections, selections=selections)

    result = await run(runtime, make_agent(), make_conversation(), on_event=events)

    tool_message = next(m for m in result.messages if m.role is MessageRole.TOOL)
    assert "Có 2 kết nối" in tool_message.content

    completed = next(e for e in events.events if e.kind is AgentEventKind.TOOL_CALL_COMPLETED)
    assert completed.error_category == "ConnectionChoiceRequired"
    assert completed.error_details["candidates"] == candidates
    assert selections.set_calls == []


# ─── Ngân sách gọi tool ───────────────────────────────────────────────────────


async def test_het_ngan_sach_thi_khong_moi_tool_nua() -> None:
    call = ModelToolCall(call_id="call_1", name="slack__post_message", input={})
    models = ScriptedModelClient([_tool_use(call), _end_turn("Dừng ở đây thôi.")])
    runtime = make_runtime(models=models)

    await run(runtime, make_agent(max_tool_calls_per_turn=1), make_conversation())

    assert models.calls[0]["tools"] == [TOOL_SPEC]
    assert models.calls[1]["tools"] == []


# ─── Ràng buộc trước khi vào vòng lặp ─────────────────────────────────────────


async def test_agent_da_luu_tru_bi_tu_choi() -> None:
    models = ScriptedModelClient([_end_turn("không tới lượt")])
    runtime = make_runtime(models=models)

    with pytest.raises(ValidationError):
        await run(runtime, make_agent(archived_at=NOW), make_conversation())
    assert models.calls == []


async def test_hoi_thoai_khong_phai_cua_minh_bi_tu_choi() -> None:
    models = ScriptedModelClient([_end_turn("không tới lượt")])
    runtime = make_runtime(models=models)

    with pytest.raises(AuthorizationError):
        await run(
            runtime,
            make_agent(),
            make_conversation(user_id="usr_khac"),
            acting_user_id="usr_1",
        )
    assert models.calls == []


async def test_hoi_thoai_da_luu_tru_bi_tu_choi() -> None:
    models = ScriptedModelClient([_end_turn("không tới lượt")])
    runtime = make_runtime(models=models)

    with pytest.raises(ValidationError):
        await run(runtime, make_agent(), make_conversation(status=ConversationStatus.ARCHIVED))
    assert models.calls == []


# ─── Sự kiện tiến độ (on_event, WP10) ─────────────────────────────────────────


async def test_on_event_phat_dung_thu_tu_khi_co_goi_tool() -> None:
    from app.domains.agent.application.ports import AgentEventKind

    call = ModelToolCall(call_id="call_1", name="slack__post_message", input={})
    models = ScriptedModelClient([_tool_use(call), _end_turn("xong")])
    events = FakeEventSink()
    runtime = make_runtime(models=models)

    result = await run(runtime, make_agent(), make_conversation(), on_event=events)

    kinds = [event.kind for event in events.events]
    assert kinds == [
        AgentEventKind.TOOL_CALL_STARTED,
        AgentEventKind.TOOL_CALL_COMPLETED,
        AgentEventKind.FINAL,
    ]
    assert events.events[0].tool_id == "slack.post_message"
    assert events.events[1].is_error is False
    assert events.events[2].text == result.final_text


async def test_on_event_danh_dau_tool_that_bai() -> None:
    from app.domains.agent.application.ports import AgentEventKind

    call = ModelToolCall(call_id="call_1", name="slack__post_message", input={})
    models = ScriptedModelClient([_tool_use(call), _end_turn("xong")])
    executor = FakeToolExecutor()
    executor.set_result("slack__post_message", ProviderUnavailableError("Slack đang bận."))
    events = FakeEventSink()
    runtime = make_runtime(models=models, executor=executor)

    await run(runtime, make_agent(), make_conversation(), on_event=events)

    completed_event = next(e for e in events.events if e.kind is AgentEventKind.TOOL_CALL_COMPLETED)
    assert completed_event.is_error is True


async def test_khong_khai_on_event_van_chay_binh_thuong() -> None:
    models = ScriptedModelClient([_end_turn("ok")])
    runtime = make_runtime(models=models)

    result = await run(runtime, make_agent(), make_conversation())

    assert result.final_text == "ok"


# ─── Execution Trace ──────────────────────────────────────────────────────────


async def test_ghi_dung_cac_buoc_vao_trace() -> None:
    call = ModelToolCall(call_id="call_1", name="slack__post_message", input={})
    models = ScriptedModelClient([_tool_use(call), _end_turn("xong")])
    trace = FakeTraceSink()
    runtime = make_runtime(models=models, trace=trace)

    await run(runtime, make_agent(), make_conversation())

    kinds = [step["kind"] for step in trace.steps]
    assert kinds == ["model_call", "tool_call", "model_call", "final_response"]
    assert all(step["status"] == "succeeded" for step in trace.steps)
    assert trace.steps[0]["attributes"]["stop_reason"] == "tool_use"
    assert trace.steps[1]["attributes"]["tool_id"] == "slack.post_message"


async def test_tool_replayed_duoc_ghi_dung_trang_thai_vao_trace() -> None:
    call = ModelToolCall(call_id="call_1", name="slack__post_message", input={})
    models = ScriptedModelClient([_tool_use(call), _end_turn("xong")])
    executor = FakeToolExecutor()
    executor.set_result(
        "slack__post_message",
        ToolInvocationResult(output={}, invocation_id="ti_1", replayed=True),
    )
    trace = FakeTraceSink()
    runtime = make_runtime(models=models, executor=executor, trace=trace)

    await run(runtime, make_agent(), make_conversation())

    tool_step = next(step for step in trace.steps if step["kind"] == "tool_call")
    assert tool_step["status"] == "replayed"


async def test_loi_model_duoc_ghi_vao_trace_roi_nem_lai() -> None:
    error: AppError = ProviderUnavailableError("Anthropic đang gặp sự cố.")
    models = ScriptedModelClient([error])
    trace = FakeTraceSink()
    runtime = make_runtime(models=models, trace=trace)

    with pytest.raises(ProviderUnavailableError):
        await run(runtime, make_agent(), make_conversation())

    assert trace.steps == [
        {
            "kind": "model_call",
            "status": "failed",
            "attributes": {"model": "claude-sonnet-5"},
            "error_category": "ProviderUnavailable",
        }
    ]
