"""HTTP + SSE của Workflow Compiler trên Agent Runtime, chạy trên PostgreSQL và Redis thật.

Hai thứ bị thay: Model Gateway (kịch bản viết sẵn) và bộ đọc nguyên liệu (một fixture
thay cho conversation thật). Mọi thứ còn lại là đường chạy thật: phân giải tenant, hai
advisor, ba tầng kiểm và sandbox, phiên và draft trong database, luồng SSE.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from conftest import infra_unavailable

os.environ.setdefault("AW_JWT_SECRET", "test-secret-khong-dung-that-nhung-du-dai-32")
os.environ.setdefault("AW_KMS_LOCAL_MASTER_KEY", "test-master-key-khong-dung-that")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

import app.entrypoints.agent as agent_entrypoint  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import session_scope  # noqa: E402
from app.core.errors import ProviderUnavailableError  # noqa: E402
from app.core.security import issue_access_token  # noqa: E402
from app.domains.agent.application.ports import (  # noqa: E402
    ModelResponse,
    ModelToolCall,
    ModelToolSpec,
    ModelUsage,
    StopReason,
)
from app.domains.tenant.application.service import TenantService  # noqa: E402
from app.domains.tenant.infrastructure.repositories import (  # noqa: E402
    SqlAlchemyMembershipRepository,
    SqlAlchemyOrganizationRepository,
    SqlAlchemyWorkspaceRepository,
)
from app.domains.workflow.application.ports import CompileSource  # noqa: E402
from app.entrypoints import compiler_wiring  # noqa: E402
from app.entrypoints.agent import create_app as create_agent_app  # noqa: E402

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent / "fixtures"
KB7 = "kb7-buoc-thua"
CONVERSATION = "cnv_nguon_bien_dich"
ORG_HEADER = "x-organization-id"
WS_HEADER = "x-workspace-id"

RAW_INTENT = {
    "nhiem_vu": "Tìm một trang Notion theo tên rồi đăng đường link của nó vào kênh test.",
    "thay_doi": ["tên trang cần tìm"],
    "co_dinh": ["kênh Slack test"],
    "dieu_kien": [],
    "khong_thuoc_nhiem_vu": ["tin thử ping", "lần tìm nhầm trang EC2"],
}


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


ANSWERS = _json(FIXTURES / "compiled" / f"{KB7}.answers.json")
IR = _json(FIXTURES / "compiled" / f"{KB7}.ir.json")


# ─── Model và nguyên liệu giả ────────────────────────────────────────────────


def _calls(name: str, payload: Mapping[str, Any]) -> ModelResponse:
    return ModelResponse(
        text="",
        tool_calls=(ModelToolCall(call_id="call_1", name=name, input=dict(payload)),),
        stop_reason=StopReason.TOOL_USE,
        usage=ModelUsage(input_tokens=10, output_tokens=5),
        model="gpt-6.1-sol",
    )


def _says(text_: str) -> ModelResponse:
    return ModelResponse(
        text=text_,
        tool_calls=(),
        stop_reason=StopReason.END_TURN,
        usage=ModelUsage(input_tokens=10, output_tokens=5),
        model="gpt-6.1-sol",
    )


INTENT = _calls("de_xuat_y_do", RAW_INTENT)
ANSWER = _calls("tra_loi", ANSWERS)


class ScriptedModelClient:
    def __init__(self, *script: ModelResponse | Exception) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        *,
        model: str,
        system: str,
        turns: Sequence[Any],
        tools: Sequence[ModelToolSpec] = (),
        max_tokens: int = 8192,
    ) -> ModelResponse:
        self.calls.append({"model": model, "tools": [tool.name for tool in tools]})
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FixtureSources:
    """Đứng thay `TraceCompileSourceReader`: conversation là kb7, của `owner`."""

    owner: Nguoi | None = None

    async def read(self, *, tenant_id: str, conversation_id: str) -> CompileSource | None:
        owner = self.owner
        if owner is None or (tenant_id, conversation_id) != (owner.organization_id, CONVERSATION):
            return None
        return CompileSource(
            document=_json(FIXTURES / "sources" / f"{KB7}.json"),
            user_id=owner.user_id,
            workspace_id=owner.workspace_id,
        )


# ─── Tenant và client ────────────────────────────────────────────────────────


class _UnusedUserDirectory:
    async def get_profiles(self, user_ids: Any) -> dict[str, Any]:
        raise NotImplementedError

    async def find_by_email(self, email: str) -> Any:
        raise NotImplementedError


async def _ping_postgres() -> None:
    async with session_scope() as session:
        await session.execute(text("SELECT 1"))


async def _provision_tenant(user_id: str, org_name: str) -> tuple[str, str]:
    async with session_scope() as session:
        service = TenantService(
            organizations=SqlAlchemyOrganizationRepository(session),
            workspaces=SqlAlchemyWorkspaceRepository(session),
            memberships=SqlAlchemyMembershipRepository(session),
            users=_UnusedUserDirectory(),
        )
        access = await service.create_organization(user_id=user_id, name=org_name)
    assert access.workspace_id is not None
    return access.organization_id, access.workspace_id


class Nguoi:
    def __init__(
        self, *, user_id: str, organization_id: str, workspace_id: str, token: str
    ) -> None:
        self.user_id = user_id
        self.organization_id = organization_id
        self.workspace_id = workspace_id
        self.token = token

    def headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self.token}",
            ORG_HEADER: self.organization_id,
            WS_HEADER: self.workspace_id,
        }


def _make_user(client: TestClient, ten: str) -> Nguoi:
    assert client.portal is not None
    user_id = f"usr_{uuid.uuid4().hex}"
    org_id, workspace_id = client.portal.call(
        _provision_tenant, user_id, f"Org {ten} {uuid.uuid4().hex[:6]}"
    )
    token, _claims = issue_access_token(get_settings(), subject=user_id)
    return Nguoi(user_id=user_id, organization_id=org_id, workspace_id=workspace_id, token=token)


class Bench:
    def __init__(self, client: TestClient, model: ScriptedModelClient, owner: Nguoi) -> None:
        self.client = client
        self.model = model
        self.owner = owner

    def stream(self, path: str, *, by: Nguoi | None = None, **body: Any) -> list[tuple[str, Any]]:
        reply = self.client.post(
            f"/v1{path}", headers=(by or self.owner).headers(), json=body or None
        )
        assert reply.status_code == 200, reply.text
        return _sse_events(reply.text)

    def open(self, *, by: Nguoi | None = None) -> list[tuple[str, Any]]:
        return self.stream(f"/conversations/{CONVERSATION}/compile-sessions", by=by)

    def post(self, session: Mapping[str, Any], action: str, *, by: Nguoi | None = None) -> Any:
        return self.client.post(
            f"/v1/compile-sessions/{session['id']}/{action}", headers=(by or self.owner).headers()
        )

    def get(self, session: Mapping[str, Any], *, by: Nguoi | None = None) -> Any:
        return self.client.get(
            f"/v1/compile-sessions/{session['id']}", headers=(by or self.owner).headers()
        )

    def query(self, statement: str, **params: Any) -> list[Any]:
        async def run() -> list[Any]:
            async with session_scope() as session:
                return list((await session.execute(text(statement), params)).all())

        assert self.client.portal is not None
        return self.client.portal.call(run)


@contextmanager
def bench(monkeypatch: pytest.MonkeyPatch, *script: ModelResponse | Exception) -> Iterator[Bench]:
    model = ScriptedModelClient(*script)
    sources = FixtureSources()
    monkeypatch.setattr(compiler_wiring, "TraceCompileSourceReader", lambda: sources)
    app = create_agent_app()
    app.dependency_overrides[agent_entrypoint.get_model_client] = lambda: model

    with TestClient(app) as running:
        assert running.portal is not None
        try:
            running.portal.call(_ping_postgres)
        except Exception as exc:  # noqa: BLE001
            infra_unavailable(f"PostgreSQL chưa sẵn sàng: {type(exc).__name__}. {exc}")
        owner = _make_user(running, "Chu")
        sources.owner = owner
        yield Bench(running, model, owner)


def _sse_events(body: str) -> list[tuple[str, Any]]:
    events: list[tuple[str, Any]] = []
    name = ""
    for line in body.splitlines():
        if line.startswith("event:"):
            name = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            events.append((name, json.loads(line.removeprefix("data:").strip())))
    return events


def _session(events: list[tuple[str, Any]]) -> dict[str, Any]:
    name, payload = events[-1]
    assert name == "session", events
    return payload  # type: ignore[no-any-return]


# ─── Từ nút bấm tới draft ────────────────────────────────────────────────────


def test_mo_phien_phat_tien_do_roi_phien_cho_chap_nhan_y_do(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with bench(monkeypatch, INTENT) as b:
        events = b.open()

        assert [name for name, _ in events] == ["status", "status", "session"]
        assert events[0][1] == {"text": "Đang đọc cuộc hội thoại."}
        session = _session(events)
        assert session["status"] == "awaiting_intent"
        assert session["intent"] == RAW_INTENT
        assert session["is_ready"] is False
        assert [message["kind"] for message in session["messages"]] == ["intent"]
        assert b.model.calls == [{"model": "gpt-6.1-sol", "tools": ["de_xuat_y_do"]}]


def test_tu_mo_phien_toi_draft_qua_hai_lan_xac_nhan(monkeypatch: pytest.MonkeyPatch) -> None:
    with bench(monkeypatch, INTENT, ANSWER) as b:
        session = _session(b.open())

        events = b.stream(f"/compile-sessions/{session['id']}/accept-intent")
        assert [payload["text"] for name, payload in events if name == "status"] == [
            "Đang dựng workflow theo ý đồ.",
            "Đang kiểm workflow với lần chạy mẫu.",
        ]
        session = _session(events)
        assert (session["status"], session["is_ready"]) == ("awaiting_confirmation", True)
        assert session["proposal"]["name"] == "Đăng link trang Notion vào Slack"
        assert session["messages"][-1]["kind"] == "assumptions"

        done = b.post(session, "confirm")
        assert done.status_code == 200, done.text
        assert done.json()["status"] == "compiled"
        workflow_id = done.json()["workflow_id"]

        # Đồng ý không gọi model: vẫn chỉ có hai lời gọi của hai bước trước.
        assert len(b.model.calls) == 2
        ((name, created_by, workspace_id),) = b.query(
            "SELECT name, created_by, workspace_id FROM workflow.workflows WHERE id = :id",
            id=workflow_id,
        )
        assert (name, created_by, workspace_id) == (
            "Đăng link trang Notion vào Slack",
            b.owner.user_id,
            b.owner.workspace_id,
        )
        ((ir, intent, compiled_from),) = b.query(
            "SELECT ir, intent, compiled_from FROM workflow.workflow_drafts "
            "WHERE workflow_id = :id",
            id=workflow_id,
        )
        assert (ir, intent, compiled_from) == (IR, RAW_INTENT, session["id"])
        assert b.get(session).json() == done.json()


def test_noi_lai_o_buoc_y_do_roi_quay_lai_tu_buoc_gia_dinh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doi = RAW_INTENT | {"thay_doi": ["tên trang cần tìm", "kênh Slack"], "co_dinh": []}
    with bench(monkeypatch, INTENT, _calls("de_xuat_y_do", doi), ANSWER) as b:
        session = _session(b.open())

        events = b.stream(
            f"/compile-sessions/{session['id']}/messages", content="kênh cũng là đầu vào"
        )
        session = _session(events)
        assert session["intent"] == doi
        assert [message["kind"] for message in session["messages"]] == ["intent", "user", "intent"]

        session = _session(b.stream(f"/compile-sessions/{session['id']}/accept-intent"))
        back = b.post(session, "reopen-intent")
        assert back.status_code == 200, back.text
        assert (back.json()["status"], back.json()["proposal"]) == ("awaiting_intent", None)


def test_model_hoi_lai_thi_phien_mang_cau_hoi_do(monkeypatch: pytest.MonkeyPatch) -> None:
    with bench(monkeypatch, _says("Bạn muốn đăng vào kênh nào?")) as b:
        session = _session(b.open())

        assert session["intent"] is None
        assert session["messages"] == [
            {
                "kind": "question",
                "text": "Bạn muốn đăng vào kênh nào?",
                "at": session["messages"][0]["at"],
            }
        ]


# ─── Lỗi ─────────────────────────────────────────────────────────────────────


def test_model_hong_giua_chung_thi_bao_loi_va_phien_khong_doi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with bench(monkeypatch, INTENT, ProviderUnavailableError("Provider đang quá tải.")) as b:
        session = _session(b.open())

        events = b.stream(f"/compile-sessions/{session['id']}/accept-intent")

        assert events[-1][0] == "error"
        assert events[-1][1]["message"] == "Provider đang quá tải."
        assert b.get(session).json() == session


def test_chua_co_bang_gia_dinh_thi_dong_y_bi_tu_choi(monkeypatch: pytest.MonkeyPatch) -> None:
    with bench(monkeypatch, INTENT) as b:
        session = _session(b.open())

        reply = b.post(session, "confirm")

        assert reply.status_code == 422, reply.text
        assert b.query(
            "SELECT count(*) FROM workflow.workflows WHERE created_by = :u", u=b.owner.user_id
        ) == [(0,)]


def test_tin_nhan_rong_bi_tu_choi_truoc_khi_goi_model(monkeypatch: pytest.MonkeyPatch) -> None:
    with bench(monkeypatch, INTENT) as b:
        session = _session(b.open())

        reply = b.client.post(
            f"/v1/compile-sessions/{session['id']}/messages",
            headers=b.owner.headers(),
            json={"content": ""},
        )

        assert reply.status_code == 422
        assert len(b.model.calls) == 1


# ─── Quyền ───────────────────────────────────────────────────────────────────


def test_nguoi_khac_khong_mo_duoc_phien_tren_conversation_cua_ban(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with bench(monkeypatch) as b:
        khac = _make_user(b.client, "Khac")

        events = b.open(by=khac)

        assert [name for name, _ in events] == ["status", "error"]
        assert events[-1][1]["message"] == "Không tìm thấy cuộc hội thoại này."
        assert b.model.calls == []


def test_nguoi_khac_thay_phien_nhu_khong_ton_tai(monkeypatch: pytest.MonkeyPatch) -> None:
    with bench(monkeypatch, INTENT) as b:
        session = _session(b.open())
        khac = _make_user(b.client, "Khac")

        assert b.get(session, by=khac).status_code == 403
        assert b.post(session, "abandon", by=khac).status_code == 403
        assert b.get(session).json()["status"] == "awaiting_intent"


def test_thieu_token_thi_khong_vao_duoc(monkeypatch: pytest.MonkeyPatch) -> None:
    with bench(monkeypatch) as b:
        reply = b.client.post(
            f"/v1/conversations/{CONVERSATION}/compile-sessions",
            headers={ORG_HEADER: b.owner.organization_id, WS_HEADER: b.owner.workspace_id},
        )

        assert reply.status_code == 401


# ─── Ngân sách ───────────────────────────────────────────────────────────────


def test_vuot_han_muc_thi_bi_chan_truoc_khi_goi_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AW_COMPILER_RATE_LIMIT", "2")
    get_settings.cache_clear()
    with bench(monkeypatch, INTENT, INTENT, INTENT) as b:
        b.open()
        b.open()

        reply = b.client.post(
            f"/v1/conversations/{CONVERSATION}/compile-sessions", headers=b.owner.headers()
        )

        assert reply.status_code == 429
        assert "retry-after" in reply.headers
        assert len(b.model.calls) == 2


def test_thao_tac_khong_goi_model_khong_tinh_vao_han_muc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AW_COMPILER_RATE_LIMIT", "1")
    get_settings.cache_clear()
    with bench(monkeypatch, INTENT) as b:
        session = _session(b.open())

        for _ in range(3):
            assert b.get(session).status_code == 200
        assert b.post(session, "abandon").status_code == 200
