"""Vòng lặp Agent: nhận tin nhắn, gọi model, gọi tool, lặp lại."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.errors import AppError, AuthorizationError
from app.core.ids import ResourceKind, new_id
from app.domains.agent.application.ports import (
    AgentEvent,
    AgentEventKind,
    AgentEventSink,
    ConnectionResolver,
    ConnectionSelectionStore,
    ExecutionTraceSink,
    ModelClient,
    ModelResponse,
    ModelToolCall,
    ModelToolResult,
    ModelToolSpec,
    ModelTurn,
    StopReason,
    ToolCallContext,
    ToolCatalog,
    ToolExecutor,
)
from app.domains.agent.application.tool_naming import from_model_name, to_model_name
from app.domains.agent.domain.entities import (
    Agent,
    Conversation,
    Message,
    MessageRole,
    ToolCallRequest,
    ensure_active,
    ensure_message_shape,
    ensure_not_archived,
    ensure_owns,
)

#: Domain cấm message ASSISTANT rỗng-và-không-tool_calls (`ensure_message_shape`).
#: Model trả lời trống (hiếm, nhưng có thể xảy ra khi `stop_reason=max_tokens`
#: cắt ngay từ đầu) thì thay bằng câu này thay vì vỡ bất biến của domain.
_EMPTY_RESPONSE_FALLBACK = "(không có phản hồi)"


class _NullTraceSink:
    """Mặc định khi không truyền `trace` — không ghi gì cả."""

    __slots__ = ()

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
        # Tham số phải khớp tên với Protocol (gọi bằng keyword) nên không đổi
        # thành `_kind` được — `del` là cách không chạm ARG002 mà không đổi
        # chữ ký.
        del kind, status, started_at, completed_at, attributes, error_category


class _NullEventSink:
    """Mặc định khi không truyền `on_event` — không phát gì cả."""

    __slots__ = ()

    async def emit(self, event: AgentEvent) -> None:
        del event


@dataclass(frozen=True, slots=True)
class TurnResult:
    """Mọi `Message` mới sinh ra trong một lượt, theo đúng thứ tự thời gian:
    tin nhắn người dùng vừa gửi, rồi xen kẽ assistant/tool cho tới câu trả lời
    cuối cùng."""

    messages: tuple[Message, ...]

    @property
    def final_text(self) -> str:
        return self.messages[-1].content


@dataclass(frozen=True, slots=True)
class _ToolCallOutcome:
    """Kết quả nội bộ của một lần gọi tool — mang thêm cấu trúc lỗi (nếu có)
    ra khỏi `_execute_tool_call`, để `run_turn` đính vào `AgentEvent` cho FE
    vẽ đúng (ví dụ thẻ chọn connection, ADR-0004) thay vì chỉ một dòng chữ."""

    message: Message
    is_error: bool
    error_category: str | None = None
    error_details: Mapping[str, Any] | None = None


def _make_message(
    conversation_id: str,
    *,
    role: MessageRole,
    content: str = "",
    tool_calls: tuple[ToolCallRequest, ...] = (),
    tool_call_id: str | None = None,
) -> Message:
    message = Message(
        id=new_id(ResourceKind.MESSAGE),
        conversation_id=conversation_id,
        role=role,
        created_at=datetime.now(UTC),
        content=content,
        tool_calls=tool_calls,
        tool_call_id=tool_call_id,
    )
    ensure_message_shape(message)
    return message


def _to_model_turns(messages: Sequence[Message]) -> list[ModelTurn]:
    """Dựng lại lịch sử cho model từ `Message` đã lưu.

    Không map 1-1: nhiều `Message` vai trò TOOL liên tiếp (kết quả của nhiều
    tool_calls song song trong cùng một lượt gọi model) phải gộp vào một
    `ModelTurn` role `user` duy nhất — đúng hình dạng Anthropic/OpenAI đều đòi
    ("trả mọi tool_result trong một message, không rải ra nhiều message").
    """
    turns: list[ModelTurn] = []
    pending_results: list[ModelToolResult] = []

    def flush() -> None:
        if pending_results:
            turns.append(ModelTurn(role="user", tool_results=tuple(pending_results)))
            pending_results.clear()

    for message in messages:
        if message.role is MessageRole.TOOL:
            assert message.tool_call_id is not None  # bảo đảm bởi ensure_message_shape
            pending_results.append(
                ModelToolResult(call_id=message.tool_call_id, content=message.content)
            )
            continue

        flush()

        if message.role is MessageRole.USER:
            turns.append(ModelTurn(role="user", text=message.content))
        else:  # MessageRole.ASSISTANT
            turns.append(
                ModelTurn(
                    role="assistant",
                    text=message.content,
                    tool_calls=tuple(
                        ModelToolCall(
                            call_id=call.call_id,
                            name=to_model_name(call.tool_id),
                            input=call.input,
                        )
                        for call in message.tool_calls
                    ),
                )
            )

    flush()
    return turns


def _idempotency_key(*, turn_id: str, tool_id: str, tool_input: Mapping[str, Any]) -> str:
    """Khoá idempotency cho một lời gọi tool bên trong một lượt.

    Tác động ghi ra bên ngoài (ví dụ `slack.post_message`) bắt buộc phải có
    khoá này — Integration Gateway từ chối thẳng nếu thiếu. Khoá phải *ổn định
    trong phạm vi một lượt*: nếu model quyết định gọi lại đúng cùng một thao
    tác sau khi thấy lỗi, hoặc runtime thử lại sau khi mất phản hồi mạng,
    Gateway thấy trùng khoá và phát lại kết quả cũ thay vì gây side effect lần
    nữa. `turn_id` đổi theo từng lượt nên "gửi lại tin đó" ở lượt sau vẫn là
    một lần gửi thật.

    Vân tay input dùng cùng công thức `fingerprint` của domain integration
    (`sort_keys`, `ensure_ascii=False`) — hai dict cùng nội dung khác thứ tự
    khoá cho cùng một khoá idempotency.
    """
    canonical = json.dumps(tool_input, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{turn_id}:{tool_id}:{digest}"


class AgentRuntime:
    def __init__(
        self,
        *,
        models: ModelClient,
        tools: ToolCatalog,
        executor: ToolExecutor,
        connections: ConnectionResolver,
        selections: ConnectionSelectionStore,
        trace: ExecutionTraceSink | None = None,
    ) -> None:
        self._models = models
        self._tools = tools
        self._executor = executor
        self._connections = connections
        self._selections = selections
        self._trace = trace or _NullTraceSink()

    async def run_turn(
        self,
        *,
        agent: Agent,
        conversation: Conversation,
        history: Sequence[Message],
        user_message: str,
        acting_user_id: str,
        authorization: str,
        organization_id: str | None = None,
        workspace_id: str | None = None,
        caller: ToolCallContext | None = None,
        on_event: AgentEventSink | None = None,
    ) -> TurnResult:
        events = on_event or _NullEventSink()
        ensure_not_archived(agent)
        ensure_owns(conversation, user_id=acting_user_id)
        ensure_active(conversation)

        turn_messages: list[Message] = [
            _make_message(conversation.id, role=MessageRole.USER, content=user_message)
        ]
        # Id tin nhắn người dùng mở lượt vừa duy nhất vừa ổn định suốt lượt —
        # dùng làm nonce để dựng khoá idempotency cho mọi lời gọi tool trong
        # lượt này (xem `_idempotency_key`).
        turn_id = turn_messages[0].id

        tool_specs = await self._tools.list_tools(
            authorization=authorization,
            organization_id=organization_id,
            workspace_id=workspace_id,
            allowed_tool_ids=agent.enabled_tool_ids,
        )

        remaining_tool_calls = agent.max_tool_calls_per_turn

        while True:
            turns = _to_model_turns([*history, *turn_messages])
            # Hết ngân sách thì không mời tool nữa — model không thể trả về
            # tool_use khi không có tool nào trong tay
            offer_tools = tool_specs if remaining_tool_calls > 0 else ()

            response = await self._complete(agent=agent, turns=turns, tools=offer_tools)

            if response.stop_reason is not StopReason.TOOL_USE or not response.tool_calls:
                text = response.text.strip() or _EMPTY_RESPONSE_FALLBACK
                turn_messages.append(
                    _make_message(conversation.id, role=MessageRole.ASSISTANT, content=text)
                )
                at = datetime.now(UTC)
                await self._trace.append_step(
                    kind="final_response", status="succeeded", started_at=at, completed_at=at
                )
                await events.emit(AgentEvent(kind=AgentEventKind.FINAL, text=text))
                break

            turn_messages.append(
                _make_message(
                    conversation.id,
                    role=MessageRole.ASSISTANT,
                    content=response.text,
                    tool_calls=tuple(
                        ToolCallRequest(
                            call_id=call.call_id,
                            tool_id=from_model_name(call.name),
                            input=call.input,
                        )
                        for call in response.tool_calls
                    ),
                )
            )

            for call in response.tool_calls:
                tool_id = from_model_name(call.name)
                await events.emit(
                    AgentEvent(kind=AgentEventKind.TOOL_CALL_STARTED, tool_id=tool_id)
                )

                outcome = await self._execute_tool_call(
                    agent=agent,
                    call=call,
                    turn_id=turn_id,
                    conversation_id=conversation.id,
                    authorization=authorization,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    caller=caller,
                )
                turn_messages.append(outcome.message)
                remaining_tool_calls -= 1

                await events.emit(
                    AgentEvent(
                        kind=AgentEventKind.TOOL_CALL_COMPLETED,
                        tool_id=tool_id,
                        is_error=outcome.is_error,
                        error_category=outcome.error_category,
                        error_details=outcome.error_details,
                    )
                )

        return TurnResult(messages=tuple(turn_messages))

    # ─── Nội bộ ──────────────────────────────────────────────────────────────

    async def _complete(
        self, *, agent: Agent, turns: list[ModelTurn], tools: Sequence[ModelToolSpec]
    ) -> ModelResponse:
        started = datetime.now(UTC)
        try:
            response = await self._models.complete(
                model=agent.model, system=agent.system_prompt, turns=turns, tools=tools
            )
        except AppError as exc:
            await self._trace.append_step(
                kind="model_call",
                status="failed",
                started_at=started,
                completed_at=datetime.now(UTC),
                attributes={"model": agent.model},
                error_category=exc.category.value,
            )
            raise

        completed = datetime.now(UTC)
        await self._trace.append_step(
            kind="model_call",
            status="succeeded",
            started_at=started,
            completed_at=completed,
            attributes={
                "model": response.model or agent.model,
                "stop_reason": str(response.stop_reason),
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        )
        return response

    async def _execute_tool_call(
        self,
        *,
        agent: Agent,
        call: ModelToolCall,
        turn_id: str,
        conversation_id: str,
        authorization: str,
        organization_id: str | None,
        workspace_id: str | None,
        caller: ToolCallContext | None,
    ) -> _ToolCallOutcome:
        """Không bao giờ ném lỗi ra ngoài — một tool hỏng trở thành một kết
        quả tool báo lỗi, để model thấy và tự quyết định làm gì tiếp theo,
        thay vì làm vỡ cả lượt hội thoại."""
        tool_id = from_model_name(call.name)
        started = datetime.now(UTC)

        try:
            if not agent.allows_tool(tool_id):
                raise AuthorizationError(
                    f"Agent {agent.id} không được cấu hình để gọi {tool_id}.",
                    details={"tool_id": tool_id},
                )

            # Đã có lựa chọn chốt sẵn cho conversation này thì dùng lại, không
            # resolve lại — "sticky" cho tới khi người dùng chủ động đổi
            # (ADR-0004). Chưa có thì resolve rồi lưu lại ngay khi thành công,
            # để lần gọi tool kế tiếp trong cùng conversation không phải hỏi
            # lại nữa.
            provider = tool_id.split(".", 1)[0]
            connection_id = await self._selections.get(
                conversation_id=conversation_id, provider=provider
            )
            if connection_id is None:
                connection_id = await self._connections.resolve(
                    tool_id=tool_id,
                    authorization=authorization,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                )
                await self._selections.set(
                    conversation_id=conversation_id,
                    provider=provider,
                    connection_id=connection_id,
                )

            result = await self._executor.invoke(
                name=call.name,
                tool_input=call.input,
                connection_id=connection_id,
                authorization=authorization,
                organization_id=organization_id,
                workspace_id=workspace_id,
                idempotency_key=_idempotency_key(
                    turn_id=turn_id, tool_id=tool_id, tool_input=call.input
                ),
                caller=caller,
            )
        except AppError as exc:
            completed = datetime.now(UTC)
            await self._trace.append_step(
                kind="tool_call",
                status="failed",
                started_at=started,
                completed_at=completed,
                attributes={"tool_id": tool_id},
                error_category=exc.category.value,
            )
            message = _make_message(
                conversation_id,
                role=MessageRole.TOOL,
                content=f"Lỗi: {exc.message}",
                tool_call_id=call.call_id,
            )
            return _ToolCallOutcome(
                message=message,
                is_error=True,
                error_category=exc.category.value,
                error_details=exc.details,
            )

        completed = datetime.now(UTC)
        await self._trace.append_step(
            kind="tool_call",
            status="replayed" if result.replayed else "succeeded",
            started_at=started,
            completed_at=completed,
            attributes={"tool_id": tool_id, "invocation_id": result.invocation_id},
        )

        message = _make_message(
            conversation_id,
            role=MessageRole.TOOL,
            content=json.dumps(dict(result.output), ensure_ascii=False),
            tool_call_id=call.call_id,
        )
        return _ToolCallOutcome(message=message, is_error=False)
