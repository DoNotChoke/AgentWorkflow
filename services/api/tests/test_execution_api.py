"""`/v1/executions` trên PostgreSQL thật, với launcher giả — không cần Temporal."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from functools import partial
from typing import Any

import pytest
from conftest import infra_unavailable
from fastapi.testclient import TestClient
from test_workflow_api import NOW, Nguoi, _clear_rate_limits, _create, _example

from app.core.db import session_scope
from app.core.errors import ProviderUnavailableError
from app.domains.connection.domain.entities import Connection, ConnectionStatus, SharingPolicy
from app.domains.connection.infrastructure.repositories import SqlAlchemyConnectionRepository
from app.domains.execution.infrastructure.http import EXECUTION_LAUNCHER_FACTORY
from app.entrypoints.http import create_app

pytestmark = pytest.mark.integration


class FakeLauncher:
    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.fail = False

    async def start(self, **request: Any) -> None:
        if self.fail:
            raise ProviderUnavailableError("Temporal không nhận lần chạy (RPCError).")
        self.started.append(request)


@pytest.fixture
def launcher() -> FakeLauncher:
    return FakeLauncher()


@pytest.fixture
def client(launcher: FakeLauncher) -> Iterator[TestClient]:
    app = create_app()
    setattr(app.state, EXECUTION_LAUNCHER_FACTORY, lambda: launcher)
    with TestClient(app) as running:
        if running.get("/readyz").json()["checks"].get("postgres") != "ok":
            infra_unavailable("PostgreSQL chưa chạy — `docker compose up -d postgres`.")
        _clear_rate_limits(running)
        yield running


async def _add_connection(
    *, nguoi: Nguoi, owner: str, sharing: SharingPolicy, provider: str = "slack"
) -> str:
    connection = Connection(
        id=f"conn_{uuid.uuid4().hex}",
        organization_id=nguoi.organization_id,
        workspace_id=nguoi.workspace_id,
        provider=provider,
        display_name=f"{provider} — test",
        status=ConnectionStatus.ACTIVE,
        granted_scopes=frozenset({"chat:write"}),
        connected_by=owner,
        created_at=NOW,
        updated_at=NOW,
        sharing=sharing,
        owner_user_id=owner,
    )
    async with session_scope() as session:
        await SqlAlchemyConnectionRepository(session).add(connection)
    return connection.id


def _connection(
    client: TestClient, nguoi: Nguoi, *, owner: str | None = None, private: bool = False
) -> str:
    assert client.portal is not None
    sharing = SharingPolicy.PRIVATE if private else SharingPolicy.WORKSPACE
    return str(
        client.portal.call(
            partial(_add_connection, nguoi=nguoi, owner=owner or nguoi.user_id, sharing=sharing)
        )
    )


def _published(nguoi: Nguoi, example: str = "kb1-slack-ghi") -> str:
    workflow_id = _create(nguoi)
    nguoi.put(f"/v1/workflows/{workflow_id}/draft", json={"ir": _example(example)})
    published = nguoi.post(f"/v1/workflows/{workflow_id}/publish")
    assert published.status_code == 201, published.text
    return workflow_id


def _run(nguoi: Nguoi, workflow_id: str, **body: Any) -> Any:
    payload = {"workflow_id": workflow_id, "inputs": {"noi_dung": "xin chào"}} | body
    return nguoi.post("/v1/executions", json=payload)


# ─── Chạy ────────────────────────────────────────────────────────────────────


def test_chay_bang_tay_tra_202_va_duong_toi_lan_chay(
    client: TestClient, launcher: FakeLauncher
) -> None:
    nguoi = Nguoi(client)
    workflow_id = _published(nguoi)
    connection = _connection(client, nguoi)

    started = _run(nguoi, workflow_id, connections_by_provider={"slack": connection})

    assert started.status_code == 202, started.text
    execution = started.json()
    assert execution["status"] == "pending"
    assert started.headers["location"] == f"/v1/executions/{execution['id']}"
    assert launcher.started[0]["execution_id"] == execution["id"]
    assert launcher.started[0]["connections"] == {"dang": connection}

    detail = nguoi.get(f"/v1/executions/{execution['id']}").json()
    assert detail["connections"] == {"dang": connection}
    assert detail["inputs"] == {"noi_dung": "xin chào"}
    assert detail["nodes"] == []


def test_thieu_connection_thi_409_chi_ra_node(client: TestClient, launcher: FakeLauncher) -> None:
    nguoi = Nguoi(client)
    refused = _run(nguoi, _published(nguoi))

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["details"] == {"nodes": [{"node": "dang", "provider": "slack"}]}
    assert launcher.started == []


def test_input_sai_thi_422(client: TestClient) -> None:
    nguoi = Nguoi(client)
    workflow_id = _published(nguoi)
    connection = _connection(client, nguoi)

    refused = _run(nguoi, workflow_id, inputs={}, connections_by_provider={"slack": connection})

    assert refused.status_code == 422, refused.text
    assert "noi_dung" in refused.json()["error"]["message"]


def test_tai_khoan_rieng_cua_nguoi_khac_trong_nhu_khong_ton_tai(client: TestClient) -> None:
    nguoi = Nguoi(client)
    workflow_id = _published(nguoi)
    cua_toi = _connection(client, nguoi, private=True)
    cua_nguoi_khac = _connection(client, nguoi, owner="usr_nguoikhac", private=True)

    assert _run(nguoi, workflow_id, connections={"dang": cua_toi}).status_code == 202
    refused = _run(nguoi, workflow_id, connections={"dang": cua_nguoi_khac})
    assert refused.status_code == 422, refused.text
    assert "Không tìm thấy kết nối" in refused.json()["error"]["message"]


def test_temporal_khong_nhan_thi_502_va_lan_chay_ghi_failed(
    client: TestClient, launcher: FakeLauncher
) -> None:
    nguoi = Nguoi(client)
    workflow_id = _published(nguoi)
    connection = _connection(client, nguoi)
    launcher.fail = True

    refused = _run(nguoi, workflow_id, connections_by_provider={"slack": connection})

    assert refused.status_code == 502, refused.text
    execution_id = refused.json()["error"]["details"]["execution_id"]
    detail = nguoi.get(f"/v1/executions/{execution_id}").json()
    assert detail["status"] == "failed"
    assert detail["error"]["category"] == "ProviderUnavailable"


# ─── Đọc ─────────────────────────────────────────────────────────────────────


def test_liet_ke_theo_workflow_moi_nhat_truoc(client: TestClient) -> None:
    nguoi = Nguoi(client)
    workflow_id = _published(nguoi)
    connection = _connection(client, nguoi)
    ids = [
        _run(nguoi, workflow_id, connections_by_provider={"slack": connection}).json()["id"]
        for _ in range(2)
    ]

    found = nguoi.get("/v1/executions", params={"workflow_id": workflow_id}).json()

    assert [item["id"] for item in found] == list(reversed(ids))
    assert found[0]["steps"] == 0 and found[0]["input_tokens"] == 0


def test_liet_ke_ca_workspace_va_loc_theo_trang_thai(client: TestClient) -> None:
    nguoi = Nguoi(client)
    connection = _connection(client, nguoi)
    ids = [
        _run(nguoi, _published(nguoi), connections_by_provider={"slack": connection}).json()["id"]
        for _ in range(2)
    ]

    found = nguoi.get("/v1/executions").json()

    assert [item["id"] for item in found] == list(reversed(ids))
    assert nguoi.get("/v1/executions", params={"status": "failed"}).json() == []
    assert len(nguoi.get("/v1/executions", params={"status": "pending"}).json()) == 2
    assert nguoi.get("/v1/executions", params={"status": "xong"}).status_code == 422
    assert Nguoi(client, "khac").get("/v1/executions").json() == []


def test_to_chuc_khac_khong_thay_lan_chay(client: TestClient) -> None:
    nguoi = Nguoi(client)
    workflow_id = _published(nguoi)
    connection = _connection(client, nguoi)
    execution_id = _run(nguoi, workflow_id, connections_by_provider={"slack": connection}).json()[
        "id"
    ]

    nguoi_khac = Nguoi(client, "khac")
    assert nguoi_khac.get(f"/v1/executions/{execution_id}").status_code == 403
    assert nguoi_khac.get("/v1/executions", params={"workflow_id": workflow_id}).json() == []
    assert _run(nguoi_khac, workflow_id).status_code == 403


# ─── Theo dõi (SSE) ──────────────────────────────────────────────────────────


def _events(body: str) -> list[tuple[str, dict[str, Any]]]:
    import json

    events: list[tuple[str, dict[str, Any]]] = []
    name = ""
    for line in body.splitlines():
        if line.startswith("event:"):
            name = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            events.append((name, json.loads(line.removeprefix("data:").strip() or "{}")))
    return events


def test_theo_doi_lan_chay_da_ket_thuc_ra_snapshot_roi_dong(
    client: TestClient, launcher: FakeLauncher
) -> None:
    nguoi = Nguoi(client)
    workflow_id = _published(nguoi)
    connection = _connection(client, nguoi)
    launcher.fail = True
    refused = _run(nguoi, workflow_id, connections_by_provider={"slack": connection})
    execution_id = refused.json()["error"]["details"]["execution_id"]

    stream = nguoi.get(f"/v1/executions/{execution_id}/events")

    assert stream.status_code == 200, stream.text
    assert stream.headers["content-type"].startswith("text/event-stream")
    events = _events(stream.text)
    assert [name for name, _ in events] == ["snapshot", "done"]
    assert events[0][1] == nguoi.get(f"/v1/executions/{execution_id}").json()


def test_theo_doi_lan_chay_cua_to_chuc_khac_bi_tu_choi_truoc_khi_luong_mo(
    client: TestClient,
) -> None:
    nguoi = Nguoi(client)
    workflow_id = _published(nguoi)
    connection = _connection(client, nguoi)
    execution_id = _run(nguoi, workflow_id, connections_by_provider={"slack": connection}).json()[
        "id"
    ]

    refused = Nguoi(client, "khac").get(f"/v1/executions/{execution_id}/events")

    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["category"] == "AuthorizationError"
