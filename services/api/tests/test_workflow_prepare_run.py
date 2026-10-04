"""`WorkflowService.prepare_run`: mọi thứ kiểm được trước khi một lần chạy tồn tại."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.core.errors import AuthorizationError, ConnectionChoiceRequiredError, ValidationError
from app.domains.integration.infrastructure.catalog_view import tool_rows
from app.domains.workflow.application.ports import BoundConnection
from app.domains.workflow.application.service import WorkflowService
from app.domains.workflow.domain.entities import Workflow, WorkflowVersion, fingerprint
from app.domains.workflow.infrastructure.catalog import RowToolCatalog

EXAMPLES = Path(__file__).resolve().parents[3] / "packages" / "schemas" / "workflow" / "examples"
NOW = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)
SLACK = BoundConnection("conn_slack", "slack", True, "active", True, "usr_admin")


def _example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


class _Workflows:
    def __init__(self) -> None:
        self.workflows: dict[str, Workflow] = {}
        self.versions: dict[str, WorkflowVersion] = {}

    async def get(self, *, tenant_id: str, workflow_id: str) -> Workflow | None:
        found = self.workflows.get(workflow_id)
        return found if found is not None and found.tenant_id == tenant_id else None

    async def get_version(
        self, *, tenant_id: str, workflow_id: str, number: int
    ) -> WorkflowVersion | None:
        for version in self.versions.values():
            if (version.tenant_id, version.workflow_id, version.number) == (
                tenant_id,
                workflow_id,
                number,
            ):
                return version
        return None

    async def get_version_by_id(self, *, tenant_id: str, version_id: str) -> WorkflowVersion | None:
        found = self.versions.get(version_id)
        return found if found is not None and found.tenant_id == tenant_id else None


class _Connections:
    async def describe(
        self, *, tenant_id: str, workspace_id: str, connection_id: str
    ) -> BoundConnection | None:
        return SLACK if connection_id == "conn_slack" else None


def _setup(*irs: dict[str, Any], published: bool = True) -> tuple[WorkflowService, _Workflows]:
    store = _Workflows()
    workflow = Workflow(
        id="wf_1",
        tenant_id="org_1",
        workspace_id="ws_1",
        name="Đăng tin",
        created_by="usr_1",
        created_at=NOW,
        updated_at=NOW,
    )
    for number, ir in enumerate(irs, start=1):
        version = WorkflowVersion(
            id=f"wfv_{number}",
            workflow_id="wf_1",
            tenant_id="org_1",
            number=number,
            ir=ir,
            ir_sha256=fingerprint(ir),
            published_at=NOW,
            published_by="usr_1",
        )
        store.versions[version.id] = version
        if published:
            workflow = replace(workflow, current_version_id=version.id)
    store.workflows[workflow.id] = workflow
    service = WorkflowService(
        repository=store,  # type: ignore[arg-type]
        catalog=RowToolCatalog(tool_rows()),
        connections=_Connections(),
    )
    return service, store


async def _prepare(service: WorkflowService, **overrides: Any) -> Any:
    arguments: dict[str, Any] = {
        "tenant_id": "org_1",
        "workspace_id": "ws_1",
        "actor_id": "usr_an",
        "workflow_id": "wf_1",
        "version": None,
        "inputs": {"noi_dung": "xin chào"},
        "by_node": {},
        "by_provider": {"slack": "conn_slack"},
    }
    arguments.update(overrides)
    return await service.prepare_run(**arguments)


async def test_mac_dinh_chay_version_hien_tai_kem_connection_da_chon() -> None:
    service, _ = _setup(_example("kb1-slack-ghi"), _example("kb1-slack-ghi"))
    runnable = await _prepare(service)
    assert (runnable.workflow_version_id, runnable.number) == ("wfv_2", 2)
    assert runnable.connections == {"dang": "conn_slack"}


async def test_chon_dung_so_version() -> None:
    service, _ = _setup(_example("kb1-slack-ghi"), _example("kb1-slack-ghi"))
    assert (await _prepare(service, version=1)).workflow_version_id == "wfv_1"
    with pytest.raises(ValidationError, match="không có version 7"):
        await _prepare(service, version=7)


async def test_workspace_khac_trong_nhu_khong_ton_tai() -> None:
    service, _ = _setup(_example("kb1-slack-ghi"))
    with pytest.raises(AuthorizationError):
        await _prepare(service, workspace_id="ws_khac")


async def test_chua_publish_hoac_dang_luu_tru_thi_khong_chay() -> None:
    service, _ = _setup(_example("kb1-slack-ghi"), published=False)
    with pytest.raises(ValidationError, match="chưa được publish"):
        await _prepare(service)

    service, store = _setup(_example("kb1-slack-ghi"))
    store.workflows["wf_1"] = replace(store.workflows["wf_1"], archived_at=NOW)
    with pytest.raises(ValidationError, match="lưu trữ"):
        await _prepare(service)


async def test_node_chua_ho_tro_bi_chan_truoc_khi_chay() -> None:
    service, _ = _setup(_example("kb5-re-nhanh"))
    with pytest.raises(ValidationError) as caught:
        await _prepare(service, inputs={})
    assert caught.value.details == {"nodes": ["dem", "du_nhieu"]}


async def test_input_sai_la_loi_cua_request_khong_phai_500() -> None:
    service, _ = _setup(_example("kb1-slack-ghi"))
    with pytest.raises(ValidationError) as caught:
        await _prepare(service, inputs={})
    assert caught.value.http_status == 422
    assert "noi_dung" in caught.value.message


async def test_thieu_connection_thi_buoc_chon() -> None:
    service, _ = _setup(_example("kb1-slack-ghi"))
    with pytest.raises(ConnectionChoiceRequiredError):
        await _prepare(service, by_provider={})


async def test_version_v1_da_publish_van_chay_duoc() -> None:
    v1 = _example("kb1-slack-ghi") | {"schema_version": "1.0", "connections": {"slack": "conn_x"}}
    service, _ = _setup(v1)
    assert (await _prepare(service)).connections == {"dang": "conn_slack"}
