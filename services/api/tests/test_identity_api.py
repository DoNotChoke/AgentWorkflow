"""Kiểm tra luồng xác thực end-to-end qua HTTP, trên PostgreSQL và Redis thật.

Cần hạ tầng:

    docker compose up -d postgres redis
    alembic upgrade head
    pytest -m integration

Những gì test ở đây mà `test_identity_service.py` không kiểm được: migration có
khớp với model không, ràng buộc unique của database có thật sự chặn không, và
response JSON có vô tình để lọt trường nhạy cảm nào không.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any, cast

import pytest
from conftest import infra_unavailable
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

MAT_KHAU = "mat-khau-du-dai-12"


def _email() -> str:
    """Email riêng cho mỗi test — bảng users không được dọn giữa các lần chạy."""
    return f"test-{uuid.uuid4().hex}@example.com"


@pytest.fixture
def client() -> Iterator[TestClient]:
    """App thật, PostgreSQL thật, Redis thật.

    Không có ``try/except`` bao quanh khối này. Một phiên bản trước có, và nó
    biến mọi lỗi thành ``pytest.skip("Không khởi động được ứng dụng")`` — đọc
    y hệt như "Docker chưa bật". Cả 16 test trong file này hỏng suốt một thời
    gian dài mà vẫn hiện ra là bỏ qua bình thường.

    Điều kiện bỏ qua giờ được nêu tên tường minh, từng cái một, và chỉ dựa trên
    thứ ``/readyz`` báo cáo. Mọi lỗi khác làm test đỏ.
    """
    from app.entrypoints.http import create_app

    with TestClient(create_app()) as c:
        checks = c.get("/readyz").json().get("checks", {})
        if checks.get("postgres") != "ok":
            infra_unavailable(
                "chưa kết nối được PostgreSQL: "
                "docker compose up -d postgres && alembic upgrade head"
            )
        # `/readyz` cố ý vẫn báo "ready" khi Redis chết (§4.6), nên phải kiểm
        # riêng: rate limit hỏng mở, và các test rate limit sẽ trượt một cách
        # khó hiểu thay vì nói thẳng ra là thiếu Redis.
        if checks.get("redis") != "ok":
            infra_unavailable("chưa kết nối được Redis: docker compose up -d redis")

        _clear_rate_limits(c)
        yield c


def _clear_rate_limits(client: TestClient) -> None:
    """Xoá bộ đếm rate limit giữa các test.

    Không nới ngưỡng trong cấu hình test: ngưỡng thật phải là thứ được chạy.
    Ở đây chỉ dọn bộ đếm, vì mọi test đều đi từ cùng một IP và sẽ chạm trần
    trong vài lần chạy nếu không dọn.

    Chạy qua ``client.portal`` chứ không qua ``asyncio.run``. ``TestClient``
    chạy app trong event loop của một thread nền, và Redis client toàn cục
    được tạo trong lifespan nên gắn với đúng loop đó. ``asyncio.run`` sẽ mở
    một loop thứ hai, và await một future của loop kia từ loop này thì lúc
    ném ``RuntimeError: ... attached to a different loop``, lúc treo vĩnh viễn.
    """
    from app.core.redis import get_redis

    async def clear() -> None:
        redis = get_redis()
        async for key in redis.scan_iter(match="aw:g:login:*"):
            await redis.delete(key)
        async for key in redis.scan_iter(match="aw:g:register:*"):
            await redis.delete(key)

    assert client.portal is not None, "fixture phải chạy bên trong `with TestClient(...)`"
    client.portal.call(clear)


COOKIE = "aw_refresh"


def _refresh_cookie(client: TestClient) -> str:
    """Giá trị refresh token hiện tại trong cookie jar của client."""
    value = client.cookies.get(COOKIE)
    assert value, "Không có cookie refresh — endpoint phải set nó"
    return str(value)


def _dat_lai_cookie(client: TestClient, value: str) -> None:
    """Ép client trình lên một refresh token cũ, để giả lập việc dùng lại."""
    client.cookies.set(COOKIE, value, path="/v1/auth")


def _register(client: TestClient, email: str | None = None) -> dict[str, Any]:
    """Body của session vừa tạo.

    Kiểu là ``dict[str, Any]`` chứ không phải ``dict[str, object]``: response
    có cấu trúc lồng nhau (``body["user"]["status"]``), và ``object`` làm mọi
    lần truy cập tầng hai thành lỗi mypy, buộc mỗi chỗ dùng phải kèm một
    ``type: ignore``. Ép kiểu một lần ở đây rẻ hơn rải ``ignore`` khắp file.
    """
    resp = client.post(
        "/v1/auth/register",
        json={"email": email or _email(), "password": MAT_KHAU},
    )
    assert resp.status_code == 201, resp.text
    return cast("dict[str, Any]", resp.json())


# ─── Đăng ký ─────────────────────────────────────────────────────────────────


def test_dang_ky_tra_ve_phien_lam_viec(client: TestClient) -> None:
    body = _register(client)

    assert body["access_token"]
    assert body["token_type"] == "Bearer"
    # Refresh token đi bằng cookie httpOnly, không bao giờ nằm trong body.
    assert "refresh_token" not in body
    assert _refresh_cookie(client)
    assert body["user"]["status"] == "active"
    assert body["user"]["email_verified"] is False


def test_response_khong_bao_gio_chua_hash_mat_khau(client: TestClient) -> None:
    """Kiểm tra trên chuỗi JSON thô, không chỉ trên các khoá đã biết."""
    email = _email()
    resp = client.post("/v1/auth/register", json={"email": email, "password": MAT_KHAU})

    assert "argon2" not in resp.text
    assert "password" not in resp.text
    assert MAT_KHAU not in resp.text


def test_ràng_buộc_unique_cua_database_chan_email_trung(client: TestClient) -> None:
    email = _email()
    _register(client, email)

    resp = client.post("/v1/auth/register", json={"email": email.upper(), "password": MAT_KHAU})

    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == "ValidationError"


def test_mat_khau_yeu_bi_tu_choi(client: TestClient) -> None:
    resp = client.post("/v1/auth/register", json={"email": _email(), "password": "ngan"})
    assert resp.status_code == 422


# ─── Đăng nhập ───────────────────────────────────────────────────────────────


def test_dang_nhap_bang_tai_khoan_vua_tao(client: TestClient) -> None:
    email = _email()
    _register(client, email)

    resp = client.post("/v1/auth/login", json={"email": email, "password": MAT_KHAU})

    assert resp.status_code == 200
    assert resp.json()["user"]["email"] == email


def test_sai_mat_khau_tra_ve_401(client: TestClient) -> None:
    email = _email()
    _register(client, email)

    resp = client.post("/v1/auth/login", json={"email": email, "password": "sai-nhung-du-dai-12"})

    assert resp.status_code == 401
    assert resp.json()["error"]["category"] == "AuthenticationError"


def test_email_khong_ton_tai_tra_ve_401_giong_het_sai_mat_khau(client: TestClient) -> None:
    email = _email()
    _register(client, email)

    khong_co = client.post("/v1/auth/login", json={"email": _email(), "password": MAT_KHAU})
    sai_mk = client.post("/v1/auth/login", json={"email": email, "password": "sai-nhung-du-dai-12"})

    assert khong_co.status_code == sai_mk.status_code == 401
    assert khong_co.json() == sai_mk.json()


def test_qua_nhieu_lan_dang_nhap_sai_bi_chan(client: TestClient) -> None:
    email = _email()
    _register(client, email)

    codes = [
        client.post(
            "/v1/auth/login", json={"email": email, "password": "sai-nhung-du-dai-12"}
        ).status_code
        for _ in range(7)
    ]

    assert 429 in codes


# ─── /me ─────────────────────────────────────────────────────────────────────


def test_me_tra_ve_user_cua_token(client: TestClient) -> None:
    body = _register(client)
    token = body["access_token"]

    resp = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json()["id"] == body["user"]["id"]


def test_me_khong_co_token_tra_ve_401(client: TestClient) -> None:
    assert client.get("/v1/auth/me").status_code == 401


def test_me_token_bia_dat_tra_ve_401(client: TestClient) -> None:
    resp = client.get("/v1/auth/me", headers={"Authorization": "Bearer khong-phai-jwt"})
    assert resp.status_code == 401


# ─── Refresh ─────────────────────────────────────────────────────────────────


def test_refresh_doi_duoc_token_moi(client: TestClient) -> None:
    _register(client)
    cu = _refresh_cookie(client)

    resp = client.post("/v1/auth/refresh")

    assert resp.status_code == 200
    assert "refresh_token" not in resp.json()
    assert _refresh_cookie(client) != cu


def test_khong_co_cookie_thi_refresh_that_bai(client: TestClient) -> None:
    """Body JSON không còn là đường trình token — chỉ cookie mới được chấp nhận."""
    _register(client)
    cu = _refresh_cookie(client)
    client.cookies.clear()

    assert client.post("/v1/auth/refresh").status_code == 401
    assert client.post("/v1/auth/refresh", json={"refresh_token": cu}).status_code == 401


def test_token_cu_bi_vo_hieu_sau_khi_xoay(client: TestClient) -> None:
    _register(client)
    cu = _refresh_cookie(client)
    client.post("/v1/auth/refresh")

    _dat_lai_cookie(client, cu)
    lai = client.post("/v1/auth/refresh")

    assert lai.status_code == 401


def test_dung_lai_token_cu_thu_hoi_ca_ho(client: TestClient) -> None:
    """Kiểm tra qua HTTP rằng phát hiện tái sử dụng có chạy thật trên database."""
    _register(client)
    cu = _refresh_cookie(client)
    client.post("/v1/auth/refresh")
    moi = _refresh_cookie(client)

    # Kẻ trộm trình lại token cũ -> cả họ token phải chết theo.
    _dat_lai_cookie(client, cu)
    client.post("/v1/auth/refresh")

    _dat_lai_cookie(client, moi)
    chet_theo = client.post("/v1/auth/refresh")
    assert chet_theo.status_code == 401


# ─── Đăng xuất ───────────────────────────────────────────────────────────────


def test_dang_xuat_tra_ve_204_va_vo_hieu_token(client: TestClient) -> None:
    _register(client)
    cu = _refresh_cookie(client)

    resp = client.post("/v1/auth/logout")
    assert resp.status_code == 204

    _dat_lai_cookie(client, cu)
    assert client.post("/v1/auth/refresh").status_code == 401


def test_dang_xuat_lap_lai_van_tra_ve_204(client: TestClient) -> None:
    """Đăng xuất khi đã hết cookie vẫn phải thành công, nếu không client kẹt."""
    _register(client)

    client.post("/v1/auth/logout")
    lai = client.post("/v1/auth/logout")

    assert lai.status_code == 204
