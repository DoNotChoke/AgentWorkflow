"""Kiểm tra `AnthropicModelClient` bằng SDK giả — không gọi mạng thật.

`self._client` được tiêm qua constructor (không tự đọc API key), nên test chỉ
cần một stub trả đúng hình dạng mà `anthropic.AsyncAnthropic().messages.create`
trả về. Lỗi thì dựng đúng exception thật của SDK (`anthropic.RateLimitError`,
...) bằng `httpx.Response` thật — không tự bịa hình dạng exception, để một
ngày SDK đổi field thì test này cũng đỏ theo.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest

from app.core.errors import (
    AuthenticationError,
    AuthorizationError,
    PlatformTimeoutError,
    ProviderUnavailableError,
    RateLimitedError,
    ValidationError,
)
from app.domains.agent.application.ports import (
    ModelToolCall,
    ModelToolResult,
    ModelToolSpec,
    ModelTurn,
    StopReason,
)
from app.domains.agent.infrastructure.anthropic_client import AnthropicModelClient

_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _status_error(cls: type[anthropic.APIStatusError], status: int, **headers: str) -> Any:
    response = httpx.Response(status, headers=headers, request=_REQUEST)
    return cls("lỗi giả lập", response=response, body=None)


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(call_id: str, name: str, tool_input: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=call_id, name=name, input=tool_input)


def _response(
    *,
    content: list[SimpleNamespace],
    stop_reason: str,
    input_tokens: int = 10,
    output_tokens: int = 5,
    model: str = "claude-sonnet-5",
) -> SimpleNamespace:
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
        model=model,
    )


class FakeMessages:
    """Thay `AsyncAnthropic().messages` — ghi lại kwargs để test đối chiếu."""

    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.last_kwargs: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> Any:
        self.last_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return self._response


class FakeAnthropicSDK:
    def __init__(self, messages: FakeMessages) -> None:
        self.messages = messages


def make_client(messages: FakeMessages) -> AnthropicModelClient:
    return AnthropicModelClient(FakeAnthropicSDK(messages))  # type: ignore[arg-type]


# ─── Dịch phản hồi ─────────────────────────────────────────────────────────


async def test_tra_ve_text_khi_khong_goi_tool() -> None:
    client = make_client(
        FakeMessages(_response(content=[_text_block("Xin chào")], stop_reason="end_turn"))
    )

    result = await client.complete(
        model="claude-sonnet-5",
        system="Bạn là trợ lý.",
        turns=[ModelTurn(role="user", text="Chào")],
    )

    assert result.text == "Xin chào"
    assert result.tool_calls == ()
    assert result.stop_reason is StopReason.END_TURN
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 5
    assert result.model == "claude-sonnet-5"


async def test_tra_ve_tool_calls_khi_model_quyet_dinh_goi_tool() -> None:
    client = make_client(
        FakeMessages(
            _response(
                content=[_tool_use_block("call_1", "slack.post_message", {"channel": "C1"})],
                stop_reason="tool_use",
            )
        )
    )

    result = await client.complete(
        model="claude-sonnet-5", system="", turns=[ModelTurn(role="user", text="Gửi tin")]
    )

    assert result.stop_reason is StopReason.TOOL_USE
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0] == ModelToolCall(
        call_id="call_1", name="slack.post_message", input={"channel": "C1"}
    )


async def test_ca_text_va_tool_call_cung_mot_luot() -> None:
    client = make_client(
        FakeMessages(
            _response(
                content=[
                    _text_block("Để tôi gửi tin nhắn này."),
                    _tool_use_block("call_1", "slack.post_message", {"channel": "C1"}),
                ],
                stop_reason="tool_use",
            )
        )
    )

    result = await client.complete(model="claude-sonnet-5", system="", turns=[])

    assert result.text == "Để tôi gửi tin nhắn này."
    assert len(result.tool_calls) == 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("end_turn", StopReason.END_TURN),
        ("stop_sequence", StopReason.END_TURN),
        ("tool_use", StopReason.TOOL_USE),
        ("max_tokens", StopReason.MAX_TOKENS),
        ("refusal", StopReason.REFUSED),
        ("pause_turn", StopReason.END_TURN),  # chưa dùng server-tool, rơi về END_TURN
    ],
)
async def test_moi_stop_reason_duoc_chuan_hoa_dung(raw: str, expected: StopReason) -> None:
    client = make_client(FakeMessages(_response(content=[], stop_reason=raw)))
    result = await client.complete(model="claude-sonnet-5", system="", turns=[])
    assert result.stop_reason is expected


# ─── Dịch yêu cầu ─────────────────────────────────────────────────────────


async def test_gui_dung_system_va_lich_su() -> None:
    messages = FakeMessages(_response(content=[_text_block("ok")], stop_reason="end_turn"))
    client = make_client(messages)

    await client.complete(
        model="claude-sonnet-5",
        system="Bạn là trợ lý Slack.",
        turns=[
            ModelTurn(role="user", text="Gửi tin nhắn cho C1"),
            ModelTurn(
                role="assistant",
                tool_calls=(
                    ModelToolCall(
                        call_id="call_1", name="slack.post_message", input={"channel": "C1"}
                    ),
                ),
            ),
            ModelTurn(
                role="user", tool_results=(ModelToolResult(call_id="call_1", content="đã gửi"),)
            ),
        ],
        tools=[
            ModelToolSpec(
                name="slack.post_message",
                description="Gửi tin nhắn",
                input_schema={"type": "object"},
            )
        ],
        max_tokens=2048,
    )

    assert messages.last_kwargs is not None
    assert messages.last_kwargs["model"] == "claude-sonnet-5"
    assert messages.last_kwargs["system"] == "Bạn là trợ lý Slack."
    assert messages.last_kwargs["max_tokens"] == 2048
    assert messages.last_kwargs["tools"] == [
        {
            "name": "slack.post_message",
            "description": "Gửi tin nhắn",
            "input_schema": {"type": "object"},
        }
    ]
    sent = messages.last_kwargs["messages"]
    assert sent[0] == {"role": "user", "content": [{"type": "text", "text": "Gửi tin nhắn cho C1"}]}
    assert sent[1]["content"][0] == {
        "type": "tool_use",
        "id": "call_1",
        "name": "slack.post_message",
        "input": {"channel": "C1"},
    }


async def test_tool_result_loi_mang_is_error() -> None:
    messages = FakeMessages(_response(content=[_text_block("ok")], stop_reason="end_turn"))
    client = make_client(messages)

    await client.complete(
        model="claude-sonnet-5",
        system="",
        turns=[
            ModelTurn(
                role="user",
                tool_results=(ModelToolResult(call_id="call_1", content="lỗi rồi", is_error=True),),
            )
        ],
    )

    sent = messages.last_kwargs["messages"]  # type: ignore[index]
    assert sent[0]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "call_1",
        "content": "lỗi rồi",
        "is_error": True,
    }


# ─── Chuẩn hoá lỗi (§37) ────────────────────────────────────────────────────


async def test_rate_limit_thanh_rate_limited_error_kem_retry_after() -> None:
    error = _status_error(anthropic.RateLimitError, 429, **{"retry-after": "12"})
    client = make_client(FakeMessages(error=error))

    with pytest.raises(RateLimitedError) as exc_info:
        await client.complete(model="claude-sonnet-5", system="", turns=[])

    assert exc_info.value.retry_after_seconds == 12.0


async def test_khong_co_retry_after_thi_van_qua_khong_loi() -> None:
    error = _status_error(anthropic.RateLimitError, 429)
    client = make_client(FakeMessages(error=error))

    with pytest.raises(RateLimitedError) as exc_info:
        await client.complete(model="claude-sonnet-5", system="", turns=[])

    assert exc_info.value.retry_after_seconds is None


async def test_not_found_thanh_validation_error_khong_retryable() -> None:
    error = _status_error(anthropic.NotFoundError, 404)
    client = make_client(FakeMessages(error=error))

    with pytest.raises(ValidationError) as exc_info:
        await client.complete(model="model-khong-ton-tai", system="", turns=[])
    assert exc_info.value.retryable is False


async def test_authentication_error() -> None:
    error = _status_error(anthropic.AuthenticationError, 401)
    client = make_client(FakeMessages(error=error))
    with pytest.raises(AuthenticationError):
        await client.complete(model="claude-sonnet-5", system="", turns=[])


async def test_permission_denied_thanh_authorization_error() -> None:
    error = _status_error(anthropic.PermissionDeniedError, 403)
    client = make_client(FakeMessages(error=error))
    with pytest.raises(AuthorizationError):
        await client.complete(model="claude-sonnet-5", system="", turns=[])


async def test_loi_500_thanh_provider_unavailable_retryable() -> None:
    error = _status_error(anthropic.InternalServerError, 500)
    client = make_client(FakeMessages(error=error))

    with pytest.raises(ProviderUnavailableError) as exc_info:
        await client.complete(model="claude-sonnet-5", system="", turns=[])
    assert exc_info.value.retryable is True


async def test_bad_request_thanh_validation_error() -> None:
    error = _status_error(anthropic.BadRequestError, 400)
    client = make_client(FakeMessages(error=error))
    with pytest.raises(ValidationError):
        await client.complete(model="claude-sonnet-5", system="", turns=[])


async def test_loi_ket_noi_thanh_provider_unavailable() -> None:
    error = anthropic.APIConnectionError(request=_REQUEST)
    client = make_client(FakeMessages(error=error))
    with pytest.raises(ProviderUnavailableError):
        await client.complete(model="claude-sonnet-5", system="", turns=[])


async def test_timeout_thanh_platform_timeout_error() -> None:
    error = anthropic.APITimeoutError(request=_REQUEST)
    client = make_client(FakeMessages(error=error))
    with pytest.raises(PlatformTimeoutError):
        await client.complete(model="claude-sonnet-5", system="", turns=[])
