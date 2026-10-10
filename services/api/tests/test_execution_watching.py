"""Theo dõi một lần chạy: chỉ nói ra thứ đã đổi. Không hạ tầng, không đồng hồ thật."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.domains.execution.application.service import ExecutionProgress
from app.domains.execution.application.watching import (
    Change,
    Done,
    ExecutionChanged,
    NodesChanged,
    Snapshot,
    watch,
)
from app.domains.execution.domain.entities import (
    Execution,
    ExecutionStatus,
    NodeExecution,
    NodeStatus,
)

NOW = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)

RUNNING = Execution(
    id="exe_1",
    tenant_id="org_1",
    workspace_id="ws_1",
    workflow_id="wf_1",
    workflow_version_id="wfv_1",
    trigger_type="manual",
    inputs={},
    initiated_by="usr_1",
    status=ExecutionStatus.RUNNING,
    created_at=NOW,
    updated_at=NOW,
    started_at=NOW,
)
SUCCEEDED = replace(RUNNING, status=ExecutionStatus.SUCCEEDED, finished_at=NOW)


def _node(path: str, status: NodeStatus = NodeStatus.PENDING) -> NodeExecution:
    return NodeExecution(
        id=f"nex_{path}",
        execution_id="exe_1",
        tenant_id="org_1",
        node_path=path,
        node_id=path.rsplit(".", 1)[-1],
        node_type="tool",
        status=status,
        updated_at=NOW,
    )


class Script:
    """Mỗi lần đọc trả về trạng thái kế tiếp; hết thì lặp lại trạng thái cuối."""

    def __init__(self, *states: ExecutionProgress) -> None:
        self._states = list(states)
        self.reads = 0
        self.slept: list[float] = []

    async def read(self) -> ExecutionProgress:
        self.reads += 1
        return self._states.pop(0) if len(self._states) > 1 else self._states[0]

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


def _state(execution: Execution, *nodes: NodeExecution) -> ExecutionProgress:
    return ExecutionProgress(execution=execution, nodes=tuple(nodes))


async def _watch(script: Script, *, max_seconds: float = 60) -> list[Change]:
    changes = watch(script.read, interval_seconds=0.5, max_seconds=max_seconds, sleep=script.sleep)
    return [change async for change in changes]


async def test_lan_chay_da_xong_ra_snapshot_roi_done_va_khong_doc_lai() -> None:
    script = Script(_state(SUCCEEDED, _node("dang", NodeStatus.SUCCEEDED)))

    changes = await _watch(script)

    assert [type(change) for change in changes] == [Snapshot, Done]
    assert (script.reads, script.slept) == (1, [])


async def test_node_doi_trang_thai_thi_chi_node_do_duoc_gui() -> None:
    tim, dang = _node("tim", NodeStatus.SUCCEEDED), _node("dang")
    dang_chay = replace(dang, status=NodeStatus.RUNNING, updated_at=NOW + timedelta(seconds=1))
    dang_xong = replace(dang, status=NodeStatus.SUCCEEDED, updated_at=NOW + timedelta(seconds=2))
    script = Script(
        _state(RUNNING, tim, dang),
        _state(RUNNING, tim, dang_chay),
        _state(SUCCEEDED, tim, dang_xong),
    )

    changes = await _watch(script)

    assert changes == [
        Snapshot(_state(RUNNING, tim, dang)),
        NodesChanged((dang_chay,)),
        NodesChanged((dang_xong,)),
        ExecutionChanged(SUCCEEDED),
        Done(),
    ]


async def test_luot_moi_cua_vong_lap_xuat_hien_giua_chung() -> None:
    khoi = _node("khoi", NodeStatus.SUCCEEDED)
    luot = (_node("moi_trang[0].doc"), _node("moi_trang[1].doc"))
    script = Script(
        _state(RUNNING, khoi),
        _state(RUNNING, khoi, *luot),
        _state(SUCCEEDED, khoi, *luot),
    )

    changes = await _watch(script)

    assert changes[1] == NodesChanged(luot)


async def test_khong_co_gi_doi_thi_khong_gui_gi() -> None:
    dang = _node("dang", NodeStatus.RUNNING)
    script = Script(
        _state(RUNNING, dang),
        _state(RUNNING, dang),
        _state(RUNNING, dang),
        _state(SUCCEEDED, replace(dang, status=NodeStatus.SUCCEEDED)),
    )

    changes = await _watch(script)

    assert [type(change) for change in changes] == [
        Snapshot,
        NodesChanged,
        ExecutionChanged,
        Done,
    ]
    assert script.slept == [0.5, 0.5, 0.5]


async def test_het_thoi_han_thi_dung_ma_khong_co_done() -> None:
    script = Script(_state(RUNNING, _node("dang", NodeStatus.RUNNING)))

    changes = await _watch(script, max_seconds=2)

    assert [type(change) for change in changes] == [Snapshot]
    assert script.slept == [0.5, 0.5, 0.5, 0.5]
