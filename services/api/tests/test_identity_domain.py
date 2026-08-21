"""Kiểm tra quy tắc nghiệp vụ thuần của Identity.

Không có hạ tầng nào trong file này — đó là điều tầng ``domain`` phải bảo đảm
(§30). Nếu một ngày nào đó những test này cần database mới chạy được, nghĩa là
ranh giới đã bị vi phạm.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import ValidationError
from app.domains.identity.domain.entities import (
    MIN_PASSWORD_LENGTH,
    RefreshToken,
    TokenVerdict,
    User,
    UserStatus,
    classify_refresh_token,
    normalize_display_name,
    normalize_email,
    validate_password_strength,
)

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def make_token(**overrides: object) -> RefreshToken:
    base: dict[str, object] = {
        "id": "rft_1",
        "user_id": "usr_1",
        "family_id": "rfa_1",
        "token_hash": "a" * 64,
        "issued_at": NOW - timedelta(hours=1),
        "expires_at": NOW + timedelta(days=30),
        "revoked_at": None,
        "rotated_to_id": None,
    }
    base.update(overrides)
    return RefreshToken(**base)  # type: ignore[arg-type]


# ─── Email ───────────────────────────────────────────────────────────────────


def test_email_duoc_ha_chu_thuong() -> None:
    """Nếu không, A@x.com và a@x.com tạo được hai tài khoản khác nhau."""
    assert normalize_email("Nguoi.Dung@Example.COM") == "nguoi.dung@example.com"


def test_email_duoc_cat_khoang_trang() -> None:
    assert normalize_email("  a@b.com  ") == "a@b.com"


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "khong-co-a-cong", "@thieu-phan-dau.com", "thieu-ten-mien@", "a@b", "a b@c.com"],
)
def test_email_sai_dinh_dang_bi_tu_choi(raw: str) -> None:
    with pytest.raises(ValidationError):
        normalize_email(raw)


def test_email_qua_dai_bi_tu_choi() -> None:
    with pytest.raises(ValidationError):
        normalize_email("a" * 320 + "@example.com")


@pytest.mark.parametrize(
    "raw",
    ["a+tag@example.com", "ten.ho@sub.example.co.uk", "chu_gach_duoi@example.io"],
)
def test_email_hop_le_nhung_la_mat_van_duoc_chap_nhan(raw: str) -> None:
    assert normalize_email(raw) == raw


# ─── Mật khẩu ────────────────────────────────────────────────────────────────


def test_mat_khau_qua_ngan_bi_tu_choi() -> None:
    with pytest.raises(ValidationError):
        validate_password_strength("a" * (MIN_PASSWORD_LENGTH - 1))


def test_mat_khau_du_dai_duoc_chap_nhan() -> None:
    validate_password_strength("a" * MIN_PASSWORD_LENGTH)


def test_khong_bat_buoc_hoa_so_ky_tu_dac_biet() -> None:
    """NIST SP 800-63B: quy tắc thành phần đẩy người dùng tới khuôn mẫu dễ đoán."""
    validate_password_strength("toan chu thuong va dau cach")


def test_mat_khau_qua_dai_bi_tu_choi() -> None:
    """Chặn trên tồn tại để tránh mỗi lần đăng nhập tốn hàng giây băm Argon2."""
    with pytest.raises(ValidationError):
        validate_password_strength("a" * 2000)


# ─── Tên hiển thị ────────────────────────────────────────────────────────────


def test_ten_hien_thi_rong_thi_lay_phan_truoc_cua_email() -> None:
    assert normalize_display_name("  ", fallback_email="nguoi.dung@example.com") == "nguoi.dung"


def test_ten_hien_thi_duoc_giu_nguyen_khi_co_gia_tri() -> None:
    assert normalize_display_name(" Tuấn Nghĩa ", fallback_email="a@b.com") == "Tuấn Nghĩa"


def test_ten_hien_thi_qua_dai_bi_cat() -> None:
    assert len(normalize_display_name("x" * 500, fallback_email="a@b.com")) == 200


# ─── Trạng thái user ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "expected"),
    [(UserStatus.ACTIVE, True), (UserStatus.DISABLED, False)],
)
def test_chi_user_active_moi_dang_nhap_duoc(status: UserStatus, expected: bool) -> None:
    user = User(
        id="usr_1",
        email="a@b.com",
        password_hash="hash",
        display_name="a",
        status=status,
        email_verified_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    assert user.can_authenticate is expected


# ─── Phân loại refresh token ─────────────────────────────────────────────────


def test_token_khong_ton_tai_la_unknown() -> None:
    assert classify_refresh_token(None, NOW) is TokenVerdict.UNKNOWN


def test_token_binh_thuong_la_valid() -> None:
    assert classify_refresh_token(make_token(), NOW) is TokenVerdict.VALID


def test_token_da_het_han_la_expired() -> None:
    het_han = make_token(expires_at=NOW - timedelta(seconds=1))
    assert classify_refresh_token(het_han, NOW) is TokenVerdict.EXPIRED


def test_token_dung_dung_luc_het_han_la_expired() -> None:
    """Biên đóng: đúng thời điểm hết hạn thì đã hết hạn."""
    assert classify_refresh_token(make_token(expires_at=NOW), NOW) is TokenVerdict.EXPIRED


def test_token_da_xoay_vong_ma_trinh_lai_la_reused() -> None:
    """Đây là tín hiệu token bị đánh cắp — cả họ phải bị thu hồi."""
    da_xoay = make_token(rotated_to_id="rft_2")
    assert classify_refresh_token(da_xoay, NOW) is TokenVerdict.REUSED


def test_token_da_thu_hoi_ma_trinh_lai_la_reused() -> None:
    da_thu_hoi = make_token(revoked_at=NOW - timedelta(minutes=5))
    assert classify_refresh_token(da_thu_hoi, NOW) is TokenVerdict.REUSED


def test_reused_duoc_uu_tien_hon_expired() -> None:
    """Token vừa hết hạn vừa bị dùng lại phải báo REUSED.

    Thứ tự này quan trọng: EXPIRED chỉ bắt đăng nhập lại, còn REUSED kích hoạt
    thu hồi cả họ. Xếp nhầm thứ tự thì một token bị đánh cắp và đã hết hạn sẽ
    không kích hoạt cảnh báo nào.
    """
    ca_hai = make_token(rotated_to_id="rft_2", expires_at=NOW - timedelta(days=1))
    assert classify_refresh_token(ca_hai, NOW) is TokenVerdict.REUSED
