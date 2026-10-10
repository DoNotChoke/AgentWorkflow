"""`ToolCatalogReader` dựng từ dữ liệu thuần.

Nhận hàng từ `integration.infrastructure.catalog_view` (do `entrypoints/wiring`
nối vào) và dựng `ToolContract` — kiểu của chính domain này.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.domains.workflow.application.ports import DEFAULT_TOOL_TIMEOUT_SECONDS, ToolContract


class RowToolCatalog:
    """Tra cứu trong bộ nhớ. Catalog là dữ liệu đã biên dịch vào tiến trình,
    nên không có gì để chờ ở đây."""

    def __init__(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self._by_key: dict[tuple[str, str], ToolContract] = {}
        self._versions: dict[str, list[str]] = {}
        for row in rows:
            contract = ToolContract(
                tool_id=row["tool_id"],
                version=row["version"],
                provider=row["provider"],
                input_schema=row["input_schema"],
                output_schema=row["output_schema"],
                mutates=bool(row["mutates"]),
                needs_approval=bool(row["needs_approval"]),
                enabled=bool(row.get("enabled", True)),
                timeout_seconds=float(row.get("timeout_seconds", DEFAULT_TOOL_TIMEOUT_SECONDS)),
            )
            self._by_key[(contract.tool_id, contract.version)] = contract
            self._versions.setdefault(contract.tool_id, []).append(contract.version)

    def find(self, tool_id: str, version: str) -> ToolContract | None:
        return self._by_key.get((tool_id, version))

    def versions(self, tool_id: str) -> tuple[str, ...]:
        return tuple(sorted(self._versions.get(tool_id, ())))
