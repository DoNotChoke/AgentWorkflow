"""Entrypoint Temporal Worker.

python -m app.entrypoints.worker
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from types import FrameType
from typing import Any

from temporalio.client import Client
from temporalio.worker import Worker

from app.core.config import Settings, get_settings
from app.core.db import dispose_engine, init_engine
from app.core.logging import configure_logging, get_logger
from app.core.observability import configure_tracing
from app.domains.workflow.infrastructure.temporal.activities import (
    ExecutorActivities,
    run_tool_node,
)
from app.entrypoints.worker_wiring import build_executor_activities

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class WorkerSpec:
    """Một `Worker` chỉ poll một queue. Tách tiến trình theo queue là đổi bảng này."""

    task_queue: str
    workflows: tuple[type, ...] = ()
    activities: tuple[Callable[..., Any], ...] = ()


def worker_specs(settings: Settings, executor: ExecutorActivities) -> list[WorkerSpec]:
    """Queue → workflow và activity. Queue không có gì để chạy thì bỏ."""
    specs = [
        WorkerSpec(settings.temporal_task_queue_workflow),
        WorkerSpec(
            settings.temporal_task_queue_processing,
            activities=(
                executor.load_plan,
                executor.seed_nodes,
                executor.record_node,
                executor.record_execution,
            ),
        ),
        WorkerSpec(settings.temporal_task_queue_integration_read, activities=(run_tool_node,)),
        WorkerSpec(settings.temporal_task_queue_integration_write, activities=(run_tool_node,)),
        WorkerSpec(settings.temporal_task_queue_ai),
    ]
    return [spec for spec in specs if spec.workflows or spec.activities]


def build_workers(client: Client, specs: Sequence[WorkerSpec]) -> list[Worker]:
    return [
        Worker(
            client,
            task_queue=spec.task_queue,
            workflows=list(spec.workflows),
            activities=list(spec.activities),
        )
        for spec in specs
    ]


async def connect(settings: Settings) -> Client:
    return await Client.connect(
        settings.temporal_host,
        namespace=settings.temporal_namespace,
    )


async def run_worker(settings: Settings, stop: asyncio.Event) -> None:
    client = await connect(settings)
    log.info(
        "temporal_connected",
        host=settings.temporal_host,
        namespace=settings.temporal_namespace,
    )

    specs = worker_specs(settings, build_executor_activities())
    async with AsyncExitStack() as stack:
        for worker in build_workers(client, specs):
            await stack.enter_async_context(worker)
        log.info("worker_starting", task_queues=[spec.task_queue for spec in specs])
        await stop.wait()
    log.info("worker_stopped")


async def main() -> None:
    settings = get_settings()
    settings.assert_production_safe()

    configure_logging(settings)
    configure_tracing(settings, component="worker")
    init_engine(settings)

    stop = asyncio.Event()

    def handle_signal(_signum: int, _frame: FrameType | None) -> None:
        log.info("shutdown_signal_received")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handle_signal)

    try:
        await run_worker(settings, stop)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
