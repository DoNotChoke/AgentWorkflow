"""Replay lịch sử đã ghi của kịch bản 3. Không hạ tầng.

Đỏ nghĩa là workflow code vừa đổi theo cách phá tất định: một execution đang
chạy dở sẽ vỡ khi worker được deploy bản mới. Tạo lại fixture
(`AW_RECORD_HISTORY=1`, xem `test_workflow_executor.py`) chỉ khi chấp nhận điều đó.
"""

from __future__ import annotations

import json
from pathlib import Path

from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from app.domains.workflow.infrastructure.temporal.workflow import GenericWorkflowExecutor

HISTORY = Path(__file__).resolve().parent / "fixtures" / "histories" / "kb3.json"


async def test_lich_su_kich_ban_ba_replay_duoc() -> None:
    raw = HISTORY.read_text(encoding="utf-8")
    started = json.loads(raw)["events"][0]["workflowExecutionStartedEventAttributes"]
    history = WorkflowHistory.from_json(started["workflowId"], raw)
    await Replayer(workflows=[GenericWorkflowExecutor]).replay_workflow(history)
