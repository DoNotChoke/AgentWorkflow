"""Bảng giả định hiện cho người dùng trước khi tạo workflow.

Dựng từ chính các câu trả lời, không từ một đoạn văn model viết riêng: thứ người dùng
đọc luôn là thứ bộ lắp nhận (ADR-0015, quyết định 6). Lời giải thích là của người trả
lời; khung trình bày là của code.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Final

from app.domains.workflow.domain.compiler.answers import (
    Answers,
    SourceAnswer,
    ValueAnswer,
    body_order,
    included_lanes,
    is_loop_item,
    loop_filter,
    loop_id,
    subsumed,
)
from app.domains.workflow.domain.compiler.assemble import Assembled
from app.domains.workflow.domain.compiler.intent import Intent, render_intent
from app.domains.workflow.domain.compiler.provenance import LaneLink, LoopCandidate
from app.domains.workflow.domain.compiler.questions import (
    Choice,
    LoopQuestion,
    QuestionSet,
    SourceQuestion,
)
from app.domains.workflow.domain.compiler.report import CompileIssue, CompileReport
from app.domains.workflow.domain.compiler.skeleton import Skeleton, StepKind

_VALUE: Final = 120
_OPS: Final = {
    "eq": "bằng",
    "ne": "khác",
    "lt": "nhỏ hơn",
    "gt": "lớn hơn",
    "contains": "chứa",
    "exists": "có giá trị",
}


@dataclass(frozen=True, slots=True)
class Assumption:
    """Một trường của một node và điều đã giả định về nó."""

    question: str
    node: str
    field: str
    #: Giá trị ở lần chạy mẫu, đã cắt ngắn.
    value: str
    detail: str
    reason: str | None = None
    #: Phần tử được chọn theo vị trí trong mảng: thứ tự provider trả về đổi thì nó sai.
    by_position: bool = False


@dataclass(frozen=True, slots=True)
class DroppedStep:
    step: str
    tool_id: str
    turn: int
    reason: str | None


@dataclass(frozen=True, slots=True)
class WrittenText:
    """Văn bản do người trả lời viết mà executor sẽ chạy: phải hiện nguyên văn."""

    id: str
    kind: str
    text: str
    detail: str


@dataclass(frozen=True, slots=True)
class Proposal:
    intent: Intent
    name: str
    #: Đầu vào của workflow: tên → giá trị ở lần chạy mẫu.
    inputs: tuple[tuple[str, str], ...]
    fixed: tuple[Assumption, ...]
    linked: tuple[Assumption, ...]
    #: Tham số trace có gửi mà workflow để tool tự quyết (ADR-0016, quyết định 6).
    defaults: tuple[Assumption, ...]
    dropped: tuple[DroppedStep, ...]
    loops: tuple[str, ...]
    written: tuple[WrittenText, ...]
    conditions: tuple[str, ...]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]

    @property
    def is_ready(self) -> bool:
        return not self.errors


def build_proposal(
    skeleton: Skeleton,
    questions: QuestionSet,
    intent: Intent,
    answers: Answers,
    assembled: Assembled | None,
    report: CompileReport,
) -> Proposal:
    fixed: list[Assumption] = []
    linked: list[Assumption] = []
    defaults: list[Assumption] = []
    dropped: list[DroppedStep] = []

    for step in skeleton.steps:
        answer = answers.steps.get(step.ref)
        if answer is None or not answer.keep:
            dropped.append(
                DroppedStep(step.ref, step.tool_id, step.turn, answer.reason if answer else None)
            )
            continue
        if subsumed(questions, answers, step.ref):
            continue
        for question in questions.of(step.ref):
            chosen = answers.sources.get(question.id)
            if chosen is None or is_loop_item(questions, answers, question):
                continue
            item = _assumption(question, chosen, answers)
            if chosen.choice is Choice.TOOL_DEFAULT:
                defaults.append(item)
            elif chosen.choice in {Choice.CONST, Choice.INPUT}:
                fixed.append(item)
            else:
                linked.append(item)

    samples = assembled.samples if assembled is not None else {}
    return Proposal(
        intent=intent,
        name=answers.name,
        inputs=tuple((name, _cut(value)) for name, value in samples.items()),
        fixed=tuple(fixed),
        linked=tuple(linked),
        defaults=tuple(defaults),
        dropped=tuple(dropped),
        loops=tuple(_loops(skeleton, questions, answers)),
        written=tuple(_written(skeleton, answers)),
        conditions=tuple(_conditions(answers)),
        warnings=tuple(_warnings(report)),
        errors=tuple(f"{issue.question or '(chung)'}: {issue.message}" for issue in report.errors),
    )


def _warnings(report: CompileReport) -> list[str]:
    """Mười ba cảnh báo cùng loại là một điều cần biết, không phải mười ba dòng."""
    by_code: dict[str, list[CompileIssue]] = {}
    for issue in report.warnings:
        by_code.setdefault(issue.code, []).append(issue)
    lines: list[str] = []
    for issues in by_code.values():
        first = issues[0]
        if len(issues) == 1:
            lines.append(f"{first.question}: {first.message}")
        else:
            where = ", ".join(issue.question for issue in issues)
            lines.append(f"{len(issues)} chỗ ({where}), ví dụ: {first.message}")
    return lines


def _assumption(question: SourceQuestion, chosen: SourceAnswer, answers: Answers) -> Assumption:
    match chosen.choice:
        case Choice.CONST:
            detail = "cố định"
        case Choice.INPUT:
            detail = f"đầu vào `{chosen.name}`"
        case Choice.TOOL_DEFAULT:
            detail = "bỏ, để tool dùng mặc định của nó"
        case Choice.LLM:
            detail = f"do node llm `{chosen.node}` sinh ra"
        case Choice.FROM_STEP:
            detail = _link(question, chosen, answers)
    return Assumption(
        question=question.id,
        node=answers.node_id(question.step),
        field=question.field,
        value=_cut(question.value),
        detail=detail,
        reason=chosen.reason,
        by_position=chosen.choice is Choice.FROM_STEP and _by_position(question, chosen),
    )


def _by_position(question: SourceQuestion, chosen: SourceAnswer) -> bool:
    assert chosen.candidate is not None
    return question.candidates[chosen.candidate - 1].element is not None and chosen.key is None


def _link(question: SourceQuestion, chosen: SourceAnswer, answers: Answers) -> str:
    assert chosen.candidate is not None
    candidate = question.candidates[chosen.candidate - 1]
    source = f"`{answers.node_id(candidate.step)}`"
    element = candidate.element
    if element is None:
        return f"lấy từ {source} → `{'.'.join(candidate.path)}`"
    array = ".".join(element.array)
    if chosen.key is None:
        return f"lấy từ {source}: phần tử thứ {element.index} của `{array}` (chọn theo vị trí)"
    key = next(key for key in element.keys if key.field == chosen.key)
    wanted = (
        f"đầu vào `{chosen.key_name}`" if chosen.key_choice is Choice.INPUT else _cut(key.value)
    )
    return f"lấy từ {source}: phần tử của `{array}` có `{key.field}` = {wanted}"


def _loops(skeleton: Skeleton, questions: QuestionSet, answers: Answers) -> list[str]:
    lines: list[str] = []
    for question in questions.loops:
        answer = answers.loops.get(question.id)
        if answer is None or not answers.keeps(question.loop.steps[0]):
            continue
        source = f"`{answers.node_id(question.loop.source)}` → `{'.'.join(question.loop.array)}`"
        if answer.as_loop:
            lines.append(
                f"`{loop_id(question.id, answers)}`: lặp trên từng phần tử của {source} "
                f"(lần chạy mẫu: {len(question.loop.steps)} phần tử)"
                + "".join(
                    f"\n  - {line}"
                    for line in (
                        *_filtering(question, answers),
                        *_lanes(skeleton, questions, question, answers),
                    )
                )
            )
        else:
            lines.append(
                f"{len(question.loop.steps)} bước lấy từ {source} được giữ riêng, "
                "mỗi bước một phần tử cố định"
            )
    return lines


def _filtering(question: LoopQuestion, answers: Answers) -> list[str]:
    """Vòng lặp chạy trên phần tử nào. Nói cả khi không lọc: đó cũng là một giả định."""
    loop = question.loop
    chosen = loop_filter(question, answers)
    if chosen:
        shown = ", ".join(f"`{name}` = {_cut(value)}" for name, value in chosen)
        return [f"chỉ những phần tử có {shown}; phần tử khác trong mảng bị bỏ qua"]
    if loop.suggested:
        shown = ", ".join(f"`{name}` = {_cut(value)}" for name, value in loop.suggested)
        return [
            f"chạy trên **mọi** phần tử của mảng, không lọc. Ở lần chạy mẫu phần tử nào cũng "
            f"có {shown}; phần tử loại khác, nếu có, cũng sẽ được xử lý như vậy"
        ]
    return []


def _lanes(
    skeleton: Skeleton, questions: QuestionSet, question: LoopQuestion, answers: Answers
) -> list[str]:
    """Thân vòng lặp nhiều bước: thứ tự chạy, và mỗi mối nối suy ra từ lần chạy mẫu."""
    loop = question.loop
    sampled = included_lanes(question, answers)
    lanes = body_order(questions, question, answers)
    if len(lanes) < 2:
        return []
    names = {lane: f"`{answers.node_id(loop.lane_steps(lane)[0])}`" for lane in lanes}
    running = f"mỗi vòng chạy lần lượt {' → '.join(names[lane] for lane in lanes)}"
    if lanes != sampled:
        running += ". Các bước chỉ đọc được đưa lên trước các bước ghi ra ngoài"
    order = {step.ref: position for position, step in enumerate(skeleton.steps)}
    # Lần chạy mẫu đã sang vòng hai của làn gốc trước khi một làn khác bắt đầu.
    if any(order[loop.lane_steps(lane)[0]] > order[loop.steps[1]] for lane in sampled[1:]):
        running += (
            f". Lần chạy mẫu thì làm xong từng việc cho cả {len(loop.steps)} phần tử rồi mới "
            "sang việc sau"
        )
    lines = [running]
    for lane in sampled[1:]:
        found = loop.lanes[lane - 1]
        lines += [
            f"{names[lane]}.{link.field}: {_lane_source(loop, link, names)}" for link in found.links
        ]
        if not found.links:
            lines.append(
                f"{names[lane]} nằm trong vòng lặp vì nó chạy đúng {len(found.steps)} lần; không "
                "dữ liệu nào nối nó với từng phần tử"
            )
    return lines


def _lane_source(loop: LoopCandidate, link: LaneLink, names: dict[int, str]) -> str:
    if link.lane is None:
        return f"lấy từ phần tử đang lặp → `{'.'.join(link.path)}`"
    source = f"lấy từ {names[link.lane]} của cùng vòng"
    if link.key is None:
        return f"{source} → `{'.'.join(link.path)}`"
    name, value = link.key
    return (
        f"{source}: phần tử của `{'.'.join(link.path)}` có `{name}` = {_cut(value)} (khoá suy "
        f"ra từ lần chạy mẫu: cả {len(loop.steps)} vòng đều chọn đúng phần tử đó)"
    )


def _written(skeleton: Skeleton, answers: Answers) -> list[WrittenText]:
    texts = [
        WrittenText(
            id=llm.id,
            kind="instructions",
            text=llm.instructions,
            detail="đọc " + ", ".join(f"`{key}`" for key in llm.inputs.values()),
        )
        for llm in answers.llms
    ]
    texts += [
        WrittenText(
            id=compute.id,
            kind="code",
            text=compute.code,
            detail=f"trả về {compute.result}; "
            + ", ".join(f"`{name}` = {_value(value)}" for name, value in compute.args.items()),
        )
        for compute in answers.computes
    ]
    # Code Agent đã chạy cũng là thứ executor sẽ chạy: người dùng có quyền thấy nó.
    texts += [
        WrittenText(
            id=answers.node_id(step.ref),
            kind="code",
            text=step.code,
            detail="code Agent đã chạy trong hội thoại",
        )
        for step in skeleton.steps
        if step.kind is StepKind.TRANSFORM and answers.keeps(step.ref)
    ]
    return texts


def _conditions(answers: Answers) -> list[str]:
    lines: list[str] = []
    for condition in answers.conditions:
        text = f"Chỉ chạy từ `{answers.node_id(condition.before)}` trở đi khi `{condition.left}` "
        text += _OPS[str(condition.op)]
        if condition.right is not None:
            text += f" {_value(condition.right)}"
        lines.append(text)
    return lines


def _value(value: ValueAnswer) -> str:
    if value.kind == "du_lieu":
        return f"`{value.ref}`"
    if value.kind == "workflow_input":
        return f"đầu vào `{value.name}` (lần chạy mẫu: {_cut(value.value)})"
    return _cut(value.value)


def _cut(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= _VALUE else f"{text[:_VALUE]}…"


# ─── Trình bày ───────────────────────────────────────────────────────────────


def render_proposal(proposal: Proposal) -> str:
    parts = [f"# {proposal.name}", render_intent(proposal.intent)]

    if proposal.inputs:
        parts.append(
            "**Đầu vào mỗi lần chạy**\n"
            + "\n".join(f"- `{name}` — lần chạy mẫu: {sample}" for name, sample in proposal.inputs)
        )
    parts += _table("Lấy từ bước trước", proposal.linked)
    parts += _table("Cố định hoặc là đầu vào", proposal.fixed)
    parts += _table("Để tool tự quyết", proposal.defaults)
    if proposal.loops:
        parts.append("**Vòng lặp**\n" + "\n".join(f"- {line}" for line in proposal.loops))
    if proposal.conditions:
        parts.append(
            "**Điều kiện đã dựng**\n" + "\n".join(f"- {line}" for line in proposal.conditions)
        )
    for text in proposal.written:
        fence = "python" if text.kind == "code" else "text"
        parts.append(f"**`{text.id}`** — {text.detail}\n```{fence}\n{text.text}\n```")
    if proposal.dropped:
        parts.append(
            "**Bước bị bỏ**\n"
            + "\n".join(
                f"- lượt {step.turn}, `{step.tool_id}`: {step.reason or '(không nêu lý do)'}"
                for step in proposal.dropped
            )
        )
    if proposal.warnings:
        parts.append("**Cần để ý**\n" + "\n".join(f"- {line}" for line in proposal.warnings))
    if proposal.errors:
        parts.append(
            "**Chưa dựng được workflow hợp lệ**\n"
            + "\n".join(f"- {line}" for line in proposal.errors)
        )
    return "\n\n".join(parts)


def proposal_document(proposal: Proposal) -> dict[str, Any]:
    """Bảng giả định ở dạng dữ liệu, để lưu và để giao diện dựng thẻ."""
    document = asdict(proposal)
    document["is_ready"] = proposal.is_ready
    # Qua JSON một vòng: tuple thành mảng, đúng thứ sẽ đọc lại từ kho.
    return json.loads(json.dumps(document, ensure_ascii=False))  # type: ignore[no-any-return]


def _table(title: str, items: tuple[Assumption, ...]) -> list[str]:
    if not items:
        return []
    lines = [f"**{title}**"]
    for item in items:
        line = f"- `{item.node}.{item.field}` = {item.value} — {item.detail}"
        if item.reason:
            line += f". {item.reason}"
        lines.append(line)
    return ["\n".join(lines)]
