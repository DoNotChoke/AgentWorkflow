"""Đổi tên hai chiều giữa `tool_id` của Integration và tên tool hợp lệ cho model."""

from __future__ import annotations

_SEPARATOR = "__"


def to_model_name(tool_id: str) -> str:
    return tool_id.replace(".", _SEPARATOR, 1)


def from_model_name(name: str) -> str:
    return name.replace(_SEPARATOR, ".", 1)
