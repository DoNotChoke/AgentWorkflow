"""Executor chạy thật trên Temporal và PostgreSQL, với tool giả.

Mỗi test dựng worker trong tiến trình, trên bộ queue riêng: executor thật, các
activity ghi trạng thái thật, chỉ `run_tool_node` là giả. Tool giả ghi lại mọi
lần bị gọi — gọi tool nào, với input gì, trên queue nào.

`AW_RECORD_HISTORY=1` ghi lại lịch sử của kịch bản 3 làm fixture cho
`test_workflow_replay.py`.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import uuid
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import infra_unavailable
from sqlalchemy import text
from temporalio import activity
from temporalio.client import Client, WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from app.core.config import get_settings
from app.core.db import dispose_engine, init_engine, session_scope
from app.core.errors import ProviderUnavailableError
from app.domains.execution.application.service import ExecutionService
from app.domains.execution.infrastructure.repositories import SqlAlchemyExecutionRepository
from app.domains.workflow.application.ports import ModelAnswer, Rejected
from app.domains.workflow.domain.entities import Workflow, WorkflowVersion, fingerprint
from app.domains.workflow.infrastructure.repositories import SqlAlchemyWorkflowRepository
from app.domains.workflow.infrastructure.temporal.messages import (
    EXECUTOR_WORKFLOW,
    RUN_TOOL_NODE,
    ExecutorInput,
    ExecutorQueues,
    RunToolInput,
)
from app.domains.workflow.infrastructure.temporal.workflow import GenericWorkflowExecutor
from app.entrypoints.worker_wiring import build_executor_activities

pytestmark = pytest.mark.integration

TENANT = "org_workflow_executor"
NOW = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)
EXAMPLES = Path(__file__).resolve().parents[3] / "packages" / "schemas" / "workflow" / "examples"
HISTORY = Path(__file__).resolve().parent / "fixtures" / "histories" / "kb3.json"

KENH = {"channels": [{"id": "C1", "name": "general"}, {"id": "C2", "name": "test"}]}
KHOI_CON = {
    "results": [
        {"id": "p1", "type": "child_page"},
        {"id": "x1", "type": "paragraph"},
        {"id": "p2", "type": "child_page"},
        {"id": "x2", "type": "heading_1"},
        {"id": "p3", "type": "child_page"},
    ]
}


# ─── Tool giả ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Call:
    tool_id: str
    input: dict[str, Any]
    queue: str
    node_path: str
    node_id: str = ""
    idempotency_key: str | None = None


Behaviour = Callable[[dict[str, Any]], dict[str, Any]]

_calls: list[Call] = []
_script: dict[str, Behaviour] = {}


def _fails(category: str) -> Behaviour:
    def behaviour(_: dict[str, Any]) -> dict[str, Any]:
        raise ApplicationError(f"{category} giả", type=category, non_retryable=True)

    return behaviour


def _page(inp: dict[str, Any]) -> dict[str, Any]:
    return {"id": inp["page_id"], "title": f"Trang {inp['page_id']}"}


DEFAULT_SCRIPT: dict[str, Behaviour] = {
    "slack.list_channels": lambda _: KENH,
    "slack.post_message": lambda inp: {"ok": True, "channel": inp["channel"], "ts": "1.0"},
    "notion.search": lambda _: {"results": [{"id": "trang_aws"}]},
    "notion.get_block_children": lambda _: KHOI_CON,
    "notion.get_page": _page,
}


@activity.defn(name=RUN_TOOL_NODE)
async def fake_tool(request: RunToolInput) -> dict[str, Any]:
    _calls.append(
        Call(
            request.tool_id,
            dict(request.input),
            activity.info().task_queue,
            request.node_path,
            request.node_id,
            request.idempotency_key,
        )
    )
    return _script[request.tool_id](request.input)


# ─── Model giả ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Asked:
    model: str
    system: str
    data: dict[str, Any]
    rejected: tuple[str, ...]


Reply = dict[str, Any] | Exception | None
_asked: list[Asked] = []
#: Lần lượt từng câu model sẽ trả; hết thì lặp lại câu cuối.
_replies: list[Reply] = []


class FakeModel:
    """`NodeModel` giả. Mỗi lần được hỏi tốn 100 token vào, 20 token ra."""

    async def answer(
        self,
        *,
        model: str,
        system: str,
        data: str,
        output_schema: Mapping[str, Any],
        rejected: Sequence[Rejected],
    ) -> ModelAnswer:
        _asked.append(Asked(model, system, json.loads(data), tuple(r.reason for r in rejected)))
        reply = _replies.pop(0) if len(_replies) > 1 else _replies[0]
        if isinstance(reply, Exception):
            raise reply
        return ModelAnswer(output=reply, input_tokens=100, output_tokens=20)


# ─── Hạ tầng ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def engine() -> AsyncIterator[None]:
    init_engine(get_settings())
    try:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        await dispose_engine()
        infra_unavailable(f"PostgreSQL chưa sẵn sàng: {type(exc).__name__}. {exc}")

    _calls.clear()
    _script.clear()
    _script.update(DEFAULT_SCRIPT)
    _asked.clear()
    _replies.clear()
    yield

    async with session_scope() as session:
        ids = {"ids": [TENANT]}
        await session.execute(
            text("DELETE FROM execution.executions WHERE tenant_id = ANY(:ids)"), ids
        )
        await session.execute(
            text("DELETE FROM workflow.workflow_versions WHERE tenant_id = ANY(:ids)"), ids
        )
        await session.execute(
            text("DELETE FROM workflow.workflows WHERE tenant_id = ANY(:ids)"), ids
        )
    await dispose_engine()


@dataclass
class Outcome:
    execution_id: str
    status: str
    failure: tuple[str | None, str | None]
    nodes: dict[str, str]
    error: WorkflowFailureError | None
    history_json: str = field(default="", repr=False)
    #: Token model đã dùng, theo node.
    usage: dict[str, Mapping[str, int]] = field(default_factory=dict)


@dataclass
class Harness:
    client: Client
    workflow_queue: str
    queues: ExecutorQueues

    async def run(self, ir: dict[str, Any], inputs: dict[str, Any]) -> Outcome:
        version = await _published(ir)
        async with session_scope() as session:
            execution = await ExecutionService(
                repository=SqlAlchemyExecutionRepository(session)
            ).create(
                tenant_id=TENANT,
                workspace_id="ws_1",
                workflow_id=version.workflow_id,
                workflow_version_id=version.id,
                trigger_type="manual",
                inputs=inputs,
                initiated_by="usr_1",
                at=NOW,
            )
        handle = await self.client.start_workflow(
            EXECUTOR_WORKFLOW,
            ExecutorInput(
                execution_id=execution.id,
                tenant_id=TENANT,
                workflow_version_id=version.id,
                inputs=inputs,
                queues=self.queues,
            ),
            id=execution.id,
            task_queue=self.workflow_queue,
        )
        error: WorkflowFailureError | None = None
        try:
            await handle.result()
        except WorkflowFailureError as raised:
            error = raised

        async with session_scope() as session:
            progress = await ExecutionService(
                repository=SqlAlchemyExecutionRepository(session)
            ).get_progress(tenant_id=TENANT, execution_id=execution.id)
        failure = progress.execution.failure
        history = await handle.fetch_history()
        return Outcome(
            execution_id=execution.id,
            status=str(progress.execution.status),
            failure=(None, None) if failure is None else (str(failure.category), failure.message),
            nodes={n.node_path: str(n.status) for n in progress.nodes},
            error=error,
            history_json=history.to_json(),
            usage={n.node_path: n.usage for n in progress.nodes if n.usage is not None},
        )


@pytest.fixture
async def harness() -> AsyncIterator[Harness]:
    settings = get_settings()
    try:
        client = await asyncio.wait_for(
            Client.connect(settings.temporal_host, namespace=settings.temporal_namespace), 5
        )
    except Exception as exc:  # noqa: BLE001
        infra_unavailable(f"Temporal chưa sẵn sàng: {type(exc).__name__}. {exc}")

    prefix = f"test-{uuid.uuid4().hex[:8]}"
    queues = ExecutorQueues(
        processing=f"{prefix}-processing",
        integration_read=f"{prefix}-read",
        integration_write=f"{prefix}-write",
        ai=f"{prefix}-ai",
    )
    executor = build_executor_activities(models=FakeModel())
    workers = [
        Worker(client, task_queue=f"{prefix}-workflow", workflows=[GenericWorkflowExecutor]),
        Worker(
            client,
            task_queue=queues.processing,
            activities=[
                executor.load_plan,
                executor.seed_nodes,
                executor.record_node,
                executor.record_execution,
                executor.run_transform_node,
            ],
        ),
        Worker(client, task_queue=queues.ai, activities=[executor.run_llm_node]),
        Worker(client, task_queue=queues.integration_read, activities=[fake_tool]),
        Worker(client, task_queue=queues.integration_write, activities=[fake_tool]),
    ]
    async with AsyncExitStack() as stack:
        for worker in workers:
            await stack.enter_async_context(worker)
        yield Harness(client=client, workflow_queue=f"{prefix}-workflow", queues=queues)


def _example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


async def _published(ir: dict[str, Any]) -> WorkflowVersion:
    workflow = Workflow(
        id=f"wf_{uuid.uuid4().hex}",
        tenant_id=TENANT,
        workspace_id="ws_1",
        name="executor",
        created_by="usr_1",
        created_at=NOW,
        updated_at=NOW,
    )
    version = WorkflowVersion(
        id=f"wfv_{uuid.uuid4().hex}",
        workflow_id=workflow.id,
        tenant_id=TENANT,
        number=1,
        ir=ir,
        ir_sha256=fingerprint(ir),
        published_at=NOW,
        published_by="usr_1",
    )
    async with session_scope() as session:
        repository = SqlAlchemyWorkflowRepository(session)
        await repository.add(workflow)
        await repository.add_version(version)
    return version


def _tools() -> list[str]:
    return [call.tool_id for call in _calls]


# ─── Chạy hết ────────────────────────────────────────────────────────────────


async def test_kich_ban_mot_chay_het_va_tool_ghi_di_queue_ghi(harness: Harness) -> None:
    outcome = await harness.run(_example("kb1-slack-ghi"), {"noi_dung": "xin chào"})

    assert outcome.error is None
    assert outcome.status == "succeeded"
    assert outcome.nodes == {"t": "succeeded", "dang": "succeeded"}
    assert _calls == [
        Call(
            "slack.post_message",
            {"channel": "C0VIDU0001", "text": "xin chào"},
            harness.queues.integration_write,
            "dang",
            "dang",
            f"idm_{outcome.execution_id}:dang:slack.post_message",
        )
    ]


async def test_kich_ban_ba_loop_qua_trang_con(harness: Harness) -> None:
    outcome = await harness.run(_example("kb3-notion-doc-sau"), {"ten_trang": "Ghi chú AWS"})

    assert outcome.error is None
    assert outcome.status == "succeeded"
    assert outcome.nodes == {
        "t": "succeeded",
        "tim": "succeeded",
        "con": "succeeded",
        "moi_trang": "succeeded",
        "moi_trang[0].doc_trang": "succeeded",
        "moi_trang[1].doc_trang": "succeeded",
        "moi_trang[2].doc_trang": "succeeded",
    }
    assert [call.input for call in _calls if call.tool_id == "notion.get_page"] == [
        {"page_id": "p1"},
        {"page_id": "p2"},
        {"page_id": "p3"},
    ]
    assert {call.queue for call in _calls} == {harness.queues.integration_read}

    if os.environ.get("AW_RECORD_HISTORY") == "1":
        HISTORY.parent.mkdir(parents=True, exist_ok=True)
        HISTORY.write_text(outcome.history_json, encoding="utf-8")


async def test_where_chon_kenh_theo_ten(harness: Harness) -> None:
    outcome = await harness.run(
        _example("kb2-slack-doc-roi-ghi"), {"ten_kenh": "test", "noi_dung": "xin chào"}
    )
    assert outcome.status == "succeeded"
    assert _calls[-1].input == {"channel": "C2", "text": "xin chào"}


# ─── Hỏng ────────────────────────────────────────────────────────────────────


async def test_tool_hong_thi_node_sau_van_pending(harness: Harness) -> None:
    _script["notion.get_block_children"] = _fails("ProviderPermanentError")
    outcome = await harness.run(_example("kb3-notion-doc-sau"), {"ten_trang": "Ghi chú AWS"})

    assert outcome.error is not None
    assert outcome.status == "failed"
    assert outcome.failure == ("ProviderPermanentError", "ProviderPermanentError giả")
    assert outcome.nodes == {
        "t": "succeeded",
        "tim": "succeeded",
        "con": "failed",
        "moi_trang": "pending",
    }


async def test_luot_giua_hong_thi_luot_sau_pending_va_loop_hong(harness: Harness) -> None:
    def page(inp: dict[str, Any]) -> dict[str, Any]:
        if inp["page_id"] == "p2":
            raise ApplicationError("Notion 404", type="ProviderPermanentError", non_retryable=True)
        return _page(inp)

    _script["notion.get_page"] = page
    outcome = await harness.run(_example("kb3-notion-doc-sau"), {"ten_trang": "Ghi chú AWS"})

    assert outcome.status == "failed"
    assert outcome.nodes["moi_trang"] == "failed"
    assert [outcome.nodes[f"moi_trang[{i}].doc_trang"] for i in range(3)] == [
        "succeeded",
        "failed",
        "pending",
    ]


async def test_vuot_max_items_thi_than_loop_khong_duoc_seed(harness: Harness) -> None:
    ir = _example("kb3-notion-doc-sau")
    ir["nodes"][3]["max_items"] = 2
    outcome = await harness.run(ir, {"ten_trang": "Ghi chú AWS"})

    assert outcome.status == "failed"
    assert outcome.failure[0] == "WorkflowExecutionError"
    assert outcome.nodes["moi_trang"] == "failed"
    assert not any(path.startswith("moi_trang[") for path in outcome.nodes)
    assert "notion.get_page" not in _tools()


async def test_where_khong_khop_noi_ro_gia_tri(harness: Harness) -> None:
    outcome = await harness.run(
        _example("kb2-slack-doc-roi-ghi"), {"ten_kenh": "khong-co", "noi_dung": "xin chào"}
    )
    assert outcome.status == "failed"
    assert outcome.nodes["dang"] == "failed"
    assert "name = 'khong-co'" in (outcome.failure[1] or "")
    assert "slack.post_message" not in _tools()


async def test_input_sai_schema_thi_trigger_hong_va_khong_goi_tool(harness: Harness) -> None:
    outcome = await harness.run(_example("kb2-slack-doc-roi-ghi"), {"noi_dung": "xin chào"})
    assert outcome.status == "failed"
    assert outcome.nodes == {"t": "failed", "kenh": "pending", "dang": "pending"}
    assert _calls == []


# ─── Khoá idempotency (ADR-0012) ─────────────────────────────────────────────


async def test_moi_luot_cua_loop_co_khoa_rieng_va_tool_doc_thi_khong_co_khoa(
    harness: Harness,
) -> None:
    """Ba lượt `post_message` là ba tin: cùng một khoá thì lượt 2 và 3 chỉ là phát lại."""
    ir = copy.deepcopy(_example("kb3-notion-doc-sau"))
    loop = next(node for node in ir["nodes"] if node["type"] == "loop")
    loop["body"] = [
        {
            "id": "bao",
            "type": "tool",
            "tool_id": "slack.post_message",
            "tool_version": "1.0",
            "input": {"channel": {"const": "C1"}, "text": {"from_item": ["id"]}},
        }
    ]
    loop["collect"] = "bao"

    outcome = await harness.run(ir, {"ten_trang": "Ghi chú AWS"})

    assert outcome.status == "succeeded"
    posts = [call for call in _calls if call.tool_id == "slack.post_message"]
    assert [call.idempotency_key for call in posts] == [
        f"idm_{outcome.execution_id}:{loop['id']}[{turn}].bao:slack.post_message"
        for turn in range(3)
    ]
    assert {call.node_id for call in posts} == {"bao"}
    reads = [call for call in _calls if call.tool_id != "slack.post_message"]
    assert reads and all(call.idempotency_key is None for call in reads)


# ─── transform và condition ──────────────────────────────────────────────────


def _lich_su(count: int) -> Behaviour:
    return lambda _: {"messages": [{"ts": str(n), "text": f"tin {n}"} for n in range(count)]}


async def test_dieu_kien_dung_thi_nhanh_true_chay(harness: Harness) -> None:
    """Kịch bản 5: đếm tin nhắn bằng code, hơn 5 tin thì mới đăng."""
    _script["slack.get_channel_history"] = _lich_su(6)

    outcome = await harness.run(_example("kb5-re-nhanh"), {})

    assert outcome.status == "succeeded"
    assert outcome.nodes == {
        "t": "succeeded",
        "lich_su": "succeeded",
        "dem": "succeeded",
        "du_nhieu": "succeeded",
        "dang": "succeeded",
    }
    assert _tools() == ["slack.get_channel_history", "slack.post_message"]


async def test_dieu_kien_sai_thi_node_sau_no_bi_bo_qua_va_lan_chay_van_xong(
    harness: Harness,
) -> None:
    _script["slack.get_channel_history"] = _lich_su(3)

    outcome = await harness.run(_example("kb5-re-nhanh"), {})

    assert outcome.status == "succeeded"
    assert outcome.nodes["du_nhieu"] == "succeeded"
    assert outcome.nodes["dang"] == "skipped"
    assert _tools() == ["slack.get_channel_history"]


async def test_bo_qua_lan_xuong_moi_node_chi_toi_duoc_qua_nhanh_do(harness: Harness) -> None:
    ir = copy.deepcopy(_example("kb5-re-nhanh"))
    ir["nodes"].append(
        {
            "id": "dang_tiep",
            "type": "tool",
            "tool_id": "slack.post_message",
            "tool_version": "1.0",
            "input": {"channel": {"const": "C0VIDU0001"}, "text": {"const": "Thêm một tin."}},
        }
    )
    ir["edges"].append({"from": "dang", "to": "dang_tiep"})
    _script["slack.get_channel_history"] = _lich_su(1)

    outcome = await harness.run(ir, {})

    assert outcome.status == "succeeded"
    assert (outcome.nodes["dang"], outcome.nodes["dang_tiep"]) == ("skipped", "skipped")
    assert _tools() == ["slack.get_channel_history"]


async def test_ket_qua_cua_transform_di_vao_node_sau(harness: Harness) -> None:
    ir = copy.deepcopy(_example("kb5-re-nhanh"))
    dem = next(node for node in ir["nodes"] if node["id"] == "dem")
    dem["code"] = "return f\"Kênh có {len(args['messages'])} tin nhắn.\""
    dem["output_schema"] = {"type": "string"}
    ir["nodes"] = [node for node in ir["nodes"] if node["id"] != "du_nhieu"]
    ir["edges"] = [
        {"from": "t", "to": "lich_su"},
        {"from": "lich_su", "to": "dem"},
        {"from": "dem", "to": "dang"},
    ]
    dang = next(node for node in ir["nodes"] if node["id"] == "dang")
    dang["input"]["text"] = {"from_node": {"node": "dem", "path": []}}
    _script["slack.get_channel_history"] = _lich_su(4)

    outcome = await harness.run(ir, {})

    assert outcome.status == "succeeded"
    assert _calls[-1].input["text"] == "Kênh có 4 tin nhắn."


async def test_code_hong_thi_node_transform_hong_va_node_sau_van_pending(harness: Harness) -> None:
    ir = copy.deepcopy(_example("kb5-re-nhanh"))
    next(node for node in ir["nodes"] if node["id"] == "dem")["code"] = "return args['khong_co']"
    _script["slack.get_channel_history"] = _lich_su(6)

    outcome = await harness.run(ir, {})

    assert outcome.status == "failed"
    assert outcome.failure[0] == "ValidationError"
    assert (outcome.nodes["dem"], outcome.nodes["du_nhieu"], outcome.nodes["dang"]) == (
        "failed",
        "pending",
        "pending",
    )


async def test_transform_tra_sai_kieu_da_khai_thi_hong(harness: Harness) -> None:
    ir = copy.deepcopy(_example("kb5-re-nhanh"))
    next(node for node in ir["nodes"] if node["id"] == "dem")["code"] = "return 'sáu'"
    _script["slack.get_channel_history"] = _lich_su(6)

    outcome = await harness.run(ir, {})

    assert outcome.status == "failed"
    assert outcome.failure[0] == "WorkflowExecutionError"
    assert "không khớp kiểu đã khai" in (outcome.failure[1] or "")
    assert outcome.nodes["dem"] == "failed"


# ─── llm ─────────────────────────────────────────────────────────────────────

TOM_TAT = {
    "schema_version": "2.0",
    "nodes": [
        {
            "id": "t",
            "type": "trigger",
            "trigger": "manual",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "id": "kenh",
            "type": "tool",
            "tool_id": "slack.list_channels",
            "tool_version": "1.0",
            "input": {},
        },
        {
            "id": "tom_tat",
            "type": "llm",
            "model": "gpt-6.1-sol",
            "instructions": "Viết một câu nêu tên các kênh trong `kenh`.",
            "input": {"kenh": {"from_node": {"node": "kenh", "path": ["channels"]}}},
            "output_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text"],
                "properties": {"text": {"type": "string", "minLength": 1}},
            },
        },
        {
            "id": "dang",
            "type": "tool",
            "tool_id": "slack.post_message",
            "tool_version": "1.0",
            "input": {
                "channel": {"const": "C0VIDU0001"},
                "text": {"from_node": {"node": "tom_tat", "path": ["text"]}},
            },
        },
    ],
    "edges": [
        {"from": "t", "to": "kenh"},
        {"from": "kenh", "to": "tom_tat"},
        {"from": "tom_tat", "to": "dang"},
    ],
}


async def test_node_llm_doc_du_lieu_cua_node_truoc_va_nuoi_node_sau(harness: Harness) -> None:
    _replies.append({"text": "Có hai kênh: general và test."})

    outcome = await harness.run(copy.deepcopy(TOM_TAT), {})

    assert outcome.status == "succeeded"
    (asked,) = _asked
    assert asked.model == "gpt-6.1-sol"
    assert asked.data == {"kenh": KENH["channels"]}
    assert "Viết một câu nêu tên các kênh trong `kenh`." in asked.system
    assert _calls[-1].input["text"] == "Có hai kênh: general và test."
    assert outcome.usage == {
        "tom_tat": {"input_tokens": 100, "output_tokens": 20, "model_calls": 1}
    }


async def test_ket_qua_sai_kieu_thi_hoi_lai_model_va_token_duoc_cong_don(harness: Harness) -> None:
    _replies.extend([{"text": 5}, {"text": "Có hai kênh."}])

    outcome = await harness.run(copy.deepcopy(TOM_TAT), {})

    assert outcome.status == "succeeded"
    assert len(_asked) == 2
    assert "Kết quả sai kiểu ở text" in _asked[1].rejected[0]
    assert outcome.usage["tom_tat"] == {
        "input_tokens": 200,
        "output_tokens": 40,
        "model_calls": 2,
    }


async def test_sai_kieu_mai_thi_node_llm_hong_sau_ba_lan_hoi(harness: Harness) -> None:
    _replies.append({"khac": "không đúng trường"})

    outcome = await harness.run(copy.deepcopy(TOM_TAT), {})

    assert outcome.status == "failed"
    assert outcome.failure[0] == "WorkflowExecutionError"
    assert "sau 3 lần" in (outcome.failure[1] or "")
    assert (outcome.nodes["tom_tat"], outcome.nodes["dang"]) == ("failed", "pending")
    # Sai kiểu là lỗi không thử lại ở tầng Temporal: ba lần hỏi, không phải chín.
    assert len(_asked) == 3
    assert _tools() == ["slack.list_channels"]


async def test_provider_hong_tam_thoi_thi_temporal_thu_lai_node_llm(harness: Harness) -> None:
    _replies.extend([ProviderUnavailableError("Provider đang quá tải."), {"text": "Có hai kênh."}])

    outcome = await harness.run(copy.deepcopy(TOM_TAT), {})

    assert outcome.status == "succeeded"
    assert len(_asked) == 2
    assert _asked[1].rejected == ()


async def test_than_loop_chay_tool_llm_va_transform_cho_tung_phan_tu(harness: Harness) -> None:
    """Hình dạng của kb8: mỗi phần tử đọc, soạn bằng model, gọt bằng code, rồi đăng."""
    ir = {
        "schema_version": "2.1",
        "nodes": [
            TOM_TAT["nodes"][0],
            {
                "id": "khoi",
                "type": "tool",
                "tool_id": "notion.get_block_children",
                "tool_version": "1.0",
                "input": {"block_id": {"const": "trang_goc"}},
            },
            {
                "id": "moi_trang",
                "type": "loop",
                "over": {"from_node": {"node": "khoi", "path": ["results"]}},
                "filter": {"type": {"const": "child_page"}},
                "max_items": 10,
                "collect": "dang",
                "body": [
                    {
                        "id": "doc",
                        "type": "tool",
                        "tool_id": "notion.get_page",
                        "tool_version": "1.0",
                        "input": {"page_id": {"from_item": ["id"]}},
                    },
                    {
                        "id": "soan",
                        "type": "llm",
                        "model": "gpt-6.1-sol",
                        "instructions": "Viết một dòng giới thiệu trang trong `trang`.",
                        "input": {"trang": {"from_node": {"node": "doc", "path": []}}},
                        "output_schema": {
                            "type": "object",
                            "required": ["text"],
                            "properties": {"text": {"type": "string"}},
                        },
                    },
                    {
                        "id": "gon",
                        "type": "transform",
                        "code": "return args['text'].strip().upper()",
                        "args": {"text": {"from_node": {"node": "soan", "path": ["text"]}}},
                        "output_schema": {"type": "string"},
                    },
                    {
                        "id": "dang",
                        "type": "tool",
                        "tool_id": "slack.post_message",
                        "tool_version": "1.0",
                        "input": {
                            "channel": {"const": "C0VIDU0001"},
                            "text": {"from_node": {"node": "gon", "path": []}},
                        },
                    },
                ],
            },
        ],
        "edges": [{"from": "t", "to": "khoi"}, {"from": "khoi", "to": "moi_trang"}],
    }
    _replies.extend([{"text": " trang một "}, {"text": "trang hai"}, {"text": "trang ba"}])

    outcome = await harness.run(ir, {})

    assert outcome.status == "succeeded"
    assert [asked.data["trang"]["id"] for asked in _asked] == ["p1", "p2", "p3"]
    posted = [call.input["text"] for call in _calls if call.tool_id == "slack.post_message"]
    assert posted == ["TRANG MỘT", "TRANG HAI", "TRANG BA"]
    assert {path: status for path, status in outcome.nodes.items() if "[1]" in path} == {
        "moi_trang[1].doc": "succeeded",
        "moi_trang[1].soan": "succeeded",
        "moi_trang[1].gon": "succeeded",
        "moi_trang[1].dang": "succeeded",
    }
    assert sorted(outcome.usage) == ["moi_trang[0].soan", "moi_trang[1].soan", "moi_trang[2].soan"]


async def test_lich_su_vua_chay_replay_lai_duoc(harness: Harness) -> None:
    from temporalio.client import WorkflowHistory
    from temporalio.worker import Replayer

    outcome = await harness.run(
        copy.deepcopy(_example("kb3-notion-doc-sau")), {"ten_trang": "Ghi chú AWS"}
    )
    history = WorkflowHistory.from_json(outcome.execution_id, outcome.history_json)
    await Replayer(workflows=[GenericWorkflowExecutor]).replay_workflow(history)
