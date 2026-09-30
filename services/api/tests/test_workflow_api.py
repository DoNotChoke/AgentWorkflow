"""Router của Workflow, chạy đầu-cuối trên Postgres thật.

Nghiệm thu của GĐ 3 nằm ở cuối file: publish được một workflow bind kết nối
chung của workspace, và **bị từ chối** khi bind một kết nối riêng tư (Q7).
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import infra_unavailable

os.environ.setdefault("AW_JWT_SECRET", "test-secret-khong-dung-that-nhung-du-dai-32")
os.environ.setdefault("AW_KMS_LOCAL_MASTER_KEY", "test-master-key-khong-dung-that")

from fastapi.testclient import TestClient  # noqa: E402

from app.entrypoints.http import create_app  # noqa: E402

pytestmark = pytest.mark.integration

EXAMPLES = Path(__file__).resolve().parents[3] / "packages" / "schemas" / "workflow" / "examples"
NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as running:
        health = running.get("/readyz").json()
        if health["checks"].get("postgres") != "ok":
            infra_unavailable("PostgreSQL chưa chạy — `docker compose up -d postgres`.")
        _clear_rate_limits(running)
        yield running


def _clear_rate_limits(client: TestClient) -> None:
    from app.core.redis import get_redis

    async def clear() -> None:
        redis = get_redis()
        async for key in redis.scan_iter(match="aw:g:register:*"):
            await redis.delete(key)

    assert client.portal is not None
    client.portal.call(clear)


class Nguoi:
    """Một người dùng thật: đăng ký qua API, mang token trong mọi request."""

    def __init__(self, client: TestClient, ten: str = "nguoi") -> None:
        self.client = client
        created = client.post(
            "/v1/auth/register",
            json={
                "email": f"{ten}-{uuid.uuid4().hex[:8]}@test.local",
                "password": "Mat-khau-du-dai-123",
                "display_name": ten,
            },
        )
        assert created.status_code == 201, created.text
        self.token = created.json()["access_token"]
        self.user_id = created.json()["user"]["id"]
        # Hai lời gọi này đi trước khi có header tenant — chúng chính là chỗ
        # lấy ra tenant, nên không dùng `self.get` được.
        auth = {"authorization": f"Bearer {self.token}"}
        self.organization_id = client.get("/v1/organizations", headers=auth).json()[0]["id"]
        self.workspace_id = client.get("/v1/workspaces", headers=auth).json()[0]["id"]

    def _headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self.token}",
            "x-organization-id": self.organization_id,
            "x-workspace-id": self.workspace_id,
        }

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.client.get(path, headers=self._headers(), **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        return self.client.post(path, headers=self._headers(), **kwargs)

    def put(self, path: str, **kwargs: Any) -> Any:
        return self.client.put(path, headers=self._headers(), **kwargs)


def _example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _create(nguoi: Nguoi, name: str = "Tổng hợp #support") -> str:
    created = nguoi.post("/v1/workflows", json={"name": name})
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


# ─── Tạo, đọc, sửa draft ─────────────────────────────────────────────────────


def test_tao_workflow_thi_co_ngay_mot_draft_hop_le(client: TestClient) -> None:
    """Draft khởi đầu có đúng một trigger: hợp lệ nhưng chưa làm gì."""
    nguoi = Nguoi(client)
    workflow_id = _create(nguoi)

    draft = nguoi.get(f"/v1/workflows/{workflow_id}/draft").json()

    assert [node["type"] for node in draft["ir"]["nodes"]] == ["trigger"]
    assert draft["validation"]["ok"] is True


def test_liet_ke_va_doc_lai_workflow(client: TestClient) -> None:
    nguoi = Nguoi(client)
    workflow_id = _create(nguoi, "Workflow của tôi")

    listed = nguoi.get("/v1/workflows").json()
    one = nguoi.get(f"/v1/workflows/{workflow_id}").json()

    assert [item["id"] for item in listed] == [workflow_id]
    assert one["name"] == "Workflow của tôi"
    assert one["current_version_id"] is None


def test_luu_duoc_draft_chua_hop_le_va_bao_cao_noi_ro_cho_sai(client: TestClient) -> None:
    """Q14: editor phải lưu được trạng thái giữa chừng, và vẫn biết sai ở đâu."""
    nguoi = Nguoi(client)
    workflow_id = _create(nguoi)
    hong = _example("kb1-slack-ghi")
    hong["nodes"][1]["input"]["channel"] = {"from_node": {"node": "khong_co", "path": ["id"]}}

    saved = nguoi.put(f"/v1/workflows/{workflow_id}/draft", json={"ir": hong})

    assert saved.status_code == 200, saved.text
    validation = saved.json()["validation"]
    assert validation["ok"] is False
    assert [issue["code"] for issue in validation["errors"]] == ["binding_node_khong_ton_tai"]
    # Vẫn được lưu, không phải bị từ chối.
    assert nguoi.get(f"/v1/workflows/{workflow_id}/draft").json()["ir"] == hong


def test_draft_sai_hinh_dang_van_luu_duoc(client: TestClient) -> None:
    nguoi = Nguoi(client)
    workflow_id = _create(nguoi)

    saved = nguoi.put(
        f"/v1/workflows/{workflow_id}/draft", json={"ir": {"nodes": "khong phai mang"}}
    )

    assert saved.status_code == 200, saved.text
    assert [issue["code"] for issue in saved.json()["validation"]["errors"]] == ["khong_doc_duoc"]


def test_canh_bao_khong_chan_publish(client: TestClient) -> None:
    """`chi_so_tho` là cảnh báo: chạy được, nhưng gần như chắc không đúng ý."""
    nguoi = Nguoi(client)
    workflow_id = _create(nguoi)
    ir = _example("kb5-re-nhanh")

    saved = nguoi.put(f"/v1/workflows/{workflow_id}/draft", json={"ir": ir}).json()

    assert saved["validation"]["ok"] is True
    assert [issue["code"] for issue in saved["validation"]["warnings"]] == ["chi_so_tho"]


# ─── Publish ─────────────────────────────────────────────────────────────────


def test_publish_dong_bang_thanh_version_mot(client: TestClient) -> None:
    """Không cần connection nào: lần chạy mới chọn."""
    nguoi = Nguoi(client)
    workflow_id = _create(nguoi)
    nguoi.put(f"/v1/workflows/{workflow_id}/draft", json={"ir": _example("kb1-slack-ghi")})

    published = nguoi.post(f"/v1/workflows/{workflow_id}/publish")

    assert published.status_code == 201, published.text
    version = published.json()
    assert version["number"] == 1
    assert len(version["ir_sha256"]) == 64
    assert nguoi.get(f"/v1/workflows/{workflow_id}").json()["current_version_id"] == version["id"]


def test_publish_lan_hai_ra_version_hai_va_version_cu_giu_nguyen(client: TestClient) -> None:
    nguoi = Nguoi(client)
    workflow_id = _create(nguoi)
    first_ir = _example("kb1-slack-ghi")
    nguoi.put(f"/v1/workflows/{workflow_id}/draft", json={"ir": first_ir})
    nguoi.post(f"/v1/workflows/{workflow_id}/publish")

    nguoi.put(f"/v1/workflows/{workflow_id}/draft", json={"ir": _example("kb2-slack-doc-roi-ghi")})
    second = nguoi.post(f"/v1/workflows/{workflow_id}/publish").json()

    assert second["number"] == 2
    versions = nguoi.get(f"/v1/workflows/{workflow_id}/versions").json()
    assert [item["number"] for item in versions] == [2, 1]
    # Version 1 vẫn mang đúng IR lúc nó được publish (§2.3).
    assert nguoi.get(f"/v1/workflows/{workflow_id}/versions/1").json()["ir"] == first_ir


def test_publish_draft_khong_hop_le_bi_tu_choi(client: TestClient) -> None:
    nguoi = Nguoi(client)
    workflow_id = _create(nguoi)
    ir = _example("kb1-slack-ghi")
    ir["nodes"][1]["tool_version"] = "9.9"
    nguoi.put(f"/v1/workflows/{workflow_id}/draft", json={"ir": ir})

    refused = nguoi.post(f"/v1/workflows/{workflow_id}/publish")

    assert refused.status_code == 422, refused.text
    codes = [issue["code"] for issue in refused.json()["error"]["details"]["issues"]]
    assert "tool_khong_ton_tai" in codes
    assert nguoi.get(f"/v1/workflows/{workflow_id}").json()["current_version_id"] is None


def test_workflow_khong_mang_connection(client: TestClient) -> None:
    """Connection chọn theo lần chạy; IR v2 không còn chỗ cho nó."""
    nguoi = Nguoi(client)
    workflow_id = _create(nguoi)
    ir = _example("kb1-slack-ghi") | {"connections": {"slack": "conn_slackvidu"}}
    nguoi.put(f"/v1/workflows/{workflow_id}/draft", json={"ir": ir})

    refused = nguoi.post(f"/v1/workflows/{workflow_id}/publish")

    assert refused.status_code == 422, refused.text
    assert "connections" in refused.text


# ─── Ranh giới ───────────────────────────────────────────────────────────────


def test_khong_thay_workflow_cua_to_chuc_khac(client: TestClient) -> None:
    chu = Nguoi(client, "chu")
    workflow_id = _create(chu)
    nguoi_la = Nguoi(client, "nguoi-la")

    assert nguoi_la.get("/v1/workflows").json() == []
    # Cùng thông điệp với "không có quyền", để không lộ id đó có thật.
    assert nguoi_la.get(f"/v1/workflows/{workflow_id}").status_code == 403


def test_thieu_token_thi_bi_tu_choi(client: TestClient) -> None:
    assert client.get("/v1/workflows").status_code in (401, 403)


def test_luu_tru_roi_dua_lai_duoc_va_khong_sua_duoc_khi_dang_luu_tru(
    client: TestClient,
) -> None:
    nguoi = Nguoi(client)
    workflow_id = _create(nguoi)

    archived = nguoi.post(f"/v1/workflows/{workflow_id}/archive").json()
    assert archived["archived_at"] is not None

    refused = nguoi.put(
        f"/v1/workflows/{workflow_id}/draft", json={"ir": _example("kb1-slack-ghi")}
    )
    assert refused.status_code == 422

    back = nguoi.post(f"/v1/workflows/{workflow_id}/unarchive").json()
    assert back["archived_at"] is None
