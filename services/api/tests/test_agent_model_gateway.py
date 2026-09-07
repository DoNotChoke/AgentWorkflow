"""Kiểm tra `ModelGateway` — điều phối theo model, không gọi SDK thật."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.domains.agent.application.ports import (
    ModelResponse,
    ModelToolSpec,
    ModelTurn,
    ModelUsage,
    StopReason,
    UnknownModelError,
)
from app.domains.agent.infrastructure.model_catalog import ModelProvider
from app.domains.agent.infrastructure.model_gateway import (
    ModelGateway,
    UnconfiguredProviderError,
)


class FakeModelClient:
    """Thoả `ModelClient` — ghi lại lời gọi cuối để test đối chiếu."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.last_model: str | None = None

    async def complete(
        self,
        *,
        model: str,
        system: str,
        turns: Sequence[ModelTurn],
        tools: Sequence[ModelToolSpec] = (),
        max_tokens: int = 8192,
    ) -> ModelResponse:
        self.last_model = model
        return ModelResponse(
            text=f"trả lời bởi {self.label}",
            tool_calls=(),
            stop_reason=StopReason.END_TURN,
            usage=ModelUsage(input_tokens=1, output_tokens=1),
            model=model,
        )


async def test_dieu_phoi_dung_client_theo_provider_cua_model() -> None:
    anthropic_client = FakeModelClient("anthropic")
    openai_client = FakeModelClient("openai")
    gateway = ModelGateway(
        {ModelProvider.ANTHROPIC: anthropic_client, ModelProvider.OPENAI: openai_client}
    )

    result = await gateway.complete(model="claude-sonnet-5", system="", turns=[])

    assert result.text == "trả lời bởi anthropic"
    assert anthropic_client.last_model == "claude-sonnet-5"
    assert openai_client.last_model is None


async def test_dieu_phoi_sang_openai() -> None:
    anthropic_client = FakeModelClient("anthropic")
    openai_client = FakeModelClient("openai")
    gateway = ModelGateway(
        {ModelProvider.ANTHROPIC: anthropic_client, ModelProvider.OPENAI: openai_client}
    )

    result = await gateway.complete(model="gpt-5.4-mini", system="", turns=[])

    assert result.text == "trả lời bởi openai"
    assert openai_client.last_model == "gpt-5.4-mini"


async def test_model_khong_co_trong_danh_muc_bao_unknown_model_error() -> None:
    gateway = ModelGateway({ModelProvider.ANTHROPIC: FakeModelClient("anthropic")})

    with pytest.raises(UnknownModelError):
        await gateway.complete(model="model-khong-co-that", system="", turns=[])


async def test_provider_hop_le_nhung_chua_cau_hinh_bao_loi_ro_rang() -> None:
    """Model có trong danh mục (thuộc OpenAI), nhưng tiến trình chỉ cấu hình
    client Anthropic — ví dụ chưa đặt `AW_OPENAI_API_KEY`."""
    gateway = ModelGateway({ModelProvider.ANTHROPIC: FakeModelClient("anthropic")})

    with pytest.raises(UnconfiguredProviderError) as exc_info:
        await gateway.complete(model="gpt-5.4-mini", system="", turns=[])

    assert exc_info.value.provider is ModelProvider.OPENAI
