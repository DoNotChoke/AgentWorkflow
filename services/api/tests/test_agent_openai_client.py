"""Kiểm tra `OpenAIModelClient` bằng SDK giả — không gọi mạng thật.

Cùng khuôn `test_agent_anthropic_client.py`: `self._client` được tiêm qua
constructor, lỗi thì dựng đúng exception thật của SDK OpenAI bằng
`httpx.Response` thật.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import openai
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
from app.domains.agent.infrastructure.openai_client import OpenAIModelClient

_REQUEST = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _status_error(cls: type[openai.APIStatusError], status: int, **headers: str) -> Any:
    response = httpx.Response(status, headers=headers, request=_REQUEST)
    return cls("lỗi giả lập", response=response, body=None)


def _tool_call(call_id: str, name: str, arguments_json: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id, function=SimpleNamespace(name=name, arguments=arguments_json)
    )


def _response(
    *,
    content: str | None,
    finish_reason: str,
    tool_calls: tuple[SimpleNamespace, ...] = (),
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    model: str = "gpt-5.4-mini",
) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=tool_calls or None)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return SimpleNamespace(choices=[choice], usage=usage, model=model)


class FakeCompletions:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.last_kwargs: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> Any:
        self.last_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return self._response


class FakeOpenAISDK:
    def __init__(self, completions: FakeCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


def make_client(completions: FakeCompletions) -> OpenAIModelClient:
    return OpenAIModelClient(FakeOpenAISDK(completions))  # type: ignore[arg-type]


# ─── Dịch phản hồi ────────────────────────────────────────────────────────


async def test_tra_ve_text_khi_khong_goi_tool() -> None:
    client = make_client(FakeCompletions(_response(content="Xin chào", finish_reason="stop")))

    result = await client.complete(
        model="gpt-5.4-mini", system="Bạn là trợ lý.", turns=[ModelTurn(role="user", text="Chào")]
    )

    assert result.text == "Xin chào"
    assert result.tool_calls == ()
    assert result.stop_reason is StopReason.END_TURN
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 5
    assert result.model == "gpt-5.4-mini"


async def test_tra_ve_tool_calls() -> None:
    client = make_client(
        FakeCompletions(
            _response(
                content=None,
                finish_reason="tool_calls",
                tool_calls=(_tool_call("call_1", "slack.post_message", '{"channel": "C1"}'),),
            )
        )
    )

    result = await client.complete(model="gpt-5.4-mini", system="", turns=[])

    assert result.stop_reason is StopReason.TOOL_USE
    assert result.tool_calls == (
        ModelToolCall(call_id="call_1", name="slack.post_message", input={"channel": "C1"}),
    )


async def test_khong_co_noi_dung_thi_text_rong() -> None:
    client = make_client(FakeCompletions(_response(content=None, finish_reason="stop")))
    result = await client.complete(model="gpt-5.4-mini", system="", turns=[])
    assert result.text == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("stop", StopReason.END_TURN),
        ("tool_calls", StopReason.TOOL_USE),
        ("length", StopReason.MAX_TOKENS),
        ("content_filter", StopReason.REFUSED),
    ],
)
async def test_moi_finish_reason_duoc_chuan_hoa_dung(raw: str, expected: StopReason) -> None:
    client = make_client(FakeCompletions(_response(content="", finish_reason=raw)))
    result = await client.complete(model="gpt-5.4-mini", system="", turns=[])
    assert result.stop_reason is expected


# ─── Dịch yêu cầu ─────────────────────────────────────────────────────────


async def test_system_la_message_dau_tien() -> None:
    completions = FakeCompletions(_response(content="ok", finish_reason="stop"))
    client = make_client(completions)

    await client.complete(
        model="gpt-5.4-mini",
        system="Bạn là trợ lý Slack.",
        turns=[ModelTurn(role="user", text="Gửi tin nhắn cho C1")],
        max_tokens=2048,
    )

    assert completions.last_kwargs is not None
    assert completions.last_kwargs["max_completion_tokens"] == 2048
    assert "max_tokens" not in completions.last_kwargs
    sent = completions.last_kwargs["messages"]
    assert sent[0] == {"role": "system", "content": "Bạn là trợ lý Slack."}
    assert sent[1] == {"role": "user", "content": "Gửi tin nhắn cho C1"}


async def test_khong_co_tool_thi_khong_gui_tham_so_tools() -> None:
    completions = FakeCompletions(_response(content="ok", finish_reason="stop"))
    client = make_client(completions)

    await client.complete(model="gpt-5.4-mini", system="", turns=[])

    assert "tools" not in completions.last_kwargs  # type: ignore[operator]


async def test_tool_call_cua_assistant_va_ket_qua_cua_tool_dich_dung_vai_tro() -> None:
    completions = FakeCompletions(_response(content="ok", finish_reason="stop"))
    client = make_client(completions)

    await client.complete(
        model="gpt-5.4-mini",
        system="",
        turns=[
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
                description="Gửi tin",
                input_schema={"type": "object"},
                version="1.0",
            )
        ],
    )

    sent = completions.last_kwargs["messages"]  # type: ignore[index]
    assert sent[1]["role"] == "assistant"
    assert sent[1]["tool_calls"][0]["function"]["name"] == "slack.post_message"
    assert sent[2] == {"role": "tool", "tool_call_id": "call_1", "content": "đã gửi"}
    assert completions.last_kwargs["tools"][0]["function"]["name"] == "slack.post_message"  # type: ignore[index]


# ─── Chuẩn hoá lỗi (§37) ────────────────────────────────────────────────────


async def test_rate_limit_thanh_rate_limited_error_kem_retry_after() -> None:
    error = _status_error(openai.RateLimitError, 429, **{"retry-after": "7"})
    client = make_client(FakeCompletions(error=error))

    with pytest.raises(RateLimitedError) as exc_info:
        await client.complete(model="gpt-5.4-mini", system="", turns=[])
    assert exc_info.value.retry_after_seconds == 7.0


async def test_not_found_thanh_validation_error_khong_retryable() -> None:
    error = _status_error(openai.NotFoundError, 404)
    client = make_client(FakeCompletions(error=error))

    with pytest.raises(ValidationError) as exc_info:
        await client.complete(model="model-khong-ton-tai", system="", turns=[])
    assert exc_info.value.retryable is False


async def test_authentication_error() -> None:
    error = _status_error(openai.AuthenticationError, 401)
    client = make_client(FakeCompletions(error=error))
    with pytest.raises(AuthenticationError):
        await client.complete(model="gpt-5.4-mini", system="", turns=[])


async def test_permission_denied_thanh_authorization_error() -> None:
    error = _status_error(openai.PermissionDeniedError, 403)
    client = make_client(FakeCompletions(error=error))
    with pytest.raises(AuthorizationError):
        await client.complete(model="gpt-5.4-mini", system="", turns=[])


async def test_loi_500_thanh_provider_unavailable_retryable() -> None:
    error = _status_error(openai.InternalServerError, 500)
    client = make_client(FakeCompletions(error=error))

    with pytest.raises(ProviderUnavailableError) as exc_info:
        await client.complete(model="gpt-5.4-mini", system="", turns=[])
    assert exc_info.value.retryable is True


async def test_bad_request_thanh_validation_error() -> None:
    error = _status_error(openai.BadRequestError, 400)
    client = make_client(FakeCompletions(error=error))
    with pytest.raises(ValidationError):
        await client.complete(model="gpt-5.4-mini", system="", turns=[])


async def test_loi_ket_noi_thanh_provider_unavailable() -> None:
    error = openai.APIConnectionError(request=_REQUEST)
    client = make_client(FakeCompletions(error=error))
    with pytest.raises(ProviderUnavailableError):
        await client.complete(model="gpt-5.4-mini", system="", turns=[])


async def test_timeout_thanh_platform_timeout_error() -> None:
    error = openai.APITimeoutError(request=_REQUEST)
    client = make_client(FakeCompletions(error=error))
    with pytest.raises(PlatformTimeoutError):
        await client.complete(model="gpt-5.4-mini", system="", turns=[])


# ─── Responses API ───────────────────────────────────────────────────────────


class FakeResponses:
    def __init__(self, response: Any) -> None:
        self._response = response
        self.last_kwargs: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> Any:
        self.last_kwargs = kwargs
        return self._response


def _responses_client(response: Any) -> tuple[OpenAIModelClient, FakeResponses]:
    responses = FakeResponses(response)
    sdk = SimpleNamespace(responses=responses, chat=SimpleNamespace(completions=None))
    return OpenAIModelClient(sdk), responses  # type: ignore[arg-type]


def _output(*items: SimpleNamespace, status: str = "completed", reason: str | None = None) -> Any:
    return SimpleNamespace(
        output=list(items),
        status=status,
        incomplete_details=SimpleNamespace(reason=reason) if reason else None,
        usage=SimpleNamespace(input_tokens=12, output_tokens=7),
        model="gpt-6.1-sol",
    )


def _function_call(call_id: str, name: str, arguments: str) -> SimpleNamespace:
    return SimpleNamespace(type="function_call", call_id=call_id, name=name, arguments=arguments)


async def test_model_chi_goi_tool_qua_responses_thi_di_duong_responses() -> None:
    """`gpt-6.1-sol` từ chối function tool ở Chat Completions khi đang suy luận."""
    client, responses = _responses_client(
        _output(
            SimpleNamespace(type="reasoning"),
            _function_call("call_1", "slack__post_message", '{"text": "xin chào"}'),
        )
    )
    turns = [
        ModelTurn(role="user", text="đăng giúp"),
        ModelTurn(
            role="assistant",
            text="Để tôi đăng.",
            tool_calls=(ModelToolCall("call_0", "slack__list_channels", {}),),
        ),
        ModelTurn(role="user", tool_results=(ModelToolResult("call_0", '{"channels": []}'),)),
    ]
    tool = ModelToolSpec(
        name="slack__post_message", description="Gửi", input_schema={"type": "object"}, version="1"
    )

    response = await client.complete(
        model="gpt-6.1-sol", system="Bạn là trợ lý.", turns=turns, tools=(tool,), max_tokens=500
    )

    assert response.stop_reason is StopReason.TOOL_USE
    assert response.tool_calls == (
        ModelToolCall("call_1", "slack__post_message", {"text": "xin chào"}),
    )
    assert (response.usage.input_tokens, response.usage.output_tokens) == (12, 7)
    sent = responses.last_kwargs
    assert sent is not None
    assert (sent["instructions"], sent["max_output_tokens"]) == ("Bạn là trợ lý.", 500)
    assert sent["input"] == [
        {"role": "user", "content": "đăng giúp"},
        {"role": "assistant", "content": "Để tôi đăng."},
        {
            "type": "function_call",
            "call_id": "call_0",
            "name": "slack__list_channels",
            "arguments": "{}",
        },
        {"type": "function_call_output", "call_id": "call_0", "output": '{"channels": []}'},
    ]
    assert sent["tools"][0]["name"] == "slack__post_message"
    assert sent["tools"][0]["parameters"] == {"type": "object"}


async def test_responses_tra_loi_bang_loi() -> None:
    message = SimpleNamespace(
        type="message", content=[SimpleNamespace(type="output_text", text="Xong rồi.")]
    )
    client, _ = _responses_client(_output(message))

    response = await client.complete(
        model="gpt-6.1-sol", system="", turns=[ModelTurn(role="user", text="xin chào")]
    )

    assert (response.text, response.stop_reason) == ("Xong rồi.", StopReason.END_TURN)


async def test_responses_bi_cat_o_tran_token_thi_khong_doc_json_cut() -> None:
    client, _ = _responses_client(
        _output(
            _function_call("call_1", "tra_loi", '{"ten": "Đăng t'),
            status="incomplete",
            reason="max_output_tokens",
        )
    )

    response = await client.complete(
        model="gpt-6.1-sol", system="", turns=[ModelTurn(role="user", text="…")]
    )

    assert response.stop_reason is StopReason.MAX_TOKENS
    assert response.tool_calls[0].input == {}
