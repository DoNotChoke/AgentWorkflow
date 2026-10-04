"""Chặng 2: nguyên liệu → khung. Mọi giá trị trong input của một bước có địa chỉ."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from app.domains.workflow.domain.compiler.source import SourceMaterial, SourceStep
from app.domains.workflow.domain.ir import Path

#: Tool dựng sẵn của Agent Runtime mà IR có node tương ứng.
TRANSFORM_TOOL: Final = "system.transform"


class StepKind(StrEnum):
    TOOL = "tool"
    #: Agent đã tự viết code và chạy nó trong sandbox.
    TRANSFORM = "transform"


@dataclass(frozen=True, slots=True)
class Literal:
    """Một giá trị lá trong input của một bước. `(step, path)` là địa chỉ của nó."""

    step: str
    #: Chỉ số mảng là chuỗi, như đường của binding trong IR: `("blocks", "0", "text")`.
    path: Path
    #: Vô hướng, hoặc một mảng/đối tượng rỗng.
    value: Any


@dataclass(frozen=True, slots=True)
class SkeletonStep:
    """Một bước sẽ thành một node, nếu được giữ."""

    ref: str
    turn: int
    kind: StepKind
    tool_id: str
    #: `None` với bước `transform`: nó không qua catalog.
    tool_version: str | None
    input: Mapping[str, Any]
    output: Mapping[str, Any]
    literals: tuple[Literal, ...]

    @property
    def root(self) -> Path:
        """Chỗ các binding của node nằm trong `input`: gốc với tool, `args` với transform."""
        return ("args",) if self.kind is StepKind.TRANSFORM else ()

    @property
    def fields(self) -> Mapping[str, Any]:
        """Những trường sẽ thành binding của node."""
        if self.kind is StepKind.TRANSFORM:
            return self.input.get("args") or {}
        return self.input

    @property
    def code(self) -> str:
        return str(self.input.get("code", "")).strip()


@dataclass(frozen=True, slots=True)
class Skeleton:
    conversation_id: str
    #: Tin nhắn mở lượt, theo số lượt. Lượt không đọc được tin nhắn thì vắng mặt.
    requests: Mapping[int, str]
    #: Theo đúng thứ tự đã chạy, xuyên qua các lượt.
    steps: tuple[SkeletonStep, ...]
    #: Tool chạy tại chỗ trong Agent Runtime mà IR chưa có node cho nó.
    unsupported: tuple[SourceStep, ...] = ()

    def step(self, ref: str) -> SkeletonStep:
        return next(step for step in self.steps if step.ref == ref)

    def literals(self) -> Iterator[Literal]:
        for step in self.steps:
            yield from step.literals


def build_skeleton(source: SourceMaterial) -> Skeleton:
    steps: list[SkeletonStep] = []
    unsupported: list[SourceStep] = []
    for turn in source.turns:
        for step in turn.steps:
            root: Path
            bound: Mapping[str, Any]
            if step.tool_id == TRANSFORM_TOOL:
                kind, root = StepKind.TRANSFORM, ("args",)
                bound = step.input.get("args") or {}
            elif step.tool_version is not None:
                kind, root, bound = StepKind.TOOL, (), step.input
            else:
                unsupported.append(step)
                continue
            steps.append(
                SkeletonStep(
                    ref=step.ref,
                    turn=turn.index,
                    kind=kind,
                    tool_id=step.tool_id,
                    tool_version=step.tool_version,
                    input=step.input,
                    output=step.output,
                    literals=tuple(
                        Literal(step=step.ref, path=path, value=value)
                        for path, value in leaves(bound, root)
                    ),
                )
            )
    return Skeleton(
        conversation_id=source.conversation_id,
        requests={turn.index: turn.request for turn in source.turns if turn.request},
        steps=tuple(steps),
        unsupported=tuple(unsupported),
    )


def leaves(value: Any, at: Path = ()) -> Iterator[tuple[Path, Any]]:
    """Mọi giá trị lá của một cây JSON, kèm đường tới nó."""
    yield from _leaves(value, at, top=True)


def _leaves(value: Any, at: Path, *, top: bool) -> Iterator[tuple[Path, Any]]:
    if isinstance(value, Mapping) and value:
        for key, inner in value.items():
            yield from _leaves(inner, (*at, str(key)), top=False)
    elif isinstance(value, list) and value:
        for index, inner in enumerate(value):
            yield from _leaves(inner, (*at, str(index)), top=False)
    elif not top:
        # Gốc rỗng không phải một giá trị: một input `{}` không gửi gì đi cả.
        yield at, value


def walk(value: Any, path: Path) -> Any:
    """Đi theo `path` trong một cây JSON đã biết là có đường đó."""
    for segment in path:
        value = value[int(segment)] if isinstance(value, list) else value[segment]
    return value
