"""Một báo cáo cho cả ba tầng kiểm, mỗi lỗi mang id của câu hỏi đã gây ra nó.

Người trả lời chưa từng thấy IR. Một lỗi nói *"dang.input.channel không tồn tại"* không
giúp gì cho nó; một lỗi nói *"N2: …"* thì nó biết phải đổi câu trả lời nào.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from app.domains.workflow.domain.compiler.answers import AnswerIssue
from app.domains.workflow.domain.compiler.assemble import Assembled
from app.domains.workflow.domain.ir import Path
from app.domains.workflow.domain.validation import Issue, Severity


class Tier(StrEnum):
    #: Câu trả lời tự nó có dùng được không.
    ANSWERS = "cau_tra_loi"
    #: IR lắp ra có qua validator không.
    DOCUMENT = "tai_lieu"
    #: IR có tái tạo được lần chạy mẫu không.
    REPLAY = "doi_chieu"


@dataclass(frozen=True, slots=True)
class CompileIssue:
    tier: Tier
    #: Rỗng khi lỗi thuộc về cả câu trả lời, hoặc khi không truy được — xem `is_internal`.
    question: str
    code: str
    message: str
    severity: Severity = Severity.ERROR
    node: str | None = None
    field: Path = ()

    @property
    def is_internal(self) -> bool:
        """Lỗi của IR mà không câu trả lời nào chịu trách nhiệm: bộ lắp đã dựng sai.
        Đưa nó cho người trả lời thì chỉ khiến nó đổi bừa một câu không liên quan."""
        return self.tier is not Tier.ANSWERS and not self.question


@dataclass(frozen=True, slots=True)
class CompileReport:
    issues: tuple[CompileIssue, ...] = ()

    @property
    def errors(self) -> tuple[CompileIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[CompileIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is Severity.WARNING)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def internal(self) -> tuple[CompileIssue, ...]:
        return tuple(issue for issue in self.errors if issue.is_internal)

    def merged(self, other: CompileReport) -> CompileReport:
        return CompileReport(self.issues + other.issues)


def from_answers(issues: Iterable[AnswerIssue]) -> CompileReport:
    return CompileReport(
        tuple(
            CompileIssue(Tier.ANSWERS, issue.question, issue.code, issue.message)
            for issue in issues
        )
    )


def from_document(tier: Tier, issues: Iterable[Issue], assembled: Assembled) -> CompileReport:
    return CompileReport(tuple(_located(tier, issue, assembled) for issue in issues))


def _located(tier: Tier, issue: Issue, assembled: Assembled) -> CompileIssue:
    severity = issue.severity
    # Ngưỡng nằm trong code do người trả lời viết thì không có địa chỉ, không hỏi được
    # "hằng hay đầu vào" (ADR-0016, quyết định 4). Với code Agent đã chạy, vẫn là cảnh báo.
    if issue.code == "hang_viet_cung" and issue.node in assembled.written:
        severity = Severity.ERROR
    return CompileIssue(
        tier=tier,
        question=_question(issue, assembled),
        code=issue.code,
        message=issue.message,
        severity=severity,
        node=issue.node,
        field=issue.field,
    )


def _question(issue: Issue, assembled: Assembled) -> str:
    if issue.node is None:
        return ""
    # Từ trường cụ thể nhất lùi dần: `input.channel.where.name` → `input.channel`.
    for length in range(len(issue.field), 0, -1):
        found = assembled.origin.get((issue.node, issue.field[:length]))
        if found is not None:
            return found
    return assembled.owners.get(issue.node, "")
