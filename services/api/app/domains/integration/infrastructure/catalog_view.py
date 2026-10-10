"""Catalog tool nhìn từ bên ngoài domain `integration`, dưới dạng dữ liệu thuần."""

from __future__ import annotations

from typing import Any

from app.domains.integration.domain.generated import catalog_v1
from app.domains.integration.domain.tools import SideEffect


def tool_rows() -> tuple[dict[str, Any], ...]:
    """Mỗi tool một hàng, đủ để một domain khác kiểm một workflow."""
    return tuple(
        {
            "tool_id": tool.tool_id,
            "version": tool.version,
            "provider": tool.provider,
            "input_schema": tool.input_schema.json_schema,
            "output_schema": tool.output_schema.json_schema,
            "mutates": tool.side_effect.mutates,
            # Hai lý do khác nhau, cùng một hệ quả với workflow v1: không có
            # node phê duyệt nên nó sẽ luôn bị policy chặn lúc chạy.
            "needs_approval": tool.approval.required or tool.side_effect is SideEffect.DESTRUCTIVE,
            "enabled": tool.enabled,
            "timeout_seconds": tool.timeout.total_seconds,
        }
        for tool in catalog_v1.TOOLS
    )
