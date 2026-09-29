"""Phần của domain Workflow mà worker cần lúc chạy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.errors import WorkflowExecutionError
from app.domains.workflow.application.ports import ToolCatalogReader, WorkflowRepository
from app.domains.workflow.domain.ir import ToolNode, parse


def tool_key(tool_id: str, version: str) -> str:
    return f"{tool_id}@{version}"


@dataclass(frozen=True, slots=True)
class LoadedPlan:
    """Version đã publish, đủ để executor chạy. Kiểu thuần — nó đi qua Temporal."""

    workflow_version_id: str
    ir: dict[str, Any]
    #: `tool_key` của những tool có side effect — executor gửi chúng sang queue ghi (Q12).
    mutating_tools: list[str] = field(default_factory=list)


class PlanLoader:
    def __init__(self, *, repository: WorkflowRepository, catalog: ToolCatalogReader) -> None:
        self._workflows = repository
        self._catalog = catalog

    async def load(self, *, tenant_id: str, workflow_version_id: str) -> LoadedPlan:
        version = await self._workflows.get_version_by_id(
            tenant_id=tenant_id, version_id=workflow_version_id
        )
        if version is None:
            raise WorkflowExecutionError(
                "Không tìm thấy version workflow của lần chạy này.",
                details={"workflow_version_id": workflow_version_id},
            )

        mutating: set[str] = set()
        for _, node in parse(version.ir).walk():
            if not isinstance(node, ToolNode):
                continue
            # Version đã qua validator lúc publish; hụt ở đây là catalog đổi sau đó.
            contract = self._catalog.find(node.tool_id, node.tool_version)
            if contract is None:
                raise WorkflowExecutionError(
                    f"Tool {node.tool_id} {node.tool_version} không còn trong catalog.",
                    details={"node": node.id, "tool_id": node.tool_id},
                )
            if contract.mutates:
                mutating.add(tool_key(node.tool_id, node.tool_version))

        return LoadedPlan(
            workflow_version_id=version.id,
            ir=dict(version.ir),
            mutating_tools=sorted(mutating),
        )
