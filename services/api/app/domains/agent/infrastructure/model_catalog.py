"""Danh mục model → provider"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from app.domains.agent.application.ports import UnknownModelError


class ModelProvider(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


#: model_id -> provider.
MODEL_CATALOG: Final[dict[str, ModelProvider]] = {
    # ─── Anthropic ───────────────────────────────────────────────────────────
    "claude-opus-5": ModelProvider.ANTHROPIC,
    "claude-sonnet-5": ModelProvider.ANTHROPIC,
    "claude-haiku-4-5": ModelProvider.ANTHROPIC,
    "claude-fable-5-1": ModelProvider.ANTHROPIC,
    "claude-opus-4-8": ModelProvider.ANTHROPIC,
    "claude-sonnet-4-6": ModelProvider.ANTHROPIC,
    # ─── OpenAI ──────────────────────────────────────────────────────────────
    "gpt-5.4": ModelProvider.OPENAI,
    "gpt-5.4-mini": ModelProvider.OPENAI,
    "gpt-5.6-sol": ModelProvider.OPENAI,
    "gpt-5.6-terra": ModelProvider.OPENAI,
}


def resolve_provider(model: str) -> ModelProvider:
    """Provider sở hữu `model`. Ném `UnknownModelError` nếu không có trong danh mục."""
    provider = MODEL_CATALOG.get(model)
    if provider is None:
        raise UnknownModelError(model, known_models=tuple(sorted(MODEL_CATALOG)))
    return provider
