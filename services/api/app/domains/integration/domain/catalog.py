"""Tool Registry — tra cứu tool và connector."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from app.core.errors import ValidationError
from app.domains.integration.domain.connectors import ConnectorSpec
from app.domains.integration.domain.tools import ToolSpec, parse_version


class ToolRegistry:
    """Danh mục tool và connector đã nạp, tra cứu được theo id và version."""

    __slots__ = ("_by_major", "_connectors", "_tools")

    def __init__(
        self,
        connectors: Sequence[ConnectorSpec],
        tools: Sequence[ToolSpec],
    ) -> None:
        self._connectors: dict[str, ConnectorSpec] = {}
        for connector in connectors:
            if connector.connector_id in self._connectors:
                raise ValidationError(f"Connector {connector.connector_id!r} khai báo hai lần.")
            self._connectors[connector.connector_id] = connector

        # Khoá đầy đủ `tool_id@version` — dùng khi người gọi chỉ đúng một bản.
        self._tools: dict[str, ToolSpec] = {}
        # Khoá `(tool_id, major)` → bản minor mới nhất của major đó.
        self._by_major: dict[tuple[str, int], ToolSpec] = {}

        for tool in tools:
            if tool.qualified in self._tools:
                raise ValidationError(f"Tool {tool.qualified!r} khai báo hai lần.")
            self._tools[tool.qualified] = tool

            major, minor = parse_version(tool.version)
            current = self._by_major.get((tool.tool_id, major))
            if current is None or parse_version(current.version)[1] < minor:
                self._by_major[(tool.tool_id, major)] = tool

        self._check_connectors_agree_with_tools(tools)

    def _check_connectors_agree_with_tools(self, tools: Iterable[ToolSpec]) -> None:
        """Đối chiếu hai chiều giữa `connector.tool_ids` và các tool đã nạp."""
        declared: set[str] = set()
        for connector in self._connectors.values():
            for tool_id in connector.tool_ids:
                if tool_id in declared:
                    raise ValidationError(f"Tool {tool_id!r} thuộc về hai connector.")
                declared.add(tool_id)

        present = {tool.tool_id for tool in tools}

        if missing := sorted(declared - present):
            raise ValidationError(f"Connector khai tool không tồn tại: {', '.join(missing)}.")
        if orphaned := sorted(present - declared):
            raise ValidationError(
                f"Tool không thuộc connector nào: {', '.join(orphaned)}. "
                "Thêm tool_id vào `tools` của connector tương ứng."
            )

        for tool in tools:
            owner = self._connectors.get(tool.provider)
            if owner is None:
                raise ValidationError(
                    f"Tool {tool.tool_id!r} khai provider {tool.provider!r} chưa có connector."
                )
            if tool.tool_id not in owner.tool_ids:
                raise ValidationError(
                    f"Tool {tool.tool_id!r} khai provider {tool.provider!r} "
                    f"nhưng connector đó không nhận nó."
                )

    # ─── Tra cứu ─────────────────────────────────────────────────────────────

    def get_tool(self, tool_id: str, version: str | None = None) -> ToolSpec:
        """Phân giải một tool.

        Ba dạng `version`:

        - ``None``    → minor mới nhất của **major cao nhất**. Dành cho người
          gọi tương tác, không dành cho Workflow đã publish.
        - ``"1"``     → minor mới nhất của major 1. Đây là dạng mà một
          Workflow nên ghim: nhận được sửa lỗi tương thích ngược, không nhận
          được thay đổi phá vỡ (§41).
        - ``"1.2"``   → đúng bản đó, không hơn.
        """
        if version is None:
            return self._latest_major(tool_id)
        if "." not in version:
            return self._latest_minor(tool_id, version)

        found = self._tools.get(f"{tool_id}@{version}")
        if found is None:
            raise ValidationError(
                f"Không có tool {tool_id!r} bản {version!r}. {self._available(tool_id)}"
            )
        return found

    def _latest_major(self, tool_id: str) -> ToolSpec:
        majors = [major for (name, major) in self._by_major if name == tool_id]
        if not majors:
            raise ValidationError(f"Không có tool {tool_id!r}.")
        return self._by_major[(tool_id, max(majors))]

    def _latest_minor(self, tool_id: str, raw_major: str) -> ToolSpec:
        try:
            major = int(raw_major)
        except ValueError as exc:
            raise ValidationError(
                f"Version tool phải là 'major' hoặc 'major.minor', nhận được {raw_major!r}."
            ) from exc

        found = self._by_major.get((tool_id, major))
        if found is None:
            raise ValidationError(
                f"Không có tool {tool_id!r} major {major}. {self._available(tool_id)}"
            )
        return found

    def _available(self, tool_id: str) -> str:
        versions = sorted(tool.version for tool in self._tools.values() if tool.tool_id == tool_id)
        if not versions:
            return f"Không có tool nào tên {tool_id!r}."
        return f"Bản đang có: {', '.join(versions)}."

    def get_connector(self, connector_id: str) -> ConnectorSpec:
        found = self._connectors.get(connector_id)
        if found is None:
            raise ValidationError(f"Không có connector {connector_id!r}.")
        return found

    def connector_for(self, tool: ToolSpec) -> ConnectorSpec:
        """Connector sở hữu tool này. Ràng buộc đã kiểm lúc dựng registry."""
        return self.get_connector(tool.provider)

    def list_tools(
        self, *, provider: str | None = None, enabled_only: bool = True
    ) -> list[ToolSpec]:
        """Tool để hiển thị hoặc để dựng danh sách cho LLM."""
        found: list[ToolSpec] = list(self._by_major.values())
        if provider is not None:
            found = [tool for tool in found if tool.provider == provider]
        if enabled_only:
            found = [tool for tool in found if tool.enabled]
        return sorted(found, key=lambda tool: (tool.tool_id, parse_version(tool.version)))

    def list_connectors(self) -> list[ConnectorSpec]:
        return sorted(self._connectors.values(), key=lambda connector: connector.label)


def default_registry() -> ToolRegistry:
    """Registry dựng từ catalog đã sinh — thứ mà ứng dụng dùng lúc chạy."""
    from app.domains.integration.domain.generated import catalog_v1

    return ToolRegistry(catalog_v1.CONNECTORS, catalog_v1.TOOLS)
