"""Khởi động một lần chạy bằng tay."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any

from app.core.errors import ErrorCategory, ProviderUnavailableError
from app.core.logging import get_logger
from app.domains.execution.application.ports import ExecutionLauncher, WorkflowRunSource
from app.domains.execution.application.service import ExecutionService
from app.domains.execution.domain.entities import Execution, ExecutionStatus, Failure

log = get_logger(__name__)

#: Mở một transaction riêng và trả `ExecutionService` trên nó; commit khi thoát sạch.
RecordsFactory = Callable[[], AbstractAsyncContextManager[ExecutionService]]


def _now() -> datetime:
    return datetime.now(UTC)


class ExecutionStarter:
    """Kiểm, ghi, rồi mới khởi động — mỗi bước ghi một transaction riêng.

    Execution phải được commit trước khi runtime nhận lệnh: activity đầu tiên của
    lần chạy đọc chính hàng đó.
    """

    def __init__(
        self,
        *,
        runs: WorkflowRunSource,
        launcher: ExecutionLauncher,
        records: RecordsFactory,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._runs = runs
        self._launcher = launcher
        self._records = records
        self._clock = clock

    async def start_manual(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        workflow_id: str,
        version: int | None,
        inputs: Mapping[str, Any],
        by_node: Mapping[str, str],
        by_provider: Mapping[str, str],
    ) -> Execution:
        runnable = await self._runs.prepare(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            workflow_id=workflow_id,
            version=version,
            inputs=inputs,
            by_node=by_node,
            by_provider=by_provider,
        )

        async with self._records() as executions:
            execution = await executions.create(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                workflow_id=runnable.workflow_id,
                workflow_version_id=runnable.workflow_version_id,
                trigger_type="manual",
                inputs=inputs,
                initiated_by=actor_id,
                at=self._clock(),
                connection_bindings=runnable.connections,
            )

        try:
            await self._launcher.start(
                execution_id=execution.id,
                tenant_id=tenant_id,
                workflow_version_id=runnable.workflow_version_id,
                inputs=inputs,
                connections=runnable.connections,
            )
        except ProviderUnavailableError as error:
            async with self._records() as executions:
                await executions.finish(
                    tenant_id=tenant_id,
                    execution_id=execution.id,
                    status=ExecutionStatus.FAILED,
                    at=self._clock(),
                    failure=Failure(ErrorCategory.PROVIDER_UNAVAILABLE, error.message),
                )
            log.warning("execution_launch_failed", execution_id=execution.id, reason=error.message)
            raise ProviderUnavailableError(
                "Không khởi động được lần chạy.", details={"execution_id": execution.id}
            ) from error

        log.info("execution_started", execution_id=execution.id, workflow_id=workflow_id)
        return execution
