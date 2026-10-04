"""Hai lời gọi model của một phiên biên dịch: hiểu ý đồ, rồi trả lời tập câu hỏi.

Không giữ trạng thái: phiên, lịch sử chat và lần xác nhận nằm ở người gọi.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from app.core.errors import ValidationError
from app.domains.workflow.application.compile_check import Checked
from app.domains.workflow.application.ports import (
    AnswerAdvisor,
    AnswerAttempt,
    DialogueTurn,
    IntentAdvisor,
)
from app.domains.workflow.domain.compiler.briefing import MAX_BRIEFING_CHARS, briefing
from app.domains.workflow.domain.compiler.digest import digest
from app.domains.workflow.domain.compiler.intent import (
    INTENT_SCHEMA,
    Intent,
    read_intent,
    render_intent,
)
from app.domains.workflow.domain.compiler.prompts import (
    ANSWERS_PROMPT,
    COMPILER_PROMPT_VERSION,
    INTENT_PROMPT,
)
from app.domains.workflow.domain.compiler.proposal import Proposal, build_proposal
from app.domains.workflow.domain.compiler.questions import QuestionSet, answer_schema
from app.domains.workflow.domain.compiler.skeleton import Skeleton
from app.domains.workflow.domain.compiler.source import SourceMaterial

__all__ = [
    "ANSWERS_PROMPT",
    "ANSWER_TOOL",
    "COMPILER_PROMPT_VERSION",
    "INTENT_PROMPT",
    "INTENT_SCHEMA",
    "INTENT_TOOL",
    "Built",
    "IntentOutcome",
    "build",
    "propose_intent",
]

#: Tên tool model gọi để nộp một đề xuất ý đồ.
INTENT_TOOL: Final = "de_xuat_y_do"
#: Tên tool model gọi để nộp câu trả lời cho tập câu hỏi.
ANSWER_TOOL: Final = "tra_loi"

#: Kiểm một câu trả lời thô. Người gọi gắn sẵn catalog, sandbox và model.
Check = Callable[[Mapping[str, Any]], Checked]


@dataclass(frozen=True, slots=True)
class IntentOutcome:
    #: `None` khi model hỏi lại thay vì đề xuất.
    intent: Intent | None
    #: Thứ hiện cho người dùng: đề xuất đã trình bày, hoặc câu model hỏi.
    message: str


async def propose_intent(
    source: SourceMaterial,
    dialogue: Sequence[DialogueTurn],
    advisor: IntentAdvisor,
    *,
    max_rounds: int = 3,
) -> IntentOutcome:
    """`dialogue` là phần chat của phiên biên dịch tính tới giờ, không gồm conversation nguồn.

    Đề xuất sai hình dạng thì lỗi quay lại model, như với câu trả lời. Phần qua lại đó
    không thuộc về phiên: người dùng chỉ thấy đề xuất đọc được.
    """
    summary = digest(source)
    history = list(dialogue)
    for attempt in range(1, max_rounds + 1):
        reply = await advisor.propose(digest=summary, dialogue=history)
        if reply.proposal is None:
            return IntentOutcome(None, reply.text)
        try:
            intent = read_intent(reply.proposal)
        except ValidationError as exc:
            if attempt == max_rounds:
                raise
            history += [
                DialogueTurn("assistant", json.dumps(reply.proposal, ensure_ascii=False)),
                DialogueTurn(
                    "user",
                    f"{exc.message} Gọi lại `{INTENT_TOOL}`: mỗi phần là một mảng các câu "
                    "ngắn, mảng rỗng nếu không có gì để ghi.",
                ),
            ]
            continue
        return IntentOutcome(intent, render_intent(intent))
    raise AssertionError("không tới được")  # pragma: no cover


@dataclass(frozen=True, slots=True)
class Built:
    checked: Checked
    #: Câu trả lời cuối cùng model nộp, kể cả khi còn lỗi.
    raw: Mapping[str, Any]
    #: Số lần model đã trả lời.
    rounds: int
    #: `None` khi câu trả lời cuối sai hình dạng: không có gì để trình bày.
    proposal: Proposal | None


async def build(
    skeleton: Skeleton,
    questions: QuestionSet,
    intent: Intent,
    advisor: AnswerAdvisor,
    check: Check,
    *,
    max_rounds: int = 3,
    max_briefing_chars: int = MAX_BRIEFING_CHARS,
    comment: str | None = None,
    previous: Mapping[str, Any] | None = None,
) -> Built:
    """Model trả lời, ba tầng kiểm chấm, lỗi quay lại model — tới khi sạch hoặc hết trần.

    `comment` và `previous` dùng khi người dùng muốn đổi một bảng giả định đã có: lời họ
    nói, và câu trả lời đang hiện.
    """
    document = briefing(skeleton, questions, intent, max_chars=max_briefing_chars)
    schema = answer_schema(questions)
    attempts: list[AnswerAttempt] = []

    while True:
        raw = await advisor.answer(
            briefing=document,
            schema=schema,
            attempts=attempts,
            comment=comment,
            previous=previous,
        )
        # Tầng 3 chạy code trong một tiến trình con và chờ nó.
        checked = await asyncio.to_thread(check, raw)
        report = checked.report
        # Lỗi của bộ lắp thì không câu trả lời nào sửa được: hỏi lại chỉ tốn lời gọi.
        if report.ok or report.internal or len(attempts) + 1 >= max_rounds:
            break
        attempts.append(
            AnswerAttempt(
                answers=raw,
                errors=tuple(
                    f"{issue.question or '(cả câu trả lời)'} [{issue.code}]: {issue.message}"
                    for issue in report.errors
                ),
            )
        )

    proposal = (
        build_proposal(skeleton, questions, intent, checked.answers, checked.assembled, report)
        if checked.answers is not None
        else None
    )
    return Built(checked=checked, raw=raw, rounds=len(attempts) + 1, proposal=proposal)
