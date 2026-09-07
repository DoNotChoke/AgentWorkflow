"""Model Gateway — điều phối nhiều `ModelClient` theo model được yêu cầu."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.domains.agent.application.ports import (
    ModelClient,
    ModelResponse,
    ModelToolSpec,
    ModelTurn,
)
from app.domains.agent.infrastructure.model_catalog import ModelProvider, resolve_provider


class UnconfiguredProviderError(Exception):
    """Model thuộc một provider có trong danh mục, nhưng chưa được cấu hình."""

    def __init__(self, provider: ModelProvider) -> None:
        self.provider = provider
        super().__init__(
            f"Provider {provider} có trong danh mục model nhưng chưa được cấu hình "
            "ở tiến trình này (thiếu API key?)."
        )


class ModelGateway:
    """Tra `model` trong danh mục, giao lại đúng client của provider đó."""

    def __init__(self, clients: Mapping[ModelProvider, ModelClient]) -> None:
        self._clients = clients

    async def complete(
        self,
        *,
        model: str,
        system: str,
        turns: Sequence[ModelTurn],
        tools: Sequence[ModelToolSpec] = (),
        max_tokens: int = 8192,
    ) -> ModelResponse:
        provider = resolve_provider(model)
        client = self._clients.get(provider)
        if client is None:
            raise UnconfiguredProviderError(provider)

        return await client.complete(
            model=model, system=system, turns=turns, tools=tools, max_tokens=max_tokens
        )
