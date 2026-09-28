"""Interface mà tầng `infrastructure` hoặc `entrypoints` phải hiện thực hoá."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ToolContract:
    """Phần của một tool mà validator workflow cần biết."""

    tool_id: str
    version: str
    provider: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    mutates: bool
    #: Tool đòi phê duyệt, hoặc xoá dữ liệu. Không có node `human_approval` ở
    #: v1 nên workflow gọi nó tự động sẽ không bao giờ đi qua được policy.
    needs_approval: bool
    enabled: bool = True


class ToolCatalogReader(Protocol):
    """Tra catalog tool. Đồng bộ: catalog là dữ liệu đã biên dịch vào tiến trình."""

    def find(self, tool_id: str, version: str) -> ToolContract | None:
        """Đúng bản `major.minor`, hoặc `None`."""

    def versions(self, tool_id: str) -> tuple[str, ...]:
        """Mọi bản đang có của `tool_id` — để thông báo lỗi nói được nên dùng bản nào."""


@dataclass(frozen=True, slots=True)
class BoundConnection:
    """Kết nối mà một workflow đang bind, nhìn từ domain `workflow`.

    Không mang token và không mang scope: publish chỉ cần biết nó có tồn tại,
    có dùng được, có phải tài khoản chung hay không.
    """

    connection_id: str
    provider: str
    #: `False` là tài khoản riêng của một người — Q7 từ chối bind nó.
    shared_with_workspace: bool
    #: `active`, `expired`, `revoked`… Đưa nguyên vào thông báo lỗi.
    status: str
    is_usable: bool


class ConnectionReader(Protocol):
    """Đọc metadata của một connection. Hiện thực ở `entrypoints/wiring.py`"""

    async def describe(
        self, *, tenant_id: str, workspace_id: str, connection_id: str
    ) -> BoundConnection | None:
        """`None` khi không tồn tại, hoặc thuộc tenant/workspace khác."""
