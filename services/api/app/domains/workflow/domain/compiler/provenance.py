"""Mỗi giá trị trong khung đến từ đâu.

Chỉ nêu ứng viên, không chọn: một giá trị có thể vừa có trong câu người dùng vừa có
trong output của một bước trước, và cái nào đúng ý là việc của chặng sau.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Final

from app.domains.workflow.domain.compiler.skeleton import (
    Literal,
    Skeleton,
    SkeletonStep,
    leaves,
    walk,
)
from app.domains.workflow.domain.ir import Path

#: Chuỗi ngắn hơn thế này trùng với một output trước là do tình cờ, không do phụ thuộc.
_MIN_STEP_MATCH: Final = 3
#: Một id có thể xuất hiện ở nhiều chỗ trong cùng một output; vài chỗ đầu là đủ để hỏi.
_MAX_PATHS_PER_STEP: Final = 3
_MAX_STEP_SOURCES: Final = 8
_UUID: Final = re.compile(
    r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class FromRequest:
    """Giá trị có trong tin nhắn mở lượt `turn`."""

    turn: int
    #: Giá trị không phải chuỗi; thứ khớp là dạng viết của nó (`10` với "10 tin nhắn").
    coerced: bool = False


@dataclass(frozen=True, slots=True)
class WhereKey:
    """Một trường của phần tử mà người dùng đã gọi tên: phần tử `name == "test"`."""

    field: str
    value: Any
    turn: int


@dataclass(frozen=True, slots=True)
class Element:
    """Giá trị nằm trong một phần tử của mảng `array`."""

    array: Path
    index: int
    #: Đường từ phần tử tới giá trị.
    pick: Path
    #: Rỗng thì chỉ còn cách trỏ bằng chỉ số.
    keys: tuple[WhereKey, ...] = ()


@dataclass(frozen=True, slots=True)
class FromStep:
    """Giá trị y hệt một giá trị trong output của bước `step`, đứng trước nó."""

    step: str
    path: Path
    element: Element | None = None
    #: `False` khi hai bên chỉ là cùng một UUID viết hai kiểu, có và không có gạch nối.
    exact: bool = True


@dataclass(frozen=True, slots=True)
class Sources:
    literal: Literal
    requests: tuple[FromRequest, ...] = ()
    steps: tuple[FromStep, ...] = ()

    @property
    def is_sourced(self) -> bool:
        return bool(self.requests or self.steps)


@dataclass(frozen=True, slots=True)
class LaneLink:
    """Một trường của làn lấy giá trị từ đâu, trong cùng một vòng."""

    field: str
    #: Làn nguồn: 0 là làn gốc. `None` là chính phần tử đang lặp.
    lane: int | None
    #: Đường trong output của bước nguồn, hoặc trong phần tử đang lặp.
    path: Path
    #: Có thì `path` trỏ vào một mảng, và phần tử được chọn là phần tử đầu tiên có trường
    #: này mang giá trị này. Suy ra từ dữ liệu: mọi vòng đều chọn đúng phần tử như vậy.
    key: tuple[str, Any] | None = None
    pick: Path = ()


class LaneEvidence(StrEnum):
    #: Bước thứ k đọc dữ liệu của đúng vòng thứ k.
    DATA = "du_lieu"
    #: Chỉ có số bước bằng số vòng; thứ thay đổi là nội dung model tự soạn.
    POSITION = "vi_tri"


@dataclass(frozen=True, slots=True)
class Lane:
    """Một nhóm bước cùng tool, mỗi bước ứng với một vòng của vòng lặp."""

    steps: tuple[str, ...]
    links: tuple[LaneLink, ...] = ()
    #: Trường thay đổi qua từng vòng mà không có nguồn: model đã tự soạn.
    written: tuple[str, ...] = ()

    @property
    def evidence(self) -> LaneEvidence:
        return LaneEvidence.DATA if self.links else LaneEvidence.POSITION


@dataclass(frozen=True, slots=True)
class LoopCandidate:
    """Các bước liền nhau gọi cùng một tool, mỗi bước cho một phần tử của cùng một mảng."""

    steps: tuple[str, ...]
    #: Trường input duy nhất khác nhau giữa các bước.
    field: Path
    source: str
    array: Path
    pick: Path
    indices: tuple[int, ...]
    #: Mọi phần tử của mảng đều được dùng.
    covers_all: bool
    #: Trường mà mọi phần tử được dùng có chung giá trị, và không phần tử bị bỏ nào có.
    filter: tuple[tuple[str, Any], ...] = ()
    #: Khi vòng lặp dùng hết mảng: trường nói về *loại* mà mọi phần tử có chung. Lần chạy
    #: mẫu không cần lọc theo nó; lần chạy khác thì có thể, nên đây là đề nghị cho người
    #: trả lời, không phải một phần của vòng lặp.
    suggested: tuple[tuple[str, Any], ...] = ()
    #: Những nhóm bước khác chạy một lần cho mỗi vòng. `steps` ở trên là làn 0.
    lanes: tuple[Lane, ...] = ()

    def lane_steps(self, lane: int) -> tuple[str, ...]:
        return self.steps if lane == 0 else self.lanes[lane - 1].steps


@dataclass(frozen=True, slots=True)
class FromContainer:
    """Cả một mảng hay đối tượng trong output của bước `step`."""

    step: str
    path: Path
    #: `False` khi bên nhận chỉ giữ một phần các trường: model chép dữ liệu bằng tay và
    #: bỏ đi trường nó không cần. Cấu trúc và thứ tự phần tử vẫn giống hệt.
    exact: bool = True


@dataclass(frozen=True, slots=True)
class ContainerSources:
    """Một trường input là mảng hay đối tượng, và những chỗ nó có thể đã được lấy nguyên."""

    step: str
    path: Path
    sources: tuple[FromContainer, ...]


@dataclass(frozen=True, slots=True)
class SourceReport:
    literals: tuple[Sources, ...]
    loops: tuple[LoopCandidate, ...] = ()
    containers: tuple[ContainerSources, ...] = ()

    def of(self, step: str, path: Path) -> Sources:
        return next(
            found
            for found in self.literals
            if found.literal.step == step and found.literal.path == path
        )

    def under(self, step: str, path: Path) -> tuple[Sources, ...]:
        """Mọi giá trị lá nằm trong trường `path` của bước `step`."""
        return tuple(
            found
            for found in self.literals
            if found.literal.step == step and found.literal.path[: len(path)] == path
        )

    def container(self, step: str, path: Path) -> tuple[FromContainer, ...]:
        for found in self.containers:
            if found.step == step and found.path == path:
                return found.sources
        return ()


def find_sources(skeleton: Skeleton) -> SourceReport:
    found: list[Sources] = []
    containers: list[ContainerSources] = []
    #: Bước đã đi qua, gần nhất trước, kèm chỉ mục giá trị → chỗ của nó trong output.
    earlier: list[tuple[SkeletonStep, _Index]] = []

    for step in skeleton.steps:
        for name, value in step.fields.items():
            if isinstance(value, Mapping | list) and value:
                containers.append(
                    ContainerSources(
                        step=step.ref,
                        path=(*step.root, name),
                        sources=_container_sources(value, [prior for prior, _ in earlier]),
                    )
                )
        said = {turn: text for turn, text in skeleton.requests.items() if turn <= step.turn}
        for literal in step.literals:
            found.append(
                Sources(
                    literal=literal,
                    requests=tuple(
                        FromRequest(turn=turn, coerced=not isinstance(literal.value, str))
                        for turn, text in sorted(said.items())
                        if _mentions(text, literal.value)
                    ),
                    steps=_step_sources(literal, earlier, said),
                )
            )
        earlier.insert(0, (step, _index(step)))

    return SourceReport(
        literals=tuple(found), loops=_loops(skeleton, found), containers=tuple(containers)
    )


# ─── Câu người dùng ──────────────────────────────────────────────────────────


def _mentions(text: str, value: Any) -> bool:
    """`value` có mặt trong `text` như một từ trọn vẹn, không phải một mẩu của từ khác."""
    if isinstance(value, bool) or not isinstance(value, str | int):
        return False
    needle = str(value)
    if not needle.strip():
        return False

    start = text.find(needle)
    while start != -1:
        end = start + len(needle)
        before = text[start - 1] if start else ""
        after = text[end] if end < len(text) else ""
        if not (needle[0].isalnum() and before.isalnum()) and not (
            needle[-1].isalnum() and after.isalnum()
        ):
            return True
        start = text.find(needle, start + 1)
    return False


# ─── Output của bước trước ───────────────────────────────────────────────────


#: Giá trị đã chuẩn hoá → những chỗ nó xuất hiện, kèm đúng dạng viết ở chỗ đó.
_Index = dict[str, list[tuple[Path, str]]]


def _canonical(value: str) -> str:
    return value.replace("-", "").lower() if _UUID.match(value) else value


def _index(step: SkeletonStep) -> _Index:
    # Output chép lại input (`post_message` trả `channel_id`) không phải nguồn của giá
    # trị đó: nguồn thật đứng trước nữa, và chỉ mục của nó vẫn còn đây.
    echoed = {
        _canonical(literal.value) for literal in step.literals if isinstance(literal.value, str)
    }
    index: _Index = {}
    for path, value in leaves(step.output):
        if not isinstance(value, str) or len(value) < _MIN_STEP_MATCH:
            continue
        key = _canonical(value)
        if key not in echoed:
            index.setdefault(key, []).append((path, value))
    return index


def _step_sources(
    literal: Literal,
    earlier: Sequence[tuple[SkeletonStep, _Index]],
    said: Mapping[int, str],
) -> tuple[FromStep, ...]:
    if not isinstance(literal.value, str):
        return ()
    sources: list[FromStep] = []
    for step, index in earlier:
        for path, written in index.get(_canonical(literal.value), ())[:_MAX_PATHS_PER_STEP]:
            sources.append(
                FromStep(
                    step=step.ref,
                    path=path,
                    element=_element(step.output, path, said),
                    exact=written == literal.value,
                )
            )
            if len(sources) == _MAX_STEP_SOURCES:
                return tuple(sources)
    return tuple(sources)


def _element(output: Mapping[str, Any], path: Path, said: Mapping[int, str]) -> Element | None:
    """Tách `path` ở mảng trong cùng mà nó đi qua. `where` của IR chỉ chọn được một tầng."""
    containers: list[Any] = [output]
    for segment in path[:-1]:
        parent = containers[-1]
        containers.append(parent[int(segment)] if isinstance(parent, list) else parent[segment])

    for depth in range(len(path) - 1, -1, -1):
        array = containers[depth]
        if not isinstance(array, list):
            continue
        index = int(path[depth])
        pick = path[depth + 1 :]
        return Element(
            array=path[:depth],
            index=index,
            pick=pick,
            keys=_where_keys(array, index, pick, said),
        )
    return None


def _where_keys(
    array: Sequence[Any], index: int, pick: Path, said: Mapping[int, str]
) -> tuple[WhereKey, ...]:
    element = array[index]
    if not isinstance(element, Mapping):
        return ()
    keys: list[WhereKey] = []
    for field, value in element.items():
        if pick == (field,):
            continue
        turn = next((turn for turn, text in sorted(said.items()) if _mentions(text, value)), None)
        if turn is None:
            continue
        # `where` lấy phần tử đầu tiên khớp: khoá chỉ dùng được nếu phần tử đó chính là nó.
        first = next(
            position
            for position, other in enumerate(array)
            if isinstance(other, Mapping) and other.get(field) == value
        )
        if first == index:
            keys.append(WhereKey(field=field, value=value, turn=turn))
    return tuple(keys)


# ─── Cả một mảng hay đối tượng ───────────────────────────────────────────────


def _container_sources(value: Any, earlier: Sequence[SkeletonStep]) -> tuple[FromContainer, ...]:
    # Không có chuỗi nào đủ dài để nhận diện thì trùng nhau là do tình cờ: `[1, 2]`.
    if not any(isinstance(leaf, str) and len(leaf) >= _MIN_STEP_MATCH for _, leaf in leaves(value)):
        return ()
    sources: list[FromContainer] = []
    for step in earlier:
        found = [
            FromContainer(step=step.ref, path=path, exact=candidate == value)
            for path, candidate in _containers(step.output, ())
            if projects(value, candidate)
        ]
        # Bản giống hệt đứng trước bản chỉ giống một phần.
        found.sort(key=lambda source: not source.exact)
        sources.extend(found[:_MAX_PATHS_PER_STEP])
        if len(sources) >= _MAX_STEP_SOURCES:
            break
    return tuple(sources[:_MAX_STEP_SOURCES])


def _containers(value: Any, at: Path) -> Iterator[tuple[Path, Any]]:
    if isinstance(value, Mapping):
        yield at, value
        for key, inner in value.items():
            yield from _containers(inner, (*at, str(key)))
    elif isinstance(value, list):
        yield at, value
        for index, inner in enumerate(value):
            yield from _containers(inner, (*at, str(index)))


def projects(kept: Any, whole: Any) -> bool:
    """`kept` là `whole` sau khi bỏ bớt trường: cùng hình dạng, cùng thứ tự, cùng giá trị."""
    if isinstance(kept, list):
        return (
            isinstance(whole, list)
            and len(kept) == len(whole)
            and all(projects(a, b) for a, b in zip(kept, whole, strict=True))
        )
    if isinstance(kept, Mapping):
        return isinstance(whole, Mapping) and all(
            key in whole and projects(inner, whole[key]) for key, inner in kept.items()
        )
    if isinstance(kept, str) and isinstance(whole, str):
        return _canonical(kept) == _canonical(whole)
    return type(kept) is type(whole) and kept == whole


# ─── Vòng lặp ────────────────────────────────────────────────────────────────


def _loops(skeleton: Skeleton, found: Sequence[Sources]) -> tuple[LoopCandidate, ...]:
    by_step: dict[str, dict[Path, Sources]] = {}
    for sources in found:
        by_step.setdefault(sources.literal.step, {})[sources.literal.path] = sources

    loops: list[LoopCandidate] = []
    steps = skeleton.steps
    start = 0
    while start < len(steps):
        best: LoopCandidate | None = None
        end = start + 2
        while end <= len(steps) and _same_call(steps[start], steps[end - 1]):
            candidate = _loop(skeleton, steps[start:end], by_step)
            if candidate is None:
                break
            best = candidate
            end += 1
        if best is None:
            start += 1
        else:
            loops.append(best)
            start += len(best.steps)

    # Bước so le: `đọc 1, đăng 1, đọc 2, đăng 2`. Nhóm đầu tiên của chu kỳ lặp trên một
    # mảng là vòng lặp gốc; các nhóm còn lại để phần làn xét.
    based = {ref for loop in loops for ref in loop.steps}
    blocks = list(_blocks(steps))
    for at, block in enumerate(blocks):
        if len(block) < 2 or any(step.ref in based for group in block for step in group):
            continue
        for split in _strides(block):
            base = next(
                (loop for group in split if (loop := _loop(skeleton, group, by_step)) is not None),
                None,
            )
            if base is not None:
                loops.append(base)
                blocks[at] = split
                break
    order = {step.ref: position for position, step in enumerate(steps)}
    loops.sort(key=lambda loop: order[loop.steps[0]])
    return _with_lanes(skeleton, loops, blocks, by_step)


def _same_call(first: SkeletonStep, other: SkeletonStep) -> bool:
    return (first.turn, first.tool_id, first.tool_version) == (
        other.turn,
        other.tool_id,
        other.tool_version,
    )


def _loop(
    skeleton: Skeleton,
    run: Sequence[SkeletonStep],
    by_step: Mapping[str, Mapping[Path, Sources]],
) -> LoopCandidate | None:
    values = [{literal.path: literal.value for literal in step.literals} for step in run]
    paths = set(values[0])
    if any(set(other) != paths for other in values[1:]):
        return None
    varying = [path for path in paths if any(other[path] != values[0][path] for other in values)]
    if len(varying) != 1:
        return None
    (field,) = varying

    # Cùng một mảng, cùng một đường từ phần tử, mỗi bước một chỉ số khác.
    shared: dict[tuple[str, Path, Path], list[int]] | None = None
    for step in run:
        here = {
            (source.step, source.element.array, source.element.pick): source.element.index
            for source in by_step[step.ref][field].steps
            if source.element is not None
        }
        if shared is None:
            shared = {key: [index] for key, index in here.items()}
        else:
            shared = {key: [*seen, here[key]] for key, seen in shared.items() if key in here}
    if not shared:
        return None

    for (source, array, pick), indices in shared.items():
        if len(set(indices)) != len(indices):
            continue
        elements = walk(skeleton.step(source).output, array)
        return LoopCandidate(
            steps=tuple(step.ref for step in run),
            field=field,
            source=source,
            array=array,
            pick=pick,
            indices=tuple(indices),
            covers_all=len(indices) == len(elements),
            filter=_filter(elements, set(indices)),
            suggested=_suggested(elements) if len(indices) == len(elements) else (),
        )
    return None


#: Tên trường thường nói phần tử thuộc loại nào.
_KIND_FIELDS: Final = ("type", "kind", "status", "category", "subtype")
_MAX_KIND_CHARS: Final = 40


def _suggested(elements: Sequence[Any]) -> tuple[tuple[str, Any], ...]:
    """Chỉ những trường có tên nói về loại: một trường bất kỳ trùng nhau ở sáu phần tử
    (`object` = "block", `archived` = false) không nói gì về phần tử thứ bảy."""
    if len(elements) < 2 or not all(isinstance(element, Mapping) for element in elements):
        return ()
    first = elements[0]
    return tuple(
        (name, first[name])
        for name in _KIND_FIELDS
        if isinstance(first.get(name), str)
        and 0 < len(first[name]) <= _MAX_KIND_CHARS
        and all(element.get(name) == first[name] for element in elements)
    )


def _filter(elements: Sequence[Any], used: set[int]) -> tuple[tuple[str, Any], ...]:
    kept = [element for index, element in enumerate(elements) if index in used]
    dropped = [element for index, element in enumerate(elements) if index not in used]
    if not dropped or not all(isinstance(element, Mapping) for element in elements):
        return ()
    return tuple(
        (field, value)
        for field, value in kept[0].items()
        if isinstance(value, str | int | bool)
        and all(element.get(field) == value for element in kept)
        and not any(element.get(field) == value for element in dropped)
    )


# ─── Làn ─────────────────────────────────────────────────────────────────────


def _with_lanes(
    skeleton: Skeleton,
    loops: Sequence[LoopCandidate],
    blocks: Sequence[tuple[_Group, ...]],
    by_step: Mapping[str, Mapping[Path, Sources]],
) -> tuple[LoopCandidate, ...]:
    """Gắn vào mỗi vòng lặp những nhóm bước khác chạy một lần cho mỗi vòng của nó."""
    order = {step.ref: position for position, step in enumerate(skeleton.steps)}
    based = {ref for loop in loops for ref in loop.steps}
    lanes: dict[int, list[Lane]] = {number: [] for number in range(len(loops))}

    for run in (group for block in blocks for group in block):
        if run[0].ref in based:
            continue
        # Cùng số vòng, và ở mỗi vòng bước của nhóm này chạy sau bước của vòng lặp.
        fitting = [
            number
            for number, loop in enumerate(loops)
            if len(loop.steps) == len(run)
            and all(
                order[base] < order[step.ref] for base, step in zip(loop.steps, run, strict=True)
            )
        ]
        placed = False
        for number in fitting:
            lane = _lane(skeleton, loops[number], lanes[number], run, by_step)
            if lane is not None and lane.links:
                lanes[number].append(lane)
                placed = True
                break
        if placed or not fitting:
            continue
        # Không có dấu vết dữ liệu: gắn vào vòng lặp gần nhất, để người trả lời quyết.
        nearest = fitting[-1]
        lane = _lane(skeleton, loops[nearest], lanes[nearest], run, by_step)
        if lane is not None:
            lanes[nearest].append(lane)

    return tuple(replace(loop, lanes=tuple(lanes[number])) for number, loop in enumerate(loops))


_Group = tuple[SkeletonStep, ...]


def _blocks(steps: Sequence[SkeletonStep]) -> Iterator[tuple[_Group, ...]]:
    """Cắt các bước thành từng khối, mỗi khối là những nhóm bước ứng với nhau theo vòng.

    Khối một nhóm: các bước liền nhau gọi cùng một tool (`A A A`), hoặc một bước lẻ.
    Khối nhiều nhóm: một chu kỳ lặp lại (`A B A B A B`), mỗi vị trí trong chu kỳ là một nhóm.
    """
    start = 0
    while start < len(steps):
        run = 1
        while start + run < len(steps) and _same_call(steps[start], steps[start + run]):
            run += 1
        period, times = _cycle(steps, start)
        # Chu kỳ chỉ thắng khi nó phủ nhiều bước hơn dãy liền nhau.
        if period * times > run:
            yield tuple(
                tuple(steps[start + offset + period * turn] for turn in range(times))
                for offset in range(period)
            )
            start += period * times
        else:
            yield (tuple(steps[start : start + run]),)
            start += run


def _same_shape(first: SkeletonStep, other: SkeletonStep) -> bool:
    return _same_call(first, other) and list(first.fields) == list(other.fields)


def _strides(block: tuple[_Group, ...]) -> Iterator[tuple[_Group, ...]]:
    """`block`, rồi các cách đọc nó với chu kỳ dài gấp đôi, gấp ba…: một vòng có thể gọi
    cùng một tool hai lần (`đọc câu hỏi, đăng, đọc đáp án, đăng`)."""
    times = len(block[0])
    for stride in range(1, times // 2 + 1):
        if times % stride == 0:
            yield tuple(group[offset::stride] for offset in range(stride) for group in block)


def _cycle(steps: Sequence[SkeletonStep], start: int) -> tuple[int, int]:
    """Chu kỳ ngắn nhất bắt đầu ở `start` lặp lại ít nhất hai lần: `(độ dài, số lần)`."""
    for period in range(2, (len(steps) - start) // 2 + 1):
        times = 1
        while start + period * (times + 1) <= len(steps) and all(
            _same_shape(steps[start + offset], steps[start + period * times + offset])
            for offset in range(period)
        ):
            times += 1
        if times >= 2:
            return period, times
    return 0, 0


def _lane(
    skeleton: Skeleton,
    loop: LoopCandidate,
    accepted: Sequence[Lane],
    run: Sequence[SkeletonStep],
    by_step: Mapping[str, Mapping[Path, Sources]],
) -> Lane | None:
    names = list(run[0].fields)
    if any(list(step.fields) != names for step in run[1:]):
        return None
    earlier = [loop.steps, *(lane.steps for lane in accepted)]

    links: list[LaneLink] = []
    written: list[str] = []
    for name in names:
        values = [step.fields[name] for step in run]
        if all(value == values[0] for value in values[1:]):
            continue
        link = _lane_link(skeleton, loop, earlier, run, name, by_step)
        if link is not None:
            links.append(link)
        elif all(
            not found.is_sourced
            for step in run
            for path, found in by_step[step.ref].items()
            if path[: len(step.root) + 1] == (*step.root, name)
        ):
            written.append(name)
        else:
            # Thay đổi qua từng vòng mà nguồn lại không theo vòng: không phải một làn.
            return None
    if not links and not written:
        return None
    return Lane(steps=tuple(step.ref for step in run), links=tuple(links), written=tuple(written))


def _lane_link(
    skeleton: Skeleton,
    loop: LoopCandidate,
    earlier: Sequence[Sequence[str]],
    run: Sequence[SkeletonStep],
    name: str,
    by_step: Mapping[str, Mapping[Path, Sources]],
) -> LaneLink | None:
    """Trường `name` của bước thứ k lấy từ cùng một chỗ trong dữ liệu của vòng thứ k."""
    #: Mô tả một nguồn không phụ thuộc k → chỉ số phần tử ở từng vòng (nếu có).
    shared: dict[tuple[Any, ...], list[int | None]] | None = None
    for position, step in enumerate(run):
        found = by_step[step.ref].get((*step.root, name))
        if found is None:
            return None
        here: dict[tuple[Any, ...], int | None] = {}
        for source in found.steps:
            element = source.element
            if (
                source.step == loop.source
                and element is not None
                and element.array == loop.array
                and element.index == loop.indices[position]
            ):
                here[("item", element.pick)] = None
            for lane, members in enumerate(earlier):
                if source.step != members[position]:
                    continue
                here[("path", lane, source.path)] = None
                if element is not None:
                    here[("element", lane, element.array, element.pick)] = element.index
        if shared is None:
            shared = {key: [index] for key, index in here.items()}
        else:
            shared = {key: [*seen, here[key]] for key, seen in shared.items() if key in here}
    if not shared:
        return None

    # Phần tử đang lặp trước; rồi phần tử chọn theo khoá, vì nó sống sót khi thứ tự đổi;
    # đường cố định sau cùng.
    for key in sorted(shared, key=lambda found: ("item", "element", "path").index(found[0])):
        if key[0] == "item":
            return LaneLink(field=name, lane=None, path=key[1])
        if key[0] == "path":
            return LaneLink(field=name, lane=key[1], path=key[2])
        _, lane, array, pick = key
        indices = [index for index in shared[key] if index is not None]
        arrays = [walk(skeleton.step(ref).output, array) for ref in earlier[lane]]
        chosen = _shared_key(arrays, indices, pick)
        if chosen is not None:
            return LaneLink(field=name, lane=lane, path=array, key=chosen, pick=pick)
    return None


def _shared_key(
    arrays: Sequence[Sequence[Any]], indices: Sequence[int], pick: Path
) -> tuple[str, Any] | None:
    """Một trường mà ở mọi vòng, phần tử được chọn là phần tử đầu tiên mang giá trị đó."""
    first = arrays[0][indices[0]]
    if not isinstance(first, Mapping):
        return None
    # Chuỗi trước: `type == "toggle"` nói được nhiều hơn `has_children == True`.
    for field, value in sorted(first.items(), key=lambda item: not isinstance(item[1], str)):
        if pick == (field,) or isinstance(value, Mapping | list) or value is None:
            continue
        if all(
            next(
                (
                    position
                    for position, other in enumerate(array)
                    if isinstance(other, Mapping) and other.get(field) == value
                ),
                None,
            )
            == index
            for array, index in zip(arrays, indices, strict=True)
        ):
            return field, value
    return None
