"""Tài liệu cho model trả lời tập câu hỏi: ý đồ đã chấp nhận, các bước, các câu hỏi."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Final

from app.core.errors import ValidationError
from app.domains.workflow.domain.compiler.intent import Intent, render_intent
from app.domains.workflow.domain.compiler.provenance import Lane, LaneLink
from app.domains.workflow.domain.compiler.questions import (
    LOOP_ITEM,
    Candidate,
    LoopQuestion,
    QuestionSet,
    SourceQuestion,
)
from app.domains.workflow.domain.compiler.skeleton import Skeleton, SkeletonStep, StepKind

MAX_BRIEFING_CHARS: Final = 60_000
_VALUE: Final = 240
_SAMPLE: Final = 400


def briefing(
    skeleton: Skeleton,
    questions: QuestionSet,
    intent: Intent,
    *,
    max_chars: int = MAX_BRIEFING_CHARS,
) -> str:
    parts = ["# Ý đồ đã được người dùng chấp nhận", render_intent(intent), "# Các lượt"]
    parts.append(
        "\n".join(f"- Lượt {turn}: {text}" for turn, text in sorted(skeleton.requests.items()))
    )
    parts.append("# Các bước, theo thứ tự đã chạy")
    parts += [_step(step, questions) for step in skeleton.steps]
    if skeleton.unsupported:
        parts.append("# Bước chưa biên dịch được (không có câu hỏi)")
        parts.append("\n".join(f"- {step.ref}: {step.tool_id}" for step in skeleton.unsupported))
    if questions.loops:
        parts.append("# Cụm bước lặp trên một mảng")
        parts.append("\n".join(_loop(question, skeleton) for question in questions.loops))
    parts.append("# Dữ liệu mà node `llm` và `tinh_toan` được đọc")
    parts.append(", ".join(f"`{ref.key}`" for ref in questions.data) or "(không có)")

    text = "\n\n".join(parts)
    if len(text) > max_chars:
        raise ValidationError(
            f"Cuộc hội thoại này quá dài để biên dịch: tài liệu dài {len(text)} ký tự, "
            f"trần là {max_chars}.",
            details={"chars": len(text), "max_chars": max_chars, "steps": len(skeleton.steps)},
        )
    return text


def _step(step: SkeletonStep, questions: QuestionSet) -> str:
    lines = [f"## {step.ref} — {step.tool_id} (lượt {step.turn})"]
    if step.kind is StepKind.TRANSFORM:
        lines += ["Code Agent đã chạy:", "```python", step.code, "```"]
        lines.append(f"Kết quả: {_cut(step.output.get('result'), _SAMPLE)}")
    else:
        lines.append("Output:")
        lines += [f"- `{key}`: {_shape(value)}" for key, value in step.output.items()]
    asked = questions.of(step.ref)
    if asked:
        lines.append("Câu hỏi:")
        lines += [_question(question) for question in asked]
    else:
        lines.append("Bước này không gửi tham số nào.")
    return "\n".join(lines)


def _question(question: SourceQuestion) -> str:
    choices = " · ".join(str(choice) for choice in question.choices)
    said = (
        f"người dùng nói ra ở lượt {', '.join(str(turn) for turn in question.said_in)}"
        if question.said_in
        else "người dùng không nói ra giá trị này"
    )
    lines = [
        f"- **{question.id}** `{'.'.join(question.path)}` = {_cut(question.value, _VALUE)}",
        f"  - lựa chọn: {choices}",
        f"  - {said}",
    ]
    lines += [
        f"  - ứng viên {number}: {_candidate(candidate)}"
        for number, candidate in enumerate(question.candidates, start=1)
    ]
    return "\n".join(lines)


def _candidate(candidate: Candidate) -> str:
    text = f"`{candidate.step}` → `{'.'.join(candidate.path)}`"
    if not candidate.exact:
        text += " (cùng dữ liệu, khác cách viết hoặc đã bị cắt bớt trường)"
    element = candidate.element
    if element is None:
        return text
    if element.keys:
        keys = ", ".join(f"`{key.field}` = {_cut(key.value, 80)}" for key in element.keys)
        return f"{text} — phần tử thứ {element.index} của mảng; chọn được theo khoá: {keys}"
    return f"{text} — phần tử thứ {element.index} của mảng; chỉ chọn được theo vị trí"


def _loop(question: LoopQuestion, skeleton: Skeleton) -> str:
    loop = question.loop
    covered = "dùng hết mảng" if loop.covers_all else "chỉ dùng một phần mảng"
    text = (
        f"- **{question.id}**: {', '.join(loop.steps)} — `{'.'.join(loop.field)}` của từng bước "
        f"lấy từ `{loop.source}` → `{'.'.join(loop.array)}[*].{'.'.join(loop.pick)}`; {covered}"
    )
    if loop.filter:
        shown = ", ".join(f"`{name}` = {_cut(value, 60)}" for name, value in loop.filter)
        text += f"; các phần tử được dùng có chung {shown}"
    lines = [text, f"  - phần tử đang lặp đọc được qua `{question.id}:{LOOP_ITEM}`"]
    lines += [
        f"  - làn {number}: {_lane(lane, skeleton)}"
        for number, lane in enumerate(loop.lanes, start=1)
    ]
    return "\n".join(lines)


def _lane(lane: Lane, skeleton: Skeleton) -> str:
    tool = skeleton.step(lane.steps[0]).tool_id
    text = f"{', '.join(lane.steps)} (`{tool}`), mỗi bước ứng với một vòng"
    for link in lane.links:
        text += f"; `{link.field}` lấy từ {_lane_source(link)}"
    if lane.written:
        text += "; " + ", ".join(f"`{name}`" for name in lane.written) + " do Agent tự soạn"
    if not lane.links:
        text += (
            ". Không dữ liệu nào nối làn này với từng phần tử: chỉ có số bước bằng số vòng. "
            "Gộp khi ý đồ nói việc này làm cho từng phần tử"
        )
    return text


def _lane_source(link: LaneLink) -> str:
    if link.lane is None:
        return f"phần tử đang lặp → `{'.'.join(link.path)}`"
    source = "làn gốc" if link.lane == 0 else f"làn {link.lane}"
    if link.key is None:
        return f"{source} của cùng vòng → `{'.'.join(link.path)}`"
    name, value = link.key
    return (
        f"{source} của cùng vòng → phần tử của `{'.'.join(link.path)}` có `{name}` = "
        f"{_cut(value, 60)} → `{'.'.join(link.pick)}`"
    )


def _shape(value: Any) -> str:
    """Hình dạng và một mẫu: đủ để biết có gì ở đó, không đủ để chép lại."""
    if isinstance(value, list):
        if not value:
            return "mảng rỗng"
        return f"mảng {len(value)} phần tử; phần tử đầu: {_cut(value[0], _SAMPLE)}"
    if isinstance(value, Mapping):
        return f"đối tượng: {_cut(value, _SAMPLE)}"
    return _cut(value, _VALUE)


def _cut(value: Any, limit: int) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else f"{text[:limit]}… (đã cắt, {len(text)} ký tự)"
