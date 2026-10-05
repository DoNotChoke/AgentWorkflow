"""Phiên biên dịch, và ý đồ của draft và version, trên PostgreSQL thật.

Chạy thật vì thứ đáng kiểm nằm ở database: khoá lạc quan theo `revision`, index duy nhất
một phần cho *mỗi người một phiên đang mở*, và JSONB trả lại đúng thứ đã ghi.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from conftest import infra_unavailable
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.db import dispose_engine, init_engine, session_scope
from app.core.errors import ValidationError
from app.domains.workflow.domain.compiler.session import (
    CompileSession,
    MessageKind,
    SessionMessage,
    SessionStatus,
    abandoned,
    assumptions_built,
    confirmed,
)
from app.domains.workflow.domain.entities import (
    Workflow,
    WorkflowDraft,
    WorkflowVersion,
    fingerprint,
)
from app.domains.workflow.infrastructure.compile_repositories import (
    SqlAlchemyCompileSessionRepository,
)
from app.domains.workflow.infrastructure.repositories import SqlAlchemyWorkflowRepository

pytestmark = pytest.mark.integration

TENANT = "org_compile_repo"
OTHER_TENANT = "org_compile_repo_khac"
WORKSPACE = "ws_compile_repo"
USER = "usr_compile_repo"
NOW = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)

INTENT = {
    "nhiem_vu": "Đăng một lời chào vào kênh test.",
    "thay_doi": ["nội dung lời chào"],
    "co_dinh": ["kênh test"],
    "dieu_kien": [],
    "khong_thuoc_nhiem_vu": [],
}
IR = {
    "schema_version": "2.0",
    "nodes": [
        {
            "id": "t",
            "type": "trigger",
            "trigger": "manual",
            "input_schema": {"type": "object", "properties": {"noi_dung": {"type": "string"}}},
        }
    ],
    "edges": [],
}


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
        ids = [TENANT, OTHER_TENANT]
        for table in ("compile_sessions", "workflow_versions", "workflow_drafts", "workflows"):
            await session.execute(
                text(f"DELETE FROM workflow.{table} WHERE tenant_id = ANY(:ids)"),  # noqa: S608
                {"ids": ids},
            )
    await dispose_engine()


def make_session(*, conversation: str | None = None, tenant: str = TENANT) -> CompileSession:
    return CompileSession(
        id=f"cps_{uuid.uuid4().hex}",
        tenant_id=tenant,
        workspace_id=WORKSPACE,
        conversation_id=conversation or f"cnv_{uuid.uuid4().hex}",
        created_by=USER,
        trace_ids=("trc_1", "trc_2"),
        source_sha256="a" * 64,
        status=SessionStatus.AWAITING_INTENT,
        revision=1,
        messages=(SessionMessage(MessageKind.INTENT, "**Nhiệm vụ.** Đăng một lời chào.", NOW),),
        question_set_version="1",
        prompt_version="1",
        intent_model="gpt-6.1-sol",
        answer_model="gpt-6.1-sol",
        created_at=NOW,
        updated_at=NOW,
        intent=INTENT,
    )


async def _add(session: CompileSession) -> None:
    async with session_scope() as db:
        await SqlAlchemyCompileSessionRepository(db).add(session)


async def _get(session: CompileSession, tenant: str = TENANT) -> CompileSession | None:
    async with session_scope() as db:
        return await SqlAlchemyCompileSessionRepository(db).get(
            tenant_id=tenant, session_id=session.id
        )


async def _save(session: CompileSession, *, expected: int) -> None:
    async with session_scope() as db:
        await SqlAlchemyCompileSessionRepository(db).save(session, expected_revision=expected)


def _built(session: CompileSession) -> CompileSession:
    return assumptions_built(
        session,
        said="kênh cũng là đầu vào",
        answers={"ten": "Chào kênh", "buoc": {"l1.b1": {"chon": "giu"}}},
        proposal={"name": "Chào kênh", "is_ready": True, "warnings": []},
        ir=IR,
        message="# Chào kênh",
        at=NOW + timedelta(minutes=1),
    )


# ─── Phiên ───────────────────────────────────────────────────────────────────


async def test_phien_luu_roi_doc_lai_nguyen_ven() -> None:
    session = make_session()
    await _add(session)

    assert await _get(session) == session


async def test_phien_cua_tenant_khac_trong_nhu_khong_ton_tai() -> None:
    session = make_session()
    await _add(session)

    assert await _get(session, tenant=OTHER_TENANT) is None


async def test_ghi_de_giu_ca_doan_chat_va_ir() -> None:
    session = make_session()
    await _add(session)
    built = _built(session)

    await _save(built, expected=session.revision)

    found = await _get(session)
    assert found == built
    assert found is not None
    assert [message.kind for message in found.messages] == [
        MessageKind.INTENT,
        MessageKind.USER,
        MessageKind.ASSUMPTIONS,
    ]
    assert found.ir == IR


async def test_ghi_tren_mot_ban_cu_bi_tu_choi_va_khong_doi_gi() -> None:
    session = make_session()
    await _add(session)
    await _save(_built(session), expected=session.revision)

    with pytest.raises(ValidationError, match="đổi ở nơi khác"):
        await _save(abandoned(session, at=NOW), expected=session.revision)

    found = await _get(session)
    assert found is not None
    assert found.status is SessionStatus.AWAITING_CONFIRMATION


async def test_moi_nguoi_mot_phien_dang_mo_tren_mot_conversation() -> None:
    session = make_session()
    await _add(session)

    with pytest.raises(IntegrityError):
        await _add(make_session(conversation=session.conversation_id))


async def test_phien_da_dong_khong_chan_phien_moi() -> None:
    session = make_session()
    await _add(session)
    await _save(abandoned(session, at=NOW), expected=session.revision)
    moi = make_session(conversation=session.conversation_id)

    await _add(moi)

    async with session_scope() as db:
        mo = await SqlAlchemyCompileSessionRepository(db).list_open(
            tenant_id=TENANT, conversation_id=session.conversation_id, created_by=USER
        )
    assert [found.id for found in mo] == [moi.id]


async def test_phien_da_bien_dich_giu_workflow_no_sinh_ra() -> None:
    session = make_session()
    await _add(session)
    built = _built(session)
    await _save(built, expected=session.revision)
    done = confirmed(built, workflow_id="wf_ket_qua", at=NOW + timedelta(minutes=2))

    await _save(done, expected=built.revision)

    found = await _get(session)
    assert found is not None
    assert (found.status, found.workflow_id) == (SessionStatus.COMPILED, "wf_ket_qua")


# ─── Ý đồ đi cùng draft và version ───────────────────────────────────────────


async def test_draft_va_version_giu_y_do_va_phien_da_sinh_ra_chung() -> None:
    workflow = Workflow(
        id=f"wf_{uuid.uuid4().hex}",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        name="Chào kênh",
        created_by=USER,
        created_at=NOW,
        updated_at=NOW,
    )
    draft = WorkflowDraft(
        workflow_id=workflow.id,
        tenant_id=TENANT,
        ir=IR,
        updated_at=NOW,
        updated_by=USER,
        intent=INTENT,
        compiled_from="cps_nguon",
    )
    version = WorkflowVersion(
        id=f"wfv_{uuid.uuid4().hex}",
        workflow_id=workflow.id,
        tenant_id=TENANT,
        number=1,
        ir=IR,
        ir_sha256=fingerprint(IR),
        published_at=NOW,
        published_by=USER,
        intent=INTENT,
        compiled_from="cps_nguon",
    )
    async with session_scope() as db:
        repository = SqlAlchemyWorkflowRepository(db)
        await repository.add(workflow)
        await repository.save_draft(draft)
        await repository.add_version(version)

    async with session_scope() as db:
        repository = SqlAlchemyWorkflowRepository(db)
        assert await repository.get_draft(tenant_id=TENANT, workflow_id=workflow.id) == draft
        assert (
            await repository.get_version(tenant_id=TENANT, workflow_id=workflow.id, number=1)
            == version
        )


async def test_luu_de_draft_khong_y_do_thi_y_do_cu_bi_xoa() -> None:
    """Repository ghi đúng thứ được đưa. Giữ ý đồ qua một lần sửa là việc của service."""
    workflow = Workflow(
        id=f"wf_{uuid.uuid4().hex}",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        name="Chào kênh",
        created_by=USER,
        created_at=NOW,
        updated_at=NOW,
    )
    draft = WorkflowDraft(
        workflow_id=workflow.id,
        tenant_id=TENANT,
        ir=IR,
        updated_at=NOW,
        updated_by=USER,
        intent=INTENT,
    )
    async with session_scope() as db:
        repository = SqlAlchemyWorkflowRepository(db)
        await repository.add(workflow)
        await repository.save_draft(draft)
        await repository.save_draft(replace(draft, intent=None))

    async with session_scope() as db:
        found = await SqlAlchemyWorkflowRepository(db).get_draft(
            tenant_id=TENANT, workflow_id=workflow.id
        )
    assert found is not None
    assert found.intent is None
