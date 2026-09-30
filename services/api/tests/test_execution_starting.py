"""`ExecutionStarter`: kiểm, ghi, rồi mới khởi động — trên port giả."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from test_execution_service import FakeExecutionRepository

from app.core.errors import ProviderUnavailableError, ValidationError
from app.domains.execution.application.ports import RunnableVersion
from app.domains.execution.application.service import ExecutionService
from app.domains.execution.application.starting import ExecutionStarter
from app.domains.execution.domain.entities import ExecutionStatus

NOW = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)
RUNNABLE = RunnableVersion("wf_1", "wfv_1", 1, {"dang": "conn_slack"})


class _Runs:
    def __init__(self, log: list[str], error: Exception | None = None) -> None:
        self._log = log
        self._error = error

    async def prepare(self, **_: Any) -> RunnableVersion:
        self._log.append("prepare")
        if self._error is not None:
            raise self._error
        return RUNNABLE


class _Launcher:
    def __init__(self, log: list[str], fail: bool = False) -> None:
        self._log = log
        self._fail = fail
        self.started: list[dict[str, Any]] = []

    async def start(self, **request: Any) -> None:
        self._log.append("start")
        if self._fail:
            raise ProviderUnavailableError("Temporal không nhận lần chạy (RPCError).")
        self.started.append(request)


def _starter(
    log: list[str], repo: FakeExecutionRepository, runs: _Runs, launcher: _Launcher
) -> ExecutionStarter:
    @asynccontextmanager
    async def records() -> AsyncIterator[ExecutionService]:
        yield ExecutionService(repository=repo)
        log.append("commit")

    return ExecutionStarter(runs=runs, launcher=launcher, records=records, clock=lambda: NOW)


async def _start(starter: ExecutionStarter, inputs: Mapping[str, Any] | None = None) -> Any:
    return await starter.start_manual(
        tenant_id="org_1",
        workspace_id="ws_1",
        actor_id="usr_an",
        workflow_id="wf_1",
        version=None,
        inputs=inputs or {"noi_dung": "xin chào"},
        by_node={},
        by_provider={"slack": "conn_slack"},
    )


async def test_ghi_va_commit_truoc_roi_moi_khoi_dong() -> None:
    log: list[str] = []
    repo = FakeExecutionRepository()
    launcher = _Launcher(log)

    execution = await _start(_starter(log, repo, _Runs(log), launcher))

    assert log == ["prepare", "commit", "start"]
    assert execution.status is ExecutionStatus.PENDING
    assert execution.connection_bindings == {"dang": "conn_slack"}
    assert launcher.started == [
        {
            "execution_id": execution.id,
            "tenant_id": "org_1",
            "workflow_version_id": "wfv_1",
            "inputs": {"noi_dung": "xin chào"},
            "connections": {"dang": "conn_slack"},
        }
    ]


async def test_kiem_hong_thi_khong_co_lan_chay_nao() -> None:
    log: list[str] = []
    repo = FakeExecutionRepository()
    runs = _Runs(log, error=ValidationError("Input sai."))

    with pytest.raises(ValidationError):
        await _start(_starter(log, repo, runs, _Launcher(log)))

    assert log == ["prepare"]
    assert repo.executions == {}


async def test_khoi_dong_hong_thi_lan_chay_duoc_ghi_failed_va_tra_502() -> None:
    log: list[str] = []
    repo = FakeExecutionRepository()

    with pytest.raises(ProviderUnavailableError) as caught:
        await _start(_starter(log, repo, _Runs(log), _Launcher(log, fail=True)))

    assert log == ["prepare", "commit", "start", "commit"]
    assert caught.value.http_status == 502
    (execution,) = repo.executions.values()
    assert caught.value.details == {"execution_id": execution.id}
    assert execution.status is ExecutionStatus.FAILED
    assert execution.failure is not None
    assert str(execution.failure.category) == "ProviderUnavailable"
