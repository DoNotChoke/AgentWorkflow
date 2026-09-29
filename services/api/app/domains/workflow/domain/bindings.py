"""Binding → giá trị thật, lúc chạy."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Final

from app.core.errors import WorkflowExecutionError
from app.domains.workflow.domain.ir import (
    Binding,
    Const,
    FromConnection,
    FromInput,
    FromItem,
    FromNode,
    LoopNode,
    Path,
    ScalarBinding,
)


@dataclass(frozen=True, slots=True)
class Missing:
    """Không có giá trị. Khác `None`: `None` là một giá trị provider trả về."""

    reason: str
    #: Đoạn đường đã đi được trước khi hụt.
    reached: Path = ()


_NO_ITEM: Final = Missing("from_item chỉ có nghĩa trong thân loop.")


@dataclass(frozen=True, slots=True)
class Scope:
    """Những gì một binding đọc được ở chỗ nó đứng."""

    inputs: Mapping[str, Any]
    #: Output theo id node. Trong thân loop, gồm cả node đã chạy của lượt hiện tại.
    outputs: Mapping[str, Any]
    item: Any = _NO_ITEM
    #: Metadata không bí mật của connection, theo provider (§11.2).
    connections: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def with_item(self, item: Any, outputs: Mapping[str, Any]) -> Scope:
        return replace(self, item=item, outputs=outputs)


def lookup(binding: Binding, scope: Scope) -> Any | Missing:
    match binding:
        case Const():
            return binding.value
        case FromInput():
            if binding.name not in scope.inputs:
                return Missing(f"input {binding.name!r} không được truyền vào.")
            return scope.inputs[binding.name]
        case FromItem():
            if isinstance(scope.item, Missing):
                return scope.item
            return _walk(scope.item, binding.path)
        case FromConnection():
            metadata = scope.connections.get(binding.provider, {})
            if binding.field not in metadata:
                return Missing(f"connection {binding.provider} không có trường {binding.field!r}.")
            return metadata[binding.field]
        case FromNode():
            return _from_node(binding, scope)


def resolve(binding: Binding, scope: Scope, *, node: str, at: Path) -> Any:
    """Như `lookup`, nhưng không có giá trị là lỗi của lần chạy."""
    value = lookup(binding, scope)
    if isinstance(value, Missing):
        location = f"{node}.{'.'.join(at)}"
        raise WorkflowExecutionError(
            f"{location}: {value.reason}",
            details={"node": node, "field": list(at), "reached": list(value.reached)},
        )
    return value


def resolve_inputs(bindings: Mapping[str, Binding], scope: Scope, *, node: str) -> dict[str, Any]:
    """Input của một node. `from_input` không được truyền thì bỏ trường đó —
    input tuỳ chọn của trigger; schema của tool quyết nó có bắt buộc không."""
    resolved: dict[str, Any] = {}
    for key, binding in bindings.items():
        if isinstance(binding, FromInput) and binding.name not in scope.inputs:
            continue
        resolved[key] = resolve(binding, scope, node=node, at=("input", key))
    return resolved


def loop_items(loop: LoopNode, scope: Scope) -> tuple[Any, ...]:
    """Phần tử mà loop sẽ chạy qua, đã lọc. Quá `max_items` là lỗi, không cắt."""
    over = resolve(loop.over, scope, node=loop.id, at=("over",))
    if not _is_array(over):
        raise WorkflowExecutionError(
            f"{loop.id}.over không phải mảng mà là {type(over).__name__}.",
            details={"node": loop.id, "field": ["over"]},
        )

    wanted = {
        key: resolve(value, scope, node=loop.id, at=("filter", key))
        for key, value in (loop.filter or {}).items()
    }
    items = tuple(item for item in over if _matches(item, wanted))
    if len(items) > loop.max_items:
        raise WorkflowExecutionError(
            f"Loop {loop.id} có {len(items)} phần tử, trần là {loop.max_items}.",
            details={"node": loop.id, "count": len(items), "max_items": loop.max_items},
        )
    return items


def _from_node(binding: FromNode, scope: Scope) -> Any | Missing:
    if binding.node not in scope.outputs:
        return Missing(f"node {binding.node!r} chưa có output.")
    value = _walk(scope.outputs[binding.node], binding.path)
    if not isinstance(value, Missing) and binding.where is not None:
        value = _first_match(binding, binding.where, value, scope)
    if isinstance(value, Missing) or binding.pick is None:
        return value
    return _walk(value, binding.pick)


def _first_match(
    binding: FromNode, where: Mapping[str, ScalarBinding], value: Any, scope: Scope
) -> Any | Missing:
    if not _is_array(value):
        return Missing(
            f"where cần một mảng ở {'.'.join(binding.path)}, gặp {type(value).__name__}.",
            binding.path,
        )
    wanted = _scalars(where, scope)
    if isinstance(wanted, Missing):
        return wanted
    for element in value:
        if _matches(element, wanted):
            return element
    shown = ", ".join(f"{key} = {expected!r}" for key, expected in wanted.items())
    return Missing(
        f"không phần tử nào trong {binding.node}.{'.'.join(binding.path)} có {shown}.",
        binding.path,
    )


def _scalars(bindings: Mapping[str, ScalarBinding], scope: Scope) -> dict[str, Any] | Missing:
    wanted: dict[str, Any] = {}
    for key, binding in bindings.items():
        value = lookup(binding, scope)
        if isinstance(value, Missing):
            return value
        wanted[key] = value
    return wanted


def _matches(element: object, wanted: Mapping[str, Any]) -> bool:
    if not wanted:
        return True
    if not isinstance(element, Mapping):
        return False
    return all(key in element and element[key] == value for key, value in wanted.items())


def _walk(value: Any, path: Path) -> Any | Missing:
    """Đi theo `path`; đoạn là số thì đi vào mảng theo chỉ số."""
    current = value
    for depth, segment in enumerate(path):
        if isinstance(current, Mapping):
            if segment not in current:
                return Missing(f"không có {'.'.join(path[: depth + 1])}.", path[:depth])
            current = current[segment]
        elif _is_array(current) and segment.isdigit():
            index = int(segment)
            if index >= len(current):
                return Missing(
                    f"{'.'.join(path[:depth]) or 'mảng'} chỉ có {len(current)} phần tử, "
                    f"không có phần tử {index}.",
                    path[:depth],
                )
            current = current[index]
        else:
            return Missing(
                f"không đi tiếp được tới {'.'.join(path[: depth + 1])}: "
                f"gặp {type(current).__name__}.",
                path[:depth],
            )
    return current


def _is_array(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes)
