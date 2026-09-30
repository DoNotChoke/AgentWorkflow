"""Một lần chạy workflow, và trạng thái từng node của nó."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from app.core.errors import ErrorCategory, ValidationError, WorkflowExecutionError

#: Trần của `inputs`, đo trên JSON chuẩn hoá. Chúng nằm cả trong DB lẫn lịch sử Temporal.
MAX_INPUTS_BYTES: Final = 64 * 1024

#: Thông báo lỗi dài hơn thế này bị cắt — chỗ đọc là màn hình tiến trình, không phải log.
MAX_ERROR_MESSAGE: Final = 2000


class ExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class NodeStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


_EXECUTION_MOVES: Final[Mapping[ExecutionStatus, frozenset[ExecutionStatus]]] = {
    # `pending → failed`: Temporal không nhận lần start.
    ExecutionStatus.PENDING: frozenset(
        {ExecutionStatus.RUNNING, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED}
    ),
    ExecutionStatus.RUNNING: frozenset(
        {
            ExecutionStatus.WAITING,
            ExecutionStatus.WAITING_FOR_APPROVAL,
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.TIMED_OUT,
        }
    ),
    ExecutionStatus.WAITING: frozenset(
        {
            ExecutionStatus.RUNNING,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.TIMED_OUT,
        }
    ),
    ExecutionStatus.WAITING_FOR_APPROVAL: frozenset(
        {
            ExecutionStatus.RUNNING,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.TIMED_OUT,
        }
    ),
}

_NODE_MOVES: Final[Mapping[NodeStatus, frozenset[NodeStatus]]] = {
    NodeStatus.PENDING: frozenset({NodeStatus.RUNNING, NodeStatus.SKIPPED}),
    NodeStatus.RUNNING: frozenset({NodeStatus.SUCCEEDED, NodeStatus.FAILED}),
}

EXECUTION_TERMINAL: Final = frozenset(
    {
        ExecutionStatus.SUCCEEDED,
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.TIMED_OUT,
    }
)
NODE_TERMINAL: Final = frozenset({NodeStatus.SUCCEEDED, NodeStatus.FAILED, NodeStatus.SKIPPED})

#: Trạng thái được phép mang lỗi.
_EXECUTION_ERRORS: Final = frozenset({ExecutionStatus.FAILED, ExecutionStatus.TIMED_OUT})


@dataclass(frozen=True, slots=True)
class Failure:
    category: ErrorCategory
    message: str

    def __post_init__(self) -> None:
        if len(self.message) > MAX_ERROR_MESSAGE:
            object.__setattr__(self, "message", self.message[: MAX_ERROR_MESSAGE - 1] + "…")


@dataclass(frozen=True, slots=True)
class Execution:
    id: str
    tenant_id: str
    workspace_id: str
    workflow_id: str
    workflow_version_id: str
    trigger_type: str
    inputs: Mapping[str, Any]
    initiated_by: str
    status: ExecutionStatus
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    failure: Failure | None = None
    #: Connection đã dùng, theo node tool. Bất biến như chính lần chạy.
    connection_bindings: Mapping[str, str] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.status in EXECUTION_TERMINAL


@dataclass(frozen=True, slots=True)
class NodeExecution:
    """Trạng thái vận hành của một lần chạy node. Không mang payload (Q11)."""

    id: str
    execution_id: str
    tenant_id: str
    #: `NodePath` của domain `workflow`, dạng chuỗi: `tim`, `moi_trang[2].doc_trang`.
    node_path: str
    node_id: str
    node_type: str
    status: NodeStatus
    updated_at: datetime
    #: Lần thử của Temporal, đếm từ 1. `0` là chưa thử lần nào.
    attempt: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    failure: Failure | None = None


def ensure_inputs_size(inputs: Mapping[str, Any]) -> None:
    size = len(json.dumps(inputs, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if size > MAX_INPUTS_BYTES:
        raise ValidationError(
            f"Input của lần chạy quá lớn: {size} byte, trần là {MAX_INPUTS_BYTES} byte.",
            details={"byte_size": size, "max_bytes": MAX_INPUTS_BYTES},
        )


def move_execution(
    execution: Execution, to: ExecutionStatus, *, at: datetime, failure: Failure | None = None
) -> Execution:
    """Chuyển trạng thái. Chuyển về chính nó thì không đổi gì; chuyển trái luật thì ném."""
    if failure is not None and to not in _EXECUTION_ERRORS:
        raise WorkflowExecutionError(f"Trạng thái {to} không mang lỗi.")
    if execution.status is to:
        return execution
    if to not in _EXECUTION_MOVES.get(execution.status, frozenset()):
        raise WorkflowExecutionError(
            f"Execution {execution.id} không chuyển được từ {execution.status} sang {to}.",
            details={"execution_id": execution.id, "from": str(execution.status), "to": str(to)},
        )
    return replace(
        execution,
        status=to,
        updated_at=at,
        started_at=execution.started_at or (at if to is ExecutionStatus.RUNNING else None),
        finished_at=at if to in EXECUTION_TERMINAL else None,
        failure=failure,
    )


def move_node(
    node: NodeExecution,
    to: NodeStatus,
    *,
    at: datetime,
    attempt: int | None = None,
    failure: Failure | None = None,
) -> NodeExecution:
    """Như `move_execution`. `running → running` với lần thử lớn hơn là một lần retry."""
    if failure is not None and to is not NodeStatus.FAILED:
        raise WorkflowExecutionError(f"Trạng thái {to} không mang lỗi.")
    attempt = node.attempt if attempt is None else attempt

    if node.status is to:
        if to is NodeStatus.RUNNING and attempt > node.attempt:
            return replace(node, attempt=attempt, updated_at=at)
        if attempt < node.attempt:
            raise _illegal(node, to, f"lần thử {attempt} cũ hơn lần {node.attempt} đã ghi")
        return node
    if to not in _NODE_MOVES.get(node.status, frozenset()):
        raise _illegal(node, to)
    if attempt < node.attempt:
        raise _illegal(node, to, f"lần thử {attempt} cũ hơn lần {node.attempt} đã ghi")

    return replace(
        node,
        status=to,
        attempt=attempt,
        updated_at=at,
        started_at=node.started_at or (at if to is NodeStatus.RUNNING else None),
        finished_at=at if to in NODE_TERMINAL else None,
        failure=failure,
    )


def _illegal(node: NodeExecution, to: NodeStatus, why: str | None = None) -> WorkflowExecutionError:
    reason = f"; {why}" if why else ""
    return WorkflowExecutionError(
        f"Node {node.node_path} không chuyển được từ {node.status} sang {to}{reason}.",
        details={
            "execution_id": node.execution_id,
            "node_path": node.node_path,
            "from": str(node.status),
            "to": str(to),
        },
    )
