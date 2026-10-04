"""Tính một node `condition`: nhánh `true` hay nhánh `false`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.core.errors import WorkflowExecutionError
from app.domains.workflow.domain.bindings import Missing, Scope, lookup
from app.domains.workflow.domain.ir import ConditionNode, ConditionOp


def holds(node: ConditionNode, scope: Scope) -> bool:
    """`exists` hỏi vế trái có giá trị không, nên vế trái vắng mặt là `False`. Với mọi
    toán tử khác, một vế vắng mặt hoặc hai vế không so được là lỗi của lần chạy."""
    left = lookup(node.left, scope)
    if node.op is ConditionOp.EXISTS:
        return not isinstance(left, Missing)
    if isinstance(left, Missing):
        raise _failed(node, "left", left.reason)

    assert node.right is not None  # bảo đảm bởi schema
    right = lookup(node.right, scope)
    if isinstance(right, Missing):
        raise _failed(node, "right", right.reason)

    match node.op:
        case ConditionOp.EQ:
            return _same(left, right)
        case ConditionOp.NE:
            return not _same(left, right)
        case ConditionOp.LT | ConditionOp.GT:
            if not _ordered(left, right):
                raise _failed(
                    node,
                    "op",
                    f"không so thứ tự được {type(left).__name__} với {type(right).__name__}.",
                )
            return bool(left < right) if node.op is ConditionOp.LT else bool(left > right)
        case ConditionOp.CONTAINS:
            if isinstance(left, str) and isinstance(right, str):
                return right in left
            if isinstance(left, Sequence) and not isinstance(left, str | bytes):
                return any(_same(item, right) for item in left)
            if isinstance(left, Mapping):
                return right in left
            raise _failed(node, "op", f"{type(left).__name__} không chứa được gì.")
    raise _failed(node, "op", f"toán tử {node.op} chưa được hỗ trợ.")


def _same(left: Any, right: Any) -> bool:
    # `True == 1` trong Python; với người viết workflow thì không.
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    return bool(left == right)


def _ordered(left: Any, right: Any) -> bool:
    numbers = (int, float)
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    if isinstance(left, numbers) and isinstance(right, numbers):
        return True
    return isinstance(left, str) and isinstance(right, str)


def _failed(node: ConditionNode, field: str, reason: str) -> WorkflowExecutionError:
    return WorkflowExecutionError(
        f"{node.id}.{field}: {reason}", details={"node": node.id, "field": [field]}
    )
