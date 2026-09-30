"""Phần của domain Workflow mà worker cần lúc chạy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import (
    ConnectionChoiceRequiredError,
    ValidationError,
    WorkflowExecutionError,
)
from app.domains.workflow.application.ports import (
    ConnectionReader,
    ToolCatalogReader,
    WorkflowRepository,
)
from app.domains.workflow.domain.ir import ToolNode, WorkflowIR, parse


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


def nodes_needing_connection(ir: WorkflowIR, catalog: ToolCatalogReader) -> dict[str, str]:
    """Node tool của workflow, kèm provider của tool đó."""
    needed: dict[str, str] = {}
    for _, node in ir.walk():
        if isinstance(node, ToolNode):
            contract = catalog.find(node.tool_id, node.tool_version)
            if contract is not None:
                needed[node.id] = contract.provider
    return needed


async def bind_run_connections(
    ir: WorkflowIR,
    *,
    catalog: ToolCatalogReader,
    connections: ConnectionReader,
    tenant_id: str,
    workspace_id: str,
    actor_id: str,
    by_node: Mapping[str, str],
    by_provider: Mapping[str, str],
) -> dict[str, str]:
    """Connection mà một lần chạy dùng, theo node tool.

    `by_node` thắng `by_provider`. Không có gì được đoán: node nào trống thì phải
    chọn. Connection riêng chỉ chính chủ dùng được; của người khác thì trông như
    không tồn tại.
    """
    needed = nodes_needing_connection(ir, catalog)

    unknown_nodes = sorted(set(by_node) - set(needed))
    unknown_providers = sorted(set(by_provider) - set(needed.values()))
    if unknown_nodes or unknown_providers:
        raise ValidationError(
            "Lựa chọn connection trỏ vào thứ workflow không dùng.",
            details={"nodes": unknown_nodes, "providers": unknown_providers},
        )

    chosen = {
        node_id: by_node.get(node_id) or by_provider.get(provider)
        for node_id, provider in needed.items()
    }
    missing = [
        {"node": node_id, "provider": needed[node_id]}
        for node_id, connection_id in chosen.items()
        if not connection_id
    ]
    if missing:
        raise ConnectionChoiceRequiredError(
            "Cần chọn connection cho " + ", ".join(item["node"] for item in missing) + ".",
            details={"nodes": missing},
        )
    resolved = {
        node_id: connection_id for node_id, connection_id in chosen.items() if connection_id
    }

    problems: list[dict[str, str]] = []
    for node_id, connection_id in resolved.items():
        provider = needed[node_id]
        found = await connections.describe(
            tenant_id=tenant_id, workspace_id=workspace_id, connection_id=connection_id
        )
        if found is None or (not found.shared_with_workspace and found.owner_user_id != actor_id):
            reason = f"Không tìm thấy kết nối {connection_id} trong workspace này."
        elif found.provider != provider:
            reason = (
                f"Kết nối {connection_id} là của {found.provider}, node {node_id} cần {provider}."
            )
        elif not found.is_usable:
            reason = f"Kết nối {connection_id} đang ở trạng thái {found.status}."
        else:
            continue
        problems.append({"node": node_id, "connection_id": connection_id, "reason": reason})

    if problems:
        raise ValidationError(
            "; ".join(problem["reason"] for problem in problems),
            details={"connections": problems},
        )
    return resolved
