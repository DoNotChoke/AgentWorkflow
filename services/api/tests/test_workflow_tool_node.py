"""Node `tool` của workflow gọi Integration Gateway qua HTTP. Không hạ tầng."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import (
    AuthorizationError,
    InvocationInProgressError,
    OutcomeUnknownError,
    ProviderUnavailableError,
    RateLimitedError,
    ValidationError,
)
from app.core.security import decode_access_token, decode_run_token
from app.domains.workflow.infrastructure.temporal.errors import to_application_error
from app.domains.workflow.infrastructure.temporal.tool_client import GatewayToolInvoker

SETTINGS = Settings(
    jwt_secret="khoa-ky-token-nguoi-dung-chi-de-test-32b",  # noqa: S106
    workflow_token_secret="khoa-ky-token-lan-chay-chi-de-test-32b",  # noqa: S106
    control_plane_internal_base_url="http://control-plane.test",
)


class Gateway:
    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._reply = httpx.Response(status, json=body or {"output": {"ok": True}})

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._reply


async def _invoke(gateway: Gateway, **overrides: Any) -> Any:
    invoker = GatewayToolInvoker(SETTINGS, transport=httpx.MockTransport(gateway.handle))
    call: dict[str, Any] = {
        "execution_id": "exe_1",
        "node_id": "dang",
        "tool_id": "slack.post_message",
        "tool_version": "1.0",
        "tool_input": {"channel": "C1", "text": "xin chào"},
        "idempotency_key": "idm_exe_1:moi_cau[2].dang:slack.post_message",
    }
    return await invoker.invoke(**(call | overrides))


async def test_goi_dung_endpoint_voi_token_cua_chinh_lan_chay() -> None:
    gateway = Gateway(body={"output": {"channel_id": "C1", "message_ts": "1.0"}})

    output = await _invoke(gateway)

    assert output == {"channel_id": "C1", "message_ts": "1.0"}
    (request,) = gateway.requests
    assert (
        str(request.url) == "http://control-plane.test/internal/v1/tools/slack.post_message/invoke"
    )
    token = request.headers["authorization"].removeprefix("Bearer ")
    claims = decode_run_token(SETTINGS, token)
    assert (claims.subject, claims.principal_kind) == ("exe_1", "workflow")
    # Worker không khai tenant: Gateway đọc nó từ bản ghi lần chạy.
    assert "x-organization-id" not in request.headers


async def test_than_request_khong_mang_connection_chi_noi_node_nao_dang_chay() -> None:
    gateway = Gateway()

    await _invoke(gateway)

    assert json.loads(gateway.requests[0].content) == {
        "input": {"channel": "C1", "text": "xin chào"},
        "tool_version": "1.0",
        "idempotency_key": "idm_exe_1:moi_cau[2].dang:slack.post_message",
        "caller": {
            "execution_id": "exe_1",
            "node_id": "dang",
            "execution_trace_id": None,
            "parent_step_id": None,
        },
    }


async def test_co_trace_thi_gateway_biet_ghi_buoc_cua_no_vao_dau() -> None:
    gateway = Gateway()

    await _invoke(gateway, trace_id="trc_1", parent_step_id="stp_1")

    assert json.loads(gateway.requests[0].content)["caller"] == {
        "execution_id": "exe_1",
        "node_id": "dang",
        "execution_trace_id": "trc_1",
        "parent_step_id": "stp_1",
    }


def test_token_lan_chay_khong_phai_access_token() -> None:
    """Khoá ký khác và loại token khác: không cái nào giải mã được bằng đường của cái kia."""
    from app.core.errors import AuthenticationError
    from app.core.security import issue_access_token, issue_run_token

    run = issue_run_token(SETTINGS, execution_id="exe_1")
    user, _ = issue_access_token(SETTINGS, subject="usr_1")

    with pytest.raises(AuthenticationError):
        decode_access_token(SETTINGS, run)
    with pytest.raises(AuthenticationError):
        decode_run_token(SETTINGS, user)


def test_chua_dat_khoa_ky_thi_worker_noi_ro_va_gateway_tu_choi() -> None:
    from app.core.errors import AuthenticationError
    from app.core.security import issue_run_token

    khong_khoa = Settings(
        jwt_secret="khoa-ky-token-nguoi-dung-chi-de-test-32b",  # noqa: S106
        workflow_token_secret="",
    )

    with pytest.raises(RuntimeError, match="AW_WORKFLOW_TOKEN_SECRET"):
        issue_run_token(khong_khoa, execution_id="exe_1")
    with pytest.raises(AuthenticationError):
        decode_run_token(khong_khoa, issue_run_token(SETTINGS, execution_id="exe_1"))


@pytest.mark.parametrize(
    ("status", "category", "expected"),
    [
        (403, "AuthorizationError", AuthorizationError),
        (422, "ValidationError", ValidationError),
        (429, "RateLimited", RateLimitedError),
        (503, "ProviderUnavailable", ProviderUnavailableError),
        (409, "InvocationInProgress", InvocationInProgressError),
        (409, "OutcomeUnknown", OutcomeUnknownError),
    ],
)
async def test_loi_cua_gateway_ve_lai_dung_hang_loi(
    status: int, category: str, expected: type[Exception]
) -> None:
    gateway = Gateway(status, {"error": {"category": category, "message": "không được"}})

    with pytest.raises(expected, match="không được"):
        await _invoke(gateway)


# ─── Hạng lỗi → cách Temporal thử lại ────────────────────────────────────────


def test_loi_tam_thoi_thi_thu_lai_va_doi_dung_chung_gateway_bao() -> None:
    error = to_application_error(RateLimitedError("chậm lại", retry_after_seconds=7))

    assert not error.non_retryable
    assert error.next_retry_delay == timedelta(seconds=7)


def test_loi_khac_dang_chay_cung_thao_tac_thi_doi_roi_goi_lai() -> None:
    error = to_application_error(InvocationInProgressError("đang chạy", retry_after_seconds=3))

    assert (error.non_retryable, error.next_retry_delay) == (False, timedelta(seconds=3))


def test_khong_ro_ket_qua_thi_khong_bao_gio_tu_goi_lai() -> None:
    """Gọi lại là đúng thứ sổ idempotency sinh ra để chặn: tin có thể đã lên Slack."""
    error = to_application_error(OutcomeUnknownError("không rõ"))

    assert error.non_retryable
    assert error.type == "OutcomeUnknown"


def test_loi_vinh_vien_thi_khong_thu_lai() -> None:
    assert to_application_error(ValidationError("sai input")).non_retryable


async def test_http_cho_lau_hon_thoi_han_cua_tool_mot_chut() -> None:
    gateway = Gateway()

    await _invoke(gateway, timeout_seconds=90.0)

    assert gateway.requests[0].extensions["timeout"]["read"] == 95.0
