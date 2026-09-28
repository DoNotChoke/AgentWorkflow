"""`WorkflowConnectionReader` — chỗ domain `workflow` nhìn sang domain `connection`.

Chạy trên PostgreSQL thật vì thứ đáng kiểm là một phép so **bằng chuỗi**:
`entrypoints` không được import `domains.*.domain` (contract 5), nên adapter so
`str(found.status) == "active"` thay vì so enum. Nếu `ConnectionStatus` đổi giá
trị, hoặc `str()` của nó ra tên thay vì giá trị, thì mọi lần publish đều từ chối
kết nối hợp lệ — và không một test giả nào thấy được.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from conftest import infra_unavailable
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import dispose_engine, init_engine, session_scope
from app.domains.connection.domain.entities import (
    Connection,
    ConnectionStatus,
    SharingPolicy,
)
from app.domains.connection.infrastructure.repositories import SqlAlchemyConnectionRepository
from app.entrypoints.wiring import WorkflowConnectionReader

pytestmark = pytest.mark.integration

TENANT = "org_workflow_wiring"
OTHER_TENANT = "org_workflow_wiring_khac"
WORKSPACE = "ws_workflow_wiring"
NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
async def engine() -> AsyncIterator[None]:
    init_engine(get_settings())
    try:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        await dispose_engine()
        infra_unavailable(f"PostgreSQL chưa sẵn sàng: {type(exc).__name__}. {exc}")

    yield

    async with session_scope() as session:
        await session.execute(
            text("DELETE FROM connection.connections WHERE organization_id = ANY(:ids)"),
            {"ids": [TENANT, OTHER_TENANT]},
        )
    await dispose_engine()


async def _add(
    *,
    tenant: str = TENANT,
    workspace: str = WORKSPACE,
    sharing: SharingPolicy = SharingPolicy.WORKSPACE,
    status: ConnectionStatus = ConnectionStatus.ACTIVE,
    provider: str = "slack",
) -> Connection:
    connection = Connection(
        id=f"conn_{uuid.uuid4().hex}",
        organization_id=tenant,
        workspace_id=workspace,
        provider=provider,
        display_name="Slack — test",
        status=status,
        granted_scopes=frozenset({"chat:write"}),
        connected_by="usr_1",
        created_at=NOW,
        updated_at=NOW,
        sharing=sharing,
        owner_user_id="usr_1",
    )
    async with session_scope() as session:
        await SqlAlchemyConnectionRepository(session).add(connection)
    return connection


async def _describe(connection_id: str, *, tenant: str = TENANT, workspace: str = WORKSPACE):  # type: ignore[no-untyped-def]
    async with session_scope() as session:
        return await WorkflowConnectionReader(session).describe(
            tenant_id=tenant, workspace_id=workspace, connection_id=connection_id
        )


async def test_ket_noi_chung_va_dang_song_thi_dung_duoc() -> None:
    connection = await _add()

    found = await _describe(connection.id)

    assert found is not None
    assert found.provider == "slack"
    assert found.shared_with_workspace is True
    assert found.is_usable is True
    assert found.status == "active"


async def test_ket_noi_rieng_tu_duoc_khai_dung_la_rieng_tu() -> None:
    connection = await _add(sharing=SharingPolicy.PRIVATE)

    found = await _describe(connection.id)

    assert found is not None
    assert found.shared_with_workspace is False


@pytest.mark.parametrize(
    "status",
    [ConnectionStatus.EXPIRED, ConnectionStatus.REVOKED, ConnectionStatus.DISABLED],
)
async def test_ket_noi_khong_active_thi_khong_dung_duoc(status: ConnectionStatus) -> None:
    connection = await _add(status=status)

    found = await _describe(connection.id)

    assert found is not None
    assert found.is_usable is False
    assert found.status == str(status)


async def test_khong_doc_duoc_ket_noi_cua_tenant_khac() -> None:
    """`get_by_id` của domain `connection` không lọc tenant — §19 đòi lọc, và
    adapter là chỗ lọc."""
    connection = await _add(tenant=OTHER_TENANT)

    assert await _describe(connection.id) is None


async def test_khong_doc_duoc_ket_noi_cua_workspace_khac() -> None:
    connection = await _add(workspace="ws_khac")

    assert await _describe(connection.id) is None


async def test_ket_noi_khong_ton_tai_tra_none() -> None:
    assert await _describe("conn_khong_co") is None
