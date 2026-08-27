"""Sổ idempotency (§15.1), chạy trên PostgreSQL thật.

Chạy thật vì thứ đáng kiểm là `INSERT ... ON CONFLICT DO NOTHING` — chỗ tranh
chấp được giải quyết. Giả nó đi thì test chỉ còn khẳng định lại chính đoạn code
đang viết, và bỏ sót đúng cái nó tồn tại để bảo vệ.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from conftest import infra_unavailable
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import dispose_engine, init_engine, session_scope
from app.core.errors import ErrorCategory, ValidationError
from app.domains.integration.domain.idempotency import (
    ClaimResult,
    IdempotencyClaim,
    InvocationStatus,
    ensure_key_present,
    ensure_same_operation,
    fingerprint,
)
from app.domains.integration.infrastructure.repositories import SqlAlchemyIdempotencyStore

pytestmark = pytest.mark.integration

TENANT = "org_idem"
OTHER_TENANT = "org_khac"


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
            text("DELETE FROM integration.tool_invocation_idempotency WHERE tenant_id = ANY(:ids)"),
            {"ids": [TENANT, OTHER_TENANT]},
        )
    await dispose_engine()


def claim_for(
    key: str,
    *,
    tenant_id: str = TENANT,
    payload: dict[str, object] | None = None,
    tool_id: str = "demo.ping",
) -> IdempotencyClaim:
    now = datetime.now(UTC)
    return IdempotencyClaim(
        tenant_id=tenant_id,
        workspace_id="ws_1",
        connection_id="conn_1",
        tool_id=tool_id,
        tool_version="1.0",
        key=key,
        request_fingerprint=fingerprint(payload or {"text": "xin chào"}),
        expires_at=now + timedelta(days=7),
        at=now,
    )


async def claim(store_claim: IdempotencyClaim) -> ClaimResult:
    async with session_scope() as session:
        return await SqlAlchemyIdempotencyStore(session).claim(store_claim)


def new_key() -> str:
    return f"idm_{uuid.uuid4().hex}"


# ─── Giành quyền chạy ────────────────────────────────────────────────────────


async def test_lan_dau_giu_duoc_cho_va_ghi_in_flight() -> None:
    result = await claim(claim_for(new_key()))

    assert result.fresh is True
    assert result.record.status is InvocationStatus.IN_FLIGHT
    assert result.record.replayable is False


async def test_lan_thu_hai_khong_giu_duoc_cho() -> None:
    key = new_key()
    await claim(claim_for(key))

    second = await claim(claim_for(key))

    assert second.fresh is False
    assert second.record.status is InvocationStatus.IN_FLIGHT


async def test_hai_lan_giu_cho_dong_thoi_chi_mot_ben_thang() -> None:
    """Chỗ mà `ON CONFLICT DO NOTHING` tồn tại để giải quyết.

    Hai transaction độc lập chạy song song, không phối hợp gì với nhau. Nếu là
    đọc-rồi-ghi thì cả hai đều nghĩ mình là bên đầu tiên và provider bị gọi hai
    lần.

    Mỗi bên phải có `session_scope` **riêng và tự commit**. Giữ cả hai
    transaction mở cùng lúc thì bên thứ hai chờ khoá của bên thứ nhất, mà bên
    thứ nhất lại chỉ commit sau khi bên thứ hai trả về — deadlock, không phải
    race.
    """
    key = new_key()

    left, right = await asyncio.gather(claim(claim_for(key)), claim(claim_for(key)))

    assert [left.fresh, right.fresh].count(True) == 1


# ─── Phát lại ────────────────────────────────────────────────────────────────


async def test_sau_khi_hoan_tat_thi_phat_lai_duoc_ket_qua_cu() -> None:
    key = new_key()
    await claim(claim_for(key))

    async with session_scope() as session:
        await SqlAlchemyIdempotencyStore(session).complete(
            tenant_id=TENANT,
            key=key,
            result={"message_ts": "1712345678.000100"},
            provider_reference="C123:1712345678.000100",
            attempts=1,
            at=datetime.now(UTC),
        )

    replay = await claim(claim_for(key))

    assert replay.fresh is False
    assert replay.record.replayable is True
    assert replay.record.result == {"message_ts": "1712345678.000100"}
    assert replay.record.provider_reference == "C123:1712345678.000100"


async def test_that_bai_duoc_ghi_lai_va_khong_phat_lai_duoc() -> None:
    """Một lần hỏng không được trả về như thể nó đã thành công."""
    key = new_key()
    await claim(claim_for(key))

    async with session_scope() as session:
        await SqlAlchemyIdempotencyStore(session).fail(
            tenant_id=TENANT,
            key=key,
            category=ErrorCategory.PROVIDER_UNAVAILABLE,
            attempts=3,
            at=datetime.now(UTC),
            detail="Slack trả 503",
        )

    record = (await claim(claim_for(key))).record

    assert record.status is InvocationStatus.FAILED
    assert record.replayable is False
    assert record.error_category is ErrorCategory.PROVIDER_UNAVAILABLE
    assert record.attempts == 3


# ─── Cách ly theo tenant (§15.1) ─────────────────────────────────────────────


async def test_cung_mot_khoa_o_hai_tenant_la_hai_ban_ghi() -> None:
    key = new_key()

    mine = await claim(claim_for(key))
    theirs = await claim(claim_for(key, tenant_id=OTHER_TENANT))

    assert mine.fresh is True
    assert theirs.fresh is True


async def test_khong_doc_duoc_ban_ghi_cua_tenant_khac() -> None:
    key = new_key()
    await claim(claim_for(key, tenant_id=OTHER_TENANT))

    async with session_scope() as session:
        found = await SqlAlchemyIdempotencyStore(session).get(tenant_id=TENANT, key=key)

    assert found is None


# ─── Vân tay ─────────────────────────────────────────────────────────────────


async def test_dung_lai_khoa_cho_input_khac_bi_tu_choi() -> None:
    """Trả về kết quả cũ ở đây còn tệ hơn báo lỗi.

    Người gọi sẽ tin rằng thao tác **mới** đã chạy, trong khi thứ họ nhận là kết
    quả của một thao tác khác.
    """
    key = new_key()
    await claim(claim_for(key, payload={"text": "một"}))

    record = (await claim(claim_for(key, payload={"text": "hai"}))).record

    with pytest.raises(ValidationError, match="thao tác khác"):
        ensure_same_operation(record, request_fingerprint=fingerprint({"text": "hai"}))


def test_van_tay_khong_phu_thuoc_thu_tu_khoa() -> None:
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})


def test_van_tay_doi_khi_gia_tri_doi() -> None:
    assert fingerprint({"text": "một"}) != fingerprint({"text": "hai"})


def test_van_tay_khong_phu_thuoc_cach_escape_unicode() -> None:
    """Tiếng Việt không được nở thành escape sequence rồi đổi vân tay."""
    assert len(fingerprint({"text": "xin chào"})) == 64


# ─── Bắt buộc có khoá ────────────────────────────────────────────────────────


def test_tool_bat_buoc_idempotency_ma_thieu_khoa_thi_tu_choi() -> None:
    with pytest.raises(ValidationError, match="bắt buộc"):
        ensure_key_present(None, tool_id="slack.post_message", required=True)


def test_tool_khong_bat_buoc_thi_thieu_khoa_van_chay() -> None:
    assert ensure_key_present(None, tool_id="slack.list_channels", required=False) is None


def test_co_khoa_thi_luon_dung_khoa_do() -> None:
    assert ensure_key_present("idm_1", tool_id="slack.post_message", required=True) == "idm_1"
