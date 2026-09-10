"""Router chỉ đọc của Trace, chạy trên Postgres thật.

Không có route ghi (xem docstring của `domains/trace/infrastructure/http.py`),
nên test này seed dữ liệu thẳng qua `TraceService` + `SqlAlchemyExecutionTraceRepository`
— đúng cách Agent Runtime và Integration Gateway sẽ gọi trong tiến trình, không
qua API.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import InMemoryPayloadStore, infra_unavailable

os.environ.setdefault("AW_JWT_SECRET", "test-secret-khong-dung-that-nhung-du-dai-32")
os.environ.setdefault("AW_KMS_LOCAL_MASTER_KEY", "test-master-key-khong-dung-that")

from fastapi.testclient import TestClient  # noqa: E402

from app.core.db import session_scope  # noqa: E402
from app.domains.trace.application.service import TraceService  # noqa: E402
from app.domains.trace.domain.entities import (  # noqa: E402
    StepKind,
    StepStatus,
    TraceSource,
    TraceStatus,
    TraceSubject,
)
from app.domains.trace.infrastructure.repositories import (  # noqa: E402
    SqlAlchemyExecutionTraceRepository,
)
from app.entrypoints.http import create_app  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as running:
        health = running.get("/readyz").json()
        if health["checks"].get("postgres") != "ok":
            infra_unavailable("PostgreSQL chưa chạy — `docker compose up -d postgres`.")
        _clear_rate_limits(running)
        yield running


def _clear_rate_limits(client: TestClient) -> None:
    """Mỗi test đăng ký người dùng mới từ cùng một IP, nên hạn mức đăng ký
    chạm trần rất nhanh khi chạy chung với các file test khác — cùng cách
    `test_integration_api.py` và `test_identity_api.py` làm."""
    from app.core.redis import get_redis

    async def clear() -> None:
        redis = get_redis()
        async for key in redis.scan_iter(match="aw:g:register:*"):
            await redis.delete(key)

    assert client.portal is not None
    client.portal.call(clear)


class Nguoi:
    """Một người dùng thật: đăng ký qua API, mang token trong mọi request."""

    def __init__(self, client: TestClient, ten: str) -> None:
        self.client = client
        self.email = f"{ten}-{uuid.uuid4().hex[:8]}@test.local"
        created = client.post(
            "/v1/auth/register",
            json={"email": self.email, "password": "Mat-khau-du-dai-123", "display_name": ten},
        )
        assert created.status_code == 201, created.text
        self.token = created.json()["access_token"]

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.client.get(path, headers=self._headers(), **kwargs)

    def _headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.token}"}


def _tenant_of(nguoi: Nguoi) -> tuple[str, str]:
    org = nguoi.get("/v1/organizations").json()[0]["id"]
    workspace = nguoi.get("/v1/workspaces").json()[0]["id"]
    return org, workspace


async def _seed_trace(
    tenant_id: str,
    workspace_id: str,
    conversation_id: str,
    initiated_by: str,
    at: datetime,
) -> str:
    """Một lượt Agent điển hình: gọi model, gọi một tool, trả lời cuối."""
    async with session_scope() as session:
        service = TraceService(
            traces=SqlAlchemyExecutionTraceRepository(session),
            payloads=InMemoryPayloadStore(),
        )
        trace = await service.start(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            source=TraceSource.AGENT,
            subject=TraceSubject(agent_id="agt_test", conversation_id=conversation_id),
            initiated_by=initiated_by,
            at=at,
        )
        model_step = await service.append_step(
            trace,
            kind=StepKind.MODEL_CALL,
            status=StepStatus.SUCCEEDED,
            started_at=at,
            completed_at=at + timedelta(milliseconds=300),
            attributes={"model": "claude-sonnet-5"},
        )
        tool_step = await service.append_step(
            trace,
            kind=StepKind.TOOL_CALL,
            status=StepStatus.SUCCEEDED,
            started_at=at + timedelta(milliseconds=310),
            completed_at=at + timedelta(milliseconds=900),
            parent_step_id=model_step.id,
            attributes={"tool_id": "slack.post_message"},
            input_payload={"channel": "C123", "text": "xin chào"},
            output_payload={"ts": "1700000000.000100"},
        )
        await service.append_step(
            trace,
            kind=StepKind.FINAL_RESPONSE,
            status=StepStatus.SUCCEEDED,
            started_at=at + timedelta(milliseconds=910),
            completed_at=at + timedelta(milliseconds=950),
            parent_step_id=tool_step.id,
        )
        await service.complete(trace, status=TraceStatus.SUCCEEDED, at=at + timedelta(seconds=1))
        return trace.id


def seed_trace(
    client: TestClient,
    tenant_id: str,
    workspace_id: str,
    conversation_id: str,
    initiated_by: str,
    at: datetime,
) -> str:
    """Seed qua portal của `TestClient` — cùng lý do `test_integration_api.py`
    làm vậy: engine gắn với event loop của `TestClient`, khác loop của
    `async def test_` do pytest-asyncio dựng."""
    assert client.portal is not None
    return str(
        client.portal.call(_seed_trace, tenant_id, workspace_id, conversation_id, initiated_by, at)
    )


# ─── GET /v1/traces/{trace_id} ────────────────────────────────────────────────


def test_xem_lai_mot_trace_kem_du_cac_buoc(client: TestClient) -> None:
    an = Nguoi(client, "An")
    org, workspace = _tenant_of(an)
    at = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)

    trace_id = seed_trace(client, org, workspace, "cnv_1", "usr_an", at)

    body = an.get(f"/v1/traces/{trace_id}").json()

    assert body["id"] == trace_id
    assert body["status"] == "succeeded"
    assert body["subject"]["conversation_id"] == "cnv_1"
    assert [step["kind"] for step in body["steps"]] == ["model_call", "tool_call", "final_response"]
    # Cây, không phải danh sách phẳng: tool_call trỏ về đúng model_call.
    model_step, tool_step, final_step = body["steps"]
    assert model_step["parent_step_id"] is None
    assert tool_step["parent_step_id"] == model_step["id"]
    assert final_step["parent_step_id"] == tool_step["id"]
    assert tool_step["attributes"] == {"tool_id": "slack.post_message"}


def test_trace_khong_ton_tai_bi_tu_choi_nhu_khong_thay(client: TestClient) -> None:
    an = Nguoi(client, "An")

    response = an.get("/v1/traces/trc_khong_ton_tai")

    assert response.status_code == 403


def test_trace_cua_to_chuc_khac_cung_bi_tu_choi(client: TestClient) -> None:
    an = Nguoi(client, "An")
    binh = Nguoi(client, "Binh")
    org_an, workspace_an = _tenant_of(an)
    at = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)

    trace_id = seed_trace(client, org_an, workspace_an, "cnv_an", "usr_an", at)

    response = binh.get(f"/v1/traces/{trace_id}")

    assert response.status_code == 403


# ─── GET /v1/conversations/{conversation_id}/traces ──────────────────────────


def test_liet_ke_trace_cua_mot_cuoc_hoi_thoai_moi_nhat_truoc(client: TestClient) -> None:
    an = Nguoi(client, "An")
    org, workspace = _tenant_of(an)
    t0 = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)

    first = seed_trace(client, org, workspace, "cnv_nhieu_luot", "usr_an", t0)
    second = seed_trace(
        client, org, workspace, "cnv_nhieu_luot", "usr_an", t0 + timedelta(minutes=5)
    )

    body = an.get("/v1/conversations/cnv_nhieu_luot/traces").json()

    assert [trace["id"] for trace in body] == [second, first]
    # Danh sách tóm tắt không mang `steps` — đúng hình dạng để duyệt lịch sử.
    assert "steps" not in body[0]


def test_liet_ke_hoi_thoai_chua_co_trace_tra_ve_rong(client: TestClient) -> None:
    an = Nguoi(client, "An")

    body = an.get("/v1/conversations/cnv_chua_ai_noi_gi/traces").json()

    assert body == []


# ─── GET /v1/traces/{trace_id}/steps/{step_id}/payload/{side} ────────────────

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def _steps(nguoi: Nguoi, trace_id: str) -> dict[str, dict[str, Any]]:
    body = nguoi.get(f"/v1/traces/{trace_id}").json()
    return {step["kind"]: step for step in body["steps"]}


def test_cay_trace_noi_payload_nam_o_dau_ma_khong_mang_noi_dung(client: TestClient) -> None:
    """Một query dựng được cả cây; nội dung để dành cho lúc người dùng mở."""
    an = Nguoi(client, "An")
    org, workspace = _tenant_of(an)
    trace_id = seed_trace(client, org, workspace, "cnv_payload", "usr_an", NOW)

    steps = _steps(an, trace_id)

    assert steps["tool_call"]["input_payload"]["location"] == "inline"
    assert steps["tool_call"]["input_payload"]["byte_size"] > 0
    assert "payload" not in steps["tool_call"]
    assert steps["model_call"]["input_payload"] is None


def test_doc_payload_cua_mot_buoc(client: TestClient) -> None:
    an = Nguoi(client, "An")
    org, workspace = _tenant_of(an)
    trace_id = seed_trace(client, org, workspace, "cnv_payload_doc", "usr_an", NOW)
    step_id = _steps(an, trace_id)["tool_call"]["id"]

    body = an.get(f"/v1/traces/{trace_id}/steps/{step_id}/payload/input").json()

    assert body["availability"] == "available"
    assert body["payload"] == {"channel": "C123", "text": "xin chào"}
    assert len(body["sha256"]) == 64


def test_buoc_khong_co_payload_phia_do_noi_ro_la_khong_luu(client: TestClient) -> None:
    """`not_stored` khác `gone`, và cả hai đều khác 'không tìm thấy'."""
    an = Nguoi(client, "An")
    org, workspace = _tenant_of(an)
    trace_id = seed_trace(client, org, workspace, "cnv_payload_thieu", "usr_an", NOW)
    step_id = _steps(an, trace_id)["model_call"]["id"]

    body = an.get(f"/v1/traces/{trace_id}/steps/{step_id}/payload/output").json()

    assert body["availability"] == "not_stored"
    assert body["payload"] is None
    assert body["sha256"] is None


def test_step_khong_ton_tai_bi_tu_choi(client: TestClient) -> None:
    an = Nguoi(client, "An")
    org, workspace = _tenant_of(an)
    trace_id = seed_trace(client, org, workspace, "cnv_payload_404", "usr_an", NOW)

    response = an.get(f"/v1/traces/{trace_id}/steps/stp_khong_ton_tai/payload/input")

    assert response.status_code == 403


def test_payload_cua_to_chuc_khac_bi_tu_choi(client: TestClient) -> None:
    """Cùng cách `GET /v1/traces/{id}` từ chối: id có thật hay không đều một câu."""
    an = Nguoi(client, "An")
    org, workspace = _tenant_of(an)
    trace_id = seed_trace(client, org, workspace, "cnv_payload_rieng", "usr_an", NOW)
    step_id = _steps(an, trace_id)["tool_call"]["id"]

    binh = Nguoi(client, "Binh")
    response = binh.get(f"/v1/traces/{trace_id}/steps/{step_id}/payload/input")

    assert response.status_code == 403


def test_phia_payload_khong_hop_le_bi_tu_choi(client: TestClient) -> None:
    an = Nguoi(client, "An")
    org, workspace = _tenant_of(an)
    trace_id = seed_trace(client, org, workspace, "cnv_payload_side", "usr_an", NOW)
    step_id = _steps(an, trace_id)["tool_call"]["id"]

    response = an.get(f"/v1/traces/{trace_id}/steps/{step_id}/payload/o-giua")

    assert response.status_code == 422
