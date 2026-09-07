"""Hiện thực `ModelClient` bằng Anthropic Messages API."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import anthropic

from app.core.errors import (
    AppError,
    AuthenticationError,
    AuthorizationError,
    PlatformTimeoutError,
    ProviderUnavailableError,
    RateLimitedError,
    ValidationError,
)
from app.domains.agent.application.ports import (
    ModelResponse,
    ModelToolCall,
    ModelToolSpec,
    ModelTurn,
    ModelUsage,
    StopReason,
)

#: `stop_reason` của Anthropic không có trong bảng này (ví dụ `pause_turn`,
#: dành cho lượt dùng server-tool nhiều bước — client này chưa khai server-tool
#: nào) rơi về `END_TURN`: coi như model đã xong phần của nó cho lượt này.
_STOP_REASON: dict[str, StopReason] = {
    "end_turn": StopReason.END_TURN,
    "stop_sequence": StopReason.END_TURN,
    "tool_use": StopReason.TOOL_USE,
    "max_tokens": StopReason.MAX_TOKENS,
    "refusal": StopReason.REFUSED,
}


def _stop_reason(raw: str) -> StopReason:
    return _STOP_REASON.get(raw, StopReason.END_TURN)


def _to_anthropic_messages(turns: Sequence[ModelTurn]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for turn in turns:
        blocks: list[dict[str, Any]] = []
        if turn.text:
            blocks.append({"type": "text", "text": turn.text})
        for call in turn.tool_calls:
            blocks.append(
                {
                    "type": "tool_use",
                    "id": call.call_id,
                    "name": call.name,
                    "input": dict(call.input),
                }
            )
        for result in turn.tool_results:
            block: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": result.call_id,
                "content": result.content,
            }
            if result.is_error:
                block["is_error"] = True
            blocks.append(block)
        messages.append({"role": turn.role, "content": blocks})
    return messages


def _to_anthropic_tools(tools: Sequence[ModelToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": dict(tool.input_schema),
        }
        for tool in tools
    ]


class AnthropicModelClient:
    """Nhận một `anthropic.AsyncAnthropic` đã dựng sẵn — không tự đọc API key."""

    def __init__(self, client: anthropic.AsyncAnthropic) -> None:
        self._client = client

    async def complete(
        self,
        *,
        model: str,
        system: str,
        turns: Sequence[ModelTurn],
        tools: Sequence[ModelToolSpec] = (),
        max_tokens: int = 8192,
    ) -> ModelResponse:
        try:
            response = await self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=_to_anthropic_messages(turns),  # type: ignore[arg-type]
                tools=_to_anthropic_tools(tools),  # type: ignore[arg-type]
            )
        except anthropic.APIError as exc:
            raise _map_error(exc) from exc

        text = "".join(block.text for block in response.content if block.type == "text")
        tool_calls = tuple(
            ModelToolCall(call_id=block.id, name=block.name, input=dict(block.input))
            for block in response.content
            if block.type == "tool_use"
        )

        return ModelResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=_stop_reason(response.stop_reason or "end_turn"),
            usage=ModelUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            ),
            model=response.model,
        )


def _retry_after_seconds(exc: anthropic.RateLimitError) -> float | None:
    raw = exc.response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _map_error(exc: anthropic.APIError) -> AppError:
    """Chuẩn hoá exception của SDK Anthropic thành `AppError`.

    Thứ tự bắt lỗi cụ thể trước tổng quát sau — đúng khuyến nghị của skill
    `claude-api`: một `except APIStatusError` duy nhất sẽ xoá mất phân biệt
    giữa lỗi đáng thử lại (429, ≥500) và lỗi không đáng (400, 404).
    """
    if isinstance(exc, anthropic.NotFoundError):
        return ValidationError(f"Model không tồn tại hoặc đã ngừng hỗ trợ: {exc.message}")

    if isinstance(exc, anthropic.RateLimitError):
        return RateLimitedError(
            f"Anthropic đang giới hạn tốc độ gọi: {exc.message}",
            retry_after_seconds=_retry_after_seconds(exc),
        )

    if isinstance(exc, anthropic.AuthenticationError):
        return AuthenticationError(f"API key Anthropic không hợp lệ: {exc.message}")

    if isinstance(exc, anthropic.PermissionDeniedError):
        return AuthorizationError(f"API key Anthropic không có quyền dùng model này: {exc.message}")

    if isinstance(exc, anthropic.APITimeoutError):
        return PlatformTimeoutError(f"Anthropic không phản hồi kịp: {exc}")

    if isinstance(exc, anthropic.APIConnectionError):
        return ProviderUnavailableError(f"Không kết nối được tới Anthropic: {exc}")

    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code >= 500 or getattr(exc, "type", "") == "overloaded_error":
            return ProviderUnavailableError(f"Anthropic đang gặp sự cố: {exc.message}")
        return ValidationError(f"Yêu cầu gửi tới Anthropic không hợp lệ: {exc.message}")

    return ProviderUnavailableError(f"Lỗi không xác định khi gọi Anthropic: {exc}")
