"""Kiểm tra hash mật khẩu, JWT phiên và refresh token (§20, §38).

Hai test đáng chú ý nhất:

- ``test_token_loai_khac_khong_dung_duoc_lam_access_token`` — chứng minh claim
  ``typ`` thật sự chặn việc dùng token do chính hệ thống ký nhưng cho mục đích
  khác làm access token.
- ``test_moi_ly_do_that_bai_deu_tra_ve_cung_mot_thong_diep`` — chứng minh lỗi
  xác thực không tiết lộ *vì sao* token hỏng.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import Settings
from app.core.errors import AuthenticationError
from app.core.security import (
    decode_access_token,
    extract_bearer_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    issue_access_token,
    password_needs_rehash,
    refresh_token_matches,
    verify_password,
    verify_password_of_missing_user,
)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        jwt_secret="khoa-ky-dung-cho-test-du-32-byte-tro-len", jwt_issuer="agentworkflow"
    )


# ─── Mật khẩu ────────────────────────────────────────────────────────────────


def test_hash_roi_verify_lai_thanh_cong() -> None:
    stored = hash_password("mat-khau-cua-toi")
    assert verify_password(stored, "mat-khau-cua-toi")


def test_hash_khong_chua_mat_khau_goc() -> None:
    assert "mat-khau-cua-toi" not in hash_password("mat-khau-cua-toi")


def test_hai_lan_hash_cung_mat_khau_cho_ket_qua_khac_nhau() -> None:
    """Salt ngẫu nhiên — hai người dùng cùng mật khẩu không nhận ra được nhau."""
    assert hash_password("giong-nhau") != hash_password("giong-nhau")


def test_mat_khau_sai_thi_khong_verify_duoc() -> None:
    assert not verify_password(hash_password("dung"), "sai")


def test_hash_hong_tra_ve_false_thay_vi_nem_loi() -> None:
    assert not verify_password("day-khong-phai-hash-argon2", "bat-ky")


def test_hash_hong_thi_coi_nhu_can_rehash() -> None:
    assert password_needs_rehash("day-khong-phai-hash-argon2")


def test_hash_moi_thi_chua_can_rehash() -> None:
    assert not password_needs_rehash(hash_password("mat-khau"))


def test_verify_cho_user_khong_ton_tai_luon_tra_ve_false() -> None:
    assert not verify_password_of_missing_user()


# ─── Access token ────────────────────────────────────────────────────────────


def test_ky_roi_giai_ma_tra_lai_dung_claim(settings: Settings) -> None:
    token, claims = issue_access_token(settings, subject="usr_abc")

    decoded = decode_access_token(settings, token)
    assert decoded.subject == "usr_abc"
    assert decoded.token_id == claims.token_id
    assert decoded.principal_kind == "user"


def test_moi_token_co_mot_jti_rieng(settings: Settings) -> None:
    """``jti`` là thứ cho phép thu hồi từng token về sau."""
    _, a = issue_access_token(settings, subject="usr_abc")
    _, b = issue_access_token(settings, subject="usr_abc")
    assert a.token_id != b.token_id


def test_token_khong_mang_tenant(settings: Settings) -> None:
    """Tenant được kiểm lại với membership mỗi request, không nằm trong token."""
    token, _ = issue_access_token(settings, subject="usr_abc")
    payload = jwt.decode(token, options={"verify_signature": False})
    assert "tenant_id" not in payload
    assert "org_id" not in payload


def test_token_ky_bang_khoa_khac_thi_bi_tu_choi(settings: Settings) -> None:
    khac = Settings(jwt_secret="mot-khoa-hoan-toan-khac-du-32-byte-tro-len")
    token, _ = issue_access_token(khac, subject="usr_abc")

    with pytest.raises(AuthenticationError):
        decode_access_token(settings, token)


def test_token_het_han_thi_bi_tu_choi(settings: Settings) -> None:
    het_han = Settings(
        jwt_secret="khoa-ky-dung-cho-test-du-32-byte-tro-len", access_token_ttl_seconds=-3600
    )
    token, _ = issue_access_token(het_han, subject="usr_abc")

    with pytest.raises(AuthenticationError):
        decode_access_token(settings, token)


def test_token_sai_issuer_thi_bi_tu_choi(settings: Settings) -> None:
    khac = Settings(
        jwt_secret="khoa-ky-dung-cho-test-du-32-byte-tro-len", jwt_issuer="he-thong-khac"
    )
    token, _ = issue_access_token(khac, subject="usr_abc")

    with pytest.raises(AuthenticationError):
        decode_access_token(settings, token)


def test_token_loai_khac_khong_dung_duoc_lam_access_token(settings: Settings) -> None:
    """Token do chính hệ thống ký nhưng cho mục đích khác phải bị từ chối."""
    now = datetime.now(tz=UTC)
    token = jwt.encode(
        {
            "sub": "usr_abc",
            "jti": "jti_gia",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
            "iss": settings.jwt_issuer,
            "typ": "email_verification",
        },
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )

    with pytest.raises(AuthenticationError):
        decode_access_token(settings, token)


def test_token_alg_none_bi_tu_choi(settings: Settings) -> None:
    """Tấn công kinh điển: đổi ``alg`` sang ``none`` để bỏ qua chữ ký."""
    now = datetime.now(tz=UTC)
    token = jwt.encode(
        {
            "sub": "usr_abc",
            "jti": "jti_gia",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
            "iss": settings.jwt_issuer,
            "typ": "access",
        },
        key="",
        algorithm="none",
    )

    with pytest.raises(AuthenticationError):
        decode_access_token(settings, token)


def test_token_thieu_claim_bat_buoc_bi_tu_choi(settings: Settings) -> None:
    now = datetime.now(tz=UTC)
    token = jwt.encode(
        {
            "sub": "usr_abc",
            "exp": int((now + timedelta(hours=1)).timestamp()),
            "iss": settings.jwt_issuer,
            "typ": "access",
        },
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )

    with pytest.raises(AuthenticationError):
        decode_access_token(settings, token)


def test_moi_ly_do_that_bai_deu_tra_ve_cung_mot_thong_diep(settings: Settings) -> None:
    """Thông điệp lỗi không được cho biết token hỏng vì lý do gì."""
    het_han = Settings(
        jwt_secret="khoa-ky-dung-cho-test-du-32-byte-tro-len", access_token_ttl_seconds=-3600
    )
    sai_khoa = Settings(jwt_secret="mot-khoa-hoan-toan-khac-du-32-byte-tro-len")

    thong_diep = set()
    for bad in (
        issue_access_token(het_han, subject="usr_abc")[0],
        issue_access_token(sai_khoa, subject="usr_abc")[0],
        "hoan-toan-khong-phai-jwt",
    ):
        with pytest.raises(AuthenticationError) as exc:
            decode_access_token(settings, bad)
        thong_diep.add(exc.value.message)

    assert len(thong_diep) == 1


def test_thuat_toan_bat_doi_xung_bi_tu_choi_ngay_khi_ky() -> None:
    """Cấu hình sai phải hỏng lúc khởi động, không phải lúc có người đăng nhập."""
    sai = Settings(jwt_secret="khoa-doi-xung-du-32-byte-tro-len-nhe", jwt_algorithm="RS256")

    with pytest.raises(RuntimeError, match="AW_JWT_ALGORITHM"):
        issue_access_token(sai, subject="usr_abc")


# ─── Độ mạnh của khoá ký ở production ────────────────────────────────────────


def test_production_tu_choi_jwt_secret_ngan() -> None:
    """Khoá HMAC ngắn hơn 32 byte phải chặn boot, không chỉ cảnh báo (RFC 7518)."""
    yeu = Settings(
        environment="production",
        jwt_secret="qua-ngan",
        kms_provider="aws",
        kms_local_master_key="mot-khoa-that-du-dai-cho-moi-truong-that",
    )

    with pytest.raises(RuntimeError, match="AW_JWT_SECRET ngắn hơn 32 byte"):
        yeu.assert_production_safe()


def test_production_chap_nhan_jwt_secret_du_dai() -> None:
    manh = Settings(
        environment="production",
        jwt_secret="mot-khoa-ky-that-su-du-dai-cho-production",
        kms_provider="aws",
        kms_local_master_key="mot-khoa-that-du-dai-cho-moi-truong-that",
    )

    manh.assert_production_safe()


def test_moi_truong_local_khong_bi_kiem_do_dai_khoa() -> None:
    """Máy phát triển vẫn dùng được giá trị mặc định trong .env.example."""
    Settings(environment="local", jwt_secret="ngan").assert_production_safe()


# ─── Header Authorization ────────────────────────────────────────────────────


def test_tach_duoc_bearer_token() -> None:
    assert extract_bearer_token("Bearer abc.def.ghi") == "abc.def.ghi"


def test_bearer_khong_phan_biet_hoa_thuong() -> None:
    assert extract_bearer_token("bearer abc") == "abc"


@pytest.mark.parametrize(
    "header",
    [None, "", "abc.def.ghi", "Basic dXNlcjpwYXNz", "Bearer", "Bearer   "],
)
def test_header_sai_dinh_dang_bi_tu_choi(header: str | None) -> None:
    with pytest.raises(AuthenticationError):
        extract_bearer_token(header)


# ─── Refresh token ───────────────────────────────────────────────────────────


def test_refresh_token_sinh_ra_kem_hash_khop_nhau() -> None:
    raw, stored = generate_refresh_token()
    assert refresh_token_matches(raw, stored)


def test_hai_lan_sinh_cho_hai_token_khac_nhau() -> None:
    a, _ = generate_refresh_token()
    b, _ = generate_refresh_token()
    assert a != b


def test_hash_khong_chua_gia_tri_tho() -> None:
    """Rò rỉ database không được đủ để mạo danh phiên làm việc."""
    raw, stored = generate_refresh_token()
    assert raw not in stored


def test_token_khac_khong_khop_hash() -> None:
    _, stored = generate_refresh_token()
    khac, _ = generate_refresh_token()
    assert not refresh_token_matches(khac, stored)


def test_hash_on_dinh_giua_cac_lan_goi() -> None:
    """Hash phải tất định, nếu không thì không tra cứu được trong database."""
    raw, _ = generate_refresh_token()
    assert hash_refresh_token(raw) == hash_refresh_token(raw)
