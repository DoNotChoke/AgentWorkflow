"""Chạy một node `llm`: hỏi model, kiểm kết quả với `output_schema`, sai thì hỏi lại."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import jsonschema

from app.core.errors import WorkflowExecutionError
from app.domains.workflow.application.ports import NodeModel, Rejected
from app.domains.workflow.domain.llm_node import RESULT_TOOL, system_prompt

__all__ = ["RESULT_TOOL", "LlmOutcome", "run_llm_node"]


@dataclass(frozen=True, slots=True)
class LlmOutcome:
    output: dict[str, Any]
    input_tokens: int
    output_tokens: int
    #: Số lần đã hỏi model.
    attempts: int


async def run_llm_node(
    models: NodeModel,
    *,
    model: str,
    instructions: str,
    data: Mapping[str, Any],
    output_schema: Mapping[str, Any],
    max_attempts: int = 3,
) -> LlmOutcome:
    """Lỗi của provider đi thẳng ra ngoài để Temporal thử lại. Kết quả sai kiểu thì hỏi lại
    ngay ở đây, tối đa `max_attempts` lần tính cả lần đầu — mỗi lần là một lời gọi có tính
    tiền, và trong thân một vòng lặp con số này nhân với số phần tử."""
    system = system_prompt(instructions)
    payload = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    validator = jsonschema.Draft202012Validator(output_schema)

    rejected: list[Rejected] = []
    spent_in = spent_out = 0
    for attempt in range(1, max_attempts + 1):
        answer = await models.answer(
            model=model,
            system=system,
            data=payload,
            output_schema=output_schema,
            rejected=rejected,
        )
        spent_in += answer.input_tokens
        spent_out += answer.output_tokens

        if answer.truncated:
            raise WorkflowExecutionError(
                "Câu trả lời của model bị cắt ở trần token trước khi nộp xong kết quả.",
                details={"model": model, "attempts": attempt},
            )
        if answer.output is None:
            reason = f"Bạn chưa gọi `{RESULT_TOOL}`. Nộp kết quả bằng một lời gọi tool đó."
        else:
            error = jsonschema.exceptions.best_match(validator.iter_errors(answer.output))
            if error is None:
                return LlmOutcome(dict(answer.output), spent_in, spent_out, attempt)
            where = "/".join(str(part) for part in error.absolute_path) or "(gốc)"
            reason = f"Kết quả sai kiểu ở {where}: {error.message}"
        rejected.append(Rejected(answer.output, reason))

    raise WorkflowExecutionError(
        f"Model không nộp được kết quả đúng kiểu sau {max_attempts} lần. Lần cuối: "
        f"{rejected[-1].reason}",
        details={"model": model, "attempts": max_attempts},
    )
