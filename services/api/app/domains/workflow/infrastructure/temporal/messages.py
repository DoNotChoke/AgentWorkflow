"""Dữ liệu đi qua ranh giới workflow ↔ activity, và tên activity.

Không import hạ tầng: workflow code của Temporal nạp module này trong sandbox.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

#: Tên workflow. Control Plane start bằng chuỗi này, không import class.
EXECUTOR_WORKFLOW: Final = "GenericWorkflowExecutor"

LOAD_PLAN: Final = "load_plan"
SEED_NODES: Final = "seed_nodes"
RECORD_NODE: Final = "record_node"
RECORD_EXECUTION: Final = "record_execution"
RUN_TOOL_NODE: Final = "run_tool_node"
RUN_TRANSFORM_NODE: Final = "run_transform_node"
RUN_LLM_NODE: Final = "run_llm_node"


@dataclass(frozen=True)
class ExecutorQueues:
    """Queue của activity. Đi theo input vì workflow code không được đọc cấu hình."""

    processing: str
    integration_read: str
    integration_write: str
    #: Queue của node `llm`. Mặc định rỗng để lịch sử cũ vẫn decode được; rỗng thì
    #: executor dùng `processing`.
    ai: str = ""


@dataclass(frozen=True)
class ExecutorInput:
    execution_id: str
    tenant_id: str
    workflow_version_id: str
    inputs: dict[str, Any]
    queues: ExecutorQueues
    #: Connection của lần chạy, theo node tool. Mặc định rỗng để lịch sử cũ vẫn decode được.
    connections: dict[str, str] = field(default_factory=dict)


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
    #: Token model đã dùng, với node `llm`.
    usage: dict[str, int] | None = None
    #: Kết quả của một node tính ngay trong workflow (`condition`), để ghi vào trace.
    detail: dict[str, Any] | None = None


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
    #: Id của node trong IR: Gateway tra connection của lần chạy theo nó.
    node_id: str = ""
    #: Có với tool ghi ra ngoài. Mang `node_path`, tức cả chỉ số lượt của loop (ADR-0012).
    idempotency_key: str | None = None
    #: Thời hạn của tool trong catalog.
    timeout_seconds: float = 60.0


@dataclass(frozen=True)
class RunTransformInput:
    tenant_id: str
    execution_id: str
    node_path: str
    code: str
    args: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunLlmInput:
    tenant_id: str
    execution_id: str
    node_path: str
    model: str
    instructions: str
    input: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LlmNodeResult:
    output: dict[str, Any]
    input_tokens: int = 0
    output_tokens: int = 0
    #: Số lần đã hỏi model, kể cả những lần kết quả sai `output_schema`.
    attempts: int = 1
