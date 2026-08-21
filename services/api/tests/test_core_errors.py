"""Kiểm tra error model (§37) và ý nghĩa retry của nó (§15).

Bảng ``RETRYABLE`` không chỉ là metadata: nó sẽ là nguồn chân lý cho retry
policy của Temporal Activity. Phân loại sai một lỗi permanent thành retryable
sẽ khiến hệ thống đập vào provider hàng chục lần vô ích; phân loại ngược lại
khiến công việc thất bại oan.
"""

from __future__ import annotations

from app.core.errors import (
    AppError,
    AuthorizationError,
    ErrorCategory,
    ProviderPermanentError,
    ProviderUnavailableError,
    RateLimitedError,
    ValidationError,
)


def test_loi_tam_thoi_thi_retry_duoc() -> None:
    assert RateLimitedError("bi gioi han").retryable
    assert ProviderUnavailableError("provider sap").retryable


def test_loi_vinh_vien_thi_khong_retry() -> None:
    assert not ProviderPermanentError("endpoint bi go bo").retryable
    assert not ValidationError("input sai").retryable
    assert not AuthorizationError("khong du quyen").retryable


def test_moi_category_deu_co_ma_http() -> None:
    for category in ErrorCategory:
        assert 400 <= AppError("x", category=category).http_status <= 504


def test_payload_chua_category_va_message() -> None:
    payload = ValidationError("thieu truong 'channel'").to_payload()
    assert payload["error"]["category"] == "ValidationError"
    assert payload["error"]["message"] == "thieu truong 'channel'"


def test_retry_after_duoc_dua_vao_payload() -> None:
    payload = RateLimitedError("cho da", retry_after_seconds=30).to_payload()
    assert payload["error"]["retry_after_seconds"] == 30


def test_details_chi_xuat_hien_khi_co() -> None:
    assert "details" not in ValidationError("x").to_payload()["error"]
    co_details = ValidationError("x", details={"field": "channel"}).to_payload()
    assert co_details["error"]["details"] == {"field": "channel"}


def test_giu_duoc_nguyen_nhan_goc_de_chan_doan() -> None:
    goc = ValueError("chi tiet cua provider")
    loi = ProviderPermanentError("provider tu choi", cause=goc)
    assert loi.__cause__ is goc
