"""Kiểm tra quy tắc nghiệp vụ thuần của Execution Trace.

Không có hạ tầng nào trong file này — đó là điều tầng ``domain`` phải bảo đảm
(§30). Nếu một ngày nào đó những test này cần database mới chạy được, nghĩa là
ranh giới đã bị vi phạm.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import ValidationError
from app.domains.trace.domain.entities import (
    MAX_ATTRIBUTE_BYTES,
    ExecutionTrace,
    PayloadRef,
    StepKind,
    StepStatus,
    TraceSource,
    TraceStatus,
    TraceStep,
    TraceSubject,
    complete,
    ensure_attributes_safe,
    ensure_not_terminal,
    ensure_subject_matches_source,
)

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def make_trace(**overrides: object) -> ExecutionTrace:
    base: dict[str, object] = {
        "id": "trc_1",
        "tenant_id": "org_1",
        "workspace_id": "ws_1",
        "source": TraceSource.AGENT,
        "status": TraceStatus.RUNNING,
        "subject": TraceSubject(agent_id="agt_1", conversation_id="cnv_1"),
        "initiated_by": "usr_1",
        "started_at": NOW,
    }
    base.update(overrides)
    return ExecutionTrace(**base)  # type: ignore[arg-type]


def make_step(**overrides: object) -> TraceStep:
    base: dict[str, object] = {
        "id": "stp_1",
        "trace_id": "trc_1",
        "kind": StepKind.MODEL_CALL,
        "status": StepStatus.SUCCEEDED,
        "started_at": NOW,
        "completed_at": NOW + timedelta(milliseconds=500),
    }
    base.update(overrides)
    return TraceStep(**base)  # type: ignore[arg-type]


# ─── PayloadRef ──────────────────────────────────────────────────────────────


def test_payload_ref_on_dinh_voi_thu_tu_khoa_khac_nhau() -> None:
    a = PayloadRef.of({"channel": "C1", "text": "hi"})
    b = PayloadRef.of({"text": "hi", "channel": "C1"})
    assert a == b


def test_payload_ref_khac_thi_hash_khac() -> None:
    a = PayloadRef.of({"text": "hi"})
    b = PayloadRef.of({"text": "hi!"})
    assert a.sha256 != b.sha256


# ─── ExecutionTrace ──────────────────────────────────────────────────────────


def test_trace_moi_khong_phai_terminal() -> None:
    trace = make_trace()
    assert trace.is_terminal is False
    assert trace.duration_ms is None


@pytest.mark.parametrize(
    "status", [TraceStatus.SUCCEEDED, TraceStatus.FAILED, TraceStatus.CANCELLED]
)
def test_trace_terminal_co_duration(status: TraceStatus) -> None:
    trace = make_trace(status=status, completed_at=NOW + timedelta(seconds=2))
    assert trace.is_terminal is True
    assert trace.duration_ms == pytest.approx(2000.0)


# ─── TraceStep ───────────────────────────────────────────────────────────────


def test_step_duration_tinh_tu_moc_thoi_gian() -> None:
    step = make_step()
    assert step.duration_ms == pytest.approx(500.0)


def test_step_goc_khong_co_parent() -> None:
    assert make_step().parent_step_id is None


def test_step_con_tro_ve_dung_parent() -> None:
    step = make_step(id="stp_2", kind=StepKind.TOOL_CALL, parent_step_id="stp_1")
    assert step.parent_step_id == "stp_1"


# ─── ensure_subject_matches_source ───────────────────────────────────────────


def test_agent_thieu_conversation_id_bi_tu_choi() -> None:
    with pytest.raises(ValidationError):
        ensure_subject_matches_source(TraceSource.AGENT, TraceSubject(agent_id="agt_1"))


def test_agent_du_agent_id_va_conversation_id_thi_qua() -> None:
    ensure_subject_matches_source(
        TraceSource.AGENT, TraceSubject(agent_id="agt_1", conversation_id="cnv_1")
    )


def test_workflow_thieu_execution_id_bi_tu_choi() -> None:
    with pytest.raises(ValidationError):
        ensure_subject_matches_source(TraceSource.WORKFLOW, TraceSubject(workflow_id="wf_1"))


def test_workflow_du_workflow_id_va_execution_id_thi_qua() -> None:
    ensure_subject_matches_source(
        TraceSource.WORKFLOW, TraceSubject(workflow_id="wf_1", execution_id="exe_1")
    )


# ─── ensure_attributes_safe ──────────────────────────────────────────────────


def test_attribute_nho_thi_qua() -> None:
    ensure_attributes_safe({"tool_id": "slack.post_message", "attempt": 1})


def test_attribute_qua_lon_bi_tu_choi() -> None:
    with pytest.raises(ValidationError):
        ensure_attributes_safe({"blob": "x" * (MAX_ATTRIBUTE_BYTES + 1)})


# ─── ensure_not_terminal / complete ──────────────────────────────────────────


def test_ensure_not_terminal_qua_voi_trace_dang_chay() -> None:
    ensure_not_terminal(make_trace())


def test_ensure_not_terminal_tu_choi_trace_da_ket() -> None:
    trace = make_trace(status=TraceStatus.SUCCEEDED, completed_at=NOW)
    with pytest.raises(ValidationError):
        ensure_not_terminal(trace)


def test_complete_dong_trace_dang_chay() -> None:
    trace = make_trace()
    at = NOW + timedelta(seconds=1)

    done = complete(trace, status=TraceStatus.SUCCEEDED, at=at)

    assert done.status is TraceStatus.SUCCEEDED
    assert done.completed_at == at
    assert done.error_category is None
    # Bất biến: hàm thuần không sửa đối tượng gốc.
    assert trace.status is TraceStatus.RUNNING


def test_complete_giu_lai_ly_do_loi() -> None:
    done = complete(
        make_trace(), status=TraceStatus.FAILED, at=NOW, error_category="ProviderUnavailable"
    )
    assert done.error_category == "ProviderUnavailable"


def test_complete_tu_choi_trang_thai_khong_phai_ket() -> None:
    with pytest.raises(ValidationError):
        complete(make_trace(), status=TraceStatus.RUNNING, at=NOW)


def test_complete_tu_choi_trace_da_ket() -> None:
    trace = make_trace(status=TraceStatus.CANCELLED, completed_at=NOW)
    with pytest.raises(ValidationError):
        complete(trace, status=TraceStatus.SUCCEEDED, at=NOW + timedelta(seconds=1))
