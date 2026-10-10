"""Theo dõi một lần chạy: đọc lại định kỳ, và chỉ nói ra thứ đã đổi giữa hai lần đọc."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from app.domains.execution.application.service import ExecutionProgress
from app.domains.execution.domain.entities import Execution, NodeExecution


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Cả lần chạy, lúc bắt đầu theo dõi."""

    progress: ExecutionProgress


@dataclass(frozen=True, slots=True)
class ExecutionChanged:
    execution: Execution


@dataclass(frozen=True, slots=True)
class NodesChanged:
    """Node mới xuất hiện (lượt của một vòng lặp) hoặc node đã đổi trạng thái."""

    nodes: tuple[NodeExecution, ...]


@dataclass(frozen=True, slots=True)
class Done:
    """Lần chạy đã kết thúc; không còn gì để theo dõi."""


type Change = Snapshot | ExecutionChanged | NodesChanged | Done


async def watch(
    read: Callable[[], Awaitable[ExecutionProgress]],
    *,
    interval_seconds: float,
    max_seconds: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> AsyncIterator[Change]:
    """Hết `max_seconds` mà lần chạy chưa xong thì dừng, không phát `Done`: bên theo dõi
    nối lại và nhận một `Snapshot` mới."""
    progress = await read()
    yield Snapshot(progress)

    execution = progress.execution
    seen = {node.node_path: node for node in progress.nodes}
    waited = 0.0
    while not execution.is_terminal:
        if waited >= max_seconds:
            return
        await sleep(interval_seconds)
        waited += interval_seconds

        progress = await read()
        changed = tuple(node for node in progress.nodes if seen.get(node.node_path) != node)
        if changed:
            seen.update((node.node_path, node) for node in changed)
            yield NodesChanged(changed)
        if progress.execution != execution:
            execution = progress.execution
            yield ExecutionChanged(execution)
    yield Done()
