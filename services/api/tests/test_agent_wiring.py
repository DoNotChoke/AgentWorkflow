"""`entrypoints/agent_wiring.py` — nối Agent ↔ Trace, chạy trên PostgreSQL thật.

Chạy thật vì thứ đáng kiểm là đúng thứ WP3 từng sửa: mỗi lần ghi phải nằm
trong transaction riêng của chính nó, không phải transaction của request đang
xử lý lượt Agent. Giả `session_scope()` đi thì test không còn chứng minh được
điều đó.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from conftest import InMemoryPayloadStore, infra_unavailable
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import dispose_engine, init_engine, session_scope
from app.domains.trace.application.service import TraceService
from app.domains.trace.infrastructure.repositories import SqlAlchemyExecutionTraceRepository
from app.entrypoints.agent_wiring import complete_agent_trace, start_agent_trace

pytestmark = pytest.mark.integration

TENANT = "org_agent_wiring"


@pytest.fixture(autouse=True)
async def engine() -> AsyncIterator[None]:
    init_engine(get_settings())
    try:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        await dispose_engine()
        infra_unavailable(f"PostgreSQL chưa sẵn sàng: {type(exc).__name__}. {exc}")

    yield

    async with session_scope() as session:
        await session.execute(
            text("DELETE FROM trace.trace_steps WHERE tenant_id = :tenant"), {"tenant": TENANT}
        )
        await session.execute(
            text("DELETE FROM trace.execution_traces WHERE tenant_id = :tenant"),
            {"tenant": TENANT},
        )
    await dispose_engine()


async def test_start_mo_duoc_trace_that() -> None:
    trace_id, sink = await start_agent_trace(
        tenant_id=TENANT,
        workspace_id="ws_1",
        agent_id=f"agt_{uuid.uuid4().hex}",
        conversation_id=f"cnv_{uuid.uuid4().hex}",
        initiated_by="usr_1",
    )

    assert trace_id.startswith("trc_")

    async with session_scope() as session:
        detail = await TraceService(
            traces=SqlAlchemyExecutionTraceRepository(session),
            payloads=InMemoryPayloadStore(),
        ).get(tenant_id=TENANT, trace_id=trace_id)
    assert detail.trace.status.value == "running"
    assert detail.steps == []
    assert sink is not None


async def test_sink_ghi_duoc_buoc_that() -> None:
    trace_id, sink = await start_agent_trace(
        tenant_id=TENANT,
        workspace_id="ws_1",
        agent_id=f"agt_{uuid.uuid4().hex}",
        conversation_id=f"cnv_{uuid.uuid4().hex}",
        initiated_by="usr_1",
    )
    at = datetime.now(UTC)

    await sink.append_step(
        kind="model_call",
        status="succeeded",
        started_at=at,
        completed_at=at + timedelta(milliseconds=300),
        attributes={"model": "claude-sonnet-5"},
    )

    async with session_scope() as session:
        detail = await TraceService(
            traces=SqlAlchemyExecutionTraceRepository(session),
            payloads=InMemoryPayloadStore(),
        ).get(tenant_id=TENANT, trace_id=trace_id)

    assert len(detail.steps) == 1
    assert detail.steps[0].kind.value == "model_call"
    assert detail.steps[0].attributes == {"model": "claude-sonnet-5"}


async def test_mot_luot_day_du_model_call_tool_call_final_response() -> None:
    trace_id, sink = await start_agent_trace(
        tenant_id=TENANT,
        workspace_id="ws_1",
        agent_id=f"agt_{uuid.uuid4().hex}",
        conversation_id=f"cnv_{uuid.uuid4().hex}",
        initiated_by="usr_1",
    )
    at = datetime.now(UTC)

    await sink.append_step(kind="model_call", status="succeeded", started_at=at, completed_at=at)
    await sink.append_step(
        kind="tool_call",
        status="succeeded",
        started_at=at,
        completed_at=at,
        attributes={"tool_id": "slack.post_message"},
    )
    await sink.append_step(
        kind="final_response", status="succeeded", started_at=at, completed_at=at
    )
    await complete_agent_trace(tenant_id=TENANT, trace_id=trace_id, status="succeeded")

    async with session_scope() as session:
        detail = await TraceService(
            traces=SqlAlchemyExecutionTraceRepository(session),
            payloads=InMemoryPayloadStore(),
        ).get(tenant_id=TENANT, trace_id=trace_id)

    assert [step.kind.value for step in detail.steps] == [
        "model_call",
        "tool_call",
        "final_response",
    ]
    assert detail.trace.status.value == "succeeded"


async def test_complete_voi_loi_ghi_lai_error_category() -> None:
    trace_id, _sink = await start_agent_trace(
        tenant_id=TENANT,
        workspace_id="ws_1",
        agent_id=f"agt_{uuid.uuid4().hex}",
        conversation_id=f"cnv_{uuid.uuid4().hex}",
        initiated_by="usr_1",
    )

    await complete_agent_trace(
        tenant_id=TENANT, trace_id=trace_id, status="failed", error_category="ProviderUnavailable"
    )

    async with session_scope() as session:
        detail = await TraceService(
            traces=SqlAlchemyExecutionTraceRepository(session),
            payloads=InMemoryPayloadStore(),
        ).get(tenant_id=TENANT, trace_id=trace_id)
    assert detail.trace.status.value == "failed"
    assert detail.trace.error_category == "ProviderUnavailable"


async def test_complete_trace_khong_ton_tai_khong_nem_loi() -> None:
    await complete_agent_trace(tenant_id=TENANT, trace_id="trc_khong_ton_tai", status="succeeded")
    # Không ném lỗi là đủ — đóng trace là dọn dẹp quan sát, không phải giao
    # dịch nghiệp vụ bắt buộc.


async def test_step_da_ghi_van_con_sau_khi_complete_that_bai() -> None:
    """Đúng bất biến WP3 từng sửa cho `IntegrationTraceSink`: `append_step` tự
    commit trong transaction của chính nó, nên nó không phụ thuộc — và không
    bị cuốn theo — bất cứ điều gì xảy ra ở một lời gọi khác sau đó, kể cả một
    lời gọi `complete_agent_trace` với `trace_id` sai."""
    trace_id, sink = await start_agent_trace(
        tenant_id=TENANT,
        workspace_id="ws_1",
        agent_id=f"agt_{uuid.uuid4().hex}",
        conversation_id=f"cnv_{uuid.uuid4().hex}",
        initiated_by="usr_1",
    )
    at = datetime.now(UTC)
    await sink.append_step(kind="model_call", status="succeeded", started_at=at, completed_at=at)

    # "Thất bại" ở đây là gọi complete cho một trace_id không tồn tại — hàm
    # không ném lỗi (đã kiểm ở test trên), nhưng dù nó CÓ ném, bước đã ghi ở
    # trên vẫn phải còn nguyên vì đã nằm trong một transaction khác, đã commit.
    await complete_agent_trace(tenant_id=TENANT, trace_id="trc_khong_ton_tai", status="failed")

    async with session_scope() as session:
        detail = await TraceService(
            traces=SqlAlchemyExecutionTraceRepository(session),
            payloads=InMemoryPayloadStore(),
        ).get(tenant_id=TENANT, trace_id=trace_id)
    assert len(detail.steps) == 1
    assert detail.trace.status.value == "running"
