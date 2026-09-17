"""Sổ idempotency (§15.1), chạy trên PostgreSQL thật.

Chạy thật vì thứ đáng kiểm là `INSERT ... ON CONFLICT DO NOTHING` và các
`UPDATE ... WHERE` compare-and-set — chỗ tranh chấp được giải quyết. Giả nó đi
thì test chỉ còn khẳng định lại chính đoạn code đang viết, và bỏ sót đúng cái
nó tồn tại để bảo vệ.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from conftest import infra_unavailable
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import dispose_engine, init_engine, session_scope
from app.core.errors import ErrorCategory, ValidationError
from app.domains.integration.domain.idempotency import (
    ClaimDecision,
    ClaimResult,
    IdempotencyClaim,
    IdempotencyRecord,
    InvocationStatus,
    decide_claim,
    ensure_key_present,
    ensure_same_operation,
    fingerprint,
)
from app.domains.integration.infrastructure.repositories import SqlAlchemyIdempotencyStore

TENANT = "org_idem"
OTHER_TENANT = "org_khac"

STORE = SqlAlchemyIdempotencyStore()


@pytest.fixture
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
    token: str | None = None,
    lease_seconds: float = 90,
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
        claim_token=token or f"tok_{uuid.uuid4().hex}",
        lease_expires_at=now + timedelta(seconds=lease_seconds),
    )


async def claim(store_claim: IdempotencyClaim) -> ClaimResult:
    return await STORE.claim(store_claim)


def new_key() -> str:
    return f"idm_{uuid.uuid4().hex}"


async def _raw_status(key: str) -> str | None:
    async with session_scope() as session:
        row = await session.execute(
            text(
                "SELECT status FROM integration.tool_invocation_idempotency"
                " WHERE tenant_id = :t AND idempotency_key = :k"
            ),
            {"t": TENANT, "k": key},
        )
        found = row.scalar_one_or_none()
    return None if found is None else str(found)


# ─── Giành quyền chạy ────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.usefixtures("engine")
async def test_lan_dau_giu_duoc_cho_va_ghi_in_flight() -> None:
    mine = claim_for(new_key())
    result = await claim(mine)

    assert result.fresh is True
    assert result.record.status is InvocationStatus.IN_FLIGHT
    assert result.record.claim_token == mine.claim_token
    assert result.record.lease_expires_at is not None


@pytest.mark.integration
@pytest.mark.usefixtures("engine")
async def test_lan_thu_hai_khong_giu_duoc_cho() -> None:
    key = new_key()
    first = claim_for(key)
    await claim(first)

    second = await claim(claim_for(key))

    assert second.fresh is False
    assert second.record.status is InvocationStatus.IN_FLIGHT
    assert second.record.claim_token == first.claim_token


@pytest.mark.integration
@pytest.mark.usefixtures("engine")
async def test_hai_lan_giu_cho_dong_thoi_chi_mot_ben_thang() -> None:
    """Chỗ mà `ON CONFLICT DO NOTHING` tồn tại để giải quyết."""
    key = new_key()

    left, right = await asyncio.gather(claim(claim_for(key)), claim(claim_for(key)))

    assert [left.fresh, right.fresh].count(True) == 1


@pytest.mark.integration
@pytest.mark.usefixtures("engine")
async def test_claim_da_commit_ngay_khong_doi_request() -> None:
    """Sổ không đi theo transaction của request: đọc bằng một session khác là thấy."""
    key = new_key()

    await claim(claim_for(key))

    assert await _raw_status(key) == "in_flight"


# ─── Chốt kết quả ────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.usefixtures("engine")
async def test_sau_khi_hoan_tat_thi_phat_lai_duoc_ket_qua_cu() -> None:
    key = new_key()
    mine = claim_for(key)
    await claim(mine)

    settled = await STORE.complete(
        tenant_id=TENANT,
        key=key,
        claim_token=mine.claim_token,
        result={"message_ts": "1712345678.000100"},
        provider_reference="C123:1712345678.000100",
        attempts=1,
        at=datetime.now(UTC),
    )

    replay = await claim(claim_for(key))

    assert settled is True
    assert replay.fresh is False
    assert replay.record.replayable is True
    assert replay.record.result == {"message_ts": "1712345678.000100"}
    assert replay.record.provider_reference == "C123:1712345678.000100"


@pytest.mark.integration
@pytest.mark.usefixtures("engine")
async def test_that_bai_duoc_ghi_lai_va_khong_phat_lai_duoc() -> None:
    """Một lần hỏng không được trả về như thể nó đã thành công."""
    key = new_key()
    mine = claim_for(key)
    await claim(mine)

    await STORE.fail(
        tenant_id=TENANT,
        key=key,
        claim_token=mine.claim_token,
        category=ErrorCategory.VALIDATION,
        attempts=1,
        at=datetime.now(UTC),
        detail="Slack trả channel_not_found",
    )

    record = (await claim(claim_for(key))).record

    assert record.status is InvocationStatus.FAILED
    assert record.replayable is False
    assert record.error_category is ErrorCategory.VALIDATION


@pytest.mark.integration
@pytest.mark.usefixtures("engine")
async def test_khong_ro_duoc_ghi_lai() -> None:
    key = new_key()
    mine = claim_for(key)
    await claim(mine)

    settled = await STORE.mark_unknown(
        tenant_id=TENANT,
        key=key,
        claim_token=mine.claim_token,
        category=ErrorCategory.OUTCOME_UNKNOWN,
        attempts=1,
        at=datetime.now(UTC),
    )

    assert settled is True
    assert await _raw_status(key) == "unknown"


@pytest.mark.integration
@pytest.mark.usefixtures("engine")
async def test_chot_sai_token_khong_ghi_de() -> None:
    key = new_key()
    await claim(claim_for(key))

    settled = await STORE.complete(
        tenant_id=TENANT,
        key=key,
        claim_token="tok_ke_mao_danh",
        result={},
        provider_reference=None,
        attempts=1,
        at=datetime.now(UTC),
    )

    assert settled is False
    assert await _raw_status(key) == "in_flight"


@pytest.mark.integration
@pytest.mark.usefixtures("engine")
async def test_da_xong_thi_khong_the_bi_danh_dau_that_bai() -> None:
    key = new_key()
    mine = claim_for(key)
    await claim(mine)
    now = datetime.now(UTC)
    await STORE.complete(
        tenant_id=TENANT,
        key=key,
        claim_token=mine.claim_token,
        result={},
        provider_reference=None,
        attempts=1,
        at=now,
    )

    settled = await STORE.fail(
        tenant_id=TENANT,
        key=key,
        claim_token=mine.claim_token,
        category=ErrorCategory.INTERNAL,
        attempts=1,
        at=now,
    )

    assert settled is False
    assert await _raw_status(key) == "succeeded"


@pytest.mark.integration
@pytest.mark.usefixtures("engine")
async def test_thanh_cong_ghi_de_duoc_khong_ro_cua_chinh_minh() -> None:
    """Lease lỡ hết và hàng bị đánh dấu `unknown` — nhưng provider đã trả thành
    công, và đó là sự thật đúng hơn."""
    key = new_key()
    mine = claim_for(key, lease_seconds=-1)
    await claim(mine)
    assert await STORE.abandon_stale(tenant_id=TENANT, key=key, at=datetime.now(UTC))

    settled = await STORE.complete(
        tenant_id=TENANT,
        key=key,
        claim_token=mine.claim_token,
        result={"ok": True},
        provider_reference=None,
        attempts=1,
        at=datetime.now(UTC),
    )

    assert settled is True
    assert await _raw_status(key) == "succeeded"


# ─── Tiếp quản và bỏ dở ──────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.usefixtures("engine")
async def test_con_lease_thi_khong_tiep_quan_duoc() -> None:
    key = new_key()
    await claim(claim_for(key))
    now = datetime.now(UTC)

    taken = await STORE.take_over(
        tenant_id=TENANT,
        key=key,
        expected=InvocationStatus.IN_FLIGHT,
        claim_token="tok_moi",
        lease_expires_at=now + timedelta(seconds=90),
        at=now,
    )

    assert taken is None
    assert await STORE.abandon_stale(tenant_id=TENANT, key=key, at=now) is False


@pytest.mark.integration
@pytest.mark.usefixtures("engine")
async def test_het_lease_thi_tiep_quan_duoc_va_token_cu_mat_quyen() -> None:
    key = new_key()
    old = claim_for(key, lease_seconds=-1)
    await claim(old)
    now = datetime.now(UTC)

    taken = await STORE.take_over(
        tenant_id=TENANT,
        key=key,
        expected=InvocationStatus.IN_FLIGHT,
        claim_token="tok_moi",
        lease_expires_at=now + timedelta(seconds=90),
        at=now,
    )
    late = await STORE.complete(
        tenant_id=TENANT,
        key=key,
        claim_token=old.claim_token,
        result={},
        provider_reference=None,
        attempts=1,
        at=now,
    )

    assert taken is not None
    assert taken.claim_token == "tok_moi"
    assert late is False


@pytest.mark.integration
@pytest.mark.usefixtures("engine")
async def test_hai_ben_cung_tiep_quan_hang_that_bai_chi_mot_ben_thang() -> None:
    key = new_key()
    mine = claim_for(key)
    await claim(mine)
    now = datetime.now(UTC)
    await STORE.fail(
        tenant_id=TENANT,
        key=key,
        claim_token=mine.claim_token,
        category=ErrorCategory.VALIDATION,
        attempts=1,
        at=now,
    )

    async def take(token: str) -> object:
        return await STORE.take_over(
            tenant_id=TENANT,
            key=key,
            expected=InvocationStatus.FAILED,
            claim_token=token,
            lease_expires_at=now + timedelta(seconds=90),
            at=now,
        )

    left, right = await asyncio.gather(take("tok_trai"), take("tok_phai"))

    assert [left is not None, right is not None].count(True) == 1


@pytest.mark.integration
@pytest.mark.usefixtures("engine")
async def test_bo_do_danh_dau_khong_ro() -> None:
    key = new_key()
    await claim(claim_for(key, lease_seconds=-1))

    abandoned = await STORE.abandon_stale(tenant_id=TENANT, key=key, at=datetime.now(UTC))

    record = await STORE.get(tenant_id=TENANT, key=key)
    assert abandoned is True
    assert record is not None
    assert record.status is InvocationStatus.UNKNOWN
    assert record.error_category is ErrorCategory.OUTCOME_UNKNOWN


# ─── Cách ly theo tenant (§15.1) ─────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.usefixtures("engine")
async def test_cung_mot_khoa_o_hai_tenant_la_hai_ban_ghi() -> None:
    key = new_key()

    mine = await claim(claim_for(key))
    theirs = await claim(claim_for(key, tenant_id=OTHER_TENANT))

    assert mine.fresh is True
    assert theirs.fresh is True


@pytest.mark.integration
@pytest.mark.usefixtures("engine")
async def test_khong_doc_duoc_ban_ghi_cua_tenant_khac() -> None:
    key = new_key()
    await claim(claim_for(key, tenant_id=OTHER_TENANT))

    assert await STORE.get(tenant_id=TENANT, key=key) is None


# ─── Vân tay ─────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.usefixtures("engine")
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


# ─── Bảng quyết định khi khoá đã có người dùng ───────────────────────────────

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
LIVE = NOW + timedelta(seconds=30)
DEAD = NOW - timedelta(seconds=1)


def _row(status: InvocationStatus, lease: datetime | None = LIVE) -> IdempotencyRecord:
    base = IdempotencyRecord(
        tenant_id=TENANT,
        key="idm_1",
        workspace_id="ws_1",
        connection_id="conn_1",
        tool_id="demo.ping",
        tool_version="1.0",
        request_fingerprint="",
        status=status,
        attempts=1,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(days=7),
    )
    return replace(base, claim_token="tok_1", lease_expires_at=lease)


@pytest.mark.parametrize(
    ("record", "mutates", "expected"),
    [
        (_row(InvocationStatus.SUCCEEDED), False, ClaimDecision.REPLAY),
        (_row(InvocationStatus.SUCCEEDED), True, ClaimDecision.REPLAY),
        (_row(InvocationStatus.IN_FLIGHT), False, ClaimDecision.IN_PROGRESS),
        (_row(InvocationStatus.IN_FLIGHT), True, ClaimDecision.IN_PROGRESS),
        (_row(InvocationStatus.IN_FLIGHT, DEAD), False, ClaimDecision.TAKE_OVER),
        (_row(InvocationStatus.IN_FLIGHT, DEAD), True, ClaimDecision.MARK_UNKNOWN),
        (_row(InvocationStatus.IN_FLIGHT, None), False, ClaimDecision.TAKE_OVER),
        (_row(InvocationStatus.IN_FLIGHT, None), True, ClaimDecision.MARK_UNKNOWN),
        (_row(InvocationStatus.FAILED), False, ClaimDecision.TAKE_OVER),
        (_row(InvocationStatus.FAILED), True, ClaimDecision.TAKE_OVER),
        (_row(InvocationStatus.UNKNOWN), False, ClaimDecision.TAKE_OVER),
        (_row(InvocationStatus.UNKNOWN), True, ClaimDecision.UNKNOWN),
    ],
)
def test_bang_quyet_dinh(record: IdempotencyRecord, mutates: bool, expected: ClaimDecision) -> None:
    assert decide_claim(record, mutates=mutates, now=NOW) is expected


def test_lease_dung_moc_la_da_het() -> None:
    assert _row(InvocationStatus.IN_FLIGHT, NOW).lease_expired(NOW) is True
