"""Client HTTP chọn `connection_id` cho một tool call — hiện thực `ConnectionResolver`.

Không tự chọn khi mơ hồ (ADR-0004): 0 connection khả dụng hay ≥2 connection
khả dụng đều là lỗi có cấu trúc, không đoán. Người gọi (`AgentRuntime`,
`service.py`) chỉ gọi `resolve()` khi conversation chưa có lựa chọn nào lưu
sẵn cho `(conversation_id, provider)` — việc đọc/ghi lựa chọn đó là của
`ConnectionSelectionStore`, một Protocol khác, không phải của module này.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import ConnectionChoiceRequiredError, MissingConnectionError
from app.domains.agent.infrastructure.control_plane_http import request_json


def _provider_of(tool_id: str) -> str:
    """`tool_id` của Integration luôn có dạng `provider.action` (§6.2)."""
    return tool_id.split(".", 1)[0]


class ConnectionResolverClient:
    """Hiện thực `ConnectionResolver` bằng HTTP tới Control Plane."""

    def __init__(self, base_url: str, *, timeout: float = 15.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def resolve(
        self,
        *,
        tool_id: str,
        authorization: str,
        organization_id: str | None,
        workspace_id: str | None,
    ) -> str:
        provider = _provider_of(tool_id)

        connections: list[dict[str, Any]] = await request_json(
            self._base_url,
            "GET",
            "/v1/connections",
            authorization=authorization,
            organization_id=organization_id,
            workspace_id=workspace_id,
            timeout_seconds=self._timeout,
        )

        candidates = [
            c for c in connections if c["provider"] == provider and c["status"] == "active"
        ]

        if not candidates:
            raise MissingConnectionError(
                f"Chưa có kết nối {provider} nào khả dụng. Hãy vào Connections để "
                "kết nối trước khi dùng tool này.",
                details={"provider": provider},
            )

        if len(candidates) > 1:
            raise ConnectionChoiceRequiredError(
                f"Có {len(candidates)} kết nối {provider} khả dụng — cần chọn một "
                "trước khi dùng tool này.",
                details={
                    "provider": provider,
                    "candidates": [
                        {
                            "connection_id": c["id"],
                            "display_name": c["display_name"],
                            "sharing": c["sharing"],
                            "external_account_label": c.get("external_account_label"),
                        }
                        for c in candidates
                    ],
                },
            )

        return str(candidates[0]["id"])
