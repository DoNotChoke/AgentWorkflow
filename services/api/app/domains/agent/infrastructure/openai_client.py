"""Hiện thực `ModelClient` bằng OpenAI: Chat Completions, và Responses cho những model
chỉ gọi được tool qua đó (`RESPONSES_API_MODELS`)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import openai

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
from app.domains.agent.infrastructure.model_catalog import RESPONSES_API_MODELS

_STOP_REASON: dict[str, StopReason] = {
    "stop": StopReason.END_TURN,
    "tool_calls": StopReason.TOOL_USE,
    "length": StopReason.MAX_TOKENS,
    "content_filter": StopReason.REFUSED,
}


def _stop_reason(raw: str | None) -> StopReason:
    return _STOP_REASON.get(raw or "stop", StopReason.END_TURN)


def _to_openai_messages(system: str, turns: Sequence[ModelTurn]) -> list[dict[str, Any]]:
    """OpenAI không có tham số `system` riêng — nó là message đầu tiên."""
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]

    for turn in turns:
        if turn.role == "assistant":
            message: dict[str, Any] = {"role": "assistant", "content": turn.text or None}
            if turn.tool_calls:
                message["tool_calls"] = [
                    {
                        "id": call.call_id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(dict(call.input)),
                        },
                    }
                    for call in turn.tool_calls
                ]
            messages.append(message)
            continue

        if turn.text:
            messages.append({"role": "user", "content": turn.text})
        # Kết quả tool là message vai trò `tool` riêng của từng lời gọi —
        # khác Anthropic, không gộp chung vào một block `user`.
        for result in turn.tool_results:
            messages.append(
                {"role": "tool", "tool_call_id": result.call_id, "content": result.content}
            )

    return messages


def _to_openai_tools(tools: Sequence[ModelToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": dict(tool.input_schema),
            },
        }
        for tool in tools
    ]


class OpenAIModelClient:
    """Nhận một `openai.AsyncOpenAI` đã dựng sẵn — cùng khuôn `AnthropicModelClient`."""

    def __init__(self, client: openai.AsyncOpenAI) -> None:
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
        if model in RESPONSES_API_MODELS:
            return await self._complete_with_responses(
                model=model, system=system, turns=turns, tools=tools, max_tokens=max_tokens
            )

        request: dict[str, Any] = {
            "model": model,
            "max_completion_tokens": max_tokens,
            "messages": _to_openai_messages(system, turns),
        }
        tool_specs = _to_openai_tools(tools)
        if tool_specs:
            request["tools"] = tool_specs

        try:
            response = await self._client.chat.completions.create(**request)
        except openai.APIError as exc:
            raise _map_error(exc) from exc

        choice = response.choices[0]
        message = choice.message

        tool_calls = tuple(
            ModelToolCall(
                call_id=call.id,
                name=call.function.name,
                input=json.loads(call.function.arguments or "{}"),
            )
            for call in (message.tool_calls or ())
        )

        usage = response.usage
        return ModelResponse(
            text=message.content or "",
            tool_calls=tool_calls,
            stop_reason=_stop_reason(choice.finish_reason),
            usage=ModelUsage(
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
            ),
            model=response.model,
        )

    async def _complete_with_responses(
        self,
        *,
        model: str,
        system: str,
        turns: Sequence[ModelTurn],
        tools: Sequence[ModelToolSpec],
        max_tokens: int,
    ) -> ModelResponse:
        request: dict[str, Any] = {
            "model": model,
            "instructions": system,
            "input": _to_responses_input(turns),
            "max_output_tokens": max_tokens,
        }
        tool_specs = _to_responses_tools(tools)
        if tool_specs:
            request["tools"] = tool_specs

        try:
            response = await self._client.responses.create(**request)
        except openai.APIError as exc:
            raise _map_error(exc) from exc

        text = "".join(
            part.text
            for item in response.output
            if item.type == "message"
            for part in item.content
            if part.type == "output_text"
        )
        tool_calls = tuple(
            ModelToolCall(call_id=item.call_id, name=item.name, input=_arguments(item.arguments))
            for item in response.output
            if item.type == "function_call"
        )

        details = getattr(response, "incomplete_details", None)
        reason = getattr(details, "reason", None)
        if response.status == "incomplete" and reason == "max_output_tokens":
            stop_reason = StopReason.MAX_TOKENS
        elif response.status == "incomplete" and reason == "content_filter":
            stop_reason = StopReason.REFUSED
        elif tool_calls:
            stop_reason = StopReason.TOOL_USE
        else:
            stop_reason = StopReason.END_TURN

        usage = response.usage
        return ModelResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=ModelUsage(
                input_tokens=usage.input_tokens if usage else 0,
                output_tokens=usage.output_tokens if usage else 0,
            ),
            model=response.model,
        )


def _to_responses_input(turns: Sequence[ModelTurn]) -> list[dict[str, Any]]:
    """Responses API không lồng lời gọi tool vào message: mỗi lời gọi và mỗi kết quả là
    một phần tử riêng của `input`."""
    items: list[dict[str, Any]] = []
    for turn in turns:
        if turn.text:
            items.append({"role": turn.role, "content": turn.text})
        for call in turn.tool_calls:
            items.append(
                {
                    "type": "function_call",
                    "call_id": call.call_id,
                    "name": call.name,
                    "arguments": json.dumps(dict(call.input)),
                }
            )
        for result in turn.tool_results:
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": result.call_id,
                    "output": result.content,
                }
            )
    return items


def _to_responses_tools(tools: Sequence[ModelToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": dict(tool.input_schema),
            "strict": False,
        }
        for tool in tools
    ]


def _arguments(raw: str | None) -> dict[str, Any]:
    """Câu trả lời bị cắt giữa chừng để lại một JSON cụt; `stop_reason` nói thay nó."""
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _retry_after_seconds(exc: openai.RateLimitError) -> float | None:
    raw = exc.response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _map_error(exc: openai.APIError) -> AppError:
    """Chuẩn hoá exception của SDK OpenAI thành `AppError`.

    Cùng thứ tự cụ thể-trước-tổng-quát như `anthropic_client._map_error`.
    """
    if isinstance(exc, openai.NotFoundError):
        return ValidationError(f"Model không tồn tại hoặc đã ngừng hỗ trợ: {exc.message}")

    if isinstance(exc, openai.RateLimitError):
        return RateLimitedError(
            f"OpenAI đang giới hạn tốc độ gọi: {exc.message}",
            retry_after_seconds=_retry_after_seconds(exc),
        )

    if isinstance(exc, openai.AuthenticationError):
        return AuthenticationError(f"API key OpenAI không hợp lệ: {exc.message}")

    if isinstance(exc, openai.PermissionDeniedError):
        return AuthorizationError(f"API key OpenAI không có quyền dùng model này: {exc.message}")

    if isinstance(exc, openai.APITimeoutError):
        return PlatformTimeoutError(f"OpenAI không phản hồi kịp: {exc}")

    if isinstance(exc, openai.APIConnectionError):
        return ProviderUnavailableError(f"Không kết nối được tới OpenAI: {exc}")

    if isinstance(exc, openai.APIStatusError):
        if exc.status_code >= 500:
            return ProviderUnavailableError(f"OpenAI đang gặp sự cố: {exc.message}")
        return ValidationError(f"Yêu cầu gửi tới OpenAI không hợp lệ: {exc.message}")

    return ProviderUnavailableError(f"Lỗi không xác định khi gọi OpenAI: {exc}")
