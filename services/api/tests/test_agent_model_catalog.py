"""Kiểm tra danh mục model → provider."""

from __future__ import annotations

import pytest

from app.domains.agent.application.ports import UnknownModelError
from app.domains.agent.infrastructure.model_catalog import (
    MODEL_CATALOG,
    ModelProvider,
    resolve_provider,
)


@pytest.mark.parametrize(
    "model", ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5", "claude-fable-5-1"]
)
def test_model_anthropic_duoc_nhan_dien(model: str) -> None:
    assert resolve_provider(model) is ModelProvider.ANTHROPIC


@pytest.mark.parametrize("model", ["gpt-5.4", "gpt-5.4-mini"])
def test_model_openai_duoc_nhan_dien(model: str) -> None:
    assert resolve_provider(model) is ModelProvider.OPENAI


def test_model_khong_ton_tai_bao_loi_kem_danh_sach_da_biet() -> None:
    with pytest.raises(UnknownModelError) as exc_info:
        resolve_provider("model-khong-co-that")

    assert exc_info.value.model == "model-khong-co-that"
    assert set(exc_info.value.known_models) == set(MODEL_CATALOG)
    # Thông điệp phải liệt kê được model đã biết — không chỉ nói "sai".
    assert "claude-sonnet-5" in str(exc_info.value)
