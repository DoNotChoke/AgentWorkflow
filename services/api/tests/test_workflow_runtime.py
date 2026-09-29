"""`PlanLoader`: version đã publish → thứ executor chạy được. Không hạ tầng."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.core.errors import WorkflowExecutionError
from app.domains.integration.infrastructure.catalog_view import tool_rows
from app.domains.workflow.application.runtime import PlanLoader
from app.domains.workflow.domain.entities import WorkflowVersion, fingerprint
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
