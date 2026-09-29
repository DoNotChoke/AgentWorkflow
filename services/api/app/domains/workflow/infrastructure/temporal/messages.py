"""Dữ liệu đi qua ranh giới workflow ↔ activity, và tên activity.

Không import hạ tầng: workflow code của Temporal nạp module này trong sandbox.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

LOAD_PLAN: Final = "load_plan"
SEED_NODES: Final = "seed_nodes"
RECORD_NODE: Final = "record_node"
RECORD_EXECUTION: Final = "record_execution"
RUN_TOOL_NODE: Final = "run_tool_node"


@dataclass(frozen=True)
class LoadPlanInput:
    tenant_id: str
    workflow_version_id: str


@dataclass(frozen=True)
class NodeRefMessage:
    node_path: str
    node_id: str
    node_type: str


@dataclass(frozen=True)
class SeedNodesInput:
    tenant_id: str
    execution_id: str
    nodes: list[NodeRefMessage]
    at: datetime


@dataclass(frozen=True)
class RecordNodeInput:
    tenant_id: str
    execution_id: str
    node: NodeRefMessage
    status: str
    at: datetime
    attempt: int | None = None
    error_category: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RecordExecutionInput:
    tenant_id: str
    execution_id: str
    #: `running` hoặc một trạng thái kết.
    status: str
    at: datetime
    error_category: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RunToolInput:
    tenant_id: str
    execution_id: str
    node_path: str
    tool_id: str
    tool_version: str
    input: dict[str, Any] = field(default_factory=dict)
