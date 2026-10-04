"""Nối Workflow Compiler (domain `workflow`) với Model Gateway (domain `agent`) và sandbox.

Chỉ được nạp trong tiến trình Agent Runtime: nó nạp `core.sandbox`, thứ contract 8 cấm ở
Control Plane. Không import `entrypoints/wiring.py` (contract 6).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.core.errors import ValidationError
from app.core.sandbox import DEFAULT_LIMITS, Limits, run
from app.domains.agent.application.ports import (
    ModelClient,
    ModelToolCall,
    ModelToolResult,
    ModelToolSpec,
    ModelTurn,
    StopReason,
)
from app.domains.workflow.application.compile_flow import (
    ANSWER_TOOL,
    ANSWERS_PROMPT,
    INTENT_PROMPT,
    INTENT_SCHEMA,
    INTENT_TOOL,
)
from app.domains.workflow.application.ports import AnswerAttempt, DialogueTurn, IntentReply

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


class SandboxRunner:
    """`TransformRunner` của domain `workflow`, bằng sandbox của Agent Runtime."""

    def __init__(self, limits: Limits = DEFAULT_LIMITS) -> None:
        self._limits = limits

    def run(self, code: str, args: Mapping[str, Any]) -> Any:
        return run(code, dict(args), self._limits).value
