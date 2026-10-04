"""Một câu trả lời thô → IR đã qua cả ba tầng kiểm, hoặc danh sách lỗi theo câu hỏi."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.core.errors import WorkflowDefinitionError
from app.domains.workflow.application.ports import ToolCatalogReader, TransformRunner
from app.domains.workflow.application.validation import validate
from app.domains.workflow.domain.compiler.answers import Answers, read_answers
from app.domains.workflow.domain.compiler.assemble import Assembled, assemble
from app.domains.workflow.domain.compiler.questions import QuestionSet, ToolShapes
from app.domains.workflow.domain.compiler.replay import replay
from app.domains.workflow.domain.compiler.report import (
    CompileIssue,
    CompileReport,
    Tier,
    from_answers,
    from_document,
)
from app.domains.workflow.domain.compiler.skeleton import Skeleton
from app.domains.workflow.domain.ir import parse


@dataclass(frozen=True, slots=True)
class Checked:
    report: CompileReport
    #: `None` khi câu trả lời sai hình dạng.
    answers: Answers | None = None
    #: `None` khi tầng 1 có lỗi: không có gì để lắp.
    assembled: Assembled | None = None


def check_answers(
    skeleton: Skeleton,
    questions: QuestionSet,
    raw: Mapping[str, Any],
    *,
    tools: ToolShapes,
    catalog: ToolCatalogReader,
    runner: TransformRunner,
    llm_model: str,
    loop_max_items: int = 50,
) -> Checked:
    """Tầng sau chỉ chạy khi tầng trước không có lỗi: đối chiếu một IR không hợp lệ với
    trace chỉ sinh ra lỗi ăn theo, che mất lỗi gốc."""
    answers, issues = read_answers(questions, skeleton, raw)
    report = from_answers(issues)
    if answers is None or not report.ok:
        return Checked(report, answers)

    assembled = assemble(
        skeleton, questions, answers, tools, llm_model=llm_model, loop_max_items=loop_max_items
    )
    try:
        ir = parse(assembled.document)
    except WorkflowDefinitionError as exc:
        # Bộ lắp dựng ra một tài liệu sai hình dạng: không câu trả lời nào có lỗi ở đây.
        broken = CompileIssue(Tier.DOCUMENT, "", "ir_sai_hinh_dang", exc.message)
        return Checked(report.merged(CompileReport((broken,))), answers, assembled)

    report = report.merged(from_document(Tier.DOCUMENT, validate(ir, catalog).issues, assembled))
    if not report.ok:
        return Checked(report, answers, assembled)

    found = replay(skeleton, assembled, runner.run)
    return Checked(report.merged(from_document(Tier.REPLAY, found, assembled)), answers, assembled)
