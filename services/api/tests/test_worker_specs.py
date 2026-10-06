"""Bảng queue → workflow/activity của worker. Không hạ tầng."""

from __future__ import annotations

from app.core.config import Settings
from app.domains.workflow.infrastructure.temporal.activities import run_tool_node
from app.domains.workflow.infrastructure.temporal.workflow import GenericWorkflowExecutor
from app.entrypoints.worker import worker_specs
from app.entrypoints.worker_wiring import build_executor_activities

EXECUTOR = build_executor_activities(settings=Settings())


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
                EXECUTOR.run_transform_node,
            ),
        ),
        "integration-read": ((), (run_tool_node,)),
        "integration-write": ((), (run_tool_node,)),
        "ai-tasks": ((), (EXECUTOR.run_llm_node,)),
    }


def test_ten_queue_lay_tu_cau_hinh() -> None:
    settings = Settings(temporal_task_queue_processing="xu-ly-rieng")
    queues = {spec.task_queue for spec in worker_specs(settings, EXECUTOR)}
    assert "xu-ly-rieng" in queues
