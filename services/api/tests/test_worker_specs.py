"""Bảng queue → workflow/activity của worker. Không hạ tầng."""

from __future__ import annotations

from app.core.config import Settings
from app.domains.workflow.infrastructure.catalog import RowToolCatalog
from app.domains.workflow.infrastructure.temporal.activities import (
    ExecutorActivities,
    run_tool_node,
)
from app.domains.workflow.infrastructure.temporal.workflow import GenericWorkflowExecutor
from app.entrypoints.worker import worker_specs
from app.entrypoints.worker_wiring import ExecutionServiceRecorder

EXECUTOR = ExecutorActivities(recorder=ExecutionServiceRecorder(), catalog=RowToolCatalog([]))


def test_moi_queue_chay_dung_workflow_va_activity_cua_no() -> None:
    specs = {
        spec.task_queue: (spec.workflows, spec.activities)
        for spec in worker_specs(Settings(), EXECUTOR)
    }
    assert specs == {
        "workflow-executor": ((GenericWorkflowExecutor,), ()),
        "data-processing": (
            (),
            (
                EXECUTOR.load_plan,
                EXECUTOR.seed_nodes,
                EXECUTOR.record_node,
                EXECUTOR.record_execution,
            ),
        ),
        "integration-read": ((), (run_tool_node,)),
        "integration-write": ((), (run_tool_node,)),
    }


def test_queue_khong_co_gi_de_chay_thi_khong_dung_worker() -> None:
    # Node llm (GĐ 6) chưa có.
    queues = {spec.task_queue for spec in worker_specs(Settings(), EXECUTOR)}
    assert "ai-tasks" not in queues


def test_ten_queue_lay_tu_cau_hinh() -> None:
    settings = Settings(temporal_task_queue_processing="xu-ly-rieng")
    queues = {spec.task_queue for spec in worker_specs(settings, EXECUTOR)}
    assert "xu-ly-rieng" in queues
