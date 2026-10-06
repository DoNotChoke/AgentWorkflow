"""Dựng Model Gateway từ cấu hình. Dùng ở Agent Runtime và ở worker."""

from __future__ import annotations

import anthropic
import openai

from app.core.config import Settings
from app.domains.agent.application.ports import ModelClient
from app.domains.agent.infrastructure.anthropic_client import AnthropicModelClient
from app.domains.agent.infrastructure.model_catalog import ModelProvider
from app.domains.agent.infrastructure.model_gateway import ModelGateway
from app.domains.agent.infrastructure.openai_client import OpenAIModelClient


def build_model_gateway(settings: Settings) -> ModelClient:
    """Chỉ ghi nhận provider nào có API key — thiếu key thì `ModelGateway` báo
    `UnconfiguredProviderError` rõ ràng thay vì một SDK lỗi khó hiểu lúc gọi."""
    clients: dict[ModelProvider, ModelClient] = {}

    anthropic_key = settings.anthropic_api_key.get_secret_value()
    if anthropic_key:
        clients[ModelProvider.ANTHROPIC] = AnthropicModelClient(
            anthropic.AsyncAnthropic(api_key=anthropic_key)
        )

    openai_key = settings.openai_api_key.get_secret_value()
    if openai_key:
        clients[ModelProvider.OPENAI] = OpenAIModelClient(openai.AsyncOpenAI(api_key=openai_key))

    return ModelGateway(clients)
