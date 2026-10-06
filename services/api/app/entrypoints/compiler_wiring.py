"""Nối Workflow Compiler (domain `workflow`) với Model Gateway (domain `agent`) và sandbox.

Chỉ được nạp trong tiến trình Agent Runtime: nó nạp `core.sandbox`, thứ contract 8 cấm ở
Control Plane. Không import `entrypoints/wiring.py` (contract 6).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import session_scope
from app.core.errors import ValidationError
from app.domains.agent.application.ports import (
    ModelClient,
    ModelToolCall,
    ModelToolResult,
    ModelToolSpec,
    ModelTurn,
    StopReason,
)
from app.domains.agent.infrastructure.repositories import (
    SqlAlchemyConversationRepository,
    SqlAlchemyMessageRepository,
)
from app.domains.integration.infrastructure.catalog_view import tool_rows
from app.domains.trace.application.service import PayloadSide, TraceService
from app.domains.trace.infrastructure.payload_store import build_payload_store
from app.domains.trace.infrastructure.repositories import SqlAlchemyExecutionTraceRepository
from app.domains.workflow.application.compile_flow import (
    ANSWER_TOOL,
    ANSWERS_PROMPT,
    INTENT_PROMPT,
    INTENT_SCHEMA,
    INTENT_TOOL,
)
from app.domains.workflow.application.compile_service import CompileService
from app.domains.workflow.application.ports import (
    AnswerAttempt,
    CompileSource,
    DialogueTurn,
    IntentReply,
)
from app.domains.workflow.infrastructure.catalog import RowToolCatalog
from app.domains.workflow.infrastructure.compile_repositories import (
    SqlAlchemyCompileSessionRepository,
)
from app.domains.workflow.infrastructure.repositories import SqlAlchemyWorkflowRepository
from app.entrypoints.sandbox_runner import SandboxRunner

__all__ = ["SandboxRunner"]

#: Với model suy luận, trần này gồm cả phần suy luận, không chỉ phần trả lời.
_INTENT_MAX_TOKENS = 4096
_ANSWER_MAX_TOKENS = 16384

_INTENT_SPEC = ModelToolSpec(
    name=INTENT_TOOL,
    description="Nộp đề xuất ý đồ của workflow để người dùng xác nhận.",
    input_schema=INTENT_SCHEMA,
    version="1",
)


class ModelIntentAdvisor:
    """`IntentAdvisor` của domain `workflow`, bằng một `ModelClient`."""

    def __init__(self, models: ModelClient, *, model: str) -> None:
        self._models = models
        self._model = model

    async def propose(self, *, digest: str, dialogue: Sequence[DialogueTurn]) -> IntentReply:
        turns = [ModelTurn(role="user", text=digest)]
        # Đề xuất trước đó đi vào lịch sử dưới dạng chữ, không dưới dạng lời gọi tool:
        # người dùng đã đọc đúng đoạn chữ đó, và model không cần một kết quả tool giả.
        turns += [ModelTurn(role=turn.role, text=turn.text) for turn in dialogue]
        response = await self._models.complete(
            model=self._model,
            system=INTENT_PROMPT,
            turns=turns,
            tools=(_INTENT_SPEC,),
            max_tokens=_INTENT_MAX_TOKENS,
        )
        call = _call(response.tool_calls, INTENT_TOOL)
        if call is None:
            return IntentReply(proposal=None, text=response.text.strip())
        return IntentReply(proposal=call.input, text=response.text.strip())


class ModelAnswerAdvisor:
    """`AnswerAdvisor` của domain `workflow`, bằng một `ModelClient`."""

    def __init__(self, models: ModelClient, *, model: str) -> None:
        self._models = models
        self._model = model

    async def answer(
        self,
        *,
        briefing: str,
        schema: Mapping[str, Any],
        attempts: Sequence[AnswerAttempt],
        comment: str | None = None,
        previous: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        opening = briefing
        if previous is not None:
            opening += "\n\n# Câu trả lời đang hiện cho người dùng\n\n```json\n"
            opening += json.dumps(previous, ensure_ascii=False, indent=2) + "\n```"
        if comment:
            opening += f"\n\n# Người dùng muốn đổi\n\n{comment}"

        turns = [ModelTurn(role="user", text=opening)]
        for number, attempt in enumerate(attempts, start=1):
            call_id = f"lan_{number}"
            turns.append(
                ModelTurn(
                    role="assistant",
                    tool_calls=(
                        ModelToolCall(call_id=call_id, name=ANSWER_TOOL, input=attempt.answers),
                    ),
                )
            )
            turns.append(
                ModelTurn(
                    role="user",
                    tool_results=(
                        ModelToolResult(
                            call_id=call_id,
                            content="Câu trả lời chưa dùng được:\n"
                            + "\n".join(f"- {error}" for error in attempt.errors),
                            is_error=True,
                        ),
                    ),
                )
            )

        spec = ModelToolSpec(
            name=ANSWER_TOOL,
            description="Nộp câu trả lời đầy đủ cho tập câu hỏi.",
            input_schema=schema,
            version="1",
        )
        response = await self._models.complete(
            model=self._model,
            system=ANSWERS_PROMPT,
            turns=turns,
            tools=(spec,),
            max_tokens=_ANSWER_MAX_TOKENS,
        )
        if response.stop_reason is StopReason.MAX_TOKENS:
            # Câu trả lời bị cắt giữa chừng là một JSON hỏng; hỏi lại chỉ ra đúng thứ đó.
            raise ValidationError(
                "Cuộc hội thoại này có quá nhiều bước để trả lời trong một lần: câu trả lời "
                "của model bị cắt giữa chừng."
            )
        call = _call(response.tool_calls, ANSWER_TOOL)
        return {} if call is None else call.input


def _call(calls: Sequence[ModelToolCall], name: str) -> ModelToolCall | None:
    return next((call for call in calls if call.name == name), None)


class TraceCompileSourceReader:
    """`CompileSourceReader` của domain `workflow`: conversation và tin nhắn ở `agent`,
    trace và payload ở `trace`. Chỉ đọc."""

    async def read(self, *, tenant_id: str, conversation_id: str) -> CompileSource | None:
        async with session_scope() as session:
            conversation = await SqlAlchemyConversationRepository(session).get(
                tenant_id=tenant_id, conversation_id=conversation_id
            )
            if conversation is None:
                return None
            said = await SqlAlchemyMessageRepository(session).list_for_conversation(
                tenant_id=tenant_id, conversation_id=conversation_id
            )
            service = TraceService(
                traces=SqlAlchemyExecutionTraceRepository(session),
                payloads=build_payload_store(get_settings()),
            )
            listed = await service.list_for_conversation(
                tenant_id=tenant_id, conversation_id=conversation_id
            )
            traces = [
                await self._trace(service, tenant_id, trace.id)
                for trace in sorted(listed, key=lambda item: item.started_at)
            ]

        # Chỉ lời có chữ: câu người dùng gõ, và câu Agent trả lời sau khi gọi xong tool.
        messages = [
            {
                "role": str(message.role),
                "content": message.content,
                "created_at": message.created_at.isoformat(),
            }
            for message in said
            if message.content
            and (
                str(message.role) == "user"
                or (str(message.role) == "assistant" and not message.tool_calls)
            )
        ]
        return CompileSource(
            document={
                "conversation_id": conversation_id,
                "messages": messages,
                "traces": traces,
            },
            user_id=conversation.user_id,
            workspace_id=conversation.workspace_id,
        )

    async def _trace(self, service: TraceService, tenant_id: str, trace_id: str) -> dict[str, Any]:
        detail = await service.get(tenant_id=tenant_id, trace_id=trace_id)
        steps: list[dict[str, Any]] = []
        for step in detail.steps:
            sides: dict[str, dict[str, Any]] = {}
            for side in PayloadSide:
                view = await service.get_payload(
                    tenant_id=tenant_id, trace_id=trace_id, step_id=step.id, side=side
                )
                sides[str(side)] = {"state": str(view.availability), "payload": view.payload}
            steps.append(
                {
                    "id": step.id,
                    "kind": str(step.kind),
                    "status": str(step.status),
                    "parent_step_id": step.parent_step_id,
                    "started_at": step.started_at.isoformat(),
                    "attributes": dict(step.attributes),
                    "error_category": step.error_category,
                    "input": sides["input"],
                    "output": sides["output"],
                }
            )
        return {
            "id": detail.trace.id,
            "status": str(detail.trace.status),
            "started_at": detail.trace.started_at.isoformat(),
            "steps": steps,
        }


class ScopedCompileSessions:
    """`CompileSessionRepository` mà mỗi lần gọi là một transaction riêng.

    Cho các thao tác có gọi model: lời gọi đó mất hàng chục giây, và không transaction
    nào nên mở suốt chừng ấy. `revision` lo phần hai request ghi chồng lên nhau."""

    async def add(self, session: Any) -> None:
        async with session_scope() as db:
            await SqlAlchemyCompileSessionRepository(db).add(session)

    async def get(self, *, tenant_id: str, session_id: str) -> Any:
        async with session_scope() as db:
            return await SqlAlchemyCompileSessionRepository(db).get(
                tenant_id=tenant_id, session_id=session_id
            )

    async def save(self, session: Any, *, expected_revision: int) -> None:
        async with session_scope() as db:
            await SqlAlchemyCompileSessionRepository(db).save(
                session, expected_revision=expected_revision
            )

    async def list_open(self, *, tenant_id: str, conversation_id: str, created_by: str) -> Any:
        async with session_scope() as db:
            return await SqlAlchemyCompileSessionRepository(db).list_open(
                tenant_id=tenant_id, conversation_id=conversation_id, created_by=created_by
            )


class ScopedDrafts:
    """`DraftWriter` mà mỗi lần gọi là một transaction riêng. Service dựng theo kiểu này
    không được dùng để `confirm`: phiên, workflow và draft phải cùng thành hoặc cùng huỷ."""

    async def add(self, workflow: Any) -> None:
        async with session_scope() as db:
            await SqlAlchemyWorkflowRepository(db).add(workflow)

    async def save_draft(self, draft: Any) -> None:
        async with session_scope() as db:
            await SqlAlchemyWorkflowRepository(db).save_draft(draft)


def build_compile_service(
    *, models: ModelClient, settings: Settings, session: AsyncSession | None = None
) -> CompileService:
    """`session` có thì mọi lần ghi nằm trong transaction của request đó; không có thì
    mỗi lần ghi một transaction riêng."""
    rows = tool_rows()
    return CompileService(
        sessions=(
            ScopedCompileSessions()
            if session is None
            else SqlAlchemyCompileSessionRepository(session)
        ),
        sources=TraceCompileSourceReader(),
        workflows=ScopedDrafts() if session is None else SqlAlchemyWorkflowRepository(session),
        intent_advisor=ModelIntentAdvisor(models, model=settings.compiler_intent_model),
        answer_advisor=ModelAnswerAdvisor(models, model=settings.compiler_answer_model),
        tools={(row["tool_id"], row["version"]): row["input_schema"] for row in rows},
        mutating={(row["tool_id"], row["version"]) for row in rows if row["mutates"]},
        catalog=RowToolCatalog(rows),
        runner=SandboxRunner(),
        intent_model=settings.compiler_intent_model,
        answer_model=settings.compiler_answer_model,
        llm_node_model=settings.compiler_llm_node_model,
        max_rounds=settings.compiler_max_rounds,
        max_briefing_chars=settings.compiler_max_briefing_chars,
    )
