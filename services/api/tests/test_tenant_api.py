"""Luồng tenant end-to-end qua HTTP, trên PostgreSQL và Redis thật.

Cần hạ tầng:

    docker compose up -d postgres redis
    alembic upgrade head

Trọng tâm ở đây là §19 — ranh giới tenant. Những gì `test_tenant_service.py`
không kiểm được: dây nối giữa Identity và Tenant có thật sự chạy khi đăng ký
không, và `tenant_id` có còn cách nào để client tự khai không.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from conftest import infra_unavailable
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

MAT_KHAU = "mat-khau-du-dai-12"


@pytest.fixture
def client() -> Iterator[TestClient]:
    from app.entrypoints.http import create_app

    with TestClient(create_app()) as c:
        checks = c.get("/readyz").json().get("checks", {})
        if checks.get("postgres") != "ok":
            infra_unavailable(
                "chưa kết nối được PostgreSQL: "
                "docker compose up -d postgres && alembic upgrade head"
            )
        if checks.get("redis") != "ok":
            infra_unavailable("chưa kết nối được Redis: docker compose up -d redis")
        _clear_rate_limits(c)
        yield c


def _clear_rate_limits(client: TestClient) -> None:
    """Xem `test_identity_api._clear_rate_limits` — cùng lý do, cùng cách chạy."""
    from app.core.redis import get_redis

    async def clear() -> None:
        redis = get_redis()
        async for key in redis.scan_iter(match="aw:g:register:*"):
            await redis.delete(key)
        async for key in redis.scan_iter(match="aw:g:login:*"):
            await redis.delete(key)

    assert client.portal is not None
    client.portal.call(clear)


class Nguoi:
    """Một tài khoản đã đăng ký, kèm header xác thực sẵn sàng dùng."""

    def __init__(self, client: TestClient, ten: str) -> None:
        resp = client.post(
            "/v1/auth/register",
            json={
                "email": f"t-{uuid.uuid4().hex}@example.com",
                "password": MAT_KHAU,
                "display_name": ten,
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        self.client = client
        self.user_id: str = body["user"]["id"]
        self.headers = {"Authorization": f"Bearer {body['access_token']}"}

    def _headers(self, extra: dict[str, str] | None) -> dict[str, str]:
        """Gộp header xác thực với header riêng của lời gọi, không đè lên nhau."""
        return {**self.headers, **(extra or {})}

    def get(self, path: str, headers: dict[str, str] | None = None, **kwargs: Any) -> Any:
        return self.client.get(path, headers=self._headers(headers), **kwargs)

    def post(self, path: str, headers: dict[str, str] | None = None, **kwargs: Any) -> Any:
        return self.client.post(path, headers=self._headers(headers), **kwargs)

    def put(self, path: str, headers: dict[str, str] | None = None, **kwargs: Any) -> Any:
        return self.client.put(path, headers=self._headers(headers), **kwargs)

    def delete(self, path: str, headers: dict[str, str] | None = None, **kwargs: Any) -> Any:
        return self.client.delete(path, headers=self._headers(headers), **kwargs)

    @property
    def organization_id(self) -> str:
        orgs = self.get("/v1/organizations").json()
        return str(orgs[0]["id"])


# ─── Cấp phát khi đăng ký ────────────────────────────────────────────────────


def test_dang_ky_thi_duoc_cap_organization_va_workspace(client: TestClient) -> None:
    """Dây nối Identity -> Tenant chạy thật, không chỉ chạy trong unit test."""
    an = Nguoi(client, "An")

    orgs = an.get("/v1/organizations")
    assert orgs.status_code == 200
    assert len(orgs.json()) == 1
    assert orgs.json()[0]["role"] == "owner"

    workspaces = an.get("/v1/workspaces")
    assert workspaces.status_code == 200
    assert [w["name"] for w in workspaces.json()] == ["Default"]


def test_slug_organization_sinh_tu_ten_hien_thi(client: TestClient) -> None:
    an = Nguoi(client, "Công ty Cổ phần")
    assert an.get("/v1/organizations").json()[0]["slug"].startswith("cong-ty-co-phan")


# ─── Xác thực ────────────────────────────────────────────────────────────────


def test_khong_co_token_thi_khong_vao_duoc(client: TestClient) -> None:
    assert client.get("/v1/workspaces").status_code == 401
    assert client.get("/v1/organizations").status_code == 401


def test_header_dev_cu_khong_con_tac_dung(client: TestClient) -> None:
    """`x-dev-tenant-id` từng là đường để client tự khai tenant. Phải chết hẳn."""
    resp = client.get(
        "/v1/workspaces",
        headers={"x-dev-tenant-id": "org_gia_mao", "x-dev-workspace-id": "ws_gia_mao"},
    )
    assert resp.status_code == 401


# ─── Ranh giới tenant (§19) ──────────────────────────────────────────────────


def test_khong_doc_duoc_organization_cua_nguoi_khac(client: TestClient) -> None:
    an = Nguoi(client, "An")
    binh = Nguoi(client, "Bình")

    resp = binh.get("/v1/workspaces", headers={"x-organization-id": an.organization_id})

    assert resp.status_code == 403
    assert resp.json()["error"]["category"] == "AuthorizationError"


def test_khong_them_duoc_thanh_vien_vao_organization_cua_nguoi_khac(
    client: TestClient,
) -> None:
    an = Nguoi(client, "An")
    binh = Nguoi(client, "Bình")

    resp = binh.post(
        "/v1/organizations/current/members",
        headers={"x-organization-id": an.organization_id},
        json={"user_id": binh.user_id, "role": "owner"},
    )

    assert resp.status_code == 403


def test_khong_xoa_duoc_workspace_cua_nguoi_khac(client: TestClient) -> None:
    an = Nguoi(client, "An")
    binh = Nguoi(client, "Bình")
    cua_an = an.get("/v1/workspaces").json()[0]["id"]

    assert binh.delete(f"/v1/workspaces/{cua_an}").status_code == 403


def test_moi_nguoi_chi_thay_organization_cua_minh(client: TestClient) -> None:
    an = Nguoi(client, "An")
    binh = Nguoi(client, "Bình")

    cua_an = {o["id"] for o in an.get("/v1/organizations").json()}
    cua_binh = {o["id"] for o in binh.get("/v1/organizations").json()}

    assert cua_an.isdisjoint(cua_binh)


# ─── Vai trò ─────────────────────────────────────────────────────────────────


def test_member_khong_tao_duoc_workspace(client: TestClient) -> None:
    chu = Nguoi(client, "Chu")
    nhan_vien = Nguoi(client, "NhanVien")
    org = chu.organization_id

    them = chu.post(
        "/v1/organizations/current/members",
        json={"user_id": nhan_vien.user_id, "role": "member"},
    )
    assert them.status_code == 201

    resp = nhan_vien.post(
        "/v1/workspaces",
        headers={"x-organization-id": org},
        json={"name": "Lam lut"},
    )
    assert resp.status_code == 403


def test_member_khong_thay_workspace_chua_duoc_cap(client: TestClient) -> None:
    chu = Nguoi(client, "Chu")
    nhan_vien = Nguoi(client, "NhanVien")
    org = chu.organization_id
    chu.post("/v1/workspaces", json={"name": "Marketing"})
    chu.post(
        "/v1/organizations/current/members",
        json={"user_id": nhan_vien.user_id, "role": "member"},
    )

    thay = nhan_vien.get("/v1/workspaces", headers={"x-organization-id": org})

    assert thay.status_code == 200
    assert thay.json() == []


def test_duoc_cap_quyen_workspace_thi_thay_no(client: TestClient) -> None:
    chu = Nguoi(client, "Chu")
    nhan_vien = Nguoi(client, "NhanVien")
    org = chu.organization_id
    marketing = chu.post("/v1/workspaces", json={"name": "Marketing"}).json()
    chu.post(
        "/v1/organizations/current/members",
        json={"user_id": nhan_vien.user_id, "role": "member"},
    )

    cap = chu.put(
        f"/v1/workspaces/{marketing['id']}/members",
        json={"user_id": nhan_vien.user_id, "role": "editor"},
    )
    assert cap.status_code == 204

    thay = nhan_vien.get("/v1/workspaces", headers={"x-organization-id": org}).json()
    assert [w["id"] for w in thay] == [marketing["id"]]


def test_owner_cuoi_cung_khong_the_tu_roi_di(client: TestClient) -> None:
    an = Nguoi(client, "An")

    resp = an.delete(f"/v1/organizations/current/members/{an.user_id}")

    assert resp.status_code == 422
    assert "cuối cùng" in resp.json()["error"]["message"]


# ─── Onboarding bước 1: đặt tên và địa chỉ organization ──────────────────────


def test_org_moi_dang_ky_chua_hoan_tat_thiet_lap(client: TestClient) -> None:
    """Frontend dựa vào cờ này để đưa người dùng vào wizard."""
    an = Nguoi(client, "An")

    assert an.get("/v1/organizations").json()[0]["onboarded"] is False


def test_dat_lai_ten_va_dia_chi_trong_luc_thiet_lap(client: TestClient) -> None:
    an = Nguoi(client, "An")
    cu = an.get("/v1/organizations").json()[0]
    dia_chi_moi = f"acme-{uuid.uuid4().hex[:8]}"

    resp = an.client.patch(
        "/v1/organizations/current",
        headers=an.headers,
        json={"name": "Acme", "slug": dia_chi_moi},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Acme"
    assert resp.json()["slug"] == dia_chi_moi
    assert resp.json()["slug"] != cu["slug"]


def test_dia_chi_dong_bang_sau_khi_hoan_tat(client: TestClient) -> None:
    """Đây là lời hứa mà màn thiết lập ghi trên màn hình — phải đúng."""
    an = Nguoi(client, "An")
    an.post("/v1/organizations/current/complete-onboarding")

    resp = an.client.patch(
        "/v1/organizations/current",
        headers=an.headers,
        json={"name": "Acme", "slug": f"acme-{uuid.uuid4().hex[:8]}"},
    )

    assert resp.status_code == 422
    assert "đã được chốt" in resp.json()["error"]["message"]


def test_van_doi_duoc_ten_hien_thi_sau_khi_hoan_tat(client: TestClient) -> None:
    an = Nguoi(client, "An")
    an.post("/v1/organizations/current/complete-onboarding")

    resp = an.client.patch(
        "/v1/organizations/current", headers=an.headers, json={"name": "Acme Corporation"}
    )

    assert resp.status_code == 200
    assert resp.json()["name"] == "Acme Corporation"
    assert resp.json()["onboarded"] is True


def test_hoan_tat_thiet_lap_lap_lai_van_thanh_cong(client: TestClient) -> None:
    """Tải lại trang giữa chừng không được biến thành lỗi."""
    an = Nguoi(client, "An")

    assert an.post("/v1/organizations/current/complete-onboarding").status_code == 200
    assert an.post("/v1/organizations/current/complete-onboarding").status_code == 200


def test_dia_chi_da_co_nguoi_dung_thi_bao_loi(client: TestClient) -> None:
    an = Nguoi(client, "An")
    binh = Nguoi(client, "Binh")
    cua_an = an.get("/v1/organizations").json()[0]["slug"]

    resp = binh.client.patch(
        "/v1/organizations/current", headers=binh.headers, json={"name": "Acme", "slug": cua_an}
    )

    assert resp.status_code == 422
    assert "đã có người dùng" in resp.json()["error"]["message"]


def test_kiem_dia_chi_con_trong(client: TestClient) -> None:
    an = Nguoi(client, "An")
    cua_an = an.get("/v1/organizations").json()[0]["slug"]

    con_trong = an.get(f"/v1/organizations/slug-available?slug=chua-ai-dung-{uuid.uuid4().hex[:8]}")
    da_dung = an.get(f"/v1/organizations/slug-available?slug={cua_an}")

    assert con_trong.json()["available"] is True
    assert da_dung.json()["available"] is False


def test_dia_chi_sai_cu_phap_tra_ve_422_chu_khong_phai_available_false(
    client: TestClient,
) -> None:
    """Sai cú pháp và đã bị chiếm cần hai cách sửa khác nhau, không được gộp."""
    an = Nguoi(client, "An")

    assert an.get("/v1/organizations/slug-available?slug=Acme Corp").status_code == 422
    assert an.get("/v1/organizations/slug-available?slug=settings").status_code == 422


def test_member_thuong_khong_doi_duoc_dia_chi(client: TestClient) -> None:
    chu = Nguoi(client, "Chu")
    nhan_vien = Nguoi(client, "NhanVien")
    org = chu.organization_id
    chu.post(
        "/v1/organizations/current/members",
        json={"user_id": nhan_vien.user_id, "role": "member"},
    )

    resp = nhan_vien.client.patch(
        "/v1/organizations/current",
        headers={**nhan_vien.headers, "x-organization-id": org},
        json={"name": "Chiem doat", "slug": "chiem-doat"},
    )

    assert resp.status_code == 403


# ─── Onboarding bước 2: đặt tên workspace đầu tiên ───────────────────────────


def test_doi_ten_workspace_mac_dinh(client: TestClient) -> None:
    """Wizard bước 2 đổi cái "Default" hệ thống cấp thành tên thật của nhóm."""
    an = Nguoi(client, "An")
    mac_dinh = an.get("/v1/workspaces").json()[0]
    assert mac_dinh["name"] == "Default"

    resp = an.client.patch(
        f"/v1/workspaces/{mac_dinh['id']}",
        headers=an.headers,
        json={"name": "Marketing", "slug": "marketing"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Marketing"
    assert resp.json()["slug"] == "marketing"
    assert an.get("/v1/workspaces").json()[0]["name"] == "Marketing"


def test_dia_chi_workspace_trung_trong_cung_org_bi_tu_choi(client: TestClient) -> None:
    an = Nguoi(client, "An")
    mac_dinh = an.get("/v1/workspaces").json()[0]
    an.post("/v1/workspaces", json={"name": "Engineering"})

    resp = an.client.patch(
        f"/v1/workspaces/{mac_dinh['id']}",
        headers=an.headers,
        json={"name": "Marketing", "slug": "engineering"},
    )

    assert resp.status_code == 422
    assert "đã có workspace" in resp.json()["error"]["message"]


def test_khong_doi_duoc_ten_workspace_cua_tenant_khac(client: TestClient) -> None:
    an = Nguoi(client, "An")
    binh = Nguoi(client, "Binh")
    cua_an = an.get("/v1/workspaces").json()[0]["id"]

    resp = binh.client.patch(
        f"/v1/workspaces/{cua_an}", headers=binh.headers, json={"name": "Chiem doat"}
    )

    assert resp.status_code == 403
