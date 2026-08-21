"""Entrypoint Temporal Worker.

python -m app.entrypoints.worker
"""

from __future__ import annotations

import asyncio
import signal
from types import FrameType

from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.core.observability import configure_tracing

log = get_logger(__name__)


@activity.defn(name="worker_heartbeat")
async def worker_heartbeat() -> str:
    """Activity giữ chỗ của Phase 0."""
    return "ok"


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

    # Phase 4: đăng ký Generic Workflow Executor và các Activity của
    # Integration Gateway ở đây.
    #
    #     workflows=[GenericWorkflowExecutor]
    #     activities=[execute_tool_node, evaluate_condition_node, ...]
    #
    # Mọi side effect ra bên ngoài PHẢI nằm trong Activity, không nằm trong
    # Workflow code — Workflow code phải deterministic (§14).
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue_processing,
        workflows=[],
        activities=[worker_heartbeat],
    )

    log.info("worker_starting", task_queue=settings.temporal_task_queue_processing)
    async with worker:
        await stop.wait()
    log.info("worker_stopped")


async def main() -> None:
    settings = get_settings()
    settings.assert_production_safe()

    configure_logging(settings)
    configure_tracing(settings, component="worker")

    stop = asyncio.Event()

    def handle_signal(_signum: int, _frame: FrameType | None) -> None:
        log.info("shutdown_signal_received")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handle_signal)

    await run_worker(settings, stop)


if __name__ == "__main__":
    asyncio.run(main())
