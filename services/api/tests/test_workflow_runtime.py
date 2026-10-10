"""`PlanLoader`: version đã publish → thứ executor chạy được. Không hạ tầng."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.core.errors import ConnectionChoiceRequiredError, ValidationError, WorkflowExecutionError
from app.domains.integration.infrastructure.catalog_view import tool_rows
from app.domains.workflow.application.ports import BoundConnection
from app.domains.workflow.application.runtime import PlanLoader, bind_run_connections
from app.domains.workflow.domain.entities import WorkflowVersion, fingerprint
from app.domains.workflow.domain.ir import parse
from app.domains.workflow.infrastructure.catalog import RowToolCatalog

EXAMPLES = Path(__file__).resolve().parents[3] / "packages" / "schemas" / "workflow" / "examples"
CATALOG = RowToolCatalog(tool_rows())


def _example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


class _Versions:
    def __init__(self, *versions: WorkflowVersion) -> None:
        self._versions = {version.id: version for version in versions}

    async def get_version_by_id(self, *, tenant_id: str, version_id: str) -> WorkflowVersion | None:
        found = self._versions.get(version_id)
        return found if found is not None and found.tenant_id == tenant_id else None


def _version(ir: dict[str, Any], *, tenant: str = "org_1") -> WorkflowVersion:
    return WorkflowVersion(
        id="wfv_1",
        workflow_id="wf_1",
        tenant_id=tenant,
        number=1,
        ir=ir,
        ir_sha256=fingerprint(ir),
        published_at=datetime(2026, 9, 29, tzinfo=UTC),
        published_by="usr_1",
    )


def _loader(*versions: WorkflowVersion) -> PlanLoader:
    return PlanLoader(repository=_Versions(*versions), catalog=CATALOG)  # type: ignore[arg-type]


async def test_tool_ghi_thi_vao_bang_mutating() -> None:
    plan = await _loader(_version(_example("kb1-slack-ghi"))).load(
        tenant_id="org_1", workflow_version_id="wfv_1"
    )
    assert plan.mutating_tools == ["slack.post_message@1.0"]
    assert plan.ir == _example("kb1-slack-ghi")


async def test_plan_mang_thoi_han_cua_tung_tool_trong_catalog() -> None:
    plan = await _loader(_version(_example("kb3-notion-doc-sau"))).load(
        tenant_id="org_1", workflow_version_id="wfv_1"
    )

    assert plan.tool_timeouts
    for key, seconds in plan.tool_timeouts.items():
        tool_id, _, version = key.partition("@")
        contract = CATALOG.find(tool_id, version)
        assert contract is not None
        assert seconds == contract.timeout_seconds > 0


async def test_tool_chi_doc_thi_khong_va_tool_trong_than_loop_cung_duoc_xet() -> None:
    plan = await _loader(_version(_example("kb3-notion-doc-sau"))).load(
        tenant_id="org_1", workflow_version_id="wfv_1"
    )
    assert plan.mutating_tools == []


async def test_tenant_khac_nhu_khong_ton_tai() -> None:
    loader = _loader(_version(_example("kb1-slack-ghi"), tenant="org_khac"))
    with pytest.raises(WorkflowExecutionError, match="Không tìm thấy"):
        await loader.load(tenant_id="org_1", workflow_version_id="wfv_1")


async def test_tool_bien_mat_khoi_catalog_sau_khi_publish() -> None:
    ir = _example("kb1-slack-ghi")
    ir["nodes"][1]["tool_version"] = "9.9"
    with pytest.raises(WorkflowExecutionError, match="không còn trong catalog"):
        await _loader(_version(ir)).load(tenant_id="org_1", workflow_version_id="wfv_1")


# ─── Connection của lần chạy ─────────────────────────────────────────────────


class _Connections:
    def __init__(self, *known: BoundConnection) -> None:
        self._known = {c.connection_id: c for c in known}

    async def describe(
        self, *, tenant_id: str, workspace_id: str, connection_id: str
    ) -> BoundConnection | None:
        return self._known.get(connection_id)


CHUNG = BoundConnection("conn_chung", "slack", True, "active", True, "usr_admin")
CUA_AN = BoundConnection("conn_an", "slack", False, "active", True, "usr_an")
CUA_BINH = BoundConnection("conn_binh", "slack", False, "active", True, "usr_binh")
HET_HAN = BoundConnection("conn_hethan", "slack", True, "expired", False, "usr_admin")
NOTION = BoundConnection("conn_notion", "notion", True, "active", True, "usr_admin")


async def _bind(
    example: str = "kb2-slack-doc-roi-ghi",
    *,
    by_node: dict[str, str] | None = None,
    by_provider: dict[str, str] | None = None,
    actor: str = "usr_an",
) -> dict[str, str]:
    return await bind_run_connections(
        parse(_example(example)),
        catalog=CATALOG,
        connections=_Connections(CHUNG, CUA_AN, CUA_BINH, HET_HAN, NOTION),
        tenant_id="org_1",
        workspace_id="ws_1",
        actor_id=actor,
        by_node=by_node or {},
        by_provider=by_provider or {},
    )


async def _bind_error(**kwargs: Any) -> ValidationError:
    with pytest.raises(ValidationError) as caught:
        await _bind(**kwargs)
    return caught.value


async def test_theo_provider_ap_cho_moi_node_cua_provider_do() -> None:
    assert await _bind(by_provider={"slack": "conn_chung"}) == {
        "kenh": "conn_chung",
        "dang": "conn_chung",
    }


async def test_hai_node_cung_provider_dung_hai_connection_khac_nhau() -> None:
    bindings = await _bind(by_provider={"slack": "conn_chung"}, by_node={"dang": "conn_an"})
    assert bindings == {"kenh": "conn_chung", "dang": "conn_an"}


async def test_node_trong_than_loop_cung_can_connection() -> None:
    bindings = await _bind("kb3-notion-doc-sau", by_provider={"notion": "conn_notion"})
    assert bindings == {"tim": "conn_notion", "con": "conn_notion", "doc_trang": "conn_notion"}


async def test_chon_duoc_tai_khoan_rieng_cua_chinh_minh() -> None:
    assert await _bind(by_provider={"slack": "conn_an"}, actor="usr_an") == {
        "kenh": "conn_an",
        "dang": "conn_an",
    }


async def test_tai_khoan_rieng_cua_nguoi_khac_trong_nhu_khong_ton_tai() -> None:
    other = await _bind_error(by_provider={"slack": "conn_binh"}, actor="usr_an")
    missing = await _bind_error(by_provider={"slack": "conn_khongco"})
    assert other.message.replace("conn_binh", "X") == missing.message.replace("conn_khongco", "X")


async def test_khong_chon_thi_buoc_chon_va_chi_ra_tung_node() -> None:
    with pytest.raises(ConnectionChoiceRequiredError) as caught:
        await _bind(by_node={"kenh": "conn_chung"})
    assert caught.value.details == {"nodes": [{"node": "dang", "provider": "slack"}]}
    assert caught.value.http_status == 409


async def test_sai_provider_va_het_han_bi_tu_choi_theo_node() -> None:
    wrong = await _bind_error(by_provider={"slack": "conn_chung"}, by_node={"dang": "conn_notion"})
    assert [p["node"] for p in wrong.details["connections"]] == ["dang"]
    assert "node dang cần slack" in wrong.message
    assert "expired" in (await _bind_error(by_provider={"slack": "conn_hethan"})).message


async def test_chon_cho_thu_workflow_khong_dung_la_loi() -> None:
    error = await _bind_error(
        by_provider={"slack": "conn_chung", "notion": "conn_notion"}, by_node={"t": "conn_chung"}
    )
    assert error.details == {"nodes": ["t"], "providers": ["notion"]}
