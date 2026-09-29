"""Máy trạng thái của execution và node. Không hạ tầng."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import ErrorCategory, ValidationError, WorkflowExecutionError
from app.domains.execution.domain.entities import (
    MAX_ERROR_MESSAGE,
    MAX_INPUTS_BYTES,
    Execution,
    ExecutionStatus,
    Failure,
    NodeExecution,
    NodeStatus,
    ensure_inputs_size,
    move_execution,
    move_node,
)

T0 = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)
T1 = T0 + timedelta(seconds=5)
T2 = T0 + timedelta(seconds=9)
LOI = Failure(ErrorCategory.PROVIDER_PERMANENT, "Notion trả 404")


def _execution(status: ExecutionStatus = ExecutionStatus.PENDING) -> Execution:
    return Execution(
        id="exe_1",
        tenant_id="org_1",
        workspace_id="ws_1",
        workflow_id="wf_1",
        workflow_version_id="wfv_1",
        trigger_type="manual",
        inputs={},
        initiated_by="usr_1",
        status=status,
        created_at=T0,
        updated_at=T0,
    )


def _node(status: NodeStatus = NodeStatus.PENDING, attempt: int = 0) -> NodeExecution:
    return NodeExecution(
        id="nex_1",
        execution_id="exe_1",
        tenant_id="org_1",
        node_path="moi_trang[2].doc_trang",
        node_id="doc_trang",
        node_type="tool",
        status=status,
        updated_at=T0,
        attempt=attempt,
    )


# ─── Execution ───────────────────────────────────────────────────────────────


def test_execution_chay_roi_xong_ghi_moc_thoi_gian() -> None:
    running = move_execution(_execution(), ExecutionStatus.RUNNING, at=T1)
    done = move_execution(running, ExecutionStatus.SUCCEEDED, at=T2)
    assert (running.started_at, running.finished_at) == (T1, None)
    assert (done.started_at, done.finished_at, done.updated_at) == (T1, T2, T2)


def test_execution_hong_mang_loi() -> None:
    running = move_execution(_execution(), ExecutionStatus.RUNNING, at=T1)
    failed = move_execution(running, ExecutionStatus.FAILED, at=T2, failure=LOI)
    assert failed.failure == LOI
    assert failed.is_terminal


def test_start_hong_thi_pending_sang_failed_duoc() -> None:
    failed = move_execution(_execution(), ExecutionStatus.FAILED, at=T1, failure=LOI)
    assert failed.started_at is None
    assert failed.finished_at == T1


def test_chuyen_ve_chinh_no_la_khong_doi_gi() -> None:
    running = move_execution(_execution(), ExecutionStatus.RUNNING, at=T1)
    assert move_execution(running, ExecutionStatus.RUNNING, at=T2) is running


@pytest.mark.parametrize(
    ("start", "to"),
    [
        (ExecutionStatus.SUCCEEDED, ExecutionStatus.RUNNING),
        (ExecutionStatus.FAILED, ExecutionStatus.SUCCEEDED),
        (ExecutionStatus.PENDING, ExecutionStatus.SUCCEEDED),
        (ExecutionStatus.RUNNING, ExecutionStatus.PENDING),
    ],
)
def test_execution_khong_chuyen_trai_luat(start: ExecutionStatus, to: ExecutionStatus) -> None:
    with pytest.raises(WorkflowExecutionError, match="không chuyển được"):
        move_execution(_execution(start), to, at=T1)


def test_chi_trang_thai_hong_mang_loi() -> None:
    with pytest.raises(WorkflowExecutionError, match="không mang lỗi"):
        move_execution(
            _execution(ExecutionStatus.RUNNING), ExecutionStatus.SUCCEEDED, at=T1, failure=LOI
        )


# ─── Node ────────────────────────────────────────────────────────────────────


def test_node_chay_roi_xong() -> None:
    running = move_node(_node(), NodeStatus.RUNNING, at=T1, attempt=1)
    done = move_node(running, NodeStatus.SUCCEEDED, at=T2)
    assert (running.attempt, running.started_at) == (1, T1)
    assert (done.attempt, done.started_at, done.finished_at) == (1, T1, T2)


def test_node_bi_bo_qua_tu_pending() -> None:
    skipped = move_node(_node(), NodeStatus.SKIPPED, at=T1)
    assert skipped.started_at is None
    assert skipped.finished_at == T1


def test_temporal_retry_tang_lan_thu_ma_giu_moc_bat_dau() -> None:
    first = move_node(_node(), NodeStatus.RUNNING, at=T1, attempt=1)
    second = move_node(first, NodeStatus.RUNNING, at=T2, attempt=2)
    assert (second.attempt, second.started_at, second.updated_at) == (2, T1, T2)


def test_ghi_lai_dung_trang_thai_cu_la_khong_doi_gi() -> None:
    done = move_node(_node(NodeStatus.RUNNING, attempt=1), NodeStatus.SUCCEEDED, at=T1)
    assert move_node(done, NodeStatus.SUCCEEDED, at=T2) is done
    running = _node(NodeStatus.RUNNING, attempt=2)
    assert move_node(running, NodeStatus.RUNNING, at=T2, attempt=2) is running


@pytest.mark.parametrize(
    ("start", "to"),
    [
        (NodeStatus.SUCCEEDED, NodeStatus.RUNNING),
        (NodeStatus.FAILED, NodeStatus.SUCCEEDED),
        (NodeStatus.SKIPPED, NodeStatus.RUNNING),
        (NodeStatus.PENDING, NodeStatus.SUCCEEDED),
        (NodeStatus.RUNNING, NodeStatus.PENDING),
    ],
)
def test_node_khong_chuyen_lui(start: NodeStatus, to: NodeStatus) -> None:
    with pytest.raises(WorkflowExecutionError) as caught:
        move_node(_node(start, attempt=1), to, at=T1)
    assert caught.value.details["node_path"] == "moi_trang[2].doc_trang"
    assert not caught.value.retryable


def test_lan_thu_cu_khong_de_len_lan_thu_moi() -> None:
    with pytest.raises(WorkflowExecutionError, match="cũ hơn"):
        move_node(_node(NodeStatus.RUNNING, attempt=3), NodeStatus.RUNNING, at=T1, attempt=2)
    with pytest.raises(WorkflowExecutionError, match="cũ hơn"):
        move_node(_node(NodeStatus.RUNNING, attempt=3), NodeStatus.FAILED, at=T1, attempt=2)


def test_node_hong_mang_loi_con_trang_thai_khac_thi_khong() -> None:
    failed = move_node(_node(NodeStatus.RUNNING, 1), NodeStatus.FAILED, at=T1, failure=LOI)
    assert failed.failure == LOI
    with pytest.raises(WorkflowExecutionError, match="không mang lỗi"):
        move_node(_node(NodeStatus.RUNNING, 1), NodeStatus.SUCCEEDED, at=T1, failure=LOI)


# ─── Giới hạn ────────────────────────────────────────────────────────────────


def test_thong_bao_loi_qua_dai_bi_cat() -> None:
    failure = Failure(ErrorCategory.INTERNAL, "x" * (MAX_ERROR_MESSAGE + 500))
    assert len(failure.message) == MAX_ERROR_MESSAGE
    assert failure.message.endswith("…")


def test_input_qua_tran_bi_tu_choi() -> None:
    ensure_inputs_size({"noi_dung": "a" * (MAX_INPUTS_BYTES - 100)})
    with pytest.raises(ValidationError, match="quá lớn"):
        ensure_inputs_size({"noi_dung": "a" * MAX_INPUTS_BYTES})
