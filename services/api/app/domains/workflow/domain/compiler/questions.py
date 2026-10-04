"""Tập câu hỏi một phiên biên dịch được hỏi, và hình dạng của câu trả lời."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from app.domains.workflow.domain.compiler.provenance import Element, LoopCandidate, SourceReport
from app.domains.workflow.domain.compiler.skeleton import Skeleton, SkeletonStep, StepKind
from app.domains.workflow.domain.ir import ConditionOp, Path

QUESTION_SET_VERSION: Final = "1"

#: Schema input của từng tool, theo `(tool_id, version)`. Dữ liệu thuần: tầng này không
#: biết catalog là gì.
ToolShapes = Mapping[tuple[str, str], Mapping[str, Any]]

NODE_ID: Final = "^[a-z][a-z0-9_]{0,31}$"
INPUT_NAME: Final = "^[a-z][a-z0-9_]{0,63}$"
#: Kiểu kết quả mà một node `transform` do LLM viết được khai.
RESULT_TYPES: Final = ("integer", "number", "boolean", "string", "array", "object")
#: Trần số node llm trong một câu trả lời. Một conversation trải phẳng có thể cần mỗi
#: tin nhắn một node; quá con số này thì nó không còn là một workflow đọc được.
MAX_LLM_NODES: Final = 20
#: Một câu ngắn vì sao chọn vậy. Hiện cho người dùng, không ảnh hưởng thực thi.
REASON: Final = {"type": "string", "maxLength": 300}


class Choice(StrEnum):
    CONST = "const"
    INPUT = "workflow_input"
    FROM_STEP = "from_step"
    #: Bỏ hẳn tham số, để mặc định trong tài liệu tool quyết định.
    TOOL_DEFAULT = "mac_dinh_cua_tool"
    #: Một node `llm` đứng trước sinh ra giá trị này.
    LLM = "llm"


@dataclass(frozen=True, slots=True)
class Candidate:
    """Một chỗ trong output của bước trước mà giá trị có thể đã được lấy từ đó."""

    step: str
    #: Đường trong output thô của bước; với bước `transform` nó bắt đầu bằng `result`.
    path: Path
    exact: bool = True
    #: Có thì giá trị nằm trong một phần tử mảng, chọn được theo chỉ số hoặc theo khoá.
    element: Element | None = None


@dataclass(frozen=True, slots=True)
class KeepQuestion:
    """Nhiệm vụ có cần bước này không."""

    step: str


@dataclass(frozen=True, slots=True)
class SourceQuestion:
    """Một trường input của một bước lấy giá trị từ đâu."""

    id: str
    step: str
    #: Trường ở tầng trên cùng: binding của IR không đi sâu hơn thế.
    path: Path
    value: Any
    choices: tuple[Choice, ...]
    candidates: tuple[Candidate, ...] = ()
    #: Những lượt mà người dùng đã nói ra giá trị này.
    said_in: tuple[int, ...] = ()

    @property
    def field(self) -> str:
        return self.path[-1]


@dataclass(frozen=True, slots=True)
class LoopQuestion:
    id: str
    loop: LoopCandidate


@dataclass(frozen=True, slots=True)
class DataRef:
    """Một phần output của một bước mà node `llm` hay `transform` được đọc."""

    step: str
    path: Path

    @property
    def key(self) -> str:
        return f"{self.step}:{'.'.join(self.path)}"


@dataclass(frozen=True, slots=True)
class QuestionSet:
    version: str
    keeps: tuple[KeepQuestion, ...]
    sources: tuple[SourceQuestion, ...]
    loops: tuple[LoopQuestion, ...]
    data: tuple[DataRef, ...]

    def of(self, step: str) -> tuple[SourceQuestion, ...]:
        return tuple(question for question in self.sources if question.step == step)

    def source(self, question_id: str) -> SourceQuestion | None:
        return next((q for q in self.sources if q.id == question_id), None)

    def loop_of(self, step: str) -> LoopQuestion | None:
        return next((q for q in self.loops if step in q.loop.steps), None)

    def ref(self, key: str) -> DataRef | None:
        return next((ref for ref in self.data if ref.key == key), None)


def build_questions(skeleton: Skeleton, report: SourceReport, tools: ToolShapes) -> QuestionSet:
    sources: list[SourceQuestion] = []
    data: list[DataRef] = []
    for step in skeleton.steps:
        for name, value in step.fields.items():
            sources.append(
                _source_question(f"N{len(sources) + 1}", step, name, value, report, tools)
            )
        if step.kind is StepKind.TRANSFORM:
            data.append(DataRef(step.ref, ("result",)))
        else:
            data.extend(DataRef(step.ref, (key,)) for key in step.output)

    # Vòng lặp chỉ dựng được khi trường thay đổi nằm ở tầng trên cùng của input.
    loops = [loop for loop in report.loops if len(loop.field) == 1]
    return QuestionSet(
        version=QUESTION_SET_VERSION,
        keeps=tuple(KeepQuestion(step.ref) for step in skeleton.steps),
        sources=tuple(sources),
        loops=tuple(LoopQuestion(f"V{n}", loop) for n, loop in enumerate(loops, start=1)),
        data=tuple(data),
    )


def _source_question(
    question_id: str,
    step: SkeletonStep,
    name: str,
    value: Any,
    report: SourceReport,
    tools: ToolShapes,
) -> SourceQuestion:
    path = (*step.root, name)
    choices = [Choice.CONST]
    candidates: tuple[Candidate, ...]
    said_in: tuple[int, ...] = ()

    if isinstance(value, Mapping | list) and value:
        candidates = tuple(
            Candidate(found.step, found.path, found.exact)
            for found in report.container(step.ref, path)
        )
        unsourced = not any(found.is_sourced for found in report.under(step.ref, path))
    else:
        found = report.of(step.ref, path)
        candidates = tuple(
            Candidate(source.step, source.path, source.exact, source.element)
            for source in found.steps
        )
        said_in = tuple(request.turn for request in found.requests)
        unsourced = not found.is_sourced and isinstance(value, str)
        if isinstance(value, str | int | float | bool):
            choices.append(Choice.INPUT)

    if candidates:
        choices.append(Choice.FROM_STEP)
    if _optional(step, name, tools):
        choices.append(Choice.TOOL_DEFAULT)
    if unsourced and not candidates:
        choices.append(Choice.LLM)

    return SourceQuestion(
        id=question_id,
        step=step.ref,
        path=path,
        value=value,
        choices=tuple(choices),
        candidates=candidates,
        said_in=said_in,
    )


def _optional(step: SkeletonStep, name: str, tools: ToolShapes) -> bool:
    if step.kind is not StepKind.TOOL or step.tool_version is None:
        return False
    schema = tools.get((step.tool_id, current_version(tools, step.tool_id, step.tool_version)))
    return schema is not None and name not in schema.get("required", ())


def current_version(tools: ToolShapes, tool_id: str, recorded: str) -> str:
    """Version để ghi vào workflow. Trace ghi bản tool lúc Agent chạy; nếu catalog không
    còn đúng bản đó thì lấy minor mới nhất của cùng major — minor chỉ thêm, không phá.
    Khác major thì giữ nguyên bản đã ghi, để validator báo đúng là không còn tool đó."""
    if (tool_id, recorded) in tools:
        return recorded
    major = recorded.partition(".")[0]
    minors = [
        int(version.partition(".")[2])
        for found, version in tools
        if found == tool_id and version.partition(".")[0] == major
    ]
    return f"{major}.{max(minors)}" if minors else recorded


# ─── Hình dạng của câu trả lời ───────────────────────────────────────────────


def answer_schema(questions: QuestionSet) -> dict[str, Any]:
    """JSON Schema của câu trả lời cho đúng tập câu hỏi này.

    Id câu hỏi và lựa chọn của từng câu đều là `enum`, nên một câu trả lời ngoài tập bị
    chặn ngay ở hình dạng — trước cả `check_answers`.
    """
    refs = [question.step for question in questions.keeps]
    data = [ref.key for ref in questions.data]
    node_id = {"type": "string", "pattern": NODE_ID}
    name = {"type": "string", "pattern": INPUT_NAME}

    value = {
        "oneOf": [
            _object({"chon": {"const": "du_lieu"}, "nguon": {"enum": data}}, ["chon", "nguon"]),
            _object({"chon": {"const": "const"}, "gia_tri": {}}, ["chon", "gia_tri"]),
            _object(
                {"chon": {"const": "workflow_input"}, "ten": name, "gia_tri": {}},
                ["chon", "ten", "gia_tri"],
            ),
        ]
    }

    return _object(
        {
            "ten": {"type": "string", "minLength": 1, "maxLength": 200},
            "buoc": _object(
                {
                    ref: _object(
                        {
                            "chon": {"enum": ["giu", "bo"]},
                            "id": node_id,
                            "ly_do": REASON,
                        },
                        ["chon"],
                    )
                    for ref in refs
                },
                refs,
            ),
            "gia_tri": _object(
                {question.id: _source_schema(question) for question in questions.sources}, []
            ),
            "vong_lap": _object(
                {
                    question.id: _object(
                        {"chon": {"enum": ["loop", "trai_phang"]}, "id": node_id, "ly_do": REASON},
                        ["chon"],
                    )
                    for question in questions.loops
                },
                [],
            ),
            "llm": {
                "type": "array",
                "maxItems": MAX_LLM_NODES,
                "items": _object(
                    {
                        "id": node_id,
                        "instructions": {"type": "string", "minLength": 1, "maxLength": 20000},
                        "dau_vao": {
                            "type": "object",
                            "propertyNames": {"pattern": INPUT_NAME},
                            "additionalProperties": {"enum": data},
                        },
                    },
                    ["id", "instructions", "dau_vao"],
                ),
            },
            "tinh_toan": {
                "type": "array",
                "maxItems": 10,
                "items": _object(
                    {
                        "id": node_id,
                        "sau_buoc": {"enum": refs},
                        "code": {"type": "string", "minLength": 1, "maxLength": 4096},
                        "ket_qua": {"enum": list(RESULT_TYPES)},
                        "args": {
                            "type": "object",
                            "propertyNames": {"pattern": INPUT_NAME},
                            "additionalProperties": value,
                        },
                    },
                    ["id", "sau_buoc", "code", "ket_qua", "args"],
                ),
            },
            "dieu_kien": {
                "type": "array",
                "maxItems": 10,
                "items": _object(
                    {
                        "id": node_id,
                        "truoc_buoc": {"enum": refs},
                        "trai": {"type": "string", "minLength": 1, "maxLength": 64},
                        "op": {"enum": [str(op) for op in ConditionOp]},
                        "phai": value,
                    },
                    ["id", "truoc_buoc", "trai", "op"],
                ),
            },
        },
        ["ten", "buoc", "gia_tri"],
    )


def _source_schema(question: SourceQuestion) -> dict[str, Any]:
    keys = sorted(
        {
            key.field
            for candidate in question.candidates
            if candidate.element is not None
            for key in candidate.element.keys
        }
    )
    # Mỗi trường chỉ có mặt khi lựa chọn dùng tới nó nằm trong tập: một trường thừa là
    # một chỗ để điền nhầm.
    properties: dict[str, Any] = {
        "chon": {"enum": [str(choice) for choice in question.choices]},
        "ly_do": REASON,
    }
    if Choice.INPUT in question.choices:
        properties["ten"] = {
            "type": "string",
            "pattern": INPUT_NAME,
            "description": "Chỉ khi chon = workflow_input: tên đầu vào.",
        }
    if Choice.LLM in question.choices:
        properties["node"] = {
            "type": "string",
            "pattern": NODE_ID,
            "description": "Chỉ khi chon = llm: id của node khai trong phần `llm`.",
        }
    if question.candidates:
        properties["ung_vien"] = {
            "type": "integer",
            "minimum": 1,
            "maximum": len(question.candidates),
            "description": "Chỉ khi chon = from_step: số thứ tự ứng viên.",
        }
    if keys:
        properties["khoa"] = {
            "enum": keys,
            "description": "Chỉ khi chọn phần tử theo một trường thay vì theo vị trí.",
        }
        properties["nguon_khoa"] = _object(
            {
                "chon": {"enum": [str(Choice.CONST), str(Choice.INPUT)]},
                "ten": {
                    "type": "string",
                    "pattern": INPUT_NAME,
                    "description": "Chỉ khi chon = workflow_input: tên đầu vào. Bỏ hẳn "
                    "trường này khi chon = const.",
                },
            },
            ["chon"],
        ) | {"description": "Giá trị của khoá là hằng hay đầu vào. Chỉ có `chon` và `ten`."}
    return _object(properties, ["chon"])


def _object(properties: Mapping[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": dict(properties),
    }
